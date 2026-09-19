import { usePolling } from '../hooks/usePolling'
import { getParkingSpots } from '../services/api'

function ParkingGrid() {
  const { data } = usePolling(getParkingSpots)
  const parkingSpots = data ?? []

  // Free / occupied per zone, e.g. "ZONE1: 24 free · 6 occupied"
  const zones = [...new Set(parkingSpots.map((spot) => spot.zone))]
  const zoneSummary = zones
    .map((zone) => {
      const spots = parkingSpots.filter((spot) => spot.zone === zone)
      const free = spots.filter((spot) => spot.status === 'free').length
      const occupied = spots.filter((spot) => spot.status === 'occupied').length
      return `${zone}: ${free} free · ${occupied} occupied`
    })
    .join('  |  ')

  return (
    <section className="parking-section">
      <div className="section-header">
        <div>
          <h2>Parking Spaces</h2>
          <p>{zoneSummary || 'Loading'} · Live parking availability</p>
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