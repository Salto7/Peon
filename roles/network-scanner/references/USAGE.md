# network-scanner usage

## CLIs
`nmap` is auto-provisioned from **`tools/catalog`** by the worker.

## Pack layout
```
roles/network-scanner/
├── ROLE.yaml
├── KNOWLEDGE.md
├── assets/run.py      # optional thin entry (no scripts/)
└── references/
```

## Output
Use **XML only**: `-oX workspace/<host>-scan.xml`

Leave the file as-is. The **analyzer** reads raw nmap XML for the report.
Do not filter ports, do not write `findings_queue.jsonl`, do not use
`-oN` / `-oG` / `-oA`.

## Rules
- Match requested port scope — never `-p-` / `1-65535` unless explicitly asked
- One scan per host/objective
- Reuse existing XML — do not re-scan just to regenerate output
