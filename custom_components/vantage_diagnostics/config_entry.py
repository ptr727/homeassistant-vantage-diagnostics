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

    held = getattr(getattr(entry, "runtime_data", None), "client", None)
    return held is not None and (client is None or held is client)


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
        return stock_entry_loaded(
            hass.config_entries.async_get_entry(self.vantage_entry_id), self.client
        )
