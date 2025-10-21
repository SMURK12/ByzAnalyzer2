#!/usr/bin/env python3
# my_app.py - Modified for Render deployment
from flask import Flask, request, jsonify, send_from_directory, session
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

# Create Flask app
app = Flask(__name__, static_folder='static', static_url_path='/static')
app.secret_key = SECRET_KEY
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"] = True  # Required for production HTTPS
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# Enable CORS
CORS(app, supports_credentials=True)

def _normalize_email(e):
    return (e or "").strip().lower()

# Optional imports for Google maps helpers
try:
    from utils.establishments1 import GoogleMapsService, Address, Establishments
except Exception:
    try:
        from establishments1 import GoogleMapsService, Address, Establishments
    except Exception as e:
        GoogleMapsService = Address = Establishments = None
        logger.warning("Could not import establishments1")
        logger.debug(e)

# Business AI
try:
    from utils.businessai import BusinessAI
except Exception as e:
    BusinessAI = None
    logger.warning("Could not import BusinessAI: %s", e)

# OTP Configuration
try:
    OTP_TTL_SECONDS = int(os.getenv('OTP_TTL_SECONDS', '300'))
except ValueError:
    OTP_TTL_SECONDS = 300

MAX_OTP_ATTEMPTS = 3

# Demographics configuration
DEFAULT_MUNICIPALITY = os.getenv("DEFAULT_MUNICIPALITY", "LAS PIÑAS")
DEMOGRAPHICS_SCHEMA = os.getenv("DEMOGRAPHICS_SCHEMA", "public")
DEMOGRAPHICS_TABLE = os.getenv("DEMOGRAPHICS_TABLE", "demographics")
BESTTIME_PRIVATE_KEY = os.getenv('BESTTIME_PRIVATE') or os.getenv('BESTTIME_API_KEY_PRIVATE')
BESTTIME_BASE = os.getenv('BESTTIME_BASE', 'https://besttime.app/api/v1')

if not BESTTIME_PRIVATE_KEY:
    logger.warning("BESTTIME_PRIVATE key not set")

# OTP helper functions
def generate_otp() -> str:
    """Generate a 6-digit OTP code"""
    return ''.join([str(random.randint(0, 9)) for _ in range(6)])

def store_otp(conn, email: str, otp: str):
    """Store OTP in database with expiration"""
    expires_at = datetime.utcnow() + timedelta(seconds=OTP_TTL_SECONDS)
    
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE otp_verifications 
            SET is_verified = TRUE 
            WHERE email = %s AND is_verified = FALSE
        """, (email,))
        
        cur.execute("""
            INSERT INTO otp_verifications (email, otp_code, expires_at)
            VALUES (%s, %s, %s)
            RETURNING id
        """, (email, otp, expires_at))
        
        return cur.fetchone()["id"]

def verify_otp(conn, email: str, otp: str) -> tuple:
    """Verify OTP code for email"""
    with conn.cursor() as cur:
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
        
        if datetime.utcnow() > row["expires_at"]:
            return False, "Verification code has expired. Please request a new one."
        
        if row["attempts"] >= MAX_OTP_ATTEMPTS:
            return False, "Too many failed attempts. Please request a new code."
        
        if row["otp_code"] == otp:
            cur.execute("""
                UPDATE otp_verifications
                SET is_verified = TRUE
                WHERE id = %s
            """, (row["id"],))
            return True, "Email verified successfully!"
        else:
            cur.execute("""
                UPDATE otp_verifications
                SET attempts = attempts + 1
                WHERE id = %s
            """, (row["id"],))
            
            remaining = MAX_OTP_ATTEMPTS - (row["attempts"] + 1)
            if remaining > 0:
                return False, f"Invalid code. {remaining} attempt(s) remaining."
            else:
                return False, "Invalid code. Maximum attempts reached."

# Database connection functions
def get_conn_params():
    url = os.getenv("DATABASE_URL")
    if url:
        # Render uses postgresql:// but psycopg2 needs postgres://
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        if "sslmode=" in url:
            return {"dsn": url}
        return {"dsn": f"{url}?sslmode=require"}
    
    host = os.getenv("PGHOST")
    port = int(os.getenv("PGPORT", "5432"))
    db = os.getenv("PGDATABASE")
    user = os.getenv("PGUSER")
    pwd = os.getenv("PGPASSWORD")
    
    missing = [k for k, v in {"PGHOST": host, "PGDATABASE": db, "PGUSER": user, "PGPASSWORD": pwd}.items() if not v]
    if missing:
        raise RuntimeError(f"Missing env vars: {', '.join(missing)}")
    
    return {"host": host, "port": port, "dbname": db, "user": user, "password": pwd, "sslmode": "require"}

_db_conn = None

def connect_with_retries(retries: int = 5, delay: float = 2.0):
    from psycopg2.extras import RealDictCursor
    params = get_conn_params()
    last_err = None
    
    for i in range(1, retries + 1):
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

def get_conn():
    """Alternative method to get a new connection"""
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)

def query_demographics(conn, municipality: str, schema: str = DEMOGRAPHICS_SCHEMA, table: str = DEMOGRAPHICS_TABLE):
    municipality = (municipality or DEFAULT_MUNICIPALITY).strip()
    sql = f'''
    SELECT *
    FROM "{schema}"."{table}"
    WHERE translate("Municipality", 'ÑÃ±', 'Nn') ILIKE translate(%s, 'ÑÃ±', 'Nn')
    LIMIT 1000;
    '''
    pattern = f"%{municipality}%"
    with conn.cursor() as cur:
        cur.execute(sql, (pattern,))
        rows = cur.fetchall()
    return rows

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"ok": False, "error": "authentication required"}), 401
        return f(*args, **kwargs)
    return decorated

# Routes
@app.route('/')
def index():
    try:
        return send_from_directory(app.static_folder, 'index123.html')
    except Exception:
        return jsonify({"ok": False, "error": "index123.html not found"}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"}), 200

@app.route('/ping', methods=['GET'])
def ping():
    return jsonify({"ok": True, "message": "pong"}), 200

# [... rest of your routes remain the same ...]
# Include all your existing routes here: /demographics, /integration, /nearby_places, etc.

# Main entry point for Render
if __name__ == '__main__':
    # Render sets PORT environment variable
    port = int(os.getenv("PORT", "10000"))
    host = os.getenv("HOST", "0.0.0.0")
    
    logger.info(f"Starting Flask app on {host}:{port}")
    
    # Use production-ready server settings
    # Note: For production, use gunicorn instead of Flask's built-in server
    app.run(host=host, port=port, debug=False)