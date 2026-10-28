# DeepSeek Balance Monitor · Native Windows

[![Release](https://img.shields.io/github/v/release/moewu325-hue/deepseek-balance-monitor)](https://github.com/moewu325-hue/deepseek-balance-monitor/releases/latest)
![Windows x64](https://img.shields.io/badge/Windows-10%20%2F%2011%20x64-0078D4)
[![MIT](https://img.shields.io/github/license/moewu325-hue/deepseek-balance-monitor)](LICENSE)

Keep your DeepSeek API balance on the Windows desktop. Choose a detailed panel, a whale bubble, or a compact taskbar strip, with background balance refresh every 60 seconds.

Built with **C++17 / Win32 / GDI / WinHTTP**, the single EXE requires no Python or additional runtime installation. v2.2.0 enlarges the bubble diameter by 50% and adds a moonlit bay, distant hills, layered water and ripples. The bundled DeepSeek icon swims and jumps along a parabolic arc, with red/green peak-period indicators in all three views.

**v2.2.0 measures 512,000 bytes (500 KiB)**, about 98.7% smaller than the previous packaged app. This recording uses the actual native window with synthetic offline balance data:

![Whale swimming and jumping; the green light means off-peak](docs/whale-swim.gif)

**[Download EXE](https://github.com/moewu325-hue/deepseek-balance-monitor/releases/latest/download/DeepSeekBalanceMonitor-x64.exe) · [Release and checksums](https://github.com/moewu325-hue/deepseek-balance-monitor/releases/latest) · [中文](README.md) · [Issues](https://github.com/moewu325-hue/deepseek-balance-monitor/issues)**

## Views and features

| View | What it provides |
| --- | --- |
| Panel | Total, granted and topped-up balance, estimated daily decrease, refresh status |
| Whale bubble | 108-DIP diameter, moonlit ocean scenery and the DeepSeek icon swimming and jumping along an arc; drag to move, double-click to expand |
| Taskbar strip | Compact horizontal balance; docks above CodexUsageBar when found |

Every view shows a red light for the legacy peak period or a green light for off-peak, accompanied by text. Animation stays inside the bubble and leaves the amount and indicator readable. Right-click for refresh, display mode, masked API key input and opt-in autostart.

The right-click bubble-size submenu offers five presets (72, 90, 108, 132 and 160 DIPs), applied immediately and saved for the next launch. The default is 108 DIPs; Windows display scaling applies as usual.

Failed requests retain the last successful balance and show a failure status. Fixed-point integers preserve six decimal places internally; currencies are recorded separately and never summed.

## Get started

1. Download `DeepSeekBalanceMonitor-x64.exe` for Windows 10/11 x64 and keep it in a stable location.
2. Run it and choose API key input from the right-click menu, or use an existing credential source below.
3. Choose a view and enable autostart only if wanted.

Compare `Get-FileHash .\DeepSeekBalanceMonitor-x64.exe -Algorithm SHA256` with the release's `SHA256SUMS.txt`.

## Credentials and persistence

Credentials are read from `DEEPSEEK_API_KEY`, then the dedicated Windows Credential Manager target `DeepSeekBalanceMonitor.ApiKey`, then explicit input. Saving a typed key is optional and occurs only after server authentication. Keys never enter configuration, usage history or logs.

HTTPS requests target only `api.deepseek.com/user/balance`, validate system certificates and disable redirects. The app does not send chat/completion requests or scan other applications' settings. See [SECURITY.md](SECURITY.md) for security reports.

Configuration and per-currency history remain in `%LOCALAPPDATA%\DeepSeekBalanceMonitor\config.json` and `usage_log.json`. Legacy `display_mode`, `collapsed` and version-2 history are supported; corrupt files are preserved. The dedicated Startup-folder VBS is modified only when autostart is explicitly toggled. A shared single-instance mutex prevents concurrent legacy/native history writers.

Peak indicators follow the existing Beijing-time weekday schedule: 09:00–12:00 and 14:00–18:00, with inclusive starts and exclusive ends; weekends are off-peak. They do not represent current model prices or billing multipliers. Daily decrease is an estimate affected by top-ups, grants and day boundaries, not an official bill. A balance retained after a failed refresh may be stale.

## Build

Verified release toolchain: [Zig 0.17.0](https://ziglang.org/download/), Windows x64 and PowerShell. Python 3.10+ standard library is needed only for tests. Compiler and JSON hashes are pinned in [dependencies](native/DEPENDENCIES.md).

```powershell
git clone https://github.com/moewu325-hue/deepseek-balance-monitor.git
cd deepseek-balance-monitor
.\native\build.ps1 -Test
```

Output: `build\native\DeepSeekBalanceMonitor.exe`. Tests do not require a real API key. Optional checks:

```powershell
# Opt in to the live invalid-credential 401 check
.\native\build.ps1 -Test -Live
# Check existing credentials; prints status, never the key or amount
.\build\native\DeepSeekBalanceMonitor.exe --check
```

An additional MSVC/CMake project is available in `native/`; the verified release uses Zig.

```text
native/
  core.cpp          Amounts, API parsing, legacy formats
  windows.cpp       HTTPS, credentials, atomic files, startup
  app.cpp           Views, animation and background refresh
  animation.hpp     Swimming, ballistic jump and peak indicator state
  icon_sprite.hpp   Cached transparent rotation and mirroring of the icon
  ocean_scene.hpp   Moonlit bay, distant hills, ripples and underwater bubbles
  main.cpp          CLI and single instance
  tests/            Core, Windows and real-window checks
  third_party/      Pinned JSON header and license
src/                Original Python migration source
docs/               Design and verification records
```

## Validation and support

The [original verification record](docs/native-verification.md) documents v2.0.0 size, core/Windows assertions, real-window and live API checks. The [animation record](docs/whale-animation-verification.md) covers the current ocean bubble, icon rendering, trajectory, indicator colors and artifacts, with earlier results retained as history. GitHub Actions builds and checks the native release; the legacy Python workflow is manual, with pre-existing differences documented.

For a missing balance, inspect refresh status, network and credentials, or run `--check`. An issue should include Windows version, display mode and error state, with no key. Replacing the EXE leaves user data separate from program resources.

## License and credits

[MIT](LICENSE); artwork provenance is retained in [NOTICE](NOTICE). Inspired by the Windows system-API architecture of [CodexUsageBar](https://github.com/luodaoyi/codex-useage-win), with independently written native code. Not affiliated with DeepSeek.
