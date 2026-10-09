"""Use the same validation library as Home Assistant's native flow sections."""

from importlib import import_module
from typing import Any

from homeassistant.data_entry_flow import section

# HA 2026.9 uses Voluptuous; 2026.10 uses Probatio. Using the native schema
# implementation also keeps selectors, service validation and nested sections
# consistent without checking HA versions or installing a separate dependency.
vol: Any = import_module(type(section.CONFIG_SCHEMA).__module__.split(".")[0])
