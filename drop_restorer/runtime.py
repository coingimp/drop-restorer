"""Small runtime helpers shared by the local launchers.

The application is distributed as a source checkout.  A virtual environment
created by Python uses ``Scripts`` on Windows and ``bin`` on Unix-like
systems; keeping that detail in one place prevents a Linux clone from
silently falling back to an unrelated system interpreter.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys


def workspace_root(anchor: str | Path | None = None) -> Path:
    """Resolve the checkout used for runtime data and child processes.

    ``DROP_RESTORER_WORKSPACE`` is used by service managers.  Otherwise the
    current checkout is found from the module path or current directory.  The
    parent-name fallback keeps the existing temporary-workspace tests and
    editable installs working when a marker file is not present.
    """
    override = os.environ.get('DROP_RESTORER_WORKSPACE', '').strip()
    if override:
        return Path(override).expanduser().resolve()
    module = Path(anchor or __file__).resolve()
    derived = next((parent.parent for parent in module.parents if parent.name == 'drop_restorer'), None)
    if derived is not None and (derived / 'pyproject.toml').is_file():
        return derived
    # A temporary test checkout may intentionally omit pyproject.toml.  Keep
    # the path derived from an explicit module anchor in that case, while
    # allowing an installed wheel to find a source checkout from its cwd.
    if derived is not None and not any(part in {'site-packages', 'dist-packages'} for part in derived.parts):
        return derived
    candidates = [Path.cwd().resolve(), *module.parents]
    for candidate in candidates:
        if (candidate / 'pyproject.toml').is_file() and (candidate / 'drop_restorer').is_dir():
            return candidate
    if derived is not None:
        return derived
    return module.parents[1]


def python_executable(workspace: Path) -> Path:
    """Return the interpreter belonging to *workspace* when it exists.

    The Windows launcher prefers ``pythonw.exe`` so a background service does
    not open a console.  Linux and macOS virtual environments expose the
    normal ``bin/python`` executable.  The final fallback keeps direct
    development runs working when a user has not created the project venv yet.
    """
    root = Path(workspace).resolve()
    if os.name == 'nt':
        candidates = (
            root / '.venv-drop-restorer' / 'Scripts' / 'pythonw.exe',
            root / '.venv-drop-restorer' / 'Scripts' / 'python.exe',
        )
    else:
        candidates = (
            root / '.venv-drop-restorer' / 'bin' / 'python',
            root / '.venv-drop-restorer' / 'bin' / 'python3',
        )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return Path(sys.executable).resolve()
