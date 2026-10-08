"""Build and verify installable and complete-source archives."""

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components/switchbot_lock_logs"
VERSION = json.loads((COMPONENT / "manifest.json").read_text())["version"]
DIST = ROOT / "dist"
DIST.mkdir(exist_ok=True)


def release_files():
    """Include code and notices, excluding generated or machine-local state."""
    for top in ("custom_components", "docs", "tests", "scripts", ".github"):
        for path in sorted((ROOT / top).rglob("*")):
            if (
                path.is_file()
                and "__pycache__" not in path.parts
                and path.suffix != ".pyc"
                and path.name != ".DS_Store"
            ):
                yield path
    for name in (
        "README.md",
        "LICENSE",
        "CHANGELOG.md",
        "hacs.json",
        "pyproject.toml",
        "requirements-dev.txt",
        ".gitignore",
    ):
        yield ROOT / name


def build(path: Path, files: list[Path]) -> None:
    """Write reproducible members and verify byte-for-byte extraction."""
    missing = [
        source.relative_to(ROOT).as_posix() for source in files if not source.is_file()
    ]
    if missing:
        raise FileNotFoundError(
            f"Cannot build release: required files missing: {', '.join(missing)}"
        )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for source in files:
            member = zipfile.ZipInfo(
                source.relative_to(ROOT).as_posix(), (2026, 10, 8, 0, 0, 0)
            )
            member.external_attr = 0o644 << 16
            member.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(member, source.read_bytes())
    with zipfile.ZipFile(path) as archive, tempfile.TemporaryDirectory() as temporary:
        assert archive.testzip() is None
        assert (
            "custom_components/switchbot_lock_logs/manifest.json" in archive.namelist()
        )
        archive.extractall(temporary)
        for source in files:
            assert (
                Path(temporary) / source.relative_to(ROOT)
            ).read_bytes() == source.read_bytes()
    print(f"Verified {path.name}: {len(files)} files")


if __name__ == "__main__":
    package = DIST / f"switchbot-lock-logs-companion-{VERSION}.zip"
    sources = DIST / f"switchbot-lock-logs-companion-{VERSION}-source.zip"
    install_files = [
        path
        for path in release_files()
        if path.is_relative_to(ROOT / "custom_components")
    ]
    # Keep documentation and upstream license notices available in the package.
    install_files += [ROOT / "README.md", ROOT / "LICENSE", ROOT / "CHANGELOG.md"]
    install_files += sorted(
        path for path in (ROOT / "docs").iterdir() if path.is_file()
    )
    build(package, install_files)
    build(sources, list(release_files()))
    (DIST / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
            for path in (package, sources)
        )
    )
