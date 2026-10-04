"""Orchestrator library — skills, planning, agent loop, sandbox (no Django).

Package map:
- ``agent`` — Job scope / bridge DTOs + runtime ABC
- ``crew`` — CrewAI role + project crew runtimes (AGENT_MODULE=crewai)
- ``skills`` — Skill model, registry, catalog, execute, provision
- ``sandbox`` — Docker session + shell runner
- ``tools`` — CLI catalog YAML + install resolver
- ``capabilities`` — shared tool registry (catalog / learn; CrewAI uses crew.tools)
- ``rpc`` — host Unix-socket RPC server (sandbox → control plane)
- ``utils`` — shared helpers (llm, stream, job_env, workspace, paths, …)
- ``planning`` / ``learn`` — planners, skill authoring
"""
