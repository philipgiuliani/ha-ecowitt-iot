"""Constants for the Ecowitt Official Integration."""

DOMAIN = "ha_ecowitt_iot"
CONF_VERSION = 2

CONF_MAC = "mac"
CONF_UPDATE_INTERVAL = "update_interval"
DEFAULT_UPDATE_INTERVAL = 10

# --- WFC01 timed "Quick Run" support ---------------------------------------
# The local gateway endpoint that accepts IoT sub-device commands.
IOT_CMD_ENDPOINT = "parse_quick_cmd_iot"

# IoT sub-device model ids (as reported in the iot device list).
WFC01_MODEL = 1  # water timer; the only model this timed-run feature targets

# Run duration (sent as `on_time`). When > 0 the device/gateway stops watering
# by itself after this many units, independent of Home Assistant.
# NOTE: the unit (seconds vs minutes) is assumed to be SECONDS based on the
# gateway payload; verify on real hardware with a small value (see README).
DEFAULT_RUN_SECONDS = 300
MIN_RUN_SECONDS = 1
MAX_RUN_SECONDS = 86340  # 23h59m, generous upper bound for the service call

# Entity services (namespaced under DOMAIN, e.g. ha_ecowitt_iot.quick_run).
SERVICE_QUICK_RUN = "quick_run"
SERVICE_QUICK_STOP = "quick_stop"
ATTR_DURATION = "duration"