"""Tests for setting up and unloading the Vantage Diagnostics integration."""

from types import SimpleNamespace

from aiovantage.errors import ClientConnectionError, LoginFailedError
from aiovantage.events import Connected, Disconnected, ObjectUpdated, Reconnected
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vantage_diagnostics.const import (
    CONF_VANTAGE_ENTRY_ID,
    DOMAIN,
    SERVICE_GET_MODULE_STATUS,
    SERVICE_GET_SYSTEM_LOG,
    SERVICE_GET_TRIGGER_HISTORY,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.loader import IntegrationNotFound, async_get_integration
from homeassistant.setup import async_setup_component

from .conftest import HOST, FakeClient, FakeEventStream, add_vantage_entry


async def test_loads_without_vantage_integration(hass: HomeAssistant) -> None:
    """Test that the domain sets up with no vantage integration installed at all."""
    # Not installed, as distinct from installed but not loaded
    with pytest.raises(IntegrationNotFound):
        await async_get_integration(hass, "vantage")

    integration = await async_get_integration(hass, DOMAIN)
    assert integration.after_dependencies == ["vantage"]

    assert await async_setup_component(hass, DOMAIN, {})
    assert "vantage" not in hass.config.components
    assert hass.services.has_service(DOMAIN, SERVICE_GET_SYSTEM_LOG)


async def test_entry_without_vantage_entry(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test that an entry whose stock entry is gone retries, and says why."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_VANTAGE_ENTRY_ID: "missing"}, unique_id="missing"
    )
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert "delete this entry and add it again" in (entry.reason or "")


async def test_entry_whose_vantage_entry_was_deleted(
    hass: HomeAssistant, vantage_entry: MockConfigEntry
) -> None:
    """Test that a deleted stock entry is an error, not a retry that never succeeds."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_VANTAGE_ENTRY_ID: vantage_entry.entry_id}
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_remove(vantage_entry.entry_id)

    assert not await hass.config_entries.async_setup(entry.entry_id)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert "delete this entry and add it again" in (entry.reason or "")


async def test_entry_with_vantage_entry_not_loaded(
    hass: HomeAssistant, client: FakeClient
) -> None:
    """Test that a stock entry still starting leaves the entry retrying."""
    vantage_entry = add_vantage_entry(hass, client, ConfigEntryState.SETUP_IN_PROGRESS)
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_VANTAGE_ENTRY_ID: vantage_entry.entry_id}
    )
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_entry_with_vantage_entry_without_client(
    hass: HomeAssistant, client: FakeClient
) -> None:
    """Test that a loaded stock entry with no runtime data leaves the entry retrying."""
    vantage_entry = add_vantage_entry(hass, client)
    del vantage_entry.runtime_data
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_VANTAGE_ENTRY_ID: vantage_entry.entry_id}
    )
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)

    assert entry.state is ConfigEntryState.SETUP_RETRY


@pytest.mark.parametrize(
    "stream_start_error",
    [ClientConnectionError("Refused"), LoginFailedError("Bad login")],
)
async def test_stream_start_failure(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    fake_stream: type[FakeEventStream],
) -> None:
    """Test that a stream that can't start retries setup, and closes the stream."""
    config_entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(config_entry.entry_id)

    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert "Failed to connect to the controller" in (config_entry.reason or "")
    fake_stream.instances[0].stop.assert_called_once()


@pytest.mark.usefixtures("init_integration")
async def test_stream_is_built_from_stock_entry(
    fake_stream: type[FakeEventStream],
) -> None:
    """Test that the stream gets its own session with the stock entry's settings."""
    assert len(fake_stream.instances) == 1
    stream = fake_stream.instances[0]
    assert stream.args == (HOST, "user", "secret")
    assert stream.kwargs["ssl"] is True


async def test_subscribes_to_both_logs(
    init_integration: MockConfigEntry,
    fake_stream: type[FakeEventStream],
    client: FakeClient,
) -> None:
    """Test that both log types are subscribed, for the connected master and peers."""
    stream = fake_stream.instances[0]
    assert [types for _callback, types in stream.log_subscriptions] == [
        ("EVENT",),
        ("SYSTEM",),
    ]
    assert stream.peers_seen == [client.peers, client.peers]
    assert init_integration.state is ConfigEntryState.LOADED


@pytest.mark.parametrize("supports_enhanced_log", [False])
async def test_no_enhanced_log(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    fake_stream: type[FakeEventStream],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test that firmware without the enhanced log subscribes to neither log."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert fake_stream.instances[0].log_subscriptions == []
    assert "no enhanced log" in caplog.text
    assert config_entry.state is ConfigEntryState.LOADED


async def test_unload_closes_stream(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_stream: type[FakeEventStream],
) -> None:
    """Test that an unload closes the stream and drops the subscriptions."""
    stream = fake_stream.instances[0]
    rpc = init_integration.runtime_data.rpc

    assert await hass.config_entries.async_unload(init_integration.entry_id)

    assert init_integration.state is ConfigEntryState.NOT_LOADED
    stream.stop.assert_called_once()
    assert stream.unsubscribe.call_count >= 2
    assert not rpc.usable


async def test_stock_entry_reload_reloads_ours(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    vantage_entry: MockConfigEntry,
    fake_stream: type[FakeEventStream],
) -> None:
    """Test that the stock entry leaving the loaded state reloads this entry."""
    first = fake_stream.instances[0]

    vantage_entry.mock_state(hass, ConfigEntryState.NOT_LOADED)
    await hass.async_block_till_done()

    # The reload closed the stream, and finds the stock entry not loaded yet
    first.stop.assert_called_once()
    assert init_integration.state is ConfigEntryState.SETUP_RETRY

    # Loaded again, a retry sets up
    vantage_entry.mock_state(hass, ConfigEntryState.LOADED)
    await hass.config_entries.async_reload(init_integration.entry_id)
    await hass.async_block_till_done()
    assert init_integration.state is ConfigEntryState.LOADED


async def test_stock_client_replaced_reloads_ours(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    vantage_entry: MockConfigEntry,
    fake_stream: type[FakeEventStream],
) -> None:
    """Test that a new stock client, seen at once as loaded, still reloads this entry."""
    new_client = FakeClient()
    vantage_entry.runtime_data = SimpleNamespace(client=new_client)
    vantage_entry.mock_state(hass, ConfigEntryState.LOADED)
    await hass.async_block_till_done()

    assert init_integration.state is ConfigEntryState.LOADED
    assert init_integration.runtime_data.client is new_client
    assert len(fake_stream.instances) == 2

    # The same client in the same state is no reason to reload
    vantage_entry.mock_state(hass, ConfigEntryState.LOADED)
    await hass.async_block_till_done()
    assert len(fake_stream.instances) == 2


async def test_reload_scheduled_once(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    vantage_entry: MockConfigEntry,
    fake_stream: type[FakeEventStream],
) -> None:
    """Test that several stock state changes before the reload run it once."""
    vantage_entry.mock_state(hass, ConfigEntryState.UNLOAD_IN_PROGRESS)
    vantage_entry.mock_state(hass, ConfigEntryState.NOT_LOADED)
    await hass.async_block_till_done()

    fake_stream.instances[0].stop.assert_called_once()
    assert len(fake_stream.instances) == 1


async def test_connection_events_set_availability(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_stream: type[FakeEventStream],
) -> None:
    """Test that the connection events of the stream update the connected flag."""
    stream = fake_stream.instances[0]
    runtime_data = init_integration.runtime_data

    stream.emit(Disconnected())
    assert not runtime_data.connected
    stream.emit(Reconnected())
    assert runtime_data.connected
    stream.emit(Disconnected())
    stream.emit(Connected())
    assert runtime_data.connected


async def test_names_cleared_after_reprogram(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    client: FakeClient,
) -> None:
    """Test that looked up names are forgotten after the controller is reprogrammed."""
    names = init_integration.runtime_data.object_names
    assert await names.name(201) == "Enclosure 1"
    assert 201 in names._cache  # noqa: SLF001

    # Another change is no reprogram
    client.masters.fire(ObjectUpdated(obj=SimpleNamespace(), attrs_changed=["name"]))
    assert 201 in names._cache  # noqa: SLF001

    sleeps: list[float] = []

    async def no_sleep(delay: float) -> None:
        sleeps.append(delay)

    from unittest.mock import patch  # noqa: PLC0415

    with patch("custom_components.vantage_diagnostics.asyncio.sleep", no_sleep):
        client.masters.fire(
            ObjectUpdated(obj=SimpleNamespace(), attrs_changed=["m_time"])
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    assert sleeps
    assert 201 not in names._cache  # noqa: SLF001


@pytest.mark.parametrize(
    ("service", "data"),
    [
        (SERVICE_GET_TRIGGER_HISTORY, {}),
        (SERVICE_GET_SYSTEM_LOG, {}),
        (SERVICE_GET_MODULE_STATUS, {}),
    ],
)
async def test_services_without_any_entry(
    hass: HomeAssistant, service: str, data: dict[str, int]
) -> None:
    """Test that no service crashes when no vantage entry is loaded."""
    assert await async_setup_component(hass, DOMAIN, {})

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, service, data, blocking=True, return_response=True
        )

    assert err.value.translation_key == "not_loaded"
    assert err.value.translation_domain == DOMAIN


async def test_service_while_stock_entry_unloaded(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    vantage_entry: MockConfigEntry,
) -> None:
    """Test that a service refuses once the stock entry no longer holds the client."""
    # Between the stock unload and our reload
    vantage_entry.mock_state(hass, ConfigEntryState.NOT_LOADED)

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, SERVICE_GET_SYSTEM_LOG, blocking=True, return_response=True
        )
    assert err.value.translation_key == "not_loaded"
