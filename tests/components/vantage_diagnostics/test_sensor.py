"""Tests for the module and controller diagnostic sensors."""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiovantage.events import Disconnected, ObjectAdded, Reconnected
from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vantage_diagnostics.aiovantage_diag import ModuleInfo
from custom_components.vantage_diagnostics.const import DOMAIN
from custom_components.vantage_diagnostics.module_status import ModuleStatus
from homeassistant.components.sensor import ATTR_STATE_CLASS, SensorStateClass
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .conftest import (
    ENCLOSURE_VID,
    HOST,
    MASTER_VID,
    MODULE_VID,
    FakeClient,
    FakeEventStream,
    make_module,
)

MODULE_SENSORS = (
    "hardware_model",
    "hardware_revision",
    "firmware_version",
    "bus_errors",
)

FULL_INFO = ModuleInfo(
    address=1,
    vid=MODULE_VID,
    revision=5,
    type=7,
    version="2.1.0 (build 4)",
    status=ModuleInfo.Status(proc0="00", proc1="00"),
    errors=3,
    model="Example-Model",
)

# The field of the bus status each sensor reports, and the state it shows for FULL_INFO
FIELDS = {
    "hardware_model": ("model", "Example-Model"),
    "hardware_revision": ("revision", "5"),
    "firmware_version": ("version", "2.1.0 (build 4)"),
    "bus_errors": ("errors", "3"),
}


def sensor_id(hass: HomeAssistant, key: str, vid: int = MODULE_VID) -> str | None:
    """Return the entity ID of an object's sensor, whose unique ID names the stock entry."""
    stock = hass.config_entries.async_entries("vantage")[0]
    return er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{stock.entry_id}:{vid}:{key}"
    )


def state_of(hass: HomeAssistant, key: str, vid: int = MODULE_VID) -> str | None:
    """Return the state of an object's sensor."""
    entity_id = sensor_id(hass, key, vid)
    assert entity_id
    state = hass.states.get(entity_id)
    return state.state if state else None


async def refresh(
    hass: HomeAssistant, entry: MockConfigEntry, status: ModuleStatus
) -> None:
    """Refresh the module status coordinator, reading the given status."""
    with patch(
        "custom_components.vantage_diagnostics.module_status.read_module_status",
        return_value=status,
    ):
        await entry.runtime_data.module_status.async_refresh()
    await hass.async_block_till_done()


@pytest.fixture
def full_info_read(modules_read: AsyncMock) -> AsyncMock:
    """Answer the bus read with every field of the module's status set."""
    modules_read.return_value = [FULL_INFO]
    return modules_read


@pytest.mark.usefixtures("init_integration")
async def test_disabled_by_default(
    hass: HomeAssistant, modules_read: AsyncMock
) -> None:
    """Test that every sensor starts disabled as a diagnostic, and nothing reads the bus for them."""
    registry = er.async_get(hass)
    for key, vid in [
        *((key, MODULE_VID) for key in MODULE_SENSORS),
        ("host", MASTER_VID),
    ]:
        entity_id = sensor_id(hass, key, vid)
        assert entity_id, key
        entry = registry.async_get(entity_id)
        assert entry
        assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
        assert entry.translation_key == key
        assert entry.entity_category is EntityCategory.DIAGNOSTIC
        assert hass.states.get(entity_id) is None

    modules_read.assert_not_awaited()


@pytest.mark.usefixtures("entity_registry_enabled_by_default", "full_info_read")
async def test_module_sensors_report_the_bus_status(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test the sensors reporting the module's status, and keeping it while it is off the bus."""
    for key, (_field, value) in FIELDS.items():
        assert state_of(hass, key) == value, key

    entity_id = sensor_id(hass, "bus_errors")
    assert entity_id
    state = hass.states.get(entity_id)
    assert state
    assert state.attributes[ATTR_STATE_CLASS] == SensorStateClass.TOTAL_INCREASING

    # Missing from two reads the confirm delay apart, so off the bus
    await refresh(hass, init_integration, ModuleStatus(enclosures={ENCLOSURE_VID}))
    freezer.tick(timedelta(seconds=6))
    await refresh(hass, init_integration, ModuleStatus(enclosures={ENCLOSURE_VID}))

    for key, (_field, value) in FIELDS.items():
        assert state_of(hass, key) == value, key

    # Back with a newer revision and one more error, as after a module swap
    await refresh(
        hass,
        init_integration,
        ModuleStatus(
            modules={MODULE_VID: replace(FULL_INFO, revision=6, errors=4)},
            enclosures={ENCLOSURE_VID},
        ),
    )
    assert state_of(hass, "hardware_revision") == "6"
    assert state_of(hass, "bus_errors") == "4"


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
@pytest.mark.parametrize("key", list(FIELDS))
async def test_module_status_lacking_a_field(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    fake_stream: type[FakeEventStream],
    modules_read: AsyncMock,
    key: str,
) -> None:
    """Test that a field the controller leaves out is unknown, and the other sensors still report."""
    field, _value = FIELDS[key]
    modules_read.return_value = [replace(FULL_INFO, **{field: None})]
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    for other, (_field, value) in FIELDS.items():
        assert state_of(hass, other) == (STATE_UNKNOWN if other == key else value)


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_module_status_with_only_a_vid(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    fake_stream: type[FakeEventStream],
    modules_read: AsyncMock,
) -> None:
    """Test a module the controller reports nothing about but its VID."""
    modules_read.return_value = [ModuleInfo(vid=MODULE_VID)]
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    for key in MODULE_SENSORS:
        assert state_of(hass, key) == STATE_UNKNOWN, key


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_module_never_read_is_unknown(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    fake_stream: type[FakeEventStream],
    modules_read: AsyncMock,
) -> None:
    """Test that a module off the bus since setup has nothing to report."""
    modules_read.return_value = []
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    for key in MODULE_SENSORS:
        assert state_of(hass, key) == STATE_UNKNOWN, key


@pytest.mark.usefixtures("entity_registry_enabled_by_default", "full_info_read")
async def test_module_sensors_unavailable_while_stream_down(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_stream: type[FakeEventStream],
) -> None:
    """Test that the module sensors follow our own event stream's connection."""
    fake_stream.instances[0].emit(Disconnected())
    await hass.async_block_till_done()
    for key in MODULE_SENSORS:
        assert state_of(hass, key) == STATE_UNAVAILABLE, key

    fake_stream.instances[0].emit(Reconnected())
    await hass.async_block_till_done()
    assert state_of(hass, "hardware_model") == "Example-Model"


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_module_sensors_unavailable_when_no_enclosure_read(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test that the module sensors are unavailable while the bus can't be read."""
    await refresh(
        hass, init_integration, ModuleStatus(failed={ENCLOSURE_VID: "Refused"})
    )

    for key in MODULE_SENSORS:
        assert state_of(hass, key) == STATE_UNAVAILABLE, key


async def test_module_added_later_gets_sensors(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    client: FakeClient,
    vantage_entry: MockConfigEntry,
) -> None:
    """Test that a module the stock integration learns of later gets its sensors."""
    dr.async_get(hass).async_get_or_create(
        config_entry_id=vantage_entry.entry_id,
        identifiers={("vantage", "103")},
        name="Module 2",
    )
    client.modules.fire(ObjectAdded(obj=make_module(103, "Module 2")))
    await hass.async_block_till_done()

    for key in MODULE_SENSORS:
        assert sensor_id(hass, key, 103), key


@pytest.mark.usefixtures("entity_registry_enabled_by_default", "init_integration")
async def test_host_sensor(hass: HomeAssistant) -> None:
    """Test that the controller's host shows on the controller this integration connects to."""
    assert state_of(hass, "host", MASTER_VID) == HOST

    entity_id = sensor_id(hass, "host", MASTER_VID)
    assert entity_id
    entry = er.async_get(hass).async_get(entity_id)
    assert entry
    device = dr.async_get(hass).async_get_device_by_identifier(
        ("vantage", str(MASTER_VID)),
        hass.config_entries.async_entries("vantage")[0].entry_id,
    )
    assert device
    assert entry.device_id == device.id


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_host_sensor_unavailable_while_stock_entry_not_loaded(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    vantage_entry: MockConfigEntry,
) -> None:
    """Test that the host sensor is unavailable once the stock entry holds another client."""
    # No state change, which would reload the entry, so only the check is exercised
    vantage_entry.runtime_data = SimpleNamespace(client=FakeClient())
    async_dispatcher_send(hass, init_integration.runtime_data.signal_connection_changed)
    await hass.async_block_till_done()

    assert state_of(hass, "host", MASTER_VID) == STATE_UNAVAILABLE


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
@pytest.mark.parametrize("number", [2, None])
async def test_no_controller_sensor_without_the_connected_master(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    fake_stream: type[FakeEventStream],
    client: FakeClient,
    number: int | None,
) -> None:
    """Test that no host sensor is added when the connected master isn't in the project."""
    client.master_number = number
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert sensor_id(hass, "host", MASTER_VID) is None
    assert sensor_id(hass, "hardware_model")


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_no_controller_sensor_without_its_stock_device(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    fake_stream: type[FakeEventStream],
    vantage_entry: MockConfigEntry,
) -> None:
    """Test that no host sensor is added when the stock integration has no device for the controller."""
    registry = dr.async_get(hass)
    device = registry.async_get_device_by_identifier(
        ("vantage", str(MASTER_VID)),
        hass.config_entries.async_entries("vantage")[0].entry_id,
    )
    assert device
    registry.async_remove_device(device.id)
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert sensor_id(hass, "host", MASTER_VID) is None
