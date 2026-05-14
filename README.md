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

MIT License
