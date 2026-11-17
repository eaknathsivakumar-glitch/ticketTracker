#!/usr/bin/env python3
"""Ticket-booking watcher for District (Chennai & other cities) -> Telegram, multi-user.

Env: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID (the owner)
  python watcher.py                 one check + command processing
  python watcher.py --listen 150    ...then keep answering Telegram taps for 150 s
  python watcher.py --debug         check once, print what was parsed, save screenshots
  python watcher.py --search NAME   find a movie on District by name
  python watcher.py --test-telegram / --set-menu
"""
import datetime, json, os, re, sys, time, urllib.parse, urllib.request, pathlib
from concurrent.futures import ThreadPoolExecutor

import bot

HERE = pathlib.Path(__file__).parent
STATE, DYN = HERE / ".state.json", HERE / "dynamic.json"
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
LANGS = ["tamil", "english", "hindi", "telugu", "malayalam", "kannada"]
BLOCK_HINTS = ["access denied", "captcha", "verify you are human", "unusual traffic", "forbidden"]
STOP = {"morning", "afternoon", "evening", "night", "after 5 pm", "couple seats", "early morning show", "available", "filling fast", "almost full"}
TIME_RE = re.compile(r"^\d{1,2}:\d{2}\s?[AP]M$", re.I)
MOVIE_RE = re.compile(r"/movies/([a-z0-9-]+?)-movie-tickets(?:-in-[a-z0-9-]+?)?-(MV\d+)", re.I)


# ---------- storage ----------
def load_state():
    try: return json.loads(STATE.read_text())
    except Exception: return {}


def save_state(s): STATE.write_text(json.dumps(s))


def load_all():
    base = json.loads((HERE / "config.json").read_text())
    try: dyn = json.loads(DYN.read_text())
    except Exception: dyn = {}
    owner = os.getenv("TELEGRAM_CHAT_ID") or "0"
    dyn["_owner"] = owner
    users = dyn.setdefault("users", {})
    dyn.setdefault("pending_users", {})
    if owner not in users:   # first run (or upgrade from the single-user version)
        legacy = dyn if "targets" in dyn else base
        city = legacy.get("city") or base.get("city", "chennai")
        users[owner] = bot.new_profile("You", [city], legacy.get("targets", []), legacy.get("theatres", []))
    return base, dyn


def save_dyn(dyn):
    DYN.write_text(json.dumps({k: v for k, v in dyn.items() if k != "_owner"}, indent=1))


# ---------- parsing ----------
def parse_page(text, anchors=(), page_url=""):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    out = {"banner": "advance bookings open" in text.lower(), "theatres": [], "langs": []}
    if "Filters" in lines:
        for l in lines[lines.index("Filters") + 1:]:
            if l.lower() in STOP: break
            if l.lower() in LANGS: out["langs"].append(l.lower())
    idx = [i - 1 for i, l in enumerate(lines) if "km away" in l and i > 0]
    faq = next((i for i, l in enumerate(lines) if l.startswith("Frequently Asked")), len(lines))
    for k, i in enumerate(idx):
        end = min(idx[k + 1] if k + 1 < len(idx) else faq, faq)
        blk = lines[i:end]; shows = []
        for l in blk[1:]:
            if "km away" in l or l.lower() in ("non-cancellable", "cancellable"): continue
            if TIME_RE.match(l): shows.append({"time": l.upper().lstrip("0"), "tags": [], "langs": []})
            elif shows:
                shows[-1]["tags"].append(l)
                if l.lower() in LANGS: shows[-1]["langs"].append(l.lower())
        name = blk[0]; link = None
        for txt, href in anchors:
            if txt and txt.strip().lower().startswith(name[:18].lower()): link = href; break
        out["theatres"].append({"name": name, "shows": shows, "text": " ".join(blk).lower(), "link": link or page_url})
    return out


def fetch_page(url, debug=False):
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            ctx = b.new_context(locale="en-IN", timezone_id="Asia/Kolkata", user_agent=UA)
            ctx.route("**/*", lambda r: r.abort() if r.request.resource_type in ("image", "media", "font") else r.continue_())
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            # showtimes load after the rest of the page: wait for them (or for a clear "not open" sign)
            try: page.wait_for_function("(() => { const t = document.body.innerText; return t.includes('km away') || t.includes('Releasing') || t.includes('No shows'); })()", timeout=20000)
            except Exception: page.wait_for_timeout(3000)
            page.wait_for_timeout(1000)
            text = page.inner_text("body")
            try: anchors = page.eval_on_selector_all("a[href]", "els => els.map(e => [e.innerText, e.href])")
            except Exception: anchors = []
            if debug:
                d = HERE / "debug"; d.mkdir(exist_ok=True)
                slug = "".join(c if c.isalnum() else "_" for c in url.split("/")[-1])[:60]
                page.screenshot(path=str(d / f"{slug}.png"), full_page=True); (d / f"{slug}.txt").write_text(text)
            b.close()
        if len(text) < 200 or any(h in text.lower() for h in BLOCK_HINTS): return {"status": "blocked"}
        data = parse_page(text, anchors, url)
        if debug:
            print(f"--- {url}\n  banner={data['banner']} langs={data['langs']}")
            for t in data["theatres"]:
                print("  •", t["name"], "|", ", ".join(s["time"] for s in t["shows"]), "|",
                      sorted({g for s in t["shows"] for g in s["tags"]})[:5], "|", t["link"])
        return {"status": "ok", "data": data}
    except Exception as e:
        print("error", url, e); return {"status": "error"}


# ---------- per-user decision ----------
def lang_ok(prof, th, data):
    want = set(prof.get("languages") or [])
    if not want: return True
    have = {l for s in th["shows"] for l in s["langs"]}
    if have: return bool(have & want)
    if data["langs"]: return bool(set(data["langs"]) & want)
    return True   # page doesn't say: don't hide it


def evaluate(prof, tname, data):
    """-> matching theatres (list; empty = nothing for this user)."""
    ths = data["theatres"]
    if not ths:
        return [{"name": "Shows are open (theatre list not shown)", "shows": [], "text": "", "link": None}] \
            if data["banner"] and not prof["theatres"] else []
    rules = [r for r in prof["theatres"] if r.get("movies", "*") == "*" or any(m.lower() in tname.lower() for m in r["movies"])]
    out = []
    for t in ths:
        ok = any(r["match"].lower() in t["name"].lower() and (not r.get("imax") or "imax" in t["text"]) for r in rules) if prof["theatres"] else True
        if ok and lang_ok(prof, t, data): out.append(t)
    return out


def fmt_alert(tname, city, multi, new, data, url):
    lines = [f"🎟️ {bot.short(tname)}" + (f" ({city.title()})" if multi else "") + " — bookings open!"]
    for t in new:
        lines.append(f"\n📍 {t['name']}")
        if t["shows"]:
            lines.append("   🕐 " + " · ".join(dict.fromkeys(s["time"] for s in t["shows"]))[:180])
            tags = list(dict.fromkeys(g for s in t["shows"] for g in s["tags"] if g.lower() not in LANGS))[:4]
            langs = sorted({l for s in t["shows"] for l in s["langs"]}) or data["langs"]
            if tags: lines.append("   🎞 " + " · ".join(tags))
            if langs: lines.append("   🗣 " + ", ".join(l.title() for l in langs))
        if t.get("link"): lines.append(f"   🔗 {t['link']}")
    return "\n".join(lines)


# ---------- running checks ----------
def run_checks(dyn, state, debug=False):
    now = datetime.datetime.now(IST); owner = dyn["_owner"]
    urls = {}
    for prof in dyn["users"].values():
        for c in prof["cities"]:
            for t in prof["targets"]: urls[t["url"].replace("{city}", c)] = None
    with ThreadPoolExecutor(max_workers=4) as ex:
        res = dict(zip(urls, ex.map(lambda u: fetch_page(u, debug), urls)))
    now = datetime.datetime.now(IST)
    for u, r in res.items():   # one warning (to the owner) if a page keeps failing
        n = state.get(u + "|fails", 0) + 1 if r["status"] != "ok" else 0
        state[u + "|fails"] = n
        if n == 6: bot.send(owner, f"⚠️ Watcher can't read {u} ({r['status']}). Possibly blocked — check the setup.")
    for uid, prof in dyn["users"].items():
        multi = len(prof["cities"]) > 1; last = {}
        for c in prof["cities"]:
            for t in prof["targets"]:
                url = t["url"].replace("{city}", c); r = res[url]; key = f"{t['name']}|{c}"
                if r["status"] != "ok": last[key] = {"res": r["status"], "th": []}; print(now.isoformat(timespec="seconds"), uid, key, r["status"]); continue
                matched = evaluate(prof, t["name"], r["data"])
                last[key] = {"res": "open" if matched else "closed", "th": [m["name"] for m in matched]}
                print(now.isoformat(timespec="seconds"), uid, key, last[key]["res"])
                mv = bot.mvid(t)
                if matched and mv not in prof["muted"] and not bot.is_paused(prof):
                    sk = f"{uid}|{key}|seen"; seen = state.get(sk, [])
                    new = [m for m in matched if m["name"] not in seen]
                    if new:
                        bot.send(uid, fmt_alert(t["name"], c, multi, new, r["data"], url), bot.alert_markup(mv))
                        state[sk] = seen + [m["name"] for m in new]
        prof["_last"] = {"at": now.strftime("%d %b %H:%M"), "items": last}
    return now


def finish_rechecks(dyn):
    for uid, prof in dyn["users"].items():
        msg = prof.pop("_recheck", None)
        if msg and os.getenv("TELEGRAM_TOKEN"):
            txt, mk = bot.s_status(prof)
            try: bot.tg("editMessageText", chat_id=uid, message_id=msg, text=txt, reply_markup=mk)
            except Exception as e: print("finish_recheck:", e)


# ---------- text commands (also reachable by typing) ----------
def find_movie(text):
    m = MOVIE_RE.search(text)
    if m: return m.groups()
    for url in re.findall(r"https?://[^\s<>\"']+", text)[:3]:
        try:
            resp = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=20)
            m = MOVIE_RE.search(resp.geturl())
            if m: return m.groups()
            m = MOVIE_RE.search(resp.read(600000).decode("utf-8", "ignore").replace("\\/", "/"))
            if m: return m.groups()
        except Exception as e: print("link resolve failed:", url, e)
    return None


def rank_movies(found, query):
    words = re.findall(r"[a-z0-9]+", query.lower())
    if not words: return []
    out = [(sum(w in slug for w in words) / len(words), -len(slug), slug, mv) for mv, slug in found.items()]
    return [(sl, mv) for sc, _, sl, mv in sorted((o for o in out if o[0] >= 0.6), reverse=True)][:6]


def search_movies(query, city):
    from playwright.sync_api import sync_playwright
    city = city.strip().lower().replace(" ", "-"); found = {}
    with sync_playwright() as p:
        b = p.chromium.launch(); ctx = b.new_context(locale="en-IN", timezone_id="Asia/Kolkata", user_agent=UA)
        ctx.route("**/*", lambda r: r.abort() if r.request.resource_type in ("image", "media", "font") else r.continue_())
        for u in (f"https://www.district.in/movies/{city}-movie-tickets", "https://www.district.in/movies/upcoming-movies",
                  f"https://www.district.in/movies/english-movies-tickets-in-{city}"):
            page = ctx.new_page()
            try:
                page.goto(u, wait_until="domcontentloaded", timeout=45000); page.wait_for_timeout(4000)
                for _ in range(10): page.mouse.wheel(0, 3000); page.wait_for_timeout(600)
                hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
            except Exception as e: print("search page failed:", u, e); hrefs = []
            page.close()
            for h in hrefs:
                m = MOVIE_RE.search(h)
                if m: found[m.group(2)] = m.group(1)
            print(f"  {u.split('district.in')[1]}: {len(found)} movies so far")
            if rank_movies(found, query): break
        b.close()
    print(f"search '{query}': scanned {len(found)} movies")
    return rank_movies(found, query)


def handle(cmd, p):
    """Typed commands. Returns (reply, changed)."""
    parts = cmd.strip().split(); c = parts[0].lower().split("@")[0]; rest = parts[1:]
    if c == "/help": return bot.HELP, False
    if c == "/city" and rest: p["cities"] = [" ".join(rest).lower().replace(" ", "-")]; return f"Tracking only {p['cities'][0].title()}.", True
    if c == "/pause":
        secs = bot.parse_dur(rest[0]) if rest else None
        p["paused"] = time.time() + secs if secs else bot.FOREVER
        return bot.pause_text(p), True
    if c == "/resume": p["paused"] = 0; return "▶ Alerts are ON", True
    if c == "/addmovie" and rest:
        m = find_movie(" ".join(rest))
        if not m: return "Couldn't find a District movie in that. Paste the district.in/movies/... link, or use ➕ Add movie → Search by name.", False
        slug, mv = m; name = slug.replace("-", " ").title() + " - District"
        if any(mv in t["url"] for t in p["targets"]): return "Already tracking that movie.", False
        p["targets"].append({"name": name, "url": f"https://www.district.in/movies/{slug}-movie-tickets-in-{{city}}-{mv}"})
        return f"Added: {name}", True
    if c == "/removemovie" and rest:
        q = " ".join(rest).lower(); n = len(p["targets"]); p["targets"] = [t for t in p["targets"] if q not in t["name"].lower()]
        return (f"Removed {n - len(p['targets'])} movie(s).", True) if len(p["targets"]) < n else ("No movie matched.", False)
    if c == "/addtheatre" and rest:
        low = [w.lower() for w in rest]; movies = "*"
        if "for" in low:
            i = low.index("for"); movies = rest[i + 1:]; rest = rest[:i]; low = low[:i]
            if not movies: return "Add movie words after 'for'.", False
        name = " ".join(w for w in rest if w.lower() != "imax").strip()
        if not name: return "Give a theatre name, e.g. /addtheatre sathyam", False
        r = {"match": name, "movies": movies}
        if "imax" in low: r["imax"] = True
        p["theatres"].append(r); return "Added: " + bot.rule_label(r), True
    if c == "/removetheatre" and rest:
        q = " ".join(rest).lower(); n = len(p["theatres"]); p["theatres"] = [r for r in p["theatres"] if q not in r["match"].lower()]
        return (f"Removed {n - len(p['theatres'])} rule(s).", True) if len(p["theatres"]) < n else ("No theatre rule matched.", False)
    return bot.HELP, False


def process_commands(dyn, timeout=0):
    if not os.getenv("TELEGRAM_TOKEN") or not os.getenv("TELEGRAM_CHAT_ID"): return False
    bot.search_fn = search_movies
    changed, n = bot.process_updates(dyn, handle, timeout)
    if changed or n: save_dyn(dyn)
    return changed


def main():
    args = sys.argv[1:]
    if "--set-menu" in args:
        menu = [("menu", "Open the control panel"), ("list", "Status of tracked movies"), ("pause", "Pause alerts, e.g. /pause 2h"), ("resume", "Turn alerts back on"), ("help", "Help")]
        print(bot.tg("setMyCommands", commands=[{"command": a, "description": b} for a, b in menu])); return 0
    if "--test-telegram" in args: bot.send(os.environ["TELEGRAM_CHAT_ID"], "✅ Ticket watcher: Telegram is working."); return 0
    base, dyn = load_all()
    if "--search" in args:
        q = args[args.index("--search") + 1]
        for sl, mv in search_movies(q, dyn["users"][dyn["_owner"]]["cities"][0]): print(mv, sl)
        return 0
    process_commands(dyn)
    state = load_state()
    now = run_checks(dyn, state, "--debug" in args)
    hb = base.get("heartbeat_hour_ist"); today = now.strftime("%Y-%m-%d")
    if hb is not None and now.hour == hb and state.get("hb") != today:
        bot.send(dyn["_owner"], "💓 Ticket watcher still running."); state["hb"] = today
    save_state(state); finish_rechecks(dyn); save_dyn(dyn)
    if "--listen" in args:   # stay alive to answer button taps quickly
        end = time.time() + int(args[args.index("--listen") + 1])
        while time.time() < end:
            process_commands(dyn, timeout=min(25, max(1, int(end - time.time()))))
            if any("_recheck" in p for p in dyn["users"].values()):
                run_checks(dyn, state, False); save_state(state); finish_rechecks(dyn); save_dyn(dyn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
