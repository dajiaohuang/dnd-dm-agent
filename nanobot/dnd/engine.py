"""Loader for the dnd-engine bundled by the always-active dnd-dm Skill."""

from __future__ import annotations

import importlib
import sys
from importlib.resources import files as package_files
from pathlib import Path
from types import ModuleType

ENGINE_SOURCE_ID = "dnd-dm-skill/dnd-engine/src/dnd_engine"


def engine_source_path() -> Path:
    """Return the installed source root containing the ``dnd_engine`` package."""
    source = package_files("nanobot").joinpath("skills", "dnd-dm", "dnd-engine", "src")
    return Path(str(source)).resolve()


def activate_engine() -> Path:
    """Make the bundled engine importable and return its source root."""
    source = engine_source_path()
    package = source / "dnd_engine"
    if not package.is_dir():
        raise RuntimeError(f"bundled dnd_engine is missing at {package}")
    source_text = str(source)
    if source_text not in sys.path:
        sys.path.insert(0, source_text)
    return source


def load_engine_module(module: str) -> ModuleType:
    """Import a module below ``dnd_engine`` from the bundled Skill."""
    activate_engine()
    qualified = module if module == "dnd_engine" or module.startswith("dnd_engine.") else (
        f"dnd_engine.{module}"
    )
    return importlib.import_module(qualified)
