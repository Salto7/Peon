---
name: ai-osint-subsidiaries
description: Ordered, resumable corporate discovery and domain enumeration. Atomically validates workspace/corp-entities.json before domain-enum can run. Use for ordered corporate discovery before domain enumeration.
allowed-tools: sandbox_setup run_skill_script skills_list skill_view list_objectives update_objective_status record_finding record_findings list_findings
metadata:
  version: 5.0.0
  category: recon
  tags: OSINT
  jobable: 'true'
  manually_created: 'true'
  lifecycle: short
---

## Mandatory entry point

Run the cohesive state graph via scripts/run.py — do not invoke `sec`, `prompt`,
`merge-ai`, or `domain-enum` manually:
```
run_skill_script("ai-osint-subsidiaries", "scripts/run.py", command='workflow "ACME" --workspace workspace')
```
Optional identity hints:
```
run_skill_script("ai-osint-subsidiaries", "scripts/run.py", command='workflow "ACME" --ticker EXM --cik 123456 --qid Q123 --workspace workspace')
```

## Scripts
- scripts/run.py — thin entry (`skill_entry.run_cli`)
- scripts/cli.py — workflow / sec / prompt / merge-ai implementation

See [workflow reference](references/REFERENCE.md) for stages, checkpoints, and RoE.
