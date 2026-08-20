# -*- coding: utf-8 -*-
"""GDB 解析层 — 读图层/字段/几何 + 坐标转换 + 生成 GeoJSON/md 快照。

依赖: pyogrio, geopandas, pyproj

坐标系: GDB 源为 EPSG:4507 (CGCS2000 / 3-degree Gauss-Kruger CM 102E)
        输出统一转 EPSG:4326 (WGS84) 供前端地图渲染
"""
import json
import logging
import os
import shutil
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

# pyogrio / geopandas 延迟到函数内导入：服务器未装时不影响 app 加载

# GDB 存储根目录
GDB_STORAGE = Path(__file__).resolve().parent.parent.parent / "data" / "gdb"


# ── GDB 图层字段别名解析 ──
# 不同 GDB 图层的字段名差异很大（如 乡镇/乡、村/村委会、小班/新小班号/原小班号、
# 林班/小班面 等），统一用别名映射为内部定位字段，避免导入时整层落空。
GDB_FIELD_ALIASES = {
    "township": ["乡镇", "乡", "县市", "县", "州（市）", "州(市)", "州"],
    "village": ["村", "村委会", "村民委员会"],
    "forest_compartment": ["林班"],
    "subcompartment": ["调查小班号", "小班", "新小班号", "原小班号", "调查号"],
    "area": ["小班面", "小班面积", "经营面", "经营面积", "上报面积", "抚育面积"],
    "feature_id": ["New_ID", "调查小班号", "新小班号", "原小班号", "调查号",
                   "FID_封山育林", "FID_退化林修复", "FID_人工造林"],
}

# ── GDB 图层 → 分类（验收表）映射 ──
# 图层名以这些关键词开头 → 归入对应分类（与 schema 三表一一对应）。
# 顺序即「分类优先级」：先匹配者生效（关键词互不前缀重叠，顺序无影响）。
# 水利水保/草原 分类已下线（表4/表5 删除），对应图层按非分类图层跳过。
GDB_CATEGORY_KEYWORDS = ["人工造林", "封山育林", "退化林修复"]

# 分类 → schema 表 id（导入/导出据此落表）
GDB_CATEGORY_TO_TABLE = {
    "人工造林": "table1",
    "封山育林": "table2",
    "退化林修复": "table3",
}

# 项目名称字段别名（图层属性中读取，无则回退文件名）
GDB_PROJECT_NAME_ALIASES = ["项目名称", "项目", "工程名称", "工程", "项目名"]


def classify_layer(layer_name):
    """按图层名前缀关键词判定所属分类。

    Args:
        layer_name: GDB 图层名

    Returns:
        分类关键词（人工造林/封山育林/退化林修复）或 None（未命中）
    """
    if not layer_name:
        return None
    for kw in GDB_CATEGORY_KEYWORDS:
        if layer_name.startswith(kw):
            return kw
    return None


def read_project_name(gdb_path, layer):
    """从图层属性字段中读取项目名称（别名匹配），无则返回 None。

    取该图层前若干行中第一个有非空值的别名列值；图层内项目名通常恒定。
    """
    try:
        gdf = read_layer(gdb_path, layer, max_features=50)
    except Exception as e:
        # 读失败回退文件名是设计行为，但原因必须留痕（如服务器缺 pyproj）
        logger.warning("读图层 %s 项目名失败，回退文件名: %s", layer, e)
        return None
    cols = [c for c in gdf.columns if c != "geometry"]
    cands = [c for c in GDB_PROJECT_NAME_ALIASES if c in cols]
    if not cands:
        return None
    for c in cands:
        for v in gdf[c].dropna():
            s = str(v).strip()
            if s and s not in ("None", "nan", "NaN"):
                return s
    return None


def derive_project_name(file_name):
    """回退：用上传文件名（去扩展名）作为项目名称。"""
    name = Path(file_name).stem if file_name else "未命名项目"
    return name


def resolve_gdb_field(columns, kind, props=None):
    """在图层字段列中按别名解析内部字段名。

    Args:
        columns: 图层实际字段名 list
        kind: GDB_FIELD_ALIASES 的 key（township/village/forest_compartment/...）
        props: 可选，某行属性 dict。提供时优先返回「有非空值」的别名列，
               避免选中名为 小班 但整列为空的列（如本层实际 ID 在 调查小班号）。

    Returns:
        命中的原始字段名（str）或 None
    """
    aliases = GDB_FIELD_ALIASES.get(kind, [])
    # 精确命中
    matched = [a for a in aliases if a in columns]
    if not matched:
        # 模糊兜底：子串包含
        for a in aliases:
            for c in columns:
                if a and (a in c or c in a):
                    matched.append(c)
    if not matched:
        return None
    if props is None:
        return matched[0]
    # 取值优先：挑有非空/非 NaN 值的列
    def _nonempty(col):
        v = props.get(col, "")
        s = str(v).strip()
        return s not in ("", "None", "nan", "NaN")
    filled = [c for c in matched if _nonempty(c)]
    return filled[0] if filled else matched[0]


def _pick_candidate(props, candidates):
    """从候选列名列表中，挑出当前行有非空值的那一列；都没有则返回首列或 None。

    用于导入时逐行解析（同名空列 vs 异名实值列，如 小班 空但 调查小班号 有值）。
    """
    if not candidates:
        return None
    for c in candidates:
        v = props.get(c, "")
        s = str(v).strip()
        if s not in ("", "None", "nan", "NaN"):
            return c
    return candidates[0]


def list_layers(gdb_path):
    """列出 GDB 所有图层。

    Returns:
        [{"name", "geometry_type", "row_count", "field_count"}, ...]
    """
    layers = []
    import pyogrio
    for row in pyogrio.list_layers(str(gdb_path)):
        name = row[0]
        geom_type = row[1] if len(row) > 1 else ""
        try:
            gdf = pyogrio.read_dataframe(str(gdb_path), layer=name, max_features=1)
            field_count = len(gdf.columns) - 1  # 减去 geometry
            # 获取行数
            info = pyogrio.read_info(str(gdb_path), layer=name)
            row_count = info.get("features", 0)
        except Exception as e:
            # 单层读取失败不阻断整层列表，但原因必须留痕
            logger.warning("读图层 %s 元信息失败（行列数计 0）: %s", name, e)
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
    import pyogrio
    gdf = pyogrio.read_dataframe(str(gdb_path), layer=layer, max_features=1)
    fields = []
    for col in gdf.columns:
        if col == "geometry":
            continue
        fields.append({"name": col, "dtype": str(gdf[col].dtype)})
    return fields


def read_layer(gdb_path, layer, max_features=None):
    """读取图层为 GeoDataFrame。"""
    import pyogrio
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


def find_gdb_dirs(gdb_dir):
    """在一二层目录内找 .gdb 目录（不深层递归）。

    Args:
        gdb_dir: 解压根目录 Path

    Returns:
        [.gdb 目录 Path, ...]（一二层命中即止，不向下深挖）
    """
    found = []
    for p in gdb_dir.iterdir():
        if p.is_dir() and p.suffix == ".gdb":
            found.append(p)
        elif p.is_dir():
            # 第二层
            for p2 in p.iterdir():
                if p2.is_dir() and p2.suffix == ".gdb":
                    found.append(p2)
    return found


def scan_classified_layers(gdb_path):
    """扫描 GDB 图层，按分类关键词筛选。

    Returns:
        {
          classified: [{name, category, row_count}],  # 命中分类关键词的图层
          skipped:    [{name, reason:"非分类图层"}]    # 未命中的图层（提示不读取）
        }
    """
    classified = []
    skipped = []
    try:
        layers = list_layers(gdb_path)
    except Exception as e:
        # 不吞异常：此处失败=整个 GDB 不可读（缺依赖/文件损坏），
        # 吞掉会让上层报「未找到分类图层」且 skipped 为空，无法定位
        raise ValueError(f"无法读取 GDB 图层（依赖缺失或文件损坏）: {e}") from e
    for l in layers:
        cat = classify_layer(l["name"])
        if cat:
            classified.append({
                "name": l["name"],
                "category": cat,
                "row_count": l.get("row_count", 0),
            })
        else:
            skipped.append({"name": l["name"], "reason": "非分类图层"})
    return {"classified": classified, "skipped": skipped}


# zip 解压防护上限（防 zip 炸弹：200MB 上传限制只约束压缩后大小）
_ZIP_MAX_MEMBERS = 10000                    # 成员数上限（正常 .gdb 约数十个文件）
_ZIP_MAX_TOTAL_SIZE = 2 * 1024 * 1024 * 1024  # 解压后总大小上限 2 GiB


def _fix_zip_name(info):
    """Windows 资源管理器压的中文 zip 文件名按 GBK 编码，zipfile 默认按
    cp437 解码会乱码；此处回退 GBK 重解码（UTF-8 标记位已置的不动）。"""
    name = info.filename
    if info.flag_bits & 0x800:  # UTF-8 文件名标记
        return name
    try:
        return name.encode("cp437").decode("gbk")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def _safe_extract(zf, dest):
    """带防护的解压：成员数/解压总量上限 + 路径穿越显式拦截 + GBK 文件名回退。"""
    infos = zf.infolist()
    if len(infos) > _ZIP_MAX_MEMBERS:
        raise ValueError(f"压缩包文件数过多（{len(infos)} > {_ZIP_MAX_MEMBERS}）")
    total = sum(i.file_size for i in infos)
    limit_mb = _ZIP_MAX_TOTAL_SIZE // 1024 // 1024
    if total > _ZIP_MAX_TOTAL_SIZE:
        raise ValueError(f"压缩包解压后过大（{total // 1024 // 1024}MB > {limit_mb}MB）")
    dest = Path(dest).resolve()
    for info in infos:
        name = _fix_zip_name(info)
        target = (dest / name).resolve()
        if target != dest and str(dest) + os.sep not in str(target) + os.sep:
            raise ValueError(f"压缩包含非法路径: {name}")
        info.filename = name  # 让 extract 使用修正后的文件名
        zf.extract(info, str(dest))


def save_gdb_upload(upload_file, gid):
    """保存上传的 GDB 文件到存储目录。

    GDB 以 zip 包上传，解压后在一二层目录内查找 .gdb 目录。

    Returns:
        {"gid", "path", "gdb_dir"}
    """
    gdb_dir = GDB_STORAGE / gid
    gdb_dir.mkdir(parents=True, exist_ok=True)

    # .gdb 是目录，无法直接 save，需走 zip
    if hasattr(upload_file, "filename") and upload_file.filename.lower().endswith(".gdb"):
        raise ValueError("GDB 目录无法直接上传，请压缩为 .zip 后上传")

    # 其余一律当 zip 处理（不限扩展名，坏包报可读错误）
    zip_path = gdb_dir / "upload.zip"
    upload_file.save(str(zip_path))
    try:
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            _safe_extract(zf, gdb_dir)
    except zipfile.BadZipFile:
        zip_path.unlink()
        raise ValueError("仅支持 .zip 格式的 GDB 压缩包")
    zip_path.unlink()

    # 在一二层目录内找 .gdb 目录
    gdb_paths = find_gdb_dirs(gdb_dir)
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
    gdb_paths = find_gdb_dirs(gdb_dir)
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
