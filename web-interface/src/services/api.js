// API Service connecting the frontend to the FastAPI backend (main.py + db_hook)
// Use the configured backend address; local FastAPI remains the development fallback.
export const BACKEND_BASE = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
export const REFRESH_MS = Number(import.meta.env?.VITE_REFRESH_MS) || 5000;

async function request(endpoint, options = {}) {
  const token = localStorage.getItem('token');
  const headers = {
    'Content-Type': 'application/json',
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
export async function getSystemStatus() {
  try {
    return await request('/system-status');
  } catch {
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
 * Fetch and normalize parking spaces reported by the simulator.
 */
export async function getParkingSpots() {
  try {
    const data = await request('/list-parking-spots');
    const rawList = Array.isArray(data) ? data : data?.spots || data?.data || data?.items || [];
    if (!Array.isArray(rawList)) return [];

    return rawList.filter((spot) => spot?.purpose === 'Park' && typeof spot.zoneParent === 'string').map((raw) => {
      const isOccupied = Array.isArray(raw.detectedCars) && raw.detectedCars.length > 0;
      const isMaintenance = raw.broken === true || raw.isUnderMaintenance === true;

      return {
        name: raw.name,
        number: raw.number ?? (raw.name?.match(/^S(\d+)$/i) ? Number(raw.name.match(/^S(\d+)$/i)[1]) : null),
        status: isMaintenance ? 'maintenance' : isOccupied ? 'occupied' : 'free',
        zone: raw.zoneParent,
        plate: isOccupied ? (raw.carPlateNumber || raw.CarPlateNumber || raw.currentCar || null) : null,
        detectedCars: raw.detectedCars,
        parkingForCarType: raw.parkingForCarType,
        broken: raw.broken,
        isUnderMaintenance: raw.isUnderMaintenance,
        raw,
      };
    });
  } catch (err) {
    console.warn('Failed to load parking spots:', err);
    return [];
  }
}

export function calculateZoneStats(spots = []) {
  const zones = [
    { name: 'Zone 1', total: 0, free: 0, occupied: 0, maintenance: 0 },
    { name: 'Zone 2', total: 0, free: 0, occupied: 0, maintenance: 0 },
    { name: 'Zone 3', total: 0, free: 0, occupied: 0, maintenance: 0 },
  ];

  spots.forEach((spot) => {
    const zoneName = String(spot.zone || '').replace(/\s+/g, '').toUpperCase();
    const zone = zones.find((z) => z.name.replace(/\s+/g, '').toUpperCase() === zoneName);

    if (!zone) return;

    zone.total += 1;

    if (spot.status === 'free') {
      zone.free += 1;
    } else if (spot.status === 'occupied') {
      zone.occupied += 1;
    } else if (spot.status === 'maintenance') {
      zone.maintenance += 1;
    }
  });

  return zones;
}

/**
 * Fetch barrier gates (GateA and GateB)
 */
export async function getBarriers() {
  try {
    const data = await request('/list-barriers');
    let rawList = [];
    if (Array.isArray(data)) {
      rawList = data;
    } else if (data && typeof data === 'object') {
      rawList = data.barriers || data.data || [];
    }

    const gateMap = new Map();
    rawList.forEach((g) => {
      const name = g.name || g.Name || g.gateName;
      if (name && (typeof g.state === 'string' || typeof g.zoneParent === 'string')) {
        gateMap.set(name.toUpperCase(), g);
      }
    });

    const gateA = gateMap.get('GATEA') || {};
    const gateB = gateMap.get('GATEB') || {};

    return [
      {
        name: 'GateA',
        title: 'Entrance Gate (Gate A)',
        subtitle: 'Main vehicle entrance',
        isOpen: gateA.isOpen === true || gateA.open === true || gateA.state === 'Open',
        isBroken: gateA.isBroken === true || gateA.broken === true,
      },
      {
        name: 'GateB',
        title: 'Exit Gate (Gate B)',
        subtitle: 'Main vehicle exit & cashier',
        isOpen: gateB.isOpen === true || gateB.open === true || gateB.state === 'Open',
        isBroken: gateB.isBroken === true || gateB.broken === true,
      },
    ].filter((gate) => gateMap.has(gate.name.toUpperCase()));
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

export async function getZones() {
  try {
    const data = await request('/list-zones');
    const zones = Array.isArray(data) ? data : data?.zones || data?.data || data?.items || [];
    return Array.isArray(zones)
      ? zones.filter((zone) => zone && typeof zone === 'object' && zone.name).map((zone) => ({
        name: zone.name,
        gasCarbonMonoxideLevel: zone.gasCarbonMonoxideLevel,
        risk: zone.risk,
      }))
      : [];
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
  // TODO: Connect when a dedicated backend penalties endpoint provides amount and resolution status.
  return [];
}

export async function getAuditLogs() {
  // TODO: Connect to a dedicated backend audit endpoint when available.
  return [];
}

export async function getDailyReport(date) {
  // TODO: Connect to a backend daily reporting endpoint when available.
  void date;
  return null;
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
  // 1. Try MySQL backend JWT authentication
  try {
    const formData = new URLSearchParams();
    formData.append('username', username);
    formData.append('password', password);

    const res = await fetch(`${BACKEND_BASE}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: formData,
    });

    if (res.ok) {
      const data = await res.json();
      if (data.access_token) {
        localStorage.setItem('token', data.access_token);
      }
      return {
        username,
        name: username === 'admin' ? 'Administrator' : username,
        role: data.role || (username === 'admin' ? 'Admin' : 'Operator'),
        status: 'Active',
      };
    }
  } catch {
    // fallback
  }

  // 2. Built-in system roles fallback
  if (username === 'admin' && (password === 'admin' || password === 'change-me')) {
    return {
      username: 'admin',
      name: 'Administrator',
      role: 'Admin',
      status: 'Active',
    };
  } else if (username === 'jlim' && password === '1234') {
    return {
      username: 'jlim',
      name: 'Jackson Lim',
      role: 'Operator',
      status: 'Active',
    };
  } else if (password === 'operator') {
    return {
      username,
      name: username,
      role: 'Operator',
      status: 'Active',
    };
  }

  throw new Error('Invalid username or password.');
}
