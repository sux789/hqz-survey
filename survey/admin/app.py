# -*- coding: utf-8 -*-
"""Admin 端 Flask app — GDB 管理 + 用户/项目管理 + 预填数据 + 导出。

挂载前缀: /admin/
不打包为 app，纯 Web 后台。

路由总览:
  GET  /admin/                          管理后台首页
  GET  /admin/api/me                    当前管理员
  POST /admin/api/gdb/upload            上传 GDB（zip）
  GET  /admin/api/gdb                   GDB 文件列表
  GET  /admin/api/gdb/<gid>             GDB 详情
  GET  /admin/api/gdb/<gid>/layers      图层列表
  GET  /admin/api/gdb/<gid>/layers/<layer>  图层字段+预览
  POST /admin/api/gdb/<gid>/geojson     生成 GeoJSON
  DELETE /admin/api/gdb/<gid>           删除 GDB
  GET  /admin/api/users                 用户列表
  POST /admin/api/users                 创建用户
  PUT  /admin/api/users/<uid>           启用/禁用
  POST /admin/api/users/<uid>/password  重置密码
  GET  /admin/api/projects              项目列表
  DELETE /admin/api/projects/<pid>      删除项目（含关联 GDB/数据）
  GET/POST/DELETE /admin/api/projects/<pid>/members  成员管理
  GET/POST /admin/api/projects/<pid>/prefilled/<tid>  预填数据
  GET  /admin/api/projects/<pid>/export  导出 xlsx
"""
import hashlib
import os
import secrets
import sqlite3
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from flask import Flask, request, jsonify, render_template, send_file, session, redirect

from survey.core import schema as S
from survey.core import storage
from survey.core import exporter
from survey.core import gdb as GDB
from survey.core import auth as A

# ── 配置 ──
LOCAL_DEV = os.environ.get("SURVEY_LOCAL_DEV") == "1"
LOCAL_USER = os.environ.get("SURVEY_LOCAL_USER", "本地管理员")
FOREST_DB = A.find_forest_db()

storage.init_db()

app = Flask(__name__)
app.secret_key = A.find_secret_key()
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024  # GDB 可能较大
app.config['FOREST_DB'] = FOREST_DB

# 简化认证：用闭包绑定配置
_login_required = A.login_required(FOREST_DB, LOCAL_DEV, LOCAL_USER)
_admin_required = A.admin_required(FOREST_DB, LOCAL_DEV, LOCAL_USER)


@app.errorhandler(413)
def _payload_too_large(_e):
    return jsonify({"error": "文件过大"}), 413


# ── 页面 ──

@app.route("/")
@_admin_required
def index():
    """管理后台首页。"""
    u = A.current_user(FOREST_DB, LOCAL_DEV, LOCAL_USER)
    return render_template("index.html", current_user=u)


# ── 管理员信息 ──

@app.route("/api/me")
@_admin_required
def api_me():
    u = A.current_user(FOREST_DB, LOCAL_DEV, LOCAL_USER)
    return jsonify({
        "id": u["id"],
        "username": u["username"],
        "display_name": u.get("display_name") or u["username"],
        "is_admin": bool(u.get("is_admin")),
    })


# ════════════════════════════════════════════
# GDB 上传 / 管理 / 解析
# ════════════════════════════════════════════

@app.route("/api/gdb/upload", methods=["POST"])
@_admin_required
def api_gdb_upload():
    """上传 GDB 文件（zip 格式）。

    Body: multipart/form-data
      - file: zip 文件
      - project_id: 关联项目 ID
    """
    u = A.current_user(FOREST_DB, LOCAL_DEV, LOCAL_USER)

    if "file" not in request.files:
        return jsonify({"error": "请选择 zip 文件"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "请选择文件"}), 400

    pid = request.form.get("project_id", "")
    if not pid:
        return jsonify({"error": "project_id 不能为空"}), 400

    proj = storage.get_project(pid)
    if not proj:
        return jsonify({"error": "项目不存在"}), 404

    # 读取内容计算 SHA256
    content = f.read()
    file_hash = hashlib.sha256(content).hexdigest()

    # 查重
    existing = storage.find_gdb_by_hash(file_hash)
    if existing:
        return jsonify({
            "error": "该 GDB 文件已上传过",
            "existing_gid": existing["id"],
            "uploaded_at": existing["uploaded_at"],
        }), 409

    # 保存文件
    gid = uuid.uuid4().hex[:12]
    f.seek(0)
    try:
        result = GDB.save_gdb_upload(f, pid, gid)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"解压失败: {e}"}), 400

    # 读取图层信息
    try:
        layers = GDB.list_layers(result["path"])
    except Exception as e:
        GDB.delete_gdb_files(gid)
        return jsonify({"error": f"读取 GDB 图层失败: {e}"}), 400

    # 写入数据库
    gdb_rec = storage.create_gdb_file(
        project_id=pid,
        file_name=f.filename,
        file_hash=file_hash,
        layers=layers,
        uploaded_by=u["username"],
    )

    # 物化导入：将「抚育区」图层小班写入 subcompartment_rows
    import_result = {"imported": 0, "skipped": 0}
    layer_names = [l["name"] for l in layers]
    target_layer = "抚育区" if "抚育区" in layer_names else (layer_names[0] if layer_names else "")
    if target_layer:
        try:
            import_result = storage.import_gdb_subcompartments(
                gid, pid, result["path"], layer=target_layer
            )
        except Exception as e:
            import_result = {"imported": 0, "skipped": 0, "error": str(e)}

    # 生成 md 快照（AI 高速路索引）
    try:
        md_dir = Path(result["gdb_dir"]) / "gdb2md"
        GDB.to_md(result["path"], str(md_dir))
    except Exception:
        pass  # md 生成失败不阻塞

    return jsonify({
        "gid": gid,
        "file_name": f.filename,
        "layers": layers,
        "imported": import_result,
        "uploaded_at": gdb_rec["uploaded_at"],
    }), 201


@app.route("/api/gdb")
@_admin_required
def api_gdb_list():
    """GDB 文件列表。"""
    pid = request.args.get("project_id", "")
    if pid:
        files = storage.list_gdb_files(pid)
    else:
        files = storage.list_gdb_files()
    return jsonify({"files": files})


@app.route("/api/gdb/<gid>")
@_admin_required
def api_gdb_detail(gid):
    """GDB 详情。"""
    rec = storage.get_gdb_file(gid)
    if not rec:
        return jsonify({"error": "GDB 不存在"}), 404
    return jsonify(rec)


@app.route("/api/gdb/<gid>/layers")
@_admin_required
def api_gdb_layers(gid):
    """图层列表。"""
    rec = storage.get_gdb_file(gid)
    if not rec:
        return jsonify({"error": "GDB 不存在"}), 404
    return jsonify({"layers": rec.get("layers", [])})


@app.route("/api/gdb/<gid>/layers/<layer>")
@_admin_required
def api_gdb_layer_preview(gid, layer):
    """图层字段 + 前N行预览。"""
    gdb_path = GDB.get_gdb_path(gid)
    if not gdb_path:
        return jsonify({"error": "GDB 文件不存在"}), 404
    try:
        fields = GDB.layer_fields(gdb_path, layer)
        gdf = GDB.read_layer(gdb_path, layer, max_features=10)
        gdf84 = GDB.to_wgs84(gdf)
        # 转成可序列化的行
        rows = []
        for _, row in gdf84.iterrows():
            r = {}
            for col in gdf84.columns:
                if col == "geometry":
                    r["_centroid"] = [round(row.geometry.centroid.x, 6), round(row.geometry.centroid.y, 6)]
                else:
                    v = row[col]
                    if v is None or (hasattr(v, "__float__") and v != v):
                        r[col] = ""
                    else:
                        r[col] = str(v)
            rows.append(r)
        return jsonify({"fields": fields, "rows": rows})
    except Exception as e:
        return jsonify({"error": f"读取图层失败: {e}"}), 400


@app.route("/api/gdb/<gid>/geojson", methods=["POST"])
@_admin_required
def api_gdb_generate_geojson(gid):
    """生成 GeoJSON（面 + 质心点）。"""
    rec = storage.get_gdb_file(gid)
    if not rec:
        return jsonify({"error": "GDB 不存在"}), 404

    data = request.get_json(force=True) if request.is_json else {}
    layer = data.get("layer", "抚育区")

    gdb_path = GDB.get_gdb_path(gid)
    if not gdb_path:
        return jsonify({"error": "GDB 文件不存在"}), 404

    try:
        result = GDB.generate_geojson_for_project(gdb_path, layer=layer)
        # 保存到 GDB 目录
        gdb_dir = Path(GDB.GDB_STORAGE) / gid
        with open(gdb_dir / "polygons.geojson", "w", encoding="utf-8") as f:
            json_dump = __import__("json").dumps(result["polygons"], ensure_ascii=False)
            f.write(json_dump)
        with open(gdb_dir / "centroids.geojson", "w", encoding="utf-8") as f:
            f.write(__import__("json").dumps(result["centroids"], ensure_ascii=False))
        return jsonify({
            "polygon_count": len(result["polygons"].get("features", [])),
            "centroid_count": len(result["centroids"].get("features", [])),
        })
    except Exception as e:
        return jsonify({"error": f"生成 GeoJSON 失败: {e}"}), 400


@app.route("/api/gdb/<gid>/geojson")
@_admin_required
def api_gdb_download_geojson(gid):
    """下载已生成的 GeoJSON。"""
    geojson_type = request.args.get("type", "polygons")
    gdb_dir = Path(GDB.GDB_STORAGE) / gid
    geojson_path = gdb_dir / f"{geojson_type}.geojson"
    if not geojson_path.exists():
        return jsonify({"error": "GeoJSON 尚未生成，请先调用 POST 生成"}), 404
    import io
    return send_file(
        io.BytesIO(geojson_path.read_bytes()),
        as_attachment=True,
        download_name=f"{gid}_{geojson_type}.geojson",
        mimetype="application/geo+json",
    )


@app.route("/api/gdb/<gid>", methods=["DELETE"])
@_admin_required
def api_gdb_delete(gid):
    """删除 GDB。"""
    rec = storage.get_gdb_file(gid)
    if not rec:
        return jsonify({"error": "GDB 不存在"}), 404
    GDB.delete_gdb_files(gid)
    storage.delete_gdb_file(gid)
    return jsonify({"ok": True})


# ════════════════════════════════════════════
# 用户管理
# ════════════════════════════════════════════

@app.route("/api/users")
@_admin_required
def api_users():
    """用户列表。"""
    conn = A.forest_connect(FOREST_DB)
    if not conn:
        return jsonify({"users": []})
    try:
        rows = conn.execute(
            "SELECT id, username, display_name, is_admin, is_active, created_at, last_login_at FROM users ORDER BY id"
        ).fetchall()
        return jsonify({"users": [dict(r) for r in rows]})
    finally:
        conn.close()


@app.route("/api/users", methods=["POST"])
@_admin_required
def api_create_user():
    """创建用户。"""
    data = request.get_json(force=True)
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    display_name = data.get("display_name", "").strip()
    is_admin = 1 if data.get("is_admin") else 0

    if not username or not password:
        return jsonify({"error": "用户名和密码不能为空"}), 400

    conn = A.forest_connect(FOREST_DB)
    if not conn:
        return jsonify({"error": "认证数据库不可用"}), 500
    try:
        existing = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if existing:
            return jsonify({"error": "用户名已存在"}), 400
        salt = secrets.token_hex(8)
        password_hash = hashlib.sha256((salt + password).encode()).hexdigest()
        conn.execute(
            "INSERT INTO users (username, password_hash, salt, display_name, is_admin, is_active, created_at) VALUES (?,?,?,?,?,?,datetime('now'))",
            (username, password_hash, salt, display_name, is_admin, 1),
        )
        app_row = conn.execute("SELECT id FROM apps WHERE code='survey'").fetchone()
        if app_row:
            new_user = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
            if new_user:
                conn.execute(
                    "INSERT OR IGNORE INTO user_app_grants (user_id, app_id) VALUES (?,?)",
                    (new_user["id"], app_row["id"]),
                )
        conn.commit()
        return jsonify({"ok": True, "username": username}), 201
    finally:
        conn.close()


@app.route("/api/users/<uid>", methods=["PUT"])
@_admin_required
def api_toggle_user(uid):
    """启用/禁用用户。"""
    data = request.get_json(force=True)
    is_active = 1 if data.get("is_active") else 0
    conn = A.forest_connect(FOREST_DB)
    if not conn:
        return jsonify({"error": "认证数据库不可用"}), 500
    try:
        conn.execute("UPDATE users SET is_active=? WHERE id=?", (is_active, uid))
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


@app.route("/api/users/<uid>/password", methods=["POST"])
@_admin_required
def api_reset_password(uid):
    """重置密码。"""
    data = request.get_json(force=True)
    password = data.get("password", "").strip()
    if not password:
        return jsonify({"error": "密码不能为空"}), 400
    conn = A.forest_connect(FOREST_DB)
    if not conn:
        return jsonify({"error": "认证数据库不可用"}), 500
    try:
        salt = secrets.token_hex(8)
        password_hash = hashlib.sha256((salt + password).encode()).hexdigest()
        conn.execute("UPDATE users SET password_hash=?, salt=? WHERE id=?", (password_hash, salt, uid))
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


# ════════════════════════════════════════════
# 项目管理
# ════════════════════════════════════════════

@app.route("/api/projects", methods=["GET", "POST"])
@_admin_required
def api_projects():
    u = A.current_user(FOREST_DB, LOCAL_DEV, LOCAL_USER)
    if request.method == "POST":
        data = request.get_json(force=True)
        name = data.get("name", "").strip()
        creator = u["username"]
        township = data.get("township", "").strip()
        if not name:
            return jsonify({"error": "项目名称不能为空"}), 400
        proj = storage.create_project(name, creator, township)
        storage.add_project_member(proj["id"], u["id"], u["username"])
        return jsonify(proj), 201
    else:
        projects = storage.list_projects()
        return jsonify({"projects": projects})


@app.route("/api/projects/<pid>")
@_admin_required
def api_project_one(pid):
    proj = storage.get_project(pid)
    if not proj:
        return jsonify({"error": "项目不存在"}), 404
    return jsonify(proj)


@app.route("/api/projects/<pid>", methods=["DELETE"])
@_admin_required
def api_project_delete(pid):
    """删除项目及其所有关联数据（批次/小班/录入记录/预填/成员）。

    同时清理该项目关联的 GDB 文件（数据库记录 + 磁盘文件），
    避免产生孤儿 GDB。删除后数据不可恢复。
    """
    proj = storage.get_project(pid)
    if not proj:
        return jsonify({"error": "项目不存在"}), 404
    # 清理关联 GDB 文件（磁盘 + 记录）
    try:
        gdb_files = storage.list_gdb_files(pid)
        for g in gdb_files:
            try:
                GDB.delete_gdb_files(g["id"])
            except Exception:
                pass
            try:
                storage.delete_gdb_file(g["id"])
            except Exception:
                pass
    except Exception:
        pass
    storage.delete_project(pid)
    return jsonify({"ok": True})


@app.route("/api/projects/<pid>/members")
@_admin_required
def api_members(pid):
    return jsonify({"members": storage.get_project_members(pid)})


@app.route("/api/projects/<pid>/members", methods=["POST"])
@_admin_required
def api_add_member(pid):
    data = request.get_json(force=True)
    user_id = data.get("user_id")
    username = data.get("username", "")
    if not user_id:
        return jsonify({"error": "user_id 不能为空"}), 400
    storage.add_project_member(pid, user_id, username)
    return jsonify({"ok": True}), 201


@app.route("/api/projects/<pid>/members/<uid>", methods=["DELETE"])
@_admin_required
def api_remove_member(pid, uid):
    storage.remove_project_member(pid, uid)
    return jsonify({"ok": True})


# ── 预填数据 ──

@app.route("/api/projects/<pid>/prefilled/<table_id>", methods=["GET", "POST"])
@_admin_required
def api_prefilled(pid, table_id):
    if request.method == "POST":
        data = request.get_json(force=True)
        subtable_id = data.get("subtable_id", "")
        row_index = data.get("row_index", 0)
        row_data = data.get("data", {})
        storage.save_prefilled(pid, table_id, subtable_id, row_index, row_data)
        return jsonify({"ok": True})
    else:
        return jsonify({"rows": storage.get_prefilled(pid, table_id)})


# ── 导出 ──

@app.route("/api/projects/<pid>/export")
@_admin_required
def api_export(pid):
    proj = storage.get_project(pid)
    if not proj:
        return jsonify({"error": "项目不存在"}), 404
    try:
        output, stats = exporter.export_project(pid)
        filename = f"{proj['name']}_验收数据.xlsx"
        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"导出失败: {e}"}), 500


# ── Schema（admin 也需要）──

@app.route("/api/schema")
@_admin_required
def api_schema():
    tables = S.get_all_tables()
    return jsonify({
        "tables": [
            {
                "id": t["id"],
                "name": t["name"],
                "sheet_name": t.get("sheet_name", ""),
                "description": t.get("description", ""),
                "data_rows": t.get("data_rows", 5),
                "has_subtables": "subtables" in t,
                "subtables": t.get("subtables", []),
                "prefilled_columns": t.get("prefilled_columns", []),
                "input_columns": t.get("input_columns", []),
                "field_groups": S.get_field_groups(t["id"]),
            }
            for t in tables
        ]
    })


@app.route("/api/schema/extras")
@_admin_required
def api_schema_extras():
    return jsonify({"fields": S.SUBCOMPARTMENT_EXTRA_FIELDS})


# ── 健康检查 ──

@app.route("/healthz")
def healthz():
    return jsonify({
        "ok": True,
        "tables": len(S.get_all_tables()),
        "db_exists": (Path(__file__).resolve().parent.parent / "survey.db").exists(),
        "forest_db": FOREST_DB is not None,
        "auth": "session" if FOREST_DB else "fallback",
        "side": "admin",
    })


def create_app(prefix='/admin'):
    """返回已配置的 app 实例。部署由 gateway 聚合。"""
    if prefix:
        app.config['APPLICATION_ROOT'] = prefix
        app.static_url_path = f"{prefix}/static"
    return app


if __name__ == "__main__":
    PORT = 8091
    if len(sys.argv) > 1:
        try:
            PORT = int(sys.argv[1])
        except ValueError:
            pass
    print("=" * 60)
    print("Admin 管理端")
    print(f"访问地址: http://127.0.0.1:{PORT}/admin/")
    print(f"认证DB: {FOREST_DB or '不可用(fallback模式)'}")
    print("=" * 60)
    app.run(host="0.0.0.0", port=PORT, debug=True)
