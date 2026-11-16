"""Tests for the module connectivity binary sensors."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from aiovantage.errors import ClientConnectionError
from aiovantage.events import Disconnected, ObjectAdded
from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.vantage_diagnostics.aiovantage_diag import ModuleInfo
from custom_components.vantage_diagnostics.const import (
    DOMAIN,
    SERVICE_GET_MODULE_STATUS,
    UNLOADED,
)
from custom_components.vantage_diagnostics.module_status import (
    ModuleStatus,
    read_module_status,
)
from homeassistant.const import (
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    EntityCategory,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util

from .conftest import (
    ENCLOSURE_VID,
    MODULE_VID,
    FakeClient,
    FakeEventStream,
    make_module,
)

ONLINE = [ModuleInfo(address=1, vid=MODULE_VID, errors=0)]


def unique_id_to_entity_id(hass: HomeAssistant, vid: int) -> str | None:
    """Return the entity ID for a module's unique ID, which names the stock entry."""
    stock = hass.config_entries.async_entries("vantage")[0]
    return er.async_get(hass).async_get_entity_id(
        "binary_sensor", DOMAIN, f"{stock.entry_id}:{vid}:connectivity"
    )


def sensor_id(hass: HomeAssistant, vid: int = MODULE_VID) -> str:
    """Return the entity ID of a module's connectivity sensor."""
    entity_id = unique_id_to_entity_id(hass, vid)
    assert entity_id
    return entity_id


def state_of(hass: HomeAssistant) -> str | None:
    """Return the state of the first module's connectivity sensor."""
    state = hass.states.get(sensor_id(hass))
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


async def log_line(hass: HomeAssistant, stream: FakeEventStream, line: str) -> None:
    """Pass the stream a system log line."""
    stream.emit_log(line)
    await hass.async_block_till_done(wait_background_tasks=True)


@pytest.mark.usefixtures("init_integration")
async def test_disabled_by_default(
    hass: HomeAssistant, modules_read: AsyncMock
) -> None:
    """Test that the sensor starts disabled, and nothing reads the bus for it."""
    entry = er.async_get(hass).async_get(sensor_id(hass))
    assert entry
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert entry.translation_key == "connectivity"
    assert entry.entity_category is EntityCategory.DIAGNOSTIC
    assert entry.original_device_class == "connectivity"
    assert hass.states.get(sensor_id(hass)) is None
    modules_read.assert_not_awaited()


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_sensor_follows_the_bus(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    modules_read: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test a module dropping off its enclosure's bus and rejoining it."""
    modules_read.assert_awaited_once()
    assert state_of(hass) == STATE_ON
    state = hass.states.get(sensor_id(hass))
    assert state
    assert state.attributes["address"] == 1
    assert state.attributes["errors"] == 0

    # Missing from one read, so held online until a second read confirms it
    await refresh(hass, init_integration, ModuleStatus(enclosures={ENCLOSURE_VID}))
    assert state_of(hass) == STATE_ON

    # Off the bus, keeping the address and error count it was last seen with
    freezer.tick(timedelta(seconds=6))
    await refresh(hass, init_integration, ModuleStatus(enclosures={ENCLOSURE_VID}))
    assert state_of(hass) == STATE_OFF
    state = hass.states.get(sensor_id(hass))
    assert state
    assert state.attributes["address"] == 1

    # Back on the bus, with one more error
    await refresh(
        hass,
        init_integration,
        ModuleStatus(
            modules={MODULE_VID: ModuleInfo(address=1, vid=MODULE_VID, errors=1)},
            enclosures={ENCLOSURE_VID},
        ),
    )
    assert state_of(hass) == STATE_ON
    state = hass.states.get(sensor_id(hass))
    assert state
    assert state.attributes["errors"] == 1

    # Unknown while its enclosure can't be read, though another enclosure was
    await refresh(
        hass,
        init_integration,
        ModuleStatus(enclosures={999}, failed={ENCLOSURE_VID: "ClientConnectionError"}),
    )
    assert state_of(hass) == STATE_UNKNOWN


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_system_log_offline_and_online(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_stream: type[FakeEventStream],
    modules_read: AsyncMock,
) -> None:
    """Test that module logs apply at once, then read the bus."""
    stream = fake_stream.instances[0]
    assert state_of(hass) == STATE_ON

    modules_read.return_value = []
    await log_line(hass, stream, f"2 Station Module Offline: 1.1 [{MODULE_VID}]")
    assert state_of(hass) == STATE_OFF
    assert modules_read.await_count == 2

    # The online log turns it back on before any read finds it
    await log_line(hass, stream, f"2 Station Module Online: 1.1 [{MODULE_VID}]")
    assert state_of(hass) == STATE_ON


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_offline_log_without_a_vid(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_stream: type[FakeEventStream],
    modules_read: AsyncMock,
) -> None:
    """Test that a module log naming no VID still reads the bus."""
    await log_line(hass, fake_stream.instances[0], "2 Station Module Offline: 1.1")

    assert modules_read.await_count == 2
    assert state_of(hass) == STATE_ON


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_missing_module_confirmed_by_second_read(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    modules_read: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test that a module missing from a read is read again before it goes off."""
    coordinator = init_integration.runtime_data.module_status

    # The poll misses it, so it's held online and the bus is read again shortly
    modules_read.return_value = []
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert modules_read.await_count == 2
    assert state_of(hass) == STATE_ON

    # A read in between misses it too, but it's too soon to confirm
    freezer.tick(timedelta(seconds=1))
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert modules_read.await_count == 3
    assert state_of(hass) == STATE_ON

    # Still missing the full delay after the first miss, so it's off
    freezer.tick(timedelta(seconds=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert modules_read.await_count == 4
    assert state_of(hass) == STATE_OFF


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_module_back_before_confirming_read(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    modules_read: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test that a module back by the confirming read never shows off the bus."""
    seen: list[str | None] = []
    coordinator = init_integration.runtime_data.module_status
    coordinator.async_add_listener(lambda: seen.append(state_of(hass)))

    modules_read.return_value = []
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    modules_read.return_value = ONLINE
    freezer.tick(timedelta(seconds=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert STATE_OFF not in seen
    assert state_of(hass) == STATE_ON


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_log_newer_than_read_wins(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_stream: type[FakeEventStream],
    modules_read: AsyncMock,
) -> None:
    """Test that a module log arriving mid-read wins over what that read found."""
    stream = fake_stream.instances[0]

    async def read_then_log(*_args: Any, **_kwargs: Any) -> list[ModuleInfo]:
        stream.emit_log(f"2 Station Module Offline: 1.1 [{MODULE_VID}]")
        return ONLINE

    modules_read.side_effect = read_then_log
    await init_integration.runtime_data.module_status.async_refresh()
    await hass.async_block_till_done()
    assert state_of(hass) == STATE_OFF

    # The log asked for a read of its own, which finds it gone
    modules_read.side_effect = None
    modules_read.return_value = []
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert state_of(hass) == STATE_OFF


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_unavailable_while_no_enclosure_can_be_read(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test that the sensors are unavailable while no enclosure can be read."""
    await refresh(
        hass,
        init_integration,
        ModuleStatus(failed={ENCLOSURE_VID: "ClientConnectionError"}),
    )

    assert state_of(hass) == STATE_UNAVAILABLE
    assert (
        f"No enclosure could be read: enclosure {ENCLOSURE_VID}: ClientConnectionError"
        in caplog.text
    )


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_poll_failure(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    modules_read: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test that a client error from the read fails the update."""
    with patch(
        "custom_components.vantage_diagnostics.module_status.read_module_status",
        side_effect=ClientConnectionError("Refused"),
    ):
        await init_integration.runtime_data.module_status.async_refresh()
    await hass.async_block_till_done()

    assert state_of(hass) == STATE_UNAVAILABLE


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_unavailable_while_stream_down(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_stream: type[FakeEventStream],
) -> None:
    """Test that the sensor follows our own event stream's connection."""
    fake_stream.instances[0].emit(Disconnected())
    await hass.async_block_till_done()
    assert state_of(hass) == STATE_UNAVAILABLE

    from aiovantage.events import Reconnected  # noqa: PLC0415

    fake_stream.instances[0].emit(Reconnected())
    await hass.async_block_till_done()
    assert state_of(hass) == STATE_ON


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_unavailable_while_stock_entry_not_loaded(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    vantage_entry: MockConfigEntry,
) -> None:
    """Test that the sensor is unavailable once the stock entry holds another client."""
    # No state change, which would reload the entry, so only the check is exercised
    vantage_entry.runtime_data = SimpleNamespace(client=FakeClient())
    init_integration.runtime_data.module_status.async_update_listeners()

    assert state_of(hass) == STATE_UNAVAILABLE


async def test_entity_joins_the_stock_device(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    vantage_entry: MockConfigEntry,
) -> None:
    """Test that the entity sits on the stock device, and no second device exists."""
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_device_by_identifier(
        ("vantage", str(MODULE_VID)), vantage_entry.entry_id
    )
    assert device
    assert device.name == "Module 1"

    entity = er.async_get(hass).async_get("binary_sensor.module_1_connectivity")
    assert entity
    assert entity.device_id == device.id
    assert entity.unique_id == f"{vantage_entry.entry_id}:{MODULE_VID}:connectivity"
    assert sensor_id(hass) == "binary_sensor.module_1_connectivity"
    devices = [
        d for d in dev_reg.devices if ("vantage", str(MODULE_VID)) in d.identifiers
    ]
    assert devices == [device]


async def test_module_without_stock_device_gets_no_entity(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    client: FakeClient,
) -> None:
    """Test that a module the stock integration has not registered is skipped."""
    client.modules.fire(ObjectAdded(obj=make_module(104, "Module 4")))
    await hass.async_block_till_done()

    assert unique_id_to_entity_id(hass, 104) is None
    assert not [
        d for d in dr.async_get(hass).devices if ("vantage", "104") in d.identifiers
    ]


async def test_module_added_later(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    client: FakeClient,
    vantage_entry: MockConfigEntry,
) -> None:
    """Test that a module the stock integration learns of gets a sensor."""
    module = make_module(103, "Module 2")
    dr.async_get(hass).async_get_or_create(
        config_entry_id=vantage_entry.entry_id,
        identifiers={("vantage", "103")},
        name="Module 2",
    )
    client.modules.fire(ObjectAdded(obj=module))
    await hass.async_block_till_done()

    assert unique_id_to_entity_id(hass, 103)


@pytest.mark.usefixtures("init_integration")
async def test_status_service_reads_the_bus_while_sensors_are_disabled(
    hass: HomeAssistant, modules_read: AsyncMock
) -> None:
    """Test that the service reads the bus though no sensor listens."""
    modules_read.assert_not_awaited()

    await hass.services.async_call(
        DOMAIN, SERVICE_GET_MODULE_STATUS, blocking=True, return_response=True
    )

    modules_read.assert_awaited_once()


@pytest.mark.usefixtures("init_integration")
async def test_unload_during_refresh_is_no_failure(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test that a refresh the unload interrupts doesn't fail or log an error."""
    coordinator = init_integration.runtime_data.module_status
    init_integration.runtime_data.rpc.close()

    await refresh(
        hass, init_integration, ModuleStatus(failed={ENCLOSURE_VID: UNLOADED})
    )

    assert coordinator.last_update_success
    assert "No enclosure could be read" not in caplog.text


async def test_read_status_unload_after_lookup(
    init_integration: MockConfigEntry, enclosure_lookup: AsyncMock
) -> None:
    """Test that an unload landing in an enclosure's lookup stops the read."""
    runtime_data = init_integration.runtime_data
    enclosure = enclosure_lookup.return_value

    async def unload_during_lookup(*args: Any) -> list[Any]:
        runtime_data.rpc.close()
        return enclosure

    enclosure_lookup.side_effect = unload_during_lookup
    runtime_data.object_names._cache.clear()  # noqa: SLF001
    with patch(
        "custom_components.vantage_diagnostics.module_status.DiagnosticInterface.get_modules"
    ) as get_modules:
        status = await read_module_status(
            runtime_data.client, runtime_data.object_names, runtime_data.rpc
        )

    get_modules.assert_not_awaited()
    assert status.failed == {ENCLOSURE_VID: UNLOADED}


async def test_read_status_lookup_cancelled(
    init_integration: MockConfigEntry, enclosure_lookup: AsyncMock
) -> None:
    """Test that an enclosure whose lookup is in flight at unload is reported unloaded."""
    runtime_data = init_integration.runtime_data
    looking_up = asyncio.Event()
    release = asyncio.Event()

    async def lookup_until_released(*args: Any) -> list[Any]:
        looking_up.set()
        await release.wait()
        return []

    enclosure_lookup.side_effect = lookup_until_released
    runtime_data.object_names._cache.clear()  # noqa: SLF001
    read = asyncio.ensure_future(
        read_module_status(
            runtime_data.client, runtime_data.object_names, runtime_data.rpc
        )
    )
    await looking_up.wait()
    runtime_data.rpc.close()

    # The lookup already sent is not cancelled, so the read waits for it, then stops
    release.set()
    status = await read

    assert status.failed == {ENCLOSURE_VID: UNLOADED}


async def test_read_status_interrupted(init_integration: MockConfigEntry) -> None:
    """Test that an enclosure whose read the unload interrupted is reported unloaded."""
    runtime_data = init_integration.runtime_data

    async def unload_during_read(*args: Any, **kwargs: Any) -> list[Any]:
        runtime_data.rpc.close()
        raise ClientConnectionError

    with patch(
        "custom_components.vantage_diagnostics.module_status.DiagnosticInterface.get_modules",
        side_effect=unload_during_read,
    ):
        status = await read_module_status(
            runtime_data.client, runtime_data.object_names, runtime_data.rpc
        )

    assert status.failed == {ENCLOSURE_VID: UNLOADED}


async def test_read_status_enclosure_without_position(
    init_integration: MockConfigEntry, enclosure_lookup: AsyncMock
) -> None:
    """Test that an enclosure whose position can't be read is reported."""
    runtime_data = init_integration.runtime_data
    enclosure_lookup.return_value = [
        SimpleNamespace(
            vid=ENCLOSURE_VID, obj=SimpleNamespace(name="Enclosure 1", parent=None)
        )
    ]
    runtime_data.object_names.clear()

    status = await read_module_status(
        runtime_data.client, runtime_data.object_names, runtime_data.rpc
    )

    assert status.failed == {ENCLOSURE_VID: "Enclosure position could not be read"}


async def test_read_status_peer_master_is_addressed(
    init_integration: MockConfigEntry, modules_read: AsyncMock
) -> None:
    """Test that an enclosure on another master is read through that master."""
    runtime_data = init_integration.runtime_data
    runtime_data.client.modules.objects[0].master = 2

    status = await read_module_status(
        runtime_data.client, runtime_data.object_names, runtime_data.rpc
    )

    assert modules_read.await_args_list[-1].kwargs == {"master": 2}
    assert status.enclosures == {ENCLOSURE_VID}
