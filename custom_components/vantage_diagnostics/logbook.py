"""Describe the Vantage trigger and system log events in the logbook."""

from collections.abc import Callable
from typing import Any

from homeassistant.components.logbook.const import (
    LOGBOOK_ENTRY_ENTITY_ID,
    LOGBOOK_ENTRY_MESSAGE,
    LOGBOOK_ENTRY_NAME,
)
from homeassistant.core import Event, HomeAssistant, callback

from .const import DOMAIN, EVENT_SYSTEM_LOG, EVENT_TRIGGER


def _text(value: Any) -> str:
    """Return a field as text, with a missing value as empty rather than "None"."""
    return "" if value is None else str(value)


def _object(name: Any, vid: Any, fallback: str) -> str:
    """Return an object's name, else its VID, else the fallback."""
    if text := _text(name):
        return text
    return f"VID {vid}" if vid is not None else fallback


def trigger_message(data: dict[str, Any]) -> str:
    """Build the logbook message for a trigger, eg. "Button 2 on Hall Keypad started task Hall Lights (button down)".

    Recorded events can predate a field, or come from the earlier fork, so every field is optional.
    """
    path = data.get("source_path")
    names = [_text(name) for name in path[:2]] if isinstance(path, list) else []
    source = _object(
        (names[0] if names else "") or data.get("source_name"),
        data.get("source_id"),
        "A Vantage object",
    )
    if len(names) > 1 and names[1]:
        source = f"{source} on {names[1]}"

    task = _object(data.get("task_name"), data.get("task_id"), "")
    message = f"{source} started task {task}" if task else f"{source} started a task"

    trigger_type = _text(data.get("trigger_type"))
    if trigger_type and trigger_type != "unknown":
        message = f"{message} ({trigger_type.replace('_', ' ')})"
    return message


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[
        [str, str, Callable[[Event[Any]], dict[str, str]]], None
    ],
) -> None:
    """Describe triggers and notable system logs, eg. "Warning: Overload (VID:120)"."""

    @callback
    def describe_trigger(event: Event[Any]) -> dict[str, str]:
        described = {
            LOGBOOK_ENTRY_NAME: "Vantage",
            LOGBOOK_ENTRY_MESSAGE: trigger_message(event.data),
        }
        if entity_id := event.data.get("entity_id"):
            described[LOGBOOK_ENTRY_ENTITY_ID] = str(entity_id)
        return described

    @callback
    def describe_system_log(event: Event[Any]) -> dict[str, str]:
        data = event.data
        log_type = str(data.get("type") or "").capitalize()
        message = str(data.get("message") or "")
        return {
            LOGBOOK_ENTRY_NAME: "Vantage",
            LOGBOOK_ENTRY_MESSAGE: f"{log_type}: {message}" if log_type else message,
        }

    async_describe_event(DOMAIN, EVENT_TRIGGER, describe_trigger)
    async_describe_event(DOMAIN, EVENT_SYSTEM_LOG, describe_system_log)
