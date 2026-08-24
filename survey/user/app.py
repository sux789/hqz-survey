# -*- coding: utf-8 -*-
"""User 端 Flask app — 精简版，只保留外业调查所需功能。

挂载前缀: /survey/
打包为 Android app（Capacitor 套壳）。

路由总览:
  GET  /survey/                          用户首页（登录后）
  GET  /survey/login                     登录页（后续做，当前测试环境隔离）
  GET  /survey/api/me                    当前用户
  GET  /survey/api/schema                表定义
  GET  /survey/api/schema/extras         扩展字段定义
  GET  /survey/api/projects              我的项目（只看自己）
  GET  /survey/api/projects/<pid>        项目详情
  GET  /survey/api/projects/<pid>/subcompartments  小班列表（支持 ?category= 过滤）
  GET  /survey/api/projects/<pid>/categories       项目含有的分类清单
  GET  /survey/api/projects/<pid>/geojson         小班面 GeoJSON
  GET  /survey/api/subcompartments/rows/<row_id>   小班详情
  GET  /survey/api/projects/<pid>/survey/<tid>/rows        网格调查行（每小班一行）
  PUT  /survey/api/projects/<pid>/survey/<tid>/rows        upsert 小班调查行
  POST /survey/api/subcompartments/rows/<row_id>/checkin  打卡
  POST /survey/api/subcompartments/rows/<row_id>/track    轨迹
  POST /survey/api/subcompartments/rows/<row_id>/photos   照片
  GET  /survey/api/projects/<pid>/export_base     导出基本信息 xlsx（?cat=分类 仅该分类）
  GET  /survey/api/projects/<pid>/export_samples  导出样地 xlsx（?sc=小班id 仅该小班）
"""
import os
import sys
import json
import gzip as _gzip_mod
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from flask import Flask, request, jsonify, render_template, send_file, redirect

from survey.core import schema as S
from survey.core import storage
from survey.core import exporter
from survey.core import gdb as GDB
from survey.core import auth as A

# ── 配置 ──
LOCAL_DEV = os.environ.get("SURVEY_LOCAL_DEV") == "1"
LOCAL_USER = os.environ.get("SURVEY_LOCAL_USER", "本地测试员")
FOREST_DB = A.find_forest_db()

storage.init_db()

app = Flask(__name__)
app.secret_key = A.find_secret_key()
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024
app.config['FOREST_DB'] = FOREST_DB

_login_required = A.login_required(FOREST_DB, LOCAL_DEV, LOCAL_USER)


@app.after_request
def _api_no_store(resp):
    """API 响应禁用 HTTP 缓存：防止 APP WebView 缓存 api/me 等，
    换账号重新登录后仍显示/使用前一个用户的数据。"""
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp


@app.after_request
def _purge_prefixed_session_cookie(resp):
    """清除历史遗留的 Path=/survey 同名 session cookie（修复前由本应用写出）：
    它在登出后仍残留，且按"最长路径优先"发送规则遮蔽 Path=/ 的新登录会话。
    每次响应都下发删除指令，老设备首次访问即自愈。"""
    prefix = app.config.get("APPLICATION_ROOT")
    if prefix:
        resp.headers.add(
            "Set-Cookie",
            f"session=; Path={prefix}; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Max-Age=0",
        )
    return resp


# 可压缩类型：文本类静态资源与 JSON（xlsx/zip/图片等二进制不压）
_GZIP_TYPES = ("text/", "application/javascript", "application/x-javascript",
               "application/json", "image/svg+xml")


@app.after_request
def _gzip_response(resp):
    """JS/CSS/大 JSON 自动 gzip（App WebView 弱网提速，110K JS ≈ 25K）。
    条件：客户端声明支持 gzip + 可压缩类型 + 200 + 体积 ≥ 1KB + 未带编码。"""
    if resp.headers.get("Content-Encoding"):
        return resp
    if "gzip" not in (request.headers.get("Accept-Encoding") or ""):
        return resp
    if resp.status_code != 200:
        return resp
    ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if not (ctype.startswith("text/") or ctype in _GZIP_TYPES):
        return resp
    try:
        data = resp.get_data()
    except Exception:
        return resp
    if len(data) < 1024:
        return resp
    try:
        comp = _gzip_mod.compress(data, 6)
    except Exception:
        return resp
    if len(comp) >= len(data):
        return resp
    resp.set_data(comp)
    resp.headers["Content-Encoding"] = "gzip"
    resp.headers["Vary"] = "Accept-Encoding"
    # ETag 保留：下次 If-None-Match 命中仍可 304 零传输
    return resp


@app.errorhandler(413)
def _payload_too_large(_e):
    return jsonify({"error": "文件过大"}), 413


# ── 页面 ──

@app.route("/")
@_login_required
def index():
    """用户首页。"""
    u = A.current_user(FOREST_DB, LOCAL_DEV, LOCAL_USER)
    return render_template("index.html", current_user=u)


@app.route("/login")
def login():
    """登录页（后续做，当前跳转 forest-data 登录或本地直通）。"""
    if LOCAL_DEV:
        return redirect("/")
    return redirect(A.LOGIN_URL + "?next=" + request.script_root + "/")


# ── 用户信息 ──

@app.route("/api/me")
@_login_required
def api_me():
    u = A.current_user(FOREST_DB, LOCAL_DEV, LOCAL_USER)
    return jsonify({
        "id": u["id"],
        "username": u["username"],
        "display_name": u.get("display_name") or u["username"],
        "is_admin": bool(u.get("is_admin")),
    })


# ── Schema ──

@app.route("/api/schema")
@_login_required
def api_schema():
    """返回全部表定义，前端按此动态渲染表单。

    一对一模型：表无子表概念，table5 样方作为 sample_array 字段在 input_columns 内。
    """
    tables = S.get_all_tables()
    return jsonify({
        "tables": [
            {
                "id": t["id"],
                "name": t["name"],
                "sheet_name": t.get("sheet_name", ""),
                "description": t.get("description", ""),
                "data_rows": t.get("data_rows", 5),
                "prefilled_columns": t.get("prefilled_columns", []),
                "input_columns": t.get("input_columns", []),
                "field_groups": S.get_field_groups(t["id"]),
            }
            for t in tables
        ]
    })


@app.route("/api/schema/extras")
@_login_required
def api_schema_extras():
    """返回小班扩展字段定义。"""
    return jsonify({"fields": S.SUBCOMPARTMENT_EXTRA_FIELDS})


# ── 项目（登录后全部可见，不按成员限制）──

@app.route("/api/projects")
@_login_required
def api_projects():
    projects = storage.list_projects()
    return jsonify({"projects": projects})


@app.route("/api/projects/<pid>")
@_login_required
def api_project_one(pid):
    proj = storage.get_project(pid)
    if not proj:
        return jsonify({"error": "项目不存在"}), 404
    return jsonify(proj)


# ── 小班列表 + 地图 ──

@app.route("/api/projects/<pid>/subcompartments")
@_login_required
def api_project_subcompartments(pid):
    """小班列表（含 GDB 几何缓存，用于地图渲染）。

    支持按 project_name / category 过滤（调查两级选择用）。
    """
    project_name = request.args.get("project_name", "") or None
    category = request.args.get("category", "") or None
    with_geom = request.args.get("with_geom") == "1"
    rows = storage.list_project_subcompartment_rows(pid, project_name=project_name, category=category)
    # 精简返回 + prefilled 映射（供网格调查页黄色列直接取值）。
    # 默认不返回 geom_geojson（占响应 85%，前端列表/网格均不用，
    # 地图走 /geojson 端点、单班详情走 rows/<id>）；需要时 ?with_geom=1。
    light = []
    for r in rows:
        data = r.get("data") or {}
        item = {
            "id": r["id"],
            "township": r.get("township", ""),
            "village": r.get("village", ""),
            "forest_compartment": r.get("forest_compartment", ""),
            "subcompartment": r.get("subcompartment", ""),
            "subcompartment_label": r.get("subcompartment_label", ""),
            "tending_area": r.get("tending_area", 0),
            "project_name": r.get("project_name", ""),
            "category": r.get("category", ""),
            "city": data.get("州", data.get("乡镇", "")),
            "tree_species": data.get("优势树", data.get("优势树种", "")),
            "ownership": data.get("土地权", data.get("土地权属", "")),
            "forest_type": data.get("林种", ""),
            # prefilled 裁掉空值（保留数值 0，如林班号为 0）：前端 pf[key] 直取，
            # undefined 与 '' 渲染等价；0 == ''/None 均为 False 不会被裁
            "prefilled": {k: v for k, v in (S.map_subcompartment_to_prefilled(data) or {}).items() if v not in ("", None)},
        }
        if with_geom:
            item["geom_geojson"] = r.get("geom_geojson", "")
        light.append(item)
    return jsonify({"rows": light, "count": len(light)})


@app.route("/api/projects/<pid>/project_names")
@_login_required
def api_project_names(pid):
    """返回项目下所有项目名称及其分类清单（调查两级选择 / 导出用）。"""
    return jsonify({"project_names": storage.get_project_names(pid)})


@app.route("/api/projects/<pid>/categories")
@_login_required
def api_project_categories(pid):
    """返回项目含有的分类清单（从 subcompartment_rows.category 去重）。"""
    conn = storage._connect()
    try:
        rows = conn.execute(
            "SELECT DISTINCT category FROM subcompartment_rows "
            "WHERE (gdb_id IN (SELECT id FROM gdb_files WHERE project_id=?) "
            "  OR batch_id IN (SELECT id FROM subcompartment_batches WHERE project_id=?)) "
            "AND category != '' ORDER BY category",
            (pid, pid)
        ).fetchall()
        return jsonify({"categories": [r["category"] for r in rows]})
    finally:
        conn.close()


@app.route("/api/projects/<pid>/geojson")
@_login_required
def api_project_geojson(pid):
    """项目小班面 GeoJSON（地图渲染）。

    优先使用已物化到 DB 的小班面 geom_geojson（来自 GDB 导入，最可靠）；
    若没有再回退到 GDB 文件实时生成。
    """
    # 1) 优先：用 subcompartment_rows 中已存储的 geom_geojson 拼接
    rows = storage.list_project_subcompartment_rows(pid)
    features = []
    for r in rows:
        gj = (r.get("geom_geojson") or "").strip()
        if not gj:
            continue
        try:
            feat = json.loads(gj)  # 单 Feature
        except Exception:
            continue
        if not feat.get("geometry"):
            continue
        props = dict(r.get("data") or {})
        # 弹窗/匹配用的关键字段（用规范化后的值，确保与列表行一致）
        props["id"] = r.get("id")
        props["subcompartment_label"] = r.get("subcompartment_label", "")
        props["乡镇"] = r.get("township", "") or ""
        props["村"] = r.get("village", "") or ""
        props["林班"] = r.get("forest_compartment", "") or ""
        props["小班"] = r.get("subcompartment", "") or ""
        feat["properties"] = props
        features.append(feat)
    if features:
        return jsonify({"type": "FeatureCollection", "features": features})

    # 2) 回退：GDB 文件实时生成
    gdb_files = storage.list_gdb_files(pid)
    if not gdb_files:
        return jsonify({"type": "FeatureCollection", "features": []})

    gdb_rec = gdb_files[0]
    gid = gdb_rec["id"]
    gdb_dir = Path(GDB.GDB_STORAGE) / gid
    polygons_path = gdb_dir / "polygons.geojson"

    if polygons_path.exists():
        return jsonify(json.loads(polygons_path.read_text(encoding="utf-8")))

    gdb_path = GDB.get_gdb_path(gid)
    if gdb_path:
        try:
            result = GDB.generate_geojson_for_project(gdb_path)
            return jsonify(result["polygons"])
        except Exception:
            pass

    return jsonify({"type": "FeatureCollection", "features": []})


# ── 小班详情 ──

@app.route("/api/subcompartments/rows/<row_id>")
@_login_required
def api_subcompartment_detail(row_id):
    """小班详情（含完整字段 + prefilled + extras）。"""
    r = storage.get_subcompartment_row(row_id)
    if not r:
        return jsonify({"error": "小班不存在"}), 404
    extras = storage.get_extras(row_id)
    prefilled = S.map_subcompartment_to_prefilled(r["data"])
    return jsonify({
        "row": r,
        "prefilled": prefilled,
        "extras": extras,
    })


@app.route("/api/subcompartments/rows/<row_id>", methods=["PUT"])
@_login_required
def api_update_subcompartment(row_id):
    """更新小班属性字段（修改 data_json，不动 GDB 源）。

    Body: {"data": {字段名: 值, ...}}
    只更新提交的字段，其余保留。
    """
    if not storage.get_subcompartment_row(row_id):
        return jsonify({"error": "小班不存在"}), 404
    body = request.get_json(force=True) or {}
    data_updates = body.get("data", {})
    if not isinstance(data_updates, dict) or not data_updates:
        return jsonify({"error": "data 不能为空"}), 400
    updated = storage.update_subcompartment_row(row_id, data_updates)
    return jsonify({"row": updated})


# ── 调查记录（小班一对一：每小班每表一行 upsert）──

@app.route("/api/projects/<pid>/survey/<table_id>/rows")
@_login_required
def api_get_survey_rows(pid, table_id):
    """获取某表所有小班的调查行（网格模式）。

    支持 ?project_name= 过滤（仅返回该项目名称下的小班调查行）。
    返回：{rows: [{subcompartment_id, data, inspector, updated_at}, ...]}
    """
    project_name = request.args.get("project_name", "") or None
    return jsonify({"rows": storage.get_survey_rows(pid, table_id, project_name=project_name)})


@app.route("/api/projects/<pid>/survey/<table_id>/rows", methods=["PUT"])
@_login_required
def api_upsert_survey_row(pid, table_id):
    """upsert 一个小班的一行调查数据（网格单元格编辑触发）。

    Body: {"subcompartment_id": "...", "data": {字段: 值}, "inspector": "...",
           "base_version": N}
    base_version（乐观锁，可选）：读取记录时返回的 version；
      缺省=不检查（兼容旧客户端）；0=记录须不存在；>=1=版本匹配才写入。
    版本不匹配 → 409 + conflict（库内当前 data/version/inspector/updated_at），
    前端弹「已被他人修改」窗提供 合并/覆盖/加载最新。
    """
    body = request.get_json(force=True) or {}
    sc_id = body.get("subcompartment_id", "")
    data = body.get("data", {})
    if not sc_id:
        return jsonify({"error": "subcompartment_id 不能为空"}), 400
    # store:false 的 computed 派生字段（成活率等级/面积分派）不落库：
    # 前端随保存重算仅用于实时显示，导出由模板公式承担（防旧值残留覆盖公式）
    table_def = S.get_table(table_id)
    if table_def and isinstance(data, dict):
        no_store = {f["key"] for f in table_def.get("input_columns", [])
                    if f.get("type") == "computed" and f.get("store") is False}
        if no_store:
            data = {k: v for k, v in data.items() if k not in no_store}
    u = A.current_user(FOREST_DB, LOCAL_DEV, LOCAL_USER)
    inspector = body.get("inspector", "") or u["username"]
    bv = body.get("base_version")
    try:
        bv = None if bv is None else int(bv)
    except (TypeError, ValueError):
        bv = None
    try:
        rec = storage.upsert_survey_row(pid, table_id, sc_id, data, inspector,
                                        base_version=bv)
    except storage.RecordConflict as e:
        cur = e.record or {}
        return jsonify({
            "error": "该小班数据已被他人修改，请合并或覆盖后重试",
            "conflict": {
                "version": cur.get("version", 1),
                "data": cur.get("data", {}),
                "inspector": cur.get("inspector", ""),
                "updated_at": cur.get("updated_at", ""),
            },
        }), 409
    return jsonify(rec)


# ── 打卡 / 轨迹 / 照片 ──

@app.route("/api/subcompartments/rows/<row_id>/checkin", methods=["POST"])
@_login_required
def api_checkin(row_id):
    if not storage.get_subcompartment_row(row_id):
        return jsonify({"error": "小班不存在"}), 404
    data = request.get_json(force=True) if request.is_json else {}
    lng = data.get("lng", "")
    lat = data.get("lat", "")
    result = storage.save_checkin(row_id, lng, lat)
    return jsonify(result)


@app.route("/api/subcompartments/rows/<row_id>/track", methods=["POST"])
@_login_required
def api_save_track(row_id):
    if not storage.get_subcompartment_row(row_id):
        return jsonify({"error": "小班不存在"}), 404
    data = request.get_json(force=True)
    points = data.get("points", [])
    if not isinstance(points, list):
        return jsonify({"error": "points 必须是数组"}), 400
    result = storage.save_track(row_id, points)
    return jsonify(result)


@app.route("/api/subcompartments/rows/<row_id>/photos", methods=["POST"])
@_login_required
def api_save_photos(row_id):
    if not storage.get_subcompartment_row(row_id):
        return jsonify({"error": "小班不存在"}), 404
    data = request.get_json(force=True)
    photos = data.get("photos", [])
    if not isinstance(photos, list):
        return jsonify({"error": "photos 必须是数组"}), 400
    result = storage.save_photos(row_id, photos)
    return jsonify(result)


# ── 导出 ──
# 用户端：基本信息（按当前分类）+ 样地（单小班/整项目）。
# 轨迹导出仅管理后台（/survey-admin），用户端不提供下载入口。

@app.route("/api/projects/<pid>/export_base")
@_login_required
def api_export_base(pid):
    """基本信息导出：?cat=<分类> 仅导出该分类（topbar「导出」按钮）；缺省整项目 3 分类。"""
    proj = storage.get_project(pid)
    if not proj:
        return jsonify({"error": "项目不存在"}), 404
    cat = request.args.get("cat", "").strip()
    try:
        output, stats = exporter.export_base(pid, category=cat or None)
        filename = f"{proj['name']}_{cat}_基本信息.xlsx" if cat else f"{proj['name']}_基本信息.xlsx"
        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"导出失败: {e}"}), 500


@app.route("/api/projects/<pid>/export_samples")
@_login_required
def api_export_samples(pid):
    """样地页导出：?sc=<小班id> 仅导出该小班（sheet 名「分类-调查小班号」）；缺省整项目。"""
    proj = storage.get_project(pid)
    if not proj:
        return jsonify({"error": "项目不存在"}), 404
    sc_id = request.args.get("sc", "").strip()
    try:
        output, stats = exporter.export_samples(pid, subcompartment_id=sc_id or None)
        if sc_id:
            sc = storage.get_subcompartment_row(sc_id)
            cat = (sc or {}).get("category") or ""
            no = (sc or {}).get("subcompartment") or (sc or {}).get("subcompartment_label") or ""
            filename = f"{proj['name']}_{cat}_{no}_样地.xlsx"
        else:
            filename = f"{proj['name']}_样地.xlsx"
        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"导出失败: {e}"}), 500


# ── 健康检查 ──

@app.route("/healthz")
def healthz():
    return jsonify({
        "ok": True,
        "tables": len(S.get_all_tables()),
        "db_exists": (Path(__file__).resolve().parent.parent / "survey.db").exists(),
        "forest_db": FOREST_DB is not None,
        "auth": "session" if FOREST_DB else "fallback",
        "side": "user",
    })


def create_app(prefix='/survey'):
    """返回已配置的 app 实例。部署由 gateway 聚合。"""
    if prefix:
        app.config['APPLICATION_ROOT'] = prefix
        app.static_url_path = f"{prefix}/static"
    # session cookie 必须与 forest-data 登录写出的那条（Path=/）完全同一条：
    # Flask 默认 SESSION_COOKIE_PATH=APPLICATION_ROOT（/survey），本应用每次
    # 响应都会额外写出第二条同名 cookie（Path=/survey）。登出只清 Path=/ 的
    # 那条，残留的前缀 cookie 按浏览器"最长路径优先"规则遮蔽新登录会话，
    # 导致换账号后 api/me 仍读到前一个用户（顶栏 user-display 显示旧用户）。
    app.config['SESSION_COOKIE_PATH'] = '/'
    # 只读会话：不刷新不回写，避免把（可能被遗留 cookie 污染的）会话写回主 cookie
    app.config['SESSION_REFRESH_EACH_REQUEST'] = False
    return app


if __name__ == "__main__":
    PORT = 8090
    if len(sys.argv) > 1:
        try:
            PORT = int(sys.argv[1])
        except ValueError:
            pass
    print("=" * 60)
    print("User 调查端")
    print(f"访问地址: http://127.0.0.1:{PORT}/survey/")
    print(f"认证DB: {FOREST_DB or '不可用(fallback模式)'}")
    print("=" * 60)
    app.run(host="0.0.0.0", port=PORT, debug=True)
