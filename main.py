import os
import json
import base64
import sqlite3
import datetime
import urllib.request
import urllib.parse
import urllib.error
from email.message import EmailMessage
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
    GOOGLE_APIS_AVAILABLE = True
except ImportError:
    GOOGLE_APIS_AVAILABLE = False

# ==============================================================================
# Configuration & Initialization
# ==============================================================================

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
ARGUS_BEARER_TOKEN = os.getenv("ARGUS_BEARER_TOKEN", "default_secret_token")
GOOGLE_CALENDAR_TOKEN = os.getenv("GOOGLE_CALENDAR_TOKEN")
DATABASE_URL = os.getenv("ARGUS_DB_PATH") or os.getenv("DATABASE_URL") or "argus.db"

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_DEFAULT_OWNER = os.getenv("GITHUB_DEFAULT_OWNER", "KYWILS21")

CANVAS_BASE_URL = os.getenv("CANVAS_BASE_URL", "https://temple.instructure.com").rstrip("/")
CANVAS_API_TOKEN = os.getenv("CANVAS_API_TOKEN", "")
CANVAS_SESSION_COOKIE = os.getenv("CANVAS_SESSION_COOKIE", "")

groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

app = FastAPI(title="ARGUS API", version="2.8.1")

# ==============================================================================
# CORS Configuration
# ==============================================================================

ALLOWED_ORIGINS = [
    "https://kywils21.github.io",
    "http://localhost:8081",
    "http://localhost:3000",
    "http://localhost:19006",
    "http://127.0.0.1:8081",
    "http://127.0.0.1:3000",
    "exp://localhost:8081",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

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
# Google OAuth Client Factory (Calendar & Gmail)
# ==============================================================================

def get_google_credentials():
    if not GOOGLE_APIS_AVAILABLE:
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
            "scopes": [
                "https://www.googleapis.com/auth/calendar",
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/gmail.compose",
                "https://www.googleapis.com/auth/gmail.modify"
            ]
        }
    elif GOOGLE_CALENDAR_TOKEN:
        try:
            creds_info = json.loads(GOOGLE_CALENDAR_TOKEN)
        except Exception:
            return None

    if not creds_info:
        return None

    try:
        return Credentials.from_authorized_user_info(creds_info)
    except Exception:
        return None

def get_calendar_service():
    creds = get_google_credentials()
    if not creds:
        return None
    try:
        return build("calendar", "v3", credentials=creds)
    except Exception:
        return None

def get_gmail_service():
    creds = get_google_credentials()
    if not creds:
        return None
    try:
        return build("gmail", "v1", credentials=creds)
    except Exception:
        return None

# ==============================================================================
# Canvas REST API Client & Tools (Token + Session Cookie Fallback)
# ==============================================================================

def _canvas_api_request(endpoint: str, method: str = "GET", payload: Optional[Dict[str, Any]] = None) -> Any:
    if not CANVAS_API_TOKEN and not CANVAS_SESSION_COOKIE:
        raise ValueError("Neither CANVAS_API_TOKEN nor CANVAS_SESSION_COOKIE is configured in Railway environment variables.")

    clean_endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"
    url = f"{CANVAS_BASE_URL}/api/v1{clean_endpoint}"
    
    data_bytes = json.dumps(payload).encode("utf-8") if payload else None
    req = urllib.request.Request(url, data=data_bytes, method=method)
    
    if CANVAS_API_TOKEN:
        req.add_header("Authorization", f"Bearer {CANVAS_API_TOKEN}")
    elif CANVAS_SESSION_COOKIE:
        clean_cookie = CANVAS_SESSION_COOKIE.strip().strip('"').strip("'")
        req.add_header("Cookie", clean_cookie)

    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    if payload:
        req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req) as resp:
            content = resp.read().decode("utf-8")
            return json.loads(content) if content else {}
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8")
        raise RuntimeError(f"Canvas API HTTP {e.code}: {err_msg}")

def canvas_list_courses() -> str:
    """Lists currently active enrolled courses, course names, codes, and IDs."""
    try:
        courses = _canvas_api_request("/courses?enrollment_state=active&include[]=term")
        if not courses:
            return "No active enrolled courses found on Canvas."

        output = []
        for c in courses:
            c_id = c.get("id")
            c_name = c.get("name") or c.get("course_code") or "Untitled Course"
            c_code = c.get("course_code", "")
            term = c.get("term", {}).get("name", "Current Term")
            output.append(f"- [{c_id}] {c_name} (Code: {c_code}, Term: {term})")
        return "\n".join(output)
    except Exception as e:
        return f"Failed to list Canvas courses: {str(e)}"

def canvas_get_assignment_rubric(course_id: str, search_query: Optional[str] = None) -> str:
    """
    Fetches assignments for a course with full details, descriptions, 
    and grading rubric criteria.
    """
    try:
        endpoint = f"/courses/{course_id}/assignments?include[]=rubric&order_by=due_at"
        assignments = _canvas_api_request(endpoint)
        if not assignments:
            return f"No assignments found for course ID {course_id}."

        matches = []
        for a in assignments:
            title = a.get("name", "Untitled")
            if search_query and search_query.lower() not in title.lower():
                continue

            a_id = a.get("id")
            due_at = a.get("due_at", "No due date specified")
            points = a.get("points_possible", "N/A")
            desc_raw = a.get("description", "") or "No description provided."
            clean_desc = desc_raw.replace("<p>", "").replace("</p>", "\n").replace("<br>", "\n").replace("<br/>", "\n")
            clean_desc = clean_desc[:600]

            rubric = a.get("rubric", [])
            rubric_details = []
            if rubric:
                for crit in rubric:
                    c_desc = crit.get("description", "Criterion")
                    c_pts = crit.get("points", 0)
                    ratings = [f"{r.get('description', '')} ({r.get('points')} pts)" for r in crit.get("ratings", [])]
                    rubric_details.append(f"  * {c_desc} [Max: {c_pts} pts]: {', '.join(ratings)}")
                rubric_block = "\n".join(rubric_details)
            else:
                rubric_block = "  No structured rubric attached to this assignment."

            matches.append(
                f"=== ASSIGNMENT: {title} (ID: {a_id}) ===\n"
                f"Due Date: {due_at}\n"
                f"Points Possible: {points}\n"
                f"Description Snippet:\n{clean_desc.strip()}\n\n"
                f"Grading Rubric:\n{rubric_block}\n"
            )

        if not matches:
            return f"No assignments matched '{search_query}' in course {course_id}."

        return "\n\n".join(matches[:4])
    except Exception as e:
        return f"Failed to fetch Canvas rubric: {str(e)}"

def canvas_get_submission_grades(course_id: str, assignment_id: Optional[str] = None) -> str:
    """
    Checks assignment submissions, grades, rubric evaluations, 
    and instructor feedback comments.
    """
    try:
        if assignment_id:
            endpoint = f"/courses/{course_id}/assignments/{assignment_id}/submissions/self?include[]=submission_comments&include[]=rubric_assessment"
            sub = _canvas_api_request(endpoint)
            submissions = [sub] if sub else []
        else:
            endpoint = f"/courses/{course_id}/students/submissions/self?include[]=assignment&include[]=submission_comments&include[]=rubric_assessment&per_page=15"
            submissions = _canvas_api_request(endpoint)

        if not submissions:
            return f"No submissions found for course {course_id}."

        results = []
        for s in submissions:
            a_info = s.get("assignment", {})
            a_name = a_info.get("name") or f"Assignment #{s.get('assignment_id')}"
            state = s.get("workflow_state", "unsubmitted")
            score = s.get("score")
            grade = s.get("grade")
            sub_at = s.get("submitted_at") or "Not submitted"

            comments = []
            for c in s.get("submission_comments", []):
                author = c.get("author_name", "Instructor")
                comment_text = c.get("comment", "")
                comments.append(f"  * [{author}]: {comment_text}")
            comment_block = "\n".join(comments) if comments else "  No instructor comments."

            rubric_assess = s.get("rubric_assessment", {})
            rubric_eval = []
            if rubric_assess:
                for crit_id, eval_data in rubric_assess.items():
                    r_pts = eval_data.get("points")
                    r_comments = eval_data.get("comments", "")
                    rubric_eval.append(f"  * Criterion {crit_id}: {r_pts} pts - {r_comments}")
                rubric_eval_block = "\n".join(rubric_eval)
            else:
                rubric_eval_block = "  No rubric breakdown evaluated."

            results.append(
                f"=== {a_name} ===\n"
                f"Status: {state.upper()}\n"
                f"Score: {score} | Grade: {grade}\n"
                f"Submitted At: {sub_at}\n"
                f"Instructor Comments:\n{comment_block}\n"
                f"Rubric Assessment:\n{rubric_eval_block}\n"
            )

        return "\n".join(results[:5])
    except Exception as e:
        return f"Failed to retrieve submission grades: {str(e)}"

# ==============================================================================
# Gmail Tools
# ==============================================================================

def gmail_search_messages(query: str = "is:unread", max_results: int = 5) -> str:
    service = get_gmail_service()
    if not service:
        return "Gmail integration is not active or Google credentials are missing."

    try:
        results = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
        messages = results.get("messages", [])

        if not messages:
            return f"No messages found matching search query: '{query}'."

        summaries = []
        for m in messages:
            msg_id = m["id"]
            msg_detail = service.users().messages().get(
                userId="me", id=msg_id, format="metadata",
                metadataHeaders=["From", "Subject", "Date"]
            ).execute()
            
            headers = {h["name"]: h["value"] for h in msg_detail.get("payload", {}).get("headers", [])}
            sender = headers.get("From", "Unknown Sender")
            subject = headers.get("Subject", "(No Subject)")
            date = headers.get("Date", "")
            snippet = msg_detail.get("snippet", "")

            summaries.append(f"- [ID: {msg_id}] From: {sender}\n  Subject: {subject}\n  Date: {date}\n  Preview: {snippet}")

        return "\n\n".join(summaries)
    except Exception as e:
        return f"Error querying Gmail: {str(e)}"

def gmail_read_message(message_id: str) -> str:
    service = get_gmail_service()
    if not service:
        return "Gmail integration is not active."

    try:
        msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        
        sender = headers.get("From", "Unknown")
        subject = headers.get("Subject", "No Subject")
        date = headers.get("Date", "")
        snippet = msg.get("snippet", "")

        return (
            f"--- Email Message Details ---\n"
            f"ID: {message_id}\n"
            f"From: {sender}\n"
            f"Subject: {subject}\n"
            f"Date: {date}\n\n"
            f"Body Content / Snippet:\n{snippet}"
        )
    except Exception as e:
        return f"Error reading message {message_id}: {str(e)}"

def gmail_create_draft(to_address: str, subject: str, body_text: str) -> str:
    service = get_gmail_service()
    if not service:
        return "Gmail integration is not active."

    try:
        message = EmailMessage()
        message["To"] = to_address
        message["Subject"] = subject
        message.set_content(body_text)

        encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        create_message = {"message": {"raw": encoded_message}}

        draft = service.users().drafts().create(userId="me", body=create_message).execute()
        draft_id = draft.get("id")
        return f"Draft created successfully in Gmail! (Draft ID: {draft_id})\nRecipient: {to_address}\nSubject: {subject}"
    except Exception as e:
        return f"Error creating Gmail draft: {str(e)}"

# ==============================================================================
# Google Calendar & Search Tools
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
# Reservation & Briefing Engines
# ==============================================================================

def check_schedule_conflict(target_time_iso: str, duration_minutes: int = 120) -> str:
    try:
        target_dt = datetime.datetime.fromisoformat(target_time_iso.replace("Z", "+00:00"))
    except Exception:
        return "Invalid ISO format for target_time_iso."

    end_dt = target_dt + datetime.timedelta(minutes=duration_minutes)
    search_start = (target_dt - datetime.timedelta(hours=3)).isoformat()
    events = fetch_raw_calendar_events(time_min_iso=search_start, max_results=10)

    conflicts = []
    for e in events:
        start_str = e.get("start")
        if not start_str:
            continue
        try:
            ev_start = datetime.datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            ev_end = ev_start + datetime.timedelta(minutes=60)
            if max(target_dt, ev_start) < min(end_dt, ev_end):
                conflicts.append(f"[{e['calendar']}] {e['summary']} at {ev_start.strftime('%I:%M %p')}")
        except Exception:
            continue

    if conflicts:
        return f"CONFLICT DETECTED: You have existing commitments during that window:\n" + "\n".join(conflicts)
    return "CLEAR: No scheduling conflicts detected on your calendars for this window."

def restaurant_reservation_tool(restaurant_name: str, location: str, party_size: int, date_str: str, time_str: str) -> str:
    search_query = f"{restaurant_name} {location} reservations OpenTable Resy phone address"
    search_info = web_search_tool(search_query, max_results=4)

    encoded_name = urllib.parse.quote(restaurant_name)
    encoded_loc = urllib.parse.quote(location)
    
    opentable_link = f"https://www.opentable.com/s?term={encoded_name}&dateTime={date_str}T{time_str.replace(':', '%3A')}&covers={party_size}"
    resy_link = f"https://resy.com/cities?query={encoded_name}"
    google_reserve_link = f"https://www.google.com/maps/search/{encoded_name}+{encoded_loc}"

    try:
        dt_start = datetime.datetime.fromisoformat(f"{date_str}T{time_str}:00")
        dt_end = dt_start + datetime.timedelta(hours=2)
        start_iso = dt_start.isoformat()
        end_iso = dt_end.isoformat()
        
        cal_summary = f"RESERVATION (HOLD): {restaurant_name} (Party of {party_size})"
        cal_desc = f"Reservation for {party_size} at {restaurant_name}.\nLocation: {location}\nOpenTable: {opentable_link}\nResy: {resy_link}\n\nSearch Intel:\n{search_info[:500]}"
        
        cal_result = create_calendar_event(cal_summary, start_iso, end_iso, description=cal_desc)
    except Exception as e:
        cal_result = f"Could not create calendar placeholder: {str(e)}"

    return (
        f"--- RESERVATION DIRECTIVE PREPARED ---\n"
        f"Venue: {restaurant_name} ({location})\n"
        f"Party Size: {party_size}\n"
        f"Time Window: {date_str} at {time_str}\n\n"
        f"Calendar Hold: {cal_result}\n\n"
        f"Direct Booking Portals:\n"
        f"1. OpenTable: {opentable_link}\n"
        f"2. Resy: {resy_link}\n"
        f"3. Google Maps / Reserve: {google_reserve_link}\n\n"
        f"Venue Intel & Phone:\n{search_info}"
    )

def executive_briefing_tool(location: str = "Philadelphia, PA") -> str:
    now = datetime.datetime.now()
    today_str = now.strftime("%A, %B %d, %Y")
    
    weather_intel = web_search_tool(f"current weather today in {location}", max_results=2)
    gmail_intel = gmail_search_messages(query="is:unread", max_results=5)

    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone().isoformat()
    agenda_events = fetch_raw_calendar_events(time_min_iso=start_of_day, max_results=15)

    agenda_lines = []
    if agenda_events:
        for ev in agenda_events:
            cal_label = ev.get("calendar", "Calendar")
            summary = ev.get("summary", "Event")
            start = ev.get("start", "")
            agenda_lines.append(f"- [{cal_label}] {summary} at {start}")
        agenda_block = "\n".join(agenda_lines)
    else:
        agenda_block = "No events, deadlines, or exams scheduled for the next 48 hours."

    return (
        f"=== EXECUTIVE BRIEFING FOR {today_str.upper()} ===\n\n"
        f"[LOCAL WEATHER - {location.upper()}]\n{weather_intel}\n\n"
        f"[ACTIONABLE INBOX // UNREAD GMAIL]\n{gmail_intel}\n\n"
        f"[CANVAS & SCHEDULE // NEXT 48 HOURS]\n{agenda_block}\n\n"
        f"=== END OF BRIEFING DATA ==="
    )

# ==============================================================================
# Tool Schema Declarations & Dispatch
# ==============================================================================

tools_schema = [
    {
        "type": "function",
        "function": {
            "name": "canvas_list_courses",
            "description": "Lists active enrolled courses on Canvas with their unique Course IDs and terms.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "canvas_get_assignment_rubric",
            "description": "Retrieves assignment instructions, prompts, points, and grading rubrics for a specific Canvas course.",
            "parameters": {
                "type": "object",
                "properties": {
                    "course_id": {"type": "string", "description": "The unique numerical Canvas course ID."},
                    "search_query": {"type": "string", "description": "Optional keyword or assignment title filter (e.g. 'Lab 2', 'Project 1')."}
                },
                "required": ["course_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "canvas_get_submission_grades",
            "description": "Checks student assignment submissions, earned scores, letter grades, rubric evaluation breakdowns, and instructor feedback.",
            "parameters": {
                "type": "object",
                "properties": {
                    "course_id": {"type": "string", "description": "The numerical Canvas course ID."},
                    "assignment_id": {"type": "string", "description": "Optional specific numerical assignment ID. If omitted, returns recent submissions."}
                },
                "required": ["course_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "executive_briefing_tool",
            "description": "Compiles a complete morning briefing chaining live weather, unread emails from Gmail, and upcoming Canvas & Google Calendar events.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City and state for weather lookup."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "gmail_search_messages",
            "description": "Searches Gmail for emails matching a query.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Gmail search query syntax."},
                    "max_results": {"type": "integer"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "gmail_read_message",
            "description": "Reads details and content of a specific email by its message ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_id": {"type": "string"}
                },
                "required": ["message_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "gmail_create_draft",
            "description": "Creates an email draft in Gmail.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to_address": {"type": "string"},
                    "subject": {"type": "string"},
                    "body_text": {"type": "string"}
                },
                "required": ["to_address", "subject", "body_text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "restaurant_reservation_tool",
            "description": "Prepares and executes a restaurant reservation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_name": {"type": "string"},
                    "location": {"type": "string"},
                    "party_size": {"type": "integer"},
                    "date_str": {"type": "string"},
                    "time_str": {"type": "string"}
                },
                "required": ["restaurant_name", "location", "party_size", "date_str", "time_str"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_schedule_conflict",
            "description": "Checks if the user has an existing class, exam, or event that conflicts with a proposed time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_time_iso": {"type": "string"},
                    "duration_minutes": {"type": "integer"}
                },
                "required": ["target_time_iso"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "github_list_repos",
            "description": "Lists repositories in the user's GitHub account.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "github_get_tree",
            "description": "Lists all folders and files inside a GitHub repo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "branch": {"type": "string"},
                    "path_prefix": {"type": "string"}
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
                    "repo": {"type": "string"},
                    "path": {"type": "string"},
                    "branch": {"type": "string"}
                },
                "required": ["repo", "path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "github_write_file",
            "description": "Creates or updates a file in GitHub with a commit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "commit_message": {"type": "string"},
                    "branch": {"type": "string"}
                },
                "required": ["repo", "path", "content", "commit_message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "github_create_issue",
            "description": "Creates a new issue on a GitHub repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "title": {"type": "string"},
                    "body": {"type": "string"}
                },
                "required": ["repo", "title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search_tool",
            "description": "Search the live web for real-time info.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_fact_tool",
            "description": "Permanently save a user fact into long-term memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fact": {"type": "string"},
                    "category": {"type": "string"}
                },
                "required": ["fact"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_calendar_events",
            "description": "Lists upcoming events across all calendars.",
            "parameters": {
                "type": "object",
                "properties": {
                    "time_min_iso": {"type": "string"},
                    "max_results": {"type": "integer"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_calendar_event",
            "description": "Creates a new event on the primary calendar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "start_time_iso": {"type": "string"},
                    "end_time_iso": {"type": "string"},
                    "description": {"type": "string"}
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
                    "event_id": {"type": "string"},
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
            "description": "Deletes an event from the primary Google Calendar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string"}
                },
                "required": ["event_id"]
            }
        }
    }
]

tool_dispatch = {
    "canvas_list_courses": canvas_list_courses,
    "canvas_get_assignment_rubric": canvas_get_assignment_rubric,
    "canvas_get_submission_grades": canvas_get_submission_grades,
    "executive_briefing_tool": executive_briefing_tool,
    "gmail_search_messages": gmail_search_messages,
    "gmail_read_message": gmail_read_message,
    "gmail_create_draft": gmail_create_draft,
    "restaurant_reservation_tool": restaurant_reservation_tool,
    "check_schedule_conflict": check_schedule_conflict,
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
    
    current_time_str = datetime.datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")

    system_prompt = (
        f"You are ARGUS, an autonomous executive AI assistant. Current time: {current_time_str}.\n"
        "You have direct execution powers over:\n"
        "1. CANVAS ACADEMIC SYSTEM: canvas_list_courses, canvas_get_assignment_rubric, and canvas_get_submission_grades. "
        "When the user asks about an assignment or rubric, first list or identify the course ID, then retrieve the criteria breakdown. "
        "When asked about scores or grades, inspect recent submissions and feedback.\n"
        "2. EXECUTIVE BRIEFINGS: executive_briefing_tool.\n"
        "3. GMAIL INTEGRATION: gmail_search_messages, gmail_read_message, gmail_create_draft.\n"
        "4. RESERVATIONS & BOOKINGS: check_schedule_conflict and restaurant_reservation_tool.\n"
        "5. GITHUB: github_list_repos, github_get_tree, github_read_file, github_write_file, github_create_issue.\n"
        "6. GOOGLE CALENDAR: list_calendar_events, create_calendar_event, etc.\n"
        "7. LIVE WEB SEARCH: web_search_tool.\n"
        "8. PERMANENT MEMORY: save_fact_tool.\n\n"
        "PERMANENT USER FACTS STORED IN MEMORY:\n"
        f"{facts_block}\n\n"
        "OPERATIONAL DIRECTIVE:\n"
        "- Chain multi-step tool calls seamlessly before returning the final report."
    )

    messages = [{"role": "system", "content": system_prompt}]
    
    for r in history_records:
        role_label = "user" if r["role"] == "user" else "assistant"
        messages.append({"role": role_label, "content": r["content"]})

    messages.append({"role": "user", "content": request.message})
    save_message(session_id, "user", request.message)

    reply_text = ""
    max_turns = 5
    turn_count = 0

    try:
        while turn_count < max_turns:
            turn_count += 1
            response = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=messages,
                tools=tools_schema,
                tool_choice="auto",
                temperature=0.4
            )

            response_msg = response.choices[0].message

            if not response_msg.tool_calls:
                reply_text = response_msg.content or "Task completed."
                break

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

        if not reply_text:
            reply_text = "Directive completed successfully."

    except Exception as e:
        reply_text = f"ARGUS backend error: {str(e)}"

    save_message(session_id, "assistant", reply_text)
    return ChatResponse(reply=reply_text, session_id=session_id)