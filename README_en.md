# DeepSeek Balance Monitor · Native Windows

A compact C++17 / Win32 desktop balance monitor. Download `DeepSeekBalanceMonitor-x64.exe` from Releases and run it on Windows 10/11 x64. No Python or third-party runtime installation is required.

Panel, animated whale bubble and compact taskbar views share a background balance refresh. Drag the panel/bubble, double-click the bubble to expand, or right-click for refresh, display modes, masked API key input and opt-in autostart. Failed requests retain the last successful balance.

Credentials come only from `DEEPSEEK_API_KEY`, the dedicated Windows Credential Manager target `DeepSeekBalanceMonitor.ApiKey`, or explicit input. Saving a typed key is optional and occurs only after successful server authentication. Keys never enter configuration, usage history or logs. HTTPS requests target only `api.deepseek.com/user/balance`, validate certificates and disable redirects.

The native app continues using `%LOCALAPPDATA%\DeepSeekBalanceMonitor\config.json` and version-2 `usage_log.json`, plus the dedicated Startup-folder VBS when explicitly enabled. Corrupt files are preserved. Amounts use fixed-point integers with six decimal places; currencies are never summed. Daily decrease is an estimate, not an official bill. Legacy peak-hour hints do not assert current model prices or billing multipliers.

Build with [Zig 0.17.0](https://ziglang.org/download/), PowerShell and Python 3.10+ for tests:

```powershell
.\native\build.ps1 -Test
.\native\build.ps1 -Test -Live
.\build\native\DeepSeekBalanceMonitor.exe --check
```

Output: `build\native\DeepSeekBalanceMonitor.exe`. Python is needed only for tests. An additional MSVC CMake project is available in `native/`; the release build verified here uses Zig. See [verification](docs/native-verification.md), [dependencies](native/DEPENDENCIES.md), [MIT license](LICENSE) and [icon notice](NOTICE).

The original Python implementation is retained as migration source. Its pre-existing test differences are documented; the native application is the maintained release. Inspired by the system-API architecture of [CodexUsageBar](https://github.com/luodaoyi/codex-useage-win), with independently written native code. Not affiliated with DeepSeek.
