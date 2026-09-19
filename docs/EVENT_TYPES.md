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

Gate open/close webhooks currently update `gates` but create only the raw `WEBHOOK` event; they do not create a separate gate event type. New automation can add one using the rule below.

## Naming new operational events

Use **UPPER_SNAKE_CASE** with a stable area prefix and a past-tense action, such as `AUTOMATION_FAN_ON`, `AUTOMATION_GATE_OPENED`, or `MAINTENANCE_REPAIR_SENT`. Use `AUTOMATION_*` for automatic decisions and actions and `MAINTENANCE_*` for repair and maintenance work. Avoid reusing a type for a different meaning. Add a row here for each new type, including the exact `raw_data` keys, then write it through `app.services.parking.log_event(db, "TYPE", ...)` inside the caller's transaction and commit with that transaction.

The daily named metrics count `CAR_ARRIVED`, `CAR_PARKED`, `CAR_DEPARTED`, `PENALTY`, `COMPONENT_BROKEN`, `COMPONENT_FIXED`, and `CO_ALERT`. `penalty_total` sums valid numeric `FineAmount` values from `PENALTY` rows; missing or invalid amounts add zero. `busiest_hour` is the UTC hour (0–23) with the most `CAR_ARRIVED` rows; an empty day returns `null`, and ties use the earliest hour. Other types remain visible in `events_by_type`.
