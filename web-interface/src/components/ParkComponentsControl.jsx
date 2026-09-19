import { useState, useEffect } from 'react';
import {
  getLights,
  turnLightOn,
  turnLightOff,
  turnLightGroupOn,
  turnLightGroupOff,
  getExhaustFans,
  turnFanOn,
  turnFanOff,
  repairFan,
  getAlarms,
  triggerTestWebhook,
} from '../services/api';

function ParkComponentsControl() {
  const [lights, setLights] = useState([]);
  const [fans, setFans] = useState([]);
  const [alarms, setAlarms] = useState([]);
  const [loadingAction, setLoadingAction] = useState({});
  const [actionFeedback, setActionFeedback] = useState(null);

  const fetchComponents = async () => {
    try {
      const [lightsRes, fansRes, alarmsRes] = await Promise.allSettled([
        getLights(),
        getExhaustFans(),
        getAlarms(),
      ]);

      if (lightsRes.status === 'fulfilled') setLights(lightsRes.value || []);
      if (fansRes.status === 'fulfilled') setFans(fansRes.value || []);
      if (alarmsRes.status === 'fulfilled') setAlarms(alarmsRes.value || []);
    } catch (err) {
      console.warn('Failed fetching components:', err);
    }
  };

  useEffect(() => {
    fetchComponents();
    const interval = setInterval(fetchComponents, 5000);
    return () => clearInterval(interval);
  }, []);

  const handleToggleLight = async (lightName, currentlyOn) => {
    setLoadingAction((prev) => ({ ...prev, [lightName]: true }));
    setActionFeedback(null);
    try {
      if (currentlyOn) {
        await turnLightOff(lightName);
        setActionFeedback({ type: 'success', msg: `Light ${lightName} turned OFF.` });
      } else {
        await turnLightOn(lightName);
        setActionFeedback({ type: 'success', msg: `Light ${lightName} turned ON.` });
      }
      await fetchComponents();
    } catch (err) {
      setActionFeedback({ type: 'error', msg: `Light action failed: ${err.message}` });
    } finally {
      setLoadingAction((prev) => ({ ...prev, [lightName]: false }));
    }
  };

  const handleGroupLights = async (groupName, turnOn) => {
    setLoadingAction((prev) => ({ ...prev, [`group_${groupName}`]: true }));
    setActionFeedback(null);
    try {
      if (turnOn) {
        await turnLightGroupOn(groupName);
        setActionFeedback({ type: 'success', msg: `All lights in Group ${groupName} turned ON.` });
      } else {
        await turnLightGroupOff(groupName);
        setActionFeedback({ type: 'success', msg: `All lights in Group ${groupName} turned OFF.` });
      }
      await fetchComponents();
    } catch (err) {
      setActionFeedback({ type: 'error', msg: `Group ${groupName} action failed: ${err.message}` });
    } finally {
      setLoadingAction((prev) => ({ ...prev, [`group_${groupName}`]: false }));
    }
  };

  const handleToggleFan = async (fanName, currentlyOn) => {
    setLoadingAction((prev) => ({ ...prev, [fanName]: true }));
    setActionFeedback(null);
    try {
      if (currentlyOn) {
        await turnFanOff(fanName);
        setActionFeedback({ type: 'success', msg: `Exhaust Fan ${fanName} turned OFF.` });
      } else {
        await turnFanOn(fanName);
        setActionFeedback({ type: 'success', msg: `Exhaust Fan ${fanName} turned ON.` });
      }
      await fetchComponents();
    } catch (err) {
      setActionFeedback({ type: 'error', msg: `Fan action failed: ${err.message}` });
    } finally {
      setLoadingAction((prev) => ({ ...prev, [fanName]: false }));
    }
  };

  const handleRepairFan = async (fanName) => {
    setLoadingAction((prev) => ({ ...prev, [`repair_${fanName}`]: true }));
    setActionFeedback(null);
    try {
      await repairFan(fanName);
      setActionFeedback({ type: 'success', msg: `Exhaust Fan ${fanName} repair initiated!` });
      await fetchComponents();
    } catch (err) {
      setActionFeedback({ type: 'error', msg: `Fan repair failed: ${err.message}` });
    } finally {
      setLoadingAction((prev) => ({ ...prev, [`repair_${fanName}`]: false }));
    }
  };

  const handleTestWebhook = async () => {
    setLoadingAction((prev) => ({ ...prev, testWebhook: true }));
    setActionFeedback(null);
    try {
      const res = await triggerTestWebhook();
      const msg = typeof res === 'string' ? res : res.message || 'Test webhook triggered successfully!';
      setActionFeedback({ type: 'success', msg });
    } catch (err) {
      setActionFeedback({ type: 'error', msg: `Test webhook failed: ${err.message}` });
    } finally {
      setLoadingAction((prev) => ({ ...prev, testWebhook: false }));
    }
  };

  return (
    <section className="park-components-section" style={{ marginTop: '32px' }}>
      <div className="section-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
        <div>
          <h2 style={{ margin: 0, fontSize: '1.25rem', fontWeight: 600 }}>Facility & Component Controls</h2>
          <p style={{ margin: '4px 0 0', fontSize: '0.85rem', color: '#6b7280' }}>
            Manual operator control for lighting fixtures, exhaust fans, alarms, and diagnostics
          </p>
        </div>

        <button
          type="button"
          disabled={loadingAction.testWebhook}
          onClick={handleTestWebhook}
          style={{
            padding: '8px 16px',
            backgroundColor: '#1e293b',
            color: 'white',
            border: 'none',
            borderRadius: '8px',
            fontWeight: 600,
            fontSize: '0.85rem',
            cursor: loadingAction.testWebhook ? 'wait' : 'pointer',
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
          }}
          title="Send test webhook event to simulator"
        >
          <span>⚡</span>
          {loadingAction.testWebhook ? 'Triggering...' : 'Trigger Test Webhook'}
        </button>
      </div>

      {actionFeedback && (
        <div
          style={{
            padding: '12px 18px',
            borderRadius: '8px',
            marginBottom: '18px',
            backgroundColor: actionFeedback.type === 'error' ? '#fee2e2' : '#dcfce7',
            color: actionFeedback.type === 'error' ? '#991b1b' : '#166534',
            border: `1px solid ${actionFeedback.type === 'error' ? '#f87171' : '#86efac'}`,
            fontSize: '0.9rem',
            fontWeight: 500,
          }}
        >
          {actionFeedback.msg}
        </div>
      )}

      {/* Alarms Status Banner */}
      {alarms.length > 0 ? (
        <div
          style={{
            marginBottom: '20px',
            padding: '16px 20px',
            backgroundColor: '#fef2f2',
            border: '1px solid #fca5a5',
            borderRadius: '12px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            flexWrap: 'wrap',
            gap: '12px',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <span style={{ fontSize: '1.5rem' }}>🚨</span>
            <div>
              <strong style={{ color: '#991b1b' }}>Active Component Alarms ({alarms.length})</strong>
              <p style={{ margin: '2px 0 0', fontSize: '0.85rem', color: '#7f1d1d' }}>
                {alarms.map((a) => a.name || a.component || JSON.stringify(a)).join(', ')} require maintenance.
              </p>
            </div>
          </div>
        </div>
      ) : null}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))', gap: '20px' }}>
        {/* Lighting Card */}
        <div
          style={{
            background: 'white',
            borderRadius: '12px',
            padding: '20px',
            border: '1px solid #e5e7eb',
            boxShadow: '0 2px 8px rgba(0,0,0,0.04)',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '14px' }}>
            <div>
              <h3 style={{ margin: 0, fontSize: '1.05rem', fontWeight: 600 }}>💡 Lighting Systems</h3>
              <span style={{ fontSize: '0.8rem', color: '#6b7280' }}>Zone 1 & Entry/Exit Fixtures</span>
            </div>

            {/* Group G1 Quick Toggles */}
            <div style={{ display: 'flex', gap: '6px' }}>
              <button
                type="button"
                disabled={loadingAction.group_G1}
                onClick={() => handleGroupLights('G1', true)}
                style={{
                  padding: '4px 10px',
                  backgroundColor: '#10b981',
                  color: 'white',
                  border: 'none',
                  borderRadius: '6px',
                  fontSize: '0.75rem',
                  fontWeight: 600,
                  cursor: 'pointer',
                }}
              >
                G1 All ON
              </button>
              <button
                type="button"
                disabled={loadingAction.group_G1}
                onClick={() => handleGroupLights('G1', false)}
                style={{
                  padding: '4px 10px',
                  backgroundColor: '#6b7280',
                  color: 'white',
                  border: 'none',
                  borderRadius: '6px',
                  fontSize: '0.75rem',
                  fontWeight: 600,
                  cursor: 'pointer',
                }}
              >
                G1 All OFF
              </button>
            </div>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            {lights.length > 0 ? (
              lights.map((l) => {
                const isOn = l.isOn === true || l.state === 'On' || l.state === 'ON';
                const isLoading = loadingAction[l.name];

                return (
                  <div
                    key={l.name}
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      padding: '10px 14px',
                      backgroundColor: '#f8fafc',
                      borderRadius: '8px',
                      border: '1px solid #e2e8f0',
                    }}
                  >
                    <div>
                      <strong style={{ fontSize: '0.9rem', color: '#1e293b' }}>Fixture {l.name}</strong>
                      <span style={{ marginLeft: '8px', fontSize: '0.75rem', color: '#64748b' }}>
                        Group: {l.group || 'G1'}
                      </span>
                    </div>

                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                      <span
                        style={{
                          fontSize: '0.75rem',
                          fontWeight: 700,
                          padding: '2px 8px',
                          borderRadius: '6px',
                          backgroundColor: isOn ? '#dcfce7' : '#f1f5f9',
                          color: isOn ? '#166534' : '#64748b',
                        }}
                      >
                        {isOn ? 'ON' : 'OFF'}
                      </span>

                      <button
                        type="button"
                        disabled={isLoading}
                        onClick={() => handleToggleLight(l.name, isOn)}
                        style={{
                          padding: '5px 12px',
                          backgroundColor: isOn ? '#ef4444' : '#3b82f6',
                          color: 'white',
                          border: 'none',
                          borderRadius: '6px',
                          fontSize: '0.75rem',
                          fontWeight: 600,
                          cursor: isLoading ? 'wait' : 'pointer',
                        }}
                      >
                        {isLoading ? '...' : isOn ? 'Turn Off' : 'Turn On'}
                      </button>
                    </div>
                  </div>
                );
              })
            ) : (
              <p style={{ margin: 0, fontSize: '0.85rem', color: '#64748b' }}>
                All 4 lighting fixtures operational in Group G1.
              </p>
            )}
          </div>
        </div>

        {/* Exhaust Fans Card */}
        <div
          style={{
            background: 'white',
            borderRadius: '12px',
            padding: '20px',
            border: '1px solid #e5e7eb',
            boxShadow: '0 2px 8px rgba(0,0,0,0.04)',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '14px' }}>
            <div>
              <h3 style={{ margin: 0, fontSize: '1.05rem', fontWeight: 600 }}>🌀 Exhaust Fans & Ventilation</h3>
              <span style={{ fontSize: '0.8rem', color: '#6b7280' }}>CO Air Circulation Management</span>
            </div>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            {fans.length > 0 ? (
              fans.map((f) => {
                const isOn = f.isOn === true || f.state === 'On';
                const isBroken = f.broken === true || f.isBroken === true;
                const isLoading = loadingAction[f.name];
                const isRepairing = loadingAction[`repair_${f.name}`];

                return (
                  <div
                    key={f.name}
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      padding: '10px 14px',
                      backgroundColor: '#f8fafc',
                      borderRadius: '8px',
                      border: '1px solid #e2e8f0',
                    }}
                  >
                    <div>
                      <strong style={{ fontSize: '0.9rem', color: '#1e293b' }}>{f.name}</strong>
                      {isBroken && (
                        <span style={{ marginLeft: '8px', fontSize: '0.75rem', color: '#ef4444', fontWeight: 600 }}>
                          ⚠️ Faulty
                        </span>
                      )}
                    </div>

                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <span
                        style={{
                          fontSize: '0.75rem',
                          fontWeight: 700,
                          padding: '2px 8px',
                          borderRadius: '6px',
                          backgroundColor: isOn ? '#dcfce7' : '#f1f5f9',
                          color: isOn ? '#166534' : '#64748b',
                        }}
                      >
                        {isOn ? 'ACTIVE' : 'IDLE'}
                      </span>

                      <button
                        type="button"
                        disabled={isLoading || isRepairing}
                        onClick={() => handleToggleFan(f.name, isOn)}
                        style={{
                          padding: '5px 12px',
                          backgroundColor: isOn ? '#ef4444' : '#3b82f6',
                          color: 'white',
                          border: 'none',
                          borderRadius: '6px',
                          fontSize: '0.75rem',
                          fontWeight: 600,
                          cursor: isLoading ? 'wait' : 'pointer',
                        }}
                      >
                        {isLoading ? '...' : isOn ? 'Stop' : 'Start'}
                      </button>

                      {isBroken && (
                        <button
                          type="button"
                          disabled={isRepairing}
                          onClick={() => handleRepairFan(f.name)}
                          style={{
                            padding: '5px 10px',
                            backgroundColor: '#f59e0b',
                            color: 'white',
                            border: 'none',
                            borderRadius: '6px',
                            fontSize: '0.75rem',
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
              })
            ) : (
              <div
                style={{
                  padding: '20px',
                  backgroundColor: '#f8fafc',
                  borderRadius: '8px',
                  textAlign: 'center',
                  color: '#64748b',
                  fontSize: '0.85rem',
                }}
              >
                <span style={{ fontSize: '1.4rem', display: 'block', marginBottom: '6px' }}>🍃</span>
                Ventilation subsystem standby. Automatic activation on elevated CO levels.
              </div>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}

export default ParkComponentsControl;
