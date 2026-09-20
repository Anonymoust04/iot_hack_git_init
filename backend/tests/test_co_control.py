"""CO monitoring in main.py: fans ON from risk Mid (simulator's own rating), OFF when back to Safe.
The simulator is faked: no real fan is switched."""

import asyncio

import main
import pytest

from app.services import device_overrides


class FakeSim:
    """list-exhaust-fans / list-zones / exhaust-fans/{name}/on|off, recording every command."""

    def __init__(self):
        self.fans = {
            "fan1": {"name": "fan1", "zoneParent": "ZONE1", "isOn": False, "broken": False, "isUnderMaintenance": False},
            "fan2": {"name": "fan2", "zoneParent": "ZONE1", "isOn": False, "broken": False, "isUnderMaintenance": False},
            "fanX": {"name": "fanX", "zoneParent": "ZONE1", "isOn": False, "broken": True, "isUnderMaintenance": False},
            "fan9": {"name": "fan9", "zoneParent": "ZONE2", "isOn": False, "broken": False, "isUnderMaintenance": False},
        }
        self.commands = []

    async def call(self, endpoint, method="GET", params=None, json_data=None):
        if endpoint == "list-zones":
            return []      # the app's own CO worker may poll while a test runs: keep it harmless
        if endpoint == "list-exhaust-fans":
            return [dict(f) for f in self.fans.values()]
        if endpoint.startswith("exhaust-fans/"):
            _, name, action = endpoint.split("/")
            self.fans[name]["isOn"] = action == "on"
            self.commands.append((name, action))
            return {"status": "success"}
        raise AssertionError(f"unexpected simulator call {endpoint}")


@pytest.fixture
def sim(monkeypatch):
    fake = FakeSim()
    monkeypatch.setattr(main, "call_simulator_api", fake.call)
    # main.py's own maintenance worker runs in the shared test app and would send its own fan
    # commands through this fake while the test is asserting: pause it for the duration.
    async def noop(*args, **kwargs):
        return None
    for worker_step in ("_maintain_fan", "_maintain_spot", "_maintain_gate"):
        monkeypatch.setattr(main, worker_step, noop)
    # ventilate_zone also consults two process-wide dicts that other tests and the workers write to:
    # a fan left under a dashboard override, or marked broken, would silently be skipped here.
    monkeypatch.setattr(main, "component_health", {})
    monkeypatch.setattr(device_overrides, "_overrides", {})
    monkeypatch.setattr(main, "zone_co", {})
    monkeypatch.setattr(main, "_audit_fans", lambda *a, **k: asyncio.sleep(0))
    return fake


def run(coro):
    return asyncio.run(coro)


def test_mid_turns_zone_fans_on_and_safe_turns_them_off(sim):
    run(main.update_zone_co("ZONE1", 3.2, "Safe"))
    assert sim.commands == []                                           # Safe: nothing to do
    sim.commands.clear()

    run(main.update_zone_co("ZONE1", 63.5, "Mid"))
    assert sorted(set(sim.commands)) == [("fan1", "on"), ("fan2", "on")]   # only ZONE1's working fans
    assert main.zone_co["ZONE1"]["ventilating"] is True

    sim.commands.clear()
    run(main.update_zone_co("ZONE1", 88.0, "High"))
    assert sim.commands == []                                           # already on: no repeat commands

    run(main.update_zone_co("ZONE1", 12.0, "Safe"))
    assert sorted(set(sim.commands)) == [("fan1", "off"), ("fan2", "off")]
    assert main.zone_co["ZONE1"]["ventilating"] is False
    assert sim.fans["fan9"]["isOn"] is False                            # other zone untouched


def test_broken_fans_are_left_alone_and_retried_after_repair(sim):
    run(main.update_zone_co("ZONE1", 70, "Critical"))
    assert ("fanX", "on") not in sim.commands                           # broken: no command (penalty)
    sim.fans["fanX"]["broken"] = False                                  # repaired by maintenance
    run(main.update_zone_co("ZONE1", 72, "Critical"))                   # next reading while ventilating
    assert ("fanX", "on") in sim.commands


def test_reading_without_risk_label_uses_the_mid_line(sim):
    run(main.update_zone_co("ZONE2", 49.9, None))
    assert sim.commands == []
    run(main.update_zone_co("ZONE2", 50.0, None))
    assert sim.commands == [("fan9", "on")]


def test_co_webhook_triggers_ventilation(sim, client):
    client.post("/webhook", json={"EventClass": "carbon_monoxide_event", "ZoneName": "ZONE1",
                                  "CarbonMonoxideLevel": 63.564693, "DangerLevel": "Mid", "EventId": "co-test-1",
                                  "SequenceId": 1, "ServerDateTime": "2026-09-20 05:00:00"})
    for _ in range(80):
        if {("fan1", "on"), ("fan2", "on")} <= set(sim.commands):
            break
        asyncio.run(asyncio.sleep(0.1))
    assert {("fan1", "on"), ("fan2", "on")} <= set(sim.commands)
    status = {z["name"]: z for z in client.get("/co-status").json()}
    assert status["ZONE1"]["risk"] == "Mid" and status["ZONE1"]["ventilating"] is True
