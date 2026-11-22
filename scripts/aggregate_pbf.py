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

class BarrierTileHandler(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.tile_stats = {}
        self.processed = 0
        self.matched = 0

    def node(self, n):
        self.processed += 1
        tags = n.tags
        
        # タグが存在しないノードは即スキップ（OSMの大半は道路形状ノード等のタグなしノード）
        if not tags:
            return

        # 対象地物かどうかのクイック判定
        is_target = False
        if "barrier" in tags and tags["barrier"] == "kerb":
            is_target = True
        elif "kerb" in tags:
            is_target = True
        elif tags.get("highway") in ("crossing", "elevator"):
            is_target = True
        elif "shop" in tags or "amenity" in tags:
            is_target = True
        elif "wheelchair" in tags or "toilets:wheelchair" in tags:
            is_target = True

        if not is_target:
            return

        loc = n.location
        if not loc.valid():
            return

        self.matched += 1
        lat = loc.lat
        lon = loc.lon

        # 登録状況判定
        is_reg = False
        if tags.get("barrier") == "kerb" or "kerb" in tags:
            k = tags.get("kerb", "")
            if k and k != "unknown":
                is_reg = True
        elif tags.get("highway") == "elevator":
            is_reg = True
        elif tags.get("wheelchair") in ("yes", "limited", "no"):
            is_reg = True
        elif tags.get("toilets:wheelchair") in ("yes", "limited", "no"):
            is_reg = True

        x, y = lat_lng_to_tile(lat, lon, TILE_BASE_ZOOM)
        key = f"{x}_{y}"

        if key not in self.tile_stats:
            self.tile_stats[key] = [0, 0] # [total, tagged] 配列でメモリ節約

        self.tile_stats[key][0] += 1
        if is_reg:
            self.tile_stats[key][1] += 1

def main():
    if len(sys.argv) < 2:
        print("Usage: python aggregate_pbf.py <path_to_pbf>")
        sys.exit(1)

    pbf_file = sys.argv[1]
    if not os.path.exists(pbf_file):
        print(f"Error: File not found: {pbf_file}")
        sys.exit(1)

    print(f"Start processing: {pbf_file}")
    start_time = time.time()

    handler = BarrierTileHandler()
    handler.apply_file(pbf_file, locations=True)

    elapsed = time.time() - start_time
    print(f"Processed: {handler.processed:,} nodes in {elapsed:.1f}s.")
    print(f"Matched: {handler.matched:,} features in {len(handler.tile_stats):,} tiles.")

    os.makedirs("data", exist_ok=True)
    out_path = os.path.join("data", "tiles.json")

    # JSON出力形式に変換
    formatted_tiles = {
        k: {"total": v[0], "tagged": v[1]}
        for k, v in handler.tile_stats.items()
    }

    payload = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base_zoom": TILE_BASE_ZOOM,
        "tiles": formatted_tiles
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(',', ':')) # 空白を除去してファイルサイズ圧縮

    print(f"Saved: {out_path} ({os.path.getsize(out_path) / (1024*1024):.2f} MB)")

if __name__ == "__main__":
    main()
