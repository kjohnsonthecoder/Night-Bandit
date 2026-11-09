"""Reviewed legacy profile imports; scope/grants persist, push/session authority does not."""
import hashlib
import json
from dataclasses import asdict,replace
from pathlib import Path
from .model import safe_parents
from night_bandit.config import Config,Blocked
from night_bandit.git_sync import Git,exclusive
from night_bandit import exclusions
from night_bandit.recovery import atomic_json


def scope(w):
    return {'folder':str(Path(w.folder).resolve()),'remote':w.remote,'branch':w.branch,'file':w.master_file}


def encoded_config(c):return json.loads(json.dumps(asdict(c),default=str))
def digest(data):return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).hexdigest()


class ImportedProfiles:
    def __init__(self,folder):self.folder=Path(folder)/'imported-profiles'
    def path(self,w):return self.folder/(w.id+'.json')
    def load(self,w):
        p=self.path(w);safe_parents(p)
        if not p.exists():return None
        try:
            data=json.loads(p.read_text())
            if data['schema']!=1 or data['scope']!=scope(w) or data['config_sha256']!=digest(data['config']):raise ValueError()
            c=Config(**data['config'])
            self.check_scope(w,c)
            if c.enable_push or c.credential_path:raise ValueError()
            return c
        except (ValueError,KeyError,TypeError):raise Blocked('Imported profile changed or no longer matches this workspace; review import again') from None
    @staticmethod
    def check_scope(w,c):
        if c.repository!=Path(w.folder).resolve() or c.expected_remote_url!=w.remote or c.branch!=w.branch or c.files!=(w.master_file,):
            raise Blocked('Imported profile must match the exact repository, branch and selected file')
    def save(self,w,source,approved=False):
        if approved is not True:raise Blocked('Review and approve the existing profile import first')
        source=Path(source);safe_parents(source);raw=source.read_bytes()
        try:c=Config(**json.loads(raw))
        except (ValueError,TypeError):raise Blocked('Choose a valid existing NIGHT BANDIT configuration') from None
        self.check_scope(w,c)
        # Credential references and live push flags never migrate as authorization.
        c=replace(c,enable_push=False,credential_path='')
        exclusions.validate(c,Git(c))
        if source.read_bytes()!=raw:raise Blocked('Source profile changed during review')
        safe_parents(self.folder);self.folder.mkdir(exist_ok=True)
        p=self.path(w);safe_parents(p);previous=p.read_bytes() if p.exists() else None
        config=encoded_config(c)
        record={'schema':1,'scope':scope(w),'config':config,'config_sha256':digest(config),'source_sha256':hashlib.sha256(raw).hexdigest()}
        with exclusive(self.folder/'import.lock'):
            if (p.read_bytes() if p.exists() else None)!=previous:raise Blocked('Imported profile changed concurrently')
            atomic_json(p,record)
        loaded=self.load(w)
        if loaded.reviewed_exclusions!=c.reviewed_exclusions:raise Blocked('Exclusion import verification failed')
        return loaded
