# Installation, configuration and recovery

[Русский](INSTALLATION.ru.md) · [Architecture](ARCHITECTURE.md) · [Usage](USER-GUIDE.md)

## Current status: a clean installation is not available

The repository is a partial source snapshot, not an installer or a complete backup. Do **not** deploy it over a working machine or expect the steps below to recreate the owner's device. The deployed Docker Compose files, `/usr/local/bin/pi-*` helpers, service unit, certificates, configuration, original `logo.png` and runtime state have not been recovered or verified. Service names and paths in source are device-specific. There is no verified fresh-install, upgrade, rollback or restore test.

## 1. Prepare a target for eventual deployment

- Use a supported Raspberry Pi with Raspberry Pi OS or compatible Debian, adequate storage and cooling, and administrator access. No tested model/OS compatibility matrix is available.
- Set up a trusted local network and SSH access. If remote access is needed, enroll the device in the intended Tailscale network and verify recovery access before changing routes or firewall rules.
- Decide which workloads should run; a Pi 3 with 1 GiB cannot be assumed to support the same workload as a Pi 5 with 8 GiB. Heavy applications should be started on demand.
- Install Docker, Python 3 and system integrations **only after** reviewing the future installer, versions and deployment configuration. Python imports include `paramiko` and `pykeepass`; various features depend on Docker, NetworkManager, Tailscale, cloudflared, rclone, CUPS and other tools. This list is illustrative, not a tested dependency specification.

## 2. Inspect the source without installation

```sh
git clone https://github.com/sm0k3net/pi-control.git
cd pi-control
python3 -m compileall -q src
node --check src/app.js
node --check src/filemanager.js
```

The repository is private unless its owner changes visibility; clone access requires GitHub authorization. These commands perform syntax checks only. The server cannot be safely started just by cloning: it requires missing configuration, certificates, system setup and helper scripts.

## 3. Inventory an existing installation

With authorized access, inventory the deployed application, service units, helpers, container images/Compose definitions, persisted volumes and network settings **without printing credentials**. Compare source checksums to this repository and document versions. Sensitive files—including dashboard environment files, KDBX, tunnel tokens, private keys, network credentials, backups and database contents—must remain outside Git. Preserve the original files and take a tested independent backup before any update.

## 4. Complete packaging before production use

1. Recover and review the deployed service and helper scripts and sanitized stack definitions.
2. Replace hard-coded username, hostname, paths, domain, container names and ports with validated configuration.
3. Create unique credentials and certificates per device, with restricted file permissions. Define the service account and least-privilege access to Docker and host commands.
4. Pin dependencies, include third-party license notices and provide an idempotent installation and migration procedure.
5. Test clean install, update, failure rollback and complete restoration on disposable supported devices. Publish a compatibility matrix and resource requirements.
6. Document configuration, monitoring, firewall bindings and backup/restore from verified deployed files.

## Configure an existing deployment

The following are **usage-level instructions for an already working deployment**, not commands to bootstrap a new one:

1. Open the panel at its actual HTTPS address on a trusted LAN or Tailscale network; verify its certificate fingerprint by a trusted channel. Sign in with the separately provisioned panel password.
2. In **Settings**, review network interfaces, Wi-Fi profiles and access addresses. Test an alternative access path before switching Wi-Fi or default routes.
3. In **Services**, start only what is required. Configure Agent Zero, n8n, Nextcloud, ONLYOFFICE and other services in their own interfaces with separately managed credentials and persistent data.
4. In **Notifications**, configure a Telegram bot token and destination chat ID, enable notifications and send a test. See [alerts](USER-GUIDE.md#telegram-alerts).
5. In **Pi nodes**, verify an SSH host-key fingerprint through a trusted channel, add credentials manually or through KDBX, inspect the target, and run auto-setup only on an authorized device. Verify the node certificate and test node access after deployment.
6. Set up backups to a separate device, verify the backup contents and perform a restoration test in an isolated environment. Cloud backup requires a separately configured rclone remote and any missing host helpers.

Do not commit configuration, secrets, vaults, databases, tokens, private keys, local logs, screenshots containing sensitive data, or backups. Treat private repositories as shared environments, not vaults.
