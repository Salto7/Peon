# AdStrike and AdStrike Agent Guide

Version: AdStrike v5.0 "AdStrike"  
Audience: authorised red team operators, penetration testers, lab users, and maintainers  
Scope: AdStrike framework, shared session model, module map, AI Agent internals, workflows, troubleshooting, and extension guidance

> Use AdStrike only in environments where you have explicit permission to test. The tool automates offensive Active Directory techniques and can cause account lockouts, service disruption, or domain compromise if used outside an authorised engagement.

## 1. What AdStrike Is

AdStrike is a terminal-based Active Directory attack framework for authorised security testing. It wraps common AD offensive workflows into a shared session model so operators can move from discovery to exploitation without manually copying credentials, hashes, tickets, target data, and findings between tools.

The framework has three operating modes:

- Interactive modules: the operator chooses a numbered module from the menu and drives the workflow manually.
- AdStrike Agent: an AI-assisted orchestrator that chooses and chains framework tools based on current evidence (Ollama local model, or Claude via `ANTHROPIC_API_KEY`).
- MCP server (no API key): AdStrike runs no LLM of its own and instead exposes its tools over the Model Context Protocol, so an MCP host you already use (Claude Code, Cursor, Claude Desktop) drives the engagement with its own subscription. See section 30. Full walkthrough: [docs/mcp.md](mcp.md).

The tool is designed for lab and engagement work where many target environments differ. It should not assume one specific machine, one specific domain, one hard-coded gMSA name, or one fixed exploitation chain. Instead it tracks evidence, validates prerequisites, and pivots when a path fails.

## 2. Design Goals

AdStrike is built around these goals:

- Preserve state across modules: target, domain, user, password, NT hash, Kerberos cache, findings, owned users, owned machines, and loot.
- Make Kerberos and NTLM workflows usable in modern AD labs where NTLM may be restricted.
- Chain common attack primitives without losing context.
- Keep findings reportable by storing evidence and remediation text.
- Support both manual operator control and AI-guided automation.
- Avoid target-specific assumptions; all logic should generalise across AD machines and labs.
- Detect dead paths and avoid repeating failing actions in loops.

## 3. Repository Layout

Important paths:

| Path | Purpose |
|---|---|
| `main.py` | Main menu, phase registry, module dispatch, banner, dashboard |
| `config/settings.py` | Global `SESSION`, `.env` loading, redaction, Kerberos helpers |
| `modules/` | Interactive attack modules and shared framework modules |
| `modules/red_team_agent.py` | Backward-compatible shim used by the main menu; delegates to `modules/agent/_core.py` |
| `modules/agent/` | Current Agent package: orchestration core, backend adapters, constants, and logging |
| `utils/helpers.py` | Console UI helpers, command helpers, findings helpers |
| `tools/bin/` | Bundled helper scripts such as `gMSADumper.py` and `gmsa_grant_and_dump.py` |
| `ActiveDirectory-SAST/` | YAML knowledge base consumed by the agent prompt |
| `output/` | Session file, reports, logs, agent runtime data |
| `output/agent_logs/` | Markdown and JSON logs for Agent runs |
| `output/agent_runtime/` | ccache files, generated krb5 configs, temporary agent artifacts |
| `docs/` | Project documentation |

## 4. Runtime Model

AdStrike keeps engagement state in a shared `SESSION` dictionary from `config/settings.py`.

Core fields:

| Field | Meaning |
|---|---|
| `dc_ip` | Domain Controller IP address |
| `dc_fqdn` | Domain Controller FQDN, if known |
| `domain` | AD DNS domain |
| `base_dn` | LDAP base DN derived from domain |
| `username` | Current primary principal |
| `password` | Current primary password |
| `nt_hash` | Current primary NT hash |
| `use_kerberos` | Whether Kerberos mode is active |
| `krb5_ccache` | Current Kerberos ccache path |
| `krb5_config` | Target-specific krb5 config path |
| `attacker_ip` | Operator host IP |
| `attacker_iface` | Operator network interface |
| `engagement` | Engagement/report name |
| `commands_run` | Command history |
| `findings` | Report findings |
| `owned_users` | Compromised users or service accounts |
| `owned_machines` | Hosts with confirmed shell or admin access |
| `loot` | Discovered secrets, hashes, and credential material |
| `agent_intel` | Agent-specific extracted evidence and dead-path state; created dynamically when the Agent runs |

The session is loaded from `.env` and persisted into `output/session.json`.

## 5. Secret Redaction

AdStrike supports redaction through `ADSTRIKE_SHOW_SECRETS`.

- If `ADSTRIKE_SHOW_SECRETS=true`, reports and logs may include real secrets.
- If false, passwords, hashes, and loot values are replaced with `***`.

The default is `false` for safer sharing and reporting. Set `ADSTRIKE_SHOW_SECRETS=true` only in a private engagement workspace when real values are required in reports.

Redaction protects display and report output, but tools still use real session values internally. Redacted placeholders are scrubbed so `***` is never reused as a credential.

## 6. Kerberos Handling

Kerberos support is critical because modern AD targets may restrict NTLM. AdStrike handles:

- Target-specific `krb5.conf` generation.
- TGT requests through Impacket or local Kerberos tooling.
- `KRB5CCNAME` and `KRB5_CONFIG` environment management.
- Time skew handling through `faketime` where available.
- LDAP and GC service-ticket prefetch for tools that need GSSAPI.
- Cache validation before using Kerberos.

Important behaviour:

- A ccache is used only if it exists and is valid.
- Expired or missing ccaches are cleared from session.
- Password or NT hash authentication is preferred when appropriate.
- Kerberos is used when NTLM is blocked or when a Kerberos-only path is required.

## 7. Main Menu Coverage

AdStrike exposes 56 menu entries grouped into 9 kill-chain phase groups plus utilities.

### Phase 0: Reconnaissance

| # | Module | Coverage |
|---|---|---|
| 1 | Recon & OSINT | DNS, WHOIS, certificate transparency, email harvesting |
| 2 | Network Discovery | nmap, masscan, nbtscan, IPv6 discovery |

### Phase 1: Initial Access

| # | Module | Coverage |
|---|---|---|
| 3 | Initial Access (No Creds) | NTLM capture, relay, ARP, DHCPv6, RID cycling |
| 4 | CVE / AD Exploits | NoPac, PrintNightmare, Zerologon |
| 5 | AMSI / Defense Evasion | AMSI bypass, CLM, AppLocker, obfuscation |
| 6 | EDR / AV Evasion | NanoDump, BOF, syscall and evasion notes |
| 7 | UAC Bypass | fodhelper, eventvwr, CMSTP, token abuse |
| 8 | Pre2K & Timeroasting | Pre-Windows 2000 accounts, MS-SNTP hash, MAQ |
| 9 | WSUS Attack | WSUS spoofing and update-delivery attack notes |

### Phase 2: Enumeration

| # | Module | Coverage |
|---|---|---|
| 10 | AD Enumeration | LDAP, SMB, GPO, DNS, trusts, SPNs, LAPS |
| 11 | PowerView Enumeration | PowerView command reference |
| 12 | BloodHound Helper | BloodHound collection and Cypher path analysis |
| 13 | File & Share Hunter | Snaffler, SYSVOL, GPP, spidering |
| 14 | NetExec / NXC Suite | SMB, LDAP, MSSQL, WinRM, RDP with NXC |
| 15 | User Hunting | Session hunting and PSRemoting checks |
| 16 | ADIDNS Abuse | DNS record injection, WPAD, wildcard records |

### Phase 3: Privilege Escalation

| # | Module | Coverage |
|---|---|---|
| 17 | Local Privilege Escalation | PowerUp, potatoes, JEA, local checks |
| 18 | Kerberos Attacks | Roasting, tickets, delegation, Bronze Bit |
| 19 | Rubeus Toolkit | TGT, TGS, S4U, PTT, harvest |
| 20 | Shadow Credentials | `msDS-KeyCredentialLink`, PKINIT, NT hash recovery |
| 21 | RBCD Full Chain | Machine account, S4U2Proxy, altservice |
| 22 | ACL / ACE Abuse | GenericAll, GenericWrite, WriteDACL, ForceChangePassword |
| 23 | Certificate Abuse (ADCS) | ESC1 through ESC13 via Certipy workflows |
| 24 | RODC Attacks | PRP, Key List, RODC Golden Ticket |
| 25 | Golden Certificate | CA key, UnPAC, PassTheCert |
| 26 | UnPAC / PassTheCert | PKINIT hash extraction and certificate auth |
| 27 | JEA Attacks | JEA endpoint checks, PSReadLine history |

### Phase 4: Lateral Movement

| # | Module | Coverage |
|---|---|---|
| 28 | Lateral Movement | PSExec, WMI, DCOM, WinRM, WinRS |
| 29 | Coercion Attacks | PrinterBug, PetitPotam, relay chains |
| 30 | MSSQL Abuse | xp_cmdshell, linked servers, UNC capture |
| 31 | Password Attacks | Spray, kerbrute, credential stuffing |
| 32 | SCCM / MECM Abuse | NAA, client push, AdminService |

### Phase 5: Credential Access

| # | Module | Coverage |
|---|---|---|
| 33 | Credential Dumping | LSASS, SAM, NTDS, lsassy, nanodump |
| 34 | DPAPI & Credential Vault | dploot, SharpDPAPI, browser and vault secrets |
| 35 | DCSync / DCShadow | Replication abuse and domain hash dumping |
| 36 | Shadow Copies Abuse | VSS, NTDS.dit, SYSTEM hive |

### Phase 6: Persistence

| # | Module | Coverage |
|---|---|---|
| 37 | Domain Persistence | Golden ticket, AdminSDHolder, TTL membership |
| 38 | Local Persistence | WMI, registry, startup, SharPersist |
| 39 | GPO Abuse | GPO create, link, scheduled task, logon script |
| 40 | DNSAdmins Abuse | DNS service DLL injection |
| 41 | Trust Attacks | Trust keys, SID history, PAM, multi-hop |
| 42 | AD Misc Abuse | Backup Operators, Skeleton Key, Exchange |

### Phase 7: Cloud and Hybrid

| # | Module | Coverage |
|---|---|---|
| 43 | Azure AD / Entra ID | AADConnect, PTA, PHS, PRT |
| 44 | Entra Hybrid Attacks | MSOL DCSync, DeviceCode, PTA |
| 45 | gMSA Attacks | gMSA enumeration, hash extraction, PTH |
| 46 | ADFS & Golden SAML | Token signing certificate, Golden SAML |

### Phase 8: Advanced Operations

| # | Module | Coverage |
|---|---|---|
| 47 | Exploit Chains | Pre-built attack chains |
| 48 | C2 Integration | Sliver, Havoc, Metasploit, Cobalt Strike references |
| 49 | Loot Parser & Analyzer | Credential parsing and scoring |
| 50 | AD Advanced Playbook | WDAC, MDI, trusts, deception, WMI filters |

### Utilities

| # | Utility | Coverage |
|---|---|---|
| 51 | AdStrike Agent | AI orchestrator |
| 52 | Smart Analyst | Output parsing and prioritised next steps |
| 53 | Kerberos Manager | TGT, PTT, S4U, ccache, krb5.conf |
| 54 | Generate Report | HTML, Markdown, JSON reports |
| 55 | Session Manager | Save, load, switch, clear sessions |
| 56 | Tool Checker | Third-party dependency checks |

## 8. AdStrike Agent Overview

The AdStrike Agent is exposed through `modules/red_team_agent.py`, but the current implementation lives in the `modules/agent/` package:

- `modules/agent/_core.py`: orchestration loop, tool handlers, intel parsing, sanitization, and fallback logic
- `modules/agent/constants.py`: runtime settings and environment-driven toggles
- `modules/agent/backends.py`: Ollama and Claude adapters
- `modules/agent/logger.py`: Markdown logging

It exposes framework functionality as AI-callable tools and runs an iterative loop:

1. Build system prompt and current session context.
2. Ask the selected AI backend for the next tool call.
3. Sanitize the model's arguments.
4. Dispatch the tool.
5. Parse output into structured intel.
6. Merge intel into session.
7. Apply loop guards, dead-path tracking, and automatic repairs.
8. Write Markdown and JSON logs.
9. Repeat until success, partial completion, failure, or max rounds.

Supported backends:

- Ollama: local/free backend, recommended for offline lab use.
- Claude: Anthropic API backend.

The agent is intentionally not a hard-coded exploit script. It is a tool orchestrator that should adapt to the evidence found on each target.

## 9. Agent Runtime Directories

| Path | Purpose |
|---|---|
| `output/agent_logs/*.md` | Human-readable agent run report |
| `output/agent_logs/*.json` | Recent JSON conversation/session data |
| `output/agent_runtime/*.ccache` | Generated Kerberos ticket caches |
| `output/agent_runtime/krb5_*.conf` | Target-specific Kerberos configs |
| `output/agent_runtime/agent_loot*` | Agent-owned runtime scratch paths created for the current run |
| `/tmp/agent_loot_chain` | Legacy/compatibility temporary downloaded share data still used by some helper flows |
| `/tmp/agent_loot` | Legacy/compatibility temporary wordlists and loot fragments still used by some helper flows |

The agent refuses to run as root, and it also refuses to continue when its output directories are owned by another user. This prevents confusing failures where user-site Python tools such as `bloodyAD` are unavailable under `sudo` or the current user cannot write reports.

### Agent Output Cleanup

Each new full-auto agent run starts with a clean agent runtime area by default:

- `output/agent_runtime/*` is removed before the new run starts.
- `output/agent_logs/agent_*.md` and `output/agent_logs/agent_*.json` are archived to `output/agent_logs/archive/<run_timestamp>/` before the new run starts.
- `output/session.json` is never removed by the agent cleanup.
- Manual module output such as `output/enum/`, BloodHound exports, and generated reports is left untouched.

This keeps stale Kerberos caches, helper scripts, temporary loot, and previous agent logs from influencing a new target run while preserving old evidence for review.

Environment switches:

| Variable | Default | Meaning |
|---|---|---|
| `AGENT_CLEAN_OUTPUT_ON_START` | `true` | Enables runtime cleanup and old agent log handling at run start |
| `AGENT_ARCHIVE_OLD_RUNS` | `true` | Archives old agent logs when true; removes old agent logs when false |

## 10. Agent Tool Set

The Agent currently exposes these 52 high-level tools:

| Tool | Purpose |
|---|---|
| `nmap_scan` | Discover ports, OS, domain, time skew |
| `enumerate_ldap` | LDAP users, groups, computers, GPOs, trusts, SPNs, LAPS |
| `enumerate_shares` | SMB share access and readable share discovery |
| `collect_bloodhound` | BloodHound data collection |
| `query_bloodhound_paths` | Neo4j/BloodHound path queries |
| `asrep_roast` | AS-REP roastable account discovery |
| `kerberoast` | SPN account roasting |
| `password_spray` | Password spray against users |
| `adcs_scan` | ADCS enumeration and ESC exploitation |
| `shadow_credentials_attack` | Shadow Credentials on writable object |
| `acl_abuse_scan` | GenericWrite, GenericAll, WriteDACL, ForceChangePassword and gMSA edge discovery |
| `force_change_password_pivot` | Reset a target user via ForceChangePassword |
| `logon_script_abuse` | Abuse writable `scriptPath` where applicable |
| `auto_loot_chain` | Download readable shares, parse credentials, test and pivot |
| `dcsync_attack` | Dump domain hashes if replication rights are available |
| `lateral_movement` | Execute commands over WinRM, WMI, PSExec-like channels |
| `windows_privesc_recon` | Post-WinRM local escalation reconnaissance |
| `credential_loot` | Post-exploitation credential hunting on a confirmed host |
| `test_credential` | Test password/hash across SMB, LDAP, WinRM |
| `discover_winrm_access` | Find which host accepts WinRM for a credential |
| `update_session` | Persist newly discovered material |
| `run_module` | Run an arbitrary numbered AdStrike module |
| `generate_report` | Produce report outputs |
| `chain_planner` | Rank attack chains from collected intel and graph data |
| `bloodyad` | Generic wrapper for bloodyAD object operations |
| `gmsa_read` | Read gMSA managed password where allowed |
| `gmsa_takeover` | Modify gMSA read membership, dump hash, prepare PTH |
| `jea_enum` | JEA and PowerShell history checks |
| `targeted_kerberoast` | Targeted SPN abuse against writable account |
| `request_tgt` | Obtain Kerberos TGT and configure session |
| `evil_winrm` | Test or launch WinRM shell path |
| `kerbrute_enum` | Username enumeration over Kerberos |
| `no_cred_surface_recon` | No-credential AD surface checks |
| `agent_complete` | End mission with status |
| `rbcd_attack` | Resource-Based Constrained Delegation chain |
| `coercion_attack` | Coercion/relay-oriented authentication forcing |
| `unconstrained_delegation` | Unconstrained delegation abuse workflow |
| `pre2k_attack` | Pre-Windows 2000 account abuse checks |
| `timeroast` | Timeroasting workflow |
| `credential_dump` | Remote credential dumping workflow |
| `laps_read` | LAPS credential read workflow |
| `mssql_abuse` | MSSQL abuse workflow |
| `shadow_copies_dump` | Shadow copy credential extraction workflow |
| `golden_ticket` | Golden Ticket workflow |
| `silver_ticket` | Silver Ticket workflow |
| `trust_attack` | Trust abuse workflow |
| `user_hunt` | User/session hunting workflow |
| `gpo_abuse` | GPO abuse workflow |
| `sccm_abuse` | SCCM/MECM abuse workflow |
| `adidns_abuse` | AD-integrated DNS abuse workflow |
| `pass_the_cert` | Pass-the-Certificate workflow |
| `rodc_attack` | RODC abuse workflow |

## 11. Tool Argument Sanitization

The agent never fully trusts model-supplied arguments. `_sanitize_tool_inputs()` normalizes arguments before execution:

- Replaces placeholders with session values.
- Forces authoritative `dc_ip` and `domain`.
- Injects real password/hash from session.
- Prevents self-targeting for tools that need a separate target.
- Converts UPN-style usernames to bare names where needed.
- Replaces invalid target names with evidence-backed targets from `agent_intel`.
- If a selected account has a known NT hash in `loot` or `agent_intel`, uses that hash instead of the session password.

This last point is important for gMSA and computer-account pivots. If the agent selects `some_gmsa$` and the session contains `some_gmsa$ -> NT hash`, the tool input becomes:

```text
username: some_gmsa$
password:
nt_hash: <known hash>
```

It must not pair a gMSA account with the original user's password.

## 12. Agent Intel Model

`_analyze_result()` extracts structured information from tool output. This is merged into `SESSION["agent_intel"]`.

Common intel fields:

| Field | Meaning |
|---|---|
| `users` | Enumerated AD users |
| `spns` | SPN accounts |
| `asrep_users` | AS-REP roastable users |
| `admin_users` | Admin-count or admin-like users |
| `esc_vulns` | ADCS ESC vulnerabilities |
| `winrm_access` | Confirmed WinRM access |
| `winrm_targets` | Hosts accepting WinRM |
| `nt_hashes` | User or certificate-derived hashes |
| `gmsa_hashes` | gMSA account hashes |
| `ccaches` | Kerberos cache paths |
| `readable_shares` | SMB shares with read access |
| `creds_in_files` | Credentials parsed from files |
| `acl_paths` | ACL abuse edges |
| `script_path_edges` | Writable or useful logon script paths |
| `delegation` | Delegation-related evidence |
| `gmsa_candidates` | gMSA accounts seen during enumeration |
| `valid_creds` | Credentials validated by tools |
| `wsus_servers` | WSUS endpoints or policy evidence |
| `local_privesc_hints` | Scheduled tasks, update paths, ADCS/WSUS hints |
| `flags` | Lab flag-like strings |
| `gmsa_read_dead_for` | Principals where direct gMSA read is exhausted |
| `acl_scan_dead_for` | Principals where ACL scan is exhausted |

## 13. Decision Engine

`_pick_next_tool()` is the agent's deterministic fallback planner. It chooses the next useful action when:

- The model does not call a tool.
- The model repeats a completed tool.
- A tool is blocked by a dead-path guard.
- The agent needs to escape a loop.

General priority order:

1. Use fresh gMSA hash with WinRM/credential testing.
2. If shell exists, run post-exploitation recon.
3. Try known hashes, valid creds, or ccaches against shell paths.
4. Handle NTLM-disabled environments by requesting TGT.
5. Run basic recon and enumeration.
6. Mine readable shares for credentials.
7. Scan ADCS and exploit viable ESC paths.
8. Scan ACL/gMSA edges.
9. Exploit concrete ACL paths.
10. Roast where evidence exists.
11. Collect/query BloodHound.
12. Hunt loot and enumerate users.
13. Generate report and complete.

## 14. Loop Guards and Dead-Path Handling

The agent has several safeguards against repeated failures:

- Recent call signature tracking.
- Tool failure counters.
- Per-principal WinRM dead-path tracking.
- Per-principal gMSA read dead-path tracking.
- Per-principal ACL scan dead-path tracking.
- Network unreachable detection.
- Completion guard to block premature `agent_complete`.
- Auto-repair for protected users, NTLM-disabled auth, and Kerberos time skew.

Examples:

- If `evil_winrm` fails for `user1`, `user1` is added to `winrm_dead_for`.
- If `gmsa_read` cannot bind over NTLM or Kerberos for `user1`, `user1` is added to `gmsa_read_dead_for`.
- If `acl_abuse_scan` returns only auth-restriction and empty fallback evidence for `user1`, `user1` is added to `acl_scan_dead_for`.

New credentials are still allowed to try the same class of tool, because dead-paths are principal-specific.

## 15. gMSA Workflows

AdStrike supports two gMSA paths.

### Direct gMSA Read

Use `gmsa_read` when the current principal has `ReadGMSAPassword` or is in the allowed readers list.

The tool attempts:

1. NetExec `ldap --gmsa`.
2. bloodyAD raw search for `msDS-ManagedPassword`.
3. `gMSADumper.py`.
4. Kerberos retry when NTLM bind is denied.

It only creates a Critical finding when it extracts a valid `account$ -> NT hash`.

### gMSA Takeover

Use `gmsa_takeover` when the current principal has write rights over the gMSA object, such as:

- GenericWrite
- GenericAll
- WriteDACL
- WriteOwner
- WriteProperty

The takeover flow:

1. Pick a reader identity.
2. Resolve reader SID.
3. Write `msDS-GroupMSAMembership` to grant read access.
4. Dump `msDS-ManagedPassword`.
5. Convert blob to NT hash.
6. Store hash in loot and owned users.
7. Try shell or credential testing with the gMSA hash.

The helper `tools/bin/gmsa_grant_and_dump.py` supports separate writer and reader identities and can use NTLM or Kerberos per side.

## 16. ADCS Workflow

`adcs_scan` uses Certipy-oriented workflows to:

- Detect Enterprise CAs.
- Enumerate templates.
- Identify ESC vulnerabilities.
- Attempt exploitation where automation is safe.
- Store hashes, ccaches, PFX files, or shell-ready state when exploitation succeeds.

If ADCS exploitation yields shell-ready material, the agent automatically attempts WinRM with the new credential material, not the old credential.

## 17. BloodHound Workflow

`collect_bloodhound` tries to collect graph data and supports fallback behaviour:

- Kerberos collection with generated krb5 config.
- Password/NTLM collection.
- IPv4-forced wrapper to avoid bad DNS/AAAA resolution.
- DCOnly fallback for unstable DNS.
- NetExec BloodHound fallback.
- Inline ACL/gMSA fallback if BloodHound collectors fail.

`query_bloodhound_paths` is used after data exists in Neo4j/BloodHound.

## 18. WinRM and Lateral Movement

`evil_winrm`, `discover_winrm_access`, and `lateral_movement` support:

- Password auth.
- NT hash / Pass-the-Hash.
- Kerberos ccache.
- FQDN selection for Kerberos.
- DC IP fallback where appropriate.

The agent marks WinRM dead per principal. If `user1` cannot WinRM, the agent will not keep trying `user1`, but it can still try `gmsa1$` or another newly discovered identity.

## 19. Reporting

AdStrike reporting includes:

- Findings with severity, description, and remediation.
- Owned users and machines.
- Captured hashes and loot keys.
- Commands and evidence where available.
- Agent round logs in Markdown.
- JSON session data.

Agent Markdown logs include:

- Round number.
- Tool name.
- Model arguments.
- Final sanitized tool input.
- Tool output.
- Mission summary.

## 20. Running the Tool

Typical manual start:

```bash
./run.sh
```

or:

```bash
python3 main.py
```

Typical session setup requires:

- DC IP
- Domain
- Username
- Password or NT hash
- Optional attacker IP/interface
- Optional Kerberos ccache

Then select:

- Module `10` for AD enumeration.
- Module `23` for ADCS.
- Module `51` for Agent.
- Module `54` for reporting.

## 21. Running the Agent

From the menu, choose `AdStrike Agent (AI)`.

Backend choices:

- Ollama: local and recommended for offline operation.
- Claude: Anthropic API mode.

Required session fields:

- `dc_ip`
- `domain`
- `username`
- password or NT hash, unless doing no-auth enumeration only

Recommended starting point:

1. Confirm VPN and DNS reachability.
2. Set session target and credential.
3. Run Agent with Ollama.
4. Watch rounds for repeated auth failures or environment issues.
5. Review `output/agent_logs/*.md`.

## 22. Common Agent Paths

### Standard Credential Path

```text
nmap_scan
enumerate_ldap
enumerate_shares
auto_loot_chain
acl_abuse_scan
adcs_scan
collect_bloodhound
query_bloodhound_paths
exploit concrete path
```

### gMSA Direct Read Path

```text
discover gMSA candidate
gmsa_read
extract account$ NT hash
test credential / WinRM
post-exploitation recon
```

### gMSA Write Path

```text
discover GenericWrite/WriteDACL on gMSA
gmsa_takeover
write msDS-GroupMSAMembership
dump managed password
use NT hash
```

### ADCS Path

```text
adcs_scan
detect ESC
exploit certificate path
obtain hash/ccache/PFX
test shell or DCSync
```

### BloodHound Path

```text
collect_bloodhound
query_bloodhound_paths
select ACL/ADCS/delegation edge
run specific exploit tool
```

## 23. Troubleshooting

### `invalidCredentials` with Known Good Password

Possible causes:

- NTLM blocked.
- Protected Users group.
- Kerberos-only principal.
- Clock skew.
- Wrong DC hostname/IP mapping.
- Tool attempting stale ccache.

Expected behaviour:

- Request TGT.
- Generate target krb5 config.
- Retry Kerberos-aware tools.
- If still failing, mark the path dead for that principal.

### `KRB_AP_ERR_SKEW`

Cause: local time differs from DC time.

Mitigation:

- Use `faketime` where available.
- Request TGT with corrected DC time.
- Verify nmap-reported server time.

### `from ccache 576`

Cause: invalid, expired, or wrong-principal ccache.

Mitigation:

- Validate ccache with `klist`.
- Clear stale Kerberos session state.
- Request a fresh TGT.

### `bloodyAD` Traceback

Cause: bloodyAD bind/client failure.

Expected behaviour:

- Compact failure output.
- Continue with LDAP, dacledit, BloodHound, or other fallback.
- Do not treat traceback as proof of exploitability.

### gMSA Critical Finding Without Hash

This should not happen. A gMSA Critical finding must require:

- Account name ending in `$`.
- Valid 32-character NT hash.
- Evidence from NXC, bloodyAD blob decode, gMSADumper, or takeover helper.

### Agent Looping

Common loop patterns:

- Repeating `evil_winrm` with same user.
- Repeating `acl_abuse_scan` after auth-restricted empty result.
- Repeating `gmsa_read` after bind failure.
- Resetting completed tools every round due to stale loot.

Expected guards:

- `winrm_dead_for`
- `gmsa_read_dead_for`
- `acl_scan_dead_for`
- recent signature anti-loop
- failure counters
- completion guard

## 24. Development Guidelines

When adding new modules or agent tools:

- Do not hard-code lab names, domains, users, hashes, or hostnames.
- Store new evidence in `SESSION` or `agent_intel`.
- Add findings only after concrete evidence exists.
- Include remediation text for reportable findings.
- Mark dead paths per principal, not globally.
- Prefer structured parsing over brittle string checks.
- Keep fallback output concise.
- Never pair a selected account with the wrong credential material.
- Respect Kerberos vs NTLM mode.
- Preserve old user changes and avoid unrelated refactors.

## 25. Adding a New Agent Tool

To add an Agent tool:

1. Implement `tool_<name>()`.
2. Add schema to `TOOLS`.
3. Add function to `TOOL_MAP`.
4. Update `_analyze_result()` if the output creates new intel.
5. Update `_pick_next_tool()` if it affects decision order.
6. Add dead-path tracking if the tool can fail repeatedly for the same principal.
7. Add report findings only when evidence is concrete.

Minimal pattern:

```python
def tool_example(dc_ip: str, domain: str, username: str,
                 password: str = "", nt_hash: str = "") -> str:
    password = _real_secret(password)
    nt_hash = _real_nt_hash(nt_hash)
    auth = _auth_args_nxc(username, password, nt_hash, domain, dc_ip)
    out = _nxc(f"ldap {shell_quote(dc_ip)} {auth} ...", timeout=30)
    return out
```

## 26. Quality Checklist

Before committing changes:

- `python3 -m py_compile` or venv compile passes.
- No target-specific strings remain in generic logic.
- Findings are evidence-gated.
- Redaction still works.
- Agent does not loop on repeated failures.
- Kerberos ccache is validated before use.
- Password/hash injection matches the selected principal.
- Reports remain readable and do not include full tracebacks unless intentionally debugging.

## 27. Operator Checklist

Before running:

- Confirm target scope and permission.
- Confirm VPN route and DNS.
- Set target domain and DC IP.
- Set valid credential or hash.
- Decide whether to use password, hash, or Kerberos.
- Check `faketime`, Impacket, NetExec, Certipy, and Kerberos tools.
- Ensure output directories are writable by the current user.

During the run:

- Watch for repeated tool pairs.
- Watch for stale ccache messages.
- Confirm findings have real evidence.
- Stop the run if a destructive path is not authorised.

After the run:

- Review `output/agent_logs/*.md`.
- Review `output/session.json`.
- Generate final report.
- Remove temporary ccaches if required.

## 28. Known Limitations

- Tool behaviour depends heavily on installed versions of NetExec, Certipy, bloodyAD, Impacket, ldap3, Kerberos libraries, and system Python packages.
- Some AD labs intentionally block NTLM or require exact FQDN/KDC mapping.
- BloodHound collection may fail due to DNS, IPv6, LDAP signing, Kerberos ticket state, or collector version mismatch.
- AI backends can propose poor actions; guards reduce but do not eliminate this.
- Redaction can hide values in reports; operators may need secure raw logs for internal validation.
- Some modules are guided playbooks rather than fully automated exploits.

## 29. Security and OPSEC Notes

- Password spraying can lock accounts; verify lockout policy first.
- Coercion and relay attacks can be noisy.
- ADCS exploitation may create certificate artifacts.
- GPO and logon script abuse can impact many users.
- WSUS attacks can affect patch infrastructure.
- DCSync and DCShadow are high-impact and should be used only with explicit approval.
- Always prefer read-only enumeration until a path is confirmed and authorised.

## 30. MCP Server (No API Key)

AdStrike ships an MCP server (`mcp_server.py`) that exposes its tools over the
[Model Context Protocol](https://modelcontextprotocol.io). On this path AdStrike
runs **no LLM of its own**: there is **no Ollama and no `ANTHROPIC_API_KEY`**. An
MCP host you already use — Claude Code, Cursor, Claude Desktop — connects to the
server, and that host's LLM drives the engagement with its own subscription. The
host is the brain; AdStrike is the toolbox.

### What it exposes

All **53 tools** are published: the **52 attack tools** from section 10 plus a
`set_engagement` tool. The 52 attack tools reuse the exact schemas from
`modules/agent/_core.py` (`TOOLS` / `TOOL_MAP`) — one source of truth, no
duplication with the standalone agent.

This path does **not** start the agent loop described in sections 8–21. It never
imports `modules/agent/backends.py` (Ollama/Claude adapters); it only imports the
tool layer. The standalone `python3 main.py` agent (Ollama/Claude) is unchanged
and still available.

### How credentials flow

The host LLM supplies only tool-specific arguments. The operator (or the host)
calls `set_engagement` **once** with the authoritative target and credentials:

| Field | Required | Notes |
|---|---|---|
| `dc_ip` | yes | Domain Controller IP |
| `domain` | yes | AD domain FQDN, e.g. `corp.local` |
| `username` | yes | Authenticating user (sAMAccountName) |
| `password` | one of | Omit if using `nt_hash` |
| `nt_hash` | one of | Omit if using `password` |
| `dc_fqdn` | optional | DC FQDN, e.g. `dc1.corp.local` |

`set_engagement` writes these into `SESSION` and persists with `save_session()`.
From then on, `_sanitize_tool_inputs()` (the same authority used by the standalone
agent, section 11) **injects the session target and credentials into every later
tool call**. The host LLM never has to repeat the password and cannot accidentally
target the wrong host or account.

### Protocol hygiene

AdStrike tool code prints colourful status to stdout, but an MCP stdio server
speaks JSON-RPC over stdout — any stray byte corrupts the protocol. `mcp_server.py`
redirects import-time chatter to stderr, then at runtime routes every `print()` to
stderr, so **stdout carries only JSON-RPC** and tool status appears as host server
logs. The server also `chdir`s to the repo root on start, so loot and runtime files
land in `output/` as usual no matter where the host launches it. Each tool call
shells out and runs blocking, so it is offloaded to a worker thread to keep the
event loop responsive.

### Registering the server

Use absolute paths to the venv interpreter and `mcp_server.py`.

Verify it loads:

```bash
./venv/bin/python3 -c "import mcp; from modules.agent._core import TOOLS, TOOL_MAP; \
print('ok', sum(1 for t in TOOLS if t['name'] in TOOL_MAP), '+ set_engagement = 53')"
```

Claude Code, Cursor, and Claude Desktop all use the same `command` + `args` shape
in their MCP config:

```json
{
  "mcpServers": {
    "adstrike": {
      "command": "/path/to/AdStrike/venv/bin/python3",
      "args": ["/path/to/AdStrike/mcp_server.py"]
    }
  }
}
```

Claude Code also supports a CLI shortcut:

```bash
claude mcp add adstrike -- /path/to/AdStrike/venv/bin/python3 /path/to/AdStrike/mcp_server.py
```

### Typical flow

1. Ask the host to `set_engagement` with the target and credentials.
2. Let the host drive — it reads each tool's output and picks the next tool
   (nmap → LDAP enum → BloodHound → matching abuse primitive), the same shape as
   the built-in agent loop, but funded by the host subscription.

The per-host config details, full tool catalogue grouped by phase, and OPSEC notes
live in [docs/mcp.md](mcp.md).

## 31. Summary

AdStrike is a broad AD testing framework with an AI orchestration layer. The core strength is the shared session model: credentials, Kerberos tickets, findings, loot, and owned assets flow between modules. The Agent builds on that by extracting structured intel and selecting next actions.

For reliable multi-machine use, the most important principles are:

- Avoid hard-coded target assumptions.
- Treat every finding as evidence-gated.
- Track dead paths per principal.
- Use the correct credential material for the selected identity.
- Validate Kerberos before using a ccache.
- Keep fallback behaviour concise and actionable.
