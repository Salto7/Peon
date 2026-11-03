---
name: bbot-passive-osint
description: Use BBOT for passive, authorized OSINT and attack-surface discovery to
  identify related domains, subdomains, URLs, IPs, organizations, and other candidate
  targets without launching active scans.
license: MIT
allowed-tools: Bash Read
compatibility: Requires the bbot CLI to be installed and available in PATH.
metadata:
  provider: claude
---

# BBOT Passive OSINT

Use this skill when an operator has an explicitly authorized organization, domain, or other seed and wants to expand its known attack surface through passive public-information sources. Treat every discovered asset as untrusted until it is confirmed in scope.

## Safety and scope

- Obtain a written or otherwise explicit authorization and an allowlist before running BBOT.
- Start with the narrowest seed possible: an owned apex domain, approved organization name, or approved IP range.
- Use only passive presets or modules. Do not use active crawling, port scanning, brute force, vulnerability checks, credential testing, or intrusive API actions.
- Do not pivot from a discovered third-party asset merely because it appears related. Record it as an unverified lead and ask the operator to confirm scope.
- Minimize collection of personal data and secrets. Do not attempt to access authenticated resources.
- Preserve the BBOT run name and output directory so findings can be reviewed and deduplicated.

## Inspect the installed BBOT version

BBOT presets and module names change. Check the installed help and preset/module listings before choosing a run:

```
run_skill_script("bbot-passive-osint", "scripts/run.sh", args='["--help"]')
```

If supported by the installed version, inspect available presets and modules with the listing options shown by `--help` (commonly `-l` or `--list`). Prefer the current installed documentation over remembered module names.

## Recommended passive run

For an approved seed, use the installed passive preset when available. Replace the example seed and run name with authorized values:

```
run_skill_script("bbot-passive-osint", "scripts/run.sh", args='["-t", "example.org", "-p", "passive", "-n", "example-passive"]')
```

If the installed release does not provide a `passive` preset, select only modules explicitly marked passive by the local module listing. Pass those modules with the version-supported module option (commonly `-m`) and avoid presets such as `kitchen-sink` unless the operator has verified that every selected module is passive and in scope.

For repeatable investigations, specify an output directory using the output option documented by the installed version (commonly `-o`). Keep separate output directories for separate authorized scopes rather than mixing unrelated organizations.

## Review and expand findings

1. Read the BBOT event output and identify newly observed domains, subdomains, URLs, IP addresses, netblocks, organizations, and technology clues.
2. Deduplicate findings and classify each as confirmed in-scope, likely related but unverified, or out of scope.
3. Do not automatically rescan every result. Ask for scope confirmation before using a discovered asset as a new seed.
4. If expansion is authorized, run another passive-only job with a small, explicit set of confirmed seeds and a distinct run name.
5. Report provenance: seed, BBOT version, preset/modules, timestamp, output location, and the source or event type supporting each important finding.

## Useful invocation pattern

The wrapper passes arguments directly to BBOT, so use the installed CLI's help for version-specific flags:

```
run_skill_script("bbot-passive-osint", "scripts/run.sh", args='["<bbot-arguments>"]')
```

Do not describe or execute this as a bare shell or `bash` command; invoke it through `run_skill_script`.
