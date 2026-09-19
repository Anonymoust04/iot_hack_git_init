import { useState, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { getVehicleDetails, sendCarToDestination, chargeCar } from "../services/api";

function VehicleDetails() {
  const { plateNumber } = useParams();
  const navigate = useNavigate();

  const [vehicle, setVehicle] = useState(null);
  const [loading, setLoading] = useState(true);
  const [destination, setDestination] = useState("leavepark");
  const [parkingCost, setParkingCost] = useState("5.00");
  const [actionFeedback, setActionFeedback] = useState(null);
  const [actionLoading, setActionLoading] = useState(false);

  const loadVehicle = async () => {
    setLoading(true);
    try {
      const data = await getVehicleDetails(plateNumber);
      if (data && data.found) {
        setVehicle(data);
      } else {
        setVehicle({
          plateNumber,
          vehicleType: "Standard",
          brand: "Vehicle",
          model: "Standard",
          colour: "Silver",
          status: "Active / Recorded",
          parkingSpot: "—",
          entryTime: "Earlier",
          exitTime: "—",
          duration: "Active",
          charged: false,
        });
      }
    } catch (err) {
      console.warn("Could not load vehicle details:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (plateNumber) {
      loadVehicle();
    }
  }, [plateNumber]);

  const handleSendDestination = async () => {
    if (!destination) return;
    setActionLoading(true);
    setActionFeedback(null);
    try {
      await sendCarToDestination(plateNumber, destination);
      setActionFeedback({
        type: "success",
        msg: `Vehicle ${plateNumber} successfully directed to ${destination}!`,
      });
      await loadVehicle();
    } catch (err) {
      setActionFeedback({
        type: "error",
        msg: `Failed to direct vehicle: ${err.message}`,
      });
    } finally {
      setActionLoading(false);
    }
  };

  const handleChargeVehicle = async () => {
    setActionLoading(true);
    setActionFeedback(null);
    try {
      const pCost = parseFloat(parkingCost) || 0.0;
      await chargeCar(plateNumber, pCost, 0.0);
      setActionFeedback({
        type: "success",
        msg: `Payment of $${pCost.toFixed(2)} recorded for ${plateNumber}!`,
      });
      await loadVehicle();
    } catch (err) {
      setActionFeedback({
        type: "error",
        msg: `Charging failed: ${err.message}`,
      });
    } finally {
      setActionLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="vehicle-details-page" style={{ padding: "40px" }}>
        <p>Loading vehicle details for {plateNumber}...</p>
      </div>
    );
  }

  const currentVehicle = vehicle || {
    plateNumber,
    vehicleType: "Sedan",
    brand: "Vehicle",
    model: "Standard",
    colour: "Silver",
    status: "Active",
    parkingSpot: "—",
    entryTime: "Recently",
    exitTime: "—",
    duration: "—",
  };

  return (
    <div className="vehicle-details-page">
      {/* Page Header */}
      <div className="details-header">
        <div>
          <p className="eyebrow">VEHICLE RECORD</p>
          <h1>{currentVehicle.plateNumber}</h1>
          <p>Detailed vehicle information and real-time simulator controls.</p>
        </div>

        <span className="vehicle-status">
          <span className="status-dot"></span>
          {currentVehicle.status || "Parked"}
        </span>
      </div>

      {/* Action Feedback Banner */}
      {actionFeedback && (
        <div
          style={{
            margin: "20px 0",
            padding: "16px 20px",
            borderRadius: "10px",
            backgroundColor: actionFeedback.type === "success" ? "#dcfce7" : "#fee2e2",
            color: actionFeedback.type === "success" ? "#166534" : "#991b1b",
            fontWeight: 500,
            border: `1px solid ${actionFeedback.type === "success" ? "#86efac" : "#fca5a5"}`,
          }}
        >
          {actionFeedback.msg}
        </div>
      )}

      {/* Vehicle Overview */}
      <section className="details-card vehicle-overview">
        <div className="vehicle-icon">🚗</div>

        <div className="vehicle-main-info">
          <p className="eyebrow">VEHICLE</p>
          <h2>
            {currentVehicle.brand} {currentVehicle.model}
          </h2>
          <p>{currentVehicle.vehicleType}</p>
        </div>

        <div className="plate-display">
          <span>NUMBER PLATE</span>
          <strong>{currentVehicle.plateNumber}</strong>
        </div>
      </section>

      {/* Interactive Vehicle Actions (From main.py: /car/{name}/goto and /car/{name}/charge) */}
      <section className="details-card">
        <div className="card-heading">
          <p className="eyebrow">OPERATOR CONTROLS</p>
          <h2>Direct Vehicle & Billing Actions</h2>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 280px), 1fr))", gap: "20px", marginTop: "16px" }}>
          {/* Direct Car */}
          <div style={{ background: "#f8fafc", padding: "18px", borderRadius: "10px", border: "1px solid #e2e8f0" }}>
            <h3 style={{ margin: "0 0 10px", fontSize: "1rem" }}>Direct Car to Destination</h3>
            <p style={{ margin: "0 0 14px", fontSize: "0.85rem", color: "#64748b" }}>
              Send vehicle to a designated spot or instruct it to leave the park.
            </p>

            <div style={{ display: "flex", flexWrap: "wrap", gap: "10px" }}>
              <select
                value={destination}
                onChange={(e) => setDestination(e.target.value)}
                style={{
                  padding: "8px 12px",
                  borderRadius: "8px",
                  border: "1px solid #cbd5e1",
                  flex: 1,
                  minWidth: 0,
                  fontSize: "0.9rem",
                }}
              >
                <option value="leavepark">leavepark (Exit Car Park)</option>
                {Array.from({ length: 30 }, (_, i) => `S${i + 1}`).map((s) => (
                  <option key={s} value={s}>
                    Spot {s}
                  </option>
                ))}
              </select>

              <button
                type="button"
                disabled={actionLoading}
                onClick={handleSendDestination}
                style={{
                  padding: "8px 16px",
                  backgroundColor: "#2563eb",
                  color: "white",
                  border: "none",
                  borderRadius: "8px",
                  fontWeight: 600,
                  cursor: actionLoading ? "wait" : "pointer",
                }}
              >
                {actionLoading ? "Sending..." : "Dispatch →"}
              </button>
            </div>
          </div>

          {/* Charge Car */}
          <div style={{ background: "#f8fafc", padding: "18px", borderRadius: "10px", border: "1px solid #e2e8f0" }}>
            <h3 style={{ margin: "0 0 10px", fontSize: "1rem" }}>Process Parking Fee</h3>
            <p style={{ margin: "0 0 14px", fontSize: "0.85rem", color: "#64748b" }}>
              Charge the vehicle for its parking duration before exit.
            </p>

            <div style={{ display: "flex", flexWrap: "wrap", gap: "10px", alignItems: "center" }}>
              <span style={{ fontWeight: 600, color: "#475569" }}>$</span>
              <input
                type="number"
                step="0.50"
                min="0"
                value={parkingCost}
                onChange={(e) => setParkingCost(e.target.value)}
                style={{
                  width: "100px",
                  padding: "8px 10px",
                  borderRadius: "8px",
                  border: "1px solid #cbd5e1",
                  fontSize: "0.9rem",
                }}
              />

              <button
                type="button"
                disabled={actionLoading}
                onClick={handleChargeVehicle}
                style={{
                  padding: "8px 16px",
                  backgroundColor: "#10b981",
                  color: "white",
                  border: "none",
                  borderRadius: "8px",
                  fontWeight: 600,
                  cursor: actionLoading ? "wait" : "pointer",
                  flex: 1,
                }}
              >
                {actionLoading ? "Processing..." : "Charge Vehicle"}
              </button>
            </div>
          </div>
        </div>
      </section>

      {/* Vehicle Information */}
      <section className="details-card">
        <div className="card-heading">
          <p className="eyebrow">VEHICLE INFORMATION</p>
          <h2>Vehicle Details</h2>
        </div>

        <div className="details-grid">
          <div className="detail-item">
            <span>Number Plate</span>
            <strong>{currentVehicle.plateNumber}</strong>
          </div>

          <div className="detail-item">
            <span>Vehicle Type</span>
            <strong>{currentVehicle.vehicleType}</strong>
          </div>

          <div className="detail-item">
            <span>Brand</span>
            <strong>{currentVehicle.brand}</strong>
          </div>

          <div className="detail-item">
            <span>Model</span>
            <strong>{currentVehicle.model}</strong>
          </div>

          <div className="detail-item">
            <span>Colour</span>
            <strong>{currentVehicle.colour}</strong>
          </div>

          <div className="detail-item">
            <span>Status</span>
            <strong>{currentVehicle.status}</strong>
          </div>
        </div>
      </section>

      {/* Parking Information */}
      <section className="details-card">
        <div className="card-heading">
          <p className="eyebrow">PARKING ACTIVITY</p>
          <h2>Parking Information</h2>
        </div>

        <div className="details-grid">
          <div className="detail-item">
            <span>Parking Spot</span>
            <strong>{currentVehicle.parkingSpot || "—"}</strong>
          </div>

          <div className="detail-item">
            <span>Entry Time</span>
            <strong>{currentVehicle.entryTime || "—"}</strong>
          </div>

          <div className="detail-item">
            <span>Exit Time</span>
            <strong>{currentVehicle.exitTime || "—"}</strong>
          </div>

          <div className="detail-item">
            <span>Duration</span>
            <strong>{currentVehicle.duration || "—"}</strong>
          </div>
        </div>
      </section>

      {/* Actions */}
      <div className="details-actions">
        <button type="button" className="back-button" onClick={() => navigate("/vehicles")}>
          ← BACK TO SEARCH
        </button>
      </div>
    </div>
  );
}

export default VehicleDetails;
