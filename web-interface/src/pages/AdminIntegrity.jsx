import { useCallback, useEffect, useState } from "react";
import {
  getDuplicateCalls,
  getIntegrityRequests,
  getIntegritySummary,
  REFRESH_MS,
} from "../services/api";
import "../styles/Integrity.css";

// Verdicts the backend can return. "unsigned_accepted" is a warning, not a drop:
// the shipped simulator never signs its webhooks.
const VERDICTS = [
  { key: "", label: "All Requests" },
  { key: "duplicate", label: "Duplicated" },
  { key: "tampered", label: "Tampered" },
  { key: "malformed", label: "Malformed" },
  { key: "invalid_field", label: "Invalid" },
  { key: "flood", label: "Flooding" },
  { key: "unsigned_accepted", label: "Unsigned" },
  { key: "unknown_target", label: "Unknown target" },
  { key: "gap", label: "Missed events" },
];

const VERDICT_LABEL = Object.fromEntries(VERDICTS.filter((v) => v.key).map((v) => [v.key, v.label]));

function Integrity() {
  const [summary, setSummary] = useState(null);
  const [requests, setRequests] = useState([]);
  const [duplicates, setDuplicates] = useState([]);
  const [verdict, setVerdict] = useState("");
  const [tab, setTab] = useState("requests");
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      const [nextSummary, nextRequests, nextDuplicates] = await Promise.all([
        getIntegritySummary(),
        getIntegrityRequests(verdict),
        getDuplicateCalls(),
      ]);
      setSummary(nextSummary);
      setRequests(nextRequests);
      setDuplicates(nextDuplicates);
      setError(null);
    } catch (err) {
      // 403 = Operator: this page is Admin only
      setError(
        /403|forbidden|role/i.test(err.message)
          ? "Admin access required to view rejected requests."
          : err.message
      );
    }
  }, [verdict]);

  useEffect(() => {
    let active = true;
    const run = () => { if (active) load(); };
    run();
    const interval = setInterval(run, REFRESH_MS);
    return () => { active = false; clearInterval(interval); };
  }, [load]);

  const accepted = summary?.accepted ?? 0;
  const rejected = summary?.rejected ?? 0;
  const total = summary?.total_requests ?? 0;
  const byVerdict = summary?.by_verdict || {};

  return (
    <div className="integrity-page">
      <header className="integrity-header">
        <h1>Request Integrity</h1>
        <p>
          Every call from the parking network is checked before it reaches the car
          system. Invalid, duplicated and tampered requests are dropped here and
          listed below.
        </p>
      </header>

      <section className="integrity-summary" aria-label="Integrity summary">
        <div className="integrity-card"><span>Requests Checked</span><strong>{total}</strong></div>
        <div className="integrity-card ok"><span>Accepted</span><strong>{accepted}</strong></div>
        <div className="integrity-card bad"><span>Rejected</span><strong>{rejected}</strong></div>
        <div className="integrity-card warn">
          <span>Duplicated Calls</span>
          <strong>{summary?.duplicate_copies ?? 0}</strong>
          <small>{summary?.duplicate_event_ids ?? 0} repeated event ids</small>
        </div>
      </section>

      {summary && Object.keys(byVerdict).length > 0 && (
        <section className="integrity-breakdown" aria-label="Rejections by reason">
          {Object.entries(byVerdict).map(([key, count]) => (
            <button
              type="button"
              key={key}
              className={verdict === key ? "active" : ""}
              onClick={() => { setVerdict(verdict === key ? "" : key); setTab("requests"); }}
            >
              <span>{VERDICT_LABEL[key] || key.replaceAll("_", " ")}</span>
              <strong>{count}</strong>
            </button>
          ))}
        </section>
      )}

      <section className="integrity-panel">
        <div className="integrity-tabs" role="tablist">
          <button type="button" role="tab" aria-selected={tab === "requests"}
                  className={tab === "requests" ? "active" : ""}
                  onClick={() => setTab("requests")}>
            Rejected &amp; Suspect Requests
          </button>
          <button type="button" role="tab" aria-selected={tab === "duplicates"}
                  className={tab === "duplicates" ? "active" : ""}
                  onClick={() => setTab("duplicates")}>
            Duplicated Calls ({duplicates.length})
          </button>
        </div>

        {tab === "requests" && (
          <>
            <div className="integrity-filters">
              {VERDICTS.map((option) => (
                <button
                  type="button"
                  key={option.key || "all"}
                  className={verdict === option.key ? "active" : ""}
                  aria-pressed={verdict === option.key}
                  onClick={() => setVerdict(option.key)}
                >
                  {option.label}
                </button>
              ))}
            </div>

            {requests.length ? (
              <div className="integrity-scroll">
                <table className="integrity-table">
                  <thead><tr>
                    <th>TIME</th><th>VERDICT</th><th>REASON</th><th>SOURCE</th>
                    <th>EVENT</th><th>PLATE</th><th>SPOT</th><th>DIGEST</th>
                  </tr></thead>
                  <tbody>
                    {requests.map((row) => (
                      <tr key={row.id}>
                        <td>{row.time}</td>
                        <td><span className={`integrity-tag ${row.severity}`}>
                          {VERDICT_LABEL[row.verdict] || row.verdict.replaceAll("_", " ")}
                        </span></td>
                        <td>{row.reason}</td>
                        <td>{row.source}</td>
                        <td>
                          {row.eventClass}
                          <small>id {row.eventId} · seq {row.sequenceId}</small>
                        </td>
                        <td>{row.plate}</td>
                        <td>{row.spot}</td>
                        <td><code>{row.digest}</code></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="integrity-empty">
                {error || "No rejected or suspect requests recorded."}
              </p>
            )}
          </>
        )}

        {tab === "duplicates" && (
          duplicates.length ? (
            <div className="integrity-scroll">
              <table className="integrity-table">
                <thead><tr>
                  <th>EVENT ID</th><th>EVENT</th><th>COPIES</th><th>PLATE</th>
                  <th>SPOT</th><th>FIRST SEEN</th><th>LAST SEEN</th><th>SOURCES</th>
                </tr></thead>
                <tbody>
                  {duplicates.map((row) => (
                    <tr key={row.id}>
                      <td><code>{row.eventId}</code></td>
                      <td>{row.eventClass}</td>
                      <td><span className="integrity-tag warning">{row.copies}×</span></td>
                      <td>{row.plate}</td>
                      <td>{row.spot}</td>
                      <td>{row.firstSeen}</td>
                      <td>{row.lastSeen}</td>
                      <td>{row.sources}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="integrity-empty">
              {error || "No duplicated calls received. Every event id has arrived once."}
            </p>
          )
        )}
      </section>
    </div>
  );
}

export default Integrity;
