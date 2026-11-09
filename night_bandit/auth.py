"""GitHub App device authorization; DPAPI-protected tokens; no embedded secret."""
import ctypes
import json
import math
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from ctypes import wintypes
from pathlib import Path
from .config import Blocked
from .validation import reject_link

class Cancelled(Blocked): pass
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise Blocked("GitHub authorization redirect refused")

class GitHubTransport:
    def request(self,url,form=None,token=None):
        if not (url.startswith("https://api.github.com/") or url in
                ("https://github.com/login/device/code","https://github.com/login/oauth/access_token")):
            raise Blocked("unsupported authorization endpoint")
        headers={"Accept":"application/json","User-Agent":"Frontline-Night-Bandit/0.3",
                 "X-GitHub-Api-Version":"2026-03-10"}
        if token: headers["Authorization"]="Bearer "+token
        data=None
        if form is not None:
            data=urllib.parse.urlencode(form).encode()
            headers["Content-Type"]="application/x-www-form-urlencoded"
        req=urllib.request.Request(url,data=data,headers=headers)
        try:
            with urllib.request.build_opener(NoRedirect()).open(req,timeout=20) as response:
                raw=response.read(2*1024*1024+1)
            if len(raw)>2*1024*1024: raise Blocked("GitHub response exceeded safety limit")
            result=json.loads(raw)
            if not isinstance(result,dict): raise Blocked("unexpected GitHub response")
            return result
        except urllib.error.HTTPError as error:
            messages={401:"GitHub session expired or unauthorized; reconnect",
                      403:"GitHub permission denied; check App installation and repository permissions",
                      404:"GitHub repository or App resource unavailable",
                      429:"GitHub rate limit reached; wait before retrying"}
            raise Blocked(messages.get(error.code,"GitHub request failed; retry after checking the connection")) from None
        except (urllib.error.URLError,TimeoutError,ValueError):
            raise Blocked("GitHub connection failed; check network and retry") from None

class Blob(ctypes.Structure):
    _fields_=[("length",wintypes.DWORD),("data",ctypes.POINTER(ctypes.c_ubyte))]

def dpapi(data, decrypt=False):
    if os.name!="nt": raise Blocked("Windows DPAPI is required; no plaintext fallback")
    buf=ctypes.create_string_buffer(data)
    incoming=Blob(len(data),ctypes.cast(buf,ctypes.POINTER(ctypes.c_ubyte)))
    entropy_bytes=b"Frontline Night Bandit credential vault v1"
    entropy_buf=ctypes.create_string_buffer(entropy_bytes)
    entropy=Blob(len(entropy_bytes),ctypes.cast(entropy_buf,ctypes.POINTER(ctypes.c_ubyte)))
    outgoing=Blob()
    crypt=ctypes.WinDLL("crypt32",use_last_error=True)
    kernel=ctypes.WinDLL("kernel32",use_last_error=True)
    kernel.LocalFree.argtypes=[ctypes.c_void_p]
    kernel.LocalFree.restype=ctypes.c_void_p
    fn=crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.POINTER(Blob),
                 ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    fn.restype=wintypes.BOOL
    if not fn(ctypes.byref(incoming),None,ctypes.byref(entropy),None,None,1,ctypes.byref(outgoing)):
        raise Blocked("Windows credential protection failed; reconnect on this Windows account")
    try: return ctypes.string_at(outgoing.data,outgoing.length)
    finally:
        ctypes.memset(outgoing.data,0,outgoing.length)
        kernel.LocalFree(ctypes.cast(outgoing.data,ctypes.c_void_p))

class CredentialVault:
    def __init__(self,path,protect=dpapi,transport=None):
        self.path=Path(path)
        self.protect=protect
        self.transport=transport or GitHubTransport()

    def _locked(self):
        # OS-owned byte lock: serializes GUI/askpass renewal and releases on crashes.
        from contextlib import contextmanager
        @contextmanager
        def locked():
            lock=self.path.with_name(self.path.name+".lock")
            if lock.exists(): reject_link(lock)
            with lock.open("a+b") as stream:
                stream.seek(0,2)
                if stream.tell()==0: stream.write(b"0");stream.flush()
                deadline=time.monotonic()+35
                while True:
                    try:
                        stream.seek(0)
                        if os.name=="nt":
                            import msvcrt
                            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
                        else:
                            import fcntl
                            fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                        break
                    except OSError:
                        if time.monotonic()>=deadline: raise Blocked("GitHub session is busy; retry shortly") from None
                        time.sleep(.05)
                try: yield
                finally:
                    stream.seek(0)
                    if os.name=="nt":
                        import msvcrt
                        msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
        return locked()

    def _write(self,data):
        encrypted=self.protect(json.dumps(data).encode())
        if self.path.exists(): reject_link(self.path)
        fd,name=tempfile.mkstemp(prefix="vault-",dir=self.path.parent)
        try:
            with os.fdopen(fd,"wb") as stream:
                stream.write(encrypted);stream.flush();os.fsync(stream.fileno())
            os.replace(name,self.path)
        finally:
            if os.path.exists(name): os.unlink(name)

    @staticmethod
    def _expiry(seconds):
        if seconds is None: return None  # GitHub explicitly omitted expiration.
        if type(seconds) is not int or not 0<seconds<=366*86400:
            raise Blocked("GitHub returned an invalid session lifetime; reconnect")
        return time.time()+seconds

    def save(self,token,expires_in=28800):
        # Compatibility for existing callers/tests; no renewal credential invented.
        self.save_authorization({"access_token":token,"expires_in":expires_in},"")

    def save_authorization(self,response,client_id):
        token=response.get("access_token")
        if not isinstance(token,str) or not token: raise Blocked("missing GitHub access token")
        data={"schema":2,"token":token,"expires_at":self._expiry(response.get("expires_in"))}
        refresh=response.get("refresh_token")
        if refresh:
            if not isinstance(refresh,str) or not re.fullmatch(r"[A-Za-z0-9_.-]{4,100}",client_id):
                raise Blocked("GitHub renewal setup is incomplete; reconnect")
            expiry=self._expiry(response.get("refresh_token_expires_in"))
            if expiry is None or data["expires_at"] is None:
                raise Blocked("GitHub renewal lifetime is missing; reconnect")
            data.update(refresh_token=refresh,refresh_expires_at=expiry,client_id=client_id)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self._locked(): self._write(data)

    def _read(self):
        reject_link(self.path)
        try:
            data=json.loads(self.protect(self.path.read_bytes(),decrypt=True))
            if not isinstance(data,dict) or not isinstance(data.get("token"),str) or not data["token"]:
                raise ValueError()
            if "expires_at" not in data: raise ValueError()
            expires=data["expires_at"]
            if expires is None:
                if data.get("schema")!=2: raise ValueError()
            elif type(expires) not in (int,float) or not math.isfinite(expires): raise ValueError()
            return data
        except (ValueError,KeyError,TypeError):
            raise Blocked("saved GitHub session is invalid; reconnect") from None

    def load(self):
        if not self.path.exists(): raise Blocked("Connect GitHub first; no saved session")
        with self._locked():
            if not self.path.exists(): raise Blocked("Connect GitHub first; no saved session")
            data=self._read();expiry=data["expires_at"];now=time.time()
            if expiry is None or now<expiry-60: return data["token"]
            refresh=data.get("refresh_token")
            if not refresh:
                if now<expiry: return data["token"]
                raise Blocked("GitHub session expired; reconnect once to enable longer sign-in")
            refresh_expiry=data.get("refresh_expires_at",0)
            client=data.get("client_id","")
            if (type(refresh_expiry) not in (int,float) or not math.isfinite(refresh_expiry) or now>=refresh_expiry
                or not isinstance(refresh,str) or not re.fullmatch(r"[A-Za-z0-9_.-]{4,100}",client)):
                raise Blocked("GitHub sign-in needs renewal; reconnect")
            response=self.transport.request("https://github.com/login/oauth/access_token",
                        {"client_id":client,"grant_type":"refresh_token","refresh_token":refresh})
            if response.get("error"):
                raise Blocked("GitHub could not renew this session; reconnect")
            token=response.get("access_token");replacement=response.get("refresh_token")
            if not isinstance(token,str) or not token or not isinstance(replacement,str) or not replacement:
                raise Blocked("GitHub returned an incomplete renewed session; reconnect")
            access_expiry=self._expiry(response.get("expires_in"))
            renewal_expiry=self._expiry(response.get("refresh_token_expires_in"))
            if access_expiry is None or renewal_expiry is None:
                raise Blocked("GitHub returned incomplete renewal lifetimes; reconnect")
            data.update(schema=2,token=token,expires_at=access_expiry,
                        refresh_token=replacement,refresh_expires_at=renewal_expiry)
            self._write(data)
            return token

    def disconnect(self):
        if not self.path.exists(): return
        with self._locked():
            if self.path.exists():
                reject_link(self.path)
                self.path.unlink()

class GitHubProvider:
    name="GitHub"
    def __init__(self,transport=None): self.transport=transport or GitHubTransport()
    def begin(self,client_id):
        if not isinstance(client_id,str) or not re.fullmatch(r"[A-Za-z0-9_.-]{4,100}",client_id):
            raise Blocked("Owner setup required: enter the public GitHub App Client ID and enable device flow")
        data=self.transport.request("https://github.com/login/device/code",{"client_id":client_id})
        if data.get("error"): raise Blocked("GitHub App registration or device flow is unavailable; check owner setup")
        if data.get("verification_uri")!="https://github.com/login/device":
            raise Blocked("unexpected GitHub verification address")
        if not data.get("device_code") or not data.get("user_code"):
            raise Blocked("GitHub returned an incomplete device challenge")
        return data
    def finish(self,client_id,challenge,cancel,wait=None,clock=time.monotonic):
        wait=wait or cancel.wait
        deadline=clock()+min(int(challenge.get("expires_in",900)),900)
        interval=max(float(challenge.get("interval",5)),5)
        while clock()<deadline:
            if cancel.is_set() or wait(interval): raise Cancelled("GitHub sign-in cancelled")
            if clock()>=deadline: break
            data=self.transport.request("https://github.com/login/oauth/access_token",
                     {"client_id":client_id,"device_code":challenge["device_code"],
                      "grant_type":"urn:ietf:params:oauth:grant-type:device_code"})
            error=data.get("error")
            if error=="authorization_pending": continue
            if error=="slow_down":
                interval=max(interval+5,float(data.get("interval",0)));continue
            if error in ("access_denied","authorization_declined"):
                raise Cancelled("GitHub authorization denied or cancelled")
            if error: raise Blocked("GitHub authorization failed or expired; restart sign-in and check App device-flow settings")
            if not data.get("access_token"): raise Blocked("GitHub returned no usable access token")
            if cancel.is_set(): raise Cancelled("GitHub sign-in cancelled")
            return data
        raise Blocked("GitHub device authorization expired; restart sign-in")
    def repositories(self,token):
        items=[]
        for page in range(1,101):
            data=self.transport.request("https://api.github.com/user/installations?per_page=100&page="+str(page),token=token)
            installations=data.get("installations",[])
            for install in installations:
                if install.get("suspended_at") or install.get("permissions",{}).get("contents")!="write": continue
                ident=install.get("id")
                if type(ident) is not int: raise Blocked("invalid GitHub installation identity")
                for repo_page in range(1,101):
                    batch=self.transport.request("https://api.github.com/user/installations/"+str(ident)+
                         "/repositories?per_page=100&page="+str(repo_page),token=token).get("repositories",[])
                    for repo in batch:
                        full=repo.get("full_name","")
                        if (repo.get("private") is True and not repo.get("archived")
                            and repo.get("permissions",{}).get("push") is True
                            and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",full)):
                            items.append({"full_name":full,"branch":repo.get("default_branch","main"),
                                          "url":"https://github.com/"+full+".git","id":repo.get("id"),
                                          "installation_id":ident})
                    if len(batch)<100: break
            if len(installations)<100: break
        if not items: raise Blocked("No writable private repositories: install the App on selected repositories with Contents read/write")
        return sorted({r["full_name"]:r for r in items}.values(),key=lambda r:r["full_name"])
    def validate_selection(self,token,full_name):
        for repo in self.repositories(token):
            if repo["full_name"]==full_name: return repo
        raise Blocked("selected repository is no longer authorized; refresh repository permissions")

def git_auth_environment(config):
    if not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git",config.expected_remote_url):
        raise Blocked("saved GitHub session may only be used with a verified GitHub HTTPS repository")
    runtime=config.repository/".git/night-bandit"
    runtime.mkdir(exist_ok=True)
    import sys
    helper=Path(__file__).with_name("askpass.py").resolve()
    for path in (str(helper),sys.executable,config.credential_path):
        if any(c in path for c in '%!"\r\n'): raise Blocked("unsupported credential helper path")
    launcher=runtime/"askpass.cmd"
    if launcher.exists(): reject_link(launcher)
    launcher.write_text('@echo off\n"'+sys.executable+'" -B "'+str(helper)+'" %*\n',encoding="utf-8")
    return {"GIT_ASKPASS":str(launcher),"GIT_TERMINAL_PROMPT":"0",
            "NIGHT_BANDIT_VAULT":config.credential_path}
