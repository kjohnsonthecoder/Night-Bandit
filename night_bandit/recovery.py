"""Durable local transaction evidence and explicit, non-destructive recovery."""
import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from .config import Blocked
from .validation import reject_link, snapshot
from . import exclusions

def digest(path):
    reject_link(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()

def atomic_json(path, data):
    if path.exists(): reject_link(path)
    fd,name=tempfile.mkstemp(prefix=path.name+"-",dir=path.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as stream:
            json.dump(data,stream,sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name): os.unlink(name)

def process_alive(pid):
    if not isinstance(pid,int) or pid<=0: return True
    if os.name=="nt":
        import ctypes
        kernel=ctypes.WinDLL("kernel32",use_last_error=True)
        kernel.OpenProcess.restype=ctypes.c_void_p
        kernel.OpenProcess.argtypes=[ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong]
        kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_ulong]
        kernel.CloseHandle.argtypes=[ctypes.c_void_p]
        handle=kernel.OpenProcess(0x00100000,False,pid)
        if not handle:
            return ctypes.get_last_error()!=87
        try: return kernel.WaitForSingleObject(handle,0)!=0
        finally: kernel.CloseHandle(handle)
    try: os.kill(pid,0); return True
    except ProcessLookupError: return False
    except PermissionError: return True

class Journal:
    terminal={"complete","complete_local","recovered_no_commit","recovered_local"}
    def __init__(self,sync):
        self.sync=sync
        self.path=sync.runtime/"transaction.json"
        self.data={}
    def load(self):
        if not self.path.exists(): return None
        reject_link(self.path)
        try: return json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError,OSError): raise Blocked("transaction journal unreadable; inspect recovery") from None
    def pending(self):
        data=self.load()
        return data if data and data.get("stage") not in self.terminal else None
    def update(self,stage,**fields):
        self.data.update(fields)
        self.data.update(stage=stage,updated_at=datetime.now(timezone.utc).isoformat())
        atomic_json(self.path,self.data)
    def begin(self,current):
        s=self.sync
        self.data={"schema":1,"pid":os.getpid(),"branch":s.config.branch,
                   "base":s.git.text("rev-parse","HEAD"),"file_sha256":current.digest,
                   "old_index_sha256":digest(s.gitdir/"index"),
                   "operation_lock_sha256":digest(s.runtime/"operation.lock"),
                   "index_lock_sha256":digest(s.gitdir/"index.lock")}
        backup=s.runtime/"original-index.bin"
        if backup.exists(): reject_link(backup)
        shutil.copyfile(s.gitdir/"index",backup)
        with backup.open("rb+") as stream: os.fsync(stream.fileno())
        self.update("validating")
    def staged(self,index,commit,tree):
        backup=self.sync.runtime/"prepared-index.bin"
        if backup.exists(): reject_link(backup)
        shutil.copyfile(index,backup)
        with backup.open("rb+") as stream: os.fsync(stream.fileno())
        self.update("commit_ready",commit=commit,tree=tree,new_index_sha256=digest(backup))
    def review(self):
        data=self.load()
        if not data: raise Blocked("no transaction journal is available")
        return data,digest(self.path)
    def repair(self,approved_hash):
        from .git_sync import exclusive
        s=self.sync
        with exclusive(s.runtime/"recovery.lock"):
            data,review_hash=self.review()
            if review_hash!=approved_hash: raise Blocked("recovery journal changed since review")
            if data.get("stage") in self.terminal: raise Blocked("journal already completed or recovered")
            for path,key in ((s.runtime/"operation.lock","operation_lock_sha256"),
                             (s.gitdir/"index.lock","index_lock_sha256")):
                if path.exists():
                    if digest(path)!=data.get(key): raise Blocked("foreign lock; recovery will not remove it")
                    try: pid=json.loads(path.read_text())["pid"]
                    except (ValueError,KeyError,TypeError): raise Blocked("legacy or unknown lock; inspect manually") from None
                    if process_alive(pid): raise Blocked("lock owner may still be running")
            for path,key in ((s.runtime/"operation.lock","operation_lock_sha256"),
                             (s.gitdir/"index.lock","index_lock_sha256")):
                if path.exists():
                    if digest(path)!=data[key]: raise Blocked("lock changed during recovery")
                    path.unlink()
            with exclusive(s.runtime/"operation.lock"),exclusive(s.gitdir/"index.lock"):
                if digest(self.path)!=approved_hash: raise Blocked("journal changed during recovery")
                if s.git.text("symbolic-ref","--short","HEAD")!=data["branch"]:
                    raise Blocked("branch differs from interrupted transaction")
                if snapshot(s.config).digest!=data["file_sha256"]:
                    raise Blocked("file changed after interruption; manual review required")
                exclusions.validate(s.config,s.git)
                head=s.git.text("rev-parse","HEAD")
                actual=digest(s.gitdir/"index")
                self.data=data
                if head==data["base"]:
                    if actual!=data["old_index_sha256"]:
                        raise Blocked("index changed; no automatic restoration authorized")
                    self.update("recovered_no_commit")
                elif data.get("commit") and head==data["commit"]:
                    if s.git.text("rev-parse",head+"^")!=data["base"]:
                        raise Blocked("interrupted commit parent mismatch")
                    backup=s.runtime/"prepared-index.bin"
                    if digest(backup)!=data.get("new_index_sha256"):
                        raise Blocked("prepared recovery index is missing or changed")
                    if actual==data["old_index_sha256"]:
                        fd,name=tempfile.mkstemp(prefix="recover-index-",dir=s.runtime)
                        os.close(fd)
                        try:
                            shutil.copyfile(backup,name)
                            os.replace(name,s.gitdir/"index")
                        finally:
                            if os.path.exists(name): os.unlink(name)
                    elif actual!=data["new_index_sha256"]:
                        raise Blocked("index contains unrelated work; recovery stopped")
                    self.update("recovered_local")
                else: raise Blocked("branch moved; recovery never resets or rewrites history")
                s.events.emit("recovered",severity="WARNING",commit=head,
                              reason="Local state repaired; no push performed. Review local-only commit before manual push.")
                return {"status":self.data["stage"],"commit":head}

    def push_pending(self,approved_hash):
        """Explicitly retry only the single reviewed journaled commit, never HEAD in general."""
        from .git_sync import exclusive
        s=self.sync
        if not s.config.enable_push:
            raise Blocked("pending push requires explicit push configuration and journal approval")
        with exclusive(s.runtime/"operation.lock"),exclusive(s.gitdir/"index.lock"):
            data,review_hash=self.review()
            if review_hash!=approved_hash:
                raise Blocked("recovery journal changed since review")
            commit=data.get("commit")
            if not commit or s.git.text("rev-parse","HEAD")!=commit:
                raise Blocked("only the exact journaled local commit can be retried")
            if s.git.text("rev-parse",commit+"^")!=data.get("base"):
                raise Blocked("pending commit parent differs; no push allowed")
            if snapshot(s.config).digest!=data.get("file_sha256"):
                raise Blocked("file differs from approved pending commit")
            if digest(s.gitdir/"index")!=data.get("new_index_sha256"):
                raise Blocked("pending commit index differs; repair and review first")
            if s.git.run("diff-tree","--no-commit-id","--name-only","-r","-z",commit)!=s.config.files[0].encode()+b"\0":
                raise Blocked("pending commit scope is not exactly the file")
            if s.git.text("rev-parse",commit+"^{tree}")!=data.get("tree"):
                raise Blocked("pending commit tree differs from journal")
            s.preflight(True,pending_commit=data)
            self.data=data
            current=snapshot(s.config);s._unchanged(current)
            live=s.git.text("ls-remote","--exit-code",s.config.remote,"refs/heads/"+s.config.branch).split()[0]
            self.update("push_pending")
            if live!=commit:
                if live!=data["base"]:raise Blocked("remote diverged; pending push stopped")
                s.events.emit("push_started",commit=commit,reason="Explicitly approved pending commit retry")
                s.git.run("push","--no-verify","--no-follow-tags","--recurse-submodules=no",
                          s.config.remote,commit+":refs/heads/"+s.config.branch)
            live=s.git.text("ls-remote","--exit-code",s.config.remote,"refs/heads/"+s.config.branch).split()[0]
            if live!=commit:raise Blocked("pending push remote verification failed")
            s.git.run("fetch","--no-tags","--no-recurse-submodules","--refmap=",s.config.remote,
                      "refs/heads/"+s.config.branch+":refs/night-bandit/checked")
            if (s.git.text("rev-parse","refs/night-bandit/checked")!=commit
                or s.git.text("rev-parse","refs/night-bandit/checked^{tree}")!=data["tree"]):
                raise Blocked("pending push fetched content differs")
            s._unchanged(current)
            if s.git.text("rev-parse","HEAD")!=commit:raise Blocked("branch moved after pending push")
            self.update("complete")
            atomic_json(s.state,{"sha256":current.digest,"commit":commit})
            s.events.emit("pushed",commit=commit,sha256=current.digest,verified_tree=data["tree"])
            return {"status":"pushed","commit":commit}
