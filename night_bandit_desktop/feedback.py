"""Evidence-gated presentation contracts. Never performs Git/auth/sync operations."""
import re
from dataclasses import dataclass
from night_bandit.config import Blocked

LABELS={'not_connected':'Not connected','ready':'Connected and ready','changes':'Local changes detected',
        'preparing':'Commit preparation','pushing':'Push in progress','verifying':'Remote verification',
        'verified':'Successfully verified','blocked':'Failed or blocked','reading':'Reading local repository',
        'reviewed':'Local review complete','local_committed':'Committed locally — not pushed'}

@dataclass
class Feedback:
    phase:str='not_connected'
    reason:str='Preview has no live synchronization connection'
    commit:str=''
    completed:int|None=None
    total:int|None=None
    @property
    def label(self):return LABELS[self.phase]
    def connection(self,verified=False):
        if verified is not True:raise Blocked('Connection readiness requires a verified provider result')
        self.phase='ready';self.reason='Provider connection verified'
    def begin_read(self):self.phase='reading';self.reason='Local read-only operation';self.completed=self.total=None
    def finish_read(self):self.phase='reviewed';self.reason='Local metadata read complete; no remote push verified'
    def event(self,record,receipt_verified=False):
        name=record.get('event','')
        if name in ('blocked','automatic_blocked','needs_review'):
            self.phase='blocked';self.reason=record.get('reason','Review required');return
        mapping={'detected':'changes','automatic_change':'changes','commit_preparation':'preparing',
                 'push_started':'pushing','remote_verification':'verifying'}
        if name in mapping:self.phase=mapping[name];self.reason='Actual operation event';return
        if name=='committed':self.phase='local_committed';self.reason='Commit exists locally; remote verification pending';return
        if name=='pushed':
            valid=lambda pattern,value:isinstance(value,str) and re.fullmatch(pattern,value) is not None
            evidence=(receipt_verified is True and valid(r'[0-9a-f]{40,64}',record.get('commit',''))
                and valid(r'[0-9a-f]{40,64}',record.get('verified_tree',''))
                and valid(r'[0-9a-f]{64}',record.get('sha256','')))
            if not evidence:self.phase='verifying';self.reason='Push report requires verified receipt';return
            self.phase='verified';self.commit=record['commit'];self.reason='Exact remote receipt verified'
    def progress(self,completed,total):
        if type(completed) is not int or type(total) is not int or total<=0 or not 0<=completed<=total:
            raise Blocked('Progress requires actual bounded operation counts')
        self.completed=completed;self.total=total
        return completed/total
