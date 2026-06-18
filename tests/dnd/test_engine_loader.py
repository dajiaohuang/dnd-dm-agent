"""Tests for the dnd-engine bundled by the default dnd-dm Skill."""

from nanobot.dnd.engine import ENGINE_SOURCE_ID, activate_engine, load_engine_module


def test_bundled_engine_is_importable() -> None:
    source = activate_engine()
    rolls = load_engine_module("dice.rolls")

    assert ENGINE_SOURCE_ID == "dnd-dm-skill/dnd-engine/src/dnd_engine"
    assert (source / "dnd_engine" / "dice" / "rolls.py").is_file()
    assert callable(rolls.rolling)
