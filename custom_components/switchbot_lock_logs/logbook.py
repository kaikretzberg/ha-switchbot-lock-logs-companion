"""Describe live and imported fingerprint accesses in native Activity."""

from typing import Any

from homeassistant.core import callback

from .const import DOMAIN


@callback
def async_describe_events(hass: Any, async_describe_event: Any) -> None:
    @callback
    def describe(event: Any) -> dict[str, Any]:
        data = event.data
        store = hass.data.get(DOMAIN, {}).get("store")
        users = store.users(data.get("lock_device_id", "")) if store else {}
        name = (
            users.get(str(data["user_id"])) or f"ID {data['user_id']}"
            if store and data.get("lock_device_id")
            else data.get("user_name") or f"ID {data['user_id']}"
        )
        message = (
            f"{name} hat geöffnet"
            if hass.config.language == "de"
            else f"{name} unlocked"
        )
        return {
            "name": data["lock_name"],
            "message": message,
            "entity_id": data.get("entity_id"),
            "icon": "mdi:fingerprint",
        }

    async_describe_event(DOMAIN, "switchbot_lock_logs_access", describe)

    async_describe_event(DOMAIN, "switchbot_lock_logs_imported_access", describe)
