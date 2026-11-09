"""Fail-closed Git transactions with an isolated index and reference CAS.

No add-all, checkout, reset, merge, rebase, hooks, force-push or automatic retry.
The ordinary Git index lock is held while preparing/publishing a local commit.
"""
import os
import subprocess
import tempfile
import json
import uuid
from contextlib import contextmanager
from pathlib import Path
from .config import Blocked
from .events import Events
from . import exclusions
from .recovery import Journal
from .validation import reject_link, snapshot


class GitFailure(Blocked):
    pass


class Git:
    def __init__(self, config):
        self.config = config
        # Ignore inherited Git overrides that might select another repo/index or
        # inject command configuration; preserve ordinary PATH/SSH auth helpers.
        self.env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
        self.env.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
        if config.credential_path:
            from .auth import git_auth_environment
            self.env.update(git_auth_environment(config))

    def run(self, *args, data=None, env=None):
        options = dict(self.env)
        options.update(env or {})
        credential_options=[]
        if self.config.credential_path:
            credential_options=["-c","credential.helper=","-c","credential.useHttpPath=true","-c","http.extraHeader="]
        process_options = {}
        if os.name == "nt":
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = subprocess.SW_HIDE
            process_options = {"creationflags": subprocess.CREATE_NO_WINDOW,
                               "startupinfo": startup}
        try:
            result = subprocess.run(
                ["git", "-c", "core.autocrlf=false", "-c", "core.fsmonitor=false",
                 "-c", "core.untrackedCache=false", "-c", "core.hooksPath=" + os.devnull, *credential_options, *args],
                cwd=self.config.repository, env=options, input=data,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=self.config.git_timeout_seconds, check=False, **process_options,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise GitFailure("Git command unavailable or timed out: " + args[0]) from None
        if result.returncode:
            # Do not expose stderr/command arguments: remotes/helpers can print secrets.
            raise GitFailure("Git command failed: " + args[0] + " (exit " + str(result.returncode) + ")")
        return result.stdout

    def text(self, *args, **kwargs):
        return self.run(*args, **kwargs).decode("utf-8").strip()


@contextmanager
def exclusive(path):
    """O_EXCL cooperates with Git index.lock and other NIGHT BANDIT processes."""
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise Blocked("concurrent activity or stale lock; inspect before retrying") from None
    try:
        owner_bytes=json.dumps({"pid":os.getpid(),"nonce":uuid.uuid4().hex}).encode("ascii")
        os.write(fd,owner_bytes)
        os.fsync(fd)
        yield
    finally:
        os.close(fd)
        if path.exists():
            if path.read_bytes()!=owner_bytes:
                raise Blocked("lock ownership changed; foreign lock preserved")
            path.unlink()


class Sync:
    def __init__(self, config, events=None):
        from .selection import validate
        validate(config)
        self.config = config
        self.git = Git(config)
        self.events = events or Events()
        self.gitdir = config.repository / ".git"
        reject_link(self.gitdir)
        if not self.gitdir.is_dir():
            raise Blocked("linked worktrees and nonstandard Git directories unsupported in v0.1")
        self.runtime = self.gitdir / "night-bandit"
        if self.runtime.exists():
            reject_link(self.runtime)
        self.runtime.mkdir(exist_ok=True)
        self.state = self.runtime / "last-success.json"

    def preflight(self, apply, pending_commit=None):
        from .selection import validate
        validate(self.config)
        g, c = self.git, self.config
        self.events.emit("validation_started",reason="Checking branch, index, exclusions and remote")
        exclusions.validate(c,g)
        reject_link(self.gitdir / "index")
        if Path(g.text("rev-parse", "--show-toplevel")).resolve() != c.repository:
            raise Blocked("configured directory is not the repository root")
        g.run("check-ref-format", "--branch", c.branch)
        if g.text("symbolic-ref", "--short", "HEAD") != c.branch:
            raise Blocked("unexpected branch or detached HEAD")
        # Check effective fetch and push destinations, including pushurl/rewrites.
        if g.text("remote", "get-url", "--all", c.remote).splitlines() != [c.expected_remote_url]:
            raise Blocked("unexpected fetch destination")
        if g.text("remote", "get-url", "--push", "--all", c.remote).splitlines() != [c.expected_remote_url]:
            raise Blocked("unexpected push destination")
        for name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD",
                     "rebase-merge", "rebase-apply", "BISECT_LOG", "sequencer"):
            if (self.gitdir / name).exists():
                raise Blocked("unfinished Git operation")
        if g.run("ls-files", "-u"):
            raise Blocked("merge conflicts in index")
        if g.run("diff", "--cached", "--name-only", "-z"):
            raise Blocked("pre-existing staged changes; index must be clean")
        records = g.run("status", "--porcelain=v1", "-z", "--untracked-files=normal", "--ignore-submodules=all").split(b"\0")
        allowed = c.files[0].encode("utf-8")
        for record in records:
            if not record: continue
            if record[3:] == allowed and record[:2] in (b" M", b"??"): continue
            name=record[3:].decode("utf-8")
            approved=c.reviewed_exclusions.get(name)
            if not approved or record[:2].decode()!=approved.get("status"):
                raise Blocked("unrelated working-tree changes; preserve and review separately")
        self.events.emit("exclusions_verified",reason="Exact approved excluded fingerprints revalidated")
        tracked = g.run("ls-files", "--stage", "-z", "--", c.files[0])
        if not tracked or not tracked.startswith(b"100644 ") or b" 0\t" not in tracked:
            raise Blocked("file must already be tracked as a regular non-executable file")
        if g.run("ls-files", "--cached", "--ignored", "--exclude-standard", "--", c.files[0]):
            raise Blocked("file matches a Git exclusion; explicit review required")
        # Hidden worktree changes are unsafe even if porcelain status is quiet.
        flag = g.run("ls-files", "-v", "--", c.files[0])[:1]
        if flag != b"H":
            raise Blocked("assume-unchanged or skip-worktree file unsupported")
        # Dry-run is Git-read-only: remote advertisement, no fetch/index/ref changes.
        self.events.emit("connection_check",reason="Checking expected remote branch advertisement")
        advertised = g.text("ls-remote", "--exit-code", c.remote, "refs/heads/" + c.branch).split()
        if len(advertised) != 2 or advertised[1] != "refs/heads/" + c.branch:
            raise Blocked("remote branch advertisement invalid")
        head = g.text("rev-parse", "HEAD")
        if advertised[0] != head:
            if not pending_commit or head!=pending_commit.get("commit") or advertised[0]!=pending_commit.get("base"):
                raise Blocked("local/remote heads differ; ahead, behind or divergent history requires review")
        if apply:
            # Fetch to a private ref only. Never change fetch configuration, origin
            # tracking refs, the worktree or local branch to incorporate incoming work.
            g.run("fetch", "--no-tags", "--no-recurse-submodules", "--refmap=", c.remote,
                  "refs/heads/" + c.branch + ":refs/night-bandit/checked")
            if g.text("rev-parse", "refs/night-bandit/checked") != advertised[0]:
                raise Blocked("remote moved during fetch; no merge attempted")
        return head

    def _same_index(self, expected):
        path = self.gitdir / "index"
        reject_link(path)
        if path.read_bytes() != expected:
            raise Blocked("ordinary Git index changed concurrently")

    def _unchanged(self, expected, automatic_authorization=None):
        from .selection import validate
        validate(self.config)
        exclusions.validate(self.config,self.git)
        current=snapshot(self.config)
        if automatic_authorization is not None:
            automatic_authorization.validate(self.config)
        elif current.digest != expected.digest:
            raise Blocked("file changed during validation; rerun after it stabilizes")

    def run(self, current=None, apply=False, push=False, approved_sha256=None, automatic_authorization=None):
        if push and (not apply or not self.config.enable_push):
            raise Blocked("push requires --apply, --push and enable_push=true")
        if apply and not approved_sha256 and automatic_authorization is None:
            raise Blocked("manual apply requires --approve-sha256 from reviewed dry-run")
        journal=Journal(self)
        if journal.pending():
            raise Blocked("local/remote heads differ or interrupted transaction pending; review explicit recovery")
        if automatic_authorization is not None:
            from .automatic import AutomaticAuthorization
            if not isinstance(automatic_authorization,AutomaticAuthorization) or not apply or not push:
                raise Blocked("Automatic sync requires explicit session authorization and push mode")
            automatic_authorization.validate(self.config)
        current = current or snapshot(self.config)
        if automatic_authorization is not None:
            approved_sha256=current.digest
            self.events.emit("automatic_change",path=self.config.files[0],sha256=current.digest,
                             mode="automatic",reason="Push authorized for this exact file and repository session")
        self.events.emit("detected", path=self.config.files[0], sha256=current.digest, bytes=len(current.data))
        if apply and current.digest != approved_sha256:
            raise Blocked("approved hash no longer matches current file")
        try:
            with exclusive(self.runtime / "operation.lock"):
                # An index.lock blocks compliant Git index writers. CAS protects
                # branch publication from users who update refs without that lock.
                with exclusive(self.gitdir / "index.lock"):
                    if apply: journal.begin(current)
                    from .onboarding import FirstCommit
                    pending_initial = FirstCommit(self).pending_dry_run(current)
                    if pending_initial:
                        if apply or push:
                            raise Blocked("initial commit awaits separate first-push approval; no automatic retry")
                        return pending_initial
                    head = self.preflight(apply)
                    self._unchanged(current, automatic_authorization=automatic_authorization)
                    g, c = self.git, self.config
                    blob = g.text("rev-parse", head + ":" + c.files[0])
                    prior = g.run("cat-file", "blob", blob)
                    if prior == current.data:
                        if apply: journal.update("complete")
                        self.events.emit("unchanged", sha256=current.digest, commit=head)
                        return {"status": "unchanged", "commit": head}
                    if not apply:
                        self.events.emit("dry_run", sha256=current.digest, base_commit=head,
                                         would_commit=True, would_push=False)
                        return {"status": "dry_run", "sha256": current.digest}
                    original_index = (self.gitdir / "index").read_bytes()
                    fd, tmp = tempfile.mkstemp(prefix="index-", dir=self.runtime)
                    os.close(fd)
                    index = Path(tmp)
                    index.write_bytes(original_index)  # Preserve unrelated index flags/metadata.
                    env = {"GIT_INDEX_FILE": str(index)}
                    committed = False
                    try:
                        # Stage only the exact reviewed bytes and mode of the one
                        # allowlisted file. Bypass filters so mixed line endings
                        # cannot silently rewrite an authoritative file.
                        new_blob = g.text("hash-object", "-w", "--stdin", data=current.data)
                        g.run("update-index", "--add", "--cacheinfo",
                              "100644," + new_blob + "," + c.files[0], env=env)
                        if g.run("diff", "--cached", "--name-only", "-z", env=env) != c.files[0].encode() + b"\0":
                            raise Blocked("isolated staged scope is not exactly the allowlist")
                        tree = g.text("write-tree", env=env)
                        self._same_index(original_index)
                        self._unchanged(current, automatic_authorization=automatic_authorization)
                        if g.text("rev-parse", "HEAD") != head or g.text("symbolic-ref", "--short", "HEAD") != c.branch:
                            raise Blocked("branch moved during transaction")
                        # Plumbing intentionally does not run repository hooks or
                        # signing programs. Public docs explain this limitation.
                        commit = g.text("commit-tree", tree, "-p", head,
                                        data=(("NIGHT BANDIT: automatic file synchronization\n\nSHA256: " if automatic_authorization else "NIGHT BANDIT: synchronize reviewed file\n\nSHA256: ")
                                              + current.digest + "\n").encode("ascii"))
                        self._same_index(original_index)
                        self._unchanged(current, automatic_authorization=automatic_authorization)
                        journal.staged(index,commit,tree)
                        g.run("update-ref", "-m", "NIGHT BANDIT reviewed file",
                              "refs/heads/" + c.branch, commit, head)
                        committed = True
                        journal.update("ref_published")
                        os.replace(index, self.gitdir / "index")
                        journal.update("index_published")
                        self.events.emit("committed", sha256=current.digest, commit=commit)
                        if not push:
                            journal.update("complete_local")
                            self.events.emit("local_only", commit=commit,
                                             reason="push disabled; review and synchronize manually")
                            return {"status": "committed", "commit": commit}
                        if g.text("rev-parse", "HEAD") != commit or g.text("symbolic-ref", "--short", "HEAD") != c.branch:
                            raise Blocked("branch moved after commit; no push attempted")
                        self._unchanged(current, automatic_authorization=automatic_authorization)
                        # Push exactly this commit, never another user's later HEAD.
                        journal.update("push_pending")
                        self.events.emit("push_started",commit=commit,reason="Normal exact-commit push requested")
                        g.run("push", "--no-verify", "--no-follow-tags", "--recurse-submodules=no", c.remote,
                              commit + ":refs/heads/" + c.branch)
                        live = g.text("ls-remote", "--exit-code", c.remote,
                                      "refs/heads/" + c.branch).split()
                        if not live or live[0] != commit:
                            raise Blocked("remote receipt mismatch; inspect before retrying")
                        g.run("fetch", "--no-tags", "--no-recurse-submodules", "--refmap=", c.remote,
                              "refs/heads/" + c.branch + ":refs/night-bandit/checked")
                        if (g.text("rev-parse", "refs/night-bandit/checked") != commit
                                or g.text("rev-parse", "refs/night-bandit/checked^{tree}") != tree):
                            raise Blocked("fetched remote tree does not match committed tree")
                        self._unchanged(current, automatic_authorization=automatic_authorization)
                        if g.text("rev-parse", "HEAD") != commit:
                            raise Blocked("local branch moved after push")
                        journal.update("complete")
                        self.events.emit("pushed", sha256=current.digest, commit=commit,
                                         verified_tree=tree)
                        import json
                        receipt_fd, receipt_name = tempfile.mkstemp(prefix="receipt-", dir=self.runtime)
                        receipt = Path(receipt_name)
                        try:
                            with os.fdopen(receipt_fd, "w", encoding="utf-8") as stream:
                                json.dump({"sha256": current.digest, "commit": commit}, stream)
                                stream.flush()
                                os.fsync(stream.fileno())
                            os.replace(receipt, self.state)
                        finally:
                            if receipt.exists():
                                receipt.unlink()
                        return {"status": "pushed", "commit": commit}
                    except Exception:
                        if committed:
                            self.events.emit("needs_review", reason="local commit retained; never auto-reset or retry")
                        raise
                    finally:
                        if index.exists():
                            index.unlink()
                        lock = Path(str(index) + ".lock")
                        if lock.exists():
                            lock.unlink()
        except (Blocked, OSError) as error:
            self.events.emit("blocked", sha256=current.digest,
                             reason=str(error) if isinstance(error,Blocked) else "Filesystem operation failed; inspect recovery and retry")
            raise
