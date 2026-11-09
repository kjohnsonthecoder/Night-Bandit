"""Hash polling with debounce and a separate stability window."""
import time
from .validation import snapshot


class ChangeDetector:
    def __init__(self, debounce_seconds, stable_seconds):
        self.delay = max(debounce_seconds, stable_seconds)
        self.candidate = None
        self.since = None
        self.emitted = None

    def observe(self, digest, now):
        if digest != self.candidate:
            self.candidate, self.since = digest, now
        if digest != self.emitted and now - self.since >= self.delay:
            self.emitted = digest
            return digest
        return None


def stable_snapshot(config, sleep=time.sleep, now=time.monotonic):
    detector = ChangeDetector(config.debounce_seconds, config.stable_seconds)
    deadline = now() + max(30.0, detector.delay * 4)
    while now() < deadline:
        current = snapshot(config)
        if detector.observe(current.digest, now()):
            return current
        sleep(config.poll_seconds)
    from .config import Blocked
    raise Blocked("file did not stabilize before timeout")
