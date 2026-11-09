"""All Git mutations occur in disposable repositories, never a user's checkout."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch

from night_bandit.config import Blocked, Config
from night_bandit.git_sync import GitFailure, Sync, exclusive
from night_bandit.validation import snapshot
from night_bandit.watcher import ChangeDetector, stable_snapshot


def git(root, *args):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    result = subprocess.run(["git", "-c", "core.autocrlf=false",
                             "-c", "core.hooksPath=" + os.devnull,
                             "-c", "commit.gpgsign=false", *args], cwd=root,
                            env=env, capture_output=True, check=True)
    return result.stdout.decode().strip()


class DetectorTests(unittest.TestCase):
    def test_first_stable_detection(self):
        d = ChangeDetector(2, 3)
        self.assertIsNone(d.observe("A", 0))
        self.assertIsNone(d.observe("A", 2))
        self.assertEqual(d.observe("A", 3), "A")

    def test_rapid_writes_debounce(self):
        d = ChangeDetector(2, 1)
        for digest, now in [("A", 0), ("B", 1), ("C", 2), ("C", 3.9)]:
            self.assertIsNone(d.observe(digest, now))
        self.assertEqual(d.observe("C", 4), "C")

    def test_unchanged_no_duplicate(self):
        d = ChangeDetector(1, 1)
        d.observe("A", 0)
        self.assertEqual(d.observe("A", 1), "A")
        self.assertIsNone(d.observe("A", 100))
        d.observe("B", 101)
        self.assertEqual(d.observe("B", 102), "B")


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="night-bandit-isolated-test-")
        self.root = Path(self.tmp.name)
        self.remote = self.root / "remote.git"
        self.repo = self.root / "working"
        self.repo.mkdir()
        git(self.root, "init", "--bare", str(self.remote))
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.name", "Isolated Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        self.file = self.repo / "notes.md"
        self.file.write_bytes(b"# Test checkpoint\nInitial\n")
        (self.repo / "unrelated.txt").write_text("preserve me\n")
        git(self.repo, "add", "--", "notes.md", "unrelated.txt")
        git(self.repo, "commit", "-m", "Isolated fixture")
        git(self.repo, "remote", "add", "origin", str(self.remote))
        git(self.repo, "push", "-u", "origin", "main")
        git(self.remote, "symbolic-ref", "HEAD", "refs/heads/main")
        self.config = Config(self.repo, str(self.remote), files=("notes.md",), poll_seconds=.001,
                             stable_seconds=.002, debounce_seconds=.002)
        self.head = git(self.repo, "rev-parse", "HEAD")
        self.remote_head = git(self.remote, "rev-parse", "main")
        self.output = io.StringIO()
        self.quiet = contextlib.redirect_stdout(self.output)
        self.quiet.__enter__()

    def tearDown(self):
        self.quiet.__exit__(None, None, None)
        self.tmp.cleanup()

    def change(self, content=b"# Test checkpoint\r\nReviewed update\n"):
        self.file.write_bytes(content)
        return snapshot(self.config)

    def sync(self, **kwargs):
        return Sync(self.config).run(**kwargs)

    def approved(self, push=False):
        current = snapshot(self.config)
        return Sync(replace(self.config, enable_push=push)).run(
            current, apply=True, push=push, approved_sha256=current.digest)

    def assert_heads_unchanged(self):
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.head)
        self.assertEqual(git(self.remote, "rev-parse", "main"), self.remote_head)

    def test_actual_file_change_hash(self):
        before = snapshot(self.config)
        after = self.change()
        self.assertNotEqual(before.digest, after.digest)
        self.assertEqual(after.digest, hashlib.sha256(after.data).hexdigest())

    def test_stable_snapshot_with_fake_clock(self):
        tick = [0.0]
        def sleep(seconds):
            tick[0] += seconds
        self.change()
        current = stable_snapshot(self.config, sleep=sleep, now=lambda: tick[0])
        self.assertEqual(current.digest, snapshot(self.config).digest)
        self.assertGreaterEqual(tick[0], .002)

    def test_default_dry_run_no_git_mutation(self):
        self.change()
        before_index = (self.repo / ".git/index").read_bytes()
        before_refs = git(self.repo, "show-ref")
        result = self.sync()
        self.assertEqual(result["status"], "dry_run")
        self.assert_heads_unchanged()
        self.assertEqual((self.repo / ".git/index").read_bytes(), before_index)
        self.assertEqual(git(self.repo, "show-ref"), before_refs)
        self.assertFalse((self.repo / ".git/FETCH_HEAD").exists())
        self.assertFalse((self.repo / ".git/night-bandit/last-success.json").exists())
        self.assertIn('"event": "dry_run"', self.output.getvalue())

    def test_unchanged_no_commit(self):
        self.assertEqual(self.sync()["status"], "unchanged")
        self.assert_heads_unchanged()

    def test_allowlist_rejects_other_paths_and_traversal(self):
        for files in [("../other.txt",), (".git/config",), (".env",), ("leads/inbox/x.csv",),
                      ("notes.md", "unrelated.txt"), ("/absolute.md",)]:
            with self.subTest(files=files), self.assertRaises(Blocked):
                replace(self.config, files=files)
        with self.assertRaises(Blocked):
            snapshot(self.config, "unrelated.txt")

    def test_unrelated_dirty_worktree_preserved(self):
        self.change()
        other = self.repo / "unrelated.txt"
        other.write_text("another person's work\n")
        expected = other.read_bytes()
        with self.assertRaisesRegex(Blocked, "unrelated"):
            self.approved()
        self.assertEqual(other.read_bytes(), expected)
        self.assert_heads_unchanged()

    def test_untracked_unrelated_file_blocks(self):
        self.change()
        (self.repo / "new.txt").write_text("do not stage")
        with self.assertRaises(Blocked):
            self.approved()
        self.assertEqual(git(self.repo, "diff", "--cached", "--name-only"), "")
        self.assert_heads_unchanged()

    def test_preexisting_staged_changes_preserved(self):
        self.change()
        other = self.repo / "unrelated.txt"
        other.write_text("staged work")
        git(self.repo, "add", "--", "unrelated.txt")
        index = (self.repo / ".git/index").read_bytes()
        with self.assertRaisesRegex(Blocked, "staged"):
            self.approved()
        self.assertEqual((self.repo / ".git/index").read_bytes(), index)
        self.assert_heads_unchanged()

    def make_incoming(self):
        clone = self.root / "other"
        git(self.root, "clone", str(self.remote), str(clone))
        git(clone, "config", "user.name", "Other Test")
        git(clone, "config", "user.email", "other@example.invalid")
        (clone / "unrelated.txt").write_text("remote update")
        git(clone, "add", "--", "unrelated.txt")
        git(clone, "commit", "-m", "Incoming fixture")
        git(clone, "push", "origin", "main")
        return clone

    def test_remote_behind_blocks_without_merge(self):
        self.make_incoming()
        self.change()
        with self.assertRaisesRegex(Blocked, "heads differ"):
            self.approved()
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.head)

    def test_remote_divergence_blocks(self):
        self.make_incoming()
        (self.repo / "unrelated.txt").write_text("local independent update")
        git(self.repo, "add", "--", "unrelated.txt")
        git(self.repo, "commit", "-m", "Local independent fixture")
        local = git(self.repo, "rev-parse", "HEAD")
        self.change()
        with self.assertRaisesRegex(Blocked, "heads differ"):
            self.approved()
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), local)

    def test_local_ahead_is_not_automatically_pushed(self):
        self.change()
        self.approved()
        local = git(self.repo, "rev-parse", "HEAD")
        with self.assertRaisesRegex(Blocked, "heads differ"):
            self.approved(push=True)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), local)
        self.assertEqual(git(self.remote, "rev-parse", "main"), self.remote_head)

    def test_existing_index_lock_blocks_and_is_preserved(self):
        self.change()
        lock = self.repo / ".git/index.lock"
        lock.write_bytes(b"other git activity")
        with self.assertRaisesRegex(Blocked, "concurrent"):
            self.approved()
        self.assertEqual(lock.read_bytes(), b"other git activity")
        self.assert_heads_unchanged()

    def test_concurrent_bandit_lock_blocks(self):
        self.change()
        sync = Sync(self.config)
        with exclusive(sync.runtime / "operation.lock"):
            with self.assertRaisesRegex(Blocked, "concurrent"):
                sync.run()
        self.assert_heads_unchanged()

    def test_command_failure_releases_owned_locks(self):
        self.change()
        sync = Sync(self.config)
        with patch.object(sync.git, "run", side_effect=GitFailure("simulated safe error")):
            with self.assertRaises(GitFailure):
                sync.run()
        self.assertFalse((self.repo / ".git/index.lock").exists())
        self.assertFalse((sync.runtime / "operation.lock").exists())
        self.assert_heads_unchanged()

    def test_manual_local_commit_exact_bytes_only(self):
        current = self.change()
        result = self.approved()
        self.assertEqual(result["status"], "committed")
        self.assertNotEqual(result["commit"], self.head)
        self.assertEqual(git(self.remote, "rev-parse", "main"), self.remote_head)
        self.assertEqual(git(self.repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"),
                         "notes.md")
        raw = subprocess.check_output(["git", "cat-file", "blob", "HEAD:notes.md"], cwd=self.repo)
        self.assertEqual(raw, current.data)
        self.assertEqual(git(self.repo, "diff", "--cached", "--name-only"), "")

    def test_manual_live_push_in_isolated_repository(self):
        current = self.change()
        result = self.approved(push=True)
        self.assertEqual(result["status"], "pushed")
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"),
                         git(self.remote, "rev-parse", "main"))
        raw = subprocess.check_output(["git", "cat-file", "blob", "main:notes.md"], cwd=self.remote)
        self.assertEqual(raw, current.data)
        receipt = json.loads((self.repo / ".git/night-bandit/last-success.json").read_text())
        self.assertEqual(receipt, {"sha256": current.digest, "commit": result["commit"]})
        self.assertEqual(self.sync()["status"], "unchanged")

    def test_push_failure_retains_local_commit_and_blocks_retry(self):
        self.change()
        sync = Sync(replace(self.config, enable_push=True))
        original = sync.git.run
        def fail_push(*args, **kwargs):
            if "push" in args:
                raise GitFailure("simulated push failure")
            return original(*args, **kwargs)
        with patch.object(sync.git, "run", side_effect=fail_push):
            with self.assertRaises(GitFailure):
                sync.run(apply=True, push=True, approved_sha256=snapshot(self.config).digest)
        self.assertNotEqual(git(self.repo, "rev-parse", "HEAD"), self.head)
        self.assertEqual(git(self.remote, "rev-parse", "main"), self.remote_head)
        self.assertFalse((sync.runtime / "last-success.json").exists())
        with self.assertRaisesRegex(Blocked, "heads differ"):
            self.approved(push=True)

    def test_push_gates(self):
        self.change()
        with self.assertRaisesRegex(Blocked, "enable_push"):
            self.sync(apply=True, push=True, approved_sha256=snapshot(self.config).digest)
        with self.assertRaisesRegex(Blocked, "approve-sha256"):
            self.sync(apply=True)
        self.assert_heads_unchanged()

    def test_review_hash_mismatch_blocks(self):
        self.change()
        with self.assertRaisesRegex(Blocked, "approved hash"):
            self.sync(apply=True, approved_sha256="0" * 64)
        self.assert_heads_unchanged()

    def test_checkpoint_changed_during_transaction_blocks(self):
        self.change()
        sync = Sync(self.config)
        original = sync.preflight
        def race(apply):
            head = original(apply)
            self.file.write_text("a newer write")
            return head
        with patch.object(sync, "preflight", side_effect=race):
            with self.assertRaisesRegex(Blocked, "changed"):
                sync.run(snapshot(self.config), apply=True,
                         approved_sha256=snapshot(self.config).digest)
        self.assert_heads_unchanged()

    def test_wrong_branch_and_remote_block(self):
        self.change()
        with self.assertRaises(Blocked):
            Sync(replace(self.config, branch="another")).run()
        with self.assertRaisesRegex(Blocked, "destination"):
            Sync(replace(self.config, expected_remote_url=str(self.root / "wrong.git"))).run()
        git(self.repo, "config", "remote.origin.pushurl", str(self.root / "wrong-push.git"))
        with self.assertRaisesRegex(Blocked, "push destination"):
            self.sync()
        self.assert_heads_unchanged()

    def test_ignored_and_skip_worktree_block(self):
        self.change()
        (self.repo / ".git/info/exclude").write_text("notes.md\n")
        with self.assertRaisesRegex(Blocked, "exclusion"):
            self.sync()
        (self.repo / ".git/info/exclude").write_text("")
        git(self.repo, "update-index", "--skip-worktree", "--", "notes.md")
        with self.assertRaisesRegex(Blocked, "skip-worktree"):
            self.sync()
        self.assert_heads_unchanged()

    def test_binary_empty_and_secret_tripwire(self):
        for data in (b"", b"a\0b", b"-----BEGIN PRIVATE KEY-----\nfixture"):
            self.file.write_bytes(data)
            with self.assertRaises(Blocked):
                snapshot(self.config)

    def test_config_strict_boolean_and_unknown_keys(self):
        with self.assertRaises(Blocked):
            replace(self.config, enable_push="false")
        config = self.root / "config.json"
        config.write_text(json.dumps({"repository": str(self.repo), "expected_remote_url": str(self.remote),
                                      "typo": True}))
        with self.assertRaises(Blocked):
            Config.load(config)


    def test_no_sensitive_content_in_logs(self):
        content = b"# Test\nPRIVATE_FIXTURE_CONTENT_DO_NOT_LOG\n"
        self.change(content)
        self.sync()
        self.assertNotIn("PRIVATE_FIXTURE_CONTENT_DO_NOT_LOG", self.output.getvalue())
        self.assertIn(snapshot(self.config).digest, self.output.getvalue())



    def test_reference_cas_preserves_another_users_commit(self):
        current = self.change()
        sync = Sync(self.config)
        original = sync.git.run
        concurrent = []
        def race(*args, **kwargs):
            if args[0] == "update-ref" and not concurrent:
                tree = git(self.repo, "rev-parse", self.head + "^{tree}")
                other = git(self.repo, "commit-tree", tree, "-p", self.head, "-m", "Other user's commit")
                git(self.repo, "update-ref", "refs/heads/main", other, self.head)
                concurrent.append(other)
            return original(*args, **kwargs)
        with patch.object(sync.git, "run", side_effect=race):
            with self.assertRaises(GitFailure):
                sync.run(current, apply=True, approved_sha256=current.digest)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), concurrent[0])
        self.assertEqual(git(self.remote, "rev-parse", "main"), self.remote_head)
        self.assertEqual(self.file.read_bytes(), current.data)

    def test_concurrent_index_change_detected_without_overwrite(self):
        current = self.change()
        sync = Sync(self.config)
        original = sync.git.run
        changed_index = []
        def race(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[0] == "write-tree":
                index = self.repo / ".git/index"
                changed_index.append(index.read_bytes() + b"isolated simulated index race")
                index.write_bytes(changed_index[0])
            return result
        with patch.object(sync.git, "run", side_effect=race):
            with self.assertRaisesRegex(Blocked, "index changed"):
                sync.run(current, apply=True, approved_sha256=current.digest)
        self.assertEqual((self.repo / ".git/index").read_bytes(), changed_index[0])
        self.assert_heads_unchanged()

    def test_remote_moves_after_advertisement_blocks_fetch(self):
        current = self.change()
        sync = Sync(self.config)
        original = sync.git.run
        raced = []
        def race(*args, **kwargs):
            if args[0] == "fetch" and not raced:
                self.make_incoming()
                raced.append(True)
            return original(*args, **kwargs)
        with patch.object(sync.git, "run", side_effect=race):
            with self.assertRaisesRegex(Blocked, "remote moved"):
                sync.run(current, apply=True, approved_sha256=current.digest)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.head)
        self.assertEqual(self.file.read_bytes(), current.data)

    def test_commit_creation_failure_does_not_publish(self):
        current = self.change()
        sync = Sync(self.config)
        original = sync.git.run
        index = (self.repo / ".git/index").read_bytes()
        def fail(*args, **kwargs):
            if args[0] == "commit-tree":
                raise GitFailure("simulated commit creation failure")
            return original(*args, **kwargs)
        with patch.object(sync.git, "run", side_effect=fail):
            with self.assertRaises(GitFailure):
                sync.run(current, apply=True, approved_sha256=current.digest)
        self.assert_heads_unchanged()
        self.assertEqual((self.repo / ".git/index").read_bytes(), index)

    def test_git_timeout_returns_controlled_error(self):
        sync = Sync(self.config)
        with patch("night_bandit.git_sync.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(["git", "SECRET_ARGUMENT"], 1)):
            with self.assertRaisesRegex(GitFailure, "timed out") as error:
                sync.git.run("status")
        self.assertNotIn("SECRET_ARGUMENT", str(error.exception))

    def test_unmerged_index_blocks_without_repair(self):
        self.change()
        blob = git(self.repo, "rev-parse", "HEAD:notes.md")
        entries = ("0 " + "0" * len(blob) + "\tnotes.md\n"
                   + "100644 " + blob + " 1\tnotes.md\n"
                   + "100644 " + blob + " 2\tnotes.md\n"
                   + "100644 " + blob + " 3\tnotes.md\n")
        subprocess.run(["git", "update-index", "--index-info"], cwd=self.repo,
                       input=entries.encode(), capture_output=True, check=True)
        index = (self.repo / ".git/index").read_bytes()
        with self.assertRaisesRegex(Blocked, "merge conflicts"):
            self.sync()
        self.assertEqual((self.repo / ".git/index").read_bytes(), index)
        self.assert_heads_unchanged()



    def test_unrelated_index_flags_and_hidden_content_preserved(self):
        current = self.change()
        other = self.repo / "unrelated.txt"
        git(self.repo, "update-index", "--assume-unchanged", "--", "unrelated.txt")
        other.write_text("hidden unrelated work")
        before_flag = git(self.repo, "ls-files", "-v", "--", "unrelated.txt")
        result = self.approved()
        self.assertEqual(result["status"], "committed")
        self.assertEqual(other.read_text(), "hidden unrelated work")
        self.assertEqual(git(self.repo, "ls-files", "-v", "--", "unrelated.txt"), before_flag)
        self.assertEqual(git(self.repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"),
                         "notes.md")
        self.assertEqual(self.file.read_bytes(), current.data)


if __name__ == "__main__":
    unittest.main()
