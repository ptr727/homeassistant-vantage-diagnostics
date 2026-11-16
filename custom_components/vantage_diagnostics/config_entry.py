"""Vantage Diagnostics config entry."""

from dataclasses import dataclass
from typing import Any

from aiovantage import Vantage
from aiovantage.command_client import EventStream

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .module_status import ModuleStatusCoordinator
from .names import ObjectNames
from .rpc import SharedRpc

type VantageDiagnosticsConfigEntry = ConfigEntry[VantageDiagnosticsData]


def stock_entry_loaded(
    entry: ConfigEntry[Any] | None, client: Vantage | None = None
) -> bool:
    """Return whether the stock entry is loaded, and holds the given client if one is given."""
    if entry is None or entry.state is not ConfigEntryState.LOADED:
        return False

    held = _held_client(entry)
    return held is not None and (client is None or held is client)


def stock_client_released(entry: ConfigEntry[Any] | None, client: Vantage) -> bool:
    """Return whether the stock integration has stopped using the given client."""
    if entry is None or _held_client(entry) is not client:
        return True

    # A stock entry that failed to unload keeps its client and goes on using it
    return entry.state not in (
        ConfigEntryState.LOADED,
        ConfigEntryState.FAILED_UNLOAD,
    )


def stock_client_usable(
    hass: HomeAssistant, vantage_entry_id: str, client: Vantage
) -> bool:
    """Return whether the stock entry is loaded and still holds the given client."""
    return stock_entry_loaded(
        hass.config_entries.async_get_entry(vantage_entry_id), client
    )


def close_released_connection(
    hass: HomeAssistant, vantage_entry_id: str, client: Vantage
) -> None:
    """Close the client's config connection once the stock integration no longer uses the client."""
    if stock_client_released(
        hass.config_entries.async_get_entry(vantage_entry_id), client
    ):
        client.config_client.close()


def _held_client(entry: ConfigEntry[Any]) -> Any:
    return getattr(getattr(entry, "runtime_data", None), "client", None)


@dataclass
class VantageDiagnosticsData:
    """Runtime data for a Vantage Diagnostics config entry."""

    client: Vantage
    """The stock integration's client, captured at setup."""

    stream: EventStream
    """The event stream this integration owns, apart from the stock client's."""

    entry_id: str
    """The ID of this config entry."""

    vantage_entry_id: str
    """The ID of the stock config entry this one follows."""

    object_names: ObjectNames
    rpc: SharedRpc
    module_status: ModuleStatusCoordinator
    connected: bool = True

    @property
    def signal_connection_changed(self) -> str:
        """Dispatcher signal sent when the event stream is lost or restored."""
        return f"{DOMAIN}_connection_changed_{self.entry_id}"

    def stock_loaded(self, hass: HomeAssistant) -> bool:
        """Return whether the stock entry is loaded and still holds our client."""
        return stock_client_usable(hass, self.vantage_entry_id, self.client)
