---
name: network-service-recon
description: NetExec (nxc) network service recon on in-scope hosts after ports/services are known. Prefer nxc modules for every protocol it supports (SMB, LDAP, WinRM, RDP, MSSQL, SSH, FTP, VNC, …). Skill worker under recon-specialist (skill_ids=network-service-recon). Not for OSINT or full web app testing.
allowed-tools: Bash Read
compatibility: Requires nxc
---

## Goal
**Network service recon** with **NetExec** (`nxc`) against authorized hosts.
Drive enums through `nxc <protocol> …` for each in-scope service NetExec
supports — do not fall back to one-off ldapsearch/smbclient unless nxc cannot
cover the brief. Run as a **skill worker under the parent expert**.

## Placement
Spawned by `recon-specialist` via
`spawn_work(..., skill_ids="network-service-recon")` with a focused brief and
`scope_slice` (hosts / ports / protocols). Obey RoE.

## Run
```
bash("nxc smb <host> -u '' -p '' --shares")
bash("nxc ldap <host> -u '' -p '' --naming-contexts")
```

## Boundaries
Host/port discovery → `recon-specialist` / skill `network-scanner`. HTTP app mapping → `http-prober` / `web`.
Final report → `analyzer`. RoE conflicts → parent / `ask_operator`.

## Collaboration & memory
- Before repeating a method, tool, or target, `recall_memory`; `save_memory` for durable lessons a later agent would otherwise rediscover.
- Report up: `send_message` findings, blockers, and scope changes to your parent / role agent — blockers and scope/RoE changes reach the governance PM.
- Stream, don't hoard: as you discover something a waiting agent needs, `list_team` then `send_message` it (to one agent, by its id) so it can start while you continue — batch per subject, do not wait for completion.
- `check_inbox` between steps; when you can only wait on another agent's output, `go_idle` (never poll).
