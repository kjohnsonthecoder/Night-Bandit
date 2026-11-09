"""Exact, explicitly reviewed exclusions; never stage or normalize excluded bytes."""
import hashlib
from datetime import datetime, timezone
from pathlib import PurePosixPath
from .config import Blocked
from .validation import reject_link

def safe_path(config, name):
    if not isinstance(name, str) or "\\" in name or ":" in name:
        raise Blocked("exclusions require plain relative paths")
    parts=PurePosixPath(name).parts
    if not parts or any(p.lower() in (".","..",".git") or any(c in p for c in "*?[]") for p in parts):
        raise Blocked("unsafe exclusion path")
    if PurePosixPath(name).is_absolute() or name in config.files:
        raise Blocked("allowlisted files cannot be excluded")
    lower=name.lower()
    if (lower.startswith("docs/locked/") or any(p.lower().startswith(".env") for p in parts)
        or lower.endswith((".db",".sqlite",".sqlite3",".pem",".key"))
        or "credential" in lower or "secret" in lower
        or ("inbox" in parts and "processed" not in parts)):
        raise Blocked("protected paths cannot be reviewed as exclusions")
    path=config.repository
    for part in parts:
        path=path/part
        reject_link(path)
    if not path.is_file() or path.stat().st_size>8*1024*1024:
        raise Blocked("exclusion must be an existing bounded regular file")
    return path

def fingerprint(config, git, name, status):
    path=safe_path(config,name)
    if status not in (" M","??"):
        raise Blocked("only unstaged regular-file changes can be excluded")
    raw=git.run("status","--porcelain=v1","-z","--untracked-files=all","--",name)
    expected=status.encode()+b" "+name.encode()+b"\0"
    if raw!=expected:
        raise Blocked("reviewed exclusion Git status changed; review again")
    before=path.stat()
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    after=path.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
        raise Blocked("excluded file changed during fingerprinting")
    return {"sha256":digest,"index_entry":git.text("ls-files","--stage","--",name),"status":status}

def approve(config, git, name, status, authority):
    if not authority.strip():
        raise Blocked("exclusion approval authority is required")
    return {**fingerprint(config,git,name,status),"approved":True,"approved_by":authority,
            "approved_at":datetime.now(timezone.utc).isoformat()}

def validate(config, git):
    for name, record in config.reviewed_exclusions.items():
        if (not isinstance(record,dict) or record.get("approved") is not True
            or not record.get("approved_by") or not record.get("approved_at")):
            raise Blocked("exclusion lacks explicit approval evidence")
        actual=fingerprint(config,git,name,record.get("status"))
        if any(actual[k]!=record.get(k) for k in ("sha256","index_entry","status")):
            raise Blocked("reviewed exclusion fingerprint changed; stop and review again")
