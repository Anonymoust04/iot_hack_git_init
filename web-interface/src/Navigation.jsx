import { NavLink } from "react-router-dom";
import { usePolling } from "./hooks/usePolling";
import { getBackendStatus } from "./services/api";

function Navigation() {
  const { data: backendOnline } = usePolling(getBackendStatus);

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

        <NavLink to="/account">
          ACCOUNT
        </NavLink>

      </div>

      <div className="navigation-status">
        <span className="status-dot"></span>
        {backendOnline ? "SYSTEM ONLINE" : "SYSTEM OFFLINE"}
      </div>
    </nav>
  );
}

export default Navigation;