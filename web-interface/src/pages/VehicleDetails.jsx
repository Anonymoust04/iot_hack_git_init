import { useNavigate, useParams } from "react-router-dom";
import { usePolling } from "../hooks/usePolling";
import { getVehicle } from "../services/api";

function VehicleDetails() {
    const navigate = useNavigate();
  // The plate from the URL (/vehicles/:plateNumber); its latest visit, kept live
  const { plateNumber } = useParams();
  const { data, error } = usePolling(
    async () => ({ vehicle: await getVehicle(plateNumber) }),
    [plateNumber]
  );
  const vehicle = data?.vehicle;

  if (!vehicle) {
    return (
      <div className="vehicle-details-page">
        <div className="details-header">
          <div>
            <p className="eyebrow">VEHICLE RECORD</p>
            <h1>{plateNumber}</h1>
            <p>
              {error
                ? `Could not load this vehicle: ${error.message}`
                : data === null
                  ? "Loading parking record..."
                  : "No parking record for this number plate."}
            </p>
          </div>
        </div>

        <div className="details-actions">
          <button type="button"
          className="back-button"
          onClick={() => navigate("/vehicles")}>
            ← BACK TO SEARCH
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="vehicle-details-page">
      {/* Page Header */}
      <div className="details-header">
        <div>
          <p className="eyebrow">VEHICLE RECORD</p>
          <h1>{vehicle.plateNumber}</h1>
          <p>Detailed vehicle information and parking history.</p>
        </div>

        <span className="vehicle-status">
          <span className="status-dot"></span>
          {vehicle.status}
        </span>
      </div>

      {/* Vehicle Overview */}
      <section className="details-card vehicle-overview">
        <div className="vehicle-icon">
          🚗
        </div>

        <div className="vehicle-main-info">
          <p className="eyebrow">VEHICLE</p>
          <h2>
            {vehicle.brand} {vehicle.model}
          </h2>
          <p>{vehicle.vehicleType}</p>
        </div>

        <div className="plate-display">
          <span>NUMBER PLATE</span>
          <strong>{vehicle.plateNumber}</strong>
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
            <strong>{vehicle.plateNumber}</strong>
          </div>

          <div className="detail-item">
            <span>Vehicle Type</span>
            <strong>{vehicle.vehicleType}</strong>
          </div>

          <div className="detail-item">
            <span>Brand</span>
            <strong>{vehicle.brand}</strong>
          </div>

          <div className="detail-item">
            <span>Model</span>
            <strong>{vehicle.model}</strong>
          </div>

          <div className="detail-item">
            <span>Colour</span>
            <strong>{vehicle.colour}</strong>
          </div>

          <div className="detail-item">
            <span>Status</span>
            <strong>{vehicle.status}</strong>
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
            <strong>{vehicle.parkingSpot}</strong>
          </div>

          <div className="detail-item">
            <span>Entry Time</span>
            <strong>{vehicle.entryTime}</strong>
          </div>

          <div className="detail-item">
            <span>Exit Time</span>
            <strong>{vehicle.exitTime}</strong>
          </div>

          <div className="detail-item">
            <span>Duration</span>
            <strong>{vehicle.duration}</strong>
          </div>
        </div>
      </section>

      {/* Actions */}
      <div className="details-actions">
        <button type="button" 
        className="back-button"
        onClick={() => navigate("/vehicles")}>
          ← BACK TO SEARCH
        </button>
      </div>
    </div>
  );
}

export default VehicleDetails;