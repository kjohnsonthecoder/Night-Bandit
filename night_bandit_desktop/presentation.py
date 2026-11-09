"""Truthful presentation helpers. No repository, authentication or update operations."""
import tkinter as tk
import re
from tkinter import font
from .branding import family
from .visuals import rounded,background,TEXT,MUTED,CRIMSON


def workspace_status(workspace,feedback=None,review=None,error=None):
    if error:return {'label':'Needs attention','tone':'blocked','connection':'Not verified','sync':'Off in preview','scope':'Review required','push':'None verified in preview'}
    if workspace.draft:return {'label':'Setup needed','tone':'muted','connection':'Not connected','sync':'Off in preview','scope':'Choose a file','push':'None verified in preview'}
    # A local folder/origin or successful read is never treated as provider authentication.
    ready=feedback is not None and feedback.phase=='ready'
    verified=feedback is not None and feedback.phase=='verified' and isinstance(feedback.commit,str) and re.fullmatch(r'[0-9a-f]{40,64}',feedback.commit) is not None
    return {'label':'Local review complete' if review else 'Local preview',
            'tone':'muted','connection':'Connected and ready' if ready else 'Not connected',
            'sync':'Off in preview','scope':'Reviewed locally' if review else 'Not reviewed',
            'push':feedback.commit[:12] if verified else 'None verified in preview'}

class Badge(tk.Canvas):
    def __init__(self,parent,text,tone='muted'):
        self.text=text;self.tone=tone;self.face=font.Font(family=family(parent),size=9)
        super().__init__(parent,width=self.face.measure(text)+32,height=30,bg=background(parent),highlightthickness=0,bd=0)
        self.bind('<Configure>',self.draw)
    def draw(self,event=None):
        self.delete('all');width=max(1,self.winfo_width());height=max(1,self.winfo_height())
        fill='#34151e' if self.tone=='blocked' else '#251c22';fg='#efa0b3' if self.tone=='blocked' else '#c4b4bd'
        rounded(self,1,2,max(1,width-2),max(1,height-4),13,fill=fill,outline='')
        self.create_oval(10,height/2-2,14,height/2+2,fill=CRIMSON if self.tone=='blocked' else MUTED,outline='')
        self.create_text(21,height/2,text=self.text,font=self.face,fill=fg,anchor='w')
