"""Button-based Telegram UI for the ticket watcher."""
import json, os, urllib.request, urllib.error

CITIES = ["Chennai", "Bengaluru", "Mumbai", "Delhi NCR", "Hyderabad", "Kolkata", "Pune", "Kochi"]


def tg(method, **p):
    import time
    for attempt in range(3):   # retry network blips (not HTTP errors like 400)
        req = urllib.request.Request(f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN']}/{method}",
                                     json.dumps(p).encode(), {"Content-Type": "application/json"})
        try:
            return json.loads(urllib.request.urlopen(req, timeout=70).read())
        except urllib.error.HTTPError: raise
        except Exception:
            if attempt == 2: raise
            time.sleep(2)


def kb(rows): return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in r] for r in rows]}
def short(n): return n.replace(" - District", "")
def mv_text(r): return "all movies" if r.get("movies", "*") == "*" else ", ".join(r["movies"])
def rule_label(r): return f"{r['match']}{' • IMAX only' if r.get('imax') else ''} → {mv_text(r)}"
BACK = [("⬅ Back to menu", "home")]


def s_main(cfg):
    th = len(cfg["theatres"]) or "any"
    return (f"🎟️ Ticket Watcher\n\n📍 City: {cfg['city'].title()}\n🎬 Movies being tracked: {len(cfg['targets'])}\n🎭 Theatre rules: {th}\n\nWhat do you want to do?",
            kb([[("🎬 Movies", "movies"), ("🎭 Theatres", "theatres")],
                [("📍 City", "city"), ("📊 Status", "status")], [("❓ Help", "help")]]))


def s_movies(cfg):
    t = "🎬 Movies being tracked\n(tap ❌ to stop tracking)\n" + ("" if cfg["targets"] else "\nNone yet.")
    rows = [[("❌ " + short(x["name"]), f"rm_m:{i}")] for i, x in enumerate(cfg["targets"])]
    return t, kb(rows + [[("➕ Add movie", "add_m")], BACK])


def s_theatres(cfg):
    t = "🎭 Your theatres\n(tap ❌ to remove)\n" + ("" if cfg["theatres"] else "\nNo rules: I alert for ANY theatre.")
    rows = [[("❌ " + rule_label(r), f"rm_t:{i}")] for i, r in enumerate(cfg["theatres"])]
    return t, kb(rows + [[("➕ Add theatre", "add_t")], BACK])


def s_city(cfg):
    rows, row = [], []
    for c in CITIES:
        row.append((("✅ " if c.lower() == cfg["city"] else "") + c, "city:" + c.lower()))
        if len(row) == 2: rows.append(row); row = []
    if row: rows.append(row)
    return f"📍 Pick your city (now: {cfg['city'].title()})", kb(rows + [[("✏️ Other city", "city_other")], BACK])


def s_status(cfg, dyn):
    last = dyn.get("_last")
    if not last: return "📊 No check has run yet.", kb([BACK])
    lines = [f"📊 Last check: {last['at']} IST\n"]
    for t in cfg["targets"]:
        it = last["items"].get(t["name"])
        if not it: lines.append(f"⚪ {short(t['name'])}: not checked yet"); continue
        if it["res"] == "open": lines.append(f"🟢 {short(t['name'])}: shows at\n" + "\n".join("   • " + x.split(",")[0] for x in it["th"][:6]))
        elif it["res"] in ("blocked", "error"): lines.append(f"⚠️ {short(t['name'])}: can't read page")
        else: lines.append(f"⚪ {short(t['name'])}: nothing at your theatres yet")
    return "\n".join(lines), kb([[("🔄 Check now", "refresh")], BACK])


HELP = ("❓ How it works\n\n• I check District every few minutes and message you when one of your theatres shows a movie you watch.\n"
        "• 🎬 Movies: add by pasting the movie's District link.\n• 🎭 Theatres: choose name, format (any / IMAX) and which movies.\n"
        "• No theatre rules = alert for any theatre.\n\nTap /menu anytime.")


def s_pick_movies(cfg, p):
    rows = [[(("☑ " if i in p["sel"] else "☐ ") + short(t["name"]), f"tm:{i}")] for i, t in enumerate(cfg["targets"])]
    rows.append([(("✅ " if not p["sel"] else "") + "All movies", "tm:all")])
    rows.append([("💾 Save", "ts"), ("Cancel", "home")])
    return f"🎭 {p['name']} ({'IMAX only' if p['imax'] else 'any format'})\nWhich movies should this theatre apply to?", kb(rows)


def prompt(text): return text, kb([[("Cancel", "home")]])


def on_callback(d, cfg, dyn, handle_text):
    changed, p = False, dyn.get("_pending")
    a, _, arg = d.partition(":")
    if a == "home": dyn.pop("_pending", None); return False, s_main(cfg)
    if a == "movies": return False, s_movies(cfg)
    if a == "theatres": return False, s_theatres(cfg)
    if a == "city": 
        if arg:
            cfg["city"] = arg; return True, s_city(cfg)
        return False, s_city(cfg)
    if a == "city_other": dyn["_pending"] = {"step": "city_name"}; return False, prompt("📍 Type the city name (as in District's links, e.g. coimbatore):")
    if a == "status": return False, s_status(cfg, dyn)
    if a == "help": return False, (HELP, kb([BACK]))
    if a == "rm_m" and arg.isdigit() and int(arg) < len(cfg["targets"]): cfg["targets"].pop(int(arg)); return True, s_movies(cfg)
    if a == "rm_t" and arg.isdigit() and int(arg) < len(cfg["theatres"]): cfg["theatres"].pop(int(arg)); return True, s_theatres(cfg)
    if a == "add_m": dyn["_pending"] = {"step": "movie_link"}; return False, prompt("🎬 Send me the movie's District link.\n(Open the movie in District → Share/copy link → paste here)")
    if a == "add_t": dyn["_pending"] = {"step": "theatre_name"}; return False, prompt("🎭 Type the theatre name (part of it is fine), e.g. Sathyam, Palazzo, Cinepolis:")
    if a == "tf" and p and p.get("step") == "format":
        p.update(step="movies", imax=(arg == "imax"), sel=[]); return False, s_pick_movies(cfg, p)
    if a == "tm" and p and p.get("step") == "movies":
        if arg == "all": p["sel"] = []
        elif arg.isdigit():
            i = int(arg); p["sel"] = [x for x in p["sel"] if x != i] if i in p["sel"] else p["sel"] + [i]
        return False, s_pick_movies(cfg, p)
    if a == "ts" and p and p.get("step") == "movies":
        mv = "*" if not p["sel"] else [short(cfg["targets"][i]["name"]).lower() for i in p["sel"] if i < len(cfg["targets"])]
        r = {"match": p["name"], "movies": mv}
        if p["imax"]: r["imax"] = True
        cfg["theatres"].append(r); dyn.pop("_pending", None)
        return True, s_theatres(cfg)
    return False, s_main(cfg)


def on_text(text, cfg, dyn, handle_text):
    """Returns (changed, screen or None, extra_reply or None)."""
    p = dyn.get("_pending")
    low = text.lower().split()[0] if text.startswith("/") else ""
    if low in ("/start", "/menu"): dyn.pop("_pending", None); return False, s_main(cfg), None
    if low == "/list": return False, s_status(cfg, dyn), None
    if text.startswith("/"):
        reply, ch = handle_text(text, cfg); return ch, None, reply
    if p and p["step"] == "movie_link":
        reply, ch = handle_text("/addmovie " + text, cfg)
        if ch: dyn.pop("_pending", None)
        return ch, (s_movies(cfg) if ch else None), reply
    if p and p["step"] == "theatre_name":
        dyn["_pending"] = {"step": "format", "name": text.strip()}
        return False, ("🎭 " + text.strip() + "\nWhich format?", kb([[("Any format", "tf:any"), ("🎥 IMAX only", "tf:imax")], [("Cancel", "home")]])), None
    if p and p["step"] == "city_name":
        cfg["city"] = text.strip().lower().replace(" ", "-"); dyn.pop("_pending", None)
        return True, s_city(cfg), None
    return False, s_main(cfg), None


def process_updates(cfg, dyn, chat, handle_text, timeout=0):
    """Fetch + handle updates. Returns (config_changed, n_updates)."""
    try:
        r = tg("getUpdates", offset=dyn.get("_offset", 0), timeout=timeout, allowed_updates=["message", "callback_query"])
    except Exception as e:
        print("getUpdates failed:", e); return False, 0
    changed = False
    for u in r.get("result", []):
        try:
            cb = u.get("callback_query")
            if cb:
                m = cb["message"]
                if str(m["chat"]["id"]) != chat: dyn["_offset"] = u["update_id"] + 1; continue
                if cb["data"] == "refresh":
                    dyn["_recheck"] = m["message_id"]
                    try: tg("editMessageText", chat_id=chat, message_id=m["message_id"], text="🔄 Checking District now… (about 30 seconds)")
                    except Exception: pass
                    try: tg("answerCallbackQuery", callback_query_id=cb["id"])
                    except Exception: pass
                    changed = True
                    dyn["_offset"] = u["update_id"] + 1
                    continue
                ch, (txt, mk) = on_callback(cb["data"], cfg, dyn, handle_text); changed |= ch
                note = None
                try: tg("editMessageText", chat_id=chat, message_id=m["message_id"], text=txt, reply_markup=mk)
                except urllib.error.HTTPError as e:
                    body = e.read().decode(errors="ignore")
                    if "not modified" in body: note = "Already up to date ✅"
                    else: print("edit failed:", e.code, body)
                except Exception as e: print("edit failed:", e)
                try: tg("answerCallbackQuery", callback_query_id=cb["id"], **({"text": note} if note else {}))
                except Exception: pass
                dyn["_offset"] = u["update_id"] + 1
                continue
            m = u.get("message") or {}
            text = (m.get("text") or "").strip()
            if str(m.get("chat", {}).get("id")) != chat or not text:
                dyn["_offset"] = u["update_id"] + 1; continue
            ch, screen, reply = on_text(text, cfg, dyn, handle_text); changed |= ch
            if reply: tg("sendMessage", chat_id=chat, text=reply)
            if screen: tg("sendMessage", chat_id=chat, text=screen[0], reply_markup=screen[1])
            dyn["_offset"] = u["update_id"] + 1
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            print("network error, will retry this update next time:", e)
            break   # keep the offset so the message is handled on the next run
        except Exception as e:
            print("update error:", e); dyn["_offset"] = u["update_id"] + 1   # real bug: skip it, don't loop
    return changed, len(r.get("result", []))
