"""Conservative polling and serialized manual history retrieval."""

from __future__ import annotations

import asyncio
import logging
from bisect import bisect_left, insort
from datetime import timedelta
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .access import fingerprint_accesses
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
        self._activity_lock = asyncio.Lock()
        self._activity_records: list[LogEntry] = []
        self._activity_raws: set[str] = set()
        self._activity_times: dict[tuple[int | None, str], list[int]] = {}
        self._activity_dirty = False
        self._activity_recovered = False
        for record in store.activity_records(target.device_id):
            self._remember_activity(record)
        self._activity_dirty = False

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
        await self.import_activity()
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
        await self.import_activity()
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
            await self.import_activity()

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

        if self._activity_contains(record):
            return
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
        if self._activity_recorder("switchbot_lock_logs_access") is not None:
            self._remember_activity(record)
        self.hass.bus.async_fire(
            "switchbot_lock_logs_access",
            {
                **record.as_dict(self.store.users(self.target.device_id)),
                "device_id": device.id if device else self.target.device_id,
                "entity_id": self.access_entity_id,
                "lock_name": self.target.name,
                "lock_device_id": self.target.device_id,
            },
            time_fired=float(record.timestamp),
        )

    def _activity_contains(self, record: LogEntry) -> bool:
        """An unlock and nearby unlatch for the same user describe one visit."""
        if record.raw in self._activity_raws:
            return True
        action = record.as_dict({})["action_name"]
        other = "unlatch" if action == "unlock" else "unlock"
        times = self._activity_times.get((record.user_id, other), [])
        index = bisect_left(times, record.timestamp - 15)
        return index < len(times) and times[index] <= record.timestamp + 15

    def _remember_activity(self, record: LogEntry) -> None:
        if not self._activity_contains(record):
            self._activity_records.append(record)
            self._activity_raws.add(record.raw)
            times = self._activity_times.setdefault(
                (record.user_id, record.as_dict({})["action_name"]), []
            )
            insort(times, record.timestamp)
            self._activity_dirty = True

    def _activity_recorder(self, event_type: str) -> Any:
        """Do not mark entries as delivered while Recorder excludes them."""
        from homeassistant.components.recorder import get_instance
        from homeassistant.helpers.recorder import DATA_INSTANCE

        if (
            not self.access_entity_id
            or DATA_INSTANCE not in self.hass.data
            or "logbook" not in self.hass.data
        ):
            return None
        recorder = get_instance(self.hass)
        if (
            not recorder.recording
            or not recorder.enabled
            or event_type in recorder.exclude_event_types
            or (
                recorder.entity_filter is not None
                and not recorder.entity_filter(self.access_entity_id)
            )
        ):
            return None
        return recorder

    def _recorded_access_raws(self) -> set[str]:
        """Recover deliveries after an upgrade or interrupted checkpoint."""
        import json

        from homeassistant.components.recorder.db_schema import (
            EventData,
            Events,
            EventTypes,
        )
        from homeassistant.components.recorder.util import session_scope
        from sqlalchemy import select

        statement = (
            select(EventData.shared_data)
            .join(Events, Events.data_id == EventData.data_id)
            .join(EventTypes, Events.event_type_id == EventTypes.event_type_id)
            .where(
                EventTypes.event_type.in_(
                    (
                        "switchbot_lock_logs_access",
                        "switchbot_lock_logs_imported_access",
                    )
                )
            )
        )
        with session_scope(hass=self.hass, read_only=True) as session:
            return {
                data["raw"]
                for (encoded,) in session.execute(statement)
                if encoded
                and (data := json.loads(encoded)).get("entity_id")
                == self.access_entity_id
                and data.get("raw")
            }

    async def _flush_activity(self, recorder: Any) -> None:
        """Queue a commit barrier even when the worker just dequeued an event."""
        from homeassistant.components.recorder.tasks import SynchronizeTask

        future: asyncio.Future[None] = self.hass.loop.create_future()
        recorder.queue_task(SynchronizeTask(future))
        async with asyncio.timeout(30):
            await future

    async def import_activity(self, *_: Any) -> None:
        """Activity failures must not invalidate a successful Bluetooth read."""
        try:
            await self._import_activity()
        except Exception:
            _LOGGER.exception("Cannot synchronize Activity for %s", self.target.name)

    async def _import_activity(self) -> None:
        """Backfill native Activity without replaying the live automation event."""
        if not self.access_entity_id or self.sync_pending:
            return
        from homeassistant.helpers import device_registry as dr

        recorder = self._activity_recorder("switchbot_lock_logs_imported_access")
        if recorder is None or self.config_entry is None:
            return
        async with self._activity_lock:
            if self.sync_pending:
                return
            if not self._activity_recovered:
                await self._flush_activity(recorder)
                delivered = await recorder.async_add_executor_job(
                    self._recorded_access_raws
                )
                for record in self.store.history(self.target.device_id):
                    if record.raw in delivered:
                        self._remember_activity(record)
                self._activity_recovered = True
            if self.sync_pending:
                return
            device = dr.async_get(self.hass).async_get_device_by_identifier(
                (DOMAIN, self.target.device_id), self.config_entry.entry_id
            )
            users = self.store.users(self.target.device_id)
            for record in reversed(
                fingerprint_accesses(self.store.history(self.target.device_id))
            ):
                if self._activity_contains(record):
                    continue
                self.hass.bus.async_fire(
                    "switchbot_lock_logs_imported_access",
                    {
                        **record.as_dict(users),
                        "device_id": device.id if device else self.target.device_id,
                        "entity_id": self.access_entity_id,
                        "lock_name": self.target.name,
                        "lock_device_id": self.target.device_id,
                    },
                    time_fired=float(record.timestamp),
                )
                self._remember_activity(record)
            if self._activity_dirty:
                # Wait for queued Recorder writes before checkpointing delivery.
                # On storage failure retain the in-memory set and retry the save.
                await self._flush_activity(recorder)
                await self.store.save_activity_records(
                    self.target.device_id, self._activity_records
                )
                self._activity_dirty = False
