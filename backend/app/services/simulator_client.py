"""Thin wrapper around the Park Simulator REST API (base: SIM_BASE_URL).

Notes from the docs:
- list-* endpoints carry a simulated operational cost -> call them ONLY on startup
  or after a crash to resync. Keep state up to date from webhooks instead.
- Control endpoints return 201 with an empty body.
"""

import logging

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)


class SimulatorClient:
    def __init__(self) -> None:
        s = get_settings()
        self._email = s.sim_email
        self._password = s.sim_password
        self._http = httpx.Client(base_url=s.sim_base_url, timeout=s.sim_timeout_seconds)
        self._token: str | None = None

    # ---- auth ----

    def login(self) -> None:
        r = self._http.post("/auth/login", json={"email": self._email, "password": self._password})
        r.raise_for_status()
        self._token = r.json()["token"]
        log.info("Logged in to simulator")

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        if self._token is None:
            self.login()
        r = self._http.request(method, path, headers={"Authorization": f"Bearer {self._token}"}, **kwargs)
        if r.status_code == 401:  # token expired -> re-login once
            self.login()
            r = self._http.request(method, path, headers={"Authorization": f"Bearer {self._token}"}, **kwargs)
        r.raise_for_status()
        return r

    # ---- discovery (use sparingly!) ----

    def list_parking_spots(self) -> list[dict]:
        return self._request("GET", "/list-parking-spots").json()

    def list_barriers(self) -> list[dict]:
        return self._request("GET", "/list-barriers").json()

    def list_lights(self) -> list[dict]:
        return self._request("GET", "/list-lights").json()

    def list_exhaust_fans(self) -> list[dict]:
        return self._request("GET", "/list-exhaust-fans").json()

    def list_alarms(self) -> list[dict]:
        return self._request("GET", "/list-alarms").json()

    def list_zones(self) -> list[dict]:
        return self._request("GET", "/list-zones").json()

    def test_webhook(self) -> str:
        return self._request("GET", "/test").text

    # ---- gates ----

    def open_gate(self, name: str) -> None:
        self._request("POST", f"/barrier-gates/{name}/open")

    def close_gate(self, name: str) -> None:
        self._request("POST", f"/barrier-gates/{name}/close")

    def repair_gate(self, name: str) -> None:
        self._request("POST", f"/barrier-gates/{name}/repair")

    # ---- lights / fans / spots (level 2+, wired now for convenience) ----

    def light(self, name: str, on: bool) -> None:
        self._request("POST", f"/lights/{name}/{'on' if on else 'off'}")

    def light_group(self, group: str, on: bool) -> None:
        self._request("POST", f"/lights/group/{group}/{'on' if on else 'off'}")

    def fan(self, name: str, on: bool) -> None:
        self._request("POST", f"/exhaust-fans/{name}/{'on' if on else 'off'}")

    def repair_fan(self, name: str) -> None:
        self._request("POST", f"/exhaust-fans/{name}/repair")

    def repair_spot(self, name: str) -> None:
        self._request("POST", f"/parking-spots/{name}/repair")

    # ---- cars ----

    def car_goto(self, plate: str, destination: str) -> None:
        """destination: a parking spot name, 'exit', or 'leavepark'."""
        self._request("POST", f"/car/{plate}/goto/{destination}")

    def charge_car(self, plate: str, parking_cost: float, charging_cost: float) -> None:
        """Only call once, and only when the car is at an exit spot (penalties otherwise)."""
        self._request(
            "POST",
            f"/car/{plate}/charge",
            params={"parkingCost": parking_cost, "chargingCost": charging_cost},
        )


_client: SimulatorClient | None = None


def get_simulator() -> SimulatorClient:
    global _client
    if _client is None:
        _client = SimulatorClient()
    return _client
