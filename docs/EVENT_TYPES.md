# Operational event types

`events` is the existing activity stream. `event_type` is an uppercase name; `event_time` is the UTC time the database row was logged. Simulator `ServerDateTime`, when present, remains in `raw_data` and can differ from `event_time`. `car_plate`, `parking_spot`, and `gate_name` are searchable columns; `raw_data` provides context. A missing context value is `NULL`.

The raw `WEBHOOK` row is saved before a webhook handler runs. A processed webhook can therefore produce both a `WEBHOOK` row and an operational row. `/api/logs/events` hides only `WEBHOOK` by default; `/api/logs/daily-summary` counts event rows by type, including `WEBHOOK`, but its named metrics use the operational types below.

| `event_type` | Meaning | `raw_data` |
| --- | --- | --- |
| `WEBHOOK` | Valid simulator webhook received, before processing | Full simulator payload, including `EventClass`, `EventId`, `SequenceId`, `ServerDateTime`; `event_id` and `sequence_id` are also indexed columns. |
| `WEBHOOK_BAD_SIGNATURE` | A webhook failed signature verification | Rejected simulator payload. |
| `SEQUENCE_GAP` | Received a webhook sequence number after a gap | `last`, `got`, `event_id`. |
| `SEQUENCE_OUT_OF_ORDER` | Received an older or repeated sequence number | `last`, `got`, `event_id`. |
| `HANDLER_ERROR` | A webhook handler raised an exception | `error` (truncated), `payload` (the simulator webhook). |
| `SYNC` | Initial simulator spots and gates copied into the database | `spots` and `gates` simulator lists. |
| `CAR_ARRIVED` | Car reached an entry spot and a visit began | Car spot action webhook when recorded by `record_arrival`; may be absent if allocation first created the visit. |
| `CAR_ENTERED` | Car drove through the entrance | Car spot action webhook. |
| `CAR_REJECTED` | No suitable free spot was found | Arrival/assignment context passed to `allocate_spot`, if any. |
| `SPOT_ASSIGNED` | A spot was reserved for a car | Arrival/assignment context passed to `allocate_spot`, if any. |
| `RESERVATION_CANCELLED` | A spot reservation was undone after a failed move | `NULL`. |
| `RESERVATION_EXPIRED` | A stale spot reservation was released | `NULL`. |
| `WRONG_SPOT` | Car parked in a different spot from the one reserved | `NULL`; actual spot is in `parking_spot`. |
| `CAR_PARKED` | Car occupied a parking spot | Car spot action webhook, when supplied. |
| `CAR_EXITING` | Car left its parking spot for the exit | Car spot action webhook, when supplied. |
| `CAR_CHARGED` | Exit charge calculated once | `minutes`, `parking_cost`, `charging_cost`. |
| `PAYMENT_ACCEPTED` | Simulator payment matched the charge | Payment webhook, when supplied. |
| `PAYMENT_REJECTED` | Payment was invalid, missing, duplicate, or did not match | `reason` and sometimes `payload` (payment webhook). |
| `CAR_DEPARTED` | Car left the park, or was sent away when full | Car spot action webhook, when supplied. |
| `SESSION_EXPIRED` | Stale active visit was closed | `NULL`. |
| `COMPONENT_BROKEN` | Gate or spot reported broken | Component webhook (`Type`, `Name`, and simulator metadata), when supplied. |
| `COMPONENT_FIXED` | Gate or spot reported fixed | Component webhook (`Type`, `Name`, and simulator metadata), when supplied. |
| `PENALTY` | Simulator assessed a fine | Full penalty webhook, including `Reason`, `FineAmount`, `Type`, `ComponentName`, and sometimes `CarPlateNumber`. |
| `CO_ALERT` | Simulator reported mid, high, or critical carbon monoxide | Full carbon monoxide webhook. |
| `INTEGRITY_REJECTED` | A request from the parking network was dropped: malformed, unsigned (strict mode), tampered, duplicated, flooding, or an unusable plate | `severity`, `reason`, `zone`, plus `source` and, for a duplicate, `copies` and `event_class`. |
| `INTEGRITY_WARNING` | A request was processed but is not trusted: a sequence gap, an out-of-order `SequenceId`, a backwards clock jump, an unknown spot/gate name, a simulator penalty, or a failed event handler | `severity`, `reason`, plus `missed`/`last`/`got`, `skew_seconds`, `field`, or `error` depending on the cause. |
| `SENSOR_ABNORMAL` | A spot sensor contradicted itself (second `CarIn` with no `CarOut`, occupancy with no plate, flapping, or occupied with no vehicle for a long time) | `severity`, `reason`, `zone`, `faults` (how many faults this spot has had). |
| `SPOT_MAINTENANCE` | A spot was withdrawn from availability or returned to it, by an operator or automatically after repeated sensor faults | `severity`, `reason`, `zone`, `auto`; on return also `was` (the previous reason). |
| `DOUBLE_PARKING` | One plate occupies more than one spot, or one spot reports more than one vehicle. Raised on the second `CarIn`, before the simulator's own penalty | `severity`, `reason`, `zone`, `spots`, `zones`, or `detected` for a spot-side report. |
| `VEHICLE_MISPARKED` | A car parked in a different spot from the one assigned to it; its reservation is released | `severity`, `reason`, `zone`, `assigned` (the spot it was sent to). |
| `PAYMENT_SUSPICIOUS` | A payment event did not match our own charge, named a car we never charged, repeated an already-settled visit, or the charge failed every attempt | `severity`, `reason`, `amount`, `expected`. |
| `PAYMENT_RETRY` | Payment was requested again after a failed or suspicious one | `severity`, `reason`, `expected`, `attempt`. |
| `PAYMENT_SETTLED` | The simulator accepted our charge, or a payment event matched it | `severity`, `reason`, `parking`, `charging`, `attempts`, or `amount`. |
| `GATE_FAILOVER` | A gate was unusable and traffic was moved to another one, or it started answering again | `severity`, `reason`, `zone`, `alternative` (the gate used instead). |
| `ZONE_CAPACITY` | The level map was loaded from the simulator, or the built-in fallback was used | `severity`, `reason`, `zones`, `spots`, or `error`. |

Gate open/close webhooks currently update `gates` but create only the raw `WEBHOOK` event; they do not create a separate gate event type. New automation can add one using the rule below.

## Incidents (Level 3)

`main.py` records every abnormal thing it notices or does through one function,
`record_incident()`. Each incident writes **both** an `events` row (the types above,
searchable on `/api/logs/events` and counted in the daily summary) and an `audit_logs`
row (visible on the Audit page), so nothing is only in memory. `raw_data` always carries
`severity` (`info` / `warning` / `critical`), `reason` and `zone`; the table lists what
each type adds. The live ring is served by `GET /api/incidents` and summarised by
`GET /api/reports/incidents`; rejected and duplicated requests also appear on the
Admin -> Integrity page (`GET /api/integrity/*`).

## Naming new operational events

Use **UPPER_SNAKE_CASE** with a stable area prefix and a past-tense action, such as `AUTOMATION_FAN_ON`, `AUTOMATION_GATE_OPENED`, or `MAINTENANCE_REPAIR_SENT`. Use `AUTOMATION_*` for automatic decisions and actions and `MAINTENANCE_*` for repair and maintenance work. Avoid reusing a type for a different meaning. Add a row here for each new type, including the exact `raw_data` keys, then write it through `app.services.parking.log_event(db, "TYPE", ...)` inside the caller's transaction and commit with that transaction.

The daily named metrics count `CAR_ARRIVED`, `CAR_PARKED`, `CAR_DEPARTED`, `PENALTY`, `COMPONENT_BROKEN`, `COMPONENT_FIXED`, and `CO_ALERT`. `penalty_total` sums valid numeric `FineAmount` values from `PENALTY` rows; missing or invalid amounts add zero. `busiest_hour` is the UTC hour (0–23) with the most `CAR_ARRIVED` rows; an empty day returns `null`, and ties use the earliest hour. Other types remain visible in `events_by_type`.
