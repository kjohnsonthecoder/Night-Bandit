"""NIGHT BANDIT desktop workspaces and presentation."""
import calendar
import getpass
import hashlib
import platform
import tkinter as tk
import uuid
import webbrowser
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from .integration import Integration
from .runtime import Runtime
from .model import Store, Profile, Workspace, LocalReader, SerialCoordinator, Blocked, history_window, shift_history
from night_bandit.events import redact
from .effects import Preferences,Motion,reveal,mix,windows_motion_allowed
from .branding import Assets,Header,EmptyIllustration,resolve_fonts
from .experience import Dialogs,Toast,Indicator,Splash
from .feedback import Feedback
from .presentation import Badge,workspace_status
from .update_ui import UpdatePanel
from .visuals import Button, Entry, Combobox, Panel, BG, PANEL, FIELD, BORDER, TEXT, MUTED, CRIMSON, SOFT

class ScrollPage:
    def __init__(self,parent,bg=BG):
        self.frame=tk.Frame(parent,bg=bg)
        self.canvas=tk.Canvas(self.frame,bg=bg,highlightthickness=0)
        self.scroll=ttk.Scrollbar(self.frame,orient='vertical',command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side='right',fill='y');self.canvas.pack(side='left',fill='both',expand=True)
        self.inner=tk.Canvas(self.canvas,bg=bg,highlightthickness=0,bd=0,width=1,height=1)
        self._backdrop=getattr(parent.winfo_toplevel(),'nb_assets',None)
        self._wheel_remainder=0.0
        self.item=self.canvas.create_window(0,0,window=self.inner,anchor='nw')
        self.inner.bind('<Configure>',self.update_region)
        self.canvas.bind('<Configure>',lambda e:self.canvas.itemconfigure(self.item,width=e.width))
    def update_region(self,event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))
        self.inner.delete('ambient')
        if self._backdrop:
            w=max(1,self.inner.winfo_width());h=max(1,self.inner.winfo_height())
            self.inner.create_image(w,max(0,h-self._backdrop.background.height()),image=self._backdrop.background,anchor='ne',tags='ambient')
            self.inner.create_rectangle(0,0,w,h,fill='#08070a',stipple='gray75',outline='',tags='ambient')
            self.inner.tag_lower('ambient')
    def scroll_delta(self,delta):
        if not delta:return None
        self._wheel_remainder+=delta/120
        units=int(self._wheel_remainder)
        if units:
            self._wheel_remainder-=units;self.canvas.yview_scroll(-units,'units')
        return 'break'
    def wheel(self,event):
        return self.scroll_delta(event.delta)

class Desktop(Integration):
    def __init__(self,root,state_dir,show_startup=False,runtime=None):
        self.root=root;self.store=Store(state_dir);self.profile=Profile(state_dir);self.serial=SerialCoordinator()
        self.view='home';self.current=None;self.reviewed={};self.statuses={};self.art=None;self.brand_art=None
        self.art_status='Approved artwork not supplied';self.history_anchor=date.today();self.history_period='week';self.history_data=None
        self.preferences=Preferences(state_dir);self.fonts=resolve_fonts(root);self.assets=Assets(root);root.nb_assets=self.assets
        self.motion=Motion(root,self.preferences);root.nb_motion=self.motion
        self.dialogs=Dialogs(root,self.assets);self.toast=Toast(root,self.preferences);self.feedback=Feedback();self._feedback_by_repo={};self._activity_records={}
        root.title('NIGHT BANDIT');root.geometry('1280x940');root.minsize(960,720);root.configure(bg=BG)
        style=ttk.Style(root);style.theme_use('clam')
        style.configure('.',font=(self.fonts['primary'],10),background=BG,foreground=TEXT)
        style.configure('TFrame',background=BG);style.configure('TLabel',background=BG,foreground=TEXT)
        style.configure('TButton',background='#25262e',foreground=TEXT,padding=(13,9),borderwidth=0)
        style.map('TButton',background=[('active','#38313c'),('disabled','#22232a')],foreground=[('disabled','#7c7b88')])
        style.configure('Accent.TButton',background=CRIMSON)
        style.map('Accent.TButton',background=[('active','#d12b4e'),('disabled','#492332')])
        style.configure('Rail.TButton',padding=(5,13),background=BG,font=(self.fonts['display'],11))
        style.configure('Selected.Rail.TButton',padding=(5,13),background=CRIMSON,font=(self.fonts['display'],11))
        style.configure('TEntry',fieldbackground=FIELD,foreground=TEXT,insertcolor=TEXT,bordercolor=BORDER,padding=10)
        style.configure('TCombobox',fieldbackground=FIELD,foreground=TEXT,arrowcolor=TEXT,padding=8)
        style.map('TCombobox',fieldbackground=[('readonly',FIELD)],selectbackground=[('readonly',FIELD)],foreground=[('readonly',TEXT)])
        style.configure('Treeview',fieldbackground=FIELD,background=FIELD,foreground=TEXT,rowheight=30,borderwidth=0)
        style.configure('Treeview.Heading',background='#24252d',foreground=TEXT,padding=8)
        style.configure('History.Treeview',font=(self.fonts['mono'],10))
        style.map('Treeview',background=[('selected',CRIMSON)])
        style.configure('Soft.TEntry',fieldbackground=FIELD,foreground=TEXT,insertcolor=TEXT,
                        borderwidth=0,bordercolor=FIELD,lightcolor=FIELD,darkcolor=FIELD,padding=0,
                        selectbackground=CRIMSON,selectforeground=TEXT)
        style.configure('Soft.TCombobox',fieldbackground=FIELD,background=FIELD,foreground=TEXT,
                        borderwidth=0,bordercolor=FIELD,lightcolor=FIELD,darkcolor=FIELD,arrowcolor=MUTED,padding=0)
        style.map('Soft.TCombobox',fieldbackground=[('readonly',FIELD)],background=[('readonly',FIELD)],
                  selectbackground=[('readonly',FIELD)],selectforeground=[('readonly',TEXT)],foreground=[('readonly',TEXT)])
        style.configure('Treeview',bordercolor=FIELD,lightcolor=FIELD,darkcolor=FIELD,borderwidth=0)
        style.configure('Treeview.Heading',bordercolor=PANEL,lightcolor=PANEL,darkcolor=PANEL,borderwidth=0,relief='flat')
        style.configure('Vertical.TScrollbar',background=BORDER,troughcolor=BG,bordercolor=BG,
                        lightcolor=BG,darkcolor=BG,arrowcolor=MUTED,borderwidth=0)
        style.configure('Horizontal.TScrollbar',background=BORDER,troughcolor=BG,bordercolor=BG,
                        lightcolor=BG,darkcolor=BG,arrowcolor=MUTED,borderwidth=0)
        root.columnconfigure(1,weight=1);root.rowconfigure(1,weight=1)
        self.header=Header(root,self.assets);self.header.grid(row=0,column=0,columnspan=2,sticky='ew')
        self.save_status=tk.StringVar(value='LOCAL PREVIEW • NOT CONNECTED')
        self.save_status.trace_add('write',lambda *args:self.header.set(self.save_status.get()))
        self.rail=tk.Frame(root,bg=BG,width=76);self.rail.grid(row=1,column=0,sticky='ns',padx=(12,8),pady=10);self.rail.grid_propagate(False)
        self.rail.rowconfigure(2,weight=1);self.rail.columnconfigure(0,weight=1)
        self.content=tk.Frame(root,bg=BG);self.content.grid(row=1,column=1,sticky='nsew',padx=(8,20),pady=(8,0))
        self.footer=tk.StringVar(value='Stopped • No automatic session')
        foot=tk.Frame(root,bg=BG);foot.grid(row=2,column=0,columnspan=2,sticky='ew',padx=22,pady=12)
        tk.Label(foot,textvariable=self.footer,bg=BG,fg=MUTED).pack(side='left')
        link=tk.Label(foot,text='Frontline Consultant ↗',bg=BG,fg=SOFT,cursor='hand2');link.pack(side='right')
        link.bind('<Button-1>',lambda e:webbrowser.open('https://frontlineconsultant.com'))
        self.indicator=Indicator(root);self.indicator.grid(row=3,column=0,columnspan=2,sticky='ew')
        root.bind_all('<MouseWheel>',self.wheel,add='+')
        startup=Splash(root,self.assets) if show_startup else None
        if startup:root.deiconify();root.update_idletasks()
        self.initialize_operations(runtime or Runtime(Path(state_dir)/'connection'));self.build_rail();self.home()
        if startup:startup.destroy()
        root.bind('<Destroy>',lambda e:(self.motion.settle(),self.toast.close()) if e.widget==root else None,add='+')
    def wheel(self,event):
        widget=event.widget
        if isinstance(widget,(tk.Text,tk.Listbox,ttk.Treeview)):return
        try:
            if widget.winfo_toplevel()!=self.root:return
            pointer=self.root.winfo_containing(event.x_root,event.y_root)
        except (tk.TclError,AttributeError):pointer=widget
        current=pointer or widget
        while current is not None:
            if current==self.rail:return self.rail_page.scroll_delta(event.delta)
            if current==self.content:return self.page.scroll_delta(event.delta)
            current=getattr(current,'master',None)
        # Some native wheel messages are targeted at the toplevel rather than the child under the pointer.
        if widget==self.root:
            x=event.x_root-self.root.winfo_rootx()
            return (self.rail_page if x<self.content.winfo_x() else self.page).scroll_delta(event.delta)
    def clear(self):
        for widget in self.content.winfo_children():self.motion.cancel_owner(widget);widget.destroy()
        self.page=ScrollPage(self.content);self.page.frame.pack(fill='both',expand=True);self.body=self.page.inner;reveal(self.page.frame,5)
    def label(self,parent,text,size=10,color=TEXT,bg=PANEL,role=None,**kwargs):
        return tk.Label(parent,text=text,font=(self.fonts[role or ('display' if size>=13 else 'primary')],size,'bold' if size>=13 else 'normal'),fg=color,bg=bg,justify='left',**kwargs)
    def card(self,title=None):
        surface=Panel(self.body,padding=26,radius=28)
        surface.pack(fill='x',padx=3,pady=(0,20));card=surface.inner
        if title:self.label(card,title,16).pack(anchor='w',pady=(0,18))
        return card
    def build_rail(self):
        for widget in self.rail.winfo_children():widget.destroy()
        named=self.preferences.data['repository_display']=='Names';self.rail.configure(width=180 if named else 76)
        Button(self.rail,text='Home' if named else '⌂',style=('Accent.TButton' if self.view=='home' else 'TButton') if named else ('Selected.Rail.TButton' if self.view=='home' else 'Rail.TButton'),command=self.home).grid(row=0,column=0,sticky='ew',pady=3)
        Button(self.rail,text='+  Add Repository' if named else '+',style='TButton' if named else 'Rail.TButton',command=self.add_repository).grid(row=1,column=0,sticky='ew',pady=6)
        self.rail_page=ScrollPage(self.rail);self.rail_page.frame.grid(row=2,column=0,sticky='nsew',pady=8);self.rail_page.scroll.pack_forget()
        for item in self.store.workspaces:
            active=self.current is not None and self.current.id==item.id
            button=Button(self.rail_page.inner,text=(item.name[:20]+'…' if len(item.name)>20 else item.name) if named else item.icon,style=('Accent.TButton' if active else 'TButton') if named else ('Selected.Rail.TButton' if active else 'Rail.TButton'),command=lambda w=item:self.workspace(w));button.pack(fill='x',pady=5)
            button.bind('<Enter>',lambda e,w=item:self.footer.set(w.name+' • '+('Setup not complete' if w.draft else 'Stopped')))
        Button(self.rail,text='Settings' if named else '⚙',style=('Accent.TButton' if self.view=='settings' else 'TButton') if named else ('Selected.Rail.TButton' if self.view=='settings' else 'Rail.TButton'),command=self.settings).grid(row=5,column=0,sticky='ew',pady=8)
        Button(self.rail,text='Documentation' if named else 'DOC',command=self.documentation).grid(row=3,column=0,sticky='ew',pady=4)
        Button(self.rail,text='Release Notes' if named else 'NEW',command=self.release_notes).grid(row=4,column=0,sticky='ew',pady=4)
    def home(self):
        self.view='home';self.current=None;self.clear();self.build_rail();profile=self.card('Profile information')
        avatar=tk.Canvas(profile,width=102,height=102,bg=PANEL,highlightthickness=0);avatar.pack(side='left',anchor='n',padx=(0,24))
        initials=''.join(v[:1] for v in self.profile.data.values()).upper() or getpass.getuser()[:2].upper()
        avatar.create_oval(3,3,99,99,fill=CRIMSON,outline=CRIMSON);avatar.create_text(51,51,text=initials,fill=TEXT,font=(self.fonts['display'],26))
        fields=tk.Frame(profile,bg=PANEL);fields.pack(side='left',fill='x',expand=True);self.profile_vars={}
        for key,title in [('first_name','First name'),('last_name','Last name')]:
            self.label(fields,title,color=MUTED).pack(anchor='w',pady=(0,5));var=tk.StringVar(value=self.profile.data[key]);self.profile_vars[key]=var
            entry=Entry(fields,textvariable=var);entry.pack(fill='x',pady=(0,12));entry.bind('<FocusOut>',lambda e:self.save_profile());entry.bind('<Return>',lambda e:self.save_profile())
        self.label(fields,'Optional. Saved only on this computer when you leave a field.',color=MUTED).pack(anchor='w')
        overview=self.card('Monitoring overview');self.monitoring_count=tk.StringVar(value=f'{len(self.store.workspaces)} workspaces    ·    {1 if self.auto_id else 0} monitoring in this window');tk.Label(overview,textvariable=self.monitoring_count,font=(self.fonts['display'],15),bg=PANEL,fg=TEXT).pack(anchor='w')
        self.label(overview,'Configure independent repositories. Start requires explicit scope approval; one repository listens at a time. Imported profiles retain their reviewed exclusions.',color=MUTED,wraplength=850).pack(anchor='w',pady=(10,14))
        if not self.store.workspaces:EmptyIllustration(overview,self.assets,bg=PANEL).pack(anchor='w');self.label(overview,'No repositories yet. Use + in the rail to create your first workspace.',color=MUTED).pack(anchor='w')
        self.home_badges={}
        for w in self.store.workspaces:
            surface=Panel(overview,fill=FIELD,radius=15,padding=(10,3));surface.pack(fill='x',pady=5);row=surface.inner;self.label(row,w.icon+'   '+w.name,bg=FIELD).pack(side='left',padx=12,pady=10)
            status=self.live_status(w);badge=Badge(row,status['label'],status['tone']);badge.pack(side='left',padx=15);self.home_badges[w.id]=badge
            Button(row,text='Open →',command=lambda item=w:self.workspace(item)).pack(side='right',padx=8)
        info=self.card('How to use NIGHT BANDIT')
        steps=['Use + in the rail to add a repository workspace. Give each one its own name and icon.','Each workspace has its own connection, folder, master file, history and activity.','Choose one master file with any safe filename. Review its exact scope before synchronization.','Browse Day, Week, Month or Year. Choose a day, select a commit, then inspect its file diffs.','Use Settings for appearance, connection, monitoring and diagnostics.']
        for i,text in enumerate(steps,1):self.label(info,str(i)+'   '+text,wraplength=900,color=MUTED).pack(anchor='w',pady=7)
        self.label(info,'Read Documentation for connection, scope review, dry-run, automatic sync and troubleshooting.',color=SOFT,wraplength=900).pack(anchor='w',pady=(15,0))
    def save_profile(self):
        try:self.profile.save(self.profile_vars['first_name'].get(),self.profile_vars['last_name'].get());self.save_status.set('Profile saved locally');self.toast.show('Profile saved on this computer.')
        except Exception as e:self.save_status.set('Profile not saved');self.dialogs.showerror('Profile needs attention',redact(str(e)),parent=self.root)
    def add_repository(self):
        count=len(self.store.workspaces)+1;item=Workspace(uuid.uuid4().hex,f'Repository {count}',f'R{count}','','','main','')
        try:self.store.upsert(item)
        except Exception as e:self.dialogs.showerror('Workspace not added',redact(str(e)),parent=self.root);return
        self.save_status.set('Workspace saved locally');self.workspace(item)
    def operate(self,w,operation):
        if self._busy:
            self.toast.show('Stop the active session before inspecting another operation.');return
        self.feedback=self._feedback_by_repo.setdefault(w.id,Feedback());self.feedback.begin_read();self.indicator.start();self.save_status.set(self.feedback.label.upper())
        self.footer.set('Checking '+w.name+'… local read only');self.root.configure(cursor='watch');self.root.update_idletasks()
        try:
            result=self.serial.run(w.id,operation);self.statuses[w.id]='Local read complete / stopped';self.feedback.finish_read()
            self.footer.set(w.name+' • Local read complete • No remote push verified');return result
        except Exception:
            self.statuses[w.id]='Needs attention';self.feedback.event({'event':'blocked','reason':self.serial.errors.get(w.id,'Read blocked')})
            self.footer.set('Failed or blocked • No synchronization attempted')
            self.dialogs.showerror('Repository needs attention',self.feedback.reason,parent=self.root)
        finally:
            self.indicator.stop();self.save_status.set(self.feedback.label.upper()+' • SYNC OFF');self.root.configure(cursor='')
    def workspace(self,w):
        self.feedback=self._feedback_by_repo.setdefault(w.id,Feedback());self.save_status.set(self.feedback.label.upper()+(' • ACTIVE' if self.auto_id==w.id else ' • SYNC OFF'))
        self.view='workspace';self.current=w;self.history_data=None;self.clear();self.build_rail()
        head=tk.Frame(self.body,bg=BG);head.pack(fill='x',padx=4,pady=(6,20));self.label(head,w.name,17,bg=BG).pack(side='left')
        Button(head,text='Edit name / icon',command=lambda:self.rename(w)).pack(side='left',padx=14)
        Button(head,text='Remove workspace',command=lambda:self.remove(w)).pack(side='right')
        if w.draft:
            connect=self.card();EmptyIllustration(connect,self.assets,bg=PANEL).pack(pady=(4,10));self.label(connect,'REPOSITORY',10,MUTED).pack(pady=(20,8));self.label(connect,'No repository selected',24).pack(pady=(0,28))
            providers=tk.Frame(connect,bg=PANEL);providers.pack(pady=10)
            Button(providers,text='Connect GitHub',command=lambda:self.connect_github(w),width=23).grid(row=0,column=0,padx=8,pady=8)
            Button(providers,text='GitLab · Coming Soon',state='disabled',width=23).grid(row=0,column=1,padx=8,pady=8)
            Button(providers,text='Bitbucket · Coming Soon',state='disabled',width=23).grid(row=1,column=0,columnspan=2,pady=8)
            self.label(connect,'Connect GitHub, select a repository and review its existing local checkout and file.',color=MUTED).pack(pady=(12,15))
            self.footer.set('Not connected • Connect GitHub to select a repository');return
        setup=self.card('Sync setup')
        status=self.live_status(w)
        if self.runtime.bound.get(w.id)==w:status['connection']='GitHub scope verified'
        if w.id in self._last_verified:status['push']=self._last_verified[w.id][:12]
        badges=tk.Frame(setup,bg=PANEL);badges.pack(fill='x',pady=(0,14))
        Badge(badges,status['connection']).pack(side='left',padx=(0,8))
        self.sync_badge=Badge(badges,'Automatic session active' if self.auto_id==w.id else 'Automatic sync off');self.sync_badge.pack(side='left',padx=(0,8))
        Badge(badges,status['scope'],status['tone']).pack(side='left')
        self.last_push_status=tk.StringVar(value='Last verified push: '+status['push']);tk.Label(setup,textvariable=self.last_push_status,bg=PANEL,fg=MUTED,wraplength=900,justify='left',font=(self.fonts['mono'],10)).pack(anchor='w',pady=(0,14))
        imported=self.runtime.profiles.load(w)
        if imported:self.label(setup,f'{len(imported.reviewed_exclusions)} reviewed exclusions preserved; changed fingerprints stop synchronization.',color=MUTED,wraplength=850).pack(anchor='w',pady=(0,12))
        self.label(setup,'Repository • exact selected scope',color=MUTED).pack(anchor='w',pady=(0,7))
        self.repository_choice=tk.StringVar(value=w.remote)
        Combobox(setup,textvariable=self.repository_choice,values=(w.remote,),state='readonly').pack(fill='x',pady=(0,15))
        for title,value in [('Local repository folder',w.folder),('Master file',w.master_file)]:
            self.label(setup,title,color=MUTED).pack(anchor='w',pady=(0,7));surface=Panel(setup,fill=FIELD,radius=16,padding=(4,3));surface.pack(fill='x',pady=(0,14));row=surface.inner
            self.label(row,value,bg=FIELD,wraplength=790,role='mono').pack(side='left',padx=15,pady=15)
            Button(row,text='Change…',command=lambda:self.edit(w)).pack(side='right',padx=10)
        controls=tk.Frame(setup,bg=PANEL);controls.pack(fill='x',pady=(4,8))
        self.start=Button(controls,text='▷  Start Automatic Sync',style='Accent.TButton',state='disabled',command=lambda:self.start_sync(w));self.start.pack(side='left',fill='x',expand=True,padx=(0,12))
        self.stop=Button(controls,text='□  Stop Automatic Sync',state='disabled',command=self.stop_sync);self.stop.pack(side='left',fill='x',expand=True)
        scope=tk.Frame(setup,bg=PANEL);scope.pack(fill='x',pady=(4,0));Button(scope,text='Review local scope',command=lambda:self.review(w)).pack(side='left')
        Button(scope,text='Connect / review GitHub',command=lambda:self.connect_github(w)).pack(side='left',padx=8)
        Button(scope,text='Run safe dry-run',command=lambda:self.dry_run_connected(w)).pack(side='left',padx=8)
        self.runtime_status=tk.StringVar()
        tk.Label(setup,textvariable=self.runtime_status,bg=PANEL,fg=SOFT,wraplength=850,justify='left').pack(anchor='w',pady=10)
        detail='Local origin is not authentication; connect and review scope before starting'
        if w.id in self.reviewed:detail='Exact local scope reviewed • '+self.reviewed[w.id]['sha256'][:12]+'… • still stopped'
        self.label(scope,detail,color=MUTED,wraplength=650).pack(side='left',padx=15)
        self.make_history(w);self.make_logs(w);self.refresh_controls();self.footer.set(w.name+' • '+self._session_message.get(w.id,'Automatic sync off'))
    def review(self,w):
        result=self.operate(w,lambda:self.runtime.reader(w).review())
        if result is not None:self.reviewed[w.id]=result;self.workspace(w)
    def make_history(self,w):
        card=self.card();top=tk.Frame(card,bg=PANEL);top.pack(fill='x');self.label(top,'Commit history',16).pack(side='left')
        self.period_buttons={}
        for name in ('day','week','month','year'):
            button=Button(top,text=name.title(),style='FilterSelected.TButton' if self.history_period==name else 'TButton',command=lambda p=name:self.set_period(w,p));button.pack(side='left',padx=4);self.period_buttons[name]=button
        Button(top,text='Load history',command=lambda:self.load_history(w)).pack(side='right')
        nav=tk.Frame(card,bg=PANEL);nav.pack(fill='x',pady=14)
        Button(nav,text='‹',width=3,command=lambda:self.move_history(w,-1)).pack(side='left')
        Button(nav,text='Today',command=lambda:self.today(w)).pack(side='left',padx=6)
        Button(nav,text='›',width=3,command=lambda:self.move_history(w,1)).pack(side='left');self.range_label=tk.StringVar()
        tk.Label(nav,textvariable=self.range_label,bg=PANEL,fg=TEXT,font=(self.fonts['display'],12)).pack(side='left',padx=18)
        self.calendar_grid=tk.Frame(card,bg=PANEL);self.calendar_grid.pack(fill='x')
        self.history_message=tk.StringVar(value='Not loaded. Counts appear only after reading real Git history.')
        tk.Label(card,textvariable=self.history_message,bg=PANEL,fg=MUTED,anchor='w',justify='left',wraplength=940).pack(fill='x',pady=(12,8))
        self.commit_day=tk.StringVar(value='Choose a date to view its commits.')
        tk.Label(card,textvariable=self.commit_day,bg=PANEL,fg=TEXT,anchor='w').pack(fill='x',pady=(10,7))
        table_frame=tk.Frame(card,bg=PANEL);table_frame.pack(fill='x')
        self.commit_table=ttk.Treeview(table_frame,style='History.Treeview',columns=('time','subject','author','sha'),show='headings',height=5)
        for key,title,width in [('time','Time',100),('subject','Commit',430),('author','Author',160),('sha','SHA',120)]:
            self.commit_table.heading(key,text=title);self.commit_table.column(key,width=width,minwidth=65,stretch=key=='subject')
        scroll=ttk.Scrollbar(table_frame,orient='vertical',command=self.commit_table.yview);self.commit_table.configure(yscrollcommand=scroll.set)
        self.commit_table.pack(side='left',fill='x',expand=True);scroll.pack(side='right',fill='y')
        self.commit_table.bind('<Double-1>',lambda e:self.open_selected_commit(w))
        actions=tk.Frame(card,bg=PANEL);actions.pack(fill='x',pady=(10,0));Button(actions,text='View selected commit & diffs',command=lambda:self.open_selected_commit(w)).pack(side='left')
        self.more=Button(actions,text='Load more commits',state='disabled',command=lambda:self.load_day(w,self.selected_day,self.next_skip,append=True));self.more.pack(side='right')
        self.commit_records={};self.render_calendar(w)
    def render_calendar(self,w):
        for widget in self.calendar_grid.winfo_children():widget.destroy()
        period=self.history_period;start,end=history_window(period,self.history_anchor)
        for key,button in self.period_buttons.items():button.configure(style='FilterSelected.TButton' if key==period else 'TButton')
        self.range_label.set(str(start.year) if period=='year' else start.strftime('%B %Y') if period=='month' else start.strftime('%b %d, %Y')+(' — '+(end-timedelta(days=1)).strftime('%b %d, %Y') if period=='week' else ''))
        counts=self.history_data['counts'] if self.history_data else None;cells=[]
        if period=='year':
            columns=4
            for month in range(1,13):
                key=f'{start.year}-{month:02d}';n=sum(v for k,v in counts.items() if k.startswith(key)) if counts is not None else None
                cells.append((calendar.month_name[month],str(start.year),n,lambda m=month:self.open_month(w,m),month==date.today().month and start.year==date.today().year))
        else:
            columns=1 if period=='day' else 7
            days=[start] if period=='day' else [start+timedelta(days=i) for i in range(7)] if period=='week' else [d for week in calendar.Calendar().monthdatescalendar(start.year,start.month) for d in week]
            for day in days:
                if period=='month' and day.month!=start.month:cells.append(None);continue
                cells.append((day.strftime('%a'),str(day.day),counts.get(day.isoformat(),0) if counts is not None else None,lambda d=day:self.load_day(w,d),day==date.today()))
        for col in range(7):self.calendar_grid.columnconfigure(col,weight=0,uniform='')
        for col in range(columns):self.calendar_grid.columnconfigure(col,weight=1,uniform='calendar')
        for index,cell in enumerate(cells):
            row,col=divmod(index,columns)
            if cell is None:continue
            title,number,n,command,today=cell
            surface=Panel(self.calendar_grid,fill=FIELD,border=CRIMSON if today else BORDER,radius=18,padding=(7,4));surface.grid(row=row,column=col,sticky='nsew',padx=4,pady=4);frame=surface.inner
            self.label(frame,title,10,MUTED,bg=FIELD).pack(pady=(9,2));self.label(frame,number,15,bg=FIELD).pack()
            Button(frame,text='—' if n is None else str(n)+' commit'+('' if n==1 else 's'),command=command).pack(fill='x',padx=5,pady=(6,8))
        reveal(self.calendar_grid,4)
    def set_period(self,w,period):self.history_period=period;self.history_data=None;self.load_history(w)
    def move_history(self,w,direction):self.history_anchor=shift_history(self.history_period,self.history_anchor,direction);self.load_history(w)
    def today(self,w):self.history_anchor=date.today();self.load_history(w)
    def open_month(self,w,month):self.history_anchor=date(self.history_anchor.year,month,1);self.history_period='month';self.load_history(w)
    def load_history(self,w):
        data=self.operate(w,lambda:self.runtime.reader(w).calendar_history(self.history_period,self.history_anchor))
        if data is None:return
        self.history_data=data;self.render_calendar(w);self.history_message.set(str(data['total'])+' real commits • '+data['source'])
    def load_day(self,w,day,skip=0,append=False):
        data=self.operate(w,lambda:self.runtime.reader(w).commits_on(day,skip))
        if data is None:return
        if not append:self.commit_table.delete(*self.commit_table.get_children());self.commit_records={}
        for record in data['rows']:
            key=self.commit_table.insert('','end',values=(datetime.fromisoformat(record['date']).astimezone().strftime('%H:%M:%S'),record['subject'],record['author'],record['commit'][:12]));self.commit_records[key]=record
        self.selected_day=day;self.next_skip=data['next_skip'];self.more.configure(state='normal' if data['more'] else 'disabled')
        self.commit_day.set(day.strftime('%A, %B %d, %Y')+' • '+str(len(self.commit_records))+' commits shown • select a commit to inspect diffs')
    def open_selected_commit(self,w):
        chosen=self.commit_table.selection()
        if not chosen:self.footer.set('Choose a day, then select a commit to view its diffs');return
        self.open_commit(w,self.commit_records[chosen[0]])

    def open_commit(self,w,record):
        metadata=self.operate(w,lambda:self.runtime.reader(w).changed_files(record['commit']))
        if metadata is None:return
        dialog=tk.Toplevel(self.root);dialog.title('NIGHT BANDIT • Commit review');dialog.geometry('1160x740');dialog.configure(bg=BG)
        head=tk.Frame(dialog,bg=BG);head.pack(fill='x',padx=20,pady=16)
        self.label(head,record['subject'],16,bg=BG,wraplength=1080).pack(anchor='w')
        self.label(head,record['commit']+'  •  '+metadata['comparison']+'  •  Read only',color=MUTED,bg=BG).pack(anchor='w',pady=6)
        body=tk.Frame(dialog,bg=BG);body.pack(fill='both',expand=True,padx=20,pady=(0,15))
        listing=tk.Listbox(body,bg=PANEL,fg=TEXT,selectbackground=CRIMSON,relief='flat',width=36,font=(self.fonts['mono'],10),exportselection=False);listing.pack(side='left',fill='y',padx=(0,12))
        area=tk.Frame(body,bg=BG);area.pack(side='left',fill='both',expand=True)
        text=tk.Text(area,bg=FIELD,fg=TEXT,insertbackground=TEXT,font=(self.fonts['mono'],10),wrap='none',relief='flat',padx=12,pady=12,state='disabled')
        sy=ttk.Scrollbar(area,orient='vertical',command=text.yview);sx=ttk.Scrollbar(area,orient='horizontal',command=text.xview)
        text.configure(yscrollcommand=sy.set,xscrollcommand=sx.set);sy.pack(side='right',fill='y');sx.pack(side='bottom',fill='x');text.pack(fill='both',expand=True)
        text.tag_configure('add',foreground='#a2d3b5');text.tag_configure('remove',foreground='#f49aaa');text.tag_configure('header',foreground='#ddbbcc')
        for item in metadata['files']:listing.insert('end',('Protected • ' if not item['viewable'] else '')+redact(item['path']))
        def show_file(event=None):
            selected=listing.curselection()
            if not selected:return
            item=metadata['files'][selected[0]]
            patch='Protected path. Diff content is not opened.' if not item['viewable'] else self.operate(w,lambda:self.runtime.reader(w).diff_text(record['commit'],item['path']))
            if patch is None:return
            text.configure(state='normal');text.delete('1.0','end')
            for line in patch.splitlines(True):text.insert('end',line,'header' if line.startswith(('+++','---','@@','diff ')) else 'add' if line.startswith('+') else 'remove' if line.startswith('-') else '')
            text.configure(state='disabled')
        listing.bind('<<ListboxSelect>>',show_file)
        self.label(dialog,'Read only • tokens redacted • long lines shortened • protected paths hidden • sync scope unchanged',color=MUTED,bg=BG).pack(anchor='w',padx=20,pady=(0,12))
        if metadata['files']:listing.selection_set(0);show_file()
    def make_logs(self,w):
        card=self.card();bar=tk.Frame(card,bg=PANEL);bar.pack(fill='x');self.label(bar,'Activity logs',16).pack(side='left')
        level=tk.StringVar(value='ALL');Combobox(bar,textvariable=level,values=('ALL','INFO','WARNING','ERROR'),state='readonly',width=10).pack(side='right',padx=8)
        log_surface=Panel(card,fill=FIELD,radius=18,padding=12);log_surface.pack(fill='x',pady=15)
        text=tk.Text(log_surface.inner,height=9,bg=FIELD,fg=TEXT,font=(self.fonts['mono'],10),relief='flat',padx=3,pady=6,wrap='word',state='disabled');text.pack(fill='x')
        self._log_widget=(w.id,text,level)
        def render(content,animate=False):
            text.configure(state='normal');text.delete('1.0','end');text.insert('1.0',content);text.configure(state='disabled')
            if animate:
                text.tag_add('incoming','1.0','end')
                self.motion.animate(text,'activity-entry',0,1,lambda t:text.tag_configure('incoming',foreground=mix(MUTED,TEXT,t)),160)

        render('No activity loaded. Refresh reads this workspace’s existing audit records.')
        def refresh():
            rows=self.operate(w,lambda:self.runtime.reader(w).activity())
            if rows is None:return
            rows=[r for r in rows if level.get()=='ALL' or r.get('severity')==level.get()]
            fingerprint=tuple((r.get('timestamp'),r.get('event'),r.get('commit')) for r in rows)
            fresh=bool(rows) and fingerprint!=self._activity_records.get(w.id);self._activity_records[w.id]=fingerprint
            render('\n\n'.join(f"{r.get('timestamp','')}  {r.get('severity','INFO')}  {r.get('event','')}\n{r.get('reason','')} {r.get('commit','')}" for r in rows) or 'No recorded activity for this workspace.',animate=fresh)
        Button(bar,text='Refresh',command=refresh).pack(side='right')
        Button(bar,text='Clear view',command=lambda:render('View cleared. Stored audit records are unchanged.')).pack(side='right',padx=8)
        self.label(card,'Connection and verification status are shown above; audit records belong only to this workspace.',color=MUTED,wraplength=900).pack(anchor='w')
    def rename(self,w):
        if self._busy:
            self.toast.show('Stop the active operation before changing workspace settings.');return
        dialog=tk.Toplevel(self.root);dialog.title('Workspace name & icon');dialog.configure(bg=BG);dialog.geometry('450x270');dialog.transient(self.root)
        f=tk.Frame(dialog,bg=BG);f.pack(fill='both',expand=True,padx=24,pady=20);name=tk.StringVar(value=w.name);icon=tk.StringVar(value=w.icon)
        self.label(f,'Display name',bg=BG).pack(anchor='w');Entry(f,textvariable=name).pack(fill='x',pady=7)
        self.label(f,'Icon • 1–3 letters, numbers or simple symbols',bg=BG).pack(anchor='w');Entry(f,textvariable=icon,width=8).pack(anchor='w',pady=7)
        def save():
            try:item=replace(w,name=name.get(),icon=icon.get());self.store.upsert(item)
            except Exception as e:self.dialogs.showerror('Workspace not saved',redact(str(e)),parent=dialog);return
            dialog.destroy();self.workspace(item)
        Button(f,text='Save workspace',style='Accent.TButton',command=save).pack(anchor='e',pady=10)
    def remove(self,w):
        if self._busy:
            self.toast.show('Stop the active operation before changing workspace settings.');return
        if not self.dialogs.askyesno('Remove workspace?',f'Remove “{w.name}” from NIGHT BANDIT?\n\nRepository folders, Git history and files will stay intact.',parent=self.root):return
        try:self.store.remove(w.id)
        except Exception as e:self.dialogs.showerror('Workspace not removed',redact(str(e)),parent=self.root);return
        self.reviewed.pop(w.id,None);self.home()
    def edit(self,w):
        if self._busy:
            self.toast.show('Stop the active operation before changing workspace settings.');return
        dialog=tk.Toplevel(self.root);dialog.title('Review local repository scope');dialog.geometry('780x620');dialog.configure(bg=BG);dialog.transient(self.root)
        form=tk.Frame(dialog,bg=BG);form.pack(fill='both',expand=True,padx=24,pady=24);form.columnconfigure(1,weight=1)
        self.label(form,'Repository setup',18,bg=BG).grid(row=0,column=0,columnspan=3,sticky='w',pady=(0,20))
        fields={};values={'name':w.name,'icon':w.icon,'remote':w.remote,'folder':w.folder,'branch':w.branch,'master_file':w.master_file}
        for row,(key,title) in enumerate([('name','Display name'),('icon','Workspace icon'),('remote','GitHub repository URL'),('folder','Local repository folder'),('branch','Branch'),('master_file','Master file')],1):
            self.label(form,title,bg=BG).grid(row=row,column=0,sticky='w',padx=(0,15),pady=10);fields[key]=tk.StringVar(value=values[key]);Entry(form,textvariable=fields[key]).grid(row=row,column=1,sticky='ew',pady=10)
            if key=='folder':Button(form,text='Browse…',command=lambda:fields['folder'].set(filedialog.askdirectory(parent=dialog) or fields['folder'].get())).grid(row=row,column=2,padx=8)
            if key=='master_file':
                def choose():
                    file=filedialog.askopenfilename(parent=dialog,initialdir=fields['folder'].get() or None)
                    if file:
                        try:fields['master_file'].set(Path(file).relative_to(Path(fields['folder'].get())).as_posix())
                        except ValueError:self.dialogs.showerror('Choose a file inside this checkout','The master file must be inside the selected repository.',parent=dialog)
                Button(form,text='Browse…',command=choose).grid(row=row,column=2,padx=8)
        self.label(form,'This saves a local-only profile. It does not connect GitHub or authorize synchronization.',color=MUTED,bg=BG,wraplength=700).grid(row=8,column=0,columnspan=3,sticky='w',pady=20)
        def save():
            try:
                item=Workspace(w.id,**{k:v.get() for k,v in fields.items()});self.runtime.reader(item).review()
                if not self.dialogs.askyesno('Confirm exact scope',f'{item.remote}\n{item.folder}\nBranch: {item.branch}\nMaster file: {item.master_file}\n\nSave this local profile? No synchronization will start.',parent=dialog):return
                self.store.upsert(item)
            except Exception as e:self.dialogs.showerror('Scope was not saved',redact(str(e)),parent=dialog);return
            self.reviewed.pop(item.id,None);dialog.destroy();self.workspace(item)
        Button(form,text='Review and save scope',style='Accent.TButton',command=save).grid(row=9,column=0,columnspan=3,sticky='e',pady=15)
    def change_preference(self,**changes):
        try:
            self.preferences.update(**changes);self.motion.settle()
            if not self.preferences.data['notifications']:self.toast.close()
            self.settings()
        except Exception as error:self.dialogs.showerror('Settings were not saved',redact(str(error)),parent=self.root)
    def settings(self):
        self.view='settings';self.current=None;self.clear();self.build_rail();card=self.card('Settings')
        self.label(card,'Appearance',14).pack(anchor='w',pady=(0,10))
        self.appearance=tk.StringVar(value='Crimson / Black');Combobox(card,textvariable=self.appearance,values=('Crimson / Black',),state='readonly',width=30).pack(anchor='w')
        Button(card,text='Light appearance • not in approved palette',state='disabled').pack(anchor='w',pady=8)
        self.label(card,'Motion and interaction',14).pack(anchor='w',pady=(18,10))
        row=tk.Frame(card,bg=PANEL);row.pack(fill='x',pady=5)
        self.label(row,'Reduced motion',color=MUTED).pack(side='left')
        reduced=self.preferences.data['reduced_motion']
        Button(row,text='On' if reduced else 'Off',style='Accent.TButton' if reduced else 'TButton',command=lambda:self.change_preference(reduced_motion=not reduced)).pack(side='right')
        self.label(card,'Windows animation preference: '+('enabled' if windows_motion_allowed() else 'reduced / unavailable')+'. Windows and Reduced Motion always override intensity.',color=MUTED,wraplength=900).pack(anchor='w',pady=8)
        self.label(card,'Animation intensity',color=MUTED).pack(anchor='w',pady=(10,5))
        self.intensity=tk.StringVar(value=self.preferences.data['intensity'])
        intensity=Combobox(card,textvariable=self.intensity,values=('Off','Subtle','Standard'),state='readonly',width=24);intensity.pack(anchor='w')
        intensity.bind('<<ComboboxSelected>>',lambda e:self.change_preference(intensity=self.intensity.get()))
        row=tk.Frame(card,bg=PANEL);row.pack(fill='x',pady=(18,5))
        enabled=self.preferences.data['notifications'];self.label(row,'In-app notifications',color=MUTED).pack(side='left')
        Button(row,text='On' if enabled else 'Off',command=lambda:self.change_preference(notifications=not enabled)).pack(side='right')
        self.label(card,'No Windows notification enrollment or external messages.',color=MUTED).pack(anchor='w',pady=5)
        self.label(card,'Repository navigation display',color=MUTED).pack(anchor='w',pady=(18,5))
        self.repo_display=tk.StringVar(value=self.preferences.data['repository_display'])
        display=Combobox(card,textvariable=self.repo_display,values=('Icons','Names'),state='readonly',width=24);display.pack(anchor='w')
        display.bind('<<ComboboxSelected>>',lambda e:self.change_preference(repository_display=self.repo_display.get()))
        for title,body in [('Connections','GitHub: connect per workspace and review exact scope. This build uses its own Windows-protected session.\nGitLab and Bitbucket: Coming Soon.'),('Monitoring','One serial I/O worker; one automatic repository session at a time. Existing file scopes and exclusions stay enforced.\nLoading animations can pause while the current synchronous read runs.'),('Profile & privacy','Names are optional and stored locally. No email signup or automatic marketing enrollment.\nClear view never erases audit history.')]:
            self.label(card,title,14).pack(anchor='w',pady=(18,7));self.label(card,body,color=MUTED,wraplength=900).pack(anchor='w')
        self.update_panel=UpdatePanel(self)
        diag=self.card('Diagnostics')
        details=(f"Python {platform.python_version()} • {platform.system()}\n{len(self.store.workspaces)} repository workspaces\nRegistry: {self.store.path}\nFonts: {self.fonts}\nBrand icon: NIGHT BANDIT\nWindow icon: registered from supplied asset\nBackground: exact supplied source, display-only crop\nTypeface files: process-private, no system installation\nGlass: layered Tk surfaces, not native Acrylic\nDistribution: Python source build")
        self.label(diag,details,color=MUTED,role='mono',wraplength=920).pack(anchor='w')
        self.footer.set('Settings • Local preferences')
