"""Tests for the Vantage Diagnostics config flow."""

# A finished flow result always carries these keys, which the typed dict leaves optional
# pyright: reportTypedDictNotRequiredAccess=false

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vantage_diagnostics.const import CONF_VANTAGE_ENTRY_ID, DOMAIN
from homeassistant.config_entries import ConfigEntryState, ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import FakeClient, add_vantage_entry

pytestmark = pytest.mark.usefixtures("mock_setup_entry")


async def start_flow(hass: HomeAssistant) -> ConfigFlowResult:
    """Start the user step."""
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})


async def test_no_vantage_integration(hass: HomeAssistant) -> None:
    """Test that the flow aborts when the stock integration has no entry at all."""
    result = await start_flow(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_vantage_entry"


async def test_vantage_entry_not_loaded(
    hass: HomeAssistant, client: FakeClient
) -> None:
    """Test that a stock entry that is not loaded does not count."""
    add_vantage_entry(hass, client, ConfigEntryState.SETUP_RETRY)

    result = await start_flow(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_vantage_entry"


async def test_one_vantage_entry(
    hass: HomeAssistant, vantage_entry: MockConfigEntry
) -> None:
    """Test that the only loaded stock entry is chosen with no input."""
    result = await start_flow(hass)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Vantage InFusion Diagnostics"
    assert result["data"] == {CONF_VANTAGE_ENTRY_ID: vantage_entry.entry_id}
    assert result["result"].unique_id == vantage_entry.entry_id


async def test_one_entry_per_controller(
    hass: HomeAssistant, vantage_entry: MockConfigEntry
) -> None:
    """Test that a stock entry followed already is not offered again."""
    MockConfigEntry(
        domain=DOMAIN,
        data={CONF_VANTAGE_ENTRY_ID: vantage_entry.entry_id},
        unique_id=vantage_entry.entry_id,
    ).add_to_hass(hass)

    result = await start_flow(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_several_vantage_entries(
    hass: HomeAssistant, vantage_entry: MockConfigEntry
) -> None:
    """Test that the flow asks which of several loaded stock entries to follow."""
    second = add_vantage_entry(hass, FakeClient())
    hass.config_entries.async_update_entry(second, title="Second Controller")

    result = await start_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_VANTAGE_ENTRY_ID: second.entry_id}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_VANTAGE_ENTRY_ID: second.entry_id}


async def test_choice_no_longer_loaded(
    hass: HomeAssistant, vantage_entry: MockConfigEntry
) -> None:
    """Test that a stock entry unloaded while the form is open aborts the flow."""
    second = add_vantage_entry(hass, FakeClient())
    result = await start_flow(hass)
    assert result["type"] is FlowResultType.FORM

    second.mock_state(hass, ConfigEntryState.NOT_LOADED)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_VANTAGE_ENTRY_ID: second.entry_id}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_vantage_entry"
