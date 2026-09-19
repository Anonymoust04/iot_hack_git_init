import { useState } from "react";
import Navigation from "../Navigation.jsx";

function Layout({ children }) {
  const [sidebarExpanded, setSidebarExpanded] = useState(() => window.innerWidth > 760);

  return (
    <div className={`app-shell${sidebarExpanded ? " sidebar-expanded" : " sidebar-collapsed"}`}>
      <Navigation expanded={sidebarExpanded} onToggle={() => setSidebarExpanded((value) => !value)} onNavigate={() => { if (window.innerWidth <= 760) setSidebarExpanded(false); }} />

      <main className="page-content">
        {children}
      </main>
    </div>
  );
}

export default Layout;
