"""Orchestrator library — planning, agent loop, sandbox (no Django).

Package map:
- ``agent`` — Job scope / bridge DTOs + runtime ABC
- ``crew`` — CrewAI role + project crew runtimes (AGENT_MODULE=crewai)
- ``sandbox`` — Docker session + shell runner
- ``tools`` — CLI catalog YAML + install resolver
- ``capabilities`` — shared tool registry (catalog / toolsmith; CrewAI uses crew.tools)
- ``mcp`` — parse MCP specs and run them inside the project sandbox
- ``utils`` — shared helpers (llm, stream, job_env, workspace, paths, …)
- ``planning`` / ``toolsmith`` — planners, role authoring
"""
