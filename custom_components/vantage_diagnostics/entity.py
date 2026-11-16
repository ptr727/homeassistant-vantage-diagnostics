"""Base entities, attached to the stock integration's module and controller devices."""

from collections.abc import Callable, Sequence
from typing import override

from aiovantage.events import ObjectAdded
from aiovantage.objects import SystemObject

from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .aiovantage_diag import ModuleInfo
from .config_entry import VantageDiagnosticsConfigEntry
from .const import LOGGER, VANTAGE_DOMAIN
from .module_status import ModuleStatusCoordinator


def stock_device(
    hass: HomeAssistant, entry: VantageDiagnosticsConfigEntry, vid: int
) -> dr.DeviceEntry | None:
    """Return the stock integration's device for an object, which it owns."""
    return dr.async_get(hass).async_get_device_by_identifier(
        (VANTAGE_DOMAIN, str(vid)), entry.runtime_data.vantage_entry_id
    )


def async_add_module_entities(
    hass: HomeAssistant,
    entry: VantageDiagnosticsConfigEntry,
    async_add_entities: AddEntitiesCallback,
    build: Callable[[SystemObject, dr.DeviceEntry], Sequence[Entity]],
) -> None:
    """Add the entities built for each module, including modules the stock integration learns of later."""
    modules = entry.runtime_data.client.modules

    def add_entities(objects: list[SystemObject]) -> None:
        entities: list[Entity] = []
        for obj in objects:
            # The device belongs to the stock entry, so look it up rather than create it
            device = stock_device(hass, entry, obj.vid)
            if device is None:
                LOGGER.debug("No stock device for module %d, skipping it", obj.vid)
                continue
            entities.extend(build(obj, device))
        async_add_entities(entities)

    add_entities(list(modules))

    # Such as after a reprogram
    def on_object_added(event: ObjectAdded[SystemObject]) -> None:
        add_entities([event.obj])

    entry.async_on_unload(modules.subscribe(ObjectAdded, on_object_added))


def connected_controller(
    hass: HomeAssistant, entry: VantageDiagnosticsConfigEntry
) -> tuple[int, dr.DeviceEntry] | None:
    """Return the VID and stock device of the controller this integration connects to."""
    client = entry.runtime_data.client
    for master in client.masters:
        if master.number != client.master_number:
            continue
        if (device := stock_device(hass, entry, master.vid)) is None:
            break
        return master.vid, device

    LOGGER.debug("No stock device for master %s, skipping it", client.master_number)
    return None


class VantageDiagnosticsEntity(Entity):
    """An entity of this integration, attached to a device the stock integration owns."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_should_poll = False

    def _attach(
        self,
        entry: VantageDiagnosticsConfigEntry,
        vid: int,
        key: str,
        device: dr.DeviceEntry,
    ) -> None:
        """Name the entity for the object with this VID, and link it to the object's stock device."""
        self._entry = entry
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.runtime_data.vantage_entry_id}:{vid}:{key}"

        # Home Assistant links the entity to this device, which another integration owns
        self.device_entry = device

    @property
    @override
    def available(self) -> bool:  # pyright: ignore[reportIncompatibleVariableOverride]
        # The stock integration must still hold the client this integration reads through
        return self._entry.runtime_data.stock_loaded(self.hass)

    @override
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                self._entry.runtime_data.signal_connection_changed,
                self.async_write_ha_state,
            )
        )


class VantageModuleEntity(
    CoordinatorEntity[ModuleStatusCoordinator], VantageDiagnosticsEntity
):
    """An entity reporting what the controller reads of a module on its enclosure bus.

    Disabled by default, since the bus is only read while one of these listens.
    """

    _attr_entity_registry_enabled_default = False

    # Kept while the module is off the bus, when the controller no longer lists it
    _last_info: ModuleInfo | None = None

    def __init__(
        self,
        entry: VantageDiagnosticsConfigEntry,
        obj: SystemObject,
        key: str,
        device: dr.DeviceEntry,
    ) -> None:
        """Initialize a module entity."""
        super().__init__(entry.runtime_data.module_status)
        self._attach(entry, obj.vid, key, device)
        self.obj = obj

    @property
    @override
    def available(self) -> bool:  # pyright: ignore[reportIncompatibleVariableOverride]
        # The coordinator's last read must have succeeded and our event stream must be up.
        # The stock integration must also still hold the client the reads go through.
        runtime_data = self._entry.runtime_data
        return (
            self.coordinator.last_update_success
            and runtime_data.connected
            and runtime_data.stock_loaded(self.hass)
        )

    @override
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._record_info()

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        self._record_info()
        super()._handle_coordinator_update()

    def _record_info(self) -> None:
        if (info := self.coordinator.data.modules.get(self.obj.vid)) is not None:
            self._last_info = info
