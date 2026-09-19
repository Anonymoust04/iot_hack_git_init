import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import "./App.css";

import Login from "./pages/Login.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import Account from "./pages/Account.jsx";
import VehicleSearch from "./pages/VehicleSearch.jsx";
import VehicleDetails from "./pages/VehicleDetails.jsx";
import Layout from "./components/Layout.jsx";

function App() {
  return (
    <BrowserRouter>
      <Routes>

        {/* Login */}
        <Route path="/login" element={<Login />} />

        {/* Dashboard */}
        <Route
          path="/dashboard"
          element={
            <Layout>
              <Dashboard />
            </Layout>
          }
        />

        {/* Vehicle Search */}
        <Route
          path="/vehicles"
          element={
            <Layout>
              <VehicleSearch />
            </Layout>
          }
        />

        {/* Vehicle Details */}
        <Route
          path="/vehicles/:plateNumber"
          element={
            <Layout>
              <VehicleDetails />
            </Layout>
          }
        />

        {/* Account */}
        <Route
          path="/account"
          element={
            <Layout>
              <Account />
            </Layout>
          }
        />

        {/* Unknown URL */}
        <Route
          path="*"
          element={<Navigate to="/login" replace />}
        />

      </Routes>
    </BrowserRouter>
  );
}

export default App;