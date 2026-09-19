import { useEffect, useState } from "react";
import { getAuditLogs, REFRESH_MS } from "../services/api";
import "../styles/Audit.css";

const filters = [
  { key: "all", label: "All Events" },
  { key: "user", label: "User Actions" },
  { key: "system", label: "System Events" },
  { key: "failed", label: "Failures" },
];

function Audit() {
  const [events, setEvents] = useState([]);
  const [filter, setFilter] = useState("all");
  const [error, setError] = useState(null);

  useEffect(() => {
    let active = true;
    const load = () => getAuditLogs()
      .then((items) => {
        if (!active) return;
        setEvents(Array.isArray(items) ? items : []);
        setError(null);
      })
      .catch((err) => {
        if (!active) return;
        // 403 = Operator: the audit log is Admin only
        setError(/403|forbidden|role/i.test(err.message) ? "Admin access required to view the audit log." : err.message);
      });
    load();
    const interval = setInterval(load, REFRESH_MS);   // keep it live, like the dashboard
    return () => { active = false; clearInterval(interval); };
  }, []);

  const isUserAction = (event) => event.category?.toLowerCase() === "user";
  const isFailed = (event) => event.result?.toLowerCase() === "failed";
  const userActions = events.filter(isUserAction).length;
  const systemEvents = events.filter((event) => event.category?.toLowerCase() === "system").length;
  const failedActions = events.filter(isFailed).length;
  const visibleEvents = events.filter((event) =>
    filter === "all" ||
    (filter === "user" && isUserAction(event)) ||
    (filter === "system" && event.category?.toLowerCase() === "system") ||
    (filter === "failed" && isFailed(event))
  );

  return (
    <div className="audit-page">
      <header className="audit-header">
        <h1>Audit Log</h1>
        <p>Track important actions, repairs, changes and system events.</p>
      </header>

      <section className="audit-summary" aria-label="Audit summary">
        <div className="audit-summary-card"><span>Total Events</span><strong>{events.length}</strong></div>
        <div className="audit-summary-card"><span>User Actions</span><strong>{userActions}</strong></div>
        <div className="audit-summary-card"><span>System Events</span><strong>{systemEvents}</strong></div>
        <div className="audit-summary-card failed"><span>Failed Actions</span><strong>{failedActions}</strong></div>
      </section>

      <section className="audit-panel">
        <h2>Event Records</h2>
        <div className="audit-filters" aria-label="Filter audit events">
          {filters.map((option) => (
            <button
              type="button"
              key={option.key}
              className={filter === option.key ? "active" : ""}
              aria-pressed={filter === option.key}
              onClick={() => setFilter(option.key)}
            >
              {option.label}
            </button>
          ))}
        </div>

        {visibleEvents.length ? (
          <div className="audit-table-scroll">
            <table className="audit-table">
              <thead><tr>
                <th>TIME</th><th>USER / SOURCE</th><th>ACTION</th><th>TARGET</th><th>DETAILS</th><th>RESULT</th>
              </tr></thead>
              <tbody>
                {visibleEvents.map((event, index) => (
                  <tr key={event.id ?? index}>
                    <td>{event.time ?? "—"}</td>
                    <td>{event.source ?? "—"}</td>
                    <td>{event.action ?? "—"}</td>
                    <td>{event.target ?? "—"}</td>
                    <td>{event.details ?? "—"}</td>
                    <td><span className={`audit-result ${event.result?.toLowerCase() || "unknown"}`}>{event.result ?? "—"}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <p className="audit-empty">{error || (events.length ? "No events match this filter" : "No audit events recorded yet")}</p>}
      </section>
    </div>
  );
}

export default Audit;
