"""Describe live fingerprint accesses on the integration device's Activity."""

from typing import Any

from homeassistant.core import callback
from homeassistant.util import dt as dt_util

from .const import DOMAIN


@callback
def async_describe_events(hass: Any, async_describe_event: Any) -> None:
    @callback
    def describe(event: Any) -> dict[str, Any]:
        data = event.data
        time = dt_util.as_local(dt_util.utc_from_timestamp(data["timestamp"]))
        name = data.get("user_name") or f"ID {data['user_id']}"
        message = (
            f"{name} hat das Schloss per Fingerabdruck entriegelt · {time:%d.%m.%Y %H:%M:%S}"
            if hass.config.language == "de"
            else f"{name} unlocked with a fingerprint · {time:%d.%m.%Y %H:%M:%S}"
        )
        return {
            "name": data["lock_name"],
            "message": message,
            "entity_id": data.get("entity_id"),
            "icon": "mdi:fingerprint",
        }

    async_describe_event(DOMAIN, "switchbot_lock_logs_access", describe)
