import { useState } from 'react'

function GateControl() {
  // Mock state for frontend development.
  // Backend/API will replace this later.
  const [entranceOpen, setEntranceOpen] = useState(false)
  const [exitOpen, setExitOpen] = useState(true)

  return (
    <section className="gate-section">
      <div className="section-header">
        <div>
          <h2>Gate Control</h2>
          <p>Monitor and control car park access</p>
        </div>
      </div>

      <div className="gate-grid">

        <div className="gate-card">
          <div className="gate-info">
            <div>
              <h3>Entrance Gate</h3>
              <p>Main vehicle entrance</p>
            </div>

            <span className={`gate-status ${entranceOpen ? 'open' : 'closed'}`}>
              {entranceOpen ? 'Open' : 'Closed'}
            </span>
          </div>

          <button
            className={`gate-button ${entranceOpen ? 'close' : 'open'}`}
            onClick={() => setEntranceOpen(!entranceOpen)}
          >
            {entranceOpen ? 'Close Gate' : 'Open Gate'}
          </button>
        </div>

        <div className="gate-card">
          <div className="gate-info">
            <div>
              <h3>Exit Gate</h3>
              <p>Main vehicle exit</p>
            </div>

            <span className={`gate-status ${exitOpen ? 'open' : 'closed'}`}>
              {exitOpen ? 'Open' : 'Closed'}
            </span>
          </div>

          <button
            className={`gate-button ${exitOpen ? 'close' : 'open'}`}
            onClick={() => setExitOpen(!exitOpen)}
          >
            {exitOpen ? 'Close Gate' : 'Open Gate'}
          </button>
        </div>

      </div>
    </section>
  )
}

export default GateControl