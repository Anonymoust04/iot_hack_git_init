"""Manual control of park components (Operator + Admin)."""

from typing import Literal

from fastapi import APIRouter, status

from app.api.deps import AdminUser, DbSession, OperatorUser
from app.services.parking import sync_from_simulator
from app.services.simulator_client import get_simulator

router = APIRouter(prefix="/api/control", tags=["control"])


@router.post("/gates/{name}/{action}", status_code=status.HTTP_202_ACCEPTED)
def gate_action(name: str, action: Literal["open", "close", "repair"], _: OperatorUser):
    sim = get_simulator()
    {"open": sim.open_gate, "close": sim.close_gate, "repair": sim.repair_gate}[action](name)
    # Gate.state is updated when the simulator's webhook reports the new state.
    return {"gate": name, "action": action}


@router.post("/cars/{plate}/goto/{destination}", status_code=status.HTTP_202_ACCEPTED)
def car_goto(plate: str, destination: str, _: OperatorUser):
    get_simulator().car_goto(plate, destination)
    return {"car": plate, "destination": destination}


@router.post("/spots/{name}/repair", status_code=status.HTTP_202_ACCEPTED)
def repair_spot(name: str, _: OperatorUser):
    get_simulator().repair_spot(name)
    return {"spot": name, "action": "repair"}


@router.post("/sync")
def resync(db: DbSession, _: AdminUser):
    """Full resync from simulator. Costly — use only after a crash / level load."""
    sync_from_simulator(db, get_simulator())
    return {"ok": True}


@router.post("/test-webhook")
def test_webhook(_: AdminUser):
    return {"simulator": get_simulator().test_webhook()}
