# watchdog tick reference

## Paths
- Tick state / raw samples → `workspace/cache/` (or run dir)
- Alerts / curated hits → `findings/`

## In-script
- `stream("log", …)` for UI
- `run_command()` / `sandbox_run` via RPC if needed
- Prefer **script** mode (no LLM per tick); `mode="agent"` only when reasoning is required each interval

```python
from orchestrator_tools import stream
stream("log", "tick ok")
```
