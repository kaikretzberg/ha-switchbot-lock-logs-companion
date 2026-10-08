import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components/switchbot_lock_logs"


def test_manifest_and_hacs():
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert list(manifest) == [
        "domain",
        "name",
        *sorted(set(manifest) - {"domain", "name"}),
    ]
    assert manifest["domain"] == "switchbot_lock_logs"
    assert manifest["dependencies"] == ["switchbot"]
    assert not manifest.get("requirements")
    for key in (
        "name",
        "version",
        "documentation",
        "issue_tracker",
        "codeowners",
        "config_flow",
        "integration_type",
        "iot_class",
    ):
        assert key in manifest
    assert json.loads((ROOT / "hacs.json").read_text())["homeassistant"] == "2026.9.4"


def test_no_fork_imports_and_only_adapter_uses_switchbot():
    forbidden = ["Lock" + "LogAction", "Lock" + "LogSource"]
    for path in COMPONENT.rglob("*.py"):
        text = path.read_text()
        assert all(name not in text for name in forbidden)
        for node in ast.walk(ast.parse(text)):
            modules = (
                [node.module]
                if isinstance(node, ast.ImportFrom)
                else [n.name for n in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            for module in modules:
                if module and (
                    module == "switchbot" or module.startswith("switchbot.")
                ):
                    assert path.name == "client.py"
        assert "BleakClient(" not in text


def test_translations_valid():
    strings = json.loads((COMPONENT / "strings.json").read_text())

    def keys(data, prefix=""):
        result = set()
        for key, value in data.items():
            path = prefix + "/" + key
            if isinstance(value, dict):
                result |= keys(value, path)
            else:
                result.add(path)
        return result

    for language in ("en", "de"):
        translated = json.loads(
            (COMPONENT / f"translations/{language}.json").read_text()
        )
        assert keys(strings) == keys(translated)


def test_required_repository_documentation_exists():
    """HACS information and the release archives must have usable source files."""
    for filename in ("README.md", "LICENSE", "CHANGELOG.md"):
        path = ROOT / filename
        assert path.is_file(), f"Required repository information missing: {filename}"
        assert path.read_text().strip(), (
            f"Required repository information empty: {filename}"
        )
