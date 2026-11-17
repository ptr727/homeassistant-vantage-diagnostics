"""Tests for the trigger and system log events."""

import asyncio
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.vantage_diagnostics.aiovantage_diag import (
    SysLogType,
    SystemLogReceived,
    TriggerType,
)
from custom_components.vantage_diagnostics.const import (
    DOMAIN,
    EVENT_SYSTEM_LOG,
    EVENT_TRIGGER,
    VANTAGE_DOMAIN,
)
from custom_components.vantage_diagnostics.helpers import enum_name
from custom_components.vantage_diagnostics.logbook import async_describe_events
from custom_components.vantage_diagnostics.system_log import (
    COMPILER_SOURCE,
    REPEAT_REMINDER,
    REPEAT_WINDOW,
    Shape,
    is_noise,
)
from homeassistant.components.logbook.const import (
    LOGBOOK_ENTRY_ENTITY_ID,
    LOGBOOK_ENTRY_MESSAGE,
    LOGBOOK_ENTRY_NAME,
)
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import (
    BUTTON_VID,
    ENCLOSURE_VID,
    HOST,
    KEYPAD_VID,
    MODULE_VID,
    TASK_VID,
    FakeEventStream,
)

pytestmark = pytest.mark.usefixtures("init_integration")


@pytest.mark.parametrize(
    ("event", "trigger_type"), [(1, "button_down"), (3, "timer"), (99, "unknown_99")]
)
async def test_trigger_event(
    hass: HomeAssistant,
    fake_stream: type[FakeEventStream],
    event: int,
    trigger_type: str,
) -> None:
    """Test that a trigger line fires an event naming the source and the task."""
    events = async_capture_events(hass, EVENT_TRIGGER)

    fake_stream.instances[0].emit_log(f"{BUTTON_VID} {TASK_VID} {event} 1 0")
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(events) == 1
    assert events[0].data == {
        "host": HOST,
        "trigger_type": trigger_type,
        "event": event,
        "source_id": BUTTON_VID,
        "source_name": "Hall Button",
        "source_path": ["Hall Button", "Hall Keypad", "Controller 1"],
        "task_id": TASK_VID,
        "task_name": "Lamp",
        "param1": 1,
        "param2": 0,
    }


async def test_trigger_names_source_entity(
    hass: HomeAssistant,
    fake_stream: type[FakeEventStream],
    vantage_entry: MockConfigEntry,
) -> None:
    """Test that a trigger names its source's stock entity, so the entity's logbook shows it."""
    events = async_capture_events(hass, EVENT_TRIGGER)
    registry = er.async_get(hass)
    source_entity = registry.async_get_or_create(
        "binary_sensor", VANTAGE_DOMAIN, str(BUTTON_VID), config_entry=vantage_entry
    )
    # An entity for a part of another object, or of another entry, is not the source's
    registry.async_get_or_create(
        "sensor", VANTAGE_DOMAIN, f"{TASK_VID}:state", config_entry=vantage_entry
    )
    registry.async_get_or_create("sensor", "other", str(TASK_VID))

    stream = fake_stream.instances[0]
    stream.emit_log(f"{BUTTON_VID} {TASK_VID} 1 0 0")
    stream.emit_log(f"{TASK_VID} {TASK_VID} 1 0 0")
    await hass.async_block_till_done(wait_background_tasks=True)

    assert [event.data.get("entity_id") for event in events] == [
        source_entity.entity_id,
        None,
    ]
    assert "entity_id" not in events[1].data


async def test_trigger_from_unloaded_object(
    hass: HomeAssistant,
    fake_stream: type[FakeEventStream],
    enclosure_lookup: Any,
) -> None:
    """Test that a source the stock client lacks is looked up once, then cached."""
    events = async_capture_events(hass, EVENT_TRIGGER)
    timer = SimpleNamespace(vid=70, obj=SimpleNamespace(name="Sunset", parent=None))
    enclosure_lookup.return_value = [timer]

    # Forget the enclosure lookup the bus read at setup made
    enclosure_lookup.reset_mock()
    for _ in range(2):
        fake_stream.instances[0].emit_log(f"70 {TASK_VID} 3 0 0")
        await hass.async_block_till_done(wait_background_tasks=True)

    enclosure_lookup.assert_awaited_once()
    assert [event.data["source_path"] for event in events] == [["Sunset"], ["Sunset"]]


async def test_triggers_fire_in_order(
    hass: HomeAssistant,
    fake_stream: type[FakeEventStream],
    enclosure_lookup: Any,
) -> None:
    """Test that triggers fire in arrival order, though the first waits on a lookup."""
    events = async_capture_events(hass, EVENT_TRIGGER)
    answer = asyncio.Event()
    timer = SimpleNamespace(vid=70, obj=SimpleNamespace(name="Sunset", parent=None))

    async def slow_lookup(*args: Any) -> list[Any]:
        await answer.wait()
        return [timer]

    enclosure_lookup.side_effect = slow_lookup
    stream = fake_stream.instances[0]
    stream.emit_log(f"70 {TASK_VID} 3 0 0")
    stream.emit_log(f"{BUTTON_VID} {TASK_VID} 1 0 0")
    await asyncio.sleep(0)
    assert not events

    answer.set()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert [event.data["source_name"] for event in events] == ["Sunset", "Hall Button"]


async def test_trigger_failure_does_not_stop_later_triggers(
    hass: HomeAssistant,
    fake_stream: type[FakeEventStream],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test that a trigger that can't be described doesn't stop the next one."""
    events = async_capture_events(hass, EVENT_TRIGGER)

    with patch(
        "custom_components.vantage_diagnostics.triggers.describe_trigger",
        side_effect=[ValueError("Bad reply"), {"source_name": "Hall Button"}],
    ):
        stream = fake_stream.instances[0]
        stream.emit_log(f"70 {TASK_VID} 3 0 0")
        stream.emit_log(f"{BUTTON_VID} {TASK_VID} 1 0 0")
        await hass.async_block_till_done(wait_background_tasks=True)

    assert "Failed to handle TriggerReceived" in caplog.text
    assert [event.data["source_name"] for event in events] == ["Hall Button"]


async def test_trigger_line_is_not_a_system_log(
    hass: HomeAssistant, fake_stream: type[FakeEventStream]
) -> None:
    """Test that a line shared by both subscriptions only fires the matching event."""
    logs = async_capture_events(hass, EVENT_SYSTEM_LOG)

    fake_stream.instances[0].emit_log(f"{BUTTON_VID} {TASK_VID} 1 0 0")
    await hass.async_block_till_done(wait_background_tasks=True)

    assert not logs


@pytest.mark.parametrize(
    ("line", "data"),
    [
        (
            f"3 Controller Overload (VID:{MODULE_VID})",
            {
                "type": "warning",
                "source": "Controller",
                "message": f"Overload (VID:{MODULE_VID})",
                "object_id": MODULE_VID,
                "object_name": "Module 1",
            },
        ),
        (
            "4 Controller Out of memory",
            {"type": "error", "source": "Controller", "message": "Out of memory"},
        ),
        (
            f"2 Station Module Online: 1.1 [{MODULE_VID}]",
            {
                "type": "information",
                "source": "Station",
                "message": f"Module Online: 1.1 [{MODULE_VID}]",
                "object_id": MODULE_VID,
                "object_name": "Module 1",
            },
        ),
        (
            "3 Controller [ALERT] Check 1",
            {"type": "warning", "source": "Controller", "message": "[ALERT] Check 1"},
        ),
        (
            f"42 Station Module Status: 1.1 [{MODULE_VID}]",
            {
                "type": "unknown_42",
                "source": "Station",
                "message": f"Module Status: 1.1 [{MODULE_VID}]",
                "object_id": MODULE_VID,
                "object_name": "Module 1",
            },
        ),
        (
            f"3 Controller Overload (VID:{ENCLOSURE_VID})",
            {
                "type": "warning",
                "source": "Controller",
                "message": f"Overload (VID:{ENCLOSURE_VID})",
                "object_id": ENCLOSURE_VID,
                "object_name": "Enclosure 1",
            },
        ),
    ],
)
async def test_system_log_event(
    hass: HomeAssistant,
    fake_stream: type[FakeEventStream],
    line: str,
    data: dict[str, Any],
) -> None:
    """Test that a notable system log line fires an event."""
    events = async_capture_events(hass, EVENT_SYSTEM_LOG)

    fake_stream.instances[0].emit_log(line)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(events) == 1
    assert events[0].data == {"host": HOST} | data


@pytest.mark.parametrize(
    "line",
    [
        "2 Controller Task completed",
        "6 Controller Task 120 started",
        f"3 {COMPILER_SOURCE} Unused variable",
        "3 Controller Script 120 compiled with warnings in 0.5 seconds.",
    ],
)
async def test_system_log_noise_fires_nothing(
    hass: HomeAssistant, fake_stream: type[FakeEventStream], line: str
) -> None:
    """Test that routine logs and compiler output fire no event."""
    events = async_capture_events(hass, EVENT_SYSTEM_LOG)

    fake_stream.instances[0].emit_log(line)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert not events


async def test_system_log_burst_fires_once(
    hass: HomeAssistant, fake_stream: type[FakeEventStream]
) -> None:
    """Test that a burst of one warning over numbered stations fires one event."""
    events = async_capture_events(hass, EVENT_SYSTEM_LOG)

    for station in range(1, 4):
        fake_stream.instances[0].emit_log(
            f"3 Controller Bus command not processed on station {station}"
        )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(events) == 1


async def test_system_log_object_name_failure(
    hass: HomeAssistant,
    fake_stream: type[FakeEventStream],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test that a log still fires when its object can't be named."""
    events = async_capture_events(hass, EVENT_SYSTEM_LOG)

    with patch(
        "custom_components.vantage_diagnostics.names.ObjectNames.name",
        side_effect=ValueError("Bad reply"),
    ):
        fake_stream.instances[0].emit_log("3 Controller Overload (VID:300)")
        await hass.async_block_till_done(wait_background_tasks=True)

    assert events[0].data["object_name"] == "VID 300"
    assert "Failed to name VID 300" in caplog.text


def test_system_log_burst_reminder() -> None:
    """Test that a burst passes the first warning, then one again hourly."""
    seen: dict[Shape, tuple[float, float]] = {}

    def noise(message: str, now: float) -> bool:
        log = SystemLogReceived(SysLogType.WARNING, "Controller", message)
        return is_noise(log, seen, now)

    assert not noise("Station 1 is slow", 0)
    assert noise("Station 2 is slow", 30)

    # Still in the burst an hour after the first passed, so one passes as a reminder
    now = 30.0
    while now + 30 < REPEAT_REMINDER:
        now += 30
        assert noise("Station 3 is slow", now)
    now = REPEAT_REMINDER + 10
    assert not noise("Station 4 is slow", now)
    assert noise("Station 5 is slow", now + 30)

    # A quiet spell ends the burst
    now += 30 + REPEAT_WINDOW
    assert not noise("Station 6 is slow", now)

    # Warnings naming another VID stay apart
    assert not noise("Station 7 is slow (VID:300)", now + 1)
    assert not noise("Station 7 is slow (VID:301)", now + 2)
    assert noise("Station 8 is slow (VID:301)", now + 3)


@pytest.mark.parametrize(
    ("value", "trigger_type", "log_type"),
    [
        (None, "unknown", "unknown"),
        (3, "timer", "warning"),
        (99, "unknown_99", "unknown_99"),
    ],
)
def test_type_names(value: int | None, trigger_type: str, log_type: str) -> None:
    """Test naming trigger and system log types, known, unknown and missing."""
    assert enum_name(TriggerType, value) == trigger_type
    assert enum_name(SysLogType, value) == log_type


async def test_path_stops_at_a_loop(
    init_integration: MockConfigEntry, client: Any
) -> None:
    """Test that a parent loop in the configuration ends the path."""
    client.objects[KEYPAD_VID].parent = SimpleNamespace(vid=BUTTON_VID, position=1)
    names = init_integration.runtime_data.object_names

    assert await names.path(BUTTON_VID) == ["Hall Button", "Hall Keypad"]


def logbook_describers(
    hass: HomeAssistant,
) -> dict[str, Callable[[Event[Any]], dict[str, str]]]:
    """Return the logbook describers the integration registers, by event type."""
    describers: dict[str, Callable[[Event[Any]], dict[str, str]]] = {}

    def describe_event(
        domain: str, event_type: str, describer: Callable[[Event[Any]], dict[str, str]]
    ) -> None:
        # The trigger is filed under the stock domain, which an entity's own logbook asks for
        assert (
            domain
            == {EVENT_TRIGGER: VANTAGE_DOMAIN, EVENT_SYSTEM_LOG: DOMAIN}[event_type]
        )
        describers[event_type] = describer

    async_describe_events(hass, describe_event)
    return describers


async def test_logbook_system_log(hass: HomeAssistant) -> None:
    """Test the logbook description of a system log event."""
    describe = logbook_describers(hass)[EVENT_SYSTEM_LOG]

    system_log = describe(
        Event(EVENT_SYSTEM_LOG, {"type": "warning", "message": "Overload (VID:102)"})
    )
    assert system_log == {
        LOGBOOK_ENTRY_NAME: "Vantage",
        LOGBOOK_ENTRY_MESSAGE: "Warning: Overload (VID:102)",
    }

    # Missing values are left out rather than rendered as "None"
    system_log = describe(
        Event(EVENT_SYSTEM_LOG, {"type": None, "message": "Overload"})
    )
    assert system_log[LOGBOOK_ENTRY_MESSAGE] == "Overload"


TRIGGER_DATA: dict[str, Any] = {
    "trigger_type": "button_down",
    "source_id": BUTTON_VID,
    "source_name": "Hall Button",
    "source_path": ["Hall Button", "Hall Keypad", "Controller 1"],
    "task_id": TASK_VID,
    "task_name": "Lamp",
}


async def test_logbook_trigger(hass: HomeAssistant) -> None:
    """Test the logbook description of a trigger, linked to its source's entity when it has one."""
    describe = logbook_describers(hass)[EVENT_TRIGGER]

    assert describe(Event(EVENT_TRIGGER, TRIGGER_DATA)) == {
        LOGBOOK_ENTRY_NAME: "Vantage",
        LOGBOOK_ENTRY_MESSAGE: "Hall Button on Hall Keypad started task Lamp (button down)",
    }

    linked = describe(
        Event(EVENT_TRIGGER, TRIGGER_DATA | {"entity_id": "binary_sensor.hall_door"})
    )
    assert linked[LOGBOOK_ENTRY_ENTITY_ID] == "binary_sensor.hall_door"


@pytest.mark.parametrize(
    ("missing", "message"),
    [
        ("trigger_type", "Hall Button on Hall Keypad started task Lamp"),
        ("source_path", "Hall Button started task Lamp (button down)"),
        (
            ("source_path", "source_name"),
            f"VID {BUTTON_VID} started task Lamp (button down)",
        ),
        (
            ("source_path", "source_name", "source_id"),
            "A Vantage object started task Lamp (button down)",
        ),
        (
            "task_name",
            f"Hall Button on Hall Keypad started task VID {TASK_VID} (button down)",
        ),
        (
            ("task_name", "task_id"),
            "Hall Button on Hall Keypad started a task (button down)",
        ),
    ],
)
async def test_logbook_trigger_missing_fields(
    hass: HomeAssistant, missing: str | tuple[str, ...], message: str
) -> None:
    """Test that a trigger lacking an optional field, such as one an older version recorded, still reads."""
    describe = logbook_describers(hass)[EVENT_TRIGGER]
    fields = (missing,) if isinstance(missing, str) else missing
    data = {key: value for key, value in TRIGGER_DATA.items() if key not in fields}

    described = describe(Event(EVENT_TRIGGER, data))
    assert described[LOGBOOK_ENTRY_MESSAGE] == message
    assert LOGBOOK_ENTRY_ENTITY_ID not in described


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"trigger_type": "unknown"}, "Hall Button on Hall Keypad started task Lamp"),
        (
            {"trigger_type": "unknown_99"},
            "Hall Button on Hall Keypad started task Lamp (unknown 99)",
        ),
        (
            {"trigger_type": None, "task_name": None},
            f"Hall Button on Hall Keypad started task VID {TASK_VID}",
        ),
        (
            {"source_path": ["", "Hall Keypad"]},
            "Hall Button on Hall Keypad started task Lamp (button down)",
        ),
    ],
)
async def test_logbook_trigger_odd_fields(
    hass: HomeAssistant, data: dict[str, Any], message: str
) -> None:
    """Test that unknown and empty trigger fields still read."""
    describe = logbook_describers(hass)[EVENT_TRIGGER]

    described = describe(Event(EVENT_TRIGGER, TRIGGER_DATA | data))
    assert described[LOGBOOK_ENTRY_MESSAGE] == message
