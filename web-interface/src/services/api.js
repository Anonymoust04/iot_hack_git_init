const API_BASE_URL = 'http://localhost:3000'

export async function getParkingSpots() {
  const response = await fetch(`${API_BASE_URL}/api/parking-spots`)

  if (!response.ok) {
    throw new Error('Failed to load parking spots')
  }

  return response.json()
}

export async function getRecentActivity() {
  const response = await fetch(`${API_BASE_URL}/api/activity`)

  if (!response.ok) {
    throw new Error('Failed to load recent activity')
  }

  return response.json()
}

export async function openGate(gateName) {
  const response = await fetch(
    `${API_BASE_URL}/api/gates/${gateName}/open`,
    {
      method: 'POST',
    }
  )

  if (!response.ok) {
    throw new Error('Failed to open gate')
  }

  return response
}

export async function closeGate(gateName) {
  const response = await fetch(
    `${API_BASE_URL}/api/gates/${gateName}/close`,
    {
      method: 'POST',
    }
  )

  if (!response.ok) {
    throw new Error('Failed to close gate')
  }

  return response
}