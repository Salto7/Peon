"""Minimal LangGraph: gate → act ↔ tools with iteration caps."""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from orchestrator.agent.config import AgentRunConfig
from orchestrator.agent.context import AgentRunContext, bind_job
from orchestrator.agent.policy import resolve_tool_names
from orchestrator.capabilities.registry import get_tools_for_names
from orchestrator.skills.misc.registry import SkillRegistry
from orchestrator.tools.llm_tools import ensure_tools_registered
from orchestrator.utils.llm import chat_model


def build_system_prompt(*, skill_names: list[str], brief: str) -> str:
    reg = SkillRegistry.shared()
    blocks: list[str] = [
        "You are a Peon job agent for an authorized engagement.",
        "Use only the bound tools. Prefer run_skill_script for catalog skills.",
        "Call provision_cli before missing CLIs. Never assume host access.",
        "Do not search the filesystem for skill scripts — invoke them via run_skill_script.",
        "Stay in-scope; do not expand RoE. Be concise.",
        "Obey OPERATOR INSTRUCTION / FOLLOW-UP messages when they appear.",
    ]
    if brief.strip():
        blocks.append("Job brief:\n" + brief.strip()[:4000])
    for name in skill_names or []:
        skill = reg.load_skill(name)
        if skill is None:
            blocks.append(f"Skill {name!r}: (not in registry)")
            continue
        body = (skill.instructions or skill.description or "").strip()[:2500]
        blocks.append(f"## Skill: {skill.name}\n{body}")
    return "\n\n".join(blocks)


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    iterations: int
    failure_replans: int


def build_agent_graph(ctx: AgentRunContext, config: AgentRunConfig):
    ensure_tools_registered()
    names = resolve_tool_names(ctx.skill_names)
    tools = get_tools_for_names(names)
    llm = chat_model()
    if tools:
        llm = llm.bind_tools(tools)
    tool_node = ToolNode(tools) if tools else None
    system = build_system_prompt(skill_names=ctx.skill_names, brief=ctx.brief)
    max_iter = config.max_iterations
    max_replans = config.max_failure_replans

    def gate(state: AgentState) -> dict[str, Any]:
        """Pull operator guidance from peon ports before each act cycle."""
        del state
        try:
            notes = list(ctx.ports.drain_operator_guidance() or [])
        except Exception as exc:
            ctx.ports.emit("error", f"operator guidance drain failed: {exc}")
            return {}
        notes = [n.strip() for n in notes if str(n or "").strip()]
        if not notes:
            return {}
        text = "\n\n".join(notes)
        ctx.ports.emit(
            "log",
            text[:2000],
            metadata={"event": "operator_guidance", "role": "assistant"},
        )
        return {"messages": [HumanMessage(content=text[:8000])]}

    def act(state: AgentState) -> dict[str, Any]:
        msgs = list(state.get("messages") or [])
        if not msgs or not isinstance(msgs[0], SystemMessage):
            msgs = [SystemMessage(content=system), *msgs]
        resp = llm.invoke(msgs)
        return {
            "messages": [resp],
            "iterations": int(state.get("iterations") or 0) + 1,
        }

    def after_tools(state: AgentState) -> dict[str, Any]:
        """Optional recovery nudge when the last tool output looks like an error."""
        replans = int(state.get("failure_replans") or 0)
        if replans >= max_replans:
            return {}
        msgs = state.get("messages") or []
        last = msgs[-1] if msgs else None
        content = str(getattr(last, "content", "") or "")
        if not content.lower().startswith("error"):
            return {}
        nudge = HumanMessage(
            content=(
                "The last tool call failed. Adjust arguments or provision a missing CLI, "
                "then retry. Do not repeat the identical failing call."
            )
        )
        return {"messages": [nudge], "failure_replans": replans + 1}

    def route(state: AgentState) -> Literal["tools", "end"]:
        if int(state.get("iterations") or 0) >= max_iter:
            return "end"
        msgs = state.get("messages") or []
        last = msgs[-1] if msgs else None
        if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
            return "tools"
        return "end"

    graph = StateGraph(AgentState)
    graph.add_node("gate", gate)
    graph.add_node("agent", act)
    graph.set_entry_point("gate")
    graph.add_edge("gate", "agent")
    if tool_node is not None:
        graph.add_node("tools", tool_node)
        graph.add_node("recover", after_tools)
        graph.add_conditional_edges("agent", route, {"tools": "tools", "end": END})
        graph.add_edge("tools", "recover")
        # Re-enter gate so mid-run operator chat is applied before the next act.
        graph.add_edge("recover", "gate")
    else:
        graph.add_edge("agent", END)
    return graph.compile()


def invoke_agent(ctx: AgentRunContext, config: AgentRunConfig) -> tuple[bool, str, int]:
    """Run the graph under job context. Returns (ok, output, iterations)."""
    compiled = build_agent_graph(ctx, config)
    human = (ctx.brief or f"Execute skills: {', '.join(ctx.skill_names)}").strip()
    with bind_job(ctx, config):
        final = compiled.invoke(
            {
                "messages": [HumanMessage(content=human[:8000])],
                "iterations": 0,
                "failure_replans": 0,
            },
            config={"recursion_limit": max(10, config.max_iterations * 2 + 5)},
        )
    iterations = int(final.get("iterations") or 0)
    msgs = final.get("messages") or []
    snippets: list[str] = []
    for m in msgs[-8:]:
        content = getattr(m, "content", None)
        if isinstance(content, str) and content.strip():
            snippets.append(content.strip()[:2000])
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("text"):
                    snippets.append(str(part["text"])[:2000])
    text = "\n\n".join(snippets) if snippets else "(no agent text)"
    ok = iterations > 0
    return ok, text, iterations
