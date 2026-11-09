"""Tk adapter for real operations; a single serial I/O worker, never parallel repositories."""
import queue
import threading
import tkinter as tk
from tkinter import filedialog
import webbrowser
from dataclasses import replace
from pathlib import Path
from .model import Workspace, Blocked
from .feedback import Feedback
from .visuals import Button, Entry, Combobox, BG, PANEL, MUTED, SOFT
from night_bandit.events import redact


class Integration:
    def initialize_operations(self, runtime):
        self.runtime=runtime;self._busy=False;self._job=None;self._messages=queue.Queue()
        self._cancel=threading.Event();self._stop=threading.Event();self.auto_id=None
        self._closing=False;self._tray=None;self._last_verified={};self._poll=None;self._session_message={}
        self.root.protocol('WM_DELETE_WINDOW',self.close_application)
        self.root.bind('<Destroy>',self._operations_destroyed,add='+')
        self._poll=self.root.after(80,self.drain_operations)

    def _operations_destroyed(self,event):
        if event.widget!=self.root:return
        self._cancel.set();self._stop.set()
        if self._poll:
            try:self.root.after_cancel(self._poll)
            except tk.TclError:pass
        if self._tray:self._tray.close();self._tray=None

    def run_task(self,key,task,done):
        if self._busy:
            self.toast.show('Stop the active operation before starting another.');return False
        self._busy=True;self.indicator.start();self.footer.set('Working — this operation is isolated to its repository')
        def work():
            try:self._messages.put(('done',key,done,task()))
            except Exception as e:
                reason=redact(str(e)) if isinstance(e,Blocked) else 'Operation failed; inspect activity and recovery before retrying'
                self._messages.put(('error',key,None,reason))
        self._job=threading.Thread(target=work,daemon=True,name='night-bandit-serial-io')
        self._job.start();return True

    def drain_operations(self):
        try:
            while True:
                kind,key,callback,value=self._messages.get_nowait()
                if kind=='tray':self.tray_action(value);continue
                if kind=='state':self.session_state(key,value);continue
                self._busy=False;self.indicator.stop()
                if self.auto_id==key:self.auto_id=None
                if kind=='error':
                    self._session_message[key]='Blocked — '+value;self.save_status.set('FAILED OR BLOCKED')
                    self.serial.errors[key]=value;self._feedback_by_repo.setdefault(key,Feedback()).event({'event':'blocked','reason':value})
                    self.footer.set('Blocked — '+value)
                    try:self.runtime.events.emit('blocked',reason=value)
                    except OSError:pass
                    if not self._closing:self.dialogs.showerror('Operation needs attention',value,parent=self.root)
                elif not self._closing:
                    self.serial.errors.pop(key,None)
                    try:callback(value)
                    except Exception as error:
                        self.footer.set('Completion needs attention')
                        self.dialogs.showerror('Completion needs attention',redact(str(error)) if isinstance(error,Blocked) else 'Saved work preserved; inspect activity before retrying.',parent=self.root)
                if self._closing and not self._busy:self.root.destroy();return
                self.refresh_controls()
        except queue.Empty:pass
        self._poll=self.root.after(80,self.drain_operations)

    def live_status(self,w):
        from .presentation import workspace_status
        status=workspace_status(w,self._feedback_by_repo.get(w.id),self.reviewed.get(w.id),self.serial.errors.get(w.id))
        if self.runtime.bound.get(w.id)==w:
            status['connection']='GitHub scope verified';status['scope']='Scope confirmed'
            if w.id not in self.serial.errors:status['label']='Connected • stopped'
        if self.auto_id==w.id:
            status['label']='Automatic session active';status['sync']='Active'
        if w.id in self._last_verified:status['push']=self._last_verified[w.id][:12]
        return status

    def refresh_controls(self):
        if hasattr(self,'monitoring_count'):
            self.monitoring_count.set(f'{len(self.store.workspaces)} workspaces    ·    {1 if self.auto_id else 0} monitoring in this window')
        for key,badge in getattr(self,'home_badges',{}).items():
            if badge.winfo_exists():
                w=next((x for x in self.store.workspaces if x.id==key),None)
                if w:
                    state=self.live_status(w);badge.text=state['label'];badge.tone=state['tone'];badge.configure(width=badge.face.measure(badge.text)+32);badge.draw()
        if self.view=='workspace' and self.current and not self.current.draft:
            w=self.current;ready=self.runtime.bound.get(w.id)==w
            self.start.configure(state='normal' if ready and not self._busy else 'disabled')
            self.stop.configure(state='normal' if self.auto_id==w.id and not self._stop.is_set() else 'disabled')
            if hasattr(self,'sync_badge'):
                self.sync_badge.text='Automatic session active' if self.auto_id==w.id else 'Automatic sync off'
                self.sync_badge.configure(width=self.sync_badge.face.measure(self.sync_badge.text)+32);self.sync_badge.draw()
            if hasattr(self,'last_push_status'):
                self.last_push_status.set('Last verified push: '+self._last_verified.get(w.id,'None verified in this session'))
            if hasattr(self,'runtime_status'):self.runtime_status.set(self._session_message.get(w.id,'Connected scope reviewed — automatic sync off' if ready else 'Connect GitHub and review scope before sync'))

    def connect_github(self,w):
        if self._busy:self.toast.show('Stop the active operation first.');return
        dialog=tk.Toplevel(self.root);dialog.title('Connect GitHub');dialog.geometry('800x650');dialog.configure(bg=BG);dialog.transient(self.root)
        form=tk.Frame(dialog,bg=BG);form.pack(fill='both',expand=True,padx=24,pady=20)
        self.label(form,'Connect your repository',20,bg=BG).pack(anchor='w')
        hint=tk.StringVar(value='Use saved sign-in or connect with GitHub. No repository permissions will be changed.')
        tk.Label(form,textvariable=hint,bg=BG,fg=SOFT,wraplength=730,justify='left').pack(anchor='w',pady=12)
        name=tk.StringVar();folder=tk.StringVar(value=w.folder);file=tk.StringVar(value=w.master_file)
        bar=tk.Frame(form,bg=BG);bar.pack(fill='x',pady=8)
        def alive():return bool(dialog.winfo_exists())
        def loaded(rows):
            if not alive():return
            picker.field.configure(values=[row['full_name'] for row in rows])
            hint.set('GitHub sign-in verified. Choose a repository, its existing local checkout and one tracked text file.')
        def login():
            self._cancel.clear()
            # Capture Tk values before moving work off the UI thread.
            def got(data):
                if not alive():self._cancel.set();return
                hint.set('Enter code '+data['user_code']+' at https://github.com/login/device. Waiting for authorization…')
                webbrowser.open('https://github.com/login/device')
                self.run_task(w.id,lambda:self.runtime.finish(data,self._cancel),loaded)
            self.run_task(w.id,self.runtime.begin,got)
        Button(bar,text='Sign in with GitHub',style='Accent.TButton',command=login).pack(side='left')
        Button(bar,text='Use saved sign-in / Refresh',command=lambda:self.run_task(w.id,self.runtime.discover,loaded)).pack(side='left',padx=8)
        self.label(form,'Repository',bg=BG).pack(anchor='w',pady=(10,4))
        picker=Combobox(form,textvariable=name,values=(),state='readonly');picker.pack(fill='x')
        for label,var,command in [('Local repository folder',folder,lambda:folder.set(filedialog.askdirectory(parent=dialog) or folder.get())),('Master file',file,None)]:
            self.label(form,label,bg=BG).pack(anchor='w',pady=(12,4));row=tk.Frame(form,bg=BG);row.pack(fill='x');Entry(row,textvariable=var).pack(side='left',fill='x',expand=True)
            if command:Button(row,text='Browse…',command=command).pack(side='right',padx=8)
            else:
                def choose():
                    path=filedialog.askopenfilename(parent=dialog,initialdir=folder.get() or None)
                    if not path:return
                    try:file.set(Path(path).absolute().relative_to(Path(folder.get()).absolute()).as_posix())
                    except ValueError:self.dialogs.showerror('Choose a file inside the checkout','Select a text file inside the selected repository.',parent=dialog)
                Button(row,text='Browse…',command=choose).pack(side='right',padx=8)
        def save():
            selected=next((x for x in self.runtime.rows if x['full_name']==name.get()),None)
            if not selected:self.dialogs.showerror('Choose a repository','Refresh and select an authorized private repository.',parent=dialog);return
            try:
                candidate=Workspace(w.id,selected['full_name'].split('/')[-1] if w.draft else w.name,w.icon,folder.get(),selected['url'],selected['branch'],file.get())
                prompt=f'{candidate.remote}\nBranch: {candidate.branch}\nCheckout: {candidate.folder}\nOnly file: {candidate.master_file}\n\nLock this scope? This does not start synchronization.'
                if not self.dialogs.askyesno('Confirm repository and selected file',prompt,parent=dialog):return
                def saved(result):
                    try:self.store.upsert(candidate)
                    except Exception:
                        self.runtime.bound.pop(candidate.id,None);raise
                    self._feedback_by_repo.setdefault(w.id,Feedback()).connection(verified=True)
                    self.reviewed.pop(w.id,None)
                    if alive():dialog.destroy()
                    self.workspace(candidate)
                self.run_task(w.id,lambda:self.runtime.bind(candidate,approved=True),saved)
            except (Blocked,OSError,ValueError) as error:self.dialogs.showerror('Scope needs attention',redact(str(error)),parent=dialog)
        Button(form,text='Connect selected repository',style='Accent.TButton',command=save).pack(anchor='e',pady=18)
        def cancel():self._cancel.set();dialog.destroy()
        dialog.protocol('WM_DELETE_WINDOW',cancel)
        Button(form,text='Cancel sign-in / Close',command=cancel).pack(anchor='e')

    def dry_run_connected(self,w):
        def done(result):
            self._session_message[w.id]='Dry-run complete — '+result['status']+'; no commit or push'
            self.footer.set(self._session_message[w.id]);self.toast.show(self._session_message[w.id]);self.refresh_controls()
        self.run_task(w.id,lambda:self.runtime.dry_run(w),done)

    def start_sync(self,w):
        if self._busy:return
        prompt=f'Repository: {w.remote}\nBranch: {w.branch}\nOnly file: {Path(w.folder)/w.master_file}\nReviewed exclusions preserved: {len(self.runtime.base_config(w).reviewed_exclusions)}\n\nAutomatically commit and normally push saved changes, including existing unsynced edits, for this session? No other workspace can run while it listens. Stop ends this session. Closing the window keeps it in the tray; Exit stops it. No Windows startup task is created.'
        if not self.dialogs.askyesno('Start automatic synchronization?',prompt,parent=self.root):return
        try:
            self.runtime.config(w)  # Fail closed before launching tray/session.
            if self._tray is None:
                from night_bandit.tray import NativeTray
                self._tray=NativeTray(lambda action:self._messages.put(('tray',None,None,action)),lambda:self.auto_id is not None)
        except (Blocked,OSError) as error:
            self.dialogs.showerror('Cannot start automatic sync',redact(str(error)),parent=self.root);return
        self._stop.clear();self.auto_id=w.id
        def notify(value):self._messages.put(('state',w.id,None,value))
        def done(result):
            self._session_message[w.id]='Automatic sync stopped';self.footer.set('Automatic sync stopped');self.refresh_controls()
        self.run_task(w.id,lambda:self.runtime.automatic(w,self._stop,notify,approved=True),done)
        self.refresh_controls()

    def session_state(self,key,value):
        self._session_message[key]=value.get('message',self._session_message.get(key,value['phase']))
        feedback=self._feedback_by_repo.setdefault(key,Feedback())
        if value['phase']=='waiting':feedback.event({'event':'detected'})
        elif value['phase']=='syncing':feedback.event({'event':'commit_preparation'})
        elif value['phase']=='blocked':feedback.event({'event':'blocked','reason':value.get('message')})
        if value['phase']=='audit':
            record=value['record'];feedback.event(record,receipt_verified=record.get('event')=='pushed')
            if feedback.phase=='verified':self._last_verified[key]=feedback.commit
            if hasattr(self,'_log_widget') and self._log_widget[0]==key:
                _,text,level=self._log_widget
                if text.winfo_exists() and (level.get()=='ALL' or record.get('severity')==level.get()):
                    text.configure(state='normal');text.insert('end',f"\n{record.get('timestamp','')} {record.get('event','')} {record.get('reason','')} {record.get('commit','')}");text.configure(state='disabled');text.see('end')
        if self.current and self.current.id==key:
            self.save_status.set(feedback.label.upper());self.footer.set(self._session_message[key])
        if value['phase'] in ('listening','stopped','blocked'):self.indicator.stop()
        elif value['phase'] in ('checking','waiting','syncing'):self.indicator.start()
        if self._tray:
            try:self._tray.update(self._session_message[key])
            except Blocked:self.stop_sync();self.root.deiconify()
        self.refresh_controls()

    def stop_sync(self):
        self._stop.set()
        if self.auto_id:self._session_message[self.auto_id]='Stopping after the current operation…'
        self.refresh_controls()

    def tray_action(self,action):
        if action=='open':self.root.deiconify();self.root.lift()
        elif action=='stop':self.stop_sync()
        elif action=='exit':self._closing=True;self.stop_sync();self._cancel.set();self.close_application()
        elif action=='error':self.stop_sync();self.root.deiconify()

    def close_application(self):
        if self.auto_id and not self._closing:self.root.withdraw();return
        if self._busy:
            self._closing=True;self._cancel.set();self.stop_sync();self.footer.set('Waiting for current operation to stop safely…');return
        self.root.destroy()

    def documentation(self):
        self.view='documentation';self.current=None;self.clear();self.build_rail()
        card=self.card('Documentation • How to use NIGHT BANDIT')
        paragraphs=[
          '1. Add Repository. A fresh application has no repository workspaces. GitHub is supported; GitLab and Bitbucket are Coming Soon.',
          '2. Connect GitHub. Use your saved sign-in or follow GitHub device authorization. NIGHT BANDIT supplies its own application settings; you never need to enter a Client ID, secret or token.',
          '3. Select an authorized private repository. Choose its existing local checkout and one tracked UTF-8 text file. Review the exact repository, branch and file before saving. This build does not clone or initialize repositories.',
          '4. Run safe dry-run. It checks the real engine without staging, committing, pushing or changing the index/refs. Resolve exclusions, divergence or recovery blockers; do not bypass them.',
          '5. Start Automatic Sync. Review the explicit session scope. Saved changes to that one file, including existing unsynced edits, may be committed and pushed. Success appears only after the engine verifies the remote commit/tree/content.',
          '6. Stop ends listening after the current operation. Closing an active session keeps it in the Windows tray; use Stop or Exit there. No Windows startup service/task is created.',
          '7. Configure several workspaces; run one automatic session at a time. Stop it before connecting, editing scopes or inspecting another checkout. Each repository keeps its own Git journal and audit log.',
          '8. Browse Day/Week/Month/Year history, select a day and commit to inspect file diffs. History covers commits reachable from the local branch; remote-only commits are not fetched by the calendar.',
          'Troubleshooting: authentication expiry -> reconnect; permissions missing -> owner reviews App access; divergence/foreign locks -> stop and inspect Recovery with the repository owner before restarting. Never delete locks or reset work to bypass a blocker.',
          'FAQ is planned. Settings includes Check for updates: compare the running version with the public release, then follow the manual application-checkout instructions. Stop and Exit before pulling updates; the app does not install them.'
        ]
        for text in paragraphs:self.label(card,text,color=MUTED,wraplength=880).pack(anchor='w',pady=9)
        self.footer.set('Documentation • Application instructions')

    def release_notes(self):
        self.view='release_notes';self.current=None;self.clear();self.build_rail()
        card=self.card('Release Notes')
        for text in ['Version 0.3.1 • Source release',
          'Added: Settings Check for updates uses anonymous public-release version comparison and manual Git update instructions; no automatic installation.',
          'Updated: GitHub sign-in uses built-in application settings; no registration fields for users.',
          'Added: real GitHub device authorization, saved-session discovery and reviewed repository/file binding in the new interface.',
          'Added: safe dry-run and explicitly authorized automatic sync through the existing engine; one serial session; Stop/tray controls and per-repository activity.',
          'Added: dedicated Documentation and Release Notes. New state starts with zero repositories; concept fixtures are not bundled defaults.',
          'Retained: crimson/black presentation, private fonts, reduced motion, editable workspace names/icons and local Git calendar/diffs.',
          'Limits: opening the app never starts a listener. GitLab/Bitbucket Coming Soon. Existing tracked-file onboarding only; empty-repository first commit and recovery interfaces are not included in this desktop release.',
          'Not included: installer, executable signing, automatic update installation or concurrent synchronization sessions. See README for availability and limitations.']:
            self.label(card,text,14 if 'Unreleased' in text else 10,color=MUTED,wraplength=880).pack(anchor='w',pady=10)
        self.footer.set('Release Notes • Version 0.3.1')
