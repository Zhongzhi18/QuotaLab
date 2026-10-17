"""Provider boundary for QuotaLab's local-agent runtime."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable

from codex_discovery import CodexExecutable, CodexExecutableResolver


class ProviderAdapter(ABC):
    provider_id: str
    display_name: str

    @abstractmethod
    def detect(self, *, force: bool = False) -> CodexExecutable | None: ...

    @abstractmethod
    def connect(self, on_notification, *, debug: bool = False) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def get_account(self) -> Any: ...

    @abstractmethod
    def get_rate_limits(self) -> Any: ...

    @abstractmethod
    def health(self) -> dict[str, Any]: ...

    @abstractmethod
    def execute_anchor(self, *, cwd: Path, prompt: str) -> dict[str, Any]: ...


class CodexProviderAdapter(ProviderAdapter):
    """Current Codex implementation; future providers can implement the same contract."""

    provider_id = "openai-codex"
    display_name = "OpenAI Codex"

    def __init__(self, resolver: CodexExecutableResolver | None, client_factory: Callable | None = None):
        self.resolver = resolver
        self.client_factory = client_factory
        self.executable: CodexExecutable | None = None
        self.client = None
        self.last_error: str | None = None

    def detect(self, *, force: bool = False) -> CodexExecutable | None:
        if not self.resolver:
            return self.executable
        self.executable = self.resolver.resolve(force=force)
        return self.executable

    def connect(self, on_notification, *, debug: bool = False) -> None:
        detected = self.executable or self.detect()
        if not detected or not self.client_factory:
            raise RuntimeError("CODEX_NOT_FOUND")
        self.client = self.client_factory(on_notification, detected.path, debug)
        self.client.start()
        self.last_error = None

    def disconnect(self) -> None:
        if self.client:
            self.client.stop()
        self.client = None

    def get_account(self) -> Any:
        if not self.client:
            raise RuntimeError("APP_SERVER_DISCONNECTED")
        return self.client.request("account/read", {"refreshToken": False})

    def get_rate_limits(self) -> Any:
        if not self.client:
            raise RuntimeError("APP_SERVER_DISCONNECTED")
        return self.client.request("account/rateLimits/read", {"excludeResetCreditDetails": False})

    def health(self) -> dict[str, Any]:
        connected = bool(self.client and not self.client.closed.is_set())
        return {
            "provider": self.provider_id,
            "displayName": self.display_name,
            "detected": self.executable is not None,
            "connected": connected,
            "version": self.executable.version if self.executable else None,
            "source": self.executable.source if self.executable else None,
            "error": self.last_error,
        }

    def execute_anchor(self, *, cwd: Path, prompt: str) -> dict[str, Any]:
        if not self.client or self.client.closed.is_set():
            raise RuntimeError("APP_SERVER_DISCONNECTED")
        thread_result = self.client.request("thread/start", {
            "cwd": str(cwd), "ephemeral": True, "approvalPolicy": "never", "sandbox": "read-only",
        })
        thread = thread_result.get("thread") if isinstance(thread_result, dict) else None
        thread_id = thread.get("id") if isinstance(thread, dict) else None
        if not isinstance(thread_id, str):
            raise RuntimeError("THREAD_START_FAILED")
        turn_result = self.client.request("turn/start", {
            "threadId": thread_id,
            "input": [{"type": "text", "text": prompt}],
            "approvalPolicy": "never",
        })
        turn = turn_result.get("turn") if isinstance(turn_result, dict) else None
        turn_id = turn.get("id") if isinstance(turn, dict) else None
        if not isinstance(turn_id, str):
            raise RuntimeError("TURN_START_FAILED")
        return {"thread_id": thread_id, "turn_id": turn_id}
