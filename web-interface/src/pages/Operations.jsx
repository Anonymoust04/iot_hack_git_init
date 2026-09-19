import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import ParkingGrid from "../components/ParkingGrid";
import GateControl from "../components/GateControl";
import ParkComponentsControl from "../components/ParkComponentsControl";
import { getParkingSpots, getSystemStatus, isLastKnownOnline, REFRESH_MS } from "../services/api";
import "../styles/Dashboard.css";

// Same as the dashboard: keep the last data so returning to this page shows it at once
let lastSpots = null;
let lastUpdatedAt = null;

function Operations() {
  const location = useLocation();
  const activeSection = ["#gate-control", "#facility-controls"].includes(location.hash) ? location.hash : "#parking-spaces";
  const [systemOnline, setSystemOnline] = useState(isLastKnownOnline);
  const [spots, setSpots] = useState(() => lastSpots ?? []);
  const [lastUpdated, setLastUpdated] = useState(() => lastUpdatedAt);
  const [status, setStatus] = useState(null);

  const refresh = async () => {
    const [status, spotList] = await Promise.all([getSystemStatus(), getParkingSpots()]);
    setStatus(status);
    const online = status.backend === "online" && status.simulator === "online";
    setSystemOnline(online);
    setSpots(online ? spotList : lastSpots ?? []);
    const stamp = new Date().toLocaleTimeString();
    setLastUpdated(stamp);
    if (online && spotList.length) {
      lastSpots = spotList;
      lastUpdatedAt = stamp;
    }
  };

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="dashboard operations-page">
      <header className="dashboard-header">
        <div>
          <p className="page-eyebrow">FACILITY OPERATIONS</p>
          <h1>Operations</h1>
          <p>Parking spaces and facility controls{lastUpdated ? ` · Updated ${lastUpdated}` : ""}</p>
        </div>
        <div className="system-status"><span className="status-dot" style={{ backgroundColor: systemOnline ? "#22c55e" : "#94a3b8" }} />{systemOnline ? "System Online" : "System Offline"}</div>
      </header>
      <div className="operations-panel" hidden={activeSection !== "#parking-spaces"}><ParkingGrid initialSpots={spots} availability={status} onRefresh={refresh} /></div>
      <div className="operations-panel" hidden={activeSection !== "#gate-control"}><GateControl systemOnline={systemOnline} /></div>
      <div className="operations-panel" hidden={activeSection !== "#facility-controls"}><ParkComponentsControl systemOnline={systemOnline} /></div>
    </div>
  );
}

export default Operations;
