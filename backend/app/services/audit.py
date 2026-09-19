"""Level 2: audit log of important actions, changes, repairs and system events.

    record_audit(db, "GATE_OPEN", actor="op1", target_type="gate", target_name="gateA")
    with audited(db, "GATE_OPEN", actor="op1", ...): do_it()      success, or failure + error text
    await record_audit_async("AUTO_REPAIR", target_type="fan", ...)   from async code (main.py)
    list_audit(db, actor=..., action=..., since=..., limit=50)     newest first

record_audit writes in its OWN short transaction (a separate session on the same database), so:
- it never commits or rolls back the caller's unfinished work;
- it never raises: an audit failure is logged, and the caller's real action carries on.
"""

import asyncio
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.audit_log import AuditLog

log = logging.getLogger(__name__)

# Column sizes in database/schema.sql: longer values are cut, not rejected
SIZES = {"actor": 64, "action": 64, "target_type": 32, "target_name": 64, "ip_address": 45}
SECRET_WORDS = ("password", "passwd", "token", "secret", "authorization")


def _cut(value: str | None, field: str) -> str | None:
    return value[: SIZES[field]] if value else None


def _safe_details(details):
    """JSON-safe copy (dates, Decimals -> text) with secret-looking keys masked."""
    if details is None:
        return None

    def mask(value):
        if isinstance(value, dict):
            return {k: "***" if any(w in str(k).lower() for w in SECRET_WORDS) else mask(v) for k, v in value.items()}
        if isinstance(value, list):
            return [mask(v) for v in value]
        return value

    return mask(json.loads(json.dumps(details, default=str)))


def record_audit(db: Session, action: str, *, actor: str | None = None, target_type: str | None = None,
                 target_name: str | None = None, success: bool = True, details: dict | list | None = None,
                 ip_address: str | None = None) -> AuditLog | None:
    """Returns the saved row, or None if it could not be saved (already logged)."""
    try:
        entry = AuditLog(
            action=_cut(action, "action") or "UNKNOWN",
            actor=_cut(actor, "actor"),
            target_type=_cut(target_type, "target_type"),
            target_name=_cut(target_name, "target_name"),
            success=success,
            details=_safe_details(details),
            ip_address=_cut(ip_address, "ip_address"),
        )
        with Session(bind=db.get_bind(), expire_on_commit=False) as own:
            own.add(entry)
            own.commit()
        return entry
    except Exception:
        log.exception("Could not write audit log %s %s/%s", action, target_type, target_name)
        return None


@contextmanager
def audited(db: Session, action: str, **fields) -> Iterator[None]:
    """Audit the wrapped block: success if it finishes, success=False with the error text if it
    raises (the error is re-raised unchanged). fields = record_audit's keyword arguments."""
    try:
        yield
    except Exception as exc:
        error = getattr(exc, "detail", None) or str(exc)
        details = {**(fields.pop("details", None) or {}), "error": str(error)[:500]}
        record_audit(db, action, success=False, details=details, **fields)
        raise
    record_audit(db, action, **fields)


async def record_audit_async(action: str, **fields) -> AuditLog | None:
    """For async code without a session (main.py routes, automation loops): same as record_audit,
    run in a worker thread with its own session so the event loop never waits on MySQL."""
    def run():
        with SessionLocal() as db:
            return record_audit(db, action, **fields)
    return await asyncio.to_thread(run)


def list_audit(db: Session, *, actor: str | None = None, action: str | None = None,
               target_type: str | None = None, target_name: str | None = None,
               since: datetime | None = None, until: datetime | None = None,
               limit: int = 50, offset: int = 0) -> list[AuditLog]:
    """Newest first. since / until compare with created_at (UTC)."""
    q = select(AuditLog)
    if actor:
        q = q.where(AuditLog.actor == actor)
    if action:
        q = q.where(AuditLog.action == action)
    if target_type:
        q = q.where(AuditLog.target_type == target_type)
    if target_name:
        q = q.where(AuditLog.target_name == target_name)
    if since:
        q = q.where(AuditLog.created_at >= since)
    if until:
        q = q.where(AuditLog.created_at <= until)
    q = q.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit).offset(offset)
    return list(db.scalars(q).all())
