"""Manual control of park components (Operator + Admin)."""

from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Request, status

from app.api.deps import AdminUser, DbSession, FanControlUser, GateControlUser, LightControlUser, OperatorUser, RepairUser
from app.models import GateState
from app.services.audit import audited
from app.services.device_overrides import set_manual_override
from app.services.components import set_gate_state
from app.services.login_attempts import client_ip
from app.services.simulator_client import get_simulator
from app.services.sync import sync_from_simulator

router = APIRouter(prefix="/api/control", tags=["control"])


def _audit(db, request: Request, user, action: str, target_type: str, target_name: str | None = None, **details):
    """Audit log entry for an operator action: who, what, which component, success or the error."""
    return audited(db, action, actor=user.username, target_type=target_type, target_name=target_name,
                   details=details or None, ip_address=client_ip(request))


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
def gate_action(name: str, action: Literal["open", "close", "repair"], request: Request, db: DbSession,
                user: GateControlUser):
    sim = get_simulator()
    with _audit(db, request, user, f"GATE_{action.upper()}", "gate", name):
        _simulator_call({"open": sim.open_gate, "close": sim.close_gate, "repair": sim.repair_gate}[action], name)
    set_manual_override(name)
    # Show the movement at once; the simulator's gate_action webhook (and the periodic sync)
    # then records the final Open / Closed.
    moving = {"open": GateState.OPENING, "close": GateState.CLOSING}.get(action)
    if moving:
        set_gate_state(db, name, moving)
    return {"gate": name, "action": action}


@router.post("/cars/{plate}/goto/{destination}", status_code=status.HTTP_202_ACCEPTED)
def car_goto(plate: str, destination: str, request: Request, db: DbSession, user: OperatorUser):
    with _audit(db, request, user, "CAR_MOVED", "car", plate, destination=destination):
        _simulator_call(get_simulator().car_goto, plate, destination)
    return {"car": plate, "destination": destination}


@router.post("/spots/{name}/repair", status_code=status.HTTP_202_ACCEPTED)
def repair_spot(name: str, request: Request, db: DbSession, user: RepairUser):
    with _audit(db, request, user, "SPOT_REPAIR", "spot", name):
        _simulator_call(get_simulator().repair_spot, name)
    return {"spot": name, "action": "repair"}


@router.post("/lights/{name}/{action}", status_code=status.HTTP_202_ACCEPTED)
def light_action(name: str, action: Literal["on", "off"], request: Request, db: DbSession, user: LightControlUser):
    with _audit(db, request, user, f"LIGHT_{action.upper()}", "light", name):
        _simulator_call(get_simulator().light, name, action == "on")
    set_manual_override(name)
    return {"light": name, "action": action}


@router.post("/lights/group/{group}/{action}", status_code=status.HTTP_202_ACCEPTED)
def light_group_action(group: str, action: Literal["on", "off"], request: Request, db: DbSession,
                       user: LightControlUser):
    with _audit(db, request, user, f"LIGHT_GROUP_{action.upper()}", "light", group):
        _simulator_call(get_simulator().light_group, group, action == "on")
    set_manual_override(f"group:{group}")
    return {"group": group, "action": action}


@router.post("/fans/{name}/{action}", status_code=status.HTTP_202_ACCEPTED)
def fan_action(name: str, action: Literal["on", "off", "repair"], request: Request, db: DbSession,
               user: FanControlUser):
    simulator = get_simulator()
    with _audit(db, request, user, f"FAN_{action.upper()}", "fan", name):
        if action == "repair":
            _simulator_call(simulator.repair_fan, name)
        else:
            _simulator_call(simulator.fan, name, action == "on")
    set_manual_override(name)
    return {"fan": name, "action": action}


@router.post("/sync")
def resync(request: Request, db: DbSession, user: AdminUser):
    """Full resync from simulator. Costly — use only after a crash / level load."""
    with _audit(db, request, user, "SIMULATOR_RESYNC", "system"):
        return sync_from_simulator(db, get_simulator())


@router.post("/test-webhook")
def test_webhook(_: AdminUser):
    return {"simulator": get_simulator().test_webhook()}
