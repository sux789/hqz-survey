# -*- coding: utf-8 -*-
"""本地测试 launcher — 用 DispatcherMiddleware 同时挂载 admin + user。

用法:
    python -m survey.launcher          # 默认端口 8090
    python -m survey.launcher 8080     # 指定端口

部署环境（bibook_deploy gateway）不走此文件，由 gateway registry 聚合挂载。
"""
import os
import sys

# 本地开发标志必须先于 app import 设置（app 在 import 时读取环境变量，
# 否则 LOCAL_DEV=False 导致本地也要登录）
os.environ.setdefault("SURVEY_LOCAL_DEV", "1")
os.environ.setdefault("SURVEY_LOCAL_USER", "本地测试员")

from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.exceptions import NotFound

from survey.admin.app import app as admin_app
from survey.user.app import app as user_app

# admin 挂 /admin/，user 挂 /survey/，根路径给 404（生产由 gateway home 占位）
root = user_app  # user_app 作主体（持有根路径）
root.wsgi_app = DispatcherMiddleware(
    NotFound(),  # 根 / 由 gateway home 占位，本地测试直接 404
    {
        "/survey": user_app.wsgi_app,
        "/admin": admin_app.wsgi_app,
    },
)


def main():
    port = 8090
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass

    print("=" * 60)
    print("  林业野外调查系统 — 本地测试模式")
    print("=" * 60)
    print(f"  User 端 (调查/打卡):  http://127.0.0.1:{port}/survey/")
    print(f"  Admin 端 (GDB/管理):  http://127.0.0.1:{port}/admin/")
    print(f"  用户: {os.environ['SURVEY_LOCAL_USER']} (管理员)")
    print(f"  认证: 已禁用（本地开发模式）")
    print("=" * 60)

    # 热加载：stat 轮询只监视 .py/.pyc 文件，data/ 目录（GDB 上传的
    # upload.zip、survey.db 等）不会触发重启——此前默认 watchdog 递归监视
    # 整个目录，上传 GDB 时请求中途被重启掐断（浏览器表现为「上传失败」），
    # 所以才被迫关闭 reloader。exclude_patterns 再排除 data/ 与 db 作双保险。
    # JS/CSS 静态文件本地禁缓存：改动后浏览器普通刷新即生效，无需重启。
    for _app in (user_app, admin_app):
        _app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

    # 模板启动自检：缺失/结构异常立即醒目提示（不阻断启动，调查功能仍可用，
    # 但导出会以同样的精确错误失败）
    from survey.core import exporter
    try:
        exporter.check_templates()
    except RuntimeError as e:
        print(f"!!! {e}")

    root.run(
        host="0.0.0.0", port=port, debug=True,
        use_reloader=True, reloader_type="stat",
        exclude_patterns=["*/data/*", "*.db", "*.db-wal", "*.db-shm"],
    )


if __name__ == "__main__":
    main()
