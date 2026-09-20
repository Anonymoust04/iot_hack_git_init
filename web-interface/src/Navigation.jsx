import { Link, NavLink, useLocation } from "react-router-dom";
import { useState } from "react";
import { usePolling } from "./hooks/usePolling";
import { getBackendStatus } from "./services/api";

function Navigation({ expanded, onToggle, onNavigate }) {
  const location = useLocation();
  const [dashboardExpanded, setDashboardExpanded] = useState(true);
  let role = "OPERATOR";
  try {
    const raw = localStorage.getItem("currentUser");
    if (raw) {
      const u = JSON.parse(raw);
      if (u.role) role = u.role.toUpperCase();
    }
  } catch {
    // ignore
  }

  const { data: status } = usePolling(getBackendStatus);
  const isOnline = status ? status.backend === "online" : true;

  return (
    <nav className="navigation" aria-label="Primary navigation">
      <div className="navigation-top">
        <div className="navigation-brand">
          <span className="brand-mark">P</span>
          <span className="navigation-label">PARK//CONTROL</span>
        </div>
        <button type="button" className="navigation-toggle" onClick={onToggle} aria-label={expanded ? "Collapse navigation" : "Expand navigation"} aria-expanded={expanded}>☰</button>
      </div>

      <div className="navigation-links">
        <div className="navigation-dashboard-group">
          <div className="navigation-dashboard-line">
            <NavLink to="/dashboard" onClick={() => setDashboardExpanded((current) => location.pathname === "/dashboard" ? !current : true)} title="Dashboard" aria-expanded={dashboardExpanded}>
              <span className="navigation-letter">D</span><span className="navigation-label">DASHBOARD</span>
            </NavLink>
          </div>
          {dashboardExpanded && <div className="navigation-sublinks" id="dashboard-views">
            <Link to="/dashboard#overview" onClick={onNavigate} className={location.pathname === "/dashboard" && (location.hash === "" || location.hash === "#overview") ? "active" : ""}>Overview</Link>
            <Link to="/dashboard#system-status" onClick={onNavigate} className={location.pathname === "/dashboard" && location.hash === "#system-status" ? "active" : ""}>System Status</Link>
            <Link to="/dashboard#activity" onClick={onNavigate} className={location.pathname === "/dashboard" && location.hash === "#activity" ? "active" : ""}>Activity</Link>
          </div>}
        </div>

        <NavLink to="/operations" onClick={onNavigate} title="Operations">
          <span className="navigation-letter">O</span><span className="navigation-label">OPERATIONS</span>
        </NavLink>
        {location.pathname === "/operations" && <div className="navigation-sublinks">
          <Link to="/operations#parking-spaces" onClick={onNavigate} className={location.hash === "" || location.hash === "#parking-spaces" ? "active" : ""}>Parking Spaces</Link>
          <Link to="/operations#gate-control" onClick={onNavigate} className={location.hash === "#gate-control" ? "active" : ""}>Gate Control</Link>
          <Link to="/operations#facility-controls" onClick={onNavigate} className={location.hash === "#facility-controls" ? "active" : ""}>Facility Controls</Link>
        </div>}

        <NavLink to="/vehicles" onClick={onNavigate} title="Vehicles">
          <span className="navigation-letter">V</span><span className="navigation-label">VEHICLES</span>
        </NavLink>

        <NavLink to="/penalties" onClick={onNavigate} title="Penalties">
          <span className="navigation-letter">P</span><span className="navigation-label">PENALTIES</span>
        </NavLink>

        <NavLink to="/audit" onClick={onNavigate} title="Audit">
          <span className="navigation-letter">A</span><span className="navigation-label">AUDIT</span>
        </NavLink>

        <NavLink to="/reports" onClick={onNavigate} title="Reports">
          <span className="navigation-letter">R</span><span className="navigation-label">REPORTS</span>
        </NavLink>
        {location.pathname === "/reports" && <div className="navigation-sublinks">
          <Link to="/reports#daily" onClick={onNavigate} className={location.hash !== "#financial" ? "active" : ""}>Daily Operations</Link>
          <Link to="/reports#financial" onClick={onNavigate} className={location.hash === "#financial" ? "active" : ""}>Financial Report</Link>
        </div>}
        {role === "ADMIN" && (
          <NavLink to="/admin/integrity" onClick={onNavigate} title="Request Integrity">
            <span className="navigation-letter">I</span><span className="navigation-label">INTEGRITY</span>
          </NavLink>
        )}
        {role === "ADMIN" && (
          <NavLink to="/admin/users" onClick={onNavigate} title="User Management">
            <span className="navigation-letter">U</span><span className="navigation-label">USER MANAGEMENT</span>
          </NavLink>
        )}
        {role === "ADMIN" && location.pathname === "/admin/users" && <div className="navigation-sublinks">
          <Link to="/admin/users#add-user" onClick={onNavigate} className={location.hash === "#add-user" ? "active" : ""}>Add User</Link>
          <Link to="/admin/users#directory" onClick={onNavigate} className={location.hash === "" || location.hash === "#directory" ? "active" : ""}>User Directory</Link>
          <Link to="/admin/users#access-rules" onClick={onNavigate} className={location.hash === "#access-rules" ? "active" : ""}>Access Rules</Link>
        </div>}

      </div>

      <div className="navigation-status">
        <NavLink className="navigation-account" to="/account" onClick={onNavigate} title="Account"><span className="navigation-letter">@</span><span className="navigation-label">ACCOUNT</span></NavLink>
        <span
          className="navigation-role navigation-label"
          style={{
            backgroundColor: role === "ADMIN" ? "#7c3aed" : "#0284c7",
          }}
        >
          {role}
        </span>
        <div className="navigation-connectivity" title={isOnline ? "System Online" : "System Offline"}>
          <span className="status-dot" style={{ backgroundColor: isOnline ? "#22c55e" : "#ef4444" }}></span>
          <span className="navigation-label">{isOnline ? "SYSTEM ONLINE" : "SYSTEM OFFLINE"}</span>
        </div>
      </div>
    </nav>
  );
}

export default Navigation;
