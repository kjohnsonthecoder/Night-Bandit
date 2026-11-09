"""Independent workspace state and read-only Git adapter. No credentials or sync calls."""
import hashlib
import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
import calendar
from pathlib import Path
from urllib.parse import urlsplit
from night_bandit.config import Config, Blocked
from night_bandit.git_sync import Git, exclusive
from night_bandit.validation import reject_link, snapshot
from night_bandit.events import read_events, redact

ICONS = ("NB", "FL", "<>", "#", "+")


def safe_parents(path):
    for item in reversed((path, *path.parents)):
        if item.exists() or item.is_symlink():
            reject_link(item)


@dataclass(frozen=True)
class Workspace:
    id: str
    name: str
    icon: str
    folder: str
    remote: str
    branch: str
    master_file: str
    provider: str = "GitHub"

    def __post_init__(self):
        if not re.fullmatch(r"[0-9a-f]{32}", self.id):
            raise Blocked("Invalid workspace identifier")
        if not self.name.strip() or len(self.name)>60 or any(ord(c)<32 for c in self.name):
            raise Blocked("Use a display name of 1–60 visible characters")
        if not re.fullmatch(r"[A-Za-z0-9+#<>]{1,3}", self.icon) or self.provider != "GitHub":
            raise Blocked("Choose a supported icon and GitHub; other providers are Coming Soon")
        if not self.folder and not self.remote and not self.master_file:
            if not self.branch or self.branch.startswith("-"):raise Blocked("Choose a named branch")
            return  # Draft screen; never a connection or execution authorization.
        if not Path(self.folder).is_absolute():
            raise Blocked("Select an absolute local repository folder")
        if Path(self.folder).drive.startswith('\\\\'):
            raise Blocked('Choose a local drive checkout, not a network share')
        # Validate scope syntax without requiring this checkout to be online at startup.
        config = Config(Path(__file__).parent, self.remote, branch=self.branch,
                        files=(self.master_file,) if self.master_file else (), enable_push=False)
        if not config.files:
            raise Blocked("Choose one master file explicitly")
        url=urlsplit(self.remote)
        if url.scheme!='https' or url.netloc.lower()!='github.com' or not re.fullmatch(r'/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?',url.path):
            raise Blocked("Use an explicit GitHub HTTPS repository URL without credentials")

    @property
    def draft(self):
        return not self.folder and not self.remote and not self.master_file

    def config(self):
        if self.draft:raise Blocked("Finish repository setup before inspecting Git")
        return Config(Path(self.folder), self.remote, branch=self.branch,
                      files=(self.master_file,) if self.master_file else (), enable_push=False, git_timeout_seconds=3.0)


class Store:
    """One private registry. Session approval, credentials and enable_push are never persisted."""
    def __init__(self, folder):
        self.folder=Path(folder).absolute()
        safe_parents(self.folder)
        self.folder.mkdir(parents=True,exist_ok=True)
        self.path=self.folder/'workspaces.json'
        safe_parents(self.path)
        self.raw=self.path.read_bytes() if self.path.exists() else None
        self.workspaces=[]
        self.settings={'appearance':'Crimson / Black','notifications':False,'monitoring':'Manual preview'}
        if self.raw:
            try:
                data=json.loads(self.raw)
                if set(data)!= {'schema','workspaces','settings'} or data['schema']!=1:
                    raise ValueError()
                settings=data['settings']
                if set(settings)!=set(self.settings) or settings['appearance']!='Crimson / Black' or type(settings['notifications']) is not bool or settings['monitoring']!='Manual preview':
                    raise ValueError()
                self.settings=settings
                self.workspaces=[Workspace(**w) for w in data['workspaces']]
                self._validate(self.workspaces)
            except (ValueError,TypeError,KeyError):
                raise Blocked('Workspace settings are invalid; existing data was preserved') from None

    def _validate(self, items):
        if len(items)>50 or len({w.id for w in items})!=len(items):
            raise Blocked('Invalid workspace count or duplicate identifier')
        paths=[os.path.normcase(str(Path(w.folder).resolve())) for w in items if w.folder]
        if len(set(paths))!=len(paths):
            raise Blocked('This local checkout already has a workspace')

    def save(self, items, settings=None):
        self._validate(items)
        settings=dict(settings or self.settings)
        if settings != {'appearance':'Crimson / Black','notifications':False,'monitoring':'Manual preview'}:
            raise Blocked('Notification and monitoring settings are not enabled in this preview')
        payload=json.dumps({'schema':1,'workspaces':[asdict(w) for w in items],'settings':settings},indent=2).encode()
        safe_parents(self.folder)
        safe_parents(self.path)
        with exclusive(self.folder/'registry.lock'):
            actual=self.path.read_bytes() if self.path.exists() else None
            if actual!=self.raw:
                raise Blocked('Settings changed in another window; reopen before saving')
            temporary=self.folder/('registry-'+uuid.uuid4().hex+'.tmp')
            try:
                with temporary.open('xb') as stream:
                    stream.write(payload);stream.flush();os.fsync(stream.fileno())
                os.replace(temporary,self.path)
            finally:
                if temporary.exists():temporary.unlink()
        self.raw=payload;self.workspaces=list(items);self.settings=settings

    def upsert(self, workspace):
        items=[workspace if w.id==workspace.id else w for w in self.workspaces]
        if not any(w.id==workspace.id for w in self.workspaces):items.append(workspace)
        self.save(items)

    def remove(self, key):
        self.save([w for w in self.workspaces if w.id!=key])


class SerialCoordinator:
    """Synchronous, non-reentrant operation gate. Zero worker threads or automatic sessions."""
    def __init__(self):
        self.active=None;self.errors={};self.results={}

    def run(self, key, operation):
        if self.active is not None:
            raise Blocked('Another workspace operation is in progress')
        self.active=key
        try:
            value=operation();self.results[key]=value;self.errors.pop(key,None)
            return value
        except Exception as error:
            self.errors[key]=redact(str(error))
            raise
        finally:
            self.active=None


class LocalReader:
    """Local read-only adapter; never fetches, loads a vault or creates .git/night-bandit state."""
    def __init__(self, workspace, denied=(), approved_config=None):
        self.workspace=workspace
        safe_parents(Path(workspace.folder))
        self.config=approved_config or workspace.config()
        folder=self.config.repository
        if approved_config is not None:
            if folder!=Path(workspace.folder).resolve() or self.config.expected_remote_url!=workspace.remote or self.config.branch!=workspace.branch or self.config.files!=(workspace.master_file,):
                raise Blocked('Reviewed configuration scope mismatch')
        prohibited=tuple(map(Path,denied))
        if any(folder==p.resolve() or folder in p.resolve().parents for p in prohibited):
            raise Blocked('This repository folder is protected')
        safe_parents(folder)
        gitdir=folder/'.git';reject_link(gitdir)
        if not gitdir.is_dir():raise Blocked('Choose a regular local Git checkout')
        self.git=Git(self.config)
        if Path(self.git.text('rev-parse','--show-toplevel')).resolve()!=folder:
            raise Blocked('Choose the repository root folder')
        if self.git.text('symbolic-ref','--short','HEAD')!=workspace.branch:
            raise Blocked('Local branch changed; review this workspace again')
        if self.git.text('remote','get-url','origin')!=workspace.remote:
            raise Blocked('Repository connection changed; review this workspace again')

    def review(self):
        w=self.workspace
        if not self.git.run('ls-files','--error-unmatch','--',w.master_file):
            raise Blocked('Choose a tracked file')
        selected=snapshot(self.config)
        return {'repository':w.remote,'folder':w.folder,'branch':w.branch,'file':w.master_file,
                'sha256':selected.digest,'bytes':len(selected.data),'head':self.git.text('rev-parse','HEAD'),
                'connection':'Local origin matches; GitHub access has not been checked',
                'last_push':self.last_push(),
                'monitoring':'Stopped — synchronization requires separate explicit session approval'}

    def history(self, period='week'):
        if period not in ('day','week','month'):raise Blocked('Choose day, week or month')
        days={'day':1,'week':7,'month':30}[period]
        since=datetime.now(timezone.utc)-timedelta(days=days)
        raw=self.git.run('log','--max-count=500','--since='+since.isoformat(),'--format=%H%x00%cI%x00%s','--',self.workspace.master_file)
        rows=[]
        for line in raw.decode('utf-8',errors='replace').splitlines():
            parts=line.split('\0',2)
            if len(parts)==3 and re.fullmatch(r'[0-9a-f]{40,64}',parts[0]):
                rows.append({'commit':parts[0],'date':parts[1],'subject':redact(parts[2]),'source':'Local Git commit; remote push not verified'})
        return rows

    def activity(self):
        runtime=self.config.repository/'.git/night-bandit'
        if runtime.exists():safe_parents(runtime)
        return read_events(runtime/'events.jsonl')

    def last_push(self):
        events=[e for e in self.activity() if e.get('event')=='pushed']
        if not events:return 'No verified push available in this preview'
        return 'Audit records a prior push; remote verification is pending in this preview'


    def calendar_history(self, period, anchor):
        start,end=history_window(period,anchor);counts={}
        # Repository-wide reachable HEAD commits, including merged ancestry. No master-file filter.
        raw=self.git.run('log',*_dates_args(start,end),'--format=%cI','HEAD')
        for line in raw.decode('ascii').splitlines():
            day=datetime.fromisoformat(line).astimezone().date()
            if start<=day<end:counts[day.isoformat()]=counts.get(day.isoformat(),0)+1
        return {'start':start,'end':end,'counts':counts,'total':sum(counts.values()),'source':'All commits reachable from the selected local branch; local time. Remote-only commits are not fetched.'}


    def commits_on(self, day, skip=0):
        if type(skip) is not int or skip<0:raise Blocked('Invalid history page')
        start,end=history_window('day',day)
        raw=self.git.run('log',*_dates_args(start,end),'--skip='+str(skip),'--max-count=101','--format=%H%x00%cI%x00%an%x00%s','HEAD')
        rows=[]
        for line in raw.decode('utf-8',errors='replace').splitlines():
            parts=line.split('\0',3)
            if len(parts)==4 and re.fullmatch(r'[0-9a-f]{40,64}',parts[0]) and datetime.fromisoformat(parts[1]).astimezone().date()==day:
                rows.append(dict(zip(('commit','date','author','subject'),[parts[0],parts[1],redact(parts[2]),redact(parts[3])])))
        return {'rows':rows[:100],'more':len(rows)>100,'next_skip':skip+100}


    def _commit_parent(self, commit):
        if not re.fullmatch(r'[0-9a-f]{40,64}',commit):raise Blocked('Choose a full verified commit identifier')
        try:self.git.run('merge-base','--is-ancestor',commit,'HEAD')
        except Blocked:raise Blocked('Commit is not in the selected branch history') from None
        parents=self.git.text('show','-s','--format=%P',commit).split()
        return parents[0] if parents else None


    def changed_files(self, commit):
        parent=self._commit_parent(commit)
        args=('diff','--name-only','--no-renames','-z',parent,commit) if parent else ('diff-tree','--root','--no-commit-id','--name-only','--no-renames','-r','-z',commit)
        names=self.git.run(*args).decode('utf-8',errors='strict').split('\0');rows=[]
        for name in filter(None,names):
            try:
                Config(self.config.repository,self.config.expected_remote_url,branch=self.config.branch,files=(name,))
                allowed=True
            except Blocked:allowed=False
            rows.append({'path':name,'viewable':allowed})
        return {'parent':parent,'files':rows,'comparison':'First parent' if parent else 'Initial commit'}


    def diff_text(self, commit, name):
        # Check protection BEFORE any blob/diff command; historical content is protected too.
        Config(self.config.repository,self.config.expected_remote_url,branch=self.config.branch,files=(name,))
        parent=self._commit_parent(commit)
        paths={f['path'] for f in self.changed_files(commit)['files']}
        if name not in paths:raise Blocked('File is not part of this commit')
        for ref in filter(None,(parent,commit)):
            entry=self.git.run('ls-tree','-z',ref,'--',name)
            if entry:
                metadata=entry.split(b'\t',1)[0].decode().split()
                if metadata[0] not in ('100644','100755') or metadata[1]!='blob':raise Blocked('Link/submodule diff is unavailable')
                if int(self.git.text('cat-file','-s',metadata[2]))>1024*1024:raise Blocked('Diff is too large for inline review (1 MiB per version)')
        flags=('--no-ext-diff','--no-textconv','--no-renames','--no-color','--unified=3')
        raw=self.git.run('diff',*flags,parent,commit,'--',name) if parent else self.git.run('show','--format=',*flags,commit,'--',name)
        if len(raw)>3*1024*1024:raise Blocked('Diff is too large for inline review')
        try:text=raw.decode('utf-8')
        except UnicodeDecodeError:raise Blocked('Binary/non-UTF-8 diff cannot be displayed inline') from None
        return '\n'.join(redact(line) for line in text.splitlines()) or 'No inline text diff available.'


class Profile:
    """Optional local-only display names. No subscription, credentials or photo collection."""
    def __init__(self, folder):
        self.folder=Path(folder);safe_parents(self.folder);self.folder.mkdir(parents=True,exist_ok=True)
        self.path=self.folder/'profile.json';safe_parents(self.path)
        self.raw=self.path.read_bytes() if self.path.exists() else None
        self.data={'first_name':'','last_name':''}
        if self.raw:
            try:self.data=json.loads(self.raw);self.validate(self.data)
            except (ValueError,TypeError):raise Blocked('Local profile is invalid; existing data preserved') from None
    @staticmethod
    def validate(data):
        if not isinstance(data,dict) or set(data)!={'first_name','last_name'}:raise ValueError('Invalid profile fields')
        if any(not isinstance(v,str) or len(v)>60 or any(ord(c)<32 for c in v) for v in data.values()):raise ValueError('Use up to 60 visible characters per name')
    def save(self, first, last):
        data={'first_name':first.strip(),'last_name':last.strip()};self.validate(data);safe_parents(self.path)
        with exclusive(self.folder/'profile.lock'):
            if (self.path.read_bytes() if self.path.exists() else None)!=self.raw:raise Blocked('Profile changed in another window; reopen first')
            raw=json.dumps(data,ensure_ascii=False).encode();temporary=self.folder/('profile-'+uuid.uuid4().hex+'.tmp')
            try:
                with temporary.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
                os.replace(temporary,self.path)
            finally:
                if temporary.exists():temporary.unlink()
        self.data=data;self.raw=raw


def history_window(period, anchor):
    if not isinstance(anchor,date):raise Blocked('Choose a calendar date')
    if period=='day':start=anchor;end=start+timedelta(days=1)
    elif period=='week':start=anchor-timedelta(days=anchor.weekday());end=start+timedelta(days=7)
    elif period=='month':
        start=anchor.replace(day=1);end=date(start.year+1,1,1) if start.month==12 else date(start.year,start.month+1,1)
    elif period=='year':start=date(anchor.year,1,1);end=date(anchor.year+1,1,1)
    else:raise Blocked('Choose day, week, month or year')
    return start,end


def shift_history(period, anchor, direction):
    if direction not in (-1,1):raise Blocked('Choose previous or next')
    if period=='day':return anchor+timedelta(days=direction)
    if period=='week':return anchor+timedelta(days=7*direction)
    if period=='year':
        year=anchor.year+direction;return anchor.replace(year=year,day=min(anchor.day,calendar.monthrange(year,anchor.month)[1]))
    if period=='month':
        index=anchor.year*12+anchor.month-1+direction;year,month=divmod(index,12);month+=1
        return date(year,month,min(anchor.day,calendar.monthrange(year,month)[1]))
    raise Blocked('Choose day, week, month or year')


def _dates_args(start,end):
    return ('--since-as-filter='+(datetime.combine(start,time.min)-timedelta(seconds=1)).astimezone().isoformat(),
            '--until='+(datetime.combine(end,time.min)-timedelta(seconds=1)).astimezone().isoformat())


