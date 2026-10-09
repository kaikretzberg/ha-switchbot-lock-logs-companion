import asyncio
import json
from importlib.metadata import version
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from switchbot.devices.device import AESMode

from custom_components.switchbot_lock_logs.lock_logs.client import (
    CompatibilityError,
    DeviceUnavailable,
    LockLogsClient,
    discover_locks,
    inspect_transport,
)
from custom_components.switchbot_lock_logs.lock_logs.parser import (
    ProtocolError,
    parse_response,
)

PACKETS = json.loads((Path(__file__).parent / "fixtures/packets.json").read_text())


def test_parser():
    log = parse_response(bytes.fromhex(PACKETS["unlock_keypad_user_7"]), "door")
    assert (
        log.timestamp,
        log.index,
        log.source,
        log.action,
        log.value,
        log.user_id,
    ) == (1700000000, 2, 1, 1, 100, 7)
    assert log.as_dict({})["user_name"] == "User 7"
    assert log.as_dict({"7": "Kai"})["user_name"] == "Kai"
    assert log.as_dict({})["result"] == "success"
    assert parse_response(bytes.fromhex(PACKETS["empty"]), "door") is None
    assert (
        parse_response(bytes.fromhex(PACKETS["low_battery_unlock"]), "door").user_id
        == 7
    )


@pytest.mark.parametrize(
    "packet",
    [
        None,
        b"",
        b"\x00",
        b"\x01",
        b"\x01\x00",
        b"\x01" + b"\x00" * 7,
        b"\x01" + b"\x00" * 4 + b"\x01" * 4,
    ],
)
def test_malformed(packet):
    with pytest.raises(ProtocolError):
        parse_response(packet, "door")


def test_unknown_codes_and_payload():
    log = parse_response(bytes.fromhex("016553f10002ffff64590407010000"), "door")
    assert log.user_id is None
    assert log.as_dict({})["action_name"] == "unknown_255"
    assert log.as_dict({})["result"] is None
    assert log.raw.endswith("590407010000")


@pytest.mark.parametrize("mode", [AESMode.CTR, AESMode.GCM])
async def test_actual_library_encryption_and_atomicity(hass, parent, mode):
    """Exercise real library encryption, decrypt and shared command lock."""
    lock = parent.lock
    lock._encryption_mode = mode
    lock._iv = b"\x01" * (12 if mode == AESMode.GCM else 16)
    original_iv = lock._iv
    commands = []
    first_read = asyncio.Event()
    continue_read = asyncio.Event()

    async def exchange(key, command, retry, max_attempts):
        assert lock._operation_lock.locked()
        assert bytes(command) == bytes.fromhex(key)
        header = bytes.fromhex(key[4:8])
        ciphertext = bytes.fromhex(key[8:])
        decrypt_mode = (
            modes.CTR(lock._iv)
            if mode == AESMode.CTR
            else modes.GCM(lock._iv, b"\x00" * 16)
        )
        decryptor = Cipher(
            algorithms.AES128(lock._encryption_key), decrypt_mode
        ).decryptor()
        plain = decryptor.update(ciphertext).hex()
        commands.append(plain)
        if plain == "001405" and commands.count(plain) == 1:
            first_read.set()
            await continue_read.wait()
            body = bytes.fromhex(PACKETS["unlock_keypad_user_7"])[1:]
        elif plain == "001405":
            body = b"\x00" * 14
        else:
            body = b"\x00" * 8
        encrypt_mode = (
            modes.CTR(lock._iv) if mode == AESMode.CTR else modes.GCM(lock._iv)
        )
        encryptor = Cipher(
            algorithms.AES128(lock._encryption_key), encrypt_mode
        ).encryptor()
        encrypted_body = encryptor.update(body) + encryptor.finalize()
        return b"\x01\x01" + header + encrypted_body

    target = discover_locks(hass)[parent.device.id]
    with (
        patch.object(lock, "_send_command_locked_with_retry", side_effect=exchange),
        patch.object(lock, "_execute_forced_disconnect"),
    ):
        task = asyncio.create_task(LockLogsClient(hass, target).fetch(1234, 3))
        await asyncio.wait_for(first_read.wait(), 2)
        ordinary_command = asyncio.create_task(lock._send_command("570f4f8101"))
        await asyncio.sleep(0)
        assert not ordinary_command.done()
        continue_read.set()
        records = await task
        await ordinary_command
    assert commands == ["001401000004d2", "001405", "001405", "0f4f8101"]
    assert records[0].user_id == 7
    assert not lock._operation_lock.locked()
    if mode == AESMode.GCM:
        assert int.from_bytes(lock._iv, "big") == int.from_bytes(original_iv, "big") + 4
    else:
        assert lock._iv == original_iv


async def test_unavailable_parent(hass, parent):
    target = discover_locks(hass)[parent.device.id]
    hass.states.async_set(parent.entity.entity_id, "unavailable")
    with pytest.raises(DeviceUnavailable):
        await LockLogsClient(hass, target).fetch()


async def test_reload_resolves_new_instance(hass, parent):
    from custom_components.switchbot_lock_logs.lock_logs.client import resolve_device

    target = discover_locks(hass)[parent.device.id]
    original = parent.lock
    assert resolve_device(hass, target) is original
    parent.entry.runtime_data.device = type(original)(
        original._device, key_id="01", encryption_key="00" * 16
    )
    assert resolve_device(hass, target) is parent.entry.runtime_data.device
    assert resolve_device(hass, target) is not original


async def test_future_library_with_unchanged_transport_is_supported(hass, parent):
    client = LockLogsClient(hass, discover_locks(hass)[parent.device.id])
    inspect_transport.cache_clear()
    with (
        patch(
            "custom_components.switchbot_lock_logs.lock_logs.client.version",
            return_value="9.0.0",
        ),
        patch.object(
            client,
            "_send_locked",
            new_callable=AsyncMock,
            side_effect=[b"\x01", bytes.fromhex(PACKETS["empty"])],
        ),
    ):
        assert await client.fetch() == []
        assert client.library_version == "9.0.0"
    inspect_transport.cache_clear()


async def test_changed_transport_fails_before_sending(hass, parent):
    client = LockLogsClient(hass, discover_locks(hass)[parent.device.id])
    inspect_transport.cache_clear()

    async def changed_send(self, key, **kwargs):
        raise AssertionError("Changed transport must never be called")

    with (
        patch.object(type(parent.lock), "_send_command", changed_send),
        patch.object(client, "_send_locked", new_callable=AsyncMock) as send,
    ):
        with pytest.raises(CompatibilityError, match="_send_command"):
            await client.fetch()
        send.assert_not_called()
    inspect_transport.cache_clear()


async def test_transport_inspection_runs_outside_event_loop(hass, parent):
    import threading

    client = LockLogsClient(hass, discover_locks(hass)[parent.device.id])
    inspect_transport.cache_clear()
    loop_thread = threading.get_ident()
    installed = version("PySwitchbot")

    def metadata_version(name):
        assert threading.get_ident() != loop_thread
        return installed

    with (
        patch(
            "custom_components.switchbot_lock_logs.lock_logs.client.version",
            metadata_version,
        ),
        patch.object(
            client,
            "_send_locked",
            new_callable=AsyncMock,
            side_effect=[b"\x01", bytes.fromhex(PACKETS["empty"])],
        ),
    ):
        await client.fetch()
    inspect_transport.cache_clear()


async def test_partial_response_not_silently_successful(hass, parent):
    client = LockLogsClient(hass, discover_locks(hass)[parent.device.id])
    with patch.object(
        client,
        "_send_locked",
        new_callable=AsyncMock,
        side_effect=[b"\x01", bytes.fromhex(PACKETS["unlock_keypad_user_7"]), b"\x00"],
    ):
        with pytest.raises(ProtocolError):
            await client.fetch()
    assert not parent.lock._operation_lock.locked()


async def test_empty_device_response(hass, parent):
    client = LockLogsClient(hass, discover_locks(hass)[parent.device.id])
    with patch.object(
        client,
        "_send_locked",
        new_callable=AsyncMock,
        side_effect=[b"\x01", bytes.fromhex(PACKETS["empty"])],
    ):
        assert await client.fetch() == []


async def test_cancellation_releases_shared_lock(hass, parent):
    client = LockLogsClient(hass, discover_locks(hass)[parent.device.id])
    entered = asyncio.Event()

    async def send(*args):
        entered.set()
        await asyncio.Event().wait()

    with patch.object(client, "_send_locked", side_effect=send):
        task = asyncio.create_task(client.fetch())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not parent.lock._operation_lock.locked()


@pytest.mark.parametrize(
    "base,count", [(-1, 20), (2**32, 20), (0, 0), (0, 101), (True, 20)]
)
async def test_client_validation(hass, parent, base, count):
    with pytest.raises(ValueError):
        await LockLogsClient(hass, discover_locks(hass)[parent.device.id]).fetch(
            base, count
        )


@pytest.mark.parametrize(
    "body,action,source,user",
    [
        ("00010100000000000000", "lock", "app", None),
        ("00020f007b030a000000", "unlock", "fingerprint", 10),
        ("00010f407b030a000000", "unlatch", "fingerprint", 10),
        ("00010f407b030c000000", "unlatch", "fingerprint", 12),
        ("00030000000000000000", "unknown_event_3_0_0", "unknown_0", None),
        ("00038000000000000000", "unknown_event_3_128_0", "unknown_128", None),
        ("00020f007b0300000000", "unknown_event_2_15_0", "unknown_15", None),
    ],
)
def test_lock_pro_app_correlated_records(body, action, source, user):
    """Captured header/payload combinations with a substituted timestamp."""
    record = parse_response(bytes.fromhex("016553f100" + body), "door", "lock_pro")
    data = record.as_dict({"10": "Kai"})
    assert data["action_name"] == action
    assert data["source_name"] == source
    assert data["user_id"] == user
    assert data["result"] == (None if action.startswith("unknown") else "success")
    if user == 10:
        assert data["user_name"] == "Kai"
    # The original Lock retains its legacy interpretation and identity rules.
    legacy = parse_response(bytes.fromhex("016553f100" + body), "door", "lock")
    assert legacy.as_dict({})["interpretation"] == "reference_fork"
    assert legacy.user_id is None


async def test_lock_pro_fetch_deduplicates_only_identical_records(hass, parent):
    hass.config_entries.async_update_entry(
        parent.entry, data={**parent.entry.data, "sensor_type": "lock_pro"}
    )
    target = discover_locks(hass)[parent.device.id]
    assert target.model == "lock_pro"
    client = LockLogsClient(hass, target)
    unlock = bytes.fromhex("016553f10000020f007b030a000000")
    unlatch = bytes.fromhex("016553f10000010f407b030a000000")
    with patch.object(
        client,
        "_send_locked",
        new_callable=AsyncMock,
        side_effect=[b"\x01", unlock, unlock, unlatch, bytes.fromhex(PACKETS["empty"])],
    ):
        logs = await client.fetch(max_entries=5)
    assert len(logs) == 2
    assert {log.as_dict({})["action_name"] for log in logs} == {"unlock", "unlatch"}
    assert all(log.user_id == 10 for log in logs)


@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
async def test_history_disconnects_under_operation_lock(hass, parent, outcome):
    """Release the actual shared library connection and cipher on every exit."""
    from types import SimpleNamespace

    lock = parent.lock
    lock._iv = b"\x01" * 16
    lock._cipher = object()
    lock._encryption_mode = AESMode.CTR
    disconnect = AsyncMock()
    lock._client = SimpleNamespace(disconnect=disconnect)
    lock._disconnect_timer = asyncio.get_running_loop().call_later(60, lambda: None)
    read_started = asyncio.Event()

    async def send(device, key):
        assert lock._operation_lock.locked()
        if key.startswith("57001401"):
            return b"\x01"
        if outcome == "error":
            raise ProtocolError("read failed")
        if outcome == "cancel":
            read_started.set()
            await asyncio.Future()
        return b"\x01" + bytes(14)

    async def released():
        assert lock._operation_lock.locked()
        assert lock._connect_lock.locked()

    disconnect.side_effect = released
    client = LockLogsClient(hass, discover_locks(hass)[parent.device.id])
    with patch.object(client, "_send_locked", side_effect=send):
        if outcome == "cancel":
            task = asyncio.create_task(client.fetch())
            await asyncio.wait_for(read_started.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif outcome == "error":
            with pytest.raises(ProtocolError):
                await client.fetch()
        else:
            assert await client.fetch() == []
    disconnect.assert_awaited_once()
    assert lock._disconnect_timer is None
    assert lock._client is None
    assert lock._iv is None
    assert lock._cipher is None
    assert lock._encryption_mode is None
    assert not lock._operation_lock.locked()
