"""Project resource garbage collection across disk and runtime backends."""

from __future__ import annotations

from typing import Any

from agent_runtime.docker.engine import DockerEngine
from peon.projects.models import Project
from peon.projects.sandbox import ProjectSandbox
from peon.projects.workspaces import prune_orphan_workspaces, workspace_entry_project_id


class ProjectResourcePruner:
    """Remove resources whose owning Project row no longer exists."""

    @classmethod
    def _runtime_project_ids(cls) -> set[str]:
        cli = DockerEngine.shared()
        project_ids: set[str] = set()
        for container_id in cli.ps_ids("label=peon.project_id"):
            project_id = workspace_entry_project_id(
                str(cli.labels(container_id).get("peon.project_id") or "")
            )
            if project_id:
                project_ids.add(project_id)
        return project_ids

    @classmethod
    def run(cls, *, persist: bool = False) -> dict[str, Any]:
        active = {
            str(project_id)
            for project_id in Project.objects.values_list("id", flat=True)
        }
        workspace_result = prune_orphan_workspaces(
            active,
            persist=persist,
        )
        orphan_project_ids = {
            project_id
            for value in workspace_result["orphans"]
            if (project_id := workspace_entry_project_id(str(value)))
        }
        orphan_runtime_ids = cls._runtime_project_ids() - active
        orphan_project_ids.update(orphan_runtime_ids)

        sandbox_results: dict[str, dict[str, Any]] = {}
        if persist:
            for project_id in sorted(orphan_runtime_ids):
                sandbox_results[project_id] = (
                    ProjectSandbox.remove_all_backends(project_id)
                )

        return {
            "active_project_ids": sorted(active),
            "orphan_project_ids": sorted(orphan_project_ids),
            "workspaces": workspace_result,
            "sandboxes": sandbox_results,
            "persisted": persist,
        }
