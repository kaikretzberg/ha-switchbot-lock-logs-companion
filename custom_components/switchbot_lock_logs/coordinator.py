"""Conservative polling and serialized manual history retrieval."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .lock_logs.client import LockLogsClient
from .lock_logs.models import LogEntry

_LOGGER = logging.getLogger(__name__)


class LogsCoordinator(DataUpdateCoordinator[list[LogEntry]]):
    """Keep failures visible; cached history is never returned as a successful read."""

    def __init__(
        self, hass: Any, entry: Any, target: Any, store: Any, interval: int
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="SwitchBot Lock Logs",
            config_entry=entry,
            update_interval=timedelta(minutes=interval),
        )
        self.target = target
        self.store = store
        self.client = LockLogsClient(hass, target)
        self.fetch_lock = asyncio.Lock()
        self.data = store.history(target.device_id)
        self._live_task: asyncio.Task[Any] | None = None
        self.access_since: int | None = None
        self._access_baseline: set[str] = set()
        self.sync_pending = False
        self.access_entity_id: str | None = None
        self._last_notified_access: LogEntry | None = None
        self.last_update_success = False

    async def _fetch(self, base_time: int, max_entries: int) -> list[LogEntry]:
        async with self.fetch_lock:
            try:
                records = await self.client.fetch(base_time, max_entries)
                await self.store.append_history(self.target.device_id, records)
                return records
            except Exception as err:
                # HA Bluetooth exceptions vary by transport; cancellation derives
                # from BaseException and is deliberately not caught.
                raise UpdateFailed(
                    f"Cannot read SwitchBot lock history: {err}"
                ) from err

    async def _async_update_data(self) -> list[LogEntry]:
        await self._fetch(0, 1)
        return self.store.history(self.target.device_id)

    async def fetch_manual(
        self, base_time: int, max_entries: int
    ) -> list[dict[str, Any]]:
        """Update sensors and return only this request's fresh response."""
        try:
            records = await self._fetch(base_time, max_entries)
        except UpdateFailed as err:
            self.async_set_update_error(err)
            raise
        self.async_set_updated_data(self.store.history(self.target.device_id))
        return [log.as_dict(self.store.users(self.target.device_id)) for log in records]

    @callback
    def handle_lock_state(self, event: Any) -> None:
        """Start the first one-record read immediately, coalescing motor transitions."""
        self.async_update_listeners()
        old = event.data.get("old_state")
        new = event.data.get("new_state")
        if (
            old is None
            or new is None
            or old.state == new.state
            or old.state in ("unknown", "unavailable")
            or new.state not in ("unlocking", "unlocked", "opening", "open")
        ):
            return
        if self._live_task is not None and not self._live_task.done():
            return
        self._access_baseline = {record.raw for record in self.data or []}
        self.access_since = int(dt_util.utcnow().timestamp()) - 5
        self.sync_pending = True
        self.async_update_listeners()
        self._live_task = self.hass.async_create_background_task(
            self._sync_access(), "Sync SwitchBot unlock history"
        )

    async def _sync_access(self) -> None:
        try:
            for attempt in range(3):
                if attempt:
                    await asyncio.sleep(2 if attempt == 1 else 5)
                try:
                    await self.fetch_manual(0, 1 if attempt == 0 else 5)
                except UpdateFailed:
                    continue
                access = self.latest_access()
                if access is not None and access.user_id is not None:
                    break
            access = self.latest_access()
            if (
                access is not None
                and access.as_dict({})["source_name"] == "fingerprint"
            ):
                self._publish_access(access)
        finally:
            self.sync_pending = False
            self.async_update_listeners()

    def latest_access(self) -> LogEntry | None:
        """Never attribute a new unlock to a user from an older access."""
        if self.access_since is None:
            return None
        for record in self.data or []:
            if record.timestamp < self.access_since:
                break
            if record.raw not in self._access_baseline and record.as_dict({})[
                "action_name"
            ] in ("unlock", "unlatch"):
                return record
        return None

    async def nightly_sync(self, now: Any) -> None:
        """Fill gaps using the largest supported device history window."""
        try:
            await self.fetch_manual(0, 100)
        except UpdateFailed:
            _LOGGER.warning(
                "Nightly SwitchBot history sync failed for %s", self.target.name
            )

    async def async_shutdown(self) -> None:
        if self._live_task is not None and not self._live_task.done():
            self._live_task.cancel()
            await asyncio.gather(self._live_task, return_exceptions=True)
        await super().async_shutdown()

    @callback
    def _publish_access(self, record: LogEntry) -> None:
        """Expose confirmed live accesses to the companion device's Activity."""
        from homeassistant.helpers import device_registry as dr

        previous = self._last_notified_access
        if previous is not None and (
            previous.raw == record.raw
            or (
                previous.user_id == record.user_id
                and abs(previous.timestamp - record.timestamp) <= 15
                and previous.as_dict({})["action_name"]
                != record.as_dict({})["action_name"]
            )
        ):
            return
        device = (
            dr.async_get(self.hass).async_get_device_by_identifier(
                (DOMAIN, self.target.device_id), self.config_entry.entry_id
            )
            if self.config_entry is not None
            else None
        )
        self._last_notified_access = record
        self.hass.bus.async_fire(
            "switchbot_lock_logs_access",
            {
                **record.as_dict(self.store.users(self.target.device_id)),
                "device_id": device.id if device else self.target.device_id,
                "entity_id": self.access_entity_id,
                "lock_name": self.target.name,
            },
        )
