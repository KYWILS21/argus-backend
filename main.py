import os
import re
import sys
import json
import time
import base64
import sqlite3
import datetime
import urllib.request
import urllib.parse
import urllib.error
import asyncio
from email.message import EmailMessage
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, Depends, Header, UploadFile, File, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from groq import Groq, RateLimitError
from fastapi.responses import HTMLResponse

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

# --- CORE CONFIGURATION & GLOBALS ---
ARGUS_BEARER_TOKEN = os.getenv("ARGUS_BEARER_TOKEN", "argus-secret-key-2026")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

RAW_GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
if "gpt-oss" in RAW_GROQ_MODEL or not RAW_GROQ_MODEL:
    GROQ_MODEL = "llama-3.3-70b-versatile"
else:
    GROQ_MODEL = RAW_GROQ_MODEL

DEMO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "generated_demos")
os.makedirs(DEMO_DIR, exist_ok=True)
GOOGLE_CALENDAR_TOKEN = os.getenv("GOOGLE_CALENDAR_TOKEN")
DATABASE_URL = os.getenv("ARGUS_DB_PATH") or os.getenv("DATABASE_URL") or "argus.db"

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_DEFAULT_OWNER = os.getenv("GITHUB_DEFAULT_OWNER", "KYWILS21").strip()

CANVAS_BASE_URL = os.getenv("CANVAS_BASE_URL", "https://templeu.instructure.com").rstrip("/")
CANVAS_API_TOKEN = os.getenv("CANVAS_API_TOKEN", "").strip()
CANVAS_SESSION_COOKIE = os.getenv("CANVAS_SESSION_COOKIE", "").strip()

PUSHOVER_USER_KEY = os.getenv("PUSHOVER_USER_KEY", "").strip()
PUSHOVER_APP_TOKEN = os.getenv("PUSHOVER_APP_TOKEN", "").strip()

groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

async def autonomous_watchdog_daemon():
    """Periodically scans Canvas and Google Calendar for upcoming deadlines and conflicts."""
    await asyncio.sleep(60)  # Wait 60s post-boot

    while True:
        try:
            # Check imminent Calendar events (starting within 3 hours)
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            horizon = (now_utc + datetime.timedelta(hours=3)).isoformat()
            events = fetch_raw_calendar_events(time_min_iso=now_utc.isoformat(), max_results=5)

            for ev in events:
                start_str = ev.get("start")
                if start_str and start_str <= horizon:
                    conn = sqlite3.connect(DATABASE_URL)
                    cur = conn.cursor()
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS system_state_cache (
                            key TEXT PRIMARY KEY,
                            last_seen_val TEXT,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    """)
                    ev_id = str(ev.get("id"))
                    cur.execute("SELECT last_seen_val FROM system_state_cache WHERE key = ?", (f"cal_{ev_id}",))
                    if not cur.fetchone():
                        cur.execute("INSERT OR REPLACE INTO system_state_cache (key, last_seen_val) VALUES (?, ?)", (f"cal_{ev_id}", ev.get("summary")))
                        conn.commit()
                        conn.close()
                        send_iphone_push("Upcoming Schedule Alert", f"Event starting soon: {ev.get('summary')} at {start_str}", priority=1)
                    else:
                        conn.close()
        except Exception as e:
            print(f"Watchdog exception: {e}")

        await asyncio.sleep(1200)  # Poll every 20 minutes

@asynccontextmanager
async def lifespan(app: FastAPI):
    watchdog = asyncio.create_task(autonomous_watchdog_daemon())
    yield
    watchdog.cancel()

app = FastAPI(title="ARGUS Core Protocol Engine", version="2.9.0", lifespan=lifespan)

# Create a demos directory
DEMO_DIR = os.path.join(os.path.dirname(__file__), "generated_demos")
os.makedirs(DEMO_DIR, exist_ok=True)

@app.get("/preview/{slug}", response_class=HTMLResponse)
def serve_client_demo(slug: str):
    safe_slug = "".join(c for c in slug if c.isalnum() or c in ("-", "_")).lower()
    file_path = os.path.join(DEMO_DIR, f"{safe_slug}.html")
    if not os.path.exists(file_path):
        return HTMLResponse(content="<h1>404 Demo Site Not Found</h1>", status_code=404)
    with open(file_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

# ==============================================================================
# CORS Configuration & Global Fallbacks
# ==============================================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

@app.exception_handler(Exception)
async def global_cors_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal server error: {str(exc)}"},
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "*",
            "Access-Control-Allow-Headers": "*",
        }
    )

@app.options("/{rest_of_path:path}")
async def cors_preflight_handler(rest_of_path: str):
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, GET, OPTIONS, DELETE, PUT",
            "Access-Control-Allow-Headers": "Authorization, Content-Type, Accept",
        }
    )

@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)

def discover_and_scrape_official_site(business_name: str, location: str) -> Dict[str, Any]:
    """
    Finds the business's actual website URL via DuckDuckGo/Yahoo/Bing HTML search,
    then crawls their real homepage and about/contact pages for exact text and real images.
    """
    discovered_urls = []
    real_site_images = []
    scraped_pages_text = []

    directory_domains = [
        "yelp.com", "yellowpages.com", "facebook.com", "instagram.com", "tripadvisor.com",
        "mapquest.com", "foursquare.com", "bbb.org", "angi.com", "thumbtack.com",
        "simpletire.com", "surecritic.com", "google.com", "bing.com", "duckduckgo.com", "yahoo.com"
    ]

    # 1. Search via DuckDuckGo Lite / HTML to find official website URL & directory snippets
    queries = [
        f"{business_name} {location} official site",
        f"{business_name} {location} address phone hours services"
    ]

    for q in queries:
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(q, max_results=6))
                for r in results:
                    href = r.get("href", "")
                    title = r.get("title", "")
                    body = r.get("body", "")
                    if body:
                        scraped_pages_text.append(f"SOURCE [{title} - {href}]: {body}")
                    if href and href.startswith("http"):
                        if not any(d in href.lower() for d in directory_domains):
                            if href not in discovered_urls:
                                discovered_urls.append(href)
        except Exception as e:
            print(f"DDGS search warning: {e}")

        # Backup HTML search via Yahoo Search (rarely blocks Render IPs)
        try:
            clean_q = urllib.parse.quote(q)
            y_url = f"https://search.yahoo.com/search?p={clean_q}"
            req = urllib.request.Request(
                y_url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}
            )
            with urllib.request.urlopen(req, timeout=6) as resp:
                y_html = resp.read().decode("utf-8", errors="ignore")
            
            # Extract organic snippets from Yahoo results
            clean_y = re.sub(r'<script.*?</script>', ' ', y_html, flags=re.DOTALL)
            clean_y = re.sub(r'<style.*?</style>', ' ', clean_y, flags=re.DOTALL)
            snippets = re.findall(r'<div[^>]*class="[^"]*compText[^"]*"[^>]*>(.*?)</div>', clean_y)
            for s in snippets[:6]:
                txt = re.sub(r'<[^>]+>', ' ', s).strip()
                if len(txt) > 30:
                    scraped_pages_text.append(f"WEB SNIPPET: {txt}")

            # Extract candidate official URLs
            links = re.findall(r'href="(https?://[^"]+)"', y_html)
            for lnk in links:
                if not any(d in lnk.lower() for d in directory_domains + ["yahoo.com", "yimg.com", "bing.com"]):
                    if lnk not in discovered_urls:
                        discovered_urls.append(lnk)
        except Exception as y_err:
            print(f"Yahoo backup search warning: {y_err}")

    # 2. Directly Crawl the Official Business Website (if discovered)
    official_domain = None
    for target_url in discovered_urls[:2]:
        try:
            print(f"[ARGUS Crawler] Crawling official business website: {target_url}")
            req = urllib.request.Request(
                target_url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            with urllib.request.urlopen(req, timeout=7) as resp:
                site_html = resp.read().decode("utf-8", errors="ignore")

            parsed_uri = urllib.parse.urlparse(target_url)
            base_origin = f"{parsed_uri.scheme}://{parsed_uri.netloc}"
            if not official_domain:
                official_domain = base_origin

            # Extract real images from the business's own website (og:image + <img> tags)
            og_imgs = re.findall(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', site_html, re.I)
            raw_imgs = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', site_html, re.I)

            for img_src in (og_imgs + raw_imgs):
                full_img = urllib.parse.urljoin(base_origin, img_src.strip())
                low = full_img.lower()
                if full_img.startswith("http") and any(ext in low for ext in [".jpg", ".jpeg", ".png", ".webp"]):
                    if not any(skip in low for skip in ["spacer", "1x1", "pixel", "facebook", "twitter", "yelp", "icon", "svg", "spinner", "loader"]):
                        if full_img not in real_site_images:
                            real_site_images.append(full_img)

            # Strip HTML tags to get pure visible text from the business's homepage
            body_clean = re.sub(r'<script.*?</script>', ' ', site_html, flags=re.DOTALL | re.I)
            body_clean = re.sub(r'<style.*?</style>', ' ', body_clean, flags=re.DOTALL | re.I)
            visible_text = re.sub(r'<[^>]+>', ' ', body_clean)
            visible_text = re.sub(r'\s+', ' ', visible_text).strip()
            if len(visible_text) > 100:
                scraped_pages_text.insert(0, f"OFFICIAL WEBSITE ({target_url}) CONTENT: {visible_text[:3000]}")
        except Exception as crawl_err:
            print(f"Direct crawl warning for {target_url}: {crawl_err}")

    corpus = "\n".join(scraped_pages_text)
    return {
        "corpus": corpus,
        "official_url": official_domain,
        "real_site_images": real_site_images
    }


def perplexity_deep_research(business_name: str, location: str, niche: str) -> Dict[str, Any]:
    """
    Stage 1: Deep Web + Official Website Research.
    Extracts verified street address, phone, hours, real offerings, and actual website images.
    """
    crawl_data = discover_and_scrape_official_site(business_name, location)
    corpus = crawl_data.get("corpus", "")

    # Deterministic Regex Extraction of Real Phone & Street Address from the live corpus
    phone_matches = re.findall(r'\(\d{3}\)\s*\d{3}[-.\s]\d{4}|\b\d{3}[-.]\d{3}[-.]\d{4}\b', corpus)
    # Filter out generic toll-free directory numbers
    real_phone = next((p for p in phone_matches if not p.startswith(("800", "888", "877", "(800", "(888"))), None)

    addr_match = re.search(
        r'\b\d{1,5}\s+[A-Z0-9][a-zA-Z0-9\.\-\s]{2,28}(?:Ave|Avenue|St|Street|Rd|Road|Blvd|Boulevard|Pkwy|Pike|Dr|Drive|Ln|Lane|Way)\b\.?(?:,?\s*Philadelphia,?\s*PA\s*\d{5}?)?',
        corpus,
        re.IGNORECASE
    )
    real_address = addr_match.group(0).strip() if addr_match else location

    return {
        "corpus": corpus if corpus else f"{business_name} in {location}",
        "verified_phone": real_phone,
        "verified_address": real_address,
        "official_url": crawl_data.get("official_url"),
        "real_site_images": crawl_data.get("real_site_images", [])
    }

def harvest_targeted_media(
    business_name: str,
    location: str,
    items: List[Dict[str, Any]],
    hero_query: str,
    real_site_images: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Stage 2B: Uses actual photos from the company's website and business-specific image search first.
    """
    real_site_images = real_site_images or []
    bad_tokens = [".svg", "logo", "icon", "favicon", "1x1", "map", "avatar", "badge"]

    # Harvest business-specific photos from DDGS Images (e.g. "Mid City Tire and Auto 709 Washington Ave Philadelphia")
    biz_specific_photos = list(real_site_images)
    try:
        with DDGS() as ddgs:
            biz_results = list(ddgs.images(f'"{business_name}" {location}', max_results=8))
            for r in biz_results:
                url = r.get("image", "")
                if url.startswith("http") and not any(b in url.lower() for b in bad_tokens):
                    if url not in biz_specific_photos:
                        biz_specific_photos.append(url)
    except Exception as e:
        print(f"Business-specific photo search warning: {e}")

    def fetch_item_image(q: str, fallback_idx: int) -> str:
        # Try finding a photo specifically matching the business + item first
        try:
            with DDGS() as ddgs:
                results = list(ddgs.images(q, max_results=4))
                for r in results:
                    url = r.get("image", "")
                    if url.startswith("http") and not any(b in url.lower() for b in bad_tokens):
                        return url
        except Exception as e:
            print(f"Item media warning for '{q}': {e}")

        # Fallback to real photos scraped from the company's actual website / business listing
        if biz_specific_photos:
            return biz_specific_photos[fallback_idx % len(biz_specific_photos)]
        return "https://images.unsplash.com/photo-1556742049-0cfed4f6a45d?auto=format&fit=crop&w=800&q=80"

    # Hero image is ALWAYS the #1 real photo from the company's website or business listing if available
    if biz_specific_photos:
        hero_img = biz_specific_photos[0]
    else:
        hero_img = fetch_item_image(f"{business_name} {location} {hero_query}", 0)

    enriched_items = []
    for idx, item in enumerate(items):
        item_name = item.get("name", "")
        # Prioritize real company website photos if we harvested enough, otherwise search specific item
        if idx + 1 < len(real_site_images):
            item["image_url"] = real_site_images[idx + 1]
        else:
            img_q = item.get("image_query") or f"{business_name} {location} {item_name}"
            item["image_url"] = fetch_item_image(img_q, idx + 1)
        enriched_items.append(item)

    return {"hero_image": hero_img, "items": enriched_items}

def get_available_groq_models() -> List[str]:
    """Dynamically queries Groq API for currently active text-generation models on your account."""
    models = []
    if groq_client:
        try:
            model_list = groq_client.models.list()
            for m in model_list.data:
                m_id = m.id
                # Exclude audio/whisper, guard, vision-only, or embedding models
                if not any(skip in m_id.lower() for skip in ["whisper", "guard", "tts", "embed", "vision"]):
                    models.append(m_id)
        except Exception as e:
            print(f"Dynamic model discovery warning: {e}")

    # Prioritize high-capability models if present in the active account list
    preferred = [
        os.getenv("GROQ_MODEL", ""),
        "llama-3.3-70b-versatile",
        "llama3-70b-8192",
        "mixtral-8x7b-32768",
        "gemma2-9b-it"
    ]
    ordered = [m for m in preferred if m and m in models]
    for m in models:
        if m not in ordered:
            ordered.append(m)

    return ordered if ordered else ["llama-3.3-70b-versatile"]


def build_deterministic_blueprint(business_name: str, niche: str, location: str, research_corpus: str) -> Dict[str, Any]:
    """
    Zero-LLM deterministic synthesis fallback.
    Parses the live scraped web research to build an industry-accurate blueprint so generation never 500s.
    """
    combined = f"{business_name} {niche} {research_corpus}".lower()

    is_auto = any(w in combined for w in ["tire", "auto", "mechanic", "brake", "oil", "repair", "inspection", "collision", "body shop"])
    is_medical_grooming = any(w in combined for w in ["dental", "dentist", "clinic", "medical", "doctor", "barber", "salon", "spa", "hair", "law"])
    is_pizza_ital = any(w in combined for w in ["pizza", "pizzeria", "steak", "cheesesteak", "hoagie", "cutlet", "italian", "bakery", "deli"])

    # At the top of build_deterministic_blueprint:
    phone_matches = re.findall(r'\(\d{3}\)\s*\d{3}[-.\s]\d{4}|\b\d{3}[-.]\d{3}[-.]\d{4}\b', research_corpus)
    extracted_phone = next((p for p in phone_matches if "555" not in p and not p.startswith(("800", "888"))), "(215) 339-9441")

    addr_match = re.search(
        r'\b\d{1,5}\s+[A-Z0-9][a-zA-Z0-9\.\-\s]{2,28}(?:Ave|Avenue|St|Street|Rd|Road|Blvd|Pike)\b\.?(?:,?\s*Philadelphia,?\s*PA\s*\d{5}?)?',
        research_corpus,
        re.IGNORECASE
    )
    extracted_address = addr_match.group(0).strip() if addr_match else location

    if is_auto:
        return {
            "business_name": business_name,
            "niche_subtitle": f"Certified Automotive & Tire Center • {location}",
            "interaction_mode": "service_booking",
            "hero_headline": f"Precision Auto Care & Tire Service at {business_name}",
            "hero_subheadline": f"Trusted in {location} for computerized diagnostics, PA state inspections, brake overhauls, and high-speed tire mounting.",
            "hero_image_query": "modern automotive mechanic service bay garage",
            "primary_cta": "Book Service Bay",
            "drawer_title": "Service Estimate & Bay Booking",
            "checkout_button_text": "Confirm Service Appointment",
            "theme": {
                "mode": "dark",
                "bg_primary": "#090d16",
                "bg_secondary": "#111827",
                "text_primary": "#f9fafb",
                "text_secondary": "#9ca3af",
                "accent_primary": "#ea580c",
                "accent_secondary": "#c2410c",
                "heading_font": "Space Grotesk",
                "body_font": "Inter",
                "border_radius": "0.5rem"
            },
            "contact_info": {
                "address": extracted_address,
                "phone": extracted_phone,
                "hours": "Mon - Sat: 8:00 AM - 6:00 PM",
                "policy_badge": "PA State Inspection Station • Same-Day Tire & Brake Service"
            },
            "categories": ["Tires & Alignment", "Brakes & Suspension", "Maintenance & Inspections"],
            "items": [
                {"id": "1", "name": "PA State Safety & Emissions Inspection", "category": "Maintenance & Inspections", "price": 75.00, "price_label": "$75.00", "badge": "Required Annual", "description": "Complete Pennsylvania DOT safety check and OBD-II emissions certification.", "options": ["Need Sticker Renewal", "Include Wiper Check", "Drop-off Early Morning"], "image_query": "mechanic car inspection lift"},
                {"id": "2", "name": "4-Tire Mount, High-Speed Balance & Install", "category": "Tires & Alignment", "price": 89.00, "price_label": "$89.00", "badge": "Most Popular", "description": "Precision mounting, laser wheel balancing, new valve stems, and torque calibration.", "options": ["All-Season Tires", "Performance Tires", "Add TPMS Reset"], "image_query": "car tire mounting wheel balance machine"},
                {"id": "3", "name": "Ceramic Brake Pad & Rotor Overhaul", "category": "Brakes & Suspension", "price": 219.00, "price_label": "From $219.00", "badge": "Safety Special", "description": "Heavy-duty ceramic brake pads, machined or new rotors, caliper lubrication, and fluid check.", "options": ["Front Axle", "Rear Axle", "Full 4-Wheel Service"], "image_query": "car disc brake rotor caliper close up"},
                {"id": "4", "name": "Laser Four-Wheel Alignment", "category": "Tires & Alignment", "price": 109.00, "price_label": "$109.00", "badge": "Precision", "description": "Computerized camber, caster, and toe adjustment to stop uneven tire wear and steering pull.", "options": ["Standard Alignment", "SUV / Light Truck"], "image_query": "wheel alignment laser sensor car"},
                {"id": "5", "name": "Full Synthetic Oil & Filter Service", "category": "Maintenance & Inspections", "price": 69.99, "price_label": "$69.99", "badge": "Quick Lube", "description": "Up to 5 quarts of full synthetic motor oil, OEM filter replacement, and 25-point inspection.", "options": ["5W-30 Synthetic", "0W-20 Synthetic", "High-Mileage Formula"], "image_query": "pouring synthetic motor oil engine"},
                {"id": "6", "name": "Computerized Engine & Electrical Diagnostics", "category": "Maintenance & Inspections", "price": 95.00, "price_label": "$95.00", "badge": "Check Engine", "description": "Full OBD-II fault code readout, live sensor telemetry, battery/alternator load testing.", "options": ["Check Engine Light", "Battery / Starting Issue", "Strange Noise"], "image_query": "automotive diagnostic scanner tool mechanic"}
            ],
            "reviews": [
                {"author": "Marcus T.", "rating": "5.0", "text": f"Honest pricing and fast turnaround at {business_name}. Got my tires mounted and state inspection done in under two hours."},
                {"author": "Elena R.", "rating": "5.0", "text": "No upsell nonsense. They showed me the exact brake wear before replacing the pads and rotors."},
                {"author": "David K.", "rating": "4.9", "text": f"Best shop in {location}. Always reliable for alignments and quick oil changes."}
            ]
        }

    if is_medical_grooming:
        return {
            "business_name": business_name,
            "niche_subtitle": f"Premier Care & Appointments • {location}",
            "interaction_mode": "service_booking",
            "hero_headline": f"Elevated Care & Personal Attention at {business_name}",
            "hero_subheadline": f"Serving {location} with tailored appointments, modern techniques, and dedicated client care.",
            "hero_image_query": f"{niche} modern studio interior clean",
            "primary_cta": "Book Appointment",
            "drawer_title": "Your Appointment Request",
            "checkout_button_text": "Confirm Booking Slot",
            "theme": {
                "mode": "light",
                "bg_primary": "#f8fafc",
                "bg_secondary": "#ffffff",
                "text_primary": "#0f172a",
                "text_secondary": "#475569",
                "accent_primary": "#0284c7",
                "accent_secondary": "#0369a1",
                "heading_font": "Plus Jakarta Sans",
                "body_font": "DM Sans",
                "border_radius": "1rem"
            },
            "contact_info": {
                "address": location,
                "phone": extracted_phone,
                "hours": "Mon - Sat: 9:00 AM - 7:00 PM",
                "policy_badge": "Accepting New Clients • Online Booking Available"
            },
            "categories": ["Signature Services", "Consultations", "Packages"],
            "items": [
                {"id": "1", "name": "Signature Full Session", "category": "Signature Services", "price": 65.00, "price_label": "$65.00", "badge": "Most Popular", "description": "Complete personalized session tailored to your exact preferences and goals.", "options": ["Morning Slot", "Afternoon Slot", "Senior Specialist"], "image_query": f"{niche} professional service close up"},
                {"id": "2", "name": "Comprehensive New Client Consultation", "category": "Consultations", "price": 45.00, "price_label": "$45.00", "badge": "First Visit", "description": "Thorough evaluation, custom care roadmap, and initial treatment.", "options": ["First-Time Visit", "Include Follow-Up Plan"], "image_query": f"{niche} consultation modern office"},
                {"id": "3", "name": "Executive Refresh Package", "category": "Packages", "price": 110.00, "price_label": "$110.00", "badge": "Premium", "description": "Extended multi-step treatment with premium products and priority scheduling.", "options": ["Standard Package", "Add Express Upgrade"], "image_query": f"{niche} luxury treatment equipment"}
            ],
            "reviews": [
                {"author": "Sarah M.", "rating": "5.0", "text": f"Spotless environment and incredible attention to detail at {business_name}."},
                {"author": "James L.", "rating": "5.0", "text": "Booking was effortless and the staff took the time to get everything right."},
                {"author": "Chloe W.", "rating": "5.0", "text": f"My go-to spot in {location}. Consistently top-tier service."}
            ]
        }

    # Default / Culinary & Hospitality Blueprint
    return {
        "business_name": business_name,
        "niche_subtitle": f"Artisan Kitchen & Local Favorite • {location}",
        "interaction_mode": "cart_order",
        "hero_headline": f"Handcrafted Daily at {business_name}",
        "hero_subheadline": f"Famous across {location} for house-baked seeded rolls, slow-simmered sauces, and made-to-order specialties.",
        "hero_image_query": f"{business_name} {location} pizza cheesesteak food close up",
        "primary_cta": "Start Pickup Order",
        "drawer_title": "Your Pickup Bag",
        "checkout_button_text": "Send Order to Kitchen",
        "theme": {
            "mode": "dark",
            "bg_primary": "#0c0a09",
            "bg_secondary": "#1c1917",
            "text_primary": "#fafaf9",
            "text_secondary": "#a8a29e",
            "accent_primary": "#d97706",
            "accent_secondary": "#b45309",
            "heading_font": "Syne",
            "body_font": "Inter",
            "border_radius": "0.85rem"
        },
        "contact_info": {
            "address": location,
            "phone": extracted_phone,
            "hours": "Tue - Sun: 11:00 AM - 9:00 PM",
            "policy_badge": "Fresh Baked Seeded Bread • Made to Order"
        },
        "categories": ["Signature Pies", "Steaks & Cutlets", "Hoagies & Sides"],
        "items": [
            {"id": "1", "name": "Cooper Sharp Ribeye Cheesesteak", "category": "Steaks & Cutlets", "price": 16.50, "price_label": "$16.50", "badge": "House Legend", "description": "Thinly sliced ribeye steak with melted Cooper Sharp cheese and caramelized onions on a fresh-baked seeded roll.", "options": ["Fried Onions", "Raw Onions", "Long Hots (+$1.50)", "Extra Cooper Sharp"], "image_query": "philly cheesesteak cooper sharp seeded roll close up"},
            {"id": "2", "name": "Grandma Square Pan Pie", "category": "Signature Pies", "price": 23.00, "price_label": "$23.00", "badge": "Best Seller", "description": "Crisp olive-oil pan crust topped with crushed San Marzano tomatoes, fresh mozzarella, roasted garlic, and sweet basil.", "options": ["Well Done Crust", "Add Cup & Char Pepperoni", "Hot Honey Drizzle"], "image_query": "square grandma pizza fresh basil mozzarella close up"},
            {"id": "3", "name": "Chicken Cutlet Italiano", "category": "Steaks & Cutlets", "price": 16.00, "price_label": "$16.00", "badge": "Local Favorite", "description": "Hand-breaded chicken breast cutlet with garlic-sautéed broccoli rabe and aged sharp provolone on seeded bread.", "options": ["Add Roasted Red Peppers", "Long Hots", "Balsamic Glaze"], "image_query": "italian chicken cutlet broccoli rabe sandwich roll"},
            {"id": "4", "name": "Upside-Down Trenton Tomato Pie", "category": "Signature Pies", "price": 22.00, "price_label": "$22.00", "badge": "Artisan Bake", "description": "Sliced mozzarella baked directly on the deck crust, ladled with chunky herb crushed tomatoes and pecorino romano.", "options": ["Classic Plain", "Add Sweet Italian Sausage", "Extra Pecorino"], "image_query": "upside down tomato pie pizza artisan crust"},
            {"id": "5", "name": "Old-World Italian Hoagie", "category": "Hoagies & Sides", "price": 15.50, "price_label": "$15.50", "badge": "Deli Classic", "description": "Imported prosciutto di Parma, hot capicola, Genoa salami, and sharp provolone with shredded lettuce, tomato, and oregano oil.", "options": ["Sweet Peppers", "Hot Cherry Peppers", "Extra Oil & Vinegar"], "image_query": "italian hoagie sub cold cuts seeded roll"},
            {"id": "6", "name": "Crispy Garlic Herb Fries", "category": "Hoagies & Sides", "price": 6.50, "price_label": "$6.50", "badge": "Hot Side", "description": "Double-cooked russet fries tossed in sea salt, cracked black pepper, and grated aged parmesan.", "options": ["Side of Cheese Whiz", "Side of Marinara"], "image_query": "crispy parmesan garlic french fries close up"}
        ],
        "reviews": [
            {"author": "Gianna P.", "rating": "5.0", "text": f"The seeded bread at {business_name} is unmatched. Best Cooper Sharp steak and Grandma pie in {location}."},
            {"author": "Mike D.", "rating": "5.0", "text": "Worth every minute of the wait. The crust has the perfect char and crunch."},
            {"author": "Tasha R.", "rating": "5.0", "text": "Chicken cutlet with broccoli rabe and sharp provolone is an absolute 10/10."}
        ]
    }


def synthesize_business_blueprint(business_name: str, niche: str, location: str, research_corpus: str) -> Dict[str, Any]:
    """
    Stage 2A: Queries active Groq models dynamically, and falls back to deterministic synthesis so it never crashes.
    """
    prompt = f"""
    You are an autonomous business intelligence and brand synthesis engine.
    Analyze the following live web research for "{business_name}" ({niche}) in "{location}":

    LIVE WEB RESEARCH CORPUS:
    {research_corpus[:3200]}

    RETURN ONLY VALID JSON matching this exact structure (with 6 real items/services):
    {{
      "business_name": "{business_name}",
      "niche_subtitle": "Descriptive category",
      "interaction_mode": "cart_order" | "service_booking",
      "hero_headline": "Bold headline tailored to the business",
      "hero_subheadline": "15-25 word value proposition grounded in research",
      "hero_image_query": "Search query for hero image",
      "primary_cta": "e.g., 'Start Pickup Order' or 'Book Service Bay'",
      "drawer_title": "e.g., 'Your Pickup Order' or 'Service Estimate & Booking'",
      "checkout_button_text": "e.g., 'Submit Order' or 'Confirm Appointment'",
      "theme": {{
        "mode": "dark" | "light",
        "bg_primary": "#0c0a09",
        "bg_secondary": "#1c1917",
        "text_primary": "#fafaf9",
        "text_secondary": "#a8a29e",
        "accent_primary": "#ea580c",
        "accent_secondary": "#c2410c",
        "heading_font": "Space Grotesk",
        "body_font": "Inter",
        "border_radius": "0.75rem"
      }},
      "contact_info": {{
        "address": "Extracted street address in {location}",
        "phone": "Extracted phone number",
        "hours": "Extracted operating hours",
        "policy_badge": "Key operational highlight"
      }},
      "categories": ["Category 1", "Category 2", "Category 3"],
      "items": [
        {{
          "id": "item-1",
          "name": "Real Item or Service Name",
          "category": "Category 1",
          "price": 18.00,
          "price_label": "$18.00",
          "badge": "Popular",
          "description": "Authentic description of item or service.",
          "options": ["Option 1", "Option 2"],
          "image_query": "Specific photo search query for this item/service"
        }}
      ],
      "reviews": [
        {{"author": "Local Customer", "rating": "5.0", "text": "Specific review praise."}}
      ]
    }}
    """

    for model_id in get_available_groq_models():
        try:
            print(f"[ARGUS Synthesizer] Attempting blueprint generation with live model: {model_id}")
            resp = groq_client.chat.completions.create(
                model=model_id,
                messages=[
                    {"role": "system", "content": "Output strictly valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                max_tokens=3200,
                response_format={"type": "json_object"}
            )
            content = resp.choices[0].message.content.strip()
            if content.startswith("```"):
                lines = content.splitlines()
                content = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
            parsed = json.loads(content)
            if parsed.get("items") and len(parsed["items"]) >= 3:
                return parsed
        except Exception as e:
            print(f"[ARGUS Synthesizer] Model '{model_id}' failed: {e}")

    print("[ARGUS Synthesizer] Using deterministic research parser fallback.")
    return build_deterministic_blueprint(business_name, niche, location, research_corpus)

def compile_bespoke_webapp(blueprint: Dict[str, Any]) -> str:
    """
    Stage 3: Dynamic Frontend Compiler.
    Generates a complete, bespoke, multi-section single-page application driven by the blueprint JSON.
    """
    theme = blueprint.get("theme", {})
    contact = blueprint.get("contact_info", {})
    heading_font = theme.get("heading_font", "Plus Jakarta Sans")
    body_font = theme.get("body_font", "Inter")
    font_url = f"https://fonts.googleapis.com/css2?family={heading_font.replace(' ', '+')}:wght@600;700;800&family={body_font.replace(' ', '+')}:wght@400;500;600;700&display=swap"

    blueprint_json_str = json.dumps(blueprint)

    return f"""<!DOCTYPE html>
<html lang="en" class="scroll-smooth">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{blueprint.get('business_name')} | {blueprint.get('niche_subtitle')}</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="{font_url}" rel="stylesheet">
  <style>
    :root {{
      --bg-primary: {theme.get('bg_primary', '#0c0a09')};
      --bg-secondary: {theme.get('bg_secondary', '#1c1917')};
      --text-primary: {theme.get('text_primary', '#fafaf9')};
      --text-secondary: {theme.get('text_secondary', '#a8a29e')};
      --accent: {theme.get('accent_primary', '#f59e0b')};
      --accent-hover: {theme.get('accent_secondary', '#d97706')};
      --radius: {theme.get('border_radius', '0.75rem')};
    }}
    body {{
      background-color: var(--bg-primary);
      color: var(--text-primary);
      font-family: '{body_font}', sans-serif;
    }}
    h1, h2, h3, h4, .font-display {{
      font-family: '{heading_font}', sans-serif;
    }}
    .surface-card {{
      background-color: var(--bg-secondary);
      border-radius: var(--radius);
      border: 1px solid rgba(128, 128, 128, 0.18);
    }}
    .btn-accent {{
      background-color: var(--accent);
      color: #ffffff;
      border-radius: var(--radius);
      transition: all 0.2s ease;
    }}
    .btn-accent:hover {{
      background-color: var(--accent-hover);
      transform: translateY(-1px);
    }}
    .text-accent {{
      color: var(--accent);
    }}
    .border-accent {{
      border-color: var(--accent);
    }}
  </style>
</head>
<body class="antialiased selection:bg-amber-500 selection:text-black min-h-screen flex flex-col justify-between">

  <!-- Top Operational Banner -->
  <div class="w-full py-2 px-4 text-center text-xs font-semibold tracking-wide border-b border-white/10" style="background-color: var(--bg-secondary); color: var(--text-secondary);">
    <span class="text-accent font-bold">● LIVE STATUS:</span> {contact.get('policy_badge', 'Open for Customers')} &nbsp;|&nbsp; 📍 {contact.get('address', '')} &nbsp;|&nbsp; 📞 {contact.get('phone', '')}
  </div>

  <!-- Sticky Navigation -->
  <header class="sticky top-0 z-40 backdrop-blur-xl border-b border-white/10" style="background-color: color-mix(in srgb, var(--bg-primary) 88%, transparent);">
    <div class="max-w-7xl mx-auto px-6 py-4 flex justify-between items-center">
      <div>
        <a href="#" onclick="switchPage('home')" class="text-2xl font-extrabold tracking-tight font-display" style="color: var(--text-primary);">
          {blueprint.get('business_name')}
        </a>
        <span class="hidden md:inline-block ml-3 text-xs px-2.5 py-1 rounded-full font-medium" style="background-color: var(--bg-secondary); color: var(--text-secondary);">
          {blueprint.get('niche_subtitle')}
        </span>
      </div>

      <nav class="hidden md:flex items-center space-x-8 text-sm font-semibold">
        <button onclick="switchPage('home')" id="nav-home" class="text-accent border-b-2 border-accent pb-1 transition">Overview & Catalog</button>
        <button onclick="switchPage('reviews')" id="nav-reviews" class="hover:opacity-80 pb-1 transition" style="color: var(--text-secondary);">Reviews & Story</button>
        <button onclick="switchPage('location')" id="nav-location" class="hover:opacity-80 pb-1 transition" style="color: var(--text-secondary);">Hours & Location</button>
      </nav>

      <div class="flex items-center space-x-3">
        <button onclick="toggleDrawer()" class="btn-accent px-5 py-2.5 text-sm font-bold flex items-center gap-2.5 shadow-lg">
          <span id="drawer-btn-label">{blueprint.get('primary_cta', 'View Selection')}</span>
          <span id="cart-count" class="bg-black/30 text-white text-xs px-2 py-0.5 rounded-full font-mono">0</span>
        </button>
      </div>
    </div>
  </header>

  <!-- VIEW 1: HOME & INTERACTIVE CATALOG -->
  <main id="page-home" class="flex-grow">
    <!-- Hero Section -->
    <section class="relative py-24 md:py-32 px-6 bg-cover bg-center border-b border-white/10" style="background-image: linear-gradient(to bottom, rgba(0,0,0,0.65), rgba(0,0,0,0.85)), url('{blueprint.get('hero_image', '')}');">
      <div class="max-w-4xl mx-auto text-center space-y-6">
        <div class="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full text-xs font-bold uppercase tracking-widest bg-white/10 text-white backdrop-blur-md border border-white/20">
          <span>★ Verified Local Standard</span>
          <span>•</span>
          <span>{contact.get('address', '')}</span>
        </div>
        <h1 class="text-4xl sm:text-6xl font-extrabold text-white tracking-tight leading-tight font-display">
          {blueprint.get('hero_headline')}
        </h1>
        <p class="text-lg sm:text-xl text-neutral-200 max-w-2xl mx-auto leading-relaxed">
          {blueprint.get('hero_subheadline')}
        </p>
        <div class="pt-4 flex flex-wrap justify-center gap-4">
          <a href="#catalog-section" class="btn-accent px-8 py-4 font-bold text-base shadow-xl">
            Explore Full Offerings
          </a>
          <button onclick="switchPage('location')" class="bg-white/10 hover:bg-white/20 text-white border border-white/25 px-8 py-4 font-bold text-base backdrop-blur-md transition" style="border-radius: var(--radius);">
            Hours & Directions
          </button>
        </div>
      </div>
    </section>

    <!-- Dynamic Search & Category Filter Bar -->
    <section id="catalog-section" class="max-w-7xl mx-auto px-6 py-16">
      <div class="flex flex-col md:flex-row md:items-end justify-between mb-10 gap-6">
        <div>
          <span class="text-xs font-bold uppercase tracking-widest text-accent">Complete Live Directory</span>
          <h2 class="text-3xl sm:text-4xl font-extrabold mt-1 font-display"> Offerings & Pricing</h2>
        </div>
        
        <div class="flex flex-wrap items-center gap-3">
          <input type="text" id="search-input" oninput="renderCatalog()" placeholder="Search items or services..." class="px-4 py-2 text-sm surface-card focus:outline-none focus:ring-2" style="color: var(--text-primary);" />
          <div id="category-tabs" class="flex flex-wrap gap-2"></div>
        </div>
      </div>

      <!-- Dynamic Item Grid -->
      <div id="catalog-grid" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8"></div>
    </section>
  </main>

  <!-- VIEW 2: REVIEWS & BRAND STORY (Multi-Page Tab View) -->
  <section id="page-reviews" class="hidden max-w-5xl mx-auto px-6 py-20 flex-grow">
    <div class="text-center mb-14">
      <span class="text-xs font-bold uppercase tracking-widest text-accent">Community Reputation</span>
      <h2 class="text-4xl font-extrabold mt-2 font-display">What Locals Are Saying</h2>
      <p class="mt-2 text-sm" style="color: var(--text-secondary);">Verified customer experiences and neighborhood feedback for {blueprint.get('business_name')}.</p>
    </div>
    <div id="reviews-grid" class="grid grid-cols-1 md:grid-cols-3 gap-6 mb-16"></div>
    <div class="surface-card p-8 text-center space-y-4">
      <h3 class="text-2xl font-bold font-display">Ready to Experience {blueprint.get('business_name')}?</h3>
      <p class="text-sm max-w-xl mx-auto" style="color: var(--text-secondary);">{blueprint.get('hero_subheadline')}</p>
      <button onclick="switchPage('home')" class="btn-accent px-6 py-3 text-sm font-bold">Return to Offerings</button>
    </div>
  </section>

  <!-- VIEW 3: HOURS, LOCATION & DIRECT CONTACT (Multi-Page Tab View) -->
  <section id="page-location" class="hidden max-w-5xl mx-auto px-6 py-20 flex-grow">
    <div class="grid grid-cols-1 md:grid-cols-2 gap-10">
      <div class="surface-card p-8 space-y-6">
        <span class="text-xs font-bold uppercase tracking-widest text-accent">Visit or Call Direct</span>
        <h2 class="text-3xl font-extrabold font-display">{blueprint.get('business_name')}</h2>
        <div class="space-y-4 text-sm">
          <div>
            <div class="font-bold opacity-70 uppercase text-xs">Location / Address</div>
            <div class="text-base font-semibold mt-1">{contact.get('address', '')}</div>
          </div>
          <div>
            <div class="font-bold opacity-70 uppercase text-xs">Direct Phone</div>
            <a href="tel:{contact.get('phone', '')}" class="text-base font-bold text-accent hover:underline mt-1 block">{contact.get('phone', '')}</a>
          </div>
          <div>
            <div class="font-bold opacity-70 uppercase text-xs">Operating Hours</div>
            <div class="text-base mt-1">{contact.get('hours', '')}</div>
          </div>
          <div class="pt-4 border-t border-white/10">
            <span class="inline-block px-3 py-1 rounded text-xs font-bold" style="background-color: var(--bg-primary); color: var(--accent);">
              {contact.get('policy_badge', '')}
            </span>
          </div>
        </div>
      </div>

      <!-- Direct Inquiry / Callback Form -->
      <div class="surface-card p-8 space-y-5">
        <h3 class="text-2xl font-bold font-display">Send a Direct Inquiry</h3>
        <p class="text-xs" style="color: var(--text-secondary);">Have a special request, catering question, or custom service inquiry? Reach out directly.</p>
        <form onsubmit="submitInquiry(event)" class="space-y-4 text-sm">
          <input type="text" required placeholder="Your Name" class="w-full p-3 rounded bg-black/20 border border-white/15 focus:outline-none" style="color: var(--text-primary);">
          <input type="tel" required placeholder="Phone Number" class="w-full p-3 rounded bg-black/20 border border-white/15 focus:outline-none" style="color: var(--text-primary);">
          <textarea rows="3" required placeholder="How can we help you?" class="w-full p-3 rounded bg-black/20 border border-white/15 focus:outline-none" style="color: var(--text-primary);"></textarea>
          <button type="submit" class="w-full btn-accent py-3 font-bold text-sm">Send Message</button>
        </form>
        <div id="inquiry-toast" class="hidden p-3 rounded text-xs font-bold bg-emerald-500/20 text-emerald-300 text-center">
          ✓ Inquiry received! Our team will contact you shortly.
        </div>
      </div>
    </div>
  </section>

  <!-- ITEM CUSTOMIZER / SERVICE OPTIONS MODAL -->
  <div id="item-modal" class="fixed inset-0 z-50 hidden items-center justify-center p-4 bg-black/75 backdrop-blur-sm">
    <div class="surface-card max-w-md w-full p-6 space-y-5 shadow-2xl">
      <div class="flex justify-between items-start">
        <div>
          <h3 id="modal-item-name" class="text-xl font-bold font-display"></h3>
          <p id="modal-item-price" class="text-sm font-bold text-accent mt-0.5"></p>
        </div>
        <button onclick="closeItemModal()" class="text-sm opacity-60 hover:opacity-100">✕</button>
      </div>
      <p id="modal-item-desc" class="text-xs leading-relaxed" style="color: var(--text-secondary);"></p>
      
      <div id="modal-options-container" class="space-y-2 pt-2">
        <label class="text-xs font-bold uppercase tracking-wider opacity-75">Select Preferences / Modifiers:</label>
        <div id="modal-options-list" class="space-y-2 text-sm"></div>
      </div>

      <div>
        <label class="text-xs font-bold uppercase tracking-wider opacity-75">Special Notes / Requests:</label>
        <input id="modal-custom-note" type="text" placeholder="Optional instructions..." class="w-full mt-1 p-2.5 text-xs rounded bg-black/20 border border-white/15" style="color: var(--text-primary);" />
      </div>

      <button id="modal-confirm-btn" class="w-full btn-accent py-3 font-bold text-sm">Add to Selection</button>
    </div>
  </div>

  <!-- SLIDE-OVER INTERACTIVE DRAWER (CART OR BOOKING ESTIMATE) -->
  <div id="side-drawer" class="fixed inset-0 z-50 pointer-events-none opacity-0 transition-opacity duration-300">
    <div onclick="toggleDrawer()" class="absolute inset-0 bg-black/70 backdrop-blur-sm"></div>
    <div id="drawer-panel" class="absolute right-0 top-0 h-full w-full max-w-md p-6 flex flex-col justify-between pointer-events-auto transform translate-x-full transition-transform duration-300 shadow-2xl" style="background-color: var(--bg-secondary);">
      <div>
        <div class="flex justify-between items-center pb-4 border-b border-white/10">
          <h2 class="text-xl font-extrabold font-display">{blueprint.get('drawer_title', 'Your Selection')}</h2>
          <button onclick="toggleDrawer()" class="text-sm opacity-70 hover:opacity-100">✕ Close</button>
        </div>
        <div id="drawer-items" class="py-5 space-y-3 overflow-y-auto max-h-[55vh]"></div>
      </div>

      <div class="pt-4 border-t border-white/10 space-y-4">
        <div class="space-y-2 text-xs">
          <input id="customer-name" type="text" placeholder="Your Full Name *" class="w-full p-2.5 rounded bg-black/20 border border-white/15" style="color: var(--text-primary);">
          <input id="customer-phone" type="tel" placeholder="Mobile Phone for Updates *" class="w-full p-2.5 rounded bg-black/20 border border-white/15" style="color: var(--text-primary);">
          <input id="customer-time" type="text" placeholder="Preferred Pickup / Appointment Time" class="w-full p-2.5 rounded bg-black/20 border border-white/15" style="color: var(--text-primary);">
        </div>
        <div class="flex justify-between items-baseline text-base font-bold pt-2">
          <span>Estimated Total</span>
          <span id="drawer-total" class="text-xl text-accent font-mono">$0.00</span>
        </div>
        <button onclick="completeCheckout()" class="w-full btn-accent py-3.5 font-extrabold text-sm shadow-lg">
          {blueprint.get('checkout_button_text', 'Confirm & Submit')}
        </button>
      </div>
    </div>
  </div>

  <!-- CONFIRMATION RECEIPT MODAL -->
  <div id="receipt-modal" class="fixed inset-0 z-50 hidden items-center justify-center p-4 bg-black/80 backdrop-blur-md">
    <div class="surface-card max-w-sm w-full p-6 text-center space-y-4 shadow-2xl">
      <div class="w-12 h-12 rounded-full flex items-center justify-center mx-auto text-xl font-bold btn-accent">✓</div>
      <h3 class="text-2xl font-extrabold font-display">Confirmed!</h3>
      <p id="receipt-summary" class="text-xs leading-relaxed" style="color: var(--text-secondary);"></p>
      <div id="receipt-box" class="p-4 rounded text-xs font-mono text-left space-y-1 bg-black/30 border border-white/10"></div>
      <button onclick="closeReceipt()" class="w-full btn-accent py-2.5 font-bold text-xs">Done</button>
    </div>
  </div>

  <!-- Footer -->
  <footer class="border-t border-white/10 py-10 px-6 text-center text-xs mt-16" style="background-color: var(--bg-secondary); color: var(--text-secondary);">
    <div class="max-w-7xl mx-auto flex flex-col sm:flex-row justify-between items-center gap-4">
      <div class="font-bold" style="color: var(--text-primary);">&copy; 2026 {blueprint.get('business_name')}. All rights reserved.</div>
      <div>📍 {contact.get('address', '')} &bull; 📞 {contact.get('phone', '')}</div>
    </div>
  </footer>

  <!-- Client-Side Application Engine -->
  <script>
    const BLUEPRINT = {blueprint_json_str};
    let activeCategory = 'All';
    let cart = [];
    let currentModalItem = null;

    function initApp() {{
      renderCategories();
      renderCatalog();
      renderReviews();
      renderDrawer();
    }}

    function switchPage(pageId) {{
      ['home', 'reviews', 'location'].forEach(p => {{
        document.getElementById('page-' + p).classList.add('hidden');
        const navBtn = document.getElementById('nav-' + p);
        if (navBtn) {{
          navBtn.className = "hover:opacity-80 pb-1 transition";
          navBtn.style.color = "var(--text-secondary)";
        }}
      }});
      document.getElementById('page-' + pageId).classList.remove('hidden');
      const activeNav = document.getElementById('nav-' + pageId);
      if (activeNav) {{
        activeNav.className = "text-accent border-b-2 border-accent pb-1 transition";
        activeNav.style.color = "var(--accent)";
      }}
      window.scrollTo({{ top: 0, behavior: 'smooth' }});
    }}

    function renderCategories() {{
      const cats = ['All', ...(BLUEPRINT.categories || [])];
      const container = document.getElementById('category-tabs');
      container.innerHTML = cats.map(c => `
        <button onclick="setCategory('${{c.replace(/'/g, "\\'")}}')" class="px-4 py-2 text-xs font-bold transition ${{activeCategory === c ? 'btn-accent' : 'surface-card hover:opacity-80'}}">
          ${{c}}
        </button>
      `).join('');
    }}

    function setCategory(cat) {{
      activeCategory = cat;
      renderCategories();
      renderCatalog();
    }}

    function renderCatalog() {{
      const query = (document.getElementById('search-input').value || '').toLowerCase();
      const grid = document.getElementById('catalog-grid');
      const items = (BLUEPRINT.items || []).filter(item => {{
        const matchesCat = activeCategory === 'All' || item.category === activeCategory;
        const matchesQuery = item.name.toLowerCase().includes(query) || (item.description || '').toLowerCase().includes(query);
        return matchesCat && matchesQuery;
      }});

      const actionLabel = BLUEPRINT.interaction_mode === 'service_booking' ? 'Select Service' : 'Add to Order';

      grid.innerHTML = items.map((item, idx) => `
        <div class="surface-card overflow-hidden flex flex-col justify-between group hover:border-white/30 transition shadow-md">
          <div>
            <div class="relative h-56 overflow-hidden bg-black/20">
              <img src="${{item.image_url}}" alt="${{item.name}}" class="w-full h-full object-cover group-hover:scale-105 transition duration-500" onerror="this.src='https://images.unsplash.com/photo-1556742049-0cfed4f6a45d?auto=format&fit=crop&w=800&q=80'">
              ${{item.badge ? `<span class="absolute top-3 left-3 px-2.5 py-1 text-[11px] font-extrabold uppercase tracking-wider btn-accent shadow">${{item.badge}}</span>` : ''}}
            </div>
            <div class="p-6">
              <div class="flex justify-between items-baseline gap-2 mb-2">
                <h3 class="text-lg font-bold font-display leading-snug">${{item.name}}</h3>
                <span class="text-sm font-extrabold text-accent shrink-0">${{item.price_label || ('$' + Number(item.price).toFixed(2))}}</span>
              </div>
              <p class="text-xs leading-relaxed" style="color: var(--text-secondary);">${{item.description}}</p>
            </div>
          </div>
          <div class="px-6 pb-6 pt-2">
            <button onclick="openItemModal(${{idx}})" class="w-full py-2.5 text-xs font-bold btn-accent">
              ${{actionLabel}}
            </button>
          </div>
        </div>
      `).join('');
    }}

    function openItemModal(index) {{
      const item = BLUEPRINT.items[index];
      currentModalItem = item;
      document.getElementById('modal-item-name').innerText = item.name;
      document.getElementById('modal-item-price').innerText = item.price_label || ('$' + Number(item.price).toFixed(2));
      document.getElementById('modal-item-desc').innerText = item.description;
      document.getElementById('modal-custom-note').value = '';

      const optsList = document.getElementById('modal-options-list');
      const opts = item.options || [];
      if (opts.length > 0) {{
        document.getElementById('modal-options-container').classList.remove('hidden');
        optsList.innerHTML = opts.map((opt, i) => `
          <label class="flex items-center space-x-2 cursor-pointer">
            <input type="checkbox" value="${{opt}}" class="modal-opt-check rounded">
            <span class="text-xs">${{opt}}</span>
          </label>
        `).join('');
      }} else {{
        document.getElementById('modal-options-container').classList.add('hidden');
      }}

      const confirmBtn = document.getElementById('modal-confirm-btn');
      confirmBtn.onclick = () => confirmModalAdd();

      const modal = document.getElementById('item-modal');
      modal.classList.remove('hidden');
      modal.classList.add('flex');
    }}

    function closeItemModal() {{
      const modal = document.getElementById('item-modal');
      modal.classList.add('hidden');
      modal.classList.remove('flex');
    }}

    function confirmModalAdd() {{
      if (!currentModalItem) return;
      const checked = Array.from(document.querySelectorAll('.modal-opt-check:checked')).map(el => el.value);
      const note = document.getElementById('modal-custom-note').value.trim();
      const modifiers = [...checked, ...(note ? [note] : [])].join(', ');

      cart.push({{
        name: currentModalItem.name,
        price: Number(currentModalItem.price) || 0,
        modifiers: modifiers,
        qty: 1
      }});

      closeItemModal();
      renderDrawer();
      toggleDrawer(true);
    }}

    function toggleDrawer(forceOpen = null) {{
      const drawer = document.getElementById('side-drawer');
      const panel = document.getElementById('drawer-panel');
      const isOpen = !drawer.classList.contains('opacity-0');
      const shouldOpen = forceOpen !== null ? forceOpen : !isOpen;

      if (shouldOpen) {{
        drawer.classList.remove('opacity-0', 'pointer-events-none');
        panel.classList.remove('translate-x-full');
      }} else {{
        drawer.classList.add('opacity-0', 'pointer-events-none');
        panel.classList.add('translate-x-full');
      }}
    }}

    function removeCartItem(idx) {{
      cart.splice(idx, 1);
      renderDrawer();
    }}

    function renderDrawer() {{
      const container = document.getElementById('drawer-items');
      const countEl = document.getElementById('cart-count');
      const totalEl = document.getElementById('drawer-total');

      countEl.innerText = cart.length;
      const total = cart.reduce((sum, i) => sum + (i.price * i.qty), 0);
      totalEl.innerText = '$' + total.toFixed(2);

      if (cart.length === 0) {{
        container.innerHTML = `<p class="text-xs text-center py-10 opacity-60">Nothing selected yet. Choose an item or service from the catalog.</p>`;
        return;
      }}

      container.innerHTML = cart.map((item, idx) => `
        <div class="p-3.5 rounded bg-black/20 border border-white/10 flex justify-between items-start gap-3">
          <div>
            <div class="text-sm font-bold">${{item.name}}</div>
            ${{item.modifiers ? `<div class="text-[11px] opacity-75 mt-0.5">${{item.modifiers}}</div>` : ''}}
            <div class="text-xs font-mono text-accent mt-1">$${{item.price.toFixed(2)}}</div>
          </div>
          <button onclick="removeCartItem(${{idx}})" class="text-xs opacity-50 hover:opacity-100">✕</button>
        </div>
      `).join('');
    }}

    function completeCheckout() {{
      if (cart.length === 0) return;
      const name = document.getElementById('customer-name').value.trim() || 'Valued Customer';
      const phone = document.getElementById('customer-phone').value.trim() || 'Provided at counter';
      const time = document.getElementById('customer-time').value.trim() || 'Standard Priority';
      const total = cart.reduce((sum, i) => sum + (i.price * i.qty), 0);

      toggleDrawer(false);
      document.getElementById('receipt-summary').innerText = `Thank you, ${{name}}! Your request has been registered with ${{BLUEPRINT.business_name}}.`;
      document.getElementById('receipt-box').innerHTML = `
        <div><strong>Reference:</strong> #ARG-${{Math.floor(1000 + Math.random() * 9000)}}</div>
        <div><strong>Customer:</strong> ${{name}} (${{phone}})</div>
        <div><strong>Requested Time:</strong> ${{time}}</div>
        <div><strong>Items Selected:</strong> ${{cart.length}}</div>
        <div><strong>Total Estimate:</strong> $${{total.toFixed(2)}}</div>
      `;
      const modal = document.getElementById('receipt-modal');
      modal.classList.remove('hidden');
      modal.classList.add('flex');
    }}

    function closeReceipt() {{
      const modal = document.getElementById('receipt-modal');
      modal.classList.add('hidden');
      modal.classList.remove('flex');
      cart = [];
      renderDrawer();
    }}

    function renderReviews() {{
      const grid = document.getElementById('reviews-grid');
      const reviews = BLUEPRINT.reviews || [];
      grid.innerHTML = reviews.map(r => `
        <div class="surface-card p-6 space-y-3">
          <div class="text-accent text-sm font-bold">★★★★★ ${{r.rating || '5.0'}}</div>
          <p class="text-xs italic leading-relaxed" style="color: var(--text-secondary);">"${{r.text}}"</p>
          <div class="text-xs font-bold pt-2">— ${{r.author}}</div>
        </div>
      `).join('');
    }}

    function submitInquiry(e) {{
      e.preventDefault();
      document.getElementById('inquiry-toast').classList.remove('hidden');
      e.target.reset();
    }}

    window.addEventListener('DOMContentLoaded', initApp);
  </script>
</body>
</html>"""


def generate_client_website_tool(
    business_name: str, 
    niche: str, 
    location: str, 
    contact_phone: Optional[str] = None, 
    highlights: Optional[str] = None
) -> str:
    # 1. Clean business name & generate clean URL slug
    clean_biz_name = re.split(r'\s+(?:and\s+just|and\s+make|with|featuring)\b', business_name, flags=re.IGNORECASE)[0].strip()
    slug = "".join(c for c in clean_biz_name.lower() if c.isalnum() or c == "-").strip("-")
    if not slug:
        slug = f"demo-{int(time.time())}"

    # 2. Stage 1: Multi-Vector Perplexity-style Deep Research
    print(f"[ARGUS Engine] Stage 1: Deep web research for '{clean_biz_name}' in '{location}'...")
    research = perplexity_deep_research(clean_biz_name, location, niche)

    # 3. Stage 2A: Synthesize Full Business Blueprint & Theme Architecture
    print(f"[ARGUS Engine] Stage 2A: Synthesizing brand palette, UX mode, and full catalog...")
    # Inside generate_client_website_tool, right after Stage 2A (synthesize_business_blueprint):
    blueprint = synthesize_business_blueprint(clean_biz_name, niche, location, research["corpus"])

    # Override any hallucinated or placeholder contact details with the real verified ones from the web crawl
    contact_info = blueprint.get("contact_info", {})
    if research.get("verified_phone") and ("555" in contact_info.get("phone", "") or not contact_info.get("phone")):
        contact_info["phone"] = research["verified_phone"]
    if research.get("verified_address") and len(research["verified_address"]) > 8:
        contact_info["address"] = research["verified_address"]
    blueprint["contact_info"] = contact_info

    # Stage 2B: Pass real_site_images into harvest_targeted_media
    media = harvest_targeted_media(
        clean_biz_name,
        location,
        blueprint.get("items", []),
        blueprint.get("hero_image_query", niche),
        real_site_images=research.get("real_site_images", [])
    )
    blueprint["hero_image"] = media["hero_image"]
    blueprint["items"] = media["items"]

    # 5. Stage 3: Compile Bespoke Multi-View Web Application
    print(f"[ARGUS Engine] Stage 3: Compiling bespoke interactive web application...")
    raw_html = compile_bespoke_webapp(blueprint)

    file_path = os.path.join(DEMO_DIR, f"{slug}.html")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(raw_html)

    preview_url = f"https://argus-backend-ojna.onrender.com/preview/{slug}"
    mode_label = "Interactive Service Booking & Estimator" if blueprint.get("interaction_mode") == "service_booking" else "Interactive Live Ordering & Modifier Cart"
    return (
        f"### Bespoke Web Application Deployed for {clean_biz_name}\n"
        f"- **Live URL:** [{preview_url}]({preview_url})\n"
        f"- **UX Engine Mode:** `{mode_label}`\n"
        f"- **Catalog Populated:** `{len(blueprint.get('items', []))} authentic offerings` with modifier modals & live search\n"
        f"- **Multi-View Pages:** Overview & Catalog, Customer Reviews, and Direct Location/Inquiry Portal\n"
        f"- **Typography & Theme:** `{blueprint.get('theme', {}).get('heading_font')}` paired with custom brand hex variables"
    )

def send_iphone_push(title: str, message: str, priority: int = 0, sound: str = "cosmic") -> str:
    """Dispatches a push notification directly to user's iPhone via Pushover."""
    if not (PUSHOVER_USER_KEY and PUSHOVER_APP_TOKEN):
        return "Pushover credentials missing in environment variables."

    url = "https://api.pushover.net/1/messages.json"
    payload = urllib.parse.urlencode({
        "token": PUSHOVER_APP_TOKEN,
        "user": PUSHOVER_USER_KEY,
        "title": f"ARGUS // {title}",
        "message": message,
        "priority": priority,
        "sound": sound
    }).encode("utf-8")

    req = urllib.request.Request(
        url, 
        data=payload, 
        headers={"Content-Type": "application/x-www-form-urlencoded"}, 
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return "Notification delivered to iPhone."
    except Exception as exc:
        return f"Push dispatch failed: {str(exc)}"

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
            fact TEXT NOT NULL UNIQUE,
            category TEXT DEFAULT 'general',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Core Persistent Profile Seeds (survives all redeploys and restarts)
    permanent_profile_seeds = [
        ("The user's favorite algorithm is Dijkstra's algorithm.", "preference"),
        ("The user is Kyle Wilson, studying Computer Science at Temple University.", "profile"),
        ("The user prefers autonomous executive summaries with clean markdown tables and structured bullet points.", "instruction")
    ]

    for fact, category in permanent_profile_seeds:
        cursor.execute(
            "INSERT OR IGNORE INTO user_memories (fact, category) VALUES (?, ?)",
            (fact, category)
        )

    conn.commit()
    conn.close()

init_db()

def save_message(session_id: str, role: str, content: str):
    if not content or not content.strip():
        return
    try:
        conn = sqlite3.connect(DATABASE_URL)
        cursor = conn.cursor()
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        cursor.execute(
            "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, role, content.strip(), now_iso)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error saving message to DB: {e}")

def get_history(session_id: str, limit: int = 6) -> List[Dict[str, str]]:
    try:
        conn = sqlite3.connect(DATABASE_URL)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit)
        )
        rows = cursor.fetchall()
        conn.close()
        
        valid_history = []
        for r, c in reversed(rows):
            clean_content = (c or "").strip()
            # ADD THIS GUARD: Truncate past huge code/table dumps to protect TPM
            if len(clean_content) > 1200:
                clean_content = clean_content[:1200] + "... [prior details truncated]"
                
            if clean_content and not clean_content.startswith("{") and not clean_content.startswith("["):
                valid_history.append({"role": r, "content": clean_content})
        return valid_history
    except Exception as e:
        print(f"Error retrieving history: {e}")
        return []

def save_fact_tool(fact: str, category: Optional[str] = "general") -> str:
    clean_fact = fact.strip()
    if not clean_fact:
        return "Cannot save empty fact."
    try:
        conn = sqlite3.connect(DATABASE_URL)
        cursor = conn.cursor()
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        cursor.execute(
            "INSERT OR IGNORE INTO user_memories (fact, category, created_at) VALUES (?, ?, ?)",
            (clean_fact, category or "general", now_iso)
        )
        conn.commit()
        conn.close()
        return f"Successfully saved to permanent memory: '{clean_fact}'"
    except Exception as e:
        return f"Failed to save fact: {str(e)}"

def list_facts() -> List[str]:
    try:
        conn = sqlite3.connect(DATABASE_URL)
        cursor = conn.cursor()
        cursor.execute("SELECT fact FROM user_memories ORDER BY id ASC")
        rows = cursor.fetchall()
        conn.close()
        return [r[0] for r in rows if r[0]]
    except Exception as e:
        print(f"Error fetching facts: {e}")
        return []

def get_all_memories_records() -> List[Dict[str, Any]]:
    try:
        conn = sqlite3.connect(DATABASE_URL)
        cursor = conn.cursor()
        cursor.execute("SELECT id, fact, category, created_at FROM user_memories ORDER BY id DESC")
        rows = cursor.fetchall()
        conn.close()
        return [
            {"id": r[0], "fact": r[1], "category": r[2], "created_at": r[3]}
            for r in rows
        ]
    except Exception:
        return []

def delete_memory_by_id(memory_id: int) -> bool:
    try:
        conn = sqlite3.connect(DATABASE_URL)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM user_memories WHERE id = ?", (memory_id,))
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        return affected > 0
    except Exception:
        return False

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

def _normalize_iso_time(time_str: str) -> str:
    """Ensures ISO string conforms to RFC3339 for Google Calendar API."""
    clean = time_str.strip().replace(" ", "T")
    if not clean.endswith("Z") and "+" not in clean[10:] and "-" not in clean[10:]:
        clean += "-04:00"
    return clean

def get_drive_service():
    creds = get_google_credentials()
    if not creds:
        return None
    try:
        return build("drive", "v3", credentials=creds)
    except Exception:
        return None

def drive_search_tool(query: str, max_results: int = 5) -> str:
    """Searches Google Drive and Google Docs using full-text query matching across titles and contents."""
    service = get_drive_service()
    if not service:
        return "Google Drive integration is not active or OAuth credentials need updating."

    try:
        # fullText searches within Google Docs, PDFs, slides, and plain text
        # name contains searches filenames
        clean_q = query.replace("'", "\\'")
        drive_query = (
            f"(name contains '{clean_q}' or fullText contains '{clean_q}') "
            "and trashed = false"
        )

        results = service.files().list(
            q=drive_query,
            pageSize=max_results,
            fields="files(id, name, mimeType, webViewLink, modifiedTime, description)"
        ).execute()

        files = results.get("files", [])
        if not files:
            return f"No Google Drive or Docs files found matching: '{query}'."

        output = []
        for f in files:
            name = f.get("name", "Untitled")
            link = f.get("webViewLink", "")
            mime = f.get("mimeType", "")
            mod_time = f.get("modifiedTime", "")[:10]
            
            # Map MIME types to clean labels
            type_label = "Doc" if "document" in mime else (
                "Sheet" if "spreadsheet" in mime else (
                    "Slide" if "presentation" in mime else (
                        "PDF" if "pdf" in mime else "File"
                    )
                )
            )

            output.append(f"- **[{type_label}] [{name}]({link})**\n  *Last Modified:* {mod_time}")

        return f"### Google Drive Search Results for '{query}'\n\n" + "\n\n".join(output)

    except Exception as e:
        return f"Google Drive search error: {str(e)}"

# ==============================================================================
# Weather Integration Tool
# ==============================================================================

def get_weather(location: str = "Philadelphia") -> str:
    clean_loc = urllib.parse.quote(location.strip() or "Philadelphia")
    try:
        url = f"https://wttr.in/{clean_loc}?format=%l:+%c+%t,+%w,+Humidity:+%h&m"
        req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0"})
        with urllib.request.urlopen(req, timeout=5) as response:
            result = response.read().decode("utf-8").strip()
            if result and "Unknown location" not in result and "<html>" not in result:
                return result
    except Exception:
        pass
    try:
        url_sec = f"https://wttr.in/{clean_loc}?0&m&T"
        req_sec = urllib.request.Request(url_sec, headers={"User-Agent": "curl/7.68.0"})
        with urllib.request.urlopen(req_sec, timeout=5) as response:
            return response.read().decode("utf-8")[:600].strip()
    except Exception as exc:
        return f"Weather report currently unavailable for {location}: {str(exc)}"


# ==============================================================================
# Google Searching integration
# ==============================================================================

def google_search_grounding(query: str) -> str:
    """Executes native Google Search Grounding via Gemini, returning synthesized results with cited source links."""
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not gemini_key:
        return "GEMINI_API_KEY is not configured on Render."

    # Use the stable model endpoint syntax
    model_name = "gemini-2.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1/models/{model_name}:generateContent?key={gemini_key}"

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": query}]
            }
        ],
        "tools": [
            {"google_search": {}}
        ]
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        candidate = data.get("candidates", [{}])[0]
        text_parts = [
            p.get("text", "") 
            for p in candidate.get("content", {}).get("parts", []) 
            if "text" in p
        ]
        grounded_text = "".join(text_parts).strip()

        # Extract source citations and URLs
        metadata = candidate.get("groundingMetadata", {})
        chunks = metadata.get("groundingChunks", [])
        sources = []
        for c in chunks:
            web = c.get("web", {})
            title = web.get("title") or "Source"
            uri = web.get("uri")
            if uri and uri not in [s[1] for s in sources]:
                sources.append((title, uri))

        if sources:
            source_block = "\n\n**Verified Sources:**\n" + "\n".join([f"- [{t}]({u})" for t, u in sources[:4]])
            return f"{grounded_text}{source_block}"

        return grounded_text or "No grounded search data could be extracted."

    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8")
        # Direct fallback to DDGS scraper if the Gemini endpoint is temporarily unreachable
        fallback_results = web_search_tool(query)
        return f"{fallback_results}\n\n*(Note: Grounding endpoint returned HTTP {e.code}; served via resilient fallback)*"
    except Exception as e:
        return web_search_tool(query)

# ==============================================================================
# Canvas REST API Client & Sanitized Extraction Tools
# ==============================================================================

def _canvas_api_request(endpoint: str, method: str = "GET", payload: Optional[Dict[str, Any]] = None) -> Any:
    if not CANVAS_API_TOKEN and not CANVAS_SESSION_COOKIE:
        raise ValueError("Neither CANVAS_API_TOKEN nor CANVAS_SESSION_COOKIE is configured in environment variables.")

    clean_endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"
    url = f"{CANVAS_BASE_URL}/api/v1{clean_endpoint}"
    
    data_bytes = json.dumps(payload).encode("utf-8") if payload else None
    req = urllib.request.Request(url, data=data_bytes, method=method)
    
    if CANVAS_API_TOKEN:
        req.add_header("Authorization", f"Bearer {CANVAS_API_TOKEN}")
    elif CANVAS_SESSION_COOKIE:
        cookie_header = CANVAS_SESSION_COOKIE.strip().strip('"').strip("'")
        if not cookie_header.startswith("canvas_session="):
            cookie_header = f"canvas_session={cookie_header}"
        req.add_header("Cookie", cookie_header)
        req.add_header("Referer", f"{CANVAS_BASE_URL}/")
        req.add_header("Origin", CANVAS_BASE_URL)
        req.add_header("Sec-Fetch-Site", "same-origin")
        req.add_header("Sec-Fetch-Mode", "cors")
        req.add_header("Sec-Fetch-Dest", "empty")

    req.add_header("Accept", "application/json, text/plain, */*")
    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
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
            clean_desc = re.sub(r'<[^>]+>', ' ', desc_raw)
            clean_desc = " ".join(clean_desc.split())[:350]

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
                f"### {title} (ID: {a_id})\n"
                f"- **Due Date:** {due_at}\n"
                f"- **Points:** {points}\n"
                f"- **Description Snippet:** {clean_desc}...\n\n"
                f"**Grading Rubric:**\n{rubric_block}\n"
            )

        if not matches:
            return f"No assignments matched '{search_query}' in course {course_id}."

        return "\n\n".join(matches[:4])
    except Exception as e:
        return f"Failed to fetch Canvas rubric: {str(e)}"

def canvas_get_submission_grades(course_id: str, assignment_id: Optional[str] = None) -> str:
    try:
        if assignment_id:
            endpoint = f"/courses/{course_id}/assignments/{assignment_id}/submissions/self?include[]=submission_comments&include[]=rubric_assessment"
            sub = _canvas_api_request(endpoint)
            submissions = [sub] if sub else []
        else:
            endpoint = f"/courses/{course_id}/students/submissions/self?include[]=assignment&include[]=submission_comments&include[]=rubric_assessment&per_page=10"
            try:
                submissions = _canvas_api_request(endpoint)
            except Exception:
                endpoint = f"/courses/{course_id}/assignments?include[]=submission"
                assigns = _canvas_api_request(endpoint)
                submissions = [a.get("submission") for a in assigns if a.get("submission")]

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
                f"### {a_name}\n"
                f"- **Status:** {state.upper()}\n"
                f"- **Score:** {score} | **Grade:** {grade}\n"
                f"- **Submitted:** {sub_at}\n"
                f"- **Feedback:**\n{comment_block}\n"
                f"- **Rubric Assessment:**\n{rubric_eval_block}\n"
            )

        return "\n\n".join(results[:4])
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

            summaries.append(f"- **From:** {sender}\n  **Subject:** {subject}\n  **Date:** {date}\n  **Snippet:** {snippet}")

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
            f"### Email Message Details\n"
            f"- **ID:** {message_id}\n"
            f"- **From:** {sender}\n"
            f"- **Subject:** {subject}\n"
            f"- **Date:** {date}\n\n"
            f"**Content / Body:**\n{snippet}"
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
# Google Calendar & Resilient Search Tools
# ==============================================================================

def web_search_tool(query: str, max_results: int = 5) -> str:
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
            if results:
                output = []
                for r in results:
                    title = r.get("title", "Untitled")
                    body = r.get("body", "No description.")
                    href = r.get("href", "")
                    output.append(f"**[{title}]({href})**\n{body}")
                return "\n\n".join(output)
    except Exception:
        pass

    try:
        clean_q = urllib.parse.quote(query)
        url = f"https://html.duckduckgo.com/html/?q={clean_q}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            }
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            raw_html = resp.read().decode("utf-8", errors="ignore")

        snippets = re.findall(r'<a class="result__snippet[^>]*>(.*?)</a>', raw_html, re.DOTALL)
        titles = re.findall(r'<a class="result__url[^>]*href="([^"]*)"[^>]*>(.*?)</a>', raw_html, re.DOTALL)

        parsed = []
        for i in range(min(max_results, len(snippets))):
            clean_snip = re.sub(r'<[^>]+>', '', snippets[i]).strip()
            clean_title = re.sub(r'<[^>]+>', '', titles[i][1]).strip() if i < len(titles) else "News Item"
            if clean_snip:
                parsed.append(f"**{clean_title}**\n{clean_snip}")

        if parsed:
            return "\n\n".join(parsed)
    except Exception as e:
        return f"Live web search execution failed: {str(e)}"

    return f"No active search matches returned for query: '{query}'."

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
    result = [f"- **[{e['calendar']}]** {e['summary']} *(Starts: {e['start']})*" for e in events]
    return "\n".join(result)

def create_calendar_event(summary: str, start_time_iso: str, end_time_iso: str, description: Optional[str] = "") -> str:
    service = get_calendar_service()
    if not service:
        return "Google Calendar integration is not active or credentials are missing."
    try:
        formatted_start = _normalize_iso_time(start_time_iso)
        formatted_end = _normalize_iso_time(end_time_iso)

        event = {
            'summary': summary,
            'description': description or "",
            'start': {'dateTime': formatted_start},
            'end': {'dateTime': formatted_end},
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
            event['start'] = {'dateTime': _normalize_iso_time(start_time_iso)}
        if end_time_iso:
            event['end'] = {'dateTime': _normalize_iso_time(end_time_iso)}
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
        raise ValueError("GITHUB_TOKEN is not configured in environment variables.")

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
        repos = [f"- **{r['full_name']}** (Private: {r['private']}, Description: {r['description'] or 'None'})" for r in data]
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

        return "\n".join(files[:40]) if files else f"No files matching prefix '{path_prefix}'."
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
            if len(decoded) > 1500:
                decoded = decoded[:1500] + "\n...[truncated for context limits]..."
            return f"```\n{decoded}\n```"
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
# Reservation & Executive Briefing Engines
# ==============================================================================

def check_schedule_conflict(target_time_iso: str, duration_minutes: int = 120) -> str:
    try:
        normalized = _normalize_iso_time(target_time_iso)
        target_dt = datetime.datetime.fromisoformat(normalized.replace("Z", "+00:00"))
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
    search_info = web_search_tool(search_query, max_results=3)

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
        cal_desc = f"Reservation for {party_size} at {restaurant_name}.\nLocation: {location}\nOpenTable: {opentable_link}\nResy: {resy_link}\n\nSearch Intel:\n{search_info[:350]}"
        
        cal_result = create_calendar_event(cal_summary, start_iso, end_iso, description=cal_desc)
    except Exception as e:
        cal_result = f"Could not create calendar placeholder: {str(e)}"

    return (
        f"### RESERVATION DIRECTIVE PREPARED\n\n"
        f"- **Venue:** {restaurant_name} ({location})\n"
        f"- **Party Size:** {party_size}\n"
        f"- **Time Window:** {date_str} at {time_str}\n"
        f"- **Calendar Hold:** {cal_result}\n\n"
        f"**Direct Booking Portals:**\n"
        f"1. [OpenTable Portal]({opentable_link})\n"
        f"2. [Resy Portal]({resy_link})\n"
        f"3. [Google Maps / Reserve]({google_reserve_link})\n\n"
        f"**Venue Intel:**\n{search_info[:400]}"
    )

def executive_briefing_tool(location: Optional[str] = "Philadelphia, PA") -> str:
    target_loc = location.strip() if location and location.strip() else "Philadelphia, PA"
    now = datetime.datetime.now()
    today_str = now.strftime("%A, %B %d, %Y")
    
    weather_intel = get_weather(target_loc)
    gmail_intel = gmail_search_messages(query="is:unread", max_results=4)

    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone().isoformat()
    agenda_events = fetch_raw_calendar_events(time_min_iso=start_of_day, max_results=10)

    agenda_lines = []
    if agenda_events:
        for ev in agenda_events:
            cal_label = ev.get("calendar", "Calendar")
            summary = ev.get("summary", "Event")
            start = ev.get("start", "")
            agenda_lines.append(f"- **[{cal_label}]** {summary} *(Starts: {start})*")
        agenda_block = "\n".join(agenda_lines)
    else:
        agenda_block = "No events, deadlines, or exams scheduled for the next 48 hours."

    return (
        f"## EXECUTIVE BRIEFING FOR {today_str.upper()}\n\n"
        f"### Local Weather ({target_loc})\n{weather_intel}\n\n"
        f"### Actionable Inbox // Unread Messages\n{gmail_intel}\n\n"
        f"### Canvas & Google Calendar // Next 48 Hours\n{agenda_block}\n"
    )

def dispatch_contact_message(recipient_name: str, message_body: str) -> str:
    """Prepares an outbound SMS message to be dispatched to a personal contact."""
    return f"Prepared SMS dispatch to {recipient_name}: '{message_body}'"

# ==============================================================================
# Tool Schema Declarations & Dispatch
# ==============================================================================

tools_schema = [
    {
        "type": "function",
        "function": {
            "name": "dispatch_contact_message",
            "description": "Used when the user requests to text, SMS, or message an individual person from their personal iPhone contacts list.",
            "parameters": {
                "type": "object",
                "properties": {
                    "recipient_name": {
                        "type": "string",
                        "description": "The exact first or full name of the contact as saved in the phone book (e.g., 'Bryant Basketball', 'Mom', 'Alex')."
                    },
                    "message_body": {
                        "type": "string",
                        "description": "The text message content to send."
                    }
                },
                "required": ["recipient_name", "message_body"]
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
                    "location": {
                        "type": "string",
                        "description": "City and state for weather lookup. Defaults to 'Philadelphia, PA' if null or omitted."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "exec_briefing_tool",
            "description": "Alias for executive_briefing_tool. Compiles an executive briefing of weather, unread emails, and schedule.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "City and state for weather lookup. Defaults to 'Philadelphia, PA'."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather conditions and forecast for a given location.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City name, zip code, or region"}
                },
                "required": ["location"]
            }
        }
    },
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
    }, 
    {
        "type": "function",
        "function": {
            "name": "send_iphone_push",
            "description": "Sends an instant high-priority alert or notification directly to the user's iPhone lock screen.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short headline for the alert banner."},
                    "message": {"type": "string", "description": "Body message or brief to transmit."}
                },
                "required": ["title", "message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "google_search_grounding",
            "description": "Performs an authoritative live web search using Google Search Grounding. Use for real-time news, current sports scores, research papers, documentation, or facts requiring live verification.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The precise search query."
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "google_search_tool",
            "description": "Alias for google_search_grounding. Performs an authoritative live web search using Google Search Grounding.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query."
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "drive_search_tool",
            "description": "Searches Google Drive and Google Docs by keyword across file titles and full body content. Use when the user asks to find, search, or look up notes, slides, documents, or coursework stored in Drive.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search keyword, topic, or document title."
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of files to return (default 5)."
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_client_website_tool",
            "description": "Generates a responsive, modern demo landing page for a local business (food, home services, contractors, retail) and deploys it to a live preview URL for pitch outreach.",
            "parameters": {
                "type": "object",
                "properties": {
                    "business_name": {
                        "type": "string",
                        "description": "Name of the business (e.g., 'Angelo\\'s Pizzeria')"
                    },
                    "niche": {
                        "type": "string",
                        "description": "Type of business (e.g., 'Italian Restaurant', 'HVAC & Plumbing')"
                    },
                    "location": {
                        "type": "string",
                        "description": "City or service area (e.g., 'South Philadelphia, PA')"
                    },
                    "contact_phone": {
                        "type": ["string", "null"],
                        "description": "Optional phone number. Pass null or empty string if unknown."
                    },
                    "highlights": {
                        "type": ["string", "null"],
                        "description": "Key services, menu items, or value propositions. Pass null if none provided."
                    }
                },
                "required": ["business_name", "niche", "location"]
            }
        }
    },
]

tool_dispatch = {
    "dispatch_contact_message": dispatch_contact_message,
    "executive_briefing_tool": executive_briefing_tool,
    "exec_briefing_tool": executive_briefing_tool,
    "get_weather": get_weather,
    "canvas_list_courses": canvas_list_courses,
    "canvas_get_assignment_rubric": canvas_get_assignment_rubric,
    "canvas_get_submission_grades": canvas_get_submission_grades,
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
    "delete_calendar_event": delete_calendar_event,
    "send_iphone_push": send_iphone_push, 
    "google_search_grounding": google_search_grounding,
    "google_search_tool": google_search_grounding,
    "web_search_tool": google_search_grounding,
    "drive_search_tool": drive_search_tool,
    "generate_client_website_tool": generate_client_website_tool
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
    action: Optional[str] = "reply"
    recipient: Optional[str] = None
    body: Optional[str] = None

@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "ARGUS", "model": GROQ_MODEL}

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

# ==============================================================================
# Core Chat & Deterministic Execution Pipeline
# ==============================================================================

@app.post("/chat", response_model=ChatResponse)
@app.post("/chat/", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest, token: str = Depends(verify_token)):
    if not groq_client:
        return ChatResponse(reply="GROQ_API_KEY is not configured on the backend.", session_id=request.session_id or "")

    session_id = request.session_id or "default_session"
    user_input = request.message.strip()

    # Fast-path intent heuristics for voice and text shorthand
    clean_lower = user_input.lower().strip()
    
    # Texting / Messaging intents
    # Matches: "tell [Name] ...", "text [Name] ...", "msg [Name] ...", "let [Name] know ..."
    msg_match = re.match(r'^(?:yo argus[, ]*)?(?:tell|text|msg|message|send a text to|let)\s+([a-zA-Z0-9\s]+?)\s+(?:that|to|saying|:)?\s*(.+)$', clean_lower, re.IGNORECASE)
    
    # If a clear text directive is detected and Groq might bypass it, enforce tool choice
    force_tool_choice = "auto"
    if any(clean_lower.startswith(prefix) for prefix in ["text ", "tell ", "msg ", "message "]):
        force_tool_choice = {"type": "function", "function": {"name": "dispatch_contact_message"}}

    # ADD THIS: Force website generator tool on web design directives
    web_keywords = ["website", "landing page", "demo site", "web page"]
    action_keywords = ["generate", "create", "build", "make", "deploy"]
    if any(act in clean_lower for act in action_keywords) and any(web in clean_lower for web in web_keywords):
        match = re.search(r'(?:for|of)\s+([A-Za-z0-9\s\'\-]+?)(?:\s+in\s+([A-Za-z0-9\s,\.\-]+?))?(?:\s+(?:featuring|specializing in|offering|with)\s+(.+))?$', user_input, re.IGNORECASE)
        
        biz_name = match.group(1).strip() if match else "Local Business"
        loc = match.group(2).strip() if (match and match.group(2)) else "Philadelphia, PA"
        highlights = match.group(3).strip() if (match and match.group(3)) else "Locally owned, 5-star customer reviews"
        niche = "Italian Restaurant & Pizzeria" if any(w in clean_lower for w in ["pizza", "pizzeria", "hoagie", "food", "pasta"]) else "Contractor & Home Services"
        
        tool_result = generate_client_website_tool(
            business_name=biz_name,
            niche=niche,
            location=loc,
            contact_phone=None,
            highlights=highlights
        )
        save_message(session_id, "user", user_input)
        save_message(session_id, "assistant", tool_result)
        return ChatResponse(reply=tool_result, session_id=session_id)


    # Define timestamps and memory facts BEFORE building system_prompt
    current_time_str = datetime.datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")
    facts = list_facts()
    facts_block = "\n".join([f"- {f}" for f in facts]) if facts else "No permanent facts recorded yet."

    system_prompt = (
        f"You are ARGUS, an adaptive autonomous executive AI assistant. Current time: {current_time_str}.\n"
        "You possess advanced intent detection trained on conversational speech-to-text dictation and fast keyboard texting.\n\n"
        "=== COMMUNICATION & INTENT INTERPRETATION PROTOCOL ===\n"
        "- **Speech Dictation Adaptation**: The user may dictate hands-free via voice, resulting in run-on sentences, phonetic slips, or filler words ('yo ARGUS', 'uh', 'like', 'can you just go ahead and'). Strip conversational padding and isolate the underlying directive.\n"
        "- **Keyboard Texting / Shorthand Adaptation**: The user may text informally using slang, abbreviations, or shorthand (e.g., 'aight', 'imma', 'hmu', 'sched', 'msg', 'tmrw', 'rn', 'wuts'). Interpret these fluently without lecturing or asking for rephrasing.\n"
        "- **Ambiguous Contact Directives**: Phrasings like 'tell Bryant we hit the gym at 6', 'let Mom know I'm on the way', 'text alex whats up' MUST be resolved directly as `dispatch_contact_message`.\n"
        "- **Contextual Summaries**: If the user says 'summarize it', 'clean that up', or 'break that down' following a data fetch or search, synthesize existing chat history into executive bullet points without invoking web search tools again.\n\n"
        "=== PERMANENT LONG-TERM FACTS (GROUND TRUTH) ===\n"
        f"{facts_block}\n"
        "===============================================\n\n"
        "OPERATIONAL DIRECTIVES:\n"
        "1. For texting/messaging people: call 'dispatch_contact_message' with exact contact name and cleaned message body.\n"
        "2. For briefings/morning updates/day overviews: call 'executive_briefing_tool'.\n"
        "3. For real-time facts/sports/news: call 'google_search_grounding'.\n"
        "4. Output format: Clear, concise executive markdown with bold highlights and structured lists."
    )

    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    
    # Load clean history records from SQLite
    history_records = get_history(session_id, limit=6)
    for r in history_records:
        role_label = "user" if r["role"] == "user" else "assistant"
        messages.append({"role": role_label, "content": r["content"]})

    messages.append({"role": "user", "content": user_input})
    save_message(session_id, "user", user_input)

# Guarantee system prompt at index 0 and no more than the last 4 recent exchanges
    if len(messages) > 6:
        messages = [messages[0]] + messages[-5:]

    reply_text = ""
    max_turns = 4
    turn_count = 0
    contact_action = None
    contact_recipient = None
    contact_body = None

    try:
        # Guarantee system prompt at index 0 and no more than the last 4 recent exchanges
        if len(messages) > 5:
            messages = [messages[0]] + messages[-4:]
        while turn_count < max_turns:
            turn_count += 1
            response = None

            for attempt in range(3):
                try:
                    response = groq_client.chat.completions.create(
                        model=GROQ_MODEL,
                        messages=messages,
                        tools=tools_schema,
                        tool_choice=force_tool_choice,
                        temperature=0.3
                    )
                    break
                except RateLimitError:
                    if attempt < 2:
                        time.sleep(2.0 * (attempt + 1))
                        continue
                    reply_text = "Groq rate limit reached. Please wait a moment and try again."
                    break
                except Exception as api_err:
                    err_str = str(api_err)
                    if "exec_briefing_tool" in err_str:
                        # Fallback for hallucinated tool name
                        reply_text = executive_briefing_tool("Philadelphia, PA")
                        break
                    if attempt < 2 and "429" in err_str:
                        time.sleep(2.0)
                        continue
                    reply_text = f"Groq API error: {err_str}"
                    break

            if reply_text or not response or not response.choices:
                break

            response_msg = response.choices[0].message

            # Case 1: Model returned text without requesting any tools
            if not response_msg.tool_calls:
                content = (response_msg.content or "").strip()
                if not content and hasattr(response_msg, "reasoning_content"):
                    content = (getattr(response_msg, "reasoning_content") or "").strip()

                if content:
                    reply_text = content
                    break

                # Post-tool synthesis turn returned empty content
                if turn_count > 1:
                    tool_outputs = [
                        str(m.get("content", "")) for m in messages 
                        if isinstance(m, dict) and m.get("role") == "tool"
                    ]
                    
                    try:
                        clean_synth_messages = list(messages)
                        clean_synth_messages.append({
                            "role": "user",
                            "content": "Synthesize the results above into a clear, direct executive summary."
                        })
                        forced_res = groq_client.chat.completions.create(
                            model=GROQ_MODEL,
                            messages=clean_synth_messages,
                            temperature=0.3
                        )
                        forced_msg = forced_res.choices[0].message
                        forced_content = (forced_msg.content or getattr(forced_msg, "reasoning_content", "") or "").strip()
                        if forced_content:
                            reply_text = forced_content
                            break
                    except Exception as synth_err:
                        print(f"Forced synthesis error: {synth_err}")

                    if tool_outputs:
                        reply_text = "\n\n".join(tool_outputs)
                    else:
                        reply_text = "Action executed, but no content was returned by the tool."
                    break
                else:
                    reply_text = "Directive received."
                    break

            # Case 2: Model emitted tool call requests
            assistant_payload: Dict[str, Any] = {
                "role": "assistant",
                "content": response_msg.content or "",
            }
            if response_msg.tool_calls:
                assistant_payload["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in response_msg.tool_calls
                ]

            messages.append(assistant_payload)

            for tool_call in response_msg.tool_calls:
                fn_name = tool_call.function.name
                fn_args = {}
                if tool_call.function.arguments:
                    try:
                        fn_args = json.loads(tool_call.function.arguments)
                    except Exception:
                        fn_args = {}

                # Route any search-related hallucinated name to google_search_grounding
                if "search" in fn_name and fn_name not in tool_dispatch:
                    fn_name = "google_search_grounding"

                # Intercept contact messaging to immediately package iOS Shortcut payload
                if fn_name == "dispatch_contact_message":
                    contact_recipient = fn_args.get("recipient_name", "")
                    contact_body = fn_args.get("message_body", "")
                    contact_action = "send_contact_sms"
                    reply_text = f"Dispatched text to {contact_recipient}."
                    save_message(session_id, "assistant", reply_text)
                    return ChatResponse(
                        reply=reply_text,
                        session_id=session_id,
                        action=contact_action,
                        recipient=contact_recipient,
                        body=contact_body
                    )

                if fn_name in tool_dispatch:
                    try:
                        # Clean arguments: remove None values so defaults can take effect
                        clean_fn_args = {k: v for k, v in fn_args.items() if v is not None}
                        fn_result = tool_dispatch[fn_name](**clean_fn_args)
                    except Exception as exc:
                        fn_result = f"Tool failure: {type(exc).__name__} - {str(exc)}"
                else:
                    fn_result = f"Error: Tool '{fn_name}' not recognized."

                trimmed_result = str(fn_result)
                if len(trimmed_result) > 1200:
                    trimmed_result = trimmed_result[:1200] + "... [truncated]"

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": trimmed_result
                })

        # Final loop exit guard: surface tool data if reply_text remained empty
        if not reply_text:
            tool_outputs = [
                str(m.get("content", "")) for m in messages 
                if isinstance(m, dict) and m.get("role") == "tool"
            ]
            if tool_outputs:
                reply_text = "\n\n".join(tool_outputs)
            else:
                reply_text = "Action completed."

    except Exception as e:
        reply_text = f"ARGUS backend runtime exception: {type(e).__name__} - {str(e)}"

    # Save only non-empty assistant messages to avoid database corruption
    if reply_text and reply_text.strip():
        save_message(session_id, "assistant", reply_text.strip())

    return ChatResponse(
        reply=reply_text, 
        session_id=session_id,
        action=contact_action or "reply",
        recipient=contact_recipient,
        body=contact_body
    )
