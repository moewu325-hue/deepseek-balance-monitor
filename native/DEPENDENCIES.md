# Native build dependencies

- Zig 0.17.0, Windows x86_64 official archive SHA-256 `b5663f69581dcf391293fbf16c06cb80d81d806545ce618b4d0bab7f0eb8c428`; source: https://ziglang.org/download/index.json (checked 2026-10-21).
- nlohmann/json 3.12.0, single `third_party/json.hpp`, SHA-256 `aaf127c04cb31c406e5b04a63f1ae89369fccde6d8fa7cdda1ed4f32dfc5de63`, MIT license in `third_party/LICENSE-json.txt`; official release https://github.com/nlohmann/json/releases/tag/v3.12.0 . Vendored at build time; no network fetch or JSON DLL at runtime.
- Windows built-in Win32, GDI, WinHTTP, Credential Manager, Shell APIs and Universal CRT. C++ runtime linked statically; no VC++ Redistributable installer required on supported Windows 10/11.
- Test runner: Python 3.10+ standard library only. Live network checks are opt-in.

The native implementation uses CodexUsageBar as an architectural reference and does not include its code. Existing whale artwork is embedded once; provenance remains described in the repository NOTICE.
