import json
import math
import os
import time
import requests

# 北九州市〜門司港・関門エリア全域 (南緯度, 西経度, 北緯度, 東経度)
BBOX = "33.80,130.65,34.05,131.05"

TILE_BASE_ZOOM = 16

# 応答精度の高いパブリックエンドポイント一覧
ENDPOINTS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass.osm.ch/api/interpreter"
]

HEADERS = {
    "User-Agent": "DiversScout-TownWatch/1.0 (OSM Barrier Map Aggregator)"
}

def lat_lng_to_tile(lat, lng, zoom):
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    x = int((lng + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y

def is_registered(tags):
    if not tags:
        return False
    # 縁石 (kerb)
    if tags.get("barrier") == "kerb" or "kerb" in tags:
        k = tags.get("kerb", "")
        if k and k != "unknown":
            return True
    # エレベーター
    if tags.get("highway") == "elevator":
        return True
    # 車椅子タグ
    if tags.get("wheelchair") in ["yes", "limited", "no"]:
        return True
    # 車椅子対応トイレ
    if tags.get("toilets:wheelchair") in ["yes", "limited", "no"]:
        return True
    return False

def main():
    query = f"""[out:json][timeout:90];
(
  node["barrier"="kerb"]({BBOX});
  node["kerb"]({BBOX});
  node["highway"="crossing"]({BBOX});
  node["highway"="elevator"]({BBOX});
  node["shop"]({BBOX});
  node["amenity"]({BBOX});
  node["wheelchair"]({BBOX});
  node["toilets:wheelchair"]({BBOX});
);
out body qt;"""

    data = None
    for url in ENDPOINTS:
        try:
            print(f"Fetching from {url}...")
            res = requests.post(
                url,
                data={"data": query},
                headers=HEADERS,
                timeout=120
            )
            print(f"Response status: {res.status_code}")
            if res.status_code == 200:
                text = res.text.strip()
                if not text.startswith("<"):
                    json_data = res.json()
                    elements = json_data.get("elements", [])
                    if len(elements) > 0:
                        data = json_data
                        print(f"Successfully fetched {len(elements)} elements from {url}")
                        break
                    else:
                        print(f"Zero elements returned from {url}")
                else:
                    print(f"HTML/XML error returned from {url}: {text[:100]}")
            else:
                print(f"HTTP error {res.status_code} from {url}")
        except Exception as e:
            print(f"Request failed for {url}: {e}")
            time.sleep(2)

    if not data or "elements" not in data or len(data["elements"]) == 0:
        raise RuntimeError("Failed to fetch valid OSM elements from all endpoints.")

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

    print(f"Success! Processed {len(data['elements'])} elements into {len(tile_stats)} tiles.")

if __name__ == "__main__":
    main()
