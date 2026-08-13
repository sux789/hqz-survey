# BLOCKS — hqz-survey 积木注册表
# AI 高速路：先读本文件按编号定位，不盲读大文件

# ═══ 共享核心层 core/ ═══
C1  Schema 契约      survey/core/schema.py        五表字段定义 + 小班映射（唯一真相源）
C2  数据层           survey/core/storage.py        SQLite：项目/小班/记录/扩展/GDB文件
C3  导出             survey/core/exporter.py       xlsx 导出（按 schema 驱动）
C4  GDB解析          survey/core/gdb.py            读图层/坐标转换/GeoJSON/md快照
C5  认证             survey/core/auth.py           forest-data session 共享

# ═══ Admin 端 admin/（不打包）═══
A1  Admin入口        survey/admin/app.py           GDB上传/用户/项目/预填/导出
A2  Admin前端        survey/admin/static/admin.js  GDB管理+用户管理+项目管理UI

# ═══ User 端 user/（打包Android）═══
U1  User入口         survey/user/app.py            调查/打卡/导出（精简）
U2  User前端         survey/user/static/app.js     地图+调查表单+打卡（移动端）

# ═══ 启动/部署 ═══
L1  本地启动         survey/launcher.py            DispatcherMiddleware 挂载 admin+user
L2  启动脚本         start.sh                      本地测试（SURVEY_LOCAL_DEV=1）
G1  网关首页         deploy/gateway/home.py        生产聚合挂载（bibook_deploy）

# ═══ 数据流（业务→代码映射）═══
# GDB上传 → A1 → C4(解析) → C2(存gdb_files) → C4(生成GeoJSON)
# 用户调查 → U1 → C2(读小班) → U2(地图渲染) → C2(存records) → C3(导出xlsx)
