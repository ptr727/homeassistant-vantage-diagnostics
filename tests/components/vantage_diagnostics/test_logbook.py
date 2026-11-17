"""Tests for the logbook entries, read back through the logbook itself."""

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, mock_component
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.vantage_diagnostics.const import EVENT_TRIGGER, VANTAGE_DOMAIN
from homeassistant.components.recorder.core import Recorder
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from .conftest import BUTTON_VID, TASK_VID, FakeEventStream


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    recorder_mock: Recorder, enable_custom_integrations: None
) -> None:
    """Start the recorder before Home Assistant, which the logbook reads from."""


async def logbook_entries(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, **filters: Any
) -> list[dict[str, Any]]:
    """Return the logbook's trigger entries, as the frontend reads them."""
    await async_wait_recording_done(hass)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "logbook/get_events",
            "start_time": dt_util.start_of_local_day().isoformat(),
        }
        | filters
    )
    response = await client.receive_json()
    assert response["success"]
    return [
        entry
        for entry in response["result"]
        if entry.get("domain") == VANTAGE_DOMAIN and "message" in entry
    ]


async def test_trigger_in_source_entity_logbook(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    vantage_entry: MockConfigEntry,
    config_entry: MockConfigEntry,
    fake_stream: type[FakeEventStream],
) -> None:
    """Test that a trigger shows in the logbook, and in its source entity's own logbook."""
    source_entity = er.async_get(hass).async_get_or_create(
        "binary_sensor", VANTAGE_DOMAIN, str(BUTTON_VID), config_entry=vantage_entry
    )
    # The frontend's files aren't installed for tests, and the logbook only needs it present
    mock_component(hass, "frontend")
    assert await async_setup_component(hass, "logbook", {})
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    fake_stream.instances[0].emit_log(f"{BUTTON_VID} {TASK_VID} 1 0 0")
    await hass.async_block_till_done(wait_background_tasks=True)
    hass.bus.async_fire(EVENT_TRIGGER, {"source_name": "Other", "task_name": "Lamp"})
    await hass.async_block_till_done()

    everything = await logbook_entries(hass, hass_ws_client)
    assert [entry["message"] for entry in everything] == [
        "Hall Button on Hall Keypad started task Lamp (button down)",
        "Other started task Lamp",
    ]
    assert everything[0]["entity_id"] == source_entity.entity_id

    entity_only = await logbook_entries(
        hass, hass_ws_client, entity_ids=[source_entity.entity_id]
    )
    assert [entry["message"] for entry in entity_only] == [
        "Hall Button on Hall Keypad started task Lamp (button down)"
    ]
