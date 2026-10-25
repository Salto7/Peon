"""
Module: AD Advanced Playbook
Purpose: Advanced AD tradecraft map distilled from the local AD advanced slides.
"""
from utils.helpers import *
from config.settings import SESSION


def _coverage():
    print(f"""
  {BABY_BLUE}{BOLD}PDF Coverage Map -> Existing Modules{RST}

  {LIGHT_PINK}Already covered well:{RST}
  - PowerShell / AMSI / .NET tradecraft        -> [5] AMSI, [6] EDR
  - Domain enum, GPO, OU, ACL, trusts          -> [8], [9], [10], [18], [33]
  - PSRemoting / WinRM / WMI movement          -> [20], [44]
  - Kerberos, roast, delegation, tickets       -> [14], [15], [17], [21]
  - Windows LAPS / gMSA                        -> [8], [46]
  - Credential extraction / DCSync             -> [25], [26], [27]
  - AdminSDHolder / DSRM / SSP / NPPSPY        -> [29], [34]
  - ADCS / Shadow Credentials / PassTheCert    -> [16], [19], [52], [53]
  - Entra Connect / MSOL / MDI-bypass path     -> [35], [45]
  - MSSQL trust abuse                          -> [22]

  {LIGHT_PINK}Added here as advanced operator checklists:{RST}
  - WDAC/AppLocker/CLM decision workflow
  - MDE/MDI detection-aware operating notes
  - WMI filter abuse and rollback workflow
  - Cross-forest Kerberoast, delegation, trust-key, FSP, PAM map
  - RACE-style security descriptor persistence review
  - Deception / honey principal validation
""")


def _wdac():
    print(f"""
  {BABY_BLUE}{BOLD}WDAC / AppLocker / CLM Operator Workflow{RST}

  {LIGHT_PINK}1. Identify the constraint first{RST}
  whoami /all
  $ExecutionContext.SessionState.LanguageMode
  Get-AppLockerPolicy -Effective | select -ExpandProperty RuleCollections
  Get-CimInstance -Namespace root\\Microsoft\\Windows\\DeviceGuard \\
    -ClassName Win32_DeviceGuard

  {LIGHT_PINK}2. Prefer signed and built-in administration surfaces{RST}
  Import-Module ActiveDirectory
  Get-ADDomain
  Get-ADUser -Filter * -Properties servicePrincipalName,adminCount
  Get-ADObject -LDAPFilter '(objectClass=trustedDomain)' -Properties *

  {LIGHT_PINK}3. If PowerShell is constrained{RST}
  - Use Microsoft-signed modules where possible.
  - Prefer LDAP/ADWS collection from Linux: ldapsearch, nxc ldap, bloodhound-python.
  - Prefer compiled .NET tooling only when authorized and needed.
  - Record the policy mode in the report before changing execution strategy.

  {LIGHT_PINK}4. Defensive validation notes{RST}
  - Verify WDAC policy enforcement mode and audit logs.
  - Review AppLocker gaps for writable paths and signed binary abuse.
  - Monitor PowerShell 4103/4104, ScriptBlock logging, and CLM transitions.
""")


def _mdi_mde():
    print(f"""
  {BABY_BLUE}{BOLD}MDE / MDI Detection-Aware Checklist{RST}

  {LIGHT_PINK}Low-noise collection preferences{RST}
  - Prefer LDAP filters that answer a specific question over broad dumps.
  - For BloodHound, collect only needed methods and avoid DC sessions unless needed.
  - Prefer ADWS/SOAPHound-style collection where available for lower LDAP noise.
  - Use Kerberos/FQDN when NTLM is disabled or monitored heavily.

  {LIGHT_PINK}High-signal detections to expect{RST}
  - DCSync: directory replication events and MDI domain dominance alerts.
  - Kerberoasting: Event ID 4769 volume and RC4 downgrade patterns.
  - Golden/Silver/Diamond ticket use: PAC, lifetime, and replay anomalies.
  - Skeleton key / LSASS patching: EDR memory tamper alerts.
  - AdminSDHolder and DCSync-rights persistence: Event ID 4670/5136.

  {LIGHT_PINK}Existing tool paths{RST}
  - MSOL / Entra Connect MDI-aware path: [45] Entra Hybrid Attacks.
  - Kerberos-only workflow: [39] Kerberos Manager and [48] AI Agent.
  - Reporting evidence: [40] Generate Report.
""")


def _wmi_filters():
    dom = SESSION.get("domain", "corp.local")
    base_dn = SESSION.get("base_dn") or "DC=" + dom.replace(".", ",DC=")
    print(f"""
  {BABY_BLUE}{BOLD}WMI Filter Abuse / Review Workflow{RST}

  WMI filters decide where linked GPOs apply. If an operator can modify a
  filter, they may change the effective target set of a GPO without directly
  editing the GPO payload.

  {LIGHT_PINK}Enumerate WMI filters{RST}
  Get-ADObject -LDAPFilter '(objectClass=msWMI-Som)' \\
    -SearchBase 'CN=SOM,CN=WMIPolicy,CN=System,{base_dn}' \\
    -Properties msWMI-Name,msWMI-Parm1,msWMI-Parm2,nTSecurityDescriptor

  {LIGHT_PINK}Map GPOs linked to WMI filters{RST}
  Get-DomainGPO | select displayname,gpcwqlfilter,gplink
  Get-ADObject -LDAPFilter '(objectClass=groupPolicyContainer)' \\
    -Properties displayName,gPCWQLFilter

  {LIGHT_PINK}Find who can modify filters{RST}
  Get-DomainObjectAcl -SearchBase 'CN=SOM,CN=WMIPolicy,CN=System,{base_dn}' \\
    -ResolveGUIDs | ?{{$_.ActiveDirectoryRights -match 'Write|GenericAll'}}

  {LIGHT_PINK}Safe rollback evidence to capture before any authorized change{RST}
  Get-ADObject -Identity '<WMI_FILTER_DN>' -Properties * | fl * > wmi_filter_backup.txt

  {LIGHT_PINK}Defensive checks{RST}
  - Alert on changes under CN=SOM,CN=WMIPolicy,CN=System.
  - Review GPOs whose target scope changes through filter edits.
  - Keep before/after query text for reporting and cleanup.
""")


def _cross_forest():
    print(f"""
  {BABY_BLUE}{BOLD}Advanced Cross-Forest / Cross-Trust Map{RST}

  {LIGHT_PINK}Enumeration questions{RST}
  Get-ADTrust -Filter *
  Get-DomainTrust
  Get-ADForest | select -ExpandProperty Domains
  Get-ADForest | select -ExpandProperty GlobalCatalogs

  {LIGHT_PINK}What to check per trust{RST}
  - Direction and transitivity.
  - SID filtering / SIDHistory status.
  - TGT delegation on forest trust.
  - Selective Authentication.
  - Foreign Security Principals and ACLs referencing foreign SIDs.
  - PAM / bastion trust and shadow principal mapping.

  {LIGHT_PINK}Attack path families already supported elsewhere{RST}
  - Trust keys, SIDHistory, ExtraSID          -> [33] Trust Attacks
  - Cross-domain Kerberoasting               -> [14], [33]
  - Delegation paths                         -> [14], [17], [21], [33]
  - MSSQL links across trusts                -> [22]
  - PAM / bastion trust review               -> [33]

  {LIGHT_PINK}Operator notes{RST}
  - Forest trusts usually enforce SID filtering; validate before assuming ExtraSID.
  - External trusts or SIDHistory-enabled trusts may permit RID > 1000 SIDs.
  - Foreign Security Principals can hide access paths that group membership views miss.
  - Always document source forest, target forest, trust direction, and the exact SID used.
""")


def _race_sd():
    dc = SESSION.get("dc_ip", "<target>")
    user = SESSION.get("username", "<user>")
    print(f"""
  {BABY_BLUE}{BOLD}RACE-Style Security Descriptor Persistence Review{RST}

  This is a review and cleanup checklist for WMI, PowerShell Remoting, and
  Remote Registry security descriptor changes.

  {LIGHT_PINK}WMI namespace access review{RST}
  Get-WmiObject -Namespace root\\cimv2 -Class __SystemSecurity
  # RACE-style action usually targets WMI namespace SDDL for a trustee.

  {LIGHT_PINK}PowerShell Remoting endpoint review{RST}
  Get-PSSessionConfiguration | fl Name,Permission
  Get-PSSessionConfiguration -Name Microsoft.PowerShell | fl *

  {LIGHT_PINK}Remote Registry review{RST}
  sc.exe \\\\{dc} qc RemoteRegistry
  reg query "\\\\{dc}\\HKLM\\SYSTEM\\CurrentControlSet\\Control\\SecurePipeServers\\winreg"

  {LIGHT_PINK}Existing module path{RST}
  - [29] Domain Persistence -> Security Descriptor Mod

  {LIGHT_PINK}Cleanup checklist{RST}
  - Remove trustee-specific ACEs from WMI namespace SDDL.
  - Revert PSSession endpoint permissions.
  - Revert Remote Registry ACL changes.
  - Confirm {user} no longer has remote WMI/WinRM/RemoteReg access unless intended.
""")


def _deception():
    print(f"""
  {BABY_BLUE}{BOLD}Deception / Honey Principal Validation{RST}

  Use this as a blue-team validation and detection exercise after hardening.

  {LIGHT_PINK}Ideas to validate{RST}
  - Honey users with attractive descriptions and no real access.
  - Honey computers or service accounts with monitored SPNs.
  - Canary ACLs where enumeration of specific attributes is alert-worthy.
  - Decoy shares and SYSVOL-like paths with monitored access.

  {LIGHT_PINK}Tools / references commonly used{RST}
  - Deploy-Deception for AD deception objects.
  - SIEM alerts for PowerView/ADExplorer-style reads on decoy attributes.
  - Event correlation around Kerberoast/AS-REP attempts against honey users.

  {LIGHT_PINK}Report what matters{RST}
  - Which decoy was accessed.
  - Which account accessed it.
  - What event source observed it.
  - Time from access to alert.
  - Whether the alert includes enough context for triage.
""")
    add_finding(
        "AD Deception Validation Planned",
        "Info",
        "Operator reviewed deception and honey-principal validation workflow.",
        "Deploy monitored decoys and validate SIEM/MDI alert fidelity.",
    )


def run():
    print_banner("AD ADVANCED PLAYBOOK", "WDAC · MDE/MDI · WMI filters · trusts · RACE · deception")
    input_or_session("domain", "Domain")
    input_or_session("dc_ip", "DC IP")

    print(f"""
  {BABY_BLUE}[1]{RST} PDF coverage map
  {BABY_BLUE}[2]{RST} WDAC / AppLocker / CLM workflow
  {BABY_BLUE}[3]{RST} MDE / MDI detection-aware checklist
  {BABY_BLUE}[4]{RST} WMI filter abuse / review workflow
  {BABY_BLUE}[5]{RST} Advanced cross-forest trust map
  {BABY_BLUE}[6]{RST} RACE-style security descriptor review
  {BABY_BLUE}[7]{RST} Deception / honey principal validation
  {BABY_BLUE}[A]{RST} Show all
  {LIGHT_PINK}[0]{RST} Back
""")
    c = input(f"  {LIGHT_PINK}Choice:{RST} ").strip().upper()

    actions = {
        "1": _coverage,
        "2": _wdac,
        "3": _mdi_mde,
        "4": _wmi_filters,
        "5": _cross_forest,
        "6": _race_sd,
        "7": _deception,
    }
    if c == "0":
        return
    if c == "A":
        for fn in actions.values():
            fn()
    elif c in actions:
        actions[c]()
    else:
        warn("Invalid choice")

    pause()
