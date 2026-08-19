# License — XAUEX

## This Project

XAUEX is **proprietary software** owned by Magrathean UK Ltd.

> See [`LICENSE`](./LICENSE) for the full proprietary licence text.
> Copyright © 2026 Magrathean UK Ltd. All rights reserved.

This file (`license.md`) is the **third-party notice and component inventory** for XAUEX. It does not grant any licence to the XAUEX source itself; the XAUEX source is governed exclusively by [`LICENSE`](./LICENSE).

---

## Third-Party Dependencies

### Python Runtime & Trading Pipeline — `requirements.txt`

| Package | License | Purpose / Declared in |
|---|---|---|
| `flask` | BSD-3-Clause | Web dashboard runtime |
| `flask-cors` | MIT | CORS header support |
| `waitress` | ZPL-2.1 | Production WSGI server |
| `openai` | Apache-2.0 | LLM context and judgment engine |
| `pydantic` | MIT | Schema validation and data modeling |
| `httpx` | BSD-3-Clause | Asynchronous HTTP transport |
| `google-auth` | Apache-2.0 | Google Cloud authentication |
| `feedparser` | BSD-2-Clause | Macro and news feed ingestion |
| `beautifulsoup4` | MIT | HTML structured parsing |
| `qdrant-client` | Apache-2.0 | Vector memory and search |
| `ctrader-open-api` | Apache-2.0 / MIT | cTrader Open API broker bridge |
| `aiohttp` | Apache-2.0 | Async network client |
| `rich` | MIT | Terminal formatting and logs |
| `schedule` | MIT | Job runner and window timers |

### Rust Tick Parser Core — `xauex/tick_parser/Cargo.toml`

| Package | License | Declared in |
|---|---|---|
| `pyo3` | MIT OR Apache-2.0 | `xauex/tick_parser/Cargo.toml` |
| `chrono` | MIT OR Apache-2.0 | `xauex/tick_parser/Cargo.toml` |

---

## License Obligations Summary

| License | Action required on redistribution |
|---|---|
| MIT | Retain copyright notice and licence text |
| Apache-2.0 | Retain NOTICE file (if any) and licence text |
| BSD-2-Clause / BSD-3-Clause | Retain copyright notice, conditions and disclaimer |
| ZPL-2.1 | Retain copyright notice and licence terms |
