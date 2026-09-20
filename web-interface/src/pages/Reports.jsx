import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { getDailyReport, getFinancialReport } from "../services/api";
import "../styles/Reports.css";

const dailyMetrics = [
  ["Vehicles Entered", "vehiclesEntered"],
  ["Vehicles Exited", "vehiclesExited"],
  ["Peak Occupancy", "peakOccupancy"],
  ["Current Occupancy", "currentOccupancy"],
  ["Operational Alerts", "operationalAlerts"],
  ["Component Failures", "componentFailures"],
  ["Maintenance Actions", "maintenanceActions"],
  ["Penalties", "penalties"],
];

const financialMetrics = [
  ["Parking Fees", "parkingRevenue"],
  ["EV Extra Fees", "evChargingRevenue"],
  ["Penalty Cost", "penaltyCost"],
  ["Gross Revenue", "totalRevenue"],
];

function localToday() {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

function money(value) {
  const amount = Number(value);
  return `RM ${Number.isFinite(amount) ? amount.toFixed(2) : "0.00"}`;
}

function statusTone(status) {
  const value = String(status || "").toLowerCase();
  if (["success", "normal", "completed"].includes(value)) return "positive";
  if (["warning", "alert", "penalty"].includes(value)) return "warning";
  if (["failed", "failure", "critical"].includes(value)) return "failed";
  return "neutral";
}

function Reports() {
  const location = useLocation();
  const tab = location.hash === "#financial" ? "financial" : "daily";
  const [date, setDate] = useState(localToday);
  const [dailyReport, setDailyReport] = useState(null);
  const [financialReport, setFinancialReport] = useState(null);
  const [error, setError] = useState(null);
  const [loadedKey, setLoadedKey] = useState(null);
  const requestKey = `${tab}:${date}`;
  const loading = Boolean(date && loadedKey !== requestKey);

  useEffect(() => {
    let active = true;
    if (!date) return () => { active = false; };
    if (tab === "daily") {
      getDailyReport(date)
        .then((report) => { if (active) { setDailyReport(report || null); setError(null); } })
        .catch((err) => { if (active) { setDailyReport(null); setError(err.message); } })
        .finally(() => { if (active) setLoadedKey(requestKey); });
    } else {
      getFinancialReport(date)
        .then((report) => { if (active) { setFinancialReport(report || null); setError(null); } })
        .catch((err) => {
          if (!active) return;
          setFinancialReport(null);
          // 403 = the account lacks the FINANCIAL_REPORTS authority
          setError(/403|forbidden|permission/i.test(err.message) ? "You do not have the financial report permission." : err.message);
        })
        .finally(() => { if (active) setLoadedKey(requestKey); });
    }
    return () => { active = false; };
  }, [date, tab, requestKey]);

  const dailyEvents = Array.isArray(dailyReport?.events) ? dailyReport.events : [];
  const financialRows = Array.isArray(financialReport?.breakdown) ? financialReport.breakdown : [];
  const hasFinancialActivity = financialRows.some((row) => Number(row.transactions) > 0);

  return (
    <div className="reports-page">
      <header className="reports-header">
        <h1>Reports</h1>
        <p>Daily operational and financial performance of the car park.</p>
      </header>

      <div className="reports-toolbar">
        <label className="reports-date">Report date <input type="date" value={date} onChange={(event) => { setDailyReport(null); setFinancialReport(null); setError(null); setDate(event.target.value); }} /></label>
      </div>

      {tab === "daily" ? (
        <>
          <section className="reports-summary" aria-label="Daily operations summary">
            {dailyMetrics.map(([label, field]) => (
              <div className="reports-summary-card" key={field}>
                <span>{label}</span>
                <strong>{dailyReport?.[field] ?? (field === "currentOccupancy" ? "Unavailable" : 0)}</strong>
              </div>
            ))}
          </section>
          <section className="reports-panel">
            <h2>Operational Summary</h2>
            {dailyEvents.length ? (
              <div className="reports-table-scroll"><table className="reports-table">
                <thead><tr><th>TIME</th><th>CATEGORY</th><th>EVENT</th><th>LOCATION / COMPONENT</th><th>STATUS</th></tr></thead>
                <tbody>{dailyEvents.map((event, index) => (
                  <tr key={event.id ?? index}>
                    <td>{event.time ?? "—"}</td><td>{event.category ?? "—"}</td><td>{event.event ?? "—"}</td>
                    <td>{event.location ?? "—"}</td><td><span className={`reports-status ${statusTone(event.status)}`}>{event.status ?? "—"}</span></td>
                  </tr>
                ))}</tbody>
              </table></div>
            ) : <p className="reports-empty" style={date === localToday() ? { textAlign: "center" } : undefined}>{error || (date === localToday() ? "No operational report data available for today" : "No operational report data available for selected date")}</p>}
          </section>
        </>
      ) : (
        <>
          {loading && <p className="reports-empty" role="status">Loading financial report…</p>}
          {!loading && error && <div className="reports-panel reports-access" role="alert">{error}</div>}
          {!loading && !error && financialReport && <>
          <section className="reports-summary" aria-label="Financial summary">
            {financialMetrics.map(([label, field]) => (
              <div className="reports-summary-card" key={field}><span>{label}</span><strong>{money(financialReport?.[field])}</strong></div>
            ))}
          </section>
          <section className="reports-panel">
            <h2>Financial Breakdown</h2>
            <p className="reports-note">Electric cars pay the parking fee plus an equal EV fee. Penalties are costs and are excluded from gross revenue.</p>
            {hasFinancialActivity ? (
              <div className="reports-table-scroll"><table className="reports-table">
                <thead><tr><th>CATEGORY</th><th>TRANSACTIONS</th><th>AMOUNT</th></tr></thead>
                <tbody>{financialRows.map((row, index) => (
                  <tr key={row.category ?? index}><td>{row.category ?? "—"}</td><td>{row.transactions ?? "—"}</td><td>{money(row.amount)}</td></tr>
                ))}</tbody>
              </table></div>
            ) : <p className="reports-empty">No paid charges or penalties for this date.</p>}
          </section>
          </>}
        </>
      )}
    </div>
  );
}

export default Reports;
