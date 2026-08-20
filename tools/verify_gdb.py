# -*- coding: utf-8 -*-
"""GDB 结构探测 — 改动 GDB 相关代码前先跑，不猜结构（AGENTS.md）。

用法:
    python tools/verify_gdb.py <.gdb 目录路径> [--layer 图层名] [-o 输出.geojson]

探测内容:
    1. 图层列表（名称/几何类型/行数/字段数）
    2. 分类图层命中（人工造林/封山育林/退化林修复）
    3. 指定图层字段 + 别名解析核对（township/village/forest_compartment/...）
    4. 坐标转换 EPSG:4507 → WGS84 抽查（落点应在中国范围）
    5. 可选：质心点 GeoJSON 导出（-o 指定路径时）
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from survey.core import gdb as GDB  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="GDB 结构探测")
    ap.add_argument("gdb", help=".gdb 目录路径")
    ap.add_argument("--layer", help="细看字段的图层名（默认取第一个分类图层，无则第一个图层）")
    ap.add_argument("-o", "--out", help="可选：导出前 50 个质心点 GeoJSON 到该路径")
    args = ap.parse_args()

    gdb_path = args.gdb
    if not Path(gdb_path).is_dir():
        sys.exit(f"路径不存在或不是目录: {gdb_path}")

    print("=" * 60)
    print("1. 图层列表")
    try:
        layers = GDB.list_layers(gdb_path)
    except Exception as e:
        sys.exit(f"读取图层失败（检查 pyogrio/geopandas/pyproj 是否安装）: {e}")
    if not layers:
        sys.exit("GDB 内没有任何图层")
    for l in layers:
        print(f"   {l['name']}  geom={l['geometry_type']} 行={l['row_count']} 字段={l['field_count']}")

    print("=" * 60)
    print("2. 分类图层命中")
    scan = GDB.scan_classified_layers(gdb_path)
    for l in scan["classified"]:
        print(f"   [命中] {l['name']} -> {l['category']}（{l['row_count']} 行）")
    for l in scan["skipped"]:
        print(f"   [跳过] {l['name']}（{l['reason']}）")

    layer = args.layer
    if not layer:
        layer = scan["classified"][0]["name"] if scan["classified"] else layers[0]["name"]
    print("=" * 60)
    print(f"3. 图层「{layer}」字段与别名解析")
    gdf = GDB.read_layer(gdb_path, layer, max_features=5)
    cols = [c for c in gdf.columns if c != "geometry"]
    print(f"   字段: {cols}")
    print(f"   CRS: {gdf.crs}  几何: {gdf.geom_type.iloc[0]}")
    props = gdf.iloc[0].to_dict()
    for kind in GDB.GDB_FIELD_ALIASES:
        hit = GDB.resolve_gdb_field(cols, kind, props)
        print(f"   {kind:20s} -> {hit or 'MISS'}")

    print("=" * 60)
    print("4. 坐标转换 → WGS84")
    g84 = GDB.to_wgs84(gdf)
    c = g84.geometry.iloc[0].centroid
    print(f"   首要素质心: lng={c.x:.6f}, lat={c.y:.6f}")
    if not (73 < c.x < 136 and 3 < c.y < 54):
        print("   !!! 落点不在中国范围，坐标系假设（EPSG:4507）可能有误")

    if args.out:
        print("=" * 60)
        print(f"5. 导出前 50 个质心点 → {args.out}")
        g50 = GDB.read_layer(gdb_path, layer, max_features=50)
        gj = GDB.to_centroid_geojson(GDB.to_wgs84(g50))
        Path(args.out).write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")
        print(f"   写入 {len(gj.get('features', []))} 个质心点")


if __name__ == "__main__":
    main()
