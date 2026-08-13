# -*- coding: utf-8 -*-
"""林业野外调查即时录入系统。

架构总览（Admin/User 分离 + GDB 数据源）:

    core/                    ← 共享核心层
      schema.py              唯一真相源：5 张表的字段定义
      storage.py             SQLite 数据层（项目/小班/记录/扩展）
      exporter.py            xlsx 导出（按 schema 驱动）
      gdb.py                 GDB 解析（读图层/坐标转换/GeoJSON）
      auth.py                共享认证（forest-data session）
        │
        ├── admin/app.py     管理端（GDB上传/用户管理/项目管理，不打包）
        ├── user/app.py      用户端（调查/打卡/导出，打包为 app）
        └── deploy/gateway/  聚合挂载 /admin/ + /survey/
"""
