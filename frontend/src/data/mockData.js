// Mock parking data.
// This will later be replaced with real backend data.

export const parkingSpots = Array.from({ length: 30 }, (_, index) => {
  const number = index + 1

  let status = 'free'

  // Example occupied spaces
  if ([2, 7, 11, 18, 23, 27].includes(number)) {
    status = 'occupied'
  }

  // Example space under maintenance
  if (number === 15) {
    status = 'maintenance'
  }

  return {
    name: `S${number}`,
    status,
    zone: 'ZONE1',
  }
})


// Calculate dashboard statistics automatically

export const dashboardStats = {
  totalSpaces: parkingSpots.length,

  availableSpaces: parkingSpots.filter(
    (spot) => spot.status === 'free'
  ).length,

  occupiedSpaces: parkingSpots.filter(
    (spot) => spot.status === 'occupied'
  ).length,

  // Temporary mock value.
  // Real backend should provide the actual number of cars inside.
  carsInside: 6,
}


// Mock activity data.
// This will later come from the database/backend.

export const recentActivities = [
  {
    id: 1,
    plate: 'WCT 759',
    event: 'Vehicle entered',
    spot: 'S4',
    time: '12:35 PM',
    status: 'Active',
  },
  {
    id: 2,
    plate: 'VBE 214',
    event: 'Vehicle parked',
    spot: 'S7',
    time: '12:28 PM',
    status: 'Parked',
  },
  {
    id: 3,
    plate: 'WXY 882',
    event: 'Payment completed',
    spot: 'S2',
    time: '12:20 PM',
    status: 'Completed',
  },
  {
    id: 4,
    plate: 'ABC 123',
    event: 'Vehicle exited',
    spot: '—',
    time: '12:15 PM',
    status: 'Departed',
  },
]