# Pi Control

**A lightweight control panel for a portable Raspberry Pi workspace.**

[Русская версия](README.ru.md) · [Architecture](docs/ARCHITECTURE.md) · [Installation](docs/INSTALLATION.md) · [User guide](docs/USER-GUIDE.md) · [Node installer](docs/NODE-INSTALL.md) · [Source snapshot](src/)

> **Development snapshot, not a turnkey release.** This repository contains the available panel source, captured on 9 November 2026. It has not been reconciled with the running Raspberry Pi since capture. Host scripts, container definitions, service units, runtime configuration and some image assets are missing. A clean installation cannot yet reproduce the running system. Do not run this snapshot on an exposed network or use it as a production installer.

Pi Control is intended to make a Raspberry Pi useful as a portable companion for a phone or computer: manage selected containers, inspect device status, browse files, use a terminal, and connect additional Pi nodes. The interface is currently Russian; a complete English UI is not implemented.

## Product preview

These are actual screenshots supplied by the project owner from an earlier version. They show the Russian interface and may differ from the present source. Network addresses are omitted from the monitoring crop.

### Sign in

![Sign-in screen](docs/images/login.png)

### Overview

![Dashboard overview](docs/images/overview.png)

### Services

![Service cards](docs/images/services.png)

### Monitoring

![System monitoring](docs/images/monitoring.png)

![Storage breakdown](docs/images/storage.png)

### Two-pane files

![Two-pane file manager](docs/images/file-manager.png)

## Capabilities in this snapshot

- **Overview and monitoring:** CPU/load, memory, storage, temperature and system information, depending on host support.
- **Services:** status and controls for selected Docker services and host workflows. Service actions rely on host-specific commands and Docker resources that are not included in this snapshot.
- **Files:** a two-pane browser for selected local roots, selected browser-device operations, archives, sharing and Telegram integration. SSH-node file browsing and limited upload/download exist; cross-node transfer and complete remote file operations are not implemented.
- **Terminal:** browser terminal using the vendored xterm.js assets, with local and SSH-node functionality. It grants command execution and requires strong access control.
- **Pi nodes:** SSH discovery and pinned host-key connections, resource checks, assisted deployment of a lightweight node panel and basic container controls. Automatic distributed workload scheduling is not implemented.
- **Lightweight node panel:** overview, monitoring, network, Tailscale route status, read-only settings and browsing/downloading files under `/home`; Docker start/stop. This is not a full installation of the main panel.
- **Other integrated areas:** network inventory, Wi-Fi/Bluetooth, knowledge base, AI chat, KDBX vault, backup and tunnel controls. Availability depends on host tools, credentials, external services and code paths; no claim of complete or independently verified operation is made.

## Example workflows

1. Sign in to the main panel over an authenticated, trusted network. The deployed panel normally uses HTTPS and may use a self-signed certificate.
2. Open **System** to check memory, storage and temperature before starting a heavier service.
3. Open **Pi nodes**, verify the SSH host-key fingerprint out of band, inspect the host and use auto-setup only on an authorized Raspberry Pi. The remote installation requires root or passwordless sudo, supported Debian/Raspberry Pi OS, sufficient resources and an available panel port.
4. Open **Files**, choose an SSH node and browse its home directory. The current implementation limits node uploads to 1 MiB and node downloads to 8 MiB through the main panel.
5. For a remote LAN, an administrator can configure a Tailscale subnet router and approve its route in the tailnet. The main host must accept the route; container connectivity requires an independent end-to-end test. Avoid overlapping address ranges.

## Source layout

| Path | Purpose |
| --- | --- |
| `src/server.py` | Main HTTPS application and routing |
| `src/index.html`, `app.js`, `style.css` | Main interface |
| `src/filemanager.js`, `files6.py`, `nodeops10.py` | Local and SSH-node file functions |
| `src/ssh_nodes7.py`, `provision11.py`, `cluster5.py` | SSH nodes, deployment and control |
| `src/pi_node_agent.py` | Lightweight remote node panel |
| `src/terminal9.py` | Interactive terminal implementation |
| `src/vault.py` | KDBX integration |
| `docs/OPERATIONS.md` | Dependencies, recovery and deployment limitations |

The third-party xterm and fit-addon license texts are included beside their assets. No license is asserted here for the remainder of the project; the owner should choose one before public redistribution.

## Setup and safety

A safe, repeatable installation of the **main panel** from scratch is not yet available. An [experimental installer for the lightweight node only](docs/NODE-INSTALL.md) is included; it has not been tested end-to-end on a new Pi. See [operations and recovery](docs/OPERATIONS.md) for prerequisites, missing components, existing-installation inventory and the work required before an installer can be validated. The source currently contains machine-specific paths, account names and service assumptions. Do not use a legacy setup script blindly on an existing Pi.

The panel can execute commands, manage Docker and access files. Treat it as privileged administrative software. Keep it within a trusted LAN or tailnet, use separate strong credentials, verify TLS/SSH fingerprints through a trusted channel, and back up and test recovery before modifying a device. Do not commit `.env` files, KDBX databases, certificates, keys, logs, machine-specific backups or personal data.

## Status and roadmap

This snapshot does not include a complete English UI, unrestricted streaming remote file transfer, complete remote file operations, distributed scheduling, a portable one-click installer, or a verified full restoration procedure. Remote tunnel management in the lightweight panel is status-only. Additional services and resource-aware deployment require verification on each target.

## Development checks

```sh
python3 -m compileall -q src
node --check src/app.js
node --check src/filemanager.js
```

These checks only validate syntax; they do not test deployment, security, service integrations or browser behavior.

### Interface releases

The dashboard provides a Russian/English interface switch and a device-wide default in Settings. Settings → System → Pi Control updates checks the latest stable release in this repository and shows its release notes.

On managed installations, the update button installs the verified `pi-control-ui.zip` and `pi-control-server.zip` for a newer stable release. The server bundle covers an explicit set of dashboard files. GitHub provides the digests used to verify both bundles.

To release a new interface version, increase `UI_VERSION` and merge that change into `main`. With GitHub Actions enabled, the workflow checks the JavaScript, builds the update bundle, generates release notes and publishes the stable release with its bundle attached. Merges without a version change do not publish. The workflow uses the repository-provided token with `contents: write`.

For KeePass, export a KDBX backup from the unlocked vault. A new installation can restore that file directly. An existing vault can merge entries using its existing import action.

### Managed updates and remote Pi nodes

On a managed installation, open **Settings → System → Pi Control updates**. The server checks for both release ZIPs and validates their SHA-256 digests. An update restarts the panel and USB monitor, checks their health, and recovers the previous managed files and UI on failure. Use **Rollback** to return to the previous installation. Host configuration and user data remain outside the update bundle.

For a new lightweight node run `sudo python3 scripts/install-node.py --check` and then `sudo python3 scripts/install-node.py` from a checkout on that Pi. For an existing node run `sudo python3 scripts/install-node.py --upgrade` from the new checkout. Verify the node's certificate fingerprint in the main panel when enrolling it. In **Pi Nodes → Task templates**, select the node or let the panel pick a reachable enrolled node with a scoped stored secret. The neighbor inventory comes from the Pi's existing neighbor cache. **Files → Remote node USB** lists detected storage and opens it for read-only browsing.

For **System → Remote desktops**, set up a noVNC desktop session on the main Pi and install the distribution's TigerVNC viewer and FreeRDP X11 client (on Debian 13, `tigervnc-viewer` and `freerdp3-x11`). The integration starts the systemd user units `novnc-xvnc`, `novnc-desktop`, and `novnc-websockify` on display `:5`, port `9595`. Set `PI_NOVNC_USER` in the dashboard unit if the desktop belongs to a different user. Add the Tailscale IPv4 of the Mac or Windows PC. Enable [Screen Sharing and VNC access on the Mac](https://support.apple.com/en-us/guide/mac-help/mh11848/mac) or [Remote Desktop on a supported Windows edition](https://support.microsoft.com/en-us/windows/experience/connectivity-networking/how-to-use-remote-desktop). The browser must be able to reach the Pi via Tailscale and the Pi must be allowed to reach the desktop service via the tailnet. Enter credentials when prompted by the viewer.

### Remote login, device discovery and file transfer

Select **Mac / Apple Screen Sharing** for macOS account credentials. Install `remmina` and `remmina-plugin-vnc` on the main Pi. Update older Mac profiles that use standard VNC. For RDP, save a Windows account or enter it interactively. The first noVNC password unlocks the Pi desktop, which hosts the destination client.

The lightweight node's **Network** page provides Wi-Fi and Bluetooth discovery and manual inventory of common TCP listeners across up to 64 addresses of a directly connected private IPv4 network. Start each discovery run from the panel. The node's **Files → Transfer folder** supports browser upload and download up to 128 GiB per file, subject to available space. The controller can stream a file from one enrolled node's transfer folder to another with progress. Existing files are kept when names collide. Upgrade enrolled nodes via `sudo python3 scripts/install-node.py --upgrade`.

### Pi Nodes MCP

Use `scripts/pi-node-mcp.py --node <enrolled-node-id>` in a stdio MCP client on the main Pi. It reads enrolled nodes through existing, scoped node secrets and provides status, interfaces, known neighbors, local listeners and Bluetooth records. You can repeat `--node` to allow more nodes. The authenticated main panel endpoint `/api/platform/node-tools` lists the configured IDs.

For a remote MCP client, add `--listen --certificate <tls-cert> --private-key <tls-key> --token-file <file>`. Use a certificate trusted by the client for the Tailscale address and a random token of at least 32 ASCII characters in a user-owned file with mode `0600`. Connect to `https://<tailscale-ip>:8794/mcp` with a Bearer token. Run separate instances on distinct ports to grant individual agents different node allowlists.
