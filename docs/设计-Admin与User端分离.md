# Admin 与 User 端分离设计

> 目标：Admin 端承载 GDB 上传/解析/管理，不打包；User 端只保留调查/打卡/导出，打包为 Android app。两端代码隔离，共享核心层。

## 一、现状与问题

| 项 | 现状 | 问题 |
|---|---|---|
| 后端 | `survey/web/app.py` 单文件 798 行，admin + user 路由混在一起 | 改 user 影响admin，反之亦然 |
| 前端 | `survey/web/static/app.js` 单 SPA，含用户管理/项目创建/批次上传/调查 | 打包 app 体积大，外业员看到管理功能 |
| 数据源 | xlsx 小班信息上传 | 已决定改用 GDB，需新增解析层 |
| 部署 | gateway 挂载单一 `/survey/` | admin 应独立路径 `/admin/` |

## 二、目标结构

```
survey/
├── core/                        # 共享核心（两端共用，不打包）
│   ├── __init__.py
│   ├── schema.py                # 五表字段定义（唯一真相源，从 survey/schema.py 移入）
│   ├── storage.py               # SQLite 调查库（从 survey/storage.py 移入）
│   ├── exporter.py              # xlsx 导出（从 survey/exporter.py 移入）
│   ├── gdb.py                   # GDB 解析（新增：读图层/字段/几何/坐标转换/生成GeoJSON）
│   └── auth.py                  # 共享认证（从 app.py 抽出：_current_user/login_required/admin_required）
│
├── admin/                       # 管理端（不打包，纯 Web 后台）
│   ├── __init__.py
│   ├── app.py                   # Flask app，挂载前缀 /admin/
│   ├── static/
│   │   └── admin.js             # admin 独立前端（全新，不用 app.js）
│   └── templates/
│       └── index.html
│
├── user/                        # 用户端（打包为 Android app）
│   ├── __init__.py
│   ├── app.py                   # Flask app，挂载前缀 /survey/（精简）
│   ├── static/
│   │   └── app.js               # user 端前端（精简：登录→项目→小班→调查→打卡→导出）
│   └── templates/
│       ├── login.html           # 登录页（后续做，当前测试环境隔离）
│       └── index.html
│
└── gateway/                     # 聚合挂载（保持不变）
    └── deploy/gateway/          # admin + user 两个 app 都挂到 gateway
```

## 三、路由划分

### Admin 端路由（`/admin/`，不打包）

| 方法 | 路径 | 职责 |
|---|---|---|
| GET | `/admin/` | 管理后台首页 |
| GET | `/admin/api/me` | 当前管理员 |
| POST | `/admin/api/gdb/upload` | 上传 .gdb 文件 |
| GET | `/admin/api/gdb/list` | GDB 文件列表 |
| GET | `/admin/api/gdb/<gid>/layers` | 某个 GDB 的图层列表 |
| GET | `/admin/api/gdb/<gid>/layers/<layer>` | 图层字段+前N行预览 |
| POST | `/admin/api/gdb/<gid>/geojson` | 生成 GeoJSON（指定图层、坐标转换4326） |
| GET | `/admin/api/gdb/<gid>/geojson` | 下载已生成的 GeoJSON |
| DELETE | `/admin/api/gdb/<gid>` | 删除 GDB |
| GET | `/admin/api/users` | 用户列表 |
| POST | `/admin/api/users` | 创建用户 |
| PUT | `/admin/api/users/<uid>` | 启用/禁用 |
| POST | `/admin/api/users/<uid>/password` | 重置密码 |
| GET/POST | `/admin/api/projects` | 项目列表/创建 |
| GET/POST/DELETE | `/admin/api/projects/<pid>/members` | 成员管理 |
| GET/POST | `/admin/api/projects/<pid>/prefilled/<table_id>` | 预填数据 |
| GET | `/admin/api/projects/<pid>/export` | 导出全部 xlsx |

### User 端路由（`/survey/`，打包为 app）

| 方法 | 路径 | 职责 |
|---|---|---|
| GET | `/survey/` | 用户首页（登录后） |
| GET | `/survey/login` | 登录页（后续做） |
| GET | `/survey/api/me` | 当前用户 |
| GET | `/survey/api/schema` | 表定义（前端动态渲染） |
| GET | `/survey/api/schema/extras` | 扩展字段定义 |
| GET | `/survey/api/projects` | 我的项目（只看自己） |
| GET | `/survey/api/projects/<pid>` | 项目详情 |
| GET | `/survey/api/projects/<pid>/subcompartments` | 小班列表（含GeoJSON质心，地图用） |
| GET | `/survey/api/projects/<pid>/geojson` | 项目小班面 GeoJSON（地图渲染） |
| GET | `/survey/api/subcompartments/rows/<row_id>` | 小班详情+prefilled+extras |
| POST | `/survey/api/projects/<pid>/records` | 保存调查记录 |
| GET | `/survey/api/projects/<pid>/records/<table_id>` | 获取记录 |
| DELETE | `/survey/api/records/<rid>` | 删除记录 |
| POST | `/survey/api/subcompartments/rows/<row_id>/checkin` | 打卡 |
| POST | `/survey/api/subcompartments/rows/<row_id>/track` | 轨迹 |
| POST | `/survey/api/subcompartments/rows/<row_id>/photos` | 照片 |
| GET | `/survey/api/projects/<pid>/export` | 导出我的 xlsx |

**User 端删除的路由**（移到 admin）：
- `/api/admin/*` 全部用户管理
- `POST /api/projects`（创建项目）
- `POST /api/projects/<pid>/prefilled/*`
- `POST /api/projects/<pid>/subcompartments/upload`
- `DELETE /api/subcompartments/batches/*`

## 四、GDB 解析层设计（core/gdb.py）

```
core/gdb.py
├── list_layers(gdb_path) → [{name, geometry_type, row_count, field_count}]
├── read_layer(gdb_path, layer, max_features=None) → GeoDataFrame
├── to_wgs84(gdf) → GeoDataFrame  # EPSG:4507 → 4326
├── to_geojson(gdf, properties=None) → dict  # 前端地图渲染
├── to_md(gdb_path, out_dir) → None  # 生成 gdb2md 快照（AI 高速路索引）
├── layer_fields(gdb_path, layer) → [{name, type}]
└── save_gdb(upload_file, storage_dir) → {gid, path, layers}
```

存储结构：
```
data/
├── gdb/
│   ├── <gid>/
│   │   ├── source.gdb          # 原始 GDB（只读，gitignore）
│   │   ├── meta.json           # {uploaded_at, uploader, layers, file_hash}
│   │   ├── preview.geojson     # 预生成质心点 GeoJSON（地图定位用）
│   │   └── gdb2md/             # md 快照（AI 索引，可入库）
│   └── ...
```

## 五、数据库变更

`storage.py` 新增表（GDB 管理）：

```sql
CREATE TABLE IF NOT EXISTS gdb_files (
    id          TEXT PRIMARY KEY,      -- gid (uuid)
    project_id  TEXT NOT NULL,         -- 关联项目
    file_name   TEXT NOT NULL,
    file_hash   TEXT NOT NULL,         -- SHA256 去重
    layers_json TEXT NOT NULL,         -- 图层列表快照
    uploaded_by TEXT NOT NULL,
    uploaded_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id)
);
```

小班行表 `subcompartment_rows` 新增字段：
```sql
ALTER TABLE subcompartment_rows ADD COLUMN gdb_id TEXT DEFAULT '';      -- 来源 GDB
ALTER TABLE subcompartment_rows ADD COLUMN gdb_feature_id TEXT DEFAULT ''; -- GDB New_ID 外键
ALTER TABLE subcompartment_rows ADD COLUMN geom_geojson TEXT DEFAULT '';   -- 小班面 GeoJSON（缓存）
```

## 六、前端分离

### admin.js（管理端，全新）
- GDB 上传/管理面板
- 图层预览（字段表 + 前N行）
- 用户管理
- 项目创建 + 成员分配
- 预填数据编辑
- 全量导出

### app.js（用户端，精简）
保留：
- 登录入口（后续做）
- 项目列表（只看自己的）
- 小班地图视图（Leaflet + GeoJSON，点击小班面）
- 小班列表搜索
- 调查表填写（schema 驱动）
- 打卡/轨迹/照片
- 导出我的数据

删除：
- adminUsers 状态及相关 UI
- 项目创建表单
- 批次上传 UI
- 预填数据编辑
- 用户管理面板

## 七、部署与打包

### 部署（gateway 聚合）
```
gateway 挂载:
  /admin/  → survey.admin.app
  /survey/ → survey.user.app
```

### User 端打包 Android app（Capacitor）
```
user 端 → Capacitor 套壳 → Android APK
  - WebView 加载 /survey/ 页面
  - 相机/GPS 原生插件
  - 离线缓存 Service Worker
```

Admin 端不打包，纯 Web 后台访问。

## 八、迁移步骤

1. 创建 `core/` 目录，移动 schema.py / storage.py / exporter.py
2. 抽出 `core/auth.py`（共享认证逻辑）
3. 新增 `core/gdb.py`（GDB 解析）
4. 拆分 `survey/web/app.py` → `survey/admin/app.py` + `survey/user/app.py`
5. 拆分 `app.js` → `admin/admin.js` + `user/app.js`
6. storage.py 加 gdb_files 表 + subcompartment_rows 新字段
7. 更新 gateway 挂载
8. 测试通过后删除旧 `survey/web/`
```

## 九、简化收益

| 项 | 改造前 | 改造后 |
|---|---|---|
| User app.js 体积 | ~全功能 | 砍掉 ~40% admin 代码 |
| User 打包 APK | 含管理功能 | 纯调查功能 |
| 路由冲突 | admin/user 同前缀 | `/admin/` vs `/survey/` 隔离 |
| 改 admin 影响 user | 是 | 否 |
| GDB 解析 | 无 | core/gdb.py 统一 |
| 数据源 | xlsx | GDB（只读）+ SQLite（调查） |
