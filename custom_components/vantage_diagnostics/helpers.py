"""Small helpers shared across the integration."""

from enum import IntEnum


def error_text(err: Exception) -> str:
    """Return an error as text, since a read timeout or dropped connection has no message."""
    return str(err) or type(err).__name__


def enum_name(enum_cls: type[IntEnum], value: int | None) -> str:
    """Return the snake case name of an enum value, eg. "out_of_range".

    A value the enum doesn't know reads "unknown_<value>", and a missing one "unknown".
    """
    if value is None:
        return "unknown"

    try:
        return enum_cls(value).name.lower()
    except ValueError:
        return f"unknown_{value}"
