"""Reusable rounded visual treatment. Pure Tk drawing; no image derivatives or dependencies."""
import tkinter as tk
from tkinter import ttk, font
from .branding import family
from .effects import motion_for,mix

BG='#0d0a0c'; PANEL='#191416'; FIELD='#110d0f'; BORDER='#34262c'
TEXT='#fff7fa'; MUTED='#b7a7af'; CRIMSON='#cd2347'; SOFT='#eca5b6'

def background(parent):
    try:return parent.cget('background')
    except tk.TclError:return BG

def rounded(canvas,x,y,extent_width,extent_height,radius,**kwargs):
    w=max(1,extent_width);h=max(1,extent_height);r=min(radius,w/2,h/2)
    fill=kwargs.get('fill','');edge=kwargs.get('outline','');stroke=kwargs.get('width',1)
    tags=kwargs.get('tags',())
    def paint(px,py,pw,ph,pr,color):
        pr=max(0,min(pr,pw/2,ph/2))
        if pr<1:return canvas.create_rectangle(px,py,px+pw,py+ph,fill=color,outline='',tags=tags)
        canvas.create_rectangle(px+pr,py,px+pw-pr,py+ph,fill=color,outline='',tags=tags)
        canvas.create_rectangle(px,py+pr,px+pw,py+ph-pr,fill=color,outline='',tags=tags)
        for ax,ay,start in ((px,py,90),(px+pw-2*pr,py,0),(px+pw-2*pr,py+ph-2*pr,270),(px,py+ph-2*pr,180)):
            canvas.create_arc(ax,ay,ax+2*pr,ay+2*pr,start=start,extent=90,style='pieslice',fill=color,outline='',tags=tags)
    if edge:
        paint(x,y,w,h,r,edge)
        if w>stroke*2 and h>stroke*2:paint(x+stroke,y+stroke,w-stroke*2,h-stroke*2,max(0,r-stroke),fill)
    else:paint(x,y,w,h,r,fill)

class Panel(tk.Canvas):
    def __init__(self,parent,fill=PANEL,border=BORDER,radius=26,padding=24,**kwargs):
        super().__init__(parent,bg=background(parent),highlightthickness=0,bd=0,width=1,height=1,**kwargs)
        self.fill=fill;self.border=border;self.radius=radius;self.glow=0.0
        self.padx,self.pady=(padding,padding) if isinstance(padding,int) else padding
        self.inner=tk.Frame(self,bg=fill)
        self.window=self.create_window(self.padx,self.pady,anchor='nw',window=self.inner)
        self.bind('<Configure>',self.resize);self.inner.bind('<Configure>',self.resize)
        for surface in (self,self.inner):
            surface.bind('<Enter>',lambda e:self.hover(True),add='+');surface.bind('<Leave>',self.leave,add='+')
        self.bind('<Destroy>',lambda e:motion_for(self).cancel_owner(self) if e.widget==self and motion_for(self) else None,add='+')
    def hover(self,value):
        target=1.0 if value else 0.0;motion=motion_for(self)
        def paint(amount):self.glow=amount;self.resize()
        if motion:motion.animate(self,'card-hover',self.glow,target,paint,160)
        else:paint(target)
    def leave(self,event):
        x,y=event.x_root-self.winfo_rootx(),event.y_root-self.winfo_rooty()
        if not (0<=x<self.winfo_width() and 0<=y<self.winfo_height()):self.hover(False)
    def resize(self,event=None):
        if not self.winfo_exists():return
        width=max(1,self.winfo_width());inner_width=max(1,width-self.padx*2)
        self.itemconfigure(self.window,width=inner_width)
        motion=motion_for(self)
        lift=round(self.glow*2) if motion is not None and motion.enabled() else 0
        self.coords(self.window,self.padx,self.pady-lift)
        height=max(1,self.inner.winfo_reqheight()+self.pady*2+3)
        if int(float(self.cget('height')))!=height:super().configure(height=height)
        self.delete('surface')
        rounded(self,2,3,max(1,width-4),max(1,height-4),self.radius,fill='#080608',outline='',tags='surface')
        rounded(self,1,0,max(1,width-2),max(1,height-4),self.radius,fill=mix(self.fill,'#24151e',self.glow*.16),outline=mix(self.border,'#b64b6b',self.glow*.18),width=1,tags='surface')
        self.tag_lower('surface')

class Button(tk.Canvas):
    """Rounded control with guarded disabled behavior, native focus and keyboard activation."""
    def __init__(self,parent,text='',command=None,style='TButton',state='normal',width=None,**kwargs):
        self.text=text;self.command=command;self.visual_style=style;self.disabled=state=='disabled';self.hover=False;self.pressed=False;self.glow=0.0
        self.face=font.Font(family=family(parent),size=10,weight='bold' if 'Rail' in style else 'normal')
        rail='Rail' in style
        pixels=52 if rail else max(42,self.face.measure(text)+30) if width is None else max(36,self.face.measure('0')*width+24)
        super().__init__(parent,width=pixels,height=50 if rail else 40,bg=background(parent),highlightthickness=0,bd=0,takefocus=0 if self.disabled else 1,cursor='' if self.disabled else 'hand2',**kwargs)
        self.bind('<Configure>',self.draw);self.bind('<Enter>',lambda e:self.set_hover(True));self.bind('<Leave>',lambda e:self.set_hover(False))
        self.bind('<ButtonPress-1>',self.press);self.bind('<ButtonRelease-1>',self.release)
        self.bind('<Return>',lambda e:self.activate());self.bind('<space>',lambda e:self.activate())
        self.bind('<FocusIn>',self.draw);self.bind('<FocusOut>',self.draw)
        self.bind('<Destroy>',lambda e:motion_for(self).cancel_owner(self) if e.widget==self and motion_for(self) else None);self.draw()
    def bind(self,sequence=None,func=None,add=None):
        return super().bind(sequence,func,add='+' if func is not None else add)
    def set_hover(self,value):
        self.hover=value;target=1.0 if value and not self.disabled else 0.0;motion=motion_for(self)
        def paint(amount):self.glow=amount;self.draw()
        if motion:motion.animate(self,'button-hover',self.glow,target,paint,140)
        else:paint(target)
    def press(self,event):
        if self.disabled:return
        self.focus_set();self.pressed=True;self.draw()
    def release(self,event):
        armed=self.pressed;self.pressed=False;self.draw()
        if armed and 0<=event.x<self.winfo_width() and 0<=event.y<self.winfo_height():self.invoke()
    def activate(self):self.invoke();return 'break'
    def invoke(self):
        if not self.disabled and self.command is not None:return self.command()
    def state(self,statespec=None):
        if statespec is None:return ('disabled',) if self.disabled else ()
        for flag in statespec:
            if flag=='disabled':self.disabled=True
            elif flag=='!disabled':self.disabled=False
        super().configure(takefocus=0 if self.disabled else 1,cursor='' if self.disabled else 'hand2');self.draw()
        return self.state()
    def configure(self,cnf=None,**kwargs):
        if cnf:kwargs.update(cnf)
        if not kwargs:return super().configure()
        if 'style' in kwargs:self.visual_style=kwargs.pop('style')
        if 'text' in kwargs:self.text=kwargs.pop('text')
        if 'command' in kwargs:self.command=kwargs.pop('command')
        if 'state' in kwargs:
            self.disabled=kwargs.pop('state')=='disabled'
            kwargs.update(takefocus=0 if self.disabled else 1,cursor='' if self.disabled else 'hand2')
        value=super().configure(**kwargs);self.draw();return value
    config=configure
    def draw(self,event=None):
        if not self.winfo_exists():return
        w=max(1,self.winfo_width());h=max(1,self.winfo_height());rail='Rail' in self.visual_style
        fill='#251b20';fg=TEXT;edge=''
        if 'Accent' in self.visual_style or 'Selected.Rail' in self.visual_style:fill=CRIMSON
        elif 'FilterSelected' in self.visual_style:fill='#fff3f6';fg='#24151b'
        elif rail:fill=BG if self.text=='+' else '#21171c';edge=BORDER if self.text=='+' else ''
        if self.disabled:fill='#24191e';fg='#796a73'
        elif self.pressed:fill='#8e1935' if 'Accent' in self.visual_style else '#3b2630'
        elif self.glow:fill=mix(fill,'#e02c53' if 'Accent' in self.visual_style or 'Selected.Rail' in self.visual_style else '#38252e',self.glow*.65)
        if self.focus_get()==self and not self.disabled:edge=SOFT
        self.delete('visual')
        shape_width=max(1,min(w,h)-4) if rail else max(1,w-4)
        shape_x=(w-shape_width)/2 if rail else 2
        rounded(self,shape_x,2,shape_width,max(1,h-4),h/2 if rail else 16,fill=fill,outline=edge,width=1,tags='visual')
        self.create_text(w/2,h/2,text=self.text,fill=fg,font=self.face,tags='visual')

class Field(tk.Canvas):
    """Rounded shell around a native keyboard/accessibility entry or combobox."""
    def __init__(self,parent,combo=False,**kwargs):
        width=kwargs.pop('width',32);self.variable=kwargs.get('textvariable');self.focused=False
        super().__init__(parent,width=max(80,width*8+28),height=48,bg=background(parent),highlightthickness=0,bd=0,takefocus=0)
        self.field=(ttk.Combobox if combo else ttk.Entry)(self,style='Soft.TCombobox' if combo else 'Soft.TEntry',**kwargs)
        self.window=self.create_window(14,24,anchor='w',window=self.field,height=30)
        tk.Canvas.bind(self,'<Configure>',self.draw)
        self.field.bind('<FocusIn>',lambda e:self.focus(True),add='+');self.field.bind('<FocusOut>',lambda e:self.focus(False),add='+')
        self.draw()
    def focus(self,value):self.focused=value;self.draw()
    def bind(self,sequence=None,func=None,add=None):return self.field.bind(sequence,func,add='+')
    def get(self):return self.field.get()
    def state(self,*args):return self.field.state(*args)
    def draw(self,event=None):
        w=max(1,self.winfo_width());self.itemconfigure(self.window,width=max(1,w-28));self.delete('surface')
        rounded(self,1,1,max(1,w-2),45,14,fill=FIELD,outline=CRIMSON if self.focused else BORDER,width=1,tags='surface');self.tag_lower('surface')

def Entry(parent,**kwargs):return Field(parent,**kwargs)
def Combobox(parent,**kwargs):return Field(parent,combo=True,**kwargs)
