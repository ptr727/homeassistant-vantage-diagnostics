"""Forward Vantage triggers, the object that started each task, to the event bus."""

from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .aiovantage_diag import TriggerReceived, TriggerType, subscribe_triggers
from .config_entry import VantageDiagnosticsConfigEntry
from .const import EVENT_TRIGGER
from .helpers import enum_name
from .names import ObjectNames
from .ordered import OrderedWorker


async def describe_trigger(
    names: ObjectNames,
    source: int,
    destination: int,
    event: int | None,
    param1: int | None,
    param2: int | None,
) -> dict[str, Any]:
    """Build the event payload, and service response entry, for a trigger."""
    source_path = await names.path(source)
    return {
        "trigger_type": enum_name(TriggerType, event),
        "event": event,
        "source_id": source,
        "source_name": source_path[0] if source_path else f"VID {source}",
        "source_path": source_path,
        "task_id": destination,
        "task_name": await names.name(destination),
        "param1": param1,
        "param2": param2,
    }


@callback
def source_entity_id(
    hass: HomeAssistant, vantage_entry_id: str, vid: int
) -> str | None:
    """Return the stock entity for an object, whose unique ID is the object's VID, if it has one."""
    # A button, the usual source, has no entity, and an entity suffixed after the VID is a part of the object
    unique_id = str(vid)
    return next(
        (
            entity.entity_id
            for entity in er.async_entries_for_config_entry(
                er.async_get(hass), vantage_entry_id
            )
            if entity.unique_id == unique_id
        ),
        None,
    )


@callback
def async_setup_triggers(
    hass: HomeAssistant, entry: VantageDiagnosticsConfigEntry
) -> None:
    """Fire an event each time an object triggers a task, given the enhanced log."""
    vantage = entry.runtime_data.client
    names = entry.runtime_data.object_names
    stream = entry.runtime_data.stream

    async def fire(trigger: TriggerReceived) -> None:
        payload = await describe_trigger(
            names,
            trigger.source,
            trigger.destination,
            trigger.event,
            trigger.param1,
            trigger.param2,
        )
        # The entity ID is what files the event under that entity's own logbook too
        if entity_id := source_entity_id(
            hass, entry.runtime_data.vantage_entry_id, trigger.source
        ):
            payload["entity_id"] = entity_id
        hass.bus.async_fire(EVENT_TRIGGER, {"host": vantage.host} | payload)

    # Fire in the order the controller reported, though some names need a lookup
    worker = OrderedWorker(hass, entry, fire, "vantage_triggers")
    entry.async_on_unload(subscribe_triggers(stream, worker.put, peers=vantage.peers))
