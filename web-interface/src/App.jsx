import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import "./App.css";

import Login from "./pages/Login.jsx";
import Account from "./pages/Account.jsx";
import VehicleSearch from "./pages/VehicleSearch.jsx";
import VehicleDetails from "./pages/VehicleDetails.jsx";
import Layout from "./components/Layout.jsx";

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />

        <Route
          path="/vehicles"
          element={
            <Layout>
              <VehicleSearch />
            </Layout>
          }
        />

        <Route
          path="/vehicles/:plateNumber"
          element={
            <Layout>
              <VehicleDetails />
            </Layout>
          }
        />

        <Route
          path="/account"
          element={
            <Layout>
              <Account />
            </Layout>
          }
        />

        <Route
          path="*"
          element={<Navigate to="/login" replace />}
        />
      </Routes>
    </BrowserRouter>
  );
}

export default App;