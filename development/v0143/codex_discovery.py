"""Discover and validate the Codex executable without changing system PATH."""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping


@dataclass(frozen=True)
class CodexExecutable:
    path: Path
    source: str
    version: str


Validator = Callable[[Path], str | None]


class CodexExecutableResolver:
    """Small stateful wrapper used for startup and runtime rediscovery."""

    def __init__(
        self,
        explicit_path: str | Path | None = None,
        *,
        environment: Mapping[str, str] | None = None,
        process_paths: Iterable[Path] | None = None,
        validator: Validator = None,
        which: Callable[[str], str | None] = shutil.which,
    ):
        self.explicit_path = explicit_path
        self.environment = environment
        self.process_paths = process_paths
        self.validator = validator or validate_codex
        self.which = which
        self.current: CodexExecutable | None = None
        self.attempts: list[dict[str, str]] = []

    def resolve(self, *, force: bool = False) -> CodexExecutable | None:
        if not force and self.current and self.current.path.is_file():
            return self.current
        self.current, self.attempts = discover_codex(
            self.explicit_path,
            environment=self.environment,
            process_paths=self.process_paths,
            validator=self.validator,
            which=self.which,
        )
        return self.current


def codex_command(executable: Path, *arguments: str) -> list[str]:
    """Build a shell-free command, including support for npm's codex.cmd."""
    if os.name == "nt" and executable.suffix.lower() in {".cmd", ".bat"}:
        command_line = subprocess.list2cmdline([str(executable), *arguments])
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", command_line]
    return [str(executable), *arguments]


def validate_codex(path: Path, *, timeout: float = 8.0) -> str | None:
    """Return the reported CLI version only when a candidate runs successfully."""
    try:
        completed = subprocess.run(
            codex_command(path, "--version"),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = (completed.stdout or completed.stderr or "").strip()
    if completed.returncode != 0 or not output or "codex-cli" not in output.lower():
        return None
    return output.splitlines()[0].strip()


def running_codex_paths() -> list[Path]:
    """Read executable paths for running codex.exe processes using Windows APIs."""
    if os.name != "nt":
        return []
    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_ulong), ("cntUsage", ctypes.c_ulong),
            ("th32ProcessID", ctypes.c_ulong), ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", ctypes.c_ulong), ("cntThreads", ctypes.c_ulong),
            ("th32ParentProcessID", ctypes.c_ulong), ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_ulong), ("szExeFile", ctypes.c_wchar * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_ulong, ctypes.c_ulong]
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)
    ]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    invalid_handle = ctypes.c_void_p(-1).value
    if snapshot == invalid_handle:
        return []

    paths: list[Path] = []
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        has_entry = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while has_entry:
            if entry.szExeFile.lower() == "codex.exe":
                process = kernel32.OpenProcess(0x1000, False, entry.th32ProcessID)
                if process:
                    try:
                        size = ctypes.c_ulong(32768)
                        buffer = ctypes.create_unicode_buffer(size.value)
                        if kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
                            paths.append(Path(buffer.value))
                    finally:
                        kernel32.CloseHandle(process)
            has_entry = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return paths


def _desktop_candidates(local_app_data: Path | None) -> list[Path]:
    if not local_app_data:
        return []
    base = local_app_data / "OpenAI" / "Codex" / "bin"
    try:
        candidates = list(base.glob("*/codex.exe"))
    except OSError:
        return []
    def modified(item: Path) -> float:
        try:
            return item.stat().st_mtime
        except OSError:
            return 0
    return sorted(candidates, key=modified, reverse=True)


def _macos_candidates(home: Path | None) -> list[Path]:
    """Return conventional Codex CLI locations on macOS; never crawl the disk."""
    if home is None:
        return []
    return [home / ".local" / "bin" / "codex", Path("/opt/homebrew/bin/codex"),
            Path("/usr/local/bin/codex")]


def discover_codex(
    explicit_path: str | Path | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    process_paths: Iterable[Path] | None = None,
    validator: Validator = validate_codex,
    which: Callable[[str], str | None] = shutil.which,
) -> tuple[CodexExecutable | None, list[dict[str, str]]]:
    """Resolve Codex in priority order and return privacy-safe attempt diagnostics."""
    env = os.environ if environment is None else environment
    candidates: list[tuple[Path, str]] = []
    preliminary_attempts: list[dict[str, str]] = []
    if explicit_path:
        candidates.append((Path(explicit_path).expanduser(), "configured"))

    path_found = False
    for name in ("codex", "codex.exe", "codex.cmd"):
        # The default resolver must search the environment snapshot supplied to
        # this discovery pass.  Frozen Windows launchers may adjust their own
        # process PATH during bootstrap, so relying on shutil.which's implicit
        # os.environ can mislabel a Desktop install as a PATH install.
        found = shutil.which(name, path=env.get("PATH")) if which is shutil.which else which(name)
        if found:
            path_found = True
            candidates.append((Path(found), "PATH"))
    if not path_found:
        preliminary_attempts.append({"source": "PATH", "result": "missing"})

    local_app_data = Path(env["LOCALAPPDATA"]) if env.get("LOCALAPPDATA") else None
    desktop_candidates = _desktop_candidates(local_app_data)

    # Codex Desktop may temporarily prepend its versioned bin directory to
    # child-process PATH.  Preserve the installation provenance in that case
    # instead of presenting it as a generic PATH installation.
    if local_app_data:
        desktop_bin = (local_app_data / "OpenAI" / "Codex" / "bin").resolve(strict=False)
        candidates = [
            (path, "Codex Desktop" if source == "PATH" and path.resolve(strict=False).is_relative_to(desktop_bin) else source)
            for path, source in candidates
        ]
    candidates.extend((path, "Codex Desktop") for path in desktop_candidates)
    if not desktop_candidates:
        preliminary_attempts.append({"source": "Codex Desktop", "result": "missing"})
    active_paths = running_codex_paths() if process_paths is None else list(process_paths)
    candidates.extend((Path(path), "running process") for path in active_paths)
    if not active_paths:
        preliminary_attempts.append({"source": "running process", "result": "missing"})

    if env.get("APPDATA"):
        npm = Path(env["APPDATA"]) / "npm"
        candidates.extend(((npm / "codex.cmd", "npm"), (npm / "codex.exe", "npm")))

    if env.get("USERPROFILE"):
        candidates.append((Path(env["USERPROFILE"]) / ".codex" / ".sandbox-bin" / "codex.exe", "sandbox fallback"))

    if sys.platform == "darwin":
        home = Path(env["HOME"]) if env.get("HOME") else None
        candidates.extend((path, "Codex CLI (macOS)") for path in _macos_candidates(home))

    attempts: list[dict[str, str]] = preliminary_attempts
    seen: set[str] = set()
    for path, source in candidates:
        try:
            normalized = path.resolve(strict=False)
        except OSError:
            normalized = path.absolute()
        key = os.path.normcase(str(normalized))
        if key in seen:
            continue
        seen.add(key)
        if not normalized.is_file():
            attempts.append({"source": source, "result": "missing"})
            continue
        version = validator(normalized)
        if version:
            attempts.append({"source": source, "result": "valid"})
            return CodexExecutable(normalized, source, version), attempts
        attempts.append({"source": source, "result": "invalid"})
    return None, attempts
