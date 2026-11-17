"""Telegram UI (buttons), per-user profiles and access control for the ticket watcher."""
import datetime, json, os, re, time, urllib.error, urllib.request

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
FOREVER = 4_000_000_000
CITIES = ["Chennai", "Bengaluru", "Mumbai", "Delhi NCR", "Hyderabad", "Kolkata", "Pune", "Kochi"]
LANG_LIST = ["Tamil", "English", "Hindi", "Telugu", "Malayalam", "Kannada"]
search_fn = None


def tg(method, **p):
    for attempt in range(3):   # retry network blips (not HTTP errors like 400)
        req = urllib.request.Request(f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN']}/{method}",
                                     json.dumps(p).encode(), {"Content-Type": "application/json"})
        try: return json.loads(urllib.request.urlopen(req, timeout=70).read())
        except urllib.error.HTTPError: raise
        except Exception:
            if attempt == 2: raise
            time.sleep(2)


def send(chat, text, markup=None):
    if not os.getenv("TELEGRAM_TOKEN"): print("[no telegram]", text); return None
    p = {"chat_id": chat, "text": text, "disable_web_page_preview": True}
    if markup: p["reply_markup"] = markup
    try: return tg("sendMessage", **p)
    except Exception as e: print("send failed:", e)


def kb(rows): return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in r] for r in rows]}
def short(n): return n.replace(" - District", "")
def mvid(t): m = re.search(r"MV\d+", t["url"]); return m.group(0) if m else t["name"]
def mv_text(r): return "all movies" if r.get("movies", "*") == "*" else ", ".join(r["movies"])
def rule_label(r): return f"{r['match']}{' • IMAX only' if r.get('imax') else ''} → {mv_text(r)}"
def is_paused(p): return p.get("paused", 0) > time.time()
BACK = [("⬅ Back to menu", "home")]


def new_profile(name, cities=None, targets=None, theatres=None):
    return {"name": name, "cities": list(cities or ["chennai"]), "targets": targets or [], "theatres": theatres or [],
            "languages": [], "paused": 0, "muted": []}


def parse_dur(s):
    m = re.fullmatch(r"(\d+)\s*([mhd])", s.strip().lower())
    return int(m.group(1)) * {"m": 60, "h": 3600, "d": 86400}[m.group(2)] if m else None


def pause_text(p):
    if not is_paused(p): return "▶ Alerts are ON"
    if p["paused"] > FOREVER - 10**7: return "⏸ Paused until you resume"
    return "⏸ Paused until " + datetime.datetime.fromtimestamp(p["paused"], IST).strftime("%d %b %H:%M")


def alert_markup(mv, muted=False):
    if muted: return kb([[("🔔 Undo mute", f"unmute:{mv}")]])
    return kb([[("🔕 Mute this movie", f"mute:{mv}"), ("⏸ Pause 2h", "pause:2h")]])


# ---------- screens ----------
def s_main(p, owner):
    n = len(p["theatres"]) or "any"
    cities = ", ".join(c.title() for c in p["cities"])
    langs = ", ".join(l.title() for l in p["languages"]) or "any"
    rows = [[("🎬 Movies", "movies"), ("🎭 Theatres", "theatres")], [("📍 Cities", "city"), ("🌐 Languages", "langs")],
            [("📊 Status", "status"), ("⏸ Pause" if not is_paused(p) else "▶ Resume", "pause")]]
    last = [("❓ Help", "help")] + ([("👥 Users", "users")] if owner else [])
    return (f"🎟️ Ticket Watcher\n\n📍 {cities}\n🎬 {len(p['targets'])} movies · 🎭 {n} theatre rules\n🌐 Languages: {langs}\n{pause_text(p)}\n\nWhat do you want to do?",
            kb(rows + [last]))


def s_movies(p):
    t = "🎬 Movies being tracked\n(tap a movie to mute/unmute · ❌ to stop tracking)\n" + ("" if p["targets"] else "\nNone yet.")
    rows = [[(("🔕 " if mvid(x) in p["muted"] else "🎬 ") + short(x["name"]), f"mu:{i}"), ("❌", f"rm_m:{i}")] for i, x in enumerate(p["targets"])]
    return t, kb(rows + [[("➕ Add movie", "add_m")], BACK])


def s_theatres(p):
    t = "🎭 Your theatres\n(tap ❌ to remove)\n" + ("" if p["theatres"] else "\nNo rules: I alert for ANY theatre.")
    rows = [[("❌ " + rule_label(r), f"rm_t:{i}")] for i, r in enumerate(p["theatres"])]
    return t, kb(rows + [[("➕ Add theatre", "add_t")], BACK])


def s_cities(p):
    rows, row = [], []
    for c in CITIES:
        row.append((("✅ " if c.lower().replace(" ", "-") in p["cities"] else "☐ ") + c, "cy:" + c.lower().replace(" ", "-")))
        if len(row) == 2: rows.append(row); row = []
    if row: rows.append(row)
    extra = [c for c in p["cities"] if c not in [x.lower().replace(" ", "-") for x in CITIES]]
    rows += [[("✅ " + c.title(), "cy:" + c)] for c in extra]
    return "📍 Cities to track (tap to turn on/off)", kb(rows + [[("✏️ Add another city", "city_other")], BACK])


def s_langs(p):
    rows, row = [], []
    for l in LANG_LIST:
        row.append((("✅ " if l.lower() in p["languages"] else "☐ ") + l, "lg:" + l.lower()))
        if len(row) == 2: rows.append(row); row = []
    if row: rows.append(row)
    return ("🌐 Only alert for these languages\n(none ticked = any language)", kb(rows + [[("Any language", "lg:any")], BACK]))


def s_pause(p):
    rows = [[("1 hour", "pz:1h"), ("3 hours", "pz:3h")], [("1 day", "pz:1d"), ("Until I resume", "pz:inf")]]
    if is_paused(p): rows.append([("▶ Resume now", "pz:0")])
    return pause_text(p) + "\n\nPause alerts for how long?\n(Missed openings are still sent after you resume.)", kb(rows + [BACK])


def s_status(p):
    last = p.get("_last")
    if not last: return "📊 No check has run yet.", kb([[("🔄 Check now", "refresh")], BACK])
    multi = len(p["cities"]) > 1
    lines = [f"📊 Last check: {last['at']} IST · {pause_text(p)}\n"]
    for c in p["cities"]:
        for t in p["targets"]:
            it = last["items"].get(f"{t['name']}|{c}")
            nm = short(t["name"]) + (f" ({c.title()})" if multi else "")
            if not it: lines.append(f"⚪ {nm}: not checked yet")
            elif it["res"] == "open": lines.append(f"🟢 {nm}: shows at\n" + "\n".join("   • " + x.split(",")[0] for x in it["th"][:6]))
            elif it["res"] in ("blocked", "error"): lines.append(f"⚠️ {nm}: can't read page")
            else: lines.append(f"⚪ {nm}: nothing at your theatres yet")
    return "\n".join(lines), kb([[("🔄 Check now", "refresh")], BACK])


def s_users(dyn, owner):
    rows, t = [], "👥 People using this bot\n"
    for uid, u in dyn["users"].items():
        t += f"\n• {u['name']}{' (you)' if uid == owner else ''}"
        if uid != owner: rows.append([(f"❌ Remove {u['name']}", f"rmu:{uid}")])
    for uid, name in dyn["pending_users"].items():
        t += f"\n• {name} — waiting for approval"
        rows.append([(f"✅ Approve {name}", f"ap:{uid}"), ("🚫 Deny", f"dn:{uid}")])
    return t, kb(rows + [BACK])


HELP = ("❓ How it works\n\n• I check District every few minutes and message you when one of your theatres shows a movie you track.\n"
        "• 🎬 Movies: add by name or district.in link. Tap a movie to mute it.\n• 🎭 Theatres: name, format (any / IMAX) and which movies.\n"
        "• 🌐 Languages and 📍 cities narrow the alerts.\n• ⏸ Pause: /pause 2h, /resume.\n• No theatre rules = alert for any theatre.\n\nOpen the panel with /menu.")


def s_results(res):
    rows = [[("➕ " + sl.replace("-", " ").title(), f"pick:{i}")] for i, (sl, mv) in enumerate(res)]
    return "🔎 Tap the right movie:", kb(rows + [[("Cancel", "home")]])


def s_pick_movies(p, pend):
    rows = [[(("☑ " if i in pend["sel"] else "☐ ") + short(t["name"]), f"tm:{i}")] for i, t in enumerate(p["targets"])]
    rows.append([(("✅ " if not pend["sel"] else "") + "All movies", "tm:all")])
    rows.append([("💾 Save", "ts"), ("Cancel", "home")])
    return f"🎭 {pend['name']} ({'IMAX only' if pend['imax'] else 'any format'})\nWhich movies should this theatre apply to?", kb(rows)


def prompt(text): return text, kb([[("Cancel", "home")]])


# ---------- callbacks / text (p = the user's profile; transient state lives in p['_...']) ----------
def on_callback(d, p, dyn, handle_text, owner):
    pend = p.get("_pending"); a, _, arg = d.partition(":"); is_owner = owner
    if a == "home": p.pop("_pending", None); return False, s_main(p, is_owner)
    if a == "movies": return False, s_movies(p)
    if a == "theatres": return False, s_theatres(p)
    if a == "city": return False, s_cities(p)
    if a == "langs": return False, s_langs(p)
    if a == "status": return False, s_status(p)
    if a == "pause": return False, s_pause(p)
    if a == "help": return False, (HELP, kb([BACK]))
    if a == "users" and is_owner: return False, s_users(dyn, dyn["_owner"])
    if a == "cy" and arg:
        if arg in p["cities"]:
            if len(p["cities"]) > 1: p["cities"].remove(arg)
        else: p["cities"].append(arg)
        return True, s_cities(p)
    if a == "city_other": p["_pending"] = {"step": "city_name"}; return False, prompt("📍 Type the city name (as in District's links, e.g. coimbatore):")
    if a == "lg":
        if arg == "any": p["languages"] = []
        elif arg in p["languages"]: p["languages"].remove(arg)
        else: p["languages"].append(arg)
        return True, s_langs(p)
    if a == "pz":
        secs = FOREVER if arg == "inf" else (0 if arg == "0" else parse_dur(arg))
        p["paused"] = 0 if not secs else (FOREVER if arg == "inf" else time.time() + secs)
        return True, s_main(p, is_owner)
    if a == "mu" and arg.isdigit() and int(arg) < len(p["targets"]):
        m = mvid(p["targets"][int(arg)])
        p["muted"].remove(m) if m in p["muted"] else p["muted"].append(m)
        return True, s_movies(p)
    if a == "rm_m" and arg.isdigit() and int(arg) < len(p["targets"]): p["targets"].pop(int(arg)); return True, s_movies(p)
    if a == "rm_t" and arg.isdigit() and int(arg) < len(p["theatres"]): p["theatres"].pop(int(arg)); return True, s_theatres(p)
    if a == "add_m": return False, ("🎬 How do you want to add the movie?", kb([[("🔎 Search by name", "add_m_name")], [("🔗 Paste a link", "add_m_link")], [("⬅ Back", "movies")]]))
    if a == "add_m_name": p["_pending"] = {"step": "movie_link"}; return False, prompt("🔎 Type the movie name (e.g. Jailer 2):")
    if a == "add_m_link": p["_pending"] = {"step": "movie_link"}; return False, prompt("🔗 Paste the movie's district.in link:")
    if a == "pick" and arg.isdigit() and int(arg) < len(p.get("_results", [])):
        sl, mv = p["_results"][int(arg)]
        handle_text(f"/addmovie https://www.district.in/movies/{sl}-movie-tickets-{mv}", p)
        p.pop("_pending", None); p.pop("_results", None); return True, s_movies(p)
    if a == "add_t": p["_pending"] = {"step": "theatre_name"}; return False, prompt("🎭 Type the theatre name (part of it is fine), e.g. Sathyam, Palazzo, Cinepolis:")
    if a == "tf" and pend and pend.get("step") == "format":
        pend.update(step="movies", imax=(arg == "imax"), sel=[]); return False, s_pick_movies(p, pend)
    if a == "tm" and pend and pend.get("step") == "movies":
        if arg == "all": pend["sel"] = []
        elif arg.isdigit():
            i = int(arg); pend["sel"] = [x for x in pend["sel"] if x != i] if i in pend["sel"] else pend["sel"] + [i]
        return False, s_pick_movies(p, pend)
    if a == "ts" and pend and pend.get("step") == "movies":
        mv = "*" if not pend["sel"] else [short(p["targets"][i]["name"]).lower() for i in pend["sel"] if i < len(p["targets"])]
        r = {"match": pend["name"], "movies": mv}
        if pend["imax"]: r["imax"] = True
        p["theatres"].append(r); p.pop("_pending", None); return True, s_theatres(p)
    return False, s_main(p, is_owner)


def on_text(text, p, handle_text, owner):
    """Returns (changed, screen or None, extra_reply or None)."""
    pend = p.get("_pending"); low = text.lower().split()[0] if text.startswith("/") else ""
    if low in ("/start", "/menu"): p.pop("_pending", None); return False, s_main(p, owner), None
    if low == "/list": return False, s_status(p), None
    if text.startswith("/"):
        reply, ch = handle_text(text, p); return ch, None, reply
    if pend and pend["step"] == "movie_link":
        if "http" not in text and search_fn:
            send(p["_chat"], "🔎 Searching District…")
            try: res = search_fn(text, p["cities"][0])
            except Exception as e: print("search failed:", e); res = []
            if not res: return False, None, "No match for that name. Try another spelling, or paste the district.in link."
            p["_results"] = [list(x) for x in res]; return False, s_results(res), None
        reply, ch = handle_text("/addmovie " + text, p)
        if ch: p.pop("_pending", None)
        return ch, (s_movies(p) if ch else None), reply
    if pend and pend["step"] == "theatre_name":
        p["_pending"] = {"step": "format", "name": text.strip()}
        return False, ("🎭 " + text.strip() + "\nWhich format?", kb([[("Any format", "tf:any"), ("🎥 IMAX only", "tf:imax")], [("Cancel", "home")]])), None
    if pend and pend["step"] == "city_name":
        c = text.strip().lower().replace(" ", "-")
        if c not in p["cities"]: p["cities"].append(c)
        p.pop("_pending", None); return True, s_cities(p), None
    return False, s_main(p, owner), None


# ---------- update loop ----------
def process_updates(dyn, handle_text, timeout=0):
    """Fetch + handle updates for ALL users. Returns (changed, n_updates)."""
    owner = dyn["_owner"]
    try: r = tg("getUpdates", offset=dyn.get("_offset", 0), timeout=timeout, allowed_updates=["message", "callback_query"])
    except Exception as e: print("getUpdates failed:", e); return False, 0
    changed = False
    for u in r.get("result", []):
        try:
            changed |= _one(u, dyn, handle_text, owner)
            dyn["_offset"] = u["update_id"] + 1
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            print("network error, will retry this update next time:", e); break
        except Exception as e:
            print("update error:", e); dyn["_offset"] = u["update_id"] + 1
    return changed, len(r.get("result", []))


def _edit(chat, m, text, markup):
    p = {"chat_id": chat, "message_id": m["message_id"], "text": text}
    if markup: p["reply_markup"] = markup   # Telegram rejects reply_markup: null
    try: tg("editMessageText", **p); return None
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")
        if "not modified" in body: return "Already up to date ✅"
        print("edit failed:", e.code, body)
    except Exception as e: print("edit failed:", e)


def _answer(cb, note=None):
    try: tg("answerCallbackQuery", callback_query_id=cb["id"], **({"text": note} if note else {}))
    except Exception: pass


def _one(u, dyn, handle_text, owner):
    cb = u.get("callback_query"); m = cb["message"] if cb else (u.get("message") or {})
    chat = str(m.get("chat", {}).get("id", "")); who = (cb or m).get("from", {}).get("first_name", "Someone")
    if not chat: return False
    if chat != owner and chat not in dyn["users"]:   # stranger: ask the owner
        if cb: _answer(cb); return False
        if chat in dyn["pending_users"]: send(chat, "⏳ Still waiting for the owner to approve you.")
        else:
            dyn["pending_users"][chat] = who
            send(chat, "👋 I've asked the owner to let you in. You'll get a message when approved.")
            send(owner, f"🔔 {who} wants to use the ticket bot.", kb([[("✅ Approve", f"ap:{chat}"), ("🚫 Deny", f"dn:{chat}")]]))
        return True
    p = dyn["users"][chat]; p["_chat"] = chat; is_owner = chat == owner
    if cb:
        d = cb["data"]; a, _, arg = d.partition(":")
        if is_owner and a in ("ap", "dn", "rmu"):
            if a == "ap" and arg in dyn["pending_users"]:
                name = dyn["pending_users"].pop(arg); dyn["users"][arg] = new_profile(name, p["cities"])
                send(arg, "✅ You're in! Send /menu to set up your movies and theatres.")
            elif a == "dn": 
                name = dyn["pending_users"].pop(arg, None)
                if name: send(arg, "Sorry, the owner didn't approve access.")
            elif a == "rmu" and arg in dyn["users"] and arg != owner: dyn["users"].pop(arg)
            _answer(cb); _edit(chat, m, *s_users(dyn, owner)); return True
        if a == "refresh":
            p["_recheck"] = m["message_id"]; _edit(chat, m, "🔄 Checking District now… (about 30 seconds)", None); _answer(cb); return True
        if a in ("mute", "unmute"):
            (p["muted"].append(arg) if a == "mute" and arg not in p["muted"] else p["muted"].remove(arg) if a == "unmute" and arg in p["muted"] else None)
            _answer(cb, "🔕 Muted. Undo anytime." if a == "mute" else "🔔 Unmuted")
            try: tg("editMessageReplyMarkup", chat_id=chat, message_id=m["message_id"], reply_markup=alert_markup(arg, a == "mute"))
            except Exception as e: print("markup:", e)
            return True
        if a == "pause" and arg != "":      # pause:2h from an alert, keeps the alert text
            secs = parse_dur(arg)
            if secs: p["paused"] = time.time() + secs
            _answer(cb, f"⏸ Paused for {arg}"); 
            try: tg("editMessageReplyMarkup", chat_id=chat, message_id=m["message_id"], reply_markup=kb([[("▶ Resume alerts", "resume")]]))
            except Exception: pass
            return True
        if a == "resume": p["paused"] = 0; _answer(cb, "▶ Alerts on"); return True
        ch, (txt, mk) = on_callback(d, p, dyn, handle_text, is_owner)
        note = _edit(chat, m, txt, mk); _answer(cb, note); return ch
    text = (m.get("text") or "").strip()
    if not text: return False
    ch, screen, reply = on_text(text, p, handle_text, is_owner)
    if reply: send(chat, reply)
    if screen: send(chat, screen[0], screen[1])
    return ch
