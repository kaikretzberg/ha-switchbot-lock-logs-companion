"""The sole module allowed to access SwitchBot runtime/library internals.

See docs/ANALYSIS.md for provenance and why a public transport cannot be used.
No class/instance patching, new device or connection, or copied cipher code.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import inspect
import logging
import textwrap
from dataclasses import dataclass
from functools import lru_cache
from importlib.metadata import version
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from ..const import LOCK_MODELS, SWITCHBOT_DOMAIN
from .models import LogEntry
from .parser import ProtocolError, parse_response

BASE_TIME_COMMAND = "57001401"
READ_COMMAND = "57001405"
_LOGGER = logging.getLogger(__name__)

# The command/locking/IV choreography adapted below is identical in the audited
# 2.4.1, 2.9.0 and 3.0.0 releases. AST hashes ignore formatting and comments.
# Check the actual inherited methods, not a package version or method names alone.
_TRANSPORT_HASHES = {
    "_send_command": "840c231b49584fc06b11692f095243392c92fb60c9abb92b783d5198264a55a1",
    "_execute_forced_disconnect": "45ad8cfa65a60f91de602fd525be01b336a3ff47dbede30385c4b372647c25cd",
    "_execute_disconnect": "f43ff0ebc022bf6631e85991c5fa4f9cb3271526aaec3757128e18ff30786bd7",
}


@lru_cache(maxsize=16)
def inspect_transport(device_type: type) -> tuple[str, str | None]:
    """Inspect installed code once per runtime class, only in an executor job."""
    installed = version("PySwitchbot")
    for name, expected in _TRANSPORT_HASHES.items():
        try:
            source = textwrap.dedent(inspect.getsource(getattr(device_type, name)))
            actual = hashlib.sha256(ast.dump(ast.parse(source)).encode()).hexdigest()
        except AttributeError, OSError, TypeError, SyntaxError:
            return installed, name
        if actual != expected:
            return installed, name
    return installed, None


class DeviceUnavailable(RuntimeError):
    """The official device/runtime is currently unavailable."""


class CompatibilityError(RuntimeError):
    """The installed library does not satisfy the audited transport contract."""


@dataclass(frozen=True)
class LockTarget:
    """Registry identity of a lock owned by the official integration."""

    device_id: str
    entry_id: str
    entity_id: str
    name: str
    address: str
    model: str = ""


def discover_locks(hass: Any) -> dict[str, LockTarget]:
    """Match official lock entities to their owning config entries and device."""
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    result = {}
    for entity in entities.entities.values():
        if (
            entity.platform != SWITCHBOT_DOMAIN
            or entity.domain != "lock"
            or not entity.device_id
        ):
            continue
        entry = hass.config_entries.async_get_entry(entity.config_entry_id)
        device = devices.async_get(entity.device_id)
        if (
            entry is None
            or entry.domain != SWITCHBOT_DOMAIN
            or device is None
            or entry.entry_id != device.config_entry_id
            or entry.data.get("sensor_type") not in LOCK_MODELS
        ):
            continue
        result[device.id] = LockTarget(
            device.id,
            entry.entry_id,
            entity.entity_id,
            device.name_by_user or device.name or entry.title,
            entry.data.get("address", entry.data.get("mac", "")),
            entry.data["sensor_type"],
        )
    return result


def resolve_target(hass: Any, device_id: str) -> LockTarget | None:
    """Resolve current and pre-2026.8 composite IDs using the public registry."""
    locks = discover_locks(hass)
    if device_id in locks:
        return locks[device_id]
    device, entry = dr.async_get_device_and_config_entry_for_domain(
        hass,
        device_id,
        domain=SWITCHBOT_DOMAIN,
    )
    return locks.get(device.id) if device is not None and entry is not None else None


def target_available(hass: Any, target: LockTarget) -> bool:
    """Use public config-entry and state APIs to track the parent availability."""
    current = resolve_target(hass, target.device_id)
    if current is None:
        return False
    target = current
    entry = hass.config_entries.async_get_entry(target.entry_id)
    state = hass.states.get(target.entity_id)
    return bool(
        entry
        and entry.state == ConfigEntryState.LOADED
        and state
        and state.state not in ("unavailable", "unknown")
    )


def resolve_device(hass: Any, target: LockTarget) -> Any:
    """Resolve on every request, including after official integration reloads."""
    from switchbot import SwitchbotLock

    current = resolve_target(hass, target.device_id)
    if current is None:
        raise DeviceUnavailable("Official SwitchBot lock registry entry is missing")
    target = current
    entry = hass.config_entries.async_get_entry(target.entry_id)
    if entry is None or entry.state != ConfigEntryState.LOADED:
        raise DeviceUnavailable(
            "Configure and load the official SwitchBot integration first"
        )
    state = hass.states.get(target.entity_id)
    if state is None or state.state in ("unavailable", "unknown"):
        raise DeviceUnavailable("The official SwitchBot lock entity is unavailable")
    device = getattr(getattr(entry, "runtime_data", None), "device", None)
    if not isinstance(device, SwitchbotLock):
        raise CompatibilityError(
            "Official SwitchBot runtime no longer exposes its lock device"
        )
    return device


class LockLogsClient:
    """Read a cursor atomically using the existing device and encryption state."""

    def __init__(self, hass: Any, target: LockTarget) -> None:
        self.hass = hass
        self.target = target
        self.library_version: str | None = None

    async def fetch(self, base_time: int = 0, max_entries: int = 20) -> list[LogEntry]:
        """Bound the entire transaction, including time waiting for the BLE lock."""
        if type(base_time) is not int or not 0 <= base_time <= 0xFFFFFFFF:
            raise ValueError("base_time must be a uint32 Unix timestamp")
        if type(max_entries) is not int or not 1 <= max_entries <= 100:
            raise ValueError("max_entries must be between 1 and 100")
        device = resolve_device(self.hass, self.target)
        # Metadata and source inspection involve disk I/O. Future releases with
        # unchanged transaction choreography remain compatible automatically.
        self.library_version, incompatible = await self.hass.async_add_executor_job(
            inspect_transport, type(device)
        )
        if incompatible:
            raise CompatibilityError(
                f"PySwitchbot {self.library_version} changed the required transport "
                f"method {incompatible}. Update SwitchBot Lock Logs for this transport."
            )
        required = (
            "_ensure_encryption_initialized",
            "_encrypt",
            "_decrypt",
            "_commandkey",
            "_send_command_locked_with_retry",
            "_increment_gcm_iv",
            "_execute_forced_disconnect",
        )
        if not isinstance(
            getattr(device, "_operation_lock", None), asyncio.Lock
        ) or any(not callable(getattr(device, name, None)) for name in required):
            raise CompatibilityError(
                "Unsupported PySwitchbot encrypted transport contract"
            )
        async with asyncio.timeout(180):
            async with device._operation_lock:
                try:
                    status = await self._send_locked(
                        device, BASE_TIME_COMMAND + base_time.to_bytes(4, "big").hex()
                    )
                    if not status or status[0] not in (1, 6):
                        raise ProtocolError("Lock rejected setting the history cursor")
                    records = []
                    seen: set[str] = set()
                    for _ in range(max_entries):
                        record = parse_response(
                            await self._send_locked(device, READ_COMMAND),
                            self.target.device_id,
                            self.target.model,
                        )
                        if record is None:
                            break
                        if record.timestamp >= base_time and record.raw not in seen:
                            seen.add(record.raw)
                            records.append(record)
                    return sorted(
                        records,
                        key=lambda log: (log.timestamp, log.index),
                        reverse=True,
                    )
                finally:
                    # Keep the shared operation lock until disconnect completes.
                    # Upstream resets the timer, connection and cipher together.
                    try:
                        async with asyncio.timeout(10):
                            await device._execute_forced_disconnect()
                    except Exception:
                        _LOGGER.warning(
                            "Failed to release SwitchBot history connection",
                            exc_info=True,
                        )

    @staticmethod
    async def _send_locked(device: Any, key: str) -> bytes:
        """Adapt the encrypted 2.4.1 send body without reacquiring its lock.

        Mirrors the audited encrypted send transaction, including the GCM IV increment.
        The library still performs encryption, decryption, connection and retries.
        """
        from switchbot.devices.device import AESMode

        if not await device._ensure_encryption_initialized():
            raise DeviceUnavailable("SwitchBot encryption initialization failed")
        ciphertext, header = device._encrypt(key[2:])
        encrypted = key[:2] + device._key_id + header + ciphertext
        result = await device._send_command_locked_with_retry(
            encrypted,
            bytearray.fromhex(device._commandkey(encrypted)),
            device._retry_count,
            device._retry_count + 1,
        )
        if result is None or len(result) < 4:
            raise ProtocolError("Missing or truncated encrypted response")
        decrypted = device._decrypt(result[4:])
        if device._encryption_mode == AESMode.GCM:
            device._increment_gcm_iv()
        return bytes(result[:1] + decrypted)
