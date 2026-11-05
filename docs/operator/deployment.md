# Production Deployment Guide

This guide covers production deployment of Cairn on Linux systems, including database provisioning, systemd user services, and socket/network configuration.

---

## 1. System Requirements

- **Operating System**: Linux (systemd-based distribution, e.g. Ubuntu 22.04+, Debian 12+, Fedora 38+).
- **Database**: PostgreSQL 15 or newer.
- **Runtime**: Compiled `cairn` binary installed in system or user `$PATH` (e.g. `/usr/local/bin/cairn` or `~/.local/bin/cairn`).

---

## 2. Database Provisioning

Cairn requires a dedicated PostgreSQL database and role:

```sh
# Create role and database
sudo -u postgres psql -c "CREATE ROLE cairn WITH LOGIN PASSWORD 'secure_password';"
sudo -u postgres psql -c "CREATE DATABASE cairn OWNER cairn;"
```

Configure your connection string:

```sh
export CAIRN_DATABASE_URL="postgres://cairn:secure_password@localhost:5432/cairn?sslmode=disable"
```

Initialize the database schema:

```sh
cairn migrate
```

---

## 3. Systemd Service Units

Cairn components run cleanly as systemd user services under the hosting user account.

### 1. API Server (`cairn-localapi.service`)

Create `~/.config/systemd/user/cairn-localapi.service`:

```ini
[Unit]
Description=Cairn Local API Daemon
After=network.target postgresql.service

[Service]
Type=simple
Environment=CAIRN_DATABASE_URL=postgres://cairn:secure_password@localhost:5432/cairn?sslmode=disable
ExecStart=/usr/local/bin/cairn serve --socket %h/.local/share/cairn/api.sock
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=default.target
```

### 2. Worker Pool (`cairn-worker-pool.service`)

Create `~/.config/systemd/user/cairn-worker-pool.service`:

```ini
[Unit]
Description=Cairn Background Worker Pool
After=cairn-localapi.service

[Service]
Type=simple
Environment=CAIRN_DATABASE_URL=postgres://cairn:secure_password@localhost:5432/cairn?sslmode=disable
ExecStart=/usr/local/bin/cairn schedule-serve --repo %h/git/cairn
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=default.target
```

### 3. Wake Supervisor (`cairn-wake.service`)

Create `~/.config/systemd/user/cairn-wake.service`:

```ini
[Unit]
Description=Cairn Wake Supervisor
After=cairn-localapi.service

[Service]
Type=simple
Environment=CAIRN_DATABASE_URL=postgres://cairn:secure_password@localhost:5432/cairn?sslmode=disable
ExecStart=/usr/local/bin/cairn wake serve --config %h/.local/share/cairn/wake-binding.json
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=default.target
```

### Enable & Start Services

```sh
systemctl --user daemon-reload
systemctl --user enable --now cairn-localapi
systemctl --user enable --now cairn-worker-pool
systemctl --user enable --now cairn-wake
```

Verify service status:

```sh
systemctl --user status cairn-localapi
```

---

## 4. Socket vs TCP Network Configuration

### Local Domain Socket (Default)

For single-host agent environments, Unix domain sockets provide optimal security, low latency, and automatic file-permission boundaries:

```sh
cairn serve --socket "$HOME/.local/share/cairn/api.sock"
```

The socket file permissions ensure that only the local user account can access the API.

### Remote TCP Listener with TLS

To expose Cairn across a trusted network to remote machines, configure a TLS listener:

```sh
cairn serve \
  --socket "$HOME/.local/share/cairn/api.sock" \
  --listen "0.0.0.0:8443" \
  --tls-cert "/etc/cairn/tls.crt" \
  --tls-key "/etc/cairn/tls.key"
```

Alternatively, if Cairn is deployed behind a reverse proxy terminating HTTPS:

```sh
cairn serve \
  --socket "$HOME/.local/share/cairn/api.sock" \
  --listen "127.0.0.1:8080" \
  --tls-terminated-proxy
```

---

## Related Documentation

- [Configuration Guide](configuration.md): Tokens, profiles, and environment variables.
- [Maintenance Guide](maintenance.md): Schema migrations, vacuuming, and recovery.
- [Daemon Deployment Guide](daemon-deployment.md): Worker pools and multi-machine operations.
