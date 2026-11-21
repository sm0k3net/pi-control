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

## Service restart preferences

Install the managed server release before integrating startup preferences. On an existing controller, run the checked-in integration script against the deployed directory, then install and enable the boot unit. Initialize from observed service state once. This records currently running services without starting dormant tools.

    sudo python3 scripts/integrate-operations.py --root /opt/pi-dashboard
    sudo install -m 0644 systemd/pi-control-autostart.service /etc/systemd/system/
    sudo python3 /opt/pi-dashboard/dashboard_platform.py --initialize-services
    sudo systemctl daemon-reload
    sudo systemctl enable pi-control-autostart.service
    sudo systemctl restart pi-dashboard.service

Keep the controller wrappers and their container settings under version control. Panel actions update the requested state and the Docker restart policy. Native services are reconciled by the controller boot unit. Lightweight nodes use Docker restart policies on successful Start and Stop actions.

## Optional integrations

Install the official Bitwarden CLI using a supported Node.js runtime. Select the trusted HTTPS Bitwarden or Vaultwarden URL, use the account's personal API key, then unlock with its master password. The connector holds the temporary client session for five minutes and supports search and copying Login fields. Review the official [CLI guide](https://bitwarden.com/help/cli/).

    sudo npm install --prefix /opt/pi-control-tools --ignore-scripts @bitwarden/cli@2026.11.0

Install avahi-utils on the controller and on the lightweight nodes that should advertise themselves over mDNS. Run Avahi only on trusted LAN interfaces. The manual enrollment flow accepts private IPv4 or Tailscale endpoints. Compare the HTTPS SHA-256 fingerprint independently before entering node credentials. Bootstrap over SSH remains available when the selected host is authorized and the SSH host key is confirmed.

To back up to another Pi, upgrade both ends. Configure the controller's public age recipient and keep the recovery identity externally. From Settings, select a paired node and create a copy or verify a completed set. The controller spools encrypted archives under /var/lib/pi-control-backup-spool and needs local disk space. The receiver keeps completed sets under /opt/pi-node/backups. Download the complete set from the node's Backups page for an offline recovery exercise.

For a downloaded node backup, keep the two age archives and manifest in the same folder. Extract the verified set to a new local recovery directory without replacing live files:

    sudo python3 scripts/restore-pi-backup.py ./backup-SET ./recovery --identity /path/to/recovery-key

Check the contents before restoring service configuration or database data to a running installation.
