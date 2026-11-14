#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GARNT TOOL REJOIN - Roblox auto-rejoin for UG Phone + Termux.
For your own accounts and devices only."""
import json
import logging
import math
import os
import re
import select
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import TimedRotatingFileHandler
from urllib.parse import parse_qs, urlparse

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    import termios
    import tty
except ImportError:  # not a POSIX terminal
    termios = tty = None

try:
    import requests
    from rich import box
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except ImportError as _e:
    print("ไม่พบไลบรารีที่จำเป็น / Missing required library: %s\nรันคำสั่ง / Run: pip install -r requirements.txt" % _e)
    sys.exit(1)

VERSION = "2.1"
FPS_CAP_MIN, FPS_CAP_MAX = 5, 240
ARCEUS_AUTOEXEC = "/storage/emulated/0/Arceus X/Autoexec"
KNOWN_EXEC_DIRS = ("/storage/emulated/0/Delta/Autoexecute", ARCEUS_AUTOEXEC)
TERMUX = "/data/data/com.termux/files"
LOGO = r"""
 ██████╗  █████╗ ██████╗ ███╗   ██╗████████╗
██╔════╝ ██╔══██╗██╔══██╗████╗  ██║╚══██╔══╝
██║  ███╗███████║██████╔╝██╔██╗ ██║   ██║   
██║   ██║██╔══██║██╔══██╗██║╚██╗██║   ██║   
╚██████╔╝██║  ██║██║  ██║██║ ╚████║   ██║   
 ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝   ╚═╝ 
"""

for _p in ("/system/bin", "/system/xbin"):
    if _p not in os.environ.get("PATH", ""):
        os.environ["PATH"] = os.environ.get("PATH", "") + ":" + _p


# ----------------------------------------------------------------- paths
def pick_base():
    """Prefer /sdcard/Download/RejoinTool; fall back to Termux home. Works from any cwd."""
    cands = [os.path.join(t, "RejoinTool") for t in ("/sdcard/Download", "/storage/emulated/0/Download")]
    for d in cands:
        try:
            for sub in ("logs", "screenshots"):
                os.makedirs(os.path.join(d, sub), exist_ok=True)
            probe = os.path.join(d, ".probe")
            with open(probe, "w") as f:
                f.write("1")
            os.remove(probe)
            return d, True
        except OSError:
            continue
    home = TERMUX + "/home" if os.path.isdir(TERMUX + "/home") else os.path.expanduser("~")
    d = os.path.join(home, "RejoinTool")
    for sub in ("logs", "screenshots"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    return d, False


BASE, STORAGE_OK = pick_base()
CFG_PATH = os.path.join(BASE, "config.json")
COOKIE_PATH = os.path.join(BASE, "cookie.txt")
LOG_DIR = os.path.join(BASE, "logs")
BACKUP_DIR = os.path.join(BASE, "backup")
SHOT_DIR = os.path.join(BASE, "screenshots")
TMP_DIR = TERMUX + "/usr/tmp" if os.path.isdir(TERMUX + "/usr/tmp") else tempfile.gettempdir()
MAX_SHOTS = 20

# ----------------------------------------------------------------- language (th / en)
def _detect_lang():
    """REJOIN_LANG=en|th wins; otherwise the "lang" key of config.json; default th."""
    v = os.environ.get("REJOIN_LANG", "").strip().lower()
    if v not in ("th", "en"):
        try:
            with open(CFG_PATH, encoding="utf-8") as f:
                v = str(json.load(f).get("lang", "th")).strip().lower()
        except (OSError, ValueError, AttributeError):
            v = "th"
    return "en" if v == "en" else "th"


LANG = _detect_lang()

_EN = {
    '********  (%d ตัวอักษร)': '********  (%d chars)',
    ' | เฉลี่ย %.0f%%': ' | avg %.0f%%',
    ' | %d คอร์': ' | %d cores',
    ' (ประมาณจากโหลดเฉลี่ย)': ' (estimated from load average)',
    'ต้องใช้รูท': 'root required',
    'เปิดแอปแล้วแต่ลิงก์เกมไม่ทำงาน (ใช้ launcher แทน)': 'App opened but the game link did not work (using launcher instead)',
    '# GARNT TOOL REJOIN - cookie.txt\n# ใส่คุกกี้ .ROBLOSECURITY ของบัญชีตัวเอง 1 บรรทัด / 1 บัญชี\n# เรียงตามอินสแตนซ์ที่ตรวจพบ (บรรทัดแรก = แพ็กเกจแรก) ห้ามแชร์ไฟล์นี้ให้ใคร\n# บรรทัดว่างและบรรทัดที่ขึ้นต้นด้วย # จะถูกข้าม\n# บรรทัดที่มีแค่ - = อินสแตนซ์นั้นยังไม่มีคุกกี้ (ใช้คงลำดับ)\n': "# GARNT TOOL REJOIN - cookie.txt\n# Put your own account's .ROBLOSECURITY cookie here, 1 line per account\n# In the order instances are detected (first line = first package). Never share this file with anyone\n# Blank lines and lines starting with # are skipped\n# A line with just - = that instance has no cookie yet (keeps the order)\n",
    'คุกกี้หมดอายุ/ไม่ถูกต้อง': 'Cookie expired/invalid',
    'ไม่พบตาราง cookies': 'cookies table not found',
    'ต้องใช้สิทธิ์รูท': 'root permission required',
    'ไม่พบฐานข้อมูลคุกกี้ (เปิดแอปค้างไว้ 1 ครั้งก่อน)': 'Cookie database not found (open the app once and leave it open first)',
    'อ่านเจ้าของโฟลเดอร์แอปไม่ได้': 'Cannot read the app folder owner',
    'คัดลอกฐานข้อมูลไม่สำเร็จ': 'Failed to copy the database',
    'เขียนฐานข้อมูลกลับไม่สำเร็จ': 'Failed to write the database back',
    'ใส่คุกกี้แล้ว': 'Cookie applied',
    'คุกกี้ใช้ไม่ได้: %s': 'Cookie unusable: %s',
    'คุกกี้ในแอปถูกเข้ารหัส อ่านไม่ได้': 'The cookie in the app is encrypted and cannot be read',
    'ไม่พบคุกกี้ (ยังไม่ได้ล็อกอินในแอปนี้)': 'No cookie found (not logged in to this app yet)',
    'คุกกี้ในแอปใช้ไม่ได้: ': 'Cookie in the app is unusable: ',
    'webhook ล้มเหลว: %s': 'webhook failed: %s',
    ' | cap: ไม่ได้ตั้งให้ ID นี้ (%s)': ' | cap: not set for this ID (%s)',
    ' | cap: เอ็กซิคิวเตอร์ไม่มี setfpscap': ' | cap: executor has no setfpscap',
    ' | cap: เรียกแล้ว error': ' | cap: call raised an error',
    ' | cap: ตั้งแล้วแต่ไม่ติด (FPS จริง %s ตัวรันอาจไม่รองรับหรือมีตั้งค่าทับ)': ' | cap: set but not effective (actual FPS %s; the executor may not support it or something overrides it)',
    ' | cap: เรียกแล้วแต่ไม่ติด (อ่านกลับได้ %s)': ' | cap: called but not effective (read back %s)',
    'สร้างแล้ว': 'created',
    'อัปเดตแล้ว': 'updated',
    'พร้อมใช้งาน': 'ready',
    'ข้าม (มีไฟล์ชื่อเดียวกันที่ไม่ใช่ของเครื่องมือ)': "skipped (a file with the same name exists that is not the tool's)",
    'เปิดใช้ heartbeat ให้แล้ว (ปิดได้ที่ เมนู 6 > Heartbeat > 1)': 'Heartbeat enabled for you (turn it off at menu 6 > Heartbeat > 1)',
    'console webhook ล้มเหลว: %s': 'console webhook failed: %s',
    'คำสั่ง: rejoin <เลข|all> | pause <เลข|all> | resume <เลข|all> | status | ram | shot | streamer | help | quit': 'Commands: rejoin <number|all> | pause <number|all> | resume <number|all> | status | ram | shot | streamer | help | quit',
    'แอปปิด/ค้าง (ไม่พบโปรเซส)': 'App closed/frozen (process not found)',
    'แอปไม่ได้อยู่หน้าจอ': 'App is not on screen',
    'ไม่มี heartbeat จากสคริปต์ในเกม': 'No heartbeat from the in-game script',
    'พบหน้าต่าง Disconnected (เช่น Error Code 277)': 'Disconnected window found (e.g. Error Code 277)',
    'ไม่ได้อยู่ในเกม': 'Not in game',
    'รีจอยตามเวลาที่ตั้งไว้': 'Timed rejoin (as configured)',
    'รอคิวเปิด': 'Waiting to launch',
    'กำลังเปิดแอป': 'Launching app',
    'กำลังเข้าเกม': 'Joining game',
    'ปกติ': 'Normal',
    'ไม่ทราบสถานะ': 'Unknown status',
    'รอรีจอย': 'Waiting to rejoin',
    'หยุดแล้ว': 'Stopped',
    'หยุดชั่วคราว': 'Paused',
    'เริ่มทำงาน %d อินสแตนซ์ (โหมด %s)': 'Started %d instances (mode %s)',
    'ไม่รูท': 'non-root',
    '▶️ เริ่มรีจอยอัตโนมัติ': '▶️ Auto rejoin started',
    'เริ่มจัดการ %d อินสแตนซ์': 'Managing %d instances',
    'โหมด': 'Mode',
    'ตรวจทุก': 'Check every',
    'แพ็กเกจ': 'Package',
    'หยุดการทำงาน (รันมา %s)': 'Stopped (ran for %s)',
    '⏹️ หยุดรีจอยอัตโนมัติ': '⏹️ Auto rejoin stopped',
    'รันมาแล้ว ': 'Ran for ',
    'รีจอย %d ครั้ง': '%d rejoins',
    'แรมว่างต่ำ': 'low free RAM',
    'ตามเวลา': 'scheduled',
    'ส่งรายงานสถานะไม่สำเร็จ: %s': 'Failed to send status report: %s',
    '%s: หลุด/ค้าง %d ครั้งใน %d นาที (ผิดปกติ)': '%s: %d drops/hangs in %d minutes (abnormal)',
    '🚨 รีจอยถี่ผิดปกติ': '🚨 Abnormally frequent rejoins',
    'บัญชี': 'Account',
    'หลุดในช่วงเวลา': 'Drops in window',
    '%d ครั้ง / %d นาที': '%d times / %d minutes',
    'แรมว่างต่ำ %d MB (เกณฑ์ %d MB)': 'Free RAM low: %d MB (threshold %d MB)',
    '🚨 แรมว่างต่ำ': '🚨 Low free RAM',
    'แรมว่าง %d MB ต่ำกว่าเกณฑ์ %d MB': 'Free RAM %d MB is below the threshold %d MB',
    'แรมว่าง': 'Free RAM',
    'เกณฑ์': 'Threshold',
    'ล้างแรม (%s): ว่าง %s → %s MB | %s': 'RAM cleared (%s): free %s → %s MB | %s',
    'ล้างแรมไม่สำเร็จ: %s': 'RAM clear failed: %s',
    '%s | รีจอย %d | ล้มเหลว %d%s': '%s | rejoins %d | failures %d%s',
    'RAM ว่าง': 'Free RAM',
    'รันมาแล้ว': 'Uptime',
    '📸 รายงานสถานะ': '📸 Status report',
    'รายงานตามคำสั่ง': 'Report on demand',
    'รายงานประจำทุก %d นาที': 'Periodic report every %d minutes',
    'หยุดชั่วคราวด้วยคำสั่ง (พิมพ์ resume)': 'Paused by command (type resume)',
    '%s: หยุดชั่วคราว': '%s: paused',
    '%s: รีจอยด้วยคำสั่ง': '%s: rejoin by command',
    'ใช้: %s <เลขอินสแตนซ์|all>': 'Usage: %s <instance number|all>',
    'สั่ง %s อินสแตนซ์ %s': 'Sent %s to instance %s',
    'โหมดซ่อนชื่อบัญชี (streamer): ': 'Account-name hiding (streamer): ',
    'เปิด': 'On',
    'ปิด': 'Off',
    'คำสั่ง %s ต้องใช้รูท': 'Command %s requires root',
    'ไม่รู้จักคำสั่ง: %s (พิมพ์ help)': 'Unknown command: %s (type help)',
    'คำสั่ง': 'Command',
    'บันทึกภาพหน้าจอ: %s': 'Screenshot saved: %s',
    'แคปหน้าจอไม่สำเร็จ': 'Screenshot failed',
    'คำสั่ง %s ผิดพลาด: %s': 'Command %s failed: %s',
    '%s: ข้อผิดพลาด %s': '%s: error %s',
    '%s: worker หยุดเพราะ %s': '%s: worker stopped because %s',
    'รอคิวเปิดแอป': 'Waiting in queue to launch app',
    'รอเข้าเกม': 'Waiting to join game',
    '%s: เปิดเกมไม่สำเร็จ: %s': '%s: failed to open game: %s',
    '%s: เปิดเกมแล้ว': '%s: game opened',
    '%s: รีจอยแล้ว (ครั้งที่ %d)': '%s: rejoined (#%d)',
    '🔁 รีจอยแล้ว': '🔁 Rejoined',
    'ครั้งที่': 'Attempt',
    'คุกกี้': 'Cookie',
    'ยังไม่มี heartbeat หลังเปิดเกมใหม่': 'No heartbeat yet after relaunch',
    'heartbeat %ds ก่อน%s%s': 'heartbeat %ds ago%s%s',
    'ไม่มี heartbeat มา %ds%s': 'No heartbeat for %ds%s',
    'อยู่ในเกม': 'In game',
    'แอปทำงานอยู่ (ไม่มีข้อมูลสถานะเกม)': 'App is running (no game status data)',
    'ไม่ทราบสถานะ (ไม่รูท/ไม่มีคุกกี้)': 'Unknown status (non-root / no cookie)',
    '%s: เสถียรครบ %ds รีเซ็ต backoff': '%s: stable for %ds, backoff reset',
    '%s: %s (ครั้งที่ %d) รีจอยใน %ds': '%s: %s (#%d) rejoining in %ds',
    '⚠️ หลุด/ค้าง': '⚠️ Dropped/frozen',
    'สาเหตุ': 'Reason',
    'ล้มเหลวติดกัน': 'Consecutive failures',
    'รีจอยใน': 'Rejoin in',
    'โหมด: ': 'Mode: ',
    '  |  ตรวจทุก %ds': '  |  check every %ds',
    '  |  หน่วงเปิด %ss': '  |  launch delay %ss',
    '  |  รันมา ': '  |  uptime ',
    '  |  RAM ว่าง %d MB': '  |  free RAM %d MB',
    '  |  โหลด %.1f': '  |  load %.1f',
    '\nพิมพ์คำสั่งแล้วกด Enter (help = รายการคำสั่ง) | quit หรือ Ctrl+C = หยุดและกลับเมนู': '\nType a command and press Enter (help = command list) | quit or Ctrl+C = stop and return to menu',
    'สถานะ': 'State',
    'เล่นมา': 'Playing',
    'ล้มเหลว': 'Failures',
    'รีจอย': 'Rejoins',
    'ถัดไป': 'Next',
    'รายละเอียด': 'Details',
    'คอนโซล': 'Console',
    'เหตุการณ์ล่าสุด (ใหม่สุดอยู่บน)': 'Latest events (newest on top)',
    'ใช่': 'Yes',
    'กด Enter เพื่อกลับเมนู': 'Press Enter to return to the menu',
    '      T O O L   R E J O I N   •   ควายเผือก rejoin   •   v': '      T O O L   R E J O I N   •   ควายเผือก rejoin   •   v',
    '   อินสแตนซ์: ': '   Instances: ',
    '   ลิงก์เกม: ': '   Game link: ',
    'ตั้งแล้ว': 'set',
    'ยังไม่ตั้ง': 'not set',
    'โฟลเดอร์: ': 'Folder: ',
    '🆕 มีเวอร์ชันใหม่ v%s (ตอนนี้ v%s) → เลือกเมนู 11 เพื่ออัปเดต': '🆕 New version v%s available (current v%s) → choose menu 11 to update',
    'ยังไม่ได้ตั้งลิงก์เกม (หรือลิงก์ไม่ถูกต้อง) ไปที่เมนู 3 ก่อน': 'Game link not set (or invalid). Go to menu 3 first',
    'ไม่พบแพ็กเกจ Roblox ไปที่เมนู 4 เพื่อสแกนก่อน': 'No Roblox packages found. Go to menu 4 to scan first',
    'กำลังดึงคุกกี้จากแอป %d อินสแตนซ์ที่ยังไม่มีคุกกี้...': 'Pulling cookies from %d apps that have no cookie yet...',
    'ได้คุกกี้ของ ': 'Got cookies for ',
    'ข้ามการล็อกอินด้วยคุกกี้: ต้องใช้รูท': 'Skipping cookie login: root required',
    'รับ heartbeat ที่ 127.0.0.1:%d (ทดสอบ: curl http://127.0.0.1:%d/ping)': 'Receiving heartbeat at 127.0.0.1:%d (test: curl http://127.0.0.1:%d/ping)',
    'เปิดพอร์ต heartbeat ไม่ได้ (%s) ข้ามการใช้ heartbeat': 'Cannot open the heartbeat port (%s); skipping heartbeat',
    '\nกำลังหยุด...': '\nStopping...',
    'หยุดการทำงานแล้ว': 'Stopped',
    'อินสแตนซ์ที่ตรวจพบ': 'Detected instances',
    'เลือกรัน': 'Selected',
    'ทำงานอยู่': 'Running',
    'อยู่หน้าจอ': 'On screen',
    'ไม่': 'No',
    'ไม่ทราบ': 'Unknown',
    'มี': 'Has',
    'โหมด: %s | ตรวจทุก %ss | หน่วงเปิด %ss | คุกกี้ในไฟล์: %d บรรทัด | RAM ว่าง %s MB | โหลด %s%s': 'Mode: %s | check every %ss | launch delay %ss | cookies in file: %d lines | free RAM %s MB | load %s%s',
    ' | โหมดไม่รูทตรวจโปรเซส/หน้าจอไม่ได้': ' | non-root mode cannot check process/screen',
    'แผงสดรีเฟรชอัตโนมัติ — กด Enter หรือ Ctrl+C เพื่อกลับเมนู': 'Live panel refreshes automatically — press Enter or Ctrl+C to return to the menu',
    'ยังไม่พบแพ็กเกจ ลองเมนู 4 (สแกนแพ็กเกจ)': 'No packages found yet. Try menu 4 (scan packages)',
    'ตั้งค่า': 'Settings',
    'รายการ': 'Item',
    'ค่าปัจจุบัน': 'Current value',
    'ลิงก์เกม / Place ID': 'Game link / Place ID',
    '(ยังไม่ตั้ง)': '(not set)',
    'ช่วงเวลาตรวจสอบ (วินาที)': 'Check interval (seconds)',
    'หน่วงเวลาเปิดแต่ละอินสแตนซ์ (วินาที)': 'Delay between launching each instance (seconds)',
    'ซ่อนชื่อบัญชี (streamer mode: โชว์ 4 ตัวแรก ที่เหลือเป็น #)': 'Hide account names (streamer mode: show first 4 characters, the rest as #)',
    'ลิงก์ที่ใช้เปิดจริง: ': 'Link actually used to open: ',
    'เลือกหมายเลข (0=กลับ)': 'Choose a number (0=back)',
    'วางลิงก์เกม (roblox.com/games/..., ลิงก์ private server, ลิงก์แชร์ หรือ Place ID):': 'Paste the game link (roblox.com/games/..., private server link, share link or Place ID):',
    'รูปแบบลิงก์ไม่ถูกต้อง': 'Invalid link format',
    'ค่าใหม่ (วินาที, ต่ำสุด %d):': 'New value (seconds, minimum %d):',
    'ต้องเป็นตัวเลข >= %d': 'Must be a number >= %d',
    'กำลังสแกนแพ็กเกจ %s ...': 'Scanning packages %s ...',
    'ไม่พบแพ็กเกจ (ถ้าอินสแตนซ์ใช้ชื่ออื่น พิมพ์ชื่อแพ็กเกจเองได้ คั่นด้วยคอมมา)': 'No packages found (if your instances use other names, type the package names yourself, comma-separated)',
    'แพ็กเกจ (Enter=ข้าม):': 'Packages (Enter=skip):',
    'แพ็กเกจที่พบ': 'Packages found',
    'เลือกที่จะรัน เช่น 1,3 | all=ทั้งหมด | Enter=คงเดิม:': 'Choose which to run, e.g. 1,3 | all=everything | Enter=keep as is:',
    'บันทึกแล้ว': 'Saved',
    'สร้างไฟล์ตัวอย่างแล้ว: ': 'Sample file created: ',
    'ยังไม่มีคุกกี้ใน cookie.txt  (1 บรรทัด / 1 บัญชี เรียงตามอินสแตนซ์)\nไฟล์: ': 'No cookies in cookie.txt yet  (1 line per account, in instance order)\nFile: ',
    'คุกกี้มากกว่าอินสแตนซ์ (%d > %d) ส่วนเกินจะถูกข้าม': 'More cookies than instances (%d > %d); the extras will be skipped',
    'คุกกี้ที่จับคู่กับอินสแตนซ์ (ตามลำดับ)': 'Cookies matched to instances (in order)',
    'บรรทัด': 'Line',
    'ผลตรวจ': 'Check result',
    'ใช้ได้: ': 'Valid: ',
    'ใช้ไม่ได้: ': 'Invalid: ',
    'โหมดไม่รูท: ตรวจคุกกี้ได้อย่างเดียว การใส่คุกกี้ลงแอปต้องใช้รูท': 'Non-root mode: can only check cookies; putting cookies into the app requires root',
    'ใส่คุกกี้ที่ใช้ได้ %d ตัวลงแอป (แอปจะถูก force-stop)?': 'Put %d valid cookies into the apps (apps will be force-stopped)?',
    'คุกกี้หมดอายุ': 'Cookie expired',
    'ส่งถี่เกินไป รอสักพัก': 'Too many requests, wait a moment',
    '(เนื้อหาตอบกลับว่าง)': '(empty response body)',
    'ส่งโทเคนซ้ำแล้วยังไม่ผ่าน': 'Token resent but still rejected',
    'เซิร์ฟเวอร์ไม่ส่งโทเคนมา': 'The server did not send a token',
    'บล็อก': 'Block',
    'เลิกบล็อก': 'Unblock',
    'ส่งคำขอเป็นเพื่อน': 'Send friend request',
    'บล็อกอยู่แล้ว (ตรวจจากรายการบล็อก)': 'Already blocked (from the block list)',
    'ไม่ได้บล็อกอยู่ ไม่ต้องทำอะไร': 'Not blocked, nothing to do',
    ' [ตรวจรายการบล็อกแล้ว: ': ' [block list checked: ',
    'บล็อกอยู่': 'blocked',
    'ไม่ได้บล็อก': 'not blocked',
    '%s: คุกกี้ใช้ไม่ได้ (%s)': '%s: cookie unusable (%s)',
    'เลือกบัญชี (เช่น 1,3 หรือ all, Enter=all):': 'Choose accounts (e.g. 1,3 or all, Enter=all):',
    'เป็นบัญชีเดียวกับเป้าหมาย ข้าม': 'Same account as the target, skipped',
    'ผล': 'Result',
    'เลือกเป้าหมายจาก: 1=บัญชีในคุกกี้, 2=พิมพ์ชื่อ/ID Roblox (Enter=1):': 'Choose the target from: 1=account in cookies, 2=type a Roblox name/ID (Enter=1):',
    'เลือกเลขบัญชีเป้าหมาย (ตามตารางด้านบน):': 'Choose the target account number (from the table above):',
    'ชื่อผู้ใช้หรือ User ID เป้าหมาย:': 'Target username or User ID:',
    'ต้องมีบัญชีที่ใช้ได้อย่างน้อย 2 บัญชี': 'You need at least 2 usable accounts',
    'บล็อกกันเอง %d บัญชี (%d การบล็อก) ใช้เวลาประมาณ %d นาที?': 'Mutually block %d accounts (%d blocks), takes about %d minutes?',
    'กำลังอ่านรายการบล็อกของแต่ละบัญชี...': "Reading each account's block list...",
    'รอบ %d/%d': 'Round %d/%d',
    '%s: ส่งถี่เกิน (429) รอ 60 วินาทีแล้วลองใหม่': '%s: rate limited (429), waiting 60 seconds then retrying',
    'หยุดแล้ว (Ctrl+C) สรุปเท่าที่ทำไปแล้ว': 'Stopped (Ctrl+C); summary of what was done so far',
    'บล็อกใหม่ %d | บล็อกอยู่แล้ว %d | ล้ม %d': 'Newly blocked %d | already blocked %d | failed %d',
    '...และอีก %d รายการ': '...and %d more',
    'ไม่มีคู่ไหนบล็อกกันอยู่ ไม่ต้องทำอะไร': 'No pair is blocking each other, nothing to do',
    'ปลดบล็อกกันเอง %d คู่ (ทิศทาง) ใช้เวลาประมาณ %d นาที?': 'Unblock %d pairs (directions) among your accounts, takes about %d minutes?',
    'ปลดบล็อก %d | ไม่ได้บล็อกอยู่ %d | ล้ม %d': 'Unblocked %d | not blocked %d | failed %d',
    'เปิด Roblox %d อินสแตนซ์ ห่างกันตัวละ %d วินาที?': 'Open Roblox on %d instances, %d seconds apart?',
    'รอ %d วินาทีก่อนเปิดตัวถัดไป...': 'Waiting %d seconds before opening the next one...',
    'หยุดแล้ว (Ctrl+C)': 'Stopped (Ctrl+C)',
    'เปิดสำเร็จ %d/%d อินสแตนซ์': 'Opened %d/%d instances successfully',
    'ปิดแอปอื่นไม่ได้ถ้าไม่มีรูท (force-stop ต้องใช้สิทธิ์รูท)': 'Cannot close other apps without root (force-stop needs root)',
    'ปิด Roblox ทั้งหมด %d อินสแตนซ์ (ทุกตัวที่สแกนเจอ ไม่ใช่เฉพาะที่เลือก)?': 'Close ALL %d Roblox instances (every detected one, not just the selected)?',
    'ปิดแล้ว': 'Closed',
    'ปิดสำเร็จ %d/%d อินสแตนซ์': 'Closed %d/%d instances successfully',
    'เปิด / ปิด Roblox ทั้งหมด': 'Open / close all Roblox',
    'ค่า': 'Value',
    'เปิด Roblox ทุกอินสแตนซ์ที่เลือกตอนนี้ (ทีละตัว ไม่ force-stop)': 'Open every selected Roblox instance now (one by one, no force-stop)',
    'หน่วงระหว่างตัว (วินาที, 0-600)': 'Delay between instances (seconds, 0-600)',
    'ปิด Roblox ทั้งหมด (force-stop ทุกอินสแตนซ์ที่สแกนเจอ ต้องใช้รูท)': 'Close all Roblox (force-stop every detected instance; requires root)',
    'ค่าหน่วงใหม่ (0-600):': 'New delay (0-600):',
    'ค่าต้องอยู่ระหว่าง 0-600': 'Value must be between 0-600',
    'ยังไม่มีคุกกี้ใน cookie.txt (เมนู 5)': 'No cookies in cookie.txt yet (menu 5)',
    'กำลังตรวจบัญชีจากคุกกี้...': 'Checking accounts from cookies...',
    'บล็อก / เพื่อน (ด้วยบัญชีจากคุกกี้)': 'Block / friends (using accounts from cookies)',
    'บล็อกผู้ใช้': 'Block a user',
    'เลิกบล็อกผู้ใช้': 'Unblock a user',
    'เพิ่มเพื่อน (ส่งคำขอไปยังผู้ใช้)': 'Add friend (send a request to a user)',
    'เพิ่มเพื่อนระหว่างบัญชีของเราเอง (ส่งคำขอ + กดรับให้อัตโนมัติ)': 'Add friends among my own accounts (send request + accept automatically)',
    'บล็อกกันเองทั้งหมด (ทุกบัญชีในคุกกี้บล็อกกันเอง ไม่ต้องเลือกเป้าหมาย)': 'Block everyone among my accounts (every account in cookies blocks the others, no target selection)',
    'ปลดบล็อกกันเองทั้งหมด (ทุกบัญชีในคุกกี้ปลดบล็อกกันเอง เฉพาะคู่ที่บล็อกกันอยู่)': 'Unblock everyone among my accounts (every account in cookies unblocks the others, only pairs that are blocking each other)',
    'ไม่พบเป้าหมายนี้': 'Target not found',
    '%s %s (%d) ด้วย %d บัญชี?': '%s %s (%d) with %d accounts?',
    'ต้องเลือกอย่างน้อย 2 บัญชี': 'Choose at least 2 accounts',
    'เพิ่มเพื่อนระหว่าง %d บัญชี?': 'Add friends among %d accounts?',
    'การดึงคุกกี้จากแอปต้องใช้สิทธิ์รูท': 'Pulling cookies from the app requires root',
    'กำลังอ่านคุกกี้จากแอป %d อินสแตนซ์ แล้วตรวจกับ Roblox API...': 'Reading cookies from %d apps, then checking with the Roblox API...',
    'คุกกี้ที่ดึงจากแอป Roblox': 'Cookies pulled from the Roblox apps',
    'ดึงได้ ใช้ได้: ': 'Pulled, valid: ',
    'บันทึก %d ตัวลง cookie.txt (แทนที่บรรทัดของอินสแตนซ์นั้น)?': "Save %d to cookie.txt (replacing that instance's line)?",
    'บันทึกแล้ว %d บรรทัด → ': 'Saved %d lines → ',
    'ตอนรีจอยจะใช้คุกกี้เหล่านี้เช็คสถานะในเกมผ่าน API (เปิด "ตรวจสถานะในเกมผ่าน Roblox API" ที่เมนู 6)': 'When rejoining, these cookies are used to check in-game status through the API (turn on "Check in-game status via Roblox API" in menu 6)',
    'ตรวจ / ใส่คุกกี้จาก cookie.txt ลงแอป (ล็อกอินด้วยคุกกี้)': 'Check / put cookies from cookie.txt into the apps (log in with cookies)',
    'ดึงคุกกี้จากแอป Roblox มาเก็บใน cookie.txt (ไว้เช็คสถานะผ่าน API) — รูท': 'Pull cookies from the Roblox apps into cookie.txt (to check status via the API) — root',
    'เริ่มรีจอยทันทีเมื่อเปิดเครื่องมือ': 'Start rejoining as soon as the tool opens',
    'เปิด Wake-lock ขณะรัน': 'Hold a wake-lock while running',
    'Force-stop ก่อนเปิดเกมใหม่': 'Force-stop before launching the game again',
    'ตรวจว่าแอปอยู่หน้าจอ': 'Check the app is on screen',
    'แคปหน้าจอเมื่อหลุด/ค้าง': 'Screenshot when dropped/frozen',
    'ล็อกอินด้วยคุกกี้อัตโนมัติก่อนเริ่ม': 'Log in with cookies automatically before starting',
    'ตรวจสถานะในเกมผ่าน Roblox API (ต้องมีคุกกี้)': 'Check in-game status via the Roblox API (needs cookies)',
    'ดึงคุกกี้จากแอปมาเก็บอัตโนมัติก่อนเริ่ม (เฉพาะอินสแตนซ์ที่ยังไม่มีคุกกี้)': 'Pull cookies from the apps automatically before starting (only instances without a cookie)',
    'ตั้งค่าอัตโนมัติ': 'Auto settings',
    'หมายเหตุ': 'Notes',
    'เฉพาะรูท': 'root only',
    'รีจอยตามเวลา (นาที, 0=ปิด)': 'Timed rejoin (minutes, 0=off)',
    'เวลารอเข้าเกมหลังเปิดแอป (วินาที)': 'Time to wait for joining the game after opening the app (seconds)',
    'Heartbeat (สคริปต์ในเกม) ...': 'Heartbeat (in-game script) ...',
    'เมนูย่อย': 'Submenu',
    'ประสิทธิภาพ: ล้างแรม / ลดการใช้ CPU ...': 'Performance: clear RAM / reduce CPU use ...',
    'เลือกหมายเลขเพื่อสลับ/แก้ไข (0=กลับ)': 'Choose a number to toggle/edit (0=back)',
    'ค่าใหม่ (ตัวเลข):': 'New value (number):',
    'ไม่ตั้ง': 'not set',
    '%d บัญชี, ค่าเริ่มต้น %s': '%d accounts, default %s',
    'ไม่แตะ': 'untouched',
    'ค่าต้องอยู่ระหว่าง %d-%d': 'Value must be between %d-%d',
    'กำลังดึง Roblox ID จากคุกกี้...': 'Getting Roblox IDs from cookies...',
    'สคริปต์ใหม่จะมีผลตอนเข้าเกมครั้งถัดไป (รีจอยหรือเปิดเกมใหม่) ต้องมี setfpscap ในเอ็กซิคิวเตอร์': 'New scripts take effect the next time you enter the game (rejoin or reopen the game). The executor must have setfpscap',
    'FPS cap ต่อบัญชี (จับคู่ด้วย Roblox ID)': 'FPS cap per account (matched by Roblox ID)',
    'ตั้งเท่ากันทุกบัญชีในตาราง': 'Set the same value for every account in the table',
    'ค่าเริ่มต้นของบัญชีที่ไม่อยู่ในตาราง (0=ไม่แตะ)': 'Default for accounts not in the table (0=untouched)',
    'ล้างค่าทั้งหมด': 'Clear all values',
    'FPS cap ของ %s (%d-%d, Enter=ล้าง):': 'FPS cap for %s (%d-%d, Enter=clear):',
    'FPS cap สำหรับทุกบัญชี (%d-%d, Enter=ล้าง):': 'FPS cap for all accounts (%d-%d, Enter=clear):',
    'ค่าเริ่มต้น (%d-%d, 0 หรือ Enter=ไม่แตะ):': 'Default (%d-%d, 0 or Enter=untouched):',
    'ล้าง FPS cap ทั้งหมด?': 'Clear all FPS caps?',
    'Heartbeat (สคริปต์ในเกม ส่งสัญญาณมาที่เครื่องมือ)': 'Heartbeat (in-game script sends a signal to the tool)',
    'เปิดใช้ heartbeat (เปิดเมื่อติดตั้งสคริปต์ในทุกโคลนแล้ว)': 'Enable heartbeat (turn on after installing the script in every clone)',
    'พอร์ตรับสัญญาณ (127.0.0.1)': 'Port to receive signals (127.0.0.1)',
    'สคริปต์ส่งทุก (วินาที)': 'Script sends every (seconds)',
    'ถือว่าหยุดส่งเมื่อเงียบเกิน (วินาที)': 'Consider it stopped when silent longer than (seconds)',
    'สร้างไฟล์สคริปต์ .lua ให้แต่ละอินสแตนซ์ (โฟลเดอร์ heartbeat/)': 'Create a .lua script file for each instance (heartbeat/ folder)',
    'โฟลเดอร์ autoexec ของเอ็กซิคิวเตอร์ (Delta / Arceus X, ใส่ {pkg} = แยกโฟลเดอร์ต่อโคลน)': 'Executor autoexec folder (Delta / Arceus X; put {pkg} = separate folder per clone)',
    'สร้าง/อัปเดต heartbeat.lua ในโฟลเดอร์นั้นอัตโนมัติ': 'Create/update heartbeat.lua in that folder automatically',
    'สร้าง/อัปเดต heartbeat.lua ตอนนี้': 'Create/update heartbeat.lua now',
    'แสดงตัวเลข FPS บนจอเกม (ลากได้ ไม่มีกรอบ ปรับขนาดตามจอ)': 'Show the FPS number on the game screen (draggable, no frame, sized to the screen)',
    'FPS cap ต่อบัญชี (ดึง Roblox ID จากคุกกี้) ...': 'FPS cap per account (get Roblox IDs from cookies) ...',
    'ค่าใหม่ (%d-%d):': 'New value (%d-%d):',
    'สร้างแล้ว: ': 'Created: ',
    'คัดลอกไฟล์ของแต่ละโคลนไปไว้ในโฟลเดอร์ autoexec ของเอ็กซิคิวเตอร์ในโคลนนั้น (หรือรันตอนเข้าเกม)': "Copy each clone's file into that clone's executor autoexec folder (or run it when you enter the game)",
    'พาธโฟลเดอร์ autoexec (Enter=ไม่เปลี่ยน, คั่นหลายโฟลเดอร์ด้วยคอมมา):': 'Autoexec folder path (Enter=no change, separate multiple folders with commas):',
    'สคริปต์ใหม่จะโหลดตอนเข้าเกมครั้งถัดไป (รีจอยหรือเปิดเกมใหม่)': 'The new script loads the next time you enter the game (rejoin or reopen the game)',
    'ประสิทธิภาพ: ล้างแรม / ลดการใช้ CPU': 'Performance: clear RAM / reduce CPU use',
    'ล้างแรมอัตโนมัติ (เฉพาะรูท)': 'Clear RAM automatically (root only)',
    'ล้างแรมทุก (นาที, 0=ไม่ล้างตามเวลา)': 'Clear RAM every (minutes, 0=not by time)',
    'ล้างเมื่อแรมว่างต่ำกว่า (MB, 0=ปิด)': 'Clear when free RAM falls below (MB, 0=off)',
    'ปิดแอปพื้นหลังที่ cached ด้วย (am kill-all) ⚠ อาจปิดโคลนที่อยู่เบื้องหลัง': 'Also kill cached background apps (am kill-all) ⚠ may close clones running in the background',
    'ปิดแอปอื่นที่ไม่เกี่ยวข้องแบบปลอดภัย (force-stop แอปที่ติดตั้งเอง เว้น Roblox/Termux/Delta และรายชื่อข้อ 6)': 'Safely close unrelated apps (force-stop apps you installed, except Roblox/Termux/Delta and the list in item 6)',
    'รายชื่อแอปที่ห้ามปิด (ส่วนของชื่อแพ็กเกจ)': 'List of apps never to close (part of the package name)',
    'โหมดประหยัด CPU (ลดความสำคัญโปรเซส, เก็บสถานะเกมไว้ 60 วินาที, รีเฟรชจอช้าลง)': 'CPU-saving mode (lower process priority, cache game status for 60 seconds, slower screen refresh)',
    'รีเฟรชแผงสถานะทุก (วินาที, ใช้เมื่อเปิดโหมดประหยัด CPU)': 'Refresh the status panel every (seconds, used when CPU-saving mode is on)',
    'ล้างแรมเดี๋ยวนี้': 'Clear RAM now',
    'RAM ว่างตอนนี้: %s MB%s': 'Free RAM now: %s MB%s',
    '  (โหมดไม่รูทล้างแรมไม่ได้)': '  (non-root mode cannot clear RAM)',
    'รายชื่อที่ห้ามปิด คั่นด้วยคอมมา (Enter=คงเดิม):': 'Never-close list, comma-separated (Enter=keep as is):',
    'ล้างแรมแล้ว: ว่าง %s → %s MB': 'RAM cleared: free %s → %s MB',
    'ล้างแรมไม่สำเร็จ: ': 'RAM clear failed: ',
    'ผลแต่ละขั้น: ': 'Result of each step: ',
    'แรมว่างแทบไม่เพิ่ม: drop_caches ล้างได้เฉพาะแคช ไม่ใช่แรมที่แอปใช้อยู่': 'Free RAM barely increased: drop_caches only clears caches, not the RAM apps are using',
    'ลองเปิดข้อ 5 (ปิดแอปอื่นแบบปลอดภัย) เมื่อ drop_caches ถูกล็อก (มักเป็นตู้คลาวด์/คอนเทนเนอร์)': 'Try turning on option 5 (safely close other apps) when drop_caches is locked (usually cloud phones/containers)',
    'เริ่มทำงาน': 'Started',
    'หลุด/ค้าง': 'Drops/hangs',
    'รีจอยแล้ว': 'Rejoined',
    'หยุดทำงาน': 'Stopped',
    'รายงานสถานะ+ภาพหน้าจอเป็นรอบ': 'Periodic status report + screenshot',
    'แจ้งเตือนผิดปกติ (รีจอยถี่/แรมต่ำ)': 'Abnormal alerts (frequent rejoins / low RAM)',
    'เปิดใช้งาน Webhook': 'Enable Webhook',
    'แจ้งเตือน: ': 'Alerts: ',
    'แนบภาพหน้าจอ (รูทเท่านั้น, เก็บล่าสุด %d รูป)': 'Attach a screenshot (root only, latest %d kept)',
    'ส่งรายงานสถานะ+ภาพหน้าจอทุกกี่นาที (0=ปิด)': 'Send a status report + screenshot every how many minutes (0=off)',
    'Console webhook: URL (ว่าง = ใช้ URL หลัก)': 'Console webhook: URL (empty = use the main URL)',
    '(ใช้ URL หลัก)': '(using the main URL)',
    'Console webhook: ส่งบันทึกคอนโซลเข้า Discord': 'Console webhook: send console log to Discord',
    'Console webhook: รวมส่งทุก (วินาที, 3-300)': 'Console webhook: batch and send every (seconds, 3-300)',
    'ทดสอบ Console webhook': 'Test console webhook',
    'ส่งข้อความทดสอบ': 'Send a test message',
    'วาง URL Webhook (Enter=ลบ):': 'Paste the Webhook URL (Enter=delete):',
    'รูปแบบ URL ไม่ถูกต้อง': 'Invalid URL format',
    'ทุกกี่นาที (0-1440):': 'Every how many minutes (0-1440):',
    'ต้องเป็นตัวเลข 0-1440': 'Must be a number 0-1440',
    'วาง URL Console webhook (Enter=ใช้ URL หลัก):': 'Paste the Console webhook URL (Enter=use the main URL):',
    'ค่าใหม่ (3-300):': 'New value (3-300):',
    'ต้องเป็นตัวเลข 3-300': 'Must be a number 3-300',
    'ยังไม่มี URL (ตั้ง URL หลักหรือ Console URL ก่อน)': 'No URL yet (set the main URL or the Console URL first)',
    'GARNT TOOL REJOIN: ทดสอบ console webhook ✅': 'GARNT TOOL REJOIN: console webhook test ✅',
    'ส่งสำเร็จ': 'Sent successfully',
    'ส่งไม่สำเร็จ ตรวจสอบ URL': 'Send failed, check the URL',
    '✅ ทดสอบ Webhook': '✅ Webhook test',
    'เชื่อมต่อสำเร็จ': 'Connected successfully',
    'กำลังดึงชื่อบัญชี / Roblox ID จากคุกกี้...': 'Getting account names / Roblox IDs from cookies...',
    'รายการอินสแตนซ์ (%d)': 'Instance list (%d)',
    'เลือกรัน %d/%d | คุกกี้ในไฟล์ %d บรรทัด | ลิงก์เกม: %s': 'Selected %d/%d | cookies in file %d lines | game link: %s',
    'มีเวอร์ชันใหม่ v%s (ตอนนี้ v%s)': 'New version available v%s (current v%s)',
    '🆕 มีเวอร์ชันใหม่': '🆕 New version available',
    'v%s -> v%s  (เมนู 11 หรือ python rejoin.py update)': 'v%s -> v%s  (menu 11 or python rejoin.py update)',
    'เช็คเวอร์ชันไม่ได้: %s': 'Cannot check the version: %s',
    'เวอร์ชันปัจจุบัน v%s กำลังเช็คเวอร์ชันบน GitHub...': 'Current version v%s, checking the version on GitHub...',
    'ต่อ GitHub ไม่ได้: %s': 'Cannot reach GitHub: %s',
    'โหลดไม่สำเร็จ HTTP %d': 'Download failed HTTP %d',
    'ไฟล์บน GitHub ไม่มี VERSION ไม่อัปเดต': 'The file on GitHub has no VERSION, not updating',
    'ตัวนี้เป็นเวอร์ชันล่าสุดแล้ว (v%s) ไม่ต้องอัปเดต': 'You already have the latest version (v%s), no update needed',
    'เครื่องใหม่กว่า GitHub (เครื่อง v%s / GitHub v%s) ไม่ลดเวอร์ชัน': 'This copy is newer than GitHub (local v%s / GitHub v%s), not downgrading',
    'ไฟล์ใหม่มีโค้ดพัง (%s) ไม่อัปเดต': 'The new file has broken code (%s), not updating',
    'เขียนไฟล์ไม่ได้: %s': 'Cannot write the file: %s',
    'อัปเดตแล้ว v%s -> v%s (เก็บของเก่าไว้ที่ %s.bak) รันโปรแกรมใหม่เพื่อใช้เวอร์ชันนี้': 'Updated v%s -> v%s (old copy kept at %s.bak). Restart the program to use this version',
    'ไม่พบไฟล์สำรอง %s.bak (ยังไม่เคยอัปเดต?)': 'Backup file %s.bak not found (never updated?)',
    'ไฟล์สำรองใช้ไม่ได้: %s': 'Backup file is unusable: %s',
    'ย้อนกลับไม่ได้: %s': 'Cannot roll back: %s',
    'ย้อนกลับเป็น v%s แล้ว (เวอร์ชันที่เพิ่งใช้อยู่ถูกเก็บเป็น .bak) รันโปรแกรมใหม่เพื่อใช้งาน': 'Rolled back to v%s (the version you were just using is kept as .bak). Restart the program to use it',
    '1) อัปเดตจาก GitHub   2) ย้อนกลับเป็นเวอร์ชันก่อนหน้า (.bak)   0) กลับ': '1) Update from GitHub   2) Roll back to the previous version (.bak)   0) Back',
    'เลือก (Enter=1):': 'Choose (Enter=1):',
    'ย้อนกลับเป็นไฟล์สำรอง?': 'Roll back to the backup file?',
    'รุ่น CPU': 'CPU model',
    'สถาปัตยกรรม': 'Architecture',
    'จำนวนคอร์': 'Core count',
    'ความเร็ว (ตอนนี้ / สูงสุด)': 'Speed (now / max)',
    'กำลังทดสอบ CPU 1 คอร์ (%d วินาที) ...': 'Testing CPU on 1 core (%d seconds) ...',
    'คะแนน 1 คอร์': 'Single-core score',
    'กำลังทดสอบ CPU ทุกคอร์ (%d คอร์ x %d วินาที) ...': 'Testing CPU on all cores (%d cores x %d seconds) ...',
    'ทดสอบหลายคอร์': 'Multi-core test',
    'ไม่สำเร็จ: %s': 'Failed: %s',
    'คะแนนทุกคอร์': 'All-core score',
    'ประสิทธิภาพเพิ่มขึ้น': 'Performance gain',
    'x%.2f จาก %d คอร์ (เต็ม = x%d)': 'x%.2f from %d cores (full = x%d)',
    'CPU ใช้งานระหว่างเทส': 'CPU usage during the test',
    'ได้ไม่ถึง 60% ของคอร์ที่มี: อาจโดนจำกัด CPU / แชร์เครื่องกับคนอื่น / มีแอปอื่นกินอยู่': 'Reached less than 60% of the available cores: the CPU may be limited / shared with others / another app is using it',
    'RAM ทั้งหมด': 'Total RAM',
    'ใช้ไปแล้ว': 'Used',
    'Swap (ใช้/ทั้งหมด)': 'Swap (used/total)',
    'กำลังทดสอบความเร็วแรม (บล็อก %d MB) ...': 'Testing RAM speed (%d MB block) ...',
    'จองแรม + แตะทุกหน้า': 'Allocate RAM + touch every page',
    'เขียน': 'Write',
    'คัดลอก': 'Copy',
    'ตรวจความถูกต้องของข้อมูล': 'Data integrity check',
    'ผ่าน': 'Passed',
    'ไม่ผ่าน ⚠ แรมอาจมีปัญหา': 'Failed ⚠ the RAM may have a problem',
    'ทดสอบความเร็ว': 'Speed test',
    'จองแรมไม่ได้ (MemoryError)': 'Cannot allocate RAM (MemoryError)',
    'อ่านไม่ได้ (dumpsys SurfaceFlinger ต้องใช้สิทธิ์ ลองโหมดรูท)': 'Cannot read (dumpsys SurfaceFlinger needs permission, try root mode)',
    'OpenGL ES ที่รองรับ': 'Supported OpenGL ES',
    'ไดรเวอร์ EGL': 'EGL driver',
    'แพลตฟอร์ม': 'Platform',
    '⚠ คำเตือน': '⚠ Warning',
    'ดูเหมือนเป็นซอฟต์แวร์เรนเดอร์ (ไม่มี GPU จริง) กราฟิกจะกิน CPU และช้ากว่ามาก': 'Looks like a software renderer (no real GPU); graphics will use the CPU and be much slower',
    'กำลังวัดโหลด GPU 5 วินาที ...': 'Measuring GPU load for 5 seconds ...',
    'โหลด GPU (เฉลี่ย / สูงสุด)': 'GPU load (avg / max)',
    'โหลด GPU': 'GPU load',
    'อ่านไม่ได้ (เครื่องนี้ไม่เปิด sysfs ของ GPU ให้อ่าน)': 'Cannot read (this device does not expose the GPU sysfs)',
    'ความเร็ว GPU': 'GPU speed',
    'Termux สร้างงานกราฟิกจริงไม่ได้ จึงวัดได้แค่ข้อมูล + โหลด; วัดคะแนนจริงใช้ GFXBench / 3DMark': 'Termux cannot create real graphics work, so only info + load can be measured; for a real score use GFXBench / 3DMark',
    'ทดสอบ CPU': 'CPU test',
    'ทดสอบ RAM': 'RAM test',
    'ทดสอบ GPU': 'GPU test',
    'เทสเครื่อง Cloud Phone': 'Cloud Phone test',
    'CPU — ข้อมูลคอร์ + คะแนน 1 คอร์ / ทุกคอร์': 'CPU — core info + single-core / all-core score',
    'RAM — ความจุ + ความเร็วเขียน/คัดลอก + ตรวจความถูกต้อง': 'RAM — capacity + write/copy speed + integrity check',
    'GPU — รุ่น/ไดรเวอร์ + โหลด (ถ้าอ่านได้)': 'GPU — model/driver + load (if readable)',
    'ทดสอบทั้งหมด (ใช้เวลาราว 30 วินาที)': 'Test everything (takes about 30 seconds)',
    'แนะนำ: หยุดรีจอย/ปิดเกมก่อนเทส เพื่อให้ผลไม่เพี้ยน': 'Tip: stop rejoining / close the game before testing so results are not skewed',
    'ยกเลิกการทดสอบ': 'Test cancelled',
    'ไฟล์ไม่ใช่ค่าตั้งค่า': 'The file is not a settings file',
    '^(#\\d+ \\S+): (.+?) \\(ครั้งที่ \\d+\\) รีจอยใน \\d+s$': '^(#\\d+ \\S+): (.+?) \\(#\\d+\\) rejoining in \\d+s$',
    '^(#\\d+ \\S+): รีจอยแล้ว \\(ครั้งที่ \\d+\\)$': '^(#\\d+ \\S+): rejoined \\(#\\d+\\)$',
    'ยังไม่มีไฟล์ log': 'No log file yet',
    'เปิดการแจ้งเตือนผิดปกติ': 'Enable abnormal alerts',
    'หลุด/ค้างกี่ครั้งถึงแจ้ง (2-50)': 'Drops/hangs before alerting (2-50)',
    'ภายในกี่นาที (1-1440)': 'Within how many minutes (1-1440)',
    'เว้นกี่นาทีก่อนแจ้งซ้ำ (1-1440)': 'Minutes to wait before alerting again (1-1440)',
    'แจ้งเมื่อแรมว่างต่ำกว่า MB (0=ใช้ค่าของล้างแรม)': 'Alert when free RAM is below MB (0=use the clear-RAM setting)',
    'แจ้งเตือนผิดปกติ (ส่งเข้า Discord ผ่านเหตุการณ์ "แจ้งเตือนผิดปกติ" ในเมนู 7)': 'Abnormal alerts (sent to Discord through the "Abnormal alerts" event in menu 7)',
    'สำรอง/กู้ค่า + log + แจ้งเตือน': 'Backup/restore + log + alerts',
    'สำรองค่าตั้งค่า (config.json -> backup/)': 'Back up settings (config.json -> backup/)',
    'กู้ค่าตั้งค่าจากไฟล์สำรอง': 'Restore settings from a backup file',
    'ดู log ล่าสุด 30 บรรทัด': 'View the latest 30 log lines',
    'สรุปรีจอย/หลุดต่ออินสแตนซ์ (ย้อนหลัง N ชั่วโมง)': 'Per-instance rejoin/drop summary (last N hours)',
    'ตั้งค่าแจ้งเตือนผิดปกติ (รีจอยถี่ / แรมต่ำ)': 'Abnormal-alert settings (frequent rejoins / low RAM)',
    'สำรองแล้ว: ': 'Backed up: ',
    'ไฟล์นี้มี URL Webhook อยู่ ห้ามแชร์ให้ใคร (คุกกี้ไม่ได้อยู่ในไฟล์นี้)': 'This file contains your Webhook URL, do not share it (cookies are not in this file)',
    'สำรองไม่ได้: %s': 'Backup failed: %s',
    'ยังไม่มีไฟล์สำรองใน ': 'No backup files yet in ',
    'เลือกไฟล์ (0=ยกเลิก):': 'Choose a file (0=cancel):',
    'กู้ค่าจาก %s (ค่าปัจจุบันจะถูกสำรองไว้ก่อน)?': 'Restore from %s (the current settings will be backed up first)?',
    'กู้ค่าแล้ว': 'Settings restored',
    'กู้ไม่ได้: %s': 'Restore failed: %s',
    'ย้อนหลังกี่ชั่วโมง (1-168, Enter=24):': 'Look back how many hours (1-168, Enter=24):',
    'ไม่พบเหตุการณ์หลุด/รีจอยใน %d ชั่วโมงที่ผ่านมา': 'No drops/rejoins found in the last %d hours',
    'สรุปย้อนหลัง %d ชั่วโมง': 'Summary of the last %d hours',
    'อินสแตนซ์': 'Instance',
    'สาเหตุบ่อยสุด': 'Most common cause',
    'เริ่มรีจอยอัตโนมัติ': 'Start auto rejoin',
    'แสดงอินสแตนซ์ / สถานะ': 'Show instances / status',
    'ตั้งค่า (ลิงก์เกม, ช่วงเวลาตรวจสอบ, หน่วงการเปิด)': 'Settings (game link, check interval, launch delay)',
    'สแกนแพ็กเกจ': 'Scan packages',
    'คุกกี้ (ล็อกอิน / ดึงจากแอป Roblox)': 'Cookies (login / pull from the Roblox apps)',
    'บล็อก / เลิกบล็อก / เพิ่มเพื่อน (ด้วยคุกกี้)': 'Block / unblock / add friends (using cookies)',
    'เปิด / ปิด Roblox ทั้งหมด (ตั้งหน่วงเวลาได้)': 'Open / close all Roblox (delay adjustable)',
    'แสดงรายการอินสแตนซ์': 'Show instance list',
    'อัปเดตเครื่องมือ (เช็คเวอร์ชันก่อน)': 'Update the tool (checks the version first)',
    'เทสเครื่อง Cloud Phone (CPU / RAM / GPU)': 'Cloud Phone test (CPU / RAM / GPU)',
    'สำรอง/กู้ค่า + ดู log + แจ้งเตือนผิดปกติ': 'Backup/restore + view log + abnormal alerts',
    'ออก': 'Exit',
    'ไม่พบ Java และไม่มีคำสั่ง pkg (ไม่ใช่ Termux?) ข้ามการติดตั้ง': 'Java not found and there is no pkg command (not Termux?); skipping installation',
    'ไม่พบ Java กำลังติดตั้ง openjdk-17 (ครั้งเดียว อาจใช้เวลาสักครู่)...': 'Java not found, installing openjdk-17 (one time, may take a moment)...',
    'ติดตั้ง Java ไม่สำเร็จ: %s': 'Java installation failed: %s',
    'ติดตั้ง Java ไม่สำเร็จ ลองเอง: pkg install openjdk-17': 'Java installation failed, try it yourself: pkg install openjdk-17',
    'ติดตั้ง Java: %s': 'Installing Java: %s',
    'สำเร็จ': 'success',
    'ล้มเหลว rc=%s': 'failed rc=%s',
    'ติดตั้ง Java สำเร็จ': 'Java installed successfully',
    'เริ่มโปรแกรม โหมด=%s โฟลเดอร์=%s': 'Program started, mode=%s, folder=%s',
    'เขียน /sdcard/Download ไม่ได้ (ยังไม่อนุญาตที่เก็บข้อมูล?) ใช้โฟลเดอร์สำรอง: ': 'Cannot write to /sdcard/Download (storage permission not granted?), using a fallback folder: ',
    'กำลังสแกนแพ็กเกจ...': 'Scanning packages...',
    'เริ่มอัตโนมัติใน 5 วินาที (Ctrl+C เพื่อยกเลิก)': 'Starting automatically in 5 seconds (Ctrl+C to cancel)',
    'เลือกเมนู:': 'Choose a menu:',
    'ลาก่อน!': 'Goodbye!',
    '\nออกจากโปรแกรม': '\nExiting the program'
}

_LUA_EN = {
    'ใช้กับบัญชีและเครื่องของตัวเองเท่านั้น ต้องรันผ่านเอ็กซิคิวเตอร์ที่รองรับ HTTP': 'Use only with your own accounts and devices. Must run through an executor that supports HTTP',
    'ชื่อแพ็กเกจหรือเลขอินสแตนซ์ ต้องตรงกับในเครื่องมือ': 'package name or instance number, must match the tool',
    'ส่งทุกกี่วินาที': 'send every N seconds',
    'true = แสดงตัวเลข FPS บนจอ (ลากย้ายได้)': 'true = show the FPS number on screen (draggable)',
    'FPS cap ของบัญชีที่ไม่อยู่ในรายการ (0 = ไม่แตะ)': 'FPS cap for accounts not in the list (0 = leave untouched)',
    'FPS cap ต่อบัญชี: [Roblox UserId] = FPS (ตั้งจากเมนูในเครื่องมือ)': "FPS cap per account: [Roblox UserId] = FPS (set from the tool's menu)",
    'FPS cap: ดู UserId ของบัญชีที่กำลังเล่นอยู่ แล้วตั้งเพดานตามที่ตั้งไว้ (ต้องมี setfpscap ในเอ็กซิคิวเตอร์)': 'FPS cap: look at the UserId of the account currently playing, then set the configured cap (the executor must have setfpscap)',
    'capInfo ถูกส่งไปกับ heartbeat (cap=) และแสดงในคอนโซล: off=ไม่ได้ตั้งให้ ID นี้ nofn=ไม่มีฟังก์ชัน err=เรียกแล้ว error': 'capInfo is sent with the heartbeat (cap=) and shown in the console: off=not set for this ID nofn=no function err=call raised an error',
    'noeffNN=เรียกแล้วแต่ FPS จริงยังเป็น NN (ไม่ติด) setNN=ตั้งแล้ว': 'noeffNN=called but actual FPS is still NN (not effective) setNN=set',
    'FPS จริงที่วัดเอง (อัปเดตจาก Heartbeat ด้านล่าง) ไม่เชื่อ getfpscap เพราะบางตัวรันอ่านค่าเพี้ยน': 'FPS measured by ourselves (updated from Heartbeat below); getfpscap is not trusted because some executors return wrong values',
    'รวบ env ที่เอ็กซิคิวเตอร์ต่างค่ายชอบซ่อนฟังก์ชันไว้: getgenv, _G, getfenv, และตาราง syn/fluxus/krnl': 'collect the envs where different executors like to hide functions: getgenv, _G, getfenv, and the syn/fluxus/krnl tables',
    'ตรวจจาก FPS จริง หลังตั้งไปแล้ว 5 วินาทีขึ้นไป: ถ้ายังสูงกว่าเพดานมาก = ตัวรันรับคำสั่งแต่ไม่ทำจริง': 'check using the real FPS, 5+ seconds after setting: if it is still far above the cap = the executor accepts the call but does nothing',
    '(ถ้าเครื่องช้ากว่า cap อยู่แล้ว แยกไม่ออกว่า cap ติดหรือไม่ จะขึ้น set ตามปกติ)': '(if the device is already slower than the cap we cannot tell whether it took effect, so it shows set as usual)',
    'เกมค้าง: ไม่ส่ง': 'game frozen: do not send',
    'ขึ้นหน้าต่าง Disconnected: ส่งข้อความ error ทันที': 'Disconnected window shown: send the error text immediately',
    'ตั้งซ้ำทุกรอบ เผื่อเกม/เอ็กซิคิวเตอร์รีเซ็ตค่า': 'set again every round in case the game/executor resets the value',
    'ตัวเลข FPS: ไม่มีกรอบ ฟอนต์กลม ขนาดปรับตามความกว้างจออัตโนมัติ ลากไปวางที่ไหนก็ได้': 'FPS number: no frame, round font, size adjusts to the screen width automatically, drag it anywhere',
    '(จำตำแหน่งไว้ถ้าเอ็กซิคิวเตอร์รองรับ writefile)': '(position is remembered if the executor supports writefile)',
    'ตัวกลมก่อน ถ้าไม่มีค่อยถอยไปตัวถัดไป': 'round fonts first, fall back to the next one if missing',
    'พื้นดำ ไม่มีกรอบ': 'black background, no frame',
    'ขอบมนเต็มที่': 'fully rounded corners',
    'ขนาดตามความกว้างจอ: เล็กกระทัดรัดแต่ยังอ่านออก': 'size follows the screen width: compact but still readable',
    'หมุนจอ/ย่อขยายหน้าต่าง': 'screen rotation / window resize'
}


def tr(s):
    """Thai text is the source language; returns the English text when LANG == "en"."""
    return _EN.get(s, s) if LANG == "en" else s


def tr_lua(text):
    """English version of the Thai comments inside the generated Lua script."""
    if LANG != "en":
        return text
    out = []
    for ln in text.split("\n"):
        if re.search(r"[\u0e00-\u0e7f]", ln):
            m = re.search(r"--\s*([^\n]*[\u0e00-\u0e7f][^\n]*)$", ln)
            if m and m.group(1).strip() in _LUA_EN:
                ln = ln[:m.start(1)] + _LUA_EN[m.group(1).strip()]
        out.append(ln)
    return "\n".join(out)


console = Console()
log = logging.getLogger("rejoin")

# ----------------------------------------------------------------- secrets scrubbing
SECRETS = set()  # raw cookies loaded in memory; used only to scrub output
_PATS = [
    (re.compile(r"_\|WARNING:[^\s\"';,]+"), "***"),
    (re.compile(r"(\.ROBLOSECURITY=)[^\s;\"']+"), r"\1***"),
    (re.compile(r"(webhooks/\d+/)[\w-]+"), r"\1***"),
    (re.compile(r"[A-Fa-f0-9]{150,}"), "***"),
]


def scrub(s):
    s = str(s)
    for c in list(SECRETS):
        if c:
            s = s.replace(c, "***")
    for p, r in _PATS:
        s = p.sub(r, s)
    return s


def mask(cookie):
    return tr("********  (%d ตัวอักษร)") % len(cookie)


# ----------------------------------------------------------------- streamer mode (display only)
STREAMER = {"on": True}
USERS = set()   # account names seen so far (used to mask them inside free text)


def hide_name(name):
    """First 4 characters stay visible, the rest becomes # (screen only; data and logs are unchanged)."""
    name = str(name or "")
    if not STREAMER["on"] or len(name) <= 4:
        return name
    return name[:4] + "#" * (len(name) - 4)


def hide_id(uid):
    """Roblox user ID on screen: first 3 digits stay visible in streamer mode."""
    uid = str(uid or "")
    if not STREAMER["on"] or len(uid) <= 3:
        return uid
    return uid[:3] + "#" * (len(uid) - 3)


def hide_users(text):
    if not STREAMER["on"]:
        return text
    for u in sorted(list(USERS), key=len, reverse=True):
        if len(u) > 4:
            text = re.sub(r"(?<![A-Za-z0-9_.])%s(?![A-Za-z0-9_])" % re.escape(u),
                          lambda m, r=hide_name(u): r, text)
    return text


class ScrubFilter(logging.Filter):
    def filter(self, record):
        record.msg = scrub(record.getMessage())
        record.args = ()
        return True


def setup_logging():
    log.setLevel(logging.INFO)
    log.propagate = False
    h = TimedRotatingFileHandler(os.path.join(LOG_DIR, "rejoin.log"), when="midnight",
                                 backupCount=7, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    h.addFilter(ScrubFilter())
    log.addHandler(h)


# ----------------------------------------------------------------- config
DEFAULTS = {
    "game_link": "",
    "check_interval": 30,
    "launch_delay": 15,
    "block_gap_sec": 20,   # social menu item 5: min seconds between two blocks from the SAME account (Roblox ~3/min per cookie)
    "open_all_delay": 10,   # main menu 9: seconds between opening each Roblox instance
    "package_prefix": "com.roblox.",
    "extra_prefixes": ["aya."],   # also scanned / protected / checked exactly like package_prefix
    "packages": [],          # empty = run every detected package
    "detected": [],
    "backoff_base": 10,
    "backoff_max": 300,
    "stable_reset": 300,
    "join_grace": 90,
    "auto": {
        "start_on_launch": False,
        "wake_lock": True,
        "force_stop": True,
        "foreground_check": False,
        "screenshot_on_crash": True,
        "cookie_login_on_start": False,
        "cookie_pull_on_start": False,
        "presence_check": True,
        "timed_rejoin_min": 0,
    },
    "heartbeat": {"enabled": True, "port": 8765, "interval": 15, "timeout": 90, "show_fps": True,
                  "fps_cap": {"default": 0, "accounts": {}}},   # accounts: {"<Roblox UserId>": {"name": ..., "cap": N}}; default 0 = leave FPS alone
    "autoexec": {"auto_create": True, "enabled_once": False, "filename": "heartbeat.lua",
                 "arceus_added": False,
                 "dirs": ["/storage/emulated/0/Delta/Autoexecute",
                          "/storage/emulated/0/Arceus X/Autoexec"]},   # {pkg} in a path = one folder per clone
    "update": {"check": True, "hours": 6, "notified": ""},   # background version check; notified = last version already pushed to webhook
    "performance": {"low_cpu": True, "ui_refresh_sec": 2},
    "alert": {"enabled": True, "storm_count": 5, "storm_window_min": 60, "cooldown_min": 30, "low_ram_mb": 0},   # low_ram_mb 0 = use ram.min_free_mb
    "ui": {"streamer_mode": True},
    "lang": "th",   # hide account names on screen (first 4 chars + #)
    "console_webhook": {"enabled": False, "url": "", "flush_sec": 10},   # blank url = reuse the main webhook
    "ram": {"enabled": True, "interval_min": 30, "min_free_mb": 300, "kill_cached": False,
            "kill_others": False, "protect": ["termux", "magisk", "delta"]},
    "webhook": {
        "url": "",
        "enabled": False,
        "send_screenshot": True,
        "periodic_min": 10,   # status report + screenshot every N minutes (0 = off)
        "events": {"start": True, "crash": True, "rejoin": True, "stop": True, "status": True, "update": True, "alert": True},
    },
}


def merge(base, new):
    out = {}
    for k, v in base.items():
        nv = new.get(k) if isinstance(new, dict) else None
        out[k] = merge(v, nv if isinstance(nv, dict) else {}) if isinstance(v, dict) else (nv if nv is not None else v)
    return out


class Config:
    def __init__(self):
        self.lock = threading.Lock()
        self.d = self.load()

    def load(self):
        raw = {}
        try:
            with open(CFG_PATH, encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                raise ValueError("bad config")
        except FileNotFoundError:
            raw = {}
        except Exception:
            try:
                shutil.copy(CFG_PATH, CFG_PATH + ".bak")
            except OSError:
                pass
            raw = {}
        d = merge(DEFAULTS, raw)
        a = d["autoexec"]
        if not a.get("arceus_added"):
            if ARCEUS_AUTOEXEC not in a["dirs"]:
                a["dirs"].append(ARCEUS_AUTOEXEC)
            a["arceus_added"] = True
        return d

    def save(self):
        with self.lock:
            tmp = CFG_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.d, f, ensure_ascii=False, indent=2)
            os.replace(tmp, CFG_PATH)


# ----------------------------------------------------------------- shell / root
class Env:
    root = False
    su = None


ENV = Env()


def find_su():
    p = shutil.which("su")
    if p:
        return p
    for c in ("/system/xbin/su", "/system/bin/su", "/sbin/su", "/su/bin/su", "/debug_ramdisk/su"):
        if os.path.exists(c):
            return c
    return None


def sh(cmd, root=False, timeout=20):
    """Run a shell command (as root through su when requested). Returns (rc, stdout+stderr)."""
    if root and os.geteuid() != 0:
        if not ENV.su:
            return 1, ""
        argv = [ENV.su, "-c", cmd]
    else:
        argv = ["sh", "-c", cmd]
    try:
        p = subprocess.run(argv, capture_output=True, encoding="utf-8", errors="replace",
                           timeout=timeout, stdin=subprocess.DEVNULL)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, ""
    except Exception as e:
        return 1, scrub(e)


def detect_root():
    ENV.su = find_su()
    if os.geteuid() == 0:
        ENV.root = True
        return
    if not ENV.su:
        ENV.root = False
        return
    _, out = sh("id", root=True, timeout=20)  # = su -c id
    ENV.root = "uid=0" in out


_cache = {}
_clock = threading.RLock()


def cached(key, ttl, fn):
    """TTL cache; the lock makes simultaneous callers share a single (expensive) call."""
    with _clock:
        v = _cache.get(key)
        if v and time.time() - v[0] < ttl:
            return v[1]
        r = fn()
        _cache[key] = (time.time(), r)
        return r


FG_CMD = "dumpsys window 2>/dev/null | grep -E 'mCurrentFocus|mFocusedApp'"
SNAP = {"fg": False, "ttl": 5}


def snapshot():
    """ONE shell call per check cycle: process names (+ foreground info when enabled)."""
    def run():
        cmd = "ps -A -o NAME 2>/dev/null"
        if SNAP["fg"]:
            cmd += "; echo '@@FG@@'; " + FG_CMD
        _, out = sh(cmd, root=ENV.root, timeout=40)
        names, _, fg = out.partition("@@FG@@")
        procs = {l.strip() for l in names.splitlines() if l.strip()}
        return {"procs": procs, "fg": fg, "ps_ok": len(procs) > 5}
    return cached(("snap", SNAP["fg"]), SNAP["ttl"], run)


def foreground_text():
    if SNAP["fg"]:
        return snapshot()["fg"]
    return cached("fg", 5, lambda: sh(FG_CMD, root=True, timeout=25)[1])


def proc_alive(pkg):
    snap = snapshot()
    if pkg in snap["procs"]:
        return True
    if ENV.root and snap["ps_ok"]:
        return False
    rc, out = sh("pidof " + shlex.quote(pkg), root=ENV.root, timeout=10)
    if rc == 127 or "not found" in out:
        return False
    return bool(re.search(r"\b\d+\b", out))


def mem_available_mb():
    try:
        d = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, _, v = line.partition(":")
                d[k] = int(v.split()[0])
        kb = d.get("MemAvailable") or (d.get("MemFree", 0) + d.get("Cached", 0))
        return int(kb / 1024)
    except Exception:
        return None


class CpuMeter:
    """CPU usage from /proc/stat deltas (root fallback; last resort: load average / cores)."""

    def __init__(self):
        self.prev, self.prev_t = None, 0.0     # sample at the previous read -> average since then

    @staticmethod
    def _sample():
        line = ""
        try:
            with open("/proc/stat") as f:
                line = f.readline()
        except OSError:
            pass
        if not line.startswith("cpu") and ENV.root:
            _, out = sh("head -n 1 /proc/stat", root=True, timeout=10)
            line = out.strip().splitlines()[0] if out.strip() else ""
        try:
            n = [int(x) for x in line.split()[1:9]]
        except ValueError:
            return None
        if not line.startswith("cpu") or len(n) < 4:
            return None
        return n[3] + (n[4] if len(n) > 4 else 0), sum(n)    # (idle+iowait, total)

    @staticmethod
    def _pct(a, b):
        di, dt = b[0] - a[0], b[1] - a[1]
        return None if dt <= 0 else max(0.0, min(100.0, 100.0 * (1 - di / dt)))

    def read(self, wait=1.0):
        """-> {"now": % over `wait` seconds, "avg": % since the previous read or None, "cores": n, "approx": bool}"""
        cores = os.cpu_count() or 1
        a = self._sample()
        if a is None:
            load = sys_load()
            return {"now": None if load is None else min(100.0, 100.0 * load / cores),
                    "avg": None, "cores": cores, "approx": True}
        time.sleep(wait)
        b = self._sample() or a
        avg = self._pct(self.prev, b) if self.prev and time.time() - self.prev_t >= 20 else None
        self.prev, self.prev_t = b, time.time()
        return {"now": self._pct(a, b), "avg": avg, "cores": cores, "approx": False}


CPU = CpuMeter()


def cpu_text():
    """Webhook text: CPU % now (1 s sample) + average since the previous report."""
    try:
        r = CPU.read()
    except Exception:
        return "-"
    if r["now"] is None:
        return "-"
    s = ("~%.0f%%" if r["approx"] else "%.0f%%") % r["now"]
    if r["avg"] is not None:
        s += tr(" | เฉลี่ย %.0f%%") % r["avg"]
    s += tr(" | %d คอร์") % r["cores"]
    if r["approx"]:
        s += tr(" (ประมาณจากโหลดเฉลี่ย)")
    return s


def AM():
    """Use the system `am` when root: Termux's wrapper `am` often fails under su."""
    return "/system/bin/am" if ENV.root and os.path.exists("/system/bin/am") else "am"


RAM_LAST = {"detail": ""}
DROP = {"ok": None}   # None = not probed yet


KEEP_PKGS = ["com.termux", "com.android.systemui", "com.topjohnwu.magisk", "ahapps.controlthescreenorientation"]


def kill_others(cfg):
    """Root only: force-stop RUNNING user-installed apps that are not protected. -> list of stopped packages.
    Protected: Roblox clones, anything matching ram.protect (substring), the launcher."""
    r = cfg.d["ram"]
    _, o3 = sh("pm list packages -3", root=True, timeout=30)
    third = {l.split(":", 1)[1].strip() for l in o3.splitlines() if l.startswith("package:")}
    snap = snapshot()
    ps_ok = snap["ps_ok"]                              # ps is blocked on some cloud phones/containers
    running = {p.split(":")[0] for p in snap["procs"]} if ps_ok else set()
    # termux/root managers are ALWAYS protected, whatever the user types in the protect list
    prot = ([x.strip().lower() for x in r["protect"] if x.strip()] + [x.lower() for x in prefixes(cfg)] + [
                                                                      "termux", "magisk", "kernelsu", "supersu", "controlthescreenorientation"])
    keep = set(cfg.d["detected"]) | set(KEEP_PKGS) | {home_pkg()}
    pool = (third & running) if ps_ok else third       # no process list: force-stop is harmless on idle apps
    victims = [p for p in sorted(pool) if p not in keep and not any(x in p.lower() for x in prot)]
    if not ps_ok:
        victims = victims[:60]
    if victims:                                        # ONE shell call instead of one per app
        sh("; ".join(AM() + " force-stop " + shlex.quote(p) for p in victims), root=True, timeout=60)
    return victims, ps_ok


def drop_ram(kill_cached=False, cfg=None):
    """Root only. Drop page cache (and optionally kill cached background apps).
    -> (ok, before_mb, after_mb); a per-step report is left in RAM_LAST["detail"]."""
    if not ENV.root:
        RAM_LAST["detail"] = tr("ต้องใช้รูท")
        return False, None, None
    before = mem_available_mb()
    steps = []
    if DROP["ok"] is None:                             # probe once per run: /proc/sys is read-only on containers/cloud phones
        rc, _ = sh("[ -w /proc/sys/vm/drop_caches ]", root=True, timeout=15)
        DROP["ok"] = (rc == 0)
        if not DROP["ok"]:
            steps.append(("drop_caches", False, "unavailable here (read-only), skipped from now on"))
    if DROP["ok"]:
        rc, out = sh("sync; echo 3 > /proc/sys/vm/drop_caches", root=True, timeout=40)
        if rc != 0:
            DROP["ok"] = False                         # don't retry every cycle
            steps.append(("drop_caches", False, "denied, skipped from now on"))
        else:
            steps.append(("drop_caches", True, ""))
    if kill_cached:
        rc, out = sh(AM() + " kill-all", root=True, timeout=30)
        steps.append(("kill-all", rc == 0, "" if rc == 0 else "failed"))
    if cfg is not None and cfg.d["ram"]["kill_others"]:
        try:
            v, ps_ok = kill_others(cfg)
            note = "" if ps_ok else " (no process list: stopped all non-protected apps)"
            steps.append(("kill-others", True, ("closed %d%s" % (len(v), note)) if v else "nothing to close"))
        except Exception as e:
            steps.append(("kill-others", False, "failed (%s)" % type(e).__name__))
    time.sleep(1)
    RAM_LAST["detail"] = "; ".join(
        "%s: %s" % (n, "ok" if (ok and not m.strip()) else (" ".join(m.split())[:80] or "failed")) for n, ok, m in steps)
    log.info("ram steps: %s", RAM_LAST["detail"])
    return any(ok for _, ok, _ in steps), before, mem_available_mb()


def home_pkg():
    def run():
        _, o = sh("cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.HOME",
                  root=True, timeout=15)
        for l in reversed(o.strip().splitlines()):
            m = re.match(r"([\w.]+)/", l.strip())
            if m:
                return m.group(1)
        return ""
    return cached("home", 3600, run)


_NICED = False


def apply_perf(cfg):
    """Lower this process priority so the game keeps the CPU (takes effect once per run)."""
    global _NICED
    if cfg.d["performance"]["low_cpu"] and not _NICED:
        try:
            os.nice(10)
            _NICED = True
        except (OSError, AttributeError):
            pass


def wake_lock(on):
    if on:
        sh("termux-wake-lock", timeout=10)
        if ENV.root:
            sh("echo garnt_rejoin > /sys/power/wake_lock", root=True)
    else:
        sh("termux-wake-unlock", timeout=10)
        if ENV.root:
            sh("echo garnt_rejoin > /sys/power/wake_unlock", root=True)


# ----------------------------------------------------------------- packages / links
def prefixes(cfg):
    """package_prefix + extra_prefixes (e.g. 'aya.') as a tuple that str.startswith accepts."""
    out = [cfg.d["package_prefix"]] + list(cfg.d.get("extra_prefixes") or [])
    return tuple(dict.fromkeys(x.strip() for x in out if isinstance(x, str) and x.strip()))


def scan_packages(prefix):
    out = ""
    for root in ((True, False) if ENV.root else (False,)):
        for cmd in ("pm list packages", "cmd package list packages"):
            _, o = sh(cmd, root=root, timeout=40)
            if "package:" in o:
                out = o
                break
        if out:
            break
    pk = set()
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("package:"):
            name = line.split(":", 1)[1].strip()
            if name.startswith(prefix):
                pk.add(name)
    main = prefix[0] if isinstance(prefix, tuple) else prefix   # main prefix first, so existing cookie order is kept
    return sorted(pk, key=lambda p: (p != "com.roblox.client", not p.startswith(main), p))


def parse_link(link):
    """Return (uri, place_id) or None. Accepts place id, roblox:// link, /games/ link, private-server and share links."""
    s = (link or "").strip()
    if not s:
        return None
    if re.fullmatch(r"\d{3,}", s):
        return "roblox://placeID=" + s, s
    if s.lower().startswith(("roblox://", "robloxmobile://")):
        m = re.search(r"place[iI][dD]=(\d+)", s)
        return s, (m.group(1) if m else "")
    m = re.search(r"roblox\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?games/(\d+)", s)
    if m:
        pid = m.group(1)
        c = re.search(r"privateServerLinkCode=([\w-]+)", s)
        return "roblox://placeID=%s%s" % (pid, "&linkCode=" + c.group(1) if c else ""), pid
    if re.match(r"https?://(www\.)?(roblox\.com|ro\.blox\.com)/", s):
        return s, ""
    return None


def open_game(pkg, uri, force_stop):
    q = shlex.quote
    if ENV.root and force_stop:
        sh(AM() + " force-stop " + q(pkg), root=True, timeout=20)
        time.sleep(2)
    tail = "-a android.intent.action.VIEW -d %s -p %s -f 0x10008000" % (q(uri), q(pkg))
    rc, out = sh(AM() + " start " + tail, root=ENV.root, timeout=30)
    if rc != 0 or re.search(r"Error|Exception|unable to resolve", out, re.I):
        rc, out2 = sh("monkey -p %s -c android.intent.category.LAUNCHER 1" % q(pkg), root=ENV.root, timeout=30)
        if rc != 0:
            return False, scrub((out + out2).strip()[:200])
        return True, tr("เปิดแอปแล้วแต่ลิงก์เกมไม่ทำงาน (ใช้ launcher แทน)")
    return True, "ok"


def take_shot(pkg):
    if not ENV.root:
        return None
    name = "%s_%s.png" % (datetime.now().strftime("%Y%m%d_%H%M%S"), pkg.split(".")[-1])
    path = os.path.join(SHOT_DIR, name)
    sh("screencap -p " + shlex.quote(path), root=True, timeout=20)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        prune_shots()
        return path
    return None


def prune_shots():
    try:
        files = sorted((os.path.join(SHOT_DIR, f) for f in os.listdir(SHOT_DIR) if f.endswith(".png")),
                       key=os.path.getmtime, reverse=True)
        for f in files[MAX_SHOTS:]:
            os.remove(f)
    except OSError:
        pass


# ----------------------------------------------------------------- cookies / roblox api
COOKIE_TEMPLATE = (
    tr("# GARNT TOOL REJOIN - cookie.txt\n"
    "# ใส่คุกกี้ .ROBLOSECURITY ของบัญชีตัวเอง 1 บรรทัด / 1 บัญชี\n"
    "# เรียงตามอินสแตนซ์ที่ตรวจพบ (บรรทัดแรก = แพ็กเกจแรก) ห้ามแชร์ไฟล์นี้ให้ใคร\n"
    "# บรรทัดว่างและบรรทัดที่ขึ้นต้นด้วย # จะถูกข้าม\n"
    "# บรรทัดที่มีแค่ - = อินสแตนซ์นั้นยังไม่มีคุกกี้ (ใช้คงลำดับ)\n")
)


def clean_cookie(line):
    c = line.strip().strip("\"'").rstrip(";").strip()
    if c.upper().startswith(".ROBLOSECURITY="):
        c = c.split("=", 1)[1].strip().strip("\"'")
    return c


def ensure_cookie_file():
    if not os.path.exists(COOKIE_PATH):
        with open(COOKIE_PATH, "w", encoding="utf-8") as f:
            f.write(COOKIE_TEMPLATE)
        return True
    return False


def read_cookies():
    """One entry per cookie line; a line that is just '-' is a placeholder (no cookie for that instance) -> ''."""
    res = []
    try:
        with open(COOKIE_PATH, encoding="utf-8-sig") as f:
            for line in f:
                l = line.strip()
                if not l or l.startswith("#"):
                    continue
                res.append("" if l == "-" else clean_cookie(l))
    except OSError:
        return []
    SECRETS.update(c for c in res if c)
    return res


def cookie_pairs(cfg):
    """Map cookie lines to ALL detected instances in order (placeholder lines are skipped)."""
    ck = read_cookies()
    pairs = [(pkg, ck[i]) for i, pkg in enumerate(cfg.d["detected"]) if i < len(ck) and ck[i]]
    return pairs, len([c for c in ck if c])


UA = "Mozilla/5.0 (Linux; Android 12; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36"


def make_session(cookie):
    s = requests.Session()
    s.headers["User-Agent"] = UA
    s.cookies.set(".ROBLOSECURITY", cookie, domain=".roblox.com", path="/")
    return s


def who_am_i(s):
    """-> (user_id, name, error)"""
    try:
        r = s.get("https://users.roblox.com/v1/users/authenticated", timeout=12)
        if r.status_code == 401:
            return None, None, tr("คุกกี้หมดอายุ/ไม่ถูกต้อง")
        if r.status_code != 200:
            return None, None, "HTTP %d" % r.status_code
        j = r.json()
        name = j.get("name", "?")
        USERS.add(name)
        return j["id"], name, None
    except Exception as e:
        return None, None, scrub(e)[:80]


def in_game(s, uid):
    """True / False / None(unknown)"""
    url = "https://presence.roblox.com/v1/presence/users"
    try:
        r = s.post(url, json={"userIds": [uid]}, timeout=12)
        if r.status_code == 403 and "x-csrf-token" in r.headers:
            s.headers["X-CSRF-TOKEN"] = r.headers["x-csrf-token"]
            r = s.post(url, json={"userIds": [uid]}, timeout=12)
        if r.status_code != 200:
            return None
        return r.json()["userPresences"][0].get("userPresenceType") == 2
    except Exception:
        return None


def chrome_now():
    return int((time.time() + 11644473600) * 1000000)


def _edit_cookie_db(dbfile, cookie):
    con = sqlite3.connect(dbfile, timeout=10)
    try:
        cols = con.execute("PRAGMA table_info(cookies)").fetchall()
        if not cols:
            raise RuntimeError(tr("ไม่พบตาราง cookies"))
        now = chrome_now()
        vals = {
            "creation_utc": now, "host_key": ".roblox.com", "name": ".ROBLOSECURITY", "value": cookie,
            "encrypted_value": b"", "path": "/", "expires_utc": now + 400 * 86400 * 1000000,
            "is_secure": 1, "is_httponly": 1, "last_access_utc": now, "has_expires": 1,
            "is_persistent": 1, "persistent": 1, "priority": 1, "samesite": -1, "source_scheme": 2,
            "source_port": 443, "last_update_utc": now, "top_frame_site_key": "", "is_same_party": 0,
            "source_type": 0,
        }
        con.execute("DELETE FROM cookies WHERE name='.ROBLOSECURITY'")
        names, params = [], []
        for _cid, name, typ, notnull, dflt, _pk in cols:
            if name in vals:
                names.append(name)
                params.append(vals[name])
            elif notnull and dflt is None:
                t = (typ or "").upper()
                names.append(name)
                params.append(b"" if "BLOB" in t else ("" if ("TEXT" in t or "CHAR" in t) else 0))
        con.execute("INSERT INTO cookies (%s) VALUES (%s)" % (",".join(names), ",".join("?" * len(names))), params)
        con.commit()
    finally:
        con.close()


def inject_cookie(pkg, cookie):
    """Root only: write .ROBLOSECURITY into the cloned app's WebView cookie DB."""
    if not ENV.root:
        return False, tr("ต้องใช้สิทธิ์รูท")
    q = shlex.quote
    _, out = sh("find /data/data/%s -maxdepth 4 -type f -name Cookies 2>/dev/null | head -n 1" % q(pkg), root=True)
    path = out.strip().splitlines()[0].strip() if out.strip() else ""
    if not path:
        return False, tr("ไม่พบฐานข้อมูลคุกกี้ (เปิดแอปค้างไว้ 1 ครั้งก่อน)")
    _, own = sh("stat -c '%%u:%%g' /data/data/%s" % q(pkg), root=True)
    own = own.strip()
    if not re.fullmatch(r"\d+:\d+", own):
        return False, tr("อ่านเจ้าของโฟลเดอร์แอปไม่ได้")
    sh(AM() + " force-stop " + q(pkg), root=True)
    tmp = os.path.join(TMP_DIR, "garnt_%s.db" % pkg.replace(".", "_"))
    try:
        rc, o = sh("cp %s %s && chmod 666 %s" % (q(path), q(tmp), q(tmp)), root=True)
        if rc != 0:
            return False, tr("คัดลอกฐานข้อมูลไม่สำเร็จ")
        _edit_cookie_db(tmp, cookie)
        rc, o = sh("cp %s %s && chown %s %s && chmod 660 %s" % (q(tmp), q(path), own, q(path), q(path)), root=True)
        if rc != 0:
            return False, tr("เขียนฐานข้อมูลกลับไม่สำเร็จ")
        sh("rm -f %s-journal %s-wal %s-shm; restorecon %s" % (q(path), q(path), q(path), q(path)), root=True)
        return True, tr("ใส่คุกกี้แล้ว")
    except Exception as e:
        return False, scrub(e)[:100]
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def cookie_login(pairs):
    res = []
    for pkg, ck in pairs:
        uid, name, err = who_am_i(make_session(ck))
        if not uid:
            res.append((pkg, False, tr("คุกกี้ใช้ไม่ได้: %s") % err))
            continue
        ok, msg = inject_cookie(pkg, ck)
        res.append((pkg, ok, ("%s: %s" % (hide_name(name), msg)) if ok else msg))
    return res


def find_cookie_db(pkg):
    _, out = sh("find /data/data/%s -maxdepth 4 -type f -name Cookies 2>/dev/null | head -n 1" % shlex.quote(pkg), root=True)
    return out.strip().splitlines()[0].strip() if out.strip() else ""


def read_app_cookie(pkg):
    """Root only: read .ROBLOSECURITY from the clone's WebView cookie DB (works on a copy, never edits the app).
    -> (cookie | None, message)"""
    if not ENV.root:
        return None, tr("ต้องใช้สิทธิ์รูท")
    path = find_cookie_db(pkg)
    if not path:
        return None, tr("ไม่พบฐานข้อมูลคุกกี้ (เปิดแอปค้างไว้ 1 ครั้งก่อน)")
    tmpd = tempfile.mkdtemp(prefix="garnt_", dir=TMP_DIR)
    try:
        s, t = shlex.quote(path), shlex.quote(tmpd)
        rc, _ = sh("cp %s %s/ && chmod -R a+rw %s" % (s, t, t), root=True)
        if rc != 0:
            return None, tr("คัดลอกฐานข้อมูลไม่สำเร็จ")
        sh("cp %s-wal %s-shm %s-journal %s/ 2>/dev/null; chmod -R a+rw %s" % (s, s, s, t, t), root=True)
        con = sqlite3.connect(os.path.join(tmpd, os.path.basename(path)), timeout=10)
        try:
            con.text_factory = lambda b: b.decode("utf-8", "replace")
            sel = "SELECT value, encrypted_value FROM cookies WHERE name='.ROBLOSECURITY' AND host_key LIKE '%roblox.com'"
            try:
                rows = con.execute(sel + " ORDER BY last_access_utc DESC").fetchall()
            except sqlite3.OperationalError:
                rows = con.execute(sel).fetchall()
        finally:
            con.close()
        for val, enc in rows:
            val = (val or "").strip()
            if val:
                ck = clean_cookie(val)
                SECRETS.add(ck)
                return ck, "ok"
        if any(enc for _, enc in rows):
            return None, tr("คุกกี้ในแอปถูกเข้ารหัส อ่านไม่ได้")
        return None, tr("ไม่พบคุกกี้ (ยังไม่ได้ล็อกอินในแอปนี้)")
    except Exception as e:
        return None, scrub(e)[:100]
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)


def pull_validated(pkgs):
    """Read each clone's cookie and check it with the Roblox API. -> [(pkg, cookie|None, name|None, message)]"""
    out = []
    for pkg in pkgs:
        ck, msg = read_app_cookie(pkg)
        if not ck:
            out.append((pkg, None, None, msg))
            continue
        uid, name, err = who_am_i(make_session(ck))
        if uid:
            out.append((pkg, ck, name, ""))
        else:
            out.append((pkg, None, None, tr("คุกกี้ในแอปใช้ไม่ได้: ") + str(err)))
    return out


def store_cookies(cfg, found):
    """found = {pkg: cookie}. Updates cookie.txt in place: comments are kept, the line order follows the detected
    instances, and gaps are filled with '-'. Returns how many lines were written."""
    det = cfg.d["detected"]
    upd = {det.index(p): c for p, c in found.items() if p in det}
    if not upd:
        return 0
    try:
        with open(COOKIE_PATH, encoding="utf-8-sig") as f:
            raw = f.read().splitlines()
    except OSError:
        raw = COOKIE_TEMPLATE.splitlines()
    pos = [n for n, l in enumerate(raw) if l.strip() and not l.strip().startswith("#")]
    for i in sorted(upd):
        if i < len(pos):
            raw[pos[i]] = upd[i]
        else:
            while len(pos) < i:
                raw.append("-")
                pos.append(len(raw) - 1)
            raw.append(upd[i])
            pos.append(len(raw) - 1)
    tmp = COOKIE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(raw) + "\n")
    os.replace(tmp, COOKIE_PATH)
    try:
        os.chmod(COOKIE_PATH, 0o600)
    except OSError:
        pass
    SECRETS.update(upd.values())
    return len(upd)


# ----------------------------------------------------------------- discord webhook
HOOK_RE = re.compile(r"^https://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+$")
EV_COLOR = {"status": 0xF1C40F, "start": 0x2ECC71, "crash": 0xE74C3C, "rejoin": 0x3498DB, "stop": 0x95A5A6, "test": 0x9B59B6, "update": 0xF39C12, "alert": 0xE67E22}


class Hook:
    def __init__(self, cfg):
        self.cfg = cfg

    def send(self, event, title, desc, fields=None, shot=None, sync=False):
        w = self.cfg.d["webhook"]
        if event != "test" and not (w["enabled"] and w["events"].get(event)):
            return True
        if not w["url"]:
            return False
        embed = {"title": scrub(title), "description": scrub(desc), "color": EV_COLOR.get(event, 0),
                 "timestamp": datetime.now(timezone.utc).isoformat(), "footer": {"text": "GARNT TOOL REJOIN"},
                 "fields": [{"name": scrub(k), "value": scrub(v) or "-", "inline": True} for k, v in (fields or [])]}
        if shot and not (w["send_screenshot"] and os.path.exists(shot)):
            shot = None
        args = (w["url"], {"username": "GARNT TOOL REJOIN", "embeds": [embed]}, shot)
        if sync:
            return self._post(*args)
        threading.Thread(target=self._post, args=args, daemon=True).start()
        return True

    def _post(self, url, payload, shot):
        try:
            for _ in range(2):
                if shot:
                    name = os.path.basename(shot)
                    payload["embeds"][0]["image"] = {"url": "attachment://" + name}
                    with open(shot, "rb") as fh:
                        r = requests.post(url, data={"payload_json": json.dumps(payload)},
                                          files={"file": (name, fh, "image/png")}, timeout=25)
                else:
                    r = requests.post(url, json=payload, timeout=15)
                if r.status_code == 429:
                    time.sleep(min(float(r.json().get("retry_after", 2)), 10))
                    continue
                if r.status_code >= 300:
                    log.warning("webhook HTTP %s", r.status_code)
                    return False
                return True
        except Exception as e:
            log.warning(tr("webhook ล้มเหลว: %s"), scrub(e))
        return False


# ----------------------------------------------------------------- heartbeat (in-game script -> local receiver)
LUA_TEMPLATE = r"""-- GARNT TOOL REJOIN heartbeat (auto-generated)
-- ใช้กับบัญชีและเครื่องของตัวเองเท่านั้น ต้องรันผ่านเอ็กซิคิวเตอร์ที่รองรับ HTTP
local HOST     = "http://127.0.0.1:__PORT__"
local ID       = "__ID__"       -- ชื่อแพ็กเกจหรือเลขอินสแตนซ์ ต้องตรงกับในเครื่องมือ
local INTERVAL = __INTERVAL__   -- ส่งทุกกี่วินาที
local SHOW_FPS = __SHOWFPS__    -- true = แสดงตัวเลข FPS บนจอ (ลากย้ายได้)
local DEFAULT_CAP = __DEFCAP__  -- FPS cap ของบัญชีที่ไม่อยู่ในรายการ (0 = ไม่แตะ)
local CAPS = {                  -- FPS cap ต่อบัญชี: [Roblox UserId] = FPS (ตั้งจากเมนูในเครื่องมือ)
__CAPS__
}

if getgenv then
    if getgenv().__GARNT_HB then return end
    getgenv().__GARNT_HB = true
end

if not game:IsLoaded() then game.Loaded:Wait() end
local Players = game:GetService("Players")
local RunService = game:GetService("RunService")
local HttpService = game:GetService("HttpService")
local GuiService = game:GetService("GuiService")
local UserInputService = game:GetService("UserInputService")

local lp = Players.LocalPlayer
while not lp do task.wait(1); lp = Players.LocalPlayer end

-- FPS cap: ดู UserId ของบัญชีที่กำลังเล่นอยู่ แล้วตั้งเพดานตามที่ตั้งไว้ (ต้องมี setfpscap ในเอ็กซิคิวเตอร์)
-- capInfo ถูกส่งไปกับ heartbeat (cap=) และแสดงในคอนโซล: off=ไม่ได้ตั้งให้ ID นี้ nofn=ไม่มีฟังก์ชัน err=เรียกแล้ว error
--   noeffNN=เรียกแล้วแต่ FPS จริงยังเป็น NN (ไม่ติด) setNN=ตั้งแล้ว
local capInfo = "off"

local CAP_NAMES = { "setfpscap", "set_fps_cap", "setfpslimit", "set_fps_limit", "setrobloxfps", "setfps" }
local fps = 0                                          -- FPS จริงที่วัดเอง (อัปเดตจาก Heartbeat ด้านล่าง) ไม่เชื่อ getfpscap เพราะบางตัวรันอ่านค่าเพี้ยน
local capSetAt, capVal = 0, 0

-- รวบ env ที่เอ็กซิคิวเตอร์ต่างค่ายชอบซ่อนฟังก์ชันไว้: getgenv, _G, getfenv, และตาราง syn/fluxus/krnl
local function scanEnvs()
    local envs = {}
    local function add(t) if type(t) == "table" then table.insert(envs, t) end end
    if getgenv then pcall(function() add(getgenv()) end) end
    add(_G)
    if getfenv then pcall(function() add(getfenv(0)) end); pcall(function() add(getfenv(1)) end) end
    local base = #envs
    for i = 1, base do
        for _, n in ipairs({ "syn", "fluxus", "krnl", "Krnl", "http" }) do
            local ok, t = pcall(function() return envs[i][n] end)
            if ok then add(t) end
        end
    end
    return envs
end

local function findCapFn()
    for _, n in ipairs(CAP_NAMES) do
        local ok, f = pcall(function() return (getfenv and getfenv()[n]) or _G[n] end)
        if ok and type(f) == "function" then return f end
    end
    for _, env in ipairs(scanEnvs()) do
        for _, n in ipairs(CAP_NAMES) do
            local ok, f = pcall(function() return env[n] end)
            if ok and type(f) == "function" then return f end
        end
    end
    return nil
end

local function applyCap()
    local cap = CAPS[lp.UserId] or DEFAULT_CAP
    if type(cap) ~= "number" or cap <= 0 then capInfo = "off"; return end
    local fn = findCapFn()
    if not fn then capInfo = "nofn"; return end
    if not pcall(fn, cap) then capInfo = "err"; return end
    if capVal ~= cap then capVal, capSetAt = cap, os.clock() end
    -- ตรวจจาก FPS จริง หลังตั้งไปแล้ว 5 วินาทีขึ้นไป: ถ้ายังสูงกว่าเพดานมาก = ตัวรันรับคำสั่งแต่ไม่ทำจริง
    -- (ถ้าเครื่องช้ากว่า cap อยู่แล้ว แยกไม่ออกว่า cap ติดหรือไม่ จะขึ้น set ตามปกติ)
    if fps > 0 and os.clock() - capSetAt >= 5 and fps > cap * 1.5 + 2 then
        capInfo = "noeff" .. tostring(fps)
    else
        capInfo = "set" .. tostring(cap)
    end
end
pcall(applyCap)

local lastFrame = os.clock()
local frames, winStart = 0, os.clock()
RunService.Heartbeat:Connect(function()
    local now = os.clock()
    lastFrame = now
    frames = frames + 1
    if now - winStart >= 1 then
        fps = math.floor(frames / (now - winStart) + 0.5)
        frames, winStart = 0, now
    end
end)

local function httpGet(url)
    local req = (syn and syn.request) or http_request or request or (http and http.request)
    if req then
        local ok = pcall(req, { Url = url, Method = "GET" })
        if ok then return true end
    end
    return (pcall(function() return game:HttpGet(url) end))
end

local function errorText()
    local ok, msg = pcall(function() return GuiService:GetErrorMessage() end)
    if ok and type(msg) == "string" and msg ~= "" then return msg end
    return nil
end

local function beat()
    if os.clock() - lastFrame > 5 then return end   -- เกมค้าง: ไม่ส่ง
    local url = string.format("%s/hb?id=%s&user=%s&uid=%d&place=%d&job=%s&fps=%d", HOST,
        HttpService:UrlEncode(ID), HttpService:UrlEncode(lp.Name), lp.UserId, game.PlaceId,
        HttpService:UrlEncode(game.JobId), fps)
    local err = errorText()                          -- ขึ้นหน้าต่าง Disconnected: ส่งข้อความ error ทันที
    if err then url = url .. "&err=" .. HttpService:UrlEncode(string.sub(err, 1, 120)) end
    url = url .. "&cap=" .. capInfo
    httpGet(url)
end

pcall(function() GuiService.ErrorMessageChanged:Connect(function() pcall(beat) end) end)

task.spawn(function()
    while true do
        pcall(applyCap)                              -- ตั้งซ้ำทุกรอบ เผื่อเกม/เอ็กซิคิวเตอร์รีเซ็ตค่า
        pcall(beat)
        task.wait(INTERVAL)
    end
end)

-- ตัวเลข FPS: ไม่มีกรอบ ฟอนต์กลม ขนาดปรับตามความกว้างจออัตโนมัติ ลากไปวางที่ไหนก็ได้
-- (จำตำแหน่งไว้ถ้าเอ็กซิคิวเตอร์รองรับ writefile)
local POS_FILE = "garnt_fps_pos.txt"

local function parentGui(gui)
    local ok = false
    if gethui then ok = pcall(function() gui.Parent = gethui() end) end
    if not ok or not gui.Parent then
        ok = pcall(function() gui.Parent = game:GetService("CoreGui") end)
    end
    if not ok or not gui.Parent then
        local pg = lp:FindFirstChildOfClass("PlayerGui") or lp:WaitForChild("PlayerGui", 10)
        if pg then gui.Parent = pg end
    end
    return gui.Parent ~= nil
end

local function loadPos()
    if not (isfile and readfile) then return nil end
    local ok, data = pcall(function() if isfile(POS_FILE) then return readfile(POS_FILE) end end)
    if ok and type(data) == "string" then
        local x, y = string.match(data, "^(%-?%d+),(%-?%d+)$")
        if x then return tonumber(x), tonumber(y) end
    end
    return nil
end

local function savePos(x, y)
    if writefile then pcall(writefile, POS_FILE, string.format("%d,%d", x, y)) end
end

local function roundFont()
    for _, name in ipairs({ "FredokaOne", "Nunito", "GothamBold" }) do   -- ตัวกลมก่อน ถ้าไม่มีค่อยถอยไปตัวถัดไป
        local ok, f = pcall(function() return Enum.Font[name] end)
        if ok and f then return f end
    end
    return Enum.Font.SourceSansBold
end

local function buildFpsBox()
    local gui = Instance.new("ScreenGui")
    gui.Name = "GARNT_FPS"
    gui.ResetOnSpawn = false
    gui.IgnoreGuiInset = true
    gui.DisplayOrder = 999999
    if not parentGui(gui) then return end

    local box = Instance.new("Frame")
    box.Name = "Box"
    box.BackgroundColor3 = Color3.fromRGB(0, 0, 0)       -- พื้นดำ ไม่มีกรอบ
    box.BackgroundTransparency = 0.25
    box.BorderSizePixel = 0
    box.Active = true
    box.Parent = gui

    local corner = Instance.new("UICorner")
    corner.CornerRadius = UDim.new(1, 0)                 -- ขอบมนเต็มที่
    corner.Parent = box

    local label = Instance.new("TextLabel")
    label.BackgroundTransparency = 1
    label.Size = UDim2.new(1, 0, 1, 0)
    label.Font = roundFont()
    label.TextScaled = true
    label.TextColor3 = Color3.fromRGB(255, 255, 255)
    label.Text = "FPS --"
    label.Parent = box

    local pad = Instance.new("UIPadding")
    pad.PaddingLeft = UDim.new(0.12, 0)
    pad.PaddingRight = UDim.new(0.12, 0)
    pad.PaddingTop = UDim.new(0.14, 0)
    pad.PaddingBottom = UDim.new(0.14, 0)
    pad.Parent = label

    local W, H = 72, 28
    local function viewport()
        local cam = workspace.CurrentCamera
        return (cam and cam.ViewportSize) or Vector2.new(800, 600)
    end

    local function clamp(x, y)
        local vp = viewport()
        return math.clamp(x, 0, math.max(0, vp.X - W)), math.clamp(y, 0, math.max(0, vp.Y - H))
    end

    local function layout()                              -- ขนาดตามความกว้างจอ: เล็กกระทัดรัดแต่ยังอ่านออก
        local vp = viewport()
        W = math.clamp(math.floor(vp.X * 0.055), 56, 110)
        H = math.floor(W * 0.4)
        box.Size = UDim2.fromOffset(W, H)
        local x, y = clamp(box.Position.X.Offset, box.Position.Y.Offset)
        box.Position = UDim2.fromOffset(x, y)
    end

    local sx, sy = loadPos()
    if not sx then sx, sy = 12, 64 end
    box.Position = UDim2.fromOffset(sx, sy)
    layout()

    local cam = workspace.CurrentCamera
    if cam then
        cam:GetPropertyChangedSignal("ViewportSize"):Connect(function() pcall(layout) end)   -- หมุนจอ/ย่อขยายหน้าต่าง
    end

    local dragging, dragStart, startX, startY = false, nil, 0, 0
    box.InputBegan:Connect(function(input)
        local t = input.UserInputType
        if t == Enum.UserInputType.MouseButton1 or t == Enum.UserInputType.Touch then
            dragging = true
            dragStart = input.Position
            startX, startY = box.Position.X.Offset, box.Position.Y.Offset
            input.Changed:Connect(function()
                if input.UserInputState == Enum.UserInputState.End then
                    dragging = false
                    savePos(box.Position.X.Offset, box.Position.Y.Offset)
                end
            end)
        end
    end)
    UserInputService.InputChanged:Connect(function(input)
        if not dragging then return end
        local t = input.UserInputType
        if t == Enum.UserInputType.MouseMovement or t == Enum.UserInputType.Touch then
            local x, y = clamp(startX + (input.Position.X - dragStart.X), startY + (input.Position.Y - dragStart.Y))
            box.Position = UDim2.fromOffset(x, y)
        end
    end)

    while gui.Parent do
        label.Text = (fps > 0) and ("FPS " .. tostring(fps)) or "FPS --"
        task.wait(0.5)
    end
end

if SHOW_FPS then
    task.spawn(function() pcall(buildFpsBox) end)
end
"""


def cap_txt(rec):
    """Short status of the in-game FPS cap, from the heartbeat record."""
    c = rec.get("cap", "")
    if not c:
        return ""
    num = "".join(ch for ch in c if ch.isdigit())
    if c == "off":
        return tr(" | cap: ไม่ได้ตั้งให้ ID นี้ (%s)") % hide_id(rec.get("uid", ""))
    if c == "nofn":
        return tr(" | cap: เอ็กซิคิวเตอร์ไม่มี setfpscap")
    if c == "err":
        return tr(" | cap: เรียกแล้ว error")
    if c.startswith("noeff"):
        return tr(" | cap: ตั้งแล้วแต่ไม่ติด (FPS จริง %s ตัวรันอาจไม่รองรับหรือมีตั้งค่าทับ)") % num
    if c.startswith("ign"):
        return tr(" | cap: เรียกแล้วแต่ไม่ติด (อ่านกลับได้ %s)") % num
    if c.startswith("set"):
        return " | cap %s ✓" % num
    return ""


def fps_caps(cfg):
    """-> (default_cap, {uid: cap}) from config; invalid entries are dropped."""
    fc = cfg.d["heartbeat"].get("fps_cap", {})
    try:
        default = max(0, min(int(fc.get("default", 0)), FPS_CAP_MAX))
    except (TypeError, ValueError):
        default = 0
    caps = {}
    for uid, rec in (fc.get("accounts") or {}).items():
        try:
            cap = int(rec.get("cap", 0) if isinstance(rec, dict) else rec)
            if str(uid).isdigit() and FPS_CAP_MIN <= cap <= FPS_CAP_MAX:
                caps[int(uid)] = cap
        except (TypeError, ValueError):
            continue
    return default, caps


def render_lua(inst_id, port, interval, show_fps=True, default_cap=0, caps=None):
    rows = ",\n".join("    [%d] = %d" % (u, c) for u, c in sorted((caps or {}).items()))
    return (tr_lua(LUA_TEMPLATE).replace("__PORT__", str(int(port))).replace("__INTERVAL__", str(int(interval)))
            .replace("__SHOWFPS__", "true" if show_fps else "false")
            .replace("__DEFCAP__", str(int(default_cap)))
            .replace("__CAPS__", rows + ("," if rows else ""))
            .replace("__ID__", re.sub(r"[^\w.\-]", "", str(inst_id))))


def write_heartbeat_files(cfg):
    h = cfg.d["heartbeat"]
    d = os.path.join(BASE, "heartbeat")
    os.makedirs(d, exist_ok=True)
    det = cfg.d["detected"] or ["1"]
    paths = []
    for i, pkg in enumerate(det, 1):
        p = os.path.join(d, "heartbeat_%d_%s.lua" % (i, pkg.split(".")[-1]))
        with open(p, "w", encoding="utf-8") as f:
            f.write(render_lua(pkg, h["port"], h["interval"], h.get("show_fps", True), *fps_caps(cfg)))
        paths.append(p)
    return paths


HB_MARK = "-- GARNT TOOL REJOIN heartbeat"


def peer_uid(client_port, server_port):
    """Root can read /proc/net/tcp: find which Android UID owns the loopback connection that is calling us."""
    for f in ("/proc/net/tcp6", "/proc/net/tcp"):
        try:
            with open(f) as fh:
                next(fh, None)
                for line in fh:
                    p = line.split()
                    if len(p) > 7 and int(p[1].rsplit(":", 1)[1], 16) == client_port \
                            and int(p[2].rsplit(":", 1)[1], 16) == server_port:
                        return int(p[7])
        except (OSError, ValueError):
            continue
    return None


def uid_packages():
    def run():
        _, out = sh("pm list packages -U", root=ENV.root, timeout=40)
        d = {}
        for l in out.splitlines():
            m = re.match(r"package:([\w.]+)\s+uid:(\d+)", l.strip())
            if m:
                d[int(m.group(2))] = m.group(1)
        return d
    return cached("uidmap", 600, run)


def autoexec_targets(cfg):
    out = []
    for d in cfg.d["autoexec"]["dirs"]:
        if "{pkg}" in d:
            out += [(d.replace("{pkg}", p), p) for p in cfg.d["detected"]]
        else:
            out.append((d, "auto"))   # shared folder: one script for every clone (id found from the caller's UID)
    return out


def install_autoexec(cfg):
    """Create/refresh heartbeat.lua in the executor's autoexec folder(s). -> [(path, created|updated|ok|skipped|error)]"""
    a, h = cfg.d["autoexec"], cfg.d["heartbeat"]
    res = []
    for d, ident in autoexec_targets(cfg):
        if d in KNOWN_EXEC_DIRS and not os.path.isdir(os.path.dirname(d)):
            continue                                   # executor not installed: don't litter /sdcard with its folder
        path = os.path.join(d, a["filename"])
        src = render_lua(ident, h["port"], h["interval"], h.get("show_fps", True), *fps_caps(cfg))
        try:
            old = None
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    old = f.read()
                if not old.startswith(HB_MARK):
                    res.append((path, "skipped"))      # someone else's file: never overwrite
                    continue
                if old == src:
                    res.append((path, "ok"))
                    continue
            os.makedirs(d, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(src)
            res.append((path, "created" if old is None else "updated"))
        except OSError as e:
            res.append((path, "error: %s" % e))
    return res


def report_autoexec(cfg, results, quiet_ok=True):
    names = {"created": tr("สร้างแล้ว"), "updated": tr("อัปเดตแล้ว"), "ok": tr("พร้อมใช้งาน"),
             "skipped": tr("ข้าม (มีไฟล์ชื่อเดียวกันที่ไม่ใช่ของเครื่องมือ)")}
    for path, st in results:
        if st == "ok" and quiet_ok:
            continue
        say("heartbeat.lua: %s → %s" % (names.get(st, st), path), "red" if st.startswith("error") else "green" if st != "skipped" else "yellow")
    if any(st == "created" for _, st in results) and not cfg.d["autoexec"]["enabled_once"]:
        cfg.d["autoexec"]["enabled_once"] = True
        cfg.d["heartbeat"]["enabled"] = True       # first install only: turn heartbeat on (later changes are yours)
        cfg.save()
        say(tr("เปิดใช้ heartbeat ให้แล้ว (ปิดได้ที่ เมนู 6 > Heartbeat > 1)"), "cyan")


class Heartbeats:
    def __init__(self):
        self.lock = threading.Lock()
        self.d, self.by_user, self.single = {}, {}, False

    def put(self, q, peer_pkg=None):
        key = peer_pkg or re.sub(r"[^\w.\-]", "", q.get("id", ""))[:64]
        if not key:
            return False
        rec = {"ts": time.time(), "user": re.sub(r"[^\w .\-]", "", q.get("user", ""))[:30],
               "uid": q.get("uid", "")[:20], "place": q.get("place", "")[:20], "job": q.get("job", "")[:64],
               "err": re.sub(r"[^\w .:()\-]", "", q.get("err", ""))[:120],
               "fps": re.sub(r"\D", "", q.get("fps", ""))[:4],
               "cap": re.sub(r"[^A-Za-z0-9]", "", q.get("cap", ""))[:12]}
        with self.lock:
            self.d[key] = rec
            if rec["user"]:
                self.by_user[rec["user"]] = rec
                USERS.add(rec["user"])
        return True

    def latest(self, inst):
        """Look up by id in strict order: pkg -> idx -> user -> auto (single instance only).
        No more max(ts) across keys, so one clone can't steal another clone's heartbeat."""
        with self.lock:
            order = [self.d.get(inst.pkg), self.d.get(str(inst.idx))]
            if inst.user:
                order.append(self.by_user.get(inst.user))
            if self.single:
                order.append(self.d.get("auto"))
            recs = [r for r in order if r]
        for r in recs:                      # first id in order that is fresh since the last launch
            if r["ts"] >= inst.launched_at:
                return r
        return recs[0] if recs else None    # none fresh: first match, hb_state() will call it stale


class HBServer:
    """Tiny local-only HTTP receiver: GET /hb?id=..&user=..&uid=..&place=..&job=.. and GET /ping."""

    def __init__(self, store, port, prefix="com.roblox."):
        self.store, self.port, self.httpd, self.prefix = store, int(port), None, prefix

    def start(self):
        store, prefix = self.store, self.prefix

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                u = urlparse(self.path)
                code, body = 404, b"not found"
                if u.path == "/ping":
                    code, body = 200, b"pong"
                elif u.path == "/hb":
                    q = {k: v[0][:80] for k, v in parse_qs(u.query).items() if v}
                    pkg = None
                    uid = peer_uid(self.client_address[1], self.server.server_address[1])
                    if uid is not None:
                        pkg = uid_packages().get(uid)
                        if pkg and not pkg.startswith(prefix):
                            pkg = None
                    code, body = (200, b"ok") if store.put(q, pkg) else (400, b"missing id")
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), H)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()


# ----------------------------------------------------------------- console webhook (mirrors the console log)
class ConsoleHook:
    """Buffers console/event lines and posts them to Discord in batches (code block, <= 1900 chars)."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.q = deque(maxlen=300)
        self.lock = threading.Lock()
        self.stop = threading.Event()

    def url(self):
        c = self.cfg.d["console_webhook"]
        return c["url"] or self.cfg.d["webhook"]["url"]

    def active(self):
        return bool(self.cfg.d["console_webhook"]["enabled"] and self.url())

    def push(self, line):
        if self.active():
            with self.lock:
                self.q.append(scrub(line)[:300])

    def start(self):
        if self.active():
            threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while not self.stop.wait(max(3, int(self.cfg.d["console_webhook"]["flush_sec"]))):
            self.flush()

    def flush(self):
        with self.lock:
            lines = list(self.q)
            self.q.clear()
        if not lines or not self.url():
            return True
        ok, chunk, size = True, [], 0
        for ln in lines + [None]:
            if ln is None or size + len(ln) + 1 > 1850:
                if chunk:
                    ok = self._post("\n".join(chunk)) and ok
                chunk, size = [], 0
            if ln is not None:
                chunk.append(ln)
                size += len(ln) + 1
        return ok

    def _post(self, text):
        try:
            for _ in range(2):
                r = requests.post(self.url(), json={"username": "GARNT CONSOLE", "content": "```\n%s\n```" % text}, timeout=15)
                if r.status_code == 429:
                    time.sleep(min(float(r.json().get("retry_after", 2)), 10))
                    continue
                return r.status_code < 300
        except Exception as e:
            log.warning(tr("console webhook ล้มเหลว: %s"), scrub(e))
        return False

    def close(self):
        self.stop.set()
        self.flush()


# ----------------------------------------------------------------- raw keyboard input + live loop
class RawInput:
    """cbreak keyboard reader: no echo, no Enter needed for polling; Ctrl+C still raises KeyboardInterrupt."""

    def __init__(self):
        self.fd, self.old, self.buf, self.dirty, self._cr = None, None, "", False, False

    def __enter__(self):
        if termios and sys.stdin.isatty():
            try:
                self.fd = sys.stdin.fileno()
                self.old = termios.tcgetattr(self.fd)
                tty.setcbreak(self.fd)
            except Exception:
                self.fd = None
        return self

    def __exit__(self, *a):
        if self.fd is not None and self.old is not None:
            try:
                termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old)
            except Exception:
                pass

    def feed(self, txt):
        """Process typed text -> list of completed lines (may include "" for a bare Enter)."""
        lines, i = [], 0
        self._cr = False   # CR+LF is merged only inside one chunk
        while i < len(txt):
            ch = txt[i]
            if ch == "\x1b":  # skip arrow keys / other escape sequences
                m = re.match(r"\x1b(\[[0-9;?]*[ -/]*[@-~]|O.)", txt[i:])
                i += len(m.group(0)) if m else 1
                continue
            if ch in "\r\n":
                if not (ch == "\n" and self._cr):
                    lines.append(self.buf)
                    self.buf = ""
                self._cr = ch == "\r"
            else:
                self._cr = False
                if ch in "\x7f\b":
                    self.buf = self.buf[:-1]
                elif ch == "\x15":
                    self.buf = ""
                elif ch.isprintable() and len(self.buf) < 120:
                    self.buf += ch
            self.dirty = True
            i += 1
        return lines

    def poll(self, timeout):
        if self.fd is None:
            time.sleep(timeout)
            return []
        r, _, _ = select.select([self.fd], [], [], timeout)
        if not r:
            return []
        return self.feed(os.read(self.fd, 256).decode("utf-8", "ignore"))


def run_live(render, handler, refresh_sec, inp=None, once=False):
    """Live panel + typed commands. render(buf) -> renderable; handler(line) may return "quit"."""
    refresh_sec = max(0.5, float(refresh_sec))
    with (inp or RawInput()) as ri, Live(console=console, auto_refresh=False, screen=True, vertical_overflow="crop") as live:
        live.update(render(ri.buf), refresh=True)
        if once and getattr(ri, "fd", 1) is None:   # no keyboard (not a terminal): show once and return
            return
        last = time.time()
        while True:
            lines = ri.poll(refresh_sec)
            for ln in lines:
                if handler(ln) == "quit":
                    live.update(render(ri.buf), refresh=True)
                    return
            if lines or ri.dirty or time.time() - last >= refresh_sec:
                ri.dirty = False
                live.update(render(ri.buf), refresh=True)
                last = time.time()


def sys_load():
    try:
        with open("/proc/loadavg") as f:
            return float(f.read().split()[0])
    except Exception:
        return None


CONSOLE_HELP = (tr("คำสั่ง: rejoin <เลข|all> | pause <เลข|all> | resume <เลข|all> | status | ram | shot | streamer | help | quit"))


# ----------------------------------------------------------------- engine
class Inst:
    def __init__(self, idx, pkg, cookie=None):
        self.idx, self.pkg, self.cookie = idx, pkg, cookie
        self.label = "#%d %s" % (idx, pkg)
        self.session = None
        self.uid = None
        self.user = ""
        self.cookie_bad = False
        self.pres_at, self.pres_val = 0.0, None
        self.wake, self.cmd, self.paused = threading.Event(), "", False
        self.state, self.detail = "queued", ""
        self.fails = self.rejoins = 0
        self.launched_at = 0.0
        self.ok_since = None
        self.next_at = 0.0
        self.started_at = time.time()
        self.fail_ts = deque(maxlen=50)   # timestamps of recent failures (for rejoin-storm alert)
        self.alert_at = 0.0


REASON = {"dead": tr("แอปปิด/ค้าง (ไม่พบโปรเซส)"), "background": tr("แอปไม่ได้อยู่หน้าจอ"),
          "noheartbeat": tr("ไม่มี heartbeat จากสคริปต์ในเกม"),
          "disconnected": tr("พบหน้าต่าง Disconnected (เช่น Error Code 277)"),
          "notingame": tr("ไม่ได้อยู่ในเกม"), "timed": tr("รีจอยตามเวลาที่ตั้งไว้")}
STATE_LABEL = {"queued": (tr("รอคิวเปิด"), "dim"), "launching": (tr("กำลังเปิดแอป"), "cyan"),
            "joining": (tr("กำลังเข้าเกม"), "yellow"), "ok": (tr("ปกติ"), "bold green"),
            "unknown": (tr("ไม่ทราบสถานะ"), "magenta"), "backoff": (tr("รอรีจอย"), "bold red"),
            "stopped": (tr("หยุดแล้ว"), "dim"), "paused": (tr("หยุดชั่วคราว"), "yellow")}


def fmt_dur(sec):
    sec = int(max(0, sec))
    return "%02d:%02d:%02d" % (sec // 3600, sec % 3600 // 60, sec % 60)


class Engine:
    MIN = 60        # seconds per "minute" (overridable for tests)
    HK_TICK = 30    # housekeeping resolution in seconds

    def __init__(self, cfg, hook, uri, instances, hb=None, chook=None):
        self.cfg, self.hook, self.uri, self.insts, self.hb = cfg, hook, uri, instances, hb
        self.chook = chook
        self.stop = threading.Event()
        self.gate = threading.Lock()
        self.last_launch = 0.0
        self.events = deque(maxlen=10)
        self.threads = []
        self.t0 = time.time()
        self.last_ram_alert = 0.0

    # -- helpers
    def event(self, msg, style=""):
        msg = scrub(msg)
        ts = datetime.now().strftime("%H:%M:%S")
        self.events.append((ts, msg, style))
        log.info(msg)
        if self.chook:
            self.chook.push("%s %s" % (ts, msg))

    def interval(self):
        return max(10, int(self.cfg.d["check_interval"]))

    def start(self):
        self.event(tr("เริ่มทำงาน %d อินสแตนซ์ (โหมด %s)") % (len(self.insts), "ROOT" if ENV.root else tr("ไม่รูท")), "green")
        self.hook.send("start", tr("▶️ เริ่มรีจอยอัตโนมัติ"), tr("เริ่มจัดการ %d อินสแตนซ์") % len(self.insts),
                       [(tr("โหมด"), "ROOT" if ENV.root else tr("ไม่รูท")), (tr("ตรวจทุก"), "%ds" % self.interval()),
                        (tr("แพ็กเกจ"), ", ".join(i.pkg for i in self.insts))])
        SNAP["fg"] = bool(ENV.root and (self.cfg.d["auto"]["foreground_check"]))
        for target, args in [(self._worker, (i,)) for i in self.insts] + [(self._housekeeper, ())]:
            t = threading.Thread(target=target, args=args, daemon=True)
            t.start()
            self.threads.append(t)

    def shutdown(self):
        self.stop.set()
        for i in self.insts:
            i.wake.set()
        for t in self.threads:
            t.join(timeout=5)
        wake_lock(False)
        SNAP["fg"] = False
        up = fmt_dur(time.time() - self.t0)
        self.event(tr("หยุดการทำงาน (รันมา %s)") % up)
        self.hook.send("stop", tr("⏹️ หยุดรีจอยอัตโนมัติ"), tr("รันมาแล้ว ") + up,
                       [(i.pkg, tr("รีจอย %d ครั้ง") % i.rejoins) for i in self.insts], sync=True)

    def _next_tick(self):
        """All instances are checked on the same time grid so one snapshot serves every instance."""
        iv = self.interval()
        return self.t0 + (int((time.time() - self.t0) // iv) + 1) * iv

    # -- housekeeping: RAM clearing + periodic status report with screenshot
    def _housekeeper(self):
        c = self.cfg.d
        last_ram = last_rep = time.time()
        while not self.stop.wait(self.HK_TICK):
            now = time.time()
            r = c["ram"]
            if r["enabled"] and ENV.root:
                free = mem_available_mb()
                due = r["interval_min"] > 0 and now - last_ram >= r["interval_min"] * self.MIN
                low = bool(r["min_free_mb"] > 0 and free is not None and free < r["min_free_mb"] and now - last_ram >= self.MIN)
                if due or low:
                    self.clear_ram(tr("แรมว่างต่ำ") if low else tr("ตามเวลา"))
                    last_ram = time.time()
            try:
                self._check_low_ram()
            except Exception as e:
                log.debug("check low ram: %s", e)
            pm = int(c["webhook"]["periodic_min"])
            if pm > 0 and now - last_rep >= pm * self.MIN:
                last_rep = now
                try:
                    self.report(pm)
                except Exception as e:
                    self.event(tr("ส่งรายงานสถานะไม่สำเร็จ: %s") % e, "red")

    def _alert_cfg(self):
        a = self.cfg.d.get("alert", {})
        return a if a.get("enabled") else None

    def _cool(self):
        try:
            return max(1, int(self.cfg.d["alert"]["cooldown_min"])) * self.MIN
        except (KeyError, TypeError, ValueError):
            return 30 * self.MIN

    def _check_storm(self, inst):
        """Failures of one instance inside a time window >= limit -> one webhook alert (then cooldown)."""
        a = self._alert_cfg()
        now = time.time()
        inst.fail_ts.append(now)
        if not a:
            return
        try:
            limit = min(50, max(2, int(a["storm_count"])))
            win = max(1, int(a["storm_window_min"])) * self.MIN
        except (KeyError, TypeError, ValueError):
            return
        n = sum(1 for x in inst.fail_ts if now - x <= win)
        if n >= limit and now - inst.alert_at >= self._cool():
            inst.alert_at = now
            self.event(tr("%s: หลุด/ค้าง %d ครั้งใน %d นาที (ผิดปกติ)") % (inst.label, n, win // self.MIN), "bold red")
            self.hook.send("alert", tr("🚨 รีจอยถี่ผิดปกติ"), inst.label,
                           [(tr("แพ็กเกจ"), inst.pkg), (tr("บัญชี"), inst.user or "-"),
                            (tr("หลุดในช่วงเวลา"), tr("%d ครั้ง / %d นาที") % (n, win // self.MIN))])

    def _check_low_ram(self):
        a = self._alert_cfg()
        if not a:
            return
        try:
            lim = int(a.get("low_ram_mb") or 0) or int(self.cfg.d["ram"]["min_free_mb"])
        except (TypeError, ValueError):
            return
        free = mem_available_mb()
        now = time.time()
        if lim > 0 and free is not None and free < lim and now - self.last_ram_alert >= self._cool():
            self.last_ram_alert = now
            self.event(tr("แรมว่างต่ำ %d MB (เกณฑ์ %d MB)") % (free, lim), "bold red")
            self.hook.send("alert", tr("🚨 แรมว่างต่ำ"), tr("แรมว่าง %d MB ต่ำกว่าเกณฑ์ %d MB") % (free, lim),
                           [(tr("แรมว่าง"), "%d MB" % free), (tr("เกณฑ์"), "%d MB" % lim)])

    def clear_ram(self, why):
        ok, before, after = drop_ram(self.cfg.d["ram"]["kill_cached"], self.cfg)
        if ok:
            self.event(tr("ล้างแรม (%s): ว่าง %s → %s MB | %s") % (why, before, after, RAM_LAST["detail"]), "cyan")
        else:
            self.event(tr("ล้างแรมไม่สำเร็จ: %s") % RAM_LAST["detail"], "red")

    def _fps_txt(self, inst):
        rec = self.hb.latest(inst) if self.hb else None
        if rec and rec.get("fps") and time.time() - rec["ts"] <= int(self.cfg.d["heartbeat"]["timeout"]):
            return " | FPS " + rec["fps"] + cap_txt(rec)
        return ""

    def report(self, minutes, shot=None):
        w = self.cfg.d["webhook"]
        if not (w["enabled"] and w["url"] and (w["events"].get("status") or minutes is None)):
            return
        if shot is None and minutes is not None and ENV.root and w["send_screenshot"]:
            shot = take_shot("status")
        mem = mem_available_mb()
        fields = [(i.label, tr("%s | รีจอย %d | ล้มเหลว %d%s") % (STATE_LABEL.get(i.state, (i.state,))[0], i.rejoins, i.fails,
                                                              (" | " + i.user) if i.user else "") + self._fps_txt(i)) for i in self.insts][:22]
        fields.append((tr("RAM ว่าง"), ("%d MB" % mem) if mem is not None else "-"))
        fields.append((tr("รันมาแล้ว"), fmt_dur(time.time() - self.t0)))
        fields.append(("CPU", cpu_text()))
        self.hook.send("status", tr("📸 รายงานสถานะ"), tr("รายงานตามคำสั่ง") if minutes is None else tr("รายงานประจำทุก %d นาที") % minutes, fields, shot)

    # -- console commands (typed in the live panel)
    def _take_cmd(self, inst):
        cmd, inst.cmd = inst.cmd, ""
        inst.wake.clear()
        return cmd

    def _manual(self, inst):
        cmd = self._take_cmd(inst)
        if cmd == "pause":
            inst.paused, inst.state, inst.detail = True, "paused", tr("หยุดชั่วคราวด้วยคำสั่ง (พิมพ์ resume)")
            self.event(tr("%s: หยุดชั่วคราว") % inst.label, "yellow")
        elif cmd in ("rejoin", "resume"):
            inst.paused, inst.fails, inst.ok_since = False, 0, None
            self.event(tr("%s: รีจอยด้วยคำสั่ง") % inst.label, "cyan")
            self._launch(inst, "rejoin", force=True)

    def _targets(self, arg):
        if arg in ("all", "a", "*"):
            return list(self.insts)
        return [i for i in self.insts if arg.isdigit() and i.idx == int(arg)]

    def console(self, line):
        line = line.strip()
        if not line:
            return None
        self.event("› " + line, "dim")
        parts = line.split()
        cmd, args = parts[0].lower(), [a.lower() for a in parts[1:]]
        if cmd in ("quit", "exit", "q"):
            return "quit"
        if cmd in ("help", "?", "h"):
            self.event(CONSOLE_HELP, "cyan")
        elif cmd in ("rejoin", "r", "pause", "p", "resume"):
            act = {"r": "rejoin", "p": "pause"}.get(cmd, cmd)
            ts = self._targets(args[0]) if args else []
            if not ts:
                self.event(tr("ใช้: %s <เลขอินสแตนซ์|all>") % cmd, "red")
            else:
                for i in ts:
                    i.cmd = act
                    i.wake.set()
                self.event(tr("สั่ง %s อินสแตนซ์ %s") % (act, ", ".join(str(i.idx) for i in ts)), "cyan")
        elif cmd == "status":
            for i in self.insts:
                self.event("#%d %s | %s" % (i.idx, STATE_LABEL.get(i.state, (i.state,))[0], i.detail or "-"))
        elif cmd in ("streamer", "hide"):
            on = (not STREAMER["on"]) if not args else args[0] in ("on", "1", "yes", "y")
            STREAMER["on"] = self.cfg.d["ui"]["streamer_mode"] = on
            self.cfg.save()
            self.event(tr("โหมดซ่อนชื่อบัญชี (streamer): ") + (tr("เปิด") if on else tr("ปิด")), "cyan")
        elif cmd in ("ram", "shot"):
            if not ENV.root:
                self.event(tr("คำสั่ง %s ต้องใช้รูท") % cmd, "red")
            else:
                threading.Thread(target=self._cmd_root, args=(cmd,), daemon=True).start()
        else:
            self.event(tr("ไม่รู้จักคำสั่ง: %s (พิมพ์ help)") % cmd, "red")
        return None

    def _cmd_root(self, cmd):
        try:
            if cmd == "ram":
                self.clear_ram(tr("คำสั่ง"))
            else:
                path = take_shot("console")
                self.event(tr("บันทึกภาพหน้าจอ: %s") % os.path.basename(path) if path else tr("แคปหน้าจอไม่สำเร็จ"), "cyan" if path else "red")
                if path:
                    self.report(None, shot=path)
        except Exception as e:
            self.event(tr("คำสั่ง %s ผิดพลาด: %s") % (cmd, e), "red")

    # -- worker
    def _worker(self, inst):
        try:
            if not self._launch(inst, "start"):
                return
            while True:
                inst.next_at = self._next_tick()
                woke = inst.wake.wait(max(0.1, inst.next_at - time.time()))
                if self.stop.is_set():
                    break
                try:
                    if woke:
                        self._manual(inst)
                    elif not inst.paused:
                        self._tick(inst)
                except Exception as e:
                    self.event(tr("%s: ข้อผิดพลาด %s") % (inst.label, e), "red")
        except Exception as e:
            self.event(tr("%s: worker หยุดเพราะ %s") % (inst.label, e), "red")
        inst.state = "stopped"

    def _launch(self, inst, kind, force=False):
        c = self.cfg.d
        inst.state, inst.detail = "queued", tr("รอคิวเปิดแอป")
        with self.gate:
            wait = self.last_launch + max(0, int(c["launch_delay"])) - time.time()
            if wait > 0 and self.stop.wait(wait):
                return False
            if self.stop.is_set():
                return False
            inst.state, inst.detail = "launching", ""
            ok, msg = open_game(inst.pkg, self.uri, c["auto"]["force_stop"] or force)
            self.last_launch = time.time()
        inst.launched_at = time.time()
        inst.pres_at = 0.0
        inst.state, inst.detail = "joining", tr("รอเข้าเกม")
        if not ok:
            self.event(tr("%s: เปิดเกมไม่สำเร็จ: %s") % (inst.label, msg), "red")
            return True
        if kind == "start":
            self.event(tr("%s: เปิดเกมแล้ว") % inst.label, "green")
        else:
            inst.rejoins += 1
            self.event(tr("%s: รีจอยแล้ว (ครั้งที่ %d)") % (inst.label, inst.rejoins), "cyan")
            self.hook.send("rejoin", tr("🔁 รีจอยแล้ว"), inst.label,
                           [(tr("แพ็กเกจ"), inst.pkg), (tr("บัญชี"), inst.user or "-"), (tr("ครั้งที่"), str(inst.rejoins))])
        return True

    def presence(self, inst):
        if not (inst.cookie and self.cfg.d["auto"]["presence_check"]) or inst.cookie_bad:
            return None
        if inst.session is None:
            inst.session = make_session(inst.cookie)
        if inst.uid is None:
            uid, name, err = who_am_i(inst.session)
            if not uid:
                if err and tr("คุกกี้") in err:
                    inst.cookie_bad = True
                    self.event("%s: %s" % (inst.label, err), "red")
                return None
            inst.uid, inst.user = uid, name
        ttl = 60 if self.cfg.d["performance"]["low_cpu"] else 0
        if ttl and time.time() - inst.pres_at < ttl - 1:
            return inst.pres_val
        inst.pres_val, inst.pres_at = in_game(inst.session, inst.uid), time.time()
        return inst.pres_val

    def hb_state(self, inst):
        """None = no usable heartbeat info (never seen / disabled); else ("ok"|"stale", detail)."""
        hc = self.cfg.d["heartbeat"]
        if not (hc["enabled"] and self.hb):
            return None
        rec = self.hb.latest(inst)
        if rec is None:
            return None  # script never reported: fall back to other signals
        if rec["ts"] < inst.launched_at:
            return "stale", tr("ยังไม่มี heartbeat หลังเปิดเกมใหม่")
        age = time.time() - rec["ts"]
        if rec.get("err") and age <= int(hc["timeout"]):
            return "err", "Disconnected: " + rec["err"]
        if rec["user"] and not inst.user:
            inst.user = rec["user"]
        who = (" [%s]" % rec["user"]) if rec["user"] else ""
        if age <= int(hc["timeout"]):
            return "ok", tr("heartbeat %ds ก่อน%s%s") % (age, who, (" | FPS " + rec["fps"] + cap_txt(rec)) if rec.get("fps") else "")
        return "stale", tr("ไม่มี heartbeat มา %ds%s") % (age, who)

    def check(self, inst):
        alive = proc_alive(inst.pkg)
        if ENV.root and not alive:
            return "dead", REASON["dead"]
        if ENV.root and alive and self.cfg.d["auto"]["foreground_check"]:
            if (inst.pkg + "/") not in foreground_text():
                return "background", REASON["background"]
        hs = self.hb_state(inst)
        if hs:
            if hs[0] == "err":
                return "disconnected", hs[1]
            return ("ok", hs[1]) if hs[0] == "ok" else ("noheartbeat", hs[1])
        pres = self.presence(inst)
        who = (" [%s]" % inst.user) if inst.user else ""
        if pres is True:
            return "ok", tr("อยู่ในเกม") + who
        if pres is False:
            return "notingame", REASON["notingame"] + who
        if alive:
            return "ok", tr("แอปทำงานอยู่ (ไม่มีข้อมูลสถานะเกม)")
        return "unknown", tr("ไม่ทราบสถานะ (ไม่รูท/ไม่มีคุกกี้)")

    def _tick(self, inst):
        c = self.cfg.d
        now = time.time()
        status, detail = self.check(inst)
        inst.detail = detail
        if status in ("ok", "unknown"):
            inst.state = status
            if inst.ok_since is None:
                inst.ok_since = now
            if status == "ok" and inst.fails and now - inst.ok_since >= c["stable_reset"]:
                inst.fails = 0
                self.event(tr("%s: เสถียรครบ %ds รีเซ็ต backoff") % (inst.label, c["stable_reset"]))
            tm = int(c["auto"]["timed_rejoin_min"])
            if tm > 0 and now - inst.launched_at >= tm * 60:
                return self._recover(inst, "timed")
            return
        elapsed = now - inst.launched_at
        jg = c["join_grace"]
        early = {"dead": min(30, jg), "disconnected": min(15, jg)}.get(status, jg)
        if elapsed < early:
            inst.state = "joining"
            return
        self._recover(inst, status)

    def _recover(self, inst, reason):
        c = self.cfg.d
        inst.ok_since = None
        delay = 0
        if reason != "timed":
            inst.fails += 1
            delay = min(int(c["backoff_max"]), int(c["backoff_base"]) * 2 ** (inst.fails - 1))
            self.event(tr("%s: %s (ครั้งที่ %d) รีจอยใน %ds") % (inst.label, REASON[reason], inst.fails, delay), "red")
            self._check_storm(inst)
            shot = take_shot(inst.pkg) if c["auto"]["screenshot_on_crash"] else None
            self.hook.send("crash", tr("⚠️ หลุด/ค้าง"), inst.label,
                           [(tr("สาเหตุ"), REASON[reason]), (tr("แพ็กเกจ"), inst.pkg), (tr("บัญชี"), inst.user or "-"),
                            (tr("ล้มเหลวติดกัน"), str(inst.fails)), (tr("รีจอยใน"), "%ds" % delay)], shot)
        else:
            self.event("%s: %s" % (inst.label, REASON[reason]), "cyan")
        if delay:
            inst.state, inst.next_at = "backoff", time.time() + delay
            if inst.wake.wait(delay):
                if self.stop.is_set():
                    return
                if self._take_cmd(inst) == "pause":
                    inst.paused, inst.state, inst.detail = True, "paused", tr("หยุดชั่วคราวด้วยคำสั่ง (พิมพ์ resume)")
                    return
        self._launch(inst, "rejoin")

    # -- live panel
    def render(self, buf=None):
        now = time.time()
        mem = mem_available_mb()
        load = sys_load()
        head = Text.assemble(
            (tr("โหมด: "), "dim"), ("ROOT" if ENV.root else tr("ไม่รูท"), "bold green" if ENV.root else "bold yellow"),
            (tr("  |  ตรวจทุก %ds") % self.interval(), ""), (tr("  |  หน่วงเปิด %ss") % self.cfg.d["launch_delay"], ""),
            (tr("  |  รันมา ") + fmt_dur(now - self.t0), "bold"),
            ((tr("  |  RAM ว่าง %d MB") % mem) if mem is not None else "", ""),
            ((tr("  |  โหลด %.1f") % load) if load is not None else "", ""),
            (tr("\nพิมพ์คำสั่งแล้วกด Enter (help = รายการคำสั่ง) | quit หรือ Ctrl+C = หยุดและกลับเมนู"), "dim"))
        t = Table(box=box.SIMPLE_HEAVY, expand=True, header_style="bold cyan")
        for col in ("#", tr("แพ็กเกจ"), tr("บัญชี"), tr("สถานะ"), tr("เล่นมา"), tr("ล้มเหลว"), tr("รีจอย"), tr("ถัดไป"), tr("รายละเอียด")):
            t.add_column(col, overflow="fold")
        for i in self.insts:
            label, style = STATE_LABEL.get(i.state, (i.state, ""))
            nxt = "%ds" % max(0, int(i.next_at - now)) if i.next_at and i.state in ("ok", "unknown", "joining", "backoff") else "-"
            up = fmt_dur(now - i.ok_since) if i.ok_since and i.state == "ok" else "-"
            t.add_row(str(i.idx), i.pkg, hide_name(i.user) or "-", Text(label, style=style), up, str(i.fails),
                      str(i.rejoins), nxt, T(hide_users(i.detail)))
        # fit the terminal: fixed rows = head 4 + table (n+5) + console 3 + events border 2
        n_ev = max(1, min(8, console.size.height - (len(self.insts) + 14)))
        ev = Text(no_wrap=True, overflow="ellipsis")
        for ts, msg, st in list(reversed(self.events))[:n_ev]:   # newest first so cropping never hides the latest
            ev.append(ts + "  ", style="dim")
            ev.append(hide_users(msg) + "\n", style=st)
        parts = [Panel(head, title="GARNT TOOL REJOIN", border_style="cyan"), t]
        if buf is not None:
            parts.append(Panel(Text.assemble(("› ", "bold yellow"), (scrub(buf), "bold"), ("█", "blink")),
                               title=tr("คอนโซล"), border_style="yellow"))
        parts.append(Panel(ev, title=tr("เหตุการณ์ล่าสุด (ใหม่สุดอยู่บน)"), border_style="blue"))
        return Group(*parts)


# ----------------------------------------------------------------- ui helpers
def T(s, style=""):
    return Text(scrub(s), style=style)


def say(s="", style=""):
    console.print(T(s, style), highlight=False)


def ask(prompt, default=""):
    try:
        v = console.input(Text(prompt + " ", style="bold yellow")).strip()
    except EOFError:
        return default or "0"
    return v or default


def confirm(prompt):
    return ask(prompt + " (y/N)").lower() in ("y", "yes", tr("ใช่"))


def pause():
    ask(tr("กด Enter เพื่อกลับเมนู"))


def onoff(v):
    return Text(tr("เปิด") if v else tr("ปิด"), style="bold green" if v else "bold red")


def clear_screen():
    if sys.stdout.isatty():
        sys.stdout.write("\033[3J\033[2J\033[H")
        sys.stdout.flush()


def banner(cfg):
    clear_screen()
    console.print(Text(LOGO.strip("\n"), style="bold cyan"), highlight=False)
    console.print(Text(tr("      T O O L   R E J O I N   •   ควายเผือก rejoin   •   v") + VERSION, style="bold magenta"))
    link = bool(cfg.d["game_link"])
    console.print(Text.assemble(
        (tr("โหมด: "), "dim"), ("ROOT" if ENV.root else tr("ไม่รูท"), "bold green" if ENV.root else "bold yellow"),
        (tr("   อินสแตนซ์: "), "dim"), (str(len(cfg.d["detected"])), "bold"),
        (tr("   ลิงก์เกม: "), "dim"), (tr("ตั้งแล้ว") if link else tr("ยังไม่ตั้ง"), "green" if link else "red"),
        ("   Webhook: ", "dim"), (tr("เปิด") if cfg.d["webhook"]["enabled"] else tr("ปิด"),
                                  "green" if cfg.d["webhook"]["enabled"] else "red")))
    console.print(Text(tr("โฟลเดอร์: ") + BASE, style="dim"))
    if UPDATE_INFO["remote"]:
        console.print(Text(tr("🆕 มีเวอร์ชันใหม่ v%s (ตอนนี้ v%s) → เลือกเมนู 11 เพื่ออัปเดต") % (UPDATE_INFO["remote"], VERSION), style="bold yellow"))
    console.print()


def selected_packages(cfg):
    sel = cfg.d["packages"]
    return [p for p in cfg.d["detected"] if not sel or p in sel]


def refresh_packages(cfg):
    found = scan_packages(prefixes(cfg))
    if found:
        cfg.d["detected"] = found
        cfg.d["packages"] = [p for p in cfg.d["packages"] if p in found]
        cfg.save()
    return found


# ----------------------------------------------------------------- menus
def menu_start(cfg, hook):
    parsed = parse_link(cfg.d["game_link"])
    pkgs = selected_packages(cfg)
    if not parsed:
        say(tr("ยังไม่ได้ตั้งลิงก์เกม (หรือลิงก์ไม่ถูกต้อง) ไปที่เมนู 3 ก่อน"), "red")
        return pause()
    if not pkgs:
        say(tr("ไม่พบแพ็กเกจ Roblox ไปที่เมนู 4 เพื่อสแกนก่อน"), "red")
        return pause()
    if cfg.d["auto"]["cookie_pull_on_start"] and ENV.root:
        have = {p for p, _ in cookie_pairs(cfg)[0]}
        need = [p for p in pkgs if p not in have]
        if need:
            say(tr("กำลังดึงคุกกี้จากแอป %d อินสแตนซ์ที่ยังไม่มีคุกกี้...") % len(need), "dim")
            got = {}
            for pkg, ck, name, msg in pull_validated(need):
                if ck:
                    got[pkg] = ck
                say("%s: %s" % (pkg, (tr("ได้คุกกี้ของ ") + hide_name(name)) if ck else msg), "green" if ck else "yellow")
            if got:
                store_cookies(cfg, got)
    if cfg.d["auto"]["cookie_login_on_start"]:
        if ENV.root:
            pairs = [(p, c) for p, c in cookie_pairs(cfg)[0] if p in pkgs]
            for pkg, ok, msg in cookie_login(pairs):
                say("%s: %s" % (pkg, msg), "green" if ok else "red")
        else:
            say(tr("ข้ามการล็อกอินด้วยคุกกี้: ต้องใช้รูท"), "yellow")
    if cfg.d["autoexec"]["auto_create"]:
        report_autoexec(cfg, install_autoexec(cfg))
    cmap = dict(cookie_pairs(cfg)[0])
    det = cfg.d["detected"]
    insts = [Inst(det.index(p) + 1, p, cmap.get(p)) for p in pkgs]
    hb = hbs = None
    hc = cfg.d["heartbeat"]
    if hc["enabled"]:
        hb = Heartbeats()
        hbs = HBServer(hb, hc["port"], prefixes(cfg))
        hb.single = len(insts) == 1
        try:
            hbs.start()
            say(tr("รับ heartbeat ที่ 127.0.0.1:%d (ทดสอบ: curl http://127.0.0.1:%d/ping)") % (hbs.port, hbs.port), "green")
        except OSError as e:
            say(tr("เปิดพอร์ต heartbeat ไม่ได้ (%s) ข้ามการใช้ heartbeat") % e, "red")
            hb = hbs = None
    if cfg.d["auto"]["wake_lock"]:
        wake_lock(True)
    apply_perf(cfg)
    pf = cfg.d["performance"]
    rate = 1.0 / max(1, int(pf["ui_refresh_sec"])) if pf["low_cpu"] else 2
    chook = ConsoleHook(cfg)
    chook.start()
    eng = Engine(cfg, hook, parsed[0], insts, hb, chook)
    eng.start()
    try:
        run_live(eng.render, eng.console, 1.0 / rate)
    except KeyboardInterrupt:
        pass
    say(tr("\nกำลังหยุด..."), "yellow")
    eng.shutdown()
    chook.close()
    if hbs:
        hbs.stop()
    say(tr("หยุดการทำงานแล้ว"), "green")
    pause()


def status_view(cfg, info, buf=""):
    det = cfg.d["detected"]
    pairs, n_ck = info
    cmap = dict(pairs)
    fg = foreground_text() if ENV.root else ""
    t = Table(title=tr("อินสแตนซ์ที่ตรวจพบ"), box=box.ROUNDED, header_style="bold cyan", expand=True)
    for col in ("#", tr("แพ็กเกจ"), tr("เลือกรัน"), tr("คุกกี้"), tr("ทำงานอยู่"), tr("อยู่หน้าจอ")):
        t.add_column(col)
    sel = selected_packages(cfg)
    for i, pkg in enumerate(det, 1):
        alive = proc_alive(pkg)
        run = Text(tr("ใช่"), style="green") if alive else (Text(tr("ไม่"), style="red") if ENV.root else Text(tr("ไม่ทราบ"), style="magenta"))
        front = Text("-") if not ENV.root else (Text(tr("ใช่"), style="green") if (pkg + "/") in fg else Text(tr("ไม่"), style="dim"))
        t.add_row(str(i), pkg, onoff(pkg in sel), Text(tr("มี") if pkg in cmap else "-"), run, front)
    mem, load = mem_available_mb(), sys_load()
    foot = tr("โหมด: %s | ตรวจทุก %ss | หน่วงเปิด %ss | คุกกี้ในไฟล์: %d บรรทัด | RAM ว่าง %s MB | โหลด %s%s") % (
        "ROOT" if ENV.root else tr("ไม่รูท"), cfg.d["check_interval"], cfg.d["launch_delay"], n_ck,
        mem if mem is not None else "?", ("%.1f" % load) if load is not None else "?",
        "" if ENV.root else tr(" | โหมดไม่รูทตรวจโปรเซส/หน้าจอไม่ได้"))
    return Group(t, T(foot, "dim"), T(tr("แผงสดรีเฟรชอัตโนมัติ — กด Enter หรือ Ctrl+C เพื่อกลับเมนู"), "yellow"))


def menu_status(cfg):
    if not cfg.d["detected"]:
        say(tr("ยังไม่พบแพ็กเกจ ลองเมนู 4 (สแกนแพ็กเกจ)"), "red")
        return pause()
    info = cookie_pairs(cfg)
    pf = cfg.d["performance"]
    try:
        run_live(lambda buf: status_view(cfg, info, buf), lambda ln: "quit",
                 max(1, int(pf["ui_refresh_sec"])) if pf["low_cpu"] else 1, once=True)
    except KeyboardInterrupt:
        pass


def switch_language(cfg):
    """Toggle th <-> en, save it, then restart the program so every label is rebuilt in the new language."""
    cfg.d["lang"] = "th" if LANG == "en" else "en"
    cfg.save()
    say("Restarting to apply the language... / กำลังรีสตาร์ตเพื่อเปลี่ยนภาษา...", "yellow")
    try:
        os.environ.pop("REJOIN_LANG", None)
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except OSError:
        say("Please start the program again to use the new language. / กรุณารันโปรแกรมใหม่เพื่อใช้ภาษาที่เลือก", "yellow")
        pause()


def menu_settings(cfg):
    while True:
        banner(cfg)
        p = parse_link(cfg.d["game_link"])
        t = Table(title=tr("ตั้งค่า"), box=box.ROUNDED, header_style="bold cyan")
        t.add_column("#")
        t.add_column(tr("รายการ"))
        t.add_column(tr("ค่าปัจจุบัน"))
        link = cfg.d["game_link"]
        t.add_row("1", tr("ลิงก์เกม / Place ID"), T((link[:60] + "…" if len(link) > 60 else link) or tr("(ยังไม่ตั้ง)")))
        t.add_row("2", tr("ช่วงเวลาตรวจสอบ (วินาที)"), str(cfg.d["check_interval"]))
        t.add_row("3", tr("หน่วงเวลาเปิดแต่ละอินสแตนซ์ (วินาที)"), str(cfg.d["launch_delay"]))
        t.add_row("4", tr("ซ่อนชื่อบัญชี (streamer mode: โชว์ 4 ตัวแรก ที่เหลือเป็น #)"), onoff(cfg.d["ui"]["streamer_mode"]))
        t.add_row("5", "Language / ภาษา", "English" if LANG == "en" else "ไทย")
        console.print(t)
        if p:
            say(tr("ลิงก์ที่ใช้เปิดจริง: ") + p[0], "dim")
        c = ask(tr("เลือกหมายเลข (0=กลับ)"))
        if c == "0":
            return
        if c == "1":
            v = ask(tr("วางลิงก์เกม (roblox.com/games/..., ลิงก์ private server, ลิงก์แชร์ หรือ Place ID):"))
            if v and not parse_link(v):
                say(tr("รูปแบบลิงก์ไม่ถูกต้อง"), "red")
                pause()
            elif v:
                cfg.d["game_link"] = v
                cfg.save()
        elif c == "4":
            cfg.d["ui"]["streamer_mode"] = STREAMER["on"] = not cfg.d["ui"]["streamer_mode"]
            cfg.save()
        elif c == "5":
            switch_language(cfg)
        elif c in ("2", "3"):
            key, lo = ("check_interval", 10) if c == "2" else ("launch_delay", 0)
            v = ask(tr("ค่าใหม่ (วินาที, ต่ำสุด %d):") % lo)
            if v.isdigit() and int(v) >= lo:
                cfg.d[key] = int(v)
                cfg.save()
            elif v:
                say(tr("ต้องเป็นตัวเลข >= %d") % lo, "red")
                pause()


def menu_scan(cfg):
    say(tr("กำลังสแกนแพ็กเกจ %s ...") % ", ".join(p + "*" for p in prefixes(cfg)), "cyan")
    found = refresh_packages(cfg)
    if not found:
        say(tr("ไม่พบแพ็กเกจ (ถ้าอินสแตนซ์ใช้ชื่ออื่น พิมพ์ชื่อแพ็กเกจเองได้ คั่นด้วยคอมมา)"), "red")
        v = ask(tr("แพ็กเกจ (Enter=ข้าม):"))
        names = [x.strip() for x in v.split(",") if re.fullmatch(r"[\w.]+", x.strip())]
        if names:
            cfg.d["detected"] = names
            cfg.save()
            found = names
        else:
            return pause()
    t = Table(title=tr("แพ็กเกจที่พบ"), box=box.ROUNDED, header_style="bold cyan")
    for col in ("#", tr("แพ็กเกจ"), tr("เลือกรัน")):
        t.add_column(col)
    sel = selected_packages(cfg)
    for i, p in enumerate(found, 1):
        t.add_row(str(i), p, onoff(p in sel))
    console.print(t)
    v = ask(tr("เลือกที่จะรัน เช่น 1,3 | all=ทั้งหมด | Enter=คงเดิม:")).lower()
    if v == "all":
        cfg.d["packages"] = []
    elif v:
        idx = [int(x) for x in re.findall(r"\d+", v) if 0 < int(x) <= len(found)]
        if idx:
            cfg.d["packages"] = [found[i - 1] for i in sorted(set(idx))]
    cfg.save()
    say(tr("บันทึกแล้ว"), "green")
    pause()


def menu_cookie(cfg):
    if ensure_cookie_file():
        say(tr("สร้างไฟล์ตัวอย่างแล้ว: ") + COOKIE_PATH, "green")
    pairs, n_ck = cookie_pairs(cfg)
    if not n_ck:
        say(tr("ยังไม่มีคุกกี้ใน cookie.txt  (1 บรรทัด / 1 บัญชี เรียงตามอินสแตนซ์)\nไฟล์: ") + COOKIE_PATH, "yellow")
        return pause()
    det = cfg.d["detected"]
    if n_ck > len(det):
        say(tr("คุกกี้มากกว่าอินสแตนซ์ (%d > %d) ส่วนเกินจะถูกข้าม") % (n_ck, len(det)), "yellow")
    t = Table(title=tr("คุกกี้ที่จับคู่กับอินสแตนซ์ (ตามลำดับ)"), box=box.ROUNDED, header_style="bold cyan")
    for col in (tr("บรรทัด"), tr("แพ็กเกจ"), tr("คุกกี้"), tr("ผลตรวจ")):
        t.add_column(col)
    valid = []
    for i, (pkg, ck) in enumerate(pairs, 1):
        uid, name, err = who_am_i(make_session(ck))
        if uid:
            valid.append((pkg, ck))
        t.add_row(str(i), pkg, mask(ck), Text((tr("ใช้ได้: ") + hide_name(name)) if uid else (tr("ใช้ไม่ได้: ") + str(err)),
                                              style="green" if uid else "red"))
    console.print(t)
    if not ENV.root:
        say(tr("โหมดไม่รูท: ตรวจคุกกี้ได้อย่างเดียว การใส่คุกกี้ลงแอปต้องใช้รูท"), "yellow")
        return pause()
    if valid and confirm(tr("ใส่คุกกี้ที่ใช้ได้ %d ตัวลงแอป (แอปจะถูก force-stop)?") % len(valid)):
        for pkg, ok, msg in cookie_login(valid):
            say("%s: %s" % (pkg, msg), "green" if ok else "red")
    pause()


def api_post(s, url, body=None):
    """POST with CSRF retry -> (ok, message)"""
    retried = False
    try:
        if "apis.roblox.com" in url:
            s.headers.setdefault("Origin", "https://www.roblox.com")
            s.headers.setdefault("Referer", "https://www.roblox.com/")
        r = s.post(url, json=body, timeout=15)
        if r.status_code == 403 and "x-csrf-token" in r.headers:
            s.headers["X-CSRF-TOKEN"] = r.headers["x-csrf-token"]
            r = s.post(url, json=body, timeout=15)
            retried = True
    except Exception as e:
        return False, scrub(e)[:80]
    msg = ""
    try:
        j = r.json()
        msg = (j.get("errors") or [{}])[0].get("message", "") or j.get("message", "")
        if r.status_code == 200 and j.get("success") is False:
            return False, msg or "success=false"
    except Exception:
        pass
    if r.status_code == 200:
        return True, "ok"
    if r.status_code == 401:
        msg = tr("คุกกี้หมดอายุ")
    elif r.status_code == 429:
        msg = tr("ส่งถี่เกินไป รอสักพัก")
    if not msg and r.status_code not in (401, 429):
        msg = scrub(" ".join((r.text or "").split()))[:70] or tr("(เนื้อหาตอบกลับว่าง)")
    if r.status_code == 403:
        msg += " [csrf: " + (tr("ส่งโทเคนซ้ำแล้วยังไม่ผ่าน") if retried else
                             tr("เซิร์ฟเวอร์ไม่ส่งโทเคนมา")) + "]"
    return False, ("HTTP %d %s" % (r.status_code, msg)).strip()


def resolve_user(s, text):
    """username or numeric ID -> (id, name) or (None, None)"""
    text = text.strip().lstrip("@")
    try:
        if text.isdigit():
            r = s.get("https://users.roblox.com/v1/users/%s" % text, timeout=12)
            if r.status_code == 200:
                j = r.json()
                USERS.add(j.get("name", ""))
                return j["id"], j.get("name", "?")
            return None, None
        r = s.post("https://users.roblox.com/v1/usernames/users",
                   json={"usernames": [text], "excludeBannedUsers": False}, timeout=12)
        d = r.json().get("data") or []
        if d:
            USERS.add(d[0]["name"])
            return d[0]["id"], d[0]["name"]
    except Exception:
        pass
    return None, None


SOCIAL_URL = {"block": "https://apis.roblox.com/user-blocking-api/v1/users/%d/block-user",
              "unblock": "https://apis.roblox.com/user-blocking-api/v1/users/%d/unblock-user",
              "friend": "https://friends.roblox.com/v1/users/%d/request-friendship",
              "accept": "https://friends.roblox.com/v1/users/%d/accept-friend-request"}
SOCIAL_NAME = {"block": tr("บล็อก"), "unblock": tr("เลิกบล็อก"), "friend": tr("ส่งคำขอเป็นเพื่อน")}

SOCIAL_LEGACY = {"block": "https://accountsettings.roblox.com/v1/users/%d/block",
                 "unblock": "https://accountsettings.roblox.com/v1/users/%d/unblock"}


def ensure_tracker(s, uid=0, force_fake=False):
    """ให้เซสชันมีคุกกี้ RBXEventTrackerV2 (browserid) ที่ apis.roblox.com ใช้เป็น BrowserTrackerID: ขอจากเซิร์ฟเวอร์ก่อน ไม่ได้ก็สร้างเอง"""
    if not force_fake:
        if any(c.name == "RBXEventTrackerV2" for c in s.cookies):
            return
        if not getattr(s, "_garnt_home", False):
            s._garnt_home = True
            try:
                s.get("https://www.roblox.com/home", timeout=15)
            except Exception:
                pass
            if any(c.name == "RBXEventTrackerV2" for c in s.cookies):
                return
    import random
    now = datetime.now()
    val = "CreateDate=%d/%d/%d %d:%02d:%02d %s&rbxid=%d&browserid=%d" % (
        now.month, now.day, now.year, now.hour % 12 or 12, now.minute, now.second,
        "AM" if now.hour < 12 else "PM", int(uid or 0), random.randint(10 ** 10, 10 ** 11 - 1))
    s.cookies.set("RBXEventTrackerV2", val, domain=".roblox.com", path="/")


def is_blocking(s, tid):
    """True/False/None(ไม่ทราบ): บัญชีนี้มี tid อยู่ในรายการที่บล็อกไว้หรือไม่"""
    cursor = ""
    try:
        for _ in range(10):
            r = s.get("https://apis.roblox.com/user-blocking-api/v1/users/get-blocked-users",
                      params={"cursor": cursor, "count": 50}, timeout=15)
            if r.status_code != 200:
                return None
            d = (r.json() or {}).get("data") or {}
            if int(tid) in [int(x) for x in (d.get("blockedUserIds") or [])]:
                return True
            cursor = d.get("cursor") or ""
            if not cursor:
                return False
    except Exception:
        return None
    return None


def social_call(s, action, tid, uid=0):
    """บล็อก/เลิกบล็อก/เพื่อน -> (ok, ข้อความ) ใช้ user-blocking-api ก่อน ถ้าได้ 404 ค่อยลอง URL เก่า"""
    body = {} if action != "friend" else None
    web = "apis.roblox.com" in SOCIAL_URL[action]
    if web:
        ensure_tracker(s, uid)
    ok, msg = api_post(s, SOCIAL_URL[action] % tid, body)
    if web and not ok and "BrowserTrackerID" in msg:
        ensure_tracker(s, uid, force_fake=True)
        ok, msg = api_post(s, SOCIAL_URL[action] % tid, body)
    if web and not ok and msg.startswith("HTTP 400") and action in ("block", "unblock"):
        st = is_blocking(s, tid)
        if st is True and action == "block":
            return True, tr("บล็อกอยู่แล้ว (ตรวจจากรายการบล็อก)")
        if st is False and action == "unblock":
            return True, tr("ไม่ได้บล็อกอยู่ ไม่ต้องทำอะไร")
        if st is not None:
            msg += tr(" [ตรวจรายการบล็อกแล้ว: ") + (tr("บล็อกอยู่") if st else tr("ไม่ได้บล็อก")) + "]"
    if not ok and msg.startswith("HTTP 404") and action in SOCIAL_LEGACY:
        ok2, msg2 = api_post(s, SOCIAL_LEGACY[action] % tid, body)
        if ok2:
            return True, msg2
        msg += " / " + msg2
    return ok, msg


def social_accounts(cfg):
    """Accounts that have a valid cookie in cookie.txt -> [(pkg, session, uid, name)]"""
    out = []
    for pkg, ck in cookie_pairs(cfg)[0]:
        s = make_session(ck)
        uid, name, err = who_am_i(s)
        if uid:
            out.append((pkg, s, uid, name))
        else:
            say(tr("%s: คุกกี้ใช้ไม่ได้ (%s)") % (pkg, err), "red")
    return out


def pick_accounts(accts):
    v = ask(tr("เลือกบัญชี (เช่น 1,3 หรือ all, Enter=all):")).strip().lower()
    if v in ("", "all", "a"):
        return list(accts)
    idx = {int(x) for x in v.replace(" ", "").split(",") if x.isdigit()}
    return [a for n, a in enumerate(accts, 1) if n in idx]


def run_social(action, accts, tid):
    """Every selected account does `action` on user tid. -> [(pkg, name, ok, message)]"""
    rows = []
    for pkg, s, uid, name in accts:
        if uid == tid:
            rows.append((pkg, name, False, tr("เป็นบัญชีเดียวกับเป้าหมาย ข้าม")))
            continue
        ok, msg = social_call(s, action, tid, uid)
        rows.append((pkg, name, ok, msg))
        time.sleep(1.5)     # stay under the rate limit
    return rows


def show_social(rows):
    t = Table(box=box.ROUNDED, header_style="bold cyan")
    for col in (tr("แพ็กเกจ"), tr("บัญชี"), tr("ผล")):
        t.add_column(col)
    for pkg, name, ok, msg in rows:
        t.add_row(pkg, hide_name(name), Text(("✓ " if ok else "✗ ") + msg, style="green" if ok else "red"))
    console.print(t)


def pick_target(accts):
    """Target of block/unblock/friend: one of the cookie accounts, or a typed username / ID (both kept). -> (id, name)"""
    src = ask(tr("เลือกเป้าหมายจาก: 1=บัญชีในคุกกี้, 2=พิมพ์ชื่อ/ID Roblox (Enter=1):")).strip()
    if src in ("", "1"):
        v = ask(tr("เลือกเลขบัญชีเป้าหมาย (ตามตารางด้านบน):")).strip()
        if v.isdigit() and 1 <= int(v) <= len(accts):
            a = accts[int(v) - 1]
            return a[2], a[3]
        return None, None
    who = ask(tr("ชื่อผู้ใช้หรือ User ID เป้าหมาย:"))
    return resolve_user(accts[0][1], who) if who else (None, None)


def blocked_ids(s):
    """ชุด User ID ที่บัญชีนี้บล็อกไว้ หรือ None ถ้าอ่านไม่ได้"""
    out, cursor = set(), ""
    try:
        for _ in range(40):
            r = s.get("https://apis.roblox.com/user-blocking-api/v1/users/get-blocked-users",
                      params={"cursor": cursor, "count": 50}, timeout=15)
            if r.status_code != 200:
                return None
            d = (r.json() or {}).get("data") or {}
            out.update(int(x) for x in (d.get("blockedUserIds") or []))
            cursor = d.get("cursor") or ""
            if not cursor:
                return out
    except Exception:
        return None
    return out


def block_each_other(cfg, accts):
    """ทุกบัญชีในคุกกี้บล็อกกันเองทุกคู่ (ทั้งสองทิศ) ไม่ต้องเลือกเป้าหมาย"""
    seen = set()
    accts = [a for a in accts if not (a[2] in seen or seen.add(a[2]))]   # คุกกี้สองตัวของบัญชีเดียวกัน นับครั้งเดียว
    n = len(accts)
    if n < 2:
        say(tr("ต้องมีบัญชีที่ใช้ได้อย่างน้อย 2 บัญชี"), "red")
        return pause()
    try:
        gap = max(0, int(cfg.d.get("block_gap_sec", 20)))
    except (TypeError, ValueError):
        gap = 20
    est = max(1, int(round((n - 1) * max(gap, n * 1.0) / 60.0)))
    if not confirm(tr("บล็อกกันเอง %d บัญชี (%d การบล็อก) ใช้เวลาประมาณ %d นาที?") % (n, n * (n - 1), est)):
        return
    say(tr("กำลังอ่านรายการบล็อกของแต่ละบัญชี..."), "dim")
    have = []
    for pkg, s, uid, name in accts:
        ensure_tracker(s, uid)
        have.append(blocked_ids(s))
    stats = [[0, 0, 0] for _ in accts]      # newly blocked, already blocked, failed
    fails, last = [], [0.0] * n
    try:
        for k in range(1, n):
            say(tr("รอบ %d/%d") % (k, n - 1), "bold")
            for i in range(n):
                j = (i + k) % n
                pkg, s, uid, name = accts[i]
                tpkg, _, tuid, tname = accts[j]
                if uid == tuid:
                    continue
                if have[i] is not None and tuid in have[i]:
                    stats[i][1] += 1
                    continue
                for attempt in range(3):
                    wait = last[i] + gap - time.time()
                    if wait > 0:
                        time.sleep(wait)
                    ok, msg = social_call(s, "block", tuid, uid)
                    last[i] = time.time()
                    if ok or not msg.startswith("HTTP 429") or attempt == 2:
                        break
                    say(tr("%s: ส่งถี่เกิน (429) รอ 60 วินาทีแล้วลองใหม่") % pkg, "yellow")
                    time.sleep(60)
                say("  %s → %s: %s" % (pkg, hide_name(tname), ("✓ " if ok else "✗ ") + msg), "green" if ok else "red")
                if ok:
                    stats[i][0] += 1
                else:
                    stats[i][2] += 1
                    fails.append((pkg, tname, msg))
                time.sleep(1)
    except KeyboardInterrupt:
        say(tr("หยุดแล้ว (Ctrl+C) สรุปเท่าที่ทำไปแล้ว"), "yellow")
    t = Table(box=box.ROUNDED, header_style="bold cyan")
    for col in (tr("แพ็กเกจ"), tr("บัญชี"), tr("ผล")):
        t.add_column(col)
    for (pkg, _, _, name), (a, b, c) in zip(accts, stats):
        t.add_row(pkg, hide_name(name), Text(tr("บล็อกใหม่ %d | บล็อกอยู่แล้ว %d | ล้ม %d") % (a, b, c),
                                             style="green" if c == 0 else "red"))
    console.print(t)
    for pkg, tname, msg in fails[:8]:
        say("%s → %s: %s" % (pkg, hide_name(tname), msg), "red")
    if len(fails) > 8:
        say(tr("...และอีก %d รายการ") % (len(fails) - 8), "red")
    pause()


def unblock_each_other(cfg, accts):
    """ทุกบัญชีในคุกกี้ปลดบล็อกกันเอง (เฉพาะคู่ที่บล็อกกันอยู่จริง) ไม่แตะรายการบล็อกอื่น"""
    seen = set()
    accts = [a for a in accts if not (a[2] in seen or seen.add(a[2]))]   # คุกกี้สองตัวของบัญชีเดียวกัน นับครั้งเดียว
    n = len(accts)
    if n < 2:
        say(tr("ต้องมีบัญชีที่ใช้ได้อย่างน้อย 2 บัญชี"), "red")
        return pause()
    try:
        gap = max(0, int(cfg.d.get("block_gap_sec", 20)))
    except (TypeError, ValueError):
        gap = 20
    say(tr("กำลังอ่านรายการบล็อกของแต่ละบัญชี..."), "dim")
    have = []
    for pkg, s, uid, name in accts:
        ensure_tracker(s, uid)
        have.append(blocked_ids(s))

    def todo(i, j):
        return accts[i][2] != accts[j][2] and (have[i] is None or accts[j][2] in have[i])

    per = [sum(1 for j in range(n) if todo(i, j)) for i in range(n)]
    total = sum(per)
    if not total:
        say(tr("ไม่มีคู่ไหนบล็อกกันอยู่ ไม่ต้องทำอะไร"), "green")
        return pause()
    est = max(1, int(round(max(per) * gap / 60.0)))
    if not confirm(tr("ปลดบล็อกกันเอง %d คู่ (ทิศทาง) ใช้เวลาประมาณ %d นาที?") % (total, est)):
        return
    stats = [[0, n - 1 - per[i], 0] for i in range(n)]      # unblocked, not blocked (skipped), failed
    fails, last = [], [0.0] * n
    try:
        for k in range(1, n):
            if not any(todo(i, (i + k) % n) for i in range(n)):
                continue
            say(tr("รอบ %d/%d") % (k, n - 1), "bold")
            for i in range(n):
                j = (i + k) % n
                if not todo(i, j):
                    continue
                pkg, s, uid, name = accts[i]
                tpkg, _, tuid, tname = accts[j]
                for attempt in range(3):
                    wait = last[i] + gap - time.time()
                    if wait > 0:
                        time.sleep(wait)
                    ok, msg = social_call(s, "unblock", tuid, uid)
                    last[i] = time.time()
                    if ok or not msg.startswith("HTTP 429") or attempt == 2:
                        break
                    say(tr("%s: ส่งถี่เกิน (429) รอ 60 วินาทีแล้วลองใหม่") % pkg, "yellow")
                    time.sleep(60)
                say("  %s → %s: %s" % (pkg, hide_name(tname), ("✓ " if ok else "✗ ") + msg), "green" if ok else "red")
                if ok:
                    stats[i][0] += 1
                else:
                    stats[i][2] += 1
                    fails.append((pkg, tname, msg))
                time.sleep(1)
    except KeyboardInterrupt:
        say(tr("หยุดแล้ว (Ctrl+C) สรุปเท่าที่ทำไปแล้ว"), "yellow")
    t = Table(box=box.ROUNDED, header_style="bold cyan")
    for col in (tr("แพ็กเกจ"), tr("บัญชี"), tr("ผล")):
        t.add_column(col)
    for (pkg, _, _, name), (a, b, c) in zip(accts, stats):
        t.add_row(pkg, hide_name(name), Text(tr("ปลดบล็อก %d | ไม่ได้บล็อกอยู่ %d | ล้ม %d") % (a, b, c),
                                             style="green" if c == 0 else "red"))
    console.print(t)
    for pkg, tname, msg in fails[:8]:
        say("%s → %s: %s" % (pkg, hide_name(tname), msg), "red")
    if len(fails) > 8:
        say(tr("...และอีก %d รายการ") % (len(fails) - 8), "red")
    pause()


def launch_all_roblox(cfg):
    """เปิด Roblox ทุกอินสแตนซ์ที่เลือกทีละตัว หน่วง open_all_delay วินาที (ค่าเริ่มต้น 10) ระหว่างตัว"""
    pkgs = selected_packages(cfg)
    if not pkgs:
        say(tr("ไม่พบแพ็กเกจ Roblox ไปที่เมนู 4 เพื่อสแกนก่อน"), "red")
        return pause()
    try:
        delay = max(0, int(cfg.d.get("open_all_delay", 10)))
    except (TypeError, ValueError):
        delay = 10
    if not confirm(tr("เปิด Roblox %d อินสแตนซ์ ห่างกันตัวละ %d วินาที?") % (len(pkgs), delay)):
        return
    done = 0
    try:
        for i, pkg in enumerate(pkgs, 1):
            rc, out = sh("monkey -p %s -c android.intent.category.LAUNCHER 1" % shlex.quote(pkg),
                         root=ENV.root, timeout=30)
            ok, msg = rc == 0, (scrub(out.strip()[:120]) or "ok")
            done += 1 if ok else 0
            say("[%d/%d] %s: %s" % (i, len(pkgs), pkg, msg), "green" if ok else "red")
            if i < len(pkgs) and delay:
                say(tr("รอ %d วินาทีก่อนเปิดตัวถัดไป...") % delay, "dim")
                time.sleep(delay)
    except KeyboardInterrupt:
        say(tr("หยุดแล้ว (Ctrl+C)"), "yellow")
    say(tr("เปิดสำเร็จ %d/%d อินสแตนซ์") % (done, len(pkgs)), "green" if done == len(pkgs) else "yellow")
    pause()


def kill_all_roblox(cfg):
    """ปิด Roblox ทุกอินสแตนซ์ที่สแกนเจอ (force-stop) ต้องใช้รูท"""
    pkgs = list(cfg.d["detected"])
    if not pkgs:
        say(tr("ไม่พบแพ็กเกจ Roblox ไปที่เมนู 4 เพื่อสแกนก่อน"), "red")
        return pause()
    if not ENV.root:
        say(tr("ปิดแอปอื่นไม่ได้ถ้าไม่มีรูท (force-stop ต้องใช้สิทธิ์รูท)"), "red")
        return pause()
    if not confirm(tr("ปิด Roblox ทั้งหมด %d อินสแตนซ์ (ทุกตัวที่สแกนเจอ ไม่ใช่เฉพาะที่เลือก)?") % len(pkgs)):
        return
    done = 0
    for i, pkg in enumerate(pkgs, 1):
        try:
            rc, out = sh(AM() + " force-stop " + shlex.quote(pkg), root=True, timeout=20)
        except Exception as e:
            rc, out = 1, str(e)
        ok = rc == 0
        done += 1 if ok else 0
        say("[%d/%d] %s: %s" % (i, len(pkgs), pkg, tr("ปิดแล้ว") if ok else (scrub(out.strip()[:120]) or tr("ล้มเหลว"))),
            "green" if ok else "red")
    say(tr("ปิดสำเร็จ %d/%d อินสแตนซ์") % (done, len(pkgs)), "green" if done == len(pkgs) else "yellow")
    pause()


def menu_open_all(cfg):
    while True:
        banner(cfg)
        t = Table(title=tr("เปิด / ปิด Roblox ทั้งหมด"), box=box.ROUNDED, header_style="bold cyan")
        for col in ("#", tr("รายการ"), tr("ค่า")):
            t.add_column(col)
        t.add_row("1", tr("เปิด Roblox ทุกอินสแตนซ์ที่เลือกตอนนี้ (ทีละตัว ไม่ force-stop)"), "")
        t.add_row("2", tr("หน่วงระหว่างตัว (วินาที, 0-600)"), str(cfg.d.get("open_all_delay", 10)))
        t.add_row("3", tr("ปิด Roblox ทั้งหมด (force-stop ทุกอินสแตนซ์ที่สแกนเจอ ต้องใช้รูท)"), "")
        console.print(t)
        c = ask(tr("เลือกหมายเลข (0=กลับ)"))
        if c == "0":
            return
        if c == "1":
            launch_all_roblox(cfg)
        elif c == "3":
            kill_all_roblox(cfg)
        elif c == "2":
            v = ask(tr("ค่าหน่วงใหม่ (0-600):"))
            if v.isdigit() and 0 <= int(v) <= 600:
                cfg.d["open_all_delay"] = int(v)
                cfg.save()
            elif v:
                say(tr("ค่าต้องอยู่ระหว่าง 0-600"), "red")
                pause()


def menu_social(cfg):
    if not cookie_pairs(cfg)[0]:
        say(tr("ยังไม่มีคุกกี้ใน cookie.txt (เมนู 5)"), "red")
        return pause()
    say(tr("กำลังตรวจบัญชีจากคุกกี้..."), "dim")
    accts = social_accounts(cfg)
    if not accts:
        return pause()
    while True:
        banner(cfg)
        t = Table(title=tr("บล็อก / เพื่อน (ด้วยบัญชีจากคุกกี้)"), box=box.ROUNDED, header_style="bold cyan")
        for col in ("#", tr("รายการ")):
            t.add_column(col)
        t.add_row("1", tr("บล็อกผู้ใช้"))
        t.add_row("2", tr("เลิกบล็อกผู้ใช้"))
        t.add_row("3", tr("เพิ่มเพื่อน (ส่งคำขอไปยังผู้ใช้)"))
        t.add_row("4", tr("เพิ่มเพื่อนระหว่างบัญชีของเราเอง (ส่งคำขอ + กดรับให้อัตโนมัติ)"))
        t.add_row("5", tr("บล็อกกันเองทั้งหมด (ทุกบัญชีในคุกกี้บล็อกกันเอง ไม่ต้องเลือกเป้าหมาย)"))
        t.add_row("6", tr("ปลดบล็อกกันเองทั้งหมด (ทุกบัญชีในคุกกี้ปลดบล็อกกันเอง เฉพาะคู่ที่บล็อกกันอยู่)"))
        console.print(t)
        a = Table(box=box.SIMPLE, header_style="bold cyan")
        for col in ("#", tr("แพ็กเกจ"), tr("บัญชี")):
            a.add_column(col)
        for n, (pkg, _, _, name) in enumerate(accts, 1):
            a.add_row(str(n), pkg, hide_name(name))
        console.print(a)
        c = ask(tr("เลือกหมายเลข (0=กลับ)"))
        if c == "0":
            return
        if c in ("1", "2", "3"):
            action = {"1": "block", "2": "unblock", "3": "friend"}[c]
            tid, tname = pick_target(accts)
            if not tid:
                say(tr("ไม่พบเป้าหมายนี้"), "red")
                pause()
                continue
            sel = pick_accounts(accts)
            if sel and confirm(tr("%s %s (%d) ด้วย %d บัญชี?") % (SOCIAL_NAME[action], hide_name(tname), tid, len(sel))):
                show_social(run_social(action, sel, tid))
                pause()
        elif c == "6":
            unblock_each_other(cfg, accts)
        elif c == "5":
            block_each_other(cfg, accts)
        elif c == "4":
            sel = pick_accounts(accts)
            if len(sel) < 2:
                say(tr("ต้องเลือกอย่างน้อย 2 บัญชี"), "red")
                pause()
                continue
            if not confirm(tr("เพิ่มเพื่อนระหว่าง %d บัญชี?") % len(sel)):
                continue
            rows = []
            for i in range(len(sel)):
                for j in range(i + 1, len(sel)):
                    (p1, s1, u1, n1), (p2, s2, u2, n2) = sel[i], sel[j]
                    ok, msg = api_post(s1, SOCIAL_URL["friend"] % u2)
                    time.sleep(1.5)
                    if ok:
                        ok, msg = api_post(s2, SOCIAL_URL["accept"] % u1, {})
                        time.sleep(1.5)
                    rows.append((p1 + " ↔ " + p2, hide_name(n1) + " ↔ " + hide_name(n2), ok, msg))
            show_social(rows)
            pause()


def menu_cookie_pull(cfg):
    if not ENV.root:
        say(tr("การดึงคุกกี้จากแอปต้องใช้สิทธิ์รูท"), "yellow")
        return pause()
    det = cfg.d["detected"]
    if not det:
        say(tr("ยังไม่พบแพ็กเกจ ลองเมนู 4 (สแกนแพ็กเกจ)"), "red")
        return pause()
    say(tr("กำลังอ่านคุกกี้จากแอป %d อินสแตนซ์ แล้วตรวจกับ Roblox API...") % len(det), "dim")
    results = pull_validated(det)
    t = Table(title=tr("คุกกี้ที่ดึงจากแอป Roblox"), box=box.ROUNDED, header_style="bold cyan")
    for col in ("#", tr("แพ็กเกจ"), tr("บัญชี"), tr("ผล")):
        t.add_column(col)
    found = {}
    for i, (pkg, ck, name, msg) in enumerate(results, 1):
        if ck:
            found[pkg] = ck
        t.add_row(str(i), pkg, hide_name(name) if name else "-",
                  Text((tr("ดึงได้ ใช้ได้: ") + mask(ck)) if ck else msg, style="green" if ck else "red"))
    console.print(t)
    if found and confirm(tr("บันทึก %d ตัวลง cookie.txt (แทนที่บรรทัดของอินสแตนซ์นั้น)?") % len(found)):
        n = store_cookies(cfg, found)
        say(tr("บันทึกแล้ว %d บรรทัด → ") % n + COOKIE_PATH, "green")
        say(tr("ตอนรีจอยจะใช้คุกกี้เหล่านี้เช็คสถานะในเกมผ่าน API (เปิด \"ตรวจสถานะในเกมผ่าน Roblox API\" ที่เมนู 6)"), "dim")
    pause()


def menu_cookie_hub(cfg):
    while True:
        banner(cfg)
        t = Table(title=tr("คุกกี้"), box=box.ROUNDED, header_style="bold cyan")
        for col in ("#", tr("รายการ")):
            t.add_column(col)
        t.add_row("1", tr("ตรวจ / ใส่คุกกี้จาก cookie.txt ลงแอป (ล็อกอินด้วยคุกกี้)"))
        t.add_row("2", tr("ดึงคุกกี้จากแอป Roblox มาเก็บใน cookie.txt (ไว้เช็คสถานะผ่าน API) — รูท"))
        console.print(t)
        c = ask(tr("เลือกหมายเลข (0=กลับ)"))
        if c == "0":
            return
        if c == "1":
            menu_cookie(cfg)
        elif c == "2":
            menu_cookie_pull(cfg)


AUTO_ITEMS = [
    ("start_on_launch", tr("เริ่มรีจอยทันทีเมื่อเปิดเครื่องมือ"), False),
    ("wake_lock", tr("เปิด Wake-lock ขณะรัน"), False),
    ("force_stop", tr("Force-stop ก่อนเปิดเกมใหม่"), True),
    ("foreground_check", tr("ตรวจว่าแอปอยู่หน้าจอ"), True),
    ("screenshot_on_crash", tr("แคปหน้าจอเมื่อหลุด/ค้าง"), True),
    ("cookie_login_on_start", tr("ล็อกอินด้วยคุกกี้อัตโนมัติก่อนเริ่ม"), True),
    ("presence_check", tr("ตรวจสถานะในเกมผ่าน Roblox API (ต้องมีคุกกี้)"), False),
    ("cookie_pull_on_start", tr("ดึงคุกกี้จากแอปมาเก็บอัตโนมัติก่อนเริ่ม (เฉพาะอินสแตนซ์ที่ยังไม่มีคุกกี้)"), True),
]


def menu_auto(cfg):
    a = cfg.d["auto"]
    while True:
        banner(cfg)
        t = Table(title=tr("ตั้งค่าอัตโนมัติ"), box=box.ROUNDED, header_style="bold cyan")
        for col in ("#", tr("รายการ"), tr("ค่า"), tr("หมายเหตุ")):
            t.add_column(col)
        for i, (k, label, root_only) in enumerate(AUTO_ITEMS, 1):
            t.add_row(str(i), label, onoff(a[k]), tr("เฉพาะรูท") if root_only else "")
        n = len(AUTO_ITEMS)
        t.add_row(str(n + 1), tr("รีจอยตามเวลา (นาที, 0=ปิด)"), str(a["timed_rejoin_min"]), "")
        t.add_row(str(n + 2), tr("เวลารอเข้าเกมหลังเปิดแอป (วินาที)"), str(cfg.d["join_grace"]), "")
        t.add_row(str(n + 3), tr("Heartbeat (สคริปต์ในเกม) ..."), onoff(cfg.d["heartbeat"]["enabled"]), tr("เมนูย่อย"))
        t.add_row(str(n + 4), tr("ประสิทธิภาพ: ล้างแรม / ลดการใช้ CPU ..."), "", tr("เมนูย่อย"))
        console.print(t)
        c = ask(tr("เลือกหมายเลขเพื่อสลับ/แก้ไข (0=กลับ)"))
        if c == "0":
            return
        if c.isdigit() and 1 <= int(c) <= n:
            k = AUTO_ITEMS[int(c) - 1][0]
            a[k] = not a[k]
            cfg.save()
        elif c == str(n + 3):
            menu_heartbeat(cfg)
        elif c == str(n + 4):
            menu_perf(cfg)
        elif c in (str(n + 1), str(n + 2)):
            v = ask(tr("ค่าใหม่ (ตัวเลข):"))
            if v.isdigit():
                if c == str(n + 1):
                    a["timed_rejoin_min"] = int(v)
                else:
                    cfg.d["join_grace"] = max(30, int(v))
                cfg.save()


def fps_cap_summary(cfg):
    default, caps = fps_caps(cfg)
    if not default and not caps:
        return tr("ไม่ตั้ง")
    return tr("%d บัญชี, ค่าเริ่มต้น %s") % (len(caps), default or tr("ไม่แตะ"))


def read_cap(prompt, allow_zero=False):
    """-> (ok, value). Enter = clear (None)."""
    v = ask(prompt).strip()
    if not v:
        return True, None
    if v.isdigit() and (FPS_CAP_MIN <= int(v) <= FPS_CAP_MAX or (allow_zero and int(v) == 0)):
        return True, int(v)
    say(tr("ค่าต้องอยู่ระหว่าง %d-%d") % (FPS_CAP_MIN, FPS_CAP_MAX), "red")
    pause()
    return False, None


def menu_fps_cap(cfg):
    """Per-account FPS cap. The account is identified by its Roblox user ID (fetched from the cookie),
    and the generated heartbeat.lua looks the ID up in-game, so it follows the account, not the clone."""
    fc = cfg.d["heartbeat"].setdefault("fps_cap", {})
    fc.setdefault("default", 0)
    if not isinstance(fc.get("accounts"), dict):
        fc["accounts"] = {}
    if not cookie_pairs(cfg)[0]:
        say(tr("ยังไม่มีคุกกี้ใน cookie.txt (เมนู 5)"), "red")
        return pause()
    say(tr("กำลังดึง Roblox ID จากคุกกี้..."), "dim")
    accts = social_accounts(cfg)
    if not accts:
        return pause()

    def apply():
        cfg.save()
        if cfg.d["autoexec"]["auto_create"]:
            report_autoexec(cfg, install_autoexec(cfg), quiet_ok=False)
        say(tr("สคริปต์ใหม่จะมีผลตอนเข้าเกมครั้งถัดไป (รีจอยหรือเปิดเกมใหม่) ต้องมี setfpscap ในเอ็กซิคิวเตอร์"), "dim")
        pause()

    while True:
        banner(cfg)
        t = Table(title=tr("FPS cap ต่อบัญชี (จับคู่ด้วย Roblox ID)"), box=box.ROUNDED, header_style="bold cyan")
        for col in ("#", tr("แพ็กเกจ"), tr("บัญชี"), "Roblox ID", "FPS cap"):
            t.add_column(col)
        for n, (pkg, _, uid, name) in enumerate(accts, 1):
            rec = fc["accounts"].get(str(uid))
            cap = rec.get("cap") if isinstance(rec, dict) else rec
            t.add_row(str(n), pkg, hide_name(name), hide_id(uid), str(cap) if cap else tr("ไม่ตั้ง"))
        console.print(t)
        n = len(accts)
        a = Table(box=box.SIMPLE, header_style="bold cyan")
        for col in ("#", tr("รายการ"), tr("ค่า")):
            a.add_column(col)
        a.add_row(str(n + 1), tr("ตั้งเท่ากันทุกบัญชีในตาราง"), "")
        a.add_row(str(n + 2), tr("ค่าเริ่มต้นของบัญชีที่ไม่อยู่ในตาราง (0=ไม่แตะ)"), str(fc["default"] or tr("ไม่แตะ")))
        a.add_row(str(n + 3), tr("ล้างค่าทั้งหมด"), "")
        console.print(a)
        c = ask(tr("เลือกหมายเลข (0=กลับ)"))
        if c == "0":
            return
        if c.isdigit() and 1 <= int(c) <= n:
            pkg, _, uid, name = accts[int(c) - 1]
            ok, cap = read_cap(tr("FPS cap ของ %s (%d-%d, Enter=ล้าง):") % (hide_name(name), FPS_CAP_MIN, FPS_CAP_MAX))
            if not ok:
                continue
            if cap:
                fc["accounts"][str(uid)] = {"name": name, "cap": cap}
            else:
                fc["accounts"].pop(str(uid), None)
            apply()
        elif c == str(n + 1):
            ok, cap = read_cap(tr("FPS cap สำหรับทุกบัญชี (%d-%d, Enter=ล้าง):") % (FPS_CAP_MIN, FPS_CAP_MAX))
            if not ok:
                continue
            for _, _, uid, name in accts:
                if cap:
                    fc["accounts"][str(uid)] = {"name": name, "cap": cap}
                else:
                    fc["accounts"].pop(str(uid), None)
            apply()
        elif c == str(n + 2):
            ok, cap = read_cap(tr("ค่าเริ่มต้น (%d-%d, 0 หรือ Enter=ไม่แตะ):") % (FPS_CAP_MIN, FPS_CAP_MAX), allow_zero=True)
            if not ok:
                continue
            fc["default"] = cap or 0
            apply()
        elif c == str(n + 3):
            if confirm(tr("ล้าง FPS cap ทั้งหมด?")):
                fc["accounts"].clear()
                fc["default"] = 0
                apply()


def menu_heartbeat(cfg):
    h = cfg.d["heartbeat"]
    while True:
        banner(cfg)
        t = Table(title=tr("Heartbeat (สคริปต์ในเกม ส่งสัญญาณมาที่เครื่องมือ)"), box=box.ROUNDED, header_style="bold cyan")
        for col in ("#", tr("รายการ"), tr("ค่า")):
            t.add_column(col)
        t.add_row("1", tr("เปิดใช้ heartbeat (เปิดเมื่อติดตั้งสคริปต์ในทุกโคลนแล้ว)"), onoff(h["enabled"]))
        t.add_row("2", tr("พอร์ตรับสัญญาณ (127.0.0.1)"), str(h["port"]))
        t.add_row("3", tr("สคริปต์ส่งทุก (วินาที)"), str(h["interval"]))
        t.add_row("4", tr("ถือว่าหยุดส่งเมื่อเงียบเกิน (วินาที)"), str(h["timeout"]))
        t.add_row("5", tr("สร้างไฟล์สคริปต์ .lua ให้แต่ละอินสแตนซ์ (โฟลเดอร์ heartbeat/)"), "")
        t.add_row("6", tr("โฟลเดอร์ autoexec ของเอ็กซิคิวเตอร์ (Delta / Arceus X, ใส่ {pkg} = แยกโฟลเดอร์ต่อโคลน)"), T(", ".join(cfg.d["autoexec"]["dirs"])))
        t.add_row("7", tr("สร้าง/อัปเดต heartbeat.lua ในโฟลเดอร์นั้นอัตโนมัติ"), onoff(cfg.d["autoexec"]["auto_create"]))
        t.add_row("8", tr("สร้าง/อัปเดต heartbeat.lua ตอนนี้"), "")
        t.add_row("9", tr("แสดงตัวเลข FPS บนจอเกม (ลากได้ ไม่มีกรอบ ปรับขนาดตามจอ)"), onoff(h["show_fps"]))
        t.add_row("10", tr("FPS cap ต่อบัญชี (ดึง Roblox ID จากคุกกี้) ..."), T(fps_cap_summary(cfg)))
        console.print(t)
        c = ask(tr("เลือกหมายเลข (0=กลับ)"))
        if c == "0":
            return
        if c == "1":
            h["enabled"] = not h["enabled"]
        elif c in ("2", "3", "4"):
            key, lo, hi = {"2": ("port", 1024, 65535), "3": ("interval", 5, 120), "4": ("timeout", 30, 600)}[c]
            v = ask(tr("ค่าใหม่ (%d-%d):") % (lo, hi))
            if v.isdigit() and lo <= int(v) <= hi:
                h[key] = int(v)
                if cfg.d["autoexec"]["auto_create"]:
                    report_autoexec(cfg, install_autoexec(cfg))   # keep the installed script in sync (port/interval)
            elif v:
                say(tr("ค่าต้องอยู่ระหว่าง %d-%d") % (lo, hi), "red")
                pause()
        elif c == "5":
            for p in write_heartbeat_files(cfg):
                say(tr("สร้างแล้ว: ") + p, "green")
            say(tr("คัดลอกไฟล์ของแต่ละโคลนไปไว้ในโฟลเดอร์ autoexec ของเอ็กซิคิวเตอร์ในโคลนนั้น (หรือรันตอนเข้าเกม)"), "dim")
            pause()
        elif c == "6":
            v = ask(tr("พาธโฟลเดอร์ autoexec (Enter=ไม่เปลี่ยน, คั่นหลายโฟลเดอร์ด้วยคอมมา):"))
            if v:
                cfg.d["autoexec"]["dirs"] = [x.strip() for x in v.split(",") if x.strip().startswith("/")] or cfg.d["autoexec"]["dirs"]
        elif c == "7":
            cfg.d["autoexec"]["auto_create"] = not cfg.d["autoexec"]["auto_create"]
        elif c == "8":
            report_autoexec(cfg, install_autoexec(cfg), quiet_ok=False)
            pause()
        elif c == "9":
            h["show_fps"] = not h["show_fps"]
            if cfg.d["autoexec"]["auto_create"]:
                report_autoexec(cfg, install_autoexec(cfg), quiet_ok=False)
                say(tr("สคริปต์ใหม่จะโหลดตอนเข้าเกมครั้งถัดไป (รีจอยหรือเปิดเกมใหม่)"), "dim")
                pause()
        elif c == "10":
            menu_fps_cap(cfg)
        cfg.save()


def menu_perf(cfg):
    r, p = cfg.d["ram"], cfg.d["performance"]
    while True:
        banner(cfg)
        t = Table(title=tr("ประสิทธิภาพ: ล้างแรม / ลดการใช้ CPU"), box=box.ROUNDED, header_style="bold cyan")
        for col in ("#", tr("รายการ"), tr("ค่า")):
            t.add_column(col)
        t.add_row("1", tr("ล้างแรมอัตโนมัติ (เฉพาะรูท)"), onoff(r["enabled"]))
        t.add_row("2", tr("ล้างแรมทุก (นาที, 0=ไม่ล้างตามเวลา)"), str(r["interval_min"]))
        t.add_row("3", tr("ล้างเมื่อแรมว่างต่ำกว่า (MB, 0=ปิด)"), str(r["min_free_mb"]))
        t.add_row("4", tr("ปิดแอปพื้นหลังที่ cached ด้วย (am kill-all) ⚠ อาจปิดโคลนที่อยู่เบื้องหลัง"), onoff(r["kill_cached"]))
        t.add_row("5", tr("ปิดแอปอื่นที่ไม่เกี่ยวข้องแบบปลอดภัย (force-stop แอปที่ติดตั้งเอง เว้น Roblox/Termux/Delta และรายชื่อข้อ 6)"), onoff(r["kill_others"]))
        t.add_row("6", tr("รายชื่อแอปที่ห้ามปิด (ส่วนของชื่อแพ็กเกจ)"), ", ".join(r["protect"]))
        t.add_row("7", tr("โหมดประหยัด CPU (ลดความสำคัญโปรเซส, เก็บสถานะเกมไว้ 60 วินาที, รีเฟรชจอช้าลง)"), onoff(p["low_cpu"]))
        t.add_row("8", tr("รีเฟรชแผงสถานะทุก (วินาที, ใช้เมื่อเปิดโหมดประหยัด CPU)"), str(p["ui_refresh_sec"]))
        t.add_row("9", tr("ล้างแรมเดี๋ยวนี้"), "")
        console.print(t)
        free = mem_available_mb()
        say(tr("RAM ว่างตอนนี้: %s MB%s") % (free if free is not None else "?", "" if ENV.root else tr("  (โหมดไม่รูทล้างแรมไม่ได้)")), "dim")
        c = ask(tr("เลือกหมายเลข (0=กลับ)"))
        if c == "0":
            return
        if c == "1":
            r["enabled"] = not r["enabled"]
        elif c == "4":
            r["kill_cached"] = not r["kill_cached"]
        elif c == "5":
            r["kill_others"] = not r["kill_others"]
        elif c == "6":
            v = ask(tr("รายชื่อที่ห้ามปิด คั่นด้วยคอมมา (Enter=คงเดิม):"))
            if v:
                r["protect"] = [x.strip() for x in v.split(",") if x.strip()]
        elif c == "7":
            p["low_cpu"] = not p["low_cpu"]
        elif c in ("2", "3", "8"):
            sec, key, lo, hi = {"2": (r, "interval_min", 0, 1440), "3": (r, "min_free_mb", 0, 8192),
                                "8": (p, "ui_refresh_sec", 1, 10)}[c]
            v = ask(tr("ค่าใหม่ (%d-%d):") % (lo, hi))
            if v.isdigit() and lo <= int(v) <= hi:
                sec[key] = int(v)
            elif v:
                say(tr("ค่าต้องอยู่ระหว่าง %d-%d") % (lo, hi), "red")
                pause()
        elif c == "9":
            ok, b4, af = drop_ram(r["kill_cached"], cfg)
            say((tr("ล้างแรมแล้ว: ว่าง %s → %s MB") % (b4, af)) if ok else (tr("ล้างแรมไม่สำเร็จ: ") + RAM_LAST["detail"]),
                "green" if ok else "red")
            if ok:
                say(tr("ผลแต่ละขั้น: ") + RAM_LAST["detail"], "dim")
                if b4 is not None and af is not None and af - b4 < 100:
                    say(tr("แรมว่างแทบไม่เพิ่ม: drop_caches ล้างได้เฉพาะแคช ไม่ใช่แรมที่แอปใช้อยู่"), "yellow")
            elif ENV.root and not (r["kill_cached"] or r["kill_others"]):
                say(tr("ลองเปิดข้อ 5 (ปิดแอปอื่นแบบปลอดภัย) เมื่อ drop_caches ถูกล็อก (มักเป็นตู้คลาวด์/คอนเทนเนอร์)"), "yellow")
            pause()
        cfg.save()


def menu_webhook(cfg, hook):
    w = cfg.d["webhook"]
    names = [("start", tr("เริ่มทำงาน")), ("crash", tr("หลุด/ค้าง")), ("rejoin", tr("รีจอยแล้ว")), ("stop", tr("หยุดทำงาน")),
             ("status", tr("รายงานสถานะ+ภาพหน้าจอเป็นรอบ")), ("alert", tr("แจ้งเตือนผิดปกติ (รีจอยถี่/แรมต่ำ)"))]
    n_shot = 3 + len(names)
    n_per = n_shot + 1
    n_cu, n_ce, n_cf, n_ct, n_test = n_per + 1, n_per + 2, n_per + 3, n_per + 4, n_per + 5
    cw = cfg.d["console_webhook"]
    while True:
        banner(cfg)
        t = Table(title="Discord Webhook", box=box.ROUNDED, header_style="bold cyan")
        for col in ("#", tr("รายการ"), tr("ค่า")):
            t.add_column(col)
        t.add_row("1", "URL Webhook", T(scrub(w["url"]) or tr("(ยังไม่ตั้ง)")))
        t.add_row("2", tr("เปิดใช้งาน Webhook"), onoff(w["enabled"]))
        for i, (k, label) in enumerate(names, 3):
            t.add_row(str(i), tr("แจ้งเตือน: ") + label, onoff(w["events"][k]))
        t.add_row(str(n_shot), tr("แนบภาพหน้าจอ (รูทเท่านั้น, เก็บล่าสุด %d รูป)") % MAX_SHOTS, onoff(w["send_screenshot"]))
        t.add_row(str(n_per), tr("ส่งรายงานสถานะ+ภาพหน้าจอทุกกี่นาที (0=ปิด)"), str(w["periodic_min"]))
        t.add_row(str(n_cu), tr("Console webhook: URL (ว่าง = ใช้ URL หลัก)"), T(scrub(cw["url"]) or tr("(ใช้ URL หลัก)")))
        t.add_row(str(n_ce), tr("Console webhook: ส่งบันทึกคอนโซลเข้า Discord"), onoff(cw["enabled"]))
        t.add_row(str(n_cf), tr("Console webhook: รวมส่งทุก (วินาที, 3-300)"), str(cw["flush_sec"]))
        t.add_row(str(n_ct), tr("ทดสอบ Console webhook"), "")
        t.add_row(str(n_test), tr("ส่งข้อความทดสอบ"), "")
        console.print(t)
        c = ask(tr("เลือกหมายเลข (0=กลับ)"))
        if c == "0":
            return
        if c == "1":
            v = ask(tr("วาง URL Webhook (Enter=ลบ):"))
            if not v:
                w["url"], w["enabled"] = "", False
            elif HOOK_RE.match(v):
                w["url"] = v
            else:
                say(tr("รูปแบบ URL ไม่ถูกต้อง"), "red")
                pause()
        elif c == "2":
            w["enabled"] = not w["enabled"]
        elif c.isdigit() and 3 <= int(c) < n_shot:
            k = names[int(c) - 3][0]
            w["events"][k] = not w["events"][k]
        elif c == str(n_shot):
            w["send_screenshot"] = not w["send_screenshot"]
        elif c == str(n_per):
            v = ask(tr("ทุกกี่นาที (0-1440):"))
            if v.isdigit() and 0 <= int(v) <= 1440:
                w["periodic_min"] = int(v)
            elif v:
                say(tr("ต้องเป็นตัวเลข 0-1440"), "red")
                pause()
        elif c == str(n_cu):
            v = ask(tr("วาง URL Console webhook (Enter=ใช้ URL หลัก):"))
            if not v:
                cw["url"] = ""
            elif HOOK_RE.match(v):
                cw["url"] = v
            else:
                say(tr("รูปแบบ URL ไม่ถูกต้อง"), "red")
                pause()
        elif c == str(n_ce):
            cw["enabled"] = not cw["enabled"]
        elif c == str(n_cf):
            v = ask(tr("ค่าใหม่ (3-300):"))
            if v.isdigit() and 3 <= int(v) <= 300:
                cw["flush_sec"] = int(v)
            elif v:
                say(tr("ต้องเป็นตัวเลข 3-300"), "red")
                pause()
        elif c == str(n_ct):
            ch = ConsoleHook(cfg)
            if not ch.url():
                say(tr("ยังไม่มี URL (ตั้ง URL หลักหรือ Console URL ก่อน)"), "red")
            else:
                ok = ch._post(tr("GARNT TOOL REJOIN: ทดสอบ console webhook ✅"))
                say(tr("ส่งสำเร็จ") if ok else tr("ส่งไม่สำเร็จ ตรวจสอบ URL"), "green" if ok else "red")
            pause()
        elif c == str(n_test):
            ok = hook.send("test", tr("✅ ทดสอบ Webhook"), tr("เชื่อมต่อสำเร็จ"), [(tr("โหมด"), "ROOT" if ENV.root else tr("ไม่รูท"))], sync=True)
            say(tr("ส่งสำเร็จ") if ok else tr("ส่งไม่สำเร็จ ตรวจสอบ URL"), "green" if ok else "red")
            pause()
        cfg.save()


def menu_list(cfg):
    """Menu 10: list of detected instances + heartbeat ID, account name and Roblox ID (name/ID fetched from the cookie)."""
    det = cfg.d["detected"]
    if not det:
        say(tr("ยังไม่พบแพ็กเกจ ลองเมนู 4 (สแกนแพ็กเกจ)"), "red")
        return pause()
    pairs, n_ck = cookie_pairs(cfg)
    cmap = dict(pairs)
    sel = selected_packages(cfg)
    who = {}                                   # pkg -> (name, uid); stays empty on bad cookie / no network
    if cmap:
        say(tr("กำลังดึงชื่อบัญชี / Roblox ID จากคุกกี้..."), "dim")
        for pkg, ck in pairs:
            uid, name, _ = who_am_i(make_session(ck))
            if uid:
                who[pkg] = (name, str(uid))
    banner(cfg)
    t = Table(title=tr("รายการอินสแตนซ์ (%d)") % len(det), box=box.ROUNDED, header_style="bold cyan")
    for col in ("#", tr("แพ็กเกจ"), "Heartbeat ID", tr("บัญชี"), "Roblox ID", tr("เลือกรัน"), tr("คุกกี้")):
        t.add_column(col)
    for i, pkg in enumerate(det, 1):
        name, uid = who.get(pkg, ("-", "-"))
        t.add_row(str(i), pkg, re.sub(r"[^\w.\-]", "", pkg), name, uid, onoff(pkg in sel),
                  Text(tr("มี"), style="green") if pkg in cmap else Text("-", style="dim"))
    console.print(t)
    link = cfg.d["game_link"]
    say(tr("เลือกรัน %d/%d | คุกกี้ในไฟล์ %d บรรทัด | ลิงก์เกม: %s") % (
        len(sel), len(det), n_ck, (link[:60] + "…" if len(link) > 60 else link) or tr("(ยังไม่ตั้ง)")), "dim")
    pause()


UPDATE_URL = "https://raw.githubusercontent.com/Garnt024020806/Free-Tool-Rejoin-Ug-open-source/main/rejoin.py"


def _ver_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v)) or (0,)


UPDATE_INFO = {"remote": None, "checked": 0.0}


def check_new_version(cfg=None, hook=None):
    """Silent check: remote VERSION -> UPDATE_INFO. Pushes a webhook once per new version. Never raises."""
    try:
        r = requests.get(UPDATE_URL, timeout=10, headers={"Cache-Control": "no-cache"})
        if r.status_code != 200:
            return None
        m = re.search(r'^VERSION\s*=\s*"([^"]+)"', r.text, re.M)
        if not m:
            return None
        remote = m.group(1)
        UPDATE_INFO["checked"] = time.time()
        if _ver_tuple(remote) <= _ver_tuple(VERSION):
            UPDATE_INFO["remote"] = None
            return None
        UPDATE_INFO["remote"] = remote
        log.info(tr("มีเวอร์ชันใหม่ v%s (ตอนนี้ v%s)"), remote, VERSION)
        if cfg and hook and cfg.d["update"].get("notified") != remote:
            cfg.d["update"]["notified"] = remote
            cfg.save()
            hook.send("update", tr("🆕 มีเวอร์ชันใหม่"), tr("v%s -> v%s  (เมนู 11 หรือ python rejoin.py update)") % (VERSION, remote))
        return remote
    except Exception as e:
        log.debug(tr("เช็คเวอร์ชันไม่ได้: %s"), scrub(e))
        return None


def start_update_watch(cfg, hook):
    if not cfg.d["update"].get("check", True):
        return

    def loop():
        while True:
            check_new_version(cfg, hook)
            try:
                hrs = max(1, int(cfg.d["update"].get("hours", 6)))
            except (TypeError, ValueError):
                hrs = 6
            time.sleep(hrs * 3600)
    threading.Thread(target=loop, daemon=True).start()


def run_update():
    """Fetch rejoin.py from GitHub, compare VERSION, replace this file only when the remote is newer.
    -> "latest" | "updated" | "newer_local" | "error" """
    say(tr("เวอร์ชันปัจจุบัน v%s กำลังเช็คเวอร์ชันบน GitHub...") % VERSION, "dim")
    try:
        r = requests.get(UPDATE_URL, timeout=20, headers={"Cache-Control": "no-cache"})
    except Exception as e:
        say(tr("ต่อ GitHub ไม่ได้: %s") % scrub(e)[:80], "red")
        return "error"
    if r.status_code != 200:
        say(tr("โหลดไม่สำเร็จ HTTP %d") % r.status_code, "red")
        return "error"
    code = r.content.replace(b"\r\n", b"\n")
    m = re.search(r'^VERSION\s*=\s*"([^"]+)"', code.decode("utf-8", "replace"), re.M)
    if not m:
        say(tr("ไฟล์บน GitHub ไม่มี VERSION ไม่อัปเดต"), "red")
        return "error"
    remote = m.group(1)
    rt, lt = _ver_tuple(remote), _ver_tuple(VERSION)
    if rt == lt:
        say(tr("ตัวนี้เป็นเวอร์ชันล่าสุดแล้ว (v%s) ไม่ต้องอัปเดต") % VERSION, "green")
        return "latest"
    if rt < lt:
        say(tr("เครื่องใหม่กว่า GitHub (เครื่อง v%s / GitHub v%s) ไม่ลดเวอร์ชัน") % (VERSION, remote), "yellow")
        return "newer_local"
    try:
        compile(code, "rejoin.py", "exec")          # never install a file that doesn't even parse
    except SyntaxError as e:
        say(tr("ไฟล์ใหม่มีโค้ดพัง (%s) ไม่อัปเดต") % e, "red")
        return "error"
    me = os.path.abspath(__file__)
    try:
        shutil.copy2(me, me + ".bak")
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(me), suffix=".tmp")
        with os.fdopen(fd, "wb") as f:
            f.write(code)
        os.replace(tmp, me)
    except Exception as e:
        say(tr("เขียนไฟล์ไม่ได้: %s") % scrub(e)[:80], "red")
        return "error"
    sd = "/sdcard/Download/rejoin.py"                # setup.sh keeps a copy here too
    if os.path.isfile(sd) and os.path.abspath(sd) != me:
        try:
            with open(sd, "wb") as f:
                f.write(code)
        except Exception:
            pass
    say(tr("อัปเดตแล้ว v%s -> v%s (เก็บของเก่าไว้ที่ %s.bak) รันโปรแกรมใหม่เพื่อใช้เวอร์ชันนี้") % (VERSION, remote, os.path.basename(me)), "green")
    UPDATE_INFO["remote"] = None
    return "updated"


def run_rollback():
    """Swap rejoin.py with rejoin.py.bak (the copy kept by run_update). Checks the backup parses first.
    The current file becomes the new .bak, so running it twice returns to where you were. -> "ok" | "error" """
    me = os.path.abspath(__file__)
    bak = me + ".bak"
    if not os.path.isfile(bak):
        say(tr("ไม่พบไฟล์สำรอง %s.bak (ยังไม่เคยอัปเดต?)") % os.path.basename(me), "red")
        return "error"
    try:
        with open(bak, "rb") as f:
            old = f.read()
        with open(me, "rb") as f:
            cur = f.read()
        compile(old, "rejoin.py.bak", "exec")
    except (OSError, SyntaxError) as e:
        say(tr("ไฟล์สำรองใช้ไม่ได้: %s") % scrub(e)[:80], "red")
        return "error"
    m = re.search(r'^VERSION\s*=\s*"([^"]+)"', old.decode("utf-8", "replace"), re.M)
    try:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(me), suffix=".tmp")
        with os.fdopen(fd, "wb") as f:
            f.write(old)
        os.replace(tmp, me)
        with open(bak, "wb") as f:
            f.write(cur)
    except OSError as e:
        say(tr("ย้อนกลับไม่ได้: %s") % scrub(e)[:80], "red")
        return "error"
    sd = "/sdcard/Download/rejoin.py"
    if os.path.isfile(sd) and os.path.abspath(sd) != me:
        try:
            with open(sd, "wb") as f:
                f.write(old)
        except OSError:
            pass
    say(tr("ย้อนกลับเป็น v%s แล้ว (เวอร์ชันที่เพิ่งใช้อยู่ถูกเก็บเป็น .bak) รันโปรแกรมใหม่เพื่อใช้งาน") % (m.group(1) if m else "?"), "green")
    return "ok"


def menu_update():
    say(tr("1) อัปเดตจาก GitHub   2) ย้อนกลับเป็นเวอร์ชันก่อนหน้า (.bak)   0) กลับ"), "cyan")
    c = ask(tr("เลือก (Enter=1):"), "1")
    if c == "1":
        run_update()
    elif c == "2":
        if confirm(tr("ย้อนกลับเป็นไฟล์สำรอง?")):
            run_rollback()
    else:
        return
    pause()


# ----------------------------------------------------------------- เทสเครื่อง (Cloud Phone): CPU / RAM / GPU
def _sysread(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        pass
    if ENV.root:
        rc, out = sh("cat " + shlex.quote(path), root=True, timeout=5)
        if rc == 0 and out.strip():
            return out.strip()
    return None


def _prop(name):
    rc, out = sh("getprop " + name, timeout=5)
    return out.strip() if rc == 0 else ""


def _cpu_work(sec):
    """Worker (module-level so multiprocessing can pickle it): SHA-256 hashes/second for `sec` seconds."""
    import hashlib
    h, n = b"garnt" * 12, 0
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        for _ in range(500):
            h = hashlib.sha256(h).digest()
        n += 500
    return n


def _cpu_model():
    try:
        with open("/proc/cpuinfo") as f:
            txt = f.read()
        for key in ("Hardware", "model name", "Processor"):
            m = re.search(r"^%s\s*:\s*(.+)$" % key, txt, re.M)
            if m and m.group(1).strip():
                return m.group(1).strip()
    except Exception:
        pass
    return _prop("ro.soc.model") or _prop("ro.board.platform") or "?"


def _fmt_mhz(khz):
    return "%d MHz" % (int(khz) // 1000) if khz and khz.isdigit() else "?"


def bench_cpu(secs=4):
    import platform
    from multiprocessing import Pool
    cores = os.cpu_count() or 1
    rows = [(tr("รุ่น CPU"), _cpu_model()), (tr("สถาปัตยกรรม"), platform.machine() or "?"), (tr("จำนวนคอร์"), str(cores))]
    base = "/sys/devices/system/cpu/cpu0/cpufreq/"
    rows.append(((tr("ความเร็ว (ตอนนี้ / สูงสุด)")),
                 "%s / %s" % (_fmt_mhz(_sysread(base + "scaling_cur_freq")), _fmt_mhz(_sysread(base + "cpuinfo_max_freq")))))
    say(tr("กำลังทดสอบ CPU 1 คอร์ (%d วินาที) ...") % secs, "dim")
    t0 = time.perf_counter()
    n1 = _cpu_work(secs)
    single = n1 / (time.perf_counter() - t0)
    rows.append((tr("คะแนน 1 คอร์"), "%.0f k hash/s" % (single / 1000)))
    multi, usage = None, None
    if cores > 1:
        say(tr("กำลังทดสอบ CPU ทุกคอร์ (%d คอร์ x %d วินาที) ...") % (cores, secs), "dim")
        try:
            t0 = time.perf_counter()
            with Pool(cores) as p:
                res = p.map_async(_cpu_work, [secs] * cores)
                time.sleep(max(1.0, secs / 2.0))
                try:
                    usage = CPU.read(wait=1.0)["now"]
                except Exception:
                    usage = None
                total = sum(res.get(timeout=secs + 60))
            multi = total / (time.perf_counter() - t0)
        except Exception as e:
            rows.append((tr("ทดสอบหลายคอร์"), tr("ไม่สำเร็จ: %s") % e))
    if multi:
        rows.append((tr("คะแนนทุกคอร์"), "%.0f k hash/s" % (multi / 1000)))
        rows.append((tr("ประสิทธิภาพเพิ่มขึ้น"), tr("x%.2f จาก %d คอร์ (เต็ม = x%d)") % (multi / single, cores, cores)))
        if usage is not None:
            rows.append((tr("CPU ใช้งานระหว่างเทส"), "%.0f%%" % usage))
        if multi / single < cores * 0.6:
            rows.append((tr("หมายเหตุ"), tr("ได้ไม่ถึง 60% ของคอร์ที่มี: อาจโดนจำกัด CPU / แชร์เครื่องกับคนอื่น / มีแอปอื่นกินอยู่")))
    return rows


def bench_ram(max_mb=64):
    rows, info = [], {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                k, _, v = line.partition(":")
                info[k] = int(v.split()[0]) // 1024
    except Exception:
        pass
    total, avail = info.get("MemTotal"), mem_available_mb()
    rows.append((tr("RAM ทั้งหมด"), "%s MB" % total if total else "?"))
    rows.append((tr("RAM ว่าง"), "%s MB" % avail if avail is not None else "?"))
    if total and avail is not None:
        rows.append((tr("ใช้ไปแล้ว"), "%d MB (%.0f%%)" % (total - avail, 100.0 * (total - avail) / total)))
    if info.get("SwapTotal"):
        rows.append((tr("Swap (ใช้/ทั้งหมด)"), "%d / %d MB" % (info["SwapTotal"] - info.get("SwapFree", 0), info["SwapTotal"])))
    size_mb = max(8, min(max_mb, int((avail or 256) * 0.15)))
    size = size_mb * 1024 * 1024
    try:
        say(tr("กำลังทดสอบความเร็วแรม (บล็อก %d MB) ...") % size_mb, "dim")
        t0 = time.perf_counter()
        a = bytearray(size)
        for i in range(0, size, 4096):          # เขียนทุกหน้าแรมให้จองจริง
            a[i] = 1
        alloc = size_mb / max(1e-6, time.perf_counter() - t0)
        pat = bytes(range(256)) * (size // 256)
        t0 = time.perf_counter()
        a[:] = pat
        write = size_mb / max(1e-6, time.perf_counter() - t0)
        b, rounds = bytearray(size), 0
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < 3:
            b[:] = a
            rounds += 1
        copy = size_mb * rounds / max(1e-6, time.perf_counter() - t0)
        ok = bytes(b) == pat
        rows += [(tr("จองแรม + แตะทุกหน้า"), "%.0f MB/s" % alloc), (tr("เขียน"), "%.0f MB/s" % write),
                 (tr("คัดลอก"), "%.0f MB/s" % copy), (tr("ตรวจความถูกต้องของข้อมูล"), tr("ผ่าน") if ok else tr("ไม่ผ่าน ⚠ แรมอาจมีปัญหา"))]
        del a, b, pat
    except MemoryError:
        rows.append((tr("ทดสอบความเร็ว"), tr("จองแรมไม่ได้ (MemoryError)")))
    except Exception as e:
        rows.append((tr("ทดสอบความเร็ว"), tr("ไม่สำเร็จ: %s") % e))
    return rows


def _gpu_load_paths():
    import glob
    paths = ["/sys/class/kgsl/kgsl-3d0/gpubusy", "/sys/class/kgsl/kgsl-3d0/gpu_busy_percentage",
             "/sys/kernel/gpu/gpu_busy", "/sys/class/misc/mali0/device/utilization",
             "/sys/devices/platform/mali/utilization"]
    for d in glob.glob("/sys/class/devfreq/*"):
        if re.search(r"gpu|kgsl|mali|g3d", os.path.basename(d), re.I):
            paths.append(d + "/load")
    return paths


def _parse_load(s):
    n = re.findall(r"\d+", s or "")
    if len(n) >= 2 and int(n[1]) > 0:
        return min(100.0, 100.0 * int(n[0]) / int(n[1]))
    if len(n) == 1:
        return float(n[0])
    return None


def bench_gpu():
    rows = []
    gles = ""
    for root in (False, True):
        if root and not ENV.root:
            break
        rc, out = sh("dumpsys SurfaceFlinger | grep -i -m1 'GLES'", root=root, timeout=15)
        if rc == 0 and out.strip():
            gles = out.strip()
            break
    if gles:
        rows.append(("GPU / OpenGL ES", gles[:110]))
    else:
        rows.append(("GPU / OpenGL ES", tr("อ่านไม่ได้ (dumpsys SurfaceFlinger ต้องใช้สิทธิ์ ลองโหมดรูท)")))
    ver = _prop("ro.opengles.version")
    if ver.isdigit():
        v = int(ver)
        rows.append((tr("OpenGL ES ที่รองรับ"), "%d.%d" % (v >> 16, v & 0xFFFF)))
    for label, key in ((tr("ไดรเวอร์ EGL"), "ro.hardware.egl"), ("Vulkan", "ro.hardware.vulkan"),
                       (tr("แพลตฟอร์ม"), "ro.board.platform")):
        val = _prop(key)
        if val:
            rows.append((label, val))
    soft = re.search(r"swiftshader|llvmpipe|softpipe|software|angle", " ".join([gles, _prop("ro.hardware.egl")]), re.I)
    if soft:
        rows.append((tr("⚠ คำเตือน"), tr("ดูเหมือนเป็นซอฟต์แวร์เรนเดอร์ (ไม่มี GPU จริง) กราฟิกจะกิน CPU และช้ากว่ามาก")))
    path, load = None, None
    for p in _gpu_load_paths():
        s = _sysread(p)
        if s and _parse_load(s) is not None:
            path = p
            break
    if path:
        say(tr("กำลังวัดโหลด GPU 5 วินาที ..."), "dim")
        vals = []
        for _ in range(5):
            time.sleep(1)
            v = _parse_load(_sysread(path))
            if v is not None:
                vals.append(v)
        if vals:
            rows.append((tr("โหลด GPU (เฉลี่ย / สูงสุด)"), "%.0f%% / %.0f%%" % (sum(vals) / len(vals), max(vals))))
    else:
        rows.append((tr("โหลด GPU"), tr("อ่านไม่ได้ (เครื่องนี้ไม่เปิด sysfs ของ GPU ให้อ่าน)")))
    for p in ("/sys/class/kgsl/kgsl-3d0/devfreq/cur_freq", "/sys/class/kgsl/kgsl-3d0/gpuclk"):
        s = _sysread(p)
        if s and s.isdigit():
            rows.append((tr("ความเร็ว GPU"), "%d MHz" % (int(s) // 1000000 if int(s) > 100000 else int(s))))
            break
    rows.append((tr("หมายเหตุ"), tr("Termux สร้างงานกราฟิกจริงไม่ได้ จึงวัดได้แค่ข้อมูล + โหลด; วัดคะแนนจริงใช้ GFXBench / 3DMark")))
    return rows


def _show_bench(title, rows):
    t = Table(title=title, box=box.ROUNDED, header_style="bold cyan", show_lines=False)
    t.add_column(tr("รายการ"), style="bold")
    t.add_column(tr("ผล"), overflow="fold")
    for k, v in rows:
        t.add_row(k, v)
    console.print(t)
    log.info("bench %s: %s", title, "; ".join("%s=%s" % r for r in rows))


def menu_bench(cfg):
    tests = {"1": (tr("ทดสอบ CPU"), bench_cpu), "2": (tr("ทดสอบ RAM"), bench_ram), "3": (tr("ทดสอบ GPU"), bench_gpu)}
    while True:
        banner(cfg)
        t = Table(title=tr("เทสเครื่อง Cloud Phone"), box=box.ROUNDED, header_style="bold cyan")
        t.add_column("#")
        t.add_column(tr("รายการ"))
        t.add_row("1", tr("CPU — ข้อมูลคอร์ + คะแนน 1 คอร์ / ทุกคอร์"))
        t.add_row("2", tr("RAM — ความจุ + ความเร็วเขียน/คัดลอก + ตรวจความถูกต้อง"))
        t.add_row("3", tr("GPU — รุ่น/ไดรเวอร์ + โหลด (ถ้าอ่านได้)"))
        t.add_row("4", tr("ทดสอบทั้งหมด (ใช้เวลาราว 30 วินาที)"))
        console.print(t)
        say(tr("แนะนำ: หยุดรีจอย/ปิดเกมก่อนเทส เพื่อให้ผลไม่เพี้ยน"), "dim")
        c = ask(tr("เลือกหมายเลข (0=กลับ)"))
        if c == "0":
            return
        picks = list(tests) if c == "4" else ([c] if c in tests else [])
        if not picks:
            continue
        try:
            for k in picks:
                name, fn = tests[k]
                _show_bench(name, fn())
        except KeyboardInterrupt:
            say(tr("ยกเลิกการทดสอบ"), "yellow")
        pause()


def backup_files():
    try:
        names = [n for n in os.listdir(BACKUP_DIR) if n.endswith(".json")]
    except OSError:
        return []
    return sorted(names, reverse=True)


def export_config(cfg, tag="config"):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    path = os.path.join(BACKUP_DIR, "%s-%s.json" % (tag, datetime.now().strftime("%Y%m%d-%H%M%S")))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg.d, f, ensure_ascii=False, indent=2)
    for n in [n for n in backup_files() if n.startswith("config-")][10:]:   # keep newest 10 manual exports
        try:
            os.remove(os.path.join(BACKUP_DIR, n))
        except OSError:
            pass
    return path


def import_config(cfg, name):
    path = os.path.join(BACKUP_DIR, os.path.basename(name))
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError(tr("ไฟล์ไม่ใช่ค่าตั้งค่า"))
    new = merge(DEFAULTS, raw)
    export_config(cfg, "before-import")
    cfg.d.clear()
    cfg.d.update(new)
    cfg.save()


LOG_TS = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ \[\w+\] (.*)$")
LOG_CRASH = re.compile(r"^(#\d+ \S+): (.+?) \((?:ครั้งที่ |#)\d+\) (?:รีจอยใน|rejoining in) \d+s$")
LOG_REJOIN = re.compile(r"^(#\d+ \S+): (?:รีจอยแล้ว|rejoined) \((?:ครั้งที่ |#)\d+\)$")


def log_files():
    try:
        return sorted(os.path.join(LOG_DIR, n) for n in os.listdir(LOG_DIR) if n.startswith("rejoin.log"))
    except OSError:
        return []


def log_summary(hours):
    """-> {label: {"crash": n, "rejoin": n, "reasons": {text: n}}} for the last `hours` hours."""
    since = datetime.now().timestamp() - hours * 3600
    out = {}
    for fp in log_files():
        try:
            if os.path.getmtime(fp) < since:
                continue
            with open(fp, encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = LOG_TS.match(line.rstrip("\n"))
                    if not m:
                        continue
                    try:
                        if datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp() < since:
                            continue
                    except ValueError:
                        continue
                    msg = m.group(2)
                    c, r = LOG_CRASH.match(msg), LOG_REJOIN.match(msg)
                    if c:
                        d = out.setdefault(c.group(1), {"crash": 0, "rejoin": 0, "reasons": {}})
                        d["crash"] += 1
                        d["reasons"][c.group(2)] = d["reasons"].get(c.group(2), 0) + 1
                    elif r:
                        out.setdefault(r.group(1), {"crash": 0, "rejoin": 0, "reasons": {}})["rejoin"] += 1
        except OSError:
            continue
    return out


def show_log_tail(n=30):
    fp = os.path.join(LOG_DIR, "rejoin.log")
    try:
        with open(fp, encoding="utf-8", errors="replace") as f:
            lines = list(deque(f, maxlen=n))
    except OSError:
        say(tr("ยังไม่มีไฟล์ log"), "yellow")
        return
    for ln in lines:
        say(ln.rstrip("\n")[:200])


def menu_alert(cfg):
    a = cfg.d["alert"]
    rows = [("enabled", tr("เปิดการแจ้งเตือนผิดปกติ"), None),
            ("storm_count", tr("หลุด/ค้างกี่ครั้งถึงแจ้ง (2-50)"), (2, 50)),
            ("storm_window_min", tr("ภายในกี่นาที (1-1440)"), (1, 1440)),
            ("cooldown_min", tr("เว้นกี่นาทีก่อนแจ้งซ้ำ (1-1440)"), (1, 1440)),
            ("low_ram_mb", tr("แจ้งเมื่อแรมว่างต่ำกว่า MB (0=ใช้ค่าของล้างแรม)"), (0, 100000))]
    while True:
        banner(cfg)
        t = Table(title=tr("แจ้งเตือนผิดปกติ (ส่งเข้า Discord ผ่านเหตุการณ์ \"แจ้งเตือนผิดปกติ\" ในเมนู 7)"), box=box.ROUNDED, header_style="bold cyan")
        for col in ("#", tr("รายการ"), tr("ค่า")):
            t.add_column(col)
        for i, (k, label, rng) in enumerate(rows, 1):
            t.add_row(str(i), label, onoff(a[k]) if rng is None else str(a[k]))
        console.print(t)
        c = ask(tr("เลือกหมายเลข (0=กลับ)"))
        if c == "0":
            return
        if c == "1":
            a["enabled"] = not a["enabled"]
        elif c.isdigit() and 2 <= int(c) <= len(rows):
            k, label, rng = rows[int(c) - 1]
            v = ask("%s:" % label)
            if v.isdigit() and rng[0] <= int(v) <= rng[1]:
                a[k] = int(v)
            else:
                say(tr("ค่าต้องอยู่ระหว่าง %d-%d") % rng, "red")
                pause()
                continue
        else:
            continue
        cfg.save()


def menu_tools(cfg):
    while True:
        banner(cfg)
        t = Table(title=tr("สำรอง/กู้ค่า + log + แจ้งเตือน"), box=box.ROUNDED, header_style="bold cyan")
        t.add_column("#")
        t.add_column(tr("รายการ"))
        t.add_row("1", tr("สำรองค่าตั้งค่า (config.json -> backup/)"))
        t.add_row("2", tr("กู้ค่าตั้งค่าจากไฟล์สำรอง"))
        t.add_row("3", tr("ดู log ล่าสุด 30 บรรทัด"))
        t.add_row("4", tr("สรุปรีจอย/หลุดต่ออินสแตนซ์ (ย้อนหลัง N ชั่วโมง)"))
        t.add_row("5", tr("ตั้งค่าแจ้งเตือนผิดปกติ (รีจอยถี่ / แรมต่ำ)"))
        console.print(t)
        c = ask(tr("เลือกหมายเลข (0=กลับ)"))
        if c == "0":
            return
        if c == "1":
            try:
                say(tr("สำรองแล้ว: ") + export_config(cfg), "green")
                say(tr("ไฟล์นี้มี URL Webhook อยู่ ห้ามแชร์ให้ใคร (คุกกี้ไม่ได้อยู่ในไฟล์นี้)"), "yellow")
            except OSError as e:
                say(tr("สำรองไม่ได้: %s") % scrub(e)[:80], "red")
            pause()
        elif c == "2":
            names = backup_files()
            if not names:
                say(tr("ยังไม่มีไฟล์สำรองใน ") + BACKUP_DIR, "yellow")
            else:
                for i, n in enumerate(names[:15], 1):
                    say("  %d) %s" % (i, n))
                v = ask(tr("เลือกไฟล์ (0=ยกเลิก):"))
                if v.isdigit() and 1 <= int(v) <= min(15, len(names)):
                    if confirm(tr("กู้ค่าจาก %s (ค่าปัจจุบันจะถูกสำรองไว้ก่อน)?") % names[int(v) - 1]):
                        try:
                            import_config(cfg, names[int(v) - 1])
                            say(tr("กู้ค่าแล้ว"), "green")
                        except (OSError, ValueError) as e:
                            say(tr("กู้ไม่ได้: %s") % scrub(e)[:80], "red")
            pause()
        elif c == "3":
            show_log_tail()
            pause()
        elif c == "4":
            v = ask(tr("ย้อนหลังกี่ชั่วโมง (1-168, Enter=24):"), "24")
            hrs = int(v) if v.isdigit() and 1 <= int(v) <= 168 else 24
            data = log_summary(hrs)
            if not data:
                say(tr("ไม่พบเหตุการณ์หลุด/รีจอยใน %d ชั่วโมงที่ผ่านมา") % hrs, "yellow")
            else:
                tb = Table(title=tr("สรุปย้อนหลัง %d ชั่วโมง") % hrs, box=box.ROUNDED, header_style="bold cyan")
                for col in (tr("อินสแตนซ์"), tr("หลุด/ค้าง"), tr("รีจอยแล้ว"), tr("สาเหตุบ่อยสุด")):
                    tb.add_column(col)
                for label, d in sorted(data.items(), key=lambda kv: -kv[1]["crash"]):
                    top = max(d["reasons"].items(), key=lambda kv: kv[1])[0] if d["reasons"] else "-"
                    tb.add_row(T(label), str(d["crash"]), str(d["rejoin"]), T(top))
                console.print(tb)
            pause()
        elif c == "5":
            menu_alert(cfg)


MENU = [("1", tr("เริ่มรีจอยอัตโนมัติ")), ("2", tr("แสดงอินสแตนซ์ / สถานะ")),
        ("3", tr("ตั้งค่า (ลิงก์เกม, ช่วงเวลาตรวจสอบ, หน่วงการเปิด)")), ("4", tr("สแกนแพ็กเกจ")),
        ("5", tr("คุกกี้ (ล็อกอิน / ดึงจากแอป Roblox)")), ("6", tr("ตั้งค่าอัตโนมัติ")), ("7", "Discord Webhook"), ("8", tr("บล็อก / เลิกบล็อก / เพิ่มเพื่อน (ด้วยคุกกี้)")),
        ("9", tr("เปิด / ปิด Roblox ทั้งหมด (ตั้งหน่วงเวลาได้)")), ("10", tr("แสดงรายการอินสแตนซ์")), ("11", tr("อัปเดตเครื่องมือ (เช็คเวอร์ชันก่อน)")),
        ("12", tr("เทสเครื่อง Cloud Phone (CPU / RAM / GPU)")), ("13", tr("สำรอง/กู้ค่า + ดู log + แจ้งเตือนผิดปกติ")), ("0", tr("ออก"))]


def ensure_java():
    """เช็ก Java ถ้ายังไม่มีและมี pkg (Termux) จะติดตั้ง openjdk-17 ให้ ไม่สำเร็จก็ข้าม ไม่ให้โปรแกรมล้ม"""
    if shutil.which("java"):
        return True
    if not shutil.which("pkg"):
        say(tr("ไม่พบ Java และไม่มีคำสั่ง pkg (ไม่ใช่ Termux?) ข้ามการติดตั้ง"), "yellow")
        return False
    say(tr("ไม่พบ Java กำลังติดตั้ง openjdk-17 (ครั้งเดียว อาจใช้เวลาสักครู่)..."), "yellow")
    try:
        r = subprocess.run(["pkg", "install", "-y", "openjdk-17"], timeout=900)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning(tr("ติดตั้ง Java ไม่สำเร็จ: %s"), e)
        say(tr("ติดตั้ง Java ไม่สำเร็จ ลองเอง: pkg install openjdk-17"), "red")
        return False
    ok = r.returncode == 0 and bool(shutil.which("java"))
    log.info(tr("ติดตั้ง Java: %s"), tr("สำเร็จ") if ok else tr("ล้มเหลว rc=%s") % r.returncode)
    say(tr("ติดตั้ง Java สำเร็จ") if ok else tr("ติดตั้ง Java ไม่สำเร็จ ลองเอง: pkg install openjdk-17"), "green" if ok else "red")
    return ok


def main():
    detect_root()
    cfg = Config()
    STREAMER["on"] = bool(cfg.d["ui"]["streamer_mode"])
    setup_logging()
    ensure_java()
    hook = Hook(cfg)
    start_update_watch(cfg, hook)
    apply_perf(cfg)
    log.info(tr("เริ่มโปรแกรม โหมด=%s โฟลเดอร์=%s"), "root" if ENV.root else "non-root", BASE)
    if not STORAGE_OK:
        say(tr("เขียน /sdcard/Download ไม่ได้ (ยังไม่อนุญาตที่เก็บข้อมูล?) ใช้โฟลเดอร์สำรอง: ") + BASE, "yellow")
    say(tr("กำลังสแกนแพ็กเกจ..."), "dim")
    refresh_packages(cfg)
    if cfg.d["autoexec"]["auto_create"]:
        report_autoexec(cfg, install_autoexec(cfg))
    if cfg.d["auto"]["start_on_launch"] and parse_link(cfg.d["game_link"]) and cfg.d["detected"]:
        say(tr("เริ่มอัตโนมัติใน 5 วินาที (Ctrl+C เพื่อยกเลิก)"), "yellow")
        try:
            time.sleep(5)
            menu_start(cfg, hook)
        except KeyboardInterrupt:
            pass
    while True:
        banner(cfg)
        for k, label in MENU:
            console.print(Text.assemble(("  [%s] " % k, "bold cyan"), (label, "bold" if k != "0" else "red")))
        console.print()
        c = ask(tr("เลือกเมนู:"))
        if c == "1":
            menu_start(cfg, hook)
        elif c == "2":
            menu_status(cfg)
        elif c == "3":
            menu_settings(cfg)
        elif c == "4":
            menu_scan(cfg)
        elif c == "5":
            menu_cookie_hub(cfg)
        elif c == "6":
            menu_auto(cfg)
        elif c == "7":
            menu_webhook(cfg, hook)
        elif c == "8":
            menu_social(cfg)
        elif c == "9":
            menu_open_all(cfg)
        elif c == "10":
            menu_list(cfg)
        elif c == "11":
            menu_update()
        elif c == "12":
            menu_bench(cfg)
        elif c == "13":
            menu_tools(cfg)
        elif c == "0":
            say(tr("ลาก่อน!"), "cyan")
            return


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].lower() in ("update", "--update", "-u"):
        sys.exit(0 if run_update() != "error" else 1)
    if len(sys.argv) > 1 and sys.argv[1].lower() in ("rollback", "--rollback"):
        sys.exit(0 if run_rollback() == "ok" else 1)
    try:
        main()
    except KeyboardInterrupt:
        say(tr("\nออกจากโปรแกรม"), "cyan")
