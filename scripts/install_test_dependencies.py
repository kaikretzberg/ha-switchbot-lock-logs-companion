"""Install the official SwitchBot dependencies into an isolated test environment.

Use after installing development requirements. Never run from the integration.
No library version is selected by this project: read the installed HA manifest.
"""

import json
import subprocess
import sys
from pathlib import Path

import homeassistant

manifest = Path(homeassistant.__file__).parent / "components/switchbot/manifest.json"
requirements = json.loads(manifest.read_text())["requirements"]
subprocess.run(
    ["uv", "pip", "install", "--python", sys.executable, *requirements], check=True
)
