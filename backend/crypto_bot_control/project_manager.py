from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import venv
from datetime import datetime, timezone
from pathlib import Path

from . import config as app_config
from .config import ensure_dirs
from .project_analyzer import SKIP_DIRS, analyze_project
from .security import sanitize_bot_id_name


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def copy_project(source: Path, dest: Path) -> None:
    source = Path(source).resolve()
    dest = Path(dest)
    if dest.exists():
        _copy_tree(source, dest)
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    _copy_tree(source, dest)


def _copy_tree(source: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        if item.name in SKIP_DIRS:
            continue
        target = dest / item.name
        if item.is_dir():
            _copy_tree(item, target)
        else:
            shutil.copy2(item, target)


def project_fingerprint(root: Path) -> str:
    h = hashlib.sha256()
    files = []
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        files.append((rel, path))
    for rel, path in files:
        h.update(rel.encode("utf-8"))
        try:
            h.update(path.read_bytes())
        except OSError:
            continue
    return h.hexdigest()


def list_relative_files(root: Path) -> dict[str, str]:
    mapping = {}
    for path in Path(root).rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        try:
            mapping[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            mapping[rel] = ""
    return mapping


def diff_file_maps(old: dict[str, str], new: dict[str, str]) -> dict:
    old_keys = set(old)
    new_keys = set(new)
    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)
    modified = sorted(k for k in (old_keys & new_keys) if old[k] != new[k])
    return {"added": added, "removed": removed, "modified": modified}


def bot_dirs(slug: str) -> dict[str, Path]:
    ensure_dirs()
    root = app_config.BOTS_DIR / slug
    return {
        "root": root,
        "current": root / "current",
        "versions": root / "versions",
        "venv": root / "venv",
        "logs": root / "logs",
        "state": root / "state",
    }


def allocate_slug(name: str, existing: set[str]) -> str:
    base = sanitize_bot_id_name(name).lower()
    slug = base
    i = 2
    while slug in existing:
        slug = f"{base}-{i}"
        i += 1
    return slug


def create_venv(venv_path: Path) -> str:
    venv_path.parent.mkdir(parents=True, exist_ok=True)
    py = venv_python(venv_path)
    if Path(py).exists():
        return py
    try:
        builder = venv.EnvBuilder(with_pip=True, clear=False)
        builder.create(str(venv_path))
    except Exception:
        return sys.executable
    py = venv_python(venv_path)
    return py if Path(py).exists() else sys.executable


def venv_python(venv_path: Path) -> str:
    if sys.platform == "win32":
        candidate = venv_path / "Scripts" / "python.exe"
    else:
        candidate = venv_path / "bin" / "python"
    return str(candidate)


def install_dependencies(venv_python_path: str, project_root: Path) -> dict:
    logs = []
    ok = True
    cmds = []
    pyproject = project_root / "pyproject.toml"
    requirements = project_root / "requirements.txt"
    if pyproject.is_file():
        cmds.append([venv_python_path, "-m", "pip", "install", "--upgrade", "pip"])
        cmds.append([venv_python_path, "-m", "pip", "install", str(project_root)])
    elif requirements.is_file():
        cmds.append([venv_python_path, "-m", "pip", "install", "--upgrade", "pip"])
        cmds.append([venv_python_path, "-m", "pip", "install", "-r", str(requirements)])
    else:
        return {"ok": True, "logs": ["No dependency manifest detected; skipped install."], "error": ""}

    for cmd in cmds:
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=180,
                cwd=str(project_root),
            )
            logs.append((proc.stdout or "") + (proc.stderr or ""))
            if proc.returncode != 0:
                ok = False
                return {
                    "ok": False,
                    "logs": logs,
                    "error": f"Dependency install failed: {' '.join(cmd)}\n{(proc.stderr or proc.stdout or '')[-2000:]}",
                }
        except Exception as exc:
            return {"ok": False, "logs": logs, "error": str(exc)}
    return {"ok": ok, "logs": logs, "error": ""}


def snapshot_version(current: Path, versions_dir: Path, label: str) -> Path:
    dest = versions_dir / label
    if dest.exists():
        dest = versions_dir / f"{label}-{utcnow().strftime('%H%M%S')}"
    _copy_tree(current, dest)
    return dest


def restore_snapshot(snapshot: Path, current: Path) -> None:
    if current.exists():
        tmp = current.with_name(current.name + ".old")
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        current.rename(tmp)
        try:
            _copy_tree(snapshot, current)
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            if current.exists():
                shutil.rmtree(current, ignore_errors=True)
            tmp.rename(current)
            raise
    else:
        _copy_tree(snapshot, current)


def parse_env_file(text: str) -> dict[str, str]:
    result = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            result[key] = value
    return result
