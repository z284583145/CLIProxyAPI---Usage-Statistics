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
import threading
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
    "quota_refresh_seconds": 14400,
    "dashboard_host": "127.0.0.1",
    "dashboard_port": 8320,
    "cliproxy_config_path": DEFAULT_CLIPROXY_CONFIG_PATH,
}

COLLECTOR_STATUS = {
    "ok": False,
    "status": "异常",
    "message": "采集器未启动",
    "last_success_at": None,
    "last_error_at": None,
    "last_error": "",
}
COLLECTOR_STATUS_LOCK = threading.Lock()


def local_now_text():
    return dt.datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")


def mark_collector_success(message="采集正常"):
    with COLLECTOR_STATUS_LOCK:
        COLLECTOR_STATUS.update(
            {
                "ok": True,
                "status": "正常",
                "message": message,
                "last_success_at": local_now_text(),
            }
        )


def mark_collector_error(exc):
    with COLLECTOR_STATUS_LOCK:
        COLLECTOR_STATUS.update(
            {
                "ok": False,
                "status": "异常",
                "message": "采集异常",
                "last_error_at": local_now_text(),
                "last_error": str(exc),
            }
        )


def collector_status():
    with COLLECTOR_STATUS_LOCK:
        return dict(COLLECTOR_STATUS)


def ensure_dirs():
    os.makedirs(BASE_DIR, exist_ok=True)


def load_config():
    ensure_dirs()
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        os.chmod(CONFIG_PATH, 0o600)
    with open(CONFIG_PATH, encoding="utf-8-sig") as f:
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


SENSITIVE_RAW_FIELDS = {"api_key", "authorization", "access_token", "refresh_token", "id_token"}


def redact_sensitive_fields(value):
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if key.lower() in SENSITIVE_RAW_FIELDS and item:
                redacted[key] = "[redacted]"
            else:
                redacted[key] = redact_sensitive_fields(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive_fields(item) for item in value]
    return value


def safe_usage_raw_json(payload):
    return json.dumps(redact_sensitive_fields(payload), ensure_ascii=False)


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
                "raw_json": safe_usage_raw_json(payload),
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


def latest_quota_age(account_names=None):
    params = []
    where_sql = ""
    if account_names is not None:
        account_names = sorted(set(account_names))
        if not account_names:
            return None
        placeholders = ",".join("?" for _ in account_names)
        where_sql = f" WHERE email IN ({placeholders})"
        params = list(account_names)
    with db_connect() as conn:
        row = conn.execute(
            f"SELECT MAX(ts_epoch) AS ts, COUNT(DISTINCT email) AS account_count FROM quota_snapshots{where_sql}",
            params,
        ).fetchone()
    # 只有当前账号都已有快照时，才能用最新快照年龄判断是否跳过真实刷新。
    if account_names is not None and row["account_count"] < len(account_names):
        return None
    return None if row["ts"] is None else time.time() - row["ts"]


def auth_files():
    return sorted(glob.glob(os.path.join(AUTH_DIR, "codex-*.json")))


def quota_auth_entries():
    entries = []
    for path in auth_files():
        try:
            with open(path, encoding="utf-8-sig") as f:
                auth = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"quota auth read failed for {path}: {exc}", file=sys.stderr)
            continue
        token = auth.get("access_token")
        if not token:
            continue
        entries.append({"path": path, "email": auth.get("email") or os.path.basename(path), "token": token})
    return entries


def current_quota_account_names():
    return sorted({entry["email"] for entry in quota_auth_entries()})


def refresh_quota(force=False):
    cfg = load_config()
    entries = quota_auth_entries()
    age = latest_quota_age([entry["email"] for entry in entries])
    if not force and age is not None and age < int(cfg["quota_refresh_seconds"]):
        return 0
    now = dt.datetime.now(dt.timezone.utc)
    inserted = 0
    with db_connect() as conn:
        for entry in entries:
            path = entry["path"]
            try:
                req = urllib.request.Request(
                    "https://chatgpt.com/backend-api/wham/usage",
                    headers={
                        "Authorization": "Bearer " + entry["token"],
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
                        entry["email"],
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
                    mark_collector_success()
                    if raw_items:
                        inserted = insert_usage(raw_items)
                        if inserted:
                            print(f"inserted {inserted} usage events", flush=True)
                    now = time.time()
                    if now - last_quota >= int(cfg["quota_refresh_seconds"]):
                        refresh_quota(force=True)
                        last_quota = now
                    time.sleep(cfg["poll_interval_seconds"])
            finally:
                client.close()
        except Exception as exc:
            mark_collector_error(exc)
            print(f"collector error: {exc}", file=sys.stderr, flush=True)
            time.sleep(5)


def start_collector_watchdog(restart_delay_seconds=5, target=collect_forever, stop_event=None):
    stop_event = stop_event or threading.Event()

    def supervise():
        while not stop_event.is_set():
            errors = []

            def run_target():
                try:
                    target()
                except BaseException as exc:
                    errors.append(exc)

            worker = threading.Thread(target=run_target, name="usage-dashboard-collector", daemon=True)
            worker.start()
            while worker.is_alive() and not stop_event.is_set():
                worker.join(1)

            if stop_event.is_set():
                break
            if errors:
                mark_collector_error(errors[0])
                print(f"collector crashed: {errors[0]}", file=sys.stderr, flush=True)
            else:
                err = RuntimeError("collector exited unexpectedly; restarting")
                mark_collector_error(err)
                print(str(err), file=sys.stderr, flush=True)
            if restart_delay_seconds:
                stop_event.wait(restart_delay_seconds)

    watchdog = threading.Thread(target=supervise, name="usage-dashboard-collector-watchdog", daemon=True)
    watchdog.start()
    return stop_event, watchdog


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


LEGACY_RANGES = {"today", "1h", "5h", "24h", "7d"}
PERIOD_TYPES = {"day", "month", "year"}


def epoch_bounds(start_local, end_local):
    return (
        start_local.astimezone(dt.timezone.utc).timestamp(),
        end_local.astimezone(dt.timezone.utc).timestamp(),
    )


def month_bounds(year, month):
    start = dt.datetime(year, month, 1, tzinfo=LOCAL_TZ)
    if month == 12:
        end = dt.datetime(year + 1, 1, 1, tzinfo=LOCAL_TZ)
    else:
        end = dt.datetime(year, month + 1, 1, tzinfo=LOCAL_TZ)
    return start, end


def normalize_summary_period(period_type=None, period_key=None):
    requested_type = period_type if period_type in PERIOD_TYPES else "day"
    now = dt.datetime.now(LOCAL_TZ)
    try:
        if requested_type == "day":
            day = dt.date.fromisoformat(str(period_key or now.date().isoformat()))
            start = dt.datetime(day.year, day.month, day.day, tzinfo=LOCAL_TZ)
            end = start + dt.timedelta(days=1)
            key = day.isoformat()
            label = key
        elif requested_type == "month":
            raw_year, raw_month = str(period_key or now.strftime("%Y-%m")).split("-", 1)
            year, month = int(raw_year), int(raw_month)
            if month < 1 or month > 12:
                raise ValueError("month must be 1-12")
            start, end = month_bounds(year, month)
            key = f"{year:04d}-{month:02d}"
            label = key
        else:
            year = int(period_key or now.year)
            start = dt.datetime(year, 1, 1, tzinfo=LOCAL_TZ)
            end = dt.datetime(year + 1, 1, 1, tzinfo=LOCAL_TZ)
            key = f"{year:04d}"
            label = key
    except (TypeError, ValueError):
        today = now.date()
        start = dt.datetime(today.year, today.month, today.day, tzinfo=LOCAL_TZ)
        end = start + dt.timedelta(days=1)
        requested_type = "day"
        key = today.isoformat()
        label = key

    start_epoch, end_epoch = epoch_bounds(start, end)
    return {
        "type": requested_type,
        "key": key,
        "label": label,
        "start": start,
        "end": end,
        "start_epoch": start_epoch,
        "end_epoch": end_epoch,
    }


def period_bucket_template(period):
    rows = []
    start = period["start"]
    end = period["end"]
    if period["type"] == "day":
        for hour in range(24):
            bucket_time = start + dt.timedelta(hours=hour)
            rows.append({"bucket": bucket_time.strftime("%Y-%m-%d %H:00"), "label": bucket_time.strftime("%H:00")})
    elif period["type"] == "month":
        days = (end.date() - start.date()).days
        for offset in range(days):
            bucket_date = start.date() + dt.timedelta(days=offset)
            rows.append({"bucket": bucket_date.isoformat(), "label": f"{bucket_date.day}日"})
    else:
        for month in range(1, 13):
            rows.append({"bucket": f"{start.year:04d}-{month:02d}", "label": f"{month}月"})
    return rows


def query_period_buckets(conn, period, start, end):
    if period["type"] == "day":
        bucket_expr = "local_hour"
    elif period["type"] == "month":
        bucket_expr = "local_date"
    else:
        bucket_expr = "substr(local_date, 1, 7)"

    rows = conn.execute(
        f"""
        SELECT {bucket_expr} bucket,
               COUNT(*) requests,
               COALESCE(SUM(total_tokens),0) total_tokens,
               COALESCE(SUM(failed),0) failed
        FROM usage_events WHERE ts_epoch >= ? AND ts_epoch < ?
        GROUP BY bucket ORDER BY bucket
        """,
        (start, end),
    ).fetchall()
    by_bucket = {row["bucket"]: dict(row) for row in rows}
    result = []
    for bucket in period_bucket_template(period):
        values = by_bucket.get(
            bucket["bucket"],
            {"requests": 0, "total_tokens": 0, "failed": 0},
        )
        result.append({**bucket, **values, "hour": bucket["label"]})
    return result


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


def query_summary(period_type="today", period_key=None):
    is_legacy_range = period_key is None and period_type in LEGACY_RANGES
    if is_legacy_range:
        start, end = range_bounds(period_type)
        where_sql = "ts_epoch BETWEEN ? AND ?"
        period_payload = {"type": "range", "key": period_type, "label": period_type}
    else:
        period = normalize_summary_period(period_type, period_key)
        start, end = period["start_epoch"], period["end_epoch"]
        where_sql = "ts_epoch >= ? AND ts_epoch < ?"
        period_payload = {k: period[k] for k in ("type", "key", "label")}
    cfg = load_config()
    with db_connect() as conn:
        total = conn.execute(
            f"""
            SELECT COUNT(*) requests,
                   COALESCE(SUM(total_tokens),0) total_tokens,
                   COALESCE(SUM(input_tokens),0) input_tokens,
                   COALESCE(SUM(output_tokens),0) output_tokens,
                   COALESCE(SUM(reasoning_tokens),0) reasoning_tokens,
                   COALESCE(SUM(cached_tokens),0) cached_tokens,
                   COALESCE(SUM(failed),0) failed
            FROM usage_events WHERE {where_sql}
            """,
            (start, end),
        ).fetchone()
        accounts = conn.execute(
            f"""
            SELECT COALESCE(source, auth_index, 'unknown') account,
                   COUNT(*) requests,
                   COALESCE(SUM(total_tokens),0) total_tokens,
                   COALESCE(SUM(input_tokens),0) input_tokens,
                   COALESCE(SUM(output_tokens),0) output_tokens,
                   COALESCE(SUM(reasoning_tokens),0) reasoning_tokens,
                   COALESCE(SUM(failed),0) failed
            FROM usage_events WHERE {where_sql}
            GROUP BY account ORDER BY total_tokens DESC
            """,
            (start, end),
        ).fetchall()
        models = conn.execute(
            f"""
            SELECT COALESCE(model, 'unknown') model,
                   COUNT(*) requests,
                   COALESCE(SUM(total_tokens),0) total_tokens,
                   COALESCE(SUM(failed),0) failed
            FROM usage_events WHERE {where_sql}
            GROUP BY model ORDER BY total_tokens DESC LIMIT 12
            """,
            (start, end),
        ).fetchall()
        if is_legacy_range:
            hours = conn.execute(
                """
                SELECT local_hour hour,
                       local_hour bucket,
                       substr(local_hour, 12, 5) label,
                       COUNT(*) requests,
                       COALESCE(SUM(total_tokens),0) total_tokens,
                       COALESCE(SUM(failed),0) failed
                FROM usage_events WHERE ts_epoch BETWEEN ? AND ?
                GROUP BY local_hour ORDER BY local_hour
                """,
                (start, end),
            ).fetchall()
            hour_rows = [dict(x) for x in hours]
        else:
            hour_rows = query_period_buckets(conn, period, start, end)
        apis = conn.execute(
            f"""
            SELECT COALESCE(NULLIF(api_key_hash, ''), 'unknown') api_key_hash,
                   COUNT(*) requests,
                   COALESCE(SUM(CASE WHEN failed = 0 THEN 1 ELSE 0 END),0) succeeded,
                   COALESCE(SUM(failed),0) failed,
                   COALESCE(SUM(total_tokens),0) total_tokens,
                   COALESCE(SUM(input_tokens),0) input_tokens,
                   COALESCE(SUM(output_tokens),0) output_tokens,
                   COALESCE(SUM(reasoning_tokens),0) reasoning_tokens
            FROM usage_events WHERE {where_sql}
            GROUP BY api_key_hash ORDER BY total_tokens DESC LIMIT 12
            """,
            (start, end),
        ).fetchall()
        api_stats = {row["api_key_hash"]: {k: row[k] for k in row.keys() if k != "api_key_hash"} for row in apis}
    return {
        "range": period_type if is_legacy_range else period_payload["key"],
        "period": period_payload,
        "summary": dict(total),
        "accounts": [dict(x) for x in accounts],
        "models": [dict(x) for x in models],
        "hours": hour_rows,
        "apis": configured_api_summaries(api_stats, cfg["cliproxy_config_path"]),
    }


def latest_quotas(force=False):
    refresh_quota(force=force)
    account_names = current_quota_account_names()
    if not account_names:
        return []
    # 余量表是历史快照表，返回前必须按当前 OAuth 文件过滤掉已移除账号。
    placeholders = ",".join("?" for _ in account_names)
    with db_connect() as conn:
        rows = conn.execute(
            f"""
            SELECT q.* FROM quota_snapshots q
            JOIN (
              SELECT email, MAX(ts_epoch) ts FROM quota_snapshots
              WHERE email IN ({placeholders})
              GROUP BY email
            ) latest ON latest.email = q.email AND latest.ts = q.ts_epoch
            WHERE q.email IN ({placeholders})
            ORDER BY email
            """,
            account_names + account_names,
        ).fetchall()
    return [{key: row[key] for key in row.keys() if key != "raw_json"} for row in rows]


def recent_requests(limit=100, period_type=None, period_key=None):
    cfg = load_config()
    api_labels = configured_api_label_by_hash(cfg["cliproxy_config_path"])
    params = []
    where_sql = ""
    if period_type in PERIOD_TYPES or period_key:
        period = normalize_summary_period(period_type, period_key)
        where_sql = "WHERE ts_epoch >= ? AND ts_epoch < ?"
        params.extend([period["start_epoch"], period["end_epoch"]])
    params.append(limit)
    with db_connect() as conn:
        rows = conn.execute(
            f"""
            SELECT timestamp, source, auth_index, model, endpoint, failed, latency_ms,
                   input_tokens, output_tokens, reasoning_tokens, cached_tokens, total_tokens,
                   request_id, api_key_hash
            FROM usage_events {where_sql} ORDER BY ts_epoch DESC LIMIT ?
            """,
            params,
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
                if "period_type" in qs or "period_key" in qs:
                    json_response(self, query_summary(qs.get("period_type", ["day"])[0], qs.get("period_key", [None])[0]))
                else:
                    json_response(self, query_summary(qs.get("range", ["today"])[0]))
            elif parsed.path == "/api/quota":
                json_response(self, {"quotas": latest_quotas(force=qs.get("force", ["0"])[0] == "1")})
            elif parsed.path == "/api/requests":
                limit = min(500, int(qs.get("limit", ["100"])[0]))
                json_response(
                    self,
                    {
                        "requests": recent_requests(
                            limit,
                            qs.get("period_type", [None])[0],
                            qs.get("period_key", [None])[0],
                        )
                    },
                )
            elif parsed.path == "/api/collector-status":
                json_response(self, collector_status())
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
    :root { color-scheme: light; --bg:#faf9f5; --panel:#f0eee8; --surface:#fffdf9; --layer-1:var(--bg); --layer-2:var(--panel); --layer-3:var(--surface); --surface-soft:#f6f4ee; --hover:#e9e6df; --row-hover:rgba(139, 134, 128, .08); --text:#2d2a26; --muted:#6d6760; --muted-soft:#a29c95; --line:#e3e1db; --line-strong:#d5d2cb; --primary:#8b8680; --primary-hover:#7f7a74; --primary-active:#726d67; --blue:var(--primary); --green:#10b981; --green-bg:#d1fae5; --green-text:#065f46; --green-border:#6ee7b7; --red:#c65746; --red-bg:#c6574624; --red-text:#8a3a30; --red-border:#c6574659; --amber:#e0aa14; --amber-bg:#e0aa1424; --amber-text:#8a6408; --amber-border:#e0aa1459; --shadow:0 1px 2px 0 #00000014; --shadow-lg:0 10px 18px -3px #0000001a; }
    * { box-sizing: border-box; }
    body { margin:0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background:linear-gradient(180deg, var(--bg), var(--surface-soft)); color:var(--text); min-height:100vh; }
    header { display:flex; align-items:center; justify-content:space-between; gap:16px; padding:18px 24px; border-bottom:1px solid var(--line); background:var(--surface); position:sticky; top:0; z-index:2; box-shadow:var(--shadow); }
    h1 { font-family:"Arial Black", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-size:22px; line-height:1; margin:0; font-weight:900; letter-spacing:0; color:var(--text); }
    main { padding:20px 24px 32px; max-width:1440px; margin:0 auto; }
    button, select { border:1px solid var(--line); background:var(--panel); color:var(--text); border-radius:8px; padding:8px 10px; font-size:14px; font-weight:600; transition:background .15s, border-color .15s, color .15s, box-shadow .15s; }
    button, select, .date-cell { cursor:pointer; }
    button:disabled, select:disabled { cursor:not-allowed; }
    button:hover:not(:disabled), select:hover:not(:disabled) { background:var(--hover); border-color:var(--line-strong); }
    button:focus-visible, select:focus-visible { outline:none; border-color:var(--primary); box-shadow:0 0 0 3px rgba(139, 134, 128, .22); }
    button.primary { background:var(--primary); color:#fff; border-color:var(--primary); }
    button.primary:hover:not(:disabled) { background:var(--primary-hover); border-color:var(--primary-hover); }
    .toolbar { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
    .collector-status { min-width:96px; display:inline-flex; align-items:center; justify-content:center; color:var(--red-text); background:var(--red-bg); border:1px solid var(--red-border); border-radius:9999px; padding:5px 10px; font-size:13px; font-weight:700; }
    .collector-status.ok { color:var(--green-text); background:var(--green-bg); border-color:var(--green-border); }
    .toast-stack { position:fixed; top:0; left:50%; width:min(420px, calc(100vw - 32px)); display:grid; gap:10px; padding-top:16px; transform:translateX(-50%); z-index:20; pointer-events:none; }
    .toast { display:flex; align-items:center; gap:10px; min-height:48px; padding:12px 16px; border:1px solid var(--line); border-radius:4px; background:var(--surface); box-shadow:var(--shadow-lg); color:var(--text); font-size:14px; font-weight:500; animation:toast-in .24s ease-out both; }
    .toast.success { color:var(--green-text); background:var(--green-bg); border-color:var(--green-border); }
    .toast.error { color:var(--red-text); background:#fff1f0; border-color:#ffd6d3; }
    .toast.warning { color:var(--amber-text); background:#fff7e6; border-color:#ffe2a8; }
    .toast.removing { animation:toast-out .2s ease-in both; }
    .toast-icon { width:16px; height:16px; display:inline-flex; align-items:center; justify-content:center; border-radius:50%; color:#fff; font-size:11px; line-height:1; font-weight:900; }
    .toast.success .toast-icon { background:var(--green); }
    .toast.error .toast-icon { background:var(--red); }
    .toast.warning .toast-icon { background:var(--amber); }
    @keyframes toast-in { from { opacity:0; transform:translateY(-24px); } to { opacity:1; transform:translateY(0); } }
    @keyframes toast-out { from { opacity:1; transform:translateY(0); } to { opacity:0; transform:translateY(-24px); } }
    .date-filter { position:relative; }
    .date-filter-control { position:relative; }
    .date-filter-trigger { height:38px; min-width:218px; display:flex; align-items:center; justify-content:flex-start; gap:8px; padding:8px 32px 8px 12px; background:var(--surface); border-radius:8px; }
    .date-filter-trigger[aria-expanded="true"] { border-color:var(--primary); box-shadow:0 0 0 3px rgba(139, 134, 128, .22); }
    .date-filter-icon { flex:none; width:14px; height:14px; color:var(--primary); }
    .date-filter-value { max-width:154px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-weight:400; color:var(--text); }
    .date-filter-clear { position:absolute; top:50%; right:8px; width:18px; height:18px; display:flex; align-items:center; justify-content:center; transform:translateY(-50%); border:0; border-radius:50%; padding:0; background:transparent; color:#98a2b3; font-size:18px; line-height:1; opacity:0; }
    .date-filter-clear[hidden] { display:none; }
    .date-filter-trigger.has-value + .date-filter-clear { display:flex; }
    .date-filter-control:hover .date-filter-clear, .date-filter-control:focus-within .date-filter-clear { opacity:1; }
    .date-filter-clear:hover { color:var(--text); background:var(--hover); }
    .date-filter-popover { position:absolute; right:0; left:auto; top:calc(100% + 8px); width:438px; min-height:302px; padding:0; display:grid; grid-template-columns:56px minmax(0, 1fr); background:var(--surface); border:1px solid var(--line); border-radius:8px; box-shadow:var(--shadow-lg); z-index:5; overflow:hidden; }
    .date-filter-popover::before { content:""; position:absolute; top:-6px; right:40px; left:auto; width:10px; height:10px; background:var(--surface); border-left:1px solid var(--line); border-top:1px solid var(--line); transform:rotate(45deg); }
    .date-filter-popover[hidden] { display:none; }
    .date-filter-menu { padding:18px 10px; border-right:1px solid var(--line); display:grid; align-content:start; gap:6px; background:var(--surface); }
    .date-filter-menu button { display:flex; align-items:center; justify-content:center; border:1px solid transparent; border-radius:8px; height:34px; background:transparent; color:var(--text); font-weight:400; line-height:1; letter-spacing:0; }
    .date-filter-menu button.active { background:rgba(139, 134, 128, .14); color:var(--text); box-shadow:none; }
    .date-filter-panel { min-width:0; display:flex; flex-direction:column; background:var(--surface); }
    .date-filter-head { display:grid; grid-template-columns:32px 32px 1fr 32px 32px; align-items:center; gap:2px; padding:11px 14px 8px; }
    .date-filter-head.compact { grid-template-columns:32px 1fr 32px; }
    .date-filter-head strong { text-align:center; font-size:16px; font-weight:500; }
    .date-nav { width:28px; height:28px; padding:0; border:0; background:var(--surface); color:var(--muted); font-size:18px; line-height:1; }
    .date-nav:hover { color:var(--text); background:var(--hover); }
    .date-filter-grid { flex:1; display:grid; padding:0 16px 14px; }
    .date-filter-grid.day { grid-template-columns:repeat(7, minmax(0, 1fr)); grid-auto-rows:36px; }
    .date-filter-grid.month, .date-filter-grid.year { grid-template-columns:repeat(4, minmax(0, 1fr)); grid-auto-rows:64px; padding-top:8px; }
    .date-weekday, .date-cell { min-width:0; display:flex; align-items:center; justify-content:center; font-size:12px; }
    .date-filter-grid.month .date-cell, .date-filter-grid.year .date-cell { font-family:inherit; font-size:12px; font-weight:400; line-height:1; }
    .date-weekday { color:var(--muted); font-weight:500; }
    .date-cell { width:100%; height:32px; margin:auto; border:1px solid transparent; border-radius:0; background:transparent; cursor:pointer; padding:0; color:var(--text); }
    .date-cell:hover { color:var(--text); background:var(--hover); }
    .date-cell.outside { color:var(--muted-soft); background:transparent; }
    .date-cell.today:not(.selected) { color:var(--text); font-weight:400; }
    .date-cell.selected { width:72%; border-radius:4px; color:#fff; border-color:var(--primary); background:var(--primary); box-shadow:none; font-weight:700; }
    .grid { display:grid; gap:14px; }
    .kpis { grid-template-columns: repeat(5, minmax(150px, 1fr)); }
    .two { grid-template-columns: 1.2fr .8fr; margin-top:14px; }
    .panel { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:14px; min-width:0; box-shadow:var(--shadow); }
    .table-panel { background:var(--panel); }
    .chart-stack .hour-panel { grid-column:1 / -1; }
    .panel h2 { margin:0 0 12px; font-size:15px; }
    .panel-head { display:flex; align-items:center; justify-content:space-between; gap:10px; margin:0 0 12px; }
    .panel-head h2 { margin:0; }
    .heading-count { margin-left:4px; color:var(--muted); font-weight:600; }
    .icon-button { width:30px; height:30px; display:inline-flex; align-items:center; justify-content:center; padding:0; border-radius:8px; color:var(--muted); }
    .icon-button:hover { color:var(--text); background:var(--hover); }
    .icon-button:disabled { cursor:wait; opacity:.7; }
    .refresh-icon { width:16px; height:16px; }
    .quota-refreshing .refresh-icon { animation:spin .8s linear infinite; }
    @keyframes spin { to { transform:rotate(360deg); } }
    .kpi .label { color:var(--muted); font-size:12px; }
    .kpi .value { font-size:24px; font-weight:700; margin-top:6px; }
    .kpi .sub { color:var(--muted); font-size:12px; margin-top:4px; }
    table { width:100%; border-collapse:separate; border-spacing:0; font-size:13px; border:1px solid var(--line); border-radius:8px; overflow:hidden; background:var(--surface); }
    thead, tbody, tr, th, td { background:var(--surface); }
    th, td { text-align:left; border-bottom:1px solid var(--line); padding:8px; white-space:nowrap; background:var(--surface); background-clip:padding-box; }
    th { color:var(--muted); font-weight:700; }
    tbody tr:hover td { background:var(--row-hover); }
    tbody tr:last-child td { border-bottom:0; }
    td.num, th.num { text-align:right; }
    .scroll { overflow:auto; max-height:420px; }
    .status { display:inline-flex; align-items:center; gap:6px; color:var(--green); background:transparent; border:0; border-radius:0; padding:0; font-weight:700; }
    .status.bad { color:var(--red); }
    .request-status { display:inline-flex; align-items:center; justify-content:center; border:1px solid var(--line); border-radius:9999px; padding:2px 8px; font-size:12px; font-weight:700; }
    .request-status.success { color:var(--green-text); background:var(--green-bg); border-color:var(--green-border); }
    .request-status.failed { color:var(--red-text); background:var(--red-bg); border-color:var(--red-border); }
    .dot { width:8px; height:8px; border-radius:50%; display:inline-block; background:var(--green); }
    .dot.bad { background:var(--red); }
    .muted { color:var(--muted); }
    canvas { width:100%; height:260px; display:block; }
    .bar { height:9px; background:var(--hover); border-radius:999px; overflow:hidden; min-width:90px; }
    .bar > span { display:block; height:100%; background:var(--green); }
    .bar > span.warn { background:var(--amber); }
    .bar > span.bad { background:var(--red); }
    .quota-percent { font-weight:700; }
    .quota-percent.good { color:var(--green); }
    .quota-percent.warn { color:var(--amber); }
    .quota-percent.bad { color:var(--red); }
    .api-panel { min-height:302px; }
    .api-list { display:grid; gap:10px; max-height:260px; overflow:auto; padding-right:2px; }
    .api-card { position:relative; border:1px solid var(--line); border-radius:8px; padding:12px; background:var(--surface); }
    .api-key { font-weight:700; margin-bottom:8px; }
    .api-metrics { display:flex; gap:6px; flex-wrap:wrap; color:var(--muted); font-size:12px; }
    .api-pill { display:inline-flex; align-items:center; gap:4px; border:1px solid var(--line); border-radius:999px; background:var(--surface-soft); padding:4px 8px; }
    .api-success { color:var(--green); }
    .api-failed { color:var(--red); }
    .api-empty { color:var(--muted); padding:20px 0; }
    @media (max-width: 900px) { .kpis, .two { grid-template-columns:1fr; } header { align-items:flex-start; flex-direction:column; } .date-filter-popover { left:0; right:auto; width:min(438px, calc(100vw - 48px)); grid-template-columns:56px minmax(0, 1fr); } .date-filter-popover::before { left:40px; right:auto; } .date-filter-menu { padding-top:14px; } }
  </style>
</head>
<body>
  <div class="toast-stack" id="toastStack" aria-live="polite" aria-atomic="true"></div>
  <header>
    <h1>CLIProxyAPI 用量统计</h1>
    <div class="toolbar">
      <span class="collector-status" id="collectorStatus">采集状态：异常</span>
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
      <button id="refresh">刷新</button>
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
      <div class="panel hour-panel"><h2 id="periodChartTitle">按小时消耗</h2><canvas id="hourChart" width="900" height="260"></canvas></div>
      <div class="panel api-panel"><h2>API 详细统计<span class="heading-count" id="apiKeyCount">（0）</span></h2><div class="api-list" id="apiDetails"></div></div>
      <div class="panel model-panel"><h2>模型消耗</h2><canvas id="modelChart" width="520" height="260"></canvas></div>
    </section>
    <section class="grid two">
      <div class="panel table-panel"><h2>账号消耗</h2><div class="scroll"><table><thead><tr><th>账号</th><th class="num">请求</th><th class="num">总 Token</th><th class="num">输入</th><th class="num">输出</th><th class="num">推理</th><th class="num">失败</th></tr></thead><tbody id="accounts"></tbody></table></div></div>
      <div class="panel table-panel"><div class="panel-head"><h2>账号余量<span class="heading-count" id="quotaAccountCount">（0）</span></h2><button type="button" class="icon-button quota-refresh" id="quotaRefresh" aria-label="刷新账号余量" title="刷新账号余量"><svg class="refresh-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12a9 9 0 0 1-15.1 6.6M3 12a9 9 0 0 1 15.1-6.6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><path d="M18 3v4h-4M6 21v-4h4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg></button></div><div class="scroll"><table><thead><tr><th>账号</th><th>状态</th><th>5h 剩余</th><th>7d 剩余</th><th>重置时间</th></tr></thead><tbody id="quotas"></tbody></table></div></div>
    </section>
    <section class="panel table-panel" style="margin-top:14px"><h2>最近每次请求/任务</h2><div class="scroll"><table><thead><tr><th>时间</th><th>账号</th><th>API</th><th>模型</th><th class="num">总 Token</th><th class="num">输入</th><th class="num">输出</th><th class="num">推理</th><th class="num">耗时</th><th>状态</th></tr></thead><tbody id="requests"></tbody></table></div></section>
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
function chartValueLabel(value){
  const n = Number(value || 0);
  if (n >= 100000000) return (n / 1000000).toFixed(0) + 'M';
  if (n >= 1000000) return (n / 1000000).toFixed(1).replace(/\.0$/, '') + 'M';
  if (n >= 10000) return (n / 1000).toFixed(0) + 'K';
  if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, '') + 'K';
  return fmt(n);
}
function labelBoxOverlaps(box, boxes){
  return boxes.some(item => box.x1 < item.x2 && box.x2 > item.x1 && box.y1 < item.y2 && box.y2 > item.y1);
}
function drawValueLabel(ctx, text, centerX, barTop, occupiedLabels){
  const width = ctx.measureText(text).width;
  const candidates = [
    Math.max(14, barTop - 7),
    Math.max(14, barTop - 23),
    Math.max(14, barTop - 39),
    Math.max(14, barTop + 13),
  ];
  let y = candidates[0];
  for (const candidate of candidates) {
    const box = {x1:centerX - width / 2 - 3, x2:centerX + width / 2 + 3, y1:candidate - 12, y2:candidate + 3};
    if (!labelBoxOverlaps(box, occupiedLabels)) {
      y = candidate;
      occupiedLabels.push(box);
      break;
    }
  }
  ctx.fillText(text, centerX, y);
}
function fillRoundedRect(ctx, x, y, width, height, radius){
  if (width <= 0 || height <= 0) return;
  const r = Math.max(0, Math.min(radius, width / 2, height / 2));
  if (ctx.roundRect) {
    ctx.beginPath();
    ctx.roundRect(x, y, width, height, r);
    ctx.fill();
    return;
  }
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + width - r, y);
  ctx.quadraticCurveTo(x + width, y, x + width, y + r);
  ctx.lineTo(x + width, y + height - r);
  ctx.quadraticCurveTo(x + width, y + height, x + width - r, y + height);
  ctx.lineTo(x + r, y + height);
  ctx.quadraticCurveTo(x, y + height, x, y + height - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.fill();
}
function esc(s){ return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function showToast(type, message){
  const stack = $('toastStack');
  if (!stack) return;
  const toast = document.createElement('div');
  const icon = type === 'success' ? '✓' : (type === 'warning' ? '!' : '×');
  toast.className = `toast ${type}`;
  toast.setAttribute('role', type === 'error' ? 'alert' : 'status');
  toast.innerHTML = `<span class="toast-icon">${icon}</span><span>${esc(message)}</span>`;
  stack.appendChild(toast);
  const removeToast = () => {
    toast.classList.add('removing');
    setTimeout(() => toast.remove(), 220);
  };
  setTimeout(removeToast, 2600);
}
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
  closeDateFilter();
  load();
}
function pickMonth(month){
  const year = visibleDate.getFullYear();
  const key = monthKey(year, month);
  selectedPeriod = {type:'month', key, label:key};
  visibleDate = new Date(year, month, 1);
  updateDateFilterTrigger();
  renderDateFilter();
  closeDateFilter();
  load();
}
function pickYear(year){
  const key = yearKey(year);
  selectedPeriod = {type:'year', key, label:key};
  visibleDate = new Date(year, visibleDate.getMonth(), 1);
  updateDateFilterTrigger();
  renderDateFilter();
  closeDateFilter();
  load();
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
    load();
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
function activePeriod(){
  return selectedPeriod || {type:'day', key:dateKey(today), label:dateKey(today)};
}
function summaryUrl(){
  const period = activePeriod();
  return `/api/summary?period_type=${encodeURIComponent(period.type)}&period_key=${encodeURIComponent(period.key)}`;
}
function requestsUrl(){
  const period = activePeriod();
  return `/api/requests?limit=120&period_type=${encodeURIComponent(period.type)}&period_key=${encodeURIComponent(period.key)}`;
}
function drawBars(canvas, rows, labelKey, valueKey, color){
  const ctx = canvas.getContext('2d'), w = canvas.width, h = canvas.height;
  ctx.clearRect(0,0,w,h); ctx.font = '12px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif';
  const pad = {l:52,r:18,t:30,b:44}; const max = Math.max(1, ...rows.map(r => Number(r[valueKey] || 0)));
  const bw = Math.max(12, (w-pad.l-pad.r) / Math.max(1, rows.length) * .62);
  const occupiedLabels = [];
  rows.forEach((r,i) => {
    const x = pad.l + i * ((w-pad.l-pad.r) / Math.max(1, rows.length)) + bw*.3;
    const bh = (h-pad.t-pad.b) * Number(r[valueKey] || 0) / max;
    const y = h-pad.b-bh;
    ctx.fillStyle = color; fillRoundedRect(ctx, x, y, bw, bh, 3);
    ctx.fillStyle = '#6d6760'; ctx.textAlign = 'center';
    const label = String(r[labelKey] || r.label || r.bucket || '').slice(-5);
    ctx.fillText(label, x+bw/2, h-18);
    if (Number(r[valueKey] || 0) > 0) { ctx.fillStyle = '#2d2a26'; drawValueLabel(ctx, chartValueLabel(r[valueKey]), x+bw/2, y, occupiedLabels); }
  });
  ctx.strokeStyle = '#e3e1db'; ctx.beginPath(); ctx.moveTo(pad.l,h-pad.b); ctx.lineTo(w-pad.r,h-pad.b); ctx.stroke();
}
function drawDayBars(canvas, rows){
  const ctx = canvas.getContext('2d'), w = canvas.width, h = canvas.height;
  ctx.clearRect(0,0,w,h); ctx.font = '12px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif';
  const pad = {l:44,r:26,t:30,b:44};
  const max = Math.max(1, ...rows.map(r => Number(r.total_tokens || 0)));
  const plotW = w - pad.l - pad.r;
  const weakEnd = 8;
  const weakW = plotW * .2;
  const normalW = plotW - weakW;
  const occupiedLabels = [];
  const drawSegment = (segment, startX, width, muted) => {
    if (!segment.length) return;
    const step = width / segment.length;
    const bw = Math.max(5, step * (muted ? .48 : .62));
    segment.forEach((r, i) => {
      const value = Number(r.total_tokens || 0);
      const x = startX + i * step + (step - bw) / 2;
      const bh = (h - pad.t - pad.b) * value / max;
      const y = h - pad.b - bh;
      ctx.fillStyle = muted ? 'rgba(16, 185, 129, .38)' : '#10b981';
      fillRoundedRect(ctx, x, y, bw, bh, 3);
      const hour = Number(String(r.label || r.hour || '').slice(0, 2));
      const showLabel = !muted || hour % 2 === 0;
      if (showLabel) {
        ctx.fillStyle = muted ? '#a29c95' : '#6d6760';
        ctx.textAlign = 'center';
        ctx.fillText(String(r.label || r.hour || '').slice(0, 5), x + bw / 2, h - 18);
      }
      if (value > 0) {
        ctx.fillStyle = '#2d2a26';
        ctx.textAlign = 'center';
        drawValueLabel(ctx, chartValueLabel(value), x + bw / 2, y, occupiedLabels);
      }
    });
  };
  drawSegment(rows.slice(0, weakEnd), pad.l, weakW, true);
  drawSegment(rows.slice(weakEnd), pad.l + weakW, normalW, false);
  ctx.strokeStyle = '#e3e1db'; ctx.beginPath(); ctx.moveTo(pad.l,h-pad.b); ctx.lineTo(w-pad.r,h-pad.b); ctx.stroke();
  ctx.strokeStyle = '#e9e6df';
  ctx.beginPath(); ctx.moveTo(pad.l + weakW, pad.t); ctx.lineTo(pad.l + weakW, h - pad.b); ctx.stroke();
}
function drawHorizontal(canvas, rows){
  const ctx = canvas.getContext('2d'), w = canvas.width, h = canvas.height;
  ctx.clearRect(0,0,w,h); ctx.font = '12px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif';
  const top = 12, rowH = 30, max = Math.max(1, ...rows.map(r => Number(r.total_tokens || 0)));
  rows.slice(0,8).forEach((r,i) => {
    const y = top + i * rowH; const labelW = 150; const barW = (w-labelW-90) * Number(r.total_tokens || 0) / max;
    ctx.fillStyle = '#2d2a26'; ctx.textAlign='left'; ctx.fillText(String(r.model || 'unknown').slice(0,22), 8, y+18);
    ctx.fillStyle = '#10b981'; fillRoundedRect(ctx, labelW, y+6, barW, 14, 4);
    ctx.fillStyle = '#6d6760'; ctx.fillText(fmt(r.total_tokens), labelW + barW + 8, y+18);
  });
}
function quotaBar(v){
  v = Math.max(0, Math.min(100, Number(v) || 0));
  const cls = v < 30 ? 'bad' : (v < 70 ? 'warn' : 'good');
  return `<div class="bar"><span class="${cls}" style="width:${Math.max(0, Math.min(100, v))}%"></span></div><span class="quota-percent ${cls}">${v}%</span>`;
}
function renderApis(rows){
  rows = rows || [];
  $('apiKeyCount').textContent = `（${rows.length}）`;
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
function renderQuotas(rows){
  rows = rows || [];
  $('quotaAccountCount').textContent = `（${rows.length}）`;
  $('quotas').innerHTML = rows.map(q => `<tr><td>${esc(q.email)}</td><td><span class="status ${q.allowed ? '' : 'bad'}"><span class="dot ${q.allowed ? '' : 'bad'}"></span>${q.allowed ? '可用' : '受限'}</span></td><td>${quotaBar(q.primary_remaining_percent)}</td><td>${quotaBar(q.secondary_remaining_percent)}</td><td><div>${esc(q.primary_reset_at)}</div><div class="muted">${esc(q.secondary_reset_at)}</div></td></tr>`).join('');
}
function renderCollectorStatus(status){
  const el = $('collectorStatus');
  const ok = Boolean(status && status.ok);
  el.textContent = '采集状态：' + (ok ? '正常' : '异常');
  el.classList.toggle('ok', ok);
  el.title = status?.last_error || status?.last_success_at || '';
}
async function refreshQuota(){
  const button = $('quotaRefresh');
  button.disabled = true;
  button.classList.add('quota-refreshing');
  try {
    const quota = await getJSON('/api/quota?force=1');
    renderQuotas(quota.quotas);
    showToast('success', '账号余量刷新成功');
  } catch (error) {
    console.error(error);
    showToast('error', '账号余量刷新失败');
  } finally {
    button.classList.remove('quota-refreshing');
    button.disabled = false;
  }
}
async function refreshDashboard(){
  const button = $('refresh');
  button.disabled = true;
  try {
    await load({forceQuota: true});
    showToast('success', '刷新成功');
  } catch (error) {
    console.error(error);
    showToast('error', '刷新失败');
  } finally {
    button.disabled = false;
  }
}
async function load({forceQuota = false} = {}){
  const quotaRequest = forceQuota ? getJSON('/api/quota?force=1') : getJSON('/api/quota');
  const [summary, quota, reqs, collector] = await Promise.all([
    getJSON(summaryUrl()),
    quotaRequest,
    getJSON(requestsUrl()),
    getJSON('/api/collector-status')
  ]);
  const s = summary.summary;
  $('kReq').textContent = fmt(s.requests); $('kFail').textContent = '失败 ' + fmt(s.failed);
  $('kTok').textContent = fmt(s.total_tokens); $('kIn').textContent = fmt(s.input_tokens);
  $('kOut').textContent = fmt(s.output_tokens); $('kReason').textContent = fmt(s.reasoning_tokens);
  $('accounts').innerHTML = summary.accounts.map(a => `<tr><td>${esc(a.account)}</td><td class="num">${fmt(a.requests)}</td><td class="num">${fmt(a.total_tokens)}</td><td class="num">${fmt(a.input_tokens)}</td><td class="num">${fmt(a.output_tokens)}</td><td class="num">${fmt(a.reasoning_tokens)}</td><td class="num">${fmt(a.failed)}</td></tr>`).join('');
  $('apiKeyCount').textContent = `（${summary.apis.length}）`;
  $('quotaAccountCount').textContent = `（${quota.quotas.length}）`;
  renderQuotas(quota.quotas);
  $('requests').innerHTML = reqs.requests.map(r => `<tr><td>${esc(r.local_time)}</td><td>${esc(r.source || r.auth_index)}</td><td>${esc(r.api_label)}</td><td>${esc(r.model)}</td><td class="num">${fmt(r.total_tokens)}</td><td class="num">${fmt(r.input_tokens)}</td><td class="num">${fmt(r.output_tokens)}</td><td class="num">${fmt(r.reasoning_tokens)}</td><td class="num">${fmt(r.latency_ms)}ms</td><td><span class="request-status ${r.failed ? 'failed' : 'success'}">${r.failed ? '失败' : '成功'}</span></td></tr>`).join('');
  renderApis(summary.apis);
  const chartTitles = {day:'按小时消耗', month:'按日消耗', year:'按月消耗'};
  $('periodChartTitle').textContent = chartTitles[summary.period?.type] || '按周期消耗';
  if (summary.period?.type === 'day') {
    drawDayBars($('hourChart'), summary.hours);
  } else {
    drawBars($('hourChart'), summary.hours, 'label', 'total_tokens', '#10b981');
  }
  drawHorizontal($('modelChart'), summary.models);
  renderCollectorStatus(collector);
}
$('refresh').onclick = () => refreshDashboard();
$('quotaRefresh').onclick = () => refreshQuota();
initDateFilter();
load(); setInterval(() => load(), 30000);
</script>
</body>
</html>
"""


def serve():
    init_db()
    cfg = load_config()
    server = ThreadingHTTPServer((cfg["dashboard_host"], int(cfg["dashboard_port"])), DashboardHandler)
    print(f"dashboard listening on http://{cfg['dashboard_host']}:{cfg['dashboard_port']}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def run():
    init_db()
    cfg = load_config()
    collector_stop_event, _collector_watchdog = start_collector_watchdog()

    server = ThreadingHTTPServer((cfg["dashboard_host"], int(cfg["dashboard_port"])), DashboardHandler)
    print("collector started", flush=True)
    print(f"dashboard listening on http://{cfg['dashboard_host']}:{cfg['dashboard_port']}", flush=True)
    print("press Ctrl+C to stop collector and dashboard", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping collector and dashboard", flush=True)
    finally:
        collector_stop_event.set()
        server.server_close()


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
    sub.add_parser("run")
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
    elif args.cmd == "run":
        run()
    elif args.cmd == "quota":
        init_db()
        print(json.dumps({"quotas": latest_quotas(force=args.force)}, ensure_ascii=False, indent=2))
    elif args.cmd == "report":
        print_report(args.range)


if __name__ == "__main__":
    main()
