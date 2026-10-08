"""Real HA registries and runtime objects; no substitute homeassistant modules."""

from types import MappingProxyType

import homeassistant  # noqa: F401 -- initialize HA's schema engine first
import pytest
from bleak.backends.device import BLEDevice
from homeassistant.config_entries import ConfigEntries, ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from switchbot import SwitchbotLock, SwitchbotModel


@pytest.fixture
async def hass(tmp_path):
    instance = HomeAssistant(str(tmp_path))
    instance.config_entries = ConfigEntries(instance, {})
    dr.async_setup(instance)
    await dr.async_load(instance)
    await er.async_load(instance)
    yield instance
    await instance.async_stop(force=True)


def make_entry(
    domain="switchbot", data=None, state=ConfigEntryState.LOADED, minor_version=2
):
    return ConfigEntry(
        domain=domain,
        data=data or {"sensor_type": "lock", "address": "AA:BB:CC:DD:EE:FF"},
        options={},
        title="Front door",
        version=1,
        minor_version=minor_version,
        source="user",
        unique_id=None,
        state=state,
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
    )


@pytest.fixture
async def parent(hass):
    from types import SimpleNamespace

    entry = make_entry()
    hass.config_entries._entries[entry.entry_id] = entry
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("switchbot", "original-identity")},
        connections={(dr.CONNECTION_BLUETOOTH, "aa:bb:cc:dd:ee:ff")},
        name="Front door",
    )
    entity = er.async_get(hass).async_get_or_create(
        "lock", "switchbot", "original-lock", config_entry=entry, device_id=device.id
    )
    hass.states.async_set(entity.entity_id, "locked")
    lock = SwitchbotLock(
        BLEDevice("AA:BB:CC:DD:EE:FF", "Front door", {}),
        key_id="01",
        encryption_key="00" * 16,
        model=SwitchbotModel.LOCK,
    )
    entry.runtime_data = SimpleNamespace(device=lock)
    return SimpleNamespace(entry=entry, device=device, entity=entity, lock=lock)
