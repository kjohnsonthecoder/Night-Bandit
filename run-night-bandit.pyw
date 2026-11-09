"""Source-distribution entry point; all private user state lives outside this checkout."""
import argparse
import os
from pathlib import Path
import tkinter as tk
from tkinter import messagebox
from night_bandit_desktop.ui import Desktop
from night_bandit_desktop.branding import load_private_fonts

def main():
    parser=argparse.ArgumentParser(description='NIGHT BANDIT desktop')
    parser.add_argument('--state-dir',type=Path,default=Path(os.environ.get('LOCALAPPDATA',Path.home()/'AppData/Local'))/'Frontline/NightBanditDesktop')
    args=parser.parse_args();load_private_fonts();root=tk.Tk();root.withdraw()
    try:
        Desktop(root,args.state_dir,show_startup=True);root.title('NIGHT BANDIT');root.deiconify();root.mainloop()
    except Exception:
        messagebox.showerror('NIGHT BANDIT could not open','Existing profiles were preserved. Check diagnostics or contact Frontline support.',parent=root)
        try:root.destroy()
        except tk.TclError:pass
        return 1
    return 0
if __name__=='__main__':raise SystemExit(main())
