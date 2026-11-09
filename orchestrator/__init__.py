"""Orchestrator library — planning, agent loop, sandbox (no Django).

Package map:
- ``agent`` — Job scope, bridge, backend registry (AGENT_MODULE=crewai)
- ``crew`` — roles, tools, router, memory (CrewAI execution is an agent backend)
- ``sandbox`` — Docker session + shell runner
- ``tools`` — CLI catalog YAML + install resolver
- ``mcp`` — parse MCP specs and run them inside the project sandbox
- ``utils`` — shared helpers (llm, stream, job_env, workspace, paths, …)
- ``planning`` / ``toolsmith`` — planners, role authoring
"""
