"""CrewAI integration tests kept independent from external LLM calls."""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from orchestrator.agent.bridges.null import NullAgentBridge
from orchestrator.agent.config import agent_run_config_from_mapping
from orchestrator.agent.job import JobScope
from orchestrator.crew.checkpoint import checkpoint_config, latest_checkpoint
from orchestrator.crew.roles.factory import build_crew_agent
from orchestrator.crew.roles.registry import RoleRegistry
from orchestrator.crew.runtime_support import augment_tool_result


class CrewRuntimeFeatureTests(SimpleTestCase):
    def _scope(self, root: str) -> JobScope:
        return JobScope(
            job_id="job/with unsafe chars",
            project_id="project-1",
            workspace=root,
            bridge=NullAgentBridge(),
        )

    def test_native_checkpoint_config_restores_latest_checkpoint(self):
        with TemporaryDirectory() as root:
            scope = self._scope(root)
            initial = checkpoint_config(scope)
            self.assertIsNotNone(initial)
            self.assertIsNone(initial.restore_from)

            branch = Path(initial.location) / "main"
            branch.mkdir(parents=True)
            older = branch / "older.json"
            newer = branch / "newer.json"
            older.write_text("{}", encoding="utf-8")
            newer.write_text("{}", encoding="utf-8")
            os.utime(older, (1, 1))
            os.utime(newer, (2, 2))

            self.assertEqual(latest_checkpoint(scope), newer)
            restored = checkpoint_config(scope, resume=True)
            self.assertEqual(Path(restored.restore_from), newer)

    def test_runtime_config_enables_native_stability_features(self):
        config = agent_run_config_from_mapping(
            {
                "AGENT_MAX_FAILURE_REPLANS": 3,
                "AGENT_MAX_EXECUTION_SECONDS": 90,
                "AGENT_MEMORY_ENABLED": "true",
                "AGENT_CHECKPOINT_ENABLED": "true",
            }
        )
        self.assertEqual(config.max_failure_replans, 3)
        self.assertEqual(config.max_execution_seconds, 90)
        self.assertTrue(config.memory_enabled)
        self.assertTrue(config.checkpoint_enabled)

    def test_tool_result_is_not_classified_by_string_prefix(self):
        with TemporaryDirectory() as root:
            scope = self._scope(root)
            result = augment_tool_result("Error is valid report content", scope=scope)
        self.assertEqual(result, "Error is valid report content")
        self.assertNotIn("_failure_replans", scope.extras)

    @patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-only"})
    def test_role_factory_uses_native_planning_and_limits(self):
        role = RoleRegistry.shared().require("network-scanner")
        agent = build_crew_agent(
            role,
            max_iterations=7,
            max_replans=2,
            max_execution_time=60,
        )
        self.assertEqual(agent.max_iter, 7)
        self.assertEqual(agent.max_retry_limit, 2)
        self.assertEqual(agent.max_execution_time, 60)
        self.assertEqual(agent.planning_config.max_replans, 2)
        self.assertEqual(agent.planning_config.reasoning_effort, "medium")


class CrewReplanControlTests(SimpleTestCase):
    def test_crewai_replan_keeps_rewritten_objective_result(self):
        from peon.projects import crew_control

        project = SimpleNamespace(status="active")
        planned = {
            "mode": "replan",
            "job_ids": ["job-1"],
            "primary_job_id": "job-1",
            "objectives": 4,
            "plan_preview": "new plan",
        }
        with (
            patch.object(crew_control, "agent_module", return_value="crewai"),
            patch.object(
                crew_control.ProjectLifecycle,
                "replan_from_prompt",
                return_value=planned,
            ) as replan,
            patch.object(crew_control, "set_crew_status") as set_status,
        ):
            result = crew_control.replan_project(project, "change scope handling")

        replan.assert_called_once_with(project, "change scope handling")
        set_status.assert_called_once_with(project, "running")
        self.assertEqual(result["mode"], "crew_replan")
        self.assertEqual(result["objectives"], 4)
