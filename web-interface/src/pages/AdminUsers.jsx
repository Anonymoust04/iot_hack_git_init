import { useCallback, useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { createUser, deleteUser, getUsers, updateUser } from "../services/api";
import "../styles/AdminUsers.css";

const permissions = [
  ["USER_MANAGEMENT", "User management", "Add, edit and remove dashboard users"],
  ["FINANCIAL_REPORTS", "Financial reports", "View and generate financial reports"],
  ["GATE_CONTROL", "Gate control", "Open and close barrier gates"],
  ["LIGHT_CONTROL", "Light control", "Control parking lights"],
  ["FAN_CONTROL", "Fan control", "Control exhaust fans"],
  ["REPAIR", "Repair permission", "Repair gates, fans and parking spots"],
];

const defaultsFor = (role) => role === "ADMIN" ? permissions.map(([key]) => key) : ["GATE_CONTROL", "LIGHT_CONTROL", "FAN_CONTROL", "REPAIR"];
const blankUser = () => ({ username: "", password: "", role: "OPERATOR", permissions: defaultsFor("OPERATOR") });

function PermissionChecks({ value, onChange, idPrefix }) {
  function toggle(permission) { onChange(value.includes(permission) ? value.filter((item) => item !== permission) : [...value, permission]); }
  return <fieldset className="permission-checks"><legend>Permissions</legend>{permissions.map(([key, label, detail]) => (
    <label className="permission-check" key={key} htmlFor={`${idPrefix}-${key}`}><input id={`${idPrefix}-${key}`} type="checkbox" checked={value.includes(key)} onChange={() => toggle(key)} /><span><strong>{label}</strong><small>{detail}</small></span></label>
  ))}</fieldset>;
}

function AccessMark({ allowed }) { return <span className={allowed ? "access-mark allowed" : "access-mark denied"}>{allowed ? "Allowed" : "Restricted"}</span>; }

function AdminUsers() {
  const location = useLocation();
  const activeSection = ["#add-user", "#access-rules"].includes(location.hash) ? location.hash : "#directory";
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState(blankUser);
  const [editing, setEditing] = useState(null);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState(null);

  const loadUsers = useCallback(async () => {
    setLoading(true);
    try { setUsers(await getUsers()); setNotice(null); }
    catch (error) { setNotice({ type: "error", text: error.message || "Unable to load the user directory." }); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { loadUsers(); }, [loadUsers]);
  function roleChange(setter, role) { setter((current) => ({ ...current, role, permissions: defaultsFor(role) })); }

  async function handleCreate(event) {
    event.preventDefault();
    const username = form.username.trim();
    if (!username || !form.password) return setNotice({ type: "error", text: "Enter a username and temporary password." });
    setSaving(true);
    try { const user = await createUser({ ...form, username }); setUsers((current) => [...current, user].sort((a, b) => a.id - b.id)); setForm(blankUser()); setNotice({ type: "success", text: `${user.username} was added with its selected permissions.` }); }
    catch (error) { setNotice({ type: "error", text: error.message || "User could not be added." }); }
    finally { setSaving(false); }
  }

  function beginEdit(user) { setEditing({ ...user, password: "", permissions: user.permissions || defaultsFor(user.role) }); }
  async function handleEdit(event) {
    event.preventDefault();
    if (!editing.username.trim()) return setNotice({ type: "error", text: "Username cannot be empty." });
    setSaving(true);
    try {
      const changes = { username: editing.username.trim(), role: editing.role, permissions: editing.permissions };
      if (editing.password) changes.password = editing.password;
      const saved = await updateUser(editing.id, changes);
      setUsers((current) => current.map((user) => user.id === saved.id ? saved : user)); setEditing(null);
      setNotice({ type: "success", text: `${saved.username}'s role and permissions were updated.` });
    } catch (error) { setNotice({ type: "error", text: error.message || "User could not be updated." }); }
    finally { setSaving(false); }
  }
  async function handleDelete(user) {
    if (!window.confirm(`Remove ${user.username}? This cannot be undone.`)) return;
    setSaving(true);
    try { await deleteUser(user.id); setUsers((current) => current.filter((item) => item.id !== user.id)); setNotice({ type: "success", text: `${user.username} was removed.` }); }
    catch (error) { setNotice({ type: "error", text: error.message || "User could not be removed." }); }
    finally { setSaving(false); }
  }

  return <div className="admin-users-page">
    <header className="admin-users-header"><div><p className="eyebrow">ADMINISTRATION / RBAC</p><h1>Users &amp; access control</h1><p>Create accounts, assign operational permissions, and maintain dashboard access.</p></div><button className="admin-refresh" type="button" onClick={loadUsers} disabled={loading}>{loading ? "REFRESHING…" : "REFRESH DIRECTORY"}</button></header>
    {notice && <div className={`admin-notice ${notice.type}`} style={notice.text === "Failed to fetch" ? { textAlign: "center" } : undefined}>{notice.text}</div>}
    <div className="admin-users-layout section-view">
      <section className="admin-panel create-user-panel" hidden={activeSection !== "#add-user"}><div className="panel-heading"><div><p className="eyebrow">NEW ACCOUNT</p><h2>Add dashboard user</h2></div><span className="admin-only-label">ADMIN ONLY</span></div><form className="create-user-form" onSubmit={handleCreate}>
        <label htmlFor="new-username">Username</label><input id="new-username" value={form.username} autoComplete="off" placeholder="e.g. operator.lee" onChange={(event) => setForm((current) => ({ ...current, username: event.target.value }))} />
        <label htmlFor="new-password">Temporary password</label><input id="new-password" type="password" value={form.password} autoComplete="new-password" placeholder="Set a secure temporary password" onChange={(event) => setForm((current) => ({ ...current, password: event.target.value }))} />
        <label htmlFor="new-role">Role</label><select id="new-role" value={form.role} onChange={(event) => roleChange(setForm, event.target.value)}><option value="OPERATOR">Operator — operations and maintenance</option><option value="ADMIN">Admin — complete system access</option></select>
        <PermissionChecks idPrefix="new" value={form.permissions} onChange={(selected) => setForm((current) => ({ ...current, permissions: selected }))} /><button className="create-user-button" type="submit" disabled={saving}>{saving ? "CREATING…" : "ADD USER"}</button>
      </form></section>
      <section className="admin-panel user-directory-panel" hidden={activeSection !== "#directory"}><div className="panel-heading"><div><p className="eyebrow">USER DIRECTORY</p><h2>Dashboard accounts</h2></div><span className="directory-count">{users.length} ACCOUNTS</span></div><div className="user-table-wrap"><table className="user-table"><thead><tr><th>User</th><th>Role</th><th>Permissions</th><th>Actions</th></tr></thead><tbody>
        {loading && <tr><td colSpan="4" className="empty-users">Loading directory…</td></tr>}
        {!loading && users.length === 0 && <tr><td colSpan="4" className="empty-users no-accounts">No accounts returned by the server.</td></tr>}
        {!loading && users.map((user) => <tr key={user.id}><td><strong>{user.username}</strong><small className="account-id">ID #{user.id}</small></td><td><span className={`role-chip ${String(user.role).toLowerCase()}`}>{user.role}</span></td><td>{user.permissions.length} assigned</td><td className="user-actions"><button type="button" onClick={() => beginEdit(user)}>EDIT</button><button className="remove-user" type="button" disabled={saving} onClick={() => handleDelete(user)}>REMOVE</button></td></tr>)}
      </tbody></table></div></section>
    </div>
    <section className="admin-panel permissions-panel" hidden={activeSection !== "#access-rules"}><div className="panel-heading"><div><p className="eyebrow">PERMISSION MANAGEMENT</p><h2>Access rules</h2></div><span className="server-source-label">PERSISTED PER USER</span></div><div className="permission-table-wrap"><table className="permission-table"><thead><tr><th>Permission</th><th>What it allows</th><th>Admin default</th><th>Operator default</th></tr></thead><tbody>{permissions.map(([key, label, detail]) => <tr key={key}><td>{label}</td><td>{detail}</td><td><AccessMark allowed /></td><td><AccessMark allowed={defaultsFor("OPERATOR").includes(key)} /></td></tr>)}</tbody></table></div><p className="permission-footnote">Use Edit on an account to grant or revoke individual permissions. Changes are saved in the database and returned with the user directory.</p></section>
    {editing && <div className="admin-modal-backdrop" role="presentation"><section className="admin-modal" role="dialog" aria-modal="true" aria-labelledby="edit-user-title"><div className="panel-heading"><div><p className="eyebrow">EDIT USER</p><h2 id="edit-user-title">{editing.username}</h2></div><button className="modal-close" type="button" onClick={() => setEditing(null)}>×</button></div><form className="create-user-form" onSubmit={handleEdit}>
      <label htmlFor="edit-username">Username</label><input id="edit-username" value={editing.username} onChange={(event) => setEditing((current) => ({ ...current, username: event.target.value }))} />
      <label htmlFor="edit-password">New password <small>(leave blank to keep current)</small></label><input id="edit-password" type="password" value={editing.password} autoComplete="new-password" onChange={(event) => setEditing((current) => ({ ...current, password: event.target.value }))} />
      <label htmlFor="edit-role">Role</label><select id="edit-role" value={editing.role} onChange={(event) => roleChange(setEditing, event.target.value)}><option value="OPERATOR">Operator</option><option value="ADMIN">Admin</option></select>
      <PermissionChecks idPrefix="edit" value={editing.permissions} onChange={(selected) => setEditing((current) => ({ ...current, permissions: selected }))} /><div className="modal-actions"><button className="modal-cancel" type="button" onClick={() => setEditing(null)}>CANCEL</button><button className="create-user-button" type="submit" disabled={saving}>{saving ? "SAVING…" : "SAVE CHANGES"}</button></div>
    </form></section></div>}
  </div>;
}

export default AdminUsers;
