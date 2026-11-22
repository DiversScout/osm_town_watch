import json
import math
import os
import time
import requests

# 対象エリアのBBox (南, 西, 北, 東) ※北九州市〜関門エリア全域をカバー
# 必要に応じて範囲を広げてください
BBOX = "33.80,130.65,34.05,131.05"

TILE_BASE_ZOOM = 16

ENDPOINTS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass.osm.ch/api/interpreter"
]

def lat_lng_to_tile(lat, lng, zoom):
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    x = int((lng + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y

def is_registered(tags):
    if not tags:
        return False
    # 縁石
    if "barrier" in tags and tags["barrier"] == "kerb":
        k = tags.get("kerb", "")
        if k and k != "unknown":
            return True
    if "kerb" in tags and tags["kerb"] != "unknown":
        return True
    # エレベーター
    if tags.get("highway") == "elevator":
        return True
    # 車椅子タグ
    if "wheelchair" in tags and tags["wheelchair"] in ["yes", "limited", "no"]:
        return True
    # トイレ
    if tags.get("toilets:wheelchair") in ["yes", "limited", "no"]:
        return True
    return False

def main():
    query = f"""
    [out:json][timeout:90];
    (
      node["barrier"="kerb"]({BBOX});
      node["kerb"]({BBOX});
      node["highway"~"^(crossing|elevator)$"]({BBOX});
      node["shop"]({BBOX});
      node["amenity"]({BBOX});
      node["wheelchair"]({BBOX});
      node["toilets:wheelchair"]({BBOX});
    );
    out skel qt;
    out tags qt;
    """

    data = None
    for url in ENDPOINTS:
        try:
            print(f"Fetching from {url}...")
            res = requests.post(url, data={"data": query}, timeout=120)
            if res.status_code == 200:
                data = res.json()
                print("Successfully fetched OSM data.")
                break
        except Exception as e:
            print(f"Failed: {e}")
            time.sleep(2)

    if not data or "elements" not in data:
        raise RuntimeError("Failed to fetch data from all Overpass endpoints.")

    tile_stats = {}

    for item in data["elements"]:
        lat = item.get("lat")
        lon = item.get("lon")
        if lat is None or lon is None:
            continue

        x, y = lat_lng_to_tile(lat, lon, TILE_BASE_ZOOM)
        key = f"{x}_{y}"

        if key not in tile_stats:
            tile_stats[key] = {"total": 0, "tagged": 0}

        tile_stats[key]["total"] += 1
        if is_registered(item.get("tags", {})):
            tile_stats[key]["tagged"] += 1

    os.makedirs("data", exist_ok=True)
    out_path = os.path.join("data", "tiles.json")

    payload = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base_zoom": TILE_BASE_ZOOM,
        "tiles": tile_stats
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    print(f"Saved {len(tile_stats)} tiles to {out_path}")

if __name__ == "__main__":
    main()
