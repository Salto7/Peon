---
name: peon-docs-convention
description: Use whenever you finish notable work in this repo (a change, a design doc, a milestone, a checkpoint) to decide whether and where to write it up — docs/changelog for a dated log entry, docs/plans for a longer-lived plan doc.
metadata:
  provider: any
---

# Peon documentation convention

This repo keeps its own history in `docs/`, not only in commit messages or chat transcripts. Two folders, two different jobs:

## `docs/changelog/` — one file per calendar day

- Path: `docs/changelog/YYYY-MM-DD.md` (local date, not UTC-shifted).
- Append-only, point-in-time log. One file per day, all of that day's entries appended to it — don't create a second file for the same day, and don't rewrite yesterday's entry to reflect today's new understanding (log the correction as a new entry instead).
- Log: changes made, docs written, milestones hit, checkpoints reached, decisions taken and why. A short paragraph or bullet list per item is enough — this is a log, not a report.
- If the day's file doesn't exist yet, create it. If it exists, read it first and append rather than overwrite.

## `docs/plans/` — one file per plan, named by topic

- Path: `docs/plans/<descriptive-slug>.md` (e.g. `docs/plans/mcp-runtime-hardening.md`, `docs/plans/artex-port-plan.md`).
- Longer-lived design/implementation documents: architecture proposals, feature plans, multi-step roadmaps, anything meant to be read and updated over the life of the work rather than appended to daily.
- Update the plan doc in place as scope or decisions change; don't fork a new file for a revision of the same plan.
- When a plan doc is created or meaningfully updated, add a short changelog entry for that day pointing at it (`docs/plans/<slug>.md` — one line on what changed) — the changelog is the index of "something happened," the plan doc is the detail.

## Rule of thumb

- Happened today, worth remembering it happened → `docs/changelog/`.
- Worth reading again next week to know what we decided and why → `docs/plans/`.
- Often both: log it today, and if it's substantial, also give it a plan doc and link the two.
