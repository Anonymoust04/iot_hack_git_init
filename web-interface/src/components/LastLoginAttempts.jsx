// Level 2: "show the last three login attempts after login".
// The login response carries them (newest first, including this login); Login.jsx saves the user,
// with `loginAttempts`, in localStorage "currentUser".

function readAttempts() {
  try {
    const user = JSON.parse(localStorage.getItem("currentUser") || "null");
    return Array.isArray(user?.loginAttempts) ? user.loginAttempts.slice(0, 3) : [];
  } catch {
    return [];
  }
}

function localTime(value) {
  const date = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

const box = {
  display: "flex", flexWrap: "wrap", alignItems: "center", gap: "8px 16px", margin: "0 0 16px",
  padding: "10px 14px", border: "1px solid rgba(127,127,127,0.35)", borderRadius: 8, fontSize: 14,
};

function LastLoginAttempts() {
  const attempts = readAttempts();
  if (!attempts.length) return null;
  const failed = attempts.filter((a) => !a.success).length;

  return (
    <section style={box} aria-label="Your last login attempts">
      <strong>Your last {attempts.length} login attempt{attempts.length > 1 ? "s" : ""}:</strong>
      {attempts.map((a, i) => (
        <span key={i} title={a.ip_address ? `from ${a.ip_address}` : undefined}>
          <span style={{ color: a.success ? "#16a34a" : "#dc2626", fontWeight: 600 }}>
            {a.success ? "✓ success" : "✗ failed"}
          </span>{" "}
          {localTime(a.attempted_at)}
        </span>
      ))}
      {failed > 0 && (
        <span style={{ color: "#dc2626" }}>
          {failed} failed attempt{failed > 1 ? "s" : ""}: if that wasn't you, change your password.
        </span>
      )}
    </section>
  );
}

export default LastLoginAttempts;
