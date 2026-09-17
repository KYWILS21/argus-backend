import os
import json
import sqlite3
import datetime
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Depends, Header, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google import genai
from google.genai import types

# Optional Google Calendar imports
try:
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    CALENDAR_AVAILABLE = True
except ImportError:
    CALENDAR_AVAILABLE = False

# ==============================================================================
# Configuration & Initialization
# ==============================================================================

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ARGUS_BEARER_TOKEN = os.getenv("ARGUS_BEARER_TOKEN", "default_secret_token")
GOOGLE_CALENDAR_TOKEN = os.getenv("GOOGLE_CALENDAR_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL", "argus.db")

client = genai.Client(api_key=GEMINI_API_KEY)

app = FastAPI(title="ARGUS API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# ==============================================================================
# Security / Auth
# ==============================================================================

def verify_token(authorization: Optional[str] = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header missing.")
    
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid Authorization header format. Expected 'Bearer <token>'.")
    
    token = parts[1]
    if token != ARGUS_BEARER_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid or unauthorized token.")
    
    return token

# ==============================================================================
# Database & Memory
# ==============================================================================

def init_db():
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

init_db()

def save_message(session_id: str, role: str, content: str):
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
        (session_id, role, content)
    )
    conn.commit()
    conn.close()

def get_history(session_id: str, limit: int = 15) -> List[Dict[str, str]]:
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
        (session_id, limit)
    )
    rows = cursor.fetchall()
    conn.close()
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

# ==============================================================================
# Google Calendar Tools
# ==============================================================================

def get_calendar_service():
    if not CALENDAR_AVAILABLE or not GOOGLE_CALENDAR_TOKEN:
        return None
    try:
        creds_info = json.loads(GOOGLE_CALENDAR_TOKEN)
        creds = Credentials.from_authorized_user_info(creds_info)
        return build("calendar", "v3", credentials=creds)
    except Exception as e:
        print(f"[Calendar Auth Error] {e}")
        return None

def list_calendar_events(time_min_iso: Optional[str] = None, max_results: int = 10) -> str:
    """Lists upcoming events from the user's primary Google Calendar."""
    service = get_calendar_service()
    if not service:
        return "Google Calendar integration is not active or token is invalid."
    try:
        now = time_min_iso or datetime.datetime.now(datetime.timezone.utc).isoformat()
        events_result = service.events().list(
            calendarId='primary',
            timeMin=now,
            maxResults=max_results,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])
        if not events:
            return "No upcoming events found."
        
        result = []
        for e in events:
            start = e.get('start', {}).get('dateTime', e.get('start', {}).get('date'))
            summary = e.get('summary', 'Untitled Event')
            eid = e.get('id')
            result.append(f"- {summary} (Starts: {start}, ID: {eid})")
        return "\n".join(result)
    except Exception as err:
        return f"Error fetching events: {str(err)}"

def create_calendar_event(summary: str, start_time_iso: str, end_time_iso: str, description: Optional[str] = "") -> str:
    """Creates a new event on the user's primary Google Calendar."""
    service = get_calendar_service()
    if not service:
        return "Google Calendar integration is not active or token is invalid."
    try:
        event = {
            'summary': summary,
            'description': description,
            'start': {'dateTime': start_time_iso},
            'end': {'dateTime': end_time_iso},
        }
        created = service.events().insert(calendarId='primary', body=event).execute()
        return f"Event created: '{created.get('summary')}' (ID: {created.get('id')})"
    except Exception as err:
        return f"Error creating event: {str(err)}"

def update_calendar_event(event_id: str, summary: Optional[str] = None, start_time_iso: Optional[str] = None, end_time_iso: Optional[str] = None) -> str:
    """Updates an existing event on the primary Google Calendar."""
    service = get_calendar_service()
    if not service:
        return "Google Calendar integration is not active or token is invalid."
    try:
        event = service.events().get(calendarId='primary', eventId=event_id).execute()
        if summary:
            event['summary'] = summary
        if start_time_iso:
            event['start'] = {'dateTime': start_time_iso}
        if end_time_iso:
            event['end'] = {'dateTime': end_time_iso}
        updated = service.events().update(calendarId='primary', eventId=event_id, body=event).execute()
        return f"Event updated: '{updated.get('summary')}'"
    except Exception as err:
        return f"Error updating event: {str(err)}"

def delete_calendar_event(event_id: str) -> str:
    """Deletes an event from the user's primary Google Calendar."""
    service = get_calendar_service()
    if not service:
        return "Google Calendar integration is not active or token is invalid."
    try:
        service.events().delete(calendarId='primary', eventId=event_id).execute()
        return f"Event {event_id} deleted successfully."
    except Exception as err:
        return f"Error deleting event: {str(err)}"

# Tool configurations: Native Google Search + Calendar Callables
tools_config = [
    {"google_search": {}},
    list_calendar_events,
    create_calendar_event,
    update_calendar_event,
    delete_calendar_event
]

# ==============================================================================
# Pydantic Schemas
# ==============================================================================

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None

class ChatResponse(BaseModel):
    reply: str
    session_id: str

# ==============================================================================
# Endpoints
# ==============================================================================

@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "ARGUS"}

@app.get("/history/{session_id}")
def get_session_history(session_id: str, token: str = Depends(verify_token)):
    return {"history": get_history(session_id, limit=30)}

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

        raw_mime = file.content_type or "audio/mp4"
        clean_mime = raw_mime.split(";")[0].strip().lower()
        if clean_mime in ["application/octet-stream", ""]:
            clean_mime = "audio/mp4"

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                types.Part.from_bytes(data=audio_bytes, mime_type=clean_mime),
                "Transcribe this speech verbatim. Output strictly the plain text transcription, with no conversational filler or commentary."
            ]
        )

        transcription = response.text.strip() if response.text else ""
        return {"text": transcription}

    except HTTPException:
        raise
    except Exception as e:
        print(f"[ARGUS Transcribe Exception] {repr(e)}")
        raise HTTPException(status_code=500, detail=f"Audio transcription error: {str(e)}")

search_tool = types.Tool(google_search=types.GoogleSearch())

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest, token: str = Depends(verify_token)):
    session_id = request.session_id or str(datetime.datetime.now().timestamp())
    save_message(session_id, "user", request.message)

    history_records = get_history(session_id, limit=10)
    chat_contents = []
    for r in history_records:
        role_label = "user" if r["role"] == "user" else "model"
        chat_contents.append(types.Content(
            role=role_label,
            parts=[types.Part.from_text(text=r["content"])]
        ))

    system_instruction = (
        "You are ARGUS, an efficient personal AI executive assistant. "
        "Use Google Search for current events, news, or factual lookups. "
        "Keep responses direct and concise."
    )

    try:
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=chat_contents,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                tools=[search_tool],
                temperature=0.7
            )
        )

        reply_text = response.text or "Action completed."
        save_message(session_id, "assistant", reply_text)
        return ChatResponse(reply=reply_text, session_id=session_id)

    except Exception as e:
        print(f"[ARGUS Error] /chat failed: {repr(e)}")
        # Return a clean JSON error with CORS headers instead of crashing
        raise HTTPException(status_code=500, detail=str(e))