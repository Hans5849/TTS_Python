#!/usr/bin/env python3
"""Deprecated filename. The unchanged legacy converter now has a descriptive name."""
from pathlib import Path as _Path
_legacy = _Path(__file__).resolve().parent / "legacy/text_to_speech_v3.py"
# Execute in this module namespace so existing import/monkeypatch behavior survives.
exec(compile(_legacy.read_text(encoding="utf-8"), str(_legacy), "exec"), globals(), globals())
