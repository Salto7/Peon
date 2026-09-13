# ai-osint-subsidiaries workflow

The `workflow` command always performs:
1. Rich corporate discovery (Wikidata/Wikipedia/SEC/domain hints) plus the SEC
   subsidiary capability
2. Normalization/deduplication and atomic publication of
   `workspace/corp-entities.json`
3. Validation of that exact canonical file
4. `domain-enum` consuming that file
5. Structured output in `workspace/corporate-recon-result.json`

State is checkpointed at `workspace/raw/corporate-recon/state.json`. Re-running
the same command resumes failed/incomplete work and skips valid completed work.
If discovery fails, is empty, or has no valid domain, domain enumeration is
blocked.

The older `sec`, `prompt`, and `merge-ai` subcommands remain for compatibility,
but agents must use `workflow` for an end-to-end recon objective.

## RoE
Passive public OSINT only. Never invent company domains.
