import "../styles/Dashboard.css";

import ParkingGrid from '../components/ParkingGrid'
import GateControl from '../components/GateControl'
import RecentActivity from '../components/RecentActivity'
import { usePolling } from '../hooks/usePolling'
import { getBackendStatus, getDashboardStats } from '../services/api'

function Dashboard() {
  const { data: stats } = usePolling(getDashboardStats)
  const { data: backendOnline } = usePolling(getBackendStatus)
  const dashboardStats = stats ?? { totalSpaces: 0, availableSpaces: 0, occupiedSpaces: 0, carsInside: 0 }

  // Only once real numbers have loaded (and the park has spots), not while loading
  const isCarParkFull = stats !== null && stats.totalSpaces > 0 && stats.availableSpaces === 0

  return (
    <div className="dashboard">

      {/* Header */}
      <header className="dashboard-header">
        <div>
          <h1>Parking Dashboard</h1>
          <p>Monitor and manage the car park in real time</p>
        </div>

        <div className="system-status">
          <span className="status-dot"></span>
          {backendOnline ? 'System Online' : 'System Offline'}
        </div>
      </header>

      {/* Full Car Park Warning */}
      {isCarParkFull && (
        <div className="capacity-warning">
          <div>
            <strong>Car Park Full</strong>
            <p>
              There are currently no available parking spaces.
              New vehicles should not be admitted.
            </p>
          </div>

          <span className="warning-badge">
            0 Spaces Available
          </span>
        </div>
      )}

      {/* Summary Statistics */}
      <section className="stats">

        <div className="stat-card">
          <p>Total Spaces</p>
          <h2>{dashboardStats.totalSpaces}</h2>
        </div>

        <div className="stat-card">
          <p>Available</p>
          <h2>{dashboardStats.availableSpaces}</h2>
        </div>

        <div className="stat-card">
          <p>Occupied</p>
          <h2>{dashboardStats.occupiedSpaces}</h2>
        </div>

        <div className="stat-card">
          <p>Cars Inside</p>
          <h2>{dashboardStats.carsInside}</h2>
        </div>

      </section>

      <ParkingGrid />

      <GateControl />

      <RecentActivity />

    </div>
  )
}

export default Dashboard