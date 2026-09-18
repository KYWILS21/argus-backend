import os
import json
import sqlite3
import datetime
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Depends, Header, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from groq import Groq
from duckduckgo_search import DDGS

# Optional Google Calendar dependencies
try:
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    CALENDAR_AVAILABLE = True
except ImportError:
    CALENDAR_AVAILABLE = False

# ==============================================================================
# Configuration & Initialization
# ==============================================================================

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
ARGUS_BEARER_TOKEN = os.getenv("ARGUS_BEARER_TOKEN", "default_secret_token")
GOOGLE_CALENDAR_TOKEN = os.getenv("GOOGLE_CALENDAR_TOKEN")
DATABASE_URL = os.getenv("ARGUS_DB_PATH") or os.getenv("DATABASE_URL") or "argus.db"

groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

app = FastAPI(title="ARGUS API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

@app.options("/{full_path:path}")
async def preflight_handler(full_path: str):
    return {"status": "ok"}

# ==============================================================================
# Security / Auth
# ==============================================================================

def verify_token(authorization: Optional[str] = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header missing.")
    
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=401, 
            detail="Invalid Authorization header format. Expected 'Bearer <token>'."
        )
    
    token = parts[1]
    if token != ARGUS_BEARER_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid or unauthorized token.")
    
    return token

# ==============================================================================
# Database & Conversation Memory (Short-Term + Long-Term)
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
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fact TEXT NOT NULL,
            category TEXT DEFAULT 'general',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

init_db()

def save_message(session_id: str, role: str, content: str):
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    cursor.execute(
        "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (session_id, role, content, now_iso)
    )
    conn.commit()
    conn.close()

def get_history(session_id: str, limit: int = 20) -> List[Dict[str, str]]:
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
        (session_id, limit)
    )
    rows = cursor.fetchall()
    conn.close()
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

def save_fact_tool(fact: str, category: Optional[str] = "general") -> str:
    """Saves a permanent fact or user detail into long-term memory."""
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    cursor.execute(
        "INSERT INTO user_memories (fact, category, created_at) VALUES (?, ?, ?)",
        (fact, category or "general", now_iso)
    )
    conn.commit()
    conn.close()
    return f"Successfully committed to permanent memory: '{fact}'"

def list_facts() -> List[str]:
    """Retrieves all saved long-term memories."""
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    cursor.execute("SELECT fact FROM user_memories ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows]

# ==============================================================================
# Live Web Search Tool
# ==============================================================================

def web_search_tool(query: str, max_results: int = 5) -> str:
    """Queries DuckDuckGo for live internet information, documentation, news, or general search."""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
            if not results:
                return f"No live search results found for query: '{query}'"
            
            output = []
            for r in results:
                title = r.get("title", "Untitled")
                body = r.get("body", "No description provided.")
                href = r.get("href", "")
                output.append(f"• {title}\n  Summary: {body}\n  Source: {href}")
            return "\n\n".join(output)
    except Exception as e:
        return f"Web search execution error: {str(e)}"

# ==============================================================================
# Google Calendar Tools Suite (Multi-Calendar & Canvas Support)
# ==============================================================================

def get_calendar_service():
    if not CALENDAR_AVAILABLE:
        return None

    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    refresh_token = os.getenv("GOOGLE_REFRESH_TOKEN")
    access_token = os.getenv("ACCESS_TOKEN")

    creds_info = None
    if client_id and client_secret and refresh_token:
        creds_info = {
            "token": access_token,
            "refresh_token": refresh_token,
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": client_id,
            "client_secret": client_secret,
            "scopes": ["https://www.googleapis.com/auth/calendar"]
        }
    elif GOOGLE_CALENDAR_TOKEN:
        try:
            creds_info = json.loads(GOOGLE_CALENDAR_TOKEN)
        except Exception as e:
            print(f"[Calendar JSON Parse Error] {e}")
            return None

    if not creds_info:
        return None

    try:
        creds = Credentials.from_authorized_user_info(creds_info)
        return build("calendar", "v3", credentials=creds)
    except Exception as e:
        print(f"[Calendar Auth Error] {e}")
        return None

def list_calendar_events(time_min_iso: Optional[str] = None, max_results: int = 15) -> str:
    service = get_calendar_service()
    if not service:
        return "Google Calendar integration is not active or credentials are missing."

    try:
        now = time_min_iso or datetime.datetime.now(datetime.timezone.utc).isoformat()
        calendar_list = service.calendarList().list().execute().get('items', [])
        if not calendar_list:
            calendar_list = [{'id': 'primary', 'summary': 'Primary'}]

        all_events = []
        for cal in calendar_list:
            cal_id = cal.get('id')
            cal_name = cal.get('summary', 'Calendar')

            try:
                events_result = service.events().list(
                    calendarId=cal_id,
                    timeMin=now,
                    maxResults=max_results,
                    singleEvents=True,
                    orderBy='startTime'
                ).execute()

                for item in events_result.get('items', []):
                    start_val = item.get('start', {}).get('dateTime', item.get('start', {}).get('date'))
                    all_events.append({
                        'calendar': cal_name,
                        'summary': item.get('summary', 'Untitled Event'),
                        'start': start_val,
                        'id': item.get('id')
                    })
            except Exception as cal_err:
                print(f"[Calendar Read Skip] Could not read {cal_name}: {cal_err}")
                continue

        if not all_events:
            return "No upcoming events found on connected calendars."

        all_events.sort(key=lambda x: x['start'] if x['start'] else "")

        result = []
        for e in all_events[:max_results]:
            result.append(f"- [{e['calendar']}] {e['summary']} (Starts: {e['start']}, ID: {e['id']})")
        
        return "\n".join(result)

    except Exception as err:
        return f"Error fetching multi-calendar events: {str(err)}"

def create_calendar_event(summary: str, start_time_iso: str, end_time_iso: str, description: Optional[str] = "") -> str:
    service = get_calendar_service()
    if not service:
        return "Google Calendar integration is not active or credentials are missing."
    try:
        event = {
            'summary': summary,
            'description': description,
            'start': {'dateTime': start_time_iso},
            'end': {'dateTime': end_time_iso},
        }
        created = service.events().insert(calendarId='primary', body=event).execute()
        return f"Event created successfully: '{created.get('summary')}' (ID: {created.get('id')})"
    except Exception as err:
        return f"Error creating event: {str(err)}"

def update_calendar_event(event_id: str, summary: Optional[str] = None, start_time_iso: Optional[str] = None, end_time_iso: Optional[str] = None) -> str:
    service = get_calendar_service()
    if not service:
        return "Google Calendar integration is not active or credentials are missing."
    try:
        event = service.events().get(calendarId='primary', eventId=event_id).execute()
        if summary:
            event['summary'] = summary
        if start_time_iso:
            event['start'] = {'dateTime': start_time_iso}
        if end_time_iso:
            event['end'] = {'dateTime': end_time_iso}
        updated = service.events().update(calendarId='primary', eventId=event_id, body=event).execute()
        return f"Event updated successfully: '{updated.get('summary')}'"
    except Exception as err:
        return f"Error updating event: {str(err)}"

def delete_calendar_event(event_id: str) -> str:
    service = get_calendar_service()
    if not service:
        return "Google Calendar integration is not active or credentials are missing."
    try:
        service.events().delete(calendarId='primary', eventId=event_id).execute()
        return f"Event {event_id} deleted successfully."
    except Exception as err:
        return f"Error deleting event: {str(err)}"

# ==============================================================================
# Tool Declarations & Dispatch
# ==============================================================================

tools_schema = [
    {
        "type": "function",
        "function": {
            "name": "web_search_tool",
            "description": "Search the live web for current events, external documentation, weather, news, or general real-time lookups.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query."}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_fact_tool",
            "description": "Permanently save a user fact, preference, rule, or personal detail into long-term memory (e.g., name, favorite algorithm, schedule preference).",
            "parameters": {
                "type": "object",
                "properties": {
                    "fact": {"type": "string", "description": "The exact factual information or preference to remember permanently."},
                    "category": {"type": "string", "description": "Optional category (e.g. 'identity', 'preference', 'project')."}
                },
                "required": ["fact"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_calendar_events",
            "description": "Lists upcoming events across all calendars including Canvas feeds, school schedules, and primary calendar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "time_min_iso": {"type": "string", "description": "ISO timestamp to list events from."},
                    "max_results": {"type": "integer", "description": "Maximum number of events to return."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_calendar_event",
            "description": "Creates a new event on the user's primary calendar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Title of the event."},
                    "start_time_iso": {"type": "string", "description": "Start ISO datetime string."},
                    "end_time_iso": {"type": "string", "description": "End ISO datetime string."},
                    "description": {"type": "string", "description": "Optional description."}
                },
                "required": ["summary", "start_time_iso", "end_time_iso"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_calendar_event",
            "description": "Updates an existing event on the primary Google Calendar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "Event ID to update."},
                    "summary": {"type": "string"},
                    "start_time_iso": {"type": "string"},
                    "end_time_iso": {"type": "string"}
                },
                "required": ["event_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_calendar_event",
            "description": "Deletes an event from the user's primary Google Calendar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "Event ID to delete."}
                },
                "required": ["event_id"]
            }
        }
    }
]

tool_dispatch = {
    "web_search_tool": web_search_tool,
    "save_fact_tool": save_fact_tool,
    "list_calendar_events": list_calendar_events,
    "create_calendar_event": create_calendar_event,
    "update_calendar_event": update_calendar_event,
    "delete_calendar_event": delete_calendar_event
}

# ==============================================================================
# Pydantic Models & Endpoints
# ==============================================================================

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None

class ChatResponse(BaseModel):
    reply: str
    session_id: str

@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "ARGUS"}

@app.get("/history/{session_id}")
def get_session_history(session_id: str, token: str = Depends(verify_token)):
    return {"history": get_history(session_id, limit=30)}

@app.get("/memories")
def get_all_memories(token: str = Depends(verify_token)):
    return {"memories": list_facts()}

@app.post("/transcribe")
@app.post("/transcribe/")
async def transcribe_audio(
    file: UploadFile = File(...),
    token: str = Depends(verify_token)
):
    if not groq_client:
        raise HTTPException(status_code=500, detail="GROQ_API_KEY is not configured in environment.")

    try:
        audio_bytes = await file.read()
        if not audio_bytes or len(audio_bytes) < 100:
            raise HTTPException(
                status_code=400,
                detail=f"Audio payload too small ({len(audio_bytes) if audio_bytes else 0} bytes)."
            )

        filename = file.filename or "recording.m4a"
        transcription = groq_client.audio.transcriptions.create(
            file=(filename, audio_bytes),
            model="whisper-large-v3",
            response_format="text"
        )
        return {"text": str(transcription).strip()}

    except HTTPException:
        raise
    except Exception as e:
        print(f"[ARGUS Transcribe Error] {repr(e)}")
        raise HTTPException(status_code=500, detail=f"Audio transcription error: {str(e)}")

@app.post("/chat", response_model=ChatResponse)
@app.post("/chat/", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest, token: str = Depends(verify_token)):
    if not groq_client:
        raise HTTPException(status_code=500, detail="GROQ_API_KEY is not configured in environment.")

    session_id = request.session_id or str(datetime.datetime.now().timestamp())
    
    # 1. Fetch persistent long-term memories
    facts = list_facts()
    facts_block = "\n".join([f"- {f}" for f in facts]) if facts else "No permanent facts recorded yet."

    # 2. Fetch session history
    history_records = get_history(session_id, limit=20)
    
    system_prompt = (
        "You are ARGUS, an efficient personal AI executive assistant.\n"
        "You have access to persistent SQLite memory, Google Calendar tools, and real-time Web Search.\n\n"
        "PERMANENT USER FACTS STORED IN MEMORY:\n"
        f"{facts_block}\n\n"
        "RULES FOR TOOLS & MEMORY:\n"
        "1. Whenever the user shares a personal fact, preference, rule, identity detail, or asks you to remember something, call save_fact_tool.\n"
        "2. When asked about current news, external websites, weather, recent developments, or questions requiring live information, call web_search_tool.\n"
        "3. When asked about upcoming events, classes, or assignments, call list_calendar_events.\n"
        "4. Use the PERMANENT USER FACTS listed above to address the user accurately.\n"
        "5. Keep responses professional, direct, and concise."
    )

    messages = [{"role": "system", "content": system_prompt}]
    
    for r in history_records:
        role_label = "user" if r["role"] == "user" else "assistant"
        messages.append({"role": role_label, "content": r["content"]})

    messages.append({"role": "user", "content": request.message})
    save_message(session_id, "user", request.message)

    try:
        response = groq_client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=messages,
            tools=tools_schema,
            tool_choice="auto",
            temperature=0.7
        )

        response_msg = response.choices[0].message
        
        # Tool execution loop
        if response_msg.tool_calls:
            messages.append({
                "role": "assistant",
                "content": response_msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments
                        }
                    }
                    for tc in response_msg.tool_calls
                ]
            })

            for tool_call in response_msg.tool_calls:
                fn_name = tool_call.function.name
                fn_args = json.loads(tool_call.function.arguments) if tool_call.function.arguments else {}
                
                if fn_name in tool_dispatch:
                    fn_result = tool_dispatch[fn_name](**fn_args)
                else:
                    fn_result = f"Error: Function {fn_name} not found."

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": str(fn_result)
                })

            second_response = groq_client.chat.completions.create(
                model="openai/gpt-oss-20b",
                messages=messages,
                tools=tools_schema,
                temperature=0.7
            )
            reply_text = second_response.choices[0].message.content or "Action processed."
        else:
            reply_text = response_msg.content or "Action processed."

    except Exception as e:
        print(f"[ARGUS Groq Error] {repr(e)}")
        reply_text = f"ARGUS backend error: {str(e)}"

    save_message(session_id, "assistant", reply_text)
    return ChatResponse(reply=reply_text, session_id=session_id)