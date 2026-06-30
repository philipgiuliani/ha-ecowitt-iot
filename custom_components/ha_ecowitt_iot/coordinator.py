"""The Ecowitt integration coordinator."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any

from aiohttp.client_exceptions import ClientError
from wittiot import API
from wittiot.errors import WittiotError

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.translation import async_get_translations

from .const import (
    CONF_MAC,
    DOMAIN,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    IOT_CMD_ENDPOINT,
)

_LOGGER = logging.getLogger(__name__)

# Transient errors tolerated via the cache-based retry path below.
_TRANSIENT_ERRORS = (WittiotError, ClientError, asyncio.TimeoutError)

# Per-request timeout. Generous so a busy single-threaded gateway has
# room to answer; prior code had no timeout and could hang 5 min on a
# half-dead socket (aiohttp default).
REQUEST_TIMEOUT_SECONDS = 60

# Number of consecutive failed polls tolerated before raising UpdateFailed.
# At update_interval=10s this bounds stale-data exposure to ~20s.
MAX_CONSECUTIVE_FAILURES = 3

# Firmware metadata rarely changes; refresh at most once per hour.
FIRMWARE_CHECK_INTERVAL_SECONDS = 3600

# last_seen attribute update throttling to avoid recorder database bloat.
LAST_SEEN_INTERVAL_SECONDS = 900

# Device identity check return values.
_IDENTITY_OK = "ok"
_IDENTITY_MISMATCH = "mismatch"
_IDENTITY_UPGRADE_REJECT = "upgrade_reject"
_IDENTITY_UPGRADE_BIND = "upgrade_bind"


class EcowittDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Define an object to hold Ecowitt data."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize."""
        update_interval = config_entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
        super().__init__(
            hass, _LOGGER, name=DOMAIN, update_interval=timedelta(seconds=update_interval)
        )
        self.config_entry = config_entry
        self.api = API(
            self.config_entry.data[CONF_HOST], session=async_get_clientsession(hass)
        )
        self._consecutive_failures = 0
        self._last_good_data: dict[str, Any] = {}
        self._firmware_update_info: dict[str, Any] | None = None
        self._last_firmware_check: float = 0.0
        self._outage_logged = False
        self._mismatch_notified = False
        self._upgrade_bound = False
        self._last_seen_value: float = 0.0
        self._last_seen_ts: float = 0.0
        # Per-IoT-device run duration (seconds), set by the number entity and
        # read by the Quick Run button. Keyed by IoT device id.
        self.iot_run_duration: dict[int, int] = {}

    async def async_quick_run(
        self, iot_id: int, iot_model: int, *, duration: int = 0
    ) -> None:
        """Start a watering run on a WFC01 water timer.

        When ``duration`` > 0 the device waters for that many seconds and then
        stops by itself, independent of Home Assistant or internet
        connectivity. This is expressed via ``val_type``/``val``, not
        ``on_time`` (the gateway ignores ``on_time`` for quick_run):
          - val_type 0 = duration in seconds, val = seconds
          - val_type 1 = duration in minutes (unused here)
        ``duration`` == 0 falls back to ``always_on`` (run until quick_stop).
        Verified on WFC01 firmware 114: val_type 0 / val N auto-stops after N s.
        """
        if duration > 0:
            cmd = {
                "on_type": 0,
                "off_type": 0,
                "always_on": 0,
                "on_time": 0,
                "off_time": 0,
                "val_type": 0,
                "val": int(duration),
                "cmd": "quick_run",
                "id": iot_id,
                "model": iot_model,
            }
        else:
            # No duration: run until explicitly stopped (gateway's run-forever
            # quick_run payload).
            cmd = {
                "on_type": 0,
                "off_type": 0,
                "always_on": 1,
                "on_time": 0,
                "off_time": 0,
                "val_type": 1,
                "val": 0,
                "cmd": "quick_run",
                "id": iot_id,
                "model": iot_model,
            }
        await self._async_send_iot_command(cmd)
        await self.async_request_refresh()

    async def async_quick_stop(self, iot_id: int, iot_model: int) -> None:
        """Stop a running IoT water timer immediately."""
        await self._async_send_iot_command(
            {"cmd": "quick_stop", "id": iot_id, "model": iot_model}
        )
        await self.async_request_refresh()

    async def _async_send_iot_command(self, cmd: dict[str, Any]) -> None:
        """POST a single IoT command to the gateway.

        The gateway's reply to a command is not reliably valid JSON, and we do
        not use it: the resulting state is read back by the next coordinator
        refresh. So we only check the HTTP status and drain the body without
        parsing it (parsing raised orjson 'unexpected content after document').
        """
        host = self.config_entry.data[CONF_HOST]
        url = f"http://{host}/{IOT_CMD_ENDPOINT}"
        session = async_get_clientsession(self.hass)
        payload = {"command": [cmd]}
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                async with session.post(url, json=payload) as resp:
                    resp.raise_for_status()
                    body = await resp.text()
            _LOGGER.debug(
                "Ecowitt IoT command %s for device %s sent; reply: %s",
                cmd.get("cmd"),
                cmd.get("id"),
                body,
            )
        except _TRANSIENT_ERRORS as err:
            _LOGGER.warning(
                "Ecowitt IoT command %s for device %s failed: %s",
                cmd.get("cmd"),
                cmd.get("id"),
                err,
            )
            raise HomeAssistantError(
                f"Ecowitt IoT command '{cmd.get('cmd')}' failed: {err}"
            ) from err

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                res: dict[str, Any] = await self.api.request_loc_allinfo()
        except _TRANSIENT_ERRORS as error:
            return self._handle_fetch_failure(error)

        identity = self._check_device_identity(res)
        actual_mac = res.get("mac", "")

        if identity == _IDENTITY_UPGRADE_BIND:
            if not self._upgrade_bound:
                self._upgrade_bound = True
                self._auto_bind_mac(actual_mac)

        elif identity in (_IDENTITY_MISMATCH, _IDENTITY_UPGRADE_REJECT):
            if not self._mismatch_notified:
                self._mismatch_notified = True
                if identity == _IDENTITY_UPGRADE_REJECT:
                    await self._send_mismatch_notification(
                        expected_info=self.config_entry.unique_id,
                        actual_info=actual_mac,
                        is_upgrade=True,
                    )
                else:
                    expected_mac = self.config_entry.data.get(CONF_MAC, "")
                    await self._send_mismatch_notification(expected_mac, actual_mac)
            self._last_good_data = {}
            raise UpdateFailed(
                f"Device mismatch: IP {self.config_entry.data[CONF_HOST]} now points to a "
                f"different device. Please update the integration configuration."
            )

        res["firmware_update"] = await self._maybe_update_firmware_info()
        now = time.time()
        if now - self._last_seen_ts >= LAST_SEEN_INTERVAL_SECONDS:
            self._last_seen_value = now
            self._last_seen_ts = now
        res["_last_seen"] = self._last_seen_value

        if self._outage_logged:
            _LOGGER.info(
                "Ecowitt gateway %s is reachable again",
                self.config_entry.data[CONF_HOST],
            )
            self._outage_logged = False

        if self._mismatch_notified:
            self._mismatch_notified = False
            await self.hass.services.async_call(
                "persistent_notification",
                "dismiss",
                service_data={
                    "notification_id": f"ecowitt_mac_mismatch_{self.config_entry.entry_id}"
                },
                blocking=False,
            )
            _LOGGER.info(
                "Ecowitt gateway %s identity re-verified",
                self.config_entry.data[CONF_HOST],
            )
        self._consecutive_failures = 0
        self._last_good_data = res
        return res

    def _check_device_identity(self, data: dict[str, Any]) -> str:
        """检查设备身份，纯校验无副作用，返回状态码."""
        expected_mac = self.config_entry.data.get(CONF_MAC, "")
        actual_mac = data.get("mac", "")

        if not expected_mac:
            if actual_mac and self.config_entry.unique_id:
                if not self._mac_matches_unique_id(actual_mac, self.config_entry.unique_id):
                    _LOGGER.warning(
                        "Upgrade detected: device at IP %s does not match configured "
                        "integration %s (MAC %s). Refusing to auto-bind.",
                        self.config_entry.data[CONF_HOST],
                        self.config_entry.unique_id,
                        actual_mac,
                    )
                    return _IDENTITY_UPGRADE_REJECT
                _LOGGER.info(
                    "Upgrade: auto-binding MAC %s for device %s at IP %s",
                    actual_mac,
                    self.config_entry.unique_id,
                    self.config_entry.data[CONF_HOST],
                )
            return _IDENTITY_UPGRADE_BIND

        if actual_mac != expected_mac:
            _LOGGER.warning(
                "Device identity mismatch at IP %s: expected MAC %s, got %s. "
                "This IP may now point to a different device. "
                "Please update the integration configuration.",
                self.config_entry.data[CONF_HOST],
                expected_mac,
                actual_mac,
            )
            return _IDENTITY_MISMATCH

        return _IDENTITY_OK

    def _auto_bind_mac(self, mac: str) -> None:
        """Automatically bind MAC address to config (upgrade path, called once)."""
        new_data = {**self.config_entry.data, CONF_MAC: mac}
        self.hass.config_entries.async_update_entry(
            self.config_entry, data=new_data
        )

    @staticmethod
    def _mac_matches_unique_id(mac: str, unique_id: str) -> bool:
        """Check if MAC matches the device name (format: MODEL-WIFI{LAST4})."""
        mac_suffix = mac.replace(":", "").replace("-", "").upper()[-4:]
        return bool(mac_suffix) and mac_suffix in unique_id.upper()

    async def _send_mismatch_notification(
        self,
        expected_info: str,
        actual_info: str,
        is_upgrade: bool = False,
    ) -> None:
        """Send a persistent notification about device identity mismatch."""
        lang = self.hass.config.language or "en"
        host = self.config_entry.data[CONF_HOST]
        notification_id = f"ecowitt_mac_mismatch_{self.config_entry.entry_id}"

        translations = await async_get_translations(
            self.hass, lang, "component", DOMAIN, ["notifications"]
        )

        title_key = f"component.{DOMAIN}.notifications.mismatch_title"
        title = translations.get(title_key, "Ecowitt – Device Identity Mismatch")

        if is_upgrade:
            message_key = f"component.{DOMAIN}.notifications.upgrade_mismatch_message"
            message_template = translations.get(
                message_key,
                f"Upgrade detected: IP {host} does not match {expected_info}. "
                f"Current MAC: {actual_info}.",
            )
            message = message_template.replace("{host}", host).replace(
                "{expected_name}", expected_info
            ).replace("{actual_mac}", actual_info)
        else:
            message_key = f"component.{DOMAIN}.notifications.mismatch_message"
            message_template = translations.get(
                message_key,
                f"IP {host} points to a different device. "
                f"Expected MAC: {expected_info}, got: {actual_info}.",
            )
            message = message_template.replace("{host}", host).replace(
                "{expected_mac}", expected_info
            ).replace("{actual_mac}", actual_info)

        await self.hass.services.async_call(
            "persistent_notification",
            "create",
            service_data={
                "message": message,
                "title": title,
                "notification_id": notification_id,
            },
            blocking=False,
        )

    def _handle_fetch_failure(self, error: Exception) -> dict[str, Any]:
        self._consecutive_failures += 1
        if self._consecutive_failures < MAX_CONSECUTIVE_FAILURES and self._last_good_data:
            _LOGGER.debug(
                "Ecowitt fetch failed (%d/%d): %s; serving last known data",
                self._consecutive_failures,
                MAX_CONSECUTIVE_FAILURES,
                error,
            )
            return self._last_good_data

        # Tolerance exhausted: drop the cache, log once, and mark unavailable.
        self._last_good_data = {}
        if not self._outage_logged:
            _LOGGER.warning(
                "Ecowitt gateway %s unreachable for %d consecutive polls (%s); "
                "entities will be unavailable until it recovers",
                self.config_entry.data[CONF_HOST],
                self._consecutive_failures,
                error,
            )
            self._outage_logged = True
        raise UpdateFailed(
            f"Gateway unreachable for {self._consecutive_failures} consecutive polls: {error}"
        ) from error

    async def _maybe_update_firmware_info(self) -> dict[str, Any]:
        # A firmware endpoint failure must not mask a successful data fetch.
        now = time.monotonic()
        if (
            self._firmware_update_info is not None
            and now - self._last_firmware_check < FIRMWARE_CHECK_INTERVAL_SECONDS
        ):
            return self._firmware_update_info

        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                firmware_info: dict[str, Any] = await self.api.request_firmware_update_info()
        except _TRANSIENT_ERRORS as err:
            _LOGGER.debug(
                "Firmware info fetch failed; keeping previous metadata: %s", err
            )
            return self._firmware_update_info or {}

        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                check_info: dict[str, Any] = await self.api.request_firmware_update_check()
        except _TRANSIENT_ERRORS as err:
            firmware_info["check_supported"] = False
            firmware_info["install_supported"] = False
            firmware_info["error"] = str(err)
        else:
            response = check_info.get("response", {})
            if isinstance(response, dict):
                firmware_info["is_new"] = response.get(
                    "is_new", firmware_info.get("is_new", False)
                )
                firmware_info["release_summary"] = response.get(
                    "msg", firmware_info.get("release_summary")
                )
                firmware_info["check_response"] = response

        self._firmware_update_info = firmware_info
        self._last_firmware_check = now
        return firmware_info
