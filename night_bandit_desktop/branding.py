"""Verified project assets and process-private fonts. Never installs fonts in Windows."""
import atexit
import ctypes
import hashlib
import json
import math
import os
import tkinter as tk
from pathlib import Path
from tkinter import font
from night_bandit.config import Blocked

ASSETS=Path(__file__).resolve().parent.parent/'assets/premium-v1'
_loaded=[]


def load_private_fonts():
    if _loaded or os.name!='nt':return
    manifest=json.loads((ASSETS/'fonts/sources.json').read_text())
    add=ctypes.windll.gdi32.AddFontResourceExW
    add.argtypes=[ctypes.c_wchar_p,ctypes.c_uint,ctypes.c_void_p];add.restype=ctypes.c_int
    for name,record in manifest['files'].items():
        if not name.endswith('.ttf'):continue
        path=ASSETS/'fonts'/name
        if hashlib.sha256(path.read_bytes()).hexdigest()!=record['sha256']:raise Blocked('Bundled font hash changed')
        if add(str(path),0x10,None)>0:_loaded.append(str(path)) # FR_PRIVATE, process only


def release_fonts():
    if os.name=='nt':
        for path in _loaded:
            try:ctypes.windll.gdi32.RemoveFontResourceExW(ctypes.c_wchar_p(path),0x10,None)
            except (AttributeError,OSError):pass
    _loaded.clear()

atexit.register(release_fonts)


def resolve_fonts(root):
    load_private_fonts()
    result={};requested={'primary':'Manrope','display':'Sora','mono':'JetBrains Mono'}
    for role,name in requested.items():
        candidate=font.Font(root=root,family=name,size=10)
        actual=candidate.actual('family')
        result[role]=name if actual.casefold()==name.casefold() else ('Consolas' if role=='mono' else 'Segoe UI')
    root.nb_fonts=result
    for name,role in [('TkDefaultFont','primary'),('TkTextFont','primary'),('TkFixedFont','mono'),('TkHeadingFont','display'),('TkCaptionFont','primary'),('TkSmallCaptionFont','primary'),('TkIconFont','primary')]:
        try:font.nametofont(name,root=root).configure(family=result[role])
        except tk.TclError:pass
    return result


def family(widget,role='primary'):
    current=widget
    while current is not None:
        fonts=getattr(current,'nb_fonts',None)
        if fonts:return fonts.get(role,'Segoe UI')
        current=getattr(current,'master',None)
    return 'Consolas' if role=='mono' else 'Segoe UI'

class Assets:
    def __init__(self,root):
        self.root=root;self.manifest=json.loads((ASSETS/'manifest.json').read_text())
        self.images={}
        for name,record in self.manifest['files'].items():
            data=(ASSETS/name).read_bytes()
            if hashlib.sha256(data).hexdigest()!=record['sha256']:raise Blocked('Supplied UI asset hash changed')
            self.images[name]=tk.PhotoImage(master=root,data=data,format='png')
        candidate=self.images.get('icon-transparent-candidate-v1.png')
        self.icon_candidate=True
        if candidate is None:candidate=self.images['icon-source.png'];self.icon_candidate=False
        # Display copies only. Exact source/derivative files are never changed here.
        # Generated candidate includes transparent padding; this display crop removes padding only.
        crop=self.manifest.get('transparent_display_bbox')
        if crop and self.icon_candidate:
            icon=tk.PhotoImage(master=root)
            icon.tk.call(str(icon),'copy',str(candidate),'-from',*crop)
        else:icon=candidate
        step=max(1,math.ceil(max(icon.width(),icon.height())/72))
        self.header_icon=icon.subsample(step,step)
        self.window_icon=icon.subsample(max(1,math.ceil(max(icon.width(),icon.height())/192)))
        background=self.images['background-source.png']
        self.background=background.subsample(max(1,math.ceil(background.width()/1000)))
        root.iconphoto(True,self.window_icon)

class Header(tk.Canvas):
    def __init__(self,parent,assets,**kwargs):
        super().__init__(parent,height=92,bg='#09080a',highlightthickness=0,bd=0,**kwargs)
        self.assets=assets;self.status='PRIVATE DESKTOP PREVIEW';self.bind('<Configure>',self.draw)
    def set(self,text):self.status=text;self.draw()
    def draw(self,event=None):
        if not self.winfo_exists():return
        w=max(1,self.winfo_width());self.delete('all')
        self.create_image(w, -230,image=self.assets.background,anchor='ne')
        self.create_rectangle(0,0,w,92,fill='#08070a',stipple='gray75',outline='')
        self.create_rectangle(0,0,max(370,w-670),92,fill='#09080a',outline='')
        self.create_line(0,91,w,91,fill='#38202b')
        self.create_image(51,45,image=self.assets.header_icon)
        self.create_text(99,32,text='NIGHT BANDIT',anchor='w',fill='#f6eff2',font=(family(self,'display'),20,'bold'))
        self.create_text(100,63,text='YOUR FILES. YOUR CONTROL.',anchor='w',fill='#b69da8',font=(family(self),8))
        self.create_text(w-24,46,text=self.status,anchor='e',fill='#c6b3bc',font=(family(self),9))

class EmptyIllustration(tk.Canvas):
    """Code-native repository illustration using supplied emblem, not a recreated locked mascot."""
    def __init__(self,parent,assets,width=260,height=130,bg='#191416'):
        super().__init__(parent,width=width,height=height,bg=bg,highlightthickness=0,bd=0)
        self.create_oval(78,16,182,120,fill='#28131c',outline='#492333')
        self.create_line(30,66,79,66,fill='#6d3047',width=2)
        self.create_line(181,66,232,66,fill='#6d3047',width=2)
        for x,y in [(30,66),(231,66),(206,34)]:self.create_oval(x-5,y-5,x+5,y+5,fill='#c52a4d',outline='')
        self.create_image(130,65,image=assets.header_icon)
