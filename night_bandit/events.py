"""Thread-safe structured metadata, redaction, live subscription and safe export."""
import json
import re
import threading
from datetime import datetime, timezone
from .validation import reject_link

FIELDS={"timestamp","event","severity","reason","sha256","bytes","commit","base_commit",
        "verified_tree","would_commit","would_push","mode","dry_run","push_requested",
        "push_enabled","path","stage"}
def redact(value):
    text=str(value)
    text=re.sub(r"(?i)(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)","[REDACTED]",text)
    text=re.sub(r"(?i)(bearer\s+)[^\s,;\"]+",r"\1[REDACTED]",text)
    text=re.sub(r"(?i)((?:access_token|refresh_token|password|secret|token)\s*[=:]\s*)[^\s,;\"]+",r"\1[REDACTED]",text)
    text=re.sub(r"(https?://)[^/@\s]+:[^/@\s]+@",r"\1[REDACTED]@",text)
    return text[:2000]
def sanitize(record):
    result={}
    for key,value in record.items():
        if key in FIELDS:
            result[key]=value if type(value) in (bool,int,float) else redact(value)
    if result.get("severity") not in ("INFO","WARNING","ERROR"):
        result["severity"]="INFO"
    return result
def read_events(path,severity="ALL"):
    if not path or not path.exists(): return []
    reject_link(path)
    with path.open("rb") as stream:
        stream.seek(max(0,path.stat().st_size-2*1024*1024))
        lines=stream.read().decode("utf-8",errors="replace").splitlines()
    records=[]
    for line in lines:
        try: item=sanitize(json.loads(line))
        except (ValueError,TypeError,AttributeError): continue
        if severity=="ALL" or item["severity"]==severity: records.append(item)
    return records[-2000:]
def export_events(path,records):
    data="".join(json.dumps(sanitize(r),sort_keys=True)+"\n" for r in records)
    if path.exists(): reject_link(path)
    path.write_text(data,encoding="utf-8")
    return data

class Events:
    def __init__(self,path=None,callback=None):
        self.path=path
        self.callback=callback
        self.lock=threading.RLock()
    def emit(self,event,**fields):
        severity=fields.pop("severity","ERROR" if event in ("blocked","error","needs_review") else "INFO")
        record=sanitize({"timestamp":datetime.now(timezone.utc).isoformat(),"event":event,
                         "severity":severity,**fields})
        line=json.dumps(record,sort_keys=True)
        with self.lock:
            print(line,flush=True)
            if self.path:
                if self.path.exists() or self.path.is_symlink(): reject_link(self.path)
                if self.path.exists() and self.path.stat().st_size>5*1024*1024:
                    rotated=self.path.with_suffix(".jsonl.1")
                    if rotated.exists(): reject_link(rotated)
                    self.path.replace(rotated)
                with self.path.open("a",encoding="utf-8") as stream:
                    stream.write(line+"\n")
            if self.callback: self.callback(record)
