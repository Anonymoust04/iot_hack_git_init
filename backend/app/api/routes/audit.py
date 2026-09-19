"""Level 2: audit log (Admin only).

GET /api/audit?actor=&action=&target_type=&target_name=&since=&until=&limit=&offset=   newest first

Response model lives here, not in app/schemas, so this feature touches no shared file.
"""

from datetime import datetime

from fastapi import APIRouter, Query

from app.api.deps import AdminUser, DbSession
from app.schemas import ORMModel
from app.services.audit import list_audit

router = APIRouter(prefix="/api/audit", tags=["audit"])


class AuditLogOut(ORMModel):
    id: int
    actor: str | None           # None = the system (automation)
    action: str
    target_type: str | None
    target_name: str | None
    success: bool
    details: dict | list | None
    ip_address: str | None
    created_at: datetime


@router.get("", response_model=list[AuditLogOut])
def search_audit(
    db: DbSession,
    _: AdminUser,
    actor: str | None = None,
    action: str | None = None,
    target_type: str | None = None,
    target_name: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return list_audit(db, actor=actor, action=action, target_type=target_type, target_name=target_name,
                      since=since, until=until, limit=limit, offset=offset)
