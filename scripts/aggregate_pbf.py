import json
import math
import os
import sys
import time
import osmium

TILE_BASE_ZOOM = 16
BLOCK_ZOOM = 10  # 約40km四方で地物を集約

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
    print(f"Start processing nationwide file: {pbf_file} ({size_mb:.1f} MB)...")
    start_time = time.time()

    tile_stats = {}      # { "x_y": [total, tagged] }
    block_features = {}  # { "bx_by": [ [id, lat, lon, type, reg, pass, desc], ... ] }

    processed = 0
    matched = 0

    # osmium.io.Reader で Node のみをダイレクトにストリーム読み込み
    reader = osmium.io.Reader(pbf_file, osmium.osm.osm_entity_bits.NODE)

    while not reader.eof():
        ob = reader.read()
        if not ob:
            break

        processed += 1

        # タグ辞書を取得
        tags = {t.k: t.v for t in ob.tags}
        if not tags:
            continue

        # 対象地物クイック判定
        is_target = False
        if tags.get("barrier") == "kerb" or "kerb" in tags:
            is_target = True
        elif tags.get("highway") in ("crossing", "elevator"):
            is_target = True
        elif "shop" in tags or "amenity" in tags:
            is_target = True
        elif "wheelchair" in tags or "toilets:wheelchair" in tags:
            is_target = True

        if not is_target:
            continue

        # 座標の取得 (Reader直下の Node は位置情報を保持している)
        try:
            loc = ob.location
            if not loc.valid():
                continue
            lat = round(loc.lat, 6)
            lon = round(loc.lon, 6)
        except Exception:
            continue

        matched += 1
        feat_type, reg, pass_status, desc = evaluate_feature(tags)

        # 1. Zoom 16 タイル統計
        x, y = lat_lng_to_tile(lat, lon, TILE_BASE_ZOOM)
        key = f"{x}_{y}"
        if key not in tile_stats:
            tile_stats[key] = [0, 0]
        tile_stats[key][0] += 1
        if reg == 1:
            tile_stats[key][1] += 1

        # 2. Zoom 10 ブロック別地物
        bx, by = lat_lng_to_tile(lat, lon, BLOCK_ZOOM)
        bkey = f"{bx}_{by}"
        if bkey not in block_features:
            block_features[bkey] = []
        block_features[bkey].append([
            ob.id, lat, lon, feat_type, reg, pass_status, desc
        ])

        if matched % 200000 == 0:
            print(f"Matched {matched:,} features (processed {processed:,} nodes)...")

    reader.close()

    elapsed = time.time() - start_time
    print(f"Finished parsing in {elapsed:.1f}s.")
    print(f"Total processed: {processed:,} nodes.")
    print(f"Matched features: {matched:,} across {len(tile_stats):,} tiles and {len(block_features):,} blocks.")

    # 出力先ディレクトリの準備
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(blocks_dir, exist_ok=True)

    # 1. Zoom 10 ブロック別地物の書き出し
    print("Writing Zoom 10 block files...")
    for bkey, feats in block_features.items():
        block_file = os.path.join(blocks_dir, f"{bkey}.json")
        with open(block_file, "w", encoding="utf-8") as f:
            json.dump(feats, f, separators=(',', ':'))

    # 2. 全国サマリー (data/tiles.json) の書き出し
    summary_tiles = {
        k: {"total": v[0], "tagged": v[1]}
        for k, v in tile_stats.items()
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

    print(f"Saved nationwide summary: {summary_path} ({len(summary_tiles):,} tiles, {os.path.getsize(summary_path)/(1024*1024):.2f} MB)")
    print("Nationwide build completed successfully.")

if __name__ == "__main__":
    main()
