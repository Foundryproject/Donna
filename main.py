import os, json, uuid, sqlite3, datetime as dt
from urllib.parse import urlencode
from dateutil import tz
from apscheduler.schedulers.background import BackgroundScheduler

from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse, PlainTextResponse
from dotenv import load_dotenv
import requests

# ---------- Config ----------
load_dotenv("tokens.env")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "donna_verify")
META_TOKEN = os.getenv("META_ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
BASE_URL = os.getenv("BASE_URL")

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

app = FastAPI()

# ---------- Storage (SQLite) ----------
DB = "donna.db"
conn = sqlite3.connect(DB, check_same_thread=False)
cur = conn.cursor()
cur.execute("""
CREATE TABLE IF NOT EXISTS users (
  wa_number TEXT PRIMARY KEY,
  google_refresh_token TEXT,
  timezone TEXT DEFAULT 'UTC'
)""")
cur.execute("""
CREATE TABLE IF NOT EXISTS reminders (
  id TEXT PRIMARY KEY,
  wa_number TEXT,
  event_id TEXT,
  summary TEXT,
  start_utc TEXT,
  remind_at_utc TEXT
)""")
conn.commit()

# ---------- WhatsApp send ----------
def wa_send(to:str, body:str):
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {META_TOKEN}"}
    data = {
        "messaging_product":"whatsapp",
        "to": to,
        "text":{"body": body}
    }
    print(f"📱 Sending WhatsApp message to {to}: {body[:50]}...")  # Debug log
    r = requests.post(url, headers=headers, json=data, timeout=20)
    print(f"📤 WhatsApp API response: {r.status_code} - {r.text}")  # Debug log
    if r.status_code >= 300:
        print("WA send error:", r.text)

# ---------- Google helpers ----------
def google_refresh_access_token(refresh_token:str):
    data = {
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token"
    }
    r = requests.post(GOOGLE_TOKEN_ENDPOINT, data=data, timeout=20)
    r.raise_for_status()
    return r.json()["access_token"]

def google_exchange_code(code:str, redirect_uri:str):
    data = {
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code"
    }
    r = requests.post(GOOGLE_TOKEN_ENDPOINT, data=data, timeout=20)
    r.raise_for_status()
    j = r.json()
    return j["access_token"], j.get("refresh_token")

def gcal_list_events(access_token:str, time_min_iso:str, time_max_iso:str, tzid:str):
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {
        "singleEvents": "true",
        "orderBy": "startTime",
        "timeMin": time_min_iso,
        "timeMax": time_max_iso,
        "maxResults": 50,
        "timeZone": tzid
    }
    r = requests.get("https://www.googleapis.com/calendar/v3/calendars/primary/events",
                     headers=headers, params=params, timeout=20)
    r.raise_for_status()
    return r.json().get("items", [])

# ---------- Webhook verification ----------
@app.get("/webhook")
def verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    print(f"🔐 Webhook verification: mode={mode}, token={token}, challenge={challenge}")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        print(f"✅ Verification successful, returning challenge: {challenge}")
        return PlainTextResponse(challenge)
    print(f"❌ Verification failed: expected token '{VERIFY_TOKEN}', got '{token}'")
    return Response(status_code=403)

# Test endpoint
@app.get("/test")
def test():
    return {"status": "working", "verify_token": VERIFY_TOKEN}

# ---------- Incoming messages ----------
@app.post("/webhook")
async def webhook(request: Request):
    payload = await request.json()
    print(f"📥 Webhook received: {payload}")  # Debug log
    try:
        change = payload["entry"][0]["changes"][0]["value"]
        if "messages" not in change: 
            return {"ok": True}

        msg = change["messages"][0]
        wa_number = msg["from"]            # e.g., "15551234567"
        text = msg.get("text", {}).get("body", "").strip().lower()
        print(f"🔍 Processing message from {wa_number}: '{text}'")  # Debug log

        if text in ("link", "link calendar", "connect calendar", "auth"):
            print(f"🔗 Building auth link for {wa_number}")  # Debug log
            url = build_google_auth_link(wa_number)
            print(f"📤 Sending auth URL: {url}")  # Debug log
            wa_send(wa_number,
                "To link your Google Calendar, tap this:\n" + url +
                "\n\n(If asked, allow 'Calendar read-only')")
            return {"ok": True}

        if text in ("today", "agenda", "meetings", "today?"):
            tzid = ensure_timezone(wa_number)
            resp = get_agenda_for_day(wa_number, dt.date.today(), tzid)
            wa_send(wa_number, resp)
            return {"ok": True}

        if text in ("tomorrow", "tmrw", "tomorrow?"):
            tzid = ensure_timezone(wa_number)
            resp = get_agenda_for_day(wa_number, dt.date.today()+dt.timedelta(days=1), tzid)
            wa_send(wa_number, resp)
            return {"ok": True}

        if text.startswith("timezone "):
            tzid = text.split(" ",1)[1].strip()
            cur.execute("UPDATE users SET timezone=? WHERE wa_number=?", (tzid, wa_number))
            conn.commit()
            wa_send(wa_number, f"Timezone set to {tzid}.")
            return {"ok": True}

        if text in ("remind", "remind me", "enable reminders"):
            tzid = ensure_timezone(wa_number)
            created = create_reminders_for_today(wa_number, tzid, minutes_before=10)
            wa_send(wa_number, f"Got it. I'll remind you 10 minutes before {created} meeting(s) today.")
            return {"ok": True}

        if text in ("test", "hello", "hi"):
            wa_send(wa_number, f"Hi there! 👋 I'm Donna, your calendar assistant. You said: '{text}'\n\nCommands:\n- link calendar\n- today / tomorrow\n- remind (10m before)\n- timezone America/New_York")
            return {"ok": True}

        # help - default response
        wa_send(wa_number,
          "Donna here 💚\nCommands:\n- link calendar\n- today / tomorrow\n- remind (10m before)\n- timezone America/New_York\n\nSend 'test' to verify I'm working!")
    except Exception as e:
        print("Webhook error:", e)
    return {"ok": True}

# ---------- OAuth: start + callback ----------
def build_google_auth_link(wa_number:str)->str:
    # store state so we know which WhatsApp user this is
    state = uuid.uuid4().hex + ":" + wa_number
    redirect_uri = f"{BASE_URL}/auth/callback"
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": GOOGLE_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state
    }
    return f"{GOOGLE_AUTH_ENDPOINT}?{urlencode(params)}"

@app.get("/auth/callback")
def auth_callback(code: str, state: str):
    # recover WhatsApp number from state
    _, wa_number = state.split(":", 1)
    redirect_uri = f"{BASE_URL}/auth/callback"

    access_token, refresh_token = google_exchange_code(code, redirect_uri)
    if refresh_token:
        # upsert user
        cur.execute("INSERT OR REPLACE INTO users (wa_number, google_refresh_token) VALUES (?,?)",
                    (wa_number, refresh_token))
        conn.commit()
        # quick welcome ping
        wa_send(wa_number, "✅ Calendar linked! Send 'today' to see your agenda, or 'remind' to get pings 10m before each meeting.")
    else:
        wa_send(wa_number, "Linked, but Google did not return a refresh token. Send 'link calendar' again and accept permissions.")
    # simple browser response
    return PlainTextResponse("You can close this tab and return to WhatsApp ✅")

# ---------- Agenda / Reminders ----------
def ensure_timezone(wa_number:str)->str:
    cur.execute("SELECT timezone FROM users WHERE wa_number=?", (wa_number,))
    row = cur.fetchone()
    if row and row[0]: return row[0]
    # default to user’s local US/Eastern if unknown; customize as needed
    tzid = "America/New_York"
    cur.execute("INSERT OR IGNORE INTO users (wa_number, timezone) VALUES (?,?)", (wa_number, tzid))
    conn.commit()
    return tzid

def get_agenda_for_day(wa_number:str, day:dt.date, tzid:str)->str:
    cur.execute("SELECT google_refresh_token FROM users WHERE wa_number=?", (wa_number,))
    row = cur.fetchone()
    if not row or not row[0]:
        return "Your calendar isn’t linked yet. Send 'link calendar' to connect."

    refresh = row[0]
    access = google_refresh_access_token(refresh)

    tzinfo = tz.gettz(tzid)
    start_local = dt.datetime.combine(day, dt.time.min).replace(tzinfo=tzinfo)
    end_local   = dt.datetime.combine(day, dt.time.max).replace(tzinfo=tzinfo)
    timeMin = start_local.isoformat()
    timeMax = end_local.isoformat()

    items = gcal_list_events(access, timeMin, timeMax, tzid)

    if not items:
        return f"No events on {day.strftime('%a %b %d')}."

    lines = [f"Agenda for {day.strftime('%a %b %d')}:"]
    for ev in items:
        # handle all-day vs timed
        start = ev.get("start", {})
        if "dateTime" in start:
            dt_local = dt.datetime.fromisoformat(start["dateTime"].replace("Z","+00:00")).astimezone(tzinfo)
            t = dt_local.strftime("%-I:%M %p")
        else:
            t = "All day"
        lines.append(f"• {t} — {ev.get('summary','(no title)')}")
    return "\n".join(lines)

def create_reminders_for_today(wa_number:str, tzid:str, minutes_before:int=10)->int:
    cur.execute("SELECT google_refresh_token FROM users WHERE wa_number=?", (wa_number,))
    row = cur.fetchone()
    if not row or not row[0]:
        return 0
    access = google_refresh_access_token(row[0])

    tzinfo = tz.gettz(tzid)
    today = dt.date.today()
    start_local = dt.datetime.combine(today, dt.time.min).replace(tzinfo=tzinfo)
    end_local   = dt.datetime.combine(today, dt.time.max).replace(tzinfo=tzinfo)
    items = gcal_list_events(access, start_local.isoformat(), end_local.isoformat(), tzid)

    created = 0
    for ev in items:
        if "dateTime" not in ev.get("start", {}):  # skip all-day
            start_dt = dt.datetime.fromisoformat(ev["start"]["dateTime"].replace("Z","+00:00"))
            remind_at = (start_dt - dt.timedelta(minutes=minutes_before)).astimezone(tz.UTC)
            rid = uuid.uuid4().hex
            cur.execute("""
              INSERT OR REPLACE INTO reminders (id, wa_number, event_id, summary, start_utc, remind_at_utc)
              VALUES (?,?,?,?,?,?)
            """, (rid, wa_number, ev["id"], ev.get("summary","(no title)"),
                  start_dt.astimezone(tz.UTC).isoformat(), remind_at.isoformat()))
            created += 1
    conn.commit()
    return created

# ---------- Scheduler to send reminders ----------
def reminder_tick():
    now = dt.datetime.now(tz.UTC)
    cur.execute("""
        SELECT id, wa_number, summary, start_utc FROM reminders
        WHERE remind_at_utc <= ? 
    """, (now.isoformat(),))
    rows = cur.fetchall()
    for rid, wa_number, summary, start_utc in rows:
        start_local = dt.datetime.fromisoformat(start_utc).astimezone(tz.gettz(ensure_timezone(wa_number)))
        msg = f"⏰ Reminder: '{summary}' at {start_local.strftime('%-I:%M %p')}."
        wa_send(wa_number, msg)
        cur.execute("DELETE FROM reminders WHERE id=?", (rid,))
    conn.commit()

scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(reminder_tick, "interval", seconds=30)
scheduler.start()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="info")
