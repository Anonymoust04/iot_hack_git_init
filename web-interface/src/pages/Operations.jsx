import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import ParkingGrid from "../components/ParkingGrid";
import GateControl from "../components/GateControl";
import ParkComponentsControl from "../components/ParkComponentsControl";
import { getParkingSpots, getSystemStatus } from "../services/api";
import "../styles/Dashboard.css";

function Operations() {
  const location = useLocation();
  const activeSection = ["#gate-control", "#facility-controls"].includes(location.hash) ? location.hash : "#parking-spaces";
  const [systemOnline, setSystemOnline] = useState(false);
  const [spots, setSpots] = useState([]);
  const [lastUpdated, setLastUpdated] = useState(null);

  const refresh = async () => {
    const [status, spotList] = await Promise.all([getSystemStatus(), getParkingSpots()]);
    const online = status.backend === "online" && status.simulator === "online";
    setSystemOnline(online);
    setSpots(online ? spotList : []);
    setLastUpdated(new Date().toLocaleTimeString());
  };

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 5000);
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
      <div className="operations-panel" hidden={activeSection !== "#parking-spaces"}><ParkingGrid initialSpots={spots} onRefresh={refresh} /></div>
      <div className="operations-panel" hidden={activeSection !== "#gate-control"}><GateControl systemOnline={systemOnline} /></div>
      <div className="operations-panel" hidden={activeSection !== "#facility-controls"}><ParkComponentsControl systemOnline={systemOnline} /></div>
    </div>
  );
}

export default Operations;
