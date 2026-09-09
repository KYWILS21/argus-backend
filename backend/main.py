"""
ARGUS MVP backend.

A minimal, always-on personal assistant backend:
- Talks to Google's Gemini via the free-tier API
- Remembers conversation history per session in SQLite
- Protected by a single shared access token (good enough for a personal MVP;
  swap for real auth later if you invite other users)

Run locally:
    export GEMINI_API_KEY=...
    export ACCESS_TOKEN=choose-a-long-random-string
    uvicorn main:app --host 0.0.0.0 --port 8000

Deploy: push this backend/ folder to Railway, Render, or Fly.io and set the
two env vars above in their dashboard. They'll give you a public HTTPS URL.
"""

import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from pydantic import BaseModel

load_dotenv()

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
ACCESS_TOKEN = os.environ["ACCESS_TOKEN"]  # shared secret your frontend sends
DB_PATH = os.environ.get("ARGUS_DB_PATH", "argus.db")
MODEL = "gemini-2.5-flash"  # Google's explicitly recommended model as of
# our testing (Sept 2026) for accounts that can't use gemini-2.5-flash
# (that one's been closed off to new accounts) and don't want the
# newest gemini-3.8-flash (via the "latest" alias), which had a very
# tight ~20 requests/day free quota in our testing. If this one also
# gets deprecated or rate-limited, check Google AI Studio's model list
# for whatever's currently recommended and swap the string here --
# that's the one line that needs to change.

# Google Calendar credentials (from get_refresh_token.py setup) -- optional,
# calendar features are simply unavailable if these aren't set.
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
GOOGLE_REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN")
CALENDAR_ENABLED = all(
    [GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN]
)
print(f"[ARGUS] Calendar integration enabled: {CALENDAR_ENABLED}")

SYSTEM_PROMPT = """You are ARGUS, a personal assistant to the user.
Be direct, warm, and efficient. Keep responses conversational and concise
unless the user asks for depth. You have access to the user's Google
Calendar (which includes their Canvas assignments and due dates, synced
in as a feed) via the get_upcoming_events tool -- use it whenever they
ask about their schedule, what's due, or upcoming events, rather than
guessing. You don't have other tool access yet (email, other services)
-- don't claim to have taken actions you can't actually perform."""

client = genai.Client(api_key=GEMINI_API_KEY)
app = FastAPI(title="ARGUS MVP")

# Live Gemini Chat sessions, keyed by session_id. Google's SDK recommends
# using its Chat object (rather than manually rebuilding history and
# calling generate_content fresh each time) because it correctly tracks
# internal state like thought_signatures across turns -- exactly the
# thing we were fighting with a manual approach. Trade-off: this lives
# in server memory, so it resets if the server restarts (a redeploy, a
# crash, etc). Our SQLite log below still keeps a permanent human-
# readable history either way, just not fed back into new Gemini calls
# after a restart -- a fresh chat simply starts with no prior context.
CHAT_SESSIONS: dict[str, "genai.chats.Chat"] = {}

# Wide-open CORS is fine for a personal single-user project; tighten this
# (to your actual frontend origin) if you ever expose this more broadly.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at REAL NOT NULL
        )"""
    )
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def load_history(session_id: str, limit: int = 30):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE session_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    return [{"role": r, "content": c} for r, c in reversed(rows)]


def save_message(session_id: str, role: str, content: str):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?)",
            (session_id, role, content, time.time()),
        )


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    reply: str
    session_id: str


def check_auth(authorization: str | None):
    if authorization != f"Bearer {ACCESS_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")


def get_calendar_service():
    creds = Credentials(
        token=None,
        refresh_token=GOOGLE_REFRESH_TOKEN,
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
    )
    return build("calendar", "v3", credentials=creds)


def get_upcoming_events(days_ahead: int, max_results: int) -> str:
    """Get the user's upcoming calendar events and assignment due dates
    (including Canvas, synced in via feed) for the next N days.

    Args:
        days_ahead: How many days ahead to look. Use 7 unless the user
            specifies otherwise.
        max_results: Maximum number of events to return. Use 15 unless
            the user asks for more or fewer.
    """
    if not CALENDAR_ENABLED:
        return "Calendar access isn't configured yet."

    service = get_calendar_service()
    now = datetime.now(timezone.utc)
    time_max = now + timedelta(days=days_ahead)
    config_kwargs = {"system_instruction": SYSTEM_PROMPT}

    if CALENDAR_ENABLED:
      config_kwargs["tools"] = [get_upcoming_events]

    # List all calendars the user has, so Canvas feed entries are included
    # alongside their primary calendar, not just the default one.
    calendar_list = service.calendarList().list().execute()
    all_events = []
    for cal in calendar_list.get("items", []):
        events_result = (
            service.events()
            .list(
                calendarId=cal["id"],
                timeMin=now.isoformat(),
                timeMax=time_max.isoformat(),
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        for event in events_result.get("items", []):
            start = event["start"].get("dateTime", event["start"].get("date"))
            all_events.append(
                f"- {event.get('summary', 'Untitled')} ({start}) "
                f"[{cal.get('summary', 'calendar')}]"
            )

    if not all_events:
        return f"No events found in the next {days_ahead} days."

    all_events.sort()
    return "\n".join(all_events[:max_results])


def get_or_create_chat(session_id: str):
    """Returns the live Gemini Chat session for this conversation,
    creating one if it doesn't exist yet. Using the SDK's Chat object
    (rather than manually rebuilding history each request) is Google's
    recommended pattern for automatic function calling -- it correctly
    threads internal state (like thought_signatures) between turns,
    which a hand-rolled history list doesn't preserve properly."""
    if session_id in CHAT_SESSIONS:
        return CHAT_SESSIONS[session_id]

    config_kwargs = {"system_instruction": SYSTEM_PROMPT}
    if CALENDAR_ENABLED:
        # Passing the Python function directly (not a manual schema)
        # enables "automatic function calling": the SDK builds the tool
        # schema from the docstring/type hints and executes the function
        # itself when Gemini asks for it. Using client.chats.create()
        # (below) rather than a one-off generate_content call lets the
        # SDK correctly track thought_signature state between turns on
        # its own -- no manual thinking-mode workarounds needed.
        config_kwargs["tools"] = [get_upcoming_events]

    chat = client.chats.create(
        model=MODEL,
        config=types.GenerateContentConfig(**config_kwargs),
    )
    CHAT_SESSIONS[session_id] = chat
    return chat


def send_with_retry(chat, message: str, max_attempts: int = 4):
    """Gemini's free tier occasionally returns 503 UNAVAILABLE when the
    model is under heavy demand, and can return 429 RESOURCE_EXHAUSTED if
    you hit its per-minute rate limit. Both are transient -- retrying
    after a short wait almost always succeeds."""
    last_error = None
    for attempt in range(max_attempts):
        try:
            return chat.send_message(message)
        except genai_errors.ServerError as e:
            last_error = e
            if attempt < max_attempts - 1:
                time.sleep(2**attempt)
        except genai_errors.ClientError as e:
            is_rate_limit = getattr(e, "status_code", None) == 429
            if not is_rate_limit:
                raise
            last_error = e
            if attempt < max_attempts - 1:
                # Rate limits need longer waits than server overload does.
                time.sleep(5 * (attempt + 1))
    raise last_error


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, authorization: str | None = Header(default=None)):
    check_auth(authorization)

    session_id = req.session_id or str(uuid.uuid4())
    gemini_chat = get_or_create_chat(session_id)

    try:
        response = send_with_retry(gemini_chat, req.message)
    except (genai_errors.ServerError, genai_errors.ClientError) as e:
        # Gemini is overloaded or rate-limited even after retries -- fail
        # gracefully instead of a raw 500, so the frontend shows
        # something useful. Log the real cause so we can see it locally.
        print(f"[ARGUS] Gemini call failed after retries: {e}")
        raise HTTPException(
            status_code=503,
            detail="Gemini is under heavy load right now. Give it a "
            "moment and try again.",
        )

    reply_text = response.text

    save_message(session_id, "user", req.message)
    save_message(session_id, "assistant", reply_text)

    return ChatResponse(reply=reply_text, session_id=session_id)


@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/health")
def health():
    return {
        "status": "ok",
        "calendar_enabled": CALENDAR_ENABLED
    }