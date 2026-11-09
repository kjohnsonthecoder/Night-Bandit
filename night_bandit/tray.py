"""Native Windows notification-area controls; no packages, service or startup entry."""
import ctypes
import os
from ctypes import wintypes as w
from .config import Blocked

class NativeTray:
    callback_message=0x8000+19
    def __init__(self,dispatch,is_running=lambda:False):
        if os.name!='nt':raise Blocked('System tray mode requires Windows')
        self.dispatch=dispatch;self.is_running=is_running;self.closed=False;self.hwnd=None
        self.user=ctypes.WinDLL('user32',use_last_error=True)
        self.shell=ctypes.WinDLL('shell32',use_last_error=True)
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        proc_type=ctypes.WINFUNCTYPE(ctypes.c_ssize_t,w.HWND,w.UINT,ctypes.c_size_t,ctypes.c_ssize_t)
        class WindowClass(ctypes.Structure):
            _fields_=[('style',w.UINT),('proc',proc_type),('class_extra',ctypes.c_int),('window_extra',ctypes.c_int),
                      ('instance',w.HINSTANCE),('icon',w.HICON),('cursor',w.HANDLE),('background',w.HANDLE),
                      ('menu',w.LPCWSTR),('name',w.LPCWSTR)]
        class Guid(ctypes.Structure):
            _fields_=[('a',w.DWORD),('b',w.WORD),('c',w.WORD),('d',ctypes.c_ubyte*8)]
        class IconData(ctypes.Structure):
            _fields_=[('size',w.DWORD),('window',w.HWND),('ident',w.UINT),('flags',w.UINT),('message',w.UINT),
                      ('icon',w.HICON),('tip',w.WCHAR*128),('state',w.DWORD),('state_mask',w.DWORD),
                      ('info',w.WCHAR*256),('version',w.UINT),('title',w.WCHAR*64),('info_flags',w.DWORD),
                      ('guid',Guid),('balloon_icon',w.HICON)]
        self.user.DefWindowProcW.argtypes=[w.HWND,w.UINT,ctypes.c_size_t,ctypes.c_ssize_t];self.user.DefWindowProcW.restype=ctypes.c_ssize_t
        self.user.RegisterClassW.argtypes=[ctypes.POINTER(WindowClass)];self.user.RegisterClassW.restype=w.WORD
        self.user.CreateWindowExW.argtypes=[w.DWORD,w.LPCWSTR,w.LPCWSTR,w.DWORD,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,w.HWND,w.HANDLE,w.HINSTANCE,ctypes.c_void_p];self.user.CreateWindowExW.restype=w.HWND
        self.user.DestroyWindow.argtypes=[w.HWND];self.user.DestroyWindow.restype=w.BOOL
        self.user.UnregisterClassW.argtypes=[w.LPCWSTR,w.HINSTANCE];self.user.UnregisterClassW.restype=w.BOOL
        self.user.LoadIconW.argtypes=[w.HINSTANCE,ctypes.c_void_p];self.user.LoadIconW.restype=w.HICON
        self.user.RegisterWindowMessageW.argtypes=[w.LPCWSTR];self.user.RegisterWindowMessageW.restype=w.UINT
        self.shell.Shell_NotifyIconW.argtypes=[w.DWORD,ctypes.POINTER(IconData)];self.shell.Shell_NotifyIconW.restype=w.BOOL
        self.kernel.GetModuleHandleW.argtypes=[w.LPCWSTR];self.kernel.GetModuleHandleW.restype=w.HMODULE
        self.user.CreatePopupMenu.restype=w.HMENU
        self.user.AppendMenuW.argtypes=[w.HMENU,w.UINT,ctypes.c_size_t,w.LPCWSTR];self.user.AppendMenuW.restype=w.BOOL
        self.user.TrackPopupMenu.argtypes=[w.HMENU,w.UINT,ctypes.c_int,ctypes.c_int,ctypes.c_int,w.HWND,ctypes.c_void_p];self.user.TrackPopupMenu.restype=w.UINT
        self.user.GetCursorPos.argtypes=[ctypes.POINTER(w.POINT)];self.user.GetCursorPos.restype=w.BOOL
        self.user.SetForegroundWindow.argtypes=[w.HWND];self.user.SetForegroundWindow.restype=w.BOOL
        self.user.PostMessageW.argtypes=[w.HWND,w.UINT,ctypes.c_size_t,ctypes.c_ssize_t];self.user.PostMessageW.restype=w.BOOL
        self.user.DestroyMenu.argtypes=[w.HMENU];self.user.DestroyMenu.restype=w.BOOL
        self.taskbar_created=self.user.RegisterWindowMessageW('TaskbarCreated')
        self.proc=proc_type(self._window_proc)
        self.instance=self.kernel.GetModuleHandleW(None);self.class_name='NightBanditTray_'+str(os.getpid())+'_'+str(id(self))
        wc=WindowClass();wc.proc=self.proc;wc.instance=self.instance;wc.name=self.class_name
        if not self.user.RegisterClassW(ctypes.byref(wc)):raise Blocked('Cannot prepare the Windows tray; keep the app open')
        self.hwnd=self.user.CreateWindowExW(0,self.class_name,'NIGHT BANDIT tray',0,0,0,0,0,None,None,self.instance,None)
        if not self.hwnd:
            self.user.UnregisterClassW(self.class_name,self.instance)
            raise Blocked('Cannot create the Windows tray; keep the app open')
        self.data=IconData();self.data.size=ctypes.sizeof(IconData);self.data.window=self.hwnd
        self.data.ident=1;self.data.flags=1|2|4;self.data.message=self.callback_message
        self.data.icon=self.user.LoadIconW(None,ctypes.c_void_p(32512))
        self.data.tip='NIGHT BANDIT — automatic sync is OFF'
        try:self._add()
        except Exception:self.close();raise
    def _add(self):
        if not self.shell.Shell_NotifyIconW(0,ctypes.byref(self.data)):
            raise Blocked('Windows tray is unavailable; keep the app open')
        self.data.version=4
        if not self.shell.Shell_NotifyIconW(4,ctypes.byref(self.data)):
            self.shell.Shell_NotifyIconW(2,ctypes.byref(self.data))
            raise Blocked('Cannot activate Windows tray controls; keep the app open')
    def _window_proc(self,hwnd,message,wp,lp):
        try:
            if not self.closed and message==self.taskbar_created:
                self._add();return 0
            if not self.closed and message==self.callback_message:
                event=lp&0xffff
                if event in (0x202,0x203,0x400):self.dispatch('open')
                elif event in (0x205,0x7b):self._menu()
                return 0
        except Exception:
            if not self.closed:self.dispatch('error')
            return 0
        return self.user.DefWindowProcW(hwnd,message,wp,lp)
    def _menu(self):
        menu=self.user.CreatePopupMenu()
        if not menu:raise Blocked('Tray menu unavailable')
        try:
            self.user.AppendMenuW(menu,0,1,'Open NIGHT BANDIT')
            self.user.AppendMenuW(menu,0 if self.is_running() else 1,2,'Stop automatic sync')
            self.user.AppendMenuW(menu,0x800,0,None)
            self.user.AppendMenuW(menu,0,3,'Exit NIGHT BANDIT')
            point=w.POINT();self.user.GetCursorPos(ctypes.byref(point));self.user.SetForegroundWindow(self.hwnd)
            choice=self.user.TrackPopupMenu(menu,0x100|0x80|0x2,point.x,point.y,0,self.hwnd,None)
            if choice:self.dispatch({1:'open',2:'stop',3:'exit'}[choice])
            self.user.PostMessageW(self.hwnd,0,0,0)
        finally:self.user.DestroyMenu(menu)
    def update(self,message):
        if self.closed:return
        self.data.tip=('NIGHT BANDIT — '+message)[:127]
        if not self.shell.Shell_NotifyIconW(1,ctypes.byref(self.data)):
            raise Blocked('Windows tray disappeared; automatic sync must stop')
    def close(self):
        if self.closed:return
        self.closed=True
        if self.hwnd:
            if hasattr(self,'data'):self.shell.Shell_NotifyIconW(2,ctypes.byref(self.data))
            self.user.DestroyWindow(self.hwnd);self.hwnd=None
            self.user.UnregisterClassW(self.class_name,self.instance)
