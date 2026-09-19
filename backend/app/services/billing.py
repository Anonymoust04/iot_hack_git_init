"""Parking charge calculation.

Docs: "charge = total number of minutes spent in park, and multiply by two if
electric (electricity charging cost)".

Interpretation used here: parkingCost = minutes, chargingCost = minutes if the car
is electric else 0 (so an electric car pays 2x in total).
TODO(team): confirm with the simulator how minutes are rounded (ceil vs floor)
and which timestamps count (entry-gate arrival vs. parked).
"""

import math
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Charge:
    minutes: int
    parking_cost: float
    charging_cost: float

    @property
    def total(self) -> float:
        return self.parking_cost + self.charging_cost


def minutes_between(start: datetime, end: datetime) -> int:
    return max(0, math.ceil((end - start).total_seconds() / 60))


def calculate_charge(arrived_at: datetime, exited_at: datetime, is_electric: bool) -> Charge:
    minutes = minutes_between(arrived_at, exited_at)
    return Charge(
        minutes=minutes,
        parking_cost=float(minutes),
        charging_cost=float(minutes) if is_electric else 0.0,
    )
