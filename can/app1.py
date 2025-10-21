#!/usr/bin/env python3
"""
app.py - Flask backend using psycopg2 to query public.demographics with a parameterized municipality.

Usage:
  pip install flask flask-cors psycopg2-binary python-dotenv
  # set env vars (DATABASE_URL preferred) or PGHOST/PGUSER/PGPASSWORD/PGDATABASE/PGPORT
  python app.py

Endpoints:
  GET  /health
  GET  /demographics?municipality=Las%20Pinas
  POST /integration   JSON body: {"municipality": "Las Pinas"}
"""
import os
import time
import logging
from typing import List, Dict, Any

from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor


load_dotenv()

# ---------------- Configuration ----------------
DEFAULT_MUNICIPALITY = "LAS PIÑAS"
DEMOGRAPHICS_SCHEMA = os.getenv("DEMOGRAPHICS_SCHEMA", "public")
DEMOGRAPHICS_TABLE = os.getenv("DEMOGRAPHICS_TABLE", "demographics")

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("demographics-backend")

# ---------------- Connection helpers (from your temp.py, slightly adapted) ----------------
def get_conn_params():
    """
    Prefer DATABASE_URL (DSN). Falls back to individual PG* env vars.
    Returns kwargs that can be passed to psycopg2.connect(...)
    """
    url = os.getenv("DATABASE_URL")
    if url:
        # psycopg2 accepts DSN string directly; ensure sslmode present
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
    return {"host": host, "port": port, "dbname": db, "user": user, "password": pwd, "sslmode": "require"}

def connect_with_retries(retries: int = 5, delay: float = 2.0):
    """
    Try to connect multiple times (useful at startup).
    Returns a psycopg2 connection with RealDictCursor support.
    """
    params = get_conn_params()
    last_err = None
    for i in range(1, retries + 1):
        try:
            # If using DSN (params contains 'dsn'), pass it as dsn
            if "dsn" in params:
                conn = psycopg2.connect(params["dsn"], cursor_factory=RealDictCursor)
            else:
                conn = psycopg2.connect(cursor_factory=RealDictCursor, **params)
            conn.autocommit = True
            logger.info("Connected to Postgres on attempt %d/%d", i, retries)
            return conn
        except Exception as e:
            last_err = e
            logger.warning("[%d/%d] Postgres connect failed: %s", i, retries, e)
            time.sleep(delay)
    logger.exception("All connection attempts failed.")
    raise last_err

# ---------------- Query function (parameterized municipality) ----------------
def query_demographics(conn, municipality: str, schema: str = DEMOGRAPHICS_SCHEMA, table: str = DEMOGRAPHICS_TABLE):
    municipality = (municipality or DEFAULT_MUNICIPALITY).strip()
    sql = f'''
    SELECT *
    FROM "{schema}"."{table}"
    WHERE translate("Municipality", 'Ññ', 'Nn') ILIKE translate(%s, 'Ññ', 'Nn')
    LIMIT 1000;
    '''
    pattern = f"%{municipality}%"
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, (pattern,))
        rows = cur.fetchall()
    return rows
# ---------------- Flask app ----------------
app = Flask(__name__)
CORS(app)

# We'll keep a single persistent connection object (reconnect if needed)
_db_conn = None

def get_db_conn():
    global _db_conn
    try:
        if _db_conn is None or _db_conn.closed:
            _db_conn = connect_with_retries()
        else:
            # quick heartbeat to ensure the connection is alive
            try:
                with _db_conn.cursor() as c:
                    c.execute("SELECT 1")
            except Exception:
                logger.info("DB connection broken; reconnecting.")
                _db_conn = connect_with_retries()
        return _db_conn
    except Exception:
        logger.exception("Unable to obtain a working DB connection.")
        raise

# ---------------- Endpoints ----------------
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200

@app.route("/demographics", methods=["GET"])
def demographics_get():
    municipality = request.args.get("municipality") or DEFAULT_MUNICIPALITY
    try:
        conn = get_db_conn()
        rows = query_demographics(conn, municipality)
        return jsonify({"municipality": municipality, "rows": rows}), 200
    except Exception as e:
        logger.exception("GET /demographics failed")
        return jsonify({"error": "Server error", "detail": str(e)}), 500

@app.route("/integration", methods=["POST", "OPTIONS"])
def integration_post():
    # Accepts JSON: { "municipality": "Las Pinas" }
    if request.method == "OPTIONS":
        return jsonify({}), 200
    data = request.get_json(silent=True) or {}
    municipality = data.get("municipality") or DEFAULT_MUNICIPALITY
    try:
        conn = get_db_conn()
        rows = query_demographics(conn, municipality)
        return jsonify({"municipality": municipality, "rows": rows}), 200
    except Exception as e:
        logger.exception("POST /integration failed")
        return jsonify({"error": "Server error", "detail": str(e)}), 500

# ---------------- Optional admin endpoints (useful for debugging) ----------------
@app.route("/admin/sample", methods=["GET"])
def admin_sample():
    # Return a small sample from the table for inspection
    try:
        limit = int(request.args.get("limit", 10))
        conn = get_db_conn()
        sql = f'SELECT * FROM "{DEMOGRAPHICS_SCHEMA}"."{DEMOGRAPHICS_TABLE}" LIMIT %s'
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (limit,))
            rows = cur.fetchall()
        return jsonify({"rows": rows}), 200
    except Exception as e:
        logger.exception("GET /admin/sample failed")
        return jsonify({"error": "Server error", "detail": str(e)}), 500

# ---------------- Run ----------------
if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "5000"))
    logger.info("Starting Flask app on %s:%d (table=%s.%s)", host, port, DEMOGRAPHICS_SCHEMA, DEMOGRAPHICS_TABLE)
    # Pre-create connection early so startup failures are obvious
    try:
        _db_conn = connect_with_retries(retries=5, delay=2.0)
    except Exception:
        logger.exception("Failed to establish DB connection at startup. App will still run but DB calls will retry on-demand.")
    app.run(host=host, port=port)
