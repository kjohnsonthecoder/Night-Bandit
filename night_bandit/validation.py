"""Bounded exact-byte snapshots. No content appears in diagnostics."""
import hashlib
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from .config import Blocked, Config

class SnapshotChanged(Blocked):
    """Read-time instability only; never permission to retry a Git transaction."""


MAX_BYTES = 8 * 1024 * 1024


def reject_link(path: Path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise Blocked("symlinks and Windows reparse points are unsupported")
    return info


@dataclass(frozen=True)
class Snapshot:
    digest: str
    data: bytes
    signature: tuple


def snapshot(config: Config, name=None):
    if not config.files: raise Blocked("Choose and confirm one file before synchronizing")
    name=config.files[0] if name is None else name
    if name not in config.files:
        raise Blocked("path is outside the exact allowlist")
    path = config.repository / name
    parent=config.repository
    for part in Path(name).parts[:-1]:
        parent=parent/part
        reject_link(parent)
    before = reject_link(path)
    if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_BYTES:
        raise Blocked("file must be a regular UTF-8 file of at most 8 MiB")
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    after = reject_link(path)
    signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if signature(before) != signature(after) or len(data) != before.st_size:
        raise SnapshotChanged("file changed while being read")
    if not data or b"\0" in data:
        raise Blocked("file is empty or binary")
    try:
        data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise Blocked("file is not UTF-8") from None
    # A tripwire, not a complete secret scanner or a substitute for owner review.
    if re.search(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})", data):
        raise Blocked("possible credential material; review locally")
    return Snapshot(hashlib.sha256(data).hexdigest(), data, signature(after))
