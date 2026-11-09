"""Persistent exact-path selection; changes require explicit reviewed confirmation."""
import json
from pathlib import Path
from .config import Blocked
from .validation import reject_link
from .recovery import atomic_json

def read(config):
    gitdir=config.repository/".git"
    reject_link(gitdir)
    if not gitdir.is_dir():raise Blocked("File selection requires a regular Git checkout")
    runtime=gitdir/"night-bandit"
    if runtime.exists():reject_link(runtime)
    path=runtime/"selection.json"
    if not path.exists():return None
    reject_link(path)
    try:
        data=json.loads(path.read_text())
        if (data["repository"]!=str(config.repository) or data["remote"]!=config.expected_remote_url
            or data["branch"]!=config.branch or not isinstance(data["file"],str)):
            raise ValueError()
        from dataclasses import replace
        replace(config,files=(data["file"],))
        return data["file"]
    except (KeyError,ValueError,TypeError):
        raise Blocked("Saved file selection does not match this repository; review explicitly") from None

def validate(config):
    if not config.files:raise Blocked("Choose and confirm one file before synchronizing")
    locked=read(config)
    if locked is not None and config.files!=(locked,):
        raise Blocked("File selection is locked; explicitly review a change before synchronizing")

def save(config,expected_previous):
    if not config.files:raise Blocked("No file selected")
    from .git_sync import exclusive
    runtime=config.repository/".git/night-bandit"
    if runtime.exists():reject_link(runtime)
    runtime.mkdir(exist_ok=True)
    with exclusive(runtime/"operation.lock"):
        if read(config)!=expected_previous:
            raise Blocked("Saved selection changed; review again")
        # Never switch away from an interrupted transaction.
        for name in ("transaction.json","initial-onboarding.json"):
            journal=runtime/name
            if journal.exists():
                reject_link(journal)
                state=json.loads(journal.read_text()).get("stage")
                if state not in ("complete","complete_local","recovered_no_commit","recovered_local"):
                    raise Blocked("Resolve interrupted operation before changing selected file")
        atomic_json(runtime/"selection.json",{"repository":str(config.repository),"remote":config.expected_remote_url,
                    "branch":config.branch,"file":config.files[0]})
