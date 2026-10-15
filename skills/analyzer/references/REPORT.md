# analyzer report format

## Report (`findings/report.md`) — embed everything, use this structure exactly
Must be readable alone. Copy data into the body (no "see file X"):

1. **Executive summary** — a short, non-technical digest: what was tested, what
   was found (counts by severity), overall risk posture.
2. **Findings and observations**
   - **Steps taken and commands executed** — every tool run, in order, with the
     exact command line(s) and an evidence-bearing excerpt of its output.
   - **Findings and their technical details** — every structured finding
     (asset, severity, evidence) with enough technical detail to act on it.
     Include inventories (hosts, URLs, services, identities, …) inline here;
     columns follow the evidence, do not force a fixed schema.
3. **Recommendations** — concrete remediation per finding (or per class of
   finding), plus any gaps/next-steps for a follow-up engagement.

Do not use any other top-level section names or ordering. Mark a reporting
objective `completed` if present. Skills queue structured rows via
`record_finding` → `workspace/findings_queue.jsonl` (peon ingests to Finding).
Use DB findings **and** every concrete row in workspace JSON arrays (for
example `dangling_domains`, verified `domains`, entities). Phase markdown
(`findings/<skill>.md`) is the source for "Steps taken and commands executed".

Findings are **engagement discoveries** about any subject class, with
evidence — not skill/job/objective completion status. `kind` / `asset_type`
are free-form.

## Hard rules
- No new evidence collection outside existing workspace artifacts
- No fabricated discoveries, assets, or identifiers
- Intermediate phase files may stay on disk; **submit only** `findings/report.md`
- **Never collapse a JSON array into a count.** If evidence has
  `dangling_domains: […]` (or similar), the report must list **every** entry
  (domain, kind, evidence) under "Findings and their technical details."
  Saying "N indicators — see artifact" is a failed report.
- Before reading large JSON, check the artifact size from
  `list_workspace_artifacts`. Call
  `read_workspace_artifact(path, max_chars=…)` with `max_chars` at least the
  file size. If the tool returns a truncation notice, re-read with a higher
  limit — truncated JSON drops trailing arrays and produces incomplete reports.
- Batch-record structured Finding rows (`record_findings`) for each security-
  relevant observation (each dangling domain, each confirmed exposure) before
  or while writing the report, so the findings board matches the report body.
- Every command actually executed must appear under "Steps taken and commands
  executed" — do not summarize a run as "ran nmap" without the exact command
  line and output excerpt.
