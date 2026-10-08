"""Entities linked to the parent's existing device registry identity."""

from datetime import UTC, datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .access import fingerprint_accesses
from .const import DOMAIN
from .coordinator import LogsCoordinator
from .lock_logs.client import target_available


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    device = dr.async_get(hass).async_get(entry.data["device_id"])
    assert device is not None
    async_add_entities(
        [
            LogSensor(entry.runtime_data, key, device)
            for key in (
                "last_activity",
                "last_user",
                "last_action",
                "log_count",
                "last_access",
            )
        ]
    )


class LogSensor(CoordinatorEntity[LogsCoordinator], SensorEntity):
    """Values derive from the same latest history response."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: LogsCoordinator, key: str, device: Any) -> None:
        super().__init__(coordinator)
        self.key = key
        self._attr_translation_key = key
        self._attr_unique_id = f"{device.id}-{key}"
        # HA >=2026.8 permits one config entry per device. Register a logical
        # history companion linked to the actual lock; never copy its BLE identity.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.id)},
            name=f"{coordinator.target.name} Logs",
            via_device_id=device.id,
        )
        if key == "last_activity":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
        if key == "last_action":
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = [
                "lock",
                "unlock",
                "unlatch",
                "jammed",
                "unlock_failed",
                "lock_failed",
                "unknown",
            ]
        if key == "last_access":
            self._attr_icon = "mdi:fingerprint"
            self._unrecorded_attributes = frozenset({"accesses", "history"})

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.key == "last_access":
            self.coordinator.access_entity_id = self.entity_id

    @property
    def available(self) -> bool:
        if self.key == "last_access" and fingerprint_accesses(
            self.coordinator.data or []
        ):
            return True
        return super().available and target_available(
            self.hass, self.coordinator.target
        )

    @property
    def native_value(self) -> datetime | str | int | None:
        logs = self.coordinator.data or []
        if self.key == "last_access":
            accesses = fingerprint_accesses(logs)
            if not accesses:
                return None
            return accesses[0].as_dict(
                self.coordinator.store.users(self.coordinator.target.device_id)
            )["user_name"]
        if self.key == "log_count":
            return len(logs)
        if not logs:
            return None
        latest = logs[0]
        if self.key == "last_activity":
            return datetime.fromtimestamp(latest.timestamp, UTC)
        values = latest.as_dict(self.coordinator.store.users(latest.device_id))
        if self.key == "last_user":
            if self.coordinator.access_since is not None:
                access = self.coordinator.latest_access()
                return (
                    access.as_dict(self.coordinator.store.users(latest.device_id))[
                        "user_name"
                    ]
                    if access
                    else None
                )
            return values["user_name"]
        action = values["action_name"]
        return action if action in (self._attr_options or []) else "unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.key == "last_access":
            records = fingerprint_accesses(self.coordinator.data or [])
            users = self.coordinator.store.users(self.coordinator.target.device_id)
            history = [
                {
                    "name": r.as_dict(users)["user_name"],
                    "user_id": r.user_id,
                    "timestamp": r.timestamp,
                    "time": dt_util.as_local(
                        datetime.fromtimestamp(r.timestamp, UTC)
                    ).strftime("%d.%m.%Y %H:%M:%S"),
                }
                for r in records[:100]
            ]
            return {
                "switchbot_access_history": True,
                "language": self.coordinator.hass.config.language,
                "lock_available": target_available(
                    self.coordinator.hass, self.coordinator.target
                ),
                "last_sync_success": self.coordinator.last_update_success,
                "access_count": len(records),
                "sync_pending": self.coordinator.sync_pending,
                "accesses": [r.as_dict(users) for r in records[:100]],
                "history": history,
                "last_access_local": history[0]["time"] if history else None,
                "last_access_user_id": records[0].user_id if records else None,
                "last_access_time": datetime.fromtimestamp(
                    records[0].timestamp, UTC
                ).isoformat()
                if records
                else None,
            }
        if self.key == "log_count":
            # Publish the bounded fetched window once, rather than duplicating
            # the history across every sensor in HA's state/recorder storage.
            users = self.coordinator.store.users(self.coordinator.target.device_id)
            return {
                "archived_count": len(self.coordinator.data or []),
                "displayed_count": min(100, len(self.coordinator.data or [])),
                "sync_pending": self.coordinator.sync_pending,
                "logs": [
                    log.as_dict(users) for log in (self.coordinator.data or [])[:100]
                ],
            }
        if not self.coordinator.data:
            return {}
        latest = self.coordinator.data[0]
        return latest.as_dict(self.coordinator.store.users(latest.device_id))
