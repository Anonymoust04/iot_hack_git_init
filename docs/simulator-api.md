# Park Simulator API: quick reference

Base path: `/api/v1`. Every endpoint except login needs `Authorization: Bearer <token>`.
Wrapped in code by [`backend/app/services/simulator_client.py`](../backend/app/services/simulator_client.py).

## Auth
- `POST /auth/login` with `{"email": "<Name>", "password": "<Password>"}` (from simulator settings) returns `{"token": "..."}`

## Discovery (COSTLY: call only once per level load or after a crash)
| Endpoint | Returns |
| --- | --- |
| `GET /list-parking-spots` | `name, purpose (Park/EntrySpot/ExitSpot), parkingForCarType (Electric/Accessible/Any), zoneParent, detectedCars[], broken, isUnderMaintenance` |
| `GET /list-barriers` | `name, zoneParent, broken, isUnderMaintenance, state (Open/Closed/Opening/Closing)` |
| `GET /list-lights` | `name, group, zoneParent, isOn` |
| `GET /list-exhaust-fans` | `name, zoneParent, broken, isUnderMaintenance, isOn` |
| `GET /list-alarms` | `name, problem` |
| `GET /list-zones` | `name, gasCarbonMonoxideLevel, risk` |
| `GET /test` | triggers a test webhook call to us |

## Control (all return `201` with an empty body)
- `POST /barrier-gates/{name}/open|close|repair`
- `POST /lights/{name}/on|off`, `POST /lights/group/{group}/on|off`
- `POST /exhaust-fans/{name}/on|off|repair`
- `POST /parking-spots/{name}/repair`
- `POST /car/{plate}/goto/{destination}`: destination = spot name | `exit` | `leavepark`
- `POST /car/{plate}/charge?parkingCost=X&chargingCost=Y`

## Rules and penalties
- Sending a car to an **occupied** spot → penalty.
- Charge **only** when the car is at an exit spot, **only once**.
- Charging cost for a **non-electric** car → penalty.
- Charge = minutes in park; electric cars pay ×2 (see `services/billing.py`).
- Park full → send arriving cars `leavepark`.
