"""LLM plan drafting and high-level plan runners."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from orchestrator.planning import (
    JobPlanner,
    ProjectPlanner,
    bookend_role_ids,
    parse_project_objectives,
)
from orchestrator.crew.router import RoleRouter
from orchestrator.utils.llm import chat_model
from peon.projects.models import Project
from peon.projects.planning_types import PlanDraft, PlanResult
from peon.projects.target_shapes import coerce_targets, format_targets
from peon.projects.workspaces import project_workspace_dir


class PlanningDraftMixin:
    """LLM draft + run helpers."""

    @classmethod
    def draft_llm_plan(cls, 
        *,
        description: str,
        mode: str = "project",
        title: str = "adhoc",
        summary: str = "",
        in_scope: list | None = None,
        exclusions: list | None = None,
        authorization: str = "",
        role_ids: list[str] | None = None,
        preferred_tags: list[str] | None = None,
        project: Project | None = None,
    ) -> PlanDraft:
        """Call RoleRouter + planners; return draft (no DB write)."""
        text = (description or "").strip()
        if not text:
            raise ValueError("description is required")

        mode_norm = (mode or "project").strip().lower()
        if mode_norm not in {"project", "job"}:
            raise ValueError("mode must be 'project' or 'job'")

        scope = coerce_targets(in_scope or [])
        excl = coerce_targets(exclusions or [])
        tags = list(preferred_tags or [])
        explicit = list(role_ids or [])
        resolved = RoleRouter.shared().resolve(
            text,
            explicit=explicit or None,
            project=mode_norm == "project",
        )
        prompt = text
        if resolved:
            prompt = f"{text}\n\nPreloaded roles: {', '.join(resolved)}"

        prior_plan = ""
        existing_summary = ""
        if project is not None and mode_norm == "project":
            if not title or title == "adhoc":
                title = project.title or title
            if not summary:
                summary = project.summary or ""
            prior_plan = (
                project.jobs.order_by("-created_at").values_list("plan_text", flat=True).first()
                or ""
            )
            if not prior_plan:
                try:
                    plan_path = (
                        project_workspace_dir(str(project.id), create=False)
                        / "plans"
                        / "latest.md"
                    )
                    if plan_path.is_file():
                        prior_plan = plan_path.read_text(encoding="utf-8", errors="replace")[
                            :4000
                        ]
                except Exception:
                    prior_plan = ""
            lines = []
            for obj in project.objectives.order_by("seq")[:40]:
                lines.append(
                    f"- OBJ-{obj.seq} [{obj.status}] {obj.title} "
                    f"role={obj.role_id or '-'}"
                )
            existing_summary = "\n".join(lines)

        llm = chat_model()
        if mode_norm == "project":
            planner = ProjectPlanner()
            msgs = planner.build_messages(
                prompt,
                project_title=title,
                project_summary=summary,
                roe=SimpleNamespace(
                    in_scope=format_targets(scope),
                    exclusions=format_targets(excl),
                    authorization_note=authorization or "",
                    testing_window_notes="",
                    abort_triggers="",
                ),
                filtered_roles_index=RoleRouter.shared().roles_index_text(),
                focus_tags=tags or None,
                prior_plan=prior_plan,
                existing_objectives_summary=existing_summary,
            )
            raw = planner.message_text(llm.invoke(msgs).content)
            payload = parse_project_objectives(raw)
            plan_text = planner.format_result(raw, project_title=title)
            obj_roles = [
                str(o.get("role_id") or "").strip()
                for o in (payload.get("objectives") or [])
                if str(o.get("role_id") or "").strip()
            ]
            return PlanDraft(
                mode=mode_norm,
                plan_text=plan_text,
                role_ids=bookend_role_ids([*obj_roles, *resolved]),
                objectives_payload=list(payload.get("objectives") or []),
                description=description,
                title=title,
                summary=summary,
                in_scope=scope,
                exclusions=excl,
                authorization=authorization or "",
                preferred_tags=tags,
            )

        planner = JobPlanner()
        msgs = planner.build_messages(prompt)
        plan_text = planner.format_result(planner.message_text(llm.invoke(msgs).content))
        return PlanDraft(
            mode=mode_norm,
            plan_text=plan_text,
            role_ids=resolved,
            description=description,
            title=title,
            preferred_tags=tags,
        )

    @classmethod
    def run_llm_plan(cls, 
        *,
        description: str,
        mode: str = "project",
        title: str = "adhoc",
        summary: str = "",
        in_scope: list | None = None,
        exclusions: list | None = None,
        authorization: str = "",
        role_ids: list[str] | None = None,
        preferred_tags: list[str] | None = None,
        project: Project | None = None,
    ) -> PlanResult:
        """Draft via LLM then persist (enqueue first ready objective for projects)."""
        draft = cls.draft_llm_plan(
            description=description,
            mode=mode,
            title=title,
            summary=summary,
            in_scope=in_scope,
            exclusions=exclusions,
            authorization=authorization,
            role_ids=role_ids,
            preferred_tags=preferred_tags,
            project=project,
        )
        return cls.persist_draft(draft, project=project)

    @classmethod
    def replan_project(cls, project: Project, *, description: str | None = None) -> PlanResult:
        """Operator replan: revise OPPLAN in place, supersede open objectives, start next ready."""
        roe = getattr(project, "roe", None)
        brief = (description or project.summary or project.title or "").strip()
        if not brief:
            raise ValueError("project needs a summary/description to replan")
        in_scope: list = []
        exclusions: list = []
        auth = ""
        if roe is not None:
            # Re-coerce so duplicate values collapse (type remains a soft hint).
            fixed_scope = coerce_targets(roe.in_scope)
            fixed_excl = coerce_targets(roe.exclusions)
            changed = False
            if fixed_scope != list(roe.in_scope or []):
                roe.in_scope = fixed_scope
                changed = True
            if fixed_excl != list(roe.exclusions or []):
                roe.exclusions = fixed_excl
                changed = True
            if changed:
                roe.save(update_fields=["in_scope", "exclusions", "updated_at"])
            in_scope = list(roe.in_scope or [])
            exclusions = list(roe.exclusions or [])
            auth = (roe.authorization_note or "")
        return cls.run_llm_plan(
            description=brief,
            mode="project",
            title=project.title,
            summary=project.summary,
            in_scope=in_scope,
            exclusions=exclusions,
            authorization=auth,
            preferred_tags=list(project.focus_tags or []),
            project=project,
        )

