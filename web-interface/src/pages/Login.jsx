import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { loginUser } from "../services/api";

function Login() {
  const navigate = useNavigate();

  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("admin");
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");

  const handleLogin = async (event) => {
    event.preventDefault();
    setErrorMsg("");
    setLoading(true);

    try {
      const user = await loginUser(username.trim(), password);
      localStorage.setItem("currentUser", JSON.stringify(user));
      navigate("/dashboard");
    } catch (err) {
      setErrorMsg(err.message || "Invalid username or password.");
    } finally {
      setLoading(false);
    }
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
          <p>Enter your Administrator or Operator credentials.</p>
        </div>

        {errorMsg && (
          <div
            style={{
              padding: "10px 14px",
              backgroundColor: "#fee2e2",
              color: "#991b1b",
              borderRadius: "8px",
              marginBottom: "16px",
              fontSize: "0.85rem",
              fontWeight: 500,
            }}
          >
            {errorMsg}
          </div>
        )}

        <form onSubmit={handleLogin}>
          <div className="input-group">
            <label htmlFor="username">USERNAME</label>
            <input
              id="username"
              type="text"
              placeholder="admin or operator username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
            />
          </div>

          <div className="input-group">
            <label htmlFor="password">PASSWORD</label>
            <input
              id="password"
              type="password"
              placeholder="Enter password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </div>

          <button type="submit" className="login-button" disabled={loading}>
            {loading ? "AUTHENTICATING..." : "ACCESS SYSTEM"}
            <span>→</span>
          </button>
        </form>

        <div className="login-footer">
          <span className="status-dot"></span>
          SYSTEM ONLINE · ROLES: ADMIN / OPERATOR
        </div>
      </div>
    </div>
  );
}

export default Login;