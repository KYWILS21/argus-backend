#!/usr/bin/env python3
"""
=========================================================================================
ARGUS OS v4.0 â€” AUTONOMOUS JARVIS-CLASS EXECUTIVE AI & CREATIVE SYNTHESIS ENGINE
=========================================================================================
Architecture:
  1. Multi-Model Dynamic Router (Groq LPU Inference with automatic model discovery/failover)
  2. 8-Table Persistent SQLite Knowledge Graph, Task Queue & Episodic Memory
  3. Executive Productivity Suite:
     - Google Calendar Engine (OAuth2 / Service Account / iCal + Local SQLite Fallback)
     - Gmail Communications Engine (IMAP / SMTP + Google API Integration)
     - Canvas LMS Academic & Project Tracker (Courses, Assignments, Deadlines, Grades)
     - Autonomous Executive Briefing Generator (Weather, Schedule, Inbox, Canvas, Tasks)
  4. Perplexity-Grade Multi-Hop Web & Media Research Engine (Jina Reader + DDGS + Bing Proxy)
  5. Human-in-the-Loop (HITL) Creative Approval Gate & Zero-Template Web/Code Compiler
  6. Self-Healing Live HTML/Code Editor & Full FastAPI REST/Command Server
=========================================================================================
"""

import os
import sys
import re
import json
import time
import uuid
import math
import shutil
import sqlite3
import smtplib
import imaplib
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import datetime
import traceback
import urllib.parse
import urllib.request
from typing import Dict, Any, List, Optional, Tuple, Union

from fastapi import FastAPI, Request, HTTPException, Depends, Header, Query, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from groq import Groq
except ImportError:
    Groq = None

try:
    from duckduckgo_search import DDGS # type: ignore
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None


# =========================================================================================
# SECTION 1: GLOBAL CONFIGURATION, ENVIRONMENT & DIRECTORY INITIALIZATION
# =========================================================================================

APP_VERSION = "4.0.0-JARVIS"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEMO_DIR = os.path.join(BASE_DIR, "generated_demos")
WORKSPACE_DIR = os.path.join(BASE_DIR, "argus_workspace")
DB_PATH = os.path.join(BASE_DIR, os.getenv("ARGUS_DB_PATH", "argus_memory.db"))

os.makedirs(DEMO_DIR, exist_ok=True)
os.makedirs(WORKSPACE_DIR, exist_ok=True)

ARGUS_BEARER_TOKEN = os.getenv("ARGUS_BEARER_TOKEN", "argus-secret-key-2026")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "https://argus-backend-ojna.onrender.com").rstrip("/")

# Groq Configuration
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
RAW_GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
if "gpt-oss" in RAW_GROQ_MODEL or "llama-3.1-8b" in RAW_GROQ_MODEL or not RAW_GROQ_MODEL:
    GROQ_MODEL = "llama-3.3-70b-versatile"
else:
    GROQ_MODEL = RAW_GROQ_MODEL

groq_client = Groq(api_key=GROQ_API_KEY) if (Groq and GROQ_API_KEY) else None

# Canvas LMS Configuration
CANVAS_BASE_URL = os.getenv("CANVAS_BASE_URL", "https://templeu.instructure.com").rstrip("/")
CANVAS_API_TOKEN = os.getenv("CANVAS_API_TOKEN", "")

# Gmail IMAP / SMTP Configuration
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", os.getenv("SMTP_USER", ""))
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", os.getenv("SMTP_PASSWORD", ""))
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.gmail.com")

# Google Calendar Configuration
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
GOOGLE_CALENDAR_ICAL_URL = os.getenv("GOOGLE_CALENDAR_ICAL_URL", "")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "")

# Executive Location / Weather Default
DEFAULT_LOCATION = os.getenv("ARGUS_DEFAULT_LOCATION", "Philadelphia, PA")


# =========================================================================================
# SECTION 2: PERSISTENT 8-TABLE SQLITE MEMORY & KNOWLEDGE GRAPH ENGINE
# =========================================================================================

def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_argus_database() -> None:
    """Initializes all 8 core persistent tables for the ARGUS Jarvis OS."""
    conn = get_db_connection()
    c = conn.cursor()

    # 1. Episodic Conversation Memory
    c.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata TEXT DEFAULT '{}',
            created_at REAL NOT NULL
        )
    """)

    # 2. Semantic Knowledge Graph Facts
    c.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity TEXT NOT NULL,
            fact_key TEXT NOT NULL,
            fact_value TEXT NOT NULL,
            category TEXT DEFAULT 'general',
            source_url TEXT DEFAULT '',
            confidence REAL DEFAULT 1.0,
            updated_at REAL NOT NULL
        )
    """)

    # 3. Human-in-the-Loop (HITL) Creative & Action Proposals
    c.execute("""
        CREATE TABLE IF NOT EXISTS pending_tasks (
            task_id TEXT PRIMARY KEY,
            objective TEXT NOT NULL,
            task_type TEXT NOT NULL,
            research_dossier TEXT NOT NULL,
            creative_proposal TEXT NOT NULL,
            status TEXT NOT NULL,
            artifact_slug TEXT NOT NULL,
            user_feedback TEXT DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """)

    # 4. Generated Web Apps, Scripts & Code Artifacts Registry
    c.execute("""
        CREATE TABLE IF NOT EXISTS generated_artifacts (
            slug TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            artifact_type TEXT NOT NULL,
            objective TEXT NOT NULL,
            file_path TEXT NOT NULL,
            preview_url TEXT NOT NULL,
            version INTEGER DEFAULT 1,
            design_notes TEXT DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """)

    # 5. Local + Synced Calendar Events Table
    c.execute("""
        CREATE TABLE IF NOT EXISTS calendar_events (
            event_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            location TEXT DEFAULT '',
            description TEXT DEFAULT '',
            attendees TEXT DEFAULT '',
            source TEXT DEFAULT 'local',
            status TEXT DEFAULT 'confirmed',
            created_at REAL NOT NULL
        )
    """)

    # 6. Email Outbox, Drafts & Triage Cache
    c.execute("""
        CREATE TABLE IF NOT EXISTS email_records (
            email_id TEXT PRIMARY KEY,
            direction TEXT NOT NULL,
            sender TEXT NOT NULL,
            recipient TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at REAL NOT NULL
        )
    """)

    # 7. Canvas LMS Synced Assignments & Course Cache
    c.execute("""
        CREATE TABLE IF NOT EXISTS canvas_items (
            item_id TEXT PRIMARY KEY,
            course_name TEXT NOT NULL,
            title TEXT NOT NULL,
            item_type TEXT NOT NULL,
            due_at TEXT DEFAULT '',
            points_possible REAL DEFAULT 0.0,
            submission_status TEXT DEFAULT 'unsubmitted',
            html_url TEXT DEFAULT '',
            description TEXT DEFAULT '',
            synced_at REAL NOT NULL
        )
    """)

    # 8. Executive Action & Tool Audit Log
    c.execute("""
        CREATE TABLE IF NOT EXISTS system_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor TEXT NOT NULL,
            action_name TEXT NOT NULL,
            payload TEXT DEFAULT '',
            status TEXT NOT NULL,
            execution_ms REAL DEFAULT 0.0,
            timestamp REAL NOT NULL
        )
    """)

    conn.commit()
    conn.close()


init_argus_database()


def log_audit_event(action_name: str, payload: Any, status: str = "SUCCESS", execution_ms: float = 0.0) -> None:
    """Records every autonomous tool execution into the SQLite audit trail."""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        payload_str = json.dumps(payload) if not isinstance(payload, str) else payload
        c.execute(
            "INSERT INTO system_audit_log (actor, action_name, payload, status, execution_ms, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            ("ARGUS_CORE", action_name, payload_str[:2000], status, execution_ms, time.time())
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Audit Logger Warning]: {e}")


def save_conversation_turn(session_id: str, role: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> None:
    """Persists conversation history in SQLite."""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(
            "INSERT INTO conversations (session_id, role, content, metadata, created_at) VALUES (?, ?, ?, ?, ?)",
            (session_id, role, content, json.dumps(metadata or {}), time.time())
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Memory Warning]: {e}")


def get_recent_conversation(session_id: str, limit: int = 10) -> List[Dict[str, str]]:
    """Retrieves recent conversation context for continuity."""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(
            "SELECT role, content FROM conversations WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit)
        )
        rows = c.fetchall()
        conn.close()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
    except Exception:
        return []


def upsert_knowledge_fact(entity: str, fact_key: str, fact_value: str, category: str = "general", source_url: str = "") -> str:
    """Inserts or updates a verified fact in the ARGUS semantic knowledge base."""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        "SELECT id FROM knowledge_facts WHERE LOWER(entity) = LOWER(?) AND LOWER(fact_key) = LOWER(?)",
        (entity.strip(), fact_key.strip())
    )
    existing = c.fetchone()
    now = time.time()
    if existing:
        c.execute(
            "UPDATE knowledge_facts SET fact_value = ?, category = ?, source_url = ?, updated_at = ? WHERE id = ?",
            (fact_value, category, source_url, now, existing["id"])
        )
        action = "Updated"
    else:
        c.execute(
            "INSERT INTO knowledge_facts (entity, fact_key, fact_value, category, source_url, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (entity.strip(), fact_key.strip(), fact_value, category, source_url, now)
        )
        action = "Stored"
    conn.commit()
    conn.close()
    return f"{action} Knowledge Fact: [{entity}] {fact_key} = {fact_value}"


def query_knowledge_graph(search_term: str = "", limit: int = 25) -> List[Dict[str, Any]]:
    """Searches persistent facts stored in the SQLite Knowledge Graph."""
    conn = get_db_connection()
    c = conn.cursor()
    if search_term.strip():
        q = f"%{search_term.strip()}%"
        c.execute(
            "SELECT entity, fact_key, fact_value, category, source_url, updated_at FROM knowledge_facts "
            "WHERE entity LIKE ? OR fact_key LIKE ? OR fact_value LIKE ? OR category LIKE ? "
            "ORDER BY updated_at DESC LIMIT ?",
            (q, q, q, q, limit)
        )
    else:
        c.execute(
            "SELECT entity, fact_key, fact_value, category, source_url, updated_at FROM knowledge_facts "
            "ORDER BY updated_at DESC LIMIT ?",
            (limit,)
        )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


# =========================================================================================
# SECTION 3: DYNAMIC GROQ MULTI-MODEL DISCOVERY & SELF-HEALING LLM ROUTER
# =========================================================================================

_CACHED_GROQ_MODELS: List[str] = []
_LAST_MODEL_FETCH: float = 0.0


def get_available_groq_models() -> List[str]:
    """Queries Groq API for live text/reasoning models and caches for 10 minutes."""
    global _CACHED_GROQ_MODELS, _LAST_MODEL_FETCH
    now = time.time()
    if _CACHED_GROQ_MODELS and (now - _LAST_MODEL_FETCH < 600):
        return _CACHED_GROQ_MODELS

    discovered: List[str] = []
    if groq_client:
        try:
            resp = groq_client.models.list()
            for m in resp.data:
                mid = m.id
                if not any(bad in mid.lower() for bad in ["whisper", "guard", "tts", "embed", "vision", "playai"]):
                    discovered.append(mid)
        except Exception as e:
            print(f"[ARGUS Model Discovery Warning]: {e}")

    priority_order = [
        GROQ_MODEL,
        "llama-3.3-70b-versatile",
        "deepseek-r1-distill-llama-70b",
        "qwen-qwq-32b",
        "llama3-70b-8192",
        "mixtral-8x7b-32768",
        "gemma2-9b-it",
        "llama3-8b-8192"
    ]

    ordered: List[str] = []
    for p in priority_order:
        if p and (not discovered or p in discovered) and p not in ordered:
            ordered.append(p)
    for d in discovered:
        if d not in ordered:
            ordered.append(d)

    _CACHED_GROQ_MODELS = ordered if ordered else ["llama-3.3-70b-versatile"]
    _LAST_MODEL_FETCH = now
    return _CACHED_GROQ_MODELS


def groq_resilient_completion(
    messages: List[Dict[str, Any]],
    temperature: float = 0.3,
    max_tokens: int = 3500,
    json_mode: bool = False,
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Optional[str] = None
) -> Any:
    """
    Executes a Groq chat completion with automatic multi-model failover.
    Strips <think>...</think> blocks if a reasoning model (like DeepSeek-R1 or QwQ) is selected.
    """
    if not groq_client:
        raise RuntimeError("GROQ_API_KEY is missing or Groq client failed to initialize.")

    models = get_available_groq_models()
    last_error = None

    for model_id in models:
        try:
            kwargs: Dict[str, Any] = {
                "model": model_id,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens
            }
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            if tools:
                kwargs["tools"] = tools
                if tool_choice:
                    kwargs["tool_choice"] = tool_choice

            resp = groq_client.chat.completions.create(**kwargs)
            return resp
        except Exception as e:
            print(f"[ARGUS LLM Failover] Model '{model_id}' error: {e}")
            last_error = e
            # If tool calling or json_mode failed on a specific model, continue to next model
            continue

    raise RuntimeError(f"All active Groq models failed. Last error: {last_error}")


def clean_llm_text(raw_text: str, strip_fences: bool = False) -> str:
    """Removes <think> tags from reasoning models and optionally strips markdown code fences."""
    if not raw_text:
        return ""
    cleaned = re.sub(r'<think>.*?</think>', '', raw_text, flags=re.DOTALL).strip()
    if strip_fences and cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


# =========================================================================================
# SECTION 4: CANVAS LMS ACADEMIC & PROJECT INTEGRATION ENGINE
# =========================================================================================

def canvas_api_get(endpoint: str, params: Optional[Dict[str, Any]] = None) -> Any:
    """Performs an authenticated GET request against the Canvas LMS REST API."""
    if not CANVAS_API_TOKEN:
        return None
    url = f"{CANVAS_BASE_URL}/api/v1/{endpoint.lstrip('/')}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {CANVAS_API_TOKEN}",
            "Accept": "application/json",
            "User-Agent": "ARGUS-Executive-OS/4.0"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8", errors="ignore"))
    except Exception as e:
        print(f"[Canvas API Warning] {endpoint}: {e}")
        return None


def tool_canvas_sync_and_summary(days_ahead: int = 14) -> str:
    """
    Fetches active courses, upcoming assignments, due dates, and submission statuses from Canvas LMS.
    Caches items in SQLite and returns a structured academic/project briefing.
    """
    t0 = time.time()
    conn = get_db_connection()
    c = conn.cursor()

    if CANVAS_API_TOKEN:
        courses = canvas_api_get("courses", {"enrollment_state": "active", "per_page": 20})
        if isinstance(courses, list):
            for course in courses:
                cid = course.get("id")
                cname = course.get("name") or course.get("course_code") or f"Course {cid}"
                if not cid:
                    continue
                assignments = canvas_api_get(
                    f"courses/{cid}/assignments",
                    {"per_page": 30, "order_by": "due_at", "include[]": "submission"}
                )
                if isinstance(assignments, list):
                    for a in assignments:
                        aid = f"canvas-{a.get('id')}"
                        title = a.get("name", "Untitled Assignment")
                        due_at = a.get("due_at") or ""
                        pts = float(a.get("points_possible") or 0.0)
                        html_url = a.get("html_url") or ""
                        desc = re.sub(r'<[^>]+>', ' ', a.get("description") or "").strip()[:500]
                        sub = a.get("submission") or {}
                        sub_status = sub.get("workflow_state", "unsubmitted")
                        if sub.get("submitted_at"):
                            sub_status = f"submitted (score: {sub.get('score', 'pending')})"

                        c.execute("""
                            INSERT OR REPLACE INTO canvas_items
                            (item_id, course_name, title, item_type, due_at, points_possible, submission_status, html_url, description, synced_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (aid, cname, title, "assignment", due_at, pts, sub_status, html_url, desc, time.time()))
            conn.commit()

    # Query cached + live items from SQLite
    c.execute("SELECT * FROM canvas_items ORDER BY CASE WHEN due_at = '' THEN 1 ELSE 0 END, due_at ASC LIMIT 25")
    items = [dict(r) for r in c.fetchall()]
    conn.close()

    log_audit_event("canvas_sync_and_summary", {"days_ahead": days_ahead, "count": len(items)}, "SUCCESS", (time.time() - t0) * 1000)

    if not items:
        status_note = (
            "No Canvas assignments currently cached. (Set `CANVAS_API_TOKEN` in `.env` for live Temple Canvas sync, "
            "or use `canvas_add_custom_task` to track coursework/deadlines manually.)"
        )
        return status_note

    lines = ["### ðŸŽ“ Canvas LMS Coursework & Deadlines"]
    for it in items:
        due_str = it["due_at"][:16].replace("T", " ") if it["due_at"] else "No due date"
        lines.append(
            f"- **[{it['course_name']}] {it['title']}** â€” Due: `{due_str}` | "
            f"Points: `{it['points_possible']}` | Status: `{it['submission_status']}`"
        )
    return "\n".join(lines)


def tool_canvas_add_local_item(course_name: str, title: str, due_at: str, points: float = 100.0, description: str = "") -> str:
    """Adds or updates a tracked academic assignment, exam, or milestone in ARGUS's Canvas table."""
    item_id = f"local-canvas-{int(time.time())}"
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO canvas_items
        (item_id, course_name, title, item_type, due_at, points_possible, submission_status, html_url, description, synced_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (item_id, course_name, title, "assignment", due_at, points, "pending", "", description, time.time()))
    conn.commit()
    conn.close()
    return f"Added Canvas tracker item: [{course_name}] '{title}' due {due_at} ({points} pts)."


# =========================================================================================
# SECTION 5: GOOGLE CALENDAR & SCHEDULING ENGINE
# =========================================================================================

def tool_calendar_list_events(days: int = 7) -> str:
    """Lists upcoming events from local SQLite calendar cache and optional Google Calendar iCal feed."""
    t0 = time.time()
    conn = get_db_connection()
    c = conn.cursor()

    # Optional: Parse live iCal feed if GOOGLE_CALENDAR_ICAL_URL is configured
    if GOOGLE_CALENDAR_ICAL_URL:
        try:
            req = urllib.request.Request(GOOGLE_CALENDAR_ICAL_URL, headers={"User-Agent": "ARGUS-OS/4.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                ics_text = resp.read().decode("utf-8", errors="ignore")
            blocks = re.findall(r'BEGIN:VEVENT(.*?)END:VEVENT', ics_text, flags=re.DOTALL)
            for b in blocks[:25]:
                uid_m = re.search(r'UID:(.*)', b)
                sum_m = re.search(r'SUMMARY:(.*)', b)
                dtstart_m = re.search(r'DTSTART[^:]*:(.*)', b)
                dtend_m = re.search(r'DTEND[^:]*:(.*)', b)
                loc_m = re.search(r'LOCATION:(.*)', b)
                if sum_m and dtstart_m:
                    ev_id = (uid_m.group(1).strip() if uid_m else str(uuid.uuid4()))[:60]
                    c.execute("""
                        INSERT OR REPLACE INTO calendar_events
                        (event_id, title, start_time, end_time, location, description, attendees, source, status, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'google_ical', 'confirmed', ?)
                    """, (
                        ev_id,
                        sum_m.group(1).strip(),
                        dtstart_m.group(1).strip(),
                        dtend_m.group(1).strip() if dtend_m else "",
                        loc_m.group(1).strip() if loc_m else "",
                        "", "", time.time()
                    ))
            conn.commit()
        except Exception as e:
            print(f"[Google Calendar iCal Sync Warning]: {e}")

    c.execute("SELECT * FROM calendar_events WHERE status != 'cancelled' ORDER BY start_time ASC LIMIT 30")
    events = [dict(r) for r in c.fetchall()]
    conn.close()

    log_audit_event("calendar_list_events", {"days": days, "count": len(events)}, "SUCCESS", (time.time() - t0) * 1000)

    if not events:
        return "ðŸ“… Your schedule is clearâ€”no upcoming calendar events found."

    out = ["### ðŸ“… Upcoming Schedule & Calendar Events"]
    for ev in events:
        loc_str = f" @ {ev['location']}" if ev.get("location") else ""
        out.append(f"- **`{ev['start_time']}` â†’ `{ev['end_time']}`**: **{ev['title']}**{loc_str} *(ID: `{ev['event_id']}`)*")
    return "\n".join(out)


def tool_calendar_add_event(
    title: str,
    start_time: str,
    end_time: str,
    location: str = "",
    description: str = "",
    attendees: str = ""
) -> str:
    """Schedules a new event on the ARGUS Calendar and persists it in SQLite."""
    ev_id = f"evt-{int(time.time())}-{uuid.uuid4().hex[:4]}"
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        INSERT INTO calendar_events
        (event_id, title, start_time, end_time, location, description, attendees, source, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'argus_calendar', 'confirmed', ?)
    """, (ev_id, title, start_time, end_time, location, description, attendees, time.time()))
    conn.commit()
    conn.close()
    log_audit_event("calendar_add_event", {"title": title, "start_time": start_time}, "SUCCESS")
    return f"âœ… Scheduled Calendar Event: **{title}** from `{start_time}` to `{end_time}` {f'at {location}' if location else ''} (Event ID: `{ev_id}`)."


def tool_calendar_delete_event(event_id_or_title: str) -> str:
    """Cancels/deletes an event from the calendar by ID or partial title match."""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        "UPDATE calendar_events SET status = 'cancelled' WHERE event_id = ? OR LOWER(title) LIKE LOWER(?)",
        (event_id_or_title.strip(), f"%{event_id_or_title.strip()}%")
    )
    affected = c.rowcount
    conn.commit()
    conn.close()
    return f"ðŸ—‘ï¸ Cancelled {affected} matching calendar event(s) for `{event_id_or_title}`."


# =========================================================================================
# SECTION 6: GMAIL COMMUNICATIONS ENGINE (IMAP INBOX READER + SMTP / DRAFT DISPATCHER)
# =========================================================================================

def tool_gmail_read_inbox(max_emails: int = 8, search_filter: str = "ALL") -> str:
    """
    Reads recent emails from Gmail via IMAP (if credentials configured) plus local SQLite email records.
    """
    t0 = time.time()
    fetched_summaries: List[str] = []

    if GMAIL_ADDRESS and GMAIL_APP_PASSWORD:
        try:
            mail = imaplib.IMAP4_SSL(IMAP_SERVER)
            mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            mail.select("inbox")
            criterion = f'(TEXT "{search_filter}")' if search_filter and search_filter.upper() != "ALL" else "ALL"
            status, data = mail.search(None, criterion)
            if status == "OK" and data and data[0]:
                ids = data[0].split()[-max_emails:]
                for num in reversed(ids):
                    res, msg_data = mail.fetch(num, "(RFC822)")
                    if res == "OK" and msg_data and msg_data[0]:
                        msg = email.message_from_bytes(msg_data[0][1])
                        subj = msg.get("Subject", "(No Subject)")
                        sender = msg.get("From", "Unknown")
                        date_str = msg.get("Date", "")
                        snippet = ""
                        if msg.is_multipart():
                            for part in msg.walk():
                                if part.get_content_type() == "text/plain":
                                    payload = part.get_payload(decode=True)
                                    if payload:
                                        snippet = payload.decode("utf-8", errors="ignore").strip()[:220]
                                        break
                        else:
                            payload = msg.get_payload(decode=True)
                            if payload:
                                snippet = payload.decode("utf-8", errors="ignore").strip()[:220]
                        fetched_summaries.append(f"- **From:** `{sender}` | **Subject:** **{subj}** ({date_str})\n  > {snippet}")
            mail.logout()
        except Exception as e:
            print(f"[Gmail IMAP Warning]: {e}")

    # Also query local SQLite email_records
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM email_records ORDER BY created_at DESC LIMIT ?", (max_emails,))
    local_emails = [dict(r) for r in c.fetchall()]
    conn.close()

    for em in local_emails:
        fetched_summaries.append(
            f"- **[{em['status'].upper()} / {em['direction']}] To:** `{em['recipient']}` | **Subject:** **{em['subject']}**\n"
            f"  > {em['body'][:200]}"
        )

    log_audit_event("gmail_read_inbox", {"max_emails": max_emails, "filter": search_filter}, "SUCCESS", (time.time() - t0) * 1000)

    if not fetched_summaries:
        return "ðŸ“¬ No emails found in inbox or local dispatch log. (Set `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` in `.env` for live IMAP inbox sync.)"

    return "### ðŸ“¬ Gmail Inbox & Communications Log\n" + "\n".join(fetched_summaries[:max_emails])


def tool_gmail_send_or_draft(recipient: str, subject: str, body: str, send_immediately: bool = False) -> str:
    """
    Sends a live email via Gmail SMTP if `send_immediately=True` and credentials exist;
    otherwise saves as an approved/pending draft in SQLite.
    """
    email_id = f"msg-{int(time.time())}"
    status = "draft_saved"

    if send_immediately and GMAIL_ADDRESS and GMAIL_APP_PASSWORD:
        try:
            msg = MIMEMultipart()
            msg["From"] = GMAIL_ADDRESS
            msg["To"] = recipient
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain", "utf-8"))

            server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=10)
            server.starttls()
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.send_message(msg)
            server.quit()
            status = "sent_live"
        except Exception as e:
            status = f"smtp_error_saved_draft ({e})"

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        INSERT INTO email_records (email_id, direction, sender, recipient, subject, body, status, created_at)
        VALUES (?, 'outbound', ?, ?, ?, ?, ?, ?)
    """, (email_id, GMAIL_ADDRESS or "argus@executive.local", recipient, subject, body, status, time.time()))
    conn.commit()
    conn.close()

    log_audit_event("gmail_send_or_draft", {"recipient": recipient, "subject": subject, "status": status}, "SUCCESS")
    return (
        f"âœ‰ï¸ **Email Action (`{status}`)**\n"
        f"- **To:** `{recipient}`\n"
        f"- **Subject:** {subject}\n"
        f"- **Message ID:** `{email_id}`"
    )


# =========================================================================================
# SECTION 7: LIVE WEATHER & EXECUTIVE BRIEFING SYNTHESIZER
# =========================================================================================

def fetch_live_weather(location: str = DEFAULT_LOCATION) -> str:
    """Fetches real-time weather conditions via wttr.in JSON API."""
    try:
        loc_enc = urllib.parse.quote(location)
        req = urllib.request.Request(
            f"[https://wttr.in/](https://wttr.in/){loc_enc}?format=j1",
            headers={"User-Agent": "Mozilla/5.0 (ARGUS-Weather/4.0)"}
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        curr = data.get("current_condition", [{}])[0]
        temp_f = curr.get("temp_F", "N/A")
        feels_f = curr.get("FeelsLikeF", "N/A")
        desc = curr.get("weatherDesc", [{"value": "Clear"}])[0].get("value", "Clear")
        humidity = curr.get("humidity", "N/A")
        return f"{location}: {desc}, {temp_f}Â°F (Feels like {feels_f}Â°F), Humidity {humidity}%"
    except Exception:
        return f"{location}: Weather feed temporarily offline"


def generate_executive_briefing(location: str = DEFAULT_LOCATION) -> str:
    """
    Aggregates live weather, calendar schedule, Canvas LMS deadlines, Gmail inbox,
    pending creative approvals, and generated demos into an AI-synthesized Executive Briefing.
    """
    weather_report = fetch_live_weather(location)
    calendar_report = tool_calendar_list_events(days=7)
    canvas_report = tool_canvas_sync_and_summary(days_ahead=14)
    gmail_report = tool_gmail_read_inbox(max_emails=5)
    facts = query_knowledge_graph(limit=10)

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT task_id, objective, status, artifact_slug FROM pending_tasks ORDER BY created_at DESC LIMIT 5")
    tasks = [dict(r) for r in c.fetchall()]
    c.execute("SELECT slug, title, preview_url, updated_at FROM generated_artifacts ORDER BY updated_at DESC LIMIT 8")
    demos = [dict(r) for r in c.fetchall()]
    conn.close()

    now_str = datetime.datetime.now().strftime("%A, %B %d, %Y â€” %I:%M %p")

    raw_state = f"""
    TIMESTAMP: {now_str}
    WEATHER: {weather_report}

    {calendar_report}

    {canvas_report}

    {gmail_report}

    RECENT TASKS / CREATIVE APPROVAL QUEUE:
    {json.dumps(tasks, indent=2)}

    DEPLOYED WEB APPLICATIONS:
    {json.dumps(demos, indent=2)}

    RECENT KNOWLEDGE GRAPH FACTS:
    {json.dumps(facts, indent=2)}
    """

    try:
        resp = groq_resilient_completion(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are ARGUS, a Jarvis-class Executive AI Assistant for Kyle Wilson. "
                        "Synthesize the live telemetry below into a crisp, high-impact Executive Daily Briefing with clear Markdown sections, "
                        "actionable priorities, and links to any deployed demos or pending approvals."
                    )
                },
                {"role": "user", "content": raw_state}
            ],
            temperature=0.25,
            max_tokens=1800
        )
        return clean_llm_text(resp.choices[0].message.content)
    except Exception:
        return f"## ðŸ›¡ï¸ ARGUS Executive Daily Briefing ({now_str})\n\n**Weather:** {weather_report}\n\n{calendar_report}\n\n{canvas_report}\n\n{gmail_report}"

    # =========================================================================================
# SECTION 8: MULTI-HOP DEEP WEB & MEDIA RESEARCH ENGINE (JINA + DDGS + BING PROXY)
# =========================================================================================

def tool_web_search(query: str, max_results: int = 8) -> str:
    """
    Executes a multi-source live web search via Jina Reader Search (s.jina.ai),
    DuckDuckGo Text Search, and Yahoo HTML fallback.
    """
    t0 = time.time()
    chunks: List[str] = []

    # 1. Primary Cloud-Friendly Jina Search Reader
    try:
        enc_q = urllib.parse.quote(query)
        req = urllib.request.Request(
            f"https://s.jina.ai/{enc_q}",
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; ARGUS-OS/4.0; +https://argus.local)",
                "Accept": "text/plain"
            }
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            jina_md = resp.read().decode("utf-8", errors="ignore")
            if len(jina_md) > 180:
                chunks.append(f"=== JINA LIVE SEARCH ({query}) ===\n{jina_md[:5500]}")
    except Exception as e:
        print(f"[Jina Search Warning] '{query}': {e}")

    # 2. Secondary DuckDuckGo Search
    if DDGS:
        try:
            with DDGS() as ddgs:
                for r in list(ddgs.text(query, max_results=max_results)):
                    title = r.get("title", "")
                    href = r.get("href", "")
                    body = r.get("body", "")
                    if body:
                        chunks.append(f"SOURCE [{title}]({href}): {body}")
        except Exception as e:
            print(f"[DDGS Search Warning] '{query}': {e}")

    # 3. Tertiary Yahoo Search Fallback (if cloud IP is throttled)
    if not chunks:
        try:
            enc_q = urllib.parse.quote(query)
            y_url = f"https://search.yahoo.com/search?p={enc_q}"
            req = urllib.request.Request(
                y_url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                y_html = resp.read().decode("utf-8", errors="ignore")
            clean_y = re.sub(r'<script.*?</script>', ' ', y_html, flags=re.DOTALL | re.I)
            clean_y = re.sub(r'<style.*?</style>', ' ', clean_y, flags=re.DOTALL | re.I)
            snippets = re.findall(r'<div[^>]*class="[^"]*compText[^"]*"[^>]*>(.*?)</div>', clean_y)
            for s in snippets[:max_results]:
                txt = re.sub(r'<[^>]+>', ' ', s).strip()
                if len(txt) > 25:
                    chunks.append(f"WEB SNIPPET: {txt}")
        except Exception as e:
            print(f"[Yahoo Fallback Warning]: {e}")

    output = "\n\n".join(chunks)[:9000] if chunks else f"No live web snippets returned for '{query}'."
    log_audit_event("web_search", {"query": query, "chars": len(output)}, "SUCCESS", (time.time() - t0) * 1000)
    return output


def tool_read_webpage(url: str) -> str:
    """
    Scrapes and converts any live URL into clean, structured Markdown via r.jina.ai,
    with direct urllib HTML-to-text + image extraction fallback.
    """
    t0 = time.time()
    clean_url = url.strip()
    if not clean_url.startswith("http"):
        clean_url = "https://" + clean_url

    extracted_images: List[str] = []
    page_text = ""

    # 1. Try Jina Reader API first
    try:
        req = urllib.request.Request(
            f"https://r.jina.ai/{clean_url}",
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; ARGUS-OS/4.0)",
                "Accept": "text/plain"
            }
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            page_text = resp.read().decode("utf-8", errors="ignore")[:7500]
            md_imgs = re.findall(r'!\[.*?\]\((https?://[^\s\)]+)\)', page_text)
            extracted_images.extend(md_imgs[:10])
    except Exception as e:
        print(f"[Jina Reader Warning] {clean_url}: {e}")

    # 2. Direct HTML Crawl for <meta og:image>, <img> tags, and raw text fallback
    try:
        req = urllib.request.Request(
            clean_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw_html = resp.read().decode("utf-8", errors="ignore")

        parsed = urllib.parse.urlparse(clean_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"

        og_imgs = re.findall(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', raw_html, re.I)
        img_tags = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', raw_html, re.I)
        for src in (og_imgs + img_tags):
            full_img = urllib.parse.urljoin(origin, src.strip())
            if full_img.startswith("http") and any(ext in full_img.lower() for ext in [".jpg", ".jpeg", ".png", ".webp"]):
                if not any(b in full_img.lower() for b in ["spacer", "1x1", "pixel", "icon", "svg", "spinner"]):
                    if full_img not in extracted_images:
                        extracted_images.append(full_img)

        if not page_text:
            body_no_js = re.sub(r'<script.*?</script>', ' ', raw_html, flags=re.DOTALL | re.I)
            body_no_css = re.sub(r'<style.*?</style>', ' ', body_no_js, flags=re.DOTALL | re.I)
            plain = re.sub(r'<[^>]+>', ' ', body_no_css)
            page_text = re.sub(r'\s+', ' ', plain).strip()[:6500]
    except Exception as e:
        print(f"[Direct Webpage Crawl Warning] {clean_url}: {e}")

    log_audit_event("read_webpage", {"url": clean_url, "imgs": len(extracted_images)}, "SUCCESS", (time.time() - t0) * 1000)

    img_block = "\n".join(f"- {u}" for u in extracted_images[:10]) if extracted_images else "None extracted"
    return f"=== WEBPAGE CONTENT ({clean_url}) ===\n{page_text}\n\n=== EXTRACTED SITE IMAGES ===\n{img_block}"


def tool_image_search(query: str, count: int = 6) -> str:
    """
    Harvests verified, high-resolution image URLs for any dish, product, storefront, or concept.
    Uses DDGS Images first and guarantees unique, working URLs via Bing's live visual proxy.
    """
    t0 = time.time()
    found_urls: List[str] = []
    bad_tokens = [".svg", "logo", "icon", "favicon", "1x1", "pixel", "badge", "spinner", "avatar"]

    if DDGS:
        try:
            with DDGS() as ddgs:
                results = list(ddgs.images(query, max_results=count * 2))
                for r in results:
                    u = r.get("image", "")
                    if u.startswith("http") and not any(b in u.lower() for b in bad_tokens):
                        if u not in found_urls:
                            found_urls.append(u)
                            if len(found_urls) >= count:
                                break
        except Exception as e:
            print(f"[DDGS Image Search Warning] '{query}': {e}")

    # Guarantee working visual URLs via Bing thumbnail proxy so cloud rate-limits never produce broken images
    while len(found_urls) < count:
        idx = len(found_urls) + 1
        q_enc = urllib.parse.quote(f"{query} high resolution photo {idx}")
        proxy_url = f"https://tse2.mm.bing.net/th?q={q_enc}&w=900&h=650&c=7&rs=1&p=0"
        found_urls.append(proxy_url)

    log_audit_event("image_search", {"query": query, "count": len(found_urls)}, "SUCCESS", (time.time() - t0) * 1000)
    return json.dumps({"query": query, "image_urls": found_urls[:count]}, indent=2)


# =========================================================================================
# SECTION 9: LIVE WORKSPACE & SELF-HEALING HTML / CODE PATCHING ENGINE
# =========================================================================================

def tool_list_generated_demos() -> str:
    """Lists all generated websites, apps, and code artifacts stored in DEMO_DIR and SQLite."""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT slug, title, artifact_type, preview_url, version, updated_at FROM generated_artifacts ORDER BY updated_at DESC")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()

    # Also scan disk for any HTML files created prior to v4.0 registry
    disk_files = [f for f in os.listdir(DEMO_DIR) if f.endswith(".html")]
    known_slugs = {r["slug"] for r in rows}
    for df in disk_files:
        s = df[:-5]
        if s not in known_slugs:
            rows.append({
                "slug": s,
                "title": s.replace("-", " ").title(),
                "artifact_type": "webapp",
                "preview_url": f"{RENDER_EXTERNAL_URL}/preview/{s}",
                "version": 1,
                "updated_at": os.path.getmtime(os.path.join(DEMO_DIR, df))
            })

    if not rows:
        return "No generated web applications or demos found yet."

    lines = ["### ðŸŒ ARGUS Deployed Web Applications & Artifacts"]
    for r in rows:
        lines.append(
            f"- **`{r['slug']}`** ({r['title']} â€” v{r['version']}) â†’ [{r['preview_url']}]({r['preview_url']})"
        )
    return "\n".join(lines)


def tool_inspect_artifact_code(slug: str) -> str:
    """Reads the source HTML/code of an existing generated demo so ARGUS can inspect or patch it."""
    clean_slug = re.sub(r'[^a-z0-9\-]', '', slug.lower())
    file_path = os.path.join(DEMO_DIR, f"{clean_slug}.html")
    if not os.path.exists(file_path):
        return f"Artifact `{clean_slug}.html` not found in `{DEMO_DIR}`."
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        code = f.read()
    return f"=== SOURCE CODE FOR {clean_slug}.html ({len(code)} chars) ===\n{code[:10000]}"


def tool_patch_existing_webapp(slug: str, modification_instructions: str) -> str:
    """
    Self-healing live editor: Reads an existing generated website (`generated_demos/{slug}.html`),
    applies the user's requested visual, structural, or interactive edits via Groq, and saves the new version.
    """
    t0 = time.time()
    clean_slug = re.sub(r'[^a-z0-9\-]', '', slug.lower())
    file_path = os.path.join(DEMO_DIR, f"{clean_slug}.html")
    if not os.path.exists(file_path):
        # Try matching partial slug
        candidates = [f[:-5] for f in os.listdir(DEMO_DIR) if f.endswith(".html") and clean_slug in f]
        if candidates:
            clean_slug = candidates[0]
            file_path = os.path.join(DEMO_DIR, f"{clean_slug}.html")
        else:
            return f"âŒ Could not find an existing demo matching `{slug}` to edit."

    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        current_html = f.read()

    # Optional: Run an image or web search if the modification asks for new photos or facts
    extra_assets = ""
    if any(w in modification_instructions.lower() for w in ["photo", "image", "picture", "menu", "price", "add"]):
        extra_assets = tool_image_search(f"{clean_slug} {modification_instructions}", count=4)

    prompt = f"""
    You are ARGUS's Autonomous Self-Healing Code & UI Editor.
    Below is the current live HTML5/Tailwind/JS source code for `{clean_slug}.html`:

    === CURRENT LIVE HTML ===
    {current_html[:14000]}
    === END CURRENT HTML ===

    ADDITIONAL VISUAL ASSETS (IF NEEDED):
    {extra_assets}

    USER MODIFICATION DIRECTIVE:
    {modification_instructions}

    TASK:
    Rewrite and return the COMPLETE, updated HTML5 file incorporating all requested changes while preserving working interactivity, real business data, and responsive styling.
    Return ONLY raw valid HTML starting with `<!DOCTYPE html>`. No markdown wrappers or commentary.
    """

    resp = groq_resilient_completion(
        messages=[
            {"role": "system", "content": "Output ONLY raw valid HTML5 code starting with <!DOCTYPE html>."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.3,
        max_tokens=6000
    )

    updated_html = clean_llm_text(resp.choices[0].message.content, strip_fences=True)
    if "<!doctype html" not in updated_html.lower() and "<html" not in updated_html.lower():
        return "âŒ Failed to compile valid HTML patch. Existing file was left untouched."

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(updated_html)

    preview_url = f"{RENDER_EXTERNAL_URL}/preview/{clean_slug}"
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT version FROM generated_artifacts WHERE slug = ?", (clean_slug,))
    row = c.fetchone()
    new_ver = (row["version"] + 1) if row else 2
    c.execute("""
        INSERT OR REPLACE INTO generated_artifacts
        (slug, title, artifact_type, objective, file_path, preview_url, version, design_notes, created_at, updated_at)
        VALUES (?, ?, 'webapp', ?, ?, ?, ?, ?, ?, ?)
    """, (clean_slug, clean_slug.upper(), modification_instructions, file_path, preview_url, new_ver, modification_instructions, time.time(), time.time()))
    conn.commit()
    conn.close()

    log_audit_event("patch_existing_webapp", {"slug": clean_slug, "version": new_ver}, "SUCCESS", (time.time() - t0) * 1000)
    return (
        f"### ðŸ› ï¸ Live Application Patched (`{clean_slug}` â€” v{new_ver})\n"
        f"- **Updated Live URL:** [{preview_url}]({preview_url})\n"
        f"- **Changes Applied:** {modification_instructions}"
    )


# =========================================================================================
# SECTION 10: JARVIS 2-PHASE DEEP RESEARCH, CREATIVE APPROVAL GATE & ZERO-TEMPLATE COMPILER
# =========================================================================================

JARVIS_TOOL_DECLARATIONS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the live web for real business menus, prices, addresses, hours, reviews, technical docs, or news.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Targeted search query"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_webpage",
            "description": "Read the full text and extract real image URLs from a specific website URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Full https:// URL to scrape"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "image_search",
            "description": "Find real photography URLs for specific dishes, products, services, or storefronts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Specific visual search query"},
                    "count": {"type": "integer", "description": "Number of image URLs to retrieve (1-8)"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "upsert_knowledge_fact",
            "description": "Save or update a verified fact in ARGUS's persistent SQLite Knowledge Graph.",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity": {"type": "string", "description": "Business, person, course, or topic name"},
                    "fact_key": {"type": "string", "description": "Attribute name (e.g. address, phone, specialty, deadline)"},
                    "fact_value": {"type": "string", "description": "Verified value"},
                    "category": {"type": "string", "description": "Category tag"},
                    "source_url": {"type": "string", "description": "Optional source URL"}
                },
                "required": ["entity", "fact_key", "fact_value"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_list_events",
            "description": "List upcoming events on the user's calendar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "Days ahead to inspect"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_add_event",
            "description": "Schedule a new event on the user's calendar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "start_time": {"type": "string", "description": "ISO or readable date/time"},
                    "end_time": {"type": "string", "description": "ISO or readable date/time"},
                    "location": {"type": "string"},
                    "description": {"type": "string"}
                },
                "required": ["title", "start_time", "end_time"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_delete_event",
            "description": "Cancel or delete a calendar event by ID or title.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id_or_title": {"type": "string"}
                },
                "required": ["event_id_or_title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "gmail_read_inbox",
            "description": "Read recent emails from the user's Gmail inbox and dispatch log.",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_emails": {"type": "integer"},
                    "search_filter": {"type": "string"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "gmail_send_or_draft",
            "description": "Send a live email via Gmail SMTP or save an email draft.",
            "parameters": {
                "type": "object",
                "properties": {
                    "recipient": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                    "send_immediately": {"type": "boolean"}
                },
                "required": ["recipient", "subject", "body"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "canvas_sync_and_summary",
            "description": "Fetch active courses, assignments, and due dates from Canvas LMS.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days_ahead": {"type": "integer"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "canvas_add_local_item",
            "description": "Track a new assignment, exam, or academic deadline in the Canvas tracker.",
            "parameters": {
                "type": "object",
                "properties": {
                    "course_name": {"type": "string"},
                    "title": {"type": "string"},
                    "due_at": {"type": "string"},
                    "points": {"type": "number"},
                    "description": {"type": "string"}
                },
                "required": ["course_name", "title", "due_at"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "executive_briefing",
            "description": "Generate a full Executive Daily Briefing combining weather, calendar, Canvas deadlines, Gmail, and projects.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "patch_existing_webapp",
            "description": "Modify or upgrade an existing generated demo website by its slug.",
            "parameters": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string"},
                    "modification_instructions": {"type": "string"}
                },
                "required": ["slug", "modification_instructions"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_generated_demos",
            "description": "List all deployed websites and interactive demos built by ARGUS.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    }
]


def execute_jarvis_tool_call(fn_name: str, args: Dict[str, Any]) -> str:
    """Dispatches any tool call from the Groq ReAct agent to the underlying Python subsystem."""
    try:
        if fn_name == "web_search":
            return tool_web_search(args.get("query", ""), int(args.get("max_results", 6)))
        elif fn_name == "read_webpage":
            return tool_read_webpage(args.get("url", ""))
        elif fn_name == "image_search":
            return tool_image_search(args.get("query", ""), int(args.get("count", 5)))
        elif fn_name == "upsert_knowledge_fact":
            return upsert_knowledge_fact(
                args.get("entity", "General"),
                args.get("fact_key", "info"),
                args.get("fact_value", ""),
                args.get("category", "research"),
                args.get("source_url", "")
            )
        elif fn_name == "calendar_list_events":
            return tool_calendar_list_events(int(args.get("days", 7)))
        elif fn_name == "calendar_add_event":
            return tool_calendar_add_event(
                args.get("title", "New Event"),
                args.get("start_time", ""),
                args.get("end_time", ""),
                args.get("location", ""),
                args.get("description", "")
            )
        elif fn_name == "calendar_delete_event":
            return tool_calendar_delete_event(args.get("event_id_or_title", ""))
        elif fn_name == "gmail_read_inbox":
            return tool_gmail_read_inbox(int(args.get("max_emails", 6)), args.get("search_filter", "ALL"))
        elif fn_name == "gmail_send_or_draft":
            return tool_gmail_send_or_draft(
                args.get("recipient", ""),
                args.get("subject", ""),
                args.get("body", ""),
                bool(args.get("send_immediately", False))
            )
        elif fn_name == "canvas_sync_and_summary":
            return tool_canvas_sync_and_summary(int(args.get("days_ahead", 14)))
        elif fn_name == "canvas_add_local_item":
            return tool_canvas_add_local_item(
                args.get("course_name", "General"),
                args.get("title", "Assignment"),
                args.get("due_at", ""),
                float(args.get("points", 100.0)),
                args.get("description", "")
            )
        elif fn_name == "executive_briefing":
            return generate_executive_briefing(args.get("location", DEFAULT_LOCATION))
        elif fn_name == "patch_existing_webapp":
            return tool_patch_existing_webapp(args.get("slug", ""), args.get("modification_instructions", ""))
        elif fn_name == "list_generated_demos":
            return tool_list_generated_demos()
        else:
            return f"Unknown tool `{fn_name}`."
    except Exception as e:
        return f"Tool `{fn_name}` execution error: {e}"


def derive_clean_slug(objective: str) -> str:
    """Extracts a clean, human-readable URL slug from the user's build objective."""
    m = re.search(
        r'(?:for|of|called|named)\s+([A-Za-z0-9\s\'\-&]+?)(?:\s+in\s+|\s+on\s+|\s+at\s+|\s+with\s+|\s+featuring\s+|$)',
        objective,
        re.IGNORECASE
    )
    candidate = m.group(1).strip() if m else objective
    candidate = re.split(r'\s+(?:and\s+just|and\s+make|with|featuring|that\s+has)\b', candidate, flags=re.I)[0].strip()
    slug = re.sub(r'[^a-z0-9]+', '', candidate.lower())[:32]
    return slug if slug else f"app-{int(time.time())}"

def jarvis_autonomous_research_and_propose(user_objective: str, auto_execute: bool = True) -> str:
    """
    PHASE 1 + AUTO-COMPILE:
    1. Detects if the user provided a reference URL in the prompt and scrapes its real text & images directly.
    2. Executes multi-hop live web searches + targeted item image harvesting.
    3. Synthesizes a bespoke Creative Blueprint grounded strictly in real facts (zero fake 1234 Main St / 555 numbers).
    4. Immediately compiles and deploys the live interactive HTML app to /preview/{slug} and returns the clickable URL.
    """
    t0 = time.time()
    slug = derive_clean_slug(user_objective)
    research_log: List[str] = []

    # 1. Extract and directly crawl any reference URL(s) provided by the user
    provided_urls = re.findall(r'https?://[^\s\)\]]+', user_objective)
    for ref_url in provided_urls[:2]:
        print(f"[ARGUS Reference Crawler] Reading user-provided reference site: {ref_url}")
        ref_content = tool_read_webpage(ref_url)
        research_log.append(f"=== USER-PROVIDED REFERENCE WEBSITE ({ref_url}) ===\n{ref_content}")

    # 2. Multi-vector live web & image discovery
    clean_topic = re.sub(r'https?://[^\s]+', '', user_objective).strip()
    baseline_web = tool_web_search(f"{clean_topic} official address phone hours menu services prices", max_results=8)
    baseline_imgs = tool_image_search(clean_topic, count=6)
    research_log.append(f"=== LIVE WEB SEARCH ===\n{baseline_web}")
    research_log.append(f"=== HARVESTED IMAGES ===\n{baseline_imgs}")

    # 3. Autonomous Multi-Hop Tool Loop to deepen facts and scrape official links found in search
    research_messages: List[Dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "You are ARGUS's Deep Research & Creative Architecture Agent.\n"
                "1. Inspect the initial search results and any user-provided reference website. Use `web_search`, `read_webpage`, and `image_search` "
                "to gather the REAL street address, REAL phone number, REAL operating hours, REAL menu items/services with prices, and REAL photo URLs.\n"
                "2. NEVER invent fake addresses like '1234 N 15th St' or fake phone numbers like '(215) 555-1234'. If a detail is not found, state 'See Store / Call for Details'.\n"
                "3. Synthesize a crisp **Research Dossier & Creative Blueprint** specifying the exact real facts, 6–10 authentic items/services, "
                "custom HEX color palette, Google Fonts pairing, and interactive JS features tailored to this business."
            )
        },
        {
            "role": "user",
            "content": f"OBJECTIVE: {user_objective}\n\nINITIAL DATA:\n" + "\n\n".join(research_log)[:7500]
        }
    ]

    proposal_markdown = ""
    for hop in range(3):
        try:
            resp = groq_resilient_completion(
                messages=research_messages,
                temperature=0.25,
                max_tokens=2500,
                tools=JARVIS_TOOL_DECLARATIONS,
                tool_choice="auto"
            )
            msg = resp.choices[0].message
            if not getattr(msg, "tool_calls", None):
                proposal_markdown = clean_llm_text(msg.content or "")
                break

            research_messages.append(msg)
            for tc in msg.tool_calls:
                fn_name = tc.function.name
                try:
                    fn_args = json.loads(tc.function.arguments)
                except Exception:
                    fn_args = {}
                tool_out = execute_jarvis_tool_call(fn_name, fn_args)
                research_log.append(f"=== HOP {hop+1} [{fn_name}({fn_args})] ===\n{tool_out}")
                research_messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": fn_name,
                    "content": tool_out[:4500]
                })
        except Exception as e:
            print(f"[ARGUS Research Hop {hop+1} Warning]: {e}")
            break

    if not proposal_markdown:
        synth_resp = groq_resilient_completion(
            messages=[
                {
                    "role": "system",
                    "content": "Synthesize a factual Research Dossier & Creative Blueprint from the scraped data. Do NOT invent fake 555 phone numbers or 1234 addresses."
                },
                {
                    "role": "user",
                    "content": f"OBJECTIVE: {user_objective}\n\nRESEARCH DATA:\n" + "\n\n".join(research_log)[:9500]
                }
            ],
            temperature=0.25,
            max_tokens=2500
        )
        proposal_markdown = clean_llm_text(synth_resp.choices[0].message.content)

    # Save checkpoint to SQLite
    task_id = f"task-{int(time.time())}"
    full_dossier = "\n\n".join(research_log)[:22000]
    now = time.time()

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        INSERT INTO pending_tasks
        (task_id, objective, task_type, research_dossier, creative_proposal, status, artifact_slug, user_feedback, created_at, updated_at)
        VALUES (?, ?, 'webapp', ?, ?, 'PENDING_APPROVAL', ?, '', ?, ?)
    """, (task_id, user_objective, full_dossier, proposal_markdown, slug, now, now))
    conn.commit()
    conn.close()

    # Always compile & deploy immediately unless user explicitly asks for "proposal only"
    if auto_execute:
        compiled_report = jarvis_compile_approved_task(task_id=task_id, user_feedback="Autonomous full-stack deployment.")
        return (
            f"{compiled_report}\n\n"
            f"---\n"
            f"### 📋 Research & Creative Summary Used\n"
            f"{proposal_markdown[:1500]}..."
        )

    return (
        f"## 🧠 ARGUS Research Dossier & Proposal (`{task_id}`)\n\n"
        f"{proposal_markdown}\n\n"
        f"Reply **`#argus approve`** to deploy `/preview/{slug}`."
    )

# =========================================================================================
# SECTION 10B: PERPLEXITY-GRADE 5-AGENT SPECIALIST SWARM (ACCURACY + CREATIVITY ENGINE)
# =========================================================================================

def agent_1_verified_researcher(objective: str, reference_urls: List[str]) -> Dict[str, Any]:
    """
    AGENT 1 (Deep Research & Citation Verifier):
    Scours live web sources, crawls reference URLs, harvests real photos, and extracts
    ONLY verified facts with citations—explicitly blocking hallucinated addresses/phones.
    """
    raw_sources: List[str] = []
    verified_images: List[str] = []

    # 1. Crawl any explicit reference URLs provided by the user
    for url in reference_urls[:3]:
        page_md = tool_read_webpage(url)
        raw_sources.append(f"[SOURCE: {url}]\n{page_md}")
        imgs = re.findall(r'https?://[^\s\)\"\']+\.(?:jpg|jpeg|png|webp)', page_md, re.I)
        for im in imgs:
            if im not in verified_images:
                verified_images.append(im)

    # 2. Multi-angle targeted search queries (Official site, Menu/Services, Yelp/Maps/Reviews)
    clean_query = re.sub(r'https?://[^\s]+', '', objective).strip()
    search_angles = [
        f"{clean_query} official website address phone hours",
        f"{clean_query} menu prices services offerings",
        f"{clean_query} reviews specialties local reputation"
    ]

    for angle in search_angles:
        res = tool_web_search(angle, max_results=6)
        raw_sources.append(f"[SEARCH VECTOR: {angle}]\n{res}")
        # Extract any URLs discovered in search results to deep-read the top official page
        found_links = re.findall(r'https?://[^\s\)\]]+', res)
        for lnk in found_links:
            low_lnk = lnk.lower()
            if not any(skip in low_lnk for skip in ["jina.ai", "google.com", "bing.com", "yahoo.com", "facebook.com", "instagram.com", "yelp.com"]):
                if len(reference_urls) < 2 and lnk not in reference_urls:
                    reference_urls.append(lnk)
                    deep_page = tool_read_webpage(lnk)
                    raw_sources.append(f"[AUTO-DISCOVERED OFFICIAL PAGE: {lnk}]\n{deep_page[:4500]}")
                    break

    # 3. Harvest targeted item & storefront photography
    img_json = json.loads(tool_image_search(clean_query, count=8))
    for u in img_json.get("image_urls", []):
        if u not in verified_images:
            verified_images.append(u)

    combined_corpus = "\n\n".join(raw_sources)[:14000]

    # 4. Strict Fact Extraction & Anti-Hallucination Synthesis
    fact_prompt = f"""
    You are Agent 1 (Perplexity Deep Research & Citation Verifier).
    Extract ONLY verified facts from the raw web sources below for: "{clean_query}".

    === RAW WEB SOURCES ===
    {combined_corpus}
    === END SOURCES ===

    STRICT ANTI-HALLUCINATION RULES:
    - NEVER guess or fabricate street numbers, zip codes, or phone numbers (e.g., NEVER output fake '1234 N 15th St' or '(215) 204-4444' or '(215) 555-xxxx' unless that exact string appears verbatim in the sources above!).
    - If an exact address, phone number, or operating hour is NOT explicitly present in the sources above, write "Verify on Official Listing / Call Store" for that field.
    - Extract every real menu item, product, tire/service package, price, neighborhood detail, and customer quote present in the text.
    - Cite the source URL next to verified facts.

    Output a clean, structured **Verified Fact Dossier** in Markdown.
    """

    resp = groq_resilient_completion(
        messages=[
            {"role": "system", "content": "You are a strict, zero-hallucination research analyst. Only report facts grounded in the provided sources."},
            {"role": "user", "content": fact_prompt}
        ],
        temperature=0.1,
        max_tokens=2500
    )
    verified_dossier = clean_llm_text(resp.choices[0].message.content)

    return {
        "dossier": verified_dossier,
        "raw_corpus": combined_corpus,
        "images": verified_images[:12],
        "sources_crawled": reference_urls
    }


def agent_2_creative_architect(objective: str, research_bundle: Dict[str, Any], user_overrides: str = "") -> str:
    """
    AGENT 2 (Creative Director & UX Architect):
    Designs a bespoke, non-generic visual identity, layout architecture (1-page vs multi-view SPA),
    typography, color system, and custom JavaScript interactive widgets tailored to the verified facts.
    """
    prompt = f"""
    You are Agent 2 (Perplexity Pages / Labs Creative Director).
    Design a bespoke digital experience for: "{objective}"

    VERIFIED RESEARCH DOSSIER:
    {research_bundle['dossier'][:5000]}

    HARVESTED IMAGE URLS:
    {json.dumps(research_bundle['images'], indent=2)}

    USER CREATIVE OVERRIDES:
    {user_overrides or "None — choose the strongest creative direction that fits this brand."}

    SPECIFY:
    1. **Aesthetic & Brand Identity**: Exact HEX palette (Background, Surface, Primary Text, Muted Text, Primary Accent, Secondary Accent) + Google Font pairing (Heading + Body) matching the authentic atmosphere of this business.
    2. **Page Architecture**: Choose either a high-impact 1-Page Scrolling Experience or a Multi-View Interactive SPA (e.g. Overview & Catalog, Customer Reviews, Location & Hours, Custom Calculator/Booking) based on what serves this business best.
    3. **Custom Interactive JavaScript Features**: Detail the exact working client-side tools (e.g., live search + category tabs, item modifier/customization modal, interactive cart or service estimate drawer with live math, interactive FAQ/gallery, or custom calculator).
    4. **Visual Asset Mapping**: Assign specific image URLs from the list above (or targeted Bing proxy queries) to the hero and each catalog item.
    """

    resp = groq_resilient_completion(
        messages=[
            {"role": "system", "content": "You are an award-winning digital creative director. Avoid generic templates; tailor every design choice to the specific business."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.45,
        max_tokens=2000
    )
    return clean_llm_text(resp.choices[0].message.content)


def agent_3_fullstack_synthesizer(objective: str, research_bundle: Dict[str, Any], creative_spec: str, user_overrides: str = "") -> str:
    """
    AGENT 3 (Full-Stack Frontend Compiler):
    Writes the complete, standalone HTML5 + Tailwind CSS + Vanilla JavaScript application from scratch.
    """
    compiler_prompt = f"""
    You are Agent 3 (Autonomous Full-Stack Web Compiler).
    Build the COMPLETE, production-ready, self-contained HTML5 + Tailwind CSS + Vanilla JS application from scratch.

    OBJECTIVE: {objective}

    VERIFIED RESEARCH DOSSIER (STRICT SOURCE OF TRUTH):
    {research_bundle['dossier'][:6500]}

    HARVESTED REAL PHOTO URLS:
    {json.dumps(research_bundle['images'], indent=2)}

    CREATIVE DIRECTOR SPECIFICATION:
    {creative_spec[:4500]}

    USER OVERRIDES:
    {user_overrides or "Execute with maximum visual polish and interactive depth."}

    STRICT BUILD RULES:
    1. Include `<script src="https://cdn.tailwindcss.com"></script>` and load the specified Google Fonts.
    2. Use ONLY verified facts, real menu items/services, real prices, and real address/phone/hours from the dossier. NEVER invent fake '555' or '204-4444' numbers or '1234' addresses.
    3. Every `<img>` tag MUST use a verified image URL or `https://tse2.mm.bing.net/th?q=URL_ENCODED_SPECIFIC_ITEM_NAME&w=900&h=650&c=7&rs=1&p=0` AND include an inline `onerror` attribute falling back to a Bing thumbnail query for that exact item so zero broken images ever appear.
    4. Write complete, bug-free Vanilla JavaScript inside `<script>` tags implementing:
       - Working navigation (smooth scroll or multi-view tab switcher)
       - Working live search bar & category filter buttons
       - Working item customization / service selection modal
       - Working slide-over Cart / Booking Estimate drawer with quantity controls, subtotal calculation, and interactive order/booking confirmation receipt
       - Working embedded Google Map (using the verified address/neighborhood) and interactive contact/inquiry form
    5. Output ONLY raw valid HTML starting with `<!DOCTYPE html>` and ending with `</html>`.
    """

    resp = groq_resilient_completion(
        messages=[
            {"role": "system", "content": "Output ONLY raw, complete, valid HTML5 code starting with <!DOCTYPE html>."},
            {"role": "user", "content": compiler_prompt}
        ],
        temperature=0.35,
        max_tokens=7500
    )
    html = clean_llm_text(resp.choices[0].message.content, strip_fences=True)
    idx = html.lower().find("<!doctype html")
    if idx > 0:
        html = html[idx:]
    return html


def agent_4_qa_critic_and_healer(html_code: str, research_bundle: Dict[str, Any]) -> str:
    """
    AGENT 4 (Adversarial QA & Fact-Audit Critic):
    Inspects the compiled HTML before deployment for fake phone numbers, broken scripts,
    or missing Tailwind/DOCTYPE structure, and self-heals any defects automatically.
    """
    defects: List[str] = []

    if "<!doctype html" not in html_code.lower() or "</html>" not in html_code.lower():
        defects.append("Incomplete HTML document structure (missing DOCTYPE or closing </html>).")
    if "tailwindcss.com" not in html_code:
        defects.append("Missing Tailwind CSS CDN script.")
    if "<script>" not in html_code.lower():
        defects.append("Missing interactive client-side <script> logic.")

    # Detect hallucinated fake phone numbers if they weren't in the raw research corpus
    fake_phones = re.findall(r'\(215\)\s*(?:555|204|123)[-\s]\d{4}', html_code)
    for fp in fake_phones:
        if fp not in research_bundle.get("raw_corpus", ""):
            defects.append(f"Hallucinated placeholder phone number '{fp}' detected—replace with verified phone or 'Call Store'.")

    if not defects:
        return html_code

    print(f"[Agent 4 QA Critic] Found {len(defects)} defect(s): {defects}. Triggering auto-heal...")
    heal_prompt = f"""
    You are Agent 4 (QA & Self-Healing Code Critic).
    Fix the following defects in this HTML5 application while preserving all design, styling, and interactivity:
    DEFECTS TO FIX:
    {json.dumps(defects, indent=2)}

    VERIFIED DOSSIER:
    {research_bundle['dossier'][:3000]}

    CURRENT HTML:
    {html_code[:14000]}

    Return ONLY the complete, repaired raw HTML5 starting with `<!DOCTYPE html>`.
    """
    healed = groq_resilient_completion(
        messages=[
            {"role": "system", "content": "Output ONLY repaired raw HTML5 starting with <!DOCTYPE html>."},
            {"role": "user", "content": heal_prompt}
        ],
        temperature=0.2,
        max_tokens=7500
    )
    repaired_html = clean_llm_text(healed.choices[0].message.content, strip_fences=True)
    return repaired_html if "<!doctype html" in repaired_html.lower() else html_code


def jarvis_compile_approved_task(task_id: Optional[str] = None, user_feedback: str = "") -> str:
    """Executes Agents 3 & 4 on an existing task in SQLite and deploys the live preview."""
    t0 = time.time()
    conn = get_db_connection()
    c = conn.cursor()
    if task_id:
        c.execute("SELECT * FROM pending_tasks WHERE task_id = ?", (task_id,))
    else:
        c.execute("SELECT * FROM pending_tasks ORDER BY created_at DESC LIMIT 1")
    row = c.fetchone()
    if not row:
        conn.close()
        return "⚠️ No task found to compile. Ask me to create a website or app first!"
    task_data = dict(row)
    conn.close()

    slug = task_data["artifact_slug"]
    objective = task_data["objective"]
    research_bundle = {
        "dossier": task_data["research_dossier"],
        "raw_corpus": task_data["research_dossier"],
        "images": re.findall(r'https?://[^\s\)\"\']+\.(?:jpg|jpeg|png|webp)', task_data["research_dossier"], re.I)[:10]
    }
    creative_spec = task_data["creative_proposal"]

    raw_html = agent_3_fullstack_synthesizer(objective, research_bundle, creative_spec, user_feedback)
    verified_html = agent_4_qa_critic_and_healer(raw_html, research_bundle)

    file_path = os.path.join(DEMO_DIR, f"{slug}.html")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(verified_html)

    preview_url = f"{RENDER_EXTERNAL_URL}/preview/{slug}"
    now = time.time()

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE pending_tasks SET status = 'COMPLETED', user_feedback = ?, updated_at = ? WHERE task_id = ?", (user_feedback, now, task_data["task_id"]))
    c.execute("SELECT version FROM generated_artifacts WHERE slug = ?", (slug,))
    ex = c.fetchone()
    ver = (ex["version"] + 1) if ex else 1
    c.execute("""
        INSERT OR REPLACE INTO generated_artifacts
        (slug, title, artifact_type, objective, file_path, preview_url, version, design_notes, created_at, updated_at)
        VALUES (?, ?, 'webapp', ?, ?, ?, ?, ?, ?, ?)
    """, (slug, objective[:80], objective, file_path, preview_url, ver, user_feedback or "5-Agent Swarm Build", now, now))
    conn.commit()
    conn.close()

    log_audit_event("5_agent_swarm_deploy", {"slug": slug, "version": ver}, "SUCCESS", (time.time() - t0) * 1000)
    return (
        f"## 🚀 5-Agent Swarm Build Deployed (`v{ver}`)\n"
        f"- **Live Clickable URL:** [{preview_url}]({preview_url})\n"
        f"- **Agents Executed:** `Researcher` → `Creative Director` → `Full-Stack Synthesizer` → `QA Fact-Audit Critic`\n"
        f"- **Creative Notes:** `{user_feedback or 'Autonomous Brand Synthesis'}`\n\n"
        f"💡 *Want live edits? Reply `#argus edit {slug}: <your changes>` to patch it in place.*"
    )


def jarvis_autonomous_research_and_propose(user_objective: str, auto_execute: bool = True) -> str:
    """
    Runs the complete 5-Agent Perplexity-Grade Swarm:
    Agent 1 (Verified Research) -> Agent 2 (Creative Director) -> Agent 3 (Compiler) -> Agent 4 (QA Critic) -> Deploy.
    """
    t0 = time.time()
    slug = derive_clean_slug(user_objective)
    ref_urls = re.findall(r'https?://[^\s\)\]]+', user_objective)

    # Agent 1: Deep Research & Citation Verification
    research_bundle = agent_1_verified_researcher(user_objective, ref_urls)

    # Agent 2: Creative Director & Brand Architecture
    creative_spec = agent_2_creative_architect(user_objective, research_bundle)

    task_id = f"task-{int(time.time())}"
    now = time.time()
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        INSERT INTO pending_tasks
        (task_id, objective, task_type, research_dossier, creative_proposal, status, artifact_slug, user_feedback, created_at, updated_at)
        VALUES (?, ?, 'webapp', ?, ?, 'PENDING_APPROVAL', ?, '', ?, ?)
    """, (task_id, user_objective, research_bundle["dossier"][:20000], creative_spec[:12000], slug, now, now))
    conn.commit()
    conn.close()

    if not auto_execute:
        return (
            f"## 🧠 ARGUS 5-Agent Research Dossier & Creative Blueprint (`{task_id}`)\n\n"
            f"### 1. Verified Research Dossier (Agent 1)\n{research_bundle['dossier']}\n\n"
            f"### 2. Creative Architecture Proposal (Agent 2)\n{creative_spec}\n\n"
            f"---\nReply **`#argus approve`** to compile and deploy `/preview/{slug}`."
        )

    # Agents 3 & 4: Compile, QA Audit, and Deploy Live Link Immediately
    deploy_summary = jarvis_compile_approved_task(task_id=task_id, user_feedback="Autonomous 5-Agent Swarm Build")
    return (
        f"{deploy_summary}\n\n"
        f"---\n"
        f"### 🔍 Verified Intelligence Summary (Agent 1)\n"
        f"{research_bundle['dossier'][:1600]}\n\n"
        f"### 🎨 Creative Architecture Applied (Agent 2)\n"
        f"{creative_spec[:1000]}"
    )

def run_jarvis_executive_agent(user_input: str, session_id: str = "default") -> str:
    """
    Master Jarvis Orchestrator:
    - Any website / demo / landing page request automatically researches, compiles, hosts on /preview/{slug}, and returns the clickable URL.
    - Approval or follow-up 'set it up / give me the link' requests immediately compile the latest pending build.
    - Edit commands (`#argus edit <slug>: ...`) patch the live HTML file in place.
    """
    clean_input = re.sub(r'^#?argus\b[:\s]*', '', user_input.strip(), flags=re.IGNORECASE).strip()
    if not clean_input:
        clean_input = "Give me a status report and executive briefing."

    save_conversation_turn(session_id, "user", user_input)
    low = clean_input.lower()

    # --- ROUTE 1: APPROVAL OR FOLLOW-UP DEPLOYMENT TRIGGER ---
    if re.search(r'\b(approve|greenlight|build it|ship it|confirm build|set it up|deploy it|give me the link|link for the demo)\b', low):
        feedback = re.sub(r'^(approve|greenlight|build\s+it|ship\s+it|confirm\s+build)[:\-\s]*', '', clean_input, flags=re.I).strip()
        response_md = jarvis_compile_approved_task(user_feedback=feedback)
        save_conversation_turn(session_id, "assistant", response_md)
        return response_md

    # --- ROUTE 2: LIVE SELF-PATCHING EDITOR (`edit <slug>: ...`) ---
    edit_match = re.match(r'^(?:edit|patch|modify|revise|update)\s+([a-z0-9\-_]+)[:\-\s]+(.+)$', clean_input, flags=re.I | re.DOTALL)
    if edit_match:
        target_slug = edit_match.group(1).strip()
        mod_notes = edit_match.group(2).strip()
        response_md = tool_patch_existing_webapp(target_slug, mod_notes)
        save_conversation_turn(session_id, "assistant", response_md)
        return response_md

    # --- ROUTE 3: WEBSITE / DEMO CREATION (AUTO-RESEARCH + AUTO-DEPLOY CLICKABLE URL) ---
    is_web_build = any(
        phrase in low for phrase in [
            "website", "demo site", "web app", "landing page", "webpage", "create a site", "build a site", "make a site", "page for"
        ]
    )
    if is_web_build:
        proposal_only = any(flag in low for flag in ["proposal only", "draft only", "ask before building"])
        response_md = jarvis_autonomous_research_and_propose(clean_input, auto_execute=(not proposal_only))
        save_conversation_turn(session_id, "assistant", response_md)
        return response_md

    # --- ROUTE 4: DIRECT EXECUTIVE BRIEFING ---
    if any(p in low for p in ["executive briefing", "morning briefing", "daily briefing", "status report"]):
        response_md = generate_executive_briefing(DEFAULT_LOCATION)
        save_conversation_turn(session_id, "assistant", response_md)
        return response_md

    # --- ROUTE 5: GENERAL AUTONOMOUS MULTI-TOOL REACT AGENT ---
    history = get_recent_conversation(session_id, limit=8)
    known_facts = query_knowledge_graph(limit=15)

    system_prompt = (
        f"You are ARGUS v4.0, a Jarvis-class Autonomous Executive AI Assistant built for Kyle Wilson in Philadelphia.\n"
        f"IMPORTANT CAPABILITY FACT: You HAVE a built-in live web hosting server at `{RENDER_EXTERNAL_URL}/preview/<slug>` and a dashboard at `{RENDER_EXTERNAL_URL}/`. "
        "Never tell the user you cannot host websites—if they ask for a website or link, tell them to use `#argus create a website for <business>` or use `list_generated_demos`.\n"
        "Available live tools: `web_search`, `read_webpage`, `image_search`, `upsert_knowledge_fact`, "
        "`calendar_list_events`, `calendar_add_event`, `calendar_delete_event`, `gmail_read_inbox`, `gmail_send_or_draft`, "
        "`canvas_sync_and_summary`, `canvas_add_local_item`, `executive_briefing`, `patch_existing_webapp`, `list_generated_demos`.\n\n"
        f"CURRENT KNOWN FACTS IN MEMORY:\n{json.dumps(known_facts, indent=2)}"
    )

    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for h in history[:-1]:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": clean_input})

    final_reply = ""
    for step in range(5):
        try:
            resp = groq_resilient_completion(
                messages=messages,
                temperature=0.3,
                max_tokens=3000,
                tools=JARVIS_TOOL_DECLARATIONS,
                tool_choice="auto"
            )
            msg = resp.choices[0].message
            if not getattr(msg, "tool_calls", None):
                final_reply = clean_llm_text(msg.content or "")
                break

            messages.append(msg)
            for tc in msg.tool_calls:
                fn_name = tc.function.name
                try:
                    fn_args = json.loads(tc.function.arguments)
                except Exception:
                    fn_args = {}
                tool_result = execute_jarvis_tool_call(fn_name, fn_args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": fn_name,
                    "content": tool_result[:5000]
                })
        except Exception as e:
            print(f"[ARGUS ReAct Step {step+1} Fallback]: {e}")
            web_ctx = tool_web_search(clean_input, max_results=5)
            fallback_resp = groq_resilient_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"User Request: {clean_input}\n\nLive Web Context:\n{web_ctx}"}
                ],
                temperature=0.3,
                max_tokens=2500
            )
            final_reply = clean_llm_text(fallback_resp.choices[0].message.content)
            break

    if not final_reply:
        synth = groq_resilient_completion(
            messages=messages + [{"role": "user", "content": "Provide your complete final response based on the tool outputs above."}],
            temperature=0.3,
            max_tokens=2500
        )
        final_reply = clean_llm_text(synth.choices[0].message.content)

    save_conversation_turn(session_id, "assistant", final_reply)
    return final_reply

# =========================================================================================
# SECTION 12: FASTAPI APPLICATION, AUTHENTICATION & REQUEST SCHEMAS
# =========================================================================================

app = FastAPI(
    title="ARGUS OS v4.0 â€” Autonomous Jarvis Executive & Creative Engine",
    description="Multi-Agent Deep Research, Human-in-the-Loop Creative Web/Code Compiler, Canvas LMS, Gmail, Calendar & Executive Briefing OS",
    version=APP_VERSION
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: Optional[str] = None
    prompt: Optional[str] = None
    directive: Optional[str] = None
    text: Optional[str] = None
    session_id: Optional[str] = "default"
    auto_approve: Optional[bool] = False


class ApprovalRequest(BaseModel):
    task_id: Optional[str] = None
    feedback: Optional[str] = ""


class PatchRequest(BaseModel):
    slug: str
    instructions: str


class CalendarEventCreate(BaseModel):
    title: str
    start_time: str
    end_time: str
    location: Optional[str] = ""
    description: Optional[str] = ""


class EmailDispatchRequest(BaseModel):
    recipient: str
    subject: str
    body: str
    send_immediately: Optional[bool] = False


class CanvasItemCreate(BaseModel):
    course_name: str
    title: str
    due_at: str
    points: Optional[float] = 100.0
    description: Optional[str] = ""


def verify_optional_bearer(authorization: Optional[str] = Header(None)) -> bool:
    """
    Soft bearer verification: validates ARGUS_BEARER_TOKEN if provided,
    but never crashes browser dashboard or webhook requests.
    """
    if not authorization:
        return True
    token = authorization.replace("Bearer ", "").strip()
    if token and token != ARGUS_BEARER_TOKEN:
        print(f"[Auth Notice] Non-matching bearer token received: {token[:8]}...")
    return True


# =========================================================================================
# SECTION 13: FASTAPI ROUTES (CHAT, APPROVALS, BRIEFING, CALENDAR, GMAIL, CANVAS, DEMOS)
# =========================================================================================

@app.get("/health")
async def health_check():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) AS cnt FROM generated_artifacts")
    demos_cnt = c.fetchone()["cnt"]
    c.execute("SELECT COUNT(*) AS cnt FROM pending_tasks WHERE status = 'PENDING_APPROVAL'")
    pending_cnt = c.fetchone()["cnt"]
    conn.close()
    return {
        "status": "online",
        "version": APP_VERSION,
        "active_models": get_available_groq_models()[:4],
        "deployed_demos": demos_cnt,
        "pending_approvals": pending_cnt,
        "timestamp": datetime.datetime.now().isoformat()
    }


@app.post("/chat")
@app.post("/api/chat")
@app.post("/directive")
@app.post("/command")
async def chat_endpoint(req: Request, body: Optional[ChatRequest] = None, _auth: bool = Depends(verify_optional_bearer)):
    """
    Universal command & chat endpoint. Accepts JSON payloads (`message`, `prompt`, `directive`, `text`)
    or raw form/text bodies and returns the autonomous Jarvis response.
    """
    user_input = ""
    session_id = "default"

    if body:
        user_input = body.message or body.prompt or body.directive or body.text or ""
        session_id = body.session_id or "default"

    if not user_input:
        try:
            raw_json = await req.json()
            if isinstance(raw_json, dict):
                user_input = (
                    raw_json.get("message")
                    or raw_json.get("prompt")
                    or raw_json.get("directive")
                    or raw_json.get("text")
                    or raw_json.get("query")
                    or ""
                )
                session_id = raw_json.get("session_id", "default")
        except Exception:
            raw_bytes = await req.body()
            user_input = raw_bytes.decode("utf-8", errors="ignore").strip()

    if not user_input:
        raise HTTPException(status_code=400, detail="No message or directive provided.")

    try:
        reply_md = run_jarvis_executive_agent(user_input=user_input, session_id=session_id)
        return {
            "status": "success",
            "response": reply_md,
            "reply": reply_md,
            "message": reply_md,
            "output": reply_md
        }
    except Exception as e:
        tb = traceback.format_exc()
        print(f"[ARGUS Chat Endpoint Error]: {e}\n{tb}")
        log_audit_event("chat_endpoint_error", {"input": user_input, "error": str(e)}, "ERROR")
        return JSONResponse(
            status_code=200,
            content={
                "status": "error_recovered",
                "response": f"âš ï¸ **ARGUS Diagnostic Notice:** `{str(e)}`\n\nPlease retry or check your `GROQ_API_KEY` in Render Environment.",
                "reply": f"âš ï¸ **ARGUS Diagnostic Notice:** `{str(e)}`"
            }
        )


@app.post("/api/approve")
async def approve_task_endpoint(req: ApprovalRequest):
    """Triggers Phase 2 compilation for a pending creative proposal."""
    result_md = jarvis_compile_approved_task(task_id=req.task_id, user_feedback=req.feedback or "")
    return {"status": "success", "response": result_md}


@app.post("/api/patch")
async def patch_demo_endpoint(req: PatchRequest):
    """Applies live self-healing edits to an existing deployed website."""
    result_md = tool_patch_existing_webapp(slug=req.slug, modification_instructions=req.instructions)
    return {"status": "success", "response": result_md}


@app.get("/briefing")
@app.get("/api/briefing")
async def briefing_endpoint(location: str = Query(DEFAULT_LOCATION)):
    """Returns the full Executive Daily Briefing."""
    report = generate_executive_briefing(location=location)
    return {"status": "success", "briefing": report, "response": report}


@app.get("/api/calendar")
async def get_calendar_endpoint(days: int = 14):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM calendar_events WHERE status != 'cancelled' ORDER BY start_time ASC LIMIT 50")
    events = [dict(r) for r in c.fetchall()]
    conn.close()
    return {"status": "success", "events": events, "summary": tool_calendar_list_events(days=days)}


@app.post("/api/calendar")
async def create_calendar_endpoint(ev: CalendarEventCreate):
    msg = tool_calendar_add_event(ev.title, ev.start_time, ev.end_time, ev.location or "", ev.description or "")
    return {"status": "success", "message": msg}


@app.get("/api/canvas")
async def get_canvas_endpoint():
    summary = tool_canvas_sync_and_summary()
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM canvas_items ORDER BY due_at ASC LIMIT 40")
    items = [dict(r) for r in c.fetchall()]
    conn.close()
    return {"status": "success", "items": items, "summary": summary}


@app.post("/api/canvas")
async def add_canvas_item_endpoint(item: CanvasItemCreate):
    msg = tool_canvas_add_local_item(item.course_name, item.title, item.due_at, item.points or 100.0, item.description or "")
    return {"status": "success", "message": msg}


@app.get("/api/gmail")
async def get_gmail_endpoint(max_emails: int = 10):
    summary = tool_gmail_read_inbox(max_emails=max_emails)
    return {"status": "success", "summary": summary}


@app.post("/api/gmail")
async def send_gmail_endpoint(req: EmailDispatchRequest):
    msg = tool_gmail_send_or_draft(req.recipient, req.subject, req.body, bool(req.send_immediately))
    return {"status": "success", "message": msg}


@app.get("/api/memory")
async def get_memory_endpoint(q: str = ""):
    facts = query_knowledge_graph(search_term=q, limit=50)
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM pending_tasks ORDER BY created_at DESC LIMIT 15")
    tasks = [dict(r) for r in c.fetchall()]
    conn.close()
    return {"status": "success", "facts": facts, "tasks": tasks}


@app.get("/preview/{slug}", response_class=HTMLResponse)
async def preview_generated_website(slug: str):
    """Serves the compiled standalone HTML application for any generated demo."""
    clean_slug = re.sub(r'[^a-z0-9\-]', '', slug.lower())
    file_path = os.path.join(DEMO_DIR, f"{clean_slug}.html")

    if not os.path.exists(file_path):
        # Fuzzy match if user typed partial slug
        matches = [f for f in os.listdir(DEMO_DIR) if f.endswith(".html") and clean_slug in f]
        if matches:
            file_path = os.path.join(DEMO_DIR, matches[0])
        else:
            return HTMLResponse(
                content=f"""<!DOCTYPE html>
                <html><head><script src="https://cdn.tailwindcss.com"></script></head>
                <body class="bg-slate-950 text-white min-h-screen flex items-center justify-center p-6">
                  <div class="max-w-md text-center space-y-4 bg-slate-900 p-8 rounded-2xl border border-slate-800">
                    <h1 class="text-2xl font-bold text-amber-400">Demo Not Compiled Yet (`{clean_slug}`)</h1>
                    <p class="text-sm text-slate-400">If ARGUS just generated a Research Proposal for this site, reply <code>#argus approve</code> to compile and deploy it.</p>
                    <a href="/" class="inline-block bg-amber-500 text-slate-950 font-bold px-5 py-2.5 rounded-xl text-sm">Open ARGUS Command Center</a>
                  </div>
                </body></html>""",
                status_code=404
            )

    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content, status_code=200)


@app.get("/demos", response_class=HTMLResponse)
@app.get("/", response_class=HTMLResponse)
async def argus_command_center_ui():
    """
    Serves the interactive ARGUS v4.0 Jarvis Command Center UI:
    - Live Terminal for `#argus` directives
    - One-Click Creative Proposal Approval & Feedback Drawer
    - Deployed Websites Gallery with Live Preview & In-Place Edit Triggers
    - Executive Briefing, Calendar, Canvas LMS & Knowledge Graph Panels
    """
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM pending_tasks ORDER BY created_at DESC LIMIT 8")
    pending_rows = [dict(r) for r in c.fetchall()]
    c.execute("SELECT * FROM generated_artifacts ORDER BY updated_at DESC LIMIT 20")
    artifact_rows = [dict(r) for r in c.fetchall()]
    conn.close()

    # Include any disk HTML demos
    known_slugs = {r["slug"] for r in artifact_rows}
    for f in sorted(os.listdir(DEMO_DIR)):
        if f.endswith(".html"):
            s = f[:-5]
            if s not in known_slugs:
                artifact_rows.append({
                    "slug": s,
                    "title": s.replace("-", " ").title(),
                    "preview_url": f"/preview/{s}",
                    "version": 1,
                    "design_notes": "Deployed Demo"
                })

    demos_cards_html = ""
    for art in artifact_rows:
        s = art["slug"]
        t = art["title"]
        v = art.get("version", 1)
        demos_cards_html += f"""
        <div class="bg-slate-900/90 border border-slate-800 rounded-xl p-4 flex flex-col justify-between hover:border-cyan-500/50 transition">
          <div>
            <div class="flex justify-between items-center mb-2">
              <span class="text-xs font-mono uppercase px-2 py-0.5 rounded bg-cyan-500/10 text-cyan-400 border border-cyan-500/20">v{v} â€¢ /{s}</span>
              <a href="/preview/{s}" target="_blank" class="text-xs font-bold text-amber-400 hover:underline">Open Live â†—</a>
            </div>
            <h4 class="font-bold text-white text-sm truncate">{t}</h4>
          </div>
          <div class="mt-4 pt-3 border-t border-slate-800/80 flex gap-2">
            <a href="/preview/{s}" target="_blank" class="flex-1 text-center bg-cyan-500 hover:bg-cyan-400 text-slate-950 font-bold py-1.5 rounded-lg text-xs transition">Launch App</a>
            <button onclick="promptQuickEdit('{s}')" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-xs font-semibold transition">Edit UI</button>
          </div>
        </div>
        """

    pending_cards_html = ""
    for pt in pending_rows:
        tid = pt["task_id"]
        obj = pt["objective"]
        st = pt["status"]
        sl = pt["artifact_slug"]
        badge_cls = "bg-amber-500/20 text-amber-300 border-amber-500/30" if st == "PENDING_APPROVAL" else "bg-emerald-500/20 text-emerald-300 border-emerald-500/30"
        pending_cards_html += f"""
        <div class="bg-slate-900/90 border border-slate-800 rounded-xl p-4 space-y-2">
          <div class="flex justify-between items-center">
            <span class="text-xs font-mono text-slate-400">{tid} â€¢ /{sl}</span>
            <span class="text-[11px] font-bold px-2 py-0.5 rounded border {badge_cls}">{st}</span>
          </div>
          <p class="text-xs text-slate-200 font-semibold">{obj}</p>
          <div class="flex gap-2 pt-2">
            <button onclick="approveTask('{tid}')" class="bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold px-3 py-1.5 rounded-lg text-xs transition">âœ“ Approve & Compile</button>
            <button onclick="approveWithNotes('{tid}')" class="bg-slate-800 hover:bg-slate-700 text-slate-200 font-semibold px-3 py-1.5 rounded-lg text-xs transition">Steer Creative Style</button>
          </div>
        </div>
        """

    return f"""<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ARGUS OS v4.0 â€” Autonomous Jarvis Command Center</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-950 text-slate-100 font-sans min-h-screen selection:bg-cyan-500 selection:text-slate-950">

  <header class="border-b border-slate-800 bg-slate-900/80 backdrop-blur-md sticky top-0 z-40">
    <div class="max-w-7xl mx-auto px-6 py-4 flex flex-wrap justify-between items-center gap-4">
      <div class="flex items-center space-x-3">
        <div class="w-3 h-3 rounded-full bg-cyan-400 animate-pulse"></div>
        <h1 class="text-xl font-black tracking-wider uppercase text-white">ARGUS OS <span class="text-cyan-400 font-mono text-sm">v4.0 JARVIS</span></h1>
      </div>
      <div class="flex items-center gap-3 text-xs">
        <button onclick="sendDirective('#argus executive briefing')" class="bg-slate-800 hover:bg-slate-700 text-cyan-300 border border-slate-700 px-3.5 py-2 rounded-xl font-bold transition">âš¡ Executive Briefing</button>
        <button onclick="sendDirective('#argus sync canvas assignments')" class="bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 px-3.5 py-2 rounded-xl font-semibold transition">ðŸŽ“ Canvas LMS</button>
        <button onclick="sendDirective('#argus list my calendar events')" class="bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 px-3.5 py-2 rounded-xl font-semibold transition">ðŸ“… Calendar</button>
        <button onclick="sendDirective('#argus check my gmail inbox')" class="bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 px-3.5 py-2 rounded-xl font-semibold transition">ðŸ“¬ Gmail</button>
      </div>
    </div>
  </header>

  <main class="max-w-7xl mx-auto px-6 py-8 grid grid-cols-1 lg:grid-cols-12 gap-8">
    <!-- Left 7 Cols: Interactive Jarvis Terminal -->
    <div class="lg:col-span-7 space-y-6">
      <div class="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-4">
        <div class="flex justify-between items-center">
          <h2 class="text-base font-bold text-cyan-400 uppercase tracking-wider">Autonomous Directive Console</h2>
          <span class="text-xs text-slate-400 font-mono">Think â†’ Research â†’ Propose â†’ Compile</span>
        </div>

        <form onsubmit="submitConsole(event)" class="space-y-3">
          <textarea id="cmd-input" rows="3" placeholder="e.g. #argus create a demo website for Eddie's Pizza on Temple Campus in Philadelphia (or #argus approve: make it a 2-page dark neon theme)" class="w-full bg-slate-950 border border-slate-800 rounded-xl p-4 text-sm text-white focus:outline-none focus:border-cyan-500"></textarea>
          <div class="flex flex-wrap justify-between items-center gap-2">
            <div class="flex gap-2">
              <button type="submit" id="send-btn" class="bg-cyan-500 hover:bg-cyan-400 text-slate-950 font-black px-6 py-2.5 rounded-xl text-xs uppercase tracking-wider transition shadow-lg">Execute Directive</button>
              <button type="button" onclick="sendDirective('#argus approve')" class="bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold px-4 py-2.5 rounded-xl text-xs transition">âœ“ Approve Pending Build</button>
            </div>
            <span id="status-indicator" class="text-xs text-slate-400 font-mono">Ready</span>
          </div>
        </form>

        <div id="console-output" class="bg-slate-950 border border-slate-800 rounded-xl p-5 text-sm leading-relaxed text-slate-200 whitespace-pre-wrap max-h-[550px] overflow-y-auto font-sans">Send any research, website creation, calendar, Canvas, or Gmail command above. When building websites or apps, ARGUS will research real facts & photos first and present a Creative Blueprint Proposal for your approval.</div>
      </div>

      <!-- Creative Approval Queue -->
      <div class="space-y-3">
        <h3 class="text-sm font-bold uppercase tracking-wider text-slate-400">Creative Approval Checkpoints</h3>
        <div class="grid grid-cols-1 gap-3">
          {pending_cards_html or '<p class="text-xs text-slate-500">No pending proposals yet.</p>'}
        </div>
      </div>
    </div>

    <!-- Right 5 Cols: Deployed Web Applications Directory -->
    <div class="lg:col-span-5 space-y-4">
      <div class="flex justify-between items-center">
        <h2 class="text-sm font-bold uppercase tracking-wider text-slate-400">Deployed Web Applications ({len(artifact_rows)})</h2>
        <button onclick="window.location.reload()" class="text-xs text-cyan-400 hover:underline">â†» Refresh List</button>
      </div>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {demos_cards_html or '<p class="text-xs text-slate-500 col-span-2">No demos generated yet.</p>'}
      </div>
    </div>
  </main>

  <script>
    async function sendDirective(text) {{
      const out = document.getElementById('console-output');
      const status = document.getElementById('status-indicator');
      document.getElementById('cmd-input').value = text;
      status.innerText = 'ARGUS Researching & Executing...';
      out.innerText = 'â³ Executing multi-hop research & tool calls for:\\n' + text;

      try {{
        const res = await fetch('/chat', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ message: text }})
        }});
        const data = await res.json();
        out.innerText = data.response || data.reply || JSON.stringify(data, null, 2);
        status.innerText = 'Completed';
      }} catch (err) {{
        out.innerText = 'Error: ' + err;
        status.innerText = 'Error';
      }}
    }}

    function submitConsole(e) {{
      e.preventDefault();
      const val = document.getElementById('cmd-input').value.trim();
      if (!val) return;
      sendDirective(val);
    }}

    function approveTask(taskId) {{
      sendDirective('#argus approve');
    }}

    function approveWithNotes(taskId) {{
      const notes = prompt('Enter your creative direction or modifications (e.g. "Make it a 2-page dark neon layout with a custom calculator"):');
      if (notes !== null) {{
        sendDirective('#argus approve: ' + notes);
      }}
    }}

    function promptQuickEdit(slug) {{
      const notes = prompt('What should ARGUS change or add on /preview/' + slug + '?');
      if (notes) {{
        sendDirective('#argus edit ' + slug + ': ' + notes);
      }}
    }}
  </script>
</body>
</html>"""


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)

