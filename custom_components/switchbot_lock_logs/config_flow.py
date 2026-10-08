"""Select a registered official SwitchBot lock, without credentials or addresses."""

import asyncio
import html
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
)
from homeassistant.util import dt as dt_util

from .access import fingerprint_accesses
from .const import DEFAULT_INTERVAL, DOMAIN
from .coordinator import LogsCoordinator
from .lock_logs.client import discover_locks
from .lock_logs.models import LogEntry
from .storage import CompanionStore


class HistoryWizard:
    """Shared guided download, mapping and history review for both entry points."""

    hass: Any
    async_abort: Any
    async_show_form: Any
    async_show_menu: Any
    async_show_progress: Any
    async_show_progress_done: Any

    def __init__(self) -> None:
        super().__init__()
        self._fetch_task: asyncio.Task[Any] | None = None
        self._user_ids: list[int] | None = None
        self._history: str = ""
        self._section_titles: dict[int, str] = {}

    def _manager(self) -> Any:
        raise NotImplementedError

    async def async_step_fetch_users(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        manager = self._manager()
        if manager is None:
            return self.async_abort(reason="not_loaded")
        if self._fetch_task is None:
            self._fetch_task = self.hass.async_create_background_task(
                manager.fetch_manual(0, 100), "Fetch SwitchBot history for user mapping"
            )
        if self._fetch_task.done():
            next_step = "users"
            if self._fetch_task.cancelled() or self._fetch_task.exception() is not None:
                next_step = "fetch_failed"
            return self.async_show_progress_done(next_step_id=next_step)
        return self.async_show_progress(
            step_id="fetch_users",
            progress_action="fetch_history",
            description_placeholders={"device_name": manager.target.name},
            progress_task=self._fetch_task,
        )

    async def async_step_fetch_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="fetch_failed", menu_options=["retry", "users"]
        )

    async def async_step_retry(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        self._fetch_task = None
        self._user_ids = None
        self._history = ""
        return await self.async_step_fetch_users()

    async def async_step_users(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        manager = self._manager()
        if manager is None:
            return self.async_abort(reason="not_loaded")
        stored = manager.store.users(manager.target.device_id)
        if self._user_ids is None:
            latest: dict[int, LogEntry] = {}
            for log in manager.data or []:
                if log.user_id is not None:
                    if (
                        log.user_id not in latest
                        or log.timestamp > latest[log.user_id].timestamp
                    ):
                        latest[log.user_id] = log
            self._user_ids = sorted(set(latest) | {int(key) for key in stored})
            rows = []
            for user_id, log in sorted(latest.items()):
                time = dt_util.as_local(dt_util.utc_from_timestamp(log.timestamp))
                label = (
                    f"Letzte Aktion am: {time:%d.%m.%Y %H:%M} Uhr"
                    if self.hass.config.language == "de"
                    else f"Last action on: {time:%d.%m.%Y %H:%M}"
                )
                rows.append(f"**ID {user_id}** · {label}")
            self._history = "\n\n".join(rows)
            for user_id in self._user_ids:
                log = latest.get(user_id)
                if log is None:
                    label = (
                        "Keine Aktion im geladenen Verlauf"
                        if self.hass.config.language == "de"
                        else "No action in the downloaded history"
                    )
                else:
                    time = dt_util.as_local(dt_util.utc_from_timestamp(log.timestamp))
                    label = (
                        f"Letzte Aktion am: {time:%d.%m.%Y %H:%M} Uhr"
                        if self.hass.config.language == "de"
                        else f"Last action on: {time:%d.%m.%Y %H:%M}"
                    )
                self._section_titles[user_id] = f"ID {user_id} · {label}"
        if not self._user_ids:
            return await self.async_step_no_users()
        name_label = (
            "Namen eintragen" if self.hass.config.language == "de" else "Enter a name"
        )
        errors = {}
        if user_input is not None:
            changes = {}
            for user_id in self._user_ids:
                values = user_input.get(self._section_titles[user_id], user_input)
                name = values.get(
                    name_label,
                    values.get(f"ID {user_id}", stored.get(str(user_id), "")),
                )
                if not isinstance(name, str) or len(name) > 100:
                    errors["base"] = "invalid_name"
                    break
                changes[user_id] = name.strip() or None
            if not errors:
                try:
                    await manager.store.set_users(manager.target.device_id, changes)
                except OSError:
                    errors["base"] = "save_failed"
                else:
                    manager.async_update_listeners()
                    return await self.async_step_review()
        return self.async_show_form(
            step_id="users",
            errors=errors,
            description_placeholders={
                "history": self._history,
                "count": str(len(manager.data or [])),
            },
            data_schema=vol.Schema(
                {
                    vol.Optional(self._section_titles[user_id], default=dict): section(
                        vol.Schema(
                            {
                                vol.Optional(
                                    name_label,
                                    default=(user_input or {})
                                    .get(
                                        self._section_titles[user_id], user_input or {}
                                    )
                                    .get(name_label, stored.get(str(user_id), "")),
                                ): TextSelector()
                            }
                        ),
                        {"collapsed": False},
                    )
                    for user_id in self._user_ids
                }
            ),
        )

    async def async_step_no_users(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="no_users", menu_options=["retry", "review"]
        )

    async def async_step_review(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        manager = self._manager()
        if manager is None:
            return self.async_abort(reason="not_loaded")
        if user_input is not None:
            return await self._finish_wizard()
        rows = []
        german = self.hass.config.language == "de"
        actions = (
            {
                "lock": "Verriegelt",
                "unlock": "Entriegelt",
                "unlatch": "Riegel entriegelt",
            }
            if german
            else {"lock": "Locked", "unlock": "Unlocked", "unlatch": "Unlatched"}
        )
        users = manager.store.users(manager.target.device_id)
        for log in fingerprint_accesses(manager.data or [])[:15]:
            values = log.as_dict(users)
            time = dt_util.as_local(dt_util.utc_from_timestamp(log.timestamp))
            action = actions.get(
                values["action_name"], "Unbekannt" if german else "Unknown"
            )
            name = values["user_name"] or (
                "Ohne Benutzerkennung" if german else "No user ID"
            )
            name = (
                html.escape(name)
                .replace("*", "\\*")
                .replace("_", "\\_")
                .replace("[", "\\[")
                .replace("]", "\\]")
            )
            rows.append(f"**{time:%d.%m.%Y %H:%M:%S}** · {action} · {name}")
        return self.async_show_form(
            step_id="review",
            data_schema=vol.Schema({}),
            description_placeholders={
                "count": str(len(fingerprint_accesses(manager.data or []))),
                "status": (
                    "Aktueller Abruf erfolgreich"
                    if german
                    else "Fresh download succeeded"
                )
                if manager.last_update_success
                else (
                    "Abruf fehlgeschlagen; zuletzt verfügbare Daten"
                    if german
                    else "Download failed; last available data"
                ),
                "events": "\n\n".join(rows)
                or ("Keine Ereignisse" if german else "No events"),
            },
        )

    async def _finish_wizard(self) -> ConfigFlowResult:
        raise NotImplementedError


class LockLogsConfigFlow(HistoryWizard, ConfigFlow, domain=DOMAIN):
    """One companion config entry per existing lock."""

    VERSION = 1
    MINOR_VERSION = 2

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        locks = discover_locks(self.hass)
        if not locks:
            return self.async_abort(reason="no_locks_found")
        errors = {}
        if user_input is not None:
            device_id = user_input["device_id"]
            if device_id not in locks:
                errors["base"] = "device_not_found"
            else:
                await self.async_set_unique_id(device_id)
                self._abort_if_unique_id_configured()
                store = self.hass.data.get(DOMAIN, {}).get("store")
                if store is None:
                    store = CompanionStore(self.hass)
                    await store.load()
                await store.migrate_device(device_id, locks[device_id].address)
                self._wizard_manager = LogsCoordinator(
                    self.hass, None, locks[device_id], store, DEFAULT_INTERVAL
                )
                return await self.async_step_fetch_users()
        return self.async_show_form(
            step_id="user",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required("device_id"): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=key, label=lock.name)
                                for key, lock in locks.items()
                            ],
                        )
                    )
                }
            ),
        )

    def _manager(self) -> Any:
        return getattr(self, "_wizard_manager", None)

    async def _finish_wizard(self) -> ConfigFlowResult:
        manager = self._manager()
        if manager.last_update_success:
            self.hass.data.setdefault(DOMAIN, {}).setdefault("setup_history", {})[
                manager.target.device_id
            ] = list(manager.data or [])
        await manager.async_shutdown()
        return self.async_create_entry(
            title=f"{manager.target.name} Logs",
            data={"device_id": manager.target.device_id},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> OptionsFlow:
        return LockLogsOptionsFlow()


class LockLogsOptionsFlow(HistoryWizard, OptionsFlow):
    """Native settings and a guided history-backed user-name editor."""

    def _manager(self) -> Any:
        return (
            self.hass.data.get(DOMAIN, {})
            .get("coordinators", {})
            .get(self.config_entry.entry_id)
        )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init", menu_options=["fetch_users", "polling"]
        )

    async def async_step_polling(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title="", data={**self.config_entry.options, **user_input}
            )
        return self.async_show_form(
            step_id="polling",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "poll_interval",
                        default=self.config_entry.options.get(
                            "poll_interval", DEFAULT_INTERVAL
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=5, max=1440)),
                }
            ),
        )

    async def _finish_wizard(self) -> ConfigFlowResult:
        return self.async_create_entry(title="", data=dict(self.config_entry.options))
