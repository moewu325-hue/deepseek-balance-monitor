# Security Policy

## Scope

DeepSeek Balance Monitor is a local Windows desktop client. Its security goal is to minimize where an API key can be discovered, retained, transmitted, or exposed. It does not provide an operating-system sandbox and cannot protect a key from malware or another process already running with the same user's authority.

## Assets

- DeepSeek API keys and compatible authentication tokens
- Account balance responses and local balance history
- Local paths and configuration that may reveal workstation details
- The integrity of the monitor, its dependency lock, and its update source

## Trust Boundaries

- Manual input crosses from the user into process memory.
- `DEEPSEEK_API_KEY` and Windows Credential Manager are external credential stores.
- Claude settings, the known Claude credential target, and legacy JSON are untrusted migration inputs.
- DeepSeek's HTTPS response is external data and is schema-validated before use.
- User-approved startup persistence crosses into `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\DeepSeekBalanceMonitor.vbs`.
- The repository, dependency artifacts, GitHub Actions, and local Git history form the software supply-chain boundary.

## Abuse Cases

- A crafted legacy file attempts to smuggle an API key or unknown field into runtime configuration.
- A malicious Claude base URL attempts to redirect a compatible token to another host.
- A proxy or endpoint redirects the balance request to an attacker-controlled destination.
- A transient server failure is mistaken for an invalid key and causes unsafe fallback or persistence.
- A modified startup entry exposes a credential or launches an unexpected target.
- Console output, exceptions, test fixtures, or Git history accidentally disclose a credential.
- Modified dependency artifacts execute instead of the reviewed packages.

## Security Design

- Normal discovery is limited to `DEEPSEEK_API_KEY`, then `DeepSeekBalanceMonitor.ApiKey`.
- Claude compatibility migration is explicit, accepts only the exact official HTTPS base URL (with optional explicit port 443), and never executes `apiKeyHelper`.
- Manual fields are masked. Persistence is opt-in and happens only after authentication is confirmed; indeterminate credentials remain memory-only.
- Requests use a fixed HTTPS balance URL, TLS verification, no redirects, and bounded timeouts. Responses require the expected schema and finite decimal values.
- Configuration and usage history use strict allowlists, atomic replacement, and read-back checks. Legacy migration drops secret and unknown fields and forces `auto_start=false`.
- Autostart is disabled by default and changed only after explicit user action. The app atomically manages its dedicated VBS file, verifies exact contents, and that file must never contain an API key.
- Runtime files stay below `%LOCALAPPDATA%\DeepSeekBalanceMonitor`; repository hygiene tests reject common private/runtime artifacts, private paths, unsafe emails, and realistic token literals.
- Runtime dependencies are exactly pinned and installed from a hash-checked lock file in CI.

## Residual Risks

- Environment variables and Windows Credential Manager are controlled by Windows and the user; their visibility and persistence are outside this application's guarantees.
- A compromised operating system, Python runtime, TLS trust store, dependency, DeepSeek account, or DeepSeek service can defeat application-level controls.
- Balance history is ordinary local JSON, not encrypted, and may disclose usage patterns to processes with access to the user's profile.
- The startup entry contains the local project or installed launcher path, which can reveal workstation details and will stop working if that target moves.
- The local “today's balance decrease” value is an estimate based on observed balances, not billing-grade accounting.
- A user can still publish private files by bypassing the documented tests or rewriting the repository after verification.

## Reporting a Vulnerability

Do not open a public issue for a suspected vulnerability or leaked credential. Use the repository's **Security → Advisories → Report a vulnerability** flow to create a private GitHub security advisory. Include affected versions, reproduction steps, impact, and any suggested mitigation, but redact API keys, balances, and private paths.

If a credential may have been exposed, revoke or rotate it in the DeepSeek console first. Removing it from a later commit is not sufficient because it may remain in Git history or caches.
