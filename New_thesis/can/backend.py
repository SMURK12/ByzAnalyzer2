# backend.py

from flask import Flask, request, jsonify, send_from_directory
from dotenv import load_dotenv
import os, traceback
load_dotenv()

# Try flexible imports for establishments1 (supporting utils/ or root)
try:
    from utils.establishments1 import GoogleMapsService, Address, Establishments
except Exception:
    try:
        from utils.establishments1 import GoogleMapsService, Address, Establishments
    except Exception as e:
        # Will raise later if missing when endpoints are used
        GoogleMapsService = Address = Establishments = None
        print("Warning: failed to import establishments1 module. Ensure it's in project or utils/ folder.")
        print(str(e))

from flask_cors import CORS

app = Flask(__name__, static_folder='static', static_url_path='/static')
CORS(app)

@app.route('/')
def index():
    # serve the maps file placed at static/maps.html
    try:
        return send_from_directory(app.static_folder, 'maps.html')
    except Exception:
        return jsonify({"ok": False, "error": "maps.html not found in static/"}), 500

@app.route('/ping', methods=['GET'])
def ping():
    return jsonify({"ok": True, "message": "pong"}), 200

@app.route('/nearby_places', methods=['POST'])
def nearby_places():
    data = request.get_json(silent=True) or {}
    try:
        lat = float(data.get('latitude', 0))
        lng = float(data.get('longitude', 0))
        radius = int(data.get('radius', 1500))
    except Exception:
        return jsonify({"ok": False, "error": "Invalid latitude/longitude/radius"}), 400

    if GoogleMapsService is None:
        return jsonify({"ok": False, "error": "GoogleMapsService not available (import failed)"}), 500

    try:
        gm = GoogleMapsService()
        places = gm.get_nearby_places(lat, lng, radius)
        return jsonify(places)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500

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

    if GoogleMapsService is None or Address is None or Establishments is None:
        return jsonify({"ok": False, "error": "Required module establishments1 is missing"}), 500

    try:
        gm = GoogleMapsService()
        comps = gm.get_address_components(lat, lng) or {}
        address = Address(
            barangay = comps.get('barangay', ''),
            municipality = comps.get('municipality', ''),
            province = comps.get('province', ''),
            region = comps.get('region', '')
        )

        est = Establishments(latitude=lat, longitude=lng, business_type=business_type, address=address, description=description, radius=2000)
        result = est.get_all_data()

        if result is None:
            result = {}

        return jsonify({"ok": True, "data": result}), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500
    
try:
    from utils.businessai import BusinessAI
except Exception as e:
    BusinessAI = None
    logger.warning("businessai.BusinessAI import failed: %s", e)



if __name__ == '__main__':
    if not os.getenv('GOOGLE_PLACES_API_KEY'):
        print("Warning: GOOGLE_PLACES_API_KEY not set (set in .env or env).")
    app.run(debug=True, host='0.0.0.0', port=5000)
