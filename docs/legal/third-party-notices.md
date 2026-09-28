# License and Third-Party Notices

## Controlling Terms

The root [`LICENSE`](../../LICENSE) contains the existing proprietary terms for XAUEX's main project-owned material, with an explicit exception for the separately licensed tick parser component. This page is an explanatory dependency inventory. It does not alter the root licence, a third-party component's own terms, or a package-level licence declaration.

The root licence expressly preserves the rights and obligations granted directly by third-party licences for third-party components. Distribution requires the actual applicable licence texts, copyright notices, and any required notices for the resolved components.

## Manifest-Derived Inventory

This table records every direct package declaration found in the checked-in manifests. It is not a complete dependency notice bundle: Python requirements are version ranges without a lockfile, and the repository does not contain installed Python distribution metadata or a complete bundle of third-party dependency licence texts. The component licence files below cover project-owned tick parser material, not the resolved dependency graph. The Rust lockfile resolves the Cargo graph but does not supply a repository-owned licence corpus.

| Manifest | Direct package declarations |
|---|---|
| `requirements.txt` | `flask>=3.1.3`, `flask-cors>=6.0.5`, `waitress>=3.0.2`, `openai>=3.19.2`, `charset-normalizer>=3.5.1`, `chardet>=5.2.0,<6.0.0`, `python-dotenv>=1.2.3`, `pydantic>=2.13.5`, `service_identity>=24.2.0`, `httpx>=0.28.1,<1.0`, `google-auth>=2.58.1,<3.0`, `feedparser>=6.0.14,<7.0`, `beautifulsoup4>=4.15.0,<5.0`, `qdrant-client>=1.19.1,<2.0`, `numpy>=2.2.6,<2.3`, `ctrader-open-api==0.9.2`, `protobuf==3.20.1`, `aiohttp>=3.14.3`, `textual>=8.2.8`, `rich>=15.0.0`, `schedule>=1.2.2` |
| `requirements-dev.txt` | Includes `requirements.txt`; `pytest>=9.1.1`, `pytest-asyncio>=1.4.0`, `ruff>=0.16.9`, `mypy>=2.3.1`, `duka==0.2.0`, `maturin>=1.15.0,<2.0` |
| `requirements-ai-legacy.txt` | `zep-cloud>=3.30.0`, `camel-oasis==0.2.5`, `camel-ai==0.2.78`, `PyMuPDF>=1.28.2` |
| `xauex/requirements.txt` | Includes `../requirements.txt` |
| `xauex/tick_parser/Cargo.toml` | `pyo3` version `0.29.2` with `extension-module`; `chrono` version `0.4.45` with `serde` |
| `xauex/tick_parser/pyproject.toml` | Build requirement `maturin>=1.15.0,<2.0` |

## Existing Repository Notice Statements

The following statements appeared in the prior checked-in inventory. They are retained as repository notice statements, but this documentation refresh did not independently verify package metadata or retrieve third-party licence texts.

| Package | Prior stated licence | Prior stated purpose |
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
| `pyo3` | MIT OR Apache-2.0 | Rust tick parser dependency |
| `chrono` | MIT OR Apache-2.0 | Rust tick parser dependency |

The registry metadata below supersedes these prior statements where they differ.

## Registry Licence Metadata

This table records the licence each direct declaration states in its registry metadata (PyPI, or crates.io for Rust crates) at the reference release shown. The reference release is the lowest version the current constraint permits. A later permitted version may carry different terms; check the exact resolved version before distribution.

| Package | Reference release | Licence in registry metadata |
|---|---|---|
| `flask` | 3.1.3 | BSD-3-Clause |
| `flask-cors` | 6.0.5 | MIT |
| `waitress` | 3.0.2 | ZPL-2.1 |
| `openai` | 3.19.2 | Apache-2.0 |
| `charset-normalizer` | 3.5.1 | MIT |
| `chardet` | 5.2.0 | GNU LGPL 2.1 or later (registry classifier: LGPLv2+; the 2.1-or-later terms are in the distribution's LICENSE file and source headers) |
| `python-dotenv` | 1.2.3 | BSD-3-Clause |
| `pydantic` | 2.13.5 | MIT |
| `service_identity` | 24.2.0 | MIT |
| `httpx` | 0.28.1 | BSD-3-Clause |
| `google-auth` | 2.58.1 | Apache-2.0 |
| `feedparser` | 6.0.14 | BSD-2-Clause |
| `beautifulsoup4` | 4.15.0 | MIT |
| `qdrant-client` | 1.19.1 | Apache-2.0 |
| `numpy` | 2.2.6 | BSD (licence classifier; the full terms are in the distribution's licence file) |
| `ctrader-open-api` | 0.9.2 | MIT |
| `protobuf` | 3.20.1 | BSD-3-Clause |
| `aiohttp` | 3.14.3 | Apache-2.0 AND MIT |
| `textual` | 8.2.8 | MIT |
| `rich` | 15.0.0 | MIT |
| `schedule` | 1.2.2 | MIT |
| `pytest` | 9.1.1 | MIT |
| `pytest-asyncio` | 1.4.0 | Apache-2.0 |
| `ruff` | 0.16.9 | MIT |
| `mypy` | 2.3.1 | MIT |
| `duka` | 0.2.0 | MIT (licence classifier) |
| `maturin` | 1.15.0 | MIT OR Apache-2.0 |
| `zep-cloud` | 3.30.0 | Apache-2.0 (the `LICENSE` file in the sdist and wheel; the registry metadata states none) |
| `camel-oasis` | 0.2.5 | Apache-2.0 |
| `camel-ai` | 0.2.78 | Apache-2.0 |
| `PyMuPDF` | 1.28.2 | GNU AGPL 3.0 or Artifex commercial licence |
| `pyo3` | 0.29.2 | MIT OR Apache-2.0 |
| `chrono` | 0.4.45 | MIT OR Apache-2.0 |

`chardet` (LGPL) is a runtime dependency. `PyMuPDF` (AGPL or commercial) is limited to the optional `requirements-ai-legacy.txt` set. Review both before distributing any bundle that includes them. Do not infer a licence for a transitive dependency from this table.

## Tick Parser Component Exception

Project-owned material under `xauex/tick_parser/` and its compiled forms is licensed, at the recipient's option, under the MIT License or the Apache License, Version 2.0. The SPDX expression is `MIT OR Apache-2.0`, preserving the existing declaration in `xauex/tick_parser/Cargo.toml`. Its `publish = false` field does not withdraw the grant.

See the [component notice](../../xauex/tick_parser/LICENSE.md), [MIT text](../../xauex/tick_parser/LICENSE-MIT) and [Apache-2.0 text](../../xauex/tick_parser/LICENSE-APACHE). The root proprietary restrictions do not apply to that component. This exception does not extend to XAUEX material outside the component directory. Dependencies and separately attributed third-party material keep their own terms and notices; earlier valid grants remain unaffected.

## Redistribution Checklist

- Resolve the exact Python and Rust dependency graphs for the distributed artefact.
- Collect each resolved component's licence, copyright, and required notice files from its authoritative distribution.
- Verify any `NOTICE`, source-offer, attribution, or copyleft obligations against the exact component version.
- Keep third-party terms separate from the root XAUEX proprietary licence and from [`trademarks.md`](./trademarks.md).

The following prior high-level summary is retained for the licences named above. It is not a substitute for the controlling text.

| Licence | Prior redistribution summary |
|---|---|
| MIT | Retain copyright notice and licence text |
| Apache-2.0 | Retain NOTICE file, if any, and licence text |
| BSD-2-Clause / BSD-3-Clause | Retain copyright notice, conditions and disclaimer |
| ZPL-2.1 | Retain copyright notice and licence terms |
