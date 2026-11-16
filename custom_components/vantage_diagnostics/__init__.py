"""The Vantage Diagnostics integration, adding diagnostics to the stock Vantage one."""

import asyncio
from typing import Any

from aiovantage import Vantage
from aiovantage.command_client import EventStream
from aiovantage.errors import ClientError
from aiovantage.events import Connected, Disconnected, ObjectUpdated, Reconnected
from aiovantage.objects import Master

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_SSL,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.typing import ConfigType
from homeassistant.util.ssl import get_default_no_verify_context

from .config_entry import (
    VantageDiagnosticsConfigEntry,
    VantageDiagnosticsData,
    stock_entry_loaded,
)
from .const import CONF_VANTAGE_ENTRY_ID, DOMAIN, LOGGER, VANTAGE_DOMAIN
from .diagnostic_services import async_register_diagnostic_services
from .helpers import error_text
from .module_status import ModuleStatusCoordinator
from .module_status_service import async_register_module_status_service
from .names import ObjectNames
from .rpc import SharedRpc
from .system_log import async_setup_system_log
from .triggers import async_setup_triggers

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR]

# The stock integration refreshes its objects 30 s after a reprogram.
# Forget the names looked up here shortly after, once that refresh is done.
NAMES_CLEAR_DELAY = 35

# The integration is only set up from config entries, never from YAML
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the response-only diagnostic services."""
    async_register_diagnostic_services(hass)
    async_register_module_status_service(hass)

    return True


def _stock_client(
    hass: HomeAssistant, entry: VantageDiagnosticsConfigEntry
) -> tuple[ConfigEntry[Any], Vantage]:
    """Return the stock entry and the client it holds, once it is loaded."""
    vantage_entry = hass.config_entries.async_get_entry(
        entry.data[CONF_VANTAGE_ENTRY_ID]
    )

    # Gone for good, so retrying would never succeed
    if vantage_entry is None or vantage_entry.domain != VANTAGE_DOMAIN:
        raise ConfigEntryError(
            translation_domain=DOMAIN, translation_key="vantage_entry_removed"
        )

    # Exists, so it may yet load
    if not stock_entry_loaded(vantage_entry):
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="vantage_not_loaded"
        )

    return vantage_entry, vantage_entry.runtime_data.client


async def async_setup_entry(
    hass: HomeAssistant, entry: VantageDiagnosticsConfigEntry
) -> bool:
    """Set up Vantage Diagnostics from a config entry."""
    vantage_entry, client = _stock_client(hass, entry)

    # An event stream of our own, since the stock client's would raise on the trigger and system log lines these subscriptions turn on
    stream = EventStream(
        vantage_entry.data[CONF_HOST],
        vantage_entry.data.get(CONF_USERNAME),
        vantage_entry.data.get(CONF_PASSWORD),
        ssl=vantage_entry.data.get(CONF_SSL, True),
        ssl_context_factory=get_default_no_verify_context,
    )
    rpc = SharedRpc(
        lambda: entry.runtime_data.stock_loaded(hass),
    )
    names = ObjectNames(hass, entry, client, rpc)

    # Home Assistant runs the unload callbacks last in, first out, and also does so if setup fails.
    # It starts each one without waiting for the one before, then waits for them all together.
    # The requests' shutdown is registered last, so it refuses new requests before anything else stops.
    entry.async_on_unload(stream.stop)
    module_status = ModuleStatusCoordinator(hass, entry, client, names, rpc)
    entry.async_on_unload(rpc.async_shutdown)
    entry.runtime_data = VantageDiagnosticsData(
        client=client,
        stream=stream,
        entry_id=entry.entry_id,
        vantage_entry_id=vantage_entry.entry_id,
        object_names=names,
        rpc=rpc,
        module_status=module_status,
    )

    # Reload when the stock entry reloads, since its client is replaced and closed
    reload_scheduled = False

    @callback
    def on_stock_state_change() -> None:
        nonlocal reload_scheduled
        if stock_entry_loaded(vantage_entry, client):
            return

        if not reload_scheduled:
            reload_scheduled = True
            LOGGER.debug("The Vantage entry changed, reloading")
            hass.config_entries.async_schedule_reload(entry.entry_id)

    entry.async_on_unload(vantage_entry.async_on_state_change(on_stock_state_change))

    try:
        connection = await stream.start()
    except ClientError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="connection_failed",
            translation_placeholders={"error": error_text(err)},
        ) from err

    # Both are only streamed through the enhanced log
    if connection.supports_enhanced_log:
        async_setup_triggers(hass, entry)
        async_setup_system_log(hass, entry)
    else:
        LOGGER.info(
            "Controller firmware has no enhanced log, so no trigger or system log "
            "events will fire"
        )

    # Mark every entity unavailable while the event stream is down
    def on_connection_changed(event: object) -> None:
        entry.runtime_data.connected = not isinstance(event, Disconnected)
        async_dispatcher_send(hass, entry.runtime_data.signal_connection_changed)

    for event_type in (Connected, Reconnected, Disconnected):
        entry.async_on_unload(stream.subscribe(event_type, on_connection_changed))

    # Names the stock integration's objects don't cover may change when the controller is reprogrammed, so forget them once the stock integration has refreshed
    def on_master_updated(event: ObjectUpdated[Master]) -> None:
        if "m_time" not in event.attrs_changed:
            return

        async def clear_names() -> None:
            await asyncio.sleep(NAMES_CLEAR_DELAY)
            names.clear()

        entry.async_create_background_task(hass, clear_names(), "vantage_clear_names")

    entry.async_on_unload(client.masters.subscribe(ObjectUpdated, on_master_updated))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: VantageDiagnosticsConfigEntry
) -> bool:
    """Unload a config entry."""
    # The platforms first, so no sensor listens and nothing polls.
    # The unload callbacks then refuse new requests, stop the coordinator and the stream, and wait for requests already sent.
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
