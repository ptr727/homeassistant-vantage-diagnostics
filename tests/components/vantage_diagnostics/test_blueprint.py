"""Tests for the module fault notification blueprint, set up as an automation from its file."""

from datetime import timedelta
from pathlib import Path
import shutil
from typing import Any

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.vantage_diagnostics.const import (
    DOMAIN,
    EVENT_MODULE_FAULT,
    EVENT_MODULE_RECOVERED,
    EVENT_TRIGGER,
    VANTAGE_DOMAIN,
)
from custom_components.vantage_diagnostics.entity import stock_device
from homeassistant.components.automation.const import DOMAIN as AUTOMATION_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from .conftest import MASTER_VID, MODULE_VID

BLUEPRINT = (
    Path(__file__).parents[3]
    / "blueprints"
    / "automation"
    / "vantage_diagnostics"
    / "vantage_module_fault.yaml"
)
NOTIFIED = "vantage_blueprint_test"
OTHER_MODULE_VID = 999
POWER_SENSOR = "sensor.module_1_line_a_power"


@pytest.fixture
async def blueprint_dir(
    hass: HomeAssistant, init_integration: MockConfigEntry, tmp_path: Path
) -> None:
    """Put the blueprint where Home Assistant loads blueprints from."""
    folder = tmp_path / "blueprints" / "automation" / "vantage_diagnostics"
    folder.mkdir(parents=True)
    shutil.copy(BLUEPRINT, folder)
    hass.config.config_dir = str(tmp_path)


def module_device(hass: HomeAssistant) -> dr.DeviceEntry:
    """Return the stock device of the module."""
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    device = stock_device(hass, entry, MODULE_VID)
    assert device is not None
    return device


async def setup_blueprint(hass: HomeAssistant, **inputs: Any) -> list[Any]:
    """Set up an automation from the blueprint, whose action fires its variables as an event."""
    notified = async_capture_events(hass, NOTIFIED)
    action = {
        "event": NOTIFIED,
        "event_data": {
            "kind": "{{ kind }}",
            "title": "{{ title }}",
            "message": "{{ message }}",
        },
    }
    assert await async_setup_component(
        hass,
        AUTOMATION_DOMAIN,
        {
            AUTOMATION_DOMAIN: {
                "id": "vantage_module_fault",
                "alias": "Vantage module fault",
                "use_blueprint": {
                    "path": "vantage_diagnostics/vantage_module_fault.yaml",
                    "input": {"actions": [action], **inputs},
                },
            }
        },
    )
    await hass.async_block_till_done()
    state = hass.states.get("automation.vantage_module_fault")
    assert state is not None
    assert state.state == "on"
    return notified


def fault_data(vid: int = MODULE_VID, **fields: Any) -> dict[str, Any]:
    """Return a fault report as the fault event carries it."""
    return {
        "module_id": vid,
        "module_name": "Module 1",
        "signal": "system_log",
        "time": "2026-11-18T14:47:05+00:00",
        "line_feed": 1,
        "line_feed_name": "Line A",
        "loads": [
            {"name": "Kitchen Cans", "on_missing_line": True, "level": 100.0},
            {"name": "Pantry", "on_missing_line": True, "level": 0.0},
            {"name": "Hall Sconces", "on_missing_line": True, "level": 40.4},
            {"name": "Porch", "on_missing_line": False, "level": 100.0},
        ],
        **fields,
    }


def range_data(trigger_type: str, **fields: Any) -> dict[str, Any]:
    """Return a range trigger as the trigger event carries it."""
    return {
        "trigger_type": trigger_type,
        "source_name": "Power Sensor - Line A",
        "source_path": [
            "Power Sensor - Line A",
            "Module 1",
            "Enclosure 1",
            "Controller 1",
        ],
        "task_name": "Line A Power",
        "module_id": MODULE_VID,
        "side_effect": False,
        **fields,
    }


def local_time(time: str) -> str:
    """Return a UTC time as the blueprint's messages show it."""
    parsed = dt_util.parse_datetime(time)
    assert parsed is not None
    return dt_util.as_local(parsed).strftime("%H:%M")


@pytest.mark.usefixtures("blueprint_dir")
async def test_fault_and_recovery(hass: HomeAssistant) -> None:
    """Test that a fault names the loads on its missing line, and a recovery its outage."""
    notified = await setup_blueprint(hass, modules=[module_device(hass).id])

    hass.bus.async_fire(EVENT_MODULE_FAULT, fault_data())
    hass.bus.async_fire(
        EVENT_MODULE_FAULT,
        fault_data(line_feed=None, line_feed_name=None, signal="offline"),
    )
    hass.bus.async_fire(
        EVENT_MODULE_FAULT,
        fault_data(line_feed=None, line_feed_name=None, signal="module_alert"),
    )
    hass.bus.async_fire(EVENT_MODULE_RECOVERED, fault_data(outage=125.4))
    hass.bus.async_fire(EVENT_MODULE_RECOVERED, fault_data(outage=8.0))
    # A module the automation does not watch
    hass.bus.async_fire(EVENT_MODULE_FAULT, fault_data(vid=OTHER_MODULE_VID))
    await hass.async_block_till_done()

    at = local_time(fault_data()["time"])
    assert [(event.data["kind"], event.data["message"]) for event in notified] == [
        (
            "fault",
            (
                f"Module 1 lost Line A at {at}. "
                "Loads on it: Kitchen Cans (100%), Pantry (off), Hall Sconces (40%)."
            ),
        ),
        ("fault", f"Module 1 dropped off its bus at {at}."),
        ("fault", f"Module 1 raised a fault at {at}."),
        ("recovered", "Module 1 is back on its bus after 2 min 5 s."),
        ("recovered", "Module 1 is back on its bus after 8 s."),
    ]
    assert notified[0].data["title"] == "Vantage module fault"
    assert notified[3].data["title"] == "Vantage module recovered"


@pytest.mark.usefixtures("blueprint_dir")
async def test_signals_switched_off(hass: HomeAssistant) -> None:
    """Test that a signal switched off notifies nothing."""
    notified = await setup_blueprint(
        hass,
        notify_fault=False,
        notify_recovered=False,
        notify_out_of_range=False,
        notify_in_range=False,
    )

    hass.bus.async_fire(EVENT_MODULE_FAULT, fault_data())
    hass.bus.async_fire(EVENT_MODULE_RECOVERED, fault_data(outage=10.0))
    hass.bus.async_fire(EVENT_TRIGGER, range_data("out_of_range"))
    hass.bus.async_fire(EVENT_TRIGGER, range_data("in_range"))
    await hass.async_block_till_done()

    assert notified == []


@pytest.mark.usefixtures("blueprint_dir")
async def test_range_triggers(hass: HomeAssistant) -> None:
    """Test that genuine range readings notify, and a trip's side effects and other tasks don't."""
    notified = await setup_blueprint(
        hass, modules=[module_device(hass).id], tasks=["Line A Power"]
    )

    hass.bus.async_fire(EVENT_TRIGGER, range_data("out_of_range"))
    hass.bus.async_fire(EVENT_TRIGGER, range_data("in_range"))
    hass.bus.async_fire(EVENT_TRIGGER, range_data("out_of_range", side_effect=True))
    hass.bus.async_fire(EVENT_TRIGGER, range_data("out_of_range", task_name="Other"))
    hass.bus.async_fire(
        EVENT_TRIGGER, range_data("out_of_range", module_id=OTHER_MODULE_VID)
    )
    hass.bus.async_fire(EVENT_TRIGGER, range_data("out_of_range", module_id=None))
    hass.bus.async_fire(EVENT_TRIGGER, {"trigger_type": "button_down"})
    await hass.async_block_till_done()

    assert [(event.data["kind"], event.data["message"]) for event in notified] == [
        ("out_of_range", "Power Sensor - Line A on Module 1 is out of range."),
        ("in_range", "Power Sensor - Line A on Module 1 is back in range."),
    ]
    assert notified[0].data["title"] == "Vantage sensor out of range"
    assert notified[1].data["title"] == "Vantage sensor back in range"


@pytest.mark.usefixtures("blueprint_dir")
async def test_side_effects_notified(hass: HomeAssistant) -> None:
    """Test that side effects notify when asked to, and every module and task is watched by default."""
    notified = await setup_blueprint(hass, notify_side_effects=True)

    hass.bus.async_fire(EVENT_TRIGGER, range_data("out_of_range", side_effect=True))
    hass.bus.async_fire(
        EVENT_TRIGGER,
        range_data(
            "in_range", module_id=None, task_name="Other", source_path=["Sensor"]
        ),
    )
    await hass.async_block_till_done()

    assert [event.data["message"] for event in notified] == [
        "Power Sensor - Line A on Module 1 is out of range, a side effect of a module fault.",
        "Sensor is back in range.",
    ]


@pytest.mark.parametrize(
    ("watch_module", "notify_fault", "notified_after_fault"),
    [
        # The fault notified, so the fallback adds nothing
        (True, True, 1),
        # The fault did not notify, so the fallback still does
        (True, False, 2),
        (False, True, 2),
    ],
)
@pytest.mark.usefixtures("blueprint_dir")
async def test_stock_fallback(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    watch_module: bool,
    notify_fault: bool,
    notified_after_fault: int,
) -> None:
    """Test that a line power sensor below 0 notifies, unless a recent fault of its module notified."""
    vantage_entry = hass.config_entries.async_entries(VANTAGE_DOMAIN)[0]
    er.async_get(hass).async_get_or_create(
        "sensor",
        VANTAGE_DOMAIN,
        "402",
        suggested_object_id="module_1_line_a_power",
        config_entry=vantage_entry,
        device_id=module_device(hass).id,
    )
    attributes = {"friendly_name": "Module 1 Power Sensor - Line A"}
    hass.states.async_set(POWER_SENSOR, "120.5", attributes)
    watched = stock_device(
        hass, init_integration, MODULE_VID if watch_module else MASTER_VID
    )
    assert watched is not None
    notified = await setup_blueprint(
        hass,
        modules=[watched.id],
        notify_fault=notify_fault,
        fallback_sensors=[POWER_SENSOR],
    )

    async def trip(hold: timedelta) -> None:
        hass.states.async_set(POWER_SENSOR, "-0.999", attributes)
        await hass.async_block_till_done()
        freezer.tick(hold)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        hass.states.async_set(POWER_SENSOR, "120.5", attributes)
        await hass.async_block_till_done()

    # The module has never faulted, as this integration failing to set up would leave it, and a reading shorter than the hold is ignored
    await trip(timedelta(minutes=1, seconds=50))
    assert notified == []
    await trip(timedelta(minutes=2, seconds=1))
    assert [(event.data["kind"], event.data["message"]) for event in notified] == [
        (
            "fallback",
            "Module 1 Power Sensor - Line A reads no power, so its module may have lost power.",
        ),
    ]
    assert notified[0].data["title"] == "Vantage line power lost"

    # This integration reported the fault
    last_fault = next(
        entry.entity_id
        for entry in er.async_entries_for_device(
            er.async_get(hass), module_device(hass).id
        )
        if entry.unique_id.endswith("last_fault")
    )
    state = hass.states.get(last_fault)
    assert state is not None
    hass.states.async_set(
        last_fault, dt_util.utcnow().isoformat(), dict(state.attributes)
    )
    await trip(timedelta(minutes=2, seconds=1))
    assert len(notified) == notified_after_fault


@pytest.mark.usefixtures("blueprint_dir")
async def test_manual_run(hass: HomeAssistant) -> None:
    """Test that running the automation by hand sends a test notification."""
    notified = await setup_blueprint(hass)

    await hass.services.async_call(
        AUTOMATION_DOMAIN,
        "trigger",
        {"entity_id": "automation.vantage_module_fault", "skip_condition": False},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert [event.data for event in notified] == [
        {
            "kind": "test",
            "title": "Vantage module fault test",
            "message": "A test of the Vantage module fault automation.",
        }
    ]


@pytest.mark.usefixtures("blueprint_dir")
async def test_default_inputs(hass: HomeAssistant) -> None:
    """Test that the blueprint sets up with no inputs at all, and runs no action then."""
    assert await async_setup_component(
        hass,
        AUTOMATION_DOMAIN,
        {
            AUTOMATION_DOMAIN: {
                "id": "vantage_module_fault",
                "alias": "Vantage module fault",
                "use_blueprint": {
                    "path": "vantage_diagnostics/vantage_module_fault.yaml",
                    "input": {},
                },
            }
        },
    )
    await hass.async_block_till_done()
    hass.bus.async_fire(EVENT_MODULE_FAULT, fault_data())
    await hass.async_block_till_done()

    state = hass.states.get("automation.vantage_module_fault")
    assert state is not None
    assert state.state == "on"
    assert state.attributes["last_triggered"] is not None
