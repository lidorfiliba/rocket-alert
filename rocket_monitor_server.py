"""
RocketAlert Monitor — שרת ענן
================================
מריץ 24/7 על PythonAnywhere ושולח התרעות לטלגרם

הוראות התקנה ב-PythonAnywhere:
1. הירשם בחינם על pythonanywhere.com
2. לחץ "Files" → העלה קובץ זה
3. לחץ "Consoles" → "Bash"
4. הרץ: pip install requests --user
5. לחץ "Tasks" → "Always-on task" → python3 rocket_monitor_server.py
"""

import requests, json, time, os, urllib.request, re
from datetime import datetime, date, timedelta

# ══ הגדרות ══
TG_BOT_TOKEN = "8545041316:AAHYqIskfkcDwgMTw4Qk5tRmQqrNf31BPao"
TG_CHAT_ID   = 1632096542
POLL_SECS    = 15   # בדיקה כל 15 שניות

OREF_ACTIVE  = "https://www.oref.org.il/WarningMessages/alert/alerts.json"
OREF_HISTORY = "https://www.oref.org.il/warningMessages/alert/History/AlertsHistory.json"
OREF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 11)",
    "Referer": "https://www.oref.org.il/",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, */*",
    "Accept-Language": "he-IL,he;q=0.9",
}

END_CATS = {10, 11, 12}
UAV_CATS = {3, 4, 17}

TG_CHANNELS = [
    "oref_updates", "tzeva_adom_israel", "israelradar",
    "kann_news", "newsisrael13", "i24newsil",
]

RSS_FEEDS = [
    "https://rss.walla.co.il/feed/22",
    "https://www.ynet.co.il/Integration/StoryRss2.xml",
    "https://www.timesofisrael.com/feed/",
    "https://www.kan.org.il/rss/",
]

LAUNCH_KEYWORDS = [
    "זוהו שיגורים", "שיגורים לעבר", "שיגורים בדרך",
    "טיל בליסטי", "מטח רקטות", "תזוזת משגרים",
    "launches detected", "ballistic missile", "rocket barrage",
    "IRGC", "איראן", "חיזבאללה",
]

NITTER_ACCOUNTS = ["AvichayAdraee", "manniefabian", "tzahalnews"]
NITTER_SERVERS  = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
]

# ══ מצב ══
sent_areas   = {}   # area -> timestamp
last_alert_dt = datetime.now()
last_warn_text = ""

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

# ══ שליחה לטלגרם ══
def tg_send(text):
    try:
        url  = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        data = {"chat_id": TG_CHAT_ID, "text": text}
        requests.post(url, json=data, timeout=10)
        log(f"TG sent: {text[:60]}")
    except Exception as e:
        log(f"TG error: {e}")

def already_sent(area, cooldown=600):
    now = time.time()
    if now - sent_areas.get(area, 0) < cooldown:
        return True
    sent_areas[area] = now
    return False

# ══ JSON בטוח ══
def safe_json(text):
    if not text: return None
    t = text.strip().lstrip("\ufeff")
    if not t or t[0] not in ("{","["): return None
    try:
        return json.loads(t)
    except Exception:
        return None

def extract_areas(item):
    if isinstance(item, str): return [item.strip()] if item.strip() else []
    if not isinstance(item, dict): return []
    areas = []
    for field in ("data","title","cities"):
        val = item.get(field,"")
        if isinstance(val, list):
            areas += [v for v in val if isinstance(v,str) and v.strip()]
        elif isinstance(val, str) and val.strip():
            areas.append(val.strip())
    return areas

# ══ בדיקת אזעקות ══
def check_alerts():
    global last_alert_dt
    session = requests.Session()
    session.headers.update(OREF_HEADERS)

    # alerts.json
    try:
        r = session.get(OREF_ACTIVE, timeout=5)
        r.encoding = "utf-8-sig"
        raw = safe_json(r.text)
        if raw:
            root_cat = int(raw.get("cat", raw.get("category", 0))) \
                       if isinstance(raw, dict) else 0
            if root_cat not in END_CATS:
                items = raw if isinstance(raw,list) else raw.get("data",[])
                if isinstance(items, list) and items:
                    new_areas = []
                    for a in items:
                        area = a if isinstance(a,str) else a.get("data","")
                        if area and not already_sent(area):
                            new_areas.append(area)
                    if new_areas:
                        threat = "UAV" if root_cat in UAV_CATS else \
                                 "BALLISTIC" if root_cat == 13 else "ROCKET"
                        cities = ", ".join(new_areas[:6])
                        extra  = f" (+{len(new_areas)-6})" if len(new_areas)>6 else ""
                        tg_send(f"🚨 אזעקה [{threat}]:\n{cities}{extra}")
                        return
    except Exception as e:
        log(f"alerts.json error: {e}")

    # history — אזעקות חדשות מאז ההפעלה
    try:
        r2 = session.get(OREF_HISTORY, timeout=10)
        r2.encoding = "utf-8-sig"
        hist = safe_json(r2.text)
        if isinstance(hist, list):
            now_dt = datetime.now()
            new_areas = []
            for it in hist:
                if not isinstance(it, dict): continue
                try:
                    idt = datetime.strptime(it.get("alertDate",""), "%Y-%m-%d %H:%M:%S")
                except Exception: continue
                if (now_dt - idt).total_seconds() > 180: break
                cat = int(it.get("category",0))
                if cat in END_CATS: continue
                if idt <= last_alert_dt: continue
                for a in extract_areas(it):
                    if not already_sent(a):
                        new_areas.append(a)
            if new_areas:
                last_alert_dt = now_dt
                cities = ", ".join(set(new_areas))[:200]
                tg_send(f"🔔 אזעקה בישראל:\n{cities}")
    except Exception as e:
        log(f"history error: {e}")

# ══ בדיקת מקורות מודיעין ══
def check_intelligence():
    global last_warn_text
    warnings = []

    # טלגרם web
    for ch in TG_CHANNELS:
        try:
            url = f"https://t.me/s/{ch}"
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=6) as r:
                html = r.read().decode("utf-8", errors="ignore")
            blocks = re.findall(
                r'<time[^>]*datetime="([^"]+)".*?'
                r'tgme_widget_message_text[^>]*>(.*?)</div>',
                html, re.DOTALL)
            for dt_str, msg_html in blocks:
                text = re.sub(r'<[^>]+>', '', msg_html).strip()
                if not text: continue
                try:
                    msg_dt = datetime.fromisoformat(
                        dt_str.replace("Z","+00:00")).replace(tzinfo=None)
                    msg_dt += timedelta(hours=3)
                    if (datetime.now() - msg_dt).total_seconds() > 1200: continue
                except Exception: pass
                for kw in LAUNCH_KEYWORDS:
                    if kw in text:
                        warnings.append(f"[{ch}] {text[:150]}")
                        break
        except Exception: continue

    # RSS
    for feed in RSS_FEEDS:
        try:
            req = urllib.request.Request(
                feed, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as r:
                text = r.read().decode("utf-8", errors="ignore")
            titles = re.findall(r'<title>([^<]{10,150})</title>', text)
            for t in titles[1:]:
                for kw in LAUNCH_KEYWORDS:
                    if kw.lower() in t.lower():
                        warnings.append(f"[RSS] {t[:150]}")
                        break
        except Exception: continue

    # Nitter (טוויטר)
    for account in NITTER_ACCOUNTS:
        for server in NITTER_SERVERS:
            try:
                url = f"{server}/{account}/rss"
                req = urllib.request.Request(
                    url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=5) as r:
                    text = r.read().decode("utf-8", errors="ignore")
                items = re.findall(
                    r'<item>.*?<title>(.*?)</title>.*?</item>',
                    text, re.DOTALL)
                for item in items[:5]:
                    item = re.sub(r'<[^>]+>', '', item).strip()
                    for kw in LAUNCH_KEYWORDS:
                        if kw.lower() in item.lower():
                            warnings.append(f"[@{account}] {item[:150]}")
                            break
                break
            except Exception: continue

    if warnings:
        first = warnings[0]
        if first != last_warn_text:
            last_warn_text = first
            if any(kw in first for kw in ["תזוזת משגרים","launcher"]):
                emoji = "📡"
            elif any(kw in first for kw in ["איראן","IRGC","חיזבאללה"]):
                emoji = "🔴"
            else:
                emoji = "⚠️"
            tg_send(f"{emoji} {first}")

# ══ לולאה ראשית ══
def main():
    log("RocketAlert Server started")
    tg_send("✅ RocketAlert Server פעיל — ניטור 24/7")
    cycle = 0
    while True:
        try:
            check_alerts()
            # בדוק מודיעין כל 3 סבבים (45 שניות)
            if cycle % 3 == 0:
                check_intelligence()
            cycle += 1
        except Exception as e:
            log(f"Main error: {e}")
        time.sleep(POLL_SECS)

if __name__ == "__main__":
    main()
