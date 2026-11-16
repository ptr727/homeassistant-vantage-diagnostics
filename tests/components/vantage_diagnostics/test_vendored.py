"""Tests for the vendored aiovantage diagnostic protocol code."""

from unittest.mock import AsyncMock, MagicMock

from aiovantage.events import EnhancedLogReceived
import pytest

from custom_components.vantage_diagnostics import aiovantage_diag
from custom_components.vantage_diagnostics.aiovantage_diag import diagnostic, events
from custom_components.vantage_diagnostics.aiovantage_diag.diagnostic import (
    DiagnosticInterface,
    EventLogEntry,
    GetEventLog,
    GetEventLogNext,
    GetModules,
    GetSysLog,
    IDiagnostic,
    NoParams,
    SysLogEntry,
)
from custom_components.vantage_diagnostics.aiovantage_diag.events import (
    SystemLogReceived,
    TriggerReceived,
    subscribe_system_log,
    subscribe_triggers,
)
from custom_components.vantage_diagnostics.aiovantage_diag.log_types import (
    SysLogType,
    TriggerType,
)


@pytest.fixture(autouse=True)
def modules_read() -> None:
    """Leave the bus read unpatched, since these tests call the interface itself."""


def test_trigger_parse() -> None:
    """Test parsing trigger lines, and rejecting lines of another shape."""
    trigger = TriggerReceived.parse("110 120 1 0 5")
    assert trigger == TriggerReceived(110, 120, 1, 0, 5)
    assert trigger
    assert trigger.trigger_type is TriggerType.BUTTON_DOWN
    assert TriggerReceived(1, 2, 99, 0, 0).trigger_type is None

    assert TriggerReceived.parse("110 120 1 0") is None
    assert TriggerReceived.parse("110 120 1 0 x") is None


def test_system_log_parse() -> None:
    """Test parsing system log lines, and rejecting lines of another shape."""
    log = SystemLogReceived.parse("3 Controller Overload (VID:102)  ")
    assert log == SystemLogReceived(3, "Controller", "Overload (VID:102)")
    assert log
    assert log.log_type is SysLogType.WARNING
    assert SystemLogReceived(99, "Controller", "x").log_type is None

    assert SystemLogReceived.parse("3 Controller") is None
    assert SystemLogReceived.parse("x Controller Hello") is None
    assert SystemLogReceived.parse("110 120 1 0 0") is None
    assert SystemLogReceived.parse("3 Object.Method status") is None


@pytest.mark.parametrize(
    ("subscribe", "log_type", "line", "parsed"),
    [
        (
            subscribe_triggers,
            "EVENT",
            "110 120 1 0 0",
            TriggerReceived(110, 120, 1, 0, 0),
        ),
        (
            subscribe_system_log,
            "SYSTEM",
            "2 Controller Task done",
            SystemLogReceived(2, "Controller", "Task done"),
        ),
    ],
)
def test_subscribe_passes_only_matching_lines(
    subscribe: object, log_type: str, line: str, parsed: object
) -> None:
    """Test that a subscription turns on its log type and filters what it passes."""
    stream = MagicMock()
    callback = MagicMock()

    unsubscribe = subscribe(stream, callback, peers=[2])  # type: ignore[operator]

    args = stream.subscribe_enhanced_log.call_args
    assert args.args[1] == log_type
    assert args.kwargs == {"peers": [2]}
    assert unsubscribe is stream.subscribe_enhanced_log.return_value
    on_log = args.args[0]
    on_log(EnhancedLogReceived("not a matching line at all here"))
    callback.assert_not_called()
    on_log(EnhancedLogReceived(line))
    callback.assert_called_once_with(parsed)


def test_entry_helpers() -> None:
    """Test the typed accessors of the log entries."""
    assert EventLogEntry(time_stamp="2026-01-02T03:04:05").timestamp is not None
    assert EventLogEntry().timestamp is None
    assert EventLogEntry(time_stamp="bad").timestamp is None
    assert EventLogEntry(event=1).trigger_type is TriggerType.BUTTON_DOWN
    assert EventLogEntry(event=99).trigger_type is None
    assert EventLogEntry().trigger_type is None
    assert SysLogEntry(type=3).log_type is SysLogType.WARNING
    assert SysLogEntry(type=99).log_type is None
    assert SysLogEntry().log_type is None


async def test_interface_calls() -> None:
    """Test that each wrapper sends its request through the config client."""
    client = MagicMock()
    client.rpc = AsyncMock(return_value="reply")

    assert await DiagnosticInterface.get_event_log(client, master=2) == "reply"
    client.rpc.assert_awaited_with(IDiagnostic, GetEventLog, NoParams(), master=2)

    assert await DiagnosticInterface.get_sys_log(client) == "reply"
    client.rpc.assert_awaited_with(IDiagnostic, GetSysLog, NoParams(), master=None)

    assert await DiagnosticInterface.get_modules(client, 3, master=2) == "reply"
    client.rpc.assert_awaited_with(IDiagnostic, GetModules, 3, master=2)

    client.rpc.return_value = EventLogEntry(time_stamp="2026-01-02T03:04:05")
    assert await DiagnosticInterface.get_event_log_next(client, 4)
    client.rpc.assert_awaited_with(IDiagnostic, GetEventLogNext, 4, master=None)

    client.rpc.return_value = EventLogEntry()
    assert await DiagnosticInterface.get_event_log_next(client, 5) is None


def test_package_exports_the_vendored_code() -> None:
    """Test that the package always exports the vendored copies."""
    assert aiovantage_diag.DiagnosticInterface is diagnostic.DiagnosticInterface
    assert aiovantage_diag.TriggerReceived is events.TriggerReceived
    assert aiovantage_diag.SysLogType is SysLogType
