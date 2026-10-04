"""CrewAI tools for discovering and executing filesystem skill packs."""

from __future__ import annotations

from orchestrator.crew.tools.decorators import crew_tool
from orchestrator.skills.catalog import filter_skills
from orchestrator.skills.execute import SkillExecutionDispatcher, SkillRunRequest
from orchestrator.skills.registry import SkillRegistry


@crew_tool("skills_list", "List executable skills from the filesystem catalog.")
def skills_list() -> str:
    skills = filter_skills(
        SkillRegistry.shared().get_registry().values(), jobable_only=True
    )
    if not skills:
        return "No executable skills."
    return "\n".join(
        f"{skill.name} [{skill.category or '-'}] — "
        f"{(skill.description or '')[:120]}"
        for skill in sorted(skills, key=lambda item: item.name)
    )


@crew_tool("skill_view", "Read a skill's instructions or one of its reference files.")
def skill_view(name: str, path: str = "") -> str:
    skill = SkillRegistry.shared().load_skill((name or "").strip())
    if skill is None:
        return f"Skill not found: {name!r}"
    return skill.format_view(path=path)


@crew_tool(
    "run_skill_script",
    "Run an executable catalog skill script inside the bound project sandbox.",
)
def run_skill_script(
    skill_name: str, script: str = "scripts/run.py", command: str = ""
) -> str:
    from agent_runtime.api import Session
    from orchestrator.agent.job import get_job

    err = Session.require_bound()
    if err:
        return err
    skill = (skill_name or "").strip()
    path = (script or "scripts/run.py").strip() or "scripts/run.py"
    if not skill:
        return "Error: skill_name required."
    if command:
        blocked = get_job().bridge.assert_command_allowed(command)
        if blocked:
            return f"Error: RoE blocked — {blocked}"
    get_job().bridge.emit(
        "tool",
        f"run_skill_script({skill}, {path})",
        metadata={"skill_name": skill, "script": path},
    )
    result = SkillExecutionDispatcher.shared().run(
        SkillRunRequest(skill_name=skill, script=path, command=command or "")
    )
    output = (result.output or "").strip()
    if len(output) > 12000:
        output = output[:12000] + "\n…(truncated)"
    return f"ok={result.ok} exit={result.exit_code}\n{output}".rstrip()
