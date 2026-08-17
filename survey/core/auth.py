# -*- coding: utf-8 -*-
"""共享认证层 — 复用 forest-data 的 session（共享 secret_key + forest_web.db）。

Admin 端和 User 端共用此模块，确保认证逻辑一致。
"""
import hashlib
import os
import secrets
import sqlite3
from functools import wraps
from pathlib import Path

from flask import request, jsonify, session, redirect

# ── 认证配置：查找 forest-data 的 secret_key 和 DB ──
_SECRET_KEY_CANDIDATES = [
    Path("/home/www/bibook_deploy/apps/forest-data/web/.secret_key"),
    Path("/Users/sux/Desktop/forest-data/web/.secret_key"),
]
_FOREST_DB_CANDIDATES = [
    Path("/home/www/bibook_deploy/apps/forest-data/web/forest_web.db"),
    Path("/Users/sux/Desktop/forest-data/web/forest_web.db"),
]
LOGIN_URL = "/forest/login"


def find_secret_key():
    """查找 forest-data 的 secret_key 文件。"""
    for p in _SECRET_KEY_CANDIDATES:
        if p.exists():
            return p.read_text().strip()
    # 本地开发 fallback
    local = Path(__file__).resolve().parent.parent / ".secret_key"
    if local.exists():
        return local.read_text().strip()
    key = secrets.token_hex(32)
    local.write_text(key)
    try:
        local.chmod(0o600)
    except OSError:
        pass
    return key


def find_forest_db():
    """查找 forest-data 的 DB 路径。"""
    for p in _FOREST_DB_CANDIDATES:
        if p.exists():
            return str(p)
    return None


def forest_connect(forest_db):
    """连接 forest_web.db。"""
    if not forest_db:
        return None
    conn = sqlite3.connect(forest_db, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def current_user(forest_db, local_dev=False, local_user="本地测试员"):
    """从 session 获取当前登录用户。

    Args:
        forest_db: forest_web.db 路径（可为 None）
        local_dev: 本地开发模式（直接返回管理员）
        local_user: 本地开发模式的用户名

    Returns:
        用户 dict 或 None
    """
    if local_dev:
        return {
            "id": 1,
            "username": local_user,
            "display_name": local_user,
            "is_admin": 1,
            "is_active": 1,
        }
    uid = session.get("user_id")
    if not uid:
        return None
    conn = forest_connect(forest_db)
    if not conn:
        return {
            "id": uid,
            "username": session.get("username", ""),
            "display_name": session.get("username", ""),
            "is_admin": 0,
            "is_active": 1,
        }
    try:
        r = conn.execute(
            "SELECT id, username, display_name, is_admin, is_active FROM users WHERE id=? AND is_active=1",
            (uid,),
        ).fetchone()
        return dict(r) if r else None
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def login_required(forest_db, local_dev=False, local_user="本地测试员"):
    """登录校验装饰器工厂。

    用法:
        @login_required(FOREST_DB, LOCAL_DEV)
        def my_view():
            ...
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            u = current_user(forest_db, local_dev, local_user)
            if not u:
                if request.path.startswith("/api/"):
                    return jsonify({"error": "未登录", "login_url": LOGIN_URL + "?next=" + request.script_root + "/"}), 401
                return redirect(LOGIN_URL + "?next=" + request.script_root + "/")
            return f(*args, **kwargs)
        return wrapper
    return decorator


def admin_required(forest_db, local_dev=False, local_user="本地测试员"):
    """管理员校验装饰器工厂。"""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            u = current_user(forest_db, local_dev, local_user)
            if not u:
                if request.path.startswith("/api/"):
                    return jsonify({"error": "未登录", "login_url": LOGIN_URL + "?next=" + request.script_root + "/"}), 401
                return redirect(LOGIN_URL + "?next=" + request.script_root + "/")
            if not u.get("is_admin"):
                return jsonify({"error": "需要管理员权限"}), 403
            return f(*args, **kwargs)
        return wrapper
    return decorator
