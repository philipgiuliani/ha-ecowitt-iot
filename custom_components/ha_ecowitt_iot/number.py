"""Number platform: per-WFC01 watering run duration."""

from __future__ import annotations

import logging

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DEFAULT_RUN_SECONDS,
    DOMAIN,
    MAX_RUN_SECONDS,
    MIN_RUN_SECONDS,
    WFC01_MODEL,
)
from .coordinator import EcowittDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


def _iot_water_devices(coordinator: EcowittDataUpdateCoordinator):
    """Yield (nickname, id, model) for each online WFC01 water timer."""
    iot_data = coordinator.data.get("iot_list") if coordinator.data else None
    if not iot_data:
        return
    for item in iot_data.get("command", []):
        if item.get("rfnet_state") == 0:
            continue
        if item.get("model") != WFC01_MODEL:
            continue
        nickname = item.get("nickname")
        if nickname is None:
            continue
        yield nickname, item.get("id"), item.get("model")


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up run-duration number entities for WFC01 devices."""
    coordinator: EcowittDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    registered: set[str] = set()

    def _build_new() -> list[NumberEntity]:
        new_entities: list[NumberEntity] = []
        for nickname, iot_id, iot_model in _iot_water_devices(coordinator):
            if nickname in registered:
                continue
            registered.add(nickname)
            new_entities.append(
                EcowittRunDurationNumber(
                    coordinator, nickname, iot_id, iot_model, entry.unique_id
                )
            )
        return new_entities

    async_add_entities(_build_new())

    def _process_new() -> None:
        entities = _build_new()
        if entities:
            async_add_entities(entities)

    coordinator.async_add_listener(_process_new)


class EcowittRunDurationNumber(CoordinatorEntity, RestoreEntity, NumberEntity):
    """Configurable watering duration used by the Quick Run button."""

    _attr_has_entity_name = True
    _attr_translation_key = "run_duration"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = NumberDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_native_min_value = MIN_RUN_SECONDS
    _attr_native_max_value = 7200
    _attr_native_step = 5
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:timer-cog-outline"

    def __init__(
        self,
        coordinator: EcowittDataUpdateCoordinator,
        device_id: str,
        iot_id: int,
        iot_model: int,
        unique_id: str,
    ) -> None:
        super().__init__(coordinator)
        self.device_id = device_id
        self._iot_id = iot_id
        self._iot_model = iot_model
        self._attr_unique_id = f"{device_id}_run_duration"
        self._attr_native_value = DEFAULT_RUN_SECONDS
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{device_id}")},
            name=f"{device_id}",
            manufacturer="Ecowitt",
            model=coordinator.data.get("ver"),
            configuration_url=f"http://{coordinator.config_entry.data[CONF_HOST]}",
            via_device=(DOMAIN, unique_id),
        )

    async def async_added_to_hass(self) -> None:
        """Restore the last set value and publish it to the coordinator."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in (
            None,
            "unknown",
            "unavailable",
        ):
            try:
                self._attr_native_value = max(
                    MIN_RUN_SECONDS,
                    min(MAX_RUN_SECONDS, int(float(last_state.state))),
                )
            except (TypeError, ValueError):
                pass
        self.coordinator.iot_run_duration[self._iot_id] = int(self._attr_native_value)

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.coordinator.iot_run_duration[self._iot_id] = int(value)
        self.async_write_ha_state()
