"""Home Assistant Store with migration from the original user mapping store."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import asdict
from typing import Any

from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .lock_logs.models import LogEntry


class CompanionStore:
    """Persist mappings under stable device registry IDs."""

    def __init__(self, hass: Any) -> None:
        self.store: Store[dict[str, Any]] = Store(hass, 1, DOMAIN)
        self.legacy: Store[dict[str, Any]] = Store(hass, 1, "switchbot_lock_logs_users")
        self.data: dict[str, Any] = {"schema_version": 1, "devices": {}}
        self.legacy_data: dict[str, Any] = {}
        self.lock = asyncio.Lock()

    async def load(self) -> None:
        """Load data; reject unknown future schemas rather than overwriting."""
        if data := await self.store.async_load():
            if data.get("schema_version") != 1 or not isinstance(
                data.get("devices"), dict
            ):
                raise ValueError("Unsupported SwitchBot Lock Logs storage schema")
            self.data = data
        self.legacy_data = await self.legacy.async_load() or {}

    async def migrate_device(self, device_id: str, address: str) -> None:
        """Copy legacy mappings once; preserve the old Store as a backup."""
        async with self.lock:
            if device_id in self.data["devices"]:
                return

            def normalize(value: str) -> str:
                return value.replace(":", "").replace("-", "").lower()

            legacy: dict[str, Any] = (
                next(
                    (
                        value
                        for key, value in self.legacy_data.items()
                        if normalize(key) == normalize(address)
                    ),
                    {},
                )
                if address
                else {}
            )
            candidate = deepcopy(self.data)
            candidate["devices"][device_id] = {"users": dict(legacy.get("users", {}))}
            await self.store.async_save(candidate)
            self.data = candidate

    def users(self, device_id: str) -> dict[str, str]:
        """Return a copy so callers cannot mutate stored state."""
        return dict(self.data["devices"].get(device_id, {}).get("users", {}))

    async def set_user(self, device_id: str, user_id: int, name: str | None) -> None:
        """Persist before publishing changed mappings to entities."""
        await self.set_users(device_id, {user_id: name})

    async def set_users(self, device_id: str, changes: dict[int, str | None]) -> None:
        """Save a form atomically, retaining mappings it did not edit."""
        async with self.lock:
            candidate = deepcopy(self.data)
            users = candidate["devices"].setdefault(device_id, {"users": {}})["users"]
            for user_id, name in changes.items():
                if name is None:
                    users.pop(str(user_id), None)
                else:
                    users[str(user_id)] = name
            await self.store.async_save(candidate)
            self.data = candidate

    def history(self, device_id: str) -> list[LogEntry]:
        """Restore the complete archive; user names are resolved at display time."""
        return [
            LogEntry(**record)
            for record in self.data["devices"].get(device_id, {}).get("history", [])
        ]

    async def append_history(
        self, device_id: str, records: list[LogEntry]
    ) -> list[LogEntry]:
        """Atomically append unique raw records without trimming older events."""
        async with self.lock:
            previous = self.history(device_id)
            merged = {record.raw: record for record in previous}
            for record in records:
                if record.device_id != device_id:
                    raise ValueError("History record belongs to another device")
                merged.setdefault(record.raw, record)
            history = sorted(
                merged.values(),
                key=lambda record: (record.timestamp, record.index),
                reverse=True,
            )
            if history == previous:
                return history
            candidate = deepcopy(self.data)
            device = candidate["devices"].setdefault(device_id, {"users": {}})
            device["history"] = [asdict(record) for record in history]
            await self.store.async_save(candidate)
            self.data = candidate
            return history
