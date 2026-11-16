"""Module connectivity binary sensors, attached to the stock integration's devices."""

from typing import Any, override

from aiovantage.events import ObjectAdded
from aiovantage.objects import SystemObject

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .aiovantage_diag import ModuleInfo
from .config_entry import VantageDiagnosticsConfigEntry
from .const import LOGGER, VANTAGE_DOMAIN
from .module_status import ModuleStatusCoordinator

# The coordinator reads the bus, one request at a time
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VantageDiagnosticsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add a connectivity binary sensor for each module, off when it drops off the bus."""
    modules = entry.runtime_data.client.modules

    dev_reg = dr.async_get(hass)

    def add_entities(objects: list[SystemObject]) -> None:
        entities: list[VantageModuleConnectivityEntity] = []
        for obj in objects:
            # The device belongs to the stock entry, so look it up rather than create it
            device = dev_reg.async_get_device_by_identifier(
                (VANTAGE_DOMAIN, str(obj.vid)), entry.runtime_data.vantage_entry_id
            )
            if device is None:
                LOGGER.debug("No stock device for module %d, skipping it", obj.vid)
                continue
            entities.append(VantageModuleConnectivityEntity(entry, obj, device))
        async_add_entities(entities)

    add_entities(list(modules))

    # Modules the stock integration learns of later, such as after a reprogram
    def on_object_added(event: ObjectAdded[SystemObject]) -> None:
        add_entities([event.obj])

    entry.async_on_unload(modules.subscribe(ObjectAdded, on_object_added))


class VantageModuleConnectivityEntity(
    CoordinatorEntity[ModuleStatusCoordinator], BinarySensorEntity
):
    """Binary sensor reporting whether a module responds on its enclosure bus.

    A module drops off the bus when it loses power, eg. when its breaker trips.
    """

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_has_entity_name = True
    _attr_translation_key = "connectivity"

    # Kept while the module is off the bus, when the controller no longer lists it
    _last_info: ModuleInfo | None = None

    def __init__(
        self,
        entry: VantageDiagnosticsConfigEntry,
        obj: SystemObject,
        device: dr.DeviceEntry,
    ) -> None:
        """Initialize a module connectivity entity."""
        super().__init__(entry.runtime_data.module_status)
        self._entry = entry
        self.obj = obj
        self._attr_unique_id = f"{obj.vid}:connectivity"

        # Home Assistant links the entity to this device, which another integration owns
        self.device_entry = device

    @property
    @override
    def available(self) -> bool:  # pyright: ignore[reportIncompatibleVariableOverride]
        # The coordinator's last read must have succeeded and our event stream must be up.
        # The stock integration must also still hold the client the reads go through.
        runtime_data = self._entry.runtime_data
        return (
            super().available
            and runtime_data.connected
            and runtime_data.stock_loaded(self.hass)
        )

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

    @override
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._record_info()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                self._entry.runtime_data.signal_connection_changed,
                self.async_write_ha_state,
            )
        )

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        self._record_info()
        super()._handle_coordinator_update()

    def _record_info(self) -> None:
        if (info := self.coordinator.data.modules.get(self.obj.vid)) is not None:
            self._last_info = info
