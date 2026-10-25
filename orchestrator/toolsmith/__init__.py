"""Toolsmith authoring: the ``CodeWritingEngine`` contract, Peon's own engine,
and the prompt/draft helpers both it and ``peon.projects.opencode
.OpenCodeEngine`` share. See ``engine.py`` for why prompts live there and
nowhere else."""

from __future__ import annotations

from orchestrator.toolsmith.engine import CodeWritingEngine
from orchestrator.toolsmith.peon_engine import PeonAuthoring, lint_skill

__all__ = ["CodeWritingEngine", "PeonAuthoring", "lint_skill"]
