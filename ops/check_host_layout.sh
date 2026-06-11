#!/usr/bin/env bash
set -euo pipefail

STRICT=0
if [[ "${1:-}" == "--strict" ]]; then
  STRICT=1
fi

SS_BIN="${SS_BIN:-ss}"
CADDY_BIN="${CADDY_BIN:-caddy}"
CADDYFILE_PATH="${CADDYFILE_PATH:-/etc/caddy/Caddyfile}"
CADDY_SNIPPET_PATH="${CADDY_SNIPPET_PATH:-/etc/caddy/Caddyfile.d/xauex-dashboard.caddy}"
PIHOLE_COMPOSE_PATH="${PIHOLE_COMPOSE_PATH:-/root/pihole/docker-compose.yml}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"

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

expect_listener() {
  local address="$1"
  "$SS_BIN" -ltn | awk '{print $4}' | grep -Fxq "$address" || fail "missing listener on $address"
  pass "listener present on $address"
}

expect_no_listener() {
  local address="$1"
  if "$SS_BIN" -ltn | awk '{print $4}' | grep -Fxq "$address"; then
    fail "unexpected listener on $address"
  fi
  pass "no listener present on $address"
}

check_caddy_layout() {
  local caddyfile="$CADDYFILE_PATH"
  local caddy_snippet="$CADDY_SNIPPET_PATH"
  if [[ ! -x "$CADDY_BIN" ]] && ! command -v "$CADDY_BIN" >/dev/null 2>&1; then
    pass "caddy not installed on this host"
    return
  fi
  [[ -f "$caddyfile" ]] || fail "missing $caddyfile"
  [[ -f "$caddy_snippet" ]] || fail "missing $caddy_snippet"
  grep -Fxq 'import /etc/caddy/Caddyfile.d/*.caddy' "$caddyfile" || fail "missing Caddy include for /etc/caddy/Caddyfile.d/*.caddy"
  grep -q 'auto_https disable_redirects' "$caddyfile" || warn "Caddy global options do not disable automatic redirects"
  grep -q 'protocols h1 h2' "$caddyfile" || warn "Caddy global options do not pin protocols to h1/h2"
  "$CADDY_BIN" validate --config "$caddyfile" >/dev/null 2>&1 || fail "caddy config validation failed"
  grep -Fq 'http://10.8.0.1:8089 {' "$caddy_snippet" || fail "missing VPN compatibility redirect for 10.8.0.1:8089"
  grep -Fq 'http://10.9.0.1:8089 {' "$caddy_snippet" || fail "missing VPN compatibility redirect for 10.9.0.1:8089"
  pass "caddy root config validates and imports repo-managed snippets"
  expect_listener "127.0.0.1:8089"
  expect_listener "10.8.0.1:80"
  expect_listener "10.8.0.1:443"
  expect_listener "10.8.0.1:8089"
  expect_listener "10.9.0.1:80"
  expect_listener "10.9.0.1:443"
  expect_listener "10.9.0.1:8089"
  expect_no_listener "109.122.223.127:8089"
}

check_window_timer_layout() {
  local signal_specs=(
    "morning|Mon-Fri *-*-* 07:55:00 Europe/London|xauex-window-signal@morning.service"
    "midday|Mon-Fri *-*-* 11:25:00 Europe/London|xauex-window-signal@midday.service"
    "us_open|Mon-Fri *-*-* 08:40:00 America/New_York|xauex-window-signal@us_open.service"
  )
  local confirm_specs=(
    "morning|Mon-Fri *-*-* 07:59:00 Europe/London|xauex-window-confirm@morning.service"
    "midday|Mon-Fri *-*-* 11:29:00 Europe/London|xauex-window-confirm@midday.service"
    "us_open|Mon-Fri *-*-* 08:44:00 America/New_York|xauex-window-confirm@us_open.service"
  )
  local legacy_templates=(
    "$SYSTEMD_DIR/xauex-window-signal@.timer"
    "$SYSTEMD_DIR/xauex-window-confirm@.timer"
  )

  local template_path
  for template_path in "${legacy_templates[@]}"; do
    [[ ! -e "$template_path" ]] || fail "legacy template timer still present: $template_path"
  done

  local spec timer_path window_label on_calendar unit_name on_calendar_count
  for spec in "${signal_specs[@]}" "${confirm_specs[@]}"; do
    IFS='|' read -r window_label on_calendar unit_name <<<"$spec"
    timer_path="$SYSTEMD_DIR/${unit_name%.service}.timer"
    [[ -f "$timer_path" ]] || fail "missing window timer: $timer_path"
    on_calendar_count="$(grep -c '^OnCalendar=' "$timer_path")"
    [[ "$on_calendar_count" -eq 1 ]] || fail "expected exactly one OnCalendar in $timer_path, found $on_calendar_count"
    grep -Fxq "OnCalendar=$on_calendar" "$timer_path" || fail "unexpected OnCalendar in $timer_path"
    grep -Fxq "Unit=$unit_name" "$timer_path" || fail "unexpected Unit target in $timer_path"
  done

  pass "window timers installed with per-slot schedules"
}

check_pihole_layout() {
  local compose_path="$PIHOLE_COMPOSE_PATH"
  if [[ ! -f "$compose_path" ]]; then
    pass "Pi-hole compose not present on this host"
    return
  fi
  if [[ "${EUID:-$(id -u)}" -ne 0 && "$compose_path" == /root/* ]]; then
    warn "run as root to verify the Pi-hole host layout"
  fi
  if grep -Eq '(^|[^0-9])80:80(/tcp)?([^0-9]|$)' "$compose_path"; then
    fail "Pi-hole still exposes port 80 directly; move admin to 8081 before using XAUEX VPN HTTPS"
  fi
  if grep -Eq '10\.8\.0\.1:8081:80|10\.9\.0\.1:8081:80' "$compose_path"; then
    pass "Pi-hole admin is offloaded from VPN port 80"
  else
    warn "Pi-hole compose exists but no 8081 admin mapping was detected"
  fi
  expect_listener "10.8.0.1:8081"
  expect_listener "10.9.0.1:8081"
}

check_caddy_layout
check_window_timer_layout
check_pihole_layout
