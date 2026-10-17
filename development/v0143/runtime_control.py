"""Windows-friendly instance discovery, port resolution, and ownership locks."""

from __future__ import annotations

import ctypes
import getpass
import hashlib
import json
import os
import socket
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


_test_port_base = os.environ.get("QUOTALAB_TEST_PORT_BASE") if os.environ.get("QUOTALAB_TEST_SCOPE") else None
DEFAULT_PORT = int(_test_port_base) if _test_port_base else 18765
LAST_PORT = DEFAULT_PORT + 34
LOCK_STALE_SECONDS = 120


def utc_timestamp() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def user_scope() -> str:
    override = os.environ.get("QUOTALAB_TEST_SCOPE", "").strip()
    identity = override or f"{getpass.getuser()}|{os.environ.get('USERDOMAIN', '')}"
    return hashlib.sha256(identity.encode("utf-8", "replace")).hexdigest()[:20]


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def port_listening(host: str, port: int, timeout: float = 0.15) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _read_json(url: str, timeout: float) -> dict[str, Any] | None:
    try:
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "QuotaLab-Discovery"})
        with urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                return None
            value = json.loads(response.read(65536).decode("utf-8"))
            return value if isinstance(value, dict) else None
    except (OSError, HTTPError, URLError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def probe_instance(host: str, port: int, timeout: float = 0.35) -> dict[str, Any] | None:
    """Identify v0.6 and legacy v0.5 instances without trusting a listening port alone."""
    base = f"http://{host}:{port}"
    current = _read_json(base + "/api/instance", timeout)
    if current and current.get("app") == "QuotaLab":
        current.setdefault("port", port)
        current.setdefault("url", base)
        return current
    legacy = _read_json(base + "/api/status", timeout)
    app = legacy.get("app") if legacy else None
    if isinstance(app, dict) and app.get("name") == "QuotaLab":
        plan = legacy.get("usage_plan") if isinstance(legacy.get("usage_plan"), dict) else {}
        return {
            "app": "QuotaLab",
            "version": str(app.get("version") or "legacy"),
            "instanceId": None,
            "pid": None,
            "port": port,
            "mode": app.get("mode"),
            "startedAt": None,
            "schedulerActive": bool(plan.get("enabled")),
            "anchorExperimentActive": bool(legacy.get("active_experiment")),
            "legacy": True,
            "url": base,
        }
    return None


def find_existing_instance(host: str = "127.0.0.1", ports=range(DEFAULT_PORT, LAST_PORT + 1)) -> dict[str, Any] | None:
    for port in ports:
        if not port_listening(host, port):
            continue
        found = probe_instance(host, port)
        if found:
            return found
    return None


@dataclass
class PortResolution:
    action: str
    port: int | None = None
    instance: dict[str, Any] | None = None
    error: str | None = None


def resolve_port(host: str, requested_port: int | None, *, scan_existing: bool = True) -> PortResolution:
    explicit = requested_port is not None
    if explicit and not 1 <= int(requested_port) <= 65535:
        return PortResolution("error", error="Port must be between 1 and 65535")
    if scan_existing:
        existing = find_existing_instance(host)
        if existing:
            return PortResolution("existing", port=int(existing["port"]), instance=existing)
    if explicit:
        port = int(requested_port)
        if not port_listening(host, port):
            return PortResolution("start", port=port)
        existing = probe_instance(host, port)
        if existing:
            return PortResolution("existing", port=port, instance=existing)
        return PortResolution("error", port=port, error=f"Port {port} is occupied by another application")
    for port in range(DEFAULT_PORT, LAST_PORT + 1):
        if not port_listening(host, port):
            return PortResolution("start", port=port)
        existing = probe_instance(host, port)
        if existing:
            return PortResolution("existing", port=port, instance=existing)
    return PortResolution("error", error=f"No available local port in {DEFAULT_PORT}-{LAST_PORT}")


class WindowsUserMutex:
    """Per-user named mutex. Windows releases it automatically after a crash."""

    def __init__(self, purpose: str):
        self.name = f"Local\\QuotaLab-{purpose}-{user_scope()}"
        self.handle = None

    def acquire(self) -> bool:
        if os.name != "nt":
            return True
        kernel32 = ctypes.windll.kernel32
        kernel32.SetLastError(0)
        handle = kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            return False
        if kernel32.GetLastError() == 183:
            kernel32.CloseHandle(handle)
            return False
        self.handle = handle
        return True

    def release(self) -> None:
        if self.handle and os.name == "nt":
            ctypes.windll.kernel32.CloseHandle(self.handle)
        self.handle = None


class OwnershipLock:
    """Auditable PID/token lock with safe stale-owner recovery."""

    def __init__(self, path: Path, purpose: str):
        self.path = path
        self.purpose = purpose
        self.token = uuid.uuid4().hex
        self.owned = False

    def _payload(self) -> dict[str, Any]:
        return {
            "app": "QuotaLab",
            "purpose": self.purpose,
            "pid": os.getpid(),
            "token": self.token,
            "heartbeat": utc_timestamp(),
            "heartbeatEpoch": time.time(),
        }

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(3):
            payload = json.dumps(self._payload(), ensure_ascii=False).encode("utf-8")
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                try:
                    os.write(descriptor, payload)
                finally:
                    os.close(descriptor)
                self.owned = True
                return True
            except FileExistsError:
                try:
                    before = self.path.read_bytes()
                    owner = json.loads(before.decode("utf-8"))
                    owner_pid = int(owner.get("pid", 0))
                except (OSError, ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
                    before, owner_pid = b"", 0
                if process_alive(owner_pid):
                    return False
                try:
                    if not before or self.path.read_bytes() == before:
                        self.path.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    return False
        return False

    def heartbeat(self) -> bool:
        if not self.owned:
            return False
        try:
            current = json.loads(self.path.read_text(encoding="utf-8"))
            if current.get("token") != self.token:
                self.owned = False
                return False
            temporary = self.path.with_name(f"{self.path.name}.{self.token}.tmp")
            temporary.write_text(json.dumps(self._payload(), ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, self.path)
            return True
        except (OSError, json.JSONDecodeError):
            self.owned = False
            return False

    def release(self) -> None:
        if not self.owned:
            return
        try:
            current = json.loads(self.path.read_text(encoding="utf-8"))
            if current.get("token") == self.token:
                self.path.unlink(missing_ok=True)
        except (OSError, json.JSONDecodeError):
            pass
        self.owned = False


class LockHeartbeat:
    def __init__(self, locks: list[OwnershipLock], stop_event, interval: float = 5.0, callback=None):
        import threading
        self.locks = locks
        self.stop_event = stop_event
        self.interval = interval
        self.callback = callback
        self.thread = threading.Thread(target=self._run, name="runtime-heartbeat", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def _run(self) -> None:
        while not self.stop_event.wait(self.interval):
            for lock in self.locks:
                lock.heartbeat()
            if self.callback:
                self.callback()

    def join(self, timeout: float | None = None) -> None:
        self.thread.join(timeout)
