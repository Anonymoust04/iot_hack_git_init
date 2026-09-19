import { useState } from "react";
import { useNavigate } from "react-router-dom";

function Login() {
  const navigate = useNavigate();

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  const handleLogin = (event) => {
    event.preventDefault();

    // Temporary frontend users.
    // This will be replaced with backend authentication later.
    const users = [
      {
        username: "admin",
        password: "admin",
        name: "Administrator",
        role: "Admin",
        status: "Active",
      },
      {
        username: "jlim",
        password: "1234",
        name: "Jackson Lim",
        role: "Operator",
        status: "Active",
      },
    ];

    const user = users.find(
      (account) =>
        account.username === username.trim() &&
        account.password === password
    );

    if (!user) {
      alert("Invalid username or password.");
      return;
    }

    // Save the logged-in user.
    localStorage.setItem(
      "currentUser",
      JSON.stringify(user)
    );

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
          SYSTEM ONLINE
        </div>

      </div>
    </div>
  );
}

export default Login;