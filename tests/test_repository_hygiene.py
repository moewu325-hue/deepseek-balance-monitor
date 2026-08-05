from __future__ import annotations

import ast
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_TEXT_SUFFIXES = {".bat", ".json", ".md", ".py", ".toml", ".txt", ".yml", ".yaml"}
PUBLIC_TEXT_NAMES = {".gitignore", "LICENSE", "NOTICE", "requirements.lock"}
CONFIG_FIELDS = {
    "refresh_interval",
    "window_x",
    "window_y",
    "opacity",
    "low_balance_warning",
    "currency",
    "snap_edge",
    "collapsed",
    "auto_start",
}
TOKEN_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]{16,}")
EMAIL_PATTERN = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
SYNTHETIC_TOKEN_ALLOWLIST = {
    "tests/test_cli.py": frozenset(
        {
            "sk-" + "synthetic-cli-secret",
            "sk-" + "synthetic-claude-secret",
        }
    ),
    "tests/test_credentials.py": frozenset(
        {
            "sk-" + "env-super-secret",
            "sk-" + "store-super-secret",
            "sk-" + "compat-super-secret",
        }
    ),
    "tests/test_runtime_recovery.py": frozenset(
        {
            "sk-" + "synthetic-controller-one",
            "sk-" + "synthetic-controller-two",
        }
    ),
    "tests/test_ui_logic.py": frozenset({"sk-" + "ui-synthetic-secret"}),
}


def _git(*args: str, text: bool = True, root: Path = ROOT) -> str | bytes:
    return subprocess.check_output(
        ["git", *args],
        cwd=root,
        text=text,
        encoding="utf-8" if text else None,
    )


def _index_files(root: Path = ROOT) -> tuple[str, ...]:
    return tuple(path for path in _git("ls-files", root=root).splitlines() if path)


def _untracked_files(root: Path = ROOT) -> tuple[str, ...]:
    return tuple(
        path
        for path in _git("ls-files", "--others", "--exclude-standard", root=root).splitlines()
        if path
    )


def _public_files(root: Path = ROOT) -> tuple[str, ...]:
    return tuple(sorted(set(_index_files(root)).union(_untracked_files(root))))


def _read_index_bytes(path: str, root: Path = ROOT) -> bytes:
    return _git("show", f":{path}", text=False, root=root)


def _read_index_text(path: str, root: Path = ROOT) -> str:
    return _read_index_bytes(path, root).decode("utf-8")


def _text_sources(
    root: Path,
    path: str,
    indexed: frozenset[str] | None = None,
) -> tuple[tuple[str, str], ...]:
    indexed = frozenset(_index_files(root)) if indexed is None else indexed
    sources: list[tuple[str, str]] = []
    if path in indexed:
        sources.append(("index", _read_index_text(path, root)))
    worktree_path = root / path
    if worktree_path.is_file():
        sources.append(("worktree", worktree_path.read_text(encoding="utf-8")))
    return tuple(sources)


def _is_public_text(path: str) -> bool:
    candidate = PurePosixPath(path)
    return candidate.name in PUBLIC_TEXT_NAMES or candidate.suffix.lower() in PUBLIC_TEXT_SUFFIXES


def _find_token_violations(root: Path, paths: tuple[str, ...]) -> list[str]:
    violations: list[str] = []
    indexed = frozenset(_index_files(root))
    for path in paths:
        if not _is_public_text(path):
            continue
        allowed = SYNTHETIC_TOKEN_ALLOWLIST.get(path, frozenset())
        for scope, content in _text_sources(root, path, indexed):
            matches = [value for value in TOKEN_PATTERN.findall(content) if value not in allowed]
            if matches:
                violations.append(f"{scope}:{path}")
    return violations


def _is_private_artifact(path: str) -> bool:
    candidate = PurePosixPath(path)
    lowered = path.lower()
    parts = {part.lower() for part in candidate.parts}
    return (
        lowered.startswith(".superpowers/")
        or lowered.startswith("docs/superpowers/")
        or candidate.name.lower() in {"config.json", "usage_log.json", ".env"}
        or candidate.name.lower().startswith(".env.")
        or candidate.name.lower().endswith(".lock.json")
        or candidate.suffix.lower() == ".exe"
        or bool(parts.intersection({".venv", "venv", "__pycache__", ".pytest_cache"}))
    )


def _find_history_token_violations(root: Path = ROOT) -> list[str]:
    seen: set[tuple[str, str]] = set()
    violations: list[str] = []
    for commit in _git("rev-list", "--all", root=root).splitlines():
        rows = _git("ls-tree", "-r", commit, root=root).splitlines()
        for row in rows:
            metadata, path = row.split("\t", 1)
            blob = metadata.split()[2]
            identity = (blob, path)
            if identity in seen or not _is_public_text(path):
                continue
            seen.add(identity)
            content = _git("show", blob, text=False, root=root).decode("utf-8", "replace")
            allowed = SYNTHETIC_TOKEN_ALLOWLIST.get(path, frozenset())
            if any(value not in allowed for value in TOKEN_PATTERN.findall(content)):
                violations.append(f"{commit[:12]}:{path}")
    return violations


class RepositoryHygieneTests(unittest.TestCase):
    def test_staged_token_is_detected_when_worktree_copy_is_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            sample = repo / "NOTICE"
            sample.write_text("sk-" + "a" * 24, encoding="utf-8")
            subprocess.run(["git", "add", "NOTICE"], cwd=repo, check=True)
            sample.write_text("safe worktree\n", encoding="utf-8")

            violations = _find_token_violations(repo, ("NOTICE",))

        self.assertEqual(["index:NOTICE"], violations)

    def test_nonignored_untracked_token_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            (repo / "NOTICE").write_text("sk-" + "u" * 24, encoding="utf-8")

            violations = _find_token_violations(repo, _public_files(repo))

        self.assertEqual(["worktree:NOTICE"], violations)

    def test_history_scans_test_files_without_broad_synthetic_bypass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            test_path = repo / "tests" / "test_sample.py"
            test_path.parent.mkdir()
            test_path.write_text('TOKEN = "sk-' + 'realistic-secret-value"\n', encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Release Gate",
                    "-c",
                    "user.email=release-gate@users.noreply.github.com",
                    "commit",
                    "-qm",
                    "test fixture",
                ],
                cwd=repo,
                check=True,
            )

            violations = _find_history_token_violations(repo)

        self.assertEqual(1, len(violations))
        self.assertTrue(violations[0].endswith(":tests/test_sample.py"))

    def test_release_manifest_exists(self) -> None:
        required = {
            ".github/workflows/ci.yml",
            "LICENSE",
            "NOTICE",
            "README.md",
            "README_en.md",
            "SECURITY.md",
            "assets/deepseek_icon.ico",
            "config.example.json",
            "pyproject.toml",
            "requirements.lock",
            "run_monitor.bat",
        }
        self.assertTrue(required.issubset(set(_index_files())))

    def test_private_and_runtime_artifacts_are_not_tracked(self) -> None:
        forbidden = [raw for raw in _public_files() if _is_private_artifact(raw)]
        self.assertEqual([], forbidden)

    def test_reachable_history_has_no_private_or_runtime_artifacts(self) -> None:
        forbidden: list[str] = []
        for commit in _git("rev-list", "--all").splitlines():
            for path in _git("ls-tree", "-r", "--name-only", commit).splitlines():
                if _is_private_artifact(path):
                    forbidden.append(f"{commit[:12]}:{path}")
        self.assertEqual([], forbidden)

    def test_public_text_has_no_private_path_or_private_email(self) -> None:
        violations: list[str] = []
        for path in _public_files():
            if not _is_public_text(path):
                continue
            for scope, content in _text_sources(ROOT, path):
                if "C:\\Users\\wsn" in content:
                    violations.append(f"{scope}:{path}: private path")
                for email in EMAIL_PATTERN.findall(content):
                    if email.lower() == "user@api.deepseek.com":
                        continue
                    if not email.lower().endswith("@users.noreply.github.com"):
                        violations.append(f"{scope}:{path}: private email")
        self.assertEqual([], violations)

    def test_commit_emails_are_noreply(self) -> None:
        emails = set(_git("log", "--all", "--format=%ae").splitlines())
        self.assertTrue(emails)
        self.assertTrue(all(email.endswith("@users.noreply.github.com") for email in emails))

    def test_current_tree_has_no_realistic_token_literal(self) -> None:
        violations = _find_token_violations(ROOT, _public_files())
        self.assertEqual([], violations)

    def test_reachable_history_has_no_realistic_token_literal(self) -> None:
        self.assertEqual([], _find_history_token_violations())

    def test_no_api_key_helper_execution_path(self) -> None:
        violations: list[str] = []
        for path in _public_files():
            if not path.endswith(".py") or path.startswith("tests/"):
                continue
            for scope, source in _text_sources(ROOT, path):
                tree = ast.parse(source, path)
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    call_text = ast.get_source_segment(source, node) or ""
                    if "apiKeyHelper" in call_text:
                        violations.append(f"{scope}:{path}")
        self.assertEqual([], violations)

    def test_example_config_is_strict_and_secret_free(self) -> None:
        raw = json.loads(_read_index_text("config.example.json"))
        self.assertEqual(CONFIG_FIELDS, set(raw))
        self.assertIs(raw["auto_start"], False)
        serialized = json.dumps(raw).lower()
        self.assertNotRegex(serialized, r"api.?key|auth.?token|password|secret")

    def test_project_metadata_pins_runtime_dependencies(self) -> None:
        text = _read_index_text("pyproject.toml")
        self.assertIn('requires-python = ">=3.10"', text)
        self.assertRegex(text, r'"requests==2\.34\.2"')
        self.assertRegex(text, r'"Pillow==12\.3\.0"')
        self.assertIn('deepseek-balance-monitor = "deepseek_balance_monitor.cli:main"', text)

    def test_lockfile_is_fully_hashed_and_pinned(self) -> None:
        text = _read_index_text("requirements.lock")
        requirement_lines = [
            line for line in text.splitlines() if line and not line.startswith((" ", "#"))
        ]
        self.assertTrue(requirement_lines)
        self.assertTrue(all("==" in line for line in requirement_lines))
        blocks = re.split(r"(?m)(?=^[A-Za-z0-9_.-]+==)", text)
        blocks = [block for block in blocks if re.match(r"^[A-Za-z0-9_.-]+==", block)]
        self.assertTrue(blocks)
        self.assertTrue(all("--hash=sha256:" in block for block in blocks))
        self.assertRegex(text, r"(?m)^requests==2\.34\.2")
        self.assertRegex(text, r"(?m)^Pillow==12\.3\.0")

    def test_ci_is_windows_matrix_and_has_no_deploy_or_secret_context(self) -> None:
        text = _read_index_text(".github/workflows/ci.yml")
        self.assertIn("windows-latest", text)
        for version in ("3.10", "3.11", "3.12", "3.13", "3.14"):
            self.assertIn(version, text)
        self.assertIn("pip install --require-hashes -r requirements.lock", text)
        self.assertIn("pip install --no-build-isolation --no-deps -e .", text)
        self.assertRegex(text, r"fetch-depth:\s*0")
        self.assertIn("unittest discover", text)
        self.assertIn("compileall", text)
        self.assertIn("--help", text)
        self.assertRegex(
            text,
            r"deepseek-balance-monitor --check\n"
            r"\s+if \(\$LASTEXITCODE -ne 1\) \{ exit 1 \}\n"
            r"\s+exit 0",
        )
        self.assertNotRegex(text.lower(), r"\bdeploy\b|\bsecrets\.")

    def test_readmes_are_concise_and_cover_required_sections(self) -> None:
        for name, headings in {
            "README.md": ("项目介绍", "技术栈", "快速开始", "目录结构", "主要功能", "配置说明", "License"),
            "README_en.md": ("Introduction", "Tech Stack", "Quick Start", "Directory Structure", "Features", "Configuration", "License"),
        }.items():
            text = _read_index_text(name)
            self.assertLessEqual(len(text.splitlines()), 200)
            for heading in headings:
                self.assertRegex(text, rf"(?m)^## {re.escape(heading)}$")
            self.assertNotIn("<repository-url>", text)
        self.assertIn("下载或检出源码，并在项目目录打开终端", _read_index_text("README.md"))
        self.assertIn(
            "Download or check out the source, then open a terminal in the project directory",
            _read_index_text("README_en.md"),
        )

    def test_icon_has_transparency_and_required_sizes(self) -> None:
        required = {(size, size) for size in (16, 24, 32, 48, 64, 128, 256)}
        with Image.open(io.BytesIO(_read_index_bytes("assets/deepseek_icon.ico"))) as image:
            self.assertEqual("ICO", image.format)
            sizes = set(image.ico.sizes())
            self.assertTrue(required.issubset(sizes))
            for size in required:
                frame = image.ico.getimage(size).convert("RGBA")
                self.assertLess(frame.getchannel("A").getextrema()[0], 255)

    def test_icon_notice_matches_generation_record(self) -> None:
        text = _read_index_text("NOTICE")
        for expected in (
            "OpenAI's built-in image generation",
            "data pulse",
            "chroma",
            "Pillow",
            "no text, brand mark, or DeepSeek logo",
            "16, 24, 32, 48, 64, 128, and 256",
        ):
            self.assertIn(expected, text)

    def test_batch_launcher_detaches_windowed_python_safely(self) -> None:
        text = _read_index_text("run_monitor.bat")
        self.assertLess(text.index(".venv\\Scripts\\pythonw.exe"), text.index("where pyw"))
        self.assertIn(
            'start "" /b "%PROJECT_DIR%.venv\\Scripts\\pythonw.exe" '
            '"%PROJECT_DIR%deepseek_monitor.py"',
            text,
        )
        for fallback in ("where pyw", "where pythonw", "where py"):
            self.assertIn(fallback, text)
        self.assertEqual(3, text.count('start "" /b'))
        self.assertIn(
            'start "" py -3 "%PROJECT_DIR%deepseek_monitor.py"',
            text,
        )
        self.assertNotIn(
            'start "" /b py -3 "%PROJECT_DIR%deepseek_monitor.py"',
            text,
        )
        self.assertNotRegex(text.lower(), r"\\startup\\|currentversion\\run")

    def test_built_wheel_contains_public_release_assets_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            environment = dict(os.environ)
            environment["PIP_NO_INDEX"] = "1"
            environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "wheel",
                    "--no-build-isolation",
                    "--no-deps",
                    "--wheel-dir",
                    str(output),
                    str(ROOT),
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, result.returncode, "offline wheel build failed")
            wheels = tuple(output.glob("*.whl"))
            self.assertEqual(1, len(wheels))
            with zipfile.ZipFile(wheels[0]) as wheel:
                names = set(wheel.namelist())
                metadata_name = next(
                    name for name in names if name.endswith(".dist-info/METADATA")
                )
                metadata = wheel.read(metadata_name).decode("utf-8")
                unsafe_text: list[str] = []
                for name in names:
                    if not _is_public_text(name):
                        continue
                    content = wheel.read(name).decode("utf-8", "replace")
                    if TOKEN_PATTERN.search(content) or "C:\\Users\\wsn" in content:
                        unsafe_text.append(name)

        self.assertTrue(any(name.endswith("/deepseek_icon.ico") for name in names))
        self.assertTrue(any(name.endswith(".dist-info/licenses/LICENSE") for name in names))
        self.assertTrue(any(name.endswith(".dist-info/licenses/NOTICE") for name in names))
        self.assertIn("Requires-Python: >=3.10", metadata)
        self.assertIn("Requires-Dist: requests==2.34.2", metadata)
        self.assertIn("Requires-Dist: Pillow==12.3.0", metadata)
        self.assertEqual([], unsafe_text)
        forbidden = (
            "tests/",
            ".superpowers/",
            "docs/superpowers/",
            "config.json",
            "usage_log.json",
            ".env",
        )
        self.assertFalse(any(any(part in name for part in forbidden) for name in names))

if __name__ == "__main__":
    unittest.main()
