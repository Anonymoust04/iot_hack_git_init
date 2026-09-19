"""Level 2 environment rules, using fake simulator calls only."""

import asyncio

import main
import pytest


class StopAfterOneCheck(Exception):
    pass


@pytest.mark.parametrize(
    ("hour", "expected_action"),
    [(5, "on"), (6, "off"), (17, "off"), (18, "on")],
)
def test_lights_follow_simulator_day_night_boundaries(monkeypatch, hour, expected_action):
    lights = [
        {"name": "working", "isOn": expected_action == "off", "broken": False},
        {"name": "already_correct", "isOn": expected_action == "on", "broken": False},
        {"name": "broken", "isOn": expected_action == "off", "broken": True},
    ]
    commands = []

    async def simulator_call(endpoint, method="GET", **_):
        if endpoint == "list-lights":
            return lights
        commands.append((endpoint, method))
        return {"status": "success"}

    async def stop_after_check(_):
        raise StopAfterOneCheck

    monkeypatch.setattr(main, "_last_sim_hour", hour)
    monkeypatch.setattr(main, "call_simulator_api", simulator_call)
    monkeypatch.setattr(main.asyncio, "sleep", stop_after_check)

    with pytest.raises(StopAfterOneCheck):
        asyncio.run(main.auto_light_controller_worker())

    assert commands == [(f"lights/working/{expected_action}", "POST")]


@pytest.mark.parametrize("previously_ventilating", [False, True])
def test_safe_co_switches_off_fans_that_were_running(monkeypatch, previously_ventilating):
    fan = {"name": "fan1", "zoneParent": "ZONE1", "isOn": True,
           "broken": False, "isUnderMaintenance": False}
    commands = []

    async def simulator_call(endpoint, method="GET", **_):
        if endpoint == "list-exhaust-fans":
            return [fan.copy()]
        commands.append((endpoint, method))
        fan["isOn"] = False
        return {"status": "success"}

    async def no_audit(*_, **__):
        return None

    monkeypatch.setattr(main, "call_simulator_api", simulator_call)
    prior_state = {"ventilating": True, "fans_on": ["fan1"]} if previously_ventilating else {}
    monkeypatch.setattr(main, "zone_co", {"ZONE1": prior_state} if prior_state else {})
    monkeypatch.setattr(main, "_audit_fans", no_audit)

    asyncio.run(main.update_zone_co("ZONE1", 12.0, "Safe"))

    assert commands == [("exhaust-fans/fan1/off", "POST")]
    assert main.zone_co["ZONE1"]["ventilating"] is False


def test_high_numeric_co_keeps_fan_on_when_risk_label_is_stale():
    assert main.co_needs_ventilation("Safe", 63.0) is True
    assert main.co_needs_ventilation("Safe", 12.0) is False
