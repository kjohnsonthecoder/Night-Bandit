"""Existing-engine bridge. No startup sync; one foreground-authorized session at a time."""
import re
from dataclasses import replace
from pathlib import Path
from .model import LocalReader, safe_parents
from night_bandit.auth import CredentialVault, GitHubProvider
from night_bandit.config import Blocked
from night_bandit.events import Events, read_events
from night_bandit.git_sync import Sync, exclusive
from night_bandit.automatic import AutomaticSession
from night_bandit import selection, exclusions
from .app_identity import GITHUB_CLIENT_ID
from .profiles import ImportedProfiles
from night_bandit.validation import snapshot


class Runtime:
    def __init__(self, state_dir, provider=None, vault=None):
        self.folder=Path(state_dir);safe_parents(self.folder)
        self.folder.mkdir(parents=True,exist_ok=True)
        self.provider=provider or GitHubProvider()
        self.vault=vault or CredentialVault(self.folder/'github-session.bin')
        self.rows=[];self.bound={};self.profiles=ImportedProfiles(self.folder)
        self.events=Events(self.folder/'connection-events.jsonl')

    def client_id(self):
        # Product identity comes from the application, never a user-entered field.
        if not isinstance(GITHUB_CLIENT_ID,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{4,100}',GITHUB_CLIENT_ID):
            raise Blocked('GitHub connection is unavailable in this build. Update NIGHT BANDIT or contact Frontline support.')
        return GITHUB_CLIENT_ID

    def begin(self):
        return self.provider.begin(self.client_id())

    def finish(self, challenge, cancel):
        client=self.client_id()
        response=self.provider.finish(client,challenge,cancel)
        if cancel.is_set():raise Blocked('GitHub sign-in cancelled')
        self.vault.save_authorization(response,client)
        self.events.emit('github_connected',reason='Device authorization completed; Windows-protected session saved')
        return self.discover()

    def discover(self):
        self.rows=self.provider.repositories(self.vault.load())
        return self.rows

    def validate_remote(self, remote, branch):
        match=re.fullmatch(r'https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\.git',remote)
        if not match:raise Blocked('Choose an authorized GitHub HTTPS repository')
        row=self.provider.validate_selection(self.vault.load(),match.group(1))
        if row['url']!=remote or row['branch']!=branch:
            raise Blocked('Repository identity or default branch changed; select it again')
        return row

    def base_config(self,w):
        return self.profiles.load(w) or w.config()

    def reader(self,w):
        if w.draft:raise Blocked('Choose a repository, existing local checkout and one file first')
        safe_parents(Path(w.folder));folder=Path(w.folder).resolve()
        imported=self.profiles.load(w)
        return LocalReader(w,approved_config=imported)

    def import_profile(self,w,source,approved=False):
        return self.profiles.save(w,source,approved)

    def guard(self, w):
        reader=self.reader(w)
        lock=Path(w.folder)/'.git/night-bandit/listener.lock'
        if lock.exists() or lock.is_symlink():
            raise Blocked('This checkout already has a listener or stale lock; preserve it and inspect before retrying')
        return reader

    def bind(self, w, approved=False):
        if approved is not True:raise Blocked('Confirm the exact repository and selected file first')
        reader=self.guard(w);reader.review();self.validate_remote(w.remote,w.branch)
        config=replace(self.base_config(w),credential_path=str(self.vault.path),git_timeout_seconds=30.0)
        exclusions.validate(config,reader.git)
        runtime=config.repository/'.git/night-bandit'
        safe_parents(runtime);runtime.mkdir(exist_ok=True)
        # Hold the same cross-process listener lease while changing selection.
        # A listener starting during provider validation cannot race this write.
        with exclusive(runtime/'listener.lock'):
            old=selection.read(config)
            if old!=w.master_file:selection.save(config,old)
        self.bound[w.id]=w
        self.events.emit('file_selected',path=w.master_file,reason='Exact workspace scope reviewed; automatic sync remains off')
        return {'status':'ready','repository':w.remote,'file':w.master_file}

    def config(self, w):
        # Full dataclass equality prevents a rename/path change from carrying authorization forward.
        if self.bound.get(w.id)!=w:raise Blocked('Connect GitHub and review this exact workspace scope first')
        self.guard(w)
        if selection.read(self.base_config(w))!=w.master_file:raise Blocked('Selected file lock changed; review scope again')
        return replace(self.base_config(w),credential_path=str(self.vault.path),git_timeout_seconds=30.0)

    def engine(self, w):
        config=self.config(w)
        return Sync(config,events=Events(config.repository/'.git/night-bandit/events.jsonl'))

    def dry_run(self, w):
        engine=self.engine(w);self.validate_remote(w.remote,w.branch)
        return engine.run(snapshot(engine.config))

    def automatic(self, w, stop, notify, approved=False):
        if approved is not True:raise Blocked('Approve automatic pushes for this exact session first')
        engine=self.engine(w);self.validate_remote(w.remote,w.branch)
        engine=Sync(replace(engine.config,enable_push=True),events=engine.events)
        engine.events.callback=lambda record:notify({'phase':'audit','record':record})
        return AutomaticSession(engine,approved=True).run(stop,notify)

    def receipt(self, w):
        """Return actual engine-verified audit evidence, never an unverified notify payload."""
        self.guard(w)
        records=read_events(Path(w.folder)/'.git/night-bandit/events.jsonl')
        return next((x for x in reversed(records) if x.get('event')=='pushed'),None)
