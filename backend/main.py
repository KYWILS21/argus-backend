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

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel

load_dotenv()

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
ACCESS_TOKEN = os.environ["ACCESS_TOKEN"]  # shared secret your frontend sends
DB_PATH = os.environ.get("ARGUS_DB_PATH", "argus.db")
MODEL = "gemini-flash-latest"  # alias that always points at Google's current
# recommended Flash model, so this won't break again when Google retires a
# specific version (as happened with gemini-2.0-flash in mid-2026)

SYSTEM_PROMPT = """You are ARGUS, a personal assistant to the user.
Be direct, warm, and efficient. Keep responses conversational and concise
unless the user asks for depth. You don't yet have tool access to the
user's calendar, email, or other services -- that's coming in a later
version -- so don't claim to have taken real-world actions."""

client = genai.Client(api_key=GEMINI_API_KEY)
app = FastAPI(title="ARGUS MVP")

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


def to_gemini_contents(history: list[dict]) -> list[types.Content]:
    """Gemini uses 'model' instead of 'assistant' for the AI's turns."""
    contents = []
    for msg in history:
        role = "model" if msg["role"] == "assistant" else "user"
        contents.append(
            types.Content(role=role, parts=[types.Part(text=msg["content"])])
        )
    return contents


def generate_with_retry(contents: list[types.Content], max_attempts: int = 4):
    """Gemini's free tier occasionally returns 503 UNAVAILABLE when the
    model is under heavy demand. This is transient -- retrying after a
    short wait almost always succeeds. We back off 1s, 2s, 4s, 8s."""
    last_error = None
    for attempt in range(max_attempts):
        try:
            return client.models.generate_content(
                model=MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT
                ),
            )
        except genai_errors.ServerError as e:
            last_error = e
            if attempt < max_attempts - 1:
                time.sleep(2**attempt)
    raise last_error


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, authorization: str | None = Header(default=None)):
    check_auth(authorization)

    session_id = req.session_id or str(uuid.uuid4())
    history = load_history(session_id)
    history.append({"role": "user", "content": req.message})

    try:
        response = generate_with_retry(to_gemini_contents(history))
    except genai_errors.ServerError:
        # Gemini is overloaded even after retries -- fail gracefully
        # instead of a raw 500, so the frontend can show something useful.
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