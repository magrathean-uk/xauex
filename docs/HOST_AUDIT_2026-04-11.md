# Host Audit 2026-04-11

## Summary

This host is functioning within the intended trust model: public mail stays public, VPN clients are trusted and laterally reachable, and admin services are protected primarily by nftables instead of service bind addresses. The main risks are stale credential handling, duplicated Docker firewall state, broad-listening admin services, and disk growth from caches and obsolete artifacts.

## Findings

### High

1. A live GitHub personal access token is stored in `/root/bolyki_github_config.env`.
   - Risk: full repo control if root or backup contents leak.
   - Action: move this to a root-only secret file outside ad hoc shell config and prefer ephemeral auth for git operations.

2. `magrathean-engine.service` runs as `root` and embeds secrets directly in the unit file.
   - File: `/etc/systemd/system/magrathean-engine.service`
   - Risk: plaintext secret exposure in service definitions and unnecessarily broad runtime privilege.
   - Action: move secrets into a root-only `EnvironmentFile=` and run as a dedicated service user unless root is strictly required.

### Medium

3. Cockpit on `9090` and `xrdp` on `3389` listen broadly and depend on nftables for VPN-only enforcement.
   - Validation: `ss -tulpn`
   - Risk: exposure if firewall policy drifts or reloads incorrectly.
   - Action: keep nftables policy authoritative, and if feasible later, bind these services only to VPN interfaces.

4. Docker-managed iptables-nft state contains duplicated chains and repeated NAT/filter rules.
   - Validation: `nft list ruleset`
   - Risk: troubleshooting difficulty, policy ambiguity, and accidental regressions during Docker/firewall maintenance.
   - Action: normalize Docker firewall state in a controlled maintenance window while preserving host-owned VPN forwarding and NAT.

5. `avahi-daemon` is active on a VPS.
   - Risk: extra discovery surface on a public host.
   - Action: keep only if VPN-side discovery is intentional enough to justify it; otherwise disable it.

### Low / Operational

6. Root filesystem pressure is real but manageable.
   - Validation: `/` at about `80%` used, with large consumers under `/var/lib`, `/root`, and the Oracle repo virtualenv.
   - Action: remove stale caches, obsolete repos, orphaned Docker artifacts, and tracked build exports; continue existing alerting.

7. The obsolete local repo `/home/bolyki/xauex` is still present even though live services now use `/home/bolyki/mirofish-gold-oracle`.
   - Risk: operator confusion and wasted disk.
   - Action: remove it after the canonical repo push is verified.

## Accepted Risks

- Public mail on `25`, `465`, `587`, and `993` is intentional.
- Passwordless sudo for `bolyki` and `bolykirescue` is intentional.
- VPN clients are intentionally trusted and mutually reachable.
- SSH on `40022`, Cockpit, and `xrdp` remain enabled for VPN-side administration.

## Current Controls

- Host-owned nftables blocks admin/app ports from `eth0` and preserves VPN egress/NAT independently of Docker.
- Backup jobs use `rustic`, fail visibly, and Teslamate backup includes restore validation.
- Root-disk and service-failure email alerts are in place.
- Weekly Docker cleanup and periodic VPN-egress checks are active.
