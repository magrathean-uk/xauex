# Contributing

Keep changes focused on an observable problem. For substantial changes, explain the intended behavior and affected runtime boundary before implementation.

## Prepare and check

1. Use the environment and commands in [docs/development.md](../docs/development.md).
2. Preserve execution gates, command validation, operator controls and the DSA shadow boundary described in [AGENTS.md](../AGENTS.md).
3. Add or update meaningful regression coverage for behavior changes. Run the focused tests, then the root pytest suite and relevant lint checks. Documentation-only changes need link, path and command checks.
4. Update the affected guide when changing environment variables, schedules, wrappers or operator behavior.
5. Explain the problem, resulting behavior, checks performed and any remaining acceptance gap in the pull request.

Do not use a live broker account, deployed runtime files or service restarts as routine test fixtures. Deployment and broker checks are separate from local validation. Include only redacted diagnostics and synthetic examples in issues and tests.

## Rights and attribution

Read [docs/legal/overview.md](../docs/legal/overview.md), [LICENSE](../LICENSE) and [docs/legal/third-party-notices.md](../docs/legal/third-party-notices.md) before contributing. Submit only material you have the right to contribute, and preserve existing notices and third-party attribution. Do not change license grants, ownership notices or package license declarations as incidental cleanup.

This guide adds no contributor assignment or new licensing agreement. Questions about the existing licensing documents belong with the contacts listed in those documents.

## Getting help

Use [SUPPORT.md](SUPPORT.md) for ordinary questions and reproducible bug reports. Report vulnerabilities privately using [SECURITY.md](SECURITY.md), not a public issue.
