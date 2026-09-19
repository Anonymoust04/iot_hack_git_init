import { NavLink } from "react-router-dom";

function Navigation() {
  return (
    <nav className="navigation">
      <div className="navigation-brand">
        <span className="brand-mark">P</span>
        <span>PARK//CONTROL</span>
      </div>

      <div className="navigation-links">
        <NavLink to="/vehicles">
          VEHICLES
        </NavLink>

        <NavLink to="/account">
          ACCOUNT
        </NavLink>
      </div>

      <div className="navigation-status">
        <span className="status-dot"></span>
        SYSTEM ONLINE
      </div>
    </nav>
  );
}

export default Navigation;
