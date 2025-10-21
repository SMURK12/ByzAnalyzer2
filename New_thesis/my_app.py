#!/usr/bin/env python3
# merged_app.py - combines demographics (app.py) and maps endpoints (backend.py)
from flask import Flask, request, jsonify, send_from_directory,session

from flask_cors import CORS
import json
from dotenv import load_dotenv
import os, time, logging, traceback
from urllib.parse import urlparse, parse_qs
from utils.foottraffic_helper import top_closest_with_foot_traffic
import requests
import psycopg2
import psycopg2.extras
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from openai import OpenAI
import tempfile

import random
from datetime import datetime, timedelta
from otp_email import send_otp_email



load_dotenv()

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("merged-app")

DATABASE_URL = os.getenv("DATABASE_URL")
SECRET_KEY = os.getenv("FLASK_SECRET_KEY") or "change_this_in_production"

if not DATABASE_URL:
    raise SystemExit("Please set DATABASE_URL environment variable")




# Create Flask app (single instance) and configure session secret
app = Flask(__name__, static_folder='static', static_url_path='/static')
app.secret_key = SECRET_KEY
app.config["SESSION_COOKIE_HTTPONLY"] = True

# Enable CORS and allow cookies to be sent by fetch() from same-origin or cross-origin (if configured)
CORS(app, supports_credentials=True)


def _normalize_email(e):
    return (e or "").strip().lower()

# ----------------- Optional imports for Google maps helpers -----------------
# try to import your establishments1 module (placed under utils/ or project root)
try:
    from utils.establishments1 import GoogleMapsService, Address, Establishments
except Exception:
    try:
        from establishments1 import GoogleMapsService, Address, Establishments
    except Exception as e:
        GoogleMapsService = Address = Establishments = None
        logger.warning("Could not import establishments1 (GoogleMapsService, Address, Establishments). Map endpoints will error if used.")
        logger.debug(e)

# ---------------------- Business AI ----------------------------------
try:
    from utils.businessai import BusinessAI
except Exception as e:
    BusinessAI = None
    logger.warning("Could not import BusinessAI (businessai.py). /generate_analysis will fallback to a mock response. Error: %s", e)

# ------------------ OTP ----------------------------------
try:
    OTP_TTL_SECONDS = int(os.getenv('OTP_TTL_SECONDS', '300'))  # 5 minutes default
except ValueError:
    OTP_TTL_SECONDS = 300

MAX_OTP_ATTEMPTS = 3


# ----------------- Postgres demographics helpers (from app.py) -----------------
DEFAULT_MUNICIPALITY = os.getenv("DEFAULT_MUNICIPALITY", "LAS PIÑAS")
DEMOGRAPHICS_SCHEMA = os.getenv("DEMOGRAPHICS_SCHEMA", "public")
DEMOGRAPHICS_TABLE  = os.getenv("DEMOGRAPHICS_TABLE", "demographics")
BESTTIME_PRIVATE_KEY = os.getenv('BESTTIME_PRIVATE') or os.getenv('BESTTIME_API_KEY_PRIVATE') or os.getenv('BESTTIME_PRIVATE_KEY')
BESTTIME_BASE = os.getenv('BESTTIME_BASE', 'https://besttime.app/api/v1')
if not BESTTIME_PRIVATE_KEY:
    logger.warning("BESTTIME_PRIVATE key is not set in environment. Requests will fail until you set it.")

# ------------------ OTP helper functions ------------------------------
def generate_otp() -> str:
    """Generate a 6-digit OTP code"""
    return ''.join([str(random.randint(0, 9)) for _ in range(6)])

def store_otp(conn, email: str, otp: str):
    """Store OTP in database with expiration"""
    expires_at = datetime.utcnow() + timedelta(seconds=OTP_TTL_SECONDS)
    
    with conn.cursor() as cur:
        # First, invalidate any existing OTPs for this email
        cur.execute("""
            UPDATE otp_verifications 
            SET is_verified = TRUE 
            WHERE email = %s AND is_verified = FALSE
        """, (email,))
        
        # Insert new OTP
        cur.execute("""
            INSERT INTO otp_verifications (email, otp_code, expires_at)
            VALUES (%s, %s, %s)
            RETURNING id
        """, (email, otp, expires_at))
        
        return cur.fetchone()["id"]

def verify_otp(conn, email: str, otp: str) -> tuple[bool, str]:
    """
    Verify OTP code for email.
    Returns: (success: bool, message: str)
    """
    with conn.cursor() as cur:
        # Get the latest unverified OTP for this email
        cur.execute("""
            SELECT id, otp_code, expires_at, attempts
            FROM otp_verifications
            WHERE email = %s AND is_verified = FALSE
            ORDER BY created_at DESC
            LIMIT 1
        """, (email,))
        
        row = cur.fetchone()
        
        if not row:
            return False, "No verification code found. Please request a new one."
        
        # Check if expired
        if datetime.utcnow() > row["expires_at"]:
            return False, "Verification code has expired. Please request a new one."
        
        # Check attempts
        if row["attempts"] >= MAX_OTP_ATTEMPTS:
            return False, "Too many failed attempts. Please request a new code."
        
        # Verify OTP
        if row["otp_code"] == otp:
            # Mark as verified
            cur.execute("""
                UPDATE otp_verifications
                SET is_verified = TRUE
                WHERE id = %s
            """, (row["id"],))
            return True, "Email verified successfully!"
        else:
            # Increment attempts
            cur.execute("""
                UPDATE otp_verifications
                SET attempts = attempts + 1
                WHERE id = %s
            """, (row["id"],))
            
            remaining = MAX_OTP_ATTEMPTS - (row["attempts"] + 1)
            if remaining > 0:
                return False, f"Invalid code. {remaining} attempt(s) remaining."
            else:
                return False, "Invalid code. Maximum attempts reached. Please request a new code."
# ------------------ End of OTP helper functions ------------------------------

def get_conn_params():
    url = os.getenv("DATABASE_URL")
    if url:
        if "sslmode=" in url:
            return {"dsn": url}
        return {"dsn": f"{url}?sslmode=require"}
    host = os.getenv("PGHOST")
    port = int(os.getenv("PGPORT", "5432"))
    db   = os.getenv("PGDATABASE")
    user = os.getenv("PGUSER")
    pwd  = os.getenv("PGPASSWORD")
    missing = [k for k,v in {"PGHOST":host,"PGDATABASE":db,"PGUSER":user,"PGPASSWORD":pwd}.items() if not v]
    if missing:
        raise RuntimeError(f"Missing env vars: {', '.join(missing)} (or set DATABASE_URL).")
    return {"host":host,"port":port,"dbname":db,"user":user,"password":pwd,"sslmode":"require"}

_db_conn = None
def connect_with_retries(retries: int = 5, delay: float = 2.0):
    import psycopg2
    from psycopg2.extras import RealDictCursor
    params = get_conn_params()
    last_err = None
    for i in range(1, retries+1):
        try:
            if "dsn" in params:
                conn = psycopg2.connect(params["dsn"], cursor_factory=RealDictCursor)
            else:
                conn = psycopg2.connect(cursor_factory=RealDictCursor, **params)
            conn.autocommit = True
            logger.info("Connected to Postgres")
            return conn
        except Exception as e:
            last_err = e
            logger.warning("Postgres connect attempt %d failed: %s", i, e)
            time.sleep(delay)
    logger.exception("All connection attempts failed")
    raise last_err

def get_db_conn():
    global _db_conn
    try:
        if _db_conn is None or getattr(_db_conn, "closed", True):
            _db_conn = connect_with_retries()
        else:
            try:
                with _db_conn.cursor() as c:
                    c.execute("SELECT 1")
            except Exception:
                logger.info("DB connection broken; reconnecting.")
                _db_conn = connect_with_retries()
        return _db_conn
    except Exception:
        logger.exception("Unable to obtain DB connection.")
        raise

def query_demographics(conn, municipality: str, schema: str = DEMOGRAPHICS_SCHEMA, table: str = DEMOGRAPHICS_TABLE):
    municipality = (municipality or DEFAULT_MUNICIPALITY).strip()
    sql = f'''
    SELECT *
    FROM "{schema}"."{table}"
    WHERE translate("Municipality", 'Ññ', 'Nn') ILIKE translate(%s, 'Ññ', 'Nn')
    LIMIT 1000;
    '''
    pattern = f"%{municipality}%"
    with conn.cursor() as cur:
        cur.execute(sql, (pattern,))
        rows = cur.fetchall()
    return rows

# ----------------- Flask app -----------------
# Serve the integrated HTML at root. Put the new temp3.html in the static folder.

@app.route('/')
def index():
    # try to serve static/temp3.html
    try:
        return send_from_directory(app.static_folder, 'index123.html') 
    except Exception:
        return jsonify({"ok": False, "error": "temp3.html not found in static/"}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status":"ok"}), 200

@app.route('/ping', methods=['GET'])
def ping():
    return jsonify({"ok": True, "message":"pong"}), 200

# ----------------- Demographics endpoints (from app.py) -----------------
@app.route('/demographics', methods=['GET'])
def demographics_get():
    municipality = request.args.get('municipality') or DEFAULT_MUNICIPALITY
    try:
        conn = get_db_conn()
        rows = query_demographics(conn, municipality)
        return jsonify({"municipality": municipality, "rows": rows}), 200
    except Exception as e:
        logger.exception("GET /demographics failed")
        return jsonify({"error":"Server error","detail": str(e)}), 500

@app.route('/integration', methods=['POST','OPTIONS'])
def integration_post():
    if request.method == 'OPTIONS': return jsonify({}), 200
    data = request.get_json(silent=True) or {}
    municipality = data.get('municipality') or DEFAULT_MUNICIPALITY
    try:
        conn = get_db_conn()
        rows = query_demographics(conn, municipality)
        return jsonify({"municipality": municipality, "rows": rows}), 200
    except Exception as e:
        logger.exception("POST /integration failed")
        return jsonify({"error":"Server error","detail": str(e)}), 500

@app.route('/admin/sample', methods=['GET'])
def admin_sample():
    try:
        limit = int(request.args.get('limit', 10))
        conn = get_db_conn()
        sql = f'SELECT * FROM "{DEMOGRAPHICS_SCHEMA}"."{DEMOGRAPHICS_TABLE}" LIMIT %s'
        with conn.cursor() as cur:
            cur.execute(sql, (limit,))
            rows = cur.fetchall()
        return jsonify({"rows": rows}), 200
    except Exception as e:
        logger.exception("GET /admin/sample failed")
        return jsonify({"error":"Server error","detail": str(e)}), 500

# ----------------- Maps / Places endpoints (from backend.py) -----------------
@app.route('/nearby_places', methods=['POST'])
def nearby_places():
    data = request.get_json(silent=True) or {}
    try:
        lat = float(data.get('latitude', 0))
        lng = float(data.get('longitude', 0))
        radius = int(data.get('radius', 1500))
    except Exception:
        return jsonify({"ok":False,"error":"Invalid latitude/longitude/radius"}), 400
    if GoogleMapsService is None:
        return jsonify({"ok":False,"error":"GoogleMapsService not available (import failed)"}), 500
    try:
        gm = GoogleMapsService()
        places = gm.get_nearby_places(lat,lng,radius)
        return jsonify({"ok":True,"data":{"competitors": places}}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok":False,"error":str(e)}), 500

@app.route('/submit_establishment', methods=['POST'])
def submit_establishment():
    data = request.get_json(silent=True) or {}
    try:
        lat = float(data['latitude'])
        lng = float(data['longitude'])
        business_type = data.get('business_type', 'other')
        description = data.get('description', '')
    except KeyError:
        return jsonify({"ok": False, "error": "latitude and longitude required"}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"Invalid input: {str(e)}"}), 400

    # 1) Try to use address_components from client first (preferred)
    address_components = data.get('address_components') or {}

    # 2) If not provided and GoogleMapsService is available, try server-side get_address_components
    municipality = address_components.get('municipality') or ''
    if (not municipality) and GoogleMapsService is not None:
        try:
            gm = GoogleMapsService()
            comps = gm.get_address_components(lat, lng) or {}
            # merge server geocode comps into address_components (don't overwrite client entries)
            for k, v in comps.items():
                if k not in address_components or not address_components.get(k):
                    address_components[k] = v
            municipality = address_components.get('municipality') or address_components.get('barangay') or ''
        except Exception as e:
            # Log but continue — we will just return empty population if geocode fails
            logger.exception("Failed to get address components from GoogleMapsService: %s", e)

    # 3) Attempt to fetch competitors via Establishments if available
    competitors = []
    other_establishments = []
    
    if GoogleMapsService is not None and Address is not None and Establishments is not None:
        try:
            print('Using AI to identify competitors...')
            # Create proper Address object
            addr_obj = Address(
                barangay=address_components.get('barangay', ''),
                municipality=address_components.get('municipality', ''),
                province=address_components.get('province', ''),
                region=address_components.get('region', '')
            )
            
            # Create Establishments object (this fetches all nearby places)
            est = Establishments(
                latitude=lat,
                longitude=lng,
                business_type=business_type,
                address=addr_obj,
                description=description,
                radius=2000
            )
            
            # Get all nearby establishments
            all_nearby = est.nearby_establishments
            
            # Use AI to identify competitors
            if BusinessAI is not None:
                ai = BusinessAI(
                    target_business_type=business_type,
                    target_lat=lat,
                    target_lng=lng,
                    target_description=description,
                    nearby_establishments=[],
                    competitors=[],
                    other_establishments=[],
                    foot_traffic=[],
                    demographics={}
                )
                competitor_result = ai.identify_competitors_with_ai(all_nearby)
                
                competitors = competitor_result['competitors']
                
                # Get other establishments (non-competitors)
                competitor_indices_set = set(competitor_result['competitor_indices'])
                other_establishments = [
                    est for idx, est in enumerate(all_nearby)
                    if idx not in competitor_indices_set
                ]
                
                # Optionally log the reasoning
                logger.info("AI Competitor Analysis: %s", competitor_result.get('reasoning'))
            else:
                # Fallback to old method if BusinessAI not available
                competitors = est.competitors
                other_establishments = est.other_establishments
            
        except Exception:
            logger.exception("Error in AI competitor identification")
            competitors = []
            other_establishments = []

    # 4) Query demographics (population) by municipality — aggregate basic stats
    population_stats = {}
    if municipality:
        try:
            conn = get_db_conn()
            rows = query_demographics(conn, municipality)
            # rows might be many; aggregate expected numeric fields if present
            # We'll try to sum fields: Total_MF, Total_M, Total_F, Child_MF, Teen_MF, YoungAdult_MF, Adult_MF, Senior_MF
            aggregated = {
                'total': 0,
                'male': 0,
                'female': 0,
                'children': 0,
                'teens': 0,
                'young_adults': 0,
                'adults': 0,
                'seniors': 0,
                'rows_count': len(rows)
            }
            for r in rows:
                # defensive: keys may vary by schema - try multiple key names
                def getnum(d, *keys):
                    for k in keys:
                        if k in d and d[k] is not None:
                            try:
                                return int(d[k])
                            except Exception:
                                pass
                    return 0
                aggregated['total'] += getnum(r, 'Total_MF', 'total', 'Total')
                aggregated['male']  += getnum(r, 'Total_M', 'male')
                aggregated['female']+= getnum(r, 'Total_F', 'female')
                aggregated['children'] += getnum(r, 'Child_MF', 'children', 'child_mf')
                aggregated['teens'] += getnum(r, 'Teen_MF', 'teens', 'teen_mf')
                aggregated['young_adults'] += getnum(r, 'YoungAdult_MF', 'young_adults')
                aggregated['adults'] += getnum(r, 'Adult_MF', 'adults')
                aggregated['seniors'] += getnum(r, 'Senior_MF', 'seniors')
            population_stats = aggregated
        except Exception:
            logger.exception("Failed to query or aggregate demographics for municipality: '%s'", municipality)
            population_stats = {}
    else:
        # no municipality found — return empty population info
        population_stats = {}

    response_payload = {
        "ok": True,
        "data": {
            "competitors": competitors,
            "other_establishments": other_establishments,
            "address_components": address_components,
            "population": population_stats
        }
    }
    return jsonify(response_payload), 200

@app.route('/generate_analysis', methods=['POST'])
def generate_analysis():
    """
    Accepts the analysis payload (see buildAnalysisPayload() in index123.html),
    uses BusinessAI if available to create an analysis, and returns JSON:
      { ok: True, analysis: "<string>", selected_barangays: [...] }
    """
    data = request.get_json(silent=True) or {}

    # Basic validation / extraction
    target = data.get("target_location") or {}
    lat = target.get("lat") or target.get("latitude") or None
    lng = target.get("lng") or target.get("longitude") or None

    if lat is None or lng is None:
        return jsonify({"ok": False, "error": "Missing target_location lat/lng"}), 400

    business_type = data.get("business_type") or ""
    description = data.get("description") or ""
    competitors = data.get("competitors") or []
    other_est = data.get("other_establishments") or []
    demographics = data.get("population_summary") or {}
    selected_barangays = data.get("selected_barangays") or []
    foot_traffic = data.get("foot_traffic") or []


    # If BusinessAI available, use it; otherwise return a safe mock
    if BusinessAI is None:
        # Fallback: simple text describing that AI module isn't available.
        mock = (
            "⚠️ BusinessAI module not available on server. "
            "This is a mock analysis. Install/enable businessai.py to get real AI output.\n\n"
            f"Business type: {business_type}\n"
            f"Location: {lat}, {lng}\n"
            f"Competitors found: {len(competitors)}\n"
            f"Foot-traffic venues: {len(foot_traffic)}\n"
            f"Population total (if provided): {demographics.get('total', '—')}\n\n"
            "Recommendations: (mock) evaluate competitor strength, validate foot traffic, test a small pilot."
        )
        return jsonify({"ok": True, "analysis": mock, "selected_barangays": selected_barangays}), 200

    try:
        # instantiate BusinessAI (signature: target_business_type, lat, lng, description, nearby_establishments, competitors, other_establishments, demographics)
        ai = BusinessAI(business_type, float(lat), float(lng), description, [], competitors, other_est, foot_traffic, demographics)
        result = ai.get_analysis()
        
        # Handle tuple return (text, warnings) or just text
        if isinstance(result, tuple):
            analysis_text = result[0]  # First element is the text
            warnings = result[1] if len(result) > 1 else []
        else:
            analysis_text = result
            warnings = []
        
        return jsonify({"ok": True, "analysis": analysis_text, "selected_barangays": selected_barangays}), 200
    except Exception as e:
        logger.exception("generate_analysis failed")
        return jsonify({"ok": False, "error": f"Server error: {e}"}), 500

# ----------------- Foot traffic endpoints (from besttime.py) -----------------
def besttime_post_qs(endpoint: str, params: dict, timeout: int = 30):
    """POST with query-string params to BestTime (API expects POST + query string)."""
    if not BESTTIME_PRIVATE_KEY:
        raise ValueError("BestTime private API key not configured in environment")
    qparams = {**params, "api_key_private": BESTTIME_PRIVATE_KEY}
    url = f"{BESTTIME_BASE.rstrip('/')}/{endpoint.lstrip('/')}"
    logger.info("POST %s params=%s", url, {k: v for k, v in qparams.items() if k != 'api_key_private'})
    r = requests.post(url, params=qparams, timeout=timeout)
    if not r.ok:
        # Try to surface JSON error if present
        try:
            return {"error": f"{r.status_code} {r.reason}", "details": r.json()}, r.status_code
        except Exception:
            return {"error": f"{r.status_code} {r.reason}", "details": r.text}, r.status_code
    return r.json()


def besttime_get_json(endpoint: str, params: dict, timeout: int = 30):
    url = f"{BESTTIME_BASE.rstrip('/')}/{endpoint.lstrip('/')}"
    logger.info("GET %s params=%s", url, params)
    r = requests.get(url, params=params, timeout=timeout)
    if not r.ok:
        try:
            return {"error": f"{r.status_code} {r.reason}", "details": r.json()}, r.status_code
        except Exception:
            return {"error": f"{r.status_code} {r.reason}", "details": r.text}, r.status_code
    return r.json()


def wait_for_progress_and_get_venues(job_id: str = None, collection_id: str = None, progress_url: str = None,
                                     timeout_seconds: int = 30, interval_seconds: int = 2):
    """
    Poll the BestTime venues/progress endpoint until 'venues' are present or timeout.
    Returns:
      (venues_list, last_response_dict)
    On timeout returns (None, last_response_dict).
    """
    if progress_url:
        # try to extract query params if present
        parsed = urlparse(progress_url)
        qs = parse_qs(parsed.query)
        job_id = job_id or (qs.get('job_id', [None])[0])
        collection_id = collection_id or (qs.get('collection_id', [None])[0])

    if not job_id or not collection_id:
        raise ValueError("Either job_id+collection_id or progress_url must be provided")

    deadline = time.time() + timeout_seconds
    last_resp = None
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        params = {"job_id": job_id, "collection_id": collection_id, "format": "raw"}
        resp = besttime_get_json('venues/progress', params)
        # besttime_get_json returns either dict or (body, status) tuple
        if isinstance(resp, tuple):
            body, status = resp
            last_resp = body
        else:
            last_resp = resp

        # If last_resp contains 'venues' and it's a list, return it
        if isinstance(last_resp, dict):
            # common shapes
            if "venues" in last_resp and isinstance(last_resp["venues"], list) and last_resp["venues"]:
                return last_resp["venues"], last_resp
            # sometimes results are nested under other keys, try to find a list of venue-like dicts
            for key in ("results", "items", "found_venues"):
                if key in last_resp and isinstance(last_resp[key], list) and last_resp[key]:
                    return last_resp[key], last_resp

        # not ready yet, wait
        sleep_time = interval_seconds
        # optional: exponential backoff (capped)
        interval_seconds = min(interval_seconds * 1.5, 8)
        time.sleep(sleep_time)

    # Timeout reached
    return None, last_resp

@app.route('/foot_traffic/search', methods=['POST', 'OPTIONS'])
def foot_traffic_search():
    """
    Existing search endpoint (kept for compatibility).
    """
    if request.method == 'OPTIONS':
        return ('', 204, {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type',
            'Access-Control-Allow-Methods': 'POST'
        })

    try:
        payload = request.get_json(force=True) or {}
        q = payload.get('q')
        lat = payload.get('lat')
        lng = payload.get('lng')
        radius = int(payload.get('radius', 5000))
        num = str(payload.get('num', '100'))
        fmt = payload.get('format', 'raw')

        if not q:
            return jsonify({"error": "Missing q"}), 400

        params = {"q": q, "format": fmt, "num": num, "radius": radius}
        if lat is not None:
            params["lat"] = lat
        if lng is not None:
            params["lng"] = lng

        result = besttime_post_qs('venues/search', params)
        if isinstance(result, tuple):
            body, status = result
            return jsonify(body), status
        return jsonify(result)
    except ValueError as ve:
        logger.exception("Config error")
        return jsonify({"error": str(ve)}), 500
    except Exception as e:
        logger.exception("Unexpected error in foot_traffic/search")
        return jsonify({"error": str(e)}), 500


@app.route('/foot_traffic/progress', methods=['GET', 'OPTIONS'])
def foot_traffic_progress():
    """
    Existing progress endpoint (kept for compatibility).
    """
    if request.method == 'OPTIONS':
        return ('', 204, {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type',
            'Access-Control-Allow-Methods': 'GET'
        })

    try:
        job_id = request.args.get('job_id')
        collection_id = request.args.get('collection_id')
        if not job_id or not collection_id:
            return jsonify({"error": "Missing job_id or collection_id"}), 400

        params = {
            'job_id': job_id,
            'collection_id': collection_id,
            'format': 'raw'
        }
        data = besttime_get_json('venues/progress', params)
        if isinstance(data, tuple):
            body, status = data
            return jsonify(body), status
        return jsonify(data)
    except Exception as e:
        logger.exception("Unexpected error in foot_traffic/progress")
        return jsonify({"error": str(e)}), 500


@app.route('/foot_traffic/closest', methods=['POST', 'OPTIONS'])
def foot_traffic_closest():
    if request.method == 'OPTIONS':
        return ('', 204, {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type',
            'Access-Control-Allow-Methods': 'POST'
        })

    try:
        payload = request.get_json(force=True) or {}
        business_type = payload.get('business_type') or payload.get('q')
        lat = payload.get('lat')
        lng = payload.get('lng')

        if not business_type:
            return jsonify({"error": "Missing business_type (or q) in request body"}), 400
        if lat is None or lng is None:
            return jsonify({"error": "Missing lat or lng in request body"}), 400

        radius = int(payload.get('radius', 2000))
        num = str(payload.get('num', '100'))
        top_n = int(payload.get('top_n', 3))

        # Query BestTime venues/search
        params = {
            "q": business_type,
            "format": "raw",
            "num": num,
            "radius": radius,
            "lat": lat,
            "lng": lng
        }

        result = besttime_post_qs('venues/search', params)
        if isinstance(result, tuple):
            body, status = result
            return jsonify(body), status

        # Attempt to extract venues directly (fast path)
        venues = []
        if isinstance(result, dict):
            if "venues" in result and isinstance(result["venues"], list):
                venues = result["venues"]
            elif "results" in result and isinstance(result["results"], list):
                venues = result["results"]
            else:
                # No direct venues found in immediate response. It might be a background job.
                venues = []

        # If no immediate venues, check for background-job info and poll progress
        if not venues:
            # Detect background response shapes
            job_id = None
            collection_id = None
            progress_link = None

            if isinstance(result, dict):
                job_id = result.get("job_id") or result.get("job")
                collection_id = result.get("collection_id")
                # _links.venue_search_progress might exist
                if "_links" in result and isinstance(result["_links"], dict):
                    progress_link = result["_links"].get("venue_search_progress")
                # sometimes BestTime puts the progress link under another key
                if not progress_link:
                    for v in result.values():
                        if isinstance(v, str) and "venues/progress" in v:
                            progress_link = v
                            break

            # If we found job_id/collection_id or progress link, poll the progress endpoint
            if job_id or collection_id or progress_link:
                # configurable timeout via payload or environment (defaults here)
                timeout_seconds = int(payload.get('progress_timeout', os.getenv('BESTTIME_PROGRESS_TIMEOUT', 30)))
                interval_seconds = float(payload.get('progress_interval', os.getenv('BESTTIME_PROGRESS_INTERVAL', 2)))

                venues_found, progress_resp = wait_for_progress_and_get_venues(
                    job_id=job_id,
                    collection_id=collection_id,
                    progress_url=progress_link,
                    timeout_seconds=timeout_seconds,
                    interval_seconds=interval_seconds
                )

                if venues_found:
                    venues = venues_found
                    # continue processing below
                else:
                    # timed out — return an informative response including the progress link so frontend can poll
                    return jsonify({
                        "error": "Venue search still running (timed out while polling).",
                        "search_response": result,
                        "progress_response": progress_resp,
                        "progress_link": progress_link or (f"venues/progress?job_id={job_id}&collection_id={collection_id}" if job_id and collection_id else None)
                    }), 202

        if not venues:
            return jsonify({"error": "No venues found in BestTime response", "search_response": result}), 404

        # Use helper to compute closest venues that have forecast data
        top = top_closest_with_foot_traffic(venues, float(lat), float(lng), top_n=top_n)
        print(len(top))

        return jsonify(top)
    except ValueError as ve:
        logger.exception("Config error")
        return jsonify({"error": str(ve)}), 500
    except Exception as e:
        logger.exception("Unexpected error in foot_traffic/closest")
        return jsonify({"error": str(e)}), 500



# ----------------- Foot traffic endpoints (from besttime.py) -----------------

def get_conn():
    # Using DictCursor for convenience
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"ok": False, "error": "authentication required"}), 401
        return f(*args, **kwargs)
    return decorated


@app.route("/login", methods=["POST"])
def login():
    """
    Expects JSON: { email, password }
    On success: sets session['user_id'] and returns { ok: true, user: {...}, redirect: "index123.html" }
    """
    payload = request.get_json(silent=True) or {}
    email = _normalize_email(payload.get("email"))
    password = payload.get("password") or ""
    if not email or not password:
        return jsonify({"ok": False, "error": "email and password required"}), 400

    conn = None
    try:
        conn = get_db_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT id, email, password_hash, full_name FROM users WHERE email = %s", (email,))
            row = cur.fetchone()
            if not row:
                return jsonify({"ok": False, "error": "invalid credentials"}), 401
            if not check_password_hash(row["password_hash"], password):
                return jsonify({"ok": False, "error": "invalid credentials"}), 401

            # Successful login -> set server-side session
            session.clear()
            session["user_id"] = row["id"]
            session["email"] = row["email"]

            # Return user info and redirect target for frontend
            return jsonify({
                "ok": True,
                "user": {"id": row["id"], "email": row["email"], "full_name": row.get("full_name")},
                "redirect": "dashboard.html"  # Changed from "index123.html"
            }), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        if conn:
            conn.close()

@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True}), 200

@app.route("/save_target", methods=["POST"])
@login_required
def save_target():
    """
    Accepts the client payload and creates:
      - a row in targets
      - rows in competitors (if present)
      - rows in foot_traffic (if present)
      - an entry in target_versions (for audit)
    Expected JSON payload shape (example):
    {
      "name": "Optional friendly name",
      "target_location": { "lat": 14.443592, "lng": 120.995579 },
      "business_type": "restaurant",
      "description": "Chinese restaurant",
      "population_summary": {...},  -- optional
      "selected_barangays": [...],   -- optional
      "competitors": [ {name, vicinity, detailsObj}, ... ],
      "foot_traffic": [ {source_name, detailsObj}, ... ],
      "ai_analysis": "some string"   -- optional
    }
    """
    payload = request.get_json(force=True)
    if not payload:
        return jsonify({"ok": False, "error": "empty payload"}), 400

    user_id = session["user_id"]
    name = payload.get("name") or payload.get("business_type") or "target"
    loc = payload.get("target_location") or {}
    try:
        lat = float(loc.get("lat"))
        lng = float(loc.get("lng"))
    except Exception:
        return jsonify({"ok": False, "error": "invalid target_location (lat,lng required)"}), 400

    business_type = payload.get("business_type")
    description = payload.get("description")

    # pack flexible parts into data JSONB
    data_blob = {
        "population_summary": payload.get("population_summary"),
        "selected_barangays": payload.get("selected_barangays"),
        "ai_analysis": payload.get("ai_analysis"),
        # keep entire "full_payload" too if you want
        "full_payload": payload
    }

    competitors = payload.get("competitors") or []
    foot_traffic = payload.get("foot_traffic") or []

    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                # Insert target
                cur.execute(
                    """
                    INSERT INTO targets (user_id, name, business_type, description, latitude, longitude, data)
                    VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)
                    RETURNING id, created_at
                    """,
                    (user_id, name, business_type, description, lat, lng, json.dumps(data_blob)),
                )
                trow = cur.fetchone()
                target_id = trow["id"]

                # Insert competitors (normalized)
                for comp in competitors:
                    # comp can be either a string or dict; normalize
                    comp_name = comp.get("name") if isinstance(comp, dict) else str(comp)
                    comp_vicinity = comp.get("vicinity") if isinstance(comp, dict) else None
                    # details: store entire object as JSONB
                    details = comp if isinstance(comp, dict) else {"raw": comp}
                    cur.execute(
                        "INSERT INTO competitors (target_id, name, vicinity, details) VALUES (%s,%s,%s,%s::jsonb)",
                        (target_id, comp_name, comp_vicinity, json.dumps(details)),
                    )

                # Insert foot_traffic rows
                for ft in foot_traffic:
                    # ft expected: { source_name: "...", details: {...} } or arbitrary object
                    source_name = ft.get("source_name") if isinstance(ft, dict) else None
                    details = ft if isinstance(ft, dict) else {"raw": ft}
                    cur.execute(
                        "INSERT INTO foot_traffic (target_id, source_name, details) VALUES (%s,%s,%s::jsonb)",
                        (target_id, source_name, json.dumps(details)),
                    )

                # Save version (audit)
                cur.execute(
                    "INSERT INTO target_versions (target_id, data) VALUES (%s,%s::jsonb)",
                    (target_id, json.dumps(payload)),
                )

        return jsonify({"ok": True, "target_id": target_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        conn.close()

@app.route("/targets", methods=["GET"])
@login_required
def list_targets():
    """
    Return list of targets for current user.
    Each item includes: target row + number of competitors and created_at.
    """
    user_id = session["user_id"]
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT t.id, t.name, t.business_type, t.description, t.latitude, t.longitude, t.created_at,
                       (SELECT COUNT(1) FROM competitors c WHERE c.target_id = t.id) AS competitor_count
                FROM targets t
                WHERE t.user_id = %s
                ORDER BY t.created_at DESC
                """,
                (user_id,),
            )
            rows = cur.fetchall()
            # convert to plain dict list (RealDictCursor returns dict-like)
            return jsonify({"ok": True, "targets": rows})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        conn.close()

@app.route("/target/<int:target_id>", methods=["GET"])
@login_required
def get_target(target_id):
    """
    Return full target with competitors and foot_traffic.
    """
    user_id = session["user_id"]
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM targets WHERE id = %s AND user_id = %s",
                (target_id, user_id),
            )
            target = cur.fetchone()
            if not target:
                return jsonify({"ok": False, "error": "not found"}), 404

            cur.execute("SELECT id, name, vicinity, details, created_at FROM competitors WHERE target_id = %s ORDER BY id", (target_id,))
            comps = cur.fetchall()

            cur.execute("SELECT id, source_name, details, created_at FROM foot_traffic WHERE target_id = %s ORDER BY id", (target_id,))
            fts = cur.fetchall()

            return jsonify({"ok": True, "target": target, "competitors": comps, "foot_traffic": fts})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        conn.close()

@app.route("/current_user", methods=["GET"])
def current_user():
    """
    Returns the current logged-in user from the session:
      { ok: true, user: { id, email, full_name } }  OR  { ok: false }
    """
    uid = session.get("user_id")
    if not uid:
        return jsonify({"ok": False}), 200
    conn = None
    try:
        conn = get_db_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT id, email, full_name FROM users WHERE id = %s", (uid,))
            row = cur.fetchone()
            if not row:
                # Session had stale user_id
                session.clear()
                return jsonify({"ok": False}), 200
            return jsonify({"ok": True, "user": {"id": row["id"], "email": row["email"], "full_name": row.get("full_name")}}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        if conn:
            conn.close()

@app.route('/dashboard')
def dashboard():
    # Serve the dashboard page
    try:
        return send_from_directory(app.static_folder, 'dashboard.html') 
    except Exception:
        return jsonify({"ok": False, "error": "dashboard.html not found in static/"}), 500

# ----------------- Text to speech API -------------------------
@app.route('/text_to_speech', methods=['POST'])
def text_to_speech():
    """
    Convert analysis text to speech using OpenAI TTS API.
    Expects JSON: { "text": "..." }
    Returns: audio/mpeg file stream
    """
    
    
    data = request.get_json(silent=True) or {}
    text = data.get('text', '')
    
    # Handle case where text might be a tuple/array (text, warnings) from BusinessAI
    if isinstance(text, (list, tuple)):
        # Take the first element which should be the actual text
        text = text[0] if len(text) > 0 else ''
    
    # Convert to string and strip
    text = str(text).strip()
    
    if not text:
        return jsonify({"ok": False, "error": "No text provided"}), 400
    
    # Limit text length to avoid very long TTS requests (OpenAI has limits)
    MAX_TTS_LENGTH = 4000
    if len(text) > MAX_TTS_LENGTH:
        logger.warning(f"Text too long for TTS ({len(text)} chars), truncating to {MAX_TTS_LENGTH}")
        text = text[:MAX_TTS_LENGTH] + "... (truncated for text-to-speech)"
    
    # Get OpenAI TTS API key
    tts_key = os.getenv('OPENAI_TEXT_TO_SPEECH') or os.getenv('OPENAI_API_KEY')
    if not tts_key:
        return jsonify({"ok": False, "error": "OpenAI TTS API key not configured"}), 500
    
    try:
        client = OpenAI(api_key=tts_key)
        
        # Create a temporary file to store the audio
        with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as tmp_file:
            tmp_path = tmp_file.name
            
            # Stream the TTS response to the temp file
            with client.audio.speech.with_streaming_response.create(
                model="gpt-4o-mini-tts",
                voice="coral",
                input=text,
                instructions="Speak in an advisable, professional tone suitable for business analysis.",
            ) as response:
                response.stream_to_file(tmp_path)
            
            # Read the file and return it
            with open(tmp_path, 'rb') as audio_file:
                audio_data = audio_file.read()
            
            # Clean up temp file
            try:
                os.unlink(tmp_path)
            except:
                pass
            
            # Return audio file
            from flask import send_file
            from io import BytesIO
            return send_file(
                BytesIO(audio_data),
                mimetype='audio/mpeg',
                as_attachment=False,
                download_name='analysis_speech.mp3'
            )
            
    except Exception as e:
        logger.exception("text_to_speech failed")
        return jsonify({"ok": False, "error": f"TTS generation failed: {str(e)}"}), 500

# ----------------- News API -------------------------

@app.route("/news", methods=["GET"])
@login_required
def user_news():
    """
    Returns news articles related to the user's previous business types.
    Uses NewsData.io API.
    """
    NEWS_API_KEY = os.getenv("NEWSDATA_API_KEY", "pub_76be3c386a3944b4afd2b1e1d73fbc07")
    base_url = "https://newsdata.io/api/1/latest"

    user_id = session["user_id"]

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Collect unique business types for this user
            cur.execute("""
                SELECT DISTINCT LOWER(TRIM(business_type)) AS business_type
                FROM targets
                WHERE user_id = %s AND business_type IS NOT NULL AND business_type <> ''
            """, (user_id,))
            types = [r["business_type"] for r in cur.fetchall() if r.get("business_type")]

        if not types:
            return jsonify({"status": "success", "totalResults": 0, "results": []})

        import urllib.parse
        q = " OR ".join(types)
        encoded_q = urllib.parse.quote(q)

        # Build query string
        url = f"{base_url}?apikey={NEWS_API_KEY}&qInTitle={encoded_q}&language=en"

        # Fetch articles
        r = requests.get(url, timeout=15)
        if not r.ok:
            return jsonify({"status": "error", "error": f"API returned {r.status_code}", "detail": r.text}), 500

        data = r.json()

        # Normalize results (limit & clean fields)
        articles = []
        for item in data.get("results", [])[:10]:
            articles.append({
                "title": item.get("title"),
                "description": item.get("description"),
                "link": item.get("link"),
                "image_url": item.get("image_url"),
                "source_name": item.get("source_name"),
                "pubDate": item.get("pubDate"),
                "category": (item.get("category") or ["general"])[0],
            })

        return jsonify({
            "status": "success",
            "totalResults": len(articles),
            "results": articles
        })
    except Exception as e:
        logger.exception("Error fetching news")
        return jsonify({"status": "error", "error": str(e)}), 500
    finally:
        conn.close()


@app.route("/send_otp", methods=["POST"])
def send_otp():
    """
    Send OTP to email for verification during signup.
    Expected JSON: { email, name }
    Returns: { ok: true } or error
    """
    payload = request.get_json(silent=True) or {}
    email = _normalize_email(payload.get("email"))
    name = payload.get("name", "")
    
    if not email:
        return jsonify({"ok": False, "error": "Email required"}), 400
    
    # Basic email validation
    import re
    if not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', email):
        return jsonify({"ok": False, "error": "Invalid email format"}), 400
    
    conn = None
    try:
        conn = get_db_conn()
        
        # Check if email already registered
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE email = %s", (email,))
            if cur.fetchone():
                return jsonify({"ok": False, "error": "Email already registered"}), 400
        
        # Generate and store OTP
        otp = generate_otp()
        store_otp(conn, email, otp)
        
        # Send email
        if send_otp_email(email, otp, name):
            logger.info(f"OTP sent to {email}")
            return jsonify({
                "ok": True, 
                "message": f"Verification code sent to {email}",
                "expires_in": OTP_TTL_SECONDS
            }), 200
        else:
            return jsonify({"ok": False, "error": "Failed to send email. Please try again."}), 500
            
    except Exception as e:
        logger.exception("send_otp failed")
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        if conn:
            conn.close()

@app.route("/verify_otp", methods=["POST"])
def verify_otp_endpoint():
    """
    Verify OTP code.
    Expected JSON: { email, otp }
    Returns: { ok: true, verified: true } or error
    """
    payload = request.get_json(silent=True) or {}
    email = _normalize_email(payload.get("email"))
    otp = payload.get("otp", "").strip()
    
    if not email or not otp:
        return jsonify({"ok": False, "error": "Email and OTP required"}), 400
    
    conn = None
    try:
        conn = get_db_conn()
        success, message = verify_otp(conn, email, otp)
        
        if success:
            return jsonify({"ok": True, "verified": True, "message": message}), 200
        else:
            return jsonify({"ok": False, "verified": False, "error": message}), 400
            
    except Exception as e:
        logger.exception("verify_otp failed")
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        if conn:
            conn.close()

@app.route("/resend_otp", methods=["POST"])
def resend_otp():
    """
    Resend OTP to email.
    Expected JSON: { email, name }
    """
    payload = request.get_json(silent=True) or {}
    email = _normalize_email(payload.get("email"))
    name = payload.get("name", "")
    
    if not email:
        return jsonify({"ok": False, "error": "Email required"}), 400
    
    conn = None
    try:
        conn = get_db_conn()
        
        # Generate new OTP
        otp = generate_otp()
        store_otp(conn, email, otp)
        
        # Send email
        if send_otp_email(email, otp, name):
            logger.info(f"OTP resent to {email}")
            return jsonify({
                "ok": True, 
                "message": "New verification code sent",
                "expires_in": OTP_TTL_SECONDS
            }), 200
        else:
            return jsonify({"ok": False, "error": "Failed to send email"}), 500
            
    except Exception as e:
        logger.exception("resend_otp failed")
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        if conn:
            conn.close()

# Modify the existing /signup endpoint to check OTP verification:

@app.route("/signup", methods=["POST"])
def signup():
    """
    Create account after OTP verification.
    Expected JSON: { name, email, password, otp }
    """
    payload = request.get_json(silent=True) or {}
    name = payload.get("name") or payload.get("full_name") or ""
    email = _normalize_email(payload.get("email"))
    password = payload.get("password") or ""
    otp = payload.get("otp", "").strip()
    
    if not email or not password:
        return jsonify({"ok": False, "error": "Email and password required"}), 400
    
    if not otp:
        return jsonify({"ok": False, "error": "Verification code required"}), 400

    pw_hash = generate_password_hash(password)
    conn = None
    try:
        conn = get_db_conn()
        
        # Verify OTP first
        success, message = verify_otp(conn, email, otp)
        if not success:
            return jsonify({"ok": False, "error": message}), 400
        
        # Create user account
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (email, password_hash, full_name) VALUES (%s,%s,%s) RETURNING id, email, full_name",
                    (email, pw_hash, name),
                )
                row = cur.fetchone()
        
        logger.info(f"New user registered: {email}")
        return jsonify({
            "ok": True, 
            "user": {"id": row["id"], "email": row["email"], "full_name": row["full_name"]}, 
            "redirect": "login.html"
        }), 201
        
    except psycopg2.IntegrityError:
        if conn:
            conn.rollback()
        return jsonify({"ok": False, "error": "Email already registered"}), 400
    except Exception as e:
        if conn:
            conn.rollback()
        logger.exception("signup failed")
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        if conn:
            conn.close()
# ----------------- Run -----------------
if __name__ == '__main__':
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "5000"))
    logger.info("Starting merged Flask app on %s:%d", host, port)
    app.run(host=host, port=port, debug=True)
