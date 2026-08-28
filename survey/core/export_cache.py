"""导出缓存层（2026-08-25，CONSTRAINTS D25）：样地/轨迹 zip 预生成缓存。

目的：管理后台下载样地/轨迹 zip 时跳过同步生成（openpyxl 逐小班开模板、
geopandas 逐小班写 SHP 都慢），缓存命中直接 send_file 磁盘文件。

设计要点：
- 缓存粒度 (project_id, category, kind)，kind ∈ {"samples", "tracks"}；
  项目级下载 = 各分类缓存 zip 合并（纯 zip I/O，毫秒级，不再缓存）。
- 目录 {项目根}/data/export_cache/{pid}/：
    {kind}-{cat}.zip   缓存文件本体
    {kind}-{cat}.json  manifest（数据指纹 + 生成时间 + stats）
  data/ 已在 .gitignore；deploy.sh rsync 不带 --delete，服务器缓存不受部署影响。
- 数据指纹 fingerprint = SHA1(records 条数|records max updated_at|
  extras 条数|extras max updated_at|小班行数)——覆盖调查记录增改、
  打卡/轨迹/照片（extras）、GDB 重新导入（行数变化）。不依赖文件 mtime。
- 并发安全：写 .tmp.{pid}.{uuid} 临时文件后 os.replace 原子替换；先替换
  zip 再替换 manifest——读到旧 manifest 时指纹不匹配只会触发重新生成，
  不会读错内容（gunicorn 多 worker 同写内容相同，后写覆盖无害）。
- 下载路径 cached_or_generate()：命中缓存返回磁盘路径；未命中同步生成
  （现行为）并回写缓存——缓存只加速，不影响正确性；ValueError（暂无
  数据）原样上抛，不缓存错误。
- prefetch()（systemd timer 每 10 分钟）：指纹一致跳过；数据最近
  quiet_minutes 内有更新（用户正在录入）也跳过，避免反复作废重算；
  生成时发现「暂无数据」（如轨迹全删）则清除过期缓存。
- 项目删除时 cleanup_project() 清理整个缓存目录（admin 端点调用）。

CLI：python -m survey.core.export_cache  → 跑一次 prefetch（timer 用）。
"""
import hashlib
import io
import json
import os
import shutil
import sys
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from survey.core import exporter
from survey.core import storage

KINDS = ("samples", "tracks")
_ROOT = Path(__file__).resolve().parent.parent.parent


def cache_root():
    return _ROOT / "data" / "export_cache"


def _manifest_path(pid, category, kind):
    return cache_root() / pid / f"{kind}-{category}.json"


def _zip_path(pid, category, kind):
    return cache_root() / pid / f"{kind}-{category}.zip"


# ---------------------------------------------------------------- 指纹

def _proj_stats(pid, category):
    """(pid, cat) 相关数据快照：records / extras / 小班行的条数与最新更新时间。

    小班行来源与 storage.list_project_subcompartment_rows 同源
    （GDB 导入 gdb_id + 旧批次导入 batch_id 两种）。
    """
    table_id = GDB_TABLE(category)
    conn = storage._connect()
    try:
        rec = conn.execute(
            "SELECT COUNT(*), COALESCE(MAX(updated_at),''), COALESCE(SUM(version),0) "
            "FROM records WHERE project_id=? AND table_id=?", (pid, table_id)).fetchone()
        ex = conn.execute(
            "SELECT COUNT(*), COALESCE(MAX(e.updated_at),'') FROM subcompartment_extras e "
            "JOIN subcompartment_rows r ON e.subcompartment_row_id=r.id "
            "LEFT JOIN gdb_files g ON r.gdb_id=g.id "
            "LEFT JOIN subcompartment_batches b ON r.batch_id=b.id "
            "WHERE (g.project_id=? OR b.project_id=?) AND r.category=?",
            (pid, pid, category)).fetchone()
        sc = conn.execute(
            "SELECT COUNT(*) FROM subcompartment_rows r "
            "LEFT JOIN gdb_files g ON r.gdb_id=g.id "
            "LEFT JOIN subcompartment_batches b ON r.batch_id=b.id "
            "WHERE (g.project_id=? OR b.project_id=?) AND r.category=?",
            (pid, pid, category)).fetchone()
        return {"rec_n": rec[0], "rec_max": rec[1], "rec_ver": rec[2],
                "ex_n": ex[0], "ex_max": ex[1], "sc_n": sc[0]}
    finally:
        conn.close()


def GDB_TABLE(category):
    from survey.core import gdb as GDB
    return GDB.GDB_CATEGORY_TO_TABLE.get(category or "")


def fingerprint(pid, category):
    s = _proj_stats(pid, category)
    raw = "|".join(str(s[k]) for k in
                   ("rec_n", "rec_max", "rec_ver", "ex_n", "ex_max", "sc_n"))
    return hashlib.sha1(raw.encode()).hexdigest()


def last_touched(pid, category):
    """数据最近更新时间（records/extras 两侧取最大，供 prefetch 静默判断）。"""
    s = _proj_stats(pid, category)
    return max(s["rec_max"], s["ex_max"]) if (s["rec_max"] or s["ex_max"]) else ""


def project_categories(pid):
    """项目实际含有的分类（去重，与 admin /categories 同源，含 GDB+批次两种来源）。"""
    conn = storage._connect()
    try:
        rows = conn.execute(
            "SELECT DISTINCT r.category FROM subcompartment_rows r "
            "LEFT JOIN gdb_files g ON r.gdb_id=g.id "
            "LEFT JOIN subcompartment_batches b ON r.batch_id=b.id "
            "WHERE (g.project_id=? OR b.project_id=?) AND r.category != '' "
            "ORDER BY r.category", (pid, pid)).fetchall()
        return [r[0] for r in rows if r[0]]
    finally:
        conn.close()


# ---------------------------------------------------------------- 读写缓存

def _atomic_write(path, data):
    """临时文件 + os.replace 原子写（多 worker 并发安全）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f"{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}"
    tmp.write_bytes(data)
    os.replace(tmp, path)


def load_cached(pid, category, kind):
    """缓存命中（指纹一致且 zip 存在）→ (zip路径, stats)；否则 None。"""
    mp = _manifest_path(pid, category, kind)
    zp = _zip_path(pid, category, kind)
    if not (mp.exists() and zp.exists()):
        return None
    try:
        m = json.loads(mp.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    if m.get("fingerprint") != fingerprint(pid, category):
        return None
    return zp, m.get("stats") or {}


def store(pid, category, kind, buf, stats):
    """写缓存（zip 先、manifest 后——见模块 docstring 并发说明）。"""
    _atomic_write(_zip_path(pid, category, kind), buf.getvalue())
    manifest = {"fingerprint": fingerprint(pid, category),
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "stats": stats or {}}
    _atomic_write(_manifest_path(pid, category, kind),
                  json.dumps(manifest, ensure_ascii=False).encode("utf-8"))


def _drop_cache(pid, category, kind):
    _zip_path(pid, category, kind).unlink(missing_ok=True)
    _manifest_path(pid, category, kind).unlink(missing_ok=True)


def cached_or_generate(pid, category, kind, inspect_date=None):
    """下载路径：命中缓存返回 (磁盘zip路径, stats)；未命中同步生成并回写。

    inspect_date（2026-08-26 验收日期过滤）：绕过缓存直接生成，返回
    (BytesIO, stats) 不落盘（临时过滤结果，命中全量缓存无意义）。
    ValueError（该分类暂无数据）原样上抛，不缓存。
    """
    if inspect_date:
        if kind != "samples":
            raise ValueError("轨迹导出不支持验收日期过滤")
        return exporter.export_samples_zip(pid, category=category,
                                           inspect_date=inspect_date)
    hit = load_cached(pid, category, kind)
    if hit:
        return hit
    fn = exporter.export_samples_zip if kind == "samples" else exporter.export_tracks_zip
    buf, stats = fn(pid, category=category)
    store(pid, category, kind, buf, stats)
    return _zip_path(pid, category, kind), stats


def cached_or_generate_project(pid, kind, inspect_date=None):
    """项目级下载：各分类缓存 zip 合并为一个 zip（BytesIO，已在位置 0）。

    单分类生成失败（暂无样地/轨迹数据）跳过该分类；全部分类无数据时
    ValueError 与单分类口径一致。inspect_date 过滤时各分类均绕过缓存。
    """
    n = 0
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for cat in project_categories(pid):
            try:
                zp, _stats = cached_or_generate(pid, cat, kind, inspect_date)
            except ValueError:
                continue
            # zp 为磁盘路径（缓存命中）或 BytesIO（过滤直生成），ZipFile 均可
            with zipfile.ZipFile(zp) as zf:
                for item in zf.infolist():
                    out.writestr(item, zf.read(item.filename))
            n += 1
    if n == 0:
        raise ValueError("该项目暂无样地数据" if kind == "samples" else "该项目暂无轨迹数据")
    buf.seek(0)
    return buf


def cleanup_project(pid):
    """项目删除时清理其全部缓存。"""
    shutil.rmtree(cache_root() / pid, ignore_errors=True)


# ---------------------------------------------------------------- 预生成

def _parse_ts(s):
    """storage 的 updated_at 为 datetime.now().isoformat(timespec='seconds')。"""
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def prefetch(quiet_minutes=10):
    """定时预生成（systemd timer 每 10 分钟调用一次）。

    跳过规则：
    1. 指纹一致（缓存已是最新）；
    2. 数据最近 quiet_minutes 内有更新——用户正在录入，生成了也马上作废；
    3. 该分类无数据 → 清除过期缓存（轨迹全删后不残留旧文件）。

    Returns:
        dict 摘要（generated/skipped_fresh/skipped_quiet/emptied 计数）
    """
    summary = {"generated": 0, "skipped_fresh": 0, "skipped_quiet": 0, "emptied": 0}
    for proj in storage.list_projects():
        pid = proj["id"]
        for cat in project_categories(pid):
            fp = fingerprint(pid, cat)
            touched = _parse_ts(last_touched(pid, cat))
            quiet_cut = datetime.now().timestamp() - quiet_minutes * 60
            for kind in KINDS:
                hit = load_cached(pid, cat, kind)
                if hit:
                    summary["skipped_fresh"] += 1
                    continue
                if touched and touched.timestamp() > quiet_cut:
                    summary["skipped_quiet"] += 1
                    continue
                try:
                    cached_or_generate(pid, cat, kind)
                    summary["generated"] += 1
                except ValueError:
                    _drop_cache(pid, cat, kind)  # 暂无数据：清除过期缓存
                    summary["emptied"] += 1
    return summary


if __name__ == "__main__":
    quiet = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    s = prefetch(quiet_minutes=quiet)
    print(f"[prefetch] generated={s['generated']} fresh={s['skipped_fresh']} "
          f"quiet={s['skipped_quiet']} emptied={s['emptied']}")
