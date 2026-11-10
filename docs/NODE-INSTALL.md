# Experimental lightweight node installer

[Русский](NODE-INSTALL.ru.md) · [Main installation status](INSTALLATION.md)

This installs **only the lightweight remote node**, not the main Pi Control panel, Docker Compose application stack, Tailscale, Cloudflare Tunnel, Agent Zero or other services. It is experimental: automated tests use mocks, and a fresh-device end-to-end run has not been verified. Read and review the source before use. The node service runs as root and can manage containers and read files under `/home`; expose it only on trusted networks.

## Requirements

Debian/Raspberry Pi OS with a running systemd, Python 3, `apt-get`, at least 450 MB RAM and 2 GiB free storage, sudo/root, port 8484 available, network access for package installation if Docker or OpenSSL are missing. The account must have access to the private GitHub repository. Network and firewall configuration are not changed. These are preflight limits, not a tested device support matrix.

## Install

```sh
git clone https://github.com/sm0k3net/pi-control.git
cd pi-control
python3 scripts/install-node.py --check
sudo python3 scripts/install-node.py
```

The check is read-only; its environment may differ from the environment under `sudo`. The installer installs missing distribution packages `docker.io` and/or `openssl`, creates `/opt/pi-node` and a systemd unit, generates an independent random password and self-signed certificate, and enables the unit. Existing Docker data is not deliberately deleted. If the node directory or unit already exists, installation stops instead of overwriting it; updates and rollback are not implemented.

On success, obtain the password **locally over a trusted channel** from root-only `/opt/pi-node/initial-password.txt`, store it securely and remove that file once safely stored. Keep the node's KDBX entry and SSH credentials independent. The agent stores a salted password hash in `config.json`; it does not yet provide password rotation in its UI.

Verify the SHA-256 certificate fingerprint out of band:

```sh
sudo openssl x509 -in /opt/pi-node/cert.pem -noout -fingerprint -sha256
sudo systemctl status pi-control-node --no-pager
sudo journalctl -u pi-control-node -n 50 --no-pager
```

Open `https://<node-private-or-tailscale-ip>:8484/` in a trusted network and verify connectivity. The node binds discovered private IPv4/Tailscale addresses; availability depends on addresses, network policy and firewall. TLS is self-signed; its fingerprint must be verified with the value read over a trusted channel. A running systemd unit alone does not confirm client connectivity. Add the node in the main controller using its certificate fingerprint and password as supported by that controller.

## Partial failures

The installer does not automatically delete partially created data or uninstall packages when a later step fails. Review `systemctl status`, the journal, `/opt/pi-node` and the unit file before a deliberate cleanup or retry. It will refuse to overwrite a partial installation. Do not remove files you need to keep. Recovery and update workflows remain to be developed and tested.
