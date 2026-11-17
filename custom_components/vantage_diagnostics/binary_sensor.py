"""Connectivity binary sensors for the modules and for this integration's event stream."""

from typing import Any, override

from aiovantage.objects import SystemObject

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .config_entry import VantageDiagnosticsConfigEntry
from .entity import (
    VantageDiagnosticsEntity,
    VantageModuleEntity,
    async_add_module_entities,
    connected_controller,
)

# The coordinator reads the bus, one request at a time
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VantageDiagnosticsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add a connectivity binary sensor for each module, and one for the event stream."""
    if (controller := connected_controller(hass, entry)) is not None:
        async_add_entities([VantageEventStreamEntity(entry, *controller)])

    def build(
        obj: SystemObject, device: dr.DeviceEntry
    ) -> list[VantageModuleConnectivityEntity]:
        return [VantageModuleConnectivityEntity(entry, obj, device)]

    async_add_module_entities(hass, entry, async_add_entities, build)


# The base classes define available differently, which pyright cannot reconcile
class VantageModuleConnectivityEntity(VantageModuleEntity, BinarySensorEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    """Binary sensor reporting whether a module responds on its enclosure bus.

    A module drops off the bus when it loses power, eg. when its breaker trips.
    """

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(
        self,
        entry: VantageDiagnosticsConfigEntry,
        obj: SystemObject,
        device: dr.DeviceEntry,
    ) -> None:
        """Initialize a module connectivity entity."""
        super().__init__(entry, obj, "connectivity", device)

    @property
    @override
    def is_on(self) -> bool | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        # None when the module's enclosure couldn't be read
        return self.coordinator.data.is_online(self.obj)

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        if (info := self._last_info) is None:
            return None

        # The error count rises each time the module loses and regains the bus
        return {"address": info.address, "errors": info.errors}


class VantageEventStreamEntity(VantageDiagnosticsEntity, BinarySensorEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    """Binary sensor reporting whether this integration's own event stream is connected.

    The stream is a Host Command session apart from the stock integration's.
    It carries the trigger and system log events, and the module entities go unavailable while it is down.
    """

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(
        self, entry: VantageDiagnosticsConfigEntry, vid: int, device: dr.DeviceEntry
    ) -> None:
        """Initialize the event stream entity on the controller it connects to."""
        self._attach(entry, vid, "event_stream", device)

    @property
    @override
    def is_on(self) -> bool:  # pyright: ignore[reportIncompatibleVariableOverride]
        return self._entry.runtime_data.connected
