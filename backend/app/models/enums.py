"""Allowed values for ENUM columns. Must match database/schema.sql exactly."""

from enum import StrEnum

from sqlalchemy import Enum


class Role(StrEnum):
    ADMIN = "ADMIN"
    OPERATOR = "OPERATOR"


class SpotPurpose(StrEnum):  # from simulator
    PARK = "Park"
    ENTRY = "EntrySpot"
    EXIT = "ExitSpot"


class CarType(StrEnum):  # simulator "parkingForCarType"
    ELECTRIC = "Electric"
    ACCESSIBLE = "Accessible"
    ANY = "Any"


class SpotStatus(StrEnum):  # our app's view of a spot
    FREE = "FREE"
    RESERVED = "RESERVED"        # assigned, car driving there
    OCCUPIED = "OCCUPIED"        # car detected in the spot
    BROKEN = "BROKEN"
    MAINTENANCE = "MAINTENANCE"


class GateState(StrEnum):  # from simulator
    OPEN = "Open"
    CLOSED = "Closed"
    OPENING = "Opening"
    CLOSING = "Closing"


class SessionStatus(StrEnum):
    ENTERING = "ENTERING"    # arrived / driving to spot
    PARKED = "PARKED"        # detected in spot
    EXITING = "EXITING"      # left the spot, heading out
    COMPLETED = "COMPLETED"  # left the car park


class PaymentStatus(StrEnum):
    PENDING = "PENDING"
    PAID = "PAID"


def db_enum(enum_cls: type[StrEnum]) -> Enum:
    """Store the enum's VALUE (e.g. 'Park'), not its Python name (e.g. 'PARK')."""
    return Enum(enum_cls, values_callable=lambda e: [m.value for m in e], validate_strings=True)
