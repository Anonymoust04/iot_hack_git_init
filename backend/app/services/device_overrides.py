"""Process-local manual overrides for devices controlled by the dashboard."""

from threading import Lock

_lock = Lock()
_overrides: dict[str, bool] = {}


def set_manual_override(name: str, enabled: bool = True) -> None:
    with _lock:
        if enabled:
            _overrides[name] = True
        else:
            _overrides.pop(name, None)


def has_manual_override(name: str) -> bool:
    with _lock:
        return _overrides.get(name, False)


def clear_manual_override(name: str) -> None:
    set_manual_override(name, False)


def clear_manual_overrides(names: list[str]) -> None:
    with _lock:
        for name in names:
            _overrides.pop(name, None)