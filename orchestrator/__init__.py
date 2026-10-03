"""Orchestrator library — skills, planning, agent loop, sandbox (no Django).

Package map:
- ``agent`` — one Job LangGraph loop + control-plane bridge
- ``skills`` — Skill model, registry, catalog, execute, provision
- ``sandbox`` — Docker session + shell runner
- ``tools`` — CLI catalog YAML + install resolver
- ``capabilities`` — LangChain tools bound per Job
- ``rpc`` — host Unix-socket RPC server (sandbox → control plane)
- ``utils`` — shared helpers (llm, stream, job_env, workspace, paths, …)
- ``planning`` / ``learn`` — planners, skill authoring
"""
