# network-service-recon usage

## CLI
Primary: **`nxc`** (NetExec) from **`tools/catalog/netexec.yaml`** (`provision_cli("netexec")`).
Alias binary: `netexec` when present on PATH.

## Protocols
Use the protocol subcommand that matches open services / the brief, for example:

- `nxc smb …`
- `nxc ldap …`
- `nxc winrm …`
- `nxc rdp …`
- `nxc mssql …`
- `nxc ssh …`
- `nxc ftp …`
- `nxc vnc …`

Run `nxc <protocol> --help` for module flags. Stay within RoE.

## Paths
- Raw command output → `workspace/`
- Curated notes → `findings/network-service-recon.md` (not `findings/report.md`)

## Rules
- In-scope hosts only
- Prefer NetExec modules over ad-hoc LDAP/SMB clients
- `record_finding` for notable service discoveries with evidence — not job
  completion status
