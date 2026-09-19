import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { getRecentActivity } from '../services/api';

function RecentActivity() {
  const navigate = useNavigate();
  const [activities, setActivities] = useState([]);
  const [activeTab, setActiveTab] = useState('all');
  const [loading, setLoading] = useState(true);
  const [lastRefreshed, setLastRefreshed] = useState(null);

  const fetchActivities = async () => {
    try {
      const data = await getRecentActivity();
      if (Array.isArray(data)) {
        setActivities(data);
        setLastRefreshed(new Date().toLocaleTimeString());
      }
    } catch (err) {
      console.warn('Failed to load recent activity:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchActivities();
    const interval = setInterval(fetchActivities, 5000);
    return () => clearInterval(interval);
  }, []);

  // Filter based on selected tab
  const filteredActivities = activities.filter((act) => {
    if (activeTab === 'all') return true;
    if (activeTab === 'car') return act.category === 'car' || (!act.category && !act.plate.startsWith('['));
    if (activeTab === 'gate') return act.category === 'gate' || (act.plate && act.plate.toUpperCase().includes('GATE'));
    if (activeTab === 'alert') return act.category === 'alert' || act.status === 'Penalty' || act.status === 'Alert';
    return true;
  });

  const getStatusColor = (status) => {
    const s = (status || '').toLowerCase();
    if (s.includes('parked')) return { bg: '#dcfce7', text: '#15803d' };
    if (s.includes('queue') || s.includes('transit')) return { bg: '#dbeafe', text: '#1d4ed8' };
    if (s.includes('departing') || s.includes('processing')) return { bg: '#fef3c7', text: '#b45309' };
    if (s.includes('departed')) return { bg: '#f1f5f9', text: '#475569' };
    if (s.includes('open')) return { bg: '#d1fae5', text: '#065f46' };
    if (s.includes('closed')) return { bg: '#f3f4f6', text: '#374151' };
    if (s.includes('penalty') || s.includes('alert')) return { bg: '#fee2e2', text: '#b91c1c' };
    return { bg: '#e0e7ff', text: '#3730a3' };
  };

  return (
    <section className="activity-section">
      <div className="section-header" style={{ flexWrap: 'wrap', gap: '12px' }}>
        <div>
          <h2>Recent Activity</h2>
          <p style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span
              style={{
                display: 'inline-block',
                width: '8px',
                height: '8px',
                borderRadius: '50%',
                backgroundColor: '#10b981',
              }}
            ></span>
            <span>Live event feed from simulator webhooks · Updating every 5s</span>
            {lastRefreshed && <span style={{ opacity: 0.6 }}>({lastRefreshed})</span>}
          </p>
        </div>

        <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
          <button
            type="button"
            onClick={fetchActivities}
            style={{
              padding: '6px 12px',
              borderRadius: '6px',
              border: '1px solid #d1d5db',
              background: 'white',
              cursor: 'pointer',
              fontSize: '0.85rem',
              fontWeight: 500,
            }}
            title="Refresh feed immediately"
          >
            ↻ Refresh
          </button>

          <button
            type="button"
            className="view-all-button"
            onClick={() => navigate('/vehicles')}
            style={{ cursor: 'pointer' }}
          >
            View All Vehicles →
          </button>
        </div>
      </div>

      {/* Filter Tabs */}
      <div style={{ display: 'flex', gap: '8px', marginBottom: '16px', flexWrap: 'wrap' }}>
        {[
          { key: 'all', label: `All Events (${activities.length})` },
          { key: 'car', label: `Vehicle Moves (${activities.filter((a) => a.category === 'car' || !a.category).length})` },
          { key: 'gate', label: `Gates (${activities.filter((a) => a.category === 'gate' || (a.plate && a.plate.toUpperCase().includes('GATE'))).length})` },
          { key: 'alert', label: `Alerts & Fines (${activities.filter((a) => a.category === 'alert').length})` },
        ].map((tab) => (
          <button
            key={tab.key}
            type="button"
            onClick={() => setActiveTab(tab.key)}
            style={{
              padding: '6px 14px',
              borderRadius: '6px',
              fontSize: '0.8rem',
              fontWeight: 600,
              cursor: 'pointer',
              border: '1px solid #cbd5e1',
              backgroundColor: activeTab === tab.key ? '#1e293b' : '#f8fafc',
              color: activeTab === tab.key ? 'white' : '#475569',
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div className="table-container">
        <table className="activity-table">
          <thead>
            <tr>
              <th>Vehicle / Source</th>
              <th>Event Description</th>
              <th>Location / Spot</th>
              <th>Time</th>
              <th>Status</th>
            </tr>
          </thead>

          <tbody>
            {filteredActivities.length > 0 ? (
              filteredActivities.map((activity) => {
                const isCar = !activity.plate.startsWith('[') && activity.category !== 'gate';
                const colors = getStatusColor(activity.status);

                return (
                  <tr
                    key={activity.id}
                    onClick={() => {
                      if (isCar) {
                        navigate(`/vehicles/${encodeURIComponent(activity.plate)}`);
                      }
                    }}
                    style={{
                      cursor: isCar ? 'pointer' : 'default',
                      transition: 'background-color 0.15s ease',
                    }}
                    title={isCar ? `Click to inspect vehicle ${activity.plate}` : undefined}
                  >
                    <td className="plate-number">
                      {isCar ? (
                        <span style={{ color: '#2563eb', textDecoration: 'underline' }}>
                          {activity.plate} ↗
                        </span>
                      ) : (
                        <span style={{ color: '#475569', fontWeight: 600 }}>{activity.plate}</span>
                      )}
                    </td>

                    <td>
                      <span style={{ fontWeight: 500 }}>{activity.event}</span>
                      {activity.sequenceId && (
                        <span style={{ marginLeft: '8px', fontSize: '0.75rem', color: '#94a3b8' }}>
                          #seq:{activity.sequenceId}
                        </span>
                      )}
                    </td>

                    <td>
                      <strong style={{ color: '#334155' }}>{activity.spot}</strong>
                    </td>

                    <td style={{ color: '#64748b' }}>{activity.time}</td>

                    <td>
                      <span
                        className="activity-status"
                        style={{
                          backgroundColor: colors.bg,
                          color: colors.text,
                        }}
                      >
                        {activity.status}
                      </span>
                    </td>
                  </tr>
                );
              })
            ) : (
              <tr>
                <td colSpan="5" style={{ textAlign: 'center', padding: '32px', color: '#64748b' }}>
                  {loading ? 'Connecting to live simulator feed...' : 'No activity logged for this category yet.'}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default RecentActivity;