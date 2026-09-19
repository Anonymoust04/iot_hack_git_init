from datetime import datetime, timedelta

from app.services.billing import calculate_charge

T0 = datetime(2026, 1, 1, 12, 0, 0)


def test_regular_car():
    c = calculate_charge(T0, T0 + timedelta(minutes=30), is_electric=False)
    assert (c.minutes, c.parking_cost, c.charging_cost) == (30, 30.0, 0.0)


def test_electric_car_pays_double():
    c = calculate_charge(T0, T0 + timedelta(minutes=30), is_electric=True)
    assert c.total == 60.0
    assert c.charging_cost == 30.0


def test_partial_minute_rounds_up():
    c = calculate_charge(T0, T0 + timedelta(minutes=2, seconds=1), is_electric=False)
    assert c.minutes == 3
