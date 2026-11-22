import json
import math
import os
import time
import requests

# 北九州市〜門司港・関門エリア全域 (南緯度, 西経度, 北緯度, 東経度)
BBOX = "33.80,130.65,34.05,131.05"

TILE_BASE_ZOOM = 16

# 応答精度の高いエンドポイント順
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
    # 座標(lat, lon)とtagsを確実に同時に返す標準クエリ
    query = f"""
    [out:json][timeout:60];
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
    out body qt;
    """

    data = None
    for url in ENDPOINTS:
        try:
            print(f"Fetching from {url}...")
            res = requests.post(
                url,
                data=f"data={query}".encode('utf-8'),
                headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
                timeout=90
            )
            if res.status_code == 200:
                text = res.text.strip()
                if not text.startswith("<"):
                    json_data = res.json()
                    if "elements" in json_data and len(json_data["elements"]) > 0:
                        data = json_data
                        print(f"Successfully fetched {len(data['elements'])} elements from OSM.")
                        break
        except Exception as e:
            print(f"Failed ({url}): {e}")
            time.sleep(2)

    if not data or "elements" not in data or len(data["elements"]) == 0:
        raise RuntimeError("Failed to fetch valid OSM elements from all
