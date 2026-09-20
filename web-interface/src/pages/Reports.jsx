import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { getDailyReport, getFinancialReport, getPaymentRecords } from "../services/api";
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
  ["Penalty Income", "penaltyIncome"],
  ["Gross Revenue", "totalRevenue"],
];

function localToday() {
  return new Date().toISOString().slice(0, 10);
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
  const [payments, setPayments] = useState([]);
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
      Promise.all([getFinancialReport(date), getPaymentRecords(date)])
        .then(([report, records]) => { if (active) { setFinancialReport(report || null); setPayments(records || []); setError(null); } })
        .catch((err) => {
          if (!active) return;
          setFinancialReport(null);
          setPayments([]);
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
        <label className="reports-date">Report date (UTC) <input type="date" value={date} onChange={(event) => { setDailyReport(null); setFinancialReport(null); setPayments([]); setError(null); setDate(event.target.value); }} /></label>
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
            <p className="reports-note">Gross revenue = parking fees + EV extra fees + penalty income.</p>
            {hasFinancialActivity ? (
              <div className="reports-table-scroll"><table className="reports-table">
                <thead><tr><th>CATEGORY</th><th>TRANSACTIONS</th><th>AMOUNT</th></tr></thead>
                <tbody>{financialRows.map((row, index) => (
                  <tr key={row.category ?? index}><td>{row.category ?? "—"}</td><td>{row.transactions ?? "—"}</td><td>{money(row.amount)}</td></tr>
                ))}</tbody>
              </table></div>
            ) : <p className="reports-empty">No paid charges or penalties for this date.</p>}
          </section>
          <section className="reports-panel">
            <h2>Payment Records (latest 100)</h2>
            {payments.length ? (
              <div className="reports-table-scroll"><table className="reports-table">
                <thead><tr><th>PAID AT (UTC)</th><th>PLATE</th><th>TYPE</th><th>PARKING</th><th>EV EXTRA</th><th>TOTAL</th></tr></thead>
                <tbody>{payments.map((payment) => (
                  <tr key={payment.id}>
                    <td>{payment.paid_at?.replace("T", " ") ?? "—"}</td>
                    <td>{payment.car_plate}</td><td>{payment.car_type ?? "—"}</td>
                    <td>{money(payment.parking_fee)}</td><td>{money(payment.ev_fee)}</td><td>{money(payment.total_amount)}</td>
                  </tr>
                ))}</tbody>
              </table></div>
            ) : <p className="reports-empty">No accepted car payments for this date.</p>}
          </section>
          </>}
        </>
      )}
    </div>
  );
}

export default Reports;
