# -*- coding: utf-8 -*-
"""GDB 集成关键点验证：读图层/字段/几何 + 坐标转换 + 生成简化 GeoJSON。"""
import json
import pyogrio
import geopandas as gpd

GDB = "/Users/sux/Desktop/hqz-survey/data/project-1.gdb"
OUT_GEOJSON = "/Users/sux/Desktop/hqz-survey/data/_preview.geojson"

print("=" * 60)
print("1. 图层列表")
for row in pyogrio.list_layers(GDB):
    print("  ", row)

print("=" * 60)
print("2. 读「抚育区」前 5 行 (GeoDataFrame)")
gdf = pyogrio.read_dataframe(GDB, layer="抚育区", max_features=5)
print("  行数:", len(gdf), " 字段数:", len(gdf.columns) - 1)  # 减去 geometry 列
print("  CRS:", gdf.crs)
print("  几何类型:", gdf.geom_type.iloc[0])
print("  字段名:", list(gdf.columns)[:-1])
g0 = gdf.geometry.iloc[0]
print("  首要素几何 bounds:", g0.bounds, " 顶点数(估):", int(g0.area * 0 + len(g0.exterior.coords) if g0.geom_type == 'Polygon' else sum(len(p.exterior.coords) for p in g0.geoms)))

print("=" * 60)
print("3. 坐标转换 EPSG:4507 -> WGS84(4326)")
gdf84 = gdf.to_crs(epsg=4326)
c0 = gdf84.geometry.iloc[0].centroid
print(f"  首要素质心 WGS84: lng={c0.x:.6f}, lat={c0.y:.6f}")
print(f"  （威信县约 lng 105.0-105.4, lat 27.4-28.1，落点应在此区间）")
print(f"  首要素 bounds(WGS84): {gdf84.geometry.iloc[0].bounds}")

print("=" * 60)
print("4. 生成简化 GeoJSON（前 50 个抚育区质心点）")
gdf50 = pyogrio.read_dataframe(GDB, layer="抚育区", max_features=50).to_crs(epsg=4326)
gdf50_centroid = gdf50.copy()
gdf50_centroid["geometry"] = gdf50_centroid.geometry.centroid
# 只保留关键属性
keep = ["乡镇", "村", "林班", "小班", "优势树", "林种", "土地权"]
keep = [c for c in keep if c in gdf50_centroid.columns]
gdf_out = gdf50_centroid[keep + ["geometry"]]
gdf_out.to_file(OUT_GEOJSON, driver="GeoJSON")
print(f"  写入 {len(gdf_out)} 个质心点到: {OUT_GEOJSON}")
print("  首行属性:", gdf_out.iloc[0][keep].to_dict())
print("  首行坐标:", list(gdf_out.geometry.iloc[0].coords)[0])

print("=" * 60)
print("5. 字段名与现有 SUBCOMPARTMENT_FIELD_MAP 对齐核对")
expected = ["州", "乡镇", "县", "村", "林班", "小班", "土地权属", "优势树种",
            "抚育面积", "封育对象", "封育年限", "封育类型", "封育方式",
            "封育措施", "育林措施", "小班面积", "项目名称"]
gdb_fields = [c for c in gdf.columns if c != "geometry"]
print("  GDB 实际字段:", gdb_fields)
print("  映射期望字段 -> GDB 命中:")
for e in expected:
    hit = e in gdb_fields
    fuzzy = [g for g in gdb_fields if e in g or g in e]
    mark = "OK" if hit else ("~" + str(fuzzy) if fuzzy else "MISS")
    print(f"    {e}: {mark}")
