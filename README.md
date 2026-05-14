<div align="center">

# 3x-ui Monitor

[![CI](https://github.com/phoseinq/3x-ui-monitor/actions/workflows/syntax-check.yml/badge.svg)](https://github.com/phoseinq/3x-ui-monitor/actions/workflows/syntax-check.yml)
[![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-web%20framework-000000?logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![3x-ui](https://img.shields.io/badge/3x--ui-compatible-orange)](https://github.com/MHSanaei/3x-ui)
[![License: MIT](https://img.shields.io/badge/License-MIT-green)](LICENSE)

**داشبورد مانیتورینگ ترافیک برای پنل‌های 3x-ui**

<img width="2559" height="1363" alt="image" src="https://github.com/user-attachments/assets/d5eb19c3-1fe0-4d0a-98f8-18e47b9bf77b" />

</div>

---

<div dir="rtl" align="right">

## نصب
[نصب دستی (بدون اسکریپت)](MANUAL.md)

### نصب معمولی

```bash
curl -fsSL https://raw.githubusercontent.com/phoseinq/3x-ui-monitor/main/install.sh | sudo bash
```

---

### نصب با پروکسی (سرور ایران)

سرور ایران بدون پروکسی به GitHub وصل نمی‌شه — هم دانلود اسکریپت و هم تمام مراحل نصب باید از طریق پروکسی بره:

```bash
curl -fsSL --proxy socks5://HOST:PORT \
  https://raw.githubusercontent.com/phoseinq/3x-ui-monitor/main/install.sh \
  | sudo bash -s -- --proxy socks5://HOST:PORT
```

اگه پروکسی یوزر و پسورد داره:

```bash
curl -fsSL --proxy socks5://user:pass@HOST:PORT \
  https://raw.githubusercontent.com/phoseinq/3x-ui-monitor/main/install.sh \
  | sudo bash -s -- --proxy socks5://user:pass@HOST:PORT
```

---

 **[بعدی: راه‌اندازی اولیه و تنظیمات ←](SETUP.md)**</div>

---

## Installation

### Standard

```bash
curl -fsSL https://raw.githubusercontent.com/phoseinq/3x-ui-monitor/main/install.sh | sudo bash
```

**[Next: First run & settings →](SETUP.md)**

---

## Verified

Tested on Ubuntu 22.04 VPS.

CI checks on every push:
- Python syntax (`compileall`)
- Ruff E9/F rules
- `bash -n` shell syntax
- `shellcheck` static analysis

All tracked files use LF line endings (`git ls-files --eol`).

---

## Reverse proxy (optional)

To serve the dashboard on port 443 with nginx:

```nginx
server {
    listen 443 ssl;
    server_name your.domain.com;

    ssl_certificate     /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

Then bind the dashboard to localhost only by editing `/etc/systemd/system/xui-dashboard.service` and setting `Environment=HOST=127.0.0.1` — or use `boy port 5000` and firewall the port externally.

Logs are managed by journald. To limit disk usage:

```bash
# keep only last 7 days of logs
journalctl --vacuum-time=7d

# or cap by size
journalctl --vacuum-size=100M
```

---

## Security notes

- **Services run as root** — required to write to `/opt/xui-monitor` and restart services via `systemctl`. Intended for personal VPN servers where root access is already present.
- **Panel password storage** — stored as plaintext in `app.db`. Protection relies on file permissions (`chmod 600`) set during install. Encrypting it on the same server with a local key would not add meaningful security.
- **Dashboard access** — bind to a trusted network or use a firewall rule to restrict port 5000 to your IP. TLS is supported via `boy https on`.

---

MIT License
