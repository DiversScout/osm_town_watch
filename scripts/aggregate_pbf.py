import json
import math
import os
import sys
import time
import osmium

TILE_BASE_ZOOM = 16
BLOCK_ZOOM = 10  # 約40km四方で地物を集約

# 移動困難者にとって目的地となり得る屋内滞在型 amenity
TARGET_AMENITIES = {
    # 飲食
    "restaurant", "cafe", "fast_food", "food_court", "bar", "pub",
    # 医療・福祉
    "hospital", "clinic", "pharmacy", "dentist", "social_facility",
    # 公共・行政・金融
    "townhall", "community_centre", "library", "post_office", "bank",
    # 文化・集客
    "cinema", "theatre", "museum", "place_of_worship"
}

def lat_lng_to_tile(lat, lng, zoom):
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    x = int((lng + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y

def evaluate_mobility_feature(tags):
    # 1. 縁石・段差 (kerb)
    if tags.get("barrier") == "kerb" or "kerb" in tags:
        k = tags.get("kerb", "")
        if k in ("flush", "lowered"):
            return "kerb", 1, "passable", f"段差解消縁石 ({k})"
        elif k == "raised":
            return "kerb", 1, "blocked", "段差あり縁石 (raised)"
        elif k and k != "unknown":
            return "kerb", 1, "limited", f"縁石 ({k})"
        return "kerb", 0, "unknown", "縁石 (段差未記録・要調査)"

    # 2. 垂直移動・スロープ (elevator / ramp)
    if tags.get("highway") == "elevator":
        w = tags.get("wheelchair", "yes")
        p = "blocked" if w == "no" else "passable"
        return "elevator", 1, p, "エレベーター"

    if tags.get("highway") == "ramp" or "ramp" in tags or "ramp:wheelchair" in tags:
        rw = tags.get("ramp:wheelchair") or tags.get("wheelchair")
        p = "passable" if rw in ("yes", "designated") else ("limited" if rw == "limited" else "passable")
        return "ramp", 1, p, "スロープ (傾斜路)"

    # 3. 横断歩道 (crossing)
    if tags.get("highway") == "crossing":
        k = tags.get("kerb", "")
        tp = tags.get("tactile_paving", "")
        has_info = bool(k or tp)
        p = "passable" if k in ("flush", "lowered") else ("blocked" if k == "raised" else "unknown")
        desc = "横断歩道" + (f" ({k})" if k else "") + (" [点字ブロック]" if tp == "yes" else "")
        return "crossing", (1 if has_info else 0), p, desc

    # 4. 点字ブロック単独ノード (tactile_paving)
    if tags.get("tactile_paving") in ("yes", "incorrect"):
        val = tags.get("tactile_paving")
        p = "passable" if val == "yes" else "limited"
        return "tactile_paving", 1, p, f"点字ブロック (視覚誘導:{val})"

    # 5. トイレ (toilets)
    if tags.get("amenity") == "toilets" or "toilets:wheelchair" in tags:
        tw = tags.get("toilets:wheelchair") or tags.get("wheelchair")
        if tw == "yes":
            return "toilet", 1, "passable", "多機能・車椅子対応トイレ"
        elif tw == "limited":
            return "toilet", 1, "limited", "車椅子利用可トイレ (要介助)"
        elif tw == "no":
            return "toilet", 1, "blocked", "車椅子非対応トイレ"
        return "toilet", 0, "unknown", "公衆トイレ (対応状況未調査)"

    # 6. 交通拠点 (station)
    if tags.get("railway") in ("station", "subway_entrance", "halt"):
        w = tags.get("wheelchair")
        name = tags.get("name", "駅出入口")
        if w in ("yes", "designated"):
            return "station", 1, "passable", f"{name} (車椅子対応)"
        elif w == "limited":
            return "station", 1, "limited", f"{name} (要介助)"
        elif w == "no":
            return "station", 1, "blocked", f"{name} (非対応)"
        return "station", 0, "unknown", f"{name} (対応状況未調査)"

    # 7. 目的地としての店舗・滞在施設 (shop / amenity)
    is_shop = "shop" in tags
    amenity_val = tags.get("amenity")
    is_target_amenity = amenity_val in TARGET_AMENITIES

    if is_shop or is_target_amenity:
        w = tags.get("wheelchair")
        name = tags.get("name") or (f"店舗 ({tags.get('shop')})" if is_shop else f"施設 ({amenity_val})")
        
        if w in ("yes", "designated"):
            return "shop" if is_shop else "poi", 1, "passable", f"{name} (車椅子可)"
        elif w == "limited":
            return "shop" if is_shop else "poi", 1, "limited", f"{name} (車椅子要介助)"
        elif w == "no":
            return "shop" if is_shop else "poi", 1, "blocked", f"{name} (車椅子不可)"
        
        # 未調査の店舗・施設は「要マッピング対象」として追跡
        return "shop" if is_shop else "poi", 0, "unknown", f"{name} (車椅子利用未調査)"

    # ポスト、ベンチ、自販機、街灯などはすべて None で除外
    return None

class MobilityBarrierHandler(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.tile_stats = {}
        self.block_features = {}
        self.processed = 0
        self.matched = 0

    def node(self, n):
        self.processed += 1
        tags = n.tags
        if not tags:
            return

        eval_result = evaluate_mobility_feature(tags)
        if not eval_result:
            return

        try:
            loc = n.location
            if not loc.valid():
                return
            lat = round(loc.lat, 6)
            lon = round(loc.lon, 6)
        except Exception:
            return

        self.matched += 1
        feat_type, reg, pass_status, desc = eval_result

        # 1. Zoom 16 タイル統計
        x, y = lat_lng_to_tile(lat, lon, TILE_BASE_ZOOM)
        key = f"{x}_{y}"
        if key not in self.tile_stats:
            self.tile_stats[key] = [0, 0]
        self.tile_stats[key][0] += 1
        if reg == 1:
            self.tile_stats[key][1] += 1

        # 2. Zoom 10 ブロック別地物
        bx, by = lat_lng_to_tile(lat, lon, BLOCK_ZOOM)
        bkey = f"{bx}_{by}"
        if bkey not in self.block_features:
            self.block_features[bkey] = []
        self.block_features[bkey].append([
            n.id, lat, lon, feat_type, reg, pass_status, desc
        ])

def main():
    if len(sys.argv) < 2:
        print("Usage: python aggregate_pbf.py <pbf_file>")
        sys.exit(1)

    pbf_file = sys.argv[1]
    if not os.path.exists(pbf_file):
        print(f"Error: File not found: {pbf_file}")
        sys.exit(1)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    data_dir = os.path.join(repo_root, "data")
    blocks_dir = os.path.join(data_dir, "blocks")

    size_mb = os.path.getsize(pbf_file) / (1024 * 1024)
    print(f"Processing pre-filtered file: {pbf_file} ({size_mb:.1f} MB)...")
    start_time = time.time()

    handler = MobilityBarrierHandler()
    handler.apply_file(pbf_file, locations=True)

    elapsed = time.time() - start_time
    print(f"Finished in {elapsed:.1f}s.")
    print(f"Processed: {handler.processed:,} nodes. Matched: {handler.matched:,} mobility features.")
    print(f"Summary: {len(handler.tile_stats):,} Zoom 16 tiles, {len(handler.block_features):,} Zoom 10 blocks.")

    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(blocks_dir, exist_ok=True)

    # 1. Zoom 10 ブロック別地物の書き出し
    print("Writing Zoom 10 block files...")
    for bkey, feats in handler.block_features.items():
        block_file = os.path.join(blocks_dir, f"{bkey}.json")
        with open(block_file, "w", encoding="utf-8") as f:
            json.dump(feats, f, separators=(',', ':'))

    # 2. 全国サマリー (data/tiles.json) の書き出し
    summary_tiles = {
        k: {"total": v[0], "tagged": v[1]}
        for k, v in handler.tile_stats.items()
    }

    summary_payload = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base_zoom": TILE_BASE_ZOOM,
        "block_zoom": BLOCK_ZOOM,
        "tiles": summary_tiles
    }

    summary_path = os.path.join(data_dir, "tiles.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary_payload, f, separators=(',', ':'))

    print(f"Saved nationwide summary: {summary_path} ({len(summary_tiles):,} tiles)")
    print("Build completed successfully.")

if __name__ == "__main__":
    main()
