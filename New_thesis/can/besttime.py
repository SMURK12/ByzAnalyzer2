# besttime_service.py  (modified)
"""
Standalone BestTime microservice.
Provides:
  - POST /foot_traffic/search    (unchanged)
  - GET  /foot_traffic/progress  (unchanged)
  - POST /foot_traffic/closest   <-- NEW: accepts business_type, lat, lng and returns top 3 closest venues with foot traffic
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import os
import logging
from dotenv import load_dotenv
import time
from urllib.parse import urlparse, parse_qs
load_dotenv()

# import helper from the uploaded file
from utils.foottraffic_helper import top_closest_with_foot_traffic

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("besttime_service")

BESTTIME_PRIVATE_KEY = os.getenv('BESTTIME_PRIVATE') or os.getenv('BESTTIME_API_KEY_PRIVATE') or os.getenv('BESTTIME_PRIVATE_KEY')
BESTTIME_BASE = os.getenv('BESTTIME_BASE', 'https://besttime.app/api/v1')

if not BESTTIME_PRIVATE_KEY:
    logger.warning("BESTTIME_PRIVATE key is not set in environment. Requests will fail until you set it.")

app = Flask(__name__)
CORS(app)



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

        return jsonify({"top_venues": top, "search_response": result})
    except ValueError as ve:
        logger.exception("Config error")
        return jsonify({"error": str(ve)}), 500
    except Exception as e:
        logger.exception("Unexpected error in foot_traffic/closest")
        return jsonify({"error": str(e)}), 500
    


if __name__ == '__main__':
    port = int(os.getenv('BESTTIME_PORT', 5000))
    logger.info("Starting BestTime microservice on port %s", port)
    app.run(debug=True, host='0.0.0.0', port=port)
