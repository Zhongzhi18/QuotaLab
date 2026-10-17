#!/usr/bin/env python3
"""QuotaLab - local, read-only Codex quota window observer."""

from __future__ import annotations

import argparse
import copy
import csv
import ctypes
import html as html_lib
import io
import json
import logging
import math
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from app_paths import default_data_root
from anchor_analysis import (
    ANCHOR_CONFIRM_TOLERANCE_SECONDS,
    POST_EXPIRY_GRACE_SECONDS,
    RESET_JITTER_TOLERANCE_SECONDS,
    classify_anchor_observation,
    reclassify_experiment,
)
from codex_discovery import CodexExecutable, CodexExecutableResolver, codex_command
from event_normalization import normalize_events
from experiment_evidence import build_evidence_bundle
from orchestration_service import OrchestrationService
from orchestration_migration import migrate_v081
from product_migration import migrate_v091
from product_migration_v011 import migrate_v010
from recommendation_lifecycle import RecommendationRefreshLoop
from product_migration_v012 import migrate_to_v012
from product_migration_v013 import migrate_to_v013
from product_migration_v0131 import migrate_to_v0131
from release_readiness import redact_text, append_calibration, smoke_summary, write_smoke, native_smoke, freshness as quota_freshness
from orchestration_verification import restart_transition
from execution_reliability import ExecutionEngine, ExecutionPreflight, ExecutionStore, FakeNetworkProbe, FakePowerAdapter, RealClock, metrics
from execution_runtime import ExecutionRuntime
from provider_adapter import CodexProviderAdapter, ProviderAdapter
from wake_adapter import FakeWakeAdapter, WindowsWakeAdapter
from runtime_control import (
    DEFAULT_PORT,
    LAST_PORT,
    LockHeartbeat,
    OwnershipLock,
    WindowsUserMutex,
    find_existing_instance,
    resolve_port,
)
from window_state import (
    FIXED,
    INSUFFICIENT_DATA,
    ROLLING,
    UNKNOWN,
    WindowStateAnalyzer,
    analyze_window_state,
    as_epoch,
    attribute_anchor_experiment,
    bucket_id,
    bucket_identity,
    pair_looks_rolling,
)


APP_VERSION = "0.14.3-plan-aware-preview"
DEFAULT_PORT = 18861
EXPERIMENT_TIMEOUT_SECONDS = 15 * 60
REQUEST_TIMEOUT_SECONDS = 15 * 60
SOURCE_ROOT = Path(__file__).resolve().parent
if os.environ.get("QUOTALAB_DATA_ROOT"):
    ROOT = Path(os.environ["QUOTALAB_DATA_ROOT"]).resolve()
else:
    ROOT = default_data_root()
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
EVIDENCE_DIR = ROOT / "evidence"
RUNTIME_DIR = ROOT / "runtime"
HISTORY_FILE = DATA_DIR / "quota_history.jsonl"
PLAN_FILE = DATA_DIR / "usage_plan.json"
SCHEDULED_RUNS_FILE = DATA_DIR / "scheduled_runs.jsonl"
RESET_EVENTS_FILE = DATA_DIR / "reset_events.jsonl"
DISPATCH_TESTS_FILE = DATA_DIR / "dispatch_tests.jsonl"
WINDOW_STATE_FILE = DATA_DIR / "window_state_observations.jsonl"
REPORT_HTML_FILE = EVIDENCE_DIR / "anchor_report.html"
REPORT_SVG_FILE = EVIDENCE_DIR / "anchor_results.svg"
LOG_FILE = LOG_DIR / "quotalab.log"
FATAL_LOG_FILE = LOG_DIR / "fatal.log"
INSTANCE_FILE = RUNTIME_DIR / "instance.json"
SETTINGS_FILE = ROOT / "settings.json"
EXECUTION_SETTINGS_FILE = DATA_DIR / "execution_settings.json"
MIGRATION_MARKER = RUNTIME_DIR / "migration-v0.6.json"
MIGRATION_V07_MARKER = RUNTIME_DIR / "migration-v0.7.json"
MINIMAL_ANCHOR_PROMPT = "Reply exactly OK. Do not use tools or modify files."
OBSERVATION_GAP_SECONDS = 600
SENSITIVE_KEY = re.compile(r"token|cookie|password|secret|authorization|api.?key|credential", re.I)
PRIVATE_ID_KEY = re.compile(r"^(accountId|installationId|codexHome)$", re.I)
EMAIL_IN_TEXT = re.compile(r"(?<![\w.+-])([\w.+-]{1,64})@([\w.-]+\.[A-Za-z]{2,})")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def current_observation_segments(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Feed only the latest continuous segment to the unchanged state analyzer."""
    groups: dict[tuple[Any, Any, Any], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(bucket_identity(row), []).append(row)
    current = []
    for group in groups.values():
        ordered = sorted(group, key=lambda item: as_epoch(item.get("observedAt", item.get("observed_at"))) or 0)
        segment: list[dict[str, Any]] = []
        previous = None
        for row in ordered:
            observed = as_epoch(row.get("observedAt", row.get("observed_at")))
            if observed is None:
                continue
            if previous is not None and observed - previous > OBSERVATION_GAP_SECONDS:
                segment = []
            segment.append(row)
            previous = observed
        current.extend(segment)
    return current


def canonical_scheduled_runs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One visible row per submitted turn; retain raw JSONL for audit.

    A persisted SUCCESS is authoritative over a later synthetic TIMEOUT for
    the same turn. This also disarms the old STARTED row after a restart.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for index, row in enumerate(rows):
        turn = row.get("turn_id") if row.get("action") == "model_call" else None
        key = f"turn:{turn}" if turn else f"row:{index}"
        groups.setdefault(key, []).append(row)
    priority = {"SUCCESS": 5, "FAILED": 4, "CANCELLED": 3, "TIMEOUT": 2, "STARTED": 1}
    visible = []
    for group in groups.values():
        chosen = max(group, key=lambda row: (priority.get(row.get("status"), 0),
                                              as_epoch(row.get("completed_at") or row.get("recorded_at")) or 0))
        visible.append({**chosen, "rawEventCount": len(group)})
    visible.sort(key=lambda row: as_epoch(row.get("completed_at") or row.get("recorded_at")) or 0, reverse=True)
    return visible


def safe_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def safe_int(value: Any, *, minimum: int | None = None) -> int | None:
    number = safe_number(value)
    if number is None:
        return None
    result = int(number)
    if minimum is not None and result < minimum:
        return None
    return result


def mask_email(value: Any) -> str | None:
    if not isinstance(value, str) or "@" not in value:
        return None
    local, domain = value.rsplit("@", 1)
    if not local or not domain:
        return None
    visible = local[:2] if len(local) > 1 else local[:1]
    return f"{visible}***@{domain}"


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            key_text = str(key)
            if SENSITIVE_KEY.search(key_text) or PRIVATE_ID_KEY.search(key_text):
                cleaned[key] = "[REDACTED]"
            elif key_text.lower() == "email":
                cleaned[key] = mask_email(item)
            else:
                cleaned[key] = sanitize(item)
        return cleaned
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)
    return value


def sanitize_text(value: str) -> str:
    value = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]+=*", r"\1[REDACTED]", value)
    return EMAIL_IN_TEXT.sub(lambda match: mask_email(match.group(0)) or "[REDACTED_EMAIL]", value)


def show_windows_message(title: str, message: str, *, error: bool = False) -> None:
    if os.name != "nt" or os.environ.get("QUOTALAB_TEST_SCOPE"):
        return
    try:
        flags = 0x10 if error else 0x40
        ctypes.windll.user32.MessageBoxW(None, message, title, flags)
    except Exception:
        logging.debug("Could not display Windows message", exc_info=True)


def ensure_console() -> None:
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    try:
        if not ctypes.windll.kernel32.AttachConsole(-1):
            ctypes.windll.kernel32.AllocConsole()
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
    except OSError:
        pass


def write_fatal_log(*, mode: str, port: int | None, exc: BaseException) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    details = {
        "timestamp": utc_now(),
        "version": APP_VERSION,
        "pid": os.getpid(),
        "mode": mode,
        "port": port,
        "exception": redact_text(sanitize_text(str(exc))),
        "traceback": redact_text(sanitize_text("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))),
    }
    with FATAL_LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(details, ensure_ascii=False, indent=2) + "\n")
    return FATAL_LOG_FILE


def migrate_v05_state(root: Path = ROOT) -> dict[str, Any]:
    """Create one backup before first v0.6 packaged run; never rewrites user records."""
    runtime_dir = root / "runtime"
    marker = runtime_dir / "migration-v0.6.json"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    if marker.is_file():
        try:
            return json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"status": "already-migrated"}
    data_dir = root / "data"
    evidence_dir = root / "evidence"
    had_v05_data = data_dir.exists() and any(data_dir.iterdir())
    backup = None
    if had_v05_data:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = root / f"backup-v0.5-{stamp}"
        counter = 1
        while backup.exists():
            backup = root / f"backup-v0.5-{stamp}-{counter}"
            counter += 1
        shutil.copytree(data_dir, backup / "data")
        if evidence_dir.exists():
            shutil.copytree(evidence_dir, backup / "evidence")
    result = {
        "status": "migrated" if had_v05_data else "fresh",
        "from": "0.5.x" if had_v05_data else None,
        "to": APP_VERSION,
        "at": utc_now(),
        "backup": str(backup) if backup else None,
    }
    marker.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def migrate_v06_state(root: Path = ROOT) -> dict[str, Any]:
    """Preserve all v0.6 local state before the first v0.7 packaged run."""
    runtime_dir = root / "runtime"
    marker = runtime_dir / "migration-v0.7.json"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    if marker.is_file():
        try:
            return json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"status": "already-migrated"}
    has_user_state = (
        ((root / "data").exists() and any((root / "data").iterdir()))
        or (root / "settings.json").is_file()
    )
    sources = [name for name in ("data", "evidence", "logs") if has_user_state and (root / name).exists()]
    backup = None
    if sources:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = root / f"backup-v0.6-{stamp}"
        counter = 1
        while backup.exists():
            backup = root / f"backup-v0.6-{stamp}-{counter}"
            counter += 1
        for name in sources:
            shutil.copytree(root / name, backup / name)
        if (root / "settings.json").is_file():
            shutil.copy2(root / "settings.json", backup / "settings.json")
    result = {"status": "migrated" if sources else "fresh", "from": "0.6.x" if sources else None, "to": APP_VERSION, "at": utc_now(), "backup": str(backup) if backup else None}
    marker.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def load_settings() -> dict[str, Any]:
    defaults = {"setup_completed": False, "codex_path": None}
    try:
        saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            defaults.update({key: saved.get(key) for key in defaults})
    except (OSError, json.JSONDecodeError):
        pass
    return defaults


def save_settings(settings: dict[str, Any]) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def window_kind(duration: int | None) -> str:
    if duration == 300:
        return "5-hour window"
    if duration == 10080:
        return "Weekly window"
    if duration is None:
        return "Window"
    return f"Custom window ({duration} min)"


def parse_window(raw: Any, *, slot: str, limit_id: str | None, limit_name: str | None) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    used_number = safe_number(raw.get("usedPercent"))
    used = None if used_number is None else max(0.0, min(100.0, used_number))
    duration = safe_int(raw.get("windowDurationMins"), minimum=1)
    resets_at = safe_int(raw.get("resetsAt"), minimum=1)
    return {
        "limit_id": limit_id,
        "limit_name": limit_name,
        "slot": slot,
        "kind": window_kind(duration),
        "used_percent": used,
        "remaining_percent": None if used is None else round(100.0 - used, 2),
        "window_duration_minutes": duration,
        "resets_at": resets_at,
    }


def parse_rate_limit_response(result: Any) -> dict[str, Any]:
    """Normalize current and older app-server response shapes without assuming buckets."""
    result = result if isinstance(result, dict) else {}
    by_id = result.get("rateLimitsByLimitId")
    snapshots: list[tuple[str | None, Any]] = []
    if isinstance(by_id, dict) and by_id:
        snapshots.extend((str(key), value) for key, value in by_id.items())
    elif isinstance(result.get("rateLimits"), dict):
        snapshots.append((None, result["rateLimits"]))

    windows: list[dict[str, Any]] = []
    limits: list[dict[str, Any]] = []
    for fallback_id, snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        limit_id = snapshot.get("limitId") if isinstance(snapshot.get("limitId"), str) else fallback_id
        limit_name = snapshot.get("limitName") if isinstance(snapshot.get("limitName"), str) else None
        parsed_windows = []
        for slot in ("primary", "secondary"):
            parsed = parse_window(snapshot.get(slot), slot=slot, limit_id=limit_id, limit_name=limit_name)
            if parsed:
                windows.append(parsed)
                parsed_windows.append(parsed)
        limits.append({
            "limit_id": limit_id,
            "limit_name": limit_name,
            "plan_type": snapshot.get("planType"),
            "rate_limit_reached_type": snapshot.get("rateLimitReachedType"),
            "spend_control_reached": snapshot.get("spendControlReached"),
            "windows": parsed_windows,
        })

    credits = result.get("rateLimitResetCredits")
    reset_credits = None
    if isinstance(credits, dict):
        reset_credits = {
            "available_count": safe_int(credits.get("availableCount"), minimum=0),
            "credits": sanitize(credits.get("credits")) if isinstance(credits.get("credits"), list) else None,
        }
    return {
        "windows": windows,
        "limits": limits,
        "rate_limit_reset_credits": reset_credits,
        "ordinary_usage_allowed": result.get("ordinaryUsageAllowed"),
    }


def stable_window(window: dict[str, Any]) -> dict[str, Any]:
    return {k: window.get(k) for k in (
        "limit_id", "limit_name", "slot", "used_percent", "remaining_percent",
        "window_duration_minutes", "resets_at",
    )}


def window_identity(window: dict[str, Any]) -> tuple[Any, ...]:
    return (window.get("limit_id"), window.get("slot"), window.get("window_duration_minutes"))


@dataclass
class PendingRequest:
    event: threading.Event
    response: dict[str, Any] | None = None


class AppServerError(RuntimeError):
    pass


class CodexAppServer:
    def __init__(self, on_notification, executable: Path, debug: bool = False):
        self.on_notification = on_notification
        self.executable = executable
        self.debug = debug
        self.process: subprocess.Popen[str] | None = None
        self.pending: dict[int, PendingRequest] = {}
        self.pending_lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.next_id = 1
        self.reader: threading.Thread | None = None
        self.stderr_reader: threading.Thread | None = None
        self.closed = threading.Event()

    def start(self) -> None:
        if not self.executable.is_file():
            raise AppServerError("CODEX_NOT_FOUND")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        self.process = subprocess.Popen(
            codex_command(self.executable, "app-server", "--stdio"),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=flags,
        )
        self.closed.clear()
        self.reader = threading.Thread(target=self._read_stdout, name="app-server-stdout", daemon=True)
        self.stderr_reader = threading.Thread(target=self._read_stderr, name="app-server-stderr", daemon=True)
        self.reader.start()
        self.stderr_reader.start()
        self.request("initialize", {
            "clientInfo": {"name": "quota_lab", "title": "QuotaLab", "version": APP_VERSION},
            "capabilities": {"experimentalApi": True},
        }, timeout=15)
        self.notify("initialized")

    def _write(self, payload: dict[str, Any]) -> None:
        process = self.process
        if not process or not process.stdin or process.poll() is not None:
            raise AppServerError("APP_SERVER_DISCONNECTED")
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if self.debug:
            logging.debug("RPC -> %s", json.dumps(sanitize(payload), ensure_ascii=False))
        with self.write_lock:
            process.stdin.write(line + "\n")
            process.stdin.flush()

    def request(self, method: str, params: Any = None, timeout: float = 20) -> Any:
        with self.pending_lock:
            request_id = self.next_id
            self.next_id += 1
            pending = PendingRequest(threading.Event())
            self.pending[request_id] = pending
        payload: dict[str, Any] = {"id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        try:
            self._write(payload)
            if not pending.event.wait(timeout):
                raise AppServerError(f"RPC_TIMEOUT:{method}")
            response = pending.response or {}
            if "error" in response:
                error = response["error"]
                raise AppServerError(f"RPC_ERROR:{method}:{error.get('message', error) if isinstance(error, dict) else error}")
            return response.get("result")
        finally:
            with self.pending_lock:
                self.pending.pop(request_id, None)

    def notify(self, method: str, params: Any = None) -> None:
        payload: dict[str, Any] = {"method": method}
        if params is not None:
            payload["params"] = params
        self._write(payload)

    def _read_stdout(self) -> None:
        process = self.process
        if not process or not process.stdout:
            return
        try:
            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    logging.warning("Non-JSON app-server stdout: %s", sanitize_text(line[:500]))
                    continue
                if self.debug:
                    logging.debug("RPC <- %s", json.dumps(sanitize(message), ensure_ascii=False))
                if "id" in message and ("result" in message or "error" in message):
                    with self.pending_lock:
                        pending = self.pending.get(message["id"])
                    if pending:
                        pending.response = message
                        pending.event.set()
                elif "id" in message and "method" in message:
                    self._answer_unknown_request(message)
                elif "method" in message:
                    self.on_notification(message.get("method"), message.get("params"))
        except Exception:
            logging.exception("App Server stdout reader failed")
        finally:
            self.closed.set()
            with self.pending_lock:
                for pending in self.pending.values():
                    pending.response = {"error": {"message": "app-server closed"}}
                    pending.event.set()

    def _answer_unknown_request(self, message: dict[str, Any]) -> None:
        try:
            self._write({"id": message.get("id"), "error": {"code": -32601, "message": "QuotaLab does not implement this server request"}})
        except Exception:
            logging.debug("Could not answer server request", exc_info=True)

    def _read_stderr(self) -> None:
        process = self.process
        if not process or not process.stderr:
            return
        for line in process.stderr:
            cleaned = line.strip()
            if cleaned:
                logging.debug("app-server stderr: %s", sanitize_text(cleaned[:1000]))

    def stop(self) -> None:
        process = self.process
        if not process:
            return
        try:
            if process.stdin:
                process.stdin.close()
        except OSError:
            pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()


class QuotaLabState:
    def __init__(self, *, mock: bool, debug: bool, codex_version: str, codex_source: str | None = None):
        self.mock = mock
        self.debug = debug
        self.lock = threading.RLock()
        self.refresh_requested = threading.Event()
        self.stop_event = threading.Event()
        self.anchor_trigger = None
        self.codex_rediscover = None
        self.codex_configure = None
        self.probe_thread = None
        self.execution_runtime: ExecutionRuntime | None = None
        self.settings = load_settings()
        self.orchestrator = OrchestrationService(DATA_DIR, mock=mock)
        from desktop_notifications import DesktopReminders
        asset_root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
        self.desktop_notifications = DesktopReminders(self, DATA_DIR / 'desktop-notifications.json', asset_root / 'assets' / 'phase.ico')
        from product_evolution import ProductEvolution
        self.evolution = ProductEvolution(ROOT, APP_VERSION)
        reset_events = self._load_jsonl(RESET_EVENTS_FILE, 2000)
        stored_window_observations = self._load_jsonl(WINDOW_STATE_FILE, 10000)
        legacy_window_observations = self._observations_from_reset_events(reset_events)
        self.window_analyzer = WindowStateAnalyzer(current_observation_segments([*legacy_window_observations, *stored_window_observations]))
        self.runtime: dict[str, Any] = {
            "instance_id": None,
            "pid": os.getpid(),
            "port": None,
            "started_at": utc_now(),
            "scheduler_owner": False,
            "last_heartbeat": utc_now(),
        }
        self.state: dict[str, Any] = {
            "app": {"name": "QuotaLab", "version": APP_VERSION, "mode": "mock" if mock else "live"},
            "provider": {"id": "openai-codex", "name": "OpenAI Codex"},
            "account": None,
            "windows": [],
            "limits": [],
            "rate_limit_reset_credits": None,
            "ordinary_usage_allowed": None,
            "status": "MOCK DATA" if mock else "STARTING",
            "error": None,
            "last_snapshot": None,
            "last_source": None,
            "codex_cli_version": codex_version,
            "codex_cli_source": codex_source,
            "codex_cli": {
                "found": mock or codex_source is not None,
                "path": None if mock or codex_source is None else "[redacted]\\codex.exe",
                "version": codex_version,
                "source": codex_source,
                "checked_sources": [],
            },
            "app_server": "MOCK" if mock else "CONNECTING",
            "recent_observations": [],
            "reset_events": normalize_events([item for item in reset_events if item.get("event_type") != "ROLLING_UPDATE"])[:50],
            "window_state_observations": stored_window_observations[:500],
            "window_states": self.window_analyzer.all_states(),
            "window_transitions": [],
            "probe": {"status": "IDLE", "intervalSeconds": 60, "durationSeconds": 600, "samples": 0, "startedAt": None, "completedAt": None, "readOnly": True, "modelTaskSent": False},
            "usage_plan": self._load_plan(),
            "scheduled_runs": canonical_scheduled_runs(self._load_jsonl(SCHEDULED_RUNS_FILE, 1000))[:30],
            "dispatch_tests": self._load_jsonl(DISPATCH_TESTS_FILE, 30),
            "next_scheduled_run": None,
            "active_experiment": None,
            "experiments": self._load_experiments(),
        }
        # Legacy imported schedules are evidence only. Startup must never resume them.
        self.state["usage_plan"]["enabled"] = False
        self.state["usage_plan"]["auto_anchor"] = False
        evidence_summary = EVIDENCE_DIR / "v0.8.1" / "a3" / "a3_summary.json"
        try:
            self.state["experiment_evidence"] = json.loads(evidence_summary.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.state["experiment_evidence"] = None

    @staticmethod
    def _observations_from_reset_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        observations = []
        for event in reversed(events):
            observations.append({
                "observedAt": event.get("detected_at"),
                "limitId": event.get("limit_id"), "slot": event.get("slot"),
                "windowDurationMins": event.get("window_duration_minutes"),
                "resetAt": event.get("new_resets_at"), "source": "legacy-reset-event",
            })
        return observations

    def set_runtime(self, *, instance_id: str, port: int, scheduler_owner: bool) -> None:
        with self.lock:
            self.runtime.update({
                "instance_id": instance_id,
                "pid": os.getpid(),
                "port": port,
                "scheduler_owner": scheduler_owner,
                "last_heartbeat": utc_now(),
            })

    def touch_runtime(self) -> None:
        with self.lock:
            self.runtime["last_heartbeat"] = utc_now()

    def instance_info(self) -> dict[str, Any]:
        with self.lock:
            active_experiment = bool(self.state.get("active_experiment"))
            mode = "mock" if self.mock else "live"
            runtime = copy.deepcopy(self.runtime)
            codex_cli = copy.deepcopy(self.state.get("codex_cli"))
        return {
            "app": "QuotaLab",
            "version": APP_VERSION,
            "instanceId": runtime.get("instance_id"),
            "pid": runtime.get("pid"),
            "port": runtime.get("port"),
            "mode": mode,
            "startedAt": runtime.get("started_at"),
            "schedulerActive": bool(runtime.get("scheduler_owner")),
            "anchorExperimentActive": active_experiment,
            "lastHeartbeat": runtime.get("last_heartbeat"),
            "codexCli": codex_cli,
        }

    def health(self) -> dict[str, Any]:
        with self.lock:
            app_server = self.state.get("app_server")
            public_status = self.state.get("status")
            scheduler_owner = bool(self.runtime.get("scheduler_owner"))
            heartbeat = self.runtime.get("last_heartbeat")
            codex_cli = copy.deepcopy(self.state.get("codex_cli"))
        codex_connected = self.mock or app_server == "CONNECTED"
        scheduler = "active" if scheduler_owner and codex_connected else "paused"
        return {
            "status": "ok" if codex_connected else "degraded",
            "application": "running",
            "dashboard": "running",
            "scheduler": scheduler,
            "schedulerOwner": scheduler_owner,
            "codex": "mock" if self.mock else ("connected" if codex_connected else "disconnected"),
            "codexStatus": public_status,
            "mode": "mock" if self.mock else "live",
            "version": APP_VERSION,
            "pid": os.getpid(),
            "port": self.runtime.get("port"),
            "lastHeartbeat": heartbeat,
            "codexCli": codex_cli,
        }

    @staticmethod
    def _load_jsonl(path: Path, limit: int) -> list[dict[str, Any]]:
        if not path.is_file():
            return []
        loaded: list[dict[str, Any]] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                item = json.loads(line)
                if isinstance(item, dict):
                    loaded.append(item)
        except (OSError, json.JSONDecodeError):
            logging.warning("Ignoring unreadable history file: %s", path.name)
            return []
        return list(reversed(loaded[-limit:]))

    @staticmethod
    def _default_plan() -> dict[str, Any]:
        return {
            "enabled": False,
            "auto_anchor": False,
            "times": ["06:00", "12:00", "18:00"],
            "campaign_slots": [],
            "intensity_sessions": [],
            "planning_strategy": "manual_times",
            "max_daily_runs": 3,
            "target_anchor_runs": 5,
            "remaining_anchor_runs": 5,
            "prompt": MINIMAL_ANCHOR_PROMPT,
            "timezone": "local",
        }

    def _load_plan(self) -> dict[str, Any]:
        plan = self._default_plan()
        if PLAN_FILE.is_file():
            try:
                saved = json.loads(PLAN_FILE.read_text(encoding="utf-8"))
                if isinstance(saved, dict):
                    plan.update({key: saved[key] for key in plan if key in saved})
            except (OSError, json.JSONDecodeError):
                logging.warning("Ignoring unreadable usage plan")
        plan["prompt"] = MINIMAL_ANCHOR_PROMPT
        return plan

    def _load_experiments(self) -> list[dict[str, Any]]:
        if not DATA_DIR.exists():
            return []
        try:
            build_evidence_bundle(ROOT, EVIDENCE_DIR / "v0.8.1" / "a3", apply_migration=False)
        except (OSError, ValueError, TypeError) as exc:
            logging.warning("A3 evidence migration skipped: %s", exc)
        loaded = []
        for path in sorted(DATA_DIR.glob("anchor_experiment_*.json"), reverse=True)[:20]:
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(item, dict):
                    loaded.append(item)  # Frozen history: no reclassification or writes on load.
            except (OSError, json.JSONDecodeError):
                logging.warning("Ignoring unreadable experiment file: %s", path.name)
        return loaded

    def public(self) -> dict[str, Any]:
        expired = None
        expired_runs: list[dict[str, Any]] = []
        with self.lock:
            expired = self._expire_experiment_locked()
            expired_runs = self._expire_scheduled_runs_locked()
            snapshot = copy.deepcopy(self.state)
            real = [item for item in snapshot.get("experiments", []) if item.get("mode") == "live"]
            controls = [item for item in real if item.get("experimentType") == "IN_WINDOW_CONTROL"]
            eligible = [item for item in real if item.get("eligibleForAnchorStatistics") is True]
            window_states = snapshot.get("window_states", {})
            snapshot["report_summary"] = {
                "real_observations": len(real),
                "in_window_controls": len(controls),
                "eligible_anchor_trials": len(eligible),
                "confirmed_anchors": sum(item.get("classification") in {"ANCHOR_CONFIRMED", "ANCHOR_CONFIRMED_BY_STATE_TRANSITION"} for item in real),
                "no_reanchor": sum(item.get("classification") in {"NO_REANCHOR", "IN_WINDOW_CONTROL_NO_REANCHOR"} for item in real),
                "inconclusive": sum(item.get("classification") in {"INCONCLUSIVE", "TIMEOUT", "REQUEST_FAILED"} for item in real),
                "failed_requests": sum(item.get("requestStatus") == "FAILED" for item in real),
                "rolling_windows": sum(item.get("state") == ROLLING for item in window_states.values()),
                "fixed_windows": sum(item.get("state") == FIXED for item in window_states.values()),
                "rolling_to_fixed_transitions": sum(item.get("transitionType") == "ROLLING_TO_FIXED" for item in real),
                "anchor_candidates": sum(item.get("classification") in {"ANCHOR_CANDIDATE", "ROLLING_TO_FIXED_ANCHOR_CANDIDATE"} for item in real),
                "eligible_transition_trials": sum(item.get("eligibleForConfirmedAnchorStatistics") is True for item in real),
                "confirmed_state_transition_anchors": sum(item.get("classification") in {"ANCHOR_CONFIRMED_BY_STATE_TRANSITION", "ANCHOR_CONFIRMED"} and item.get("transitionType") == "ROLLING_TO_FIXED" for item in real),
                # Compatibility aliases; neither includes control trials as eligible successes.
                "real_experiments": len(real),
                "measured_experiments": len(eligible),
                "target_experiments": snapshot.get("usage_plan", {}).get("target_anchor_runs", 5),
                "ready_for_review": len(eligible) >= 3,
            }
            cli = snapshot.get("codex_cli", {})
            account = snapshot.get("account")
            steps = {
                "detect": bool(cli.get("found")),
                "connect": snapshot.get("app_server") in {"CONNECTED", "MOCK"},
                "account": bool(account),
                "quota": bool(snapshot.get("windows")) and quota_freshness(snapshot)=='FRESH',
            }
            snapshot["setup"] = {
                "completed": bool(self.settings.get("setup_completed")),
                "ready": all(steps.values()),
                "steps": steps,
            }
            snapshot["diagnostics"] = {
                "localAgent": "running",
                "address": f"127.0.0.1:{self.runtime.get('port') or DEFAULT_PORT}",
                "provider": snapshot.get("provider"),
                "codexCli": cli,
                "appServer": snapshot.get("app_server"),
                "authentication": "connected" if account else "unavailable",
                "accountPlan": account.get("plan_type") if isinstance(account, dict) else None,
                "scheduler": "owner" if self.runtime.get("scheduler_owner") else "read-only",
                "lastQuotaUpdate": snapshot.get("last_snapshot"),
                "dataDirectory": str(ROOT),
                "windowStateAnalyzer": "running",
                "windowStates": window_states,
                "probe": snapshot.get("probe"),
                "experimentEvaluator": "READY",
                "eventDedup": "READY",
                "evidenceStore": "READY" if snapshot.get("experiment_evidence") else "NO_A3_EVIDENCE",
            }
            snapshot["health"] = self.health()
        if self.execution_runtime is not None:
            snapshot["execution"] = self.execution_runtime.public()
        snapshot["orchestration"] = self.orchestrator.public(snapshot)
        snapshot['evolution'] = self.evolution.public(snapshot, self.orchestrator)
        snapshot['desktopNotifications'] = self.desktop_notifications.public()
        if expired:
            self._save_experiment(expired)
        for row in expired_runs:
            self._append_jsonl(SCHEDULED_RUNS_FILE, row)
        return snapshot

    def set_connection(self, app_server: str, *, status: str | None = None, error: str | None = None) -> None:
        with self.lock:
            if app_server == "CONNECTED" and self.state.get("app_server") != "CONNECTED":
                self.orchestrator.lifecycle.append({"type": "CODEX_RECONNECT", "timestamp": utc_now()})
            if app_server=='CONNECTED' and self.state.get('app_server')!='CONNECTED':
                self.state['provider_connected_at']=utc_now()
                self.state['provider_generation']=self.state.get('provider_generation',0)+1
            self.state["app_server"] = app_server
            if status:
                self.state["status"] = status
            self.state["error"] = error

    def set_codex_discovery(self, result: CodexExecutable | None, attempts: list[dict[str, str]]) -> None:
        checked = list(dict.fromkeys(item.get("source", "unknown") for item in attempts))
        with self.lock:
            self.state["codex_cli_version"] = result.version if result else "Unavailable"
            self.state["codex_cli_source"] = result.source if result else None
            self.state["codex_cli"] = {
                "found": result is not None,
                "path": "[redacted]\\" + result.path.name if result else None,
                "version": result.version if result else None,
                "source": result.source if result else None,
                "checked_sources": checked,
            }

    def set_codex_rediscover(self, callback) -> None:
        with self.lock:
            self.codex_rediscover = callback

    def set_codex_configure(self, callback) -> None:
        with self.lock:
            self.codex_configure = callback

    def request_codex_rediscovery(self) -> dict[str, Any]:
        with self.lock:
            callback = self.codex_rediscover
        if callback is None:
            return {"status": "unavailable"}
        callback()
        return {"status": "rediscovery_requested"}

    def configure_codex_path(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("codex_path is required")
        with self.lock:
            callback = self.codex_configure
        if callback is None:
            raise ValueError("Codex configuration is unavailable")
        result = callback(value.strip())
        self.settings["codex_path"] = value.strip()
        save_settings(self.settings)
        return result

    def complete_setup(self) -> dict[str, Any]:
        public = self.public()
        if not public.get("setup", {}).get("ready") and not self.mock:
            raise ValueError("SETUP_NOT_READY")
        self.settings["setup_completed"] = True
        save_settings(self.settings)
        return {"status": "completed"}

    def record_mock_dispatch_test(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        public = self.public()
        row = {
            "id": uuid.uuid4().hex,
            "mode": "mock",
            "scheduledAt": now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "actualAt": utc_now(),
            "delaySeconds": 0.0,
            "requestResult": "MOCK_NO_REQUEST",
            "quotaBefore": copy.deepcopy(public.get("windows", [])),
            "quotaAfter": copy.deepcopy(public.get("windows", [])),
        }
        self._append_jsonl(DISPATCH_TESTS_FILE, row)
        with self.lock:
            self.state["dispatch_tests"].insert(0, copy.deepcopy(row))
            del self.state["dispatch_tests"][30:]
        return row

    def set_anchor_trigger(self, trigger) -> None:
        with self.lock:
            self.anchor_trigger = trigger

    def trigger_minimal_anchor(self) -> dict[str, Any]:
        if not self.mock:
            raise AppServerError("CLOUD_VALIDATION_READ_ONLY")
        with self.lock:
            trigger = self.anchor_trigger
        if trigger is None:
            raise AppServerError("APP_SERVER_TRIGGER_UNAVAILABLE")
        return trigger()

    def set_account(self, result: Any) -> None:
        result = result if isinstance(result, dict) else {}
        account = result.get("account")
        with self.lock:
            if isinstance(account, dict):
                self.state["account"] = {
                    "type": account.get("type"),
                    "email": mask_email(account.get("email")),
                    "plan_type": account.get("planType"),
                    "requires_openai_auth": result.get("requiresOpenaiAuth"),
                }
            else:
                self.state["account"] = None

    def update_limits(self, result: Any, source: str) -> None:
        parsed = parse_rate_limit_response(result)
        observed_at = utc_now()
        changed: list[dict[str, Any]] = []
        reset_events: list[dict[str, Any]] = []
        detailed_events: list[dict[str, Any]] = []
        state_observations: list[dict[str, Any]] = []
        completed_experiment = None
        with self.lock:
            before = {window_identity(w): stable_window(w) for w in self.state.get("windows", [])}
            for window in parsed["windows"]:
                stable = stable_window(window)
                identity = window_identity(window)
                previous_series = self.window_analyzer.observations(identity)
                previous_observation = previous_series[-1] if previous_series else None
                previous_stable_state = self.window_analyzer.last_stable_state.get(identity)
                previous_at = as_epoch(previous_observation.get("observedAt")) if previous_observation else None
                current_at = as_epoch(observed_at)
                if previous_at is not None and current_at is not None and current_at - previous_at > OBSERVATION_GAP_SECONDS:
                    gap = {
                        "detected_at": observed_at, "source": source,
                        "limit_id": stable.get("limit_id"), "slot": stable.get("slot"),
                        "window_duration_minutes": stable.get("window_duration_minutes"),
                        "event_type": "OBSERVATION_GAP", "gap_seconds": round(current_at - previous_at, 3),
                        "attribution": "UNKNOWN", "note": "Transition may have occurred while observations were absent",
                    }
                    reset_events.append(gap)
                    detailed_events.append(gap)
                    self.window_analyzer.series.pop(identity, None)
                    self.window_analyzer.last_stable_state.pop(identity, None)
                    previous_observation = None
                    previous_stable_state = None
                observation = {
                    "observedAt": observed_at, "limitId": stable.get("limit_id"),
                    "limitName": stable.get("limit_name"), "slot": stable.get("slot"),
                    "windowDurationMins": stable.get("window_duration_minutes"),
                    "usedPercent": stable.get("used_percent"), "remainingPercent": stable.get("remaining_percent"),
                    "resetAt": stable.get("resets_at"), "source": source,
                }
                analysis = self.window_analyzer.add(observation)
                observation.update({"horizonSeconds": analysis.get("latestHorizonSeconds"), "stateAtObservation": analysis.get("state")})
                state_observations.append(observation)
                window["window_state"] = analysis.get("state")
                window["window_state_evidence"] = analysis.get("evidence")
                window["window_state_samples"] = analysis.get("samples")
                window["window_state_span_seconds"] = analysis.get("observationSpanSeconds")
                window["reset_slope"] = analysis.get("resetSlope")
                window["horizon_seconds"] = analysis.get("latestHorizonSeconds")
                if before.get(window_identity(window)) != stable:
                    changed.append(stable)
                old = before.get(window_identity(window))
                old_reset = old.get("resets_at") if old else None
                new_reset = stable.get("resets_at")
                if old_reset and new_reset and abs(new_reset - old_reset) > RESET_JITTER_TOLERANCE_SECONDS:
                    rolling_update = bool(previous_observation and pair_looks_rolling(previous_observation, observation))
                    event = {
                        "detected_at": observed_at,
                        "source": source,
                        "limit_id": stable.get("limit_id"),
                        "slot": stable.get("slot"),
                        "window_duration_minutes": stable.get("window_duration_minutes"),
                        "old_resets_at": old_reset,
                        "new_resets_at": new_reset,
                        "delta_seconds": new_reset - old_reset,
                        "event_type": "ROLLING_UPDATE" if rolling_update else ("FIXED_RESET_CHANGE" if analysis.get("state") == FIXED else "TRUE_RESET_UNCONFIRMED"),
                        "window_state": analysis.get("state"),
                    }
                    detailed_events.append(event)
                    if not rolling_update:
                        reset_events.append(event)
                current_stable_state = analysis.get("state") if analysis.get("state") in {ROLLING, FIXED} else None
                if previous_stable_state and current_stable_state and previous_stable_state != current_stable_state:
                    transition = {
                        "detected_at": observed_at, "source": source,
                        "limit_id": stable.get("limit_id"), "slot": stable.get("slot"),
                        "window_duration_minutes": stable.get("window_duration_minutes"),
                        "old_resets_at": old_reset, "new_resets_at": new_reset,
                        "delta_seconds": None if old_reset is None or new_reset is None else new_reset - old_reset,
                        "event_type": f"{previous_stable_state}_TO_{current_stable_state}",
                        "window_state": current_stable_state,
                    }
                    transition = restart_transition(transition, self.orchestrator.lifecycle)
                    reset_events.append(transition)
                    detailed_events.append(transition)
            self.state.update(parsed)
            self.state["window_states"] = self.window_analyzer.all_states()
            self.state["last_snapshot"] = observed_at
            self.state['quota_provider_generation']=self.state.get('provider_generation',0)
            self.state["last_source"] = source
            self.state["status"] = "MOCK DATA" if self.mock else "LIVE"
            self.state["error"] = None
            observations = self.state["recent_observations"]
            for window in changed:
                observations.insert(0, {"observed_at": observed_at, "source": source, **window})
            del observations[20:]
            self.state["reset_events"] = normalize_events([*reversed(reset_events), *self.state["reset_events"]])[:50]
            for observation in state_observations:
                self.state["window_state_observations"].insert(0, observation)
            del self.state["window_state_observations"][500:]
            probe = self.state.get("probe", {})
            if probe.get("status") == "RUNNING":
                probe["samples"] = int(probe.get("samples", 0)) + 1
                probe["lastSampleAt"] = observed_at
            completed_experiment = self._detect_experiment_locked(observed_at)
        for window in changed:
            self._append_history(observed_at, source, window)
        for event in detailed_events:
            self._append_jsonl(RESET_EVENTS_FILE, event)
        for observation in state_observations:
            self._append_jsonl(WINDOW_STATE_FILE, observation)
        self._compact_window_observations()
        self._write_runtime_evidence(result)
        if completed_experiment:
            self._save_experiment(completed_experiment)
        # Immediate invalidation on observed input change, even with no browser open.
        with self.lock:
            recommendation_snapshot=copy.deepcopy(self.state)
        with self.orchestrator.lock:
            self.orchestrator.recommendation_lifecycle.tick(recommendation_snapshot)
        append_calibration(DATA_DIR,recommendation_snapshot,self.orchestrator.data['recommendations'][0] if self.orchestrator.data['recommendations'] else None)

    def _append_history(self, observed_at: str, source: str, window: dict[str, Any]) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        row = {"observed_at": observed_at, "mode": "mock" if self.mock else "live", "source": source, **window}
        with HISTORY_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    @staticmethod
    def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    @staticmethod
    def _compact_window_observations() -> None:
        """Keep at most 30 days/10,000 valid observations without unbounded growth."""
        if not WINDOW_STATE_FILE.is_file():
            return
        try:
            lines = WINDOW_STATE_FILE.read_text(encoding="utf-8").splitlines()
            if len(lines) <= 10000 and WINDOW_STATE_FILE.stat().st_size <= 10 * 1024 * 1024:
                return
            cutoff = time.time() - 30 * 86400
            kept = []
            for line in lines[-20000:]:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (as_epoch(item.get("observedAt")) or 0) >= cutoff:
                    kept.append(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
            WINDOW_STATE_FILE.write_text("\n".join(kept[-10000:]) + ("\n" if kept else ""), encoding="utf-8")
        except OSError:
            logging.warning("Could not compact window state observations")

    def start_probe(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("Invalid probe settings")
        interval = safe_int(payload.get("intervalSeconds", 60), minimum=30)
        duration = safe_int(payload.get("durationSeconds", 600), minimum=120)
        if interval not in {30, 60, 120}:
            raise ValueError("intervalSeconds must be 30, 60, or 120")
        if duration is None or duration > 3600:
            raise ValueError("durationSeconds must be between 120 and 3600")
        with self.lock:
            if self.probe_thread and self.probe_thread.is_alive():
                raise ValueError("A Reset Stability Probe is already running")
            self.state["probe"] = {
                "status": "RUNNING", "intervalSeconds": interval, "durationSeconds": duration,
                "samples": 0, "startedAt": utc_now(), "completedAt": None,
                "lastSampleAt": None, "readOnly": True, "modelTaskSent": False,
            }
            probe = ResetStabilityProbe(self, interval_seconds=interval, duration_seconds=duration)
            self.probe_thread = probe
            probe.start()
            return copy.deepcopy(self.state["probe"])

    def stop_probe(self) -> dict[str, Any]:
        with self.lock:
            probe_thread = self.probe_thread
            if probe_thread and probe_thread.is_alive():
                probe_thread.cancel.set()
            probe = self.state.get("probe", {})
            if probe.get("status") == "RUNNING":
                probe["status"] = "STOPPING"
            return copy.deepcopy(probe)

    def save_plan(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("Invalid plan")
        raw_times = payload.get("times")
        if not isinstance(raw_times, list):
            raise ValueError("times must be a list")
        times = sorted(set(str(value).strip() for value in raw_times))
        if not times or len(times) > 8 or any(not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value) for value in times):
            raise ValueError("Use 1-8 local times in HH:MM format")
        raw_slots = payload.get("campaign_slots", [])
        if not isinstance(raw_slots, list) or len(raw_slots) > 5:
            raise ValueError("campaign_slots must be a list of at most 5 local datetimes")
        campaign_slots: list[str] = []
        for value in raw_slots:
            text_value = str(value).strip()
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d", text_value):
                raise ValueError("campaign slots must use YYYY-MM-DDTHH:MM")
            try:
                datetime.fromisoformat(text_value)
            except ValueError as exc:
                raise ValueError("campaign slot is not a valid local datetime") from exc
            campaign_slots.append(text_value)
        campaign_slots = sorted(set(campaign_slots))
        maximum = safe_int(payload.get("max_daily_runs"), minimum=1)
        if maximum is None or maximum > 8:
            raise ValueError("max_daily_runs must be between 1 and 8")
        target = safe_int(payload.get("target_anchor_runs", 5), minimum=1)
        if target is None or target > 5:
            raise ValueError("target_anchor_runs must be between 1 and 5")
        enabled = payload.get("enabled") is True
        auto_anchor = payload.get("auto_anchor") is True
        plan = {
            "enabled": enabled,
            "auto_anchor": auto_anchor,
            "times": times,
            "campaign_slots": campaign_slots,
            "max_daily_runs": maximum,
            "target_anchor_runs": target,
            "remaining_anchor_runs": target if enabled and auto_anchor else target,
            "prompt": MINIMAL_ANCHOR_PROMPT,
            "timezone": "local",
            "updated_at": utc_now(),
        }
        if isinstance(payload.get("intensity_sessions"), list):
            plan["intensity_sessions"] = sanitize(payload["intensity_sessions"])
            plan["planning_strategy"] = "session_midpoint_minus_5_hours"
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        PLAN_FILE.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        with self.lock:
            self.state["usage_plan"] = plan
        return copy.deepcopy(plan)

    def plan_intensity_sessions(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict) or not isinstance(payload.get("sessions"), list):
            raise ValueError("sessions must be a list")
        raw_sessions = payload["sessions"]
        if not 1 <= len(raw_sessions) <= 5:
            raise ValueError("Use between 1 and 5 high-intensity sessions")
        now = datetime.now()
        with self.lock:
            short_windows = [
                safe_int(item.get("window_duration_minutes"), minimum=1)
                for item in self.state.get("windows", [])
                if item.get("slot") == "primary" or item.get("window_duration_minutes") == 300
            ]
        window_minutes = next((value for value in short_windows if value), 300)
        planned = []
        anchors = []
        for item in raw_sessions:
            if not isinstance(item, dict):
                raise ValueError("Each session must contain start and end")
            try:
                start = datetime.fromisoformat(str(item.get("start", "")))
                end = datetime.fromisoformat(str(item.get("end", "")))
            except ValueError as exc:
                raise ValueError("Session times must use local YYYY-MM-DDTHH:MM format") from exc
            duration = end - start
            if duration < timedelta(minutes=15) or duration > timedelta(hours=10):
                raise ValueError("Each session must last between 15 minutes and 10 hours")
            reset_target = start + duration / 2
            anchor = reset_target - timedelta(minutes=window_minutes)
            if anchor <= now + timedelta(minutes=1):
                raise ValueError("The calculated minimal-task time must be in the future")
            planned.append({
                "start": start.isoformat(timespec="minutes"),
                "end": end.isoformat(timespec="minutes"),
                "target_reset": reset_target.isoformat(timespec="minutes"),
                "anchor_time": anchor.isoformat(timespec="minutes"),
                "window_duration_minutes": window_minutes,
            })
            anchors.append(anchor.isoformat(timespec="minutes"))
        if len(set(anchors)) != len(anchors):
            raise ValueError("Two sessions calculate to the same minimal-task time")
        day_counts: dict[str, int] = {}
        for anchor in anchors:
            day = anchor[:10]
            day_counts[day] = day_counts.get(day, 0) + 1
        return self.save_plan({
            "enabled": True,
            "auto_anchor": True,
            "times": sorted({value[11:] for value in anchors}),
            "campaign_slots": sorted(anchors),
            "max_daily_runs": max(day_counts.values()),
            "target_anchor_runs": len(anchors),
            "intensity_sessions": planned,
        })

    def consume_anchor_run(self) -> dict[str, Any]:
        with self.lock:
            plan = copy.deepcopy(self.state["usage_plan"])
            remaining = max(0, int(plan.get("remaining_anchor_runs", plan.get("target_anchor_runs", 5))) - 1)
            plan["remaining_anchor_runs"] = remaining
            if remaining == 0:
                plan["enabled"] = False
                plan["auto_anchor"] = False
                plan["completed_at"] = utc_now()
            self.state["usage_plan"] = plan
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        PLAN_FILE.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        return copy.deepcopy(plan)

    def set_next_scheduled_run(self, value: str | None) -> None:
        with self.lock:
            self.state["next_scheduled_run"] = value

    def scheduled_run_count_today(self, day: str) -> int:
        with self.lock:
            return sum(1 for row in self.state["scheduled_runs"] if row.get("local_day") == day and row.get("action") == "model_call")

    def has_scheduled_key(self, key: str) -> bool:
        with self.lock:
            return any(row.get("schedule_key") == key for row in self.state["scheduled_runs"])

    def record_scheduled_run(self, row: dict[str, Any]) -> None:
        self._append_jsonl(SCHEDULED_RUNS_FILE, row)
        with self.lock:
            self.state["scheduled_runs"].insert(0, copy.deepcopy(row))
            del self.state["scheduled_runs"][30:]

    def set_scheduled_request_status(self, *, turn_id: str, status: str) -> dict[str, Any] | None:
        normalized = status.upper()
        if normalized not in {"SUCCESS", "FAILED", "TIMEOUT", "CANCELLED"}:
            return None
        transition = None
        completed_experiment = None
        with self.lock:
            if any(row.get("turn_id") == turn_id and row.get("status") in {"SUCCESS", "FAILED", "CANCELLED"}
                   for row in self.state.get("scheduled_runs", [])):
                return None
            for row in self.state.get("scheduled_runs", []):
                if row.get("turn_id") == turn_id and row.get("status") == "STARTED":
                    transition = {**row, "status": normalized, "completed_at": utc_now(), "recorded_at": utc_now()}
                    row.update(transition)
                    self._set_experiment_request_status_locked(row.get("experiment_id"), normalized)
                    completed_experiment = next((copy.deepcopy(item) for item in self.state.get("experiments", []) if item.get("id") == row.get("experiment_id")), None)
                    break
        if transition:
            self._append_jsonl(SCHEDULED_RUNS_FILE, transition)
        if completed_experiment:
            self._save_experiment(completed_experiment)
        return copy.deepcopy(transition)

    def _set_experiment_request_status_locked(self, experiment_id: str | None, status: str) -> None:
        if not experiment_id:
            return
        active = self.state.get("active_experiment")
        if isinstance(active, dict) and active.get("id") == experiment_id:
            if active.get("requestStatus") != "SUCCESS" or status == "SUCCESS":
                active["requestStatus"] = status
        for experiment in self.state.get("experiments", []):
            if experiment.get("id") == experiment_id:
                if experiment.get("requestStatus") != "SUCCESS" or status == "SUCCESS":
                    experiment["requestStatus"] = status

    def _expire_scheduled_runs_locked(self) -> list[dict[str, Any]]:
        now = time.time()
        expired = []
        for row in self.state.get("scheduled_runs", []):
            if row.get("status") != "STARTED":
                continue
            if any(other is not row and other.get("turn_id") == row.get("turn_id")
                   and other.get("status") in {"SUCCESS", "FAILED", "CANCELLED"}
                   for other in self.state.get("scheduled_runs", [])):
                continue
            deadline = safe_number(row.get("request_timeout_epoch"))
            if deadline is None:
                started = safe_number(row.get("request_started_epoch"))
                deadline = None if started is None else started + REQUEST_TIMEOUT_SECONDS
            if deadline is not None and now >= deadline:
                transition = {**row, "status": "TIMEOUT", "completed_at": utc_now(), "recorded_at": utc_now()}
                row.update(transition)
                self._set_experiment_request_status_locked(row.get("experiment_id"), "TIMEOUT")
                expired.append(copy.deepcopy(transition))
        return expired

    def _write_runtime_evidence(self, raw_result: Any) -> None:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        public = self.public()
        report = {
            "generated_at": utc_now(),
            "mode": public["app"]["mode"],
            "codex_cli_version": public["codex_cli_version"],
            "codex_cli_source": public["codex_cli_source"],
            "app_server": public["app_server"],
            "status": public["status"],
            "window_count": len(public["windows"]),
            "windows": public["windows"],
            "error": public["error"],
        }
        (EVIDENCE_DIR / "runtime_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (EVIDENCE_DIR / "rate_limit_sample.redacted.json").write_text(
            json.dumps(sanitize(raw_result), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def start_experiment(self) -> dict[str, Any]:
        with self.lock:
            if self.state.get("active_experiment"):
                raise ValueError("An experiment is already active")
            windows = copy.deepcopy(self.state.get("windows", []))
            candidates = [window for window in windows if window.get("window_duration_minutes") == 300]
            if not candidates:
                raise ValueError("No 5-hour window is currently available")
            target = next((window for window in candidates if window.get("limit_id") == "codex"), candidates[0])
            previous_reset = safe_number(target.get("resets_at"))
            eligible_after = None if previous_reset is None else previous_reset + POST_EXPIRY_GRACE_SECONDS
            now_epoch = time.time()
            identity = window_identity(target)
            pre_analysis = self.window_analyzer.analyze(identity)
            experiment = {
                "id": datetime.now().strftime("%Y%m%d_%H%M%S"),
                "mode": "mock" if self.mock else "live",
                "started_at": utc_now(),
                "prepared_at": utc_now(),
                "status": "PREPARED",
                "target_limit_id": target.get("limit_id"),
                "target_slot": target.get("slot"),
                "target_window_duration_minutes": target.get("window_duration_minutes"),
                "previousResetAt": previous_reset,
                "triggerScheduledAt": None,
                "windowDurationMins": target.get("window_duration_minutes"),
                "eligibleAfter": eligible_after,
                "preparedExperimentType": "IN_WINDOW_CONTROL" if previous_reset and now_epoch < previous_reset else "POST_EXPIRY_ANCHOR",
                "eligibilityNotice": (
                    "Current five-hour window is active. Continuing creates an in-window control, not an eligible post-expiry Anchor trial."
                    if previous_reset and now_epoch < previous_reset
                    else "Eligible after the previous reset plus the 30-second grace period."
                ),
                "requestStatus": "UNKNOWN",
                "preWindowState": pre_analysis.get("state"),
                "preStateSamples": pre_analysis.get("samples"),
                "preObservationSpanSeconds": pre_analysis.get("observationSpanSeconds"),
                "resetSlopeBefore": pre_analysis.get("resetSlope"),
                "horizonMeanBefore": pre_analysis.get("horizonMeanSeconds"),
                "before": {"observed_at": self.state.get("last_snapshot"), "windows": windows},
            }
            self.state["active_experiment"] = experiment
            return copy.deepcopy(experiment)

    def mark_experiment_triggered(self) -> dict[str, Any]:
        with self.lock:
            experiment = self.state.get("active_experiment")
            if not experiment:
                raise ValueError("No active experiment")
            if experiment.get("status") != "PREPARED":
                raise ValueError("Experiment is not waiting for a manual trigger")
            trigger_epoch = time.time()
            experiment["status"] = "TRIGGERED"
            experiment["trigger_at"] = datetime.fromtimestamp(trigger_epoch, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            experiment["trigger_epoch"] = trigger_epoch
            experiment["triggerActualAt"] = trigger_epoch
            duration = safe_int(experiment.get("windowDurationMins"), minimum=1)
            experiment["expectedResetAt"] = None if duration is None else trigger_epoch + duration * 60
            previous_reset = safe_number(experiment.get("previousResetAt"))
            pre_state = experiment.get("preWindowState")
            if pre_state == ROLLING:
                experiment["experimentType"] = "ANCHOR_TRANSITION_CANDIDATE"
            elif previous_reset is None:
                experiment["experimentType"] = "INCONCLUSIVE"
            elif pre_state == FIXED and trigger_epoch < previous_reset:
                experiment["experimentType"] = "IN_WINDOW_CONTROL"
            elif trigger_epoch >= previous_reset + POST_EXPIRY_GRACE_SECONDS:
                experiment["experimentType"] = "POST_EXPIRY_ANCHOR"
            else:
                experiment["experimentType"] = "INCONCLUSIVE"
            experiment["eligibleForAnchorStatistics"] = experiment["experimentType"] == "POST_EXPIRY_ANCHOR"
            experiment["timeout_at"] = datetime.fromtimestamp(trigger_epoch + EXPERIMENT_TIMEOUT_SECONDS, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            return copy.deepcopy(experiment)

    def cancel_experiment(self) -> dict[str, Any]:
        with self.lock:
            experiment = self.state.get("active_experiment")
            if not experiment:
                raise ValueError("No active experiment")
            experiment["status"] = "CANCELLED"
            experiment["classification"] = "CANCELLED"
            experiment["experimentType"] = experiment.get("experimentType", "INCONCLUSIVE")
            experiment["eligibleForAnchorStatistics"] = False
            experiment["requestStatus"] = experiment.get("requestStatus", "CANCELLED")
            experiment["completed_at"] = utc_now()
            completed = copy.deepcopy(experiment)
            self.state["experiments"].insert(0, completed)
            del self.state["experiments"][20:]
            self.state["active_experiment"] = None
        self._save_experiment(completed)
        return completed

    def _expire_experiment_locked(self) -> dict[str, Any] | None:
        experiment = self.state.get("active_experiment")
        if not experiment or experiment.get("status") != "TRIGGERED":
            return None
        trigger_epoch = safe_number(experiment.get("trigger_epoch"))
        if trigger_epoch is None or time.time() - trigger_epoch < EXPERIMENT_TIMEOUT_SECONDS:
            return None
        experiment["status"] = "TIMED OUT"
        experiment["classification"] = "TIMEOUT"
        experiment["experimentType"] = experiment.get("experimentType", "INCONCLUSIVE")
        experiment["eligibleForAnchorStatistics"] = False
        experiment["resetJitterToleranceSeconds"] = RESET_JITTER_TOLERANCE_SECONDS
        experiment["anchorToleranceSeconds"] = ANCHOR_CONFIRM_TOLERANCE_SECONDS
        experiment["notes"] = "No conclusive follow-up snapshot arrived before timeout."
        experiment["completed_at"] = utc_now()
        completed = copy.deepcopy(experiment)
        self.state["experiments"].insert(0, completed)
        del self.state["experiments"][20:]
        self.state["active_experiment"] = None
        return completed

    def _detect_experiment_locked(self, detected_at: str) -> dict[str, Any] | None:
        experiment = self.state.get("active_experiment")
        if not experiment or experiment.get("status") != "TRIGGERED":
            return None
        before_windows = experiment.get("before", {}).get("windows", [])
        before_by_id = {window_identity(w): w for w in before_windows}
        target_identity = (
            experiment.get("target_limit_id"),
            experiment.get("target_slot"),
            experiment.get("target_window_duration_minutes"),
        )
        candidate = None
        for after in self.state.get("windows", []):
            if window_identity(after) != target_identity:
                continue
            before = before_by_id.get(window_identity(after))
            old_reset = before.get("resets_at") if before else None
            new_reset = after.get("resets_at")
            if before:
                candidate = (before, after)
                break
        if not candidate:
            return None
        before, after = candidate
        detected_epoch = time.time()
        trigger_epoch = safe_number(experiment.get("trigger_epoch"))
        if trigger_epoch is None:
            return None
        duration = after.get("window_duration_minutes")
        classification = classify_anchor_observation(
            previous_reset_at=before.get("resets_at"),
            trigger_actual_at=trigger_epoch,
            window_duration_minutes=duration,
            observed_reset_at=after.get("resets_at"),
            request_status=experiment.get("requestStatus"),
        )
        observed_reset = classification.get("observedResetAt")
        observed_seconds = None if observed_reset is None else observed_reset - trigger_epoch
        experiment.update({
            **classification,
            "status": classification["classification"],
            "detected_at": detected_at,
            "completed_at": detected_at,
            "after": {"observed_at": self.state.get("last_snapshot"), "windows": copy.deepcopy(self.state.get("windows", []))},
            "matched_window": {"before": before, "after": after},
            "old_resets_at": classification.get("previousResetAt"),
            "new_resets_at": classification.get("observedResetAt"),
            "window_duration_minutes": duration,
            "observed_minutes_from_trigger": None if observed_seconds is None else round(observed_seconds / 60, 3),
            "expected_window_seconds": duration * 60 if duration else None,
            "difference_from_expected_seconds": classification.get("anchorErrorSeconds"),
            "detection_latency_seconds": round(detected_epoch - trigger_epoch, 3),
            "note": classification.get("notes"),
            "classificationSchemaVersion": 1,
        })
        attributed = attribute_anchor_experiment(experiment, self.window_analyzer.observations())
        experiment.update(attributed)
        # A rolling pre-window needs a stable post-window series. One post snapshot is
        # deliberately only a candidate and remains active until stability or timeout.
        if experiment.get("preWindowState") == ROLLING:
            post_state = experiment.get("postWindowState")
            if experiment.get("requestStatus") != "SUCCESS" or post_state not in {ROLLING, FIXED}:
                experiment["status"] = experiment.get("classification", "ANCHOR_CANDIDATE")
                experiment.pop("completed_at", None)
                return None
        elif experiment.get("preWindowState") not in {FIXED, ROLLING}:
            experiment["status"] = experiment.get("classification", "INCONCLUSIVE")
            experiment.pop("completed_at", None)
            return None
        experiment["completed_at"] = detected_at
        self.state["experiments"].insert(0, copy.deepcopy(experiment))
        del self.state["experiments"][20:]
        self.state["active_experiment"] = None
        return copy.deepcopy(experiment)

    def _save_experiment(self, experiment: dict[str, Any]) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = DATA_DIR / f"anchor_experiment_{experiment['id']}.json"
        path.write_text(json.dumps(experiment, ensure_ascii=False, indent=2), encoding="utf-8")
        self._write_report_artifacts()

    def experiment_file(self, experiment_id: str) -> Path | None:
        if not re.fullmatch(r"\d{8}_\d{6}", experiment_id):
            return None
        path = DATA_DIR / f"anchor_experiment_{experiment_id}.json"
        return path if path.is_file() else None

    def experiments_csv(self) -> str:
        with self.lock:
            experiments = copy.deepcopy(self.state.get("experiments", []))
        output = io.StringIO(newline="")
        fields = [
            "id", "mode", "status", "experimentType", "classification", "requestStatus",
            "eligibleForAnchorStatistics", "previousResetAt", "triggerScheduledAt", "triggerActualAt",
            "windowDurationMins", "expectedResetAt", "observedResetAt", "resetShiftSeconds",
            "anchorErrorSeconds", "resetJitterToleranceSeconds", "anchorToleranceSeconds", "notes",
            "legacyClassification", "prepared_at", "trigger_at", "detected_at", "completed_at",
            "target_limit_id", "target_slot", "target_window_duration_minutes",
            "window_duration_minutes", "old_resets_at", "new_resets_at",
            "observed_minutes_from_trigger", "expected_window_seconds",
            "difference_from_expected_seconds", "detection_latency_seconds",
            "preWindowState", "postWindowState", "preStateSamples", "postStateSamples",
            "preObservationSpanSeconds", "postObservationSpanSeconds", "resetSlopeBefore",
            "resetSlopeAfter", "horizonMeanBefore", "horizonMeanAfter", "transitionType",
            "transitionDetectedAt", "anchorAttribution", "anchorAttributionEvidence",
            "classificationVersion", "eligibleForConfirmedAnchorStatistics",
        ]
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(experiments)
        return output.getvalue()

    def _real_experiments(self) -> list[dict[str, Any]]:
        with self.lock:
            return [copy.deepcopy(item) for item in reversed(self.state.get("experiments", [])) if item.get("mode") == "live"]

    def experiment_chart_svg(self) -> str:
        experiments = self._real_experiments()
        measured = [item for item in experiments if safe_number(item.get("anchorErrorSeconds", item.get("difference_from_expected_seconds"))) is not None]
        eligible = [item for item in experiments if item.get("eligibleForAnchorStatistics") is True]
        controls = [item for item in experiments if item.get("experimentType") == "IN_WINDOW_CONTROL"]
        width, height = 960, 480
        left, right, top, bottom = 90, 35, 75, 80
        plot_w, plot_h = width - left - right, height - top - bottom
        max_abs = max([300.0] + [abs(float(item.get("anchorErrorSeconds", item.get("difference_from_expected_seconds")))) for item in measured])
        span = min(max_abs * 1.2, 10800.0)
        def y(value: float) -> float:
            return top + (span - value) / (2 * span) * plot_h
        def x(index: int) -> float:
            return left + (index + 0.5) * plot_w / max(1, len(experiments))
        band_top, band_bottom = y(120), y(-120)
        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="chart-title chart-desc">',
            '<title id="chart-title">QuotaLab Anchor and in-window control observations</title>',
            '<desc id="chart-desc">Anchor error in seconds. The green band marks plus or minus 120 seconds. Square markers are in-window controls and are excluded from eligible Anchor statistics.</desc>',
            '<rect width="100%" height="100%" rx="22" fill="#08131d"/>',
            '<style>text{font-family:Segoe UI,Arial,sans-serif;fill:#dcecf0}.muted{fill:#89a6b3}.grid{stroke:#294354;stroke-width:1}.axis{stroke:#89a6b3;stroke-width:1.5}</style>',
            '<text x="42" y="38" font-size="22" font-weight="700">QuotaLab · Anchor and control observations</text>',
            f'<text x="42" y="60" class="muted" font-size="13">Eligible Anchor trials {len(eligible)} · In-window controls {len(controls)} · green band = ±120 s confirmation tolerance</text>',
            f'<rect x="{left}" y="{band_top:.1f}" width="{plot_w}" height="{band_bottom-band_top:.1f}" fill="#17463d" opacity=".55"/>',
        ]
        for value in (-span, -120, 0, 120, span):
            yy = y(value)
            parts.append(f'<line x1="{left}" y1="{yy:.1f}" x2="{width-right}" y2="{yy:.1f}" class="grid"/>')
            parts.append(f'<text x="{left-12}" y="{yy+5:.1f}" text-anchor="end" class="muted" font-size="12">{value:.0f}s</text>')
        parts.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{height-bottom}" class="axis"/>')
        parts.append(f'<line x1="{left}" y1="{y(0):.1f}" x2="{width-right}" y2="{y(0):.1f}" class="axis"/>')
        parts.append(f'<text x="20" y="{top+plot_h/2:.1f}" transform="rotate(-90 20 {top+plot_h/2:.1f})" text-anchor="middle" class="muted" font-size="12">Difference from 5-hour expectation (seconds)</text>')
        parts.append(f'<text x="{left+plot_w/2:.1f}" y="{height-8}" text-anchor="middle" class="muted" font-size="12">Observation sequence · ■ control · ● eligible Anchor</text>')
        for index, item in enumerate(experiments):
            xx = x(index)
            diff = safe_number(item.get("anchorErrorSeconds", item.get("difference_from_expected_seconds")))
            label = f"A{index + 1}"
            if diff is None:
                yy, color = height - bottom + 28, "#718c98"
                parts.append(f'<path d="M {xx-6:.1f} {yy-6:.1f} L {xx+6:.1f} {yy+6:.1f} M {xx+6:.1f} {yy-6:.1f} L {xx-6:.1f} {yy+6:.1f}" stroke="{color}" stroke-width="3"/>')
            else:
                plotted = max(-span, min(span, diff))
                yy = y(plotted)
                is_control = item.get("experimentType") == "IN_WINDOW_CONTROL"
                color = "#65a8ff" if is_control else ("#4de1c1" if abs(diff) <= ANCHOR_CONFIRM_TOLERANCE_SECONDS else "#ffc767")
                if is_control:
                    parts.append(f'<rect x="{xx-8:.1f}" y="{yy-8:.1f}" width="16" height="16" fill="{color}" stroke="#e8f2f4" stroke-width="2"/>')
                else:
                    parts.append(f'<circle cx="{xx:.1f}" cy="{yy:.1f}" r="8" fill="{color}" stroke="#e8f2f4" stroke-width="2"/>')
                parts.append(f'<text x="{xx:.1f}" y="{yy-14:.1f}" text-anchor="middle" font-size="12">{diff:.0f}s</text>')
            kind = "CONTROL" if item.get("experimentType") == "IN_WINDOW_CONTROL" else "ANCHOR"
            parts.append(f'<text x="{xx:.1f}" y="{height-bottom+52}" text-anchor="middle" font-size="11">{label} {kind}</text>')
        if not experiments:
            parts.append(f'<text x="{width/2}" y="{height/2}" text-anchor="middle" class="muted" font-size="18">Waiting for Anchor or control observations</text>')
        parts.append('</svg>')
        return "".join(parts)

    def window_state_csv(self) -> str:
        with self.lock:
            observations = self.window_analyzer.observations()
        fields = ["observedAt", "limitId", "limitName", "slot", "windowDurationMins", "usedPercent", "remainingPercent", "resetAt", "horizonSeconds", "source"]
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sorted(observations, key=lambda item: item.get("observedAt", 0)))
        return output.getvalue()

    def window_state_chart_svg(self) -> str:
        """Two normalized mini-plots per bucket: reset movement and remaining horizon."""
        with self.lock:
            series_by_bucket = [(bucket_id(series[-1]), copy.deepcopy(series[-12:])) for series in self.window_analyzer.series.values() if series]
        width, panel_h = 960, 180
        height = 70 + max(1, len(series_by_bucket)) * panel_h
        parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
                 '<rect width="100%" height="100%" fill="#08131d"/><style>text{font-family:Segoe UI,Arial;fill:#dcecf0}.m{fill:#89a6b3}.g{stroke:#294354}.r{fill:none;stroke:#65a8ff;stroke-width:3}.h{fill:none;stroke:#4de1c1;stroke-width:3}</style>',
                 '<text x="34" y="34" font-size="22" font-weight="700">Reset Stability Timeline</text>',
                 '<text x="34" y="55" class="m" font-size="12">Blue: resetAt movement (bucket-local scale) · Green: remaining horizon</text>']
        if not series_by_bucket:
            parts.append('<text x="480" y="130" text-anchor="middle" class="m">No observations yet</text>')
        for index, (name, points) in enumerate(series_by_bucket):
            top = 75 + index * panel_h
            analysis = analyze_window_state(points)
            left, plot_w, plot_h = 180, 730, 115
            xs = [point["observedAt"] for point in points]
            resets = [point["resetAt"] for point in points]
            horizons = [point["horizonSeconds"] for point in points]
            x0, x1 = min(xs), max(xs)
            r0, r1 = min(resets), max(resets)
            h0, h1 = min(horizons), max(horizons)
            def px(value): return left + (value - x0) / max(1, x1 - x0) * plot_w
            def py(value, low, high): return top + 25 + plot_h - (value - low) / max(1, high - low) * plot_h
            reset_path = " ".join(("M" if i == 0 else "L") + f" {px(p['observedAt']):.1f} {py(p['resetAt'], r0, r1):.1f}" for i, p in enumerate(points))
            horizon_path = " ".join(("M" if i == 0 else "L") + f" {px(p['observedAt']):.1f} {py(p['horizonSeconds'], h0, h1):.1f}" for i, p in enumerate(points))
            parts.extend([f'<text x="34" y="{top+28}" font-size="13" font-weight="700">{html_lib.escape(name)}</text>',
                          f'<text x="34" y="{top+49}" class="m" font-size="12">{analysis["state"]} · n={analysis["samples"]} · slope={analysis["resetSlope"]}</text>',
                          f'<rect x="{left}" y="{top+25}" width="{plot_w}" height="{plot_h}" fill="#0c1925" stroke="#294354"/>',
                          f'<path d="{reset_path}" class="r"/><path d="{horizon_path}" class="h"/>'])
        parts.append('</svg>')
        return "".join(parts)

    def experiment_report_html(self) -> str:
        experiments = self._real_experiments()
        controls = [item for item in experiments if item.get("experimentType") == "IN_WINDOW_CONTROL"]
        eligible = [item for item in experiments if item.get("eligibleForAnchorStatistics") is True]
        confirmed = sum(item.get("classification") in {"ANCHOR_CONFIRMED", "ANCHOR_CONFIRMED_BY_STATE_TRANSITION"} for item in experiments)
        no_reanchor = sum(item.get("classification") in {"NO_REANCHOR", "IN_WINDOW_CONTROL_NO_REANCHOR"} for item in experiments)
        inconclusive = sum(item.get("classification") in {"INCONCLUSIVE", "TIMEOUT", "REQUEST_FAILED"} for item in experiments)
        status = "证据不足：至少需要 3 次合格的 post-expiry Anchor 实验" if len(eligible) < 3 else "已有初步可审查证据；仍不等于官方机制证明"
        rows = []
        for index, item in enumerate(experiments):
            diff = safe_number(item.get("anchorErrorSeconds", item.get("difference_from_expected_seconds")))
            rows.append("<tr>" + "".join([
                f"<td>A{index+1}</td>", f"<td>{html_lib.escape(str(item.get('experimentType', 'INCONCLUSIVE')))}</td>",
                f"<td>{html_lib.escape(str(item.get('classification', item.get('status', '—'))))}</td>",
                f"<td>{html_lib.escape(str(item.get('requestStatus', 'UNKNOWN')))}</td>",
                f"<td>{'YES' if item.get('eligibleForAnchorStatistics') is True else 'NO'}</td>",
                f"<td>{'—' if diff is None else f'{diff:.1f} s'}</td>",
                f"<td>{html_lib.escape(str(item.get('preWindowState', '—')))} → {html_lib.escape(str(item.get('postWindowState', '—')))}</td>",
                f"<td>{html_lib.escape(str(item.get('anchorAttribution', '—')))}</td>",
            ]) + "</tr>")
        svg = self.experiment_chart_svg()
        stability_svg = self.window_state_chart_svg()
        return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>QuotaLab Anchor 实验报告</title><style>
@page{{size:A4;margin:12mm}}*{{box-sizing:border-box}}body{{font:13px/1.45 "Segoe UI",Arial,sans-serif;color:#15232c;margin:0}}h1{{margin:0 0 4px}}.meta{{color:#607784}}.metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:14px 0}}.metric{{border:1px solid #cdd9df;border-radius:8px;padding:9px}}.metric b{{display:block;font-size:20px}}svg{{width:100%;height:auto}}table{{width:100%;border-collapse:collapse;margin-top:12px}}th,td{{border-bottom:1px solid #dbe4e8;padding:6px;text-align:left;font-size:11px}}.note{{background:#eef6f5;border-left:4px solid #238a78;padding:9px;margin-top:12px}}footer{{margin-top:10px;color:#607784;font-size:10px}}</style></head><body>
<h1>QuotaLab Anchor 实验报告</h1><div class="meta">自动生成时间：{utc_now()} · 本地真实 Codex 数据</div>
<div class="metrics"><div class="metric"><span>真实观测</span><b>{len(experiments)}</b></div><div class="metric"><span>窗口内对照</span><b>{len(controls)}</b></div><div class="metric"><span>合格 Anchor</span><b>{len(eligible)}</b></div><div class="metric"><span>已确认锚定</span><b>{confirmed}</b></div><div class="metric"><span>未重新锚定</span><b>{no_reanchor}</b></div><div class="metric"><span>证据不足</span><b>{inconclusive}</b></div><div class="metric"><span>当前判断</span><b style="font-size:12px">{status}</b></div></div>
{svg}{stability_svg}<table><thead><tr><th>观测</th><th>实验类型</th><th>实验判定</th><th>请求状态</th><th>计入 Anchor 统计</th><th>Anchor error</th><th>窗口状态</th><th>归因</th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="8">等待真实观测</td></tr>'}</tbody></table>
<div class="note"><b>方法：</b>每个 quota bucket 独立使用至少 3 个样本、至少 120 秒跨度判断 Rolling / Fixed。普通 rolling update 不计作 reset event。只有请求前 ROLLING、请求 SUCCESS、请求后 FIXED，且固定 reset 落在 trigger + duration 的 ±120 秒内，才确认状态转换锚定。<br><b>限制：</b>这些是 QuotaLab 当前实测中与 Rolling / Fixed 模型一致的行为，不是 OpenAI 对额度机制的官方保证；FIXED 本身不等于请求归因。</div>
<footer>证据链：想法 → 本地原型 → Codex app-server 快照 → Anchor 实验 → 结果图与报告。原始 JSON 保存在本机 data 目录。</footer></body></html>'''

    def _write_report_artifacts(self) -> None:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        REPORT_SVG_FILE.write_text(self.experiment_chart_svg(), encoding="utf-8")
        REPORT_HTML_FILE.write_text(self.experiment_report_html(), encoding="utf-8")


class ResetStabilityProbe(threading.Thread):
    """Read-only sampling coordinator; the LiveMonitor remains the sole RPC owner."""

    def __init__(self, state: QuotaLabState, *, interval_seconds: int, duration_seconds: int):
        super().__init__(name="reset-stability-probe", daemon=True)
        self.state = state
        self.interval_seconds = interval_seconds
        self.duration_seconds = duration_seconds
        self.cancel = threading.Event()

    def run(self) -> None:
        deadline = time.monotonic() + self.duration_seconds
        self.state.refresh_requested.set()
        while not self.cancel.is_set() and not self.state.stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if self.cancel.wait(min(self.interval_seconds, remaining)):
                break
            self.state.refresh_requested.set()
        with self.state.lock:
            probe = self.state.state.get("probe", {})
            probe["status"] = "CANCELLED" if self.cancel.is_set() else "COMPLETED"
            probe["completedAt"] = utc_now()
            probe["readOnly"] = True
            probe["modelTaskSent"] = False


class LiveMonitor(threading.Thread):
    def __init__(
        self,
        state: QuotaLabState,
        executable: Path | None = None,
        resolver: CodexExecutableResolver | None = None,
    ):
        super().__init__(name="live-monitor", daemon=True)
        self.state = state
        self.executable = executable
        self.resolver = resolver
        self.rediscover_requested = threading.Event()
        self.provider: ProviderAdapter = CodexProviderAdapter(resolver, CodexAppServer)
        if resolver and resolver.current:
            self.provider.executable = resolver.current
        self.state.set_codex_rediscover(self.request_rediscovery)
        self.state.set_codex_configure(self.configure_path)

    @property
    def client(self):
        return self.provider.client

    @client.setter
    def client(self, value) -> None:
        self.provider.client = value

    def request_rediscovery(self) -> None:
        self.rediscover_requested.set()
        if self.client:
            self.client.stop()

    def configure_path(self, value: str) -> dict[str, Any]:
        if not self.resolver:
            raise ValueError("Codex resolver is unavailable")
        previous = self.resolver.explicit_path
        self.resolver.explicit_path = value
        result = self.resolver.resolve(force=True)
        if not result or result.source != "configured":
            self.resolver.explicit_path = previous
            self.resolver.resolve(force=True)
            raise ValueError("INVALID_CODEX_PATH")
        self.provider.executable = result
        self.executable = result.path
        self.state.set_codex_discovery(result, self.resolver.attempts)
        self.request_rediscovery()
        return {"status": "validated", "source": result.source, "version": result.version}

    def _rediscover_if_needed(self) -> None:
        needs_discovery = self.rediscover_requested.is_set() or self.executable is None or not self.executable.is_file()
        if not needs_discovery or not self.resolver:
            return
        self.rediscover_requested.clear()
        result = self.provider.detect(force=True)
        self.executable = result.path if result else None
        self.state.set_codex_discovery(result, self.resolver.attempts)
        logging.info("Codex rediscovery result: found=%s source=%s attempts=%s", bool(result), result.source if result else None, self.resolver.attempts)

    def on_notification(self, method: str, params: Any) -> None:
        logging.info("Notification: %s", method)
        if method == "account/rateLimits/updated":
            self.state.refresh_requested.set()
        if method in {"turn/completed", "turn/failed", "turn/status/changed"} and isinstance(params, dict):
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else params
            turn_id = turn.get("id") or turn.get("turnId")
            raw_status = str(turn.get("status") or ("failed" if method == "turn/failed" else "completed")).lower()
            request_status = "SUCCESS" if raw_status in {"completed", "success", "succeeded"} else "FAILED" if raw_status in {"failed", "error"} else None
            if isinstance(turn_id, str) and request_status:
                self.state.set_scheduled_request_status(turn_id=turn_id, status=request_status)

    def run(self) -> None:
        while not self.state.stop_event.is_set():
            try:
                self._rediscover_if_needed()
                if self.executable is None:
                    raise AppServerError("CODEX_NOT_FOUND")
                logging.info("Starting codex app-server from validated %s candidate", self.state.state.get("codex_cli_source"))
                self.provider.connect(self.on_notification, debug=self.state.debug)
                self.state.set_anchor_trigger(self.trigger_anchor)
                self.state.set_connection("CONNECTED", status="CONNECTED")
                account = self.provider.get_account()
                self.state.set_account(account)
                if isinstance(account, dict) and account.get("requiresOpenaiAuth") and not account.get("account"):
                    raise AppServerError("CODEX_NOT_AUTHENTICATED")
                self.refresh("initial")
                next_poll = time.monotonic() + 60
                while not self.state.stop_event.is_set() and not self.client.closed.is_set():
                    timeout = max(0.1, min(1.0, next_poll - time.monotonic()))
                    if self.state.refresh_requested.wait(timeout):
                        self.state.refresh_requested.clear()
                        self.refresh("notification")
                        next_poll = time.monotonic() + 60
                    elif time.monotonic() >= next_poll:
                        self.refresh("poll")
                        next_poll = time.monotonic() + 60
                if not self.state.stop_event.is_set():
                    raise AppServerError("APP_SERVER_DISCONNECTED")
            except FileNotFoundError:
                self.state.set_connection("DISCONNECTED", status="UNAVAILABLE", error="CODEX_NOT_FOUND")
            except (AppServerError, RuntimeError) as exc:
                error = str(exc)
                status = "UNAVAILABLE" if "NOT_AUTHENTICATED" in error or "NOT_FOUND" in error else "STALE"
                self.state.set_connection("DISCONNECTED", status=status, error=error)
                logging.error("Monitor error: %s", error)
            except Exception as exc:
                self.state.set_connection("DISCONNECTED", status="STALE", error=f"PROTOCOL_CHANGED:{exc}")
                logging.error("Unexpected monitor error: %s\n%s", exc, traceback.format_exc())
            finally:
                self.state.set_anchor_trigger(None)
                self.provider.disconnect()
            if not self.state.stop_event.wait(5):
                self.state.set_connection("RECONNECTING", status="STALE")

    def refresh(self, source: str) -> None:
        if not self.client:
            raise AppServerError("APP_SERVER_DISCONNECTED")
        logging.info("RPC account/rateLimits/read (%s)", source)
        result = self.provider.get_rate_limits()
        self.state.update_limits(result, source)

    def trigger_anchor(self) -> dict[str, Any]:
        raise AppServerError("CLOUD_VALIDATION_READ_ONLY")


class MockMonitor(threading.Thread):
    def __init__(self, state: QuotaLabState):
        super().__init__(name="mock-monitor", daemon=True)
        self.state = state
        self.mock_reset_shifted = False

    def run(self) -> None:
        now = int(time.time())
        self.state.set_account({"account": {"type": "chatgpt", "email": "mock@example.com", "planType": "plus"}, "requiresOpenaiAuth": True})
        self.state.set_connection("MOCK", status="MOCK DATA")
        self._publish(now, "initial")
        while not self.state.stop_event.wait(5):
            public = self.state.public()
            active = public.get("active_experiment")
            if not active:
                self.mock_reset_shifted = False
            elif active.get("status") == "TRIGGERED" and not self.mock_reset_shifted:
                self.mock_reset_shifted = True
                self._publish(int(time.time()) + 17, "notification")

    def _publish(self, anchor: int, source: str) -> None:
        result = {
            "rateLimits": {
                "limitId": "codex",
                "limitName": "Codex",
                "planType": "plus",
                "primary": {"usedPercent": 36, "windowDurationMins": 300, "resetsAt": anchor + 300 * 60},
                "secondary": {"usedPercent": 48, "windowDurationMins": 10080, "resetsAt": anchor + 4 * 86400},
            },
            "rateLimitResetCredits": {"availableCount": 0, "credits": []},
        }
        self.state.update_limits(result, source)


class UsagePlanner(threading.Thread):
    """Local scheduler. Model calls are opt-in, capped, and use a fixed minimal prompt."""

    def __init__(self, state: QuotaLabState, execution_owner: bool = True):
        super().__init__(name="usage-planner", daemon=True)
        self.state = state
        self.execution_owner = execution_owner

    @staticmethod
    def _next_run(times: list[str], now: datetime) -> datetime | None:
        candidates = []
        for value in times:
            hour, minute = (int(part) for part in value.split(":"))
            candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate <= now:
                candidate += timedelta(days=1)
            candidates.append(candidate)
        return min(candidates) if candidates else None

    def run(self) -> None:
        while not self.state.stop_event.is_set():
            try:
                self._tick()
            except Exception:
                logging.error("Usage planner error:\n%s", traceback.format_exc())
            self.state.stop_event.wait(5)

    def _tick(self) -> None:
        if not self.execution_owner:
            return
        public = self.state.public()
        plan = public.get("usage_plan") or {}
        times = plan.get("times") or []
        campaign_slots = plan.get("campaign_slots") or []
        now = datetime.now().astimezone()
        future_slots = []
        for value in campaign_slots:
            scheduled = datetime.fromisoformat(value).replace(tzinfo=now.tzinfo)
            key = f"campaign|{value}"
            if scheduled > now and not self.state.has_scheduled_key(key):
                future_slots.append(scheduled)
        next_run = min(future_slots) if future_slots else (self._next_run(times, now) if times and not campaign_slots else None)
        self.state.set_next_scheduled_run(next_run.isoformat(timespec="seconds") if plan.get("enabled") and next_run else None)
        if not plan.get("enabled"):
            return
        if campaign_slots:
            for value in campaign_slots:
                scheduled = datetime.fromisoformat(value).replace(tzinfo=now.tzinfo)
                lateness = (now - scheduled).total_seconds()
                key = f"campaign|{value}"
                if 0 <= lateness <= 600 and not self.state.has_scheduled_key(key):
                    self._execute_slot(key, value, now, plan, public)
                elif 600 < lateness <= 700 and not self.state.has_scheduled_key(key):
                    self.state.record_scheduled_run({
                        "schedule_key": key, "scheduled_time": value,
                        "local_day": scheduled.date().isoformat(), "recorded_at": utc_now(),
                        "mode": "mock" if self.state.mock else "live",
                        "action": "skipped", "status": "MISSED_OFFLINE_WINDOW",
                    })
            return
        for value in times:
            hour, minute = (int(part) for part in value.split(":"))
            scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            lateness = (now - scheduled).total_seconds()
            if 0 <= lateness <= 90:
                key = f"{now.date().isoformat()}|{value}"
                if not self.state.has_scheduled_key(key):
                    self._execute_slot(key, value, now, plan, public)

    def _execute_slot(self, key: str, value: str, now: datetime, plan: dict[str, Any], public: dict[str, Any]) -> None:
        if not self.execution_owner:
            return
        base = {
            "schedule_key": key,
            "scheduled_time": value,
            "local_day": now.date().isoformat(),
            "recorded_at": utc_now(),
            "mode": "mock" if self.state.mock else "live",
        }
        if not plan.get("auto_anchor"):
            self.state.record_scheduled_run({**base, "action": "reminder", "status": "DUE"})
            return
        if not self.state.mock and not (self.state.execution_runtime and self.state.execution_runtime.settings.get("liveExecutionEnabled")):
            self.state.record_scheduled_run({**base, "action": "skipped", "status": "REAL_EXECUTION_DISABLED"})
            return
        if self.state.scheduled_run_count_today(base["local_day"]) >= plan.get("max_daily_runs", 1):
            self.state.record_scheduled_run({**base, "action": "skipped", "status": "DAILY_CAP"})
            return
        reason = None
        if public.get("active_experiment"):
            reason = "EXPERIMENT_ACTIVE"
        elif not any(window.get("window_duration_minutes") == 300 for window in public.get("windows", [])):
            reason = "NO_5_HOUR_WINDOW"
        elif public.get("ordinary_usage_allowed") is False:
            reason = "USAGE_NOT_ALLOWED"
        elif not self.state.mock and public.get("status") != "LIVE":
            reason = "NOT_CONNECTED"
        if reason:
            if key.startswith("campaign|"):
                return
            self.state.record_scheduled_run({**base, "action": "skipped", "status": reason})
            return
        try:
            experiment = self.state.start_experiment()
            if self.state.mock:
                self.state.mark_experiment_triggered()
                self.state.record_scheduled_run({**base, "action": "model_call", "status": "MOCK_TRIGGERED", "experiment_id": experiment["id"]})
                self.state.consume_anchor_run()
                return
            trigger_result = self.state.trigger_minimal_anchor()
            triggered = self.state.mark_experiment_triggered()
            started_epoch = time.time()
            self.state.record_scheduled_run({
                **base, "action": "model_call", "status": "STARTED",
                "thread_id": trigger_result.get("thread_id"), "turn_id": trigger_result.get("turn_id"),
                "experiment_id": experiment["id"],
                "request_started_epoch": started_epoch,
                "request_timeout_epoch": started_epoch + REQUEST_TIMEOUT_SECONDS,
            })
            with self.state.lock:
                self.state._set_experiment_request_status_locked(triggered["id"], "STARTED")
            self.state.consume_anchor_run()
        except (AppServerError, OSError, ValueError) as exc:
            try:
                if self.state.public().get("active_experiment"):
                    self.state.cancel_experiment()
            except ValueError:
                pass
            self.state.record_scheduled_run({**base, "action": "failed", "status": sanitize_text(str(exc))[:160]})


HTML = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>QuotaLab · Codex 额度窗口实验</title>
<meta name="description" content="本地、只读的 Codex 额度窗口观测工具">
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='16' fill='%230b1220'/%3E%3Cpath d='M16 42V22h8v20zm12 0V14h8v28zm12 0V27h8v15z' fill='%234de1c1'/%3E%3C/svg%3E">
<style>
:root{color-scheme:dark;--bg:#071019;--panel:#0c1925;--panel2:#0f2130;--line:#20384a;--text:#e8f2f4;--muted:#89a6b3;--mint:#4de1c1;--blue:#65a8ff;--amber:#ffc767;--red:#ff6b78}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 88% 0,#12324a 0,transparent 34%),var(--bg);color:var(--text);font:16px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;min-height:100vh}.shell{max-width:1180px;margin:auto;padding:28px 22px 60px}.top{display:flex;justify-content:space-between;gap:18px;align-items:flex-start;margin-bottom:24px}.brand{display:flex;gap:14px;align-items:center}.mark{width:46px;height:46px;border:1px solid #315268;background:linear-gradient(145deg,#153043,#0b1823);display:grid;place-items:center;border-radius:13px;box-shadow:0 12px 40px #0008}.mark b{font:800 18px ui-monospace,monospace;color:var(--mint)}h1{font-size:1.42rem;margin:0;letter-spacing:-.02em}.sub{color:var(--muted);font-size:.9rem}.top-actions{display:flex;gap:10px;align-items:center}.lang-switch{display:flex;gap:3px;padding:3px;border:1px solid var(--line);border-radius:10px;background:#09151f}.lang-switch button{width:auto;padding:5px 9px;border-radius:7px;background:transparent;color:var(--muted);font-size:.78rem}.lang-switch button.active-lang{background:#173345;color:var(--text)}.status{display:flex;gap:9px;align-items:center;border:1px solid var(--line);border-radius:999px;padding:8px 12px;background:#09151f}.dot{width:8px;height:8px;border-radius:50%;background:var(--muted)}.live .dot{background:var(--mint);box-shadow:0 0 12px var(--mint)}.mock .dot{background:var(--amber)}.bad .dot{background:var(--red)}.grid{display:grid;grid-template-columns:1.4fr .8fr;gap:18px}.stack{display:grid;gap:18px}.card{border:1px solid var(--line);border-radius:18px;background:linear-gradient(145deg,#0e1e2b,#091620);padding:20px;box-shadow:0 18px 50px #0003}.eyebrow{text-transform:uppercase;letter-spacing:.14em;font-size:.72rem;color:var(--muted);font-weight:750}.account-row{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:14px}.kv{background:#091722;border:1px solid #1b3445;border-radius:12px;padding:12px}.kv span{display:block;color:var(--muted);font-size:.76rem}.kv strong{display:block;margin-top:3px;font-size:.96rem;overflow-wrap:anywhere}.windows{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.window h2{font-size:1rem;margin:5px 0 18px}.meter{height:9px;background:#152b39;border-radius:99px;overflow:hidden;margin:10px 0 18px}.meter i{display:block;height:100%;background:linear-gradient(90deg,var(--mint),var(--blue));border-radius:inherit}.numbers{display:flex;align-items:end;justify-content:space-between}.big{font:750 2.45rem/1 ui-monospace,SFMono-Regular,Consolas,monospace;letter-spacing:-.06em}.remain{color:var(--mint);font-weight:700}.details{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:14px}.detail{border-top:1px solid var(--line);padding-top:10px}.detail span{display:block;color:var(--muted);font-size:.75rem}.detail strong{font:650 .88rem ui-monospace,monospace}.count{color:var(--blue)}button{appearance:none;border:0;border-radius:11px;background:var(--mint);color:#04130f;font-weight:800;padding:11px 14px;cursor:pointer;width:100%;font-size:.9rem}button:hover{filter:brightness(1.06)}button:disabled{opacity:.45;cursor:not-allowed}.guide{font-size:.86rem;color:var(--muted);margin:12px 0 0}.guide code{color:#d8eaf0}.active{margin-top:14px;padding:12px;border:1px dashed #3c6679;border-radius:12px;color:#b8d5df;font-size:.86rem}.table{width:100%;border-collapse:collapse;margin-top:10px}.table th,.table td{text-align:left;padding:9px 6px;border-bottom:1px solid #183041;font-size:.82rem}.table th{color:var(--muted);font-weight:600}.empty{padding:28px 5px;color:var(--muted);text-align:center}.error{color:#ff9ca5;background:#2a131b;border:1px solid #5c2631;padding:10px 12px;border-radius:10px;margin-top:12px;font:13px ui-monospace,monospace}.footer{color:#668694;font-size:.76rem;margin-top:20px;display:flex;justify-content:space-between;gap:12px}.pill{font-size:.72rem;padding:3px 7px;border-radius:6px;background:#183041;color:#a9c5d0}.exp{border-top:1px solid var(--line);padding:12px 0}.exp:first-of-type{border-top:0}.exp strong{font-size:.88rem}.exp p{margin:4px 0;color:var(--muted);font-size:.78rem}.exp a{color:var(--blue);font-size:.78rem}.skeleton{height:250px;display:grid;place-items:center;color:var(--muted)}
.active-actions{display:grid;grid-template-columns:1fr auto;gap:8px;margin-top:12px}.active-actions button{width:auto}.active-actions .secondary{background:#173040;color:#b9d3dd;border:1px solid #2a4d60}.history-head{display:flex;align-items:center;justify-content:space-between;gap:12px}.history-head a{color:var(--blue);font-size:.76rem;text-decoration:none}.active p{margin:6px 0;color:var(--muted)}.plan-form{display:grid;gap:11px;margin-top:14px}.plan-form label{color:var(--muted);font-size:.78rem}.plan-form input[type=text],.plan-form input[type=number],.plan-form input[type=datetime-local]{width:100%;margin-top:5px;border:1px solid #29485b;background:#08151f;color:var(--text);padding:9px 10px;border-radius:9px}.check{display:flex;gap:9px;align-items:flex-start}.check input{margin-top:4px}.notice{color:var(--amber);font-size:.76rem;margin:8px 0}.run{border-top:1px solid var(--line);padding:9px 0;font-size:.78rem}.run:first-child{border-top:0}.run span{color:var(--muted)}.report-figure{width:100%;margin-top:12px;border:1px solid var(--line);border-radius:12px;background:#08131d}.report-links{display:flex;gap:12px;margin-top:10px}.report-links a{color:var(--blue);font-size:.8rem}.subform{border-bottom:1px solid var(--line);padding-bottom:16px;margin-bottom:14px}
.setup{margin-bottom:18px}.setup h2{margin:5px 0}.setup-steps{display:grid;grid-template-columns:repeat(4,1fr);gap:9px;margin:16px 0}.setup-step{padding:12px;border:1px solid var(--line);border-radius:11px;background:#091722;font-size:.82rem}.setup-step.ok{border-color:#287b6d;color:var(--mint)}.settings-actions{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px}.secondary,.button-link{background:#173040;color:#b9d3dd;border:1px solid #2a4d60}.button-link{display:grid;place-items:center;border-radius:11px;padding:11px 14px;text-decoration:none;font-weight:800;font-size:.9rem}
@media(max-width:850px){.grid{grid-template-columns:1fr}.windows{grid-template-columns:1fr}.account-row{grid-template-columns:1fr}.top{align-items:center}.top-actions{flex-direction:column-reverse;align-items:flex-end}.sub{display:none}.shell{padding:20px 14px 40px}.big{font-size:2rem}}
</style></head><body><main class="shell">
<header class="top"><div class="brand"><div class="mark"><b>QL</b></div><div><h1>QuotaLab <span class="pill">v0.7.2</span></h1><div id="subtitle" class="sub"></div></div></div><div class="top-actions"><div class="lang-switch" role="group" aria-label="Language"><button id="lang-zh" type="button">中文</button><button id="lang-en" type="button">EN</button></div><div id="status" class="status"><i class="dot"></i><strong></strong></div></div></header>
<section id="setup-assistant" class="card setup" hidden><div class="eyebrow">FIRST RUN SETUP</div><h2 id="setup-title"></h2><p id="setup-guide" class="guide"></p><div id="setup-steps" class="setup-steps"></div><form id="codex-path-form" class="plan-form"><label><span id="manual-path-label"></span><input id="manual-codex-path" type="text" placeholder="C:\...\codex.exe"></label><button id="validate-path" type="submit"></button></form><div class="settings-actions"><button id="setup-retry" class="secondary" type="button"></button><button id="enter-dashboard" type="button"></button></div></section>
<div class="grid"><section class="stack"><div class="card"><div id="account-title" class="eyebrow"></div><div class="account-row" id="account"></div><div id="error"></div></div><div id="windows" class="windows"><div class="card skeleton"></div></div><div class="card"><div id="probe-title" class="eyebrow"></div><p id="probe-guide" class="guide"></p><div id="probe-status" class="guide"></div><form id="probe-form" class="plan-form"><label><span id="probe-interval-label"></span><select id="probe-interval"><option value="30">30 s</option><option value="60" selected>60 s</option><option value="120">120 s</option></select></label><label><span id="probe-duration-label"></span><input id="probe-duration" type="number" min="120" max="3600" value="600"></label><div class="settings-actions"><button id="start-probe" type="submit"></button><button id="stop-probe" class="secondary" type="button"></button></div></form><div id="probe-buckets"></div><div class="report-links"><a href="/api/window-state.svg" target="_blank">Reset Stability Timeline</a><a href="/api/window-state/export.csv">CSV</a></div></div><div class="card"><div id="recent-title" class="eyebrow"></div><div id="observations" class="empty"></div></div><div class="card"><div id="reset-events-title" class="eyebrow"></div><div id="reset-events" class="empty"></div></div></section>
<aside class="stack"><div class="card"><div id="plan-title" class="eyebrow"></div><p id="plan-guide" class="guide"></p><form id="intensity-form" class="plan-form subform"><div id="intensity-title" class="eyebrow"></div><label><span id="session-start-label"></span><input id="session-start" type="datetime-local"></label><label><span id="session-end-label"></span><input id="session-end" type="datetime-local"></label><div id="intensity-preview" class="notice"></div><button id="create-intensity-plan" type="submit"></button></form><details><summary id="advanced-title"></summary><form id="plan-form" class="plan-form"><label><span id="times-label"></span><input id="plan-times" type="text" placeholder="06:00, 12:00, 18:00"></label><label><span id="max-label"></span><input id="plan-max" type="number" min="1" max="8"></label><label><span id="target-label"></span><input id="plan-target" type="number" min="1" max="5"></label><label class="check"><input id="plan-enabled" type="checkbox"><span id="enabled-label"></span></label><label class="check"><input id="plan-auto" type="checkbox"><span id="auto-label"></span></label><div id="auto-warning" class="notice"></div><button id="save-plan" type="submit"></button></form><div class="active"><span id="next-label"></span> <strong id="next-run">—</strong><br><span id="remaining-label"></span> <strong id="remaining-runs">—</strong><div id="campaign-slots" class="guide"></div></div><div id="scheduled-runs"></div></details></div><details class="card"><summary id="dispatch-title"></summary><p id="dispatch-guide" class="guide"></p><button id="run-dispatch-test" type="button"></button><div id="dispatch-tests"></div></details><details class="card"><summary id="experiment-advanced"></summary><div id="anchor-eyebrow" class="eyebrow"></div><h2 id="anchor-title" style="margin:7px 0 4px;font-size:1.08rem"></h2><p id="anchor-guide" class="guide"></p><button id="start" type="button"></button><div id="active"></div><div class="history-head" style="margin-top:16px"><div id="history-title" class="eyebrow"></div><a id="export-csv" href="/api/experiments/export.csv"></a></div><div id="experiments" class="empty"></div><div id="report-title" class="eyebrow"></div><p id="report-summary" class="guide"></p><img class="report-figure" src="/api/report.svg" alt="Anchor experiment result chart"><div class="report-links"><a id="open-report" href="/api/report" target="_blank"></a><a id="open-chart" href="/api/report.svg" target="_blank"></a></div></details><div class="card"><div id="data-title" class="eyebrow"></div><div class="account-row" style="grid-template-columns:1fr"><div class="kv"><span id="app-health-label"></span><strong id="app-health">—</strong></div><div class="kv"><span id="dashboard-health-label"></span><strong id="dashboard-health">—</strong></div><div class="kv"><span id="scheduler-health-label"></span><strong id="scheduler-health">—</strong></div><div class="kv"><span id="last-label"></span><strong id="last">—</strong></div><div class="kv"><span>Codex CLI</span><strong id="cli">—</strong></div><div class="kv"><span id="source-label"></span><strong id="cli-source">—</strong></div><div class="kv"><span id="server-label"></span><strong id="server">—</strong></div></div><form id="settings-codex-form" class="plan-form"><label><span id="settings-path-label"></span><input id="settings-codex-path" type="text" placeholder="C:\...\codex.exe"></label><button id="save-codex-path" type="submit"></button></form><div class="settings-actions"><button id="refresh-status" class="secondary" type="button"></button><button id="reconnect-codex" class="secondary" type="button"></button></div><div class="settings-actions"><button id="rediscover-codex" class="secondary" type="button"></button><a id="open-logs" class="button-link" href="/api/logs" target="_blank"></a></div><button id="exit-app" class="secondary" type="button" style="margin-top:8px"></button></div></aside></div>
<footer class="footer"><span id="footer-local"></span><span id="footer-privacy"></span></footer></main>
<script>
const messages={zh:{title:'QuotaLab · Codex 额度窗口实验',subtitle:'Codex 额度观测与本地使用计划',account:'账户',recent:'最近观测',anchorEyebrow:'窗口锚点实验',anchorTitle:'Anchor 与窗口内对照观测',anchorGuide:'先保存实验前快照。当前窗口仍活动时，继续运行只会记录为窗口内对照；只有旧窗口重置加 30 秒后才是合格 Anchor。',startAnchor:'准备 Anchor 实验',runControl:'运行窗口内对照实验',history:'实验历史',exportCsv:'导出汇总 CSV',dataStatus:'数据状态',lastSnapshot:'最近快照',appServer:'应用服务器',footerLocal:'所有数据均保留在本机。',footerPrivacy:'仅限 127.0.0.1 · 不读取 Cookie · 不需要 API Key',email:'邮箱',plan:'套餐',auth:'认证',connected:'已连接',unavailable:'不可用',unknown:'未知',quota:'额度',primary:'主窗口',secondary:'次窗口',fiveHour:'5 小时窗口',weekly:'周窗口',customWindow:'自定义窗口（{n} 分钟）',used:'已使用',remaining:'剩余',duration:'周期',countdown:'倒计时',resetAt:'重置时间',limitId:'限额 ID',rateUnavailable:'额度数据暂不可用',time:'时间',window:'窗口',reset:'重置',noChanges:'尚未记录变化',prepared:'实验已准备',preparedHelp:'请先阅读资格提示；发送后立即点击“标记已发送”。',markSent:'标记已发送',cancel:'取消实验',triggered:'已记录手动触发',triggeredAt:'触发时间',waitingReset:'正在等待后续额度快照，最长等待 15 分钟。',cancelled:'已取消',timedOut:'已超时',mock:'模拟',live:'真实',exportJson:'导出 JSON',noExperiments:'尚无已完成实验',starting:'启动中',disconnected:'已断开',stale:'数据陈旧',reconnecting:'重新连接中',latency:'检测延迟'},en:{title:'QuotaLab · Codex quota window experiment',subtitle:'Codex quota observation and local usage planning',account:'Account',recent:'Recent observations',anchorEyebrow:'Window anchor experiment',anchorTitle:'Anchor and in-window control observations',anchorGuide:'Save the before snapshot first. While the current window is active, continuing records an in-window control. An eligible Anchor begins only after the old reset plus 30 seconds.',startAnchor:'Prepare Anchor Experiment',runControl:'Run In-window Control',history:'Experiment history',exportCsv:'Export summary CSV',dataStatus:'Data status',lastSnapshot:'Last snapshot',appServer:'App Server',footerLocal:'All data stays on this computer.',footerPrivacy:'127.0.0.1 only · no cookies · no API key',email:'Email',plan:'Plan',auth:'Auth',connected:'Connected',unavailable:'Unavailable',unknown:'Unknown',quota:'Quota',primary:'Primary',secondary:'Secondary',fiveHour:'5-hour window',weekly:'Weekly window',customWindow:'Custom window ({n} min)',used:'used',remaining:'remaining',duration:'Duration',countdown:'Countdown',resetAt:'Reset at',limitId:'Limit ID',rateUnavailable:'Rate limit data unavailable',time:'Time',window:'Window',reset:'Reset',noChanges:'No changes recorded yet.',prepared:'EXPERIMENT PREPARED',preparedHelp:'Review the eligibility notice first, then mark the authorized request as sent.',markSent:'Mark request sent',cancel:'Cancel experiment',triggered:'MANUAL TRIGGER RECORDED',triggeredAt:'Trigger time',waitingReset:'Waiting up to 15 minutes for a follow-up quota snapshot.',cancelled:'CANCELLED',timedOut:'TIMEOUT',mock:'MOCK',live:'LIVE',exportJson:'Export JSON',noExperiments:'No completed experiments.',starting:'STARTING',disconnected:'DISCONNECTED',stale:'STALE',reconnecting:'RECONNECTING',latency:'Detection latency'}};
Object.assign(messages.zh,{planTitle:'日常使用',planGuide:'填写明天的高强度使用时段，QuotaLab 会计算建议重置与最小任务时间。前一天授权后，到点无需再次确认。',intensityTitle:'明天高强度使用',sessionStart:'开始时间',sessionEnd:'结束时间',intensityPreview:'建议重置 {reset} · 建议 Anchor {anchor}',createIntensity:'授权计划',confirmIntensity:'将按时段中点减去实际短窗口时长自动发送固定最小任务，并替换当前计划。确认吗？',advancedTitle:'实验 / 高级调度',experimentAdvanced:'Anchor 实验 · 查看实验进度',timesLabel:'高级计划时间（HH:MM，逗号分隔）',maxLabel:'每日自动调用上限',targetLabel:'自动任务次数（1–5）',enabledLabel:'启用计划',autoLabel:'到点自动发送最小任务',autoWarning:'自动发送会真实消耗 Codex 额度，不能增加或绕过官方限额。达到目标次数后自动停止。',savePlan:'保存高级计划',nextRun:'下次计划：',remainingRuns:'本轮剩余：',campaignSchedule:'一次性任务序列：',resetEvents:'重置事件',noResetEvents:'尚未检测到 resetsAt 变化',recentRuns:'最近计划运行',noRuns:'尚无计划运行',confirmAuto:'自动发送会消耗真实 Codex 额度。确认启用吗？',saved:'计划已保存',reportTitle:'实验结果图与报告',openReport:'打开一页实验报告',openChart:'打开结果图',reportProgress:'真实实验 {n}/{target}，有效差值 {m} 次',applicationHealth:'应用程序',dashboardHealth:'本地 Dashboard',schedulerHealth:'Scheduler',codexSource:'Codex 来源',rediscoverCodex:'重新检测 Codex CLI',running:'运行中',activeStatus:'活动',pausedStatus:'暂停',exitApp:'退出 QuotaLab',confirmExit:'确定退出 QuotaLab？计划和实验数据会保留。'});
Object.assign(messages.en,{planTitle:'Normal Usage',planGuide:'Enter tomorrow’s high-intensity period. QuotaLab calculates a recommended reset and minimal-task time. Advance authorization removes the need for an overnight confirmation.',intensityTitle:'Tomorrow’s high-intensity usage',sessionStart:'Start',sessionEnd:'End',intensityPreview:'Recommended reset {reset} · Recommended Anchor {anchor}',createIntensity:'Authorize Plan',confirmIntensity:'This replaces the current plan and sends the fixed minimal task one actual short-window duration before the session midpoint. Continue?',advancedTitle:'Experiment / Advanced scheduling',experimentAdvanced:'Anchor Experiment · View progress',timesLabel:'Advanced times (HH:MM, comma-separated)',maxLabel:'Daily automatic call cap',targetLabel:'Automatic task count (1–5)',enabledLabel:'Enable plan',autoLabel:'Automatically send the minimal task',autoWarning:'Automatic sends consume real Codex quota and cannot increase or bypass official limits. The campaign stops at its target.',savePlan:'Save advanced plan',nextRun:'Next plan:',remainingRuns:'Campaign remaining:',campaignSchedule:'One-off task sequence:',resetEvents:'Reset events',noResetEvents:'No resetsAt change detected yet.',recentRuns:'Recent planned runs',noRuns:'No planned run yet.',confirmAuto:'Automatic sending consumes real Codex quota. Enable it?',saved:'Plan saved',reportTitle:'Experiment chart and report',openReport:'Open one-page report',openChart:'Open result chart',reportProgress:'Real trials {n}/{target}; measured differences {m}',applicationHealth:'Application',dashboardHealth:'Local Dashboard',schedulerHealth:'Scheduler',codexSource:'Codex source',rediscoverCodex:'Rediscover Codex CLI',running:'Running',activeStatus:'Active',pausedStatus:'Paused',exitApp:'Exit QuotaLab',confirmExit:'Exit QuotaLab? Plans and experiment data will be kept.'});
Object.assign(messages.zh,{setupTitle:'欢迎使用 QuotaLab',setupGuide:'本地 Agent 正在检查 Codex、认证与额度读取链路。所有数据保留在本机。',detectStep:'检测 Codex',connectStep:'连接 app-server',accountStep:'读取账户',quotaStep:'读取额度',manualPath:'手动指定 Codex CLI 路径',validatePath:'验证并使用路径',retryDetection:'重新检测',enterDashboard:'进入 Dashboard',dispatchTitle:'定时发送可靠性测试',dispatchGuide:'仅验证调度时间与日志，不用于证明五小时窗口规律。此按钮只运行 Mock，不发送真实任务。',runDispatch:'运行 Mock 调度测试',settingsPath:'Codex CLI 路径',savePath:'验证路径',refreshStatus:'刷新',reconnectCodex:'重新连接',openLogs:'打开日志'});
Object.assign(messages.en,{setupTitle:'Welcome to QuotaLab',setupGuide:'The Local Agent is checking Codex, authentication, and the quota read path. All data stays local.',detectStep:'Detect Codex',connectStep:'Connect app-server',accountStep:'Read account',quotaStep:'Read quota',manualPath:'Manual Codex CLI path',validatePath:'Validate and use path',retryDetection:'Retry detection',enterDashboard:'Enter Dashboard',dispatchTitle:'Scheduled Dispatch Reliability Test',dispatchGuide:'Tests timing and logging only; it does not prove a five-hour quota anchor. This button is Mock-only and sends no real task.',runDispatch:'Run Mock Dispatch Test',settingsPath:'Codex CLI path',savePath:'Validate path',refreshStatus:'Refresh',reconnectCodex:'Reconnect',openLogs:'Open logs'});
Object.assign(messages.zh,{anchorTitle:'Anchor 与窗口内对照观测',anchorGuide:'当前窗口仍活动时，继续运行只记录为窗口内对照；只有旧窗口重置加 30 秒后才是合格 Anchor。',runControl:'运行窗口内对照实验',waitingReset:'正在等待后续额度快照，最长等待 15 分钟。',noResetEvents:'尚未检测到超过 5 秒抖动阈值的 resetsAt 变化',reportProgress:'真实观测 {real} · 窗口内对照 {controls} · 合格 Anchor {eligible} · 已确认 {confirmed}',ANCHOR_CONFIRMED:'已确认锚定',NO_REANCHOR:'未观察到重新锚定',RESET_CHANGED_NOT_ANCHORED:'重置时间发生变化，但不符合锚定预期',IN_WINDOW_CONTROL_NO_REANCHOR:'窗口内对照：未观察到重新锚定',IN_WINDOW_CONTROL_RESET_CHANGED:'窗口内对照：重置时间发生明显变化',INCONCLUSIVE:'证据不足',REQUEST_FAILED:'请求失败',TIMEOUT:'已超时',CANCELLED:'已取消'});
Object.assign(messages.en,{anchorTitle:'Anchor and in-window control observations',anchorGuide:'While the current window is active, continuing records an in-window control. An eligible Anchor begins only after the old reset plus 30 seconds.',runControl:'Run In-window Control',waitingReset:'Waiting up to 15 minutes for a follow-up quota snapshot.',noResetEvents:'No resetsAt movement beyond the 5-second jitter threshold.',reportProgress:'Real observations {real} · In-window controls {controls} · Eligible Anchors {eligible} · Confirmed {confirmed}',ANCHOR_CONFIRMED:'ANCHOR CONFIRMED',NO_REANCHOR:'NO RE-ANCHOR OBSERVED',RESET_CHANGED_NOT_ANCHORED:'RESET CHANGED, NOT ANCHORED',IN_WINDOW_CONTROL_NO_REANCHOR:'IN-WINDOW CONTROL · NO RE-ANCHOR',IN_WINDOW_CONTROL_RESET_CHANGED:'IN-WINDOW CONTROL · RESET CHANGED',INCONCLUSIVE:'INCONCLUSIVE',REQUEST_FAILED:'REQUEST FAILED',TIMEOUT:'TIMEOUT',CANCELLED:'CANCELLED'});
Object.assign(messages.zh,{probeTitle:'窗口状态观测',probeGuide:'此测试仅读取额度状态，不发送模型任务，不消耗模型调用额度。',probeInterval:'采样间隔',probeDuration:'持续时间（秒）',startProbe:'启动只读 Probe',stopProbe:'停止',windowState:'窗口状态',samples:'样本',span:'跨度',slope:'Reset slope',horizon:'Horizon',UNKNOWN:'未知',ROLLING:'滚动 / 未固定',FIXED:'固定窗口',INSUFFICIENT_DATA:'数据不足',ANCHOR_CANDIDATE:'Anchor 候选',ROLLING_TO_FIXED_ANCHOR_CANDIDATE:'滚动到固定候选',ANCHOR_CONFIRMED_BY_STATE_TRANSITION:'观察到与请求相关的窗口锚定',ROLLING_WINDOW_NO_ANCHOR:'窗口保持滚动，未观察到锚定'});
Object.assign(messages.en,{probeTitle:'Reset Stability Probe',probeGuide:'This test only reads quota state. It sends no model task and consumes no model-call quota.',probeInterval:'Sampling interval',probeDuration:'Duration (seconds)',startProbe:'Start read-only Probe',stopProbe:'Stop',windowState:'Window state',samples:'Samples',span:'Span',slope:'Reset slope',horizon:'Horizon',UNKNOWN:'Unknown',ROLLING:'Rolling / unfixed',FIXED:'Fixed window',INSUFFICIENT_DATA:'Insufficient data',ANCHOR_CANDIDATE:'Anchor candidate',ROLLING_TO_FIXED_ANCHOR_CANDIDATE:'Rolling-to-fixed candidate',ANCHOR_CONFIRMED_BY_STATE_TRANSITION:'Request-associated window anchor observed',ROLLING_WINDOW_NO_ANCHOR:'Window remained rolling; no anchor observed'});
let latest=null;let locale='zh';const $=s=>document.querySelector(s);const esc=s=>String(s??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const t=(key,vars={})=>{let value=messages[locale][key]??messages.en[key]??key;for(const [k,v] of Object.entries(vars))value=value.replace(`{${k}}`,v);return value};
const dt=v=>{if(!v)return'—';const d=typeof v==='number'?new Date(v*1000):new Date(v);return isNaN(d)?'—':d.toLocaleString(locale==='zh'?'zh-CN':'en-US')};
const countdown=v=>{if(!v)return'—';let s=Math.max(0,Math.floor(v-Date.now()/1000));const d=Math.floor(s/86400);s%=86400;const h=Math.floor(s/3600);s%=3600;const m=Math.floor(s/60),x=s%60;return(d?d+'d ':'')+[h,m,x].map(n=>String(n).padStart(2,'0')).join(':')};
const kindLabel=w=>w.window_duration_minutes===300?t('fiveHour'):w.window_duration_minutes===10080?t('weekly'):t('customWindow',{n:w.window_duration_minutes??'—'});
const slotLabel=slot=>slot==='primary'?t('primary'):slot==='secondary'?t('secondary'):slot;
const statusLabel=status=>({LIVE:t('live'),'MOCK DATA':t('mock'),STARTING:t('starting'),CONNECTED:t('connected'),STALE:t('stale'),UNAVAILABLE:t('unavailable'),DISCONNECTED:t('disconnected'),RECONNECTING:t('reconnecting')})[status]||status;
function updateIntensityPreview(){const box=$('#intensity-preview'),start=$('#session-start')?.value,end=$('#session-end')?.value;if(!box||!start||!end){if(box)box.textContent='';return}const a=new Date(start),b=new Date(end);if(isNaN(a)||isNaN(b)||b<=a){box.textContent='';return}const reset=new Date((a.getTime()+b.getTime())/2),minutes=latest?.windows?.find(w=>w.slot==='primary'&&w.window_duration_minutes)?.window_duration_minutes||300,anchor=new Date(reset.getTime()-minutes*60*1000);box.textContent=t('intensityPreview',{reset:dt(reset),anchor:dt(anchor)})}
function applyStatic(){document.documentElement.lang=locale==='zh'?'zh-CN':'en';document.title=t('title');$('#subtitle').textContent=t('subtitle');$('#account-title').textContent=t('account');$('#recent-title').textContent=t('recent');$('#anchor-eyebrow').textContent=t('anchorEyebrow');$('#anchor-title').textContent=t('anchorTitle');$('#anchor-guide').innerHTML=t('anchorGuide');$('#start').textContent=t('startAnchor');$('#history-title').textContent=t('history');$('#export-csv').textContent=t('exportCsv');$('#data-title').textContent=t('dataStatus');$('#last-label').textContent=t('lastSnapshot');$('#server-label').textContent=t('appServer');$('#footer-local').textContent=t('footerLocal');$('#footer-privacy').textContent=t('footerPrivacy');for(const code of ['zh','en']){$(`#lang-${code}`).classList.toggle('active-lang',locale===code);$(`#lang-${code}`).setAttribute('aria-pressed',String(locale===code))}}
const baseApplyStatic=applyStatic;applyStatic=()=>{baseApplyStatic();$('#plan-title').textContent=t('planTitle');$('#plan-guide').textContent=t('planGuide');$('#intensity-title').textContent=t('intensityTitle');$('#session-start-label').textContent=t('sessionStart');$('#session-end-label').textContent=t('sessionEnd');$('#create-intensity-plan').textContent=t('createIntensity');$('#advanced-title').textContent=t('advancedTitle');$('#experiment-advanced').textContent=t('experimentAdvanced');$('#times-label').textContent=t('timesLabel');$('#max-label').textContent=t('maxLabel');$('#target-label').textContent=t('targetLabel');$('#enabled-label').textContent=t('enabledLabel');$('#auto-label').textContent=t('autoLabel');$('#auto-warning').textContent=t('autoWarning');$('#save-plan').textContent=t('savePlan');$('#next-label').textContent=t('nextRun');$('#remaining-label').textContent=t('remainingRuns');$('#reset-events-title').textContent=t('resetEvents');$('#report-title').textContent=t('reportTitle');$('#open-report').textContent=t('openReport');$('#open-chart').textContent=t('openChart');$('#app-health-label').textContent=t('applicationHealth');$('#dashboard-health-label').textContent=t('dashboardHealth');$('#scheduler-health-label').textContent=t('schedulerHealth');$('#source-label').textContent=t('codexSource');$('#rediscover-codex').textContent=t('rediscoverCodex');$('#exit-app').textContent=t('exitApp');updateIntensityPreview()};
const productApplyStatic=applyStatic;applyStatic=()=>{productApplyStatic();$('#setup-title').textContent=t('setupTitle');$('#setup-guide').textContent=t('setupGuide');$('#manual-path-label').textContent=t('manualPath');$('#validate-path').textContent=t('validatePath');$('#setup-retry').textContent=t('retryDetection');$('#enter-dashboard').textContent=t('enterDashboard');$('#dispatch-title').textContent=t('dispatchTitle');$('#dispatch-guide').textContent=t('dispatchGuide');$('#run-dispatch-test').textContent=t('runDispatch');$('#settings-path-label').textContent=t('settingsPath');$('#save-codex-path').textContent=t('savePath');$('#refresh-status').textContent=t('refreshStatus');$('#reconnect-codex').textContent=t('reconnectCodex');$('#open-logs').textContent=t('openLogs')};
const probeApplyStatic=applyStatic;applyStatic=()=>{probeApplyStatic();$('#probe-title').textContent=t('probeTitle');$('#probe-guide').textContent=t('probeGuide');$('#probe-interval-label').textContent=t('probeInterval');$('#probe-duration-label').textContent=t('probeDuration');$('#start-probe').textContent=t('startProbe');$('#stop-probe').textContent=t('stopProbe')};
function setLocale(next){locale=next==='en'?'en':'zh';try{localStorage.setItem('quotalab_locale',locale)}catch(e){}applyStatic();if(latest)render(latest)}
function render(s){latest=s;const cls=s.status==='LIVE'?'live':s.status==='MOCK DATA'?'mock':(s.error?'bad':'');$('#status').className='status '+cls;$('#status strong').textContent=statusLabel(s.status);const a=s.account||{};$('#account').innerHTML=`<div class="kv"><span>${t('email')}</span><strong>${esc(a.email||t('unavailable'))}</strong></div><div class="kv"><span>${t('plan')}</span><strong>${esc(a.plan_type||t('unknown'))}</strong></div><div class="kv"><span>${t('auth')}</span><strong>${a.type?t('connected'):t('unavailable')}</strong></div>`;$('#error').innerHTML=s.error?`<div class="error">${esc(s.error)}</div>`:'';
$('#windows').innerHTML=s.windows?.length?s.windows.map(w=>`<article class="card window"><div class="eyebrow">${esc(w.limit_name||w.limit_id||t('quota'))} · ${esc(slotLabel(w.slot))}</div><h2>${esc(kindLabel(w))}</h2><div class="numbers"><div><span class="big">${w.used_percent??'—'}%</span><div class="sub" style="display:block">${t('used')}</div></div><div class="remain">${w.remaining_percent??'—'}% ${t('remaining')}</div></div><div class="meter"><i style="width:${Math.max(0,Math.min(100,w.used_percent||0))}%"></i></div><div class="details"><div class="detail"><span>${t('duration')}</span><strong>${w.window_duration_minutes??'—'} min</strong></div><div class="detail"><span>${t('countdown')}</span><strong class="count" data-reset="${w.resets_at||''}">${countdown(w.resets_at)}</strong></div><div class="detail"><span>${t('resetAt')}</span><strong>${dt(w.resets_at)}</strong></div><div class="detail"><span>${t('windowState')}</span><strong>${esc(t(w.window_state||'INSUFFICIENT_DATA'))}</strong></div></div></article>`).join(''):`<div class="card skeleton">${t('rateUnavailable')}</div>`;
$('#observations').innerHTML=s.recent_observations?.length?`<table class="table"><thead><tr><th>${t('time')}</th><th>${t('window')}</th><th>${t('used')}</th><th>${t('reset')}</th></tr></thead><tbody>${s.recent_observations.slice(0,10).map(o=>`<tr><td>${dt(o.observed_at)}</td><td>${esc(kindLabel(o))}</td><td>${o.used_percent??'—'}%</td><td>${dt(o.resets_at)}</td></tr>`).join('')}</tbody></table>`:`<div class="empty">${t('noChanges')}</div>`;
const active=s.active_experiment;const primary=s.windows?.find(w=>w.window_duration_minutes===300);const inWindow=primary?.resets_at>Date.now()/1000;$('#start').textContent=inWindow?t('runControl'):t('startAnchor');$('#start').disabled=!!active||!primary;$('#start').hidden=!!active;if(active){const prepared=active.status==='PREPARED';const eligibility=`<p>${esc(active.eligibilityNotice||'')}<br>Previous reset: ${dt(active.previousResetAt)} · Eligible after: ${dt(active.eligibleAfter)}${active.triggerActualAt?`<br>Selected Anchor: ${dt(active.triggerActualAt)} · Expected next reset: ${dt(active.expectedResetAt)} · Tolerance: ±120 s`:''}</p>`;$('#active').innerHTML=`<div class="active"><strong>${prepared?t('prepared'):t('triggered')}</strong><br>${prepared?t('preparedHelp'):`${t('triggeredAt')} ${dt(active.trigger_at)}<br>${t('waitingReset')}`}${eligibility}<div class="active-actions">${prepared?`<button type="button" data-action="trigger">${t('markSent')}</button>`:''}<button type="button" class="secondary" data-action="cancel">${t('cancel')}</button></div></div>`}else{$('#active').innerHTML=''};
const expStatus=e=>esc(messages[locale][e.classification||e.status]||e.classification||e.status);$('#experiments').innerHTML=s.experiments?.length?s.experiments.map(e=>{const stamp=e.detected_at||e.completed_at||e.trigger_at||e.prepared_at;const delta=e.anchorErrorSeconds??e.difference_from_expected_seconds??'—';const latency=e.detection_latency_seconds==null?'':` · ${t('latency')} ${e.detection_latency_seconds} s`;return `<div class="exp"><strong>${expStatus(e)} <span class="pill">${esc(e.experimentType||'INCONCLUSIVE')}</span> <span class="pill">${e.mode==='mock'?t('mock'):t('live')}</span></strong><p>${dt(stamp)} · ${e.windowDurationMins??e.window_duration_minutes??'—'} min · Anchor error ${delta} s · Eligible ${e.eligibleForAnchorStatistics?'YES':'NO'}${latency}</p><a href="/api/experiments/export?id=${encodeURIComponent(e.id)}">${t('exportJson')}</a></div>`}).join(''):`<div class="empty">${t('noExperiments')}</div>`;$('#last').textContent=dt(s.last_snapshot);const cli=s.codex_cli||{};$('#cli').textContent=s.codex_cli_version||t('unavailable');$('#cli-source').textContent=s.codex_cli_source||(cli.checked_sources?.length?`${t('unavailable')} · ${cli.checked_sources.join(', ')}`:t('unavailable'));$('#server').textContent=statusLabel(s.app_server||'UNAVAILABLE')}
const baseRender=render;render=s=>{baseRender(s);const p=s.usage_plan||{};const form=$('#plan-form');if(!form.dataset.loaded){$('#plan-times').value=(p.times||[]).join(', ');$('#plan-max').value=p.max_daily_runs||3;$('#plan-target').value=p.target_anchor_runs||5;$('#plan-enabled').checked=!!p.enabled;$('#plan-auto').checked=!!p.auto_anchor;form.dataset.loaded='1'}$('#next-run').textContent=dt(s.next_scheduled_run);$('#remaining-runs').textContent=p.remaining_anchor_runs??p.target_anchor_runs??'—';$('#campaign-slots').textContent=p.campaign_slots?.length?t('campaignSchedule')+' '+p.campaign_slots.map(dt).join(' · '):'';const rs=s.report_summary||{};$('#report-summary').textContent=t('reportProgress',{real:rs.real_observations??0,controls:rs.in_window_controls??0,eligible:rs.eligible_anchor_trials??0,confirmed:rs.confirmed_anchors??0});$('#reset-events').innerHTML=s.reset_events?.length?`<table class="table"><thead><tr><th>${t('time')}</th><th>${t('window')}</th><th>${t('used')}</th><th>${t('reset')}</th></tr></thead><tbody>${s.reset_events.slice(0,10).map(e=>`<tr><td>${dt(e.detected_at)}</td><td>${e.window_duration_minutes??'—'} min</td><td>—</td><td>${dt(e.old_resets_at)} → ${dt(e.new_resets_at)}</td></tr>`).join('')}</tbody></table>`:`<div class="empty">${t('noResetEvents')}</div>`;$('#scheduled-runs').innerHTML=`<div class="eyebrow" style="margin-top:16px">${t('recentRuns')}</div>`+(s.scheduled_runs?.length?s.scheduled_runs.slice(0,6).map(r=>`<div class="run"><strong>${esc(r.scheduled_time)} · ${esc(r.status)}</strong><br><span>${dt(r.recorded_at)} · ${esc(r.action)}</span></div>`).join(''):`<div class="empty">${t('noRuns')}</div>`);const h=s.health||{};$('#app-health').textContent=t('running');$('#dashboard-health').textContent=t('running');$('#scheduler-health').textContent=h.scheduler==='active'?t('activeStatus'):t('pausedStatus');const img=$('.report-figure');img.src='/api/report.svg?ts='+encodeURIComponent(s.last_snapshot||Date.now())};
const productRender=render;render=s=>{productRender(s);const setup=s.setup||{},steps=setup.steps||{};$('#setup-assistant').hidden=!!setup.completed;$('#enter-dashboard').disabled=!setup.ready;$('#setup-steps').innerHTML=[['detect',t('detectStep')],['connect',t('connectStep')],['account',t('accountStep')],['quota',t('quotaStep')]].map(([key,label])=>`<div class="setup-step ${steps[key]?'ok':''}">${steps[key]?'✓':'○'} ${label}</div>`).join('');$('#dispatch-tests').innerHTML=s.dispatch_tests?.length?s.dispatch_tests.slice(0,5).map(x=>`<div class="run"><strong>${esc(x.requestResult)} · ${x.delaySeconds}s</strong><br><span>${dt(x.actualAt)}</span></div>`).join(''):`<div class="empty">${t('noRuns')}</div>`};
const probeRender=render;render=s=>{probeRender(s);const p=s.probe||{},running=['RUNNING','STOPPING'].includes(p.status);$('#probe-status').textContent=`${p.status||'IDLE'} · ${p.samples||0} ${t('samples')} · ${p.intervalSeconds||60}s / ${p.durationSeconds||600}s`;$('#start-probe').disabled=running;$('#stop-probe').disabled=!running;const states=s.window_states||{};$('#probe-buckets').innerHTML=Object.keys(states).length?Object.entries(states).map(([id,x])=>`<div class="run"><strong>${esc(id)} · ${esc(t(x.state))}</strong><br><span>${t('samples')} ${x.samples} · ${t('span')} ${x.observationSpanSeconds}s · ${t('slope')} ${x.resetSlope??'—'} · ${t('horizon')} ${x.latestHorizonSeconds??'—'}s · ${esc(x.evidence)}</span></div>`).join(''):`<div class="empty">${t('INSUFFICIENT_DATA')}</div>`};
async function refresh(){try{const r=await fetch('/api/status',{cache:'no-store'});render(await r.json())}catch(e){$('#status').className='status bad';$('#status strong').textContent=t('disconnected')}}
$('#start').addEventListener('click',async()=>{const r=await fetch('/api/experiments/start',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});if(!r.ok)alert((await r.json()).error);await refresh()});
$('#active').addEventListener('click',async event=>{const action=event.target.dataset.action;if(!action)return;const r=await fetch(`/api/experiments/${action}`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});if(!r.ok)alert((await r.json()).error);await refresh()});
$('#session-start').addEventListener('input',updateIntensityPreview);$('#session-end').addEventListener('input',updateIntensityPreview);
$('#intensity-form').addEventListener('submit',async event=>{event.preventDefault();const start=$('#session-start').value,end=$('#session-end').value;if(!start||!end)return;if(!confirm(t('confirmIntensity')))return;const r=await fetch('/api/plan/intensity',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sessions:[{start,end}]})});if(!r.ok){alert((await r.json()).error);return}delete $('#plan-form').dataset.loaded;alert(t('saved'));await refresh()});
$('#plan-form').addEventListener('submit',async event=>{event.preventDefault();const enabled=$('#plan-enabled').checked,auto=$('#plan-auto').checked;if(enabled&&auto&&!confirm(t('confirmAuto')))return;const payload={enabled,auto_anchor:auto,times:$('#plan-times').value.split(/[,，\s]+/).filter(Boolean),max_daily_runs:Number($('#plan-max').value),target_anchor_runs:Number($('#plan-target').value)};const r=await fetch('/api/plan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});if(!r.ok){alert((await r.json()).error);return}alert(t('saved'));await refresh()});
$('#rediscover-codex').addEventListener('click',async()=>{await fetch('/api/codex/rediscover',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});setTimeout(refresh,800)});
$('#setup-retry').addEventListener('click',async()=>{await fetch('/api/codex/rediscover',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});setTimeout(refresh,800)});$('#reconnect-codex').addEventListener('click',async()=>{await fetch('/api/codex/reconnect',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});setTimeout(refresh,800)});$('#refresh-status').addEventListener('click',refresh);$('#run-dispatch-test').addEventListener('click',async()=>{await fetch('/api/dispatch-tests/mock',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});await refresh()});
$('#probe-form').addEventListener('submit',async e=>{e.preventDefault();const r=await fetch('/api/probe/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({intervalSeconds:Number($('#probe-interval').value),durationSeconds:Number($('#probe-duration').value)})});if(!r.ok)alert((await r.json()).error);await refresh()});$('#stop-probe').addEventListener('click',async()=>{await fetch('/api/probe/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});await refresh()});
async function submitCodexPath(value){const r=await fetch('/api/settings/codex-path',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({codex_path:value})});if(!r.ok){alert((await r.json()).error);return false}setTimeout(refresh,800);return true}$('#codex-path-form').addEventListener('submit',async e=>{e.preventDefault();await submitCodexPath($('#manual-codex-path').value)});$('#settings-codex-form').addEventListener('submit',async e=>{e.preventDefault();await submitCodexPath($('#settings-codex-path').value)});$('#enter-dashboard').addEventListener('click',async()=>{const r=await fetch('/api/setup/complete',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});if(r.ok)await refresh()});
$('#exit-app').addEventListener('click',async()=>{if(!confirm(t('confirmExit')))return;const r=await fetch('/api/shutdown',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});if(r.ok){document.body.innerHTML=`<main class="shell"><div class="card"><h1>QuotaLab</h1><p>${esc(t('exitApp'))}</p></div></main>`}});
$('#lang-zh').addEventListener('click',()=>setLocale('zh'));$('#lang-en').addEventListener('click',()=>setLocale('en'));try{locale=localStorage.getItem('quotalab_locale')||((navigator.language||'').toLowerCase().startsWith('zh')?'zh':'en')}catch(e){locale='zh'}applyStatic();setInterval(()=>document.querySelectorAll('[data-reset]').forEach(x=>x.textContent=countdown(Number(x.dataset.reset))),1000);setInterval(refresh,5000);refresh();
</script></body></html>'''

# Retain the v0.7.2 markup above for comparison while the UI is evaluated.
UI_ASSET_ROOT = Path(getattr(sys, "_MEIPASS", SOURCE_ROOT))
HTML = (UI_ASSET_ROOT / "ui_preview.html").read_text(encoding="utf-8")
UI_REFINEMENT_CSS = "\n".join(
    (UI_ASSET_ROOT / "styles" / name).read_text(encoding="utf-8")
    for name in ("tokens.css", "layout.css", "components.css", "pages.css")
)
HTML = HTML.replace("</style>", f"\n{UI_REFINEMENT_CSS}\n</style>", 1)
HTML = HTML.replace("window.addEventListener('hashchange',navigate);", (UI_ASSET_ROOT / "ui_orchestration.js").read_text(encoding="utf-8") + "\n" + (UI_ASSET_ROOT / "ui_productization.js").read_text(encoding="utf-8") + "\n" + (UI_ASSET_ROOT / "ui_lifecycle.js").read_text(encoding="utf-8") + "\n" + (UI_ASSET_ROOT / "ui_release.js").read_text(encoding="utf-8") + "\nwindow.addEventListener('hashchange',navigate);")
HTML = HTML.replace("['overview','plan','activity','lab','diagnostics','settings'].includes", "orchRoutes.includes")


HTML = HTML.replace("window.addEventListener('hashchange',navigate);", (UI_ASSET_ROOT / 'ui_hardening.js').read_text(encoding='utf-8') + "\nwindow.addEventListener('hashchange',navigate);")
HTML = HTML.replace("window.addEventListener('hashchange',navigate);", (UI_ASSET_ROOT / 'ui_interaction.js').read_text(encoding='utf-8') + "\nwindow.addEventListener('hashchange',navigate);")
HTML = HTML.replace('</style>', (UI_ASSET_ROOT / 'styles/evolution.css').read_text(encoding='utf-8')+'\n</style>', 1)
HTML = HTML.replace("window.addEventListener('hashchange',navigate);", (UI_ASSET_ROOT / 'ui_evolution.js').read_text(encoding='utf-8') + "\nwindow.addEventListener('hashchange',navigate);")
HTML = HTML.replace('</style>', (UI_ASSET_ROOT / 'styles/visual-refinement.css').read_text(encoding='utf-8') + '\n</style>', 1)
HTML = HTML.replace("window.addEventListener('hashchange',navigate);", (UI_ASSET_ROOT / 'ui_visual.js').read_text(encoding='utf-8') + "\nwindow.addEventListener('hashchange',navigate);")
HTML = HTML.replace('</style>', (UI_ASSET_ROOT / 'styles/art-direction.css').read_text(encoding='utf-8') + '\n</style>', 1)
HTML = HTML.replace("window.addEventListener('hashchange',navigate);", (UI_ASSET_ROOT / 'ui_art_direction.js').read_text(encoding='utf-8') + "\nwindow.addEventListener('hashchange',navigate);")
HTML = HTML.replace("window.addEventListener('hashchange',navigate);", (UI_ASSET_ROOT / 'ui_task_workbench.js').read_text(encoding='utf-8') + "\nwindow.addEventListener('hashchange',navigate);")
from urllib.parse import quote as _asset_quote
HTML = re.sub(r'<link rel="icon"[^>]+>', '<link rel="icon" href="data:image/svg+xml,' + _asset_quote((UI_ASSET_ROOT / 'assets/phase-mark.svg').read_text(encoding='utf-8')) + '">', HTML, count=1)

class QuotaLabHandler(BaseHTTPRequestHandler):
    state: QuotaLabState
    server_version = "QuotaLab/0.9.0-orchestrator-preview"

    def log_message(self, fmt: str, *args: Any) -> None:
        logging.info("HTTP %s - %s", self.address_string(), fmt % args)

    def _json(self, payload: Any, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            logging.debug("Local UI client closed its request before the response completed")

    def _same_origin(self) -> bool:
        host=urlparse('http://'+self.headers.get('Host',''))
        if host.hostname not in {'127.0.0.1','localhost'} or host.port!=self.server.server_port:return False
        if self.headers.get('Sec-Fetch-Site') in {'cross-site','same-site'}:return False
        if self.headers.get('Content-Type','').split(';',1)[0].strip().lower()!='application/json':return False
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed = urlparse(origin)
        return parsed.scheme=='http' and parsed.hostname in {"127.0.0.1", "localhost"} and parsed.port == self.server.server_port

    def do_GET(self) -> None:
        try:
            host=urlparse('http://'+self.headers.get('Host',''))
            if host.hostname not in {'127.0.0.1','localhost'} or host.port!=self.server.server_port:
                self._json({'error':'LOCAL_HOST_REQUIRED'},403);return
        except ValueError:self._json({'error':'LOCAL_HOST_REQUIRED'},403);return
        parsed = urlparse(self.path)
        if parsed.path == "/":
            data = HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif parsed.path == "/api/status":
            self._json(self.state.public())
        elif parsed.path == "/api/orchestration":
            self._json(self.state.public()["orchestration"])
        elif parsed.path == "/api/execution":
            runtime = self.state.execution_runtime
            self._json(runtime.public() if runtime else {"engine": "UNAVAILABLE", "jobs": []})
        elif parsed.path.startswith("/api/evidence/a3/"):
            names = {"summary.json": ("a3_summary.json", "application/json"),
                     "report": ("a3_report.html", "text/html; charset=utf-8"),
                     "timeline.svg": ("a3_timeline.svg", "image/svg+xml"),
                     "pre.csv": ("a3_observations_pre.csv", "text/csv; charset=utf-8"),
                     "post.csv": ("a3_observations_post.csv", "text/csv; charset=utf-8")}
            item = names.get(parsed.path.removeprefix("/api/evidence/a3/"))
            target = EVIDENCE_DIR / "v0.8.1" / "a3" / item[0] if item else None
            if target is None or not target.is_file():
                self._json({"error": "NOT_FOUND"}, 404)
                return
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", item[1])
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif parsed.path == "/api/instance":
            self._json(self.state.instance_info())
        elif parsed.path == "/api/health":
            self._json(self.state.health())
        elif parsed.path == "/api/provider":
            public = self.state.public()
            self._json({**public.get("provider", {}), "status": public.get("diagnostics", {})})
        elif parsed.path == "/api/codex/status":
            public = self.state.public()
            cli = public.get("codex_cli", {})
            self._json({**cli, "connected": public.get("app_server") in {"CONNECTED", "MOCK"}, "authenticated": bool(public.get("account"))})
        elif parsed.path == "/api/settings":
            public = self.state.public()
            self._json({"codexPathConfigured": bool(self.state.settings.get("codex_path")), "codexPath": public.get("codex_cli", {}).get("path"), "port": self.server.server_port, "dataDirectory": str(ROOT), "language": "browser"})
        elif parsed.path == "/api/logs":
            text = ""
            try:
                text = "\n".join(LOG_FILE.read_text(encoding="utf-8").splitlines()[-200:])
            except OSError:
                pass
            data = sanitize_text(text).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif parsed.path == "/api/experiments/export":
            experiment_id = parse_qs(parsed.query).get("id", [""])[0]
            path = self.state.experiment_file(experiment_id)
            if not path:
                self._json({"error": "NOT_FOUND"}, 404)
                return
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif parsed.path == "/api/experiments/export.csv":
            data = self.state.experiments_csv().encode("utf-8-sig")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="quotalab_anchor_experiments.csv"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif parsed.path == "/api/report.svg":
            data = self.state.experiment_chart_svg().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif parsed.path == "/api/window-state.svg":
            data = self.state.window_state_chart_svg().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif parsed.path == "/api/window-state/export.csv":
            data = self.state.window_state_csv().encode("utf-8-sig")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="quotalab_window_state_observations.csv"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif parsed.path == "/api/report":
            data = self.state.experiment_report_html().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self._json({"error": "NOT_FOUND"}, 404)

    def do_POST(self) -> None:
        if not self._same_origin():
            self._json({"error": "INVALID_ORIGIN"}, 403)
            return
        orchestration_routes = {"/api/orchestration/"+a for a in ("demand", "recommend", "preferences", "profile", "signal", "signals-fetch", "handoff", "handoff-confirm", "handoff-cancel", "verify", "demo", "demo-clear", "cloud-create", "cloud-handoff", "cloud-scheduled", "cloud-executed", "cloud-verify", "task-instruction", "action-check", "reminder", "reminder-dismiss", "reminder-displayed")}
        evolution_routes={'/api/evolution/'+a for a in ('onboarding','onboarding-complete','onboarding-restart','outcome','update-source','update-check','update-offer','update-download','storage-preview','storage-clean')}
        orchestration_routes |= evolution_routes
        orchestration_routes |= {'/api/desktop-notifications/settings', '/api/desktop-notifications/test'}
        if self.path not in orchestration_routes | {"/api/experiments/start", "/api/experiments/trigger", "/api/experiments/cancel", "/api/probe/start", "/api/probe/stop", "/api/dispatch-tests/mock", "/api/plan", "/api/plan/intensity", "/api/codex/rediscover", "/api/codex/reconnect", "/api/settings/codex-path", "/api/setup/complete", "/api/shutdown", "/api/execution/jobs", "/api/execution/authorize", "/api/execution/cancel", "/api/execution/settings", "/api/execution/wake-test"}:
            self._json({"error": "NOT_FOUND"}, 404)
            return
        try:
            length = safe_int(self.headers.get("Content-Length"), minimum=0) or 0
            if length > (32768 if self.path in orchestration_routes else 4096):
                self._json({"error": "PAYLOAD_TOO_LARGE"}, 413)
                return
            body = self.rfile.read(length) if length else b"{}"
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("OBJECT_PAYLOAD_REQUIRED")
            if self.path == '/api/desktop-notifications/settings':
                self._json(self.state.desktop_notifications.configure(payload))
                return
            if self.path == '/api/desktop-notifications/test':
                self._json(self.state.desktop_notifications.test_notification())
                return
            if self.path in orchestration_routes:
                if self.path in evolution_routes:
                    self._json(self.state.evolution.handle(self.path.rsplit('/',1)[-1],payload,self.state));return
                self._json(self.state.orchestrator.handle(self.path.rsplit("/", 1)[-1], payload, self.state.public()))
                return
            if self.path.startswith("/api/execution/"):
                runtime = self.state.execution_runtime
                if runtime is None:
                    self._json({"error": "EXECUTION_UNAVAILABLE"}, 503)
                    return
                if self.path == "/api/execution/jobs":
                    self._json(runtime.create(payload), 201)
                elif self.path == "/api/execution/authorize":
                    self._json(runtime.authorize(str(payload.get("jobId", ""))))
                elif self.path == "/api/execution/cancel":
                    self._json(runtime.cancel(str(payload.get("jobId", ""))))
                elif self.path == "/api/execution/settings":
                    self._json(runtime.save_settings(payload))
                else:
                    self._json(runtime.wake_test())
                return
            if self.path == "/api/shutdown":
                self._json({"status": "stopping"})
                self.wfile.flush()
                self.state.stop_event.set()
                threading.Thread(target=self.server.shutdown, name="http-shutdown", daemon=True).start()
            elif self.path in {"/api/codex/rediscover", "/api/codex/reconnect"}:
                self._json(self.state.request_codex_rediscovery(), 202)
            elif self.path == "/api/settings/codex-path":
                self._json(self.state.configure_codex_path(payload.get("codex_path")))
            elif self.path == "/api/setup/complete":
                self._json(self.state.complete_setup())
            elif self.path == "/api/dispatch-tests/mock":
                self._json(self.state.record_mock_dispatch_test(), 201)
            elif self.path == "/api/probe/start":
                self._json(self.state.start_probe(payload), 201)
            elif self.path == "/api/probe/stop":
                self._json(self.state.stop_probe())
            elif self.path == "/api/experiments/start":
                experiment = self.state.start_experiment()
                self._json(experiment, 201)
            elif self.path == "/api/experiments/trigger":
                self._json(self.state.mark_experiment_triggered())
            elif self.path == "/api/experiments/cancel":
                self._json(self.state.cancel_experiment())
            elif self.path == "/api/plan":
                self._json(self.state.save_plan(payload))
            else:
                self._json(self.state.plan_intensity_sessions(payload), 201)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json({"error": "INVALID_JSON"}, 400)
        except (ValueError, TypeError, OverflowError) as exc:
            self._json({"error": str(exc)}, 409)


class ExclusiveThreadingHTTPServer(ThreadingHTTPServer):
    """Prevent multiple QuotaLab processes from sharing one listening port."""

    allow_reuse_address = False

    def server_bind(self) -> None:
        if self.server_address[0] not in {'127.0.0.1','localhost'}:raise ValueError('LOOPBACK_ONLY')
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def configured_codex_path(command_line_path: str | None) -> str | None:
    """Load an optional user override without ever modifying PATH."""
    if command_line_path:
        return command_line_path
    if os.environ.get("QUOTALAB_CODEX_PATH"):
        return os.environ["QUOTALAB_CODEX_PATH"]
    try:
        settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = settings.get("codex_path") if isinstance(settings, dict) else None
    return value if isinstance(value, str) and value.strip() else None


def configure_logging(debug: bool) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.FileHandler(LOG_FILE, encoding="utf-8")]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    class PrivacyFilter(logging.Filter):
        def filter(self, record):
            record.msg=redact_text(record.getMessage());record.args=()
            if record.exc_info:
                record.exc_text=redact_text(logging.Formatter().formatException(record.exc_info));record.exc_info=None
            return True
    for handler in handlers:handler.addFilter(PrivacyFilter())
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(threadName)s %(message)s",
        handlers=handlers,
        force=True,
    )


def run_doctor(*, host: str, explicit_codex_path: str | None, mock: bool = False) -> dict[str, Any]:
    """Run safe read-only diagnostics; never starts a Codex thread or turn."""
    existing = find_existing_instance(host)
    result: dict[str, Any] = {
        "quotaLabVersion": APP_VERSION,
        "localAgent": existing or "not-running",
        "dataDirectory": str(ROOT),
        "schedulerLockPresent": (RUNTIME_DIR / "scheduler.lock").is_file(),
        "modelTaskSent": False,
    }
    if mock:
        result.update({"codexCli": {"found": False, "source": "mock", "version": "Mock mode"}, "appServer": "not-tested", "auth": "mock", "quotaApi": "mock"})
        return result
    resolver = CodexExecutableResolver(configured_codex_path(explicit_codex_path))
    executable = resolver.resolve(force=True)
    result["codexCli"] = {
        "found": executable is not None,
        "source": executable.source if executable else None,
        "version": executable.version if executable else None,
        "path": "[redacted]\\" + executable.path.name if executable else None,
        "attempts": resolver.attempts,
    }
    if not executable:
        result.update({"appServer": "not-started", "auth": "unavailable", "quotaApi": "unavailable"})
        return result
    client = CodexAppServer(lambda *_args: None, executable.path, False)
    try:
        client.start()
        result["appServer"] = "connected"
        account = client.request("account/read", {"refreshToken": False})
        authenticated = not (isinstance(account, dict) and account.get("requiresOpenaiAuth") and not account.get("account"))
        result["auth"] = "connected" if authenticated else "required"
        if authenticated:
            limits = client.request("account/rateLimits/read", {"excludeResetCreditDetails": False})
            parsed=parse_rate_limit_response(limits)
            result['bucketCount']=len(parsed.get('windows',[]))
            result["quotaApi"] = "available" if parsed.get("windows") else "no-windows"
            append_calibration(DATA_DIR,{'app_server':'CONNECTED','windows':parsed.get('windows',[])})
        else:
            result["quotaApi"] = "blocked-by-auth"
    except Exception as exc:
        result.setdefault("appServer", "failed")
        result["error"] = sanitize_text(str(exc))
    finally:
        client.stop()
    return result


def find_preview_instance(host: str) -> dict[str, Any] | None:
    for port in range(DEFAULT_PORT, DEFAULT_PORT + 30):
        found = find_existing_instance(host, ports=[port])
        if found and str(found.get("version", "")).startswith("0.14.3"):
            return found
    return None


def stop_existing_instance(host: str) -> bool:
    existing = find_preview_instance(host)
    if not existing:
        return False
    request = Request(str(existing.get("url") or f"http://{host}:{existing['port']}") + "/api/shutdown", data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=3) as response:
            return response.status == 200
    except OSError:
        return False


def open_dashboard_once(url: str, *, cooldown: float = 12.0) -> bool:
    """Coalesce rapid launches across processes while allowing a later reopen."""
    guard = WindowsUserMutex("DashboardOpen-v0143")
    if not guard.acquire():
        return False
    try:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        marker = RUNTIME_DIR / "dashboard-open.json"
        now = time.time()
        try:
            previous = json.loads(marker.read_text(encoding="utf-8"))
            if not isinstance(previous, dict):
                previous = {}
            elapsed = now - float(previous.get("openedAt", 0))
            if previous.get("url") == url and 0 <= elapsed < cooldown:
                return False
        except (OSError, ValueError, TypeError):
            pass
        opened = webbrowser.open(url)
        if opened:
            marker.write_text(json.dumps({"url": url, "openedAt": now}), encoding="utf-8")
        return bool(opened)
    except OSError:
        logging.warning("Dashboard reopen marker unavailable")
        return False
    finally:
        guard.release()


def open_dashboard_when_ready(url: str, stop_event: threading.Event, *, timeout: float = 10.0) -> bool:
    """Thin-launcher readiness gate: open the browser only after Local Agent health responds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not stop_event.is_set():
        try:
            with urlopen(url + "/api/health", timeout=0.5) as response:
                if response.status == 200:
                    open_dashboard_once(url)
                    return True
        except OSError:
            pass
        stop_event.wait(0.2)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local read-only Codex quota observer")
    parser.add_argument("command", nargs="?", choices=["start", "open", "status", "stop", "doctor", "release-smoke", "delete-owned-data"], default="start")
    parser.add_argument('--delete-data-root');parser.add_argument('--delete-confirmation')
    parser.add_argument('--report-dir',help='Privacy-safe smoke report directory')
    parser.add_argument("--mock", action="store_true", help="Use clearly labelled mock quota data")
    parser.add_argument("--debug", action="store_true", help="Log sanitized JSON-RPC payloads")
    parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "localhost"])
    parser.add_argument("--port", type=int, default=None, help=f"Preferred local port (default {DEFAULT_PORT})")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the local dashboard")
    parser.add_argument("--status", action="store_true", help="Show the running QuotaLab instance and exit")
    parser.add_argument("--open", action="store_true", dest="open_existing", help="Open the running Dashboard without starting a scheduler")
    parser.add_argument("--console", action="store_true", help="Show a console window for diagnostics")
    parser.add_argument("--doctor", action="store_true", help="Run safe Local Agent and Codex diagnostics")
    parser.add_argument("--codex-path", help="Optional path to codex.exe/codex.cmd; does not change PATH")
    args = parser.parse_args(argv)
    if args.command=='delete-owned-data':
        from storage_manager import delete_owned_data
        guard=WindowsUserMutex('Instance-v0143')
        if not guard.acquire():return 3
        try:
            delete_owned_data(args.delete_data_root or ROOT,args.delete_confirmation);return 0
        except (ValueError,OSError):return 2
        finally:guard.release()
    if args.command == "open":
        args.open_existing = True
    elif args.command == "status":
        args.status = True
    elif args.doctor:
        args.command = "doctor"
    if args.debug or args.console or args.status or args.command == "doctor":
        ensure_console()
    configure_logging(args.debug)
    if args.command=='release-smoke':
        report=native_smoke(sys.modules[__name__],args.report_dir or ROOT/'release-smoke',args.mock,args.port or 18796)
        return 1 if report.get('validationLevel')=='LOCAL_MACHINE_FAIL' else 0
    if args.command == "doctor":
        raw=run_doctor(host=args.host, explicit_codex_path=args.codex_path, mock=args.mock)
        print(json.dumps(smoke_summary(raw,APP_VERSION,raw.get('bucketCount',0)),ensure_ascii=False,indent=2))
        return 0
    if args.command == "stop":
        stopped = stop_existing_instance(args.host)
        print("QuotaLab stopping." if stopped else "QuotaLab is not running.")
        return 0 if stopped else 1
    mode = "mock" if args.mock else "live"
    requested_port = args.port
    instance_mutex = WindowsUserMutex("Instance-v0143")
    instance_lock: OwnershipLock | None = None
    scheduler_lock: OwnershipLock | None = None
    heartbeat: LockHeartbeat | None = None
    server: ExclusiveThreadingHTTPServer | None = None
    state: QuotaLabState | None = None
    monitor: MockMonitor | LiveMonitor | None = None
    planner: UsagePlanner | None = None
    instance_id = uuid.uuid4().hex

    try:
        if args.status or args.open_existing:
            existing = find_preview_instance(args.host)
            if not existing:
                message = "QuotaLab is not running."
                print(message)
                if args.open_existing:
                    show_windows_message("QuotaLab", message, error=True)
                return 1
            url = str(existing.get("url") or f"http://{args.host}:{existing['port']}")
            if args.status:
                print(json.dumps(existing, ensure_ascii=False, indent=2))
            if args.open_existing:
                webbrowser.open(url)
            return 0

        # Mock tests with an explicit port are deliberately isolated from a live user instance.
        requested_port = requested_port if requested_port is not None else DEFAULT_PORT
        scan_existing = False
        resolution = resolve_port(args.host, requested_port, scan_existing=scan_existing)
        if resolution.action == "existing":
            existing = resolution.instance or {}
            if not str(existing.get("version", "")).startswith("0.14.3"):
                raise RuntimeError("Port is occupied by a different QuotaLab version")
            url = str(existing.get("url") or f"http://{args.host}:{resolution.port}")
            logging.info("QuotaLab already running at %s (version %s)", url, existing.get("version", "unknown"))
            print(f"QuotaLab already running at {url}")
            if not args.no_browser:
                open_dashboard_once(url)
            return 0
        if resolution.action == "error" or resolution.port is None:
            raise RuntimeError(resolution.error or "No local port is available")

        if not instance_mutex.acquire():
            existing = find_preview_instance(args.host)
            if existing:
                url = str(existing.get("url") or f"http://{args.host}:{existing['port']}")
                if not args.no_browser:
                    open_dashboard_once(url)
                print(f"QuotaLab already running at {url}")
                return 0
            logging.info("QuotaLab is already starting; reusing the launch in progress")
            return 0

        # This preview has a version-isolated data root. Never import or mutate an
        # earlier preview's personal plans/history during candidate validation.
        from product_migration_v014 import migrate_to_v014
        migration = migrate_to_v014(None, ROOT)
        logging.info('schema15 isolated preview status=%s',migration['status'])

        instance_lock = OwnershipLock(RUNTIME_DIR / "instance.lock", "instance")
        if not instance_lock.acquire():
            raise RuntimeError("Another QuotaLab process owns the runtime instance lock")

        port = resolution.port
        server = ExclusiveThreadingHTTPServer((args.host, port), QuotaLabHandler)
        discovery: CodexExecutable | None = None
        resolver: CodexExecutableResolver | None = None
        if args.mock:
            version, source = "Mock mode", "Mock mode"
        else:
            resolver = CodexExecutableResolver(configured_codex_path(args.codex_path))
            discovery = resolver.resolve(force=True)
            attempts = resolver.attempts
            version = discovery.version if discovery else "Unavailable"
            source = discovery.source if discovery else None
            logging.info("Codex discovery attempts: %s", attempts)
        logging.info("QuotaLab %s starting; Codex=%s; source=%s; mode=%s; port=%s", APP_VERSION, version, source, mode, port)
        state = QuotaLabState(mock=args.mock, debug=args.debug, codex_version=version, codex_source=source)
        if resolver:
            state.set_codex_discovery(discovery, resolver.attempts)
        scheduler_lock = OwnershipLock(RUNTIME_DIR / "scheduler.lock", "scheduler")
        scheduler_owner = scheduler_lock.acquire()
        state.set_runtime(instance_id=instance_id, port=port, scheduler_owner=scheduler_owner)
        agent_exe = Path(sys.executable if getattr(sys, "frozen", False) else __file__)
        state.execution_runtime = ExecutionRuntime(state, DATA_DIR / "lab-fallback", agent_exe, port, scheduler_owner=scheduler_owner)
        QuotaLabHandler.state = state
        state._write_report_artifacts()

        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        INSTANCE_FILE.write_text(json.dumps(state.instance_info(), ensure_ascii=False, indent=2), encoding="utf-8")
        monitor = MockMonitor(state) if args.mock else LiveMonitor(state, discovery.path if discovery else None, resolver)
        RecommendationRefreshLoop(state).start()
        state.desktop_notifications.start()
        monitor.start()
        if scheduler_owner:
            planner = UsagePlanner(state, execution_owner=True)
            planner.start()
            state.execution_runtime.start()
        else:
            logging.warning("Scheduler ownership unavailable; Dashboard is read-only")
        owned_locks = [instance_lock] + ([scheduler_lock] if scheduler_owner else [])
        heartbeat = LockHeartbeat(owned_locks, state.stop_event, callback=state.touch_runtime)
        heartbeat.start()

        url = f"http://{args.host}:{server.server_port}"
        logging.info("Dashboard ready at %s", url)
        print(f"QuotaLab running at {url}")
        if not args.no_browser:
            browser_thread = threading.Thread(target=open_dashboard_when_ready, args=(url, state.stop_event), name="thin-launcher", daemon=True)
            browser_thread.start()
        server.serve_forever(poll_interval=0.5)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        logging.error("Fatal startup/runtime error: %s\n%s", sanitize_text(str(exc)), traceback.format_exc())
        log_path = write_fatal_log(mode=mode, port=requested_port, exc=exc)
        message = f"QuotaLab could not start.\n\nReason:\n{sanitize_text(str(exc))}\n\nLog:\n{log_path}"
        print(message, file=sys.stderr or sys.stdout)
        show_windows_message("QuotaLab", message, error=True)
        return 2
    finally:
        if state:
            state.stop_event.set()
            if state.desktop_notifications.is_alive():
                state.desktop_notifications.join(timeout=4)
        if server:
            server.server_close()
        if isinstance(monitor, LiveMonitor) and monitor.client:
            monitor.client.stop()
        if monitor:
            monitor.join(timeout=4)
        if planner:
            planner.join(timeout=2)
        if heartbeat:
            heartbeat.join(timeout=2)
        if INSTANCE_FILE.is_file():
            try:
                saved = json.loads(INSTANCE_FILE.read_text(encoding="utf-8"))
                if saved.get("instanceId") == instance_id:
                    INSTANCE_FILE.unlink(missing_ok=True)
            except (OSError, json.JSONDecodeError):
                pass
        if scheduler_lock:
            scheduler_lock.release()
        if instance_lock:
            instance_lock.release()
        instance_mutex.release()
        logging.info("QuotaLab stopped")


if __name__ == "__main__":
    raise SystemExit(main())
