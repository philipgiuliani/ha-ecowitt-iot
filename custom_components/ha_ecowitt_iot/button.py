"""Button platform: per-WFC01 timed "Quick Run" with device-side auto-stop."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_platform
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_DURATION,
    DEFAULT_RUN_SECONDS,
    DOMAIN,
    MAX_RUN_SECONDS,
    MIN_RUN_SECONDS,
    SERVICE_QUICK_RUN,
    SERVICE_QUICK_STOP,
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
    """Set up Quick Run buttons for WFC01 devices and register services."""
    coordinator: EcowittDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    registered: set[str] = set()

    def _build_new() -> list[ButtonEntity]:
        new_entities: list[ButtonEntity] = []
        for nickname, iot_id, iot_model in _iot_water_devices(coordinator):
            if nickname in registered:
                continue
            registered.add(nickname)
            new_entities.append(
                EcowittQuickRunButton(
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

    # Entity services: ha_ecowitt_iot.quick_run / .quick_stop targeting buttons.
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_QUICK_RUN,
        {
            vol.Optional(ATTR_DURATION): vol.All(
                vol.Coerce(int), vol.Range(min=MIN_RUN_SECONDS, max=MAX_RUN_SECONDS)
            )
        },
        "async_quick_run_service",
    )
    platform.async_register_entity_service(
        SERVICE_QUICK_STOP,
        {},
        "async_quick_stop_service",
    )


class EcowittQuickRunButton(CoordinatorEntity, ButtonEntity):
    """Start a timed watering run; the device stops itself when time is up."""

    _attr_has_entity_name = True
    _attr_translation_key = "quick_run"
    _attr_icon = "mdi:water"

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
        self._attr_unique_id = f"{device_id}_quick_run"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{device_id}")},
            name=f"{device_id}",
            manufacturer="Ecowitt",
            model=coordinator.data.get("ver"),
            configuration_url=f"http://{coordinator.config_entry.data[CONF_HOST]}",
            via_device=(DOMAIN, unique_id),
        )

    def _configured_duration(self) -> int:
        return int(
            self.coordinator.iot_run_duration.get(self._iot_id, DEFAULT_RUN_SECONDS)
        )

    async def async_press(self) -> None:
        """Run for the duration configured on the sibling number entity."""
        await self.coordinator.async_quick_run(
            self._iot_id, self._iot_model, on_time=self._configured_duration()
        )

    async def async_quick_run_service(self, duration: int | None = None) -> None:
        """Service handler: run for an explicit duration or the configured one."""
        on_time = int(duration) if duration is not None else self._configured_duration()
        await self.coordinator.async_quick_run(
            self._iot_id, self._iot_model, on_time=on_time
        )

    async def async_quick_stop_service(self) -> None:
        """Service handler: stop watering immediately."""
        await self.coordinator.async_quick_stop(self._iot_id, self._iot_model)
