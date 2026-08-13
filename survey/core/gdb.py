# -*- coding: utf-8 -*-
"""GDB 解析层 — 读图层/字段/几何 + 坐标转换 + 生成 GeoJSON/md 快照。

依赖: pyogrio, geopandas, pyproj

坐标系: GDB 源为 EPSG:4507 (CGCS2000 / 3-degree Gauss-Kruger CM 102E)
        输出统一转 EPSG:4326 (WGS84) 供前端地图渲染
"""
import json
import os
import shutil
import zipfile
from pathlib import Path

import pyogrio
import geopandas as gpd

# GDB 存储根目录
GDB_STORAGE = Path(__file__).resolve().parent.parent.parent / "data" / "gdb"


def list_layers(gdb_path):
    """列出 GDB 所有图层。

    Returns:
        [{"name", "geometry_type", "row_count", "field_count"}, ...]
    """
    layers = []
    for row in pyogrio.list_layers(str(gdb_path)):
        name = row[0]
        geom_type = row[1] if len(row) > 1 else ""
        try:
            gdf = pyogrio.read_dataframe(str(gdb_path), layer=name, max_features=1)
            field_count = len(gdf.columns) - 1  # 减去 geometry
            # 获取行数
            info = pyogrio.read_info(str(gdb_path), layer=name)
            row_count = info.get("features", 0)
        except Exception:
            field_count = 0
            row_count = 0
        layers.append({
            "name": name,
            "geometry_type": str(geom_type),
            "row_count": row_count,
            "field_count": field_count,
        })
    return layers


def layer_fields(gdb_path, layer):
    """获取某图层的字段列表。

    Returns:
        [{"name", "dtype"}, ...]
    """
    gdf = pyogrio.read_dataframe(str(gdb_path), layer=layer, max_features=1)
    fields = []
    for col in gdf.columns:
        if col == "geometry":
            continue
        fields.append({"name": col, "dtype": str(gdf[col].dtype)})
    return fields


def read_layer(gdb_path, layer, max_features=None):
    """读取图层为 GeoDataFrame。"""
    kwargs = {"layer": layer}
    if max_features:
        kwargs["max_features"] = max_features
    return pyogrio.read_dataframe(str(gdb_path), **kwargs)


def to_wgs84(gdf):
    """坐标系转换到 WGS84 (EPSG:4326)。"""
    if gdf.crs is None:
        # 假设源为 EPSG:4507
        gdf = gdf.set_crs(epsg=4507)
    return gdf.to_crs(epsg=4326)


def to_geojson(gdf, properties=None):
    """GeoDataFrame → GeoJSON dict。

    Args:
        gdf: GeoDataFrame（应已转 WGS84）
        properties: 要保留的属性字段列表，None 则保留全部

    Returns:
        GeoJSON dict
    """
    if properties:
        keep = [c for c in properties if c in gdf.columns] + ["geometry"]
        gdf = gdf[keep]
    # 确保是 WGS84
    if gdf.crs and gdf.crs.to_epsg() != 4326:
        gdf = to_wgs84(gdf)
    return json.loads(gdf.to_json())


def to_centroid_geojson(gdf, properties=None):
    """生成质心点 GeoJSON（用于地图定位/列表渲染）。"""
    gdf_c = gdf.copy()
    gdf_c["geometry"] = gdf_c.geometry.centroid
    return to_geojson(gdf_c, properties)


def to_md(gdb_path, out_dir):
    """生成 gdb2md 快照（AI 高速路索引）。

    每个图层一个 md 文件，含属性表 + 质心坐标。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    layers = list_layers(gdb_path)
    readme_lines = [
        "# GDB 图层总览",
        "",
        f"- 源文件: `{gdb_path}`",
        f"- 图层数: {len(layers)}",
        "",
        "| # | 图层名 | 几何类型 | 行数 | 字段数 | 导出文件 |",
        "|---|---|---|---:|---:|---|",
    ]

    for i, layer_info in enumerate(layers, 1):
        name = layer_info["name"]
        safe_name = f"{i:02d}_{name}"
        md_path = out_dir / f"{safe_name}.md"

        gdf = read_layer(gdb_path, name)
        gdf84 = to_wgs84(gdf)
        # 添加质心坐标列
        centroid = gdf84.geometry.centroid
        gdf_export = gdf84.copy()
        gdf_export["几何中心X"] = centroid.x.round(6)
        gdf_export["几何中心Y"] = centroid.y.round(6)

        # 写 md
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"# {name}\n\n")
            f.write(f"- 几何类型: `{layer_info['geometry_type']}`\n")
            f.write(f"- 行数: {layer_info['row_count']}\n")
            f.write(f"- 字段数: {layer_info['field_count']}\n")
            f.write(f"- 坐标系: EPSG:4326 (WGS84)\n\n")
            f.write("> 说明：本文件为该图层属性表的 Markdown 导出。末尾附加「几何中心X / 几何中心Y」(WGS84质心坐标) 便于定位。\n\n")
            f.write("## 数据表\n\n")
            # 表头
            cols = [c for c in gdf_export.columns if c != "geometry"]
            f.write("| " + " | ".join(cols) + " |\n")
            f.write("|" + "|".join(["---"] * len(cols)) + "|\n")
            # 数据行
            for _, row in gdf_export.iterrows():
                vals = []
                for c in cols:
                    v = row[c]
                    if v is None or (hasattr(v, "__float__") and v != v):
                        vals.append("")
                    else:
                        vals.append(str(v))
                f.write("| " + " | ".join(vals) + " |\n")

        readme_lines.append(
            f"| {i} | {name} | {layer_info['geometry_type']} | "
            f"{layer_info['row_count']} | {layer_info['field_count']} | "
            f"[{safe_name}.md]({safe_name}.md) |"
        )

    with open(out_dir / "README.md", "w", encoding="utf-8") as f:
        f.write("\n".join(readme_lines) + "\n")


def save_gdb_upload(upload_file, project_id, gid):
    """保存上传的 GDB 文件到存储目录。

    GDB 可以是目录（已解压）或 zip 包。

    Returns:
        {"gid", "path", "gdb_dir"}
    """
    gdb_dir = GDB_STORAGE / gid
    gdb_dir.mkdir(parents=True, exist_ok=True)

    # GDB 可能以 zip 上传
    if hasattr(upload_file, "filename") and upload_file.filename.lower().endswith(".zip"):
        zip_path = gdb_dir / "upload.zip"
        upload_file.save(str(zip_path))
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            zf.extractall(str(gdb_dir))
        zip_path.unlink()
    elif hasattr(upload_file, "filename") and upload_file.filename.lower().endswith(".gdb"):
        # .gdb 是目录，无法直接 save，需走 zip
        raise ValueError("GDB 目录无法直接上传，请压缩为 .zip 后上传")
    else:
        # 尝试当 zip 处理
        zip_path = gdb_dir / "upload.zip"
        upload_file.save(str(zip_path))
        try:
            with zipfile.ZipFile(str(zip_path), "r") as zf:
                zf.extractall(str(gdb_dir))
            zip_path.unlink()
        except zipfile.BadZipFile:
            zip_path.unlink()
            raise ValueError("仅支持 .zip 格式的 GDB 压缩包")

    # 找到 .gdb 目录
    gdb_paths = list(gdb_dir.rglob("*.gdb"))
    if not gdb_paths:
        raise ValueError("压缩包内未找到 .gdb 目录")
    gdb_path = gdb_paths[0]

    return {
        "gid": gid,
        "path": str(gdb_path),
        "gdb_dir": str(gdb_dir),
    }


def delete_gdb_files(gid):
    """删除 GDB 存储目录。"""
    gdb_dir = GDB_STORAGE / gid
    if gdb_dir.exists():
        shutil.rmtree(str(gdb_dir))


def get_gdb_path(gid):
    """获取 GDB 文件路径（用于读取）。"""
    gdb_dir = GDB_STORAGE / gid
    if not gdb_dir.exists():
        return None
    gdb_paths = list(gdb_dir.rglob("*.gdb"))
    return str(gdb_paths[0]) if gdb_paths else None


def generate_geojson_for_project(gdb_path, layer="抚育区", properties=None):
    """为项目生成小班面 GeoJSON + 质心点 GeoJSON。

    Args:
        gdb_path: GDB 文件路径
        layer: 图层名（默认「抚育区」）
        properties: 要保留的属性字段

    Returns:
        {"polygons": GeoJSON dict, "centroids": GeoJSON dict}
    """
    gdf = read_layer(gdb_path, layer)
    gdf84 = to_wgs84(gdf)

    if properties is None:
        # 默认保留关键属性
        default_props = ["乡镇", "村", "林班", "小班", "优势树", "林种",
                         "土地权", "小班面", "经营面", "New_ID"]
        properties = [c for c in default_props if c in gdf84.columns]

    polygons = to_geojson(gdf84, properties)
    centroids = to_centroid_geojson(gdf84, properties)

    return {"polygons": polygons, "centroids": centroids}
