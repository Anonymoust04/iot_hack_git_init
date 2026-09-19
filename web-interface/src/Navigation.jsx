import { NavLink } from "react-router-dom";
import { usePolling } from "./hooks/usePolling";
import { getBackendStatus } from "./services/api";

function Navigation() {
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
    <nav className="navigation">
      <div className="navigation-brand">
        <span className="brand-mark">P</span>
        <span>PARK//CONTROL</span>
      </div>

      <div className="navigation-links">
        <NavLink to="/dashboard">
          DASHBOARD
        </NavLink>

        <NavLink to="/vehicles">
          VEHICLES
        </NavLink>

        {role === "ADMIN" && (
          <NavLink to="/admin/users">
            ADMIN
          </NavLink>
        )}

        <NavLink to="/account">
          ACCOUNT
        </NavLink>
      </div>

      <div className="navigation-status" style={{ display: "flex", alignItems: "center", gap: "10px" }}>
        <span
          style={{
            fontSize: "0.75rem",
            fontWeight: 700,
            padding: "2px 8px",
            borderRadius: "4px",
            backgroundColor: role === "ADMIN" ? "#7c3aed" : "#0284c7",
            color: "white",
            letterSpacing: "0.05em",
          }}
        >
          {role}
        </span>
        <span
          className="status-dot"
          style={{ backgroundColor: isOnline ? "#22c55e" : "#ef4444" }}
        ></span>
        <span>{isOnline ? "SYSTEM ONLINE" : "SYSTEM OFFLINE"}</span>
      </div>
    </nav>
  );
}

export default Navigation;
