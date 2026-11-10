# Operations, recovery and installation status

## What this repository contains

This is a source snapshot, not a backup of the live machine and not a reproducible distribution. It excludes runtime state and credentials. The snapshot cannot be verified against the live machine while server access is unavailable. Source in `src/` includes device-specific paths and assumptions and is provided for inspection and development.

## Required before a fresh installation can be supported

1. Inventory and review the deployed `/opt/pi-dashboard`, related systemd units, `/usr/local/bin/pi-*` helpers and `/opt/pi-stack` container definitions. The repository does not contain the deployed helpers or stack definitions.
2. Recover the actual image assets and compare checksums of deployed source with this snapshot. `logo.png` is absent; the included `logo.svg` is from a preview and is not a verified replacement.
3. Replace embedded host names, usernames, paths, network assumptions and domains with validated configuration. Inspect privilege boundaries and independently review authentication, terminal access, file operations, shell execution, Docker access and tunnel exposure.
4. Define and pin supported OS versions, architectures, packages and image versions. Known Python dependencies include Paramiko and PyKeePass; optional capabilities require external executables and libraries. This list is not a complete bill of materials.
5. Create installation and upgrade tooling that initializes a unique admin credential and TLS certificate, configures a dedicated service identity and narrowly scoped privileges, validates dependencies, avoids overwriting existing installations and supports rollback.
6. Document the data layout and test backup and restore of KDBX, app data, service volumes and secrets on a disposable host. Never publish those data.
7. Test a clean installation and upgrade on supported models, including a lower-memory Pi; verify browser, services, terminal, SSH pinning, files and remote node provisioning. Document verified limits.

No one-click installation command is supplied until these prerequisites are met.

## Existing deployments

Before any update, create and verify an independent backup, record the installed versions and systemd configuration, and retain a tested rollback. Do not replace the live directory with this snapshot: host scripts, stack configuration, credentials and runtime files are separate and may be needed by the running service. The old experimental upgrade scripts are intentionally excluded from this snapshot.

## Networking and access

The primary panel and node panel provide privileged control. Restrict access to a trusted LAN or a carefully configured tailnet. Use TLS, separate high-entropy credentials, verified certificate fingerprints and SSH host-key pinning. A subnet router may expose devices in its advertised network to authorized tailnet clients, subject to route approval and access policies; only announce networks you control or are authorized to access. Validate routes and firewall behavior from both the host and the particular Docker container. Overlapping subnets can cause traffic to reach the wrong network.

The lightweight node's file page is limited to viewing and downloading from `/home`; access is subject to the privileges of its service. Main-panel SSH transfers have lower size limits. Telegram, cloud tunnels and AI providers transmit data to third-party services when enabled; review configuration and destination before use.

## Tests included in this snapshot

Only basic source syntax checks are documented in the README. There is no packaged integration test suite, dependency lockfile, security assessment, automated installer test or verified restoration drill in this snapshot.
