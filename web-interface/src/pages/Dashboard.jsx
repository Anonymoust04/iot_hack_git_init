import { useState, useEffect } from "react";
import "../styles/Dashboard.css";

import ParkingGrid from "../components/ParkingGrid";
import GateControl from "../components/GateControl";
import ParkComponentsControl from "../components/ParkComponentsControl";
import RecentActivity from "../components/RecentActivity";
import LastLoginAttempts from "../components/LastLoginAttempts";
import { getSystemStatus, getParkingSpots, getActiveCars, getBarrierHealthData, getLights, getExhaustFans, getAlarms, getZones, calculateZoneStats, isLastKnownOnline, REFRESH_MS } from "../services/api";

const componentGroups = [
  { key: "spots", name: "Parking Spots", type: "Parking Spot" },
  { key: "barriers", name: "Barrier Gates", type: "Barrier Gate" },
  { key: "lights", name: "Lights", type: "Light" },
  { key: "fans", name: "Exhaust Fans", type: "Exhaust Fan" },
];

function componentCondition(component, type) {
  if (component?.broken === true || component?.isBroken === true) return "broken";
  if (component?.isUnderMaintenance === true || component?.status === "maintenance") return "maintenance";
  if (type === "Light" && component?.broken === undefined && component?.isBroken === undefined && component?.isUnderMaintenance === undefined) return "unknown";
  return "operational";
}

function riskStyle(risk) {
  const value = String(risk || "").toLowerCase();
  if (value === "normal" || value === "safe") return "safe";
  if (value === "warning" || value === "elevated") return "warning";
  if (value === "critical" || value === "dangerous") return "critical";
  return "unknown";
}

function coReading(zone) {
  const value = zone.gasCarbonMonoxideLevel;
  return value === null || value === undefined || value === "" || !Number.isFinite(Number(value))
    ? null : Number(value);
}

function sameZone(parent, name) {
  return typeof parent === "string" && typeof name === "string"
    && parent.replace(/\s+/g, "").toLowerCase() === name.replace(/\s+/g, "").toLowerCase();
}

// Last dashboard snapshot, kept while the app is open. Leaving for another page (Audit, Penalties...)
// unmounts this one, so without it the dashboard would show "Unavailable" again until the next refresh.
let lastSnapshot = null;
const emptyStats = { totalSpaces: 90, availableSpaces: null, occupiedSpaces: null, carsInside: null };
const emptyComponents = { barriers: [], lights: [], fans: [], alarms: [] };

function Dashboard() {
  const [stats, setStats] = useState(() => lastSnapshot?.stats ?? emptyStats);
  const [spots, setSpots] = useState(() => lastSnapshot?.spots ?? []);
  const [zoneStats, setZoneStats] = useState(() => lastSnapshot?.zoneStats ?? []);
  // Start from the last known status (shared across pages): switching tabs must not flash "offline"
  const [systemOnline, setSystemOnline] = useState(isLastKnownOnline);
  const [alertsAvailable, setAlertsAvailable] = useState(() => lastSnapshot?.alertsAvailable ?? false);
  const [lastUpdated, setLastUpdated] = useState(() => lastSnapshot?.lastUpdated ?? null);
  const [components, setComponents] = useState(() => lastSnapshot?.components ?? emptyComponents);
  const [zones, setZones] = useState(() => lastSnapshot?.zones ?? []);

  const worstCoZone = zones.filter((zone) => coReading(zone) !== null)
    .reduce((worst, zone) => !worst || coReading(zone) > coReading(worst) ? zone : worst, null);

  const healthGroups = componentGroups.map((group) => {
    const items = group.key === "spots" ? spots : components[group.key];
    const conditions = items.map((item) => componentCondition(item, group.type));
    const broken = conditions.filter((condition) => condition === "broken").length;
    const maintenance = conditions.filter((condition) => condition === "maintenance").length;
    const unknown = conditions.filter((condition) => condition === "unknown").length;
    return {
      ...group,
      items,
      operational: items.length - broken - maintenance - unknown,
      issues: broken + maintenance,
      status: broken ? "broken" : maintenance ? "maintenance" : !items.length || unknown ? "unknown" : "operational",
    };
  });
  const issues = healthGroups.flatMap((group) => group.items.flatMap((item) => {
    const condition = componentCondition(item, group.type);
    return condition === "broken" || condition === "maintenance"
      ? [{ name: item.name, type: group.type, zone: item.zone || item.zoneParent, condition }]
      : [];
  }));
  const totalComponents = healthGroups.reduce((total, group) => total + group.items.length, 0);
  const operationalComponents = healthGroups.reduce((total, group) => total + group.operational, 0);
  const hasUnknownHealth = healthGroups.some((group) => group.status === "unknown");
  const systemHealth = !totalComponents ? "Unavailable" : issues.some((issue) => issue.condition === "broken")
    ? "Warning" : issues.length ? "Maintenance" : hasUnknownHealth ? "Unknown" : "Healthy";

  const componentAlerts = healthGroups
    .filter((group) => ["spots", "barriers", "fans"].includes(group.key))
    .flatMap((group) => group.items.flatMap((item) => {
      const condition = componentCondition(item, group.type);
      if (condition !== "broken" && condition !== "maintenance") return [];
      return [{
        severity: condition === "broken" ? "Critical" : "Warning",
        source: `${group.type} ${item.name || "Unknown"}`,
        message: condition === "broken" ? "Component broken" : "Component under maintenance",
        zone: item.zone || item.zoneParent || "—",
      }];
    }));
  const alarmAlerts = components.alarms.filter((alarm) => typeof alarm?.problem === "string" && alarm.problem.trim())
    .map((alarm) => ({
      severity: ["critical", "warning", "info"].includes(String(alarm.severity).toLowerCase())
        ? String(alarm.severity).toLowerCase() : "Warning",
      source: alarm.name || "Simulator Alarm",
      message: alarm.problem,
      zone: alarm.zoneParent || "—",
    }));
  const coAlerts = zones.filter((zone) => ["warning", "critical"].includes(riskStyle(zone.risk)))
    .map((zone) => ({
      severity: riskStyle(zone.risk) === "critical" ? "Critical" : "Warning",
      source: `${zone.name} CO`,
      message: `CO risk: ${zone.risk}`,
      zone: zone.name,
    }));
  const activeAlerts = [...componentAlerts, ...alarmAlerts, ...coAlerts]
    .sort((a, b) => (a.severity === "Critical" ? 0 : 1) - (b.severity === "Critical" ? 0 : 1));

  const fetchDashboardData = async () => {
    try {
      const [sysStatus, spotList, activeList, barriers, lights, fans, alarms, zoneList] = await Promise.all([
        getSystemStatus(),
        getParkingSpots(),
        getActiveCars(),
        getBarrierHealthData(),
        getLights(),
        getExhaustFans(),
        getAlarms(),
        getZones(),
      ]);

      const isOnline = sysStatus.backend === "online" && sysStatus.simulator === "online";
      setSystemOnline(isOnline);
      setAlertsAvailable(isOnline &&
        (spotList.length > 0 || barriers.length > 0 || fans.length > 0 || alarms.length > 0 || zoneList.length > 0));

      const occupiedCount = spotList.filter((s) => s.status === "occupied").length;
      const availableCount = spotList.filter((s) => s.status === "free").length;

      const nextStats = {
        totalSpaces: isOnline && spotList.length ? spotList.length : 90,
        availableSpaces: isOnline && spotList.length ? availableCount : null,
        occupiedSpaces: isOnline && spotList.length ? occupiedCount : null,
        carsInside: isOnline ? Math.max(activeList.length, occupiedCount, sysStatus.cars_inside || 0) : null,
      };
      const nextComponents = isOnline ? { barriers, lights, fans, alarms } : emptyComponents;
      const nextZoneStats = calculateZoneStats(isOnline ? spotList : []);
      const nextUpdated = new Date().toLocaleTimeString();

      setStats(nextStats);
      setSpots(isOnline ? spotList : []);
      setComponents(nextComponents);
      setZones(isOnline ? zoneList : []);
      setZoneStats(nextZoneStats);
      setLastUpdated(nextUpdated);
      if (isOnline) {
        // remember it, so coming back from another page shows data at once
        lastSnapshot = { stats: nextStats, spots: spotList, zoneStats: nextZoneStats, zones: zoneList,
                         components: nextComponents, alertsAvailable: spotList.length > 0, lastUpdated: nextUpdated };
      }
    } catch (err) {
      console.warn("Error refreshing dashboard:", err);
      if (lastSnapshot) return;   // keep the last data on screen; the next refresh (3 s) tries again
      setSystemOnline(false);
      setAlertsAvailable(false);
      setStats(emptyStats);
      setSpots([]);
      setComponents(emptyComponents);
      setZones([]);
      setZoneStats(calculateZoneStats([]));
      setLastUpdated(null);
    }
  };

  useEffect(() => {
    fetchDashboardData();
    const interval = setInterval(fetchDashboardData, REFRESH_MS);   // VITE_REFRESH_MS, default 5 s
    return () => clearInterval(interval);
  }, []);

  const isCarParkFull = systemOnline && stats.availableSpaces === 0;

  return (
    <div className="dashboard">
      {/* Header */}
      <header className="dashboard-header">
        <div>
          <h1>Parking Dashboard</h1>
          <p>
            Monitor and manage the car park in real time{" "}
            {lastUpdated && <span style={{ opacity: 0.6, fontSize: "0.85em" }}>· Updated {lastUpdated}</span>}
          </p>
        </div>

        <div className="system-status">
          <span
            className="status-dot"
            style={{
              backgroundColor: systemOnline ? "#22c55e" : "#ef4444",
            }}
          ></span>
          {systemOnline ? "System Online" : "System Offline"}
        </div>
      </header>

      <LastLoginAttempts />

      {/* Full Car Park Warning */}
      {isCarParkFull && (
        <div className="capacity-warning" style={{
          marginBottom: "24px",
          padding: "20px",
          borderRadius: "12px",
          backgroundColor: "#fee2e2",
          border: "1px solid #f87171",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center"
        }}>
          <div>
            <strong style={{ color: "#991b1b", fontSize: "1.1rem" }}>Car Park Full</strong>
            <p style={{ margin: "6px 0 0", color: "#7f1d1d" }}>
              There are currently 0 available parking spaces.
              New vehicles arriving at Gate A are automatically directed to leavepark.
            </p>
          </div>

          <span style={{
            padding: "8px 16px",
            backgroundColor: "#dc2626",
            color: "white",
            fontWeight: 700,
            borderRadius: "8px",
            fontSize: "0.9rem"
          }}>
            0 SPACES AVAILABLE
          </span>
        </div>
      )}


      {/* Summary Statistics */}
      <section className="stats">
        <div className="stat-card">
          <p>Total Spaces</p>
          <h2>{stats.totalSpaces}</h2>
        </div>

        <div className="stat-card">
          <p>Available</p>
          <h2 className={stats.availableSpaces === null ? "unavailable-stat" : ""} style={{ color: stats.availableSpaces === null ? "#64748b" : stats.availableSpaces > 0 ? "#10b981" : "#ef4444" }}>
            {stats.availableSpaces ?? "Unavailable"}
          </h2>
        </div>

        <div className="stat-card">
          <p>Occupied</p>
          <h2 className={stats.occupiedSpaces === null ? "unavailable-stat" : ""}>{stats.occupiedSpaces ?? "Unavailable"}</h2>
        </div>

        <div className="stat-card">
          <p>Cars Inside</p>
          <h2 className={stats.carsInside === null ? "unavailable-stat" : ""}>{stats.carsInside ?? "Unavailable"}</h2>
        </div>
      </section>

      <section className="operations-overview">
        <div className="operation-card">
          <p>System Health</p>
          <h2 className={["Unavailable", "Unknown"].includes(systemHealth) ? "environment-unknown" : issues.length ? "health-warning" : "co-normal"}>
            {systemHealth}
          </h2>
          <span className="operation-detail">
            {systemOnline ? `${operationalComponents} / ${totalComponents} components operational` : "Component data unavailable"}
          </span>
        </div>

        <div className="operation-card">
          <p>CO Level</p>
          <h2 className={`environment-${worstCoZone ? riskStyle(worstCoZone.risk) : "unknown"}`}>
            {worstCoZone ? `${coReading(worstCoZone)} ppm` : "Unavailable"}
          </h2>
          <span className="operation-detail">
            {worstCoZone ? `${worstCoZone.name} · ${worstCoZone.risk || "Risk unavailable"}` : "CO data unavailable"}
          </span>
        </div>

        <div className="operation-card">
          <p>Active Alerts</p>
          <h2 className={alertsAvailable ? "alert-value" : "environment-unknown"}>
            {alertsAvailable ? activeAlerts.length : "Unavailable"}
          </h2>
          <span className="operation-detail">
            Requires attention
          </span>
        </div>

        <div className="operation-card">
          <p>Maintenance Due</p>
          <h2 className={systemOnline && totalComponents > 0 ? "maintenance-value" : "environment-unknown"}>
            {systemOnline && totalComponents > 0 ? issues.length : "Unavailable"}
          </h2>
          <span className="operation-detail">
            Components need attention
          </span>
        </div>
      </section>


      {/* Zone Status Breakdown */}
      <section style={{ marginTop: "28px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "14px" }}>
          <h3 style={{ margin: 0, fontSize: "1.15rem", fontWeight: 600 }}>Occupancy by Zone</h3>
          <span style={{ fontSize: "0.85rem", color: "#6b7280" }}>Real-time zone distribution</span>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 260px), 1fr))", gap: "16px" }}>
          {zoneStats.map((z) => {
            const freePercent = z.total > 0 ? Math.round((z.free / z.total) * 100) : 0;
            return (
              <div
                key={z.name}
                style={{
                  background: "white",
                  borderRadius: "12px",
                  padding: "18px 22px",
                  border: "1px solid #e5e7eb",
                  boxShadow: "0 2px 8px rgba(0,0,0,0.04)",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <strong style={{ fontSize: "1.05rem", color: "#1e293b" }}>{z.name}</strong>
                  <span
                    style={{
                      fontSize: "0.8rem",
                      fontWeight: 600,
                      padding: "3px 8px",
                      borderRadius: "6px",
                      backgroundColor: z.total === 0 ? "#f3f4f6" : z.free > 0 ? "#dcfce7" : "#fee2e2",
                      color: z.total === 0 ? "#6b7280" : z.free > 0 ? "#166534" : "#991b1b",
                    }}
                  >
                    {z.total === 0 ? "Unavailable" : z.free > 0 ? `${z.free} Free` : "Zone Full"}
                  </span>
                </div>

                <div style={{ marginTop: "12px", display: "flex", justifyContent: "space-between", fontSize: "0.85rem", color: "#64748b" }}>
                  <span>Occupied: <strong>{z.occupied}</strong></span>
                  <span>Free: <strong>{z.free}</strong></span>
                  <span>Total: <strong>{z.total}</strong></span>
                </div>

                {/* Progress bar */}
                <div style={{ marginTop: "10px", height: "8px", width: "100%", backgroundColor: "#e2e8f0", borderRadius: "999px", overflow: "hidden" }}>
                  <div
                    style={{
                      height: "100%",
                      width: `${100 - freePercent}%`,
                      backgroundColor: z.total === 0 ? "#cbd5e1" : freePercent > 30 ? "#3b82f6" : freePercent > 0 ? "#f59e0b" : "#ef4444",
                      transition: "width 0.3s ease",
                    }}
                  ></div>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <section className="component-health">
        <div className="section-header">
          <div>
            <h2>Component Health</h2>
            <p>Live status of parking and facility components</p>
          </div>
        </div>

        <div className="component-health-grid">
          {healthGroups.map((group) => (
            <div className="component-health-card" key={group.key}>
              <strong>{group.name}</strong>
              <p>{group.operational} / {group.items.length} Operational</p>
              <span className={`component-health-status ${group.status}`}>
                {group.issues ? `${group.issues} ${group.issues === 1 ? "Issue" : "Issues"}`
                  : group.status === "unknown" ? "Health unavailable" : "Healthy"}
              </span>
            </div>
          ))}
        </div>

        <h3>Issues Requiring Attention</h3>
        {issues.length ? (
          <div className="component-issues">
            {issues.map((issue, index) => (
              <div className="component-issue" key={`${issue.type}-${issue.name}-${index}`}>
                <div>
                  <strong>{issue.name}</strong>
                  <p>{issue.type}{issue.zone ? ` · ${issue.zone}` : ""}</p>
                </div>
                <span className={`component-health-status ${issue.condition}`}>
                  {issue.condition === "broken" ? "Broken" : "Maintenance"}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <p className="component-health-empty">
            {totalComponents ? "All components operational" : "No live component data available"}
            {totalComponents > 0 && hasUnknownHealth && " (where health is reported; some component health data is unavailable)"}
          </p>
        )}
      </section>

      <section className="environment-section">
        <div className="section-header">
          <div>
            <h2>Environmental Status</h2>
            <p>Live CO, ventilation, and lighting by zone</p>
          </div>
        </div>
        {zones.length ? (
          <div className="environment-grid">
            {zones.map((zone) => {
              const zoneFans = components.fans.filter((fan) => sameZone(fan?.zoneParent, zone.name));
              const zoneLights = components.lights.filter((light) => sameZone(light?.zoneParent, zone.name));
              const runningFans = zoneFans.filter((fan) => fan?.isOn === true && fan?.broken !== true && fan?.isUnderMaintenance !== true);
              const onLights = zoneLights.filter((light) => light?.isOn === true);
              const brokenFans = zoneFans.filter((fan) => fan?.broken === true);
              const maintenanceFans = zoneFans.filter((fan) => fan?.broken !== true && fan?.isUnderMaintenance === true);
              const reading = coReading(zone);
              return (
                <div className="environment-card" key={zone.name}>
                  <h3>{zone.name}</h3>
                  <p>CO Level: <strong>{reading === null ? "CO data unavailable" : `${reading} ppm`}</strong></p>
                  <p>Risk: <span className={`environment-risk environment-${riskStyle(zone.risk)}`}>{zone.risk || "Unavailable"}</span></p>
                  <p>Auto ventilation: <strong className={zone.ventilating ? "environment-warning" : ""}>
                    {zone.ventilating ? `ON (${zone.fansOn?.length ? zone.fansOn.join(", ") : "fans starting"})` : "Off"}
                  </strong> <small>(auto from {zone.ventilateFrom || "Mid"} risk until Safe)</small></p>
                  <p>Ventilation: <strong>{zoneFans.length ? `Fans: ${runningFans.length} / ${zoneFans.length} Running` : "Fan data unavailable"}</strong></p>
                  {brokenFans.map((fan) => <p className="environment-fan-issue environment-critical" key={`broken-${fan.name}`}>{fan.name}: Broken</p>)}
                  {maintenanceFans.map((fan) => <p className="environment-fan-issue environment-warning" key={`maintenance-${fan.name}`}>{fan.name}: Under Maintenance</p>)}
                  <p>Lighting: <strong>{zoneLights.length ? `Lights: ${onLights.length} / ${zoneLights.length} On` : "Light data unavailable"}</strong></p>
                </div>
              );
            })}
          </div>
        ) : <p className="environment-empty">CO data unavailable</p>}
      </section>

      <section className="active-alerts-section">
        <div className="section-header">
          <div>
            <h2>Active Alerts</h2>
            <p>Current issues requiring operator attention</p>
          </div>
        </div>
        {!alertsAvailable ? (
          <p className="active-alerts-empty">Live alert data unavailable</p>
        ) : activeAlerts.length ? (
          <>
            <div className="active-alerts-list">
              {activeAlerts.slice(0, 10).map((alert, index) => (
                <div className="active-alert-row" key={`${alert.source}-${alert.message}-${index}`}>
                  <span className={`active-alert-severity ${alert.severity.toLowerCase()}`}>{alert.severity}</span>
                  <strong>{alert.source}</strong>
                  <span>{alert.message}</span>
                  <span>{alert.zone}</span>
                  <span className="active-alert-state">Active</span>
                </div>
              ))}
            </div>
            {activeAlerts.length > 10 && <p className="active-alerts-more">{activeAlerts.length - 10} additional active alerts</p>}
          </>
        ) : <p className="active-alerts-empty">All systems operating normally</p>}
      </section>

      {/* Interactive Parking Grid with Zone Filter */}
      <ParkingGrid initialSpots={spots} onRefresh={fetchDashboardData} />

      {/* Barrier Gate Control */}
      <GateControl systemOnline={systemOnline} />

      {/* Facility & Component Controls (Lights, Exhaust Fans, Alarms, Test Webhook) */}
      <ParkComponentsControl systemOnline={systemOnline} />

      {/* Live Recent Activity */}
      <RecentActivity />
    </div>
  );
}

export default Dashboard;
