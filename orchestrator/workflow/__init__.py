"""Preset workflows: the designer graph, its validation, its compilation into a runnable plan, and the
LLM-assisted generator. Nothing here runs a job — the compiled plan is handed to the objective scheduler."""

from __future__ import annotations

from orchestrator.workflow.compiler import CompiledPlan, WorkflowCompileError, compile_workflow
from orchestrator.workflow.generator import deduce_workflow_from_project, generate_workflow
from orchestrator.workflow.graph import auto_layout
from orchestrator.workflow.linter import WorkflowLinter

__all__ = [
    "CompiledPlan",
    "WorkflowCompileError",
    "WorkflowLinter",
    "auto_layout",
    "compile_workflow",
    "deduce_workflow_from_project",
    "generate_workflow",
]
