# Public sources for corporate entity expansion

Read this when choosing registries or validating confidence.

## Primary (US / global public companies)

| Source | URL | Use for |
|--------|-----|---------|
| EDGAR search UI | https://www.sec.gov/edgar/search/ | Human browse / accession discovery |
| Company tickers | https://www.sec.gov/files/company_tickers.json | Name → CIK |
| Submissions API | https://data.sec.gov/submissions/CIK{cik10}.json | Filings index |
| Archives | https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/ | Exhibit 21, 10-K, 20-F |
| Full-text search | https://efts.sec.gov/LATEST/search-index | Keyword hits |

SEC requests a descriptive `User-Agent` with contact email. Scripts set
`CorpOSINT/1.0 (research; contact@acme.com)` — override via `SEC_USER_AGENT`.

**Subsidiary filings:** Exhibit 21 / EX-21 to Form 10-K (domestic) or similar
lists in 20-F. Prefer the most recent annual report.

## Domain discovery

| Source | URL / tool | Use for |
|--------|------------|---------|
| urlscan search | https://urlscan.io/api/v1/search/?q=domain:{d} | Related hosts, page IP/ASN |
| urlscan result UI | https://urlscan.io/result/{uuid}/ | Evidence links |
| Playwright + Bing | https://www.bing.com/search | Live subsidiary/brand/domain search |
| DNSLytics Search | https://search.dnslytics.com/search | Domain HTML tags (GTM) + reverse |
| DNSLytics query | `fqdn:{domain}` / `html.tag:GTM-XXXX` | Tag extract + reverse domains |
| DNSLytics API (paid) | https://api.dnslytics.net/v1/reverseganalytics/{tag} | Reverse GTM with `DNSLYTICS_API_KEY` |

Optional: `URLSCAN_API_KEY`, `DNSLYTICS_API_KEY`. Throttle DNSLytics (~2s).

## ASN / IP ownership

| Source | URL / tool | Use for |
|--------|------------|---------|
| Team Cymru | `whois -h whois.cymru.com " -v {ip}"` | Primary IP→ASN + prefix |
| RIPE Stat network-info | https://stat.ripe.net/data/network-info/data.json?resource={ip} | ASN fallback |
| RIPE Stat as-overview | https://stat.ripe.net/data/as-overview/data.json?resource=AS{asn} | ASN holder |
| RIPE Stat prefixes | https://stat.ripe.net/data/announced-prefixes/data.json?resource=AS{asn} | Announced prefixes |
| BGPView (optional) | https://api.bgpview.io/... | Extra ASN metadata when reachable |
| IP RDAP | https://rdap.org/ip/{ip} | Allocation / org |
| IP WHOIS | `whois {ip}` | OrgName / netname ownership check |

Only sample a few representative IPs per prefix (network hosts). Do not scan ranges.

## Corporate web

- Official homepage, `/about`, `/brands`, `/our-companies`, investor relations
- Press releases announcing acquisitions (medium until confirmed)
- Footer copyright / imprint / legal entity pages

## Other registries (use when jurisdiction known)

| Jurisdiction | Example registry |
|--------------|------------------|
| UK | https://find-and-update.company-information.service.gov.uk/ |
| EU / multi | National business registers; OpenCorporates (public pages) |
| DE | Unternehmensregister / Handelsregister extracts (public) |
| FR | https://www.sirene.fr / Infogreffe public |
| CA | SEDAR+ / provincial registries |
| AU | ASIC Connect |

Do not scrape behind logins or paywalls.

## Domain / DNS / identity

| Protocol | Endpoint pattern |
|----------|------------------|
| RDAP domain | https://rdap.org/domain/{domain} |
| WHOIS domain | system `whois {domain}` |
| Entra OIDC | https://login.microsoftonline.com/{domain}/v2.0/.well-known/openid-configuration |
| Entra realm | https://login.microsoftonline.com/getuserrealm.srf?login=user@{domain}&json=1 |
| IdefixTools UI | https://idefixtools.com/tenant-lookup.html | Tenant GUID/domain → org + domains |
| Idefix lookup API | https://func-idefix-tenantlookup.azurewebsites.net/api/lookup/{id} | displayName, defaultDomain |
| Idefix domains API | https://func-idefix-tenantlookup.azurewebsites.net/api/domains/{domain} | Verified domain list |
| Autodiscover fed | https://autodiscover-s.outlook.com/autodiscover/autodiscover.svc | GetFederationInformation domains |

## Confidence rubric

- **high** — Exhibit 21 / official IR; domain self-identifies; IP WHOIS org matches legal name
- **medium** — urlscan same registrable domain; press/investor PDF; ASN description match
- **low** — Playwright title heuristic; name similarity only
- **dangling** — parked/for-sale domain whose Entra tenant ID matches the company
- **reject** — CDN/shared hosting noise, unrelated homonym (parking is kept for Entra check)
