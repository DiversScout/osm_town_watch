import json
import math
import os
import sys
import time
import glob
import osmium

TILE_BASE_ZOOM = 16
BLOCK_ZOOM = 10  # 地物を束ねる上位区画ズーム (約40km四方)

def lat_lng_to_tile(lat, lng, zoom):
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    x = int((lng + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y

def evaluate_feature(tags):
    # 縁石 (kerb)
    if tags.get("barrier") == "kerb" or "kerb" in tags:
        k = tags.get("kerb", "")
        if k in ("flush", "lowered"):
            return "kerb", 1, "passable", f"段差解消縁石 ({k})"
        elif k == "raised":
            return "kerb", 1, "blocked", "段差あり縁石 (raised)"
        elif k and k != "unknown":
            return "kerb", 1, "limited", f"縁石 ({k})"
        return "kerb", 0, "unknown", "縁石 (段差未記録)"

    # エレベーター
    if tags.get("highway") == "elevator":
        return "elevator", 1, "passable", "エレベーター"

    # 車椅子対応トイレ
    if tags.get("toilets:wheelchair") in ("yes", "limited", "no"):
        val = tags.get("toilets:wheelchair")
        p = "passable" if val == "yes" else ("limited" if val == "limited" else "blocked")
        return "toilet", 1, p, "車椅子対応トイレ"

    # 車椅子対応施設
    if tags.get("wheelchair"):
        w = tags.get("wheelchair")
        p = "passable" if w == "yes" else ("limited" if w == "limited" else "blocked")
        name = tags.get("name", "施設")
        return "poi", 1, p, f"{name} (車椅子:{w})"

    # 横断歩道
    if tags.get("highway") == "crossing":
        return "crossing", 0, "unknown", "横断歩道 (未登録)"

    # 一般店舗・施設
    name = tags.get("name", "施設/店舗")
    return "poi", 0, "unknown", f"{name} (未登録)"

class RegionalTileHandler(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.tile_stats = {}      # { "x_y": [total, tagged] }
        self.block_features = {}  # { "bx_by": [ [id, lat, lon, type, reg, pass, desc], ... ] }
        self.processed = 0
        self.matched = 0

    def node(self, n):
        self.processed += 1
        tags = n.tags
        if not tags:
            return

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

        # 座標の安全な取得
        try:
            loc = n.location
            if not loc.valid():
                return
            lat = round(loc.lat, 6)
            lon = round(loc.lon, 6)
        except BaseException:
            # osmium.InvalidLocationError など C++ 例外を含むすべての基底例外を捕捉
            return

        self.matched += 1
        feat_type, reg, pass_status, desc = evaluate_feature(tags)

        # 1. Zoom 16 タイル統計
        x, y = lat_lng_to_tile(lat, lon, TILE_BASE_ZOOM)
        key = f"{x}_{y}"
        if key not in self.tile_stats:
            self.tile_stats[key] = [0, 0]
        self.tile_stats[key][0] += 1
        if reg == 1:
            self.tile_stats[key][1] += 1

        # 2. Zoom 10 ブロック別個別地物
        bx, by = lat_lng_to_tile(lat, lon, BLOCK_ZOOM)
        bkey = f"{bx}_{by}"
        if bkey not in self.block_features:
            self.block_features[bkey] = []
        self.block_features[bkey].append([
            n.id, lat, lon, feat_type, reg, pass_status, desc
        ])

def main():
    if len(sys.argv) < 2:
        print("Usage: python aggregate_pbf.py <pbf_dir_or_file>")
        sys.exit(1)

    target_path = sys.argv[1]
    if os.path.isdir(target_path):
        pbf_files = sorted(glob.glob(os.path.join(target_path, "*.osm.pbf")))
    else:
        pbf_files = [target_path]

    if not pbf_files:
        print(f"Error: No PBF files found in {target_path}")
        sys.exit(1)

    handler = RegionalTileHandler()
    total_start = time.time()

    for pbf in pbf_files:
        if not os.path.exists(pbf):
            print(f"Warning: File not found: {pbf}")
            continue

        size_mb = os.path.getsize(pbf) / (1024 * 1024)
        print(f"Reading: {pbf} ({size_mb:.1f} MB) ...")
        t0 = time.time()

        # locations=True でノード座標を有効化
        handler.apply_file(pbf, locations=True)
        print(f"Finished {pbf} in {time.time() - t0:.1f}s")

    print(f"All files processed in {time.time() - total_start:.1f}s.")
    print(f"Total processed nodes: {handler.processed:,}")
    print(f"Matched barrier features: {handler.matched:,} across {len(handler.tile_stats):,} Zoom 16 tiles.")

    # 出力先ディレクトリの準備
    os.makedirs("data", exist_ok=True)
    blocks_dir = os.path.join("data", "blocks")
    os.makedirs(blocks_dir, exist_ok=True)

    # 1. Zoom 10 ブロック別地物の書き出し（ブロック単位で更新）
    print("Writing Zoom 10 block feature files...")
    for bkey, feats in handler.block_features.items():
        block_file = os.path.join(blocks_dir, f"{bkey}.json")
        with open(block_file, "w", encoding="utf-8") as f:
            json.dump(feats, f, separators=(',', ':'))

    # 2. 全国サマリー (data/tiles.json) の読み込み＆差分マージ
    summary_path = os.path.join("data", "tiles.json")
    existing_tiles = {}

    if os.path.exists(summary_path):
        try:
            with open(summary_path, "r", encoding="utf-8") as f:
                old_data = json.load(f)
                existing_tiles = old_data.get("tiles", {})
                print(f"Loaded existing summary: {len(existing_tiles):,} tiles.")
        except Exception as e:
            print(f"Warning: Failed to load existing summary ({e}), creating new.")

    # 今回走査した地方のタイルデータで上書きマージ
    for k, v in handler.tile_stats.items():
        existing_tiles[k] = {"total": v[0], "tagged": v[1]}

    summary_payload = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base_zoom": TILE_BASE_ZOOM,
        "block_zoom": BLOCK_ZOOM,
        "tiles": existing_tiles
    }

    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary_payload, f, separators=(',', ':'))

    print(f"Saved merged nationwide summary: {summary_path} ({len(existing_tiles):,} tiles total, {os.path.getsize(summary_path)/(1024*1024):.2f} MB)")

if __name__ == "__main__":
    main()
