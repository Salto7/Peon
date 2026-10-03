"""LangGraph StateGraph for one Job: gate → act ↔ tools with iteration caps."""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from orchestrator.agent.config import AgentRunConfig
from orchestrator.agent.job import JobScope
from orchestrator.capabilities.registry import (
    ensure_registered,
    get_tools_for_names,
    resolve_tool_names,
)
from orchestrator.config import get_config
from orchestrator.prompts import AGENT_RECOVER_NUDGE
from orchestrator.skills.registry import SkillRegistry


def build_system_prompt(*, skill_names: list[str], brief: str) -> str:
    reg = SkillRegistry.shared()
    blocks: list[str] = [get_config().system_preamble.strip()]
    if brief.strip():
        blocks.append("Task brief:\n" + brief.strip()[:4000])
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


def build_agent_graph(
    scope: JobScope,
    config: AgentRunConfig,
    *,
    checkpointer: Any | None = None,
):
    ensure_registered()
    names = resolve_tool_names(scope.skill_names)
    tools = get_tools_for_names(names)
    llm = chat_model_bound(tools)
    tool_node = ToolNode(tools) if tools else None
    system = build_system_prompt(skill_names=scope.skill_names, brief=scope.brief)
    max_iter = config.max_iterations
    max_replans = config.max_failure_replans

    def gate(state: AgentState) -> dict[str, Any]:
        """Pull operator guidance + peer inbox before each act cycle."""
        del state
        notes: list[str] = []
        try:
            notes.extend(list(scope.bridge.drain_operator_guidance() or []))
        except Exception as exc:
            scope.bridge.emit("error", f"operator guidance drain failed: {exc}")
        try:
            notes.extend(list(scope.bridge.drain_peer_messages() or []))
        except Exception as exc:
            scope.bridge.emit("error", f"peer message drain failed: {exc}")
        notes = [n.strip() for n in notes if str(n or "").strip()]
        if not notes:
            return {}
        text = "\n\n".join(notes)
        scope.bridge.emit(
            "log",
            text[:2000],
            metadata={"event": "agent_inbox", "role": "assistant"},
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
        replans = int(state.get("failure_replans") or 0)
        if replans >= max_replans:
            return {}
        msgs = state.get("messages") or []
        last = msgs[-1] if msgs else None
        content = str(getattr(last, "content", "") or "")
        if not content.lower().startswith("error"):
            return {}
        return {
            "messages": [HumanMessage(content=AGENT_RECOVER_NUDGE)],
            "failure_replans": replans + 1,
        }

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
        graph.add_edge("recover", "gate")
    else:
        graph.add_edge("agent", END)
    return graph.compile(checkpointer=checkpointer)


def chat_model_bound(tools: list[Any]):
    from orchestrator.utils.llm import chat_model

    llm = chat_model()
    return llm.bind_tools(tools) if tools else llm


def extract_output_text(final: dict[str, Any]) -> str:
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
    return "\n\n".join(snippets) if snippets else "(no agent text)"
