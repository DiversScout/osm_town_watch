import json
import math
import os
import sys
import time
import osmium

TILE_BASE_ZOOM = 16

def lat_lng_to_tile(lat, lng, zoom):
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    x = int((lng + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y

def is_registered(tags):
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

def is_target_feature(tags):
    if "barrier" in tags and tags["barrier"] == "kerb":
        return True
    if "kerb" in tags:
        return True
    if tags.get("highway") in ["crossing", "elevator"]:
        return True
    if "shop" in tags or "amenity" in tags:
        return True
    if "wheelchair" in tags or "toilets:wheelchair" in tags:
        return True
    return False

class BarrierTileHandler(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.tile_stats = {}
        self.processed_nodes = 0
        self.matched_features = 0

    def node(self, n):
        self.processed_nodes += 1
        tags = n.tags

        if not is_target_feature(tags):
            return

        if not n.location.valid():
            return

        self.matched_features += 1
        lat = n.location.lat
        lon = n.location.lon

        x, y = lat_lng_to_tile(lat, lon, TILE_BASE_ZOOM)
        key = f"{x}_{y}"

        if key not in self.tile_stats:
            self.tile_stats[key] = {"total": 0, "tagged": 0}

        self.tile_stats[key]["total"] += 1
        if is_registered(tags):
            self.tile_stats[key]["tagged"] += 1

def main():
    if len(sys.argv) < 2:
        print("Usage: python aggregate_pbf.py <path_to_pbf>")
        sys.exit(1)

    pbf_file = sys.argv[1]
    if not os.path.exists(pbf_file):
        print(f"Error: File not found: {pbf_file}")
        sys.exit(1)

    print(f"Start processing {pbf_file}...")
    start_time = time.time()

    handler = BarrierTileHandler()
    # locations=True でノード座標を直接取得
    handler.apply_file(pbf_file, locations=True)

    elapsed = time.time() - start_time
    print(f"Processed {handler.processed_nodes} nodes in {elapsed:.1f}s.")
    print(f"Matched {handler.matched_features} barrier-related features across {len(handler.tile_stats)} tiles.")

    os.makedirs("data", exist_ok=True)
    out_path = os.path.join("data", "tiles.json")

    payload = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base_zoom": TILE_BASE_ZOOM,
        "tiles": handler.tile_stats
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    print(f"Saved nationwide tile summary to {out_path}")

if __name__ == "__main__":
    main()
