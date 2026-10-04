from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from orchestrator.agent import JobScope
from orchestrator.crew.flows.engagement import build_engagement_crew
from orchestrator.crew.runtimes.job_crewai import project_crew_requested
from orchestrator.planning import bookend_project_objectives
from peon.projects.console_chat import (
    _do_instruct,
    _fallback_intent,
    handle_console_chat,
)
from peon.projects.findings import FindingStore
from peon.projects.lifecycle import ProjectLifecycle
from peon.projects.models import (
    Job,
    JobDirective,
    JobDirectiveKind,
    JobStatus,
    Objective,
    ObjectiveStatus,
    Project,
    ProjectStatus,
)
from peon.projects.planning_persist import validate_project_objectives


class PlanningValidationTests(SimpleTestCase):
    def test_empty_plan_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "no objectives"):
            validate_project_objectives([])

    def test_unknown_role_is_rejected(self):
        rows = bookend_project_objectives(
            [
                {
                    "title": "Invented work",
                    "role_id": "not-a-real-role",
                    "phase": "recon",
                }
            ]
        )
        with self.assertRaisesRegex(ValueError, "unknown CrewAI role"):
            validate_project_objectives(rows)

    def test_bookended_known_role_plan_is_valid(self):
        rows = bookend_project_objectives(
            [
                {
                    "title": "Scan authorized target",
                    "role_id": "network-scanner",
                    "phase": "recon",
                }
            ]
        )
        validate_project_objectives(rows)


class CrewConfigurationTests(SimpleTestCase):
    def test_manager_role_does_not_implicitly_expand_to_project_crew(self):
        scope = JobScope(job_id="manager", role_ids=["project-manager"])
        self.assertFalse(project_crew_requested(scope))

    def test_explicit_project_mode_expands_to_project_crew(self):
        scope = JobScope(
            job_id="manager",
            role_ids=["project-manager"],
            extras={"crew_mode": "project"},
        )
        self.assertTrue(project_crew_requested(scope))

    @patch("crewai.Crew", side_effect=lambda **kwargs: kwargs)
    @patch("crewai.Task", side_effect=lambda **kwargs: kwargs)
    @patch(
        "orchestrator.crew.flows.engagement.build_crew_agent",
        side_effect=lambda role, **kwargs: {"id": role.id, **kwargs},
    )
    @patch(
        "orchestrator.crew.flows.engagement.llm_id_for_crew",
        return_value="openrouter/openai/test-model",
    )
    def test_planning_uses_configured_llm(
        self, llm_id, build_agent, task, crew
    ):
        result = build_engagement_crew(
            brief="scan the authorized target",
            role_ids=["network-scanner"],
        )

        # Manager has advanced_reasoning → crew-level planning enabled.
        self.assertTrue(result.get("planning"))
        self.assertEqual(result.get("planning_llm"), "openrouter/openai/test-model")
        llm_id.assert_called_once_with()
        self.assertGreaterEqual(build_agent.call_count, 3)
        manager_calls = [
            call
            for call in build_agent.call_args_list
            if call.args[0].id == "project-manager"
        ]
        self.assertEqual(len(manager_calls), 1)
        self.assertEqual(manager_calls[0].kwargs, {"tools": []})
        self.assertNotIn("agent", task.call_args_list[0].kwargs)


class FindingStoreTests(TestCase):
    def test_store_requires_title_and_dedupes(self):
        project = Project.objects.create(title="findings")
        store = FindingStore()
        self.assertIsNone(store.record(project, {"title": ""}))
        first = store.record(
            project,
            {
                "title": "Open port 443/tcp",
                "kind": "open_port",
                "host": "192.168.10.1",
                "port": 443,
                "evidence_path": "workspace/scan.nmap",
            },
        )
        second = store.record(
            project,
            {
                "title": "Open port 443/tcp",
                "kind": "open_port",
                "host": "192.168.10.1",
                "port": 443,
                "evidence_path": "workspace/scan.nmap",
            },
        )
        self.assertIsNotNone(first)
        self.assertEqual(first.id, second.id)
        self.assertEqual(project.findings.count(), 1)


class ManagerBookendTests(TestCase):
    def test_initial_manager_objective_skips_llm(self):
        from peon.projects import job_run

        project = Project.objects.create(title="mgr-bookend", status=ProjectStatus.ACTIVE)
        obj = Objective.objects.create(
            project=project,
            seq=1,
            title="Project manager — review and dispatch",
            role_id="project-manager",
            status=ObjectiveStatus.PENDING,
        )
        job = Job.objects.create(
            project=project,
            objective=obj,
            title="PM",
            role_ids=["project-manager"],
            status=JobStatus.RUNNING,
        )

        with patch.object(job_run, "run_job_via_agent") as via_agent:
            out = job_run.run_job(job)

        via_agent.assert_not_called()
        out.refresh_from_db()
        self.assertEqual(out.status, JobStatus.COMPLETED)
        obj.refresh_from_db()
        self.assertEqual(obj.status, ObjectiveStatus.COMPLETED)

    def test_manager_with_steer_uses_llm_path(self):
        from peon.projects import job_run

        project = Project.objects.create(title="mgr-steer", status=ProjectStatus.ACTIVE)
        obj = Objective.objects.create(
            project=project,
            seq=1,
            title="Project manager",
            role_id="project-manager",
            status=ObjectiveStatus.PENDING,
        )
        job = Job.objects.create(
            project=project,
            objective=obj,
            title="PM",
            role_ids=["project-manager"],
            status=JobStatus.RUNNING,
        )
        JobDirective.objects.create(
            job=job, kind=JobDirectiveKind.STEER, content="REPLAN: expand ports"
        )

        with (
            patch.object(job_run, "run_job_via_agent") as via_agent,
            patch.object(job_run.ProjectSandbox, "provision") as provision,
            patch.object(job_run, "_bind_job_env", return_value=object()),
            patch.object(job_run.JobEnv, "reset"),
            patch.object(job_run.Session, "reset"),
        ):
            provision.return_value = type(
                "SB", (), {"mode": "docker", "name": "x", "action": "reuse", "base_commands": []}
            )()
            via_agent.return_value = job
            job_run.run_job(job)

        via_agent.assert_called_once()


class JobDispatchTests(TestCase):
    @patch("peon.projects.job_run.run_job")
    def test_duplicate_delivery_does_not_rerun_running_job(self, run_job):
        job = Job.objects.create(
            title="already claimed",
            status=JobStatus.RUNNING,
            role_ids=["network-scanner"],
        )

        from peon.projects.tasks import process_job

        process_job.fn(str(job.id))

        run_job.assert_not_called()

    def test_terminal_project_marks_running_crew_done(self):
        project = Project.objects.create(title="crew", crew_status="running")
        Job.objects.create(
            title="completed objective",
            status=JobStatus.COMPLETED,
            role_ids=["analyzer"],
            project=project,
        )

        ProjectLifecycle.reconcile_project_status(project)

        project.refresh_from_db()
        self.assertEqual(project.status, ProjectStatus.FINISHED)
        self.assertEqual(project.crew_status, "done")


class ConsoleChatRoutingTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(title="chat routing")

    def test_fallback_replans_when_no_agent_is_live(self):
        self.assertEqual(_fallback_intent(self.project, "prioritize port 443"), "replan")

    def test_fallback_instructs_when_an_agent_is_live(self):
        Job.objects.create(
            title="manager",
            status=JobStatus.RUNNING,
            role_ids=["project-manager"],
            project=self.project,
        )
        self.assertEqual(
            _fallback_intent(self.project, "prioritize port 443"),
            "instruct",
        )

    @patch("peon.projects.console_chat._do_instruct")
    @patch("peon.projects.console_chat._classify", return_value="instruct")
    def test_default_chat_can_instruct_live_agents(self, classify, instruct):
        instruct.return_value = {"ok": True, "mode": "instruct", "job_ids": []}

        result = handle_console_chat(self.project, "focus on TLS first")

        classify.assert_called_once_with(self.project, "focus on TLS first")
        instruct.assert_called_once_with(
            self.project, "focus on TLS first", job_id=None
        )
        self.assertEqual(result["mode"], "instruct")

    @patch("peon.projects.console_intents._do_replan")
    def test_explicit_instruction_replans_if_no_agent_is_live(self, replan):
        replan.return_value = {"ok": True, "mode": "replan"}

        result = _do_instruct(self.project, "also scan port 443")

        replan.assert_called_once_with(self.project, "also scan port 443")
        self.assertEqual(result["mode"], "replan")


class OperatorControlFacadeTests(TestCase):
    def test_console_stop_sets_crew_status_paused(self):
        from peon.projects.console_chat import _do_stop

        project = Project.objects.create(
            title="stop crew",
            status=ProjectStatus.ACTIVE,
            crew_status="running",
        )
        result = _do_stop(project, "stop everything")
        project.refresh_from_db()
        self.assertEqual(project.status, ProjectStatus.PAUSED)
        self.assertEqual(project.crew_status, "paused")
        self.assertEqual(result["crew_status"], "paused")

    @patch("peon.projects.crew_control.enqueue_job")
    @patch("peon.projects.crew_control.agent_module", return_value="crewai")
    def test_replan_sets_crew_status_running(self, _module, _enqueue):
        from peon.projects.crew_control import replan_project

        project = Project.objects.create(
            title="replan crew",
            status=ProjectStatus.ACTIVE,
            crew_status="paused",
        )
        result = replan_project(project, "focus on TLS")
        project.refresh_from_db()
        self.assertEqual(project.crew_status, "running")
        self.assertEqual(result["mode"], "crew_replan")
        self.assertTrue(result["job_ids"])


class RoleJobTreeTests(TestCase):
    def test_ops_agents_tree_uses_role_job_tree(self):
        from peon.projects.project_status import ProjectOpsPayload
        from peon.projects.role_job_tree import RoleJobTree

        project = Project.objects.create(title="tree")
        manager = Job.objects.create(
            title="manager",
            status=JobStatus.RUNNING,
            role_ids=["project-manager"],
            project=project,
        )
        scanner = Job.objects.create(
            title="scan",
            status=JobStatus.PENDING,
            role_ids=["network-scanner"],
            project=project,
        )
        jobs = [manager, scanner]
        roots, children = RoleJobTree.build_job_hierarchy(jobs)
        tree = ProjectOpsPayload.agents_tree(jobs)

        self.assertEqual(len(roots), 1)
        self.assertEqual(roots[0].id, manager.id)
        self.assertEqual([j.id for j in children[str(manager.id)]], [scanner.id])
        self.assertEqual(len(tree), 1)
        self.assertEqual(tree[0]["id"], str(manager.id))
        self.assertEqual(tree[0]["subagents"][0]["id"], str(scanner.id))


class SharedToolBodyTests(SimpleTestCase):
    @patch("orchestrator.capabilities.tools.ShellRunner.shared")
    @patch("orchestrator.capabilities.tools.emit")
    @patch("orchestrator.capabilities.tools.get_job")
    @patch("orchestrator.capabilities.tools.require_bound", return_value=None)
    def test_run_cli_applies_roe_gate(self, require_bound, get_job, emit, shell_shared):
        from orchestrator.capabilities.tools import run_cli_body

        bridge = type("B", (), {})()
        bridge.assert_command_allowed = lambda cmd: "out of scope"
        get_job.return_value = type("J", (), {"bridge": bridge})()

        out = run_cli_body("nmap 10.0.0.1")

        self.assertIn("RoE blocked", out)
        shell_shared.assert_not_called()
        emit.assert_not_called()
        require_bound.assert_called_once_with()

    @patch("orchestrator.capabilities.tools.ShellRunner.shared")
    @patch("orchestrator.capabilities.tools.emit")
    @patch("orchestrator.capabilities.tools.get_job")
    @patch("orchestrator.capabilities.tools.require_bound", return_value=None)
    def test_run_cli_emits_structured_envelope(
        self, require_bound, get_job, emit, shell_shared
    ):
        from orchestrator.capabilities.tools import run_cli_body
        from orchestrator.utils.stream_events import (
            EVENT_RUN_CLI,
            event_of,
            payload_command,
            payload_hosts,
        )

        shell_shared.return_value.run_shell.return_value = 0
        bridge = type("B", (), {})()
        bridge.assert_command_allowed = lambda cmd: ""
        bridge.duplicate_scan_reason = lambda cmd: ""
        get_job.return_value = type("J", (), {"bridge": bridge})()

        out = run_cli_body("nmap -sV 10.0.0.1")

        self.assertEqual(out, "exit=0")
        emit.assert_called_once()
        args = emit.call_args.args
        meta = dict(emit.call_args.kwargs)
        self.assertEqual(args[0], "tool")
        self.assertEqual(event_of(meta), EVENT_RUN_CLI)
        self.assertEqual(payload_command(meta), "nmap -sV 10.0.0.1")
        self.assertIn("10.0.0.1", payload_hosts(meta))
        require_bound.assert_called_once_with()


class StreamEventsTests(SimpleTestCase):

    def test_envelope_getters(self):
        from orchestrator.utils.stream_events import (
            display_command,
            envelope,
            event_of,
            hosts_in_command,
            is_event,
            payload_cli,
            payload_command,
            payload_hosts,
        )

        meta = envelope(
            "run_cli",
            {"command": "nmap 10.0.0.1", "cli": "nmap", "hosts": ["10.0.0.1"]},
            tag="shell",
        )
        self.assertEqual(event_of(meta), "run_cli")
        self.assertTrue(is_event(meta, "run_cli"))
        self.assertEqual(payload_command(meta), "nmap 10.0.0.1")
        self.assertEqual(payload_cli(meta), "nmap")
        self.assertEqual(payload_hosts(meta), ["10.0.0.1"])
        self.assertEqual(display_command(meta, "ignored"), "nmap 10.0.0.1")
        self.assertEqual(display_command({}, "fallback text"), "fallback text")
        # Path-embedded hosts are not probe targets; bare IPs / URLs are.
        self.assertEqual(hosts_in_command("cat workspace/10.0.0.1.xml"), [])
        self.assertEqual(
            hosts_in_command("nmap -oX workspace/scan.xml 10.0.0.1"), ["10.0.0.1"]
        )
        self.assertEqual(hosts_in_command("curl https://10.0.0.1/path"), ["10.0.0.1"])


class WorkspaceArtifactTests(SimpleTestCase):
    def test_format_index_lists_prior_agent_files(self):
        import tempfile
        from pathlib import Path

        from peon.projects.workspaces import format_workspace_artifact_index

        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / "workspace").mkdir()
            (ws / "workspace" / "scan.xml").write_text("<root/>", encoding="utf-8")
            (ws / "workspace" / "notes.json").write_text("[]", encoding="utf-8")
            text = format_workspace_artifact_index(ws)
        self.assertIn("workspace/scan.xml", text)
        self.assertIn("workspace/notes.json", text)


class RoleReasoningFlagTests(SimpleTestCase):
    def test_manager_has_advanced_reasoning_and_objective_tools(self):
        from orchestrator.crew.roles.registry import RoleRegistry

        mgr = RoleRegistry.shared().require("project-manager")
        # Agent reasoning off (slow); crew-level planning via advanced_reasoning.
        self.assertFalse(mgr.reasoning)
        self.assertTrue(mgr.advanced_reasoning)
        self.assertIn("list_objectives", mgr.tools)
        self.assertIn("update_objective_status", mgr.tools)
        self.assertNotIn("assert_in_scope", mgr.tools)

    def test_specialists_disable_reasoning(self):
        from orchestrator.crew.roles.registry import RoleRegistry

        scanner = RoleRegistry.shared().require("network-scanner")
        self.assertFalse(scanner.reasoning)
        self.assertFalse(scanner.advanced_reasoning)


class RoleDraftLintTests(SimpleTestCase):
    def test_lint_role_pack_rejects_bad_id(self):
        from orchestrator.learn.role_draft import lint_role_pack

        out = lint_role_pack(
            name="Bad Name",
            role_yaml="id: bad\ngoal: x\ncrew_role: X\n",
        )
        self.assertFalse(out["compatible"])


class RoleAuthoringBindingTests(SimpleTestCase):
    def test_code_writer_owns_authoring_engine_and_prompts(self):
        from orchestrator.crew.roles.registry import RoleRegistry

        role = RoleRegistry.shared().require("code-writer")
        self.assertEqual(role.authoring_engine, "opencode")
        self.assertTrue(role.authoring_bootstrap)
        tool_prompt = role.authoring_prompt_text("tool")
        role_prompt = role.authoring_prompt_text("role")
        self.assertIn("catalog", tool_prompt.lower())
        self.assertIn("ROLE.yaml", role_prompt)

    def test_role_authoring_resolves_learn_role(self):
        from peon.projects.role_authoring import RoleAuthoring

        role = RoleAuthoring.shared().authoring_role()
        self.assertEqual(role.id, "code-writer")
