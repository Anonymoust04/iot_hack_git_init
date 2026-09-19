-- =====================================================================
-- Step 8 — Useful dashboard / debugging queries.
-- Run in MySQL Workbench / DBeaver, or copy into SQLAlchemy text().
-- Times are UTC. "Today" below means the UTC day; for a local-time day, have the
-- backend compute the start/end of the local day in UTC and pass them as parameters.
-- =====================================================================

-- Total parking spots (only real parking bays, not entry/exit spots)
SELECT COUNT(*) AS total_spots
FROM parking_spots
WHERE purpose = 'Park';

-- Free spots (computed, never stored as a counter)
SELECT COUNT(*) AS free_spots
FROM parking_spots
WHERE purpose = 'Park' AND status = 'FREE'
  AND broken = FALSE AND under_maintenance = FALSE;

-- Occupied spots (RESERVED counts as taken: a car is on its way)
SELECT COUNT(*) AS occupied_spots
FROM parking_spots
WHERE purpose = 'Park' AND status IN ('OCCUPIED', 'RESERVED');

-- Free / reserved / occupied / unavailable per zone (dashboard main view)
SELECT zone,
       COUNT(*)                                          AS total,
       SUM(status = 'FREE')                              AS free,
       SUM(status = 'RESERVED')                          AS reserved,
       SUM(status = 'OCCUPIED')                          AS occupied,
       SUM(status IN ('BROKEN', 'MAINTENANCE'))          AS unavailable
FROM parking_spots
WHERE purpose = 'Park'
GROUP BY zone
ORDER BY zone;

-- Cars currently parked, with where and since when
SELECT ps.car_plate, sp.name AS spot, sp.zone, ps.parked_time
FROM parking_sessions ps
JOIN parking_spots sp ON sp.id = ps.parking_spot_id
WHERE ps.status = 'PARKED'
ORDER BY ps.parked_time;

-- Active sessions = every car inside the car park (entering, parked or exiting)
SELECT ps.id, ps.car_plate, ps.status, sp.name AS spot, ps.entry_time,
       TIMESTAMPDIFF(MINUTE, ps.entry_time, UTC_TIMESTAMP()) AS minutes_so_far
FROM parking_sessions ps
LEFT JOIN parking_spots sp ON sp.id = ps.parking_spot_id
WHERE ps.status <> 'COMPLETED'
ORDER BY ps.entry_time;

-- Today's completed sessions
SELECT ps.id, ps.car_plate, sp.name AS spot, ps.entry_time, ps.exit_time,
       ps.parking_cost, ps.charging_cost, ps.payment_status
FROM parking_sessions ps
LEFT JOIN parking_spots sp ON sp.id = ps.parking_spot_id
WHERE ps.status = 'COMPLETED'
  AND ps.exit_time >= UTC_DATE() AND ps.exit_time < UTC_DATE() + INTERVAL 1 DAY
ORDER BY ps.exit_time DESC;

-- Revenue today (paid sessions only)
SELECT COALESCE(SUM(parking_cost), 0)                      AS parking_revenue,
       COALESCE(SUM(charging_cost), 0)                     AS charging_revenue,
       COALESCE(SUM(parking_cost + charging_cost), 0)      AS total_revenue,
       COUNT(*)                                            AS paid_sessions
FROM parking_sessions
WHERE payment_status = 'PAID'
  AND exit_time >= UTC_DATE() AND exit_time < UTC_DATE() + INTERVAL 1 DAY;

-- Latest 20 events (activity feed)
SELECT id, event_time, event_type, car_plate, parking_spot, gate_name
FROM events
ORDER BY id DESC
LIMIT 20;

-- Raw simulator webhooks (use this to learn the payload format!)
SELECT id, event_time, raw_data
FROM events
WHERE event_type = 'WEBHOOK'
ORDER BY id DESC
LIMIT 20;

-- History for one car plate: all visits, then all events
SELECT ps.*, sp.name AS spot
FROM parking_sessions ps
LEFT JOIN parking_spots sp ON sp.id = ps.parking_spot_id
WHERE ps.car_plate = 'ABC123'
ORDER BY ps.id DESC;

SELECT event_time, event_type, parking_spot, gate_name
FROM events
WHERE car_plate = 'ABC123'
ORDER BY id DESC;

-- Current gate states
SELECT name, zone, state, broken, under_maintenance, updated_at
FROM gates
ORDER BY name;

-- ---------------------------------------------------------------------
-- Debugging: data that should never exist. Each query should return 0 rows.
-- ---------------------------------------------------------------------

-- A car holding two spots at once
SELECT current_car, COUNT(*) FROM parking_spots
WHERE current_car IS NOT NULL GROUP BY current_car HAVING COUNT(*) > 1;

-- A car with more than one active session
SELECT car_plate, COUNT(*) FROM parking_sessions
WHERE status <> 'COMPLETED' GROUP BY car_plate HAVING COUNT(*) > 1;

-- RESERVED/OCCUPIED spot without a car, or FREE spot with a car
SELECT name, status, current_car FROM parking_spots
WHERE (status IN ('RESERVED', 'OCCUPIED') AND current_car IS NULL)
   OR (status = 'FREE' AND current_car IS NOT NULL);
