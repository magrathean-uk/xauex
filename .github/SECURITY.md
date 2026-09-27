# Security Policy

## System and Scope

XAUEX is an XAUUSD signal and cTrader demo-execution runtime. This policy records security-relevant repository behavior. It does not establish the state, exposure, or health of any deployment.

The covered code includes the signal and execution path, local dashboard and health endpoints, runtime files, credential handling, and the optional DSA research sidecar. Findings in those areas are reportable when they can realistically expose protected data, alter a command or order, bypass a safety gate, or expand access beyond the declared local and VPN-only design.

## Reporting a Vulnerability

The repository declares these reporting routes:

- GitHub private vulnerability reporting.
- `contact@magrathean.uk` with subject `SECURITY: XAUEX`.

Do not publish broker API credentials, private keys, live trading account numbers, webhook URLs, database snapshots, or exploit details.

No response time, acknowledgement period, or private-advisory availability is promised here.

## Threat Model and Trust Boundaries

- Credentials, access tokens, dashboard bearer tokens, command secrets, and service-account material are secrets. They must not enter version control, logs, reports, or vulnerability disclosures.
- The dashboard and health services are designed to bind to loopback. The checked-in ingress example limits proxying to loopback over VPN interfaces. Deployment configuration must be verified independently.
- Read-only dashboard routes expose runtime state, evidence, positions, journals, and account-derived data. Manual control routes can queue commands only after a configured dashboard bearer token and manual-command secret are accepted.
- Signed manual commands must remain authenticated, scoped to supported command shapes, fresh, and non-replayable before the runtime consumes them.
- `cmd.json` is execution-sensitive. Signal writers preserve its kill switch, and runtime file helpers use bounded reads, reject symlinks, and write sensitive JSON atomically with restrictive file permissions.
- The DSA sidecar is optional and disabled by default. Its default base URL is local and its default result is shadow-only, but environment configuration can change both. The intended boundary is that it supplies research evidence only and does not write XAUEX command files or bypass signal parsing, confirmation, kill-switch, or risk gates.

## Security Invariants

- An unauthenticated caller cannot enable or use manual dashboard controls.
- A malformed, expired, unsigned, tampered, or replayed manual command is not accepted for execution.
- A missing or corrupt command-replay ledger fails closed for the relevant command.
- Dashboard API responses retain the configured security headers and no-store cache policy.
- Secrets and runtime records retain the repository's documented handling boundaries.
- Confirmation, freshness, spread, risk, session, symbol, replay, and kill-switch gates are not bypassed.

## Reportable Findings and Severity Context

Report issues that can plausibly:

- disclose a credential, token, command secret, runtime record, account data, or other non-public operational data;
- permit a dashboard action without its intended authentication or command verification;
- alter a command, bypass a trading or replay gate, or cause an order outside the declared demo boundary;
- expose a loopback-only service to an unintended network; or
- defeat the intended DSA sidecar separation or make its evidence affect an XAUEX command or execution path.

Reachability, affected deployment configuration, and whether the issue can influence an order or disclose non-public data determine severity. The code and its tests show intended controls, not evidence that a deployed instance enforces them.

## Scope and Safe Harbour

MAGRATHEAN UK LTD will not pursue a good-faith researcher for security disclosures that:

- Target non-production test environments or researcher-owned instances;
- Avoid denial of service, data corruption, or execution of real-money orders;
- Report promptly and allow reasonable time for remediation;
- Do not condition disclosure on financial compensation.

## Out of Scope and Limitations

Trading outcomes, market predictions, strategy quality, and financial advice are not security findings by themselves. This document does not make a claim about a live broker account, deployment, VPN configuration, or the current availability of any reporting channel. A documentation review is not a security audit.
