"""AIPOS-339 — Shared kickoff safe-transmission constants.

Single source of truth for the shell-interpretation hazard list used when deciding
whether a kickoff must be written to a temp file (``--append-system-prompt @<file>``)
instead of passed inline (AIPOS-F91: the two former consumers agent_supervise.py /
agent_launch_check.py were retired; agent_materialize's kickoff is checked against it).
"""
from __future__ import annotations

# Shell-interpretation hazards that require safe file-based kickoff transmission.
# If any of these characters/sequences appear in kickoff content, the kickoff
# MUST be written to a temp file and passed via @file syntax to prevent
# shell evaluation.
KICKOFF_HAZARDS: list[str] = ["`", "$(", "${", "\n"]
