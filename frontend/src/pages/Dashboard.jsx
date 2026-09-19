import '../styles/Dashboard.css'

import ParkingGrid from '../components/ParkingGrid'
import GateControl from '../components/GateControl'
import RecentActivity from '../components/RecentActivity'
import { dashboardStats } from '../data/mockData'

function Dashboard() {
  const isCarParkFull = dashboardStats.availableSpaces === 0

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
          System Online
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