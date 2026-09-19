// Backend API client: every page gets its data from here.
// Settings come from web-interface/.env (copy .env.example).

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL
export const REFRESH_MS = Number(import.meta.env.VITE_REFRESH_MS) || 3000

if (!API_BASE_URL) {
  throw new Error('VITE_API_BASE_URL is not set: copy web-interface/.env.example to web-interface/.env')
}

// The logged-in user (and their token) lives here; Account.jsx reads the same key.
const USER_KEY = 'currentUser'

function getToken() {
  try {
    return JSON.parse(localStorage.getItem(USER_KEY))?.token
  } catch {
    return null
  }
}

async function request(path, options = {}) {
  const token = getToken()
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  })

  // Not logged in / token expired: back to the login page
  if (response.status === 401 && path !== '/api/auth/login') {
    localStorage.removeItem(USER_KEY)
    window.location.assign('/login')
    throw new Error('Please log in again.')
  }

  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed (${response.status})`)
  }

  return response.json()
}

// ---- formatting ---------------------------------------------------------

const ROLE_LABELS = { ADMIN: 'Admin', OPERATOR: 'Operator' }

// Our spot states -> the ParkingGrid's CSS classes
const SPOT_STATUS = {
  FREE: 'free',
  RESERVED: 'occupied', // a car is on its way there
  OCCUPIED: 'occupied',
  BROKEN: 'maintenance',
  MAINTENANCE: 'maintenance',
}

// Event log types -> RecentActivity rows (status = its CSS class)
const ACTIVITY = {
  CAR_ARRIVED: { event: 'Arrived at entrance', status: 'Active' },
  CAR_ENTERED: { event: 'Vehicle entered', status: 'Active' },
  CAR_PARKED: { event: 'Vehicle parked', status: 'Parked' },
  CAR_EXITING: { event: 'Left parking spot', status: 'Active' },
  PAYMENT_ACCEPTED: { event: 'Payment completed', status: 'Completed' },
  CAR_DEPARTED: { event: 'Vehicle exited', status: 'Departed' },
}

const SESSION_STATUS = {
  ENTERING: 'Entering',
  PARKED: 'Parked',
  EXITING: 'Exiting',
  COMPLETED: 'Exited',
}

// The simulator calls ordinary cars "Normal"; we store that as spot type "Any"
const CAR_TYPE_LABELS = { Any: 'Normal', Electric: 'Electric', Accessible: 'Accessible' }

// Shown for details the simulator does not provide (make, model, colour)
const NOT_PROVIDED = '—'

// Event times are stored in UTC; visit times use the simulator's own clock as-is.
function formatTime(value, { utc = false } = {}) {
  if (!value) return '—'
  const date = new Date(utc && !value.endsWith('Z') ? `${value}Z` : value)
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function formatDuration(start, end) {
  const minutes = Math.max(0, Math.round((new Date(end) - new Date(start)) / 60000))
  const hours = Math.floor(minutes / 60)
  return hours ? `${hours}h ${minutes % 60}m` : `${minutes}m`
}

function toVehicle(session) {
  return {
    plateNumber: session.car_plate,
    vehicleType: CAR_TYPE_LABELS[session.car_type] ?? NOT_PROVIDED,
    brand: NOT_PROVIDED,
    model: '',
    colour: NOT_PROVIDED,
    status: SESSION_STATUS[session.status] ?? session.status,
    parkingSpot: session.spot_name ?? '—',
    entryTime: formatTime(session.entry_time),
    exitTime: formatTime(session.exit_time),
    duration: session.exit_time ? formatDuration(session.entry_time, session.exit_time) : 'In progress',
  }
}

// ---- auth ---------------------------------------------------------------

export async function login(username, password) {
  const { access_token: token } = await request('/api/auth/login', {
    method: 'POST',
    body: new URLSearchParams({ username, password }),
  })
  const me = await request('/api/auth/me', { headers: { Authorization: `Bearer ${token}` } })

  const user = {
    username: me.username,
    name: me.username,
    role: ROLE_LABELS[me.role] ?? me.role,
    status: 'Active', // the backend just accepted this account
    token,
  }
  localStorage.setItem(USER_KEY, JSON.stringify(user))
  return user
}

export async function getBackendStatus() {
  const response = await fetch(`${API_BASE_URL}/health`)
  return response.ok
}

// ---- dashboard ----------------------------------------------------------

export async function getDashboardStats() {
  const data = await request('/api/dashboard')
  const sum = (key) => data.zones.reduce((total, zone) => total + zone[key], 0)
  return {
    totalSpaces: sum('total'),
    availableSpaces: data.total_free,
    occupiedSpaces: sum('occupied') + sum('reserved'),
    carsInside: data.cars_inside,
  }
}

export async function getParkingSpots() {
  const spots = await request('/api/dashboard/spots')
  return spots
    .filter((spot) => spot.purpose === 'Park')
    .map((spot) => ({ name: spot.name, status: SPOT_STATUS[spot.status] ?? 'maintenance', zone: spot.zone }))
    .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }))
}

export async function getRecentActivity(limit = 10) {
  const params = new URLSearchParams({ limit })
  Object.keys(ACTIVITY).forEach((type) => params.append('event_type', type))
  const events = await request(`/api/history/events?${params}`)
  return events.map((event) => ({
    id: event.id,
    plate: event.car_plate,
    ...ACTIVITY[event.event_type],
    spot: event.parking_spot ?? '—',
    time: formatTime(event.event_time, { utc: true }),
  }))
}

// ---- gates --------------------------------------------------------------

// Only the barriers configured as entrance / exit (ENTRY_GATE / EXIT_GATE in the backend .env)
export async function getGates() {
  const gates = await request('/api/dashboard/gates')
  return gates
    .filter((gate) => gate.role)
    .sort((a, b) => (a.role === 'entrance' ? -1 : 1) - (b.role === 'entrance' ? -1 : 1))
    .map((gate) => ({
      name: gate.name,
      role: gate.role,
      zone: gate.zone,
      state: gate.broken ? 'Broken' : gate.under_maintenance ? 'Maintenance' : gate.state,
      isOpen: gate.state === 'Open' || gate.state === 'Opening',
    }))
}

export async function openGate(gateName) {
  return request(`/api/control/gates/${encodeURIComponent(gateName)}/open`, { method: 'POST' })
}

export async function closeGate(gateName) {
  return request(`/api/control/gates/${encodeURIComponent(gateName)}/close`, { method: 'POST' })
}

// ---- vehicles -----------------------------------------------------------

// Latest visit of each car whose plate starts with `plate`
export async function searchVehicles(plate) {
  const sessions = await request(`/api/history/sessions?${new URLSearchParams({ plate, limit: 50 })}`)
  const latest = new Map()
  sessions.forEach((session) => {
    if (!latest.has(session.car_plate)) latest.set(session.car_plate, session) // newest first
  })
  return [...latest.values()].map(toVehicle)
}

export async function getVehicle(plate) {
  const vehicles = await searchVehicles(plate)
  return vehicles.find((vehicle) => vehicle.plateNumber.toLowerCase() === plate.toLowerCase()) ?? null
}
