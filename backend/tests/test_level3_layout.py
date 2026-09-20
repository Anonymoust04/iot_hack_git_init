"""The level map is read from the simulator, not hard-coded.

Level 3 has a different map from Level 2 (more zones, more spots, and through-gates
like gate7 / gate19), so main.py builds its zone registry from GET /list-parking-spots
and GET /list-barriers at startup.
"""

import asyncio

import main
import pytest


def sim_spot(name, purpose="Park", zone="ZONE1", car_type="Any"):
    return {"name": name, "purpose": purpose, "zoneParent": zone,
            "parkingForCarType": car_type, "broken": False, "isUnderMaintenance": False}


def sim_gate(name, zone="ZONE1", state="Closed"):
    return {"name": name, "zoneParent": zone, "state": state,
            "broken": False, "isUnderMaintenance": False}


# A three-zone Level 3 style map: zone 2 has TWO entrances, and gate7 / gate19 sit
# outside every zone.
LEVEL3_SPOTS = (
    [sim_spot(f"A{i}", zone="ZONE1") for i in range(1, 6)]
    + [sim_spot("ENTRY1", purpose="EntrySpot", zone="ZONE1"),
       sim_spot("EXIT1", purpose="ExitSpot", zone="ZONE1")]
    # B3 is listed twice, the second time as an Electric bay: the simulator may repeat
    # a spot, and it must still be counted once.
    + [sim_spot(f"B{i}", zone="ZONE2") for i in range(1, 8)]
    + [sim_spot("B3", zone="ZONE2", car_type="Electric")]
    + [sim_spot("ENTRY2", purpose="EntrySpot", zone="ZONE2"),
       sim_spot("ENTRY3", purpose="EntrySpot", zone="ZONE2"),
       sim_spot("EXIT2", purpose="ExitSpot", zone="ZONE2")]
    + [sim_spot(f"C{i}", zone="ZONE10") for i in range(1, 4)]
    + [sim_spot("ENTRY4", purpose="EntrySpot", zone="ZONE10"),
       sim_spot("EXIT4", purpose="ExitSpot", zone="ZONE10")]
)

LEVEL3_GATES = (
    [sim_gate("gate1", "ZONE1"), sim_gate("gate2", "ZONE1")]
    + [sim_gate("gate3", "ZONE2"), sim_gate("gate4", "ZONE2"), sim_gate("gate5", "ZONE2")]
    + [sim_gate("gate20", "ZONE10"), sim_gate("gate21", "ZONE10")]
    + [sim_gate("gate7", ""), sim_gate("gate19", "")]
)


@pytest.fixture
def level3(monkeypatch):
    """Replace the registry with the Level 3 style map, then restore Level 2."""
    saved = {name: getattr(main, name) for name in (
        "ZONE_DEFS", "ZONES", "VALID_PARKING_SPOTS", "SPOT_ZONE", "ZONE_CAPACITY",
        "ENTRANCE_GATES", "EXIT_GATES", "ALL_GATES", "GATE_ZONE", "GATE_ENTRY_SPOT",
        "GATE_EXIT_SPOT", "ENTRY_SPOT_GATE", "EXIT_SPOT_GATE", "ENTRY_SPOTS",
        "SPOT_CAR_TYPES", "OTHER_GATES", "layout_source")}
    saved_spots = dict(main.parking_spots)

    defs = main.build_zone_defs(LEVEL3_SPOTS, LEVEL3_GATES)
    main.apply_zone_defs(defs, source="test")
    yield defs

    for name, value in saved.items():
        setattr(main, name, value)
    main.parking_spots.clear()
    main.parking_spots.update(saved_spots)


def test_zones_are_built_from_the_simulator_map(level3):
    assert [z["id"] for z in level3] == ["ZONE1", "ZONE2", "ZONE10"]   # ZONE10 sorts after ZONE2
    assert [z["label"] for z in level3] == ["Zone 1", "Zone 2", "Zone 10"]
    # Only Park spots count towards capacity; EntrySpot / ExitSpot do not.
    assert main.ZONE_CAPACITY == {"ZONE1": 5, "ZONE2": 7, "ZONE10": 3}
    assert len(main.VALID_PARKING_SPOTS) == 15
    assert main.SPOT_CAR_TYPES["B3"] == "Electric"
    assert main.get_spot_type("A1") == "Any"


def test_gates_are_paired_with_their_zone_entry_and_exit_spots(level3):
    # Zone 2 has two EntrySpots, so its first two gates are entrances.
    assert main.ENTRANCE_GATES == ["gate1", "gate3", "gate4", "gate20"]
    assert main.EXIT_GATES == ["gate2", "gate5", "gate21"]
    assert main.GATE_ENTRY_SPOT == {"gate1": "ENTRY1", "gate3": "ENTRY2",
                                    "gate4": "ENTRY3", "gate20": "ENTRY4"}
    assert main.GATE_EXIT_SPOT == {"gate2": "EXIT1", "gate5": "EXIT2", "gate21": "EXIT4"}
    assert main.entry_spot_for_gate("gate4") == "ENTRY3"
    assert main.gate_for_entry_spot("ENTRY3") == "gate4"
    assert main.exit_gate_for_spot("EXIT4") == "gate21"


def test_a_spot_knows_every_entrance_that_can_reach_it(level3):
    # Zone 2 is served by two entrances: losing one must not strand its spots.
    assert main.entrance_gates_for_spot("B1") == ["gate3", "gate4"]
    assert main.spot_gate("B1") == "gate3"
    assert main.exit_gates_for_zone(main.zone_of_spot("B1")) == ["gate5"]
    assert main.zone_of_gate("gate21") == "ZONE10"


def test_gates_outside_every_zone_are_not_used_for_routing(level3):
    assert main.OTHER_GATES == ["gate7", "gate19"]
    assert "gate7" not in main.ENTRANCE_GATES and "gate7" not in main.EXIT_GATES
    assert {"gate7", "gate19"} <= set(main.ALL_GATES)   # still commandable
    assert main.zone_of_gate("gate7") is None


def test_runtime_state_follows_the_new_map(level3):
    assert set(main.parking_spots) == set(main.VALID_PARKING_SPOTS)
    assert all(main.parking_spots[s] for s in main.VALID_PARKING_SPOTS)   # all free
    assert {"ENTRY1", "ENTRY2", "ENTRY3", "ENTRY4"} <= set(main.entry_queues)
    assert {"ENTRY1", "ENTRY2", "ENTRY3", "ENTRY4"} <= set(main.entry_occupied)
    for gate in main.ALL_GATES:
        assert gate in main.gate_queues


def test_env_can_override_which_gate_is_an_entrance(monkeypatch):
    from app.config import get_settings

    # Force the SECOND gate of Zone 1 to be the entrance instead of the first.
    monkeypatch.setattr(get_settings(), "entry_gate", "gate2")
    monkeypatch.setattr(get_settings(), "exit_gate", "gate1")
    defs = main.build_zone_defs(
        [sim_spot("A1"), sim_spot("ENTRY1", purpose="EntrySpot"), sim_spot("EXIT1", purpose="ExitSpot")],
        [sim_gate("gate1"), sim_gate("gate2")])
    assert defs[0]["entrances"] == ["gate2"] and defs[0]["exits"] == ["gate1"]


def test_discovery_keeps_the_fallback_when_the_simulator_is_unreachable(monkeypatch):
    before = list(main.VALID_PARKING_SPOTS)

    async def refuse(*_args, **_kwargs):
        raise RuntimeError("simulator offline")

    monkeypatch.setattr(main, "call_simulator_api", refuse)
    assert asyncio.run(main.discover_layout()) is False
    assert main.VALID_PARKING_SPOTS == before   # the park keeps running on the fallback map


def test_discovery_reads_both_simulator_lists(monkeypatch):
    calls = []

    async def fake_call(endpoint, method="GET", **_):
        calls.append(endpoint)
        return {"list-parking-spots": LEVEL3_SPOTS, "list-barriers": LEVEL3_GATES}[endpoint]

    monkeypatch.setattr(main, "call_simulator_api", fake_call)
    saved = (main.ZONE_DEFS, dict(main.parking_spots))
    try:
        assert asyncio.run(main.discover_layout()) is True
        assert sorted(calls) == ["list-barriers", "list-parking-spots"]
        assert [z["id"] for z in main.ZONE_DEFS] == ["ZONE1", "ZONE2", "ZONE10"]
        assert main.layout_source.startswith("simulator")
    finally:
        main.apply_zone_defs(saved[0], source="restored")
        main.parking_spots.clear()
        main.parking_spots.update(saved[1])


def test_through_gates_are_opened_at_startup_and_never_auto_closed(monkeypatch):
    opened, closed = [], []

    async def fake_call(endpoint, method="GET", **_):
        if endpoint.endswith("/open"):
            opened.append(endpoint.split("/")[1])
        elif endpoint.endswith("/close"):
            closed.append(endpoint.split("/")[1])
        return {"status": "success"}

    async def no_audit(*_, **__):
        return None

    monkeypatch.setattr(main, "call_simulator_api", fake_call)
    monkeypatch.setattr(main, "audit_system", no_audit)
    monkeypatch.setattr(main, "ALWAYS_OPEN_GATES", ["gate7", "gate19"])

    asyncio.run(main.open_always_open_gates())
    assert opened == ["gate7", "gate19"]

    # The auto-close timer must leave them alone for the rest of the level.
    asyncio.run(main.auto_close_gate_after_delay("gate7", 0))
    asyncio.run(main.auto_close_gate_after_delay("gate19", 0))
    assert closed == []
