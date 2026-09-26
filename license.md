# License and Third-Party Notices

## Controlling Terms

The root [`LICENSE`](./LICENSE) contains the existing proprietary terms for XAUEX's main project-owned material, with an explicit exception for the separately licensed tick parser component. This page is an explanatory dependency inventory. It does not alter the root licence, a third-party component's own terms, or a package-level licence declaration.

The root licence expressly preserves the rights and obligations granted directly by third-party licences for third-party components. Distribution requires the actual applicable licence texts, copyright notices, and any required notices for the resolved components.

## Manifest-Derived Inventory

This table records every direct package declaration found in the checked-in manifests. It is not a complete dependency notice bundle: Python requirements are version ranges without a lockfile, and the repository does not contain installed Python distribution metadata or a complete bundle of third-party dependency licence texts. The component licence files below cover project-owned tick parser material, not the resolved dependency graph. The Rust lockfile resolves the Cargo graph but does not supply a repository-owned licence corpus.

| Manifest | Direct package declarations |
|---|---|
| `requirements.txt` | `flask>=3.1.3`, `flask-cors>=6.0.2`, `waitress>=3.0.2`, `openai>=2.31.0`, `charset-normalizer>=3.0.0`, `chardet>=5.0.0,<6.0.0`, `python-dotenv>=1.2.2`, `pydantic>=2.12.5`, `service_identity>=24.1.0`, `httpx>=0.28.1,<1.0`, `google-auth>=2.29.0,<3.0`, `feedparser>=6.0.12,<7.0`, `beautifulsoup4>=4.14.3,<5.0`, `qdrant-client>=1.17.1,<2.0`, `numpy>=2.2.6,<2.3`, `ctrader-open-api`, `protobuf`, `aiohttp>=3.13.5`, `textual>=8.2.3`, `rich>=14.3.4`, `schedule>=1.2.2` |
| `requirements-dev.txt` | Includes `requirements.txt`; `pytest>=9.0.3`, `pytest-asyncio`, `ruff>=0.15.12`, `mypy>=1.20.2`, `duka` |
| `requirements-ai-legacy.txt` | `zep-cloud==3.20.0`, `camel-oasis==0.2.5`, `camel-ai==0.2.90`, `PyMuPDF>=1.27.2.2` |
| `xauex/requirements.txt` | Includes `../requirements.txt` |
| `xauex/tick_parser/Cargo.toml` | `pyo3` version `0.22` with `extension-module`; `chrono` version `0.4` with `serde` |
| `xauex/tick_parser/pyproject.toml` | Build requirement `maturin>=1.0,<2.0` |

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

The repository contains no equivalent licence statement for the remaining direct declarations. Do not infer one from a package name, a version constraint, or a transitive dependency.

## Tick Parser Component Exception

Project-owned material under `xauex/tick_parser/` and its compiled forms is licensed, at the recipient's option, under the MIT License or the Apache License, Version 2.0. The SPDX expression is `MIT OR Apache-2.0`, preserving the existing declaration in `xauex/tick_parser/Cargo.toml`. Its `publish = false` field does not withdraw the grant.

See the [component notice](xauex/tick_parser/LICENSE.md), [MIT text](xauex/tick_parser/LICENSE-MIT) and [Apache-2.0 text](xauex/tick_parser/LICENSE-APACHE). The root proprietary restrictions do not apply to that component. This exception does not extend to XAUEX material outside the component directory. Dependencies and separately attributed third-party material keep their own terms and notices; earlier valid grants remain unaffected.

## Redistribution Checklist

- Resolve the exact Python and Rust dependency graphs for the distributed artefact.
- Collect each resolved component's licence, copyright, and required notice files from its authoritative distribution.
- Verify any `NOTICE`, source-offer, attribution, or copyleft obligations against the exact component version.
- Keep third-party terms separate from the root XAUEX proprietary licence and from [`TRADEMARKS.md`](./TRADEMARKS.md).

The following prior high-level summary is retained for the licences named above. It is not a substitute for the controlling text.

| Licence | Prior redistribution summary |
|---|---|
| MIT | Retain copyright notice and licence text |
| Apache-2.0 | Retain NOTICE file, if any, and licence text |
| BSD-2-Clause / BSD-3-Clause | Retain copyright notice, conditions and disclaimer |
| ZPL-2.1 | Retain copyright notice and licence terms |
