#!/usr/bin/env python3
import argparse
import datetime as dt
import glob
import hashlib
import json
import os
import socket
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo


BASE_DIR = os.path.expanduser("~/.cli-proxy-api/usage-dashboard")
AUTH_DIR = os.path.expanduser("~/.cli-proxy-api")
DB_PATH = os.path.join(BASE_DIR, "usage.sqlite")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
LOCAL_TZ = ZoneInfo("Asia/Shanghai")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CLIPROXY_CONFIG_PATH = os.path.abspath(os.path.join(SCRIPT_DIR, os.pardir, "config.yaml"))


DEFAULT_CONFIG = {
    "cliproxy_host": "127.0.0.1",
    "cliproxy_port": 8317,
    "management_key": "",
    "poll_interval_seconds": 2,
    "quota_refresh_seconds": 7200,
    "dashboard_host": "127.0.0.1",
    "dashboard_port": 8320,
    "cliproxy_config_path": DEFAULT_CLIPROXY_CONFIG_PATH,
}


def ensure_dirs():
    os.makedirs(BASE_DIR, exist_ok=True)


def load_config():
    ensure_dirs()
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        os.chmod(CONFIG_PATH, 0o600)
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    merged = dict(DEFAULT_CONFIG)
    merged.update(cfg)
    merged["management_key"] = os.environ.get("CLIPROXY_MANAGEMENT_KEY", merged["management_key"])
    merged["cliproxy_config_path"] = os.environ.get("CLIPROXY_CONFIG_PATH", merged["cliproxy_config_path"])
    return merged


def db_connect():
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db():
    with db_connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS usage_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              event_key TEXT NOT NULL UNIQUE,
              timestamp TEXT NOT NULL,
              ts_epoch REAL NOT NULL,
              local_date TEXT NOT NULL,
              local_hour TEXT NOT NULL,
              request_id TEXT,
              auth_index TEXT,
              source TEXT,
              provider TEXT,
              model TEXT,
              endpoint TEXT,
              auth_type TEXT,
              api_key_hash TEXT,
              failed INTEGER NOT NULL DEFAULT 0,
              latency_ms INTEGER DEFAULT 0,
              input_tokens INTEGER DEFAULT 0,
              output_tokens INTEGER DEFAULT 0,
              reasoning_tokens INTEGER DEFAULT 0,
              cached_tokens INTEGER DEFAULT 0,
              total_tokens INTEGER DEFAULT 0,
              raw_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_events(ts_epoch);
            CREATE INDEX IF NOT EXISTS idx_usage_date ON usage_events(local_date);
            CREATE INDEX IF NOT EXISTS idx_usage_source ON usage_events(source);
            CREATE INDEX IF NOT EXISTS idx_usage_auth ON usage_events(auth_index);

            CREATE TABLE IF NOT EXISTS quota_snapshots (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              timestamp TEXT NOT NULL,
              ts_epoch REAL NOT NULL,
              email TEXT NOT NULL,
              plan TEXT,
              allowed INTEGER,
              limit_reached INTEGER,
              primary_used_percent INTEGER,
              primary_remaining_percent INTEGER,
              primary_reset_at TEXT,
              secondary_used_percent INTEGER,
              secondary_remaining_percent INTEGER,
              secondary_reset_at TEXT,
              credits_balance TEXT,
              raw_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_quota_email_ts ON quota_snapshots(email, ts_epoch);
            """
        )


def parse_rfc3339(value):
    if not value:
        return dt.datetime.now(dt.timezone.utc)
    text = value.replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return dt.datetime.now(dt.timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def resp_command(*parts):
    data = [f"*{len(parts)}\r\n".encode()]
    for part in parts:
        b = str(part).encode()
        data.append(f"${len(b)}\r\n".encode())
        data.append(b + b"\r\n")
    return b"".join(data)


class RespClient:
    def __init__(self, host, port, password, timeout=10):
        if not password:
            raise RuntimeError("management_key is required in config.json or CLIPROXY_MANAGEMENT_KEY")
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.file = self.sock.makefile("rb")
        self.send("AUTH", password)
        reply = self.read()
        if not (isinstance(reply, str) and reply.upper() == "OK"):
            raise RuntimeError(f"AUTH failed: {reply!r}")

    def close(self):
        try:
            self.file.close()
        finally:
            self.sock.close()

    def send(self, *parts):
        self.sock.sendall(resp_command(*parts))

    def read_line(self):
        line = self.file.readline()
        if not line:
            raise EOFError("RESP connection closed")
        return line.rstrip(b"\r\n")

    def read(self):
        line = self.read_line()
        prefix = line[:1]
        payload = line[1:]
        if prefix == b"+":
            return payload.decode()
        if prefix == b"-":
            raise RuntimeError(payload.decode())
        if prefix == b":":
            return int(payload)
        if prefix == b"$":
            length = int(payload)
            if length == -1:
                return None
            data = self.file.read(length)
            self.file.read(2)
            return data.decode("utf-8", "replace")
        if prefix == b"*":
            count = int(payload)
            if count == -1:
                return None
            return [self.read() for _ in range(count)]
        raise RuntimeError(f"Unknown RESP prefix: {line!r}")

    def rpop(self, count=100):
        self.send("RPOP", "queue", count)
        result = self.read()
        if result is None:
            return []
        if isinstance(result, list):
            return [x for x in result if x]
        return [result]


def event_key(payload, raw):
    rid = payload.get("request_id")
    if rid:
        return rid
    return hashlib.sha256(raw.encode()).hexdigest()


def insert_usage(raw_items):
    inserted = 0
    with db_connect() as conn:
        for raw in raw_items:
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            ts_utc = parse_rfc3339(payload.get("timestamp"))
            ts_local = ts_utc.astimezone(LOCAL_TZ)
            tokens = payload.get("tokens") or {}
            api_key = payload.get("api_key") or ""
            api_hash = hashlib.sha256(api_key.encode()).hexdigest()[:12] if api_key else ""
            values = {
                "event_key": event_key(payload, raw),
                "timestamp": ts_utc.isoformat(),
                "ts_epoch": ts_utc.timestamp(),
                "local_date": ts_local.strftime("%Y-%m-%d"),
                "local_hour": ts_local.strftime("%Y-%m-%d %H:00"),
                "request_id": payload.get("request_id"),
                "auth_index": payload.get("auth_index"),
                "source": payload.get("source"),
                "provider": payload.get("provider"),
                "model": payload.get("model"),
                "endpoint": payload.get("endpoint"),
                "auth_type": payload.get("auth_type"),
                "api_key_hash": api_hash,
                "failed": 1 if payload.get("failed") else 0,
                "latency_ms": int(payload.get("latency_ms") or 0),
                "input_tokens": int(tokens.get("input_tokens") or 0),
                "output_tokens": int(tokens.get("output_tokens") or 0),
                "reasoning_tokens": int(tokens.get("reasoning_tokens") or 0),
                "cached_tokens": int(tokens.get("cached_tokens") or 0),
                "total_tokens": int(tokens.get("total_tokens") or 0),
                "raw_json": raw,
            }
            try:
                conn.execute(
                    """
                    INSERT INTO usage_events (
                      event_key,timestamp,ts_epoch,local_date,local_hour,request_id,auth_index,source,
                      provider,model,endpoint,auth_type,api_key_hash,failed,latency_ms,input_tokens,
                      output_tokens,reasoning_tokens,cached_tokens,total_tokens,raw_json
                    ) VALUES (
                      :event_key,:timestamp,:ts_epoch,:local_date,:local_hour,:request_id,:auth_index,:source,
                      :provider,:model,:endpoint,:auth_type,:api_key_hash,:failed,:latency_ms,:input_tokens,
                      :output_tokens,:reasoning_tokens,:cached_tokens,:total_tokens,:raw_json
                    )
                    """,
                    values,
                )
                inserted += 1
            except sqlite3.IntegrityError:
                pass
    return inserted


def latest_quota_age():
    with db_connect() as conn:
        row = conn.execute("SELECT MAX(ts_epoch) AS ts FROM quota_snapshots").fetchone()
    return None if row["ts"] is None else time.time() - row["ts"]


def auth_files():
    return sorted(glob.glob(os.path.join(AUTH_DIR, "codex-*.json")))


def refresh_quota(force=False):
    cfg = load_config()
    age = latest_quota_age()
    if not force and age is not None and age < cfg["quota_refresh_seconds"]:
        return 0
    now = dt.datetime.now(dt.timezone.utc)
    inserted = 0
    with db_connect() as conn:
        for path in auth_files():
            try:
                auth = json.load(open(path))
                token = auth.get("access_token")
                email = auth.get("email") or os.path.basename(path)
                if not token:
                    continue
                req = urllib.request.Request(
                    "https://chatgpt.com/backend-api/wham/usage",
                    headers={
                        "Authorization": "Bearer " + token,
                        "Accept": "application/json",
                        "User-Agent": "codex-cli",
                    },
                )
                with urllib.request.urlopen(req, timeout=20) as resp:
                    data = json.load(resp)
                rl = data.get("rate_limit") or {}
                primary = rl.get("primary_window") or {}
                secondary = rl.get("secondary_window") or {}
                primary_used = int(primary.get("used_percent") or 0)
                secondary_used = int(secondary.get("used_percent") or 0)
                conn.execute(
                    """
                    INSERT INTO quota_snapshots (
                      timestamp,ts_epoch,email,plan,allowed,limit_reached,
                      primary_used_percent,primary_remaining_percent,primary_reset_at,
                      secondary_used_percent,secondary_remaining_percent,secondary_reset_at,
                      credits_balance,raw_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        now.isoformat(),
                        now.timestamp(),
                        email,
                        data.get("plan_type"),
                        1 if rl.get("allowed") else 0,
                        1 if rl.get("limit_reached") else 0,
                        primary_used,
                        max(0, 100 - primary_used),
                        epoch_to_local(primary.get("reset_at")),
                        secondary_used,
                        max(0, 100 - secondary_used),
                        epoch_to_local(secondary.get("reset_at")),
                        str((data.get("credits") or {}).get("balance", "")),
                        json.dumps(data, ensure_ascii=False),
                    ),
                )
                inserted += 1
            except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, KeyError) as exc:
                print(f"quota refresh failed for {path}: {exc}", file=sys.stderr)
    return inserted


def epoch_to_local(value):
    if not value:
        return ""
    return dt.datetime.fromtimestamp(int(value), LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")


def collect_forever():
    init_db()
    cfg = load_config()
    last_quota = 0
    while True:
        try:
            client = RespClient(cfg["cliproxy_host"], cfg["cliproxy_port"], cfg["management_key"])
            try:
                while True:
                    raw_items = client.rpop(100)
                    if raw_items:
                        inserted = insert_usage(raw_items)
                        if inserted:
                            print(f"inserted {inserted} usage events", flush=True)
                    now = time.time()
                    if now - last_quota >= cfg["quota_refresh_seconds"]:
                        refresh_quota(force=True)
                        last_quota = now
                    time.sleep(cfg["poll_interval_seconds"])
            finally:
                client.close()
        except Exception as exc:
            print(f"collector error: {exc}", file=sys.stderr, flush=True)
            time.sleep(5)


def range_bounds(name):
    now = dt.datetime.now(LOCAL_TZ)
    if name == "5h":
        start = now - dt.timedelta(hours=5)
    elif name == "1h":
        start = now - dt.timedelta(hours=1)
    elif name == "24h":
        start = now - dt.timedelta(hours=24)
    elif name == "7d":
        start = now - dt.timedelta(days=7)
    else:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(dt.timezone.utc).timestamp(), now.astimezone(dt.timezone.utc).timestamp()


def strip_yaml_scalar(value):
    text = value.strip()
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        return text[1:-1]
    return text.split(" #", 1)[0].strip()


def configured_api_keys(config_path):
    if not config_path:
        return []
    path = os.path.expanduser(config_path)
    if not os.path.exists(path):
        return []
    keys = []
    in_top_level_api_keys = False
    with open(path, encoding="utf-8-sig") as f:
        for raw in f:
            if not raw.strip():
                continue
            stripped = raw.strip()
            if stripped.startswith("#"):
                if in_top_level_api_keys and not raw.startswith(" "):
                    break
                continue
            indent = len(raw) - len(raw.lstrip(" "))
            if indent == 0 and stripped == "api-keys:":
                in_top_level_api_keys = True
                continue
            if in_top_level_api_keys:
                if indent == 0:
                    break
                if stripped.startswith("- "):
                    key = strip_yaml_scalar(stripped[2:])
                    if key:
                        keys.append(key)
    return keys


def api_key_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def mask_config_api_key(value):
    text = str(value or "")
    if not text:
        return "unknown"
    if "-" in text[3:]:
        return text.rsplit("-", 1)[-1][-4:]
    if text.startswith("sk-") and len(text) > 10:
        return text[-4:]
    if len(text) <= 8:
        return text
    return text[-4:]


def configured_api_summaries(stats_by_hash, config_path):
    configured = configured_api_keys(config_path)
    if not configured:
        return [
            {
                "label": "unknown" if key_hash == "unknown" else "hash******" + key_hash[-4:],
                **values,
            }
            for key_hash, values in sorted(
                stats_by_hash.items(), key=lambda item: item[1]["total_tokens"], reverse=True
            )
        ]
    summaries = []
    for key in configured:
        key_hash = api_key_hash(key)
        values = stats_by_hash.get(
            key_hash,
            {
                "requests": 0,
                "succeeded": 0,
                "failed": 0,
                "total_tokens": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
            },
        )
        summaries.append({"label": mask_config_api_key(key), "api_key_hash": key_hash, **values})
    return sorted(summaries, key=lambda item: item["total_tokens"], reverse=True)


def configured_api_label_by_hash(config_path):
    return {api_key_hash(key): mask_config_api_key(key) for key in configured_api_keys(config_path)}


def query_summary(range_name):
    start, end = range_bounds(range_name)
    cfg = load_config()
    with db_connect() as conn:
        total = conn.execute(
            """
            SELECT COUNT(*) requests,
                   COALESCE(SUM(total_tokens),0) total_tokens,
                   COALESCE(SUM(input_tokens),0) input_tokens,
                   COALESCE(SUM(output_tokens),0) output_tokens,
                   COALESCE(SUM(reasoning_tokens),0) reasoning_tokens,
                   COALESCE(SUM(cached_tokens),0) cached_tokens,
                   COALESCE(SUM(failed),0) failed
            FROM usage_events WHERE ts_epoch BETWEEN ? AND ?
            """,
            (start, end),
        ).fetchone()
        accounts = conn.execute(
            """
            SELECT COALESCE(source, auth_index, 'unknown') account,
                   COUNT(*) requests,
                   COALESCE(SUM(total_tokens),0) total_tokens,
                   COALESCE(SUM(input_tokens),0) input_tokens,
                   COALESCE(SUM(output_tokens),0) output_tokens,
                   COALESCE(SUM(reasoning_tokens),0) reasoning_tokens,
                   COALESCE(SUM(failed),0) failed
            FROM usage_events WHERE ts_epoch BETWEEN ? AND ?
            GROUP BY account ORDER BY total_tokens DESC
            """,
            (start, end),
        ).fetchall()
        models = conn.execute(
            """
            SELECT COALESCE(model, 'unknown') model,
                   COUNT(*) requests,
                   COALESCE(SUM(total_tokens),0) total_tokens,
                   COALESCE(SUM(failed),0) failed
            FROM usage_events WHERE ts_epoch BETWEEN ? AND ?
            GROUP BY model ORDER BY total_tokens DESC LIMIT 12
            """,
            (start, end),
        ).fetchall()
        hours = conn.execute(
            """
            SELECT local_hour hour,
                   COUNT(*) requests,
                   COALESCE(SUM(total_tokens),0) total_tokens,
                   COALESCE(SUM(failed),0) failed
            FROM usage_events WHERE ts_epoch BETWEEN ? AND ?
            GROUP BY local_hour ORDER BY local_hour
            """,
            (start, end),
        ).fetchall()
        apis = conn.execute(
            """
            SELECT COALESCE(NULLIF(api_key_hash, ''), 'unknown') api_key_hash,
                   COUNT(*) requests,
                   COALESCE(SUM(CASE WHEN failed = 0 THEN 1 ELSE 0 END),0) succeeded,
                   COALESCE(SUM(failed),0) failed,
                   COALESCE(SUM(total_tokens),0) total_tokens,
                   COALESCE(SUM(input_tokens),0) input_tokens,
                   COALESCE(SUM(output_tokens),0) output_tokens,
                   COALESCE(SUM(reasoning_tokens),0) reasoning_tokens
            FROM usage_events WHERE ts_epoch BETWEEN ? AND ?
            GROUP BY api_key_hash ORDER BY total_tokens DESC LIMIT 12
            """,
            (start, end),
        ).fetchall()
        api_stats = {row["api_key_hash"]: {k: row[k] for k in row.keys() if k != "api_key_hash"} for row in apis}
    return {
        "range": range_name,
        "summary": dict(total),
        "accounts": [dict(x) for x in accounts],
        "models": [dict(x) for x in models],
        "hours": [dict(x) for x in hours],
        "apis": configured_api_summaries(api_stats, cfg["cliproxy_config_path"]),
    }


def latest_quotas(force=False):
    refresh_quota(force=force)
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT q.* FROM quota_snapshots q
            JOIN (
              SELECT email, MAX(ts_epoch) ts FROM quota_snapshots GROUP BY email
            ) latest ON latest.email = q.email AND latest.ts = q.ts_epoch
            ORDER BY email
            """
        ).fetchall()
    return [dict(row) for row in rows]


def recent_requests(limit=100):
    cfg = load_config()
    api_labels = configured_api_label_by_hash(cfg["cliproxy_config_path"])
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT timestamp, source, auth_index, model, endpoint, failed, latency_ms,
                   input_tokens, output_tokens, reasoning_tokens, cached_tokens, total_tokens,
                   request_id, api_key_hash
            FROM usage_events ORDER BY ts_epoch DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["local_time"] = parse_rfc3339(item["timestamp"]).astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
        key_hash = item.get("api_key_hash") or ""
        item["api_label"] = api_labels.get(key_hash, "unknown" if not key_hash else "hash******" + key_hash[-4:])
        result.append(item)
    return result


def json_response(handler, payload, status=200):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        try:
            if parsed.path == "/":
                self.serve_html()
            elif parsed.path == "/api/summary":
                json_response(self, query_summary(qs.get("range", ["today"])[0]))
            elif parsed.path == "/api/quota":
                json_response(self, {"quotas": latest_quotas(force=qs.get("force", ["0"])[0] == "1")})
            elif parsed.path == "/api/requests":
                limit = min(500, int(qs.get("limit", ["100"])[0]))
                json_response(self, {"requests": recent_requests(limit)})
            elif parsed.path == "/api/health":
                json_response(self, {"ok": True, "db": DB_PATH, "auth_files": len(auth_files())})
            else:
                json_response(self, {"error": "not found"}, 404)
        except Exception as exc:
            json_response(self, {"error": str(exc)}, 500)

    def serve_html(self):
        body = DASHBOARD_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


DASHBOARD_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>CLIProxyAPI 用量统计</title>
  <style>
    :root { color-scheme: light; --bg:#f6f7f9; --panel:#fff; --text:#17202a; --muted:#667085; --line:#d9dee7; --blue:#2563eb; --green:#0f9f6e; --red:#d92d20; --amber:#b7791f; }
    * { box-sizing: border-box; }
    body { margin:0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background:var(--bg); color:var(--text); }
    header { display:flex; align-items:center; justify-content:space-between; gap:16px; padding:18px 24px; border-bottom:1px solid var(--line); background:#fff; position:sticky; top:0; z-index:2; }
    h1 { font-size:20px; margin:0; }
    main { padding:20px 24px 32px; max-width:1440px; margin:0 auto; }
    button, select { border:1px solid var(--line); background:#fff; color:var(--text); border-radius:6px; padding:8px 10px; font-size:14px; }
    button.primary { background:var(--blue); color:#fff; border-color:var(--blue); }
    .toolbar { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
    .date-filter { position:relative; }
    .date-filter-control { position:relative; }
    .date-filter-trigger { height:38px; min-width:218px; display:flex; align-items:center; justify-content:flex-start; gap:8px; padding:8px 32px 8px 12px; background:#fff; border-radius:4px; }
    .date-filter-trigger[aria-expanded="true"] { border-color:#2684ff; box-shadow:0 0 0 2px rgba(38, 132, 255, .12); }
    .date-filter-icon { flex:none; width:14px; height:14px; color:#b8c2d2; }
    .date-filter-value { max-width:154px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-weight:400; color:#344054; }
    .date-filter-clear { position:absolute; top:50%; right:8px; width:18px; height:18px; display:flex; align-items:center; justify-content:center; transform:translateY(-50%); border:0; border-radius:50%; padding:0; background:transparent; color:#98a2b3; font-size:18px; line-height:1; opacity:0; }
    .date-filter-clear[hidden] { display:none; }
    .date-filter-trigger.has-value + .date-filter-clear { display:flex; }
    .date-filter-control:hover .date-filter-clear, .date-filter-control:focus-within .date-filter-clear { opacity:1; }
    .date-filter-clear:hover { color:#667085; background:#eef2f7; }
    .date-filter-popover { position:absolute; left:0; top:calc(100% + 8px); width:438px; min-height:302px; padding:0; display:grid; grid-template-columns:110px minmax(0, 1fr); background:#fff; border:1px solid var(--line); border-radius:3px; box-shadow:0 12px 30px rgba(15, 23, 42, .14); z-index:5; }
    .date-filter-popover::before { content:""; position:absolute; top:-6px; left:40px; width:10px; height:10px; background:#fff; border-left:1px solid var(--line); border-top:1px solid var(--line); transform:rotate(45deg); }
    .date-filter-popover[hidden] { display:none; }
    .date-filter-menu { padding:18px 10px; border-right:1px solid var(--line); display:grid; align-content:start; gap:6px; background:#fff; }
    .date-filter-menu button { display:flex; align-items:center; justify-content:center; border:1px solid transparent; border-radius:3px; height:34px; background:transparent; color:#1f2937; font-weight:400; line-height:1; letter-spacing:0; }
    .date-filter-menu button.active { background:#eef6ff; color:#1f2937; box-shadow:none; }
    .date-filter-panel { min-width:0; display:flex; flex-direction:column; background:#fff; }
    .date-filter-head { display:grid; grid-template-columns:32px 32px 1fr 32px 32px; align-items:center; gap:2px; padding:11px 14px 8px; }
    .date-filter-head.compact { grid-template-columns:32px 1fr 32px; }
    .date-filter-head strong { text-align:center; font-size:16px; font-weight:500; }
    .date-nav { width:28px; height:28px; padding:0; border:0; background:#fff; color:#4a5568; font-size:18px; line-height:1; }
    .date-nav:hover { color:var(--blue); background:#f4f8ff; }
    .date-filter-grid { flex:1; display:grid; padding:0 16px 14px; }
    .date-filter-grid.day { grid-template-columns:repeat(7, minmax(0, 1fr)); grid-auto-rows:36px; }
    .date-filter-grid.month, .date-filter-grid.year { grid-template-columns:repeat(4, minmax(0, 1fr)); grid-auto-rows:64px; padding-top:8px; }
    .date-weekday, .date-cell { min-width:0; display:flex; align-items:center; justify-content:center; font-size:12px; }
    .date-filter-grid.month .date-cell, .date-filter-grid.year .date-cell { font-family:inherit; font-size:12px; font-weight:400; line-height:1; }
    .date-weekday { color:#344054; font-weight:500; }
    .date-cell { width:100%; height:32px; margin:auto; border:1px solid transparent; border-radius:0; background:transparent; cursor:pointer; padding:0; color:#2f3a4a; }
    .date-cell:hover { color:var(--blue); background:#f2f7ff; }
    .date-cell.outside { color:#aab4c3; background:#f6f8fb; }
    .date-cell.today:not(.selected) { color:#1677ff; font-weight:700; }
    .date-cell.selected { width:72%; border-radius:4px; color:#1677ff; border-color:transparent; background:#eef6ff; box-shadow:none; font-weight:400; }
    .grid { display:grid; gap:14px; }
    .kpis { grid-template-columns: repeat(5, minmax(150px, 1fr)); }
    .two { grid-template-columns: 1.2fr .8fr; margin-top:14px; }
    .panel { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px; min-width:0; }
    .chart-stack .hour-panel { grid-column:1 / -1; }
    .panel h2 { margin:0 0 12px; font-size:15px; }
    .kpi .label { color:var(--muted); font-size:12px; }
    .kpi .value { font-size:24px; font-weight:700; margin-top:6px; }
    .kpi .sub { color:var(--muted); font-size:12px; margin-top:4px; }
    table { width:100%; border-collapse:collapse; font-size:13px; }
    th, td { text-align:left; border-bottom:1px solid var(--line); padding:8px; white-space:nowrap; }
    th { color:var(--muted); font-weight:600; }
    td.num, th.num { text-align:right; }
    .scroll { overflow:auto; max-height:420px; }
    .status { display:inline-flex; align-items:center; gap:6px; }
    .request-status { font-weight:600; }
    .request-status.success { color:var(--green); }
    .request-status.failed { color:var(--red); }
    .dot { width:8px; height:8px; border-radius:50%; display:inline-block; background:var(--green); }
    .dot.bad { background:var(--red); }
    .muted { color:var(--muted); }
    canvas { width:100%; height:260px; display:block; }
    .bar { height:9px; background:#edf1f7; border-radius:999px; overflow:hidden; min-width:90px; }
    .bar > span { display:block; height:100%; background:var(--green); }
    .bar > span.warn { background:var(--amber); }
    .bar > span.bad { background:var(--red); }
    .api-panel { min-height:302px; }
    .api-list { display:grid; gap:10px; max-height:260px; overflow:auto; padding-right:2px; }
    .api-card { position:relative; border:1px solid var(--line); border-radius:6px; padding:12px; background:#fbfaf7; }
    .api-key { font-weight:700; margin-bottom:8px; }
    .api-metrics { display:flex; gap:6px; flex-wrap:wrap; color:var(--muted); font-size:12px; }
    .api-pill { display:inline-flex; align-items:center; gap:4px; border-radius:999px; background:#f0f1ed; padding:4px 8px; }
    .api-success { color:var(--green); }
    .api-failed { color:var(--red); }
    .api-empty { color:var(--muted); padding:20px 0; }
    @media (max-width: 900px) { .kpis, .two { grid-template-columns:1fr; } header { align-items:flex-start; flex-direction:column; } .date-filter-popover { width:min(438px, calc(100vw - 48px)); grid-template-columns:74px minmax(0, 1fr); } .date-filter-menu { padding-top:14px; } }
  </style>
</head>
<body>
  <header>
    <h1>CLIProxyAPI 用量统计</h1>
    <div class="toolbar">
      <div class="date-filter" id="dateFilter">
        <div class="date-filter-control">
          <button type="button" class="date-filter-trigger" id="dateFilterTrigger" aria-expanded="false" aria-controls="dateFilterPopover">
            <svg class="date-filter-icon" viewBox="0 0 16 16" aria-hidden="true">
              <rect x="2.5" y="3.5" width="11" height="10" rx="1" fill="none" stroke="currentColor"/>
              <path d="M2.5 6.5h11M5 2.5v3M11 2.5v3M5 8.5h1.5M7.25 8.5h1.5M9.5 8.5H11M5 10.75h1.5M7.25 10.75h1.5M9.5 10.75H11" fill="none" stroke="currentColor" stroke-linecap="round"/>
            </svg>
            <span class="date-filter-value" id="dateFilterSelection"></span>
          </button>
          <button type="button" class="date-filter-clear" id="dateFilterClear" aria-label="清除日期筛选" hidden>&times;</button>
        </div>
        <div class="date-filter-popover" id="dateFilterPopover" hidden>
          <div class="date-filter-menu" aria-label="日期粒度">
            <button type="button" data-view="day">日</button>
            <button type="button" data-view="month">月</button>
            <button type="button" data-view="year">年</button>
          </div>
          <div class="date-filter-panel">
            <div class="date-filter-head" id="dateFilterHead">
              <button type="button" class="date-nav" data-shift="year-prev" aria-label="上一年">&laquo;</button>
              <button type="button" class="date-nav" data-shift="month-prev" aria-label="上一月">&lsaquo;</button>
              <strong id="dateFilterTitle"></strong>
              <button type="button" class="date-nav" data-shift="month-next" aria-label="下一月">&rsaquo;</button>
              <button type="button" class="date-nav" data-shift="year-next" aria-label="下一年">&raquo;</button>
            </div>
            <div class="date-filter-grid" id="dateFilterGrid"></div>
          </div>
        </div>
      </div>
      <select id="range">
        <option value="today">今天</option>
        <option value="1h">最近 1 小时</option>
        <option value="5h">最近 5 小时</option>
        <option value="24h">最近 24 小时</option>
        <option value="7d">最近 7 天</option>
      </select>
      <button id="refresh">刷新</button>
      <button id="quota" class="primary">刷新余量</button>
      <span id="updated" class="muted"></span>
    </div>
  </header>
  <main>
    <section class="grid kpis">
      <div class="panel kpi"><div class="label">请求/任务数</div><div class="value" id="kReq">0</div><div class="sub" id="kFail">失败 0</div></div>
      <div class="panel kpi"><div class="label">总 Tokens</div><div class="value" id="kTok">0</div><div class="sub">输入 + 输出 + 推理</div></div>
      <div class="panel kpi"><div class="label">输入 Tokens</div><div class="value" id="kIn">0</div><div class="sub">含缓存命中另计</div></div>
      <div class="panel kpi"><div class="label">输出 Tokens</div><div class="value" id="kOut">0</div><div class="sub">模型回复</div></div>
      <div class="panel kpi"><div class="label">推理 Tokens</div><div class="value" id="kReason">0</div><div class="sub">reasoning</div></div>
    </section>
    <section class="grid two chart-stack">
      <div class="panel hour-panel"><h2>按小时消耗</h2><canvas id="hourChart" width="900" height="260"></canvas></div>
      <div class="panel api-panel"><h2>API 详细统计</h2><div class="api-list" id="apiDetails"></div></div>
      <div class="panel model-panel"><h2>模型消耗</h2><canvas id="modelChart" width="520" height="260"></canvas></div>
    </section>
    <section class="grid two">
      <div class="panel"><h2>账号消耗</h2><div class="scroll"><table><thead><tr><th>账号</th><th class="num">请求</th><th class="num">总 Token</th><th class="num">输入</th><th class="num">输出</th><th class="num">推理</th><th class="num">失败</th></tr></thead><tbody id="accounts"></tbody></table></div></div>
      <div class="panel"><h2>账号余量</h2><div class="scroll"><table><thead><tr><th>账号</th><th>状态</th><th>5h 剩余</th><th>7d 剩余</th><th>重置时间</th></tr></thead><tbody id="quotas"></tbody></table></div></div>
    </section>
    <section class="panel" style="margin-top:14px"><h2>最近每次请求/任务</h2><div class="scroll"><table><thead><tr><th>时间</th><th>账号</th><th>API</th><th>模型</th><th class="num">总 Token</th><th class="num">输入</th><th class="num">输出</th><th class="num">推理</th><th class="num">耗时</th><th>状态</th></tr></thead><tbody id="requests"></tbody></table></div></section>
  </main>
<script>
const nf = new Intl.NumberFormat('zh-CN');
const $ = id => document.getElementById(id);
function fmt(n){ return nf.format(n || 0); }
function compact(n){
  const value = Number(n || 0);
  if (value >= 1000000) return (value / 1000000).toFixed(value >= 10000000 ? 0 : 1).replace(/\.0$/, '') + 'M';
  if (value >= 1000) return (value / 1000).toFixed(value >= 10000 ? 0 : 1).replace(/\.0$/, '') + 'K';
  return fmt(value);
}
function esc(s){ return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
async function getJSON(url){ const r = await fetch(url); if(!r.ok) throw new Error(await r.text()); return r.json(); }
const today = new Date();
let calendarView = 'day';
let visibleDate = new Date(today.getFullYear(), today.getMonth(), 1);
let selectedPeriod = {type:'day', key: dateKey(today), label: dateKey(today)};
function pad2(n){ return String(n).padStart(2, '0'); }
function dateKey(date){ return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`; }
function monthKey(year, month){ return `${year}-${pad2(month + 1)}`; }
function yearKey(year){ return String(year); }
function closeDateFilter(){
  $('dateFilterPopover').hidden = true;
  $('dateFilterTrigger').setAttribute('aria-expanded', 'false');
}
function updateDateFilterTrigger(){
  const trigger = $('dateFilterTrigger');
  const clear = $('dateFilterClear');
  $('dateFilterSelection').textContent = selectedPeriod ? selectedPeriod.label : '选择日期';
  trigger.classList.toggle('has-value', Boolean(selectedPeriod));
  clear.hidden = !selectedPeriod;
}
function setCalendarView(view){
  calendarView = view;
  document.querySelectorAll('[data-view]').forEach(btn => btn.classList.toggle('active', btn.dataset.view === view));
  renderDateFilter();
}
function shiftCalendar(shift){
  const year = visibleDate.getFullYear();
  const month = visibleDate.getMonth();
  if (calendarView === 'day') {
    if (shift === 'year-prev') visibleDate = new Date(year - 1, month, 1);
    if (shift === 'year-next') visibleDate = new Date(year + 1, month, 1);
    if (shift === 'month-prev') visibleDate = new Date(year, month - 1, 1);
    if (shift === 'month-next') visibleDate = new Date(year, month + 1, 1);
  } else if (calendarView === 'month') {
    if (shift.endsWith('prev')) visibleDate = new Date(year - 1, month, 1);
    if (shift.endsWith('next')) visibleDate = new Date(year + 1, month, 1);
  } else if (calendarView === 'year') {
    if (shift.endsWith('prev')) visibleDate = new Date(year - 10, month, 1);
    if (shift.endsWith('next')) visibleDate = new Date(year + 10, month, 1);
  }
  renderDateFilter();
}
function pickDay(key){
  const [year, month] = key.split('-').map(Number);
  selectedPeriod = {type:'day', key, label:key};
  visibleDate = new Date(year, month - 1, 1);
  updateDateFilterTrigger();
  renderDateFilter();
}
function pickMonth(month){
  const year = visibleDate.getFullYear();
  const key = monthKey(year, month);
  selectedPeriod = {type:'month', key, label:key};
  visibleDate = new Date(year, month, 1);
  updateDateFilterTrigger();
  renderDateFilter();
}
function pickYear(year){
  const key = yearKey(year);
  selectedPeriod = {type:'year', key, label:key};
  visibleDate = new Date(year, visibleDate.getMonth(), 1);
  updateDateFilterTrigger();
  renderDateFilter();
}
function renderDayGrid(grid){
  const year = visibleDate.getFullYear();
  const month = visibleDate.getMonth();
  const weekdays = ['日','一','二','三','四','五','六'];
  const firstDay = new Date(year, month, 1).getDay();
  const gridStart = new Date(year, month, 1 - firstDay);
  $('dateFilterTitle').textContent = `${year} 年 ${month + 1} 月`;
  $('dateFilterHead').classList.remove('compact');
  document.querySelectorAll('[data-shift^="month"]').forEach(btn => btn.hidden = false);
  grid.className = 'date-filter-grid day';
  grid.innerHTML = weekdays.map(day => `<div class="date-weekday">${day}</div>`).join('');
  for (let i = 0; i < 42; i++) {
    const date = new Date(gridStart.getFullYear(), gridStart.getMonth(), gridStart.getDate() + i);
    const key = dateKey(date);
    const classes = ['date-cell'];
    if (key === dateKey(today)) classes.push('today');
    if (selectedPeriod?.type === 'day' && selectedPeriod.key === key) classes.push('selected');
    if (date.getMonth() !== month) classes.push('outside');
    grid.insertAdjacentHTML('beforeend', `<button type="button" class="${classes.join(' ')}" data-date="${key}">${date.getDate()}</button>`);
  }
}
function renderMonthGrid(grid){
  const year = visibleDate.getFullYear();
  const labels = ['一月','二月','三月','四月','五月','六月','七月','八月','九月','十月','十一月','十二月'];
  $('dateFilterTitle').textContent = `${year}年`;
  $('dateFilterHead').classList.add('compact');
  document.querySelectorAll('[data-shift^="month"]').forEach(btn => btn.hidden = true);
  grid.className = 'date-filter-grid month';
  grid.innerHTML = labels.map((label, month) => {
    const active = selectedPeriod?.type === 'month' && selectedPeriod.key === monthKey(year, month) ? ' selected' : '';
    return `<button type="button" class="date-cell${active}" data-month="${month}">${label}</button>`;
  }).join('');
}
function renderYearGrid(grid){
  const currentYear = visibleDate.getFullYear();
  const startYear = Math.floor(currentYear / 10) * 10;
  $('dateFilterTitle').textContent = `${startYear}年 - ${startYear + 9}年`;
  $('dateFilterHead').classList.add('compact');
  document.querySelectorAll('[data-shift^="month"]').forEach(btn => btn.hidden = true);
  grid.className = 'date-filter-grid year';
  grid.innerHTML = Array.from({length:10}, (_, i) => {
    const year = startYear + i;
    const active = selectedPeriod?.type === 'year' && selectedPeriod.key === yearKey(year) ? ' selected' : '';
    return `<button type="button" class="date-cell${active}" data-year="${year}">${year}</button>`;
  }).join('');
}
function renderDateFilter(){
  const grid = $('dateFilterGrid');
  if (calendarView === 'year') renderYearGrid(grid);
  if (calendarView === 'month') renderMonthGrid(grid);
  if (calendarView === 'day') renderDayGrid(grid);
}
function initDateFilter(){
  $('dateFilterTrigger').onclick = () => {
    const popover = $('dateFilterPopover');
    popover.hidden = !popover.hidden;
    $('dateFilterTrigger').setAttribute('aria-expanded', String(!popover.hidden));
    renderDateFilter();
  };
  document.querySelectorAll('[data-shift]').forEach(btn => btn.onclick = () => shiftCalendar(btn.dataset.shift));
  document.querySelectorAll('[data-view]').forEach(btn => btn.onclick = () => setCalendarView(btn.dataset.view));
  $('dateFilterClear').onclick = event => {
    event.stopPropagation();
    selectedPeriod = null;
    updateDateFilterTrigger();
    renderDateFilter();
  };
  $('dateFilterGrid').onclick = event => {
    const target = event.target.closest('button');
    if (!target) return;
    if (target.dataset.date) pickDay(target.dataset.date);
    if (target.dataset.month) pickMonth(Number(target.dataset.month));
    if (target.dataset.year) pickYear(Number(target.dataset.year));
  };
  document.addEventListener('click', event => {
    if ($('dateFilter').contains(event.target)) return;
    closeDateFilter();
  });
  updateDateFilterTrigger();
  setCalendarView('day');
}
function drawBars(canvas, rows, labelKey, valueKey, color){
  const ctx = canvas.getContext('2d'), w = canvas.width, h = canvas.height;
  ctx.clearRect(0,0,w,h); ctx.font = '12px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif';
  const pad = {l:52,r:18,t:30,b:44}; const max = Math.max(1, ...rows.map(r => Number(r[valueKey] || 0)));
  const bw = Math.max(12, (w-pad.l-pad.r) / Math.max(1, rows.length) * .62);
  rows.forEach((r,i) => {
    const x = pad.l + i * ((w-pad.l-pad.r) / Math.max(1, rows.length)) + bw*.3;
    const bh = (h-pad.t-pad.b) * Number(r[valueKey] || 0) / max;
    const y = h-pad.b-bh;
    ctx.fillStyle = color; ctx.fillRect(x,y,bw,bh);
    ctx.fillStyle = '#667085'; ctx.textAlign = 'center';
    const label = String(r[labelKey] || '').slice(-5);
    ctx.fillText(label, x+bw/2, h-18);
    if (bh > 18) { ctx.fillStyle = '#17202a'; ctx.fillText(fmt(r[valueKey]), x+bw/2, Math.max(14, y-7)); }
  });
  ctx.strokeStyle = '#d9dee7'; ctx.beginPath(); ctx.moveTo(pad.l,h-pad.b); ctx.lineTo(w-pad.r,h-pad.b); ctx.stroke();
}
function drawHorizontal(canvas, rows){
  const ctx = canvas.getContext('2d'), w = canvas.width, h = canvas.height;
  ctx.clearRect(0,0,w,h); ctx.font = '12px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif';
  const top = 12, rowH = 30, max = Math.max(1, ...rows.map(r => Number(r.total_tokens || 0)));
  rows.slice(0,8).forEach((r,i) => {
    const y = top + i * rowH; const labelW = 150; const barW = (w-labelW-90) * Number(r.total_tokens || 0) / max;
    ctx.fillStyle = '#344054'; ctx.textAlign='left'; ctx.fillText(String(r.model || 'unknown').slice(0,22), 8, y+18);
    ctx.fillStyle = '#0f9f6e'; ctx.fillRect(labelW, y+6, barW, 14);
    ctx.fillStyle = '#667085'; ctx.fillText(fmt(r.total_tokens), labelW + barW + 8, y+18);
  });
}
function quotaBar(v){
  const cls = v <= 10 ? 'bad' : (v <= 30 ? 'warn' : '');
  return `<div class="bar"><span class="${cls}" style="width:${Math.max(0, Math.min(100, v))}%"></span></div><span>${v}%</span>`;
}
function renderApis(rows){
  if (!rows || !rows.length) {
    $('apiDetails').innerHTML = '<div class="api-empty">暂无 API 数据</div>';
    return;
  }
  $('apiDetails').innerHTML = rows.map(api => `
    <div class="api-card">
      <div class="api-key">${esc(api.label || 'unknown')}</div>
      <div class="api-metrics">
        <span class="api-pill">请求次数: ${fmt(api.requests)} <span class="api-success">(${fmt(api.succeeded)}</span><span class="api-failed">${fmt(api.failed)})</span></span>
        <span class="api-pill">Token数量: ${compact(api.total_tokens)}</span>
      </div>
    </div>
  `).join('');
}
async function load(forceQuota=false){
  const range = $('range').value;
  const [summary, quota, reqs] = await Promise.all([
    getJSON('/api/summary?range=' + encodeURIComponent(range)),
    getJSON('/api/quota' + (forceQuota ? '?force=1' : '')),
    getJSON('/api/requests?limit=120')
  ]);
  const s = summary.summary;
  $('kReq').textContent = fmt(s.requests); $('kFail').textContent = '失败 ' + fmt(s.failed);
  $('kTok').textContent = fmt(s.total_tokens); $('kIn').textContent = fmt(s.input_tokens);
  $('kOut').textContent = fmt(s.output_tokens); $('kReason').textContent = fmt(s.reasoning_tokens);
  $('accounts').innerHTML = summary.accounts.map(a => `<tr><td>${esc(a.account)}</td><td class="num">${fmt(a.requests)}</td><td class="num">${fmt(a.total_tokens)}</td><td class="num">${fmt(a.input_tokens)}</td><td class="num">${fmt(a.output_tokens)}</td><td class="num">${fmt(a.reasoning_tokens)}</td><td class="num">${fmt(a.failed)}</td></tr>`).join('');
  $('quotas').innerHTML = quota.quotas.map(q => `<tr><td>${esc(q.email)}</td><td><span class="status"><span class="dot ${q.allowed ? '' : 'bad'}"></span>${q.allowed ? '可用' : '受限'}</span></td><td>${quotaBar(q.primary_remaining_percent)}</td><td>${quotaBar(q.secondary_remaining_percent)}</td><td><div>${esc(q.primary_reset_at)}</div><div class="muted">${esc(q.secondary_reset_at)}</div></td></tr>`).join('');
  $('requests').innerHTML = reqs.requests.map(r => `<tr><td>${esc(r.local_time)}</td><td>${esc(r.source || r.auth_index)}</td><td>${esc(r.api_label)}</td><td>${esc(r.model)}</td><td class="num">${fmt(r.total_tokens)}</td><td class="num">${fmt(r.input_tokens)}</td><td class="num">${fmt(r.output_tokens)}</td><td class="num">${fmt(r.reasoning_tokens)}</td><td class="num">${fmt(r.latency_ms)}ms</td><td><span class="request-status ${r.failed ? 'failed' : 'success'}">${r.failed ? '失败' : '成功'}</span></td></tr>`).join('');
  renderApis(summary.apis);
  drawBars($('hourChart'), summary.hours, 'hour', 'total_tokens', '#2563eb');
  drawHorizontal($('modelChart'), summary.models);
  $('updated').textContent = '更新于 ' + new Date().toLocaleTimeString('zh-CN');
}
$('refresh').onclick = () => load(false);
$('quota').onclick = () => load(true);
$('range').onchange = () => load(false);
initDateFilter();
load(false); setInterval(() => load(false), 30000);
</script>
</body>
</html>
"""


def serve():
    init_db()
    cfg = load_config()
    server = ThreadingHTTPServer((cfg["dashboard_host"], int(cfg["dashboard_port"])), DashboardHandler)
    print(f"dashboard listening on http://{cfg['dashboard_host']}:{cfg['dashboard_port']}", flush=True)
    server.serve_forever()


def print_report(range_name):
    init_db()
    summary = query_summary(range_name)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sub.add_parser("collect")
    sub.add_parser("serve")
    quota_p = sub.add_parser("quota")
    quota_p.add_argument("--force", action="store_true")
    report_p = sub.add_parser("report")
    report_p.add_argument("range", choices=["today", "1h", "5h", "24h", "7d"])
    args = parser.parse_args()
    if args.cmd == "init":
        init_db()
        load_config()
        print(DB_PATH)
    elif args.cmd == "collect":
        collect_forever()
    elif args.cmd == "serve":
        serve()
    elif args.cmd == "quota":
        init_db()
        print(json.dumps({"quotas": latest_quotas(force=args.force)}, ensure_ascii=False, indent=2))
    elif args.cmd == "report":
        print_report(args.range)


if __name__ == "__main__":
    main()
