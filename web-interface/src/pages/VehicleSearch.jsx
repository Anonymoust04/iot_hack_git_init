import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { searchVehicles } from "../services/api";

function VehicleSearch() {
  const navigate = useNavigate();

  // What the user is currently typing
  const [inputValue, setInputValue] = useState("");

  // What the user actually searched for
  const [searchTerm, setSearchTerm] = useState("");

  // Parking records from the backend for the searched plate
  const [vehicles, setVehicles] = useState([]);

  // Only runs when the SEARCH button is clicked
  const handleSearch = async (event) => {
    event.preventDefault();

    const term = inputValue.trim();
    if (term === "") return;

    try {
      setVehicles(await searchVehicles(term));
    } catch (err) {
      alert(`Search failed: ${err.message}`);
      setVehicles([]);
    }
    setSearchTerm(term);
  };

  // Clear both the input and the search results
  const handleClear = () => {
    setInputValue("");
    setSearchTerm("");
    setVehicles([]);
  };

  // Only filter vehicles after SEARCH has been clicked
  const filteredVehicles =
    searchTerm === ""
      ? []
      : vehicles.filter(
          (vehicle) =>
            vehicle.plateNumber.toLowerCase() === searchTerm.toLowerCase()
        );

  return (
    <div className="vehicle-search-page">
      {/* Page Header */}
      <div className="search-header">
        <p className="eyebrow">VEHICLE RECORDS</p>

        <h1>Vehicle Search</h1>

        <p>
          Search parking records using a vehicle number plate.
        </p>
      </div>

      {/* Search Panel */}
      <div className="search-panel">
        <form onSubmit={handleSearch}>
          <label htmlFor="plateNumber">
            NUMBER PLATE
          </label>

          <div className="search-input-row">
            <input
              id="plateNumber"
              type="text"
              placeholder="Enter vehicle number plate"
              value={inputValue}
              onChange={(event) => setInputValue(event.target.value)}
            />

            <button
              type="submit"
              className="search-button"
            >
              SEARCH →
            </button>
          </div>
        </form>

        {searchTerm && (
          <button
            type="button"
            className="clear-button"
            onClick={handleClear}
          >
            CLEAR SEARCH
          </button>
        )}
      </div>

      {/* Search Results */}
      {searchTerm && (
        <div className="search-results">
          <div className="results-header">
            <p className="eyebrow">SEARCH RESULTS</p>

            <h2>
              {filteredVehicles.length}{" "}
              {filteredVehicles.length === 1
                ? "vehicle"
                : "vehicles"}{" "}
              found
            </h2>
          </div>

          {filteredVehicles.length > 0 ? (
            <div className="vehicle-list">
              {filteredVehicles.map((vehicle) => (
                <div
                  className="vehicle-result-card"
                  key={vehicle.plateNumber}
                >
                  <div className="vehicle-result-main">
                    <div className="vehicle-result-icon">
                      🚗
                    </div>

                    <div>
                      <span>NUMBER PLATE</span>

                      <h3>
                        {vehicle.plateNumber}
                      </h3>

                      <p>
                        {vehicle.brand} {vehicle.model} ·{" "}
                        {vehicle.vehicleType}
                      </p>
                    </div>
                  </div>

                  <div className="vehicle-result-info">
                    <div>
                      <span>SPOT</span>
                      <strong>
                        {vehicle.parkingSpot}
                      </strong>
                    </div>

                    <div>
                      <span>ENTRY</span>
                      <strong>
                        {vehicle.entryTime}
                      </strong>
                    </div>

                    <div>
                      <span>STATUS</span>
                      <strong>
                        {vehicle.status}
                      </strong>
                    </div>
                  </div>

                  <button
                    type="button"
                    className="details-button"
                    onClick={() =>
                      navigate(
                        `/vehicles/${encodeURIComponent(
                          vehicle.plateNumber
                        )}`
                      )
                    }
                  >
                    VIEW DETAILS →
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <div className="no-results">
              <div className="no-results-icon">
                ?
              </div>

              <h3>No vehicle found</h3>

              <p>
                No parking record matches this number plate.
              </p>

              <button
                type="button"
                onClick={handleClear}
              >
                TRY ANOTHER SEARCH
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default VehicleSearch;