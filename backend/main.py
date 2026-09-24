import os
import json
import base64
import sqlite3
import datetime
import urllib.request
import urllib.error
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Depends, Header, UploadFile, File, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from groq import Groq

try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

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

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_DEFAULT_OWNER = os.getenv("GITHUB_DEFAULT_OWNER", "KYWILS21")

groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

app = FastAPI(title="ARGUS API", version="2.3.1")

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

@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)

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
    
    token = parts[1].strip().strip('"').strip("'")
    expected_token = ARGUS_BEARER_TOKEN.strip().strip('"').strip("'")

    if token != expected_token:
        raise HTTPException(status_code=403, detail="Invalid or unauthorized token.")
    
    return token

# ==============================================================================
# Database & Memory Persistence
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
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    cursor.execute(
        "INSERT INTO user_memories (fact, category, created_at) VALUES (?, ?, ?)",
        (fact, category or "general", now_iso)
    )
    conn.commit()
    conn.close()
    return f"Successfully saved to permanent memory: '{fact}'"

def list_facts() -> List[str]:
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    cursor.execute("SELECT fact FROM user_memories ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows]

def get_all_memories_records() -> List[Dict[str, Any]]:
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    cursor.execute("SELECT id, fact, category, created_at FROM user_memories ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    return [
        {"id": r[0], "fact": r[1], "category": r[2], "created_at": r[3]}
        for r in rows
    ]

def delete_memory_by_id(memory_id: int) -> bool:
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM user_memories WHERE id = ?", (memory_id,))
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected > 0

# ==============================================================================
# GitHub Integration Tools
# ==============================================================================

def _github_api_request(endpoint: str, method: str = "GET", payload: Optional[Dict[str, Any]] = None) -> Any:
    if not GITHUB_TOKEN:
        raise ValueError("GITHUB_TOKEN is not configured in Railway environment variables.")

    url = f"https://api.github.com{endpoint}"
    data_bytes = json.dumps(payload).encode("utf-8") if payload else None
    
    req = urllib.request.Request(url, data=data_bytes, method=method)
    req.add_header("Authorization", f"Bearer {GITHUB_TOKEN}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "ARGUS-Assistant")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if payload:
        req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req) as resp:
            content = resp.read().decode("utf-8")
            return json.loads(content) if content else {}
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8")
        raise RuntimeError(f"GitHub API {e.code} error: {err_msg}")

def github_list_repos(limit: int = 15) -> str:
    try:
        data = _github_api_request(f"/user/repos?sort=updated&per_page={limit}")
        if not data:
            return "No repositories found for this account."
        repos = [f"- {r['full_name']} (Private: {r['private']}, Description: {r['description'] or 'None'})" for r in data]
        return "\n".join(repos)
    except Exception as e:
        return f"Failed to list GitHub repositories: {str(e)}"

def github_get_tree(repo: str, branch: str = "main", path_prefix: Optional[str] = None) -> str:
    owner = GITHUB_DEFAULT_OWNER
    if "/" in repo:
        owner, repo = repo.split("/", 1)
    try:
        data = _github_api_request(f"/repos/{owner}/{repo}/git/trees/{branch}?recursive=1")
        tree = data.get("tree", [])
        if not tree:
            return f"No files detected on branch '{branch}' for {owner}/{repo}."

        files = []
        for item in tree:
            p = item.get("path", "")
            if path_prefix and not p.startswith(path_prefix.strip("/")):
                continue
            item_type = "DIR " if item.get("type") == "tree" else "FILE"
            files.append(f"[{item_type}] {p}")

        return "\n".join(files[:60]) if files else f"No files matching prefix '{path_prefix}'."
    except Exception as e:
        return f"Failed to fetch directory tree: {str(e)}"

def github_read_file(repo: str, path: str, branch: str = "main") -> str:
    owner = GITHUB_DEFAULT_OWNER
    if "/" in repo:
        owner, repo = repo.split("/", 1)
    clean_path = path.strip("/")
    try:
        data = _github_api_request(f"/repos/{owner}/{repo}/contents/{clean_path}?ref={branch}")
        if data.get("encoding") == "base64" and data.get("content"):
            decoded = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
            return f"--- Content of {clean_path} ({owner}/{repo}) ---\n{decoded}"
        return f"File found but unexpected encoding or directory: {clean_path}"
    except Exception as e:
        return f"Failed to read file '{path}': {str(e)}"

def github_write_file(repo: str, path: str, content: str, commit_message: str, branch: str = "main") -> str:
    owner = GITHUB_DEFAULT_OWNER
    if "/" in repo:
        owner, repo = repo.split("/", 1)
    clean_path = path.strip("/")
    sha = None

    try:
        existing = _github_api_request(f"/repos/{owner}/{repo}/contents/{clean_path}?ref={branch}")
        sha = existing.get("sha")
    except Exception:
        pass

    payload: Dict[str, Any] = {
        "message": commit_message,
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
        "branch": branch
    }
    if sha:
        payload["sha"] = sha

    try:
        resp = _github_api_request(f"/repos/{owner}/{repo}/contents/{clean_path}", method="PUT", payload=payload)
        commit_sha = resp.get("commit", {}).get("sha", "")[:7]
        return f"Successfully committed '{clean_path}' to {owner}/{repo} on branch '{branch}' (Commit: {commit_sha})."
    except Exception as e:
        return f"Failed to commit file '{path}': {str(e)}"

def github_create_issue(repo: str, title: str, body: Optional[str] = "") -> str:
    owner = GITHUB_DEFAULT_OWNER
    if "/" in repo:
        owner, repo = repo.split("/", 1)
    payload = {"title": title, "body": body or ""}
    try:
        resp = _github_api_request(f"/repos/{owner}/{repo}/issues", method="POST", payload=payload)
        return f"Issue created: #{resp.get('number')} '{resp.get('title')}' ({resp.get('html_url')})"
    except Exception as e:
        return f"Failed to create issue: {str(e)}"

# ==============================================================================
# Live Web Search & Google Calendar
# ==============================================================================

def web_search_tool(query: str, max_results: int = 5) -> str:
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
            if not results:
                return f"No results found for search query: '{query}'"
            
            output = []
            for r in results:
                title = r.get("title", "Untitled")
                body = r.get("body", "No description.")
                href = r.get("href", "")
                output.append(f"Title: {title}\nSnippet: {body}\nURL: {href}")
            return "\n\n".join(output)
    except Exception as e:
        return f"Web search error: {str(e)}"

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
        except Exception:
            return None

    if not creds_info:
        return None

    try:
        creds = Credentials.from_authorized_user_info(creds_info)
        return build("calendar", "v3", credentials=creds)
    except Exception:
        return None

def fetch_raw_calendar_events(time_min_iso: Optional[str] = None, max_results: int = 25) -> List[Dict[str, Any]]:
    service = get_calendar_service()
    if not service:
        return []

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
            except Exception:
                continue

        all_events.sort(key=lambda x: x['start'] if x['start'] else "")
        return all_events[:max_results]
    except Exception:
        return []

def list_calendar_events(time_min_iso: Optional[str] = None, max_results: int = 15) -> str:
    events = fetch_raw_calendar_events(time_min_iso, max_results)
    if not events:
        return "No upcoming events found on connected calendars or integration is inactive."
    result = [f"- [{e['calendar']}] {e['summary']} (Starts: {e['start']}, ID: {e['id']})" for e in events]
    return "\n".join(result)

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
# Tool Schema Declarations & Dispatch
# ==============================================================================

tools_schema = [
    {
        "type": "function",
        "function": {
            "name": "github_list_repos",
            "description": "Lists repositories in the user's GitHub account.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Maximum repositories to return (default 15)."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "github_get_tree",
            "description": "Lists all folders and files inside a GitHub repo to locate target folders and file paths.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository name (e.g. 'argus-backend')."},
                    "branch": {"type": "string", "description": "Branch name (default 'main')."},
                    "path_prefix": {"type": "string", "description": "Optional directory filter (e.g. 'backend/' or 'src/')."}
                },
                "required": ["repo"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "github_read_file",
            "description": "Reads and inspects code or text from any file in a GitHub repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository name (e.g. 'argus-backend')."},
                    "path": {"type": "string", "description": "Path to the file inside the repo (e.g. 'backend/main.py')."},
                    "branch": {"type": "string", "description": "Branch name (default 'main')."}
                },
                "required": ["repo", "path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "github_write_file",
            "description": "Creates a new file or updates an existing file with a commit directly into the GitHub repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository name."},
                    "path": {"type": "string", "description": "Path to write or update (e.g. 'notes/todo.md' or 'src/main.py')."},
                    "content": {"type": "string", "description": "Full file content string."},
                    "commit_message": {"type": "string", "description": "Git commit message."},
                    "branch": {"type": "string", "description": "Target branch (default 'main')."}
                },
                "required": ["repo", "path", "content", "commit_message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "github_create_issue",
            "description": "Creates a new issue or tracking ticket on a GitHub repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository name."},
                    "title": {"type": "string", "description": "Issue title."},
                    "body": {"type": "string", "description": "Issue description / body."}
                },
                "required": ["repo", "title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search_tool",
            "description": "Search the live web for current weather, news, facts, documentation, or real-time info.",
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
            "description": "Permanently save a user fact, preference, rule, or personal detail into long-term memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fact": {"type": "string", "description": "The exact factual information or preference to remember permanently."},
                    "category": {"type": "string", "description": "Optional category."}
                },
                "required": ["fact"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_calendar_events",
            "description": "Lists upcoming events across all calendars including Canvas feeds and personal calendar.",
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
    "github_list_repos": github_list_repos,
    "github_get_tree": github_get_tree,
    "github_read_file": github_read_file,
    "github_write_file": github_write_file,
    "github_create_issue": github_create_issue,
    "web_search_tool": web_search_tool,
    "save_fact_tool": save_fact_tool,
    "list_calendar_events": list_calendar_events,
    "create_calendar_event": create_calendar_event,
    "update_calendar_event": update_calendar_event,
    "delete_calendar_event": delete_calendar_event
}

# ==============================================================================
# Endpoints
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
    return {"memories": get_all_memories_records()}

@app.delete("/memories/{memory_id}")
def delete_memory(memory_id: int, token: str = Depends(verify_token)):
    success = delete_memory_by_id(memory_id)
    if not success:
        raise HTTPException(status_code=404, detail="Memory record not found.")
    return {"status": "deleted", "id": memory_id}

@app.get("/agenda")
def get_agenda(token: str = Depends(verify_token)):
    events = fetch_raw_calendar_events(max_results=20)
    return {"events": events}

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
        byte_len = len(audio_bytes) if audio_bytes else 0

        # WebM header alone is ~800-1200 bytes. If byte_len < 1400, no speech was recorded.
        if not audio_bytes or byte_len < 1400:
            raise HTTPException(
                status_code=400,
                detail=f"Audio sample contains only container headers ({byte_len} bytes). Please speak before clicking stop."
            )

        filename = file.filename or "recording.webm"
        
        transcription = groq_client.audio.transcriptions.create(
            file=(filename, audio_bytes),
            model="whisper-large-v3",
            prompt="Voice command directed to personal AI assistant ARGUS.",
            response_format="text",
            temperature=0.0
        )
        
        text = str(transcription).strip()
        cleaned_lower = text.lower().rstrip(".!?, ")

        hallucination_blacklist = {"you", "thank you", "thanks", "subtitles by", "thank you for watching", "bye"}
        if cleaned_lower in hallucination_blacklist or len(cleaned_lower) <= 1:
            return {"text": ""}

        return {"text": text}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Audio transcription error: {str(e)}")

@app.post("/chat", response_model=ChatResponse)
@app.post("/chat/", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest, token: str = Depends(verify_token)):
    if not groq_client:
        raise HTTPException(status_code=500, detail="GROQ_API_KEY is not configured in environment.")

    session_id = request.session_id or str(datetime.datetime.now().timestamp())
    
    facts = list_facts()
    facts_block = "\n".join([f"- {f}" for f in facts]) if facts else "No permanent facts recorded yet."
    history_records = get_history(session_id, limit=20)
    
    system_prompt = (
        "You are ARGUS, an advanced AI executive assistant with direct tool execution powers.\n"
        "You have synchronized access to:\n"
        "1. GITHUB ACCOUNT & FOLDERS: github_list_repos, github_get_tree, github_read_file, github_write_file, github_create_issue. "
        "When asked to inspect code, find a file, navigate folders, or commit changes, locate the repository and files and complete the operation.\n"
        "2. LIVE WEB SEARCH: web_search_tool for live weather, documentation, facts, and news.\n"
        "3. GOOGLE CALENDAR & CANVAS: list_calendar_events, create_calendar_event, etc.\n"
        "4. PERMANENT MEMORY: save_fact_tool.\n\n"
        "PERMANENT USER FACTS STORED IN MEMORY:\n"
        f"{facts_block}\n\n"
        "INSTRUCTIONS:\n"
        "- When executing tasks on GitHub repositories, inspect directory trees first if you need to discover folder locations.\n"
        "- Synthesize tool outputs directly and conversationally for the user.\n"
        "- Keep responses crisp, executive, and precise."
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
            temperature=0.4
        )

        response_msg = response.choices[0].message
        
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

            last_tool_output = ""
            for tool_call in response_msg.tool_calls:
                fn_name = tool_call.function.name
                fn_args = json.loads(tool_call.function.arguments) if tool_call.function.arguments else {}
                
                if fn_name in tool_dispatch:
                    fn_result = tool_dispatch[fn_name](**fn_args)
                else:
                    fn_result = f"Error: Function {fn_name} not found."

                last_tool_output = str(fn_result)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": last_tool_output
                })

            second_response = groq_client.chat.completions.create(
                model="openai/gpt-oss-20b",
                messages=messages,
                tools=tools_schema,
                temperature=0.4
            )
            
            content_output = second_response.choices[0].message.content
            reply_text = content_output if (content_output and content_output.strip()) else last_tool_output
        else:
            reply_text = response_msg.content or "No response generated."

    except Exception as e:
        reply_text = f"ARGUS backend error: {str(e)}"

    save_message(session_id, "assistant", reply_text)
    return ChatResponse(reply=reply_text, session_id=session_id)