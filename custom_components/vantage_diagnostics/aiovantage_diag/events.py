"""Trigger and system log events parsed from the enhanced log.

Vendored from the src/aiovantage/events.py of two loopj/aiovantage pull requests.
TriggerReceived is from pull request 407, head commit 04aefb748eadb9ebd841c4ae7aaebd7957661616.
SystemLogReceived is from pull request 408, head commit 5187274d000e63d16d54eeefd4268ffaacf1624c.
Their classes are unchanged, apart from the wording of one comment. The two subscribe functions at the end are the pull
requests' EventStream.subscribe_triggers and EventStream.subscribe_system_log,
moved to functions because the installed EventStream does not carry them.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from aiovantage.command_client import EventStream
from aiovantage.events import EnhancedLogReceived

from .log_types import SysLogType, TriggerType


@dataclass
class TriggerReceived:
    """An object triggering a task, from the "EVENT" enhanced log.

    Passed to the callbacks of `EventStream.subscribe_triggers`, rather than emitted.
    """

    source: int
    """The VID of the object that fired, eg. a button, timer or sensor."""

    destination: int
    """The VID of the task that was started."""

    event: int
    """The type of the trigger, see `aiovantage.config_client.TriggerType`."""

    param1: int
    """The first trigger parameter."""

    param2: int
    """The second trigger parameter."""

    @classmethod
    def parse(cls, log: str) -> TriggerReceived | None:
        """Parse an "EVENT" enhanced log of the form "<src> <dst> <event> <p1> <p2>".

        Enhanced logs carry no type tag, so any five-integer log parses as a trigger.
        Only pass it logs from a stream that writes no other all-numeric log type.

        Returns:
            The trigger, or None if the log is not a trigger log.
        """
        tokens = log.split()
        if len(tokens) != 5:
            return None

        try:
            return cls(*(int(token) for token in tokens))
        except ValueError:
            return None

    @property
    def trigger_type(self) -> TriggerType | None:
        """The type of the trigger, if known."""
        try:
            return TriggerType(self.event)
        except ValueError:
            return None


@dataclass
class SystemLogReceived:
    """A system log, from the "SYSTEM" enhanced log.

    Passed to the callbacks of `EventStream.subscribe_system_log`, rather than emitted.
    """

    type: int
    """The type of the log, see `aiovantage.config_client.SysLogType`."""

    source: str
    """The source of the log, eg. "Controller"."""

    message: str
    """The log message."""

    @classmethod
    def parse(cls, log: str) -> SystemLogReceived | None:
        """Parse a "SYSTEM" enhanced log of the form "<type> <source> <message>".

        The source is taken to be one word, as the controller writes it, eg.
        "Controller".

        Returns:
            The system log, or None if the log is not a system log.
        """
        tokens = log.split(maxsplit=2)
        if len(tokens) != 3:
            return None

        log_type, source, message = tokens

        # Enhanced logs carry no type tag, so tell them apart by shape: object status logs carry an "Interface.Method", and trigger logs are all numeric
        if not log_type.isdecimal() or "." in source or _is_int(source):
            return None

        return cls(int(log_type), source, message.strip())

    @property
    def log_type(self) -> SysLogType | None:
        """The type of the log, if known."""
        try:
            return SysLogType(self.type)
        except ValueError:
            return None


def _is_int(token: str) -> bool:
    try:
        int(token)
    except ValueError:
        return False
    return True


def subscribe_triggers(
    stream: EventStream,
    callback: Callable[[TriggerReceived], None],
    *,
    peers: Iterable[int] = (),
) -> Callable[[], None]:
    """Subscribe to objects triggering tasks, eg. a button press starting a task.

    Subscribing turns on the controller's "EVENT" enhanced log, which stays written
    after the client disconnects.

    Args:
        stream: The event stream to subscribe on.
        callback: The callback to invoke when a trigger is received.
        peers: Master numbers of the peers whose triggers should be logged too.

    Returns:
        A function that can be used to unsubscribe from triggers.
    """

    # Other enhanced log types share the stream, so only pass on trigger logs
    def on_enhanced_log(event: EnhancedLogReceived) -> None:
        if trigger := TriggerReceived.parse(event.log):
            callback(trigger)

    return stream.subscribe_enhanced_log(on_enhanced_log, "EVENT", peers=peers)


def subscribe_system_log(
    stream: EventStream,
    callback: Callable[[SystemLogReceived], None],
    *,
    peers: Iterable[int] = (),
) -> Callable[[], None]:
    """Subscribe to the system log, eg. modules going offline or losing a line feed.

    Subscribing turns on the controller's "SYSTEM" enhanced log, which stays written
    after the client disconnects.

    Args:
        stream: The event stream to subscribe on.
        callback: The callback to invoke when a system log is received.
        peers: Master numbers of the peers whose system logs should be sent too.

    Returns:
        A function that can be used to unsubscribe from the system log.
    """

    # Other enhanced log types share the stream, so only pass on system logs
    def on_enhanced_log(event: EnhancedLogReceived) -> None:
        if system_log := SystemLogReceived.parse(event.log):
            callback(system_log)

    return stream.subscribe_enhanced_log(on_enhanced_log, "SYSTEM", peers=peers)
