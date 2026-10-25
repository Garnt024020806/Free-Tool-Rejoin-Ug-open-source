#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GARNT TOOL REJOIN - Roblox auto-rejoin for UG Phone / Termux.
For your own accounts and devices only."""
import os
import re
import sys
import json
import time
import queue
import shlex
import shutil
import sqlite3
import tempfile
import logging
import threading
import subprocess
from collections import deque
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import TimedRotatingFileHandler
from urllib.parse import urlparse, parse_qs

try:
    import requests
    from rich import box
    from rich.console import Console, Group
    from rich.live import Live
    from rich.markup import escape
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except ImportError as _e:
    print(f"ไม่พบโมดูล Python: {_e.name}\nแก้ไข: pip install -r ~/requirements.txt")
    sys.exit(1)

APP = "GARNT TOOL REJOIN"
ROOT = False  # set in main()
UA = "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36"


# ----------------------------------------------------------------------------
# Paths (absolute, no cwd dependence)
# ----------------------------------------------------------------------------
def home_dir():
    h = os.environ.get("HOME")
    if h and os.path.isdir(h):
        return h
    t = "/data/data/com.termux/files/home"
    return t if os.path.isdir(t) else os.path.expanduser("~")


def pick_base():
    cands = []
    if os.environ.get("REJOIN_DIR"):
        cands.append(os.environ["REJOIN_DIR"])
    cands += ["/sdcard/Download/RejoinTool", os.path.join(home_dir(), "RejoinTool")]
    for c in cands:
        try:
            os.makedirs(c, exist_ok=True)
            t = os.path.join(c, ".w")
            with open(t, "w") as f:
                f.write("1")
            os.remove(t)
            return c
        except Exception:
            continue
    print("ไม่สามารถเขียนไฟล์ได้ทั้งใน /sdcard และ home — ตรวจสอบสิทธิ์ storage")
    sys.exit(1)


BASE = pick_base()
LOGS = os.path.join(BASE, "logs")
SHOTS = os.path.join(BASE, "screenshots")
HBDIR = os.path.join(BASE, "heartbeat")
CFG_PATH = os.path.join(BASE, "config.json")
COOKIE_PATH = os.path.join(BASE, "cookie.txt")
for _d in (LOGS, SHOTS, HBDIR):
    os.makedirs(_d, exist_ok=True)


# ----------------------------------------------------------------------------
# Scrubbing / masking (cookies and webhook tokens never reach logs or output)
# ----------------------------------------------------------------------------
RE_C1 = re.compile(r"_\|WARNING:-DO-NOT-SHARE-THIS[^\s\"';,]*")
RE_C2 = re.compile(r"(\.ROBLOSECURITY[=:\s]+)[^\s\"';,]+", re.I)
RE_HOOK = re.compile(r"(https?://(?:[\w-]+\.)?discord(?:app)?\.com/api/webhooks/\d+/)[\w\-]+", re.I)
RE_LONG = re.compile(r"[A-Za-z0-9_\-|.]{150,}")
RE_HOOK_OK = re.compile(r"^https://(?:[\w-]+\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w\-]+$")


def scrub(s):
    s = str(s)
    s = RE_C1.sub("[COOKIE]", s)
    s = RE_C2.sub(r"\1[COOKIE]", s)
    s = RE_HOOK.sub(r"\1[TOKEN]", s)
    s = RE_LONG.sub("[REDACTED]", s)
    return s


def mask_cookie(c):
    return f"••••••••({len(c)} ตัวอักษร)"


def valid_webhook(u):
    return bool(RE_HOOK_OK.match((u or "").strip()))


class ScrubFilter(logging.Filter):
    def filter(self, record):
        record.msg = scrub(record.getMessage())
        record.args = ()
        return True


log = logging.getLogger("rejoin")


def setup_logging():
    if log.handlers:
        return
    log.setLevel(logging.INFO)
    h = TimedRotatingFileHandler(os.path.join(LOGS, "rejoin.log"), when="midnight",
                                 backupCount=7, encoding="utf-8")
    h.addFilter(ScrubFilter())
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(h)
    log.propagate = False


# ----------------------------------------------------------------------------
# Config (defaults merged on load, corrupt -> backup + reset, atomic save)
# ----------------------------------------------------------------------------
DEFAULTS = {
    "game": {"raw": "", "uri": "", "place_id": "", "kind": ""},
    "detected": [],
    "selected": [],
    "check_interval": 30,
    "launch_delay": 15,
    "join_grace": 90,
    "backoff_base": 10,
    "backoff_max": 300,
    "stable_reset": 300,
    "timed_rejoin_min": 60,
    "auto": {
        "start_on_launch": False, "wake_lock": True, "force_stop": True,
        "foreground_check": False, "screenshot_on_crash": True,
        "cookie_on_start": False, "presence_check": True,
        "timed_rejoin": False, "join_grace": True,
    },
    "heartbeat": {"enabled": False, "port": 8765, "interval": 15, "timeout": 90},
    "performance": {
        "clear_ram": False, "ram_interval_min": 30, "ram_low_mb": 300,
        "kill_all": False, "snapshot_cache": True, "nice": True,
        "presence_cache": True, "panel_refresh": 2,
    },
    "webhook": {
        "enabled": False, "url": "", "start": True, "crash": True,
        "rejoin": True, "stop": True, "status": True, "screenshot": True,
        "periodic_min": 10,
    },
}


def _compat(default, v):
    if isinstance(default, bool):
        return isinstance(v, bool)
    if isinstance(default, (int, float)):
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if isinstance(default, str):
        return isinstance(v, str)
    if isinstance(default, list):
        return isinstance(v, list)
    return True


def deep_merge(base, over):
    out = json.loads(json.dumps(base))
    if not isinstance(over, dict):
        return out
    for k, v in over.items():
        if k in out:
            if isinstance(out[k], dict) and isinstance(v, dict):
                out[k] = deep_merge(out[k], v)
            elif _compat(out[k], v):
                out[k] = v
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, path=None):
        self.path = path or CFG_PATH
        self.data = {}
        self.load()

    def load(self):
        raw = {}
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if not isinstance(raw, dict):
                    raise ValueError("not a dict")
            except Exception:
                bak = f"{self.path}.corrupt-{datetime.now():%Y%m%d_%H%M%S}"
                try:
                    shutil.move(self.path, bak)
                except Exception:
                    pass
                log.warning("config เสียหาย — สำรองไว้ที่ %s และรีเซ็ต", bak)
                raw = {}
        self.data = deep_merge(DEFAULTS, raw)
        self.save()

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def __getitem__(self, k):
        return self.data[k]

    def __setitem__(self, k, v):
        self.data[k] = v


# ----------------------------------------------------------------------------
# Shell helpers
# ----------------------------------------------------------------------------
def detect_root():
    try:
        if os.geteuid() == 0:
            return True
    except AttributeError:
        pass
    try:
        r = subprocess.run(["su", "-c", "id"], capture_output=True, text=True, timeout=10)
        return "uid=0" in r.stdout
    except Exception:
        return False


def sh(cmd, root=False, timeout=20):
    try:
        is0 = hasattr(os, "geteuid") and os.geteuid() == 0
        argv = ["su", "-c", cmd] if (root and ROOT and not is0) else ["sh", "-c", cmd]
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except Exception as e:
        return 1, scrub(e)


def mem_avail_mb():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return None


def scan_packages():
    found = set()
    for cmd in ("pm list packages", "cmd package list packages"):
        rc, out = sh(cmd, root=ROOT)
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("package:com.roblox."):
                found.add(line[len("package:"):])
        if found:
            break
    if not found and ROOT:
        rc, out = sh("ls /data/data", root=True)
        for line in out.split():
            if line.startswith("com.roblox."):
                found.add(line)
    return sorted(found)


def parse_selection(text, n):
    t = (text or "").strip().lower()
    if t in ("all", "a", "ทั้งหมด"):
        return list(range(n))
    out = []
    for part in re.split(r"[,\s]+", t):
        if part.isdigit() and 1 <= int(part) <= n and int(part) - 1 not in out:
            out.append(int(part) - 1)
    return out


def wake_lock(on):
    if on:
        sh("termux-wake-lock")
        if ROOT:
            sh("echo garnt_rejoin > /sys/power/wake_lock", root=True)
    else:
        sh("termux-wake-unlock")
        if ROOT:
            sh("echo garnt_rejoin > /sys/power/wake_unlock", root=True)


def take_screenshot(tag):
    if not ROOT:
        return None
    safe = re.sub(r"[^\w.-]", "_", tag)[:40]
    path = os.path.join(SHOTS, f"{datetime.now():%Y%m%d_%H%M%S}_{safe}.png")
    rc, _ = sh(f"screencap -p {shlex.quote(path)}", root=True, timeout=20)
    if rc != 0 or not os.path.exists(path):
        return None
    sh(f"chmod 666 {shlex.quote(path)}", root=True)
    files = sorted((os.path.join(SHOTS, f) for f in os.listdir(SHOTS) if f.endswith(".png")),
                   key=os.path.getmtime)
    for old in files[:-20]:
        try:
            os.remove(old)
        except OSError:
            pass
    return path


def clear_ram(cfg, why):
    if not ROOT:
        return None
    before = mem_avail_mb()
    sh("sync; echo 3 > /proc/sys/vm/drop_caches", root=True)
    if cfg["performance"]["kill_all"]:
        sh("am kill-all", root=True)
    after = mem_avail_mb()
    log.info("เคลียร์ RAM (%s): %s MB -> %s MB", why, before, after)
    return before, after


# ----------------------------------------------------------------------------
# Game link parsing
# ----------------------------------------------------------------------------
def parse_game_link(text, resolve=False, _depth=0):
    t = (text or "").strip().strip("\"'").strip()
    if not t:
        return None
    if re.fullmatch(r"\d{4,}", t):
        return {"kind": "place", "place_id": t, "uri": f"roblox://placeID={t}"}
    if t.lower().startswith("roblox://"):
        m = re.search(r"place[Ii][Dd]=(\d+)", t)
        return {"kind": "deeplink", "place_id": m.group(1) if m else "", "uri": t}
    if "://" not in t:
        t = "https://" + t
    u = urlparse(t)
    host = u.netloc.lower()
    q = parse_qs(u.query)
    if host.endswith("ro.blox.com") and resolve and _depth < 3:
        try:
            r = requests.get(t, allow_redirects=False, timeout=10, headers={"User-Agent": UA})
            loc = r.headers.get("Location")
            if loc:
                return parse_game_link(loc, resolve, _depth + 1)
        except Exception:
            return None
        return None
    if not host.endswith("roblox.com"):
        return None
    path = u.path
    if "share" in path and "code" in q:
        typ = (q.get("type", ["Server"])[0]) or "Server"
        return {"kind": "share", "place_id": "",
                "uri": f"roblox://navigation/share_links?code={q['code'][0]}&type={typ}"}
    m = re.search(r"/games/(\d+)", path)
    pid = m.group(1) if m else (q.get("placeId", q.get("placeid", [""]))[0])
    if not pid or not pid.isdigit():
        return None
    code = q.get("privateServerLinkCode", q.get("linkCode", [""]))[0]
    if code:
        return {"kind": "private", "place_id": pid, "uri": f"roblox://placeID={pid}&linkCode={code}"}
    return {"kind": "place", "place_id": pid, "uri": f"roblox://placeID={pid}"}


# ----------------------------------------------------------------------------
# Cookies
# ----------------------------------------------------------------------------
def parse_cookie_text(text):
    out = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        s = s.strip("\"'").strip()
        if s.upper().startswith(".ROBLOSECURITY="):
            s = s[len(".ROBLOSECURITY="):]
        s = s.strip("\"'").rstrip(";").strip()
        if s:
            out.append(s)
    return out


def read_cookies():
    if not os.path.exists(COOKIE_PATH):
        try:
            with open(COOKIE_PATH, "w", encoding="utf-8") as f:
                f.write("# วางคุกกี้ 1 บรรทัดต่อ 1 บัญชี (เรียงตามแพ็กเกจที่สแกนได้)\n"
                        "# บรรทัดที่ขึ้นต้นด้วย # และบรรทัดว่างจะถูกข้าม\n")
        except Exception:
            pass
        return []
    try:
        with open(COOKIE_PATH, "r", encoding="utf-8", errors="ignore") as f:
            return parse_cookie_text(f.read())
    except Exception:
        return []


def validate_cookie(c):
    """Return (ok, info). info = dict(id,name) or an error string (never contains the cookie)."""
    try:
        r = requests.get("https://users.roblox.com/v1/users/authenticated",
                         headers={"Cookie": f".ROBLOSECURITY={c}", "User-Agent": UA}, timeout=15)
        if r.status_code == 200:
            j = r.json()
            return True, {"id": j.get("id"), "name": j.get("name")}
        return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, scrub(e)[:80]


def chrome_time(unix):
    return int((unix + 11644473600) * 1_000_000)


def inject_cookie_db(db_path, cookie):
    """Generic Chromium/WebView Cookies DB writer: reads the table schema."""
    con = sqlite3.connect(db_path, timeout=10)
    try:
        cols = con.execute("PRAGMA table_info(cookies)").fetchall()
        if not cols:
            raise RuntimeError("ไม่พบตาราง cookies")
        now = chrome_time(time.time())
        exp = chrome_time(time.time() + 400 * 86400)
        vals = {
            "creation_utc": now, "host_key": ".roblox.com", "top_frame_site_key": "",
            "name": ".ROBLOSECURITY", "value": cookie, "encrypted_value": b"",
            "path": "/", "expires_utc": exp, "is_secure": 1, "is_httponly": 1,
            "last_access_utc": now, "has_expires": 1, "is_persistent": 1,
            "priority": 1, "samesite": -1, "source_scheme": 2, "source_port": 443,
            "is_same_party": 0, "last_update_utc": now, "source_type": 0,
            "has_cross_site_ancestor": 0,
        }
        con.execute("DELETE FROM cookies WHERE name=? AND host_key LIKE ?",
                    (".ROBLOSECURITY", "%roblox.com"))
        names, params = [], []
        for _cid, name, typ, notnull, dflt, pk in cols:
            if name in vals:
                v = vals[name]
            elif notnull and dflt is None and not pk:
                up = (typ or "").upper()
                v = b"" if "BLOB" in up else ("" if ("CHAR" in up or "TEXT" in up) else 0)
            else:
                continue
            names.append('"%s"' % name)
            params.append(v)
        con.execute(f"INSERT INTO cookies ({','.join(names)}) VALUES ({','.join('?' * len(names))})", params)
        con.commit()
        try:
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            con.execute("PRAGMA journal_mode=DELETE")
        except Exception:
            pass
    finally:
        con.close()


def inject_for_package(pkg, cookie):
    """Root only. Returns (ok, message)."""
    if not ROOT:
        return False, "ต้องใช้ root"
    rc, out = sh(f"find /data/data/{pkg} -name Cookies -type f 2>/dev/null | head -1", root=True)
    db = out.strip().splitlines()[0] if out.strip() else ""
    if not db:
        return False, "ไม่พบไฟล์ Cookies (เปิดแอปครั้งแรกก่อน)"
    sh(f"am force-stop {pkg}", root=True)
    time.sleep(1)
    rc, owner = sh(f"stat -c %u:%g /data/data/{pkg}", root=True)
    owner = owner.strip() if rc == 0 and re.fullmatch(r"\d+:\d+", owner.strip()) else ""
    tmp = tempfile.mkdtemp(prefix="rj_", dir=home_dir())
    try:
        dbdir, b = os.path.dirname(db), os.path.basename(db)
        sh(f"cd {shlex.quote(dbdir)} && for f in {b} {b}-wal {b}-journal; do "
           f"[ -f $f ] && cp $f {shlex.quote(tmp)}/; done; chmod 666 {shlex.quote(tmp)}/*", root=True)
        local = os.path.join(tmp, b)
        if not os.path.exists(local):
            return False, "คัดลอกไฟล์ Cookies ไม่สำเร็จ"
        inject_cookie_db(local, cookie)
        q = shlex.quote
        chown = f"chown {owner} {q(db)} && " if owner else ""
        rc, o = sh(f"rm -f {q(db)}-wal {q(db)}-shm {q(db)}-journal; cp {q(local)} {q(db)} && "
                   f"{chown}chmod 660 {q(db)} && (restorecon {q(db)} 2>/dev/null; true)", root=True)
        if rc != 0:
            return False, "เขียนไฟล์กลับไม่สำเร็จ: " + scrub(o)[:60]
        return True, "ฉีดคุกกี้สำเร็จ"
    except Exception as e:
        return False, scrub(e)[:80]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ----------------------------------------------------------------------------
# Heartbeat Lua template
# ----------------------------------------------------------------------------
LUA_TEMPLATE = r'''-- GARNT TOOL REJOIN heartbeat (auto-generated). ID = @ID@
-- WARNING: executors may violate Roblox rules and risk account bans. Use at your own risk.
local ID = "@ID@"
local PORT = @PORT@
local INTERVAL = @INTERVAL@
local BASE = "http://127.0.0.1:" .. tostring(PORT)

local env = (getgenv and getgenv()) or _G
if env.__GARNT_HB_RUNNING then return end
env.__GARNT_HB_RUNNING = true

if not game:IsLoaded() then game.Loaded:Wait() end

local Players = game:GetService("Players")
local RunService = game:GetService("RunService")
local GuiService = game:GetService("GuiService")
local HttpService = game:GetService("HttpService")

local lp = Players.LocalPlayer
while not lp do
	task.wait(0.5)
	lp = Players.LocalPlayer
end

local lastBeat = os.clock()
RunService.Heartbeat:Connect(function()
	lastBeat = os.clock()
end)

local requestFn = (syn and syn.request) or http_request or request or (http and http.request)

local function enc(v)
	return HttpService:UrlEncode(tostring(v or ""))
end

local function httpGet(url)
	if requestFn then
		local ok = pcall(function()
			requestFn({ Url = url, Method = "GET" })
		end)
		if ok then return true end
	end
	local ok2 = pcall(function()
		game:HttpGet(url)
	end)
	return ok2
end

local function errorShowing()
	local ok, res = pcall(function()
		local msg = GuiService:GetErrorMessage()
		if msg and msg ~= "" then return true end
		local gui = game:GetService("CoreGui"):FindFirstChild("RobloxPromptGui")
		if gui then
			local ov = gui:FindFirstChild("promptOverlay")
			if ov and ov:FindFirstChild("ErrorPrompt") then return true end
		end
		return false
	end)
	return ok and res == true
end

task.spawn(function()
	while true do
		if (os.clock() - lastBeat) <= 5 and not errorShowing() then
			local url = BASE .. "/hb?id=" .. enc(ID)
				.. "&user=" .. enc(lp.Name)
				.. "&uid=" .. enc(lp.UserId)
				.. "&place=" .. enc(game.PlaceId)
				.. "&job=" .. enc(game.JobId)
			httpGet(url)
		end
		task.wait(INTERVAL)
	end
end)
'''


def render_lua(pkg, port, interval):
    return (LUA_TEMPLATE.replace("@ID@", pkg).replace("@PORT@", str(int(port)))
            .replace("@INTERVAL@", str(int(interval))))


def generate_lua_files(cfg, pkgs, outdir=None):
    outdir = outdir or HBDIR
    os.makedirs(outdir, exist_ok=True)
    paths = []
    for p in pkgs:
        path = os.path.join(outdir, f"{p}.lua")
        with open(path, "w", encoding="utf-8") as f:
            f.write(render_lua(p, cfg["heartbeat"]["port"], cfg["heartbeat"]["interval"]))
        paths.append(path)
    return paths


# ----------------------------------------------------------------------------
# Discord webhook
# ----------------------------------------------------------------------------
class Webhook:
    def __init__(self, cfg):
        self.cfg = cfg
        self.q = queue.Queue()
        self.t = None

    def active(self, ev=None):
        w = self.cfg["webhook"]
        if not w["enabled"] or not valid_webhook(w["url"]):
            return False
        return True if ev is None else bool(w.get(ev, False))

    def emit(self, ev, title, desc="", shot=None, sync=False, color=0x3498DB):
        if not self.active(ev):
            return
        item = (title, desc, shot, color)
        if sync:
            self.send(*item)
            return
        if self.t is None:
            self.t = threading.Thread(target=self._loop, daemon=True)
            self.t.start()
        self.q.put(item)

    def _loop(self):
        while True:
            item = self.q.get()
            try:
                self.send(*item)
            except Exception as e:
                log.warning("webhook error: %s", scrub(e))

    def send(self, title, desc, shot=None, color=0x3498DB):
        emb = {"title": scrub(title)[:250], "description": scrub(desc)[:3900], "color": color,
               "timestamp": datetime.now(timezone.utc).isoformat()}
        attach = None
        if shot and ROOT and self.cfg["webhook"]["screenshot"] and os.path.exists(shot):
            attach = shot
            emb["image"] = {"url": "attachment://" + os.path.basename(shot)}
        return self._post({"embeds": [emb]}, attach)

    def _post(self, payload, attach=None):
        url = self.cfg["webhook"]["url"].strip()
        for _ in range(4):
            try:
                if attach:
                    with open(attach, "rb") as fh:
                        r = requests.post(url, data={"payload_json": json.dumps(payload)},
                                          files={"file": (os.path.basename(attach), fh, "image/png")},
                                          timeout=25)
                else:
                    r = requests.post(url, json=payload, timeout=15)
            except Exception as e:
                log.warning("webhook ส่งไม่สำเร็จ: %s", scrub(e)[:100])
                return False
            if r.status_code == 429:
                try:
                    wait = float(r.json().get("retry_after", 1))
                except Exception:
                    wait = 2.0
                time.sleep(min(max(wait, 0.5), 30))
                continue
            if 200 <= r.status_code < 300:
                return True
            log.warning("webhook HTTP %s", r.status_code)
            return False
        return False


# ----------------------------------------------------------------------------
# Engine
# ----------------------------------------------------------------------------
def next_backoff(cur, base=10, cap=300):
    return base if cur <= 0 else min(cur * 2, cap)


def fmt_dur(sec):
    sec = int(max(0, sec))
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


class LaunchGate:
    """Guarantees `delay` seconds between ANY two launches."""

    def __init__(self, delay):
        self.delay = delay
        self.last = 0.0
        self.lock = threading.Lock()

    def wait(self, stop):
        with self.lock:
            now = time.time()
            slot = max(now, self.last + self.delay)
            self.last = slot
        w = slot - now
        if w > 0:
            return not stop.wait(w)
        return not stop.is_set()


class Snapshot:
    """One shell call per cycle, shared by all instances (locked TTL cache)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.ts = 0.0
        self.data = (None, "")

    def get(self, ttl, want_fg):
        with self.lock:
            if ttl > 0 and time.time() - self.ts < ttl:
                return self.data
            cmd = "ps -A"
            if want_fg:
                cmd += ("; echo '@@FG@@'; dumpsys activity activities 2>/dev/null | "
                        "grep -E 'topResumedActivity|mResumedActivity' | head -2")
            rc, out = sh(cmd, root=True, timeout=25)
            procs, fg = None, ""
            if rc == 0 and out:
                head, _, tail = out.partition("@@FG@@")
                procs = {ln.split()[-1] for ln in head.splitlines() if ln.split()}
                fg = tail
            self.data = (procs, fg)
            self.ts = time.time()
            return self.data


class Inst:
    def __init__(self, pkg):
        self.pkg = pkg
        self.state = "รอเริ่ม"
        self.detail = ""
        self.fails = 0
        self.rejoins = 0
        self.account = ""
        self.uid = None
        self.cookie = None
        self.last_launch = 0.0
        self.backoff = 0
        self.retry_at = 0.0
        self.ok_since = 0.0
        self.ever_hb = False
        self.last_hb = 0.0
        self.busy = False


class Engine:
    def __init__(self, cfg, pkgs):
        self.cfg = cfg
        self.insts = [Inst(p) for p in pkgs]
        self.by_pkg = {i.pkg: i for i in self.insts}
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.gate = LaunchGate(cfg["launch_delay"])
        self.snap = Snapshot()
        self.wh = Webhook(cfg)
        self.events = deque(maxlen=60)
        self.t0 = time.time()
        self.next_tick = time.time()
        self.pres = {}
        self.pool = ThreadPoolExecutor(max_workers=max(4, len(pkgs) + 2))
        self.hb_server = None
        self.last_ram = time.time()
        self.last_periodic = time.time()

    # ---- helpers
    def event(self, text):
        text = scrub(text)
        self.events.append((datetime.now().strftime("%H:%M:%S"), text))
        log.info(text)

    def status_lines(self):
        return [f"`{i.pkg}` {i.state} | รีจอย {i.rejoins} | พลาด {i.fails} | {i.account or '-'}"
                for i in self.insts]

    # ---- lifecycle
    def start(self):
        c = self.cfg
        if c["performance"]["nice"]:
            try:
                os.nice(10)
            except Exception:
                pass
        if c["auto"]["wake_lock"]:
            wake_lock(True)
        self.load_cookies()
        if ROOT and c["auto"]["cookie_on_start"]:
            for i in self.insts:
                if i.cookie:
                    ok, msg = inject_for_package(i.pkg, i.cookie)
                    self.event(f"{i.pkg}: คุกกี้ — {msg}")
        if c["heartbeat"]["enabled"]:
            self.start_hb_server()
        threading.Thread(target=self._sched, daemon=True).start()
        threading.Thread(target=self._maint, daemon=True).start()
        self.event(f"เริ่มทำงาน {len(self.insts)} อินสแตนซ์ ({'root' if ROOT else 'non-root'})")
        self.wh.emit("start", "▶ เริ่มทำงาน", "\n".join(self.status_lines()), color=0x2ECC71)
        for i in self.insts:
            self.begin_rejoin(i, "เริ่มต้น", 0, initial=True)

    def shutdown(self):
        self.stop.set()
        if self.hb_server:
            try:
                self.hb_server.shutdown()
                self.hb_server.server_close()
            except Exception:
                pass
        if self.cfg["auto"]["wake_lock"]:
            wake_lock(False)
        self.pool.shutdown(wait=False)

    def end(self):
        """Stop and send the stop event synchronously."""
        self.shutdown()
        self.event("หยุดทำงาน")
        self.wh.emit("stop", "⏹ หยุดทำงาน",
                     f"อัปไทม์ {fmt_dur(time.time() - self.t0)}\n" + "\n".join(self.status_lines()),
                     sync=True, color=0xE74C3C)

    # ---- cookies
    def load_cookies(self):
        cookies = read_cookies()
        order = list(self.cfg["detected"])
        cmap = {p: cookies[k] for k, p in enumerate(order) if k < len(cookies)}
        for i in self.insts:
            i.cookie = cmap.get(i.pkg)
            if i.cookie:
                ok, info = validate_cookie(i.cookie)
                if ok:
                    i.uid, i.account = info["id"], info["name"]
                    self.event(f"{i.pkg}: คุกกี้ใช้ได้ — {i.account}")
                else:
                    i.cookie = None
                    self.event(f"{i.pkg}: คุกกี้ใช้ไม่ได้ ({info})")

    # ---- heartbeat
    def start_hb_server(self):
        eng = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _ok(self, body):
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                u = urlparse(self.path)
                if u.path == "/ping":
                    return self._ok(b"pong")
                if u.path == "/hb":
                    eng.on_heartbeat({k: v[0] for k, v in parse_qs(u.query).items()})
                    return self._ok(b"ok")
                self.send_response(404)
                self.end_headers()

        try:
            srv = ThreadingHTTPServer(("127.0.0.1", int(self.cfg["heartbeat"]["port"])), H)
            srv.daemon_threads = True
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            self.hb_server = srv
            self.event(f"heartbeat รับที่ 127.0.0.1:{self.cfg['heartbeat']['port']}")
        except Exception as e:
            self.event(f"เปิดเซิร์ฟเวอร์ heartbeat ไม่ได้: {scrub(e)[:60]}")

    def on_heartbeat(self, q):
        inst = self.by_pkg.get(q.get("id", ""))
        if not inst:
            return
        inst.ever_hb = True
        inst.last_hb = time.time()
        if q.get("user"):
            inst.account = q["user"][:40]
        if str(q.get("uid", "")).isdigit():
            inst.uid = int(q["uid"])

    # ---- checks
    def presence(self, inst):
        if not (inst.uid and inst.cookie):
            return None
        ttl = 60 if self.cfg["performance"]["presence_cache"] else 0
        hit = self.pres.get(inst.uid)
        if hit and ttl and time.time() - hit[0] < ttl:
            return hit[1]
        try:
            r = requests.post("https://presence.roblox.com/v1/presence/users",
                              json={"userIds": [inst.uid]},
                              headers={"Cookie": f".ROBLOSECURITY={inst.cookie}", "User-Agent": UA},
                              timeout=10)
            if r.status_code == 200:
                t = r.json()["userPresences"][0]["userPresenceType"]
                self.pres[inst.uid] = (time.time(), t)
                return t
        except Exception:
            pass
        return None

    def evaluate(self, inst, now):
        c = self.cfg
        grace_on = c["auto"]["join_grace"]
        in_grace = grace_on and now < inst.last_launch + c["join_grace"]
        reason, known, confirmed, hb_ok = None, False, False, False
        if ROOT:
            ttl = min(10, c["check_interval"] / 2) if c["performance"]["snapshot_cache"] else 0
            procs, fg = self.snap.get(ttl, c["auto"]["foreground_check"])
            if procs is not None:
                known = True
                alive = any(p == inst.pkg or p.startswith(inst.pkg + ":") for p in procs)
                if not alive and now >= inst.last_launch + 20:
                    reason = "โปรเซสไม่ทำงาน"
                elif c["auto"]["foreground_check"] and not in_grace and inst.pkg not in fg:
                    reason = "แอปไม่ได้อยู่หน้าจอ"
        hb = c["heartbeat"]
        if reason is None and hb["enabled"] and inst.ever_hb:
            known = True
            if inst.last_hb < inst.last_launch:
                if now > inst.last_launch + c["join_grace"]:
                    reason = "ไม่มี heartbeat หลังรีจอย"
            elif now - inst.last_hb > hb["timeout"]:
                reason = f"heartbeat เงียบเกิน {hb['timeout']}s"
            else:
                hb_ok = confirmed = True
        if reason is None and c["auto"]["presence_check"] and inst.uid and inst.cookie:
            p = self.presence(inst)
            if p is not None:
                known = True
                if p == 2:
                    confirmed = True
                elif not in_grace and not hb_ok:
                    reason = "presence: ไม่อยู่ในเกม"
        if reason:
            return "fail", reason, False
        if not known:
            return "unknown", "", False
        if in_grace and not confirmed:
            return "joining", "", False
        return "ok", "", confirmed

    def check(self, inst):
        if self.stop.is_set():
            return
        with self.lock:
            if inst.busy:
                return
        now = time.time()
        try:
            status, reason, confirmed = self.evaluate(inst, now)
        except Exception as e:
            inst.detail = scrub(e)[:40]
            return
        if status == "fail":
            inst.fails += 1
            inst.ok_since = 0.0
            inst.backoff = next_backoff(inst.backoff, self.cfg["backoff_base"], self.cfg["backoff_max"])
            self.event(f"{inst.pkg}: {reason} → รีจอยใน {inst.backoff}s")
            inst.state, inst.detail = "หลุด", reason
            self.begin_rejoin(inst, reason, inst.backoff)
            return
        if status == "unknown":
            inst.state, inst.detail = "ไม่ทราบ", "ไม่มีสัญญาณตรวจสอบ"
        elif status == "joining":
            inst.state, inst.detail = "กำลังเข้าเกม", "อยู่ในช่วงรอ"
        else:
            inst.state = "ในเกม" if confirmed else "ทำงาน"
            inst.detail = ""
            if inst.ok_since == 0.0:
                inst.ok_since = now
            elif now - inst.ok_since >= self.cfg["stable_reset"] and (inst.backoff or inst.fails):
                inst.backoff, inst.fails = 0, 0
                self.event(f"{inst.pkg}: เสถียรแล้ว รีเซ็ต backoff")
        a = self.cfg["auto"]
        if a["timed_rejoin"] and now - inst.last_launch >= self.cfg["timed_rejoin_min"] * 60:
            self.event(f"{inst.pkg}: รีจอยตามเวลา")
            self.begin_rejoin(inst, "รีจอยตามเวลา", 0, timed=True)

    # ---- launching
    def begin_rejoin(self, inst, reason, delay, initial=False, timed=False):
        with self.lock:
            if inst.busy:
                return
            inst.busy = True
        threading.Thread(target=self._worker, args=(inst, reason, delay, initial, timed), daemon=True).start()

    def _worker(self, inst, reason, delay, initial, timed):
        try:
            if delay:
                inst.state = "รอ backoff"
                inst.retry_at = time.time() + delay
                if self.stop.wait(delay):
                    return
            if not initial and not timed:
                shot = None
                if ROOT and self.cfg["auto"]["screenshot_on_crash"]:
                    shot = take_screenshot(f"crash_{inst.pkg}")
                self.wh.emit("crash", "⚠ หลุด/ค้าง",
                             f"`{inst.pkg}` ({inst.account or '-'})\nสาเหตุ: {reason}", shot=shot, color=0xE67E22)
            inst.state = "รอคิวเปิด"
            if not self.gate.wait(self.stop):
                return
            if not initial and ROOT and self.cfg["auto"]["force_stop"]:
                sh(f"am force-stop {inst.pkg}", root=True)
                time.sleep(2)
            ok, how = self.launch(inst)
            inst.last_launch = time.time()
            inst.ok_since = 0.0
            if not initial:
                inst.rejoins += 1
            inst.state = "กำลังเข้าเกม" if ok else "เปิดไม่สำเร็จ"
            inst.detail = how
            self.event(f"{inst.pkg}: {'เปิดเกม' if ok else 'เปิดไม่สำเร็จ'} ({how})")
            if not initial:
                self.wh.emit("rejoin", "🔁 รีจอยแล้ว", f"`{inst.pkg}` ({inst.account or '-'})\n"
                             f"สาเหตุ: {reason}\nรีจอยรวม: {inst.rejoins}", color=0x3498DB)
        except Exception as e:
            inst.state, inst.detail = "ผิดพลาด", scrub(e)[:40]
            log.warning("worker error %s: %s", inst.pkg, scrub(e))
        finally:
            inst.busy = False

    def launch(self, inst):
        uri = self.cfg["game"]["uri"]
        user = "--user 0 " if ROOT else ""
        cmd = f"am start {user}-a android.intent.action.VIEW -d {shlex.quote(uri)} -p {inst.pkg}"
        rc, out = sh(cmd, root=True, timeout=25)
        if rc == 0 and "Error" not in out and "Exception" not in out:
            return True, "deep link"
        rc2, out2 = sh(f"monkey -p {inst.pkg} -c android.intent.category.LAUNCHER 1", root=True, timeout=25)
        if rc2 == 0 and "monkey aborted" not in out2.lower():
            return True, "monkey (deep link ล้มเหลว)"
        return False, "deep link และ monkey ล้มเหลว"

    # ---- background loops
    def _sched(self):
        iv = max(5, int(self.cfg["check_interval"]))
        while not self.stop.is_set():
            self.next_tick = (int(time.time() // iv) + 1) * iv
            if self.stop.wait(max(0, self.next_tick - time.time())):
                break
            for i in self.insts:
                try:
                    self.pool.submit(self.check, i)
                except RuntimeError:
                    return

    def _maint(self):
        while not self.stop.wait(30):
            c = self.cfg
            now = time.time()
            pf = c["performance"]
            if ROOT and pf["clear_ram"]:
                low = mem_avail_mb()
                due = now - self.last_ram >= pf["ram_interval_min"] * 60
                lowmem = low is not None and low < pf["ram_low_mb"] and now - self.last_ram >= 60
                if due or lowmem:
                    r = clear_ram(c, "ตามเวลา" if due else "RAM ต่ำ")
                    self.last_ram = now
                    if r:
                        self.event(f"เคลียร์ RAM {r[0]}→{r[1]} MB")
            pm = c["webhook"]["periodic_min"]
            if pm and self.wh.active("status") and now - self.last_periodic >= pm * 60:
                self.last_periodic = now
                shot = take_screenshot("status") if (ROOT and c["webhook"]["screenshot"]) else None
                ram = mem_avail_mb()
                self.wh.emit("status", "📊 รายงานสถานะ",
                             "\n".join(self.status_lines()) +
                             f"\nRAM ว่าง: {ram if ram is not None else '?'} MB"
                             f"\nอัปไทม์: {fmt_dur(now - self.t0)}", shot=shot, color=0x9B59B6)

    # ---- UI
    def render(self):
        now = time.time()
        ram = mem_avail_mb()
        head = (f"อัปไทม์ {fmt_dur(now - self.t0)}  |  RAM ว่าง {ram if ram is not None else '?'} MB  |  "
                f"โหมด {'root' if ROOT else 'non-root'}  |  ตรวจทุก {self.cfg['check_interval']}s")
        t = Table(box=box.SIMPLE_HEAVY, expand=True)
        for col in ("#", "แพ็กเกจ", "บัญชี", "สถานะ", "พลาด", "รีจอย", "เช็กถัดไป", "รายละเอียด"):
            t.add_column(col, overflow="fold")
        colors = {"ในเกม": "green", "ทำงาน": "green", "กำลังเข้าเกม": "yellow", "รอ backoff": "yellow",
                  "หลุด": "red", "เปิดไม่สำเร็จ": "red", "ไม่ทราบ": "cyan"}
        for n, i in enumerate(self.insts, 1):
            if i.state == "รอ backoff":
                nxt = f"{max(0, i.retry_at - now):.0f}s (backoff)"
            else:
                nxt = f"{max(0, self.next_tick - now):.0f}s"
            t.add_row(str(n), i.pkg, i.account or "-", Text(i.state, style=colors.get(i.state, "white")),
                      str(i.fails), str(i.rejoins), nxt, i.detail)
        ev = Text("\n".join(f"{a} {b}" for a, b in list(self.events)[-8:]) or "-")
        return Group(Panel(Text(head), title=APP, border_style="cyan"), t,
                     Panel(ev, title="เหตุการณ์ล่าสุด"))


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
LET = {
    "G": ["  ____ ", " / ___|", "| |  _ ", "| |_| |", " \\____|"],
    "A": ["    _    ", "   / \\   ", "  / _ \\  ", " / ___ \\ ", "/_/   \\_\\"],
    "R": [" ____  ", "|  _ \\ ", "| |_) |", "|  _ < ", "|_| \\_\\"],
    "N": [" _   _ ", "| \\ | |", "|  \\| |", "| |\\  |", "|_| \\_|"],
    "T": [" _____ ", "|_   _|", "  | |  ", "  | |  ", "  |_|  "],
    "E": [" _____ ", "| ____|", "|  _|  ", "| |___ ", "|_____|"],
    "J": ["     _ ", "    | |", " _  | |", "| |_| |", " \\___/ "],
    "O": ["  ___  ", " / _ \\ ", "| | | |", "| |_| |", " \\___/ "],
    "I": [" ___ ", "|_ _|", " | | ", " | | ", "|___|"],
}


def art(word):
    return "\n".join("".join(LET[c][r] for c in word) for r in range(5))


console = Console()


def ask(prompt):
    console.print(prompt, end="", markup=False)
    try:
        return input().strip()
    except EOFError:
        return "0"


def ask_int(prompt, lo, hi, cur):
    v = ask(f"{prompt} (ปัจจุบัน {cur}, {lo}-{hi}): ")
    if v.isdigit() and lo <= int(v) <= hi:
        return int(v)
    console.print("[red]ค่าไม่ถูกต้อง — ไม่เปลี่ยนแปลง[/red]")
    return cur


def banner():
    console.print(Text(art("GARNT"), style="bold cyan"))
    console.print(Text("  T  O  O  L", style="bold white"))
    console.print(Text(art("REJOIN"), style="bold magenta"))
    console.print(Text("ควายเผือก rejoin", style="bold yellow"))


def pause():
    ask("\nกด Enter เพื่อกลับ...")


def toggle_str(v):
    return "[green]เปิด[/green]" if v else "[red]ปิด[/red]"


def get_run_packages(cfg):
    if not cfg["detected"]:
        console.print("กำลังสแกนแพ็กเกจ...")
        cfg["detected"] = scan_packages()
        cfg.save()
    if not cfg["detected"]:
        console.print("[yellow]ไม่พบแพ็กเกจ com.roblox.* — พิมพ์ชื่อแพ็กเกจเอง คั่นด้วยคอมมา[/yellow]")
        names = [x.strip() for x in ask("แพ็กเกจ: ").split(",") if x.strip()]
        cfg["detected"] = sorted(set(names))
        cfg.save()
    sel = [p for p in cfg["selected"] if p in cfg["detected"]]
    return sel or list(cfg["detected"])


def start_run(cfg):
    if not cfg["game"]["uri"]:
        console.print("[red]ยังไม่ได้ตั้งลิงก์เกม — ไปที่เมนู 3 ก่อน[/red]")
        return
    pkgs = get_run_packages(cfg)
    if not pkgs:
        console.print("[red]ไม่มีแพ็กเกจให้รัน[/red]")
        return
    eng = Engine(cfg, pkgs)
    eng.start()
    console.print("[green]เริ่มทำงานแล้ว — กด Ctrl+C เพื่อหยุดและกลับเมนู[/green]")
    try:
        with Live(eng.render(), console=console, auto_refresh=False) as live:
            while True:
                time.sleep(max(0.5, float(cfg["performance"]["panel_refresh"])))
                live.update(eng.render(), refresh=True)
    except KeyboardInterrupt:
        pass
    finally:
        console.print("[yellow]กำลังหยุด...[/yellow]")
        eng.end()


def show_status(cfg):
    pkgs = cfg["detected"]
    if not pkgs:
        console.print("[yellow]ยังไม่มีรายการแพ็กเกจ — ใช้เมนู 4 สแกนก่อน[/yellow]")
        return
    cookies = read_cookies()
    procs = None
    if ROOT:
        procs, _ = Snapshot().get(0, False)
    t = Table(title="อินสแตนซ์", box=box.SIMPLE_HEAVY)
    for c in ("#", "แพ็กเกจ", "เลือกรัน", "คุกกี้", "สถานะ"):
        t.add_column(c)
    for n, p in enumerate(pkgs, 1):
        if procs is None:
            st = "ไม่ทราบ"
        else:
            st = "ทำงาน" if any(x == p or x.startswith(p + ":") for x in procs) else "ไม่ทำงาน"
        t.add_row(str(n), p, "✓" if (p in cfg["selected"] or not cfg["selected"]) else "-",
                  "มี" if n <= len(cookies) else "-", st)
    console.print(t)
    console.print(f"เกม: {escape(cfg['game']['uri'] or '(ยังไม่ตั้ง)')}")


def menu_settings(cfg):
    while True:
        console.print(Panel(
            f"1) ลิงก์เกม: {escape(cfg['game']['uri'] or '-')}\n"
            f"2) ช่วงตรวจสอบ: {cfg['check_interval']} วิ\n"
            f"3) หน่วงเวลาเปิดแอป: {cfg['launch_delay']} วิ\n"
            f"4) ช่วงรอเข้าเกม (grace): {cfg['join_grace']} วิ\n"
            f"5) รีจอยตามเวลา: {cfg['timed_rejoin_min']} นาที\n0) กลับ", title="ตั้งค่า"))
        c = ask("เลือก: ")
        if c == "1":
            link = ask("ใส่ Place ID / ลิงก์เกม / ลิงก์เซิร์ฟส่วนตัว / share link: ")
            r = parse_game_link(link, resolve=True)
            if r:
                cfg["game"] = {"raw": link, "uri": r["uri"], "place_id": r["place_id"], "kind": r["kind"]}
                cfg.save()
                console.print(f"[green]ตั้งค่าแล้ว ({r['kind']}): {escape(r['uri'])}[/green]")
            else:
                console.print("[red]อ่านลิงก์ไม่ได้[/red]")
        elif c == "2":
            cfg["check_interval"] = ask_int("ช่วงตรวจสอบ (วิ)", 10, 600, cfg["check_interval"]); cfg.save()
        elif c == "3":
            cfg["launch_delay"] = ask_int("หน่วงเวลาเปิดแอป (วิ)", 0, 300, cfg["launch_delay"]); cfg.save()
        elif c == "4":
            cfg["join_grace"] = ask_int("grace (วิ)", 10, 600, cfg["join_grace"]); cfg.save()
        elif c == "5":
            cfg["timed_rejoin_min"] = ask_int("รีจอยทุก (นาที)", 1, 1440, cfg["timed_rejoin_min"]); cfg.save()
        else:
            return


def menu_scan(cfg):
    pk = scan_packages()
    if not pk:
        console.print("[yellow]ไม่พบแพ็กเกจ com.roblox.* — พิมพ์ชื่อเอง คั่นด้วยคอมมา (เว้นว่าง=ยกเลิก)[/yellow]")
        pk = sorted({x.strip() for x in ask("แพ็กเกจ: ").split(",") if x.strip()})
        if not pk:
            return
    cfg["detected"] = pk
    for n, p in enumerate(pk, 1):
        console.print(f" {n}) {p}")
    sel = parse_selection(ask("เลือกที่จะรัน (เช่น 1,3 หรือ all): "), len(pk))
    cfg["selected"] = [pk[i] for i in sel] if sel else list(pk)
    cfg.save()
    console.print(f"[green]เลือก {len(cfg['selected'])} แพ็กเกจ[/green]")


def menu_cookie(cfg):
    if not cfg["detected"]:
        cfg["detected"] = scan_packages()
        cfg.save()
    cookies = read_cookies()
    console.print(f"ไฟล์: {COOKIE_PATH}\nพบคุกกี้ {len(cookies)} รายการ | แพ็กเกจ {len(cfg['detected'])} รายการ")
    if not cookies:
        console.print("[yellow]วางคุกกี้ลงใน cookie.txt (1 บรรทัด/บัญชี) แล้วเข้าเมนูนี้ใหม่[/yellow]")
        return
    if len(cookies) != len(cfg["detected"]):
        console.print("[yellow]จำนวนคุกกี้ไม่เท่าจำนวนแพ็กเกจ — จับคู่ตามลำดับเท่าที่มี[/yellow]")
    t = Table(box=box.SIMPLE_HEAVY)
    for c in ("#", "แพ็กเกจ", "คุกกี้", "ผลตรวจ"):
        t.add_column(c)
    pairs = []
    for n, c in enumerate(cookies, 1):
        pkg = cfg["detected"][n - 1] if n - 1 < len(cfg["detected"]) else "-"
        ok, info = validate_cookie(c)
        t.add_row(str(n), pkg, mask_cookie(c), f"✓ {info['name']}" if ok else f"✗ {info}")
        if ok and pkg != "-":
            pairs.append((pkg, c))
    console.print(t)
    if not ROOT:
        console.print("[cyan]โหมดไม่ root: ตรวจสอบอย่างเดียว (ฉีดคุกกี้ไม่ได้)[/cyan]")
        return
    if pairs and ask("ฉีดคุกกี้เข้าแอปตอนนี้? (y/N): ").lower() == "y":
        for pkg, c in pairs:
            ok, msg = inject_for_package(pkg, c)
            console.print(f"{pkg}: {'[green]' if ok else '[red]'}{escape(msg)}[/]")


AUTO_ITEMS = [
    ("start_on_launch", "เริ่มทำงานอัตโนมัติเมื่อเปิดโปรแกรม"),
    ("wake_lock", "Wake-lock กันเครื่องหลับ"),
    ("force_stop", "Force-stop ก่อนรีจอย (root)"),
    ("foreground_check", "ตรวจแอปที่อยู่หน้าจอ (root)"),
    ("screenshot_on_crash", "จับภาพเมื่อหลุด (root)"),
    ("cookie_on_start", "ล็อกอินคุกกี้ตอนเริ่ม (root)"),
    ("presence_check", "ตรวจ presence API (ต้องมีคุกกี้)"),
    ("timed_rejoin", "รีจอยตามเวลา"),
    ("join_grace", "ช่วงรอเข้าเกม (join grace)"),
]


def menu_auto(cfg):
    while True:
        lines = [f"{n}) {label}: {toggle_str(cfg['auto'][k])}" for n, (k, label) in enumerate(AUTO_ITEMS, 1)]
        console.print(Panel("\n".join(lines) + "\nh) Heartbeat\np) Performance\n0) กลับ", title="ตั้งค่าอัตโนมัติ"))
        c = ask("เลือก: ").lower()
        if c.isdigit() and 1 <= int(c) <= len(AUTO_ITEMS):
            k = AUTO_ITEMS[int(c) - 1][0]
            cfg["auto"][k] = not cfg["auto"][k]
            cfg.save()
        elif c == "h":
            menu_heartbeat(cfg)
        elif c == "p":
            menu_perf(cfg)
        else:
            return


def menu_heartbeat(cfg):
    h = cfg["heartbeat"]
    while True:
        console.print(Panel(
            f"1) เปิดใช้ heartbeat: {toggle_str(h['enabled'])}\n2) พอร์ต: {h['port']}\n"
            f"3) ส่งทุก (วิ) ในสคริปต์: {h['interval']}\n4) timeout: {h['timeout']} วิ\n"
            f"5) สร้างไฟล์ .lua → {HBDIR}\n0) กลับ\n"
            "[yellow]คำเตือน: executor อาจผิดกฎ Roblox และเสี่ยงโดนแบน[/yellow]", title="Heartbeat"))
        c = ask("เลือก: ")
        if c == "1":
            h["enabled"] = not h["enabled"]
        elif c == "2":
            h["port"] = ask_int("พอร์ต", 1024, 65535, h["port"])
        elif c == "3":
            h["interval"] = ask_int("ส่งทุก (วิ)", 5, 120, h["interval"])
        elif c == "4":
            h["timeout"] = ask_int("timeout (วิ)", 20, 600, h["timeout"])
        elif c == "5":
            pk = cfg["detected"] or scan_packages()
            if not pk:
                console.print("[red]ไม่มีแพ็กเกจ — สแกนก่อน (เมนู 4)[/red]")
            else:
                for p in generate_lua_files(cfg, pk):
                    console.print(f"[green]สร้าง {p}[/green]")
        else:
            cfg.save()
            return
        cfg.save()


def menu_perf(cfg):
    p = cfg["performance"]
    tog = [("clear_ram", "เคลียร์ RAM อัตโนมัติ (root)"), ("kill_all", "am kill-all ตอนเคลียร์ (อาจฆ่าโคลนอื่น)"),
           ("snapshot_cache", "แชร์ snapshot โปรเซส (ลด CPU)"), ("nice", "os.nice(10)"),
           ("presence_cache", "แคช presence 60 วิ")]
    while True:
        lines = [f"{n}) {lb}: {toggle_str(p[k])}" for n, (k, lb) in enumerate(tog, 1)]
        lines += [f"6) เคลียร์ทุก: {p['ram_interval_min']} นาที", f"7) เคลียร์เมื่อ RAM ว่าง < {p['ram_low_mb']} MB",
                  f"8) รีเฟรชแผงสด: {p['panel_refresh']} วิ", "9) เคลียร์ RAM ตอนนี้ (root)", "0) กลับ"]
        console.print(Panel("\n".join(lines), title="Performance"))
        c = ask("เลือก: ")
        if c.isdigit() and 1 <= int(c) <= 5:
            k = tog[int(c) - 1][0]
            p[k] = not p[k]
        elif c == "6":
            p["ram_interval_min"] = ask_int("ทุก (นาที)", 1, 720, p["ram_interval_min"])
        elif c == "7":
            p["ram_low_mb"] = ask_int("RAM ต่ำกว่า (MB)", 100, 4000, p["ram_low_mb"])
        elif c == "8":
            p["panel_refresh"] = ask_int("รีเฟรช (วิ)", 1, 10, p["panel_refresh"])
        elif c == "9":
            r = clear_ram(cfg, "ผู้ใช้สั่ง")
            console.print(f"RAM ว่าง {r[0]} → {r[1]} MB" if r else "[red]ต้องใช้ root[/red]")
        else:
            cfg.save()
            return
        cfg.save()


def menu_webhook(cfg):
    w = cfg["webhook"]
    keys = [("enabled", "เปิดใช้ webhook"), ("start", "เหตุการณ์: เริ่ม"), ("crash", "เหตุการณ์: หลุด"),
            ("rejoin", "เหตุการณ์: รีจอย"), ("stop", "เหตุการณ์: หยุด"), ("status", "รายงานสถานะ"),
            ("screenshot", "แนบภาพหน้าจอ (root)")]
    while True:
        lines = [f"{n}) {lb}: {toggle_str(w[k])}" for n, (k, lb) in enumerate(keys, 1)]
        url = scrub(w["url"]) if w["url"] else "-"
        lines += [f"8) URL: {escape(url)}", "9) ทดสอบส่งข้อความ", f"p) รายงานทุก {w['periodic_min']} นาที (0=ปิด)", "0) กลับ"]
        console.print(Panel("\n".join(lines), title="Discord Webhook"))
        c = ask("เลือก: ").lower()
        if c.isdigit() and 1 <= int(c) <= 7:
            k = keys[int(c) - 1][0]
            w[k] = not w[k]
        elif c == "8":
            u = ask("วาง Webhook URL: ")
            if valid_webhook(u):
                w["url"] = u.strip()
            else:
                console.print("[red]URL ไม่ถูกต้อง[/red]")
        elif c == "9":
            if not valid_webhook(w["url"]):
                console.print("[red]ยังไม่ได้ตั้ง URL ที่ถูกต้อง[/red]")
            else:
                ok = Webhook(cfg).send("ทดสอบ", f"{APP}: ทดสอบ webhook สำเร็จ", None, 0x2ECC71)
                console.print("[green]ส่งสำเร็จ[/green]" if ok else "[red]ส่งไม่สำเร็จ[/red]")
        elif c == "p":
            w["periodic_min"] = ask_int("นาที", 0, 1440, w["periodic_min"])
        else:
            cfg.save()
            return
        cfg.save()


def main_menu(cfg):
    while True:
        banner()
        console.print(f"โหมด: {'[green]root[/green]' if ROOT else '[yellow]non-root[/yellow]'}  |  "
                      f"เกม: {escape(cfg['game']['place_id'] or cfg['game']['uri'] or 'ยังไม่ตั้ง')}  |  "
                      f"แพ็กเกจ: {len(cfg['selected']) or len(cfg['detected'])}\n")
        console.print("1) เริ่ม auto-rejoin\n2) แสดงอินสแตนซ์ / สถานะ\n3) ตั้งค่า (ลิงก์เกม, ช่วงตรวจ, หน่วงเวลา)\n"
                      "4) สแกนแพ็กเกจ (และเลือกที่จะรัน)\n5) ล็อกอินด้วยคุกกี้ (ไม่บังคับ)\n"
                      "6) ตั้งค่าอัตโนมัติ (Heartbeat / Performance)\n7) Discord webhook\n0) ออก")
        c = ask("\nเลือกเมนู: ")
        if c == "1":
            start_run(cfg)
        elif c == "2":
            show_status(cfg); pause()
        elif c == "3":
            menu_settings(cfg)
        elif c == "4":
            menu_scan(cfg); pause()
        elif c == "5":
            menu_cookie(cfg); pause()
        elif c == "6":
            menu_auto(cfg)
        elif c == "7":
            menu_webhook(cfg)
        elif c == "0":
            console.print("ลาก่อน")
            return


def main():
    global ROOT
    setup_logging()
    ROOT = detect_root()
    cfg = Config()
    if cfg["auto"]["start_on_launch"] and cfg["game"]["uri"]:
        start_run(cfg)
    main_menu(cfg)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nออกจากโปรแกรม")