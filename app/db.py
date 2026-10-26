"""SQLite store: receipts, line_items, rejections, and 27B baseline times. D3's answers are kept on every row.

image_sha256 identifies the image by content, so the stats can count each distinct image once (GET /api/stats).
"""
import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

PATH = "data/receipts.db"
LOCK = threading.Lock()
SCHEMA = """
CREATE TABLE IF NOT EXISTS receipts (
  id INTEGER PRIMARY KEY, upload_id TEXT, file TEXT, created_at TEXT, needs_review INTEGER, store_name TEXT, date TEXT,
  currency TEXT, total REAL, sumcheck_ok INTEGER, receipt_json TEXT, sumcheck_json TEXT, d3_json TEXT, extract_ms REAL);
CREATE TABLE IF NOT EXISTS line_items (
  receipt_id INTEGER REFERENCES receipts(id), position INTEGER, name TEXT, quantity REAL, unit_price REAL, amount REAL);
CREATE TABLE IF NOT EXISTS rejections (
  id INTEGER PRIMARY KEY, upload_id TEXT, file TEXT, created_at TEXT, reasons_json TEXT, message TEXT, d3_json TEXT,
  message_ms REAL);
CREATE TABLE IF NOT EXISTS baseline_27b (
  image_sha256 TEXT PRIMARY KEY, file TEXT, measured_at TEXT, ms REAL, completion_tokens INTEGER, prompt_tokens INTEGER);
"""
UPLOADS = "data/uploads"



def connect(path=PATH):
    con = sqlite3.connect(path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    _migrate(con)
    return con


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def _migrate(con):
    """Adds image_sha256 to databases made before it existed, hashing the stored upload files."""
    files = None
    with LOCK, con:
        if "prompt_tokens" not in [r[1] for r in con.execute("PRAGMA table_info(baseline_27b)")]:
            con.execute("ALTER TABLE baseline_27b ADD COLUMN prompt_tokens INTEGER")
        for table in ("receipts", "rejections"):
            if "image_sha256" not in [r[1] for r in con.execute(f"PRAGMA table_info({table})")]:
                con.execute(f"ALTER TABLE {table} ADD COLUMN image_sha256 TEXT")
            rows = con.execute(f"SELECT id, upload_id FROM {table} WHERE image_sha256 IS NULL").fetchall()
            if rows and files is None:
                files = {os.path.splitext(f)[0]: f for f in os.listdir(UPLOADS)} if os.path.isdir(UPLOADS) else {}
            for r in rows:
                if r["upload_id"] in files:
                    h = sha256(open(os.path.join(UPLOADS, files[r["upload_id"]]), "rb").read())
                    con.execute(f"UPDATE {table} SET image_sha256 = ? WHERE id = ?", (h, r["id"]))


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def add_receipt(con, upload_id, file, rc, sc, d3, needs_review, extract_ms, image_sha256=None):
    with LOCK, con:
        cur = con.execute(
            "INSERT INTO receipts (upload_id, file, created_at, needs_review, store_name, date, currency, total, sumcheck_ok,"
            " receipt_json, sumcheck_json, d3_json, extract_ms, image_sha256) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (upload_id, file, _now(), int(needs_review), rc.get("store_name"), rc.get("date"), rc.get("currency"),
             rc.get("total"), int(sc["ok"]), json.dumps(rc), json.dumps(sc), json.dumps(d3), extract_ms, image_sha256))
        rid = cur.lastrowid
        con.executemany("INSERT INTO line_items VALUES (?,?,?,?,?,?)",
                        [(rid, n, i.get("name"), i.get("quantity"), i.get("unit_price"), i.get("amount"))
                         for n, i in enumerate(rc.get("items") or [])])
    return rid


def add_rejection(con, upload_id, file, reasons, message, d3, message_ms, image_sha256=None):
    with LOCK, con:
        return con.execute(
            "INSERT INTO rejections (upload_id, file, created_at, reasons_json, message, d3_json, message_ms, image_sha256)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (upload_id, file, _now(), json.dumps(reasons), message, json.dumps(d3), message_ms, image_sha256)).lastrowid


def add_baseline(con, image_sha256, file, ms, completion_tokens, prompt_tokens=None):
    with LOCK, con:
        con.execute("INSERT OR REPLACE INTO baseline_27b (image_sha256, file, measured_at, ms, completion_tokens, prompt_tokens)"
                    " VALUES (?,?,?,?,?,?)", (image_sha256, file, _now(), ms, completion_tokens, prompt_tokens))


def baselines(con):
    """{image_sha256: ms} of the 27B baseline times measured for uploads (the demo set's are in data/demo/baseline.json)."""
    with LOCK:
        return {r[0]: r[1] for r in con.execute("SELECT image_sha256, ms FROM baseline_27b")}


def baseline_tokens(con, image_sha256):
    """Prompt plus completion tokens of the 27B baseline on this upload, or None (not measured, or measured before
    prompt tokens were kept)."""
    with LOCK:
        r = con.execute("SELECT prompt_tokens, completion_tokens FROM baseline_27b WHERE image_sha256 = ?", (image_sha256,)).fetchone()
    return r[0] + (r[1] or 0) if r and r[0] is not None else None


def all_runs(con):
    """Every stored run, oldest first, with what the stats need."""
    with LOCK:
        a = con.execute("SELECT 'receipts' AS tbl, id, created_at, upload_id, image_sha256, d3_json, extract_ms AS model_ms,"
                        " needs_review, currency, total, NULL AS reasons_json FROM receipts").fetchall()
        b = con.execute("SELECT 'rejections' AS tbl, id, created_at, upload_id, image_sha256, d3_json, message_ms AS model_ms,"
                        " 0 AS needs_review, NULL AS currency, NULL AS total, reasons_json FROM rejections").fetchall()
    return sorted(a + b, key=lambda r: (r["created_at"], r["id"]))


def recent_receipts(con, limit=50):
    with LOCK:
        rows = con.execute("SELECT * FROM receipts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{"id": r["id"], "file": r["file"], "image_url": f"/api/uploads/{r['upload_id']}", "created_at": r["created_at"],
             "needs_review": r["needs_review"], "receipt": json.loads(r["receipt_json"]), "sumcheck_ok": bool(r["sumcheck_ok"]),
             "d3": json.loads(r["d3_json"])} for r in rows]


def recent_rejections(con, limit=50):
    with LOCK:
        rows = con.execute("SELECT * FROM rejections ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{"id": r["id"], "file": r["file"], "image_url": f"/api/uploads/{r['upload_id']}", "created_at": r["created_at"],
             "reasons": json.loads(r["reasons_json"]), "message": r["message"], "d3": json.loads(r["d3_json"])} for r in rows]


def reset(con):
    """Clears the results. The baseline_27b times stay: they describe images, not runs."""
    with LOCK, con:
        con.executescript("DELETE FROM line_items; DELETE FROM receipts; DELETE FROM rejections;")
