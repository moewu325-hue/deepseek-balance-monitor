# DeepSeek Balance Monitor

A credential-conscious Windows desktop bubble that reads a DeepSeek API account balance and displays one preferred currency.

## Introduction

The app refreshes through DeepSeek's fixed HTTPS balance endpoint and locally estimates today's balance decrease. Normal credential discovery is deliberately limited to one environment variable and one dedicated Windows credential instead of searching unrelated apps, browsers, or arbitrary configuration folders.

## Tech Stack

- Python 3.10+
- Tkinter desktop UI
- Requests HTTPS client
- Windows Credential Manager
- Pillow for icon asset validation

## Quick Start

1. Download or check out the source, then open a terminal in the project directory.
2. Create and install: run `py -3.11 -m venv .venv`, then `.venv\Scripts\python -m pip install --require-hashes -r requirements.lock` and `.venv\Scripts\python -m pip install --no-build-isolation --no-deps -e .`.
3. Start with `run_monitor.bat`, or run `.venv\Scripts\deepseek-balance-monitor.exe`.

## Directory Structure

```text
deepseek-balance-monitor/
├─ assets/
│  └─ deepseek_icon.ico
├─ src/
│  └─ deepseek_balance_monitor/
├─ tests/
├─ config.example.json
├─ requirements.lock
└─ run_monitor.bat
```

## Features

- Discovers credentials and fetches balances on worker threads; Tk updates stay on the main thread.
- Calls only `https://api.deepseek.com/user/balance`, with TLS verification, redirects disabled, and bounded connect/read timeouts.
- Excludes a rejected candidate after HTTP 401 and tries the next source; temporary network or service failures do not permanently reject it.
- Displays total balance and “today's balance decrease (estimated)” without time-based surcharges or multiplier pricing estimates.
- Keeps daily baselines and history isolated by currency; amounts in different currencies are never added.
- Uses a Windows named mutex to prevent duplicate instances of this version.

## Configuration

Runtime data lives under `%LOCALAPPDATA%\DeepSeekBalanceMonitor`: a credential-free `config.json` and currency-scoped `usage_log.json`. History retains the latest 10,000 samples, and adjacent samples with the same currency and balance are not written twice. `config.example.json` contains all nine accepted fields. `auto_start` defaults to `false`; this release does not create or modify Windows startup entries.

Currency selection first looks for the configured `currency`. A single returned currency is a safe fallback. Multiple returned currencies without the preferred one cause an explicit error and are never added together. CNY uses `¥`, USD uses `$`, and unknown codes render as `CODE 12.34`.

### Credential Sources and Manual Entry

Normal automatic discovery follows exactly this order:

1. `DEEPSEEK_API_KEY`
2. `DeepSeekBalanceMonitor.ApiKey` in Windows Credential Manager

If neither works, the UI opens a masked input. The default is process memory only. The app writes the dedicated Windows credential only when persistence is explicitly selected and the service confirms authentication. The value is sent only to the fixed DeepSeek HTTPS balance endpoint for validation. The app does not write it to configuration, history, or logs; persistence of environment variables and Windows credentials remains the responsibility of those external mechanisms.

Claude-compatible sources are excluded from normal discovery. Only `deepseek-balance-monitor --migrate-claude-compat` reads compatible tokens paired with the strict official URL `https://api.deepseek.com/anthropic` (or explicit `:443`) and the known credential target. The program never executes `apiKeyHelper`.

### CLI

```powershell
deepseek-balance-monitor --check
deepseek-balance-monitor --set-key
deepseek-balance-monitor --migrate-legacy D:\path\to\legacy-directory
deepseek-balance-monitor --migrate-claude-compat
```

`--set-key` uses hidden input and defaults to no persistence. Legacy migration copies only the documented non-sensitive fields and balance history, forces `auto_start=false`, and drops API keys and unknown fields. If a legacy version stored a plaintext key, rotate that key in the DeepSeek console after migration.

## Security

See [SECURITY.md](SECURITY.md) for trust boundaries, residual risks, and private reporting instructions. Before publishing a fork, run the complete test suite and keep machine-local configuration, history, and credentials out of Git.

## License

MIT; see [LICENSE](LICENSE). DeepSeek is a trademark of its respective owner. This project is not affiliated with, sponsored by, or endorsed by DeepSeek.
