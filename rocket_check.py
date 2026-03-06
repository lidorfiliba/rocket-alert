"""
RocketAlert — GitHub Actions + Termux
"""
import requests, json, urllib.request, re, time, os
from datetime import datetime, timedelta

TG_BOT_TOKEN = "8545041316:AAHYqIskfkcDwgMTw4Qk5tRmQqrNf31BPao"
TG_CHAT_ID   = -1003584552650
STATE_FILE   = "state.json"
HISTORY_WINDOW_SEC = 180

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
UAV_CATS  = {3, 4, 17}

TG_CHANNELS = ["oref_updates", "tzeva_adom_israel", "israelradar", "kann_news", "newsisrael13"]
EARLY_WARNING_KEYWORDS = ["התרעה מקדימה", "זיהוי שיגורים", "שיגורים לעבר", "טיל בליסטי", "מטח רקטות"]

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
        data = {"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"}
        requests.post(url, json=data, timeout=10)
        print(f"TG: {text[:80]}")
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

def threat_name(cat):
    if cat in UAV_CATS: return "כטב״מ"
    if cat == 13: return "טיל בליסטי"
    return "רקטות"

def threat_emoji(cat):
    if cat in UAV_CATS: return "🛸"
    if cat == 13: return "💥"
    return "🚨"

def check(state):
    sent = set(state.get("sent_alerts", []))
    now  = datetime.now()
    session = requests.Session()
    session.headers.update(OREF_HEADERS)

    # 1. אזעקות פעילות
    try:
        r = session.get(OREF_ACTIVE, timeout=5)
        r.encoding = "utf-8-sig"
        raw = safe_json(r.text)
        if raw:
            root_cat = int(raw.get("cat", raw.get("category",0))) if isinstance(raw,dict) else 0
            if root_cat not in END_CATS:
                items = raw if isinstance(raw,list) else raw.get("data",[])
                if isinstance(items, list) and items:
                    new_areas = []
                    for a in items:
                        area = a if isinstance(a,str) else a.get("data","")
                        key  = f"alert:{area}"
                        if area and key not in sent:
                            new_areas.append(area)
                            sent.add(key)
                    if new_areas:
                        emoji = threat_emoji(root_cat)
                        name  = threat_name(root_cat)
                        cities = "\n• ".join([", ".join(new_areas[i:i+8]) for i in range(0, len(new_areas), 8)])
                        extra = f"\n<i>+{len(new_areas)-8} נוספים</i>" if len(new_areas) > 8 else ""
                        tg_send(f"{emoji} <b>אזעקה — {name}</b>\n• {cities}{extra}")
    except Exception as e:
        print(f"active error: {e}")

    # 2. היסטוריה — קיבוץ לפי אזור
    try:
        r2 = session.get(OREF_HISTORY, timeout=10)
        r2.encoding = "utf-8-sig"
        hist = safe_json(r2.text)
        if isinstance(hist, list):
            last_dt_str = state.get("last_alert_dt","")
            last_dt = datetime.strptime(last_dt_str, "%Y-%m-%d %H:%M:%S") if last_dt_str else now - timedelta(seconds=HISTORY_WINDOW_SEC+30)
            by_zone = {}
            newest  = None
            for it in hist:
                if not isinstance(it, dict): continue
                try:
                    idt = datetime.strptime(it.get("alertDate",""), "%Y-%m-%d %H:%M:%S")
                except: continue
                if (now - idt).total_seconds() > HISTORY_WINDOW_SEC: break
                cat = int(it.get("category",0))
                if cat in END_CATS: continue
                if idt <= last_dt: continue
                if newest is None: newest = idt
                zone = it.get("areaname", it.get("area", "כללי"))
                zkey = f"{zone}:{cat}"
                for a in extract_areas(it):
                    key = f"hist:{a}:{idt.strftime('%Y%m%d%H%M')}"
                    if key not in sent:
                        by_zone.setdefault(zkey, {"zone": zone, "cat": cat, "areas": []})
                        by_zone[zkey]["areas"].append(a)
                        sent.add(key)
            if newest:
                state["last_alert_dt"] = newest.strftime("%Y-%m-%d %H:%M:%S")
            for data in by_zone.values():
                areas  = list(set(data["areas"]))
                emoji  = threat_emoji(data["cat"])
                name   = threat_name(data["cat"])
                cities = ", ".join(areas[:20])
                extra  = f" (+{len(areas)-20})" if len(areas) > 20 else ""
                tg_send(f"{emoji} <b>אזעקה — {name}</b>\n📍 {data['zone']}\n{cities}{extra}")
    except Exception as e:
        print(f"history error: {e}")

    # 3. התרעה מקדימה
    for ch in TG_CHANNELS:
        try:
            url = f"https://t.me/s/{ch}"
            req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
            blocks = re.findall(r'<time[^>]*datetime="([^"]+)".*?tgme_widget_message_text[^>]*>(.*?)</div>', html, re.DOTALL)
            for dt_str, msg_html in blocks:
                text = re.sub(r'<[^>]+>', '', msg_html).strip()
                if not text: continue
                try:
                    msg_dt = datetime.fromisoformat(dt_str.replace("Z","+00:00")).replace(tzinfo=None)
                    msg_dt += timedelta(hours=3)
                    if (now - msg_dt).total_seconds() > 600: continue
                except: pass
                for kw in EARLY_WARNING_KEYWORDS:
                    if kw in text:
                        key = f"warn:{text[:60]}"
                        if key not in sent:
                            sent.add(key)
                            tg_send(f"📡 <b>התרעה מקדימה</b>\n{text[:300]}")
                        break
        except: continue

    state["sent_alerts"] = list(sent)[-300:]
    save_state(state)
    print(f"Done {datetime.now().strftime('%H:%M:%S')}")

def main():
    state = load_state()
    is_actions = os.environ.get("GITHUB_ACTIONS") == "true"
    if is_actions:
        check(state)
    else:
        while True:
            check(state)
            time.sleep(3)

if __name__ == "__main__":
    main()
