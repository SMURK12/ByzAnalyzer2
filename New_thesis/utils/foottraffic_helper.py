import math
from typing import List, Dict, Any

def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    # Earth radius in meters
    R = 6371008.8
    # convert degrees to radians
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi/2.0)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda/2.0)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def average_day_mean(venue: Dict[str, Any]) -> float:
    # Safely compute average of day_info.day_mean across days if available
    forecasts = venue.get("venue_foot_traffic_forecast") or []
    means = []
    for d in forecasts:
        info = d.get("day_info") or {}
        m = info.get("day_mean")
        if isinstance(m, (int, float)):
            means.append(m)
    return sum(means) / len(means) if means else 0.0

def top_closest_with_foot_traffic(venues: List[Dict[str, Any]],
                                 target_lat: float,
                                 target_lon: float,
                                 top_n: int = 2) -> List[Dict[str, Any]]:
    # Step 1: filter valid forecasted venues with coordinates
    candidates = []
    for v in venues:
        if not v.get("forecast"):
            continue
        lat = v.get("venue_lat")
        lon = v.get("venue_lon")
        if lat is None or lon is None:
            continue
        # compute distance
        dist = haversine_meters(target_lat, target_lon, float(lat), float(lon))
        avg_mean = average_day_mean(v)
        # enrich for sorting & return
        v_copy = dict(v)  # shallow copy to avoid mutating original
        v_copy["_distance_m"] = dist
        v_copy["_avg_day_mean"] = avg_mean
        candidates.append(v_copy)

    # Step 2: sort by distance, tiebreaker by avg_day_mean descending
    candidates.sort(key=lambda x: (x["_distance_m"], -x["_avg_day_mean"]))

    # Step 3: return top N (or fewer if not enough)
    return candidates[:top_n]

# Example usage:
# result = top_closest_with_foot_traffic(progress_json["venues"], 14.4516, 120.9773, top_n=2)
# for r in result:
#     print(r["venue_name"], r["_distance_m"], r["_avg_day_mean"])

