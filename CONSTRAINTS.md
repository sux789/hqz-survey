# CONSTRAINTS — hqz-survey 硬约束注册表

> 改动前必读。任何代码/模板/部署改动违反本表即 bug。
> 格式：编号 | 约束 | 来源（日期/反转记录）。
> 新增约束追加在对应分组末尾，编号不复用；废弃约束移入文末「反转记录」，不删除。

## A 架构与端分离

- A1 Admin 与 User 端必须隔离；Admin 端不打包进 App，仅 User 端打包 Android
- A2 后端分离为 survey/admin/app.py 与 survey/user/app.py；前端分离为 admin.js 与 user/app.js
- A3 Admin URL 用 /survey-admin 前缀，不用 /admin（避免与 forest-data 管理后台冲突）
- A4 数据流：GDB上传 → import_gdb_subcompartments → subcompartment_rows → 用户端地图渲染与调查 → records → exporter
- A5 本地缓存与离线同步逻辑（含 syncDot 元素）已移除，不得重新引入

## B 数据模型

- B1 数据库 8 表：projects / project_members / gdb_files / subcompartment_rows / subcompartment_extras / records / prefilled（prefilled 已停用见 R7）
- B2 GDB 文件只读；修改只写 SQLite 的 data_json 字段
- B3 subcompartment_rows 必含 gdb_id、gdb_feature_id、geom_geojson
- B4 林班/小班字段在数据库层为无符号整数
- B5 小班(subcompartment)是项目最小粒度；调查表1~5、照片、轨迹、打卡全部与小班一对一，唯一键 = (project_id, table_id, subcompartment_id)
- B6 table5b 样方级调查收编为该小班 table5 记录 data_json.samples 子数组，不作独立子表；table5 无 has_subtables/subtables 结构
- B7 records 表无 subtable_id/row_index 列；统一走 upsert_survey_row/get_survey_rows
- B8 "5个表id用小班id"指小班id作业务唯一定位键，records 主键仍独立 uuid（避免同小班跨表主键冲突）
- B9 小班下拉 value 必须用 subcompartment_rows 主键 id，不能用调查小班号（多林班同号小班会选不中）
- B10 几何数据：geom_geojson（单面供点击）+ polygons.geojson 文件（全量供渲染）
- B11 GDB 导入时"乡"和"乡镇"都映射到 township
- B12 旧小班已录入数据为测试数据：完全放弃，不删除、不迁移、不兼容

## C 字段与业务口径

- C1 林班 > 0 时小班标签显示"林班-小班"；林班为 0 时仅显示小班号（无"0-"前缀）
- C2 小班号读取优先级：GDB"调查小班号" > "小班" > "新小班号"
- C3 进入项目时项目 ID 写 localStorage 键 'hqz_survey_last_project'；init() 自动进入上次项目，无记录则取列表第一个；须触发完整工作流（小班/分类/网格页渲染），不允许"未选择项目"空页
- C4 验收人(inspector)无输入框，取 api/me 的 display_name 或 username；'userInput' 元素已删除
- C5 打卡状态筛选：样地坐标 x、y 均非空=已打卡，否则未打卡
- C6 管理情况5字段（作业设计/会议纪要/讲话记录/调研报告/监理报告）enum ["有","无"] 默认"有"；前端不保存未触碰默认值，导出时空值走 _norm_enum 取默认；已录入值优先
- C7 全部5表末尾含 survey_completed（调查已经完成）enum ["是","否"] 默认"否"；table1 导出写 BK 列
- C8 成活率(%) = round(平均成活株数) × 666.67 ÷ (样地面积 × 每亩设计株树) × 100，结果保留2位小数
- C9 AN列（小班查数株数）= Σ对应样地种植株数（同口径样地模板B30公式），不依赖"网格面积×种植网格数"
- C10 死亡株数不手填不录入（模板公式自动算 种植-成活）
- C11 手写值全部不录入（签字/手写项导出留空；AR/AU 手写签字导出留空）
- C12 样方字段作为1:1宽表字段显示在基础信息后、验收人员字段前；无"切换样方"按钮和样方数组模式

## D 导出

- D1 导出入口仅管理后台 + 用户端 topbar「导出」（基本信息/样地，按当前项目+分类）
- D2 导出记录按（林班, 调查小班号）数字序；exporter 显式 sort，不依赖 SQL ORDER BY
- D3 合格率导出写数值（如 95.24），非百分比字符串
- D4 轨迹导出 GPX 1.1，按小班打包 ZIP；Excel 轨迹列写对应 GPX 文件名
- D5 基本信息导出：一项目一文件（tpl-base 3分类sheet）；分类参数指定时其余 sheet 移除
- D6 样地导出：tpl-samples 块结构（39行/块），每分类一个 sheet、每小班一块；单小班模式仅导该小班所属分类一个 sheet、一个小班块
- D7 sheet 名格式「分类-调查小班号」，经 _sheet_safe 清禁止字符并截断 31 字符
- D8 文件名格式：基本信息 `{项目名}_{分类}_基本信息.xlsx`；样地 `{项目名}_{分类}_调查小班号_样地.xlsx`
- D9 导出 Excel A2 单元格含小班和调查小班号；同时显示小班(原始)与调查小班号
- D10 GDB 属性导入的字符串数值（"2023.0"）经 _fmt_num 数值化，整数去 .0；含汉字的原始小班号（如「红9」）原样保留
- D11 坐标导出保留全精度浮点（打卡坐标列表1 AV/AW 等不做2位舍入）
- D12 导出范围：用户端仅当前分类当前小班；管理后台不传参保持整项目导出

## E 样地

- E1 每个样地一个照片按钮；一样地可多照片；仅存相册文件名不上传、不自动生成
- E2 添加样地时点按钮自动 GPS 获取坐标
- E3 样地删除：仅最后一个样地可删；最后一个样地数据完整（面积/种植株数/成活株数/经纬度均填）时不可删，需先将面积或种植株数置0或空
- E4 输入校验：面积/种植株数/成活株数非负；种植株数 ≥ 成活株数
- E5 样地倒序显示（最新在最上）；经纬度只读文本，不显示输入框
- E6 保存三时机：光标离开立即保存 / 停顿1.2秒自动保存 / 手动按钮；顶部实时状态（未保存/保存中/已保存/保存失败）；失败自动重试2次后保留内存数据允许手动重试
- E7 添加新样地前校验已有样地完整性（面积/种植株数/成活株数/经纬度），不完整阻止并提示缺失项
- E8 样地为独立页面非弹窗；卡片 repeat(auto-fill, minmax(150px,1fr))；Android 物理返回键触发返回小班

## F 轨迹 / 照片 / 打卡

- F1 打卡自动填充样地坐标 x/y（GPS 经度→x，纬度→y）和验收时间，仅填空值不覆盖
- F2 同一设备同时只记一条轨迹；开始新轨迹前自动停止并保存旧轨迹，状态显示"已停止"
- F3 轨迹点实时同步 UI，每15秒自动保存；切换小班/退后台/关页面兜底保存；停止时无轨迹点须明确提示
- F4 照片保存走原生插件 savePhoto（MediaStore 写入 Pictures/验收照片/），不用 WebView <a download>；提示显示原生返回真实路径，且提示框须清理旧提示
- F5 照片水印：EXIF GPS 优先、回退当前定位；左下黑字白边三行（日期/坐标/备注）；重编码 JPEG 0.92，扩展名 .jpg；水印含样地号
- F6 App 导出落盘走原生 saveFile（MediaStore Downloads/验收导出/）；浏览器/旧 APK 回退 <a download>

## G Android / 打包

- G1 App 必须申请 GPS（ACCESS_COARSE/FINE_LOCATION）与相机权限，缺失时提示用户
- G2 打包只用 GitHub Actions，不本地打包；workflow 用 'npx cap sync android'（保留自定义 Android 文件）
- G3 android/ 目录必须进版本控制（不 gitignore）
- G4 Actions 自动发布最新 APK 到 'latest' release，固定下载链接
- G5 AndroidManifest 必须声明 <queries>（IMAGE_CAPTURE/VIDEO_CAPTURE），否则拍照静默回退文件选择器（2026-08-17 修复，commit e97326b）
- G6 前端启动及拍照前做权限检查，拒绝时弹引导（AppPermissionsPlugin）

## H 部署 / 运维

- H1 生产：admin 挂 https://forest.bibook.top/admin/；gateway 在 www@forest.bibook.top（systemd --user gateway.service），shared_venv=/home/www/shared_venv
- H2 launcher.py 的 SURVEY_LOCAL_DEV 必须在 import app 之前设置（app 模块级求值 LOCAL_DEV）
- H3 deploy.sh 必须排除 *.bak*、*.db 等（见脚本 EXCLUDE）
- H4 launcher 热加载 reloader_type="stat"，exclude_patterns 排除 data/ 与 *.db（防上传被重启掐断）；本地 SEND_FILE_MAX_AGE_DEFAULT=0
- H5 服务器 shared_venv 需 geopandas/pyogrio/pyproj/shapely；缺失时 GDB 上传报"未找到分类图层"
- H6 模板统一在项目根 tpl/（tpl-base.xlsx / tpl-samples.xlsx），deploy.sh 同步；官方原版归档 tpl/official/ 仅供对照
- H7 gdb.py 图层分类前缀：人工造林/封山育林/退化林修复（水利水保/草原已彻底删除）
- H8 project_name_exists_global 以 projects 表为真相源查重；delete_project 按项目名兜底清理孤儿小班行

## R 反转记录（已废弃约束，保留溯源）

- R1 已删：本地缓存/离线同步（syncDot）→ 见 A5
- R2 已删：table4/table5 水利水保/草原分类、tpl 多余 Sheet3 密点参考
- R3 已删：'userInput' 验收人输入框 → 见 C4
- R4 已删：旧一对多轨 save_record/get_records/delete_record → 见 B7
- R5 已删：样方切换按钮与样方数组模式 → 见 C12
- R6 已删：照片 WebView <a download> 保存方式 → 见 F4（鸿蒙落盘位置不可控）
- R7 已停用：prefilled 表；预填黄色列由 map_subcompartment_to_prefilled 从 subcompartment_rows 实时映射
