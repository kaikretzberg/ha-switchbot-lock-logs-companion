"""Companion integration for the official SwitchBot Bluetooth integration."""

from typing import Any

import voluptuous as vol
from homeassistant.const import Platform
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
)

from .const import DEFAULT_INTERVAL, DEFAULT_MAX_ENTRIES, DOMAIN
from .coordinator import LogsCoordinator
from .lock_logs.client import resolve_target
from .storage import CompanionStore

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.SENSOR]
SERVICES = ("get_lock_logs", "set_lock_user_name", "delete_lock_user_name")


async def async_setup(hass: Any, config: Any) -> bool:
    store = CompanionStore(hass)
    await store.load()
    setup_history = hass.data.get(DOMAIN, {}).get("setup_history", {})
    hass.data[DOMAIN] = {
        "store": store,
        "coordinators": {},
        "setup_history": setup_history,
    }
    register_services(hass)
    return True


def register_services(hass: Any) -> None:
    """Install validated service handlers independently of individual entries."""

    def coordinator(call: Any) -> LogsCoordinator:
        coordinators = hass.data[DOMAIN]["coordinators"]
        for value in coordinators.values():
            if value.target.device_id == call.data["device_id"]:
                return value
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="device_not_configured"
        )

    async def get_logs(call: Any) -> dict[str, Any]:
        manager = coordinator(call)
        try:
            logs = await manager.fetch_manual(
                call.data["base_time"], call.data["max_entries"]
            )
        except Exception as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="fetch_logs_error"
            ) from err
        return {"device_id": manager.target.device_id, "logs": logs, "count": len(logs)}

    async def stored_logs(call: Any) -> dict[str, Any]:
        manager = coordinator(call)
        users = manager.store.users(manager.target.device_id)
        logs = [
            record.as_dict(users)
            for record in manager.store.history(manager.target.device_id)
        ]
        return {"device_id": manager.target.device_id, "logs": logs, "count": len(logs)}

    async def set_user(call: Any) -> None:
        manager = coordinator(call)
        await manager.store.set_user(
            manager.target.device_id, call.data["user_id"], call.data["name"]
        )
        manager.async_update_listeners()

    async def delete_user(call: Any) -> None:
        manager = coordinator(call)
        await manager.store.set_user(
            manager.target.device_id, call.data["user_id"], None
        )
        manager.async_update_listeners()

    device: dict[Any, Any] = {vol.Required("device_id"): cv.string}
    hass.services.async_register(
        DOMAIN,
        "get_stored_lock_logs",
        stored_logs,
        schema=vol.Schema(device),
        supports_response=SupportsResponse.ONLY,
    )
    user: dict[Any, Any] = {
        **device,
        vol.Required("user_id"): vol.All(cv.positive_int, vol.Range(max=255)),
    }
    hass.services.async_register(
        DOMAIN,
        SERVICES[0],
        get_logs,
        schema=vol.Schema(
            {
                **device,
                vol.Optional("max_entries", default=DEFAULT_MAX_ENTRIES): vol.All(
                    cv.positive_int, vol.Range(min=1, max=100)
                ),
                vol.Optional("base_time", default=0): vol.All(
                    cv.positive_int, vol.Range(max=0xFFFFFFFF)
                ),
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICES[1],
        set_user,
        schema=vol.Schema(
            {
                **user,
                vol.Required("name"): vol.All(cv.string, vol.Length(min=1, max=100)),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN, SERVICES[2], delete_user, schema=vol.Schema(user)
    )


async def async_setup_entry(hass: Any, entry: Any) -> bool:
    """Create entities even while the parent is unavailable, then poll quietly."""
    target = resolve_target(hass, entry.data["device_id"])
    if target is None:
        from homeassistant.exceptions import ConfigEntryNotReady

        raise ConfigEntryNotReady("Official SwitchBot lock registry entry is missing")
    store = hass.data[DOMAIN]["store"]
    await store.migrate_device(target.device_id, target.address)
    manager = entry.runtime_data = LogsCoordinator(
        hass,
        entry,
        target,
        store,
        entry.options.get("poll_interval", DEFAULT_INTERVAL),
    )
    # Ordinary refresh, not first_refresh: an offline lock must not prevent setup.
    initial = hass.data[DOMAIN].get("setup_history", {}).pop(target.device_id, None)
    if initial is not None:
        manager.async_set_updated_data(
            await store.append_history(target.device_id, initial)
        )
    else:
        await manager.async_refresh()
    hass.data[DOMAIN]["coordinators"][entry.entry_id] = manager
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_options_updated))
    entry.async_on_unload(
        async_track_state_change_event(
            hass,
            [target.entity_id],
            manager.handle_lock_state,
        )
    )
    entry.async_on_unload(
        async_track_time_change(hass, manager.nightly_sync, hour=3, minute=0, second=0)
    )
    return True


async def async_options_updated(hass: Any, entry: Any) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: Any, entry: Any) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    hass.data[DOMAIN]["coordinators"].pop(entry.entry_id, None)
    await entry.runtime_data.async_shutdown()
    return True


async def async_migrate_entry(hass: Any, entry: Any) -> bool:
    """Keep old registry IDs and remove obsolete copied address/credential data."""
    if entry.version != 1 or entry.minor_version > 2:
        return False
    target = resolve_target(hass, entry.data["device_id"])
    device_id = target.device_id if target else entry.data["device_id"]
    # Only change this companion's entity IDs, never the official entries.
    if old_address := entry.data.get("mac_address"):
        from homeassistant.helpers import entity_registry as er

        registry = er.async_get(hass)
        for key in ("last_activity", "last_user"):
            old_id = registry.async_get_entity_id(
                "sensor", DOMAIN, f"{old_address}-{key}"
            )
            if (
                old_id is not None
                and registry.async_get_entity_id("sensor", DOMAIN, f"{device_id}-{key}")
                is None
            ):
                registry.async_update_entity(old_id, new_unique_id=f"{device_id}-{key}")
    hass.config_entries.async_update_entry(
        entry,
        data={"device_id": device_id},
        unique_id=device_id,
        minor_version=2,
    )
    return True
