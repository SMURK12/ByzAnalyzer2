#!/usr/bin/env python3
# merged_app.py - combines demographics (app.py) and maps endpoints (backend.py)
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
import os, time, logging, traceback


load_dotenv()

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("merged-app")

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

# --- add near other Flask endpoints in merged_app.py ---
try:
    from utils.businessai import BusinessAI
except Exception as e:
    BusinessAI = None
    logger.warning("businessai.BusinessAI import failed: %s", e)

# ----------------- Postgres demographics helpers (from app.py) -----------------
DEFAULT_MUNICIPALITY = os.getenv("DEFAULT_MUNICIPALITY", "LAS PIÑAS")
DEMOGRAPHICS_SCHEMA = os.getenv("DEMOGRAPHICS_SCHEMA", "public")
DEMOGRAPHICS_TABLE  = os.getenv("DEMOGRAPHICS_TABLE", "demographics")

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
app = Flask(__name__, static_folder='static', static_url_path='/static')
CORS(app)

# Serve the integrated HTML at root. Put the new temp3.html in the static folder.
@app.route('/')
def index():
    # try to serve static/temp3.html
    try:
        return send_from_directory(app.static_folder, 'index (1).html') 
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
    if GoogleMapsService is not None and Address is not None and Establishments is not None:
        try:
            addr_obj = Address(
                barangay = address_components.get('barangay',''),
                municipality = address_components.get('municipality',''),
                province = address_components.get('province',''),
                region = address_components.get('region','')
            )
            est = Establishments(latitude=lat, longitude=lng, business_type=business_type, address=addr_obj, description=description, radius=2000)
            # prefer get_all_data result; fallback to get_competitors
            try:
                result = est.get_all_data() or {}
                # find competitors list in common result keys
                for key in ('competitors','nearby','places','nearby_places','results'):
                    if isinstance(result, dict) and key in result:
                        competitors = result[key]
                        break
                if not competitors:
                    # If result itself is a list, treat that as competitors
                    if isinstance(result, list):
                        competitors = result
            except Exception:
                if hasattr(est, 'get_competitors'):
                    try:
                        competitors = est.get_competitors() or []
                    except Exception:
                        competitors = []
        except Exception:
            logger.exception("Error building Establishments / getting competitors")
            competitors = []
    else:
        # No establishments module available — return empty competitors
        competitors = []

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
            "address_components": address_components,
            "population": population_stats
        }
    }
    return jsonify(response_payload), 200





@app.route('/generate_analysis', methods=['POST'])
def generate_analysis():
    """
    Expects JSON:
    {
      "target_location": {"lat": <float>, "lng": <float>},
      "business_type": "restaurant",
      "description": "...",
      "population_summary": { ... },   # aggregated numbers (optional)
      "selected_barangays": [...],     # list of selected barangay names
      "competitors": [...],
      "other_establishments": [...],
      "extra_prompt": "optional string"  # appended to LLM prompt (from UI)
    }
    """
    if BusinessAI is None:
        return jsonify({"ok": False, "error": "BusinessAI not available on server (import failed)"}), 500

    data = request.get_json(silent=True) or {}
    logger.info("Received analysis request: %s", data)
    
    tl = data.get('target_location') or {}
    lat = tl.get('lat')
    lng = tl.get('lng')
    business_type = data.get('business_type', 'other')
    description = data.get('description', '')
    competitors = data.get('competitors', [])
    other_establishments = data.get('other_establishments', [])
    demographics = data.get('population_summary') or data.get('demographics') or {}
    selected_barangays = data.get('selected_barangays', [])
    extra_prompt = data.get('extra_prompt', '')

    # Store selected barangays for later integration (as requested)
    if selected_barangays:
        global _saved_barangays
        _saved_barangays = selected_barangays.copy()
        logger.info("Selected barangays saved for later integration: %s", selected_barangays)

    # basic validation
    try:
        lat = float(lat)
        lng = float(lng)
    except Exception:
        return jsonify({"ok": False, "error": "Invalid or missing target_location.lat/lng"}), 400

    try:
        ai = BusinessAI(
            target_business_type=business_type,
            target_lat=lat,
            target_lng=lng,
            target_description=description,
            nearby_establishments=other_establishments,
            competitors=competitors,
            other_establishments=other_establishments,
            demographics=demographics
        )
        analysis = ai.get_analysis()
        
        # Add extra prompt context if provided
        if extra_prompt:
            analysis += f"\n\nAdditional Instructions:\n{extra_prompt}"
        
        return jsonify({
            "ok": True, 
            "analysis": analysis,
            "selected_barangays": selected_barangays  # Echo back for confirmation
        }), 200
    except Exception as e:
        logger.exception("generate_analysis failed")
        return jsonify({"ok": False, "error": str(e)}), 500
    


# ----------------- Barangay Storage for Later Integration -----------------
# Simple in-memory storage for selected barangays (you can replace with database later)
_saved_barangays = []

@app.route('/get_saved_barangays', methods=['GET'])
def get_saved_barangays():
    """Retrieve the list of saved barangays for later integration"""
    return jsonify({
        "ok": True,
        "saved_barangays": _saved_barangays,
        "count": len(_saved_barangays)
    }), 200

@app.route('/save_barangays', methods=['POST'])
def save_barangays():
    """Save barangays for later integration"""
    data = request.get_json(silent=True) or {}
    barangays = data.get('barangays', [])
    
    if barangays:
        global _saved_barangays
        _saved_barangays = barangays.copy()
        logger.info("Barangays saved for later integration: %s", barangays)
        return jsonify({
            "ok": True,
            "message": f"Saved {len(barangays)} barangays",
            "saved_barangays": _saved_barangays
        }), 200
    else:
        return jsonify({
            "ok": False,
            "error": "No barangays provided"
        }), 400

# ----------------- Run -----------------
if __name__ == '__main__':
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "5000"))
    logger.info("Starting merged Flask app on %s:%d", host, port)
    app.run(host=host, port=port, debug=True)
