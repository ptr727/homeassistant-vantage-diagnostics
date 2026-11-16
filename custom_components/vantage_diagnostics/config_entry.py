"""Vantage Diagnostics config entry."""

from dataclasses import dataclass

from aiovantage import Vantage
from aiovantage.command_client import EventStream

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .module_status import ModuleStatusCoordinator
from .names import ObjectNames

type VantageDiagnosticsConfigEntry = ConfigEntry[VantageDiagnosticsData]


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
    module_status: ModuleStatusCoordinator
    connected: bool = True

    @property
    def signal_connection_changed(self) -> str:
        """Dispatcher signal sent when the event stream is lost or restored."""
        return f"{DOMAIN}_connection_changed_{self.entry_id}"

    def stock_loaded(self, hass: HomeAssistant) -> bool:
        """Return whether the stock entry is loaded and still holds our client."""
        entry = hass.config_entries.async_get_entry(self.vantage_entry_id)
        if entry is None or entry.state is not ConfigEntryState.LOADED:
            return False

        return getattr(getattr(entry, "runtime_data", None), "client", None) is (
            self.client
        )
