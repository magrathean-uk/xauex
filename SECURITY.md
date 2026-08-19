# Security Policy — XAUEX

## Private Reporting

Report vulnerabilities through GitHub private vulnerability reporting or email `contact@magrathean.uk` with subject `SECURITY: XAUEX`.

Do not publish broker API credentials, private keys, live trading account numbers, webhook URLs, database snapshots, or exploit details.

## Scope & Safe Harbour

Magrathean UK Ltd. will not pursue a good-faith researcher for security disclosures that:
- Target non-production test environments or researcher-owned instances;
- Avoid denial of service, data corruption, or execution of real-money orders;
- Report promptly and allow reasonable time for remediation;
- Do not condition disclosure on financial compensation.

## Operational Security Requirements

- Never commit `.env`, live cTrader credentials, or API secret tokens.
- Keep the operator dashboard bound to loopback (`127.0.0.1`) behind WireGuard or VPN-only Caddy ingress.
