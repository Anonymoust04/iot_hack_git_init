import { useState, useEffect } from "react";
import "../styles/Dashboard.css";

import ParkingGrid from "../components/ParkingGrid";
import GateControl from "../components/GateControl";
import ParkComponentsControl from "../components/ParkComponentsControl";
import RecentActivity from "../components/RecentActivity";
import { getSystemStatus, getParkingSpots, getActiveCars, calculateZoneStats } from "../services/api";

function Dashboard() {
  const [stats, setStats] = useState({
    totalSpaces: 30,
    availableSpaces: 30,
    occupiedSpaces: 0,
    carsInside: 0,
  });

  const [spots, setSpots] = useState([]);
  const [zoneStats, setZoneStats] = useState([]);
  const [systemOnline, setSystemOnline] = useState(false);
  const [lastUpdated, setLastUpdated] = useState(null);

  const fetchDashboardData = async () => {
    try {
      const [sysStatus, spotList, activeList] = await Promise.all([
        getSystemStatus(),
        getParkingSpots(),
        getActiveCars(),
      ]);

      const isOnline = sysStatus.backend === "online";
      setSystemOnline(isOnline);

      const occupiedCount = spotList.filter((s) => s.status === "occupied").length;
      const availableCount = spotList.filter((s) => s.status === "free").length;

      setStats({
        totalSpaces: spotList.length || 30,
        availableSpaces: availableCount,
        occupiedSpaces: occupiedCount,
        carsInside: Math.max(activeList.length, occupiedCount, sysStatus.cars_inside || 0),
      });

      setSpots(spotList);
      setZoneStats(calculateZoneStats(spotList));
      setLastUpdated(new Date().toLocaleTimeString());
    } catch (err) {
      console.warn("Error refreshing dashboard:", err);
      setSystemOnline(false);
    }
  };

  useEffect(() => {
    fetchDashboardData();
    const interval = setInterval(fetchDashboardData, 5000);
    return () => clearInterval(interval);
  }, []);

  const isCarParkFull = stats.availableSpaces === 0;

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
          <h2 style={{ color: stats.availableSpaces > 0 ? "#10b981" : "#ef4444" }}>
            {stats.availableSpaces}
          </h2>
        </div>

        <div className="stat-card">
          <p>Occupied</p>
          <h2>{stats.occupiedSpaces}</h2>
        </div>

        <div className="stat-card">
          <p>Cars Inside</p>
          <h2>{stats.carsInside}</h2>
        </div>
      </section>

      {/* Zone Status Breakdown */}
      <section style={{ marginTop: "28px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "14px" }}>
          <h3 style={{ margin: 0, fontSize: "1.15rem", fontWeight: 600 }}>Occupancy by Zone</h3>
          <span style={{ fontSize: "0.85rem", color: "#6b7280" }}>Real-time zone distribution</span>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: "16px" }}>
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
                      backgroundColor: z.free > 0 ? "#dcfce7" : "#fee2e2",
                      color: z.free > 0 ? "#166534" : "#991b1b",
                    }}
                  >
                    {z.free > 0 ? `${z.free} Free` : "Zone Full"}
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
                      backgroundColor: freePercent > 30 ? "#3b82f6" : freePercent > 0 ? "#f59e0b" : "#ef4444",
                      transition: "width 0.3s ease",
                    }}
                  ></div>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {/* Interactive Parking Grid with Zone Filter */}
      <ParkingGrid initialSpots={spots} onRefresh={fetchDashboardData} />

      {/* Barrier Gate Control */}
      <GateControl />

      {/* Facility & Component Controls (Lights, Exhaust Fans, Alarms, Test Webhook) */}
      <ParkComponentsControl />

      {/* Live Recent Activity */}
      <RecentActivity />
    </div>
  );
}

export default Dashboard;