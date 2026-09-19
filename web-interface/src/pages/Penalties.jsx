import { useEffect, useState } from "react";
import { getPenalties } from "../services/api";
import "../styles/Penalties.css";

function Penalties() {
  const [penalties, setPenalties] = useState([]);

  useEffect(() => {
    let active = true;
    getPenalties()
      .then((items) => {
        if (active) setPenalties(Array.isArray(items) ? items : []);
      })
      .catch(() => {
        if (active) setPenalties([]);
      });
    return () => { active = false; };
  }, []);

  const unresolved = penalties.filter((penalty) => penalty.status === "Unresolved").length;
  const resolved = penalties.filter((penalty) => penalty.status === "Resolved").length;
  const totalCost = penalties.reduce((total, penalty) => total + (Number(penalty.amount) || 0), 0);

  return (
    <div className="penalties-page">
      <header className="penalties-header">
        <h1>Penalties</h1>
        <p>Track penalties caused by operational mistakes or rule violations.</p>
      </header>

      <section className="penalties-summary" aria-label="Penalty summary">
        <div className="penalties-summary-card"><span>Total Penalties</span><strong>{penalties.length}</strong></div>
        <div className="penalties-summary-card"><span>Total Cost</span><strong>{totalCost ? totalCost.toFixed(2) : "0"}</strong></div>
        <div className="penalties-summary-card attention"><span>Unresolved</span><strong>{unresolved}</strong></div>
        <div className="penalties-summary-card resolved"><span>Resolved</span><strong>{resolved}</strong></div>
      </section>

      <section className="penalties-panel">
        <h2>Penalty Records</h2>
        {penalties.length ? (
          <div className="penalties-table-scroll">
            <table className="penalties-table">
              <thead><tr>
                <th>TIME</th><th>TYPE</th><th>DESCRIPTION</th><th>VEHICLE / COMPONENT</th><th>AMOUNT</th><th>STATUS</th>
              </tr></thead>
              <tbody>
                {penalties.map((penalty, index) => (
                  <tr key={penalty.id ?? index}>
                    <td>{penalty.time ?? "—"}</td>
                    <td>{penalty.type ?? "—"}</td>
                    <td>{penalty.description ?? "—"}</td>
                    <td>{penalty.subject ?? "—"}</td>
                    <td>{penalty.amount ?? "—"}</td>
                    <td><span className={`penalty-status ${penalty.status === "Resolved" ? "resolved" : "unresolved"}`}>{penalty.status ?? "—"}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <p className="penalties-empty" style={{ textAlign: "center" }}>No penalties recorded</p>}
      </section>
    </div>
  );
}

export default Penalties;
