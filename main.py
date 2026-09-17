import os
import sqlite3
import datetime
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Header, Depends, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google import genai
from google.genai import types
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

app = FastAPI(title="ARGUS Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

ACCESS_TOKEN = os.getenv("ACCESS_TOKEN", "your_secret_access_token")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")

# SQLite persistence directory on Railway persistent volume or local fallback
DB_DIR = "/data" if os.path.exists("/data") else "."
DB_PATH = os.path.join(DB_DIR, "argus.db")

client = genai.Client(api_key=GEMINI_API_KEY)

# --- Database Setup ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

init_db()

def save_message(session_id: str, role: str, content: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
        (session_id, role, content)
    )
    conn.commit()
    conn.close()

def get_history(session_id: str, limit: int = 20) -> List[dict]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id ASC LIMIT ?",
        (session_id, limit)
    )
    rows = cur.fetchall()
    conn.close()
    return [{"role": r[0], "content": r[1]} for r in rows]

# --- Google Calendar Helpers & Tools ---
def get_calendar_service():
    creds = Credentials(
        None,
        refresh_token=GOOGLE_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=["https://www.googleapis.com/auth/calendar"]
    )
    return build("calendar", "v3", credentials=creds)

def list_calendar_events(time_min: Optional[str] = None, max_results: int = 10):
    """Lists upcoming events from the primary calendar."""
    service = get_calendar_service()
    if not time_min:
        time_min = datetime.datetime.now(datetime.timezone.utc).isoformat()
    events_result = service.events().list(
        calendarId="primary",
        timeMin=time_min,
        maxResults=max_results,
        singleEvents=True,
        orderBy="startTime"
    ).execute()
    return events_result.get("items", [])

def create_calendar_event(summary: str, start_time: str, end_time: str, description: Optional[str] = None):
    """Creates an event on the primary calendar. Times must be ISO 8601 formatted strings."""
    service = get_calendar_service()
    body = {
        "summary": summary,
        "description": description or "",
        "start": {"dateTime": start_time},
        "end": {"dateTime": end_time},
    }
    return service.events().insert(calendarId="primary", body=body).execute()

def update_calendar_event(
    event_id: str,
    summary: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    description: Optional[str] = None,
    location: Optional[str] = None,
):
    """Updates an existing event on the primary calendar using partial update semantics."""
    service = get_calendar_service()
    patch_body = {}
    if summary is not None:
        patch_body["summary"] = summary
    if description is not None:
        patch_body["description"] = description
    if location is not None:
        patch_body["location"] = location
    if start_time is not None:
        patch_body["start"] = {"dateTime": start_time}
    if end_time is not None:
        patch_body["end"] = {"dateTime": end_time}

    if not patch_body:
        return {"status": "no_changes_requested", "event_id": event_id}

    updated_event = service.events().patch(
        calendarId="primary",
        eventId=event_id,
        body=patch_body
    ).execute()

    return {
        "status": "updated",
        "id": updated_event.get("id"),
        "summary": updated_event.get("summary"),
        "start": updated_event.get("start", {}).get("dateTime"),
        "end": updated_event.get("end", {}).get("dateTime"),
    }

def delete_calendar_event(event_id: str):
    """Deletes an event from the primary calendar given its event_id."""
    service = get_calendar_service()
    service.events().delete(calendarId="primary", eventId=event_id).execute()
    return {"status": "deleted", "event_id": event_id}

calendar_tools = [
    list_calendar_events,
    create_calendar_event,
    update_calendar_event,
    delete_calendar_event
]

# --- Auth Dependency ---
def verify_token(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token format")
    token = authorization.split(" ")[1]
    if token != ACCESS_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return token

# --- Request / Response Models ---
class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None

class ChatResponse(BaseModel):
    reply: str
    session_id: str

# --- Endpoints ---

@app.get("/history/{session_id}")
async def get_chat_history(session_id: str, token: str = Depends(verify_token)):
    messages = get_history(session_id)
    return {"messages": messages}

@app.post("/transcribe")
@app.post("/transcribe/")
async def transcribe_audio(
    file: UploadFile = File(...),
    token: str = Depends(verify_token)
):
    try:
        audio_bytes = await file.read()

        if not audio_bytes or len(audio_bytes) < 100:
            raise HTTPException(
                status_code=400,
                detail=f"Audio payload too small ({len(audio_bytes) if audio_bytes else 0} bytes)."
            )

        # Clean MIME type (Safari often appends codec details like 'audio/mp4;codecs=opus')
        raw_mime = file.content_type or "audio/mp4"
        clean_mime = raw_mime.split(";")[0].strip().lower()

        # Fallback to audio/mp4 if octet-stream or unrecognized
        if clean_mime in ["application/octet-stream", ""]:
            clean_mime = "audio/mp4"

        print(f"[ARGUS Transcribe] Received {len(audio_bytes)} bytes, detected MIME: {clean_mime}")

        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=[
                types.Part.from_bytes(
                    data=audio_bytes,
                    mime_type=clean_mime
                ),
                "Transcribe this speech verbatim. Output strictly the plain text transcription, with no conversational filler or commentary."
            ]
        )

        transcription = response.text.strip() if response.text else ""
        print(f"[ARGUS Transcribe] Result: '{transcription}'")
        return {"text": transcription}

    except HTTPException:
        raise
    except Exception as e:
        print(f"[ARGUS Transcribe Exception] Type: {type(e).__name__}, Message: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Audio transcription error: {str(e)}")



@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest, token: str = Depends(verify_token)):
    session_id = request.session_id or str(datetime.datetime.now().timestamp())

    save_message(session_id, "user", request.message)

    history_records = get_history(session_id, limit=15)

    chat_contents = []
    for r in history_records:
        role_label = "user" if r["role"] == "user" else "model"
        chat_contents.append(types.Content(
            role=role_label,
            parts=[types.Part.from_text(text=r["content"])]
        ))

    system_instruction = (
        "You are ARGUS, an efficient, personal AI executive assistant. "
        "You have full access to manage the user's Google Calendar via tools. "
        "Keep responses concise, clear, and direct. When managing events, confirm actions cleanly."
    )

    try:
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=chat_contents,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                tools=calendar_tools,
                temperature=0.7
            )
        )

        reply_text = response.text or "Action processed."
        save_message(session_id, "assistant", reply_text)
        return ChatResponse(reply=reply_text, session_id=session_id)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Inference error: {str(e)}")