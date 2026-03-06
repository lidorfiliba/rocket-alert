"""
RocketAlert — GitHub Actions Check
רץ כל דקה דרך GitHub Actions
שולח לטלגרם רק אם יש משהו חדש
"""
import requests, json, time, urllib.request, re, os
from datetime import datetime, timedelta

TG_BOT_TOKEN = "8545041316:AAHYqIskfkcDwgMTw4Qk5tRmQqrNf31BPao"
TG_CHAT_ID   = -1003584552650

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
    "kann_news", "newsisrael13",
]

RSS_FEEDS = [
    "https://rss.walla.co.il/feed/22",
    "https://www.ynet.co.il/Integration/StoryRss2.xml",
    "https://www.timesofisrael.com/feed/",
]

LAUNCH_KEYWORDS = [
    "זוהו שיגורים", "שיגורים לעבר", "שיגורים בדרך",
    "טיל בליסטי", "מטח רקטות", "תזוזת משגרים",
    "launches detected", "ballistic missile", "rocket barrage",
    "IRGC", "איראן", "חיזבאללה",
]

STATE_FILE = "state.json"

# ── חלון זמן ──
# בודק היסטוריה של 3 דקות אחורה (במקום 5) כדי לא לפספס אזעקות קצרות
HISTORY_WINDOW_SEC = 180

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except:
        return {"sent_alerts": [], "last_warn": "", "last_alert_dt": ""}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def tg_send(text):
    try:
        url  = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        data = {"chat_id": TG_CHAT_ID, "text": text}
        r = requests.post(url, json=data, timeout=10)
        print(f"TG sent: {text[:60]}")
    except Exception as e:
        print(f"TG error: {e}")

def safe_json(text):
    if not text: return None
    t = text.strip().lstrip("\ufeff")
    if not t or t[0] not in ("{","["): return None
    try: return json.loads(t)
    except: return None

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

def main():
    state = load_state()
    sent  = set(state.get("sent_alerts", []))
    now   = datetime.now()

    session = requests.Session()
    session.headers.update(OREF_HEADERS)

    # ── 1. alerts.json — אזעקות פעילות עכשיו ──
    try:
        r = session.get(OREF_ACTIVE, timeout=5)
        r.encoding = "utf-8-sig"
        raw = safe_json(r.text)
        if raw:
            root_cat = int(raw.get("cat", raw.get("category", 0))) \
                       if isinstance(raw, dict) else 0
            if root_cat not in END_CATS:
                items = raw if isinstance(raw, list) else raw.get("data", [])
                if isinstance(items, list) and items:
                    new_areas = []
                    for a in items:
                        area = a if isinstance(a, str) else a.get("data", "")
                        key  = f"alert:{area}"
                        if area and key not in sent:
                            new_areas.append(area)
                            sent.add(key)
                    if new_areas:
                        threat = "UAV" if root_cat in UAV_CATS else \
                                 "BALLISTIC" if root_cat == 13 else "ROCKET"
                        cities = ", ".join(new_areas[:8])
                        extra  = f" (+{len(new_areas)-8})" if len(new_areas) > 8 else ""
                        tg_send(f"🚨 אזעקה [{threat}]:\n{cities}{extra}")
    except Exception as e:
        print(f"alerts error: {e}")

    # ── 2. AlertsHistory.json — היסטוריה של 3 דקות ──
    # זה מבטיח שאזעקות קצרות שפספסנו ב-alerts.json יתפסו כאן
    try:
        r2 = session.get(OREF_HISTORY, timeout=10)
        r2.encoding = "utf-8-sig"
        hist = safe_json(r2.text)
        if isinstance(hist, list):
            last_dt_str = state.get("last_alert_dt", "")
            last_dt = datetime.strptime(last_dt_str, "%Y-%m-%d %H:%M:%S") \
                      if last_dt_str else now - timedelta(seconds=HISTORY_WINDOW_SEC + 30)

            new_areas = []
            newest    = None

            for it in hist:
                if not isinstance(it, dict):
                    continue
                try:
                    idt = datetime.strptime(it.get("alertDate", ""), "%Y-%m-%d %H:%M:%S")
                except:
                    continue

                # עצור אם האזעקה ישנה מחלון הזמן
                if (now - idt).total_seconds() > HISTORY_WINDOW_SEC:
                    break

                cat = int(it.get("category", 0))
                if cat in END_CATS:
                    continue

                # דלג על מה שכבר שלחנו
                if idt <= last_dt:
                    continue

                if newest is None:
                    newest = idt

                for a in extract_areas(it):
                    key = f"hist:{a}:{idt.strftime('%Y%m%d%H%M')}"
                    if key not in sent:
                        new_areas.append((a, cat))
                        sent.add(key)

            if newest:
                state["last_alert_dt"] = newest.strftime("%Y-%m-%d %H:%M:%S")

            if new_areas:
                # קבץ לפי סוג איום
                by_threat = {}
                for area, cat in new_areas:
                    threat = "UAV" if cat in UAV_CATS else \
                             "BALLISTIC" if cat == 13 else "ROCKET"
                    by_threat.setdefault(threat, []).append(area)

                for threat, areas in by_threat.items():
                    cities = ", ".join(set(areas))[:250]
                    tg_send(f"🔔 אזעקה [{threat}]:\n{cities}")

    except Exception as e:
        print(f"history error: {e}")

    # ── 3. בדיקת מודיעין ──
    warnings = []

    for ch in TG_CHANNELS:
        try:
            url = f"https://t.me/s/{ch}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
            blocks = re.findall(
                r'<time[^>]*datetime="([^"]+)".*?tgme_widget_message_text[^>]*>(.*?)</div>',
                html, re.DOTALL)
            for dt_str, msg_html in blocks:
                text = re.sub(r'<[^>]+>', '', msg_html).strip()
                if not text:
                    continue
                try:
                    msg_dt = datetime.fromisoformat(
                        dt_str.replace("Z", "+00:00")).replace(tzinfo=None)
                    msg_dt += timedelta(hours=3)
                    if (now - msg_dt).total_seconds() > 600:
                        continue
                except:
                    pass
                for kw in LAUNCH_KEYWORDS:
                    if kw in text:
                        warnings.append(f"[{ch}] {text[:150]}")
                        break
        except:
            continue

    for feed in RSS_FEEDS:
        try:
            req = urllib.request.Request(feed, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                text = resp.read().decode("utf-8", errors="ignore")
            titles = re.findall(r'<title>([^<]{10,150})</title>', text)
            for t in titles[1:]:
                for kw in LAUNCH_KEYWORDS:
                    if kw.lower() in t.lower():
                        warnings.append(f"[RSS] {t[:150]}")
                        break
        except:
            continue

    if warnings:
        first = warnings[0]
        if first != state.get("last_warn", ""):
            state["last_warn"] = first
            if any(kw in first for kw in ["תזוזת משגרים", "launcher"]):
                emoji = "📡"
            elif any(kw in first for kw in ["איראן", "IRGC", "חיזבאללה"]):
                emoji = "🔴"
            else:
                emoji = "⚠️"
            tg_send(f"{emoji} {first}")

    state["sent_alerts"] = list(sent)[-300:]
    save_state(state)
    print(f"Done. Warnings found: {len(warnings)}, Sent keys: {len(sent)}")

if __name__ == "__main__":
    main()
