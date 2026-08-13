#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gateway 根 app：统一首页（公共菜单 / 登录提示）。

行为：
  未登录 → 登录提示卡片 + 公共应用预览
  已登录 → 可见应用网格（按用户授权过滤，无权限的标红/灰显）

鉴权复用 forest-data：登录跳转 /forest/login，cookie 同域共享（同一 secret_key）。
"""
import os
import sqlite3
import pathlib
import yaml
from datetime import datetime

from flask import (
    Flask, render_template, jsonify, redirect, url_for, session, request
)
from urllib.parse import unquote

HERE = pathlib.Path(__file__).resolve().parent
CFG = yaml.safe_load(open(HERE / "config.yaml"))
FOREST_DB = pathlib.Path(CFG["forest_db"])
SECRET_KEY_FILE = pathlib.Path(CFG["secret_key_file"])
LOGIN_URL = "/forest/login"   # 复用 forest-data 的登录页


def create_home_app(registry):
    app = Flask(
        __name__,
        template_folder=str(HERE / "templates"),
        static_folder=str(HERE / "static"),
    )
    # 共享 forest-data 的 secret_key → 同域同 cookie 共享 session
    app.secret_key = SECRET_KEY_FILE.read_text().strip() if SECRET_KEY_FILE.exists() else os.urandom(32)
    app.config["DATABASE"] = str(FOREST_DB)

    def _db():
        conn = sqlite3.connect(FOREST_DB)
        conn.row_factory = sqlite3.Row
        return conn

    def _current_user():
        uid = session.get("user_id")   # 与 forest-data auth.py 的 session key 一致
        if not uid:
            return None
        db = _db()
        try:
            r = db.execute(
                "SELECT id,username,display_name,is_admin,is_active,expires_at FROM users WHERE id=? AND is_active=1", (uid,)
            ).fetchone()
        except sqlite3.OperationalError:
            r = None
        db.close()
        return dict(r) if r else None

    def _check_app_access(user_id, app_code):
        """校验用户是否有指定程序的访问权限（含有效期 + 账号过期）。
        逻辑与 auth.py check_app_access 一致。"""
        db = _db()
        try:
            u = db.execute(
                "SELECT is_admin, expires_at FROM users WHERE id=? AND is_active=1",
                (user_id,)
            ).fetchone()
            if not u:
                return False
            # 账号过期检查
            if u["expires_at"]:
                try:
                    exp_date = u["expires_at"][:10]
                    if exp_date < datetime.now().strftime("%Y-%m-%d"):
                        return False
                except Exception:
                    pass
            # 管理员直接放行
            if u["is_admin"]:
                return True
            # 普通用户：查授权
            today = datetime.now().strftime("%Y-%m-%d")
            g = db.execute(
                """SELECT g.id FROM user_app_grants g
                   JOIN apps a ON g.app_id = a.id
                   WHERE g.user_id = ?
                     AND a.code = ?
                     AND a.is_active = 1
                     AND (g.valid_from IS NULL OR g.valid_from <= ?)
                     AND (g.valid_until IS NULL OR g.valid_until >= ?)""",
                (user_id, app_code, today, today)
            ).fetchone()
            return g is not None
        except sqlite3.OperationalError:
            return False
        finally:
            db.close()

    def _list_apps(user=None):
        """合并 appspec（enabled）与 apps 表（is_active），过滤 admin。

        每个应用附带 has_access 字段（需传入 user）。
        """
        apps = []
        seen = set()
        # 1. apps 表（forest-data 等，有 auth 系统的项目）
        db = _db()
        try:
            rows = db.execute(
                "SELECT code,name,base_path,description FROM apps WHERE is_active=1 AND code != 'admin' ORDER BY id"
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        db.close()
        for r in rows:
            d = dict(r)
            if d["code"] == "xzstock":
                continue
            if user:
                d["has_access"] = _check_app_access(user["id"], d["code"])
            else:
                d["has_access"] = False
            apps.append(d)
            seen.add(d["code"])
        # 2. appspec（x2sum 等无 auth 系统的，以及 apps 表未收录的）
        for prefix, spec in registry.specs.items():
            code = spec.get("code", "")
            if code in seen or code == "admin":
                continue
            apps.append({
                "code": code,
                "name": spec.get("name", code),
                "base_path": prefix,
                "description": spec.get("description", ""),
                "has_access": True,  # 无 auth 系统的应用默认可访问
            })
            seen.add(code)
        return apps

    def _match_requested_app(next_url, apps):
        """从 next_url 匹配对应的应用 code。
        next_url 形如 /survey/ 或 /forest/upload，按 base_path 前缀匹配。"""
        if not next_url:
            return None
        next_url = unquote(next_url)
        # 精确匹配 base_path 或前缀匹配
        for a in apps:
            bp = a["base_path"]
            if next_url == bp or next_url.startswith(bp + "/") or next_url.startswith(bp + "?"):
                return a["code"]
        return None

    @app.route("/")
    def home():
        u = _current_user()
        apps = _list_apps(user=u)
        # 读取 next 参数（登录页传来的目标 URL）
        next_url = request.args.get("next", "")
        requested_code = _match_requested_app(next_url, apps)
        return render_template("home.html", logged_in=bool(u), user=u, apps=apps,
                               login_url=LOGIN_URL, requested_code=requested_code)

    @app.route("/api/me/apps")
    def me_apps():
        u = _current_user()
        if not u:
            return jsonify([])
        return jsonify(_list_apps(user=u))

    @app.route("/api/public/apps")
    def public_apps():
        return jsonify([])

    return app
