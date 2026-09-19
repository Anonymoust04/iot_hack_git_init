"""Fine-grained permissions used by the dashboard RBAC layer."""

from enum import StrEnum


class Permission(StrEnum):
    USER_MANAGEMENT = "USER_MANAGEMENT"
    FINANCIAL_REPORTS = "FINANCIAL_REPORTS"
    GATE_CONTROL = "GATE_CONTROL"
    LIGHT_CONTROL = "LIGHT_CONTROL"
    FAN_CONTROL = "FAN_CONTROL"
    REPAIR = "REPAIR"


ALL_PERMISSIONS = frozenset(Permission)
OPERATOR_DEFAULT_PERMISSIONS = frozenset({
    Permission.GATE_CONTROL,
    Permission.LIGHT_CONTROL,
    Permission.FAN_CONTROL,
    Permission.REPAIR,
})
