import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { repairParkingSpot } from '../services/api';

function ParkingGrid({ initialSpots = [], onRefresh }) {
  const navigate = useNavigate();
  const [selectedSpot, setSelectedSpot] = useState(null);
  const [repairing, setRepairing] = useState(false);
  const [repairMsg, setRepairMsg] = useState(null);

  const spots =
    initialSpots.length > 0
      ? initialSpots
      : Array.from({ length: 30 }, (_, index) => {
          const num = index + 1;
          return {
            name: `S${num}`,
            number: num,
            status: 'free',
            zone: 'Zone 1',
          };
        });

  const handleSpotClick = (spot) => {
    setSelectedSpot(spot);
    setRepairMsg(null);
  };

  const handleRepair = async (spotName) => {
    setRepairing(true);
    setRepairMsg(null);
    try {
      await repairParkingSpot(spotName);
      setRepairMsg(`Successfully repaired ${spotName}!`);
      if (onRefresh) onRefresh();
    } catch (err) {
      setRepairMsg(`Repair failed: ${err.message}`);
    } finally {
      setRepairing(false);
    }
  };

  const freeCount = spots.filter((s) => s.status === 'free').length;

  return (
    <section className="parking-section">
      <div className="section-header">
        <div>
          <h2>Parking Spaces</h2>
          <p>
            Zone 1 (S1 - S30) · Live availability ({freeCount} / {spots.length} Free)
          </p>
        </div>

        {/* Zone 1 indicator */}
        <div style={{ display: 'flex', alignItems: 'center' }}>
          <span
            style={{
              padding: '6px 14px',
              borderRadius: '6px',
              fontSize: '0.8rem',
              fontWeight: 700,
              backgroundColor: '#1e293b',
              color: 'white',
              letterSpacing: '0.04em',
            }}
          >
            Zone 1
          </span>
        </div>

        <div className="parking-legend">
          <span>
            <i className="legend-dot free"></i>
            Free
          </span>

          <span>
            <i className="legend-dot occupied"></i>
            Occupied
          </span>

          <span>
            <i className="legend-dot maintenance"></i>
            Maintenance
          </span>
        </div>
      </div>

      {/* Spot Detail & Action Banner */}
      {selectedSpot && (
        <div
          style={{
            marginBottom: '20px',
            padding: '16px 20px',
            borderRadius: '10px',
            backgroundColor: '#f8fafc',
            border: '1px solid #cbd5e1',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            flexWrap: 'wrap',
            gap: '12px',
          }}
        >
          <div>
            <strong style={{ fontSize: '1.1rem', marginRight: '10px' }}>{selectedSpot.name}</strong>
            <span style={{ fontSize: '0.85rem', color: '#64748b', marginRight: '12px' }}>({selectedSpot.zone})</span>
            <span
              style={{
                textTransform: 'uppercase',
                padding: '3px 8px',
                borderRadius: '6px',
                fontSize: '0.8rem',
                fontWeight: 600,
                backgroundColor:
                  selectedSpot.status === 'free'
                    ? '#dcfce7'
                    : selectedSpot.status === 'occupied'
                    ? '#fee2e2'
                    : '#fef3c7',
                color:
                  selectedSpot.status === 'free'
                    ? '#166534'
                    : selectedSpot.status === 'occupied'
                    ? '#991b1b'
                    : '#92400e',
              }}
            >
              {selectedSpot.status}
            </span>
            {selectedSpot.plate && (
              <span
                onClick={() => navigate(`/vehicles/${encodeURIComponent(selectedSpot.plate)}`)}
                style={{ marginLeft: '12px', color: '#2563eb', fontWeight: 600, cursor: 'pointer', textDecoration: 'underline' }}
                title="View vehicle details"
              >
                Vehicle: {selectedSpot.plate} ↗
              </span>
            )}
            {repairMsg && (
              <span style={{ marginLeft: '16px', color: repairMsg.includes('failed') ? '#ef4444' : '#10b981', fontWeight: 500 }}>
                {repairMsg}
              </span>
            )}
          </div>

          <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
            <button
              type="button"
              disabled={repairing}
              onClick={() => handleRepair(selectedSpot.name)}
              style={{
                padding: '6px 14px',
                backgroundColor: '#f59e0b',
                color: 'white',
                border: 'none',
                borderRadius: '6px',
                cursor: repairing ? 'not-allowed' : 'pointer',
                fontWeight: 600,
                fontSize: '0.85rem',
              }}
            >
              {repairing ? 'Repairing...' : 'Repair Spot'}
            </button>
            <button
              type="button"
              onClick={() => setSelectedSpot(null)}
              style={{
                background: 'transparent',
                border: 'none',
                cursor: 'pointer',
                color: '#64748b',
                fontSize: '0.85rem',
              }}
            >
              ✕ Close
            </button>
          </div>
        </div>
      )}

      <div className="parking-grid">
        {spots.map((spot) => (
          <div
            key={spot.name}
            onClick={() => handleSpotClick(spot)}
            className={`parking-spot ${spot.status}`}
            style={{
              cursor: 'pointer',
              borderWidth: selectedSpot?.name === spot.name ? '3px' : '2px',
              borderColor: selectedSpot?.name === spot.name ? '#3b82f6' : undefined,
            }}
            title={`Click to inspect or repair ${spot.name}`}
          >
            <strong>{spot.name}</strong>
            <span style={{ fontSize: '0.75rem' }}>{spot.status}</span>
            {spot.plate && (
              <span style={{ fontSize: '0.7rem', fontWeight: 600, opacity: 0.9 }}>
                {spot.plate}
              </span>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

export default ParkingGrid;