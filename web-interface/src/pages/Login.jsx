import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { usePolling } from "../hooks/usePolling";
import { getBackendStatus, login } from "../services/api";

function Login() {
  const navigate = useNavigate();

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const { data: backendOnline } = usePolling(getBackendStatus);

  const handleLogin = async (event) => {
    event.preventDefault();

    // Backend login (Admin / Operator accounts in the database).
    // The user and their token are saved for the other pages.
    try {
      await login(username.trim(), password);
    } catch (err) {
      alert(err.message || "Invalid username or password.");
      return;
    }

    // Go to vehicle search.
    navigate("/vehicles");
  };

  return (
    <div className="login-page">
      <div className="login-panel">

        <div className="login-brand">
          <span className="brand-mark">P</span>

          <div>
            <h1>PARK//CONTROL</h1>
            <p>Parking Operations System</p>
          </div>
        </div>

        <div className="login-heading">
          <p className="eyebrow">SECURE ACCESS</p>

          <h2>Sign in to continue</h2>

          <p>
            Enter your operator credentials to access the system.
          </p>
        </div>

        <form onSubmit={handleLogin}>

          <div className="input-group">
            <label htmlFor="username">
              USERNAME
            </label>

            <input
              id="username"
              type="text"
              placeholder="Enter username"
              value={username}
              onChange={(event) =>
                setUsername(event.target.value)
              }
            />
          </div>

          <div className="input-group">
            <label htmlFor="password">
              PASSWORD
            </label>

            <input
              id="password"
              type="password"
              placeholder="Enter password"
              value={password}
              onChange={(event) =>
                setPassword(event.target.value)
              }
            />
          </div>

          <button
            type="submit"
            className="login-button"
          >
            ACCESS SYSTEM
            <span>→</span>
          </button>

        </form>

        <div className="login-footer">
          <span className="status-dot"></span>
          {backendOnline ? "SYSTEM ONLINE" : "SYSTEM OFFLINE"}
        </div>

      </div>
    </div>
  );
}

export default Login;