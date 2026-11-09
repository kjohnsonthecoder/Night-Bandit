"""Local effects preferences, reduced-motion policy and finite Tk transitions. No threads."""
import ctypes
import json
import os
import time
import uuid
from pathlib import Path
from .model import safe_parents
from night_bandit.config import Blocked
from night_bandit.git_sync import exclusive

DEFAULTS={'appearance':'dark','reduced_motion':False,'intensity':'Subtle','notifications':True,'repository_display':'Icons'}

class Preferences:
    def __init__(self,folder):
        self.folder=Path(folder);safe_parents(self.folder);self.folder.mkdir(parents=True,exist_ok=True)
        self.path=self.folder/'effects.json';safe_parents(self.path)
        self.raw=self.path.read_bytes() if self.path.exists() else None
        if self.raw and len(self.raw)>4096:raise Blocked('Effects settings exceed safe size')
        try:self.data=json.loads(self.raw) if self.raw else dict(DEFAULTS);self.validate(self.data)
        except (ValueError,TypeError):raise Blocked('Effects settings invalid; existing file preserved') from None
    @staticmethod
    def validate(data):
        if not isinstance(data,dict) or set(data)!=set(DEFAULTS):raise ValueError('Invalid effects fields')
        if data['appearance']!='dark':raise ValueError('Only approved dark appearance is available')
        if type(data['reduced_motion']) is not bool or type(data['notifications']) is not bool:raise ValueError('Invalid preference value')
        if data['intensity'] not in ('Off','Subtle','Standard') or data['repository_display'] not in ('Icons','Names'):raise ValueError('Invalid appearance option')
    def update(self,**changes):
        data={**self.data,**changes};self.validate(data);safe_parents(self.path)
        with exclusive(self.folder/'effects.lock'):
            actual=self.path.read_bytes() if self.path.exists() else None
            if actual!=self.raw:raise Blocked('Effects changed in another window; reopen before saving')
            raw=json.dumps(data,indent=2).encode();temp=self.folder/('effects-'+uuid.uuid4().hex+'.tmp')
            try:
                with temp.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
                os.replace(temp,self.path)
            finally:
                if temp.exists():temp.unlink()
        self.data=data;self.raw=raw


def windows_motion_allowed():
    if os.name!='nt':return False
    try:
        enabled=ctypes.c_int(0)
        ok=ctypes.windll.user32.SystemParametersInfoW(0x1042,0,ctypes.byref(enabled),0)
        return bool(ok and enabled.value)
    except (AttributeError,OSError):return False


def mix(first,second,amount):
    t=max(0,min(1,amount));a=[int(first[i:i+2],16) for i in (1,3,5)];b=[int(second[i:i+2],16) for i in (1,3,5)]
    return '#'+''.join(f'{round(x+(y-x)*t):02x}' for x,y in zip(a,b))

class Motion:
    """At most 30 FPS, finite <=240ms tweens; policy rechecked each frame. No pulsing loops."""
    def __init__(self,root,preferences,system_policy=windows_motion_allowed,clock=time.monotonic):
        self.root=root;self.preferences=preferences;self.system_policy=system_policy;self.clock=clock;self.pending={}
    def enabled(self):
        p=self.preferences.data
        return not p['reduced_motion'] and p['intensity']!='Off' and self.system_policy()
    def cancel(self,owner,key,finish=False):
        item=self.pending.pop((id(owner),key),None)
        if item:
            try:owner.after_cancel(item['timer'])
            except Exception:pass
            if finish and owner.winfo_exists():item['callback'](item['target'])
    def animate(self,owner,key,start,target,callback,duration=160):
        self.cancel(owner,key)
        if not owner.winfo_exists():return
        if hasattr(owner,'bind') and not getattr(owner,'_nb_motion_cleanup',False):
            owner.bind('<Destroy>',lambda e:self.cancel_owner(owner) if e.widget==owner else None,add='+');owner._nb_motion_cleanup=True
        if not self.enabled():callback(target);return
        duration=min(240,max(100,duration if self.preferences.data['intensity']=='Standard' else min(duration,160)))
        token=(id(owner),key);began=self.clock()
        record={'timer':None,'callback':callback,'target':target,'owner':owner};self.pending[token]=record
        def tick():
            if self.pending.get(token) is not record:return
            if not owner.winfo_exists():self.pending.pop(token,None);return
            elapsed=(self.clock()-began)*1000/duration
            if not self.enabled():elapsed=1
            t=max(0,min(1,elapsed));eased=t*t*(3-2*t)
            callback(start+(target-start)*eased)
            if t>=1:self.pending.pop(token,None)
            else:record['timer']=owner.after(34,tick)
        callback(start);record['timer']=owner.after(34,tick)
    def cancel_owner(self,owner):
        for owner_id,key in list(self.pending):
            if owner_id==id(owner):self.cancel(owner,key)
    def settle(self):
        for token,item in list(self.pending.items()):
            self.pending.pop(token,None)
            try:item['owner'].after_cancel(item['timer']);item['callback'](item['target'])
            except Exception:pass


def motion_for(widget):
    current=widget
    while current is not None:
        motion=getattr(current,'nb_motion',None)
        if motion is not None:return motion
        current=getattr(current,'master',None)
    return None


def reveal(widget,distance=5):
    motion=motion_for(widget)
    if motion is None:return
    # Pack padding is visual only; actual content/operation state never waits on animation.
    def position(value):
        if widget.winfo_exists():widget.pack_configure(pady=(round(value),0))
    motion.animate(widget,'reveal',distance,0,position,160)
