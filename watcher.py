#!/usr/bin/env python3
"""Ticket-booking watcher for BookMyShow / District. Alerts via Telegram.

Env: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
Usage:
  python watcher.py --test-telegram   # send a test message
  python watcher.py --debug           # check once, print page text + save screenshots to ./debug
  python watcher.py                   # normal run (used by cron / GitHub Actions)
Exit code 10 = bookings opened (workflow uses this to stop itself).
"""
import json, os, re, sys, datetime, urllib.request, urllib.parse, pathlib

HERE = pathlib.Path(__file__).parent
STATE = HERE / ".state.json"
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
BLOCK_HINTS = ["access denied", "captcha", "verify you are human", "unusual traffic", "forbidden"]


def send(msg):
    tok, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        print("[no telegram creds] " + msg); return
    data = urllib.parse.urlencode({"chat_id": chat, "text": msg, "disable_web_page_preview": "true"}).encode()
    for attempt in range(3):   # retry network blips so an alert is never lost
        try:
            urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data, timeout=20).read(); return
        except Exception as e:
            if attempt == 2: print("send failed:", e)
            else: import time; time.sleep(2)


def load_state():
    try: return json.loads(STATE.read_text())
    except Exception: return {}


def save_state(s): STATE.write_text(json.dumps(s))


DYN = HERE / "dynamic.json"
HELP = ("Commands:\n/list\n/addmovie <district movie link>\n/removemovie <name>\n"
        "/addtheatre <name> [imax] [for <movie words>]\n/removetheatre <name>\n/city <name>\n"
        "Example: /addtheatre palazzo imax for dune doomsday")


def load_cfg():
    cfg = json.loads((HERE / "config.json").read_text())
    dyn = {}
    try: dyn = json.loads(DYN.read_text())
    except Exception: pass
    for k in ("city", "targets", "theatres"):
        if k in dyn: cfg[k] = dyn[k]
    return cfg, dyn


def save_dyn(cfg, dyn):
    dyn.update({k: cfg[k] for k in ("city", "targets", "theatres")})
    DYN.write_text(json.dumps(dyn, indent=1))


def fmt_rule(r):
    mv = "all movies" if r.get("movies", "*") == "*" else ", ".join(r["movies"])
    return f"• {r['match']}{' (IMAX only)' if r.get('imax') else ''} -> {mv}"


def handle(cmd, cfg):
    """Returns (reply, changed)."""
    parts = cmd.strip().split()
    c = parts[0].lower().split("@")[0]; rest = parts[1:]
    if c in ("/start", "/help", "/commands"): return HELP, False
    if c == "/list":
        mv = "\n".join("• " + t["name"] for t in cfg["targets"]) or "(none)"
        th = "\n".join(fmt_rule(r) for r in cfg["theatres"]) or "(any theatre)"
        return f"City: {cfg['city']}\nMovies:\n{mv}\nTheatres:\n{th}", False
    if c == "/city" and rest:
        cfg["city"] = " ".join(rest).lower(); return f"City set to {cfg['city']}", True
    if c == "/addmovie" and rest:
        m = re.search(r"/movies/(.+?)-movie-tickets(?:-in-[a-z0-9-]+?)?-(MV\d+)", rest[0])
        if not m: return "Send the District movie page link, e.g. https://www.district.in/movies/...-MV123", False
        slug, mv = m.groups()
        name = slug.replace("-", " ").title() + " - District"
        if any(mv in t["url"] for t in cfg["targets"]): return "Already watching that movie.", False
        cfg["targets"].append({"name": name, "url": f"https://www.district.in/movies/{slug}-movie-tickets-in-{{city}}-{mv}",
                               "open_keywords": [], "require_text": []})
        return f"Added: {name}\nTip: theatre rules limited to specific movies won't cover it; use /addtheatre <name> for <movie words>.", True
    if c == "/removemovie" and rest:
        q = " ".join(rest).lower(); n = len(cfg["targets"])
        cfg["targets"] = [t for t in cfg["targets"] if q not in t["name"].lower()]
        return (f"Removed {n - len(cfg['targets'])} movie(s).", True) if len(cfg["targets"]) < n else ("No movie matched.", False)
    if c == "/addtheatre" and rest:
        low = [w.lower() for w in rest]
        movies = "*"
        if "for" in low:
            i = low.index("for"); movies = rest[i+1:]; rest = rest[:i]; low = low[:i]
            if not movies: return "Add movie words after 'for'.", False
        imax = "imax" in low
        name = " ".join(w for w in rest if w.lower() != "imax").strip()
        if not name: return "Give a theatre name, e.g. /addtheatre sathyam", False
        r = {"match": name, "movies": movies}
        if imax: r["imax"] = True
        cfg["theatres"].append(r)
        return "Added:\n" + fmt_rule(r), True
    if c == "/removetheatre" and rest:
        q = " ".join(rest).lower(); n = len(cfg["theatres"])
        cfg["theatres"] = [r for r in cfg["theatres"] if q not in r["match"].lower()]
        return (f"Removed {n - len(cfg['theatres'])} rule(s).", True) if len(cfg["theatres"]) < n else ("No theatre rule matched.", False)
    return HELP, False


def process_commands(cfg, dyn, timeout=0):
    import bot
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if not os.getenv("TELEGRAM_TOKEN") or not chat: return
    changed, n = bot.process_updates(cfg, dyn, chat, handle, timeout)
    if changed or n: save_dyn(cfg, dyn)


def check(page, t, debug):
    page.goto(t["url"], wait_until="domcontentloaded", timeout=45000)
    try: page.wait_for_function("document.body.innerText.includes('Frequently Asked')", timeout=15000)
    except Exception: page.wait_for_timeout(3000)
    text = page.inner_text("body")
    if debug:
        d = HERE / "debug"; d.mkdir(exist_ok=True)
        slug = "".join(c if c.isalnum() else "_" for c in t["name"])
        page.screenshot(path=str(d / f"{slug}.png"), full_page=True)
        (d / f"{slug}.txt").write_text(text)
        print(f"--- {t['name']} ---\n{text[:1500]}\n")
    low = text.lower()
    if len(text) < 200 or any(h in low for h in BLOCK_HINTS):
        return "blocked"
    if any(k.lower() in low for k in t.get("require_text", []) if k) is False and t.get("require_text"):
        return "closed"
    # Real signal: actual showtimes (e.g. "09:30 AM") or the "Advance bookings open" banner.
    # (Do NOT match "Book tickets": it is in the footer of every page.)
    has_show = re.search(r"\b\d{1,2}:\d{2}\s?[AP]M\b", text) or "advance bookings open" in low
    if has_show:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        idx = [i-1 for i, l in enumerate(lines) if "km away" in l and i > 0]
        faq = next((i for i, l in enumerate(lines) if l.startswith("Frequently Asked")), len(lines))
        blocks = []
        for k, i in enumerate(idx):
            end = min(idx[k+1] if k+1 < len(idx) else faq, faq)
            blocks.append((lines[i], " ".join(lines[i:end]).lower()))
        rules = t.get("_filter", [])
        nm = t["name"].lower()
        rules = [r for r in rules if r.get("movies", "*") == "*" or any(m.lower() in nm for m in r["movies"])]
        if t.get("_filter"):
            t["_theatres"] = [name for name, blk in blocks
                if any(r["match"].lower() in name.lower() and (not r.get("imax") or "imax" in blk) for r in rules)]
            if not t["_theatres"]:
                return "closed"   # open somewhere, but not at your theatres/formats
        else:
            t["_theatres"] = [name for name, _ in blocks]
        return "open"
    return "closed"


UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def fetch(t, debug):
    """One browser per thread (Playwright's sync API is not thread-safe)."""
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            ctx = b.new_context(locale="en-IN", timezone_id="Asia/Kolkata", user_agent=UA)
            ctx.route("**/*", lambda r: r.abort() if r.request.resource_type in ("image", "media", "font") else r.continue_())
            page = ctx.new_page()
            res = check(page, t, debug)
            b.close()
            return t, res
    except Exception as e:
        print("error", t["name"], e); return t, "error"


def run_checks(cfg, state, debug):
    from concurrent.futures import ThreadPoolExecutor
    now = datetime.datetime.now(IST)
    opened, last = [], {}
    city = cfg.get("city", "").strip().lower().replace(" ", "-")
    if not city: print("Set a city first (Telegram: /menu -> City)"); return None
    targets = []
    for x in cfg["targets"]:
        t = dict(x); t["url"] = t["url"].replace("{city}", city); t["_filter"] = cfg.get("theatres", [])
        if "PASTE_" not in t["url"]: targets.append(t)
    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(lambda t: fetch(t, debug), targets))
    now = datetime.datetime.now(IST)
    for t, res in results:
        print(now.isoformat(timespec="seconds"), t["name"], res)
        last[t["name"]] = {"res": res, "th": t.get("_theatres", [])}
        if res == "open":
            ths = t.get("_theatres", [])
            if t["_filter"]:   # alert for each NEW matching theatre
                seen = state.get(t["name"] + "|seen", [])
                new = [x for x in ths if x not in seen]
                if new:
                    send(f"🎟️ SHOWS AT YOUR THEATRE: {t['name']}\n" + "\n".join("• " + x for x in new) + f"\n{t['url']}")
                    state[t["name"] + "|seen"] = seen + new; opened.append(t["name"])
            elif not state.get(t["name"] + "|open"):
                send(f"🎟️ BOOKINGS OPEN: {t['name']}\n" + "\n".join("• " + x for x in ths) + f"\n{t['url']}")
                state[t["name"] + "|open"] = True; opened.append(t["name"])
        if res in ("blocked", "error"):
            n = state.get(t["name"] + "|fails", 0) + 1
            state[t["name"] + "|fails"] = n
            if n == 6:  # warn once after ~6 consecutive failures
                send(f"⚠️ Watcher can't read {t['name']} ({res}). Possibly blocked — check the setup.")
        else:
            state[t["name"] + "|fails"] = 0
    return opened, last, now


def finish_recheck(cfg, dyn):
    """Update the 'Checking now...' message with fresh status."""
    msg = dyn.pop("_recheck", None)
    if not msg or not os.getenv("TELEGRAM_TOKEN"): return
    import bot
    txt, mk = bot.s_status(cfg, dyn)
    try: bot.tg("editMessageText", chat_id=os.getenv("TELEGRAM_CHAT_ID"), message_id=msg, text=txt, reply_markup=mk)
    except Exception as e: print("finish_recheck:", e)


def main():
    args = sys.argv[1:]
    if "--set-menu" in args:
        menu = [("menu","Open the control panel"),("list","Status of watched movies"),("help","Help")]
        data = json.dumps({"commands": [{"command": a, "description": b} for a, b in menu]}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN']}/setMyCommands", data, {"Content-Type": "application/json"})
        print(urllib.request.urlopen(req, timeout=20).read().decode()); return 0
    if False:
        menu = [("list","Show watched movies and theatres"),("addmovie","Add movie: /addmovie <District link>"),
                ("removemovie","Remove a movie"),("addtheatre","Add theatre: /addtheatre <name> [imax] [for <movie>]"),
                ("removetheatre","Remove a theatre"),("city","Change city"),("help","Show all commands")]
        data = urllib.parse.urlencode({"commands": json.dumps([{"command": a, "description": b} for a, b in menu])}).encode()
        print(urllib.request.urlopen(f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN']}/setMyCommands", data, timeout=20).read().decode())
        return 0
    if "--test-telegram" in args:
        send("✅ Ticket watcher: Telegram is working."); return 0
    debug = "--debug" in args
    cfg, dyn = load_cfg()
    process_commands(cfg, dyn)
    state = load_state()
    now = datetime.datetime.now(IST)
    opened = []
    last = {}

    r = run_checks(cfg, state, debug)
    if r is None: return 2
    opened, last, now = r

    # daily heartbeat (first run in the configured IST hour)
    hb = cfg.get("heartbeat_hour_ist")
    today = now.strftime("%Y-%m-%d")
    if hb is not None and now.hour == hb and state.get("hb") != today:
        send("💓 Ticket watcher still running. Not open yet." if not opened else "💓 Watcher running.")
        state["hb"] = today
    save_state(state)
    dyn["_last"] = {"at": now.strftime("%d %b %H:%M"), "items": last}
    finish_recheck(cfg, dyn); save_dyn(cfg, dyn)
    if "--listen" in args:   # stay alive to answer button taps quickly
        import time
        secs = int(args[args.index("--listen") + 1]); end = time.time() + secs
        while time.time() < end:
            process_commands(cfg, dyn, timeout=min(25, max(1, int(end - time.time()))))
            if dyn.get("_recheck"):   # user tapped "Check now"
                r = run_checks(cfg, state, False)
                if r:
                    opened2, last, now = r; opened += opened2
                    dyn["_last"] = {"at": now.strftime("%d %b %H:%M"), "items": last}
                    save_state(state)
                finish_recheck(cfg, dyn); save_dyn(cfg, dyn)
    if cfg.get("theatres"): return 0   # theatre mode keeps watching for new theatres
    return 10 if opened and all(state.get(t["name"] + "|open") for t in cfg["targets"]) else 0


if __name__ == "__main__":
    sys.exit(main())
