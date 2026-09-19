import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { getActiveCars, getVehicleDetails, getHistorySessions } from "../services/api";

function VehicleSearch() {
  const navigate = useNavigate();

  const [inputValue, setInputValue] = useState("");
  const [searchTerm, setSearchTerm] = useState("");

  const [activeVehicles, setActiveVehicles] = useState([]);
  const [searchResults, setSearchResults] = useState([]);
  const [loading, setLoading] = useState(false);

  const fetchActiveVehicles = async () => {
    try {
      const list = await getActiveCars();
      setActiveVehicles(list);
    } catch (err) {
      console.warn("Failed to load active cars:", err);
    }
  };

  useEffect(() => {
    fetchActiveVehicles();
    const interval = setInterval(fetchActiveVehicles, 5000);
    return () => clearInterval(interval);
  }, []);

  const handleSearch = async (event) => {
    event.preventDefault();
    const query = inputValue.trim();
    setSearchTerm(query);

    if (!query) {
      setSearchResults([]);
      return;
    }

    setLoading(true);
    try {
      // 1. Filter local active cars
      const activeMatches = activeVehicles.filter((v) =>
        v.plateNumber?.toLowerCase().includes(query.toLowerCase())
      );

      // 2. Query backend for this specific plate
      let detailedResult = null;
      try {
        const detail = await getVehicleDetails(query);
        if (detail && detail.found) {
          detailedResult = detail;
        }
      } catch {
        // detail endpoint failed or not found
      }

      const resultsMap = new Map();
      activeMatches.forEach((v) => resultsMap.set(v.plateNumber, v));
      if (detailedResult) {
        resultsMap.set(detailedResult.plateNumber, {
          ...detailedResult,
          parkingSpot: detailedResult.parkingSpot || detailedResult.assignedSpot || "—",
        });
      }

      // 3. Query database history sessions
      try {
        const historyList = await getHistorySessions(query);
        if (Array.isArray(historyList)) {
          historyList.forEach((sess) => {
            const plate = sess.car_plate || sess.plateNumber;
            if (plate && !resultsMap.has(plate)) {
              resultsMap.set(plate, {
                plateNumber: plate,
                vehicleType: sess.car_type || "Vehicle",
                parkingSpot: sess.spot_name || "—",
                entryTime: sess.entry_time ? new Date(sess.entry_time).toLocaleTimeString() : "Earlier",
                exitTime: sess.exit_time ? new Date(sess.exit_time).toLocaleTimeString() : "—",
                status: sess.status === "completed" ? "Exited" : "Active",
                duration: sess.fee_cents ? `Fee: $${(sess.fee_cents / 100).toFixed(2)}` : "Completed",
                charged: sess.status === "completed" || Boolean(sess.fee_cents),
              });
            }
          });
        }
      } catch {
        // ignore
      }

      setSearchResults(Array.from(resultsMap.values()));
    } finally {
      setLoading(false);
    }
  };

  const handleClear = () => {
    setInputValue("");
    setSearchTerm("");
    setSearchResults([]);
  };

  const displayList = searchTerm ? searchResults : activeVehicles;

  return (
    <div className="vehicle-search-page">
      {/* Page Header */}
      <div className="search-header">
        <p className="eyebrow">VEHICLE RECORDS</p>
        <h1>Vehicle Search</h1>
        <p>Search real-time parking records using a vehicle number plate.</p>
      </div>

      {/* Search Panel */}
      <div className="search-panel">
        <form onSubmit={handleSearch}>
          <label htmlFor="plateNumber">NUMBER PLATE</label>

          <div className="search-input-row">
            <input
              id="plateNumber"
              type="text"
              placeholder="Enter vehicle number plate (e.g. WCT 759, TLT 388)"
              value={inputValue}
              onChange={(event) => setInputValue(event.target.value)}
            />

            <button type="submit" className="search-button" disabled={loading}>
              {loading ? "SEARCHING..." : "SEARCH →"}
            </button>
          </div>
        </form>

        {searchTerm && (
          <button type="button" className="clear-button" onClick={handleClear}>
            CLEAR SEARCH
          </button>
        )}
      </div>

      {/* Search Results / Active Cars List */}
      <div className="search-results">
        <div className="results-header">
          <p className="eyebrow">{searchTerm ? "SEARCH RESULTS" : "CURRENTLY PARKED VEHICLES"}</p>
          <h2>
            {displayList.length} {displayList.length === 1 ? "vehicle" : "vehicles"}{" "}
            {searchTerm ? "found" : "active inside"}
          </h2>
        </div>

        {displayList.length > 0 ? (
          <div className="vehicle-list">
            {displayList.map((vehicle) => (
              <div className="vehicle-result-card" key={vehicle.plateNumber}>
                <div className="vehicle-result-main">
                  <div className="vehicle-result-icon">🚗</div>

                  <div>
                    <span>NUMBER PLATE</span>
                    <h3>{vehicle.plateNumber}</h3>
                    <p>
                      {vehicle.brand || "Standard"} {vehicle.model || "Car"} · {vehicle.vehicleType || "Vehicle"}
                    </p>
                  </div>
                </div>

                <div className="vehicle-result-info">
                  <div>
                    <span>SPOT</span>
                    <strong>{vehicle.parkingSpot || vehicle.assignedSpot || "—"}</strong>
                  </div>

                  <div>
                    <span>ENTRY</span>
                    <strong>{vehicle.entryTime || "Recent"}</strong>
                  </div>

                  <div>
                    <span>STATUS</span>
                    <strong>{vehicle.status || "Parked"}</strong>
                  </div>
                </div>

                <button
                  type="button"
                  className="details-button"
                  onClick={() => navigate(`/vehicles/${encodeURIComponent(vehicle.plateNumber)}`)}
                >
                  VIEW DETAILS →
                </button>
              </div>
            ))}
          </div>
        ) : (
          <div className="no-results">
            <div className="no-results-icon">?</div>
            <h3>{searchTerm ? "No vehicle found" : "No vehicles currently parked"}</h3>
            <p>
              {searchTerm
                ? `No parking record matches "${searchTerm}".`
                : "Vehicles will appear here as they enter through Gate A."}
            </p>
            {searchTerm && (
              <button type="button" onClick={handleClear}>
                VIEW ALL ACTIVE VEHICLES
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default VehicleSearch;