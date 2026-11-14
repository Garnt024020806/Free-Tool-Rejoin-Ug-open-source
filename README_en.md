# GARNT TOOL REJOIN — English Guide

[ภาษาไทย](README.md) | **English**

An automatic Roblox rejoin tool for UG Phone + Termux (use it only with your own accounts and devices).

> Note: this guide is translated, but the program itself still shows its menus and messages in Thai. Menu numbers in this guide match the numbers in the program.

**Download Material:** https://gofile.io/d/1y4Uo29l

## Quick Install (read just this part)

Follow the steps in order. Copy each command, paste it into Termux, then press Enter.

**1. Install Termux** from F-Droid or GitHub (do NOT use the Play Store version — it is outdated and will not work).

**2. Choose the "all mirrors" option**
```
termux-change-repo
```
Move with the arrow keys, press Space to select **Mirror group**, then select **Rotate between several mirrors**, then press Enter (OK).

**3. Install everything with one command**
```
curl -Ls https://raw.githubusercontent.com/Garnt024020806/TOOL/main/setup.sh | bash
```
- A storage-permission popup will appear → tap **Allow** right away.
- Wait until it finishes; this can take several minutes (if you allowed it late, run the same command again).

**4. Run the program**

Rooted device (e.g. UG Phone):
```
su -c "export PATH=$PATH:/data/data/com.termux/files/usr/bin && export TERM=xterm-256color && cd /storage/emulated/0/Download && python ./rejoin.py"
```
Non-rooted device:
```
cd ~ && python rejoin.py
```

**5. Start using it:** menu **3** set the game link → menu **4** scan packages → menu **1** start rejoining.

If something goes wrong, see the troubleshooting section at the end, or read the detailed steps in section 1 below.

---

## Related Files

| File | Purpose |
|---|---|
| `rejoin.py` | Main program (single file) |
| `setup.sh` | One-command installer |
| `requirements.txt` | Python libraries (`rich`, `requests`) |
| `/sdcard/Download/RejoinTool/config.json` | Settings (contains no cookies) |
| `/sdcard/Download/RejoinTool/cookie.txt` | Cookies, 1 line per account (optional) |
| `/sdcard/Download/RejoinTool/logs/` | Daily logs, kept for 7 days |
| `/sdcard/Download/RejoinTool/heartbeat/` | `.lua` scripts the menu generates per instance (single example: `heartbeat.lua`) |
| `/sdcard/Download/RejoinTool/screenshots/` | Screenshots, latest 20 kept (root only) |

---

## 1. Installation (detailed, in order)

### Step 1 — Choose a Termux mirror (all mirrors)
```
termux-change-repo
```
Move with the arrow keys, press Space to select **"Mirror group"** → select **"Rotate between several mirrors"** (uses all mirrors in rotation) → OK.
If you skip this step and `pkg update` is slow or gives a 404 error, come back and redo it (if it still fails, try "Single mirror" and pick a nearby one).

### Step 2 — One-command install
```
curl -Ls https://raw.githubusercontent.com/Garnt024020806/TOOL/main/setup.sh | bash
```
The script does, in order: `termux-setup-storage` → `pkg update/upgrade` → `python`, `python-pip`, `tsu`, `libexpat`, `openssl`, `openjdk-17` → `pip install -r requirements.txt` → downloads `rejoin.py` to `/sdcard/Download/rejoin.py` (plus a copy at `~/rejoin.py`).

- The storage-permission dialog appears as soon as it starts → tap **Allow** quickly (before the script reaches the file-download line). If you were too slow, run the install command again.
- If `pkg upgrade` asks anything, the script answers yes for you; just wait for it to finish.

### Step 3 — Android / Termux permissions and how to check them

| Permission | How to set it | How to check it |
|---|---|---|
| Storage | Settings > Apps > Termux > Permissions > Files and media / Storage = Allow (or run `termux-setup-storage`) | `touch /sdcard/Download/t && rm /sdcard/Download/t` must show no error |
| Disable battery saving | Settings > Apps > Termux > Battery = **Unrestricted / Not optimized** (do the same for every cloned Roblox) | Settings > Battery > Optimization: Termux/Roblox must not be in the restricted list |
| Root permission | Enable root on the UG Phone, run `su` the first time and tap **Grant** for Termux in the root manager (Magisk/KernelSU) | `su -c id` must print `uid=0(root)` |
| Display over other apps (non-root mode) | Settings > Apps > Termux > Display over other apps = Allow | Without it, Termux cannot launch the game in non-root mode |
| Keep Termux in recents | Pull down the Termux notification and tap "Acquire wakelock", or let the program do it (Auto settings > Wake-lock) | The Termux notification shows "wake lock held" |

### Step 4 — Run the program (root mode)
```
su -c "export PATH=$PATH:/data/data/com.termux/files/usr/bin && export TERM=xterm-256color && cd /storage/emulated/0/Download && python ./rejoin.py"
```
Non-root mode (or running from `~`):
```
cd ~ && python rejoin.py
```
The program detects the mode itself with `su -c id` — you do not need to say which mode you use.

---

## 2. How to use

1. Menu **3**: set the game link (Place ID, `roblox.com/games/...`, private-server link, share link, or `roblox://...` are supported) and adjust the check interval / launch delay.
2. Menu **4**: scan `com.roblox.*` packages and choose which to run (`1,3` or `all`).
3. (Optional) Menu **5** cookies: option 1 puts cookies from `cookie.txt` into the app (login) / option 2 **pulls cookies from the Roblox app** (root) into `cookie.txt` so they can be used for status checks through the API (cookies are validated against the API before saving; a line shown as `-` means that instance has no cookie; you can make it pull automatically before start in menu 6).
4. (Optional) Menu **8** block / unblock / add friend using accounts from the cookies (you can pick accounts, and there is an option to add friends among your own accounts that sends the request and accepts it for you). Requests are spaced 1.5 seconds apart to avoid rate limits. Roblox may sometimes ask for a captcha, which the tool cannot solve.
   - Block/unblock uses the newer endpoint `apis.roblox.com/user-blocking-api/v1/users/<id>/block-user` (`unblock-user`), because the old URL `accountsettings.roblox.com/.../block` returns HTTP 404 (if the new one returns 404, the old one is tried).
   - Roblox limits blocking to about 3 per minute per cookie. On HTTP 429, wait 1 minute and try again. On HTTP 403, the result table shows what Roblox replied (cookie removed) and whether the CSRF token was already re-sent, to help find the cause.
   - Option **5** in menu 8 = open all Roblox instances one by one (opens the game from the configured link; if no link is set it opens only the app), 10 seconds between instances, adjustable with `open_all_delay` in `config.json`. Press Ctrl+C to stop midway.
   - Block/unblock showing `HTTP 403 Request Context BrowserTrackerID is missing or invalid` means Roblox needs the `RBXEventTrackerV2` cookie (the `browserid=` value). The tool opens `www.roblox.com/home` to get this cookie from the server; if that fails it creates one itself (not secret, never saved to a file).
   - If block/unblock returns HTTP 400, the tool reads the account's block list (`get-blocked-users`) and reports the real state, e.g. "already blocked" (counted as success) or "not blocked" when unblocking.
   - Option **6** in menu 8 = block all accounts from each other: every account with a usable cookie in `cookie.txt` blocks every other one, both directions (7 accounts = 42 blocks). No target selection; already-blocked pairs are skipped (read from each account's block list); two cookies of the same account count once. Because of the roughly 3-per-minute limit, the tool waits at least `block_gap_sec` (default 20) seconds between two blocks from the same account; 7 accounts take about 2–3 minutes. On HTTP 429 it waits 60 seconds and retries up to 2 times. Press Ctrl+C to stop and see a summary of what was done.
   - Option **7** = unblock all among your own accounts (the pair of option 6): each account unblocks the other accounts in the cookies, only pairs that are really blocking each other according to each account's block list. Anyone else the account has blocked is not touched. Uses the same pacing and 429 handling as option 6 (`block_gap_sec`). If no pair is blocked, it says so and finishes without sending anything.
5. Menu **6** adjusts automation; menu **7** sets the Discord webhook.
6. Menu **1** starts rejoining — press `Ctrl+C` to stop and return to the menu.

### Core behavior
- Instances are launched one at a time, 15 seconds apart (both at start and for mid-run rejoins).
- Checks every 30 seconds — after a drop it waits 10s → 20s → 40s … up to 300s before rejoining, and resets after 5 minutes of continuous stability.
- After launching the app there is a 90-second join grace period (adjustable in menu 6) during which an instance is not counted as dropped.

### Root vs non-root

| Capability | Root | Non-root |
|---|---|---|
| Check the app process | ✅ | ⚠️ not detectable on most devices |
| Check the app is on screen | ✅ (off by default) | ❌ |
| Force-stop before relaunch | ✅ | ❌ |
| Screenshot / attach to webhook | ✅ | ❌ |
| Put cookies into the app | ✅ | ❌ (cookie validity can still be checked) |
| Check "in game" via Roblox API | ✅ if you have a cookie | ✅ if you have a cookie |

Non-root mode without cookies shows "unknown status" — use "rejoin on a timer (minutes)" in menu 6 instead.

### Auto-create heartbeat.lua in Delta's autoexec folder
The tool creates/updates `heartbeat.lua` in `/storage/emulated/0/Delta/Autoexecute/` for you every time the program opens, every time you start rejoining, and whenever you change the heartbeat port/interval. You do not need to copy the file yourself.
- Other files in the folder (e.g. `BSS.lua`) are not touched, and an existing `heartbeat.lua` that is not the tool's own is skipped, not overwritten.
- The first time the file is created successfully, heartbeat is enabled once (you can turn it off later in menu 6 > Heartbeat > 1 and it will not be turned on again).
- Change the folder, toggle auto-create, or create immediately with menu 6 > Heartbeat options 6, 7, 8 (put `{pkg}` in the path if each clone has its own autoexec folder; it will create one per clone).
- One file works for several clones sharing a folder: in root mode the tool finds which app (UID) a signal came from via `/proc/net/tcp` and matches it to the package itself. If that cannot be read, it matches the single running instance, or matches by account name when a cookie is available.
- Rejoin the game once after the file is created so Delta runs the script when you enter the game.

### Heartbeat (in-game script)
A Lua script running in the game sends a signal to the tool (`127.0.0.1`, on the same device) every 15 seconds, so the tool knows the instance is "really playing" without root or cookies. The script **stops sending** when the game frame freezes for more than 5 seconds or a Disconnected/Error window appears.

**FPS box on the game screen:** the script shows a black box with a white border that you can drag anywhere (position is remembered if the executor supports `writefile`) and sends the FPS value to show in the details column / webhook report. Toggle it in menu 6 > Heartbeat > 9 (a new script loads the next time you enter the game).

How to install:
1. Menu 6 > Heartbeat > press 5 to create one `.lua` file per instance (each file already has `ID` set to that clone's package name).
2. Copy each clone's file into that clone's executor autoexec folder, or run it after entering the game.
3. Menu 6 > Heartbeat > press 1 to enable it, then start rejoining with menu 1.

Behavior: if it is silent for more than 90 seconds (adjustable), or there is no signal after a rejoin beyond the join grace time, the tool rejoins with the normal backoff. If no signal has ever been received from that instance, the tool falls back to the old checking method (no rejoin loop) and shows this in "details".

Notes: you need an executor that can make HTTP calls to `127.0.0.1`, and using an executor may violate Roblox's rules and can get an account banned — the decision and the risk are yours. Test the receiver with `curl http://127.0.0.1:8765/ping` (it must answer `pong` while rejoining is running).

### Live panel + rejoin console (menu 1)
While running, the live panel shows per-instance status (state, uptime, failures/rejoins, next check time, details) plus free RAM and device load. The latest events are below (newest on top), and there is a **console** box where you can type a command and press Enter immediately.

| Command | What it does |
|---|---|
| `rejoin 1` / `rejoin all` | Force a rejoin now (root: force-stop first), skipping backoff and not counted as a failure (short: `r`) |
| `pause 1` / `pause all` | Temporarily stop managing an instance (short: `p`) |
| `resume 1` / `resume all` | Resume managing and rejoin immediately |
| `status` | Summarize all instances |
| `ram` | Clear RAM now (root) |
| `shot` | Take a screenshot, save it, and send a report to the webhook if set (root) |
| `clear` | Force-clear stacked windows (root) |
| `streamer [on|off]` | Turn account-name hiding on/off (shows the first 4 characters, the rest as `#`, only on screen — logs/webhook are unaffected). Also in menu 3 option 4 (default: on) |
| `help` / `quit` | Show commands / stop and return to the menu (or press Ctrl+C) |

The instance number is the number in the `#` column, matching menu 2 and the cookie order. Menu **2** is also a live panel that refreshes automatically (press Enter to go back).

### Console webhook (menu 7)
Sends every console line (events, typed commands, results) to Discord as a code block, batched every 10 seconds (adjustable 3–300). You can set a URL separate from the main webhook; if left empty the main URL is used. Off by default. Settings in `config.json`: `console_webhook.enabled/url/flush_sec`.
Note: a Discord webhook can only send messages out; you cannot command the tool from Discord (that would need a bot). Use the console box in the live panel instead.

### Disconnected / Error Code 277 (dropped but not rejoining)
Roblox draws the Disconnected window inside the game itself, so Android still sees a normally running app — older versions without cookies/heartbeat therefore reported "normal" even though the account was disconnected. This version detects it in 3 ways (they can be combined):
1. **Screenshot check (root, on by default)** — takes a raw screenshot and looks for a dark-gray rectangular panel (#393B3D) with a white button inside. Checked every 30 seconds; it must be seen twice in a row before rejoining (to avoid false detections). Configure in menu 6 > Screen.
2. **Heartbeat** — the in-game script sends the error text (e.g. Error Code 277) the moment the window appears (use the latest `.lua`; regenerate in menu 6 > Heartbeat > 5).
3. **Cookie + Roblox API** — status changes from in-game to offline; works in both root and non-root mode.

Limitation: method 1 only sees the instance that is on screen (the focused window). With several clones, combine it with method 2 or 3. If Roblox's window layout changes so it is no longer detected, try menu 6 > Screen > 8 (test Disconnected detection now, does nothing else) while the window is showing.

### Clear stacked windows (menu 6 > Screen)
Checks every 60 seconds (root only): closes stuck system windows, force-stops apps showing a "has stopped / not responding" window so the system can reopen them via rejoin, and force-stops clones that have stacked tasks (must be seen 2 times in a row, with a 10-minute gap per app). The option "close stray apps drawn over the screen" is off by default (excluding Termux/system/launcher/Magisk). If a normal clone gets force-stopped when it should not, turn off "force-stop apps with stacked windows". Option 9 shows what was detected first, and option 10 clears immediately.

### Clear RAM / reduce CPU use / status reports (menu 6 > Performance, menu 7)
**Clear RAM (root only)** — drops the memory cache (`drop_caches`) every 30 minutes and/or when free RAM falls below 300 MB (at least 1 minute apart), and shows free RAM on the status panel. The `am kill-all` option is off by default because it may kill clones running in the background.

On cloud phones (e.g. UG Phone) `drop_caches` is locked read-only. The tool tries once, then skips it for the rest of the run and uses **soft background-app killing** instead (`am kill` only for apps you installed that are not on screen, excluding Roblox/Termux/Delta/the do-not-kill list). It is on by default (`ram.soft_kill`, menu 6 > Performance > 10). The free-RAM number shown belongs to the host machine and may not move; look at the "killed N/M" text in each step's result instead.

**Reduce CPU use (on by default)**
- Checks all instances together on one schedule and uses a single shell call per round (process list + screen) instead of one call per instance.
- Lowers the tool's own process priority (`nice 10`) so the game gets CPU first (takes effect when the program starts).
- Caches game-status results from the Roblox API for 60 seconds and refreshes the status panel every 2 seconds (adjustable).
- Turns on "check the app is on screen" only when needed, because `dumpsys` uses the most CPU.

**Status report + screenshot to Discord every 10 minutes** (includes a **CPU** field: current + average since the previous report + core count; if `/proc/stat` cannot be read it is estimated from load average and prefixed with ~) — menu 7 sets the number of minutes (0 = off) and the "status report" switch. A screenshot can be attached only in root mode (non-root sends text only). The latest 20 screenshots are kept in `screenshots/`.

Values in `config.json`: `webhook.periodic_min` (10), `ram.interval_min` (30), `ram.min_free_mb` (300), `ram.kill_cached` (false), `ram.soft_kill` (true), `performance.low_cpu` (true), `performance.ui_refresh_sec` (2).

### Backup/restore + log + abnormal-event alerts (menu 13)
- **1 Back up settings** copies `config.json` into the `backup/` folder (latest 10 kept). **2 Restore** from a backup file (your current settings are backed up before restoring). The backup file contains your webhook URL, so do not share it; cookies are in `cookie.txt` and are not part of the backup.
- **3 View log** shows the latest 30 lines. **4 Per-instance summary** for the last N hours: number of drops/hangs, number of rejoins, and the most common cause.
- **5 Abnormal-event alerts** go to Discord (toggle the "abnormal alerts" event in menu 7): N drops/hangs within the configured window (default 5 in 60 minutes per instance), and free RAM below the threshold. Repeat alerts are sent no more often than every 30 minutes.
- **Roll back a version:** menu 11 option 2, or `python rejoin.py rollback`, swaps back to the `.bak` file kept at update time (it always checks the file can run first, and running it again swaps back).

### Cloud Phone test (menu 12)
Tests the real specs of a cloud phone to see whether you got what you rented — stop rejoining / close the game first so results are not skewed. Press `Ctrl+C` to cancel midway.
- **Option 1 CPU** — shows model/architecture/core count/speed, then measures a single-core score and an all-core score (hashes per second) with the ratio to single-core, plus CPU % during the test. If it reaches less than 60% of the core count, it warns that the CPU may be throttled/shared.
- **Option 2 RAM** — shows capacity/free/swap, then measures allocation, write and copy speed (MB/s) and verifies the written data. Uses blocks of at most 64 MB and at most 15% of free RAM so the system does not kill the app.
- **Option 3 GPU** — reads the GPU model / OpenGL ES / Vulkan from `dumpsys SurfaceFlinger` and `getprop` (some devices need root mode), and measures GPU load for 5 seconds if the device lets it read the sysfs file. If it is a software renderer (SwiftShader/llvmpipe) it warns that there is no real GPU. Limitation: Termux cannot create real graphics work, so there is no GPU score — use GFXBench / 3DMark for a real one.
- **Option 4** runs everything in sequence (about 30 seconds). Every result is saved to `logs/rejoin.log`.

### About cookies (cookie.txt)
- 1 line per account, matched with detected instances in order (first line = first package). Blank lines and lines starting with `#` are skipped.
- The program always shows cookies hidden, never writes them to logs, never sends them to the webhook, and never stores them in `config.json`.
- **Never share this file or pass it on** — anyone with the cookie can access your account immediately.
- Putting a cookie in writes to the app's cookie database (`app_webview/.../Cookies`); this depends on the Roblox/clone version. If it fails, log in inside the app by hand once.

---

## 3. Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: rich` / `requests` | `pip install -r requirements.txt` (if it still fails when run through `su`, install from normal Termux, not inside su) |
| `CANNOT LINK EXECUTABLE` or `libpython... not found` when running through `su` | Add `export LD_LIBRARY_PATH=/data/data/com.termux/files/usr/lib &&` before `cd ...` in the command |
| `python: not found` inside `su` | Make sure the command contains `export PATH=$PATH:/data/data/com.termux/files/usr/bin` |
| Program says "non-root" although the device is rooted | Run `su -c id` yourself in Termux and tap Grant in the root app; if it does not print `uid=0`, permission was not granted |
| "Cannot write /sdcard/Download" | Allow the storage permission for Termux and run `termux-setup-storage` again |
| Packages not found | In menu 4 type the package names yourself (comma-separated), or edit `package_prefix` in `config.json` |
| App opens but does not enter the game (shows "using launcher instead") | The link is not passed into the cloned app. Try a plain Place ID, or a `roblox://placeID=...` link |
| Rejoin loops even though you are in the game | Turn off "check the app is on screen" (menu 6) when running several instances at once — only one app can be on screen |
| Cookie "expired/invalid" | Log in on the web again and copy a new `.ROBLOSECURITY` (logging out invalidates the old cookie) |
| heartbeat.lua is not in the Delta folder | Menu 6 > Heartbeat > 8 shows the result (created/skipped/error); also check Termux has storage permission |
| Heartbeat does not arrive (details show "no heartbeat yet") | Check the script's `ID` matches the package name, the port matches menu 6 > Heartbeat, the executor allows HTTP to localhost, and menu 1 is running (the receiver is only on while rejoining runs) |
| Status report does not arrive every 10 minutes | Menu 7: enable the webhook, enable "status report", set minutes > 0, and menu 1 must be running (the first report comes after the set number of minutes) |
| Cannot clear RAM | Requires root; try menu 6 > Performance > 9 for an immediate result (a locked drop_caches on cloud phones is normal, see "killed N/M") |
| Cannot arrange windows | Requires root and freeform windows; the "failed ... reason: ..." message in events/Discord gives the real cause (e.g. 40-second timeout or an `am task resize` error). Send that message so the command can be adjusted; full details are in logs/ |
| Disconnected (Error 277) but no rejoin | Needs root mode + menu 6 > Screen option 1 on. Use option 8 to test while the window is showing. If it says "cannot detect", the raw screenshot cannot be read — use heartbeat or cookies instead; with several clones you must also use heartbeat/cookies |
| Menu screen appears twice / garbled display | v1.4 changed how the screen is cleared. If it still happens, close and reopen Termux and use the run command with `export TERM=xterm-256color` |
| Typing a console command does nothing | Run from a real terminal in Termux (not a pipe). Type `help`, press Enter, and look at the events box |
| Webhook does not send | Menu 7 > 8 sends a test; the URL must start with `https://discord.com/api/webhooks/` |
| Wrong characters on screen | Set `export TERM=xterm-256color` and use a font in Termux that supports Thai |
| Want to see what happened earlier | Open `/sdcard/Download/RejoinTool/logs/rejoin.log` |
