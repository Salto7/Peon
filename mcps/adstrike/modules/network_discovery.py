"""
Module: Network Discovery
Techniques: nmap AD port scan, masscan, nbtscan, netdiscover,
            IPv6 discovery, DC identification, service fingerprinting
"""
from utils.helpers import *
from config.settings import SESSION

def run():
    print_banner("NETWORK DISCOVERY", "Host/port/service enumeration")
    dc      = input_or_session("dc_ip",       "DC IP / Target")
    iface   = input_or_session("attacker_iface", "Network interface (e.g. eth0, tun0)")

    subnet  = prompt(f"Target subnet (e.g. 192.168.1.0/24)")

    print(f"""
  [1]  Full AD Port Scan (nmap)
  [2]  Fast Host Discovery (masscan)
  [3]  NBT / NetBIOS Scan (nbtscan)
  [4]  IPv6 Discovery (mitm6 / nmap)
  [5]  DC Identification
  [6]  SMB Signing Check (full subnet)
  [7]  Service Version Fingerprint
  [0]  Back
""")
    c = input(f"  {M}Choice:{RST} ").strip()

    if c == "1":
        print(f"""
  {C}Full AD Port Scan — nmap:{RST}

  {Y}Quick top-ports scan:{RST}
  nmap -sV -sC -T4 \\
    -p 22,53,80,88,135,139,389,443,445,464,593,636,3268,3269,3389,5985,5986,9389 \\
    {subnet} -oA /tmp/nmap_ad_{dc}

  {Y}Comprehensive AD service scan:{RST}
  nmap -sV -sC -T4 \\
    --script=smb-security-mode,smb2-security-mode,smb-vuln-ms17-010,\\
ldap-rootdse,msrpc-enum,krb5-enum-users \\
    -p 88,135,139,389,445,464,593,636,3268,3269,3389,5985 \\
    {dc} -oA /tmp/nmap_dc_{dc}

  {Y}Vuln scan:{RST}
  nmap -sV --script vuln -p 445,135,3389 {subnet} -oA /tmp/nmap_vuln

  {Y}Parse live hosts:{RST}
  grep "report for" /tmp/nmap_ad_{dc}.gnmap | awk '{{print $5}}'
""")
        run_cmd(
            f"nmap -sV -sC -T4 "
            f"-p 88,135,139,389,445,464,636,3268,3389,5985 "
            f"{dc} -oA /tmp/nmap_dc_{dc}"
        )

    elif c == "2":
        print(f"""
  {C}Fast Host Discovery — masscan:{RST}

  {Y}Full subnet ping sweep:{RST}
  sudo masscan {subnet} -p 445,88,3389,5985 --rate 1000 \\
    -oL /tmp/masscan_{subnet.replace("/","_")}.txt

  {Y}Parse live hosts:{RST}
  grep "open" /tmp/masscan_{subnet.replace("/","_")}.txt | \\
    awk '{{print $4}}' | sort -u > /tmp/live_hosts.txt
  echo "Live hosts:"; cat /tmp/live_hosts.txt

  {Y}Then nmap only live hosts:{RST}
  nmap -sV -iL /tmp/live_hosts.txt \\
    -p 88,135,139,389,445,464,636,3268,3389,5985 \\
    -oA /tmp/nmap_live
""")
        run_cmd(
            f"sudo masscan {subnet} -p 445,88,3389,5985 --rate 1000 "
            f"-oL /tmp/masscan_out.txt"
        )

    elif c == "3":
        print(f"""
  {C}NBT / NetBIOS Scan:{RST}

  {Y}nbtscan:{RST}
  sudo nbtscan {subnet}
  sudo nbtscan -r {subnet} > /tmp/nbtscan_results.txt

  {Y}nmap NetBIOS scripts:{RST}
  nmap -sU -p 137 --script nbstat {subnet}

  {Y}nxc SMB host list:{RST}
  nxc smb {subnet} 2>/dev/null | grep -v "\\[\\*\\]"
""")
        run_cmd(f"sudo nbtscan {subnet}")

    elif c == "4":
        print(f"""
  {C}IPv6 Discovery:{RST}

  {Y}mitm6 scan (passive — discover IPv6 hosts):{RST}
  sudo mitm6 -i {iface} -d {SESSION.get("domain","")} --debug 2>&1 | head -30

  {Y}nmap IPv6 sweep:{RST}
  nmap -6 -sn fe80::/10 --interface {iface}
  nmap -6 -sV {subnet.replace(".0/24","::1/64")}

  {Y}alive6 (thc-ipv6):{RST}
  sudo alive6 {iface}

  {Y}ping6 link-local:{RST}
  ping6 -c 3 ff02::1%{iface}
""")

    elif c == "5":
        dom = SESSION.get("domain", "")
        print(f"""
  {C}DC Identification:{RST}

  {Y}DNS SRV record lookup (most reliable):{RST}
  nslookup -type=SRV _ldap._tcp.dc._msdcs.{dom} {dc}
  dig SRV _ldap._tcp.dc._msdcs.{dom} @{dc}
  dig SRV _kerberos._tcp.{dom} @{dc}

  {Y}nxc — identify DCs and domain info:{RST}
  nxc smb {subnet} --gen-relay-list /tmp/smb_hosts.txt
  nxc smb {subnet} 2>/dev/null | grep "domain:{dom}"

  {Y}nmap LDAP rootDSE (identifies DC):{RST}
  nmap -p 389 --script ldap-rootdse {subnet}

  {Y}impacket GetDomainInfo:{RST}
  impacket-GetADUsers -all {dom}/:{"{none}"}@{dc} -dc-ip {dc}
""")
        run_cmd(f"nmap -p 389 --script ldap-rootdse {dc}")

    elif c == "6":
        print(f"""
  {C}SMB Signing Check — identify relay targets:{RST}

  {Y}nxc (fastest):{RST}
  nxc smb {subnet} --gen-relay-list /tmp/relay_targets.txt
  echo "Relay targets (signing disabled):"; cat /tmp/relay_targets.txt

  {Y}nmap:{RST}
  nmap -p 445 --script smb2-security-mode {subnet} -oG /tmp/smb_signing.txt
  grep -i "Message signing enabled but not required" /tmp/smb_signing.txt

  {Y}RunFinger.py (Responder suite):{RST}
  python3 /usr/share/responder/tools/RunFinger.py -i {subnet}
""")
        run_cmd(f"nxc smb {subnet} --gen-relay-list /tmp/relay_targets.txt")
        add_finding("SMB Signing Disabled", "High",
                    f"Hosts without SMB signing found in {subnet} — NTLM relay is possible",
                    "Enable SMB signing on all hosts via GPO; enforce SMB signing on domain controllers")

    elif c == "7":
        print(f"""
  {C}Service Version Fingerprinting:{RST}

  {Y}Full version detection on DC:{RST}
  nmap -sV -sC -A -T4 {dc} \\
    -p 22,53,80,88,135,139,389,443,445,464,593,636,3268,3269,3389,5985,5986,9389 \\
    -oA /tmp/nmap_full_{dc}

  {Y}OS detection:{RST}
  sudo nmap -O -T4 {dc}

  {Y}Banner grab specific services:{RST}
  nc -nv {dc} 445    # SMB banner
  openssl s_client -connect {dc}:636    # LDAPS certificate
  openssl s_client -connect {dc}:3269   # GC LDAPS cert
""")
        run_cmd(
            f"nmap -sV -T4 {dc} "
            f"-p 88,135,139,389,445,636,3268,3269,3389,5985 "
            f"-oA /tmp/nmap_full_{dc}"
        )

    pause()
