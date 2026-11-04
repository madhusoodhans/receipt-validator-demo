"""Visitors: one browser tab each (a random UUID the page keeps in sessionStorage and sends as X-Visitor-Id).

Each visitor sees only their own runs and upload files. Upload files live on disk in UPLOAD_ROOT/<visitor id>/, never in
memory. Limits: one run at a time per visitor, uploads per minute per visitor and per IP, uploads and rows kept per
visitor, total upload disk, number of visitors; visitors idle for IDLE_S are removed with their rows and files.
"""
import os
import re
import secrets
import shutil
import threading
import time
from collections import deque
from datetime import datetime

import db

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
UPLOAD_ID = re.compile(r"^[0-9a-f]{20}$")
EXTS = (".jpg", ".png", ".webp")


def valid(visitor_id):
    """The id in lower case if it is UUID-shaped, else None."""
    v = (visitor_id or "").strip().lower()
    return v if UUID.match(v) else None


def _epoch(iso):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return 0.0


class Visitors:
    def __init__(self, con, root, idle_s=3600, max_uploads=20, max_rows=100, max_bytes=2 * 1024 ** 3,
                 max_visitors=500, per_visitor_min=6, per_ip_min=20, clock=time.time):
        self.con, self.root, self.clock = con, root, clock
        self.idle_s, self.max_uploads, self.max_rows, self.max_bytes = idle_s, max_uploads, max_rows, max_bytes
        self.max_visitors, self.per_visitor_min, self.per_ip_min = max_visitors, per_visitor_min, per_ip_min
        self.lock = threading.Lock()
        self.seen = {}  # visitor id -> last request, for visitors with stored runs or files
        self.running = set()  # visitors with a run in progress
        self.hits = {}  # ("v", id) or ("ip", address) -> deque of upload times in the last minute
        self.uploads = deque()  # every accepted upload's time in the last hour (GET /api/metrics)
        os.makedirs(root, exist_ok=True)
        for v, iso in db.visitors(con).items():
            self.seen[v] = _epoch(iso)
        for v in os.listdir(root):
            if valid(v) == v:
                self.seen[v] = max(self.seen.get(v, 0.0), os.path.getmtime(os.path.join(root, v)))

    # -- requests ------------------------------------------------------------------------------------------------
    def touch(self, visitor_id):
        """Marks a known visitor as active (any request with their id). Unknown ids are not stored."""
        with self.lock:
            if visitor_id in self.seen:
                self.seen[visitor_id] = self.clock()

    def _recent(self, key, now):
        """The upload times of `key` in the last minute (a deque the caller may append to)."""
        q = self.hits.setdefault(key, deque())
        while q and q[0] <= now - 60:
            q.popleft()
        return q

    def admit(self, visitor_id, ip, count=True):
        """Starts a run: None if allowed (the visitor is then busy until done()), else a polite refusal
        (http status, message). count=False skips the per-minute limits (demo images when Play all is on)."""
        now = self.clock()
        with self.lock:
            if visitor_id in self.running:
                return 409, "A check is already running in this tab. Wait for it to finish, then try again."
            if count:
                qv, qi = self._recent(("v", visitor_id), now), self._recent(("ip", ip), now)
                if len(qv) >= self.per_visitor_min:
                    return 429, f"You can check up to {self.per_visitor_min} images a minute. Please wait a moment and try again."
                if len(qi) >= self.per_ip_min:
                    return 429, "Too many uploads from your network right now. Please wait a minute and try again."
                qv.append(now); qi.append(now)
            self.running.add(visitor_id)
            new = visitor_id not in self.seen
            self.seen[visitor_id] = now
            self.uploads.append(now)
        if new:
            self._cap_visitors(visitor_id)
        return None

    def done(self, visitor_id):
        """Ends the visitor's run and applies the per-visitor and disk caps."""
        with self.lock:
            self.running.discard(visitor_id)
        self._cap_visitor(visitor_id)
        self._cap_disk(visitor_id)

    # -- files ---------------------------------------------------------------------------------------------------
    def folder(self, visitor_id):
        return os.path.join(self.root, visitor_id)

    def save(self, visitor_id, raw, ext):
        """Stores an upload in the visitor's folder; returns its random id."""
        uid = secrets.token_hex(10)
        os.makedirs(self.folder(visitor_id), exist_ok=True)
        with open(os.path.join(self.folder(visitor_id), uid + ext), "wb") as f:
            f.write(raw)
        return uid

    def path(self, visitor_id, upload_id):
        """The file of this visitor's upload, or None (another visitor's upload is simply not found)."""
        if not visitor_id or not UPLOAD_ID.match(upload_id or ""):
            return None
        for ext in EXTS:
            p = os.path.join(self.folder(visitor_id), upload_id + ext)
            if os.path.isfile(p):
                return p
        return None

    def _remove_files(self, visitor_id, upload_ids):
        for uid in upload_ids:
            p = self.path(visitor_id, uid)
            if p:
                os.remove(p)

    def _files(self, visitor_id):
        """[(mtime, upload id, path, bytes)] of the visitor's uploads, newest first."""
        try:
            entries = list(os.scandir(self.folder(visitor_id)))
        except FileNotFoundError:
            return []
        out = [(e.stat().st_mtime, os.path.splitext(e.name)[0], e.path, e.stat().st_size) for e in entries if e.is_file()]
        return sorted(out, reverse=True)

    def disk_bytes(self):
        try:
            return sum(f[3] for v in os.listdir(self.root) for f in self._files(v))
        except FileNotFoundError:
            return 0

    # -- removal -------------------------------------------------------------------------------------------------
    def reset(self, visitor_id):
        """POST /api/reset: this visitor's rows and files only."""
        db.reset(self.con, visitor_id)
        shutil.rmtree(self.folder(visitor_id), ignore_errors=True)

    def purge(self, visitor_id):
        """Removes everything of a visitor: rows, files and limits."""
        self.reset(visitor_id)
        with self.lock:
            self.seen.pop(visitor_id, None)
            self.hits.pop(("v", visitor_id), None)

    def _cap_visitor(self, visitor_id):
        """Keeps the newest max_uploads uploads (files and their rows) and max_rows runs of this visitor."""
        self._remove_files(visitor_id, db.trim(self.con, visitor_id, self.max_uploads, uploads_only=True))
        self._remove_files(visitor_id, db.trim(self.con, visitor_id, self.max_rows))
        old = self._files(visitor_id)[self.max_uploads:]  # files of runs that stored no row (failed or stopped)
        for _t, _uid, p, _n in old:
            os.remove(p)
        if old:
            db.drop_uploads(self.con, visitor_id, [f[1] for f in old])

    def _idle_order(self, keep):
        """Visitors that may be removed, least recently active first."""
        with self.lock:
            return [v for v, _t in sorted(self.seen.items(), key=lambda x: x[1]) if v != keep and v not in self.running]

    def _cap_visitors(self, keep):
        with self.lock:
            extra = len(self.seen) - self.max_visitors
        for v in self._idle_order(keep)[:max(0, extra)]:
            self.purge(v)

    def _cap_disk(self, keep):
        used = self.disk_bytes()
        for v in self._idle_order(keep):
            if used <= self.max_bytes:
                break
            used -= sum(f[3] for f in self._files(v))
            self.purge(v)

    def cleanup(self):
        """Removes visitors idle for idle_s (rows and files), folders of no known visitor, and stale rate counters.
        Returns the number of visitors removed."""
        now = self.clock()
        with self.lock:
            idle = [v for v, t in self.seen.items() if t < now - self.idle_s and v not in self.running]
            known = set(self.seen)
            for k in [k for k, q in self.hits.items() if not q or q[-1] <= now - 60]:
                del self.hits[k]
            while self.uploads and self.uploads[0] <= now - 3600:
                self.uploads.popleft()
        for v in idle:
            self.purge(v)
        for v in os.listdir(self.root):  # e.g. left from before a restart with a fresh database
            p = os.path.join(self.root, v)
            if v not in known and os.path.getmtime(p) < now - self.idle_s:
                shutil.rmtree(p, ignore_errors=True)
        return len(idle)

    def loop(self, every_s=300):
        while True:
            time.sleep(every_s)
            try:
                n = self.cleanup()
                if n:
                    print(f"cleanup: removed {n} idle visitor{'s' if n != 1 else ''}", flush=True)
            except Exception as e:  # noqa: BLE001
                print("cleanup failed:", e, flush=True)

    def metrics(self):
        now = self.clock()
        with self.lock:
            return {"visitors": len(self.seen), "active_5min": sum(t > now - 300 for t in self.seen.values()),
                    "running": len(self.running), "uploads_last_hour": sum(t > now - 3600 for t in self.uploads)}
