# 001 Admin/User 端分离与 GDB 集成

> 迭代类型：架构重构
> 日期：2026-08-13
> 关联文档：[设计-Admin与User端分离.md](../docs/设计-Admin与User端分离.md)、[043方法论](/Users/sux/Desktop/forest-data/doc/043-新项目方法论落地指南_从0到1的积木高速公路.md)

## 1. 需求背景（用户原话）

> admin 端，上传gdb 和管理gdb，gdb 解析等放在 admin 不用 app.js，类似分开隔离。user 端只给登录界面、调查、打卡功能及导出。admin 端不要打包为 app，只有 user 端打包为 app。为了简化 app。

> gdb 和 excel 只能选取一个，放弃 excel。用 gdb 后增加版本管理，gitignore 放弃 data 目录。后续要有 AI 使用高速路或主线连接业务架构和代码架构及文档的离散目标。

## 2. 验证结果

### GDB 关键点验证（_verify_gdb.py）
- 图层：6 个（抚育区 1028×68、小班界、标准地、控制点、国有林场界、林班界）
- 坐标转换：EPSG:4507 → WGS84，质心 lng≈105.0-105.4, lat≈27.4-28.1（威信县范围正确）
- GeoJSON 生成：前 50 个质心点已写入 data/_preview.geojson
- 字段对齐：GDB「优势树/土地权」vs schema「优势树种/土地权属」，需加别名

### 测试结果
- pytest 18 项全部通过（test_schema.py + test_exporter.py）
- 两端 healthz 正常：/survey/healthz + /admin/healthz 均返回 ok
- 两端 api/me 认证正常

## 3. 设计方案

### 架构（积木切分）
```
core/    共享核心（C1-C5）← 两端共用，不打包
admin/   管理端（A1-A2）  ← GDB/用户/项目，不打包
user/    用户端（U1-U2）  ← 调查/打卡，打包 Android
```

### 路由隔离
- Admin: /admin/（GDB上传/解析/用户/项目/预填/导出）
- User:  /survey/（schema/项目只读/小班地图/调查/打卡/导出）

### GDB 数据流
```
GDB(zip上传) → C4解析 → C2存gdb_files → C4生成GeoJSON(4326)
                                                    ↓
U2地图渲染 ← U1读GeoJSON ← C2 ← C4
调查数据 → C2存records → C3导出xlsx（不写回GDB）
```

### AI 高速路（BLOCKS.md）
按 043 方法论 B3 阶段，建立 BLOCKS.md 作为 AI 索引：
- 12 个积木编号（C1-C5, A1-A2, U1-U2, L1-L2, G1）
- AI 先读 BLOCKS.md 按编号定位文件，不盲读大文件

## 4. 代码修改

| 文件 | 操作 | 说明 |
|---|---|---|
| survey/schema.py → survey/core/schema.py | 移动 | 核心层 |
| survey/storage.py → survey/core/storage.py | 移动 | +gdb_files表 +小班行新字段 |
| survey/exporter.py → survey/core/exporter.py | 移动 | import 路径更新 |
| survey/core/gdb.py | 新增 | GDB解析层 |
| survey/core/auth.py | 新增 | 共享认证 |
| survey/admin/app.py | 新增 | Admin端 |
| survey/admin/static/admin.js | 新增 | Admin前端(902行) |
| survey/user/app.py | 新增 | User端 |
| survey/user/static/app.js | 新增 | User前端(1647行,精简) |
| survey/launcher.py | 新增 | 本地DispatcherMiddleware挂载 |
| survey/web/ | 删除 | 旧单app结构 |
| survey/subcompartment_parser.py | 删除 | xlsx解析(已放弃Excel) |
| survey/normalize.py | 删除 | xlsx相关 |
| BLOCKS.md | 新增 | AI高速路索引 |
| .gitignore | 新增 | 排除data/ |

## 5. 测试验证

- pytest: 18/18 通过
- /survey/healthz: ok=true, side=user, tables=5
- /admin/healthz: ok=true, side=admin, tables=5
- /survey/api/me: 认证正常
- /admin/api/me: 认证正常

## 6. 反转记录

- **反转：Excel → GDB**。原架构用 xlsx 小班信息上传，现放弃 Excel 改用 GDB。删除 subcompartment_parser.py、normalize.py、test_subcompartment.py。
- **反转：单app → 双app分离**。原 survey/web/app.py 单文件 798 行混合 admin/user，现拆为 admin/ + user/，core/ 共享。

## 7. 部署记录

- 本地：./start.sh（launcher.py 挂载 /admin/ + /survey/）
- 生产：bibook_deploy gateway 聚合挂载（待配置）
- .gitignore 排除 data/（GDB源文件不入库）
