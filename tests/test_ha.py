import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from conftest import make_entry
from homeassistant.data_entry_flow import AbortFlow, FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.switchbot_lock_logs import (
    async_migrate_entry,
    async_setup,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.switchbot_lock_logs.config_flow import (
    LockLogsConfigFlow,
    LockLogsOptionsFlow,
)
from custom_components.switchbot_lock_logs.const import DOMAIN
from custom_components.switchbot_lock_logs.coordinator import LogsCoordinator
from custom_components.switchbot_lock_logs.lock_logs.client import discover_locks
from custom_components.switchbot_lock_logs.lock_logs.parser import parse_response
from custom_components.switchbot_lock_logs.sensor import LogSensor
from custom_components.switchbot_lock_logs.storage import CompanionStore

RECORD = bytes.fromhex("016553f10002010164590307010000")


@pytest.mark.parametrize("model", ["lock", "lock_pro", "lock_lite", "lock_ultra"])
async def test_discovery_models(hass, parent, model):
    hass.config_entries.async_update_entry(
        parent.entry, data={**parent.entry.data, "sensor_type": model}
    )
    target = discover_locks(hass)[parent.device.id]
    assert target.entry_id == parent.entry.entry_id
    assert target.entity_id == parent.entity.entity_id


async def test_discovery_excludes_unowned_and_non_locks(hass, parent):
    hass.config_entries.async_update_entry(parent.entry, data={"sensor_type": "bot"})
    assert not discover_locks(hass)
    other = make_entry(domain="switchbot_cloud")
    hass.config_entries._entries[other.entry_id] = other
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=other.entry_id, identifiers={("switchbot_cloud", "cloud")}
    )
    er.async_get(hass).async_get_or_create(
        "lock", "switchbot_cloud", "cloud", config_entry=other, device_id=device.id
    )
    assert not discover_locks(hass)


async def test_config_flow(hass, parent):
    flow = LockLogsConfigFlow()
    flow.hass = hass
    flow.handler = DOMAIN
    flow.context = {"source": "user"}
    result = await flow.async_step_user()
    assert result["type"] == "form"
    result = await flow.async_step_user({"device_id": "invalid"})
    assert result["errors"] == {"base": "device_not_found"}
    with patch(
        "custom_components.switchbot_lock_logs.coordinator.LockLogsClient.fetch",
        return_value=[],
    ):
        await flow.async_step_user({"device_id": parent.device.id})
        await flow._fetch_task
    assert (await flow.async_step_fetch_users())["step_id"] == "users"
    assert (await flow.async_step_users())["step_id"] == "no_users"
    result = await flow.async_step_review({})
    assert result["type"] == "create_entry"
    assert result["data"] == {"device_id": parent.device.id}
    assert flow.unique_id == parent.device.id


async def test_config_flow_no_locks(hass):
    flow = LockLogsConfigFlow()
    flow.hass = hass
    assert (await flow.async_step_user())["reason"] == "no_locks_found"


async def test_duplicate_flow(hass, parent):
    existing = make_entry(DOMAIN, {"device_id": parent.device.id})
    hass.config_entries._entries[existing.entry_id] = existing
    hass.config_entries.async_update_entry(existing, unique_id=parent.device.id)
    flow = LockLogsConfigFlow()
    flow.hass = hass
    flow.handler = DOMAIN
    flow.context = {"source": "user"}
    with pytest.raises(AbortFlow, match="already_configured"):
        await flow.async_step_user({"device_id": parent.device.id})


async def test_options_flow(hass, parent):
    entry = make_entry(DOMAIN, {"device_id": parent.device.id})
    hass.config_entries._entries[entry.entry_id] = entry
    flow = LockLogsOptionsFlow()
    flow.hass = hass
    flow.handler = entry.entry_id
    menu = await flow.async_step_init()
    assert menu["menu_options"] == ["fetch_users", "polling"]
    form = await flow.async_step_polling()
    assert form["data_schema"]({}) == {"poll_interval": 15}
    assert (await flow.async_step_polling({"poll_interval": 30}))["data"] == {
        "poll_interval": 30
    }


async def test_real_store_roundtrip_and_delete(hass, parent):
    store = CompanionStore(hass)
    await store.load()
    await store.migrate_device(parent.device.id, "AA:BB:CC:DD:EE:FF")
    await store.set_user(parent.device.id, 7, "Kai")
    loaded = CompanionStore(hass)
    await loaded.load()
    assert loaded.users(parent.device.id) == {"7": "Kai"}
    copy = loaded.users(parent.device.id)
    copy.clear()
    assert loaded.users(parent.device.id) == {"7": "Kai"}
    await loaded.set_user(parent.device.id, 7, None)
    again = CompanionStore(hass)
    await again.load()
    assert again.users(parent.device.id) == {}


async def test_legacy_mapping_migration(hass, parent):
    store = CompanionStore(hass)
    await store.legacy.async_save(
        {"aa:bb:cc:dd:ee:ff": {"name": "door", "users": {"7": "Guest"}}}
    )
    await store.load()
    await store.migrate_device(parent.device.id, "AA:BB:CC:DD:EE:FF")
    assert store.users(parent.device.id) == {"7": "Guest"}
    await store.set_user(parent.device.id, 7, "New")
    await store.migrate_device(parent.device.id, "AA:BB:CC:DD:EE:FF")
    assert store.users(parent.device.id)["7"] == "New"
    assert (await store.legacy.async_load())["aa:bb:cc:dd:ee:ff"]["users"][
        "7"
    ] == "Guest"


async def test_storage_failure_does_not_publish_unsaved_mapping(hass):
    store = CompanionStore(hass)
    with patch.object(store.store, "async_save", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            await store.set_user("door", 7, "Kai")
    assert store.users("door") == {}


async def test_unknown_storage_schema(hass):
    store = CompanionStore(hass)
    await store.store.async_save({"schema_version": 2, "devices": {}})
    with pytest.raises(ValueError, match="schema"):
        await store.load()


async def test_entry_migration(hass, parent):
    entry = make_entry(
        DOMAIN,
        {"device_id": parent.device.id, "mac_address": "old", "device_name": "Old"},
        minor_version=1,
    )
    hass.config_entries._entries[entry.entry_id] = entry
    assert await async_migrate_entry(hass, entry)
    assert entry.data == {"device_id": parent.device.id}
    assert entry.minor_version == 2


async def setup_coordinator(hass, parent):
    await async_setup(hass, {})
    entry = make_entry(DOMAIN, {"device_id": parent.device.id})
    hass.config_entries._entries[entry.entry_id] = entry
    store = hass.data[DOMAIN]["store"]
    await store.migrate_device(parent.device.id, "AA:BB:CC:DD:EE:FF")
    manager = LogsCoordinator(
        hass, entry, discover_locks(hass)[parent.device.id], store, 15
    )
    hass.data[DOMAIN]["coordinators"][entry.entry_id] = manager
    return entry, manager


async def test_services_update_sensors_and_user_mapping(hass, parent):
    entry, manager = await setup_coordinator(hass, parent)
    sensor = LogSensor(manager, "last_user", parent.device)
    sensor.hass = hass
    from unittest.mock import Mock

    listener = Mock()
    remove = manager.async_add_listener(lambda: listener())
    with patch.object(
        manager.client, "fetch", return_value=[parse_response(RECORD, parent.device.id)]
    ) as fetch:
        result = await hass.services.async_call(
            DOMAIN,
            "get_lock_logs",
            {"device_id": parent.device.id},
            blocking=True,
            return_response=True,
        )
    fetch.assert_awaited_once_with(0, 20)
    assert result["count"] == 1
    assert result["logs"][0]["user_id"] == 7
    assert sensor.native_value == "User 7"
    await hass.services.async_call(
        DOMAIN,
        "set_lock_user_name",
        {"device_id": parent.device.id, "user_id": 7, "name": "Kai"},
        blocking=True,
    )
    assert sensor.native_value == "Kai"
    count = LogSensor(manager, "log_count", parent.device)
    assert count.extra_state_attributes["logs"][0]["user_name"] == "Kai"
    await hass.services.async_call(
        DOMAIN,
        "delete_lock_user_name",
        {"device_id": parent.device.id, "user_id": 7},
        blocking=True,
    )
    assert sensor.native_value == "User 7"
    assert sensor.available
    hass.states.async_set(parent.entity.entity_id, "unavailable")
    assert not sensor.available
    remove()
    await manager.async_shutdown()


async def test_service_failure_and_validation(hass, parent):
    _, manager = await setup_coordinator(hass, parent)
    with patch.object(manager.client, "fetch", side_effect=RuntimeError("offline")):
        with pytest.raises(HomeAssistantError) as error:
            await hass.services.async_call(
                DOMAIN,
                "get_lock_logs",
                {"device_id": parent.device.id},
                blocking=True,
                return_response=True,
            )
        assert error.value.translation_domain == DOMAIN
        assert error.value.translation_key == "fetch_logs_error"
        assert "offline" in str(error.value.__cause__)
    assert not manager.last_update_success
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "get_lock_logs",
            {"device_id": "other"},
            blocking=True,
            return_response=True,
        )
    import voluptuous as vol

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            "get_lock_logs",
            {"device_id": parent.device.id, "max_entries": 101},
            blocking=True,
            return_response=True,
        )
    await manager.async_shutdown()


async def test_sensors_timestamp_and_empty(hass, parent):
    _, manager = await setup_coordinator(hass, parent)
    sensor = LogSensor(manager, "last_activity", parent.device)
    count = LogSensor(manager, "log_count", parent.device)
    assert sensor.native_value is None
    assert count.native_value == 0
    manager.async_set_updated_data([parse_response(RECORD, parent.device.id)])
    assert sensor.native_value.isoformat() == "2023-11-14T22:13:20+00:00"
    assert sensor.device_info["via_device_id"] == parent.device.id
    assert sensor.device_info["identifiers"] == {(DOMAIN, parent.device.id)}
    assert count.native_value == 1
    assert count.extra_state_attributes["logs"][0]["raw"] == RECORD[1:].hex()
    manager.async_set_updated_data([])
    assert sensor.native_value is None
    assert count.extra_state_attributes["logs"] == []
    await manager.async_shutdown()


async def test_offline_entry_setup_and_unload(hass, parent):
    await async_setup(hass, {})
    entry = make_entry(DOMAIN, {"device_id": parent.device.id})
    hass.config_entries._entries[entry.entry_id] = entry
    hass.states.async_set(parent.entity.entity_id, "unavailable")
    with patch.object(
        hass.config_entries, "async_forward_entry_setups", new_callable=AsyncMock
    ):
        assert await async_setup_entry(hass, entry)
    assert not entry.runtime_data.last_update_success
    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        new_callable=AsyncMock,
        return_value=False,
    ):
        assert not await async_unload_entry(hass, entry)
    assert entry.entry_id in hass.data[DOMAIN]["coordinators"]
    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        new_callable=AsyncMock,
        return_value=True,
    ):
        assert await async_unload_entry(hass, entry)
    assert entry.entry_id not in hass.data[DOMAIN]["coordinators"]
    await entry._async_process_on_unload(hass)


async def test_real_sensor_platform_uses_linked_companion_device(hass, parent):
    """Register entities through HA's real entity platform and device registry."""
    import logging
    from datetime import timedelta

    from homeassistant.helpers.entity_platform import EntityPlatform

    from custom_components.switchbot_lock_logs import sensor as module

    entry, manager = await setup_coordinator(hass, parent)
    manager.async_set_updated_data([parse_response(RECORD, parent.device.id)])
    platform = EntityPlatform(
        hass=hass,
        logger=logging.getLogger(__name__),
        domain="sensor",
        platform_name=DOMAIN,
        platform=module,
        scan_interval=timedelta(seconds=30),
        entity_namespace=None,
    )
    platform.config_entry = entry
    entities = [
        LogSensor(manager, key, parent.device)
        for key in (
            "last_activity",
            "last_user",
            "last_action",
            "log_count",
            "last_access",
        )
    ]
    # Platform metadata normally comes from translation loading, not transport.
    await platform.async_add_entities(entities)
    assert len(platform.entities) == 5
    assert manager.access_entity_id == entities[-1].entity_id
    assert all(hass.states.get(entity.entity_id) is not None for entity in entities)
    device_ids = {
        er.async_get(hass).async_get(entity.entity_id).device_id for entity in entities
    }
    assert len(device_ids) == 1
    companion = dr.async_get(hass).async_get(device_ids.pop())
    assert companion.via_device_id == parent.device.id
    assert companion.config_entry_id == entry.entry_id
    assert parent.device.config_entry_id == parent.entry.entry_id
    assert len(dr.async_get(hass).devices) == 2
    await platform.async_reset()
    await manager.async_shutdown()


async def test_sensor_id_migration_keeps_entity_ids(hass, parent):
    entry = make_entry(
        DOMAIN,
        {"device_id": parent.device.id, "mac_address": "AA:BB:CC:DD:EE:FF"},
        minor_version=1,
    )
    hass.config_entries._entries[entry.entry_id] = entry
    registry = er.async_get(hass)
    sensor = registry.async_get_or_create(
        "sensor", DOMAIN, "AA:BB:CC:DD:EE:FF-last_activity", config_entry=entry
    )
    assert await async_migrate_entry(hass, entry)
    assert (
        registry.async_get(sensor.entity_id).unique_id
        == f"{parent.device.id}-last_activity"
    )
    assert entry.unique_id == parent.device.id


@pytest.mark.parametrize("language", ["de", "en"])
async def test_dashboard_template_renders_history_and_offline(hass, parent, language):
    """Render the shipped card through HA's template engine, including escaping."""
    import asyncio
    from dataclasses import replace
    from pathlib import Path

    import yaml
    from homeassistant.helpers.template import Template
    from homeassistant.util import dt as dt_util

    _, manager = await setup_coordinator(hass, parent)
    original = parse_response(RECORD, parent.device.id)
    latest = replace(original, timestamp=1791391500)
    older = replace(original, timestamp=1791305100, user_id=None)
    manager.async_set_updated_data([latest, older])
    await manager.store.set_user(parent.device.id, 7, '<script>alert("name")</script>')
    sensor = LogSensor(manager, "log_count", parent.device)
    hass.states.async_set(
        "sensor.YOUR_LOCK_LOG_COUNT", sensor.native_value, sensor.extra_state_attributes
    )
    card_path = Path(__file__).parent.parent / f"docs/archive-card-{language}.yaml"
    config = yaml.safe_load(await asyncio.to_thread(card_path.read_text))
    template = Template(config["content"], hass)
    previous_zone = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Berlin"))
    try:
        result = template.async_render({"config": config}, parse_result=False)
        assert "07.10.2026" in result
        assert "06.10.2026" in result
        assert "18:45" in result
        assert "<script>" not in result
        assert "&lt;script&gt;" in result
        assert "Action 1" in result
        assert "Source 1" in result
        hass.states.async_set(
            "sensor.YOUR_LOCK_LOG_COUNT", "unavailable", sensor.extra_state_attributes
        )
        offline = template.async_render({"config": config}, parse_result=False)
        assert "unavailable" in offline
        assert "18:45" not in offline
        hass.states.async_set("sensor.YOUR_LOCK_LOG_COUNT", 0, {"logs": []})
        empty = template.async_render({"config": config}, parse_result=False)
        assert "Noch keine" in empty if language == "de" else "No events" in empty
    finally:
        dt_util.set_default_time_zone(previous_zone)
        await manager.async_shutdown()


async def test_user_mapping_options_fetch_and_atomic_save(hass, parent):
    import asyncio
    from dataclasses import replace
    from unittest.mock import Mock

    entry, manager = await setup_coordinator(hass, parent)
    hass.config_entries.async_update_entry(entry, options={"poll_interval": 30})
    await manager.store.set_users(parent.device.id, {7: "Old", 99: "Saved only"})
    record = parse_response(RECORD, parent.device.id)
    logs = [
        record,
        replace(record, user_id=12, raw="different-user-12"),
        replace(record, user_id=None, raw="no-user"),
    ]
    flow = LockLogsOptionsFlow()
    flow.hass = hass
    flow.handler = entry.entry_id

    async def delayed_fetch(*args):
        await asyncio.sleep(0)
        return logs

    with patch.object(manager.client, "fetch", side_effect=delayed_fetch) as fetch:
        progress = await flow.async_step_fetch_users()
        assert progress["type"] == "progress"
        assert progress["description_placeholders"]["device_name"] == parent.device.name
        await progress["progress_task"]
        assert (await flow.async_step_fetch_users())["step_id"] == "users"
    fetch.assert_awaited_once_with(0, 100)
    form = await flow.async_step_users()
    sections = form["data_schema"]({})
    assert len(sections) == 3
    assert sections[flow._section_titles[7]]["Enter a name"] == "Old"
    assert sections[flow._section_titles[12]]["Enter a name"] == ""
    hass.config.language = "de"
    german_form = await flow.async_step_users()
    german_sections = german_form["data_schema"]({})
    assert german_sections[flow._section_titles[7]]["Namen eintragen"] == "Old"
    assert german_sections[flow._section_titles[12]]["Namen eintragen"] == ""
    hass.config.language = "en"
    assert "Last action on:" in flow._section_titles[7]
    assert "No action" in flow._section_titles[99]
    assert "ID 7" in form["description_placeholders"]["history"]
    assert "ID 12" in form["description_placeholders"]["history"]
    assert "ID None" not in form["description_placeholders"]["history"]
    assert "Last action on:" in form["description_placeholders"]["history"]
    form = await flow.async_step_users({"ID 7": "x" * 101})
    assert form["errors"] == {"base": "invalid_name"}
    with patch.object(
        manager.store.store, "async_save", side_effect=OSError("disk full")
    ):
        form = await flow.async_step_users({"ID 7": "Kai", "ID 99": ""})
    assert form["errors"] == {"base": "save_failed"}
    assert manager.store.users(parent.device.id) == {"7": "Old", "99": "Saved only"}
    listener = Mock()
    remove = manager.async_add_listener(listener)
    result = await flow.async_step_users(
        {
            flow._section_titles[7]: {"Enter a name": " Kai "},
            flow._section_titles[12]: {"Enter a name": "Guest"},
            flow._section_titles[99]: {"Enter a name": ""},
        }
    )
    assert result["step_id"] == "review"
    result = await flow.async_step_review({})
    assert result["type"] == "create_entry"
    assert result["data"] == {"poll_interval": 30}
    assert manager.store.users(parent.device.id) == {"7": "Kai", "12": "Guest"}
    listener.assert_called_once()
    persisted = CompanionStore(hass)
    await persisted.load()
    assert persisted.users(parent.device.id) == {"7": "Kai", "12": "Guest"}
    remove()
    await manager.async_shutdown()


async def test_user_mapping_options_offline_retry_and_no_ids(hass, parent):
    entry, manager = await setup_coordinator(hass, parent)
    flow = LockLogsOptionsFlow()
    flow.hass = hass
    flow.handler = entry.entry_id
    await manager.store.set_user(parent.device.id, 7, "Existing")
    import asyncio

    async def offline_fetch(*args):
        await asyncio.sleep(0)
        raise RuntimeError("offline")

    with patch.object(manager.client, "fetch", side_effect=offline_fetch):
        progress = await flow.async_step_fetch_users()
        with pytest.raises(HomeAssistantError, match="offline"):
            await progress["progress_task"]
        result = await flow.async_step_fetch_users()
    assert result["step_id"] == "fetch_failed"
    assert (await flow.async_step_fetch_failed())["menu_options"] == ["retry", "users"]
    assert (await flow.async_step_users())["data_schema"]({}) == {
        flow._section_titles[7]: {"Enter a name": "Existing"}
    }
    with patch.object(manager.client, "fetch", return_value=[]):
        progress = await flow.async_step_retry()
        await flow._fetch_task
        assert (await flow.async_step_fetch_users())["step_id"] == "users"
    await manager.store.set_user(parent.device.id, 7, None)
    # A new visit has no fields to edit and offers retry instead of a blank form.
    fresh = LockLogsOptionsFlow()
    fresh.hass = hass
    fresh.handler = entry.entry_id
    assert (await fresh.async_step_users())["step_id"] == "no_users"
    await manager.async_shutdown()


async def test_user_mapping_requires_loaded_coordinator(hass, parent):
    entry = make_entry(DOMAIN, {"device_id": parent.device.id})
    hass.config_entries._entries[entry.entry_id] = entry
    flow = LockLogsOptionsFlow()
    flow.hass = hass
    flow.handler = entry.entry_id
    assert (await flow.async_step_fetch_users())["reason"] == "not_loaded"


async def test_first_run_wizard_transfers_fetched_history_to_setup(hass, parent):
    from dataclasses import replace

    flow = LockLogsConfigFlow()
    flow.hass = hass
    flow.handler = DOMAIN
    flow.context = {"source": "user"}
    record = parse_response(
        bytes.fromhex("016ac673ec00020f007b030a000000"), parent.device.id, "lock_pro"
    )
    logs = [
        replace(record, timestamp=record.timestamp + i, raw=f"record-{i}")
        for i in range(40)
    ]
    with patch(
        "custom_components.switchbot_lock_logs.coordinator.LockLogsClient.fetch",
        return_value=list(reversed(logs)),
    ) as fetch:
        await flow.async_step_user({"device_id": parent.device.id})
        await flow._fetch_task
    fetch.assert_awaited_once_with(0, 100)
    form = await flow.async_step_users()
    assert form["step_id"] == "users"
    review = await flow.async_step_users({"ID 10": "Kai"})
    assert review["step_id"] == "review"
    assert review["description_placeholders"]["count"] == "40"
    assert "Kai" in review["description_placeholders"]["events"]
    result = await flow.async_step_review({})
    entry = make_entry(DOMAIN, result["data"])
    hass.config_entries._entries[entry.entry_id] = entry
    await async_setup(hass, {})
    with (
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new_callable=AsyncMock
        ),
        patch(
            "custom_components.switchbot_lock_logs.coordinator.LockLogsClient.fetch"
        ) as fetch_again,
    ):
        assert await async_setup_entry(hass, entry)
    fetch_again.assert_not_called()
    assert len(entry.runtime_data.data) == 40
    assert entry.runtime_data.last_update_success
    assert entry.runtime_data.store.users(parent.device.id) == {"10": "Kai"}
    assert not hass.data[DOMAIN]["setup_history"]
    await entry.runtime_data.async_shutdown()
    await entry._async_process_on_unload(hass)


async def test_poll_uses_one_record_and_keeps_archive(hass, parent):
    _, manager = await setup_coordinator(hass, parent)
    with patch.object(manager.client, "fetch", return_value=[]) as fetch:
        await manager.async_refresh()
    fetch.assert_awaited_once_with(0, 1)
    await manager.async_shutdown()


@pytest.mark.parametrize("failed", [False, True])
async def test_real_flow_progress(hass, parent, failed):
    gate = asyncio.Event()

    async def fetch(*args):
        await gate.wait()
        if failed:
            raise RuntimeError("offline")
        return []

    manager = hass.config_entries.flow
    created = LockLogsConfigFlow()
    created.init_step = "user"
    with (
        patch.object(manager, "async_create_flow", return_value=created),
        patch(
            "custom_components.switchbot_lock_logs.coordinator.LockLogsClient.fetch",
            side_effect=fetch,
        ),
    ):
        initial = await manager.async_init(DOMAIN, context={"source": "user"})
        result = await manager.async_configure(
            initial["flow_id"], {"device_id": parent.device.id}
        )
        assert result["type"] == FlowResultType.SHOW_PROGRESS
        assert result["progress_action"] == "fetch_history"
        gate.set()
        flow = created
        await asyncio.gather(flow._fetch_task, return_exceptions=True)
        await hass.async_block_till_done()
        result = await manager.async_configure(result["flow_id"])
        assert result["step_id"] == ("fetch_failed" if failed else "no_users")
        manager.async_abort(result["flow_id"])
        await created._wizard_manager.async_shutdown()


async def test_archive_merges_duplicates_empty_reads_and_survives_restart(hass, parent):
    from dataclasses import replace

    entry, manager = await setup_coordinator(hass, parent)
    old = parse_response(RECORD, parent.device.id)
    new = replace(old, timestamp=old.timestamp + 1, raw="new-record")
    with patch.object(manager.client, "fetch", return_value=[old]):
        await manager.fetch_manual(0, 100)
    with patch.object(manager.client, "fetch", return_value=[new, old, new]):
        fresh = await manager.fetch_manual(0, 1)
    assert len(fresh) == 3
    assert manager.data == [new, old]
    with patch.object(manager.client, "fetch", return_value=[]):
        assert await manager.fetch_manual(0, 1) == []
    assert manager.data == [new, old]
    restored = CompanionStore(hass)
    await restored.load()
    assert restored.history(parent.device.id) == [new, old]
    with patch.object(restored.store, "async_save", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            await restored.append_history(
                parent.device.id, [replace(new, raw="unsaved")]
            )
    assert restored.history(parent.device.id) == [new, old]
    response = await hass.services.async_call(
        DOMAIN,
        "get_stored_lock_logs",
        {"device_id": parent.device.id},
        blocking=True,
        return_response=True,
    )
    assert response["count"] == 2
    await manager.async_shutdown()


async def test_unlock_listener_reads_one_then_retries_and_merges(hass, parent):
    from dataclasses import replace
    from types import SimpleNamespace

    from homeassistant.util import dt as dt_util

    _, manager = await setup_coordinator(hass, parent)
    old = parse_response(RECORD, parent.device.id)
    await manager.store.append_history(parent.device.id, [old])
    manager.async_set_updated_data([old])
    new = replace(
        old, timestamp=int(dt_util.utcnow().timestamp()), raw="current-unlock"
    )
    event = SimpleNamespace(
        data={
            "old_state": SimpleNamespace(state="locked"),
            "new_state": SimpleNamespace(state="unlocked"),
        }
    )
    with patch.object(manager.client, "fetch", side_effect=[[old], [new]]) as fetch:
        manager.handle_lock_state(event)
        task = manager._live_task
        manager.handle_lock_state(event)
        assert manager._live_task is task
        assert LogSensor(manager, "last_user", parent.device).native_value is None
        await task
    assert [call.args for call in fetch.await_args_list] == [(0, 1), (0, 5)]
    assert manager.data == [new, old]
    assert not manager.sync_pending
    with patch.object(manager.client, "fetch", return_value=[new]) as fetch:
        await manager.nightly_sync(None)
    fetch.assert_awaited_once_with(0, 100)
    assert manager.data == [new, old]
    with patch.object(manager.client, "fetch", side_effect=asyncio.CancelledError):
        manager.handle_lock_state(event)
        await manager.async_shutdown()
    assert not manager.sync_pending


async def test_access_view_filters_locks_and_pairs_unlatch(hass, parent):
    from dataclasses import replace

    from custom_components.switchbot_lock_logs.access import fingerprint_accesses

    _, manager = await setup_coordinator(hass, parent)
    unlock = parse_response(
        bytes.fromhex("016ac673ec00020f007b030a000000"), parent.device.id, "lock_pro"
    )
    unlatch = parse_response(
        bytes.fromhex("016ac673f200010f407b030a000000"), parent.device.id, "lock_pro"
    )
    locked = parse_response(
        bytes.fromhex("016ac6770c00010100000000000000"), parent.device.id, "lock_pro"
    )
    second = replace(unlock, timestamp=unlock.timestamp + 1, raw="second-unlock")
    manager.async_set_updated_data([locked, unlatch, second, unlock])
    assert fingerprint_accesses(manager.data) == [second, unlock]
    assert fingerprint_accesses([unlatch]) == [unlatch]
    await manager.store.set_user(parent.device.id, 10, "Kai")
    sensor = LogSensor(manager, "last_access", parent.device)
    assert sensor.native_value == "Kai"
    assert sensor.extra_state_attributes["access_count"] == 2
    assert all(
        row["action_name"] == "unlock"
        for row in sensor.extra_state_attributes["accesses"]
    )
    emitted = []
    remove = hass.bus.async_listen("switchbot_lock_logs_access", emitted.append)
    manager._publish_access(unlock)
    manager._publish_access(unlatch)
    await hass.async_block_till_done()
    assert len(emitted) == 1
    from custom_components.switchbot_lock_logs.logbook import async_describe_events

    handlers = {}
    async_describe_events(
        hass, lambda domain, event, describe: handlers.update({event: describe})
    )
    assert "Kai" in handlers["switchbot_lock_logs_access"](emitted[0])["message"]
    remove()
    await manager.async_shutdown()


@pytest.mark.parametrize("language", ["de", "en"])
async def test_native_access_template_and_entity_history(hass, parent, language):
    from dataclasses import replace
    from pathlib import Path

    import yaml
    from homeassistant.helpers.template import Template

    _, manager = await setup_coordinator(hass, parent)
    unlock = parse_response(
        bytes.fromhex("016ac673ec00020f007b030a000000"), parent.device.id, "lock_pro"
    )
    await manager.store.set_user(parent.device.id, 10, "<script>Kai</script>")
    manager.async_set_updated_data([unlock])
    sensor = LogSensor(manager, "last_access", parent.device)
    manager.sync_pending = True
    assert sensor.native_value == "<script>Kai</script>"
    first_time = sensor.extra_state_attributes["last_access_time"]
    assert sensor.extra_state_attributes["last_access_user_id"] == 10
    assert sensor.extra_state_attributes["history"][0]["name"] == sensor.native_value
    assert "history" in sensor._unrecorded_attributes
    second = replace(unlock, timestamp=unlock.timestamp + 60, raw="second-access")
    manager.async_set_updated_data([second, unlock])
    assert sensor.native_value == "<script>Kai</script>"
    assert sensor.extra_state_attributes["last_access_time"] != first_time
    assert len(sensor.extra_state_attributes["history"]) == 2
    path = Path(__file__).parent.parent / f"docs/dashboard-{language}.yaml"
    config = yaml.safe_load(await asyncio.to_thread(path.read_text))
    assert config["type"] == "markdown"
    entity_id = config["entity_id"]
    hass.states.async_set(entity_id, sensor.native_value, sensor.extra_state_attributes)
    template = Template(config["content"], hass)
    rendered = template.async_render({"config": config}, parse_result=False)
    assert "&lt;script&gt;Kai&lt;/script&gt;" in rendered
    assert "<script>" not in rendered
    assert sensor.extra_state_attributes["last_access_local"] in rendered
    assert "abrufen" not in rendered
    hass.states.async_set(entity_id, "unavailable", sensor.extra_state_attributes)
    offline = template.async_render({"config": config}, parse_result=False)
    assert "&lt;script&gt;Kai&lt;/script&gt;" in offline
    hass.states.async_set(entity_id, "unknown", {"history": []})
    empty = template.async_render({"config": config}, parse_result=False)
    assert "Noch keine" in empty if language == "de" else "No fingerprint" in empty
    await manager.async_shutdown()


@pytest.mark.parametrize("language", ["de", "en"])
async def test_automatic_native_card_language(hass, parent, language):
    from pathlib import Path

    import yaml
    from homeassistant.helpers.template import Template

    hass.config.language = language
    _, manager = await setup_coordinator(hass, parent)
    unlock = parse_response(
        bytes.fromhex("016ac673ec00020f007b030a000000"), parent.device.id, "lock_pro"
    )
    await manager.store.set_user(parent.device.id, 10, "Kai")
    manager.async_set_updated_data([unlock])
    sensor = LogSensor(manager, "last_access", parent.device)
    config = yaml.safe_load(
        await asyncio.to_thread(
            (Path(__file__).parent.parent / "docs/dashboard.yaml").read_text
        )
    )
    hass.states.async_set(
        config["entity_id"], sensor.native_value, sensor.extra_state_attributes
    )
    template = Template(config["content"], hass)
    result = template.async_render({"config": config}, parse_result=False)
    assert "Kai" in result
    assert ("Wer hat geöffnet?" if language == "de" else "Who unlocked?") in result
    from types import SimpleNamespace

    from custom_components.switchbot_lock_logs.logbook import async_describe_events

    descriptions = {}
    async_describe_events(
        hass, lambda domain, event, describe: descriptions.update({event: describe})
    )
    event = SimpleNamespace(
        data={**unlock.as_dict({"10": "Kai"}), "lock_name": "Front door"}
    )
    message = descriptions["switchbot_lock_logs_access"](event)["message"]
    assert message == ("Kai hat geöffnet" if language == "de" else "Kai unlocked")
    action = LogSensor(manager, "last_action", parent.device)
    assert action.native_value == "unlock"
    assert action.device_class == "enum"
    from dataclasses import replace

    manager.async_set_updated_data([replace(unlock, action=200, raw="unknown")])
    assert action.native_value == "unknown"
    assert action.extra_state_attributes["action"] == 200
    await manager.async_shutdown()


async def test_activity_backfill_original_times_restart_and_live_separation(
    hass, parent
):
    from dataclasses import replace
    from types import SimpleNamespace

    entry, manager = await setup_coordinator(hass, parent)
    manager.access_entity_id = "sensor.front_door_last_access"
    await manager.store.set_user(parent.device.id, 10, "Kai")
    unlock = parse_response(
        bytes.fromhex("016ac673ec00020f007b030a000000"), parent.device.id, "lock_pro"
    )
    unlatch = replace(
        unlock, source=1, value=64, timestamp=unlock.timestamp + 6, raw="paired"
    )
    second = replace(unlock, timestamp=unlock.timestamp + 60, raw="second")
    locked = replace(unlock, source=1, action=1, user_id=None, raw="lock")
    await manager.store.append_history(
        parent.device.id, [unlock, unlatch, second, locked]
    )
    recorder = SimpleNamespace(
        queue_task=lambda task: task.future.set_result(None),
        async_add_executor_job=AsyncMock(return_value=set()),
    )
    imported, live = [], []
    remove_imported = hass.bus.async_listen(
        "switchbot_lock_logs_imported_access", imported.append
    )
    remove_live = hass.bus.async_listen("switchbot_lock_logs_access", live.append)
    with patch.object(manager, "_activity_recorder", return_value=recorder):
        await manager.import_activity()
        await hass.async_block_till_done()
        assert len(imported) == 2
        assert not live
        assert sorted(event.time_fired_timestamp for event in imported) == [
            unlock.timestamp,
            second.timestamp,
        ]
        assert all(
            event.data["entity_id"] == manager.access_entity_id for event in imported
        )
        assert all(event.data["user_name"] == "Kai" for event in imported)
        from custom_components.switchbot_lock_logs.logbook import async_describe_events

        handlers = {}
        async_describe_events(
            hass, lambda domain, event, describe: handlers.update({event: describe})
        )
        describe = handlers["switchbot_lock_logs_imported_access"]
        await manager.store.set_user(parent.device.id, 10, "Renamed")
        assert "Renamed" in describe(imported[0])["message"]
        await manager.store.set_user(parent.device.id, 10, None)
        assert "ID 10" in describe(imported[0])["message"]
        await manager.import_activity()
        assert len(imported) == 2
        # A normal restart restores the checkpoint, even if Recorder purged events.
        store = CompanionStore(hass)
        await store.load()
        restored = LogsCoordinator(hass, entry, manager.target, store, 15)
        restored.access_entity_id = manager.access_entity_id
        with patch.object(restored, "_activity_recorder", return_value=recorder):
            await restored.import_activity()
            await hass.async_block_till_done()
            assert len(imported) == 2
            # Live accesses stay on the automation event, and are not imported twice.
            third = replace(unlock, timestamp=unlock.timestamp + 120, raw="third")
            await store.append_history(parent.device.id, [third])
            restored.sync_pending = True
            await restored.import_activity()
            assert len(imported) == 2
            restored._publish_access(third)
            restored.sync_pending = False
            await restored.import_activity()
            await hass.async_block_till_done()
            assert len(live) == 1
            assert len(imported) == 2
            # Gaps recovered by nightly/manual reads become historical Activity only.
            fourth = replace(unlock, timestamp=unlock.timestamp + 180, raw="fourth")
            with patch.object(restored.client, "fetch", return_value=[fourth]):
                await restored.nightly_sync(None)
            await hass.async_block_till_done()
            assert len(imported) == 3
            assert len(live) == 1
        await restored.async_shutdown()
    remove_imported()
    remove_live()
    await manager.async_shutdown()


async def test_activity_recovers_existing_delivery_and_retries_checkpoint(hass, parent):
    from types import SimpleNamespace

    _, manager = await setup_coordinator(hass, parent)
    manager.access_entity_id = "sensor.front_door_last_access"
    unlock = parse_response(
        bytes.fromhex("016ac673ec00020f007b030a000000"), parent.device.id, "lock_pro"
    )
    await manager.store.append_history(parent.device.id, [unlock])
    recorder = SimpleNamespace(
        queue_task=lambda task: task.future.set_result(None),
        async_add_executor_job=AsyncMock(return_value={unlock.raw}),
    )
    imported = []
    remove = hass.bus.async_listen(
        "switchbot_lock_logs_imported_access", imported.append
    )
    with patch.object(manager, "_activity_recorder", return_value=recorder):
        with patch.object(
            manager.store.store, "async_save", side_effect=OSError("full")
        ):
            await manager.import_activity()
            assert manager._activity_dirty
        assert not imported  # Upgrade / interrupted checkpoint is not replayed.
        await manager.import_activity()
    assert [
        record.raw for record in manager.store.activity_records(parent.device.id)
    ] == [unlock.raw]
    assert manager.store.history(parent.device.id) == [unlock]
    remove()
    await manager.async_shutdown()


async def test_activity_import_is_stored_in_real_recorder(hass, parent):
    from homeassistant.components.recorder import get_instance
    from homeassistant.loader import async_setup as setup_loader
    from homeassistant.setup import async_setup_component

    setup_loader(hass)
    from homeassistant.helpers.recorder import DATA_RECORDER, RecorderData

    hass.data[DATA_RECORDER] = RecorderData()
    assert await async_setup_component(
        hass, "recorder", {"recorder": {"auto_purge": False}}
    )
    hass.data["logbook"] = {}
    await hass.async_start()
    await hass.async_block_till_done()
    recorder = get_instance(hass)
    await recorder.async_block_till_done()
    _, manager = await setup_coordinator(hass, parent)
    manager.access_entity_id = "sensor.front_door_last_access"
    unlock = parse_response(
        bytes.fromhex("016ac673ec00020f007b030a000000"), parent.device.id, "lock_pro"
    )
    await manager.store.append_history(parent.device.id, [unlock])
    assert manager._activity_recorder("switchbot_lock_logs_imported_access") is recorder
    assert manager.store.history(parent.device.id) == [unlock]
    await manager.import_activity()
    assert manager._activity_records
    assert await recorder.async_add_executor_job(manager._recorded_access_raws) == {
        unlock.raw
    }
    # If a process stopped after the DB commit but before the store checkpoint,
    # the next import discovers the existing event instead of inserting another.
    manager._activity_records = []
    manager._activity_raws.clear()
    manager._activity_times.clear()
    manager._activity_recovered = False
    await manager.import_activity()

    def stored_times():
        from homeassistant.components.recorder.db_schema import Events, EventTypes
        from homeassistant.components.recorder.util import session_scope
        from sqlalchemy import select

        with session_scope(hass=hass, read_only=True) as session:
            return list(
                session.scalars(
                    select(Events.time_fired_ts)
                    .join(EventTypes, Events.event_type_id == EventTypes.event_type_id)
                    .where(
                        EventTypes.event_type == "switchbot_lock_logs_imported_access"
                    )
                )
            )

    assert await recorder.async_add_executor_job(stored_times) == [
        float(unlock.timestamp)
    ]
    from datetime import timedelta

    from homeassistant.components.logbook.models import LogbookConfig
    from homeassistant.components.logbook.processor import EventProcessor
    from homeassistant.util import dt as dt_util

    from custom_components.switchbot_lock_logs.logbook import async_describe_events

    handlers = {}
    async_describe_events(
        hass,
        lambda domain, event, describe: handlers.update({event: (domain, describe)}),
    )
    hass.data["logbook"] = LogbookConfig(handlers)
    await manager.store.set_user(parent.device.id, 10, "Kai")
    processor = EventProcessor(
        hass, list(handlers), entity_ids=[manager.access_entity_id], timestamp=True
    )
    start = dt_util.utc_from_timestamp(unlock.timestamp) - timedelta(seconds=1)
    rows = await recorder.async_add_executor_job(
        processor.get_events, start, start + timedelta(seconds=2)
    )
    assert len(rows) == 1
    assert rows[0]["entity_id"] == manager.access_entity_id
    assert "Kai" in rows[0]["message"]
    await manager.async_shutdown()


async def test_confirmed_last_access_survives_failed_reads_and_offline_lock(
    hass, parent
):
    from homeassistant.helpers.update_coordinator import UpdateFailed

    _, manager = await setup_coordinator(hass, parent)
    await manager.store.set_user(parent.device.id, 10, "Kai")
    unlock = parse_response(
        bytes.fromhex("016ac673ec00020f007b030a000000"), parent.device.id, "lock_pro"
    )
    manager.async_set_updated_data([unlock])
    sensor = LogSensor(manager, "last_access", parent.device)
    sensor.hass = hass
    manager.async_set_update_error(UpdateFailed("Bluetooth timeout"))
    hass.states.async_set(parent.entity.entity_id, "unavailable")
    assert sensor.available
    assert sensor.native_value == "Kai"
    assert sensor.extra_state_attributes["lock_available"] is False
    assert sensor.extra_state_attributes["last_sync_success"] is False
    manager.async_set_updated_data([])
    assert not sensor.available  # No confirmed access to retain.
    await manager.async_shutdown()
