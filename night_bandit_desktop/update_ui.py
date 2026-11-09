"""Manual update-check UI. Anonymous HTTP work is separate from Git synchronization."""
import queue
import threading
import tkinter as tk
import webbrowser
from night_bandit import __version__
from .updates import check_updates,UpdateResult
from .update_source import PUBLIC_GITHUB_REPOSITORY
from .visuals import Button,PANEL,FIELD,TEXT,MUTED

class UpdateService:
    def __init__(self,checker=check_updates):self.checker=checker;self.job=None;self.messages=queue.Queue();self.last=None
    def start(self):
        if self.job and self.job.is_alive():return False
        checker=self.checker;messages=self.messages
        def work():
            try:result=checker()
            except Exception:result=UpdateResult('error',__version__,message='The update check could not complete. Please try again.')
            messages.put(result)
        self.job=threading.Thread(target=work,daemon=True,name='night-bandit-public-version-check');self.job.start();return True
    def result(self):
        try:
            while True:self.last=self.messages.get_nowait()
        except queue.Empty:return self.last

class UpdatePanel:
    def __init__(self,app):
        self.app=app;self.root=app.root;self.timer=None;self.closed=False;self.release_url='';self.instructions=''
        if not hasattr(app,'update_service'):app.update_service=UpdateService()
        self.service=app.update_service
        card=app.card('Application updates');self.card=card
        app.label(card,'Running version: '+__version__,role='mono').pack(anchor='w')
        app.label(card,'Public source: '+(PUBLIC_GITHUB_REPOSITORY or 'Not configured'),color=MUTED).pack(anchor='w',pady=7)
        row=tk.Frame(card,bg=PANEL);row.pack(fill='x',pady=8)
        self.check=Button(row,text='Check for updates',style='Accent.TButton',command=self.start);self.check.pack(side='left')
        self.notes=Button(row,text='View release notes',state='disabled',command=self.open_notes);self.notes.pack(side='left',padx=10)
        self.copy=Button(row,text='Copy update instructions',state='disabled',command=self.copy_instructions);self.copy.pack(side='left')
        self.status=tk.StringVar(value='Check when you choose. Updates are installed manually after you review the instructions.')
        tk.Label(card,textvariable=self.status,bg=PANEL,fg=TEXT,wraplength=880,justify='left',font=(app.fonts['primary'],10)).pack(anchor='w',pady=9)
        self.text=tk.Text(card,height=15,bg=FIELD,fg=TEXT,font=(app.fonts['mono'],10),wrap='word',relief='flat',padx=12,pady=12,state='disabled')
        card.bind('<Destroy>',lambda e:self.close() if e.widget==card else None,add='+')
        if self.service.job and self.service.job.is_alive():self.check.configure(state='disabled');self.status.set('Checking the public GitHub release…');self.poll()
        elif self.service.result():self.show(self.service.last)
    def start(self):
        if self.closed:return
        self.service.result()  # Drain any previous completed check before requesting another.
        if not self.service.start():return
        self.check.configure(state='disabled');self.status.set('Checking the public GitHub release…');self.poll()
    def poll(self):
        self.timer=None
        if self.closed:return
        if self.service.job and self.service.job.is_alive():self.timer=self.root.after(100,self.poll);return
        result=self.service.result()
        if result:self.show(result)
        self.check.configure(state='normal')
    def show(self,result):
        detail=(' Running: '+result.current+'. Latest published: '+result.latest+'.') if result.latest else ''
        self.status.set(result.message+detail);self.release_url=result.release_url;self.instructions=result.instructions
        self.notes.configure(state='normal' if self.release_url else 'disabled');self.copy.configure(state='normal' if self.instructions else 'disabled')
        self.text.configure(state='normal');self.text.delete('1.0','end');self.text.insert('1.0',self.instructions);self.text.configure(state='disabled')
        if self.instructions:self.text.pack(fill='x',pady=(8,4))
        else:self.text.pack_forget()
    def open_notes(self):
        if self.release_url:webbrowser.open(self.release_url)
    def copy_instructions(self):
        if self.instructions:self.root.clipboard_clear();self.root.clipboard_append(self.instructions)
    def close(self):
        self.closed=True
        if self.timer:
            try:self.root.after_cancel(self.timer)
            except tk.TclError:pass
            self.timer=None
