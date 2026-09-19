import { useState } from 'react'
import { usePolling } from '../hooks/usePolling'
import { closeGate, getGates, openGate } from '../services/api'

// Card text for each gate role (which barrier is which comes from the backend .env)
const GATE_LABELS = {
  entrance: { title: 'Entrance Gate', description: 'Main vehicle entrance' },
  exit: { title: 'Exit Gate', description: 'Main vehicle exit' },
}

function GateControl() {
  // Live gate state from the backend (updated by the simulator's gate webhooks)
  const { data, error, refresh } = usePolling(getGates)
  const gates = data ?? []
  const [busyGate, setBusyGate] = useState(null)

  const toggleGate = async (gate) => {
    setBusyGate(gate.name)
    try {
      await (gate.isOpen ? closeGate(gate.name) : openGate(gate.name))
      await refresh()
    } catch (err) {
      alert(`Could not ${gate.isOpen ? 'close' : 'open'} ${gate.name}: ${err.message}`)
    } finally {
      setBusyGate(null)
    }
  }

  return (
    <section className="gate-section">
      <div className="section-header">
        <div>
          <h2>Gate Control</h2>
          <p>Monitor and control car park access</p>
        </div>
      </div>

      <div className="gate-grid">

        {gates.map((gate) => (
          <div className="gate-card" key={gate.name}>
            <div className="gate-info">
              <div>
                <h3>{GATE_LABELS[gate.role].title}</h3>
                <p>{GATE_LABELS[gate.role].description} · {gate.name}</p>
              </div>

              <span className={`gate-status ${gate.isOpen ? 'open' : 'closed'}`}>
                {gate.state}
              </span>
            </div>

            <button
              className={`gate-button ${gate.isOpen ? 'close' : 'open'}`}
              onClick={() => toggleGate(gate)}
              disabled={busyGate === gate.name}
            >
              {gate.isOpen ? 'Close Gate' : 'Open Gate'}
            </button>
          </div>
        ))}

        {data !== null && gates.length === 0 && (
          <p>No gates yet: start the simulator, then the backend syncs them.</p>
        )}
        {error && <p>Could not load gates: {error.message}</p>}

      </div>
    </section>
  )
}

export default GateControl
