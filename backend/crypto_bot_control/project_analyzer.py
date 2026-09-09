from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from .config import COMMON_ENV_HINTS, ENTRY_POINT_CANDIDATES, TELEGRAM_COMMAND_PATTERNS

SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    "dist",
    "build",
}

ENV_NAME_RE = re.compile(r"""os\.environ(?:\.get)?\(\s*['"]([A-Z0-9_]+)['"]""")
ENV_GETENV_RE = re.compile(r"""os\.getenv\(\s*['"]([A-Z0-9_]+)['"]""")
DOTENV_RE = re.compile(r"""^([A-Z0-9_]+)\s*=""", re.MULTILINE)
COMMAND_RE = re.compile(r"""['"](/(?:start|status|positions|stop|pause|resume|help|balance|pnl|close)(?:@[A-Za-z0-9_]+)?)['"]""")


def _iter_project_files(root: Path) -> list[Path]:
    files: list[Path] = []
    if not root.exists():
        return files
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        files.append(path)
    return files


def detect_entry_points(root: Path) -> list[str]:
    found: list[str] = []
    for name in ENTRY_POINT_CANDIDATES:
        candidate = root / name
        if candidate.is_file():
            found.append(name)
    for py in sorted(root.glob("*.py")):
        rel = py.name
        if rel not in found:
            try:
                text = py.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if 'if __name__ == "__main__"' in text or "if __name__ == '__main__'" in text:
                found.append(rel)
    return found


def detect_python_files(root: Path) -> list[str]:
    files = []
    for path in _iter_project_files(root):
        if path.suffix == ".py":
            files.append(str(path.relative_to(root)).replace("\\", "/"))
    return sorted(files)


def detect_dependency_files(root: Path) -> dict:
    result = {
        "pyproject_toml": (root / "pyproject.toml").is_file(),
        "requirements_txt": (root / "requirements.txt").is_file(),
        "pipfile": (root / "Pipfile").is_file(),
        "preferred": None,
        "packages": [],
    }
    if result["pyproject_toml"]:
        result["preferred"] = "pyproject.toml"
        result["packages"] = _parse_pyproject(root / "pyproject.toml")
    elif result["requirements_txt"]:
        result["preferred"] = "requirements.txt"
        result["packages"] = _parse_requirements(root / "requirements.txt")
    elif result["pipfile"]:
        result["preferred"] = "Pipfile"
    return result


def _parse_requirements(path: Path) -> list[str]:
    packages = []
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            packages.append(line.split(";")[0].strip())
    except OSError:
        pass
    return packages


def _parse_pyproject(path: Path) -> list[str]:
    packages = []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return packages
    in_deps = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[project") and "dependencies" in text.lower():
            pass
        if stripped in {"dependencies = [", "dependencies=["}:
            in_deps = True
            continue
        if in_deps:
            if stripped.startswith("]"):
                break
            pkg = stripped.strip(",").strip('"').strip("'")
            if pkg:
                packages.append(pkg)
    return packages


def detect_env_names(root: Path) -> list[str]:
    names: set[str] = set()
    example = root / ".env.example"
    if example.is_file():
        try:
            names.update(DOTENV_RE.findall(example.read_text(encoding="utf-8", errors="ignore")))
        except OSError:
            pass
    env_file = root / ".env"
    if env_file.is_file():
        try:
            names.update(DOTENV_RE.findall(env_file.read_text(encoding="utf-8", errors="ignore")))
        except OSError:
            pass
    for path in _iter_project_files(root):
        if path.suffix != ".py":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        names.update(ENV_NAME_RE.findall(text))
        names.update(ENV_GETENV_RE.findall(text))
    for hint in COMMON_ENV_HINTS:
        if hint in names:
            continue
    return sorted(names)


def detect_capabilities(root: Path) -> dict:
    blob = ""
    for path in _iter_project_files(root):
        if path.suffix not in {".py", ".txt", ".toml", ".md", ".env", ".example", ".cfg", ".ini", ".json", ".yml", ".yaml"}:
            continue
        try:
            blob += "\n" + path.read_text(encoding="utf-8", errors="ignore")[:50000]
        except OSError:
            continue
    lower = blob.lower()
    telegram_cmds = sorted(set(COMMAND_RE.findall(blob)))
    pause_supported = any(cmd in {"/pause", "/resume"} for cmd in telegram_cmds) or (
        "def pause" in lower or "on_pause" in lower
    )
    return {
        "binance": any(k in lower for k in ("binance", "ccxt", "python-binance", "binance.client")),
        "binance_futures": any(k in lower for k in ("futures", "um_futures", "fapi")),
        "telegram": any(k in lower for k in ("telegram", "python-telegram-bot", "telebot", "aiogram")),
        "websocket": any(k in lower for k in ("websocket", "websockets", "ws.connect")),
        "rest": any(k in lower for k in ("requests.", "httpx", "aiohttp", "urllib")),
        "telegram_commands": telegram_cmds,
        "pause_supported": pause_supported,
        "logging": "logging" in lower,
        "dotenv": "dotenv" in lower or ".env" in lower,
    }


def detect_plaintext_env(root: Path) -> bool:
    return (root / ".env").is_file()


def analyze_project(root: Path) -> dict:
    root = Path(root).resolve()
    python_files = detect_python_files(root)
    entry_points = detect_entry_points(root)
    deps = detect_dependency_files(root)
    env_names = detect_env_names(root)
    caps = detect_capabilities(root)
    uncertain = []
    if not entry_points:
        uncertain.append("Could not confidently detect an entry point")
    if not deps["preferred"]:
        uncertain.append("Could not confidently detect a dependency manifest")
    if not env_names:
        uncertain.append("Could not confidently detect environment-variable names")
    return {
        "root": str(root),
        "python_files": python_files,
        "file_count": len(python_files),
        "entry_points": entry_points,
        "dependencies": deps,
        "env_names": env_names,
        "capabilities": caps,
        "plaintext_env_present": detect_plaintext_env(root),
        "uncertain": uncertain,
        "python_version": "unknown",
    }


def static_ast_safe(path: Path) -> bool:
    try:
        ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        return True
    except (SyntaxError, OSError):
        return False
