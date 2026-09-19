import { usePolling } from '../hooks/usePolling'
import { getRecentActivity } from '../services/api'

function RecentActivity() {
  const { data } = usePolling(getRecentActivity)
  const recentActivities = data ?? []

  return (
    <section className="activity-section">
      <div className="section-header">
        <div>
          <h2>Recent Activity</h2>
          <p>Latest vehicle events in the car park</p>
        </div>

        <button className="view-all-button">View All</button>
      </div>

      <div className="table-container">
        <table className="activity-table">
          <thead>
            <tr>
              <th>Vehicle</th>
              <th>Event</th>
              <th>Parking Spot</th>
              <th>Time</th>
              <th>Status</th>
            </tr>
          </thead>

          <tbody>
            {recentActivities.map((activity) => (
              <tr key={activity.id}>
                <td className="plate-number">
                  {activity.plate}
                </td>

                <td>
                  {activity.event}
                </td>

                <td>
                  {activity.spot}
                </td>

                <td>
                  {activity.time}
                </td>

                <td>
                  <span
                    className={`activity-status ${activity.status.toLowerCase()}`}
                  >
                    {activity.status}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

export default RecentActivity