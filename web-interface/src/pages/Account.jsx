import { useNavigate } from "react-router-dom";

function Account() {
  const navigate = useNavigate();

  // Get the currently logged-in user.
  const savedUser = localStorage.getItem("currentUser");

  // Convert the saved JSON string back into an object.
  const user = savedUser
    ? JSON.parse(savedUser)
    : null;

  // If there is no logged-in user,
  // send the user back to Login.
  if (!user) {
    return (
      <div className="account-page">
        <div className="account-header">
          <p className="eyebrow">ACCOUNT</p>

          <h1>No Active Account</h1>

          <p>
            Please log in before accessing your account.
          </p>

          <button
            type="button"
            className="login-button"
            onClick={() => navigate("/login")}
          >
            GO TO LOGIN →
          </button>
        </div>
      </div>
    );
  }

  const handleLogout = () => {
    // Remove the logged-in user.
    localStorage.removeItem("currentUser");

    // Return to Login.
    navigate("/login");
  };

  return (
    <div className="account-page">

      {/* Header */}
      <div className="account-header">
        <p className="eyebrow">ACCOUNT</p>

        <h1>Account Settings</h1>

        <p>
          View your account information and system access.
        </p>
      </div>

      <div className="account-content">

        {/* Profile */}
        <section className="account-card profile-card">

          <div className="profile-avatar">
            {user.name.charAt(0)}
          </div>

          <div className="profile-info">

            <h2>{user.name}</h2>

            <p>@{user.username}</p>

            <span className="status-badge">
              <span className="status-dot"></span>
              {user.status}
            </span>

          </div>

        </section>

        {/* Account Information */}
        <section className="account-card">

          <div className="card-heading">
            <div>

              <p className="eyebrow">
                ACCOUNT INFORMATION
              </p>

              <h2>User Details</h2>

            </div>
          </div>

          <div className="account-details">

            <div className="detail-row">
              <span>Full Name</span>
              <strong>{user.name}</strong>
            </div>

            <div className="detail-row">
              <span>Username</span>
              <strong>{user.username}</strong>
            </div>

            <div className="detail-row">
              <span>Role</span>
              <strong>{user.role}</strong>
            </div>

            <div className="detail-row">
              <span>Account Status</span>
              <strong>{user.status}</strong>
            </div>

          </div>

        </section>

        {/* System Access */}
        <section className="account-card access-card">

          <div>

            <p className="eyebrow">
              SYSTEM ACCESS
            </p>

            <h2>{user.role} Access</h2>

            <p>
              Your account is currently registered as an{" "}
              <strong>{user.role}</strong>.
            </p>

          </div>

          <div className="role-badge">
            {user.role}
          </div>

        </section>

        {/* Logout */}
        <section className="account-actions">

          <button
            type="button"
            className="logout-button"
            onClick={handleLogout}
          >
            LOG OUT
            <span>→</span>
          </button>

        </section>

      </div>
    </div>
  );
}

export default Account;