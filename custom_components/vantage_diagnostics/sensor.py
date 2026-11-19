"""Diagnostic sensors for the modules and the controller, which the stock integration doesn't show."""

from collections.abc import Callable
from dataclasses import dataclass
import datetime as dt
from typing import Any, override

from aiovantage.objects import SystemObject

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .aiovantage_diag import ModuleInfo
from .config_entry import VantageDiagnosticsConfigEntry
from .entity import (
    VantageDiagnosticsEntity,
    VantageModuleEntity,
    async_add_module_entities,
    connected_controller,
)

# The coordinator reads the bus, one request at a time
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class VantageModuleSensorEntityDescription(SensorEntityDescription):
    """A module sensor, and the field of the module's bus status it reports."""

    value_fn: Callable[[ModuleInfo], str | int | None]


MODULE_SENSORS: tuple[VantageModuleSensorEntityDescription, ...] = (
    # The stock device's model is the Design Center object type, not the hardware
    VantageModuleSensorEntityDescription(
        key="hardware_model",
        value_fn=lambda info: info.model,
    ),
    # Shows when a module was swapped for a newer revision of the same hardware
    VantageModuleSensorEntityDescription(
        key="hardware_revision",
        value_fn=lambda info: info.revision,
    ),
    VantageModuleSensorEntityDescription(
        key="firmware_version",
        value_fn=lambda info: info.version,
    ),
    # The controller only raises the count, each time the module loses and regains the bus
    VantageModuleSensorEntityDescription(
        key="bus_errors",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda info: info.errors,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VantageDiagnosticsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the module sensors, and the host and address of the controller this integration connects to."""
    if (controller := connected_controller(hass, entry)) is not None:
        async_add_entities(
            [
                VantageControllerHostEntity(entry, *controller),
                VantageControllerIpAddressEntity(entry, *controller),
            ]
        )

    def build(obj: SystemObject, device: dr.DeviceEntry) -> list[Entity]:
        return [
            *(
                VantageModuleSensorEntity(entry, obj, device, description)
                for description in MODULE_SENSORS
            ),
            VantageModuleLastFaultEntity(entry, obj, device),
        ]

    async_add_module_entities(hass, entry, async_add_entities, build)


# The base classes define available differently, which pyright cannot reconcile
class VantageModuleSensorEntity(VantageModuleEntity, SensorEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    """Sensor reporting one field of a module's status on its enclosure bus.

    It keeps the last value read while the module is off the bus.
    """

    def __init__(
        self,
        entry: VantageDiagnosticsConfigEntry,
        obj: SystemObject,
        device: dr.DeviceEntry,
        description: VantageModuleSensorEntityDescription,
    ) -> None:
        """Initialize a module sensor."""
        super().__init__(entry, obj, description.key, device)
        self.entity_description = description
        self._value_fn = description.value_fn

    @property
    @override
    def native_value(self) -> str | int | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        # Unknown until the module is first read, or when the controller leaves the field out
        if (info := self._last_info) is None:
            return None

        return self._value_fn(info)


class VantageModuleLastFaultEntity(VantageDiagnosticsEntity, SensorEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    """Sensor reporting when a module last faulted, with a short summary of the fault.

    The full report is in the fault event and the get_fault_report service, since it is too big for attributes.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self,
        entry: VantageDiagnosticsConfigEntry,
        obj: SystemObject,
        device: dr.DeviceEntry,
    ) -> None:
        """Initialize a module's last fault sensor."""
        self._attach(entry, obj.vid, "last_fault", device)
        self._vid = obj.vid

    @property
    @override
    def native_value(self) -> dt.datetime | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        # Unknown until the module first faults
        if (report := self._entry.runtime_data.fault_tracker.report(self._vid)) is None:
            return None

        return dt_util.parse_datetime(report["time"])

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        if (report := self._entry.runtime_data.fault_tracker.report(self._vid)) is None:
            return None

        return {
            "signal": report["signal"],
            "line_feed": report["line_feed"],
            "line_feed_name": report["line_feed_name"],
            "recovered": report["recovered"],
            "outage": report["outage"],
            "side_effects": len(report["side_effects"]),
        }

    @override
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                self._entry.runtime_data.fault_tracker.signal_updated(self._vid),
                self.async_write_ha_state,
            )
        )


class VantageControllerHostEntity(VantageDiagnosticsEntity, SensorEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    """Sensor reporting the host of the controller as configured, a name or an address."""

    def __init__(
        self, entry: VantageDiagnosticsConfigEntry, vid: int, device: dr.DeviceEntry
    ) -> None:
        """Initialize the host sensor on the controller this integration connects to."""
        self._attach(entry, vid, "host", device)
        self._attr_native_value = entry.runtime_data.client.host


class VantageControllerIpAddressEntity(VantageDiagnosticsEntity, SensorEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    """Sensor reporting the address this integration's event stream connects to.

    It replaces the IP address sensor the stock integration removed.
    It keeps the last address while the stream is down.
    """

    def __init__(
        self, entry: VantageDiagnosticsConfigEntry, vid: int, device: dr.DeviceEntry
    ) -> None:
        """Initialize the IP address sensor on the controller this integration connects to."""
        self._attach(entry, vid, "ip_address", device)

    @property
    @override
    def native_value(self) -> str | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        return self._entry.runtime_data.ip_address
