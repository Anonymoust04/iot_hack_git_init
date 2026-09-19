# Frontend

Framework not decided yet (React/Vite, Next.js, or plain server-rendered pages).

Whatever we pick, it only talks to our FastAPI backend, never to the simulator directly:

| Page | Backend endpoints |
| --- | --- |
| Login | `POST /api/auth/login` (form: username, password) → `access_token`, `role` |
| Dashboard | `GET /api/dashboard` (free/occupied per zone, gate states, `park_full`) |
| Spots | `GET /api/dashboard/spots?zone=ZONE1` |
| Control (Operator) | `POST /api/control/gates/{name}/{open\|close\|repair}`, `POST /api/control/cars/{plate}/goto/{dest}` |
| History | `GET /api/history/sessions?plate=&status=&since=&until=` |
| Users (Admin) | `GET/POST /api/auth/users` |

Send `Authorization: Bearer <access_token>` on every request after login.
Full interactive API docs: http://localhost:8000/docs once the backend is running.

The dev servers on ports 3000 and 5173 are already allowed via `CORS_ORIGINS` in `.env`.
