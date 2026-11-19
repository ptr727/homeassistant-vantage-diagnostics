"""Small helpers shared across the integration."""

import asyncio
from enum import IntEnum
from typing import Protocol


class SocketConnection(Protocol):
    """An aiovantage connection, which keeps its socket's stream writer private."""

    _writer: asyncio.StreamWriter | None


def error_text(err: Exception) -> str:
    """Return an error as text, since a read timeout or dropped connection has no message."""
    return str(err) or type(err).__name__


def peer_address(connection: SocketConnection) -> str | None:
    """Return the address a connection's socket is connected to, or None while it has none.

    It is the address actually in use, so it needs no DNS lookup of the configured host.
    """
    # The aiovantage connection keeps its socket private, and exposes only the host as configured
    writer = connection._writer  # noqa: SLF001
    if writer is None:
        return None

    peer = writer.get_extra_info("peername")
    return str(peer[0]) if peer else None


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
