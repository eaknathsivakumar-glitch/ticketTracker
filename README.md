# Ticket watcher (BookMyShow / District -> Telegram)

1. Edit config.json: paste the movie page URLs. Optional: "require_text": ["PVR Phoenix"] to only alert when that theatre/text appears.
2. Test locally (needs: pip install playwright && playwright install chromium):
   export TELEGRAM_TOKEN=... TELEGRAM_CHAT_ID=...
   python watcher.py --test-telegram
   python watcher.py --debug     # prints page text + screenshots in ./debug; tune open_keywords from this
3. Deploy 24/7: push this folder as its own GitHub repo, add repo secrets TELEGRAM_TOKEN and TELEGRAM_CHAT_ID,
   enable Actions. Runs every 5 min, daily heartbeat at 9 AM IST, auto-disables after bookings open.
4. If you see "can't read ... blocked" alerts, run it on a home machine/VPS with cron instead:
   */3 * * * * cd /path/ticket-watcher && TELEGRAM_TOKEN=... TELEGRAM_CHAT_ID=... python3 watcher.py
