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

    # 注意：必须关闭 reloader。watchdog 会监视 data/ 目录，上传 GDB 时
    # 写入的 upload.zip 触发自动重启，导致请求中途被掐断（连接 reset）→
    # 浏览器表现为「上传失败」。保留 debug 以输出错误栈，但不自动重载。
    root.run(host="0.0.0.0", port=port, debug=True, use_reloader=False)


if __name__ == "__main__":
    main()
