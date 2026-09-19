"""Manual control of park components (Operator + Admin)."""

from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, status

from app.api.deps import AdminUser, DbSession, OperatorUser
from app.models import GateState
from app.services.components import set_gate_state
from app.services.simulator_client import get_simulator
from app.services.sync import sync_from_simulator

router = APIRouter(prefix="/api/control", tags=["control"])


def _simulator_call(fn, *args):
    """Run a simulator command; a refusal or an unreachable simulator becomes a readable 502."""
    try:
        fn(*args)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"Simulator refused ({exc.response.status_code}): {exc.response.text[:200]}")
    except httpx.RequestError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Simulator unreachable: {exc}")


@router.post("/gates/{name}/{action}", status_code=status.HTTP_202_ACCEPTED)
def gate_action(name: str, action: Literal["open", "close", "repair"], db: DbSession, _: OperatorUser):
    sim = get_simulator()
    _simulator_call({"open": sim.open_gate, "close": sim.close_gate, "repair": sim.repair_gate}[action], name)
    # Show the movement at once; the simulator's gate_action webhook (and the periodic sync)
    # then records the final Open / Closed.
    moving = {"open": GateState.OPENING, "close": GateState.CLOSING}.get(action)
    if moving:
        set_gate_state(db, name, moving)
    return {"gate": name, "action": action}


@router.post("/cars/{plate}/goto/{destination}", status_code=status.HTTP_202_ACCEPTED)
def car_goto(plate: str, destination: str, _: OperatorUser):
    _simulator_call(get_simulator().car_goto, plate, destination)
    return {"car": plate, "destination": destination}


@router.post("/spots/{name}/repair", status_code=status.HTTP_202_ACCEPTED)
def repair_spot(name: str, _: OperatorUser):
    get_simulator().repair_spot(name)
    return {"spot": name, "action": "repair"}


@router.post("/sync")
def resync(db: DbSession, _: AdminUser):
    """Full resync from simulator. Costly — use only after a crash / level load."""
    return sync_from_simulator(db, get_simulator())


@router.post("/test-webhook")
def test_webhook(_: AdminUser):
    return {"simulator": get_simulator().test_webhook()}
