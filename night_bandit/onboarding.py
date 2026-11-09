"""Reviewed local-only initialization of an existing empty remote checkout."""
import hashlib
import json
import os
import tempfile
from pathlib import Path
from .config import Blocked
from .git_sync import exclusive
from .recovery import atomic_json
from .validation import snapshot, reject_link

INITIAL_CHECKPOINT = (b"# Your selected file\n\n"
                      b"Edit this text, run a dry-run, then review your changes before synchronizing.\n")

class FirstCommit:
    def __init__(self, engine):
        self.engine = engine
        self.g = engine.git
        self.c = engine.config
        self.path = engine.runtime / "initial-onboarding.json"

    def has_head(self):
        # Inspect refs, never interpret an arbitrary failed rev-parse as emptiness.
        return bool(self.g.run("for-each-ref", "--format=%(objectname)", "refs/heads/" + self.c.branch).strip())

    def boundary(self):
        from .selection import validate
        validate(self.c)
        g,c = self.g,self.c
        if Path(g.text("rev-parse", "--show-toplevel")).resolve() != c.repository:
            raise Blocked("configured directory is not the repository root")
        g.run("check-ref-format", "--branch", c.branch)
        if g.text("symbolic-ref", "--short", "HEAD") != c.branch:
            raise Blocked("unexpected branch or detached HEAD")
        for args in (("remote","get-url","--all",c.remote), ("remote","get-url","--push","--all",c.remote)):
            if g.text(*args).splitlines() != [c.expected_remote_url]:
                raise Blocked("unexpected onboarding remote")
        for name in ("MERGE_HEAD","CHERRY_PICK_HEAD","REVERT_HEAD","rebase-merge","rebase-apply","BISECT_LOG","sequencer"):
            if (self.engine.gitdir/name).exists():
                raise Blocked("unfinished Git operation")
        if g.run("ls-files","-u"):
            raise Blocked("merge conflicts in index")

    def review(self):
        self.boundary()
        if "/" in self.c.files[0]:
            raise Blocked("Choose a root filename for an empty repository first commit")
        if self.path.exists():
            raise Blocked("first-commit journal exists; inspect it before retrying")
        if self.has_head() or self.g.run("for-each-ref"):
            raise Blocked("first commit requires an unborn checkout with no refs")
        if self.g.run("ls-remote",self.c.remote).strip():
            raise Blocked("remote is not empty; fetch and review separately")
        if self.g.run("ls-files"):
            raise Blocked("empty onboarding requires an empty index")
        # Include ignored files: initialization must never overwrite or adopt local work.
        if any(p.name != ".git" for p in self.c.repository.iterdir()):
            raise Blocked("empty onboarding requires a pristine checkout; preserve existing files")
        if self.g.run("check-attr","filter","--",self.c.files[0]).strip().split(b": ")[-1] != b"unspecified":
            raise Blocked("file filter unsupported")
        author = self.g.text("var","GIT_AUTHOR_IDENT")
        committer = self.g.text("var","GIT_COMMITTER_IDENT")
        index=self.engine.gitdir/"index"
        if index.exists(): reject_link(index)
        data={"repository":str(self.c.repository),"remote":self.c.expected_remote_url,
              "branch":self.c.branch,"sha256":hashlib.sha256(INITIAL_CHECKPOINT).hexdigest(),
              "content":INITIAL_CHECKPOINT.decode(),"file":self.c.files[0],"index_sha256":hashlib.sha256(index.read_bytes()).hexdigest() if index.exists() else None,
              "author":author.rsplit(">",1)[0]+">", "committer":committer.rsplit(">",1)[0]+">"}
        data["review_sha256"]=hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()
        return data

    def create(self, approved_review_sha256):
        with exclusive(self.engine.runtime/"operation.lock"), exclusive(self.engine.gitdir/"index.lock"):
            review=self.review()
            if review["review_sha256"] != approved_review_sha256:
                raise Blocked("first-commit review changed or approval missing; review again")
            record={"stage":"preparing","review":review}
            atomic_json(self.path,record)
            fd,name=tempfile.mkstemp(prefix="initial-index-",dir=self.engine.runtime)
            os.close(fd);os.unlink(name)
            index=Path(name)
            try:
                with (self.c.repository/self.c.files[0]).open("xb") as stream:
                    stream.write(INITIAL_CHECKPOINT);stream.flush();os.fsync(stream.fileno())
                current=snapshot(self.c)
                if current.digest != review["sha256"]: raise Blocked("initial file changed")
                env={"GIT_INDEX_FILE":str(index)}
                self.g.run("read-tree","--empty",env=env)
                blob=self.g.text("hash-object","-w","--stdin",data=current.data)
                self.g.run("update-index","--add","--cacheinfo","100644,"+blob+","+self.c.files[0],env=env)
                tree=self.g.text("write-tree",env=env)
                commit=self.g.text("commit-tree",tree,data=b"NIGHT BANDIT: reviewed initial test file\n")
                self.boundary()
                if self.has_head() or self.g.run("for-each-ref") or self.g.run("ls-remote",self.c.remote).strip():
                    raise Blocked("local or remote history changed during onboarding")
                if any(p.name not in (".git",self.c.files[0]) for p in self.c.repository.iterdir()):
                    raise Blocked("unexpected files appeared during onboarding; preserve and review")
                if self.g.run("ls-files") or snapshot(self.c).digest != review["sha256"]:
                    raise Blocked("file or index changed during onboarding")
                original=self.engine.gitdir/"index"
                now=hashlib.sha256(original.read_bytes()).hexdigest() if original.exists() else None
                if now != review["index_sha256"]: raise Blocked("index changed during onboarding")
                record.update(stage="commit_ready",commit=commit,tree=tree,
                              prepared_index_sha256=hashlib.sha256(index.read_bytes()).hexdigest())
                atomic_json(self.path,record)
                self.g.run("update-ref","-m","NIGHT BANDIT reviewed first commit","refs/heads/"+self.c.branch,commit,"0"*len(commit))
                record["stage"]="ref_published";atomic_json(self.path,record)
                os.replace(index,original)
                record["stage"]="complete_local";atomic_json(self.path,record)
                self.engine.events.emit("committed",commit=commit,sha256=current.digest,
                                        reason="Reviewed first commit LOCAL ONLY; separate push approval required")
                return {"status":"initial_commit_pending_push","commit":commit,"sha256":current.digest,
                        "push_performed":False,"next_action":"Review and explicitly approve a separate first push"}
            except BaseException:
                self.engine.events.emit("needs_review",reason="First commit interrupted; preserve files, index and journal for manual review")
                raise
            finally:
                if index.exists(): index.unlink()

    def pending_dry_run(self, current):
        if not self.path.exists(): return None
        reject_link(self.path)
        try: record=json.loads(self.path.read_text())
        except (OSError,ValueError): raise Blocked("initial onboarding journal unreadable; inspect manually") from None
        if record.get("stage") != "complete_local":
            raise Blocked("interrupted initial onboarding; inspect journal, never automatically reset or retry")
        self.boundary()
        remote=self.g.text("ls-remote",self.c.remote)
        if remote:
            return None  # Normal preflight validates published history; allow later reviewed edits.
        commit=record["commit"]
        if self.g.text("rev-parse","HEAD") != commit or current.digest != record["review"]["sha256"]:
            raise Blocked("reviewed initial commit or file changed")
        if self.g.run("ls-files","-v","--",self.c.files[0])[:1] != b"H":
            raise Blocked("assume-unchanged or skip-worktree file unsupported")
        if self.g.run("status","--porcelain=v1","-z","--untracked-files=all"):
            raise Blocked("initial checkout changed; preserve and review")
        if self.g.text("rev-parse","HEAD^{tree}") != record["tree"] or self.g.run("cat-file","blob","HEAD:"+self.c.files[0]) != current.data:
            raise Blocked("initial committed tree differs from review")
        self.engine.events.emit("dry_run",commit=commit,sha256=current.digest,would_commit=False,would_push=False,
                                reason="Reviewed initial commit is local only; remote empty, push approval required")
        return {"status":"initial_commit_pending_push","commit":commit,"sha256":current.digest,
                "push_performed":False,"next_action":"Review and explicitly approve a separate first push"}
