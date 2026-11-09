"""Explicit configuration; one user-selected text file, never a directory."""
import json
import math
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath


class Blocked(RuntimeError):
    """A safety precondition was not met."""


@dataclass(frozen=True)
class Config:
    repository: Path
    expected_remote_url: str
    branch: str = "main"
    remote: str = "origin"
    files: tuple[str, ...] = ()
    enable_push: bool = False
    poll_seconds: float = 1.0
    debounce_seconds: float = 2.0
    stable_seconds: float = 2.0
    git_timeout_seconds: float = 30.0
    reviewed_exclusions: dict = field(default_factory=dict)
    credential_path: str = ""

    def __post_init__(self):
        object.__setattr__(self, "repository", Path(self.repository).resolve(strict=True))
        if not self.repository.is_dir():
            raise Blocked("repository must be an existing directory")
        if not isinstance(self.files,(tuple,list)) or len(self.files)>1:
            raise Blocked("select exactly one file, never a directory or multiple files")
        object.__setattr__(self,"files",tuple(self.files))
        if self.files:
            name=self.files[0]
            if not isinstance(name,str) or not name or any(c in name for c in "\\:*?[]<>|\"\r\n\0"):
                raise Blocked("selected file must be one plain repository-relative path")
            parts=name.split("/");lower=name.lower()
            if (PurePosixPath(name).is_absolute() or any(not p or p in (".","..") or p.startswith("-")
                or p.endswith((" ",".")) or p.lower() in (".git","con","nul","aux","prn") for p in parts)
                or any(p.lower().startswith(".env") for p in parts)
                or "credential" in lower or "secret" in lower
                or lower.endswith((".db",".sqlite",".sqlite3",".pem",".key"))
                or lower.startswith("docs/locked/") or ("inbox" in [p.lower() for p in parts] and "processed" not in [p.lower() for p in parts])):
                raise Blocked("unsafe or protected file selection")
        if not isinstance(self.reviewed_exclusions, dict):
            raise Blocked("reviewed_exclusions must be an object")
        if not isinstance(self.credential_path, str):
            raise Blocked("credential_path must be a local encrypted-vault reference")
        if type(self.enable_push) is not bool:
            raise Blocked("enable_push must be a JSON boolean")
        for value in (self.poll_seconds, self.debounce_seconds, self.stable_seconds,
                      self.git_timeout_seconds):
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
                raise Blocked("timing values must be positive finite numbers")
        if not isinstance(self.branch,str) or self.remote != "origin" or not self.branch or self.branch.startswith("-"):
            raise Blocked("v0.1 requires origin and a named branch")
        if not isinstance(self.expected_remote_url, str) or not self.expected_remote_url or "\n" in self.expected_remote_url:
            raise Blocked("expected_remote_url must be explicitly configured")
        # No URL credentials or token-bearing query strings in configuration.
        if "://" in self.expected_remote_url:
            from urllib.parse import urlsplit
            url = urlsplit(self.expected_remote_url)
            if url.username or url.password or url.query or url.fragment:
                raise Blocked("remote URL must not contain credentials, query or fragment")

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        permitted = set(cls.__dataclass_fields__)
        if not isinstance(data, dict) or set(data) - permitted:
            raise Blocked("unknown configuration fields")
        if "repository" not in data or "expected_remote_url" not in data:
            raise Blocked("repository and expected_remote_url are required")
        if "files" in data:
            if not isinstance(data["files"], list):
                raise Blocked("files must be a JSON array")
            data["files"] = tuple(data["files"])
        return cls(**data)
