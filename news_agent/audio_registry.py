"""Thread-safe registry of temporary audio file paths served to DashScope."""
import threading

_lock = threading.Lock()
_registry: dict[str, str] = {}


def register(token: str, path: str) -> None:
    with _lock:
        _registry[token] = path


def lookup(token: str) -> str | None:
    with _lock:
        return _registry.get(token)


def unregister(token: str) -> None:
    with _lock:
        _registry.pop(token, None)
