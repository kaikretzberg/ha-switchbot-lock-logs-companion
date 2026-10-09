"""Download archived protocol records without account or encryption secrets."""

from typing import Any

from .const import DOMAIN
from .lock_logs.client import target_available


async def async_get_config_entry_diagnostics(hass: Any, entry: Any) -> dict[str, Any]:
    """Export only companion data; names are replaced by their credential IDs."""
    device_id = entry.data["device_id"]
    state = hass.data.get(DOMAIN, {})
    store = state.get("store")
    manager = state.get("coordinators", {}).get(entry.entry_id)
    records = store.history(device_id) if store else []
    delivered = store.activity_records(device_id) if store else []
    return {
        "model": manager.target.model if manager else None,
        "timezone": hass.config.time_zone,
        "schedule": {"nightly_hour": 2, "max_entries": 100, "periodic_polling": False},
        "lock_available": target_available(hass, manager.target) if manager else False,
        "last_sync_success": manager.last_update_success if manager else None,
        "pyswitchbot_version": manager.client.library_version if manager else None,
        "last_fetch_error": manager.last_fetch_error if manager else None,
        "sync_pending": manager.sync_pending if manager else False,
        "mapped_user_ids": sorted(store.users(device_id)) if store else [],
        "archived_count": len(records),
        "logs": [record.as_dict({}) for record in records],
        "activity_delivered_raws": [record.raw for record in delivered],
    }
