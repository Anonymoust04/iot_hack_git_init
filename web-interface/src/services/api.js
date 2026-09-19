// API Service connecting the frontend to the FastAPI backend (main.py + db_hook)
// Use the configured backend address; local FastAPI remains the development fallback.
export const BACKEND_BASE = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
export const REFRESH_MS = Number(import.meta.env?.VITE_REFRESH_MS) || 5000;

async function request(endpoint, options = {}) {
  const token = localStorage.getItem('token');
  const headers = {
    'Content-Type': 'application/json',
    // ngrok's free tunnel (public backend URL) shows an HTML warning page unless this header is sent
    'ngrok-skip-browser-warning': '1',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(options.headers || {}),
  };

  // Always target FastAPI backend directly on port 8000 (CORS is enabled)
  const targetUrl = endpoint.startsWith('http')
    ? endpoint
    : `${BACKEND_BASE}${endpoint.startsWith('/') ? '' : '/'}${endpoint}`;

  const res = await fetch(targetUrl, { ...options, headers });
  if (!res.ok) {
    let errMsg = `Request to ${endpoint} failed (${res.status})`;
    try {
      const errJson = await res.json();
      if (errJson.detail) errMsg = typeof errJson.detail === 'string' ? errJson.detail : JSON.stringify(errJson.detail);
    } catch {
      // ignore
    }
    throw new Error(errMsg);
  }

  const contentType = res.headers.get('content-type');
  if (contentType && contentType.includes('application/json')) {
    return await res.json();
  }
  return await res.text();
}

/**
 * System and health status
 */
// Last good status, shared by every page: switching tabs (a page remounts) or one slow refresh must not
// flash "offline". Only report offline when the backend hasn't answered for STATUS_GRACE_MS.
const STATUS_GRACE_MS = 15000;
let lastStatus = null;
let lastStatusAt = 0;

export function getLastKnownStatus() {
  return lastStatus;
}

export function isLastKnownOnline() {
  return Boolean(lastStatus && lastStatus.backend === 'online' && lastStatus.simulator === 'online');
}

export async function getSystemStatus() {
  try {
    const status = await request('/system-status');
    lastStatus = status;
    lastStatusAt = Date.now();
    return status;
  } catch {
    if (lastStatus && Date.now() - lastStatusAt < STATUS_GRACE_MS) {
      return lastStatus; // one missed refresh: keep showing the last known state
    }
    return {
      backend: 'offline',
      simulator: 'offline',
      total_spots: 90,
      available_spots: 90,
      occupied_spots: 0,
      cars_inside: 0,
      park_full: false,
    };
  }
}

export const getBackendStatus = getSystemStatus;

/**
 * Parking spaces from the backend database (GET /api/dashboard/spots): kept equal to the simulator by
 * webhooks + a periodic re-sync, and it knows the plates. No simulator list-* call per refresh.
 * status: free | occupied (a car is parked or on its way) | maintenance (broken / under maintenance)
 */
const SPOT_STATUS = { FREE: 'free', RESERVED: 'occupied', OCCUPIED: 'occupied', BROKEN: 'maintenance', MAINTENANCE: 'maintenance' };

export async function getParkingSpots() {
  try {
    const rows = await request('/api/dashboard/spots');
    return (Array.isArray(rows) ? rows : [])
      .filter((spot) => spot.purpose === 'Park')
      .map((spot) => ({
        name: spot.name,
        number: Number(spot.name.match(/\d+/)?.[0]) || null,
        status: SPOT_STATUS[spot.status] || 'free',
        zone: spot.zone,
        plate: spot.current_car || null,
        detectedCars: spot.status === 'OCCUPIED' ? 1 : 0,
        parkingForCarType: spot.car_type,
        broken: spot.broken,
        isUnderMaintenance: spot.under_maintenance,
        raw: spot,
      }))
      .sort((a, b) => a.zone.localeCompare(b.zone) || a.name.localeCompare(b.name, undefined, { numeric: true }));
  } catch (err) {
    console.warn('Failed to load parking spots:', err);
    return [];
  }
}

// "ZONE1" -> "Zone 1"
function zoneLabel(zone) {
  const match = String(zone || '').match(/^zone\s*(\d+)$/i);
  return match ? `Zone ${match[1]}` : String(zone || 'Unassigned');
}

export function calculateZoneStats(spots = []) {
  // One card per zone that has spots (Level 1: 1 zone, Level 2: 3 zones)
  const zones = new Map();
  spots.forEach((spot) => {
    const key = spot.zone || '';
    if (!zones.has(key)) zones.set(key, { name: zoneLabel(key), zone: key, total: 0, free: 0, occupied: 0, maintenance: 0 });
    const zone = zones.get(key);
    zone.total += 1;
    if (spot.status === 'free') zone.free += 1;
    else if (spot.status === 'occupied') zone.occupied += 1;
    else zone.maintenance += 1;
  });
  return [...zones.values()].sort((a, b) => a.zone.localeCompare(b.zone, undefined, { numeric: true }));
}

/**
 * All barrier gates from the backend (GET /api/dashboard/gates): state, broken / maintenance, and
 * role = entrance | exit (backend settings ENTRY_GATE / EXIT_GATE), entrances first.
 */
const GATE_ROLE_ORDER = { entrance: 0, exit: 1 };

export async function getBarriers() {
  try {
    const rows = await request('/api/dashboard/gates');
    return (Array.isArray(rows) ? rows : [])
      .map((gate) => {
        const role = gate.role || null;
        const where = gate.zone ? zoneLabel(gate.zone) : 'Park';
        return {
          name: gate.name,
          role,
          zone: gate.zone,
          state: gate.state,
          title: role === 'entrance' ? `Entrance Gate (${gate.name})` : role === 'exit' ? `Exit Gate (${gate.name})` : `Gate ${gate.name}`,
          subtitle: gate.broken ? `${where} · BROKEN` : gate.under_maintenance ? `${where} · under maintenance` : `${where} · ${gate.state}`,
          isOpen: gate.state === 'Open' || gate.state === 'Opening',
          isBroken: gate.broken === true || gate.under_maintenance === true,
        };
      })
      .sort((a, b) => (GATE_ROLE_ORDER[a.role] ?? 2) - (GATE_ROLE_ORDER[b.role] ?? 2) || a.name.localeCompare(b.name, undefined, { numeric: true }));
  } catch (err) {
    console.warn('Failed to load barrier gates:', err);
    return [];
  }
}

// Keep the full simulator barrier list for health counts and zone details.
export async function getBarrierHealthData() {
  try {
    const data = await request('/list-barriers');
    const barriers = Array.isArray(data) ? data : data?.barriers || data?.data || [];
    return Array.isArray(barriers)
      ? barriers.filter((gate) => gate && (typeof gate.state === 'string' || typeof gate.zoneParent === 'string'))
      : [];
  } catch {
    return [];
  }
}

/**
 * Barrier gate controls
 */
export async function openGate(gateName) {
  return await request(`/api/control/gates/${encodeURIComponent(gateName)}/open`, { method: 'POST' });
}

export async function closeGate(gateName) {
  return await request(`/api/control/gates/${encodeURIComponent(gateName)}/close`, { method: 'POST' });
}

export async function repairGate(gateName) {
  return await request(`/api/control/gates/${encodeURIComponent(gateName)}/repair`, { method: 'POST' });
}

/**
 * Parking Spot control
 */
export async function repairParkingSpot(spotName) {
  return await request(`/api/control/spots/${encodeURIComponent(spotName)}/repair`, { method: 'POST' });
}

/**
 * Lighting Controls
 */
export async function getLights() {
  try {
    const data = await request('/list-lights');
    return Array.isArray(data) ? data : [];
  } catch {
    return [];
  }
}

export async function turnLightOn(name) {
  return await request(`/api/control/lights/${encodeURIComponent(name)}/on`, { method: 'POST' });
}

export async function turnLightOff(name) {
  return await request(`/api/control/lights/${encodeURIComponent(name)}/off`, { method: 'POST' });
}

export async function turnLightGroupOn(groupName) {
  return await request(`/api/control/lights/group/${encodeURIComponent(groupName)}/on`, { method: 'POST' });
}

export async function turnLightGroupOff(groupName) {
  return await request(`/api/control/lights/group/${encodeURIComponent(groupName)}/off`, { method: 'POST' });
}

/**
 * Exhaust Fan Controls
 */
export async function getExhaustFans() {
  try {
    const data = await request('/list-exhaust-fans');
    return Array.isArray(data) ? data : [];
  } catch {
    return [];
  }
}

// CO per zone from the backend's CO controller (GET /co-status): no simulator call per refresh.
// risk = the simulator's own rating (Safe < Mid < High < Critical); fans run from Mid until Safe again.
export async function getZones() {
  try {
    const zones = await request('/co-status');
    return (Array.isArray(zones) ? zones : []).map((zone) => ({
      name: zone.name,
      gasCarbonMonoxideLevel: zone.gasCarbonMonoxideLevel,
      risk: zone.risk,
      ventilating: zone.ventilating === true,
      fansOn: Array.isArray(zone.fansOn) ? zone.fansOn : [],
      ventilateFrom: zone.ventilateFrom,
    }));
  } catch {
    return [];
  }
}

export async function turnFanOn(name) {
  return await request(`/api/control/fans/${encodeURIComponent(name)}/on`, { method: 'POST' });
}

export async function turnFanOff(name) {
  return await request(`/api/control/fans/${encodeURIComponent(name)}/off`, { method: 'POST' });
}

export async function repairFan(name) {
  return await request(`/api/control/fans/${encodeURIComponent(name)}/repair`, { method: 'POST' });
}

/**
 * Alarms & Maintenance Diagnostics
 */
export async function getAlarms() {
  try {
    const data = await request('/list-alarms');
    return Array.isArray(data) ? data : [];
  } catch {
    return [];
  }
}

export async function triggerTestWebhook() {
  return await request('/test');
}

/**
 * Recent Activity from webhook events & arrivals & DB history
 */
export async function getRecentActivity() {
  // 1. Try dedicated formatted endpoint
  try {
    const data = await request('/recent-activity?limit=25');
    if (Array.isArray(data)) {
      return data;
    }
  } catch (err) {
    console.warn('Failed /recent-activity, trying fallback:', err);
  }

  // 2. Fallback: Aggregate from all-events and recent-arrivals
  try {
    const [eventsRes, arrivalsRes] = await Promise.allSettled([
      request('/all-events?limit=30'),
      request('/recent-arrivals?limit=20'),
    ]);

    const events = (eventsRes.status === 'fulfilled' && eventsRes.value?.events) || [];
    const arrivals = (arrivalsRes.status === 'fulfilled' && arrivalsRes.value?.recent_arrivals) || [];

    const activities = [];
    let idCounter = 1;

    // Process webhook events (newest first)
    events.slice().reverse().forEach((evt) => {
      const plate = evt.CarPlateNumber || evt.CarPlate || evt.car_plate || evt.plate;
      if (!plate) return;

      const spotType = evt.SpotType || '';
      const spotName = evt.SpotName || '';
      const direction = evt.Direction || '';
      let eventText = `${spotType} ${direction}`;
      let statusText = 'Active';

      if (spotType === 'EntrySpot' || spotName.toUpperCase().includes('ENTRY')) {
        eventText = 'Arrived at Entrance';
        statusText = 'Queued';
      } else if (spotType === 'Park' && direction === 'CarIn') {
        eventText = `Parked in ${spotName}`;
        statusText = 'Parked';
      } else if (spotType === 'Park' && direction === 'CarOut') {
        eventText = `Left spot ${spotName}`;
        statusText = 'Departing';
      } else if (spotType === 'ExitSpot' || spotName.toUpperCase().includes('EXIT')) {
        eventText = 'Exited car park';
        statusText = 'Departed';
      }

      activities.push({
        id: evt.EventId || `evt-${idCounter++}`,
        plate,
        event: eventText,
        spot: spotName || '—',
        time: (evt.ServerDateTime || evt.Timestamp || 'Recent').split(' ').pop(),
        status: statusText,
      });
    });

    // Supplement with arrivals
    arrivals.forEach((arr) => {
      const p = arr.car_plate || arr.plate;
      if (p && !activities.some((a) => a.plate === p)) {
        activities.push({
          id: `arr-${idCounter++}`,
          plate: p,
          event: 'Arrived at Entrance',
          spot: arr.spot_name || 'EntrySpot',
          time: arr.arrival_time || 'Just now',
          status: 'Active',
        });
      }
    });

    return activities.slice(0, 15);
  } catch (err) {
    console.warn('Failed to load recent activities:', err);
    return [];
  }
}

/**
 * Vehicles & Active Cars
 */
export async function getActiveCars() {
  try {
    const res = await request('/active-cars');
    return res.cars || [];
  } catch {
    return [];
  }
}

export async function getVehicleDetails(plateNumber) {
  try {
    return await request(`/vehicles/${encodeURIComponent(plateNumber)}`);
  } catch (err) {
    return {
      found: false,
      plateNumber,
      message: err.message,
    };
  }
}

export async function sendCarToDestination(plateNumber, destination) {
  return await request(`/car/${encodeURIComponent(plateNumber)}/goto/${encodeURIComponent(destination)}`, {
    method: 'POST',
  });
}

/**
 * Query database historical sessions
 */
export async function getHistorySessions(plate = '') {
  try {
    const endpoint = plate
      ? `/api/history/sessions?plate=${encodeURIComponent(plate)}`
      : `/api/history/sessions?limit=50`;
    return await request(endpoint);
  } catch {
    return [];
  }
}

export async function getPenalties() {
  // Penalties are the simulator's own penalty events (stored by the backend)
  const rows = await request('/api/penalties?limit=500');
  return rows.map((row) => ({
    id: row.id,
    time: localTime(row.received_at),
    type: row.type || '—',
    description: row.reason || '—',
    subject: row.component || row.car_plate || '—',
    amount: row.fine_amount == null ? 0 : Number(row.fine_amount),
    status: 'Recorded', // the simulator has no "resolved" state for a penalty
  }));
}

// ---- Level 2: audit log, penalties, daily report (backend: /api/audit, /api/penalties, /api/logs) ----

// Backend times are UTC without a zone marker: show them in local time
function localTime(value) {
  if (!value) return '—';
  const date = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function describeDetails(details) {
  if (details == null) return '—';
  if (typeof details !== 'object') return String(details);
  return Object.entries(details)
    .map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(', ') : typeof value === 'object' ? JSON.stringify(value) : value}`)
    .join('; ');
}

export async function getAuditLogs() {
  // Admin only: an Operator gets 403, shown as an empty list
  const rows = await request('/api/audit?limit=200');
  return rows.map((row) => ({
    id: row.id,
    time: localTime(row.created_at),
    category: row.actor ? 'user' : 'system',
    source: row.actor || 'System',
    action: row.action,
    target: [row.target_type, row.target_name].filter(Boolean).join(' ') || '—',
    details: describeDetails(row.details),
    result: row.success ? 'Success' : 'Failed',
  }));
}

const EVENT_CATEGORY = [
  [/^CAR_/, 'Vehicle'],
  [/^PENALTY$/, 'Penalty'],
  [/^COMPONENT_|^MAINTENANCE_|^AUTO_REPAIR/, 'Component'],
  [/^CO_ALERT$/, 'Alert'],
];
const EVENT_STATUS = { PENALTY: 'penalty', CO_ALERT: 'alert', COMPONENT_BROKEN: 'warning', HANDLER_ERROR: 'warning' };

export async function getDailyReport(date) {
  // date = 'YYYY-MM-DD'; the backend groups events by UTC day
  const [summary, events, dashboard] = await Promise.all([
    request(`/api/logs/daily-summary?day=${encodeURIComponent(date)}`),
    request(`/api/logs/events?since=${date}T00:00:00&until=${date}T23:59:59&limit=200`),
    date === new Date().toISOString().slice(0, 10) ? request('/api/dashboard').catch(() => null) : null,
  ]);
  const occupied = dashboard?.zones?.reduce((total, zone) => total + zone.occupied + zone.reserved, 0);
  return {
    vehiclesEntered: summary.cars_arrived,
    vehiclesExited: summary.cars_departed,
    peakOccupancy: '—', // not recorded yet
    currentOccupancy: occupied ?? 'Unavailable',
    operationalAlerts: summary.co_alerts,
    componentFailures: summary.components_broken,
    maintenanceActions: summary.components_fixed,
    penalties: summary.penalties,
    events: events.map((event) => ({
      id: event.id,
      time: localTime(event.event_time),
      category: (EVENT_CATEGORY.find(([pattern]) => pattern.test(event.event_type)) || [null, 'System'])[1],
      event: event.event_type.replaceAll('_', ' ').toLowerCase().replace(/^./, (c) => c.toUpperCase()),
      location: event.parking_spot || event.gate_name || event.car_plate || '—',
      status: EVENT_STATUS[event.event_type] || 'ok',
    })),
  };
}

export async function getFinancialReport(date) {
  // TODO: Connect to a backend financial reporting endpoint when available.
  void date;
  return null;
}

export async function chargeCar(plateNumber, parkingCost = 0.0, chargingCost = 0.0) {
  return await request(
    `/car/${encodeURIComponent(plateNumber)}/charge?parking_cost=${parkingCost}&charging_cost=${chargingCost}`,
    { method: 'POST' }
  );
}

/**
 * Admin-only user directory. The backend is the source of truth for roles;
 * never keep a second editable user list in the browser.
 */
export async function getUsers() {
  return request('/api/auth/users');
}

export async function createUser({ username, password, role, permissions }) {
  return request('/api/auth/users', {
    method: 'POST',
    body: JSON.stringify({
      username,
      password,
      role,
      permissions,
    }),
  });
}

export async function updateUser(id, changes) {
  return request(`/api/auth/users/${id}`, { method: 'PUT', body: JSON.stringify(changes) });
}

export async function deleteUser(id) {
  return request(`/api/auth/users/${id}`, { method: 'DELETE' });
}

/**
 * Authentication with roles (Admin / Operator)
 */
export async function loginUser(username, password) {
  // The backend is the only authority: it checks the password, records the attempt (success or
  // failure) and returns the user's last 3 login attempts, shown after login.
  const formData = new URLSearchParams();
  formData.append('username', username);
  formData.append('password', password);

  let res;
  try {
    res = await fetch(`${BACKEND_BASE}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'ngrok-skip-browser-warning': '1' },
      body: formData,
    });
  } catch {
    throw new Error('Cannot reach the server. Is the backend running?');
  }

  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(typeof data.detail === 'string' ? data.detail : 'Invalid username or password.');
  }
  if (data.access_token) {
    localStorage.setItem('token', data.access_token);
  }
  return {
    username,
    name: username === 'admin' ? 'Administrator' : username,
    role: data.role || 'Operator',
    status: 'Active',
    loginAttempts: Array.isArray(data.login_attempts) ? data.login_attempts : [],
  };
}
