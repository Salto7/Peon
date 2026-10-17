# Report JSON schema

The `ticker` field contains the company’s stock market ticker symbol. The JSON
key remains `ticker` for compatibility with the existing CLI and reports.

`uv run python main.py` (full pipeline) emits an object shaped like:

```json
{
  "query": "Example Corp",
  "generated_at": "2026-10-11T19:00:00Z",
  "parent": {
    "name": "Example Corp",
    "legal_name": "Example Corporation",
    "cik": "0001234567",
    "ticker": "EXAM",
    "primary_domain": "example.com",
    "sources": ["https://www.sec.gov/..."]
  },
  "entities": [
    {
      "name": "Example Brand LLC",
      "type": "subsidiary",
      "confidence": "high",
      "relationship_verified": true,
      "evidence": "Listed in Exhibit 21 of 10-K",
      "sources": ["https://www.sec.gov/Archives/..."],
      "domains": [
        {
          "domain": "examplebrand.com",
          "relationship_verified": true,
          "evidence": "Copyright footer matches legal name",
          "sources": ["https://examplebrand.com/"]
        }
      ]
    }
  ],
  "domains": [
    {
      "domain": "example.com",
      "discovery": {
        "relationship_verified": true,
        "evidence": "Provided via --domain",
        "confidence": "high",
        "sources": []
      },
      "rdap": {
        "registrar": "Example Registrar Inc.",
        "registrant_org": "Example Corporation",
        "rdap_url": "https://rdap.example/domain/example.com"
      },
      "whois": { "used_fallback": false, "raw_excerpt": null },
      "azure_tenant": {
        "tenant_found": true,
        "tenant_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
        "issuer": "https://login.microsoftonline.com/.../v2.0"
      },
      "asn": {
        "resolved_ips": ["203.0.113.10"],
        "asns": [
          {
            "asn": 64500,
            "name": "EXAMPLE-AS",
            "description": "Example Corporation",
            "prefixes": [{ "prefix": "203.0.113.0/24" }],
            "observed_ips": ["203.0.113.10"],
            "sampled_ips": [
              {
                "ip": "203.0.113.10",
                "rdap": { "org": "Example Corporation", "name": "EXAMPLE-NET" },
                "whois": { "used": true, "org": "Example Corporation", "netname": "EXAMPLE-NET" },
                "ownership": {
                  "matched": true,
                  "confidence": "high",
                  "hits": ["example"],
                  "evidence": "Org/netname tokens match"
                }
              }
            ],
            "ownership_verified": true,
            "ownership_confidence": "high"
          }
        ]
      }
    }
  ],
  "urlscan": {
    "queries": ["domain:example.com"],
    "result_count": 12,
    "domains": [
      {
        "domain": "cdn.example.com",
        "relation": "subdomain_tree",
        "ips": ["203.0.113.20"],
        "asns": ["64500"],
        "sources": ["https://urlscan.io/result/.../"]
      }
    ]
  },
  "playwright": {
    "queries": ["\"Example Corp\" subsidiaries"],
    "domains": [{ "domain": "examplebrand.com", "confidence": "low" }],
    "hit_count": 8,
    "error": null
  },
  "dnslytics": { "tags": ["GTM-XXXX"], "domains": [] },
  "tenant_domains": {
    "tenant_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
    "display_name": "Example Corporation",
    "default_domain": "example.onmicrosoft.com",
    "domain_count": 12,
    "domains": [
      {
        "domain": "examplebrand.com",
        "relation": "entra_verified",
        "confidence": "high",
        "sources": ["https://idefixtools.com/tenant-lookup.html"]
      }
    ]
  },
  "dangling_domains": [
    {
      "domain": "oldbrand-forsale.com",
      "tenant_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
      "matched_via": "example.com",
      "evidence": "Parked domain still verified in company Entra tenant"
    }
  ],
  "summary": {
    "company": "Example Corporation",
    "ticker": "EXAM",
    "cik": "0001234567",
    "primary_domain": "example.com",
    "entra_tenant_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
    "domains": [
      {
        "domain": "example.com",
        "sources": ["https://…"],
        "evidence": "Primary / query domain",
        "confidence": "high",
        "verified": true
      }
    ],
    "entities": [
      {
        "name": "Example Brand LLC",
        "domains": [],
        "sources": ["https://www.sec.gov/…"],
        "evidence": "Exhibit 21 …",
        "confidence": "high",
        "verified": true
      }
    ],
    "counts": { "domains": 1, "entities": 1, "dangling": 0 }
  },
  "summary_text": "Company: Example Corporation\n…",
  "notes": [],
  "errors": []
}
```

Domain rows may also include `is_parked`, `dangling_entra`, `dangling_entra_detail`.

Use `--summary-only` on the CLI to emit just `summary`. Agents should present
`summary` first and critically discard parser junk from the full report.

## Field constraints

- `type`: `subsidiary` | `brand` | `dba` | `affiliate` | `parent`
- `confidence`: `high` | `medium` | `low`
- `urlscan.domains[].relation`: `same_registrable` | `subdomain_tree` | `related`
- Omit unknown optional fields or set them to `null`; never invent values
- Do not invent subsidiary domains from legal names alone
- ASN sampling must not imply a full prefix inventory or port scan
