"""Tests for the module fault report, its events, sensor and service, and the side-effect tagging."""

import asyncio
from collections.abc import Generator, Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from aiovantage.errors import ClientError
from aiovantage.events import ObjectUpdated
from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.vantage_diagnostics.aiovantage_diag import (
    EventLogEntry,
    ModuleInfo,
    TriggerType,
)
from custom_components.vantage_diagnostics.const import (
    DOMAIN,
    EVENT_MODULE_FAULT,
    EVENT_MODULE_RECOVERED,
    EVENT_TRIGGER,
    SERVICE_GET_FAULT_REPORT,
)
from custom_components.vantage_diagnostics.fault_report import (
    FAULT_WINDOW,
    OFFLINE_HOLD,
    TIMELINE_MAX_ROWS,
    controller_time,
    reading,
    storage_key,
)
from custom_components.vantage_diagnostics.module_status import ModuleStatus
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_FINAL_WRITE
from homeassistant.core import Event, HomeAssistant
from homeassistant.util import dt as dt_util

from .conftest import (
    BUTTON_VID,
    ENCLOSURE_VID,
    HOST,
    MODULE_VID,
    TASK_VID,
    FakeClient,
    FakeController,
    FakeEventStream,
    last_fault_id,
)

ALERT_TASK_VID = 140
TEMPERATURE_VID = 401
POWER_A_VID = 402
POWER_B_VID = 403
CURRENT_A_VID = 406
LOAD_VIDS = {1: 301, 2: 302, 4: 304, 10: 310}
UNMOUNTED_LOAD_VID = 399
MISSING_FEED_LOG = (
    f"3 Controller [ALERT] Module 'Module 1' (VID:{MODULE_VID}) "
    "detected missing line feed 1"
)
MODULE_OFFLINE_LOG = f"2 Station Module Offline: 1.1 [{MODULE_VID}]"
MODULE_ONLINE_LOG = f"2 Station Module Online: 1.1 [{MODULE_VID}]"
MODULE_INFO = ModuleInfo(address=1, vid=MODULE_VID, errors=0)


def sensor(vid: int, name: str, position: int, level: str | None) -> SimpleNamespace:
    """Return one of the module's sensors, at its fixed position."""
    return SimpleNamespace(
        vid=vid,
        name=name,
        parent=SimpleNamespace(vid=MODULE_VID, position=position),
        level=Decimal(level) if level is not None else None,
    )


@pytest.fixture(autouse=True)
def whole_second(freezer: FrozenDateTimeFactory) -> None:
    """Start on a whole second, as the controller's trigger history times are."""
    freezer.move_to(dt_util.utcnow().replace(microsecond=0))


@pytest.fixture
def module_model() -> str:
    """The model of the module, as the controller's object names it."""
    return "SDM12-EM"


@pytest.fixture
def client(module_model: str) -> FakeClient:
    """Return a client whose module has line feeds, sensors and loads."""
    client = FakeClient()
    module = client.modules.objects[0]
    module.model = module_model
    module.line_feed_table = [
        SimpleNamespace(name=f"Line {line}", position=index)
        for index, line in enumerate("ABCD", start=1)
    ]
    sensors = [
        # An unpowered module's sensors report the not-reported marker
        sensor(TEMPERATURE_VID, "Temperature Sensor 1", 1, "-0.999"),
        sensor(POWER_A_VID, "Power Sensor - Line A", 2, "-0.999"),
        sensor(POWER_B_VID, "Power Sensor - Line B", 3, "120.5"),
        sensor(CURRENT_A_VID, "Current Sensor - Line A", 6, None),
    ]
    client.omni_sensors = FakeController(sensors)
    loads = [
        SimpleNamespace(
            vid=vid,
            name=f"Channel {channel} Cans",
            parent=SimpleNamespace(vid=MODULE_VID, position=channel),
            level=Decimal(0),
        )
        for channel, vid in LOAD_VIDS.items()
    ]
    # A station's load, on no module
    loads.append(
        SimpleNamespace(
            vid=UNMOUNTED_LOAD_VID,
            name="Station Lamp",
            parent=SimpleNamespace(vid=BUTTON_VID, position=1),
            level=Decimal(0),
        )
    )
    client.loads = FakeController(loads)
    client.objects |= {obj.vid: obj for obj in sensors}
    client.objects[ALERT_TASK_VID] = SimpleNamespace(
        vid=ALERT_TASK_VID, name="Hardware Fault Alert", parent=None
    )
    return client


@pytest.fixture
def history() -> list[EventLogEntry]:
    """The controller's trigger history, filled by a test."""
    return []


@pytest.fixture(autouse=True)
def history_read(history: list[EventLogEntry]) -> Generator[AsyncMock]:
    """Answer the trigger history read with the test's history."""
    with patch(
        "custom_components.vantage_diagnostics.diagnostic_services.DiagnosticInterface.get_event_log",
        side_effect=lambda *args, **kwargs: list(history),
    ) as get_event_log:
        yield get_event_log


def stream_of(fake_stream: type[FakeEventStream]) -> FakeEventStream:
    """Return the stream of the entry set up last."""
    return fake_stream.instances[-1]


async def emit(hass: HomeAssistant, stream: FakeEventStream, line: str) -> None:
    """Pass the stream an enhanced log line, and wait for what it fires."""
    stream.emit_log(line)
    await hass.async_block_till_done(wait_background_tasks=True)


def trigger_line(source: int, event: int, param1: int = 0) -> str:
    """Return the enhanced log line of a trigger."""
    return f"{source} {ALERT_TASK_VID} {event} {param1} 0"


async def bus_drop(
    hass: HomeAssistant, stream: FakeEventStream, modules_read: AsyncMock
) -> None:
    """Drop the module off its bus, as the controller logs and reads it."""
    modules_read.return_value = []
    await emit(hass, stream, MODULE_OFFLINE_LOG)


async def bus_return(
    hass: HomeAssistant, stream: FakeEventStream, modules_read: AsyncMock
) -> None:
    """Bring the module back on its bus."""
    modules_read.return_value = [MODULE_INFO]
    await emit(hass, stream, MODULE_ONLINE_LOG)


async def fault_reports(hass: HomeAssistant, **data: Any) -> list[dict[str, Any]]:
    """Call the service, and return the reports it answers with."""
    response = await hass.services.async_call(
        DOMAIN, SERVICE_GET_FAULT_REPORT, data, blocking=True, return_response=True
    )
    assert response is not None
    assert response["errors"] == []
    reports = response["reports"]
    assert isinstance(reports, list)
    return [report for report in reports if isinstance(report, dict)]


def trigger_data(events: Sequence[Event[Any]], source: int) -> list[dict[str, Any]]:
    """Return the data of the trigger events a source fired."""
    return [dict(event.data) for event in events if event.data["source_id"] == source]


def local_text(when: Any) -> str:
    """Return a time as the controller writes it, local with no offset."""
    return dt_util.as_local(when).replace(tzinfo=None).isoformat(timespec="seconds")


@pytest.mark.usefixtures("init_integration")
async def test_trip_with_named_line_feed(
    hass: HomeAssistant,
    client: FakeClient,
    fake_stream: type[FakeEventStream],
    modules_read: AsyncMock,
    history: list[EventLogEntry],
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test a trip as the controller reports it, from the missing line feed to the reset."""
    stream = stream_of(fake_stream)
    faults = async_capture_events(hass, EVENT_MODULE_FAULT)
    recoveries = async_capture_events(hass, EVENT_MODULE_RECOVERED)
    triggers = async_capture_events(hass, EVENT_TRIGGER)

    # A load on line A turns on, and a button starts a task, ten minutes before the trip
    load = client.loads.objects[0]
    load.level = Decimal(100)
    client.loads.fire(ObjectUpdated(load, ["level"]))
    pressed = dt_util.utcnow()
    freezer.tick(timedelta(minutes=10))
    tripped = dt_util.utcnow()
    history.extend(
        [
            # Older than the window, and after it
            EventLogEntry(
                time_stamp=local_text(tripped - timedelta(hours=1)),
                source=BUTTON_VID,
                destination=TASK_VID,
                event=TriggerType.BUTTON_DOWN,
            ),
            EventLogEntry(
                time_stamp=local_text(pressed),
                source=BUTTON_VID,
                destination=TASK_VID,
                event=TriggerType.BUTTON_DOWN,
                param1=1,
                param2=0,
            ),
            EventLogEntry(
                time_stamp=local_text(tripped + timedelta(minutes=5)),
                source=BUTTON_VID,
                destination=TASK_VID,
                event=TriggerType.BUTTON_UP,
            ),
            # No time the controller could have written
            EventLogEntry(time_stamp="never", source=BUTTON_VID, destination=TASK_VID),
        ]
    )

    # The controller logs the missing feed, then raises the module's alert for the same fault
    await emit(hass, stream, MISSING_FEED_LOG)
    await emit(hass, stream, trigger_line(MODULE_VID, TriggerType.MODULE_ALERT, 1))

    assert len(faults) == 1
    report = dict(faults[0].data)
    assert {
        key: report[key] for key in report if key not in ("lines", "loads", "timeline")
    } == {
        "host": HOST,
        "module_id": MODULE_VID,
        "module_name": "Module 1",
        "signal": "system_log",
        "time": tripped.isoformat(),
        "line_feed": 1,
        "line_feed_name": "Line A",
        "offline": None,
        "recovered": None,
        "outage": None,
        "temperature": None,
        "timeline_errors": [],
        "side_effects": [],
    }
    assert report["lines"] == [
        {"line_feed": 1, "line": "A", "name": "Line A", "power": None, "current": None},
        {
            "line_feed": 2,
            "line": "B",
            "name": "Line B",
            "power": 120.5,
            "current": None,
        },
        {"line_feed": 3, "line": "C", "name": "Line C", "power": None, "current": None},
        {"line_feed": 4, "line": "D", "name": "Line D", "power": None, "current": None},
    ]
    assert [
        (load["channel"], load["line_feed"], load["on_missing_line"], load["level"])
        for load in report["loads"]
    ] == [
        (1, 1, True, 100.0),
        (2, 1, True, 0.0),
        (4, 2, False, 0.0),
        (10, 4, False, 0.0),
    ]
    assert report["loads"][0]["last_on"] == pressed.isoformat()
    assert report["loads"][0]["last_off"] is None
    assert report["timeline"] == [
        {
            "time": pressed.isoformat(),
            "t": -600.0,
            "kind": "trigger",
            "source_id": BUTTON_VID,
            "source_name": "Hall Button",
            "task_name": "Lamp",
            "trigger_type": "button_down",
            "param1": 1,
        },
        {
            "time": pressed.isoformat(),
            "t": -600.0,
            "kind": "load",
            "load_id": LOAD_VIDS[1],
            "name": "Channel 1 Cans",
            "level": 100.0,
        },
    ]

    # The module drops off the bus, and its sensors read out of range because they lost power
    freezer.tick(timedelta(seconds=3))
    await bus_drop(hass, stream, modules_read)
    freezer.tick(timedelta(seconds=4))
    await emit(hass, stream, trigger_line(POWER_A_VID, TriggerType.OUT_OF_RANGE))
    assert trigger_data(triggers, POWER_A_VID)[-1]["side_effect"] is True

    # Still the one fault, long after the window, since the module stays off the bus
    freezer.tick(FAULT_WINDOW * 2)
    await emit(hass, stream, MISSING_FEED_LOG)
    assert len(faults) == 1

    # The breaker is reset, and the sensors read back in range
    await bus_return(hass, stream, modules_read)
    await emit(hass, stream, trigger_line(POWER_A_VID, TriggerType.IN_RANGE))
    assert trigger_data(triggers, POWER_A_VID)[-1]["side_effect"] is True

    outage = round((dt_util.utcnow() - tripped).total_seconds(), 1)
    assert len(recoveries) == 1
    assert recoveries[0].data == {
        "host": HOST,
        "module_id": MODULE_VID,
        "module_name": "Module 1",
        "time": tripped.isoformat(),
        "recovered": dt_util.utcnow().isoformat(),
        "outage": outage,
    }

    (stored,) = await fault_reports(hass, module_id=MODULE_VID)
    assert stored["offline"] == (tripped + timedelta(seconds=3)).isoformat()
    assert stored["outage"] == outage
    assert [effect["trigger_type"] for effect in stored["side_effects"]] == [
        "out_of_range",
        "in_range",
    ]
    assert await fault_reports(hass) == [stored]
    assert await fault_reports(hass, module_id=MODULE_VID + 1) == []

    state = hass.states.get(last_fault_id(hass))
    assert state
    assert dt_util.parse_datetime(state.state) == tripped
    assert state.attributes["signal"] == "system_log"
    assert state.attributes["line_feed"] == 1
    assert state.attributes["line_feed_name"] == "Line A"
    assert state.attributes["outage"] == outage
    assert state.attributes["side_effects"] == 2

    # The event kept what it fired with, though the stored report went on changing
    assert faults[0].data["offline"] is None
    assert faults[0].data["side_effects"] == []


@pytest.mark.parametrize("module_model", ["UDM08-EM"])
@pytest.mark.usefixtures("init_integration")
async def test_trip_seen_only_as_a_bus_drop(
    hass: HomeAssistant,
    client: FakeClient,
    fake_stream: type[FakeEventStream],
    modules_read: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test that a module off the bus past the hold starts a fault on its own."""
    stream = stream_of(fake_stream)
    faults = async_capture_events(hass, EVENT_MODULE_FAULT)
    dropped = dt_util.utcnow()

    await bus_drop(hass, stream, modules_read)

    # The module's sensors stop reporting during the hold
    power_b = client.omni_sensors.get(POWER_B_VID)
    power_b.level = Decimal("-0.999")
    freezer.tick(timedelta(seconds=OFFLINE_HOLD - 1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert faults == []

    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(faults) == 1
    report = faults[0].data
    assert report["signal"] == "offline"
    assert report["time"] == dropped.isoformat()
    assert report["offline"] == dropped.isoformat()
    assert report["line_feed"] is None
    assert report["line_feed_name"] is None
    # Read as the module left the bus, not when the hold ended
    assert report["lines"][1]["power"] == 120.5
    # A model whose channel map this doesn't know lists its loads by channel alone
    assert [(load["channel"], load["line_feed"]) for load in report["loads"]] == [
        (1, None),
        (2, None),
        (4, None),
        (10, None),
    ]

    # The controller's alert for the same fault adds nothing
    await emit(hass, stream, trigger_line(MODULE_VID, TriggerType.MODULE_ALERT, 1))
    assert len(faults) == 1


@pytest.mark.usefixtures("init_integration")
async def test_program_write_raises_no_report(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_stream: type[FakeEventStream],
    modules_read: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test that a program write, which re-announces the modules and logs no drop, raises no report."""
    faults = async_capture_events(hass, EVENT_MODULE_FAULT)
    triggers = async_capture_events(hass, EVENT_TRIGGER)

    # A read while the module is still to be re-announced misses it, then its online log brings it back
    modules_read.return_value = []
    await init_integration.runtime_data.module_status.async_refresh()
    freezer.tick(timedelta(seconds=2))
    await bus_return(hass, stream_of(fake_stream), modules_read)
    freezer.tick(timedelta(seconds=OFFLINE_HOLD * 2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert faults == []
    assert await fault_reports(hass) == []

    # The module never left the bus, so a sensor back in range then is a genuine reading
    await emit(
        hass, stream_of(fake_stream), trigger_line(POWER_A_VID, TriggerType.IN_RANGE)
    )
    assert trigger_data(triggers, POWER_A_VID)[-1]["side_effect"] is False


@pytest.mark.usefixtures("init_integration")
async def test_brief_drop_raises_no_report(
    hass: HomeAssistant,
    fake_stream: type[FakeEventStream],
    modules_read: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test that a module back on the bus within the hold raises no report."""
    stream = stream_of(fake_stream)
    faults = async_capture_events(hass, EVENT_MODULE_FAULT)
    triggers = async_capture_events(hass, EVENT_TRIGGER)

    await bus_drop(hass, stream, modules_read)
    freezer.tick(timedelta(seconds=5))
    await bus_return(hass, stream, modules_read)
    freezer.tick(timedelta(seconds=OFFLINE_HOLD * 2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert faults == []
    assert await fault_reports(hass) == []
    state = hass.states.get(last_fault_id(hass))
    assert state
    assert state.state == "unknown"

    # A sensor back in range soon after the module rejoined the bus is part of that change
    await emit(hass, stream, trigger_line(POWER_A_VID, TriggerType.IN_RANGE))
    assert trigger_data(triggers, POWER_A_VID)[-1]["side_effect"] is True


@pytest.mark.usefixtures("init_integration")
async def test_genuine_out_of_range(
    hass: HomeAssistant, fake_stream: type[FakeEventStream]
) -> None:
    """Test that a range trigger with no fault around it is a genuine reading."""
    stream = stream_of(fake_stream)
    faults = async_capture_events(hass, EVENT_MODULE_FAULT)
    triggers = async_capture_events(hass, EVENT_TRIGGER)

    await emit(hass, stream, trigger_line(POWER_B_VID, TriggerType.OUT_OF_RANGE))
    await emit(hass, stream, trigger_line(POWER_B_VID, TriggerType.IN_RANGE))
    # A sensor that is on no module, and a trigger of another type
    await emit(hass, stream, trigger_line(BUTTON_VID, TriggerType.OUT_OF_RANGE))
    await emit(hass, stream, trigger_line(BUTTON_VID, TriggerType.BUTTON_DOWN))

    assert [data["side_effect"] for data in trigger_data(triggers, POWER_B_VID)] == [
        False,
        False,
    ]
    assert [data.get("side_effect") for data in trigger_data(triggers, BUTTON_VID)] == [
        False,
        None,
    ]
    # A range trigger names its sensor's module, null for a sensor on none, and no other trigger has the field
    assert [data["module_id"] for data in trigger_data(triggers, POWER_B_VID)] == [
        MODULE_VID,
        MODULE_VID,
    ]
    assert [
        data.get("module_id", 0) for data in trigger_data(triggers, BUTTON_VID)
    ] == [
        None,
        0,
    ]
    assert faults == []


@pytest.mark.usefixtures("init_integration")
async def test_alert_from_another_object(
    hass: HomeAssistant, fake_stream: type[FakeEventStream]
) -> None:
    """Test that only a module's own alert starts a fault, and that one with no feed names none."""
    stream = stream_of(fake_stream)
    faults = async_capture_events(hass, EVENT_MODULE_FAULT)

    await emit(hass, stream, trigger_line(BUTTON_VID, TriggerType.MODULE_ALERT, 1))
    assert faults == []

    await emit(hass, stream, trigger_line(MODULE_VID, TriggerType.MODULE_ALERT, 0))
    assert len(faults) == 1
    assert faults[0].data["signal"] == "module_alert"
    assert faults[0].data["line_feed"] is None
    assert not any(load["on_missing_line"] for load in faults[0].data["loads"])


@pytest.mark.usefixtures("init_integration")
async def test_fault_that_never_drops_the_module(
    hass: HomeAssistant,
    fake_stream: type[FakeEventStream],
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test that a fault whose module stays on the bus ends with its window, so a later one reports again."""
    stream = stream_of(fake_stream)
    faults = async_capture_events(hass, EVENT_MODULE_FAULT)
    recoveries = async_capture_events(hass, EVENT_MODULE_RECOVERED)

    await emit(hass, stream, MISSING_FEED_LOG)
    freezer.tick(FAULT_WINDOW + timedelta(seconds=1))
    await emit(hass, stream, MISSING_FEED_LOG)

    assert len(faults) == 2
    assert recoveries == []


@pytest.mark.usefixtures("init_integration")
async def test_timeline_keeps_rows_nearest_the_fault(
    hass: HomeAssistant,
    fake_stream: type[FakeEventStream],
    history: list[EventLogEntry],
) -> None:
    """Test that a busy window keeps only the rows nearest the fault."""
    now = dt_util.utcnow()
    history.extend(
        EventLogEntry(
            time_stamp=local_text(now - timedelta(seconds=index)),
            source=BUTTON_VID,
            destination=TASK_VID,
            event=TriggerType.BUTTON_DOWN,
        )
        for index in range(TIMELINE_MAX_ROWS + 10, 0, -1)
    )
    faults = async_capture_events(hass, EVENT_MODULE_FAULT)

    await emit(hass, stream_of(fake_stream), MISSING_FEED_LOG)

    timeline = faults[0].data["timeline"]
    assert len(timeline) == TIMELINE_MAX_ROWS
    assert timeline[0]["t"] == -TIMELINE_MAX_ROWS
    assert timeline[-1]["t"] == -1


@pytest.mark.usefixtures("init_integration")
async def test_unreadable_history_is_reported(
    hass: HomeAssistant,
    fake_stream: type[FakeEventStream],
    history_read: AsyncMock,
) -> None:
    """Test that a fault still reports when its trigger history can't be read, and says so."""
    history_read.side_effect = ClientError("Timeout")
    faults = async_capture_events(hass, EVENT_MODULE_FAULT)

    await emit(hass, stream_of(fake_stream), MISSING_FEED_LOG)

    assert len(faults) == 1
    assert faults[0].data["timeline"] == []
    assert faults[0].data["timeline_errors"] == [
        {"host": HOST, "master": 1, "error": "Timeout"}
    ]


@pytest.mark.usefixtures("init_integration")
async def test_timeline_failure_still_fires_the_report(
    hass: HomeAssistant, fake_stream: type[FakeEventStream]
) -> None:
    """Test that a fault still reports when building its timeline fails unexpectedly."""
    faults = async_capture_events(hass, EVENT_MODULE_FAULT)

    with patch(
        "custom_components.vantage_diagnostics.fault_report.read_trigger_history",
        side_effect=RuntimeError("boom"),
    ):
        await emit(hass, stream_of(fake_stream), MISSING_FEED_LOG)

    assert len(faults) == 1
    assert faults[0].data["timeline"] == []
    assert faults[0].data["timeline_errors"] == [
        {"host": HOST, "master": None, "error": "boom"}
    ]


@pytest.mark.usefixtures("init_integration")
async def test_side_effects_join_their_own_fault(
    hass: HomeAssistant,
    fake_stream: type[FakeEventStream],
    modules_read: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test that a side effect joins the fault it belongs to, never an earlier one long recovered."""
    stream = stream_of(fake_stream)
    faults = async_capture_events(hass, EVENT_MODULE_FAULT)
    triggers = async_capture_events(hass, EVENT_TRIGGER)

    # An earlier trip, long recovered
    await emit(hass, stream, MISSING_FEED_LOG)
    await bus_drop(hass, stream, modules_read)
    await bus_return(hass, stream, modules_read)
    freezer.tick(timedelta(minutes=10))
    (earlier,) = await fault_reports(hass)
    assert earlier["side_effects"] == []

    # A drop with no warning, whose sensors read out of range before the hold starts its fault
    await bus_drop(hass, stream, modules_read)
    freezer.tick(timedelta(seconds=6))
    await emit(hass, stream, trigger_line(POWER_A_VID, TriggerType.OUT_OF_RANGE))
    assert trigger_data(triggers, POWER_A_VID)[-1]["side_effect"] is True
    assert await fault_reports(hass) == [earlier]

    freezer.tick(timedelta(seconds=OFFLINE_HOLD))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(faults) == 2
    assert [effect["source_id"] for effect in faults[1].data["side_effects"]] == [
        POWER_A_VID
    ]

    # Long after that one recovered, a drop shorter than the hold joins neither
    await bus_return(hass, stream, modules_read)
    freezer.tick(timedelta(minutes=10))
    (later,) = await fault_reports(hass)
    await bus_drop(hass, stream, modules_read)
    await emit(hass, stream, trigger_line(POWER_A_VID, TriggerType.OUT_OF_RANGE))
    freezer.tick(timedelta(seconds=5))
    await bus_return(hass, stream, modules_read)
    await emit(hass, stream, trigger_line(POWER_A_VID, TriggerType.IN_RANGE))
    assert trigger_data(triggers, POWER_A_VID)[-1]["side_effect"] is True
    assert await fault_reports(hass) == [later]


@pytest.mark.usefixtures("init_integration")
async def test_trip_again_soon_after_a_reset(
    hass: HomeAssistant,
    fake_stream: type[FakeEventStream],
    modules_read: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test that a breaker reset onto a fault, which trips again at once, keeps each trip's side effects apart."""
    stream = stream_of(fake_stream)
    faults = async_capture_events(hass, EVENT_MODULE_FAULT)

    # A whole-supply trip that only drops the module, reset after a minute
    await bus_drop(hass, stream, modules_read)
    freezer.tick(timedelta(seconds=OFFLINE_HOLD + 1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    await bus_return(hass, stream, modules_read)
    (first,) = await fault_reports(hass)

    # It trips again, and its sensors read out of range before the hold starts the second fault
    freezer.tick(timedelta(seconds=60))
    await bus_drop(hass, stream, modules_read)
    freezer.tick(timedelta(seconds=6))
    await emit(hass, stream, trigger_line(POWER_A_VID, TriggerType.OUT_OF_RANGE))
    assert await fault_reports(hass) == [first]
    freezer.tick(timedelta(seconds=OFFLINE_HOLD))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(faults) == 2
    assert [effect["source_id"] for effect in faults[1].data["side_effects"]] == [
        POWER_A_VID
    ]


@pytest.mark.usefixtures("init_integration")
async def test_report_before_its_timeline_is_read(
    hass: HomeAssistant,
    fake_stream: type[FakeEventStream],
    history_read: AsyncMock,
) -> None:
    """Test that a report whose trigger history is still being read says so, rather than reading as a quiet half hour."""
    release = asyncio.Event()

    async def slow_read(*args: Any, **kwargs: Any) -> list[EventLogEntry]:
        await release.wait()
        return []

    history_read.side_effect = slow_read
    faults = async_capture_events(hass, EVENT_MODULE_FAULT)

    stream_of(fake_stream).emit_log(MISSING_FEED_LOG)
    await hass.async_block_till_done()
    (pending,) = await fault_reports(hass)
    assert pending["timeline"] is None
    assert pending["timeline_errors"] is None

    # Once read, the report fires with what it read
    release.set()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(faults) == 1
    assert faults[0].data["timeline"] == []
    assert faults[0].data["timeline_errors"] == []


async def test_removing_the_entry_removes_its_reports(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_stream: type[FakeEventStream],
    hass_storage: dict[str, Any],
) -> None:
    """Test that deleting the entry deletes the reports it kept, which no new entry would read."""
    await emit(hass, stream_of(fake_stream), MISSING_FEED_LOG)
    assert await hass.config_entries.async_unload(init_integration.entry_id)
    key = storage_key(init_integration.entry_id)
    assert key in hass_storage

    await hass.config_entries.async_remove(init_integration.entry_id)
    await hass.async_block_till_done()

    assert key not in hass_storage


@pytest.mark.parametrize("online_after_restart", [False, True])
async def test_restart_mid_fault(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_stream: type[FakeEventStream],
    modules_read: AsyncMock,
    freezer: FrozenDateTimeFactory,
    online_after_restart: bool,
) -> None:
    """Test that a report survives a reload mid-fault, and the recovery still reports."""
    faults = async_capture_events(hass, EVENT_MODULE_FAULT)
    recoveries = async_capture_events(hass, EVENT_MODULE_RECOVERED)
    tripped = dt_util.utcnow()
    await emit(hass, stream_of(fake_stream), MISSING_FEED_LOG)
    freezer.tick(timedelta(seconds=3))
    await bus_drop(hass, stream_of(fake_stream), modules_read)
    (before,) = await fault_reports(hass)

    # The breaker is reset while Home Assistant restarts, or after
    modules_read.return_value = [MODULE_INFO] if online_after_restart else []
    freezer.tick(timedelta(seconds=60))
    assert await hass.config_entries.async_reload(init_integration.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    # Kept as it was, apart from a recovery the first read after the restart found
    (after,) = await fault_reports(hass)
    recovery = ("recovered", "outage")
    assert {key: value for key, value in after.items() if key not in recovery} == {
        key: value for key, value in before.items() if key not in recovery
    }
    state = hass.states.get(last_fault_id(hass))
    assert state
    assert dt_util.parse_datetime(state.state) == tripped

    if not online_after_restart:
        assert recoveries == []
        freezer.tick(timedelta(seconds=10))
        await bus_return(hass, stream_of(fake_stream), modules_read)

    assert len(faults) == 1
    assert len(recoveries) == 1
    assert recoveries[0].data["time"] == tripped.isoformat()
    assert recoveries[0].data["outage"] == round(
        (dt_util.utcnow() - tripped).total_seconds(), 1
    )

    # The sensors back in range after the reset are part of the recovery, wherever it was first seen
    triggers = async_capture_events(hass, EVENT_TRIGGER)
    freezer.tick(timedelta(seconds=10))
    await emit(
        hass, stream_of(fake_stream), trigger_line(POWER_A_VID, TriggerType.IN_RANGE)
    )
    assert trigger_data(triggers, POWER_A_VID)[-1]["side_effect"] is True


@pytest.mark.usefixtures("init_integration")
async def test_unload_cancels_the_hold(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_stream: type[FakeEventStream],
    modules_read: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test that a module off the bus at unload raises no report afterwards."""
    faults = async_capture_events(hass, EVENT_MODULE_FAULT)
    await bus_drop(hass, stream_of(fake_stream), modules_read)

    assert await hass.config_entries.async_unload(init_integration.entry_id)
    freezer.tick(timedelta(seconds=OFFLINE_HOLD * 2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert faults == []


@pytest.mark.parametrize(
    ("local", "expected"),
    [(None, None), (datetime(2026, 1, 2, 3, 4, 5), "2026-01-02T11:04:05+00:00")],
)
async def test_controller_time(
    hass: HomeAssistant, local: datetime | None, expected: str | None
) -> None:
    """Test that a controller time is read in Home Assistant's time zone."""
    await hass.config.async_set_time_zone("US/Pacific")
    result = controller_time(local)
    assert (result.isoformat() if result else None) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), (Decimal("-0.999"), None), (Decimal("12.5"), 12.5)],
)
def test_reading(value: Decimal | None, expected: float | None) -> None:
    """Test that the not-reported marker reads as no reading."""
    assert reading(value) == expected


@pytest.mark.usefixtures("init_integration")
async def test_dimming_is_no_switch(
    hass: HomeAssistant,
    client: FakeClient,
    fake_stream: type[FakeEventStream],
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test that a load's last on and off times move only when it switches, not when it dims."""
    load = client.loads.objects[0]
    faults = async_capture_events(hass, EVENT_MODULE_FAULT)

    load.level = Decimal(100)
    client.loads.fire(ObjectUpdated(load, ["level"]))
    switched_on = dt_util.utcnow()
    freezer.tick(timedelta(minutes=1))
    load.level = Decimal(40)
    client.loads.fire(ObjectUpdated(load, ["level"]))
    freezer.tick(timedelta(minutes=1))
    # A change to another attribute is no level change
    client.loads.fire(ObjectUpdated(load, ["name"]))

    await emit(hass, stream_of(fake_stream), MISSING_FEED_LOG)

    (first,) = (row for row in faults[0].data["loads"] if row["load_id"] == load.vid)
    assert first["level"] == 40.0
    assert first["last_on"] == switched_on.isoformat()
    assert first["last_off"] is None


async def test_reports_saved_without_an_unload(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_stream: type[FakeEventStream],
    hass_storage: dict[str, Any],
) -> None:
    """Test that a report reaches the store as Home Assistant stops, which unloads no entry."""
    await emit(hass, stream_of(fake_stream), MISSING_FEED_LOG)
    key = storage_key(init_integration.entry_id)
    assert key not in hass_storage

    hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
    await hass.async_block_till_done()

    assert init_integration.state is ConfigEntryState.LOADED
    assert hass_storage[key]["data"] == {
        str(MODULE_VID): (await fault_reports(hass))[0]
    }


async def test_cut_short_report_fires_at_setup(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    fake_stream: type[FakeEventStream],
    hass_storage: dict[str, Any],
) -> None:
    """Test that a report whose history read a restart cut short is read and fired once the entry loads."""
    stored = {
        "host": HOST,
        "module_id": MODULE_VID,
        "module_name": "Module 1",
        "signal": "system_log",
        "time": dt_util.utcnow().isoformat(),
        "line_feed": 1,
        "line_feed_name": "Line A",
        "offline": None,
        "recovered": None,
        "outage": None,
        "temperature": None,
        "lines": [],
        "loads": [],
        "timeline": None,
        "timeline_errors": None,
        "side_effects": [],
    }
    hass_storage[storage_key(config_entry.entry_id)] = {
        "version": 1,
        "minor_version": 1,
        "key": storage_key(config_entry.entry_id),
        "data": {str(MODULE_VID): stored},
    }
    faults = async_capture_events(hass, EVENT_MODULE_FAULT)

    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(faults) == 1
    assert faults[0].data["time"] == stored["time"]
    assert faults[0].data["timeline"] == []
    assert faults[0].data["timeline_errors"] == []


async def test_program_write_past_the_confirming_read(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_stream: type[FakeEventStream],
    modules_read: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test that a module a program write re-announces after the confirming read raises no report, since it is back within the hold."""
    faults = async_capture_events(hass, EVENT_MODULE_FAULT)
    module_status = init_integration.runtime_data.module_status

    # Missing from a read and from the confirming read 5 s later, so it is off the bus
    modules_read.return_value = []
    await module_status.async_refresh()
    freezer.tick(timedelta(seconds=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert module_status.data.is_online(module_status_module(init_integration)) is False

    # Its online log arrives well within the hold
    freezer.tick(timedelta(seconds=4))
    await bus_return(hass, stream_of(fake_stream), modules_read)
    freezer.tick(timedelta(seconds=OFFLINE_HOLD * 2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert faults == []


async def test_hold_waits_out_an_unreadable_enclosure(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_stream: type[FakeEventStream],
    modules_read: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test that a hold ending while the module's enclosure can't be read runs again, rather than dropping the fault."""
    faults = async_capture_events(hass, EVENT_MODULE_FAULT)
    module_status = init_integration.runtime_data.module_status
    dropped = dt_util.utcnow()
    await bus_drop(hass, stream_of(fake_stream), modules_read)

    # Another enclosure reads, this one fails, so the module's state is unknown
    with patch(
        "custom_components.vantage_diagnostics.module_status.read_module_status",
        return_value=ModuleStatus(
            enclosures={ENCLOSURE_VID + 1}, failed={ENCLOSURE_VID: "Timeout"}
        ),
    ):
        freezer.tick(timedelta(seconds=1))
        await module_status.async_refresh()
        assert (
            module_status.data.is_online(module_status_module(init_integration)) is None
        )
        freezer.tick(timedelta(seconds=OFFLINE_HOLD))
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert faults == []

    # A later read finds it still off the bus, so the next hold starts the fault from the drop
    await module_status.async_refresh()
    freezer.tick(timedelta(seconds=OFFLINE_HOLD))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(faults) == 1
    assert faults[0].data["time"] == dropped.isoformat()


def module_status_module(entry: MockConfigEntry) -> Any:
    """Return the module the client holds."""
    return next(iter(entry.runtime_data.client.modules))


async def test_module_off_the_bus_at_setup(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    fake_stream: type[FakeEventStream],
    modules_read: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test that a module already off the bus at the first read, as after a trip while Home Assistant was down, still reports."""
    faults = async_capture_events(hass, EVENT_MODULE_FAULT)
    modules_read.return_value = []
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    first_read = dt_util.utcnow()

    freezer.tick(timedelta(seconds=OFFLINE_HOLD + 1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(faults) == 1
    assert faults[0].data["signal"] == "offline"
    assert faults[0].data["time"] == first_read.isoformat()
    assert faults[0].data["offline"] == first_read.isoformat()
