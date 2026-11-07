"""Soft Open Asset Model hints for the engagement graph.

Producers invent any subject type and relation label. This module only
supplies optional display labels / aliases — it never infers a type from a
value. Unknown types stay first-class.
"""

from __future__ import annotations

from dataclasses import dataclass

from peon.projects.targets import sanitize_label


@dataclass(frozen=True)
class AssetClass:
    slug: str
    oam: str
    label: str
    aliases: tuple[str, ...] = ()


CLASSES: tuple[AssetClass, ...] = (
    AssetClass(
        "fqdn", "FQDN", "FQDN",
        ("host", "hostname", "domain", "fqdn", "subdomain", "dns", "dns_name", "site"),
    ),
    AssetClass(
        "ip", "IPAddress", "IP address",
        ("ip", "ip_address", "ipv4", "ipv6", "address"),
    ),
    AssetClass(
        "netblock", "Netblock", "Netblock",
        ("netblock", "cidr", "subnet", "network", "net", "range"),
    ),
    AssetClass(
        "asn", "AutonomousSystem", "Autonomous system",
        ("asn", "as", "autonomous_system"),
    ),
    AssetClass(
        "wifi", "Wireless", "Wireless network",
        ("wifi", "wlan", "bssid", "essid", "ssid", "access_point"),
    ),
    AssetClass(
        "service", "Service", "Service",
        ("service", "port", "endpoint", "socket", "listener", "banner"),
    ),
    AssetClass("url", "URL", "URL", ("url", "uri", "link", "webpage", "page")),
    AssetClass(
        "certificate", "TLSCertificate", "TLS certificate",
        ("certificate", "cert", "tls", "tls_certificate", "ssl", "x509"),
    ),
    AssetClass(
        "organization", "Organization", "Organization",
        (
            "organization", "organisation", "company", "org", "vendor", "brand",
            "asset", "name", "business", "subsidiary", "team", "business_unit",
        ),
    ),
    AssetClass(
        "person", "Person", "Person",
        ("person", "employee", "individual", "people", "staff", "executive"),
    ),
    AssetClass("contact", "ContactRecord", "Contact record", ("contact", "contact_record")),
    AssetClass(
        "email", "Identifier (email)", "Email address",
        ("email", "email_address", "mail", "mailbox"),
    ),
    AssetClass("phone", "Phone", "Phone", ("phone", "telephone", "msisdn", "mobile")),
    AssetClass(
        "account", "Account", "Account",
        (
            "account", "username", "handle", "login", "user", "profile", "social",
            "samaccount", "upn", "principal",
        ),
    ),
    AssetClass(
        "identifier", "Identifier", "Identifier",
        ("identifier", "id", "token", "sid", "guid", "uuid", "object_id", "tenant_id"),
    ),
    AssetClass(
        "hash", "Identifier (hash)", "Hash",
        ("hash", "digest", "sha256", "sha1", "md5", "fingerprint", "ntlm", "netntlm"),
    ),
    AssetClass(
        "credential", "Credential", "Credential",
        (
            "credential", "password", "secret", "api_key", "apikey", "key",
            "ssh_key", "private_key", "cert_key", "bearer", "cookie",
        ),
    ),
    AssetClass(
        "session", "Session", "Session",
        ("session", "cookie_jar", "auth_session", "jwt", "oauth_token", "refresh_token"),
    ),
    AssetClass(
        "ticket", "Ticket", "Ticket",
        ("ticket", "kerberos", "tgt", "tgs", "kirbi", "ccache"),
    ),
    AssetClass(
        "cloud_resource", "CloudResource", "Cloud resource",
        (
            "cloud_resource", "cloud", "arn", "bucket", "s3", "blob", "azure_resource",
            "gcp_resource", "instance", "vm", "lambda", "serverless_function",
            "cloud_function", "subscription", "resource_group",
        ),
    ),
    AssetClass(
        "container", "Container", "Container",
        ("container", "docker", "pod", "image", "k8s", "kubernetes", "namespace"),
    ),
    AssetClass(
        "device", "Device", "Device",
        (
            "device", "host_os", "workstation", "server", "laptop", "endpoint",
            "machine", "computer", "iot", "appliance",
        ),
    ),
    AssetClass(
        "share", "Share", "Share",
        ("share", "smb", "nfs", "cifs", "unc", "file_share"),
    ),
    AssetClass(
        "process", "Process", "Process",
        ("process", "pid", "service_name", "daemon", "binary_running"),
    ),
    AssetClass(
        "ad_object", "DirectoryObject", "Directory object",
        (
            "ad_object", "ad", "ldap", "ou", "group", "security_group", "gpo",
            "domain_controller", "forest", "trust",
        ),
    ),
    AssetClass(
        "domain_record", "DomainRecord", "Domain registration",
        ("domain_record", "whois", "registration", "rdap"),
    ),
    AssetClass(
        "ipnet_record", "IPNetRecord", "Network registration",
        ("ipnet_record", "ipnet", "netrange", "inetnum"),
    ),
    AssetClass(
        "autnum_record", "AutnumRecord", "ASN registration",
        ("autnum_record", "autnum", "aut_num"),
    ),
    AssetClass(
        "product", "Product", "Product",
        (
            "product", "software", "package", "library", "technology", "tech",
            "framework", "application", "app", "cpe",
        ),
    ),
    AssetClass(
        "release", "ProductRelease", "Release",
        ("release", "version", "build", "product_release"),
    ),
    AssetClass(
        "repository", "Repository", "Repository",
        ("repository", "repo", "git", "source"),
    ),
    AssetClass(
        "vulnerability", "Vulnerability", "Vulnerability",
        (
            "vulnerability", "vuln", "cve", "ghsa", "advisory", "weakness",
            "misconfiguration", "finding_vuln",
        ),
    ),
    AssetClass(
        "file", "File", "File",
        (
            "file", "path", "artifact", "document", "binary", "sample", "malware",
            "pcap", "dump", "attachment", "evidence", "script", "payload",
        ),
    ),
    AssetClass(
        "location", "Location", "Location",
        ("location", "geo", "city", "country", "region", "place", "datacenter", "office"),
    ),
    AssetClass(
        "funds", "FundsTransfer", "Funds transfer",
        ("funds_transfer", "payment", "transaction", "wallet", "crypto_address"),
    ),
    AssetClass("other", "Other", "Other", ()),
)

_BY_SLUG = {c.slug: c for c in CLASSES}
_BY_ALIAS: dict[str, AssetClass] = {}
for _c in CLASSES:
    _BY_ALIAS[_c.slug] = _c
    for _a in _c.aliases:
        _BY_ALIAS.setdefault(_a, _c)

def _open_class(slug: str) -> AssetClass:
    """Synthesize a class for any producer type (function, path, uri, …).

    Exact catalogue slugs keep their soft OAM entry. Alias hits only borrow the
    OAM name — the producer slug stays the identity.
    """
    s = sanitize_label(slug, default="other") or "other"
    if s in _BY_SLUG:
        return _BY_SLUG[s]
    label = s.replace("_", " ").strip().title() or "Other"
    known = _BY_ALIAS.get(s)
    oam = known.oam if known is not None else s
    return AssetClass(slug=s, oam=oam, label=label)


def classify(typ: str, value: str = "") -> AssetClass:
    """Class for a stored asset — producer type only (``value`` ignored)."""
    del value
    return _open_class(typ or "other")


@dataclass(frozen=True)
class RelationSpec:
    label: str
    title: str
    sources: frozenset[str]
    targets: frozenset[str]
    oam: bool = True


def _spec(label: str, title: str, src: str, dst: str, *, oam: bool = True) -> RelationSpec:
    return RelationSpec(
        label,
        title,
        frozenset(src.split()) if src else frozenset(),
        frozenset(dst.split()) if dst else frozenset(),
        oam,
    )


_ANY = ""
RELATIONS: dict[str, RelationSpec] = {
    r.label: r
    for r in (
        _spec("a_record", "A record", "fqdn", "ip"),
        _spec("aaaa_record", "AAAA record", "fqdn", "ip"),
        _spec("cname_record", "CNAME", "fqdn", "fqdn"),
        _spec("ns_record", "NS record", "fqdn", "fqdn"),
        _spec("mx_record", "MX record", "fqdn", "fqdn"),
        _spec("srv_record", "SRV record", "fqdn", "fqdn"),
        _spec("ptr_record", "PTR record", "ip fqdn", "fqdn"),
        _spec("txt_record", "TXT record", "fqdn", _ANY),
        _spec("node", "Subdomain", "fqdn", "fqdn"),
        _spec("port", "Port", "ip fqdn device", "service"),
        _spec("contains", "Contains", "netblock", "ip netblock"),
        _spec("announces", "Announces", "asn", "netblock"),
        _spec(
            "registration", "Registration", "fqdn netblock asn ip",
            "domain_record ipnet_record autnum_record",
        ),
        _spec("managed_by", "Managed by", _ANY, "organization person contact"),
        _spec("associated_with", "Associated with", _ANY, _ANY),
        _spec(
            "legal_name", "Legal name",
            "domain_record ipnet_record autnum_record", "organization",
        ),
        _spec("contact", "Contact", "organization person domain_record", "email phone contact"),
        _spec("certificate", "Certificate", "service url fqdn", "certificate"),
        _spec("subject", "Subject", "certificate", "fqdn"),
        _spec("hosts", "Hosts", "ip fqdn service device", "url"),
        _spec(
            "owns", "Owns", "organization person",
            "fqdn netblock asn account product repository cloud_resource device credential",
        ),
        _spec("member_of", "Member of", "person account ad_object", "organization ad_object"),
        _spec(
            "uses", "Uses",
            "service url fqdn organization device cloud_resource", "product release",
        ),
        _spec(
            "located_at", "Located at",
            "organization person ip netblock device", "location",
        ),
        _spec(
            "authenticates_as", "Authenticates as",
            "credential session ticket", "account person",
        ),
        _spec(
            "has_access", "Has access",
            "account person credential session ticket",
            "service url share cloud_resource device ad_object repository",
        ),
        _spec(
            "admin_of", "Admin of",
            "account person", "device ad_object cloud_resource organization",
        ),
        _spec("trusts", "Trusts", "ad_object organization", "ad_object organization"),
        _spec(
            "runs_on", "Runs on",
            "service process container product", "device ip fqdn cloud_resource",
        ),
        _spec("stores", "Stores", "share cloud_resource device file", "credential file hash"),
        _spec(
            "vulnerable_to", "Vulnerable to",
            "service product device url fqdn cloud_resource", "vulnerability",
        ),
        _spec("exploits", "Exploits", "vulnerability file", "service product device"),
        _spec(
            "pivots_to", "Pivots to",
            "device account session", "device service share cloud_resource",
        ),
        _spec("ip_address", "Resolves to", "url", "ip", oam=False),
        _spec("domain", "On domain", "url", "fqdn", oam=False),
        _spec("same_file", "Same file", "file", "file", oam=False),
        _spec("hashes", "Hashes", "hash", "file", oam=False),
    )
}


def is_bare_port_value(typ: str, value: str) -> bool:
    """True when ``value`` is only a port digit for a ``port`` relation target."""
    return bool(value) and value.isdigit() and classify(typ, value).slug in RELATIONS["port"].targets


def relation_title(label: str) -> str:
    spec = RELATIONS.get(label)
    return spec.title if spec else label.replace("_", " ").capitalize()


def relation_status(rel: str, source: AssetClass, target: AssetClass) -> str:
    """``ok`` | ``unexpected`` | ``custom``.

    Soft OAM: only warn when *both* endpoints are catalogue classes and the
    pair violates the registered shape. Open producer types (function, path,
    …) make the edge unconstrained.
    """
    spec = RELATIONS.get(rel)
    if spec is None:
        return "custom"
    src_known = source.slug in _BY_SLUG and source.slug != "other"
    dst_known = target.slug in _BY_SLUG and target.slug != "other"
    if not (src_known and dst_known):
        return "ok"
    if spec.sources and source.slug not in spec.sources:
        return "unexpected"
    if spec.targets and target.slug not in spec.targets:
        return "unexpected"
    return "ok"

