# analyzer report format

## Report (`findings/report.md`) — embed everything
Must be readable alone. Copy data into the body (no “see file X”):

1. Executive summary
2. Scope / RoE
3. Project-plan coverage (completed / blocked / skipped)
4. Inventories — structured assets from workspace JSON (any asset type: host, URL,
   service, identity, cloud, package, …). Columns follow the evidence; do not force
   a domain/entity schema.
5. Findings by severity with status and inline evidence
6. Gaps / next steps

Mark a reporting objective `completed` if present. Skills queue structured rows via
`record_finding` → `workspace/findings_queue.jsonl` (peon ingests to Finding). Use
DB findings as the primary findings section; phase markdown is supporting detail.

## Hard rules
- No new scanning or out-of-scope access
- No fabricated CVEs, vulns, or assets
- Intermediate phase files may stay on disk; **submit only** `findings/report.md`
