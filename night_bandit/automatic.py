"""Explicitly started single-file automatic sync; no service or startup task."""
import hashlib
import json
import time
from dataclasses import asdict,dataclass
from .config import Blocked
from .validation import snapshot, SnapshotChanged
from .watcher import ChangeDetector
from .git_sync import exclusive
from . import selection

def binding(config):
    return hashlib.sha256(json.dumps(asdict(config),sort_keys=True,default=str).encode()).hexdigest()

@dataclass(frozen=True)
class AutomaticAuthorization:
    config_binding: str
    selected_file: str
    def validate(self,config):
        if not config.enable_push or binding(config)!=self.config_binding:
            raise Blocked("Automatic session scope changed; stop and authorize again")
        if selection.read(config)!=self.selected_file or config.files!=(self.selected_file,):
            raise Blocked("Automatic session requires the exact confirmed file selection")

class AutomaticSession:
    def __init__(self,engine,approved=False):
        if approved is not True:raise Blocked("Confirm automatic pushes for this exact file and repository first")
        self.engine=engine
        if not engine.config.enable_push:raise Blocked("Automatic session requires explicit push opt-in")
        name=selection.read(engine.config)
        if not name or engine.config.files!=(name,):raise Blocked("Choose and lock one file before starting automatic sync")
        self.authorization=AutomaticAuthorization(binding(engine.config),name)
        self.authorization.validate(engine.config)

    def run(self,stop,notify=None):
        s=self.engine;notify=notify or (lambda record:None)
        def state(phase,**fields):notify(dict(phase=phase,**fields))
        # One listener per checkout. Never remove someone else's stale/active lock.
        try:
            with exclusive(s.runtime/"listener.lock"):
                self.authorization.validate(s.config)
                state("checking",message="Checking the selected file and GitHub connection")
                initial=snapshot(s.config)
                baseline=s.run(initial)
                if baseline["status"] not in ("unchanged","dry_run"):
                    raise Blocked("Finish initial repository onboarding before starting automatic sync")
                detector=ChangeDetector(max(2,s.config.debounce_seconds),max(2,s.config.stable_seconds))
                current=snapshot(s.config)
                detector.observe(current.digest,time.monotonic())
                if baseline["status"]=="unchanged":detector.emitted=initial.digest
                missing_since=None
                s.events.emit("automatic_started",path=s.config.files[0],mode="automatic",push_enabled=True,
                              reason="Explicit session authorization; listener continues in tray; Stop, Exit or a blocker ends this session")
                state("listening",message="Listening — save your selected file to sync it automatically")
                while not stop.is_set():
                    self.authorization.validate(s.config)
                    try:current=snapshot(s.config);missing_since=None
                    # Only retry unstable POLLING reads before any transaction.
                    # Sync.run failures below are never caught here or retried.
                    except (FileNotFoundError,PermissionError,SnapshotChanged):
                        if missing_since is None:missing_since=time.monotonic()
                        if time.monotonic()-missing_since>max(5,detector.delay*2):
                            raise Blocked("Selected file did not stabilize or is unavailable; automatic sync stopped")
                        detector=ChangeDetector(detector.delay,detector.delay)
                        state("waiting",message="Waiting for the selected file to finish saving")
                        if stop.wait(s.config.poll_seconds):break
                        continue
                    before=detector.candidate
                    ready=detector.observe(current.digest,time.monotonic())
                    if current.digest!=before:state("waiting",message="Change detected — waiting for the save to settle")
                    if ready:
                        if stop.is_set():break
                        state("syncing",message="Saving a local commit and pushing to GitHub")
                        result=s.run(current,apply=True,push=True,automatic_authorization=self.authorization)
                        state("synced",message="Changes verified on GitHub" if result["status"]=="pushed" else "Already up to date",**result)
                        state("listening",message="Listening — save your selected file to sync it automatically")
                    if stop.wait(s.config.poll_seconds):break
                s.events.emit("automatic_stopped",mode="automatic",reason="Owner stopped this listener session")
                state("stopped",message="Automatic sync stopped")
                return {"status":"stopped"}
        except (Blocked,OSError) as error:
            reason=str(error) if isinstance(error,Blocked) else "File or Git operation failed; inspect activity before restarting"
            s.events.emit("automatic_blocked",severity="ERROR",mode="automatic",reason=reason)
            state("blocked",message=reason)
            raise
