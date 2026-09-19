import { parkingSpots } from '../data/mockData'

function ParkingGrid() {
  return (
    <section className="parking-section">
      <div className="section-header">
        <div>
          <h2>Parking Spaces</h2>
          <p>ZONE1 · Live parking availability</p>
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

      <div className="parking-grid">
        {parkingSpots.map((spot) => (
          <div
            key={spot.name}
            className={`parking-spot ${spot.status}`}
          >
            <strong>{spot.name}</strong>
            <span>{spot.status}</span>
          </div>
        ))}
      </div>
    </section>
  )
}

export default ParkingGrid