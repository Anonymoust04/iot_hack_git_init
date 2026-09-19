import { useState, useEffect } from 'react';
import { getBarriers, openGate, closeGate, repairGate } from '../services/api';


function GateControl({ systemOnline = false }) {
  const [gates, setGates] = useState([]);

  const [loading, setLoading] = useState({});
  const [actionFeedback, setActionFeedback] = useState({});

  const refreshGates = async () => {
    if (!systemOnline) {
      setGates([]);
      return;
    }
    try {
      const data = await getBarriers();
      setGates(Array.isArray(data) ? data : []);
    } catch (err) {
      console.warn('Failed to fetch gate status:', err);
      setGates([]);
    }
  };

  useEffect(() => {
    refreshGates();
    const interval = setInterval(refreshGates, 5000);
    return () => clearInterval(interval);
  }, [systemOnline]);

  // Every gate the backend knows (Level 2: 3 entrances, 3 exits + others), entrances first
  const displayGates = systemOnline ? gates : [];

  const handleToggleGate = async (gateName, shouldOpen) => {
    setLoading((prev) => ({ ...prev, [gateName]: true }));
    setActionFeedback((prev) => ({ ...prev, [gateName]: null }));

    try {
      if (shouldOpen) {
        await openGate(gateName);
        setActionFeedback((prev) => ({ ...prev, [gateName]: { type: 'success', msg: `${gateName} opened` } }));
      } else {
        await closeGate(gateName);
        setActionFeedback((prev) => ({ ...prev, [gateName]: { type: 'success', msg: `${gateName} closed` } }));
      }
      await refreshGates();
    } catch (err) {
      setActionFeedback((prev) => ({ ...prev, [gateName]: { type: 'error', msg: err.message } }));
    } finally {
      setLoading((prev) => ({ ...prev, [gateName]: false }));
    }
  };

  const handleRepair = async (gateName) => {
    setLoading((prev) => ({ ...prev, [`${gateName}_repair`]: true }));
    setActionFeedback((prev) => ({ ...prev, [gateName]: null }));

    try {
      await repairGate(gateName);
      setActionFeedback((prev) => ({ ...prev, [gateName]: { type: 'success', msg: `${gateName} repaired` } }));
      await refreshGates();
    } catch (err) {
      setActionFeedback((prev) => ({ ...prev, [gateName]: { type: 'error', msg: err.message } }));
    } finally {
      setLoading((prev) => ({ ...prev, [`${gateName}_repair`]: false }));
    }
  };

  return (
    <section className="gate-section">
      <div className="section-header">
        <div>
          <h2>Gate Control</h2>
          <p>Operator manual override and live status for barrier gates</p>
        </div>
      </div>

      <div className="gate-grid">
        {displayGates.map((gate) => {
          const stateAvailable = systemOnline && gates.some((item) => item.name === gate.name);
          const isLoading = loading[gate.name];
          const isRepairing = loading[`${gate.name}_repair`];
          const feedback = actionFeedback[gate.name];

          return (
            <div className="gate-card" key={gate.name}>
              <div className="gate-info">
                <div>
                  <h3>{gate.title || gate.name}</h3>
                  <p>{gate.subtitle || (gate.name === 'GateA' ? 'Main vehicle entrance' : 'Main vehicle exit')}</p>
                </div>

                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '4px' }}>
                  <span className={`gate-status ${!stateAvailable ? 'unavailable' : gate.isOpen ? 'open' : 'closed'}`}>
                    {!stateAvailable ? 'Unavailable' : gate.isOpen ? 'Open' : 'Closed'}
                  </span>
                  {stateAvailable && gate.isBroken && (
                    <span style={{ fontSize: '0.75rem', color: '#ef4444', fontWeight: 600 }}>
                      ⚠️ Faulty
                    </span>
                  )}
                </div>
              </div>

              {stateAvailable && feedback && (
                <div
                  style={{
                    fontSize: '0.8rem',
                    marginBottom: '10px',
                    color: feedback.type === 'error' ? '#ef4444' : '#10b981',
                    fontWeight: 500,
                  }}
                >
                  {feedback.msg}
                </div>
              )}

              <div style={{ display: 'flex', gap: '10px' }}>
                <button
                  type="button"
                  disabled={!stateAvailable || isLoading || isRepairing}
                  className={`gate-button ${gate.isOpen ? 'close' : 'open'}`}
                  onClick={() => handleToggleGate(gate.name, !gate.isOpen)}
                  style={{ flex: 1, cursor: !stateAvailable ? 'not-allowed' : isLoading ? 'wait' : 'pointer' }}
                >
                  {!stateAvailable ? 'Control Unavailable' : isLoading ? 'Processing...' : gate.isOpen ? 'Close Gate' : 'Open Gate'}
                </button>

                {stateAvailable && gate.isBroken && (
                  <button
                    type="button"
                    disabled={isRepairing || isLoading}
                    onClick={() => handleRepair(gate.name)}
                    style={{
                      padding: '8px 16px',
                      backgroundColor: '#f59e0b',
                      color: 'white',
                      border: 'none',
                      borderRadius: '8px',
                      fontWeight: 600,
                      cursor: isRepairing ? 'wait' : 'pointer',
                    }}
                  >
                    {isRepairing ? '...' : 'Repair'}
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

export default GateControl;
