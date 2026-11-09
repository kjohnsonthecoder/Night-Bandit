"""Branded local dialogs, finite notifications, operation indicators and startup surface."""
import tkinter as tk
from .visuals import Button,Panel,BG,PANEL,TEXT,MUTED,CRIMSON,SOFT,rounded
from .branding import family
from .effects import mix,motion_for

class Indicator(tk.Canvas):
    def __init__(self,parent):
        super().__init__(parent,height=8,width=200,bg=BG,highlightthickness=0,bd=0)
        self.active=False;self.fraction=None;self.step=0;self.timer=None;self.bind('<Configure>',self.draw)
        self.bind('<Destroy>',lambda e:self.stop() if e.widget==self else None)
    def start(self):
        self.active=True;self.fraction=None;self.draw()
        if self.timer is None:self.tick()
    def stop(self):
        self.active=False
        if self.timer:
            try:self.after_cancel(self.timer)
            except tk.TclError:pass
            self.timer=None
        try:self.draw()
        except tk.TclError:pass
    def counts(self,completed,total):
        if type(completed) is not int or type(total) is not int or total<=0 or not 0<=completed<=total:raise ValueError('Actual progress counts required')
        target=completed/total;motion=motion_for(self)
        def paint(value):self.fraction=value;self.draw()
        if motion:motion.animate(self,'actual-progress',self.fraction or 0,target,paint,180)
        else:paint(target)
    def tick(self):
        self.timer=None
        if not self.active or not self.winfo_exists():return
        motion=motion_for(self)
        if motion is None or not motion.enabled():self.draw();self.timer=None;return
        self.step=(self.step+1)%20;self.draw();self.timer=self.after(140,self.tick)
    def draw(self,event=None):
        self.delete('all');w=max(1,self.winfo_width())
        rounded(self,0,2,w,4,2,fill='#29151e',outline='')
        if self.fraction is not None:rounded(self,0,2,max(1,w*self.fraction),4,2,fill=CRIMSON,outline='')
        elif self.active:
            motion=motion_for(self);moving=motion is not None and motion.enabled()
            x=(w*0.65)*(self.step/19) if moving else 0
            rounded(self,x,2,max(24,w*0.35),4,2,fill=CRIMSON,outline='')

class Toast:
    def __init__(self,root,preferences):self.root=root;self.preferences=preferences;self.panel=None;self.timer=None
    def show(self,message):
        if not self.preferences.data['notifications']:return
        self.close();self.panel=Panel(self.root,fill='#24151d',border='#703047',radius=16,padding=16)
        tk.Label(self.panel.inner,text='NIGHT BANDIT',bg='#24151d',fg=SOFT,font=(family(self.root,'display'),9,'bold')).pack(anchor='w')
        tk.Label(self.panel.inner,text=message,bg='#24151d',fg=TEXT,wraplength=330,justify='left',font=(family(self.root),10)).pack(anchor='w',pady=(5,0))
        self.panel.place(relx=1,rely=1,anchor='se',x=-24,y=-45,width=370)
        self.timer=self.root.after(4000,self.close)
    def close(self):
        if self.timer:
            try:self.root.after_cancel(self.timer)
            except tk.TclError:pass
            self.timer=None
        if self.panel:
            try:self.panel.destroy()
            except tk.TclError:pass
            self.panel=None

class Dialogs:
    def __init__(self,root,assets=None):self.root=root;self.assets=assets
    def showerror(self,title,message,parent=None):return self.show(title,message,parent,False)
    def askyesno(self,title,message,parent=None):return self.show(title,message,parent,True)
    def show(self,title,message,parent=None,question=False):
        parent=parent or self.root;window=tk.Toplevel(parent);window.title('NIGHT BANDIT • '+title);window.configure(bg=BG)
        window.geometry('640x440');window.minsize(520,350);window.transient(parent)
        window.columnconfigure(0,weight=1);window.rowconfigure(1,weight=1)
        if self.assets:window.iconphoto(True,self.assets.window_icon)
        result={'value':False}
        header=tk.Frame(window,bg=BG);header.grid(row=0,column=0,sticky='ew',padx=22,pady=(18,10))
        if self.assets:tk.Label(header,image=self.assets.header_icon,bg=BG).pack(side='left',padx=(0,16))
        tk.Label(header,text=title,bg=BG,fg=TEXT,font=(family(self.root,'display'),15,'bold'),wraplength=480,justify='left').pack(side='left')
        body=tk.Frame(window,bg=PANEL);body.grid(row=1,column=0,sticky='nsew',padx=22,pady=8)
        text=tk.Text(body,height=6,bg=PANEL,fg=MUTED,font=(family(self.root),10),wrap='word',relief='flat',padx=16,pady=14)
        scroll=tk.Scrollbar(body,orient='vertical',command=text.yview);text.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y');text.pack(fill='both',expand=True);text.insert('1.0',message);text.configure(state='disabled')
        row=tk.Frame(window,bg=BG);row.grid(row=2,column=0,sticky='ew',padx=22,pady=(10,20))
        def finish(value=False):result['value']=value;window.destroy()
        if question:
            cancel=Button(row,text='Cancel',command=lambda:finish(False));cancel.pack(side='right',padx=5);cancel.focus_set()
            Button(row,text='Confirm',style='Accent.TButton',command=lambda:finish(True)).pack(side='right',padx=5)
        else:Button(row,text='Close',command=finish).pack(side='right')
        window.protocol('WM_DELETE_WINDOW',finish);window.bind('<Escape>',lambda e:finish(False))
        window.grab_set();window.wait_window();return result['value']

class Splash(tk.Frame):
    """Same-window startup surface, shown only while real workspace construction runs. No delay or success badge."""
    def __init__(self,root,assets):
        super().__init__(root,bg=BG);self.place(relwidth=1,relheight=1);self.lift()
        group=tk.Frame(self,bg=BG);group.place(relx=.5,rely=.5,anchor='center')
        tk.Label(group,image=assets.header_icon,bg=BG).pack()
        tk.Label(group,text='NIGHT BANDIT',bg=BG,fg=TEXT,font=(family(root,'display'),25,'bold')).pack(pady=18)
        tk.Label(group,text='Preparing your workspace',bg=BG,fg=MUTED,font=(family(root),11)).pack()
