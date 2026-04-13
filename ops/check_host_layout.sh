#!/usr/bin/env bash
set -euo pipefail

STRICT=0
if [[ "${1:-}" == "--strict" ]]; then
  STRICT=1
fi

fail() {
  echo "[FAIL] $*" >&2
  exit 1
}

warn() {
  echo "[WARN] $*" >&2
  if [[ "$STRICT" -eq 1 ]]; then
    exit 1
  fi
}

pass() {
  echo "[OK] $*"
}

check_caddy_layout() {
  local caddyfile="/etc/caddy/Caddyfile"
  if ! command -v caddy >/dev/null 2>&1; then
    pass "caddy not installed on this host"
    return
  fi
  [[ -f "$caddyfile" ]] || fail "missing $caddyfile"
  [[ -f /etc/caddy/Caddyfile.d/xauex-dashboard.caddy ]] || fail "missing /etc/caddy/Caddyfile.d/xauex-dashboard.caddy"
  grep -Fxq 'import /etc/caddy/Caddyfile.d/*.caddy' "$caddyfile" || fail "missing Caddy include for /etc/caddy/Caddyfile.d/*.caddy"
  grep -q 'auto_https disable_redirects' "$caddyfile" || warn "Caddy global options do not disable automatic redirects"
  grep -q 'protocols h1 h2' "$caddyfile" || warn "Caddy global options do not pin protocols to h1/h2"
  caddy validate --config "$caddyfile" >/dev/null 2>&1 || fail "caddy config validation failed"
  pass "caddy root config validates and imports repo-managed snippets"
}

check_pihole_layout() {
  local compose_path="/root/pihole/docker-compose.yml"
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    warn "run as root to verify the Pi-hole host layout"
  fi
  if [[ ! -f "$compose_path" ]]; then
    pass "Pi-hole compose not present on this host"
    return
  fi
  if grep -Eq '(^|[^0-9])80:80(/tcp)?([^0-9]|$)' "$compose_path"; then
    fail "Pi-hole still exposes port 80 directly; move admin to 8081 before using XAUEX VPN HTTPS"
  fi
  if grep -Eq '10\.8\.0\.1:8081:80|10\.9\.0\.1:8081:80' "$compose_path"; then
    pass "Pi-hole admin is offloaded from VPN port 80"
  else
    warn "Pi-hole compose exists but no 8081 admin mapping was detected"
  fi
}

check_caddy_layout
check_pihole_layout
