"""Config flow for the Vantage Diagnostics integration."""

from typing import Any

import probatio as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
)

from .const import CONF_VANTAGE_ENTRY_ID, DOMAIN, VANTAGE_DOMAIN


class VantageDiagnosticsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Follow one loaded stock Vantage entry, which holds the controller connection."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the stock entry to follow, asking only when more than one is free."""
        loaded: list[ConfigEntry[Any]] = list(
            self.hass.config_entries.async_loaded_entries(VANTAGE_DOMAIN)
        )
        if not loaded:
            return self.async_abort(reason="no_vantage_entry")

        # One companion entry per controller
        configured = self._async_current_ids()
        free = [entry for entry in loaded if entry.entry_id not in configured]
        if not free:
            return self.async_abort(reason="already_configured")

        if user_input is None and len(free) > 1:
            return self.async_show_form(
                step_id="user",
                data_schema=_schema(free),
            )

        vantage_entry_id = (
            user_input[CONF_VANTAGE_ENTRY_ID] if user_input else free[0].entry_id
        )
        chosen = next(
            (entry for entry in free if entry.entry_id == vantage_entry_id), None
        )
        if chosen is None:
            return self.async_abort(reason="no_vantage_entry")

        await self.async_set_unique_id(chosen.entry_id)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"{chosen.title} Diagnostics",
            data={CONF_VANTAGE_ENTRY_ID: chosen.entry_id},
        )


def _schema(entries: list[ConfigEntry[Any]]) -> vol.Schema:
    """Return the schema offering each free stock entry by title."""
    return vol.Schema(
        {
            vol.Required(CONF_VANTAGE_ENTRY_ID): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=entry.entry_id, label=entry.title)
                        for entry in entries
                    ]
                )
            )
        }
    )
