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
- C2 小班号读取优先级：GDB"调查小班号" > "小班" > "新小班号"；选中值非数值（含汉字如「红9」）时提取数字部分作调查小班号（_derive_uint_with_digits，无数字→0 跳过该行）
- C13 小班语义（2026-08-21，废弃「小班原始」）：小班 = GDB 原值原样保留（可含汉字，如「新增17」），导入不覆写、仅用于导入/导出展示（基本信息 F/G 列、样地表标题括注）；调查小班号 = 数字业务键（DB 列 subcompartment uint，排序/标签/文件名/select 全局使用），导入时规范化写回 data_json（"5.0"→5）；「小班原始」键与小班重复已废弃——新导入不再写入，旧数据行保留映射兼容读取（schema 映射序：小班在前、小班原始在后，旧值优先生效）
- C3 进入项目时项目 ID 写 localStorage 键 'hqz_survey_last_project'；init() 自动进入上次项目，无记录则取列表第一个；须触发完整工作流（小班/分类/网格页渲染），不允许"未选择项目"空页
- C4 验收人(inspector)无输入框，取 api/me 的 display_name 或 username；'userInput' 元素已删除
- C5 打卡状态筛选：样地坐标 x、y 均非空=已打卡，否则未打卡
- C6 管理情况5字段（作业设计/会议纪要/讲话记录/调研报告/监理报告）enum ["有","无"] 默认"有"；前端不保存未触碰默认值，导出时空值走 _norm_enum 取默认；已录入值优先
- C7 全部5表末尾含 survey_completed（调查已经完成）enum ["是","否"] 默认"否"；table1 导出写 BK 列
- C8 率类字段统一存比率 0-1 不 ×100（2026-08-21 起）：percent 输入字段（成活率三列/施工率/建档率/管护率/抚育率等）与 computed 合格率均存 0.9524 形态；前端输入 95 显示 95%、保存 ÷100、回显 ×100（pctToStore/pctToDisplay）；schema 校验 0-1；旧 ×100 数据已由 tools/fix_rate_data.py 迁移（>1 判定法 ÷100，幂等）；旧 666.67×100 成活率公式已废弃（见 R10）
- C9 小班查数株数（基本信息 AN/BE/AR 列 + 前端 computed）= 调查总株数（样地模板 B34 同口径）= round(Σ种植÷个数÷150×单个网格面积×种植网格数量)；个数=手写 sm_total_count（>0生效）回退实际样地数；合格率=Σ成活÷Σ种植（比率0-1保留4位）；合格株树=round(查数株数×合格率)（率同比率不÷100）随之联动（2026-08-21 反转旧Σ种植口径，见 R8）
- C10 死亡株数不手填不录入（模板公式自动算 种植-成活）
- C11 手写值全部不录入（签字/手写项导出留空；AR/AU 手写签字导出留空）
- C12 样方字段作为1:1宽表字段显示在基础信息后、验收人员字段前；无"切换样方"按钮和样方数组模式

## D 导出

- D1 导出入口仅管理后台 + 用户端 topbar「导出」（基本信息/样地，按当前项目+分类）
- D2 导出记录按（林班, 调查小班号）数字序；exporter 显式 sort，不依赖 SQL ORDER BY
- D3 合格率导出写数值（比率 0-1 如 0.9524），非百分比字符串；率类单元格（含模板行5后新增导出行）统一 0.00% 百分比格式，Excel 显示 95.24%；模板分派公式阈值按比率比较（AP>=0.9 合格、<=0.4 失败）
- D4 轨迹导出 GPX 1.1，按小班打包 ZIP；Excel 轨迹列写对应 GPX 文件名
- D5 基本信息导出：一项目一文件（tpl-base 3分类sheet）；分类参数指定时其余 sheet 移除
- D6 样地导出：tpl-samples 块结构（39行/块：R1标题含调查小班号 / R2年度县乡 / R3列头 / R4-26数据槽 / R27-39汇总区），每分类一个 sheet、每小班一块；单小班模式仅导该小班所属分类一个 sheet、一个小班块；样地行备注（sample.remark，卡片成活株数后文本框）导出写 I 列（模板 I3=备注，2026-08-21 用户手加表头）
- D7 sheet 名格式「分类-调查小班号」，经 _sheet_safe 清禁止字符并截断 31 字符
- D8 文件名格式：基本信息 `{项目名}_{分类}_基本信息.xlsx`；样地 `{项目名}_{分类}_调查小班号_样地.xlsx`
- D9 导出 Excel A2 单元格含小班和调查小班号；同时显示小班（GDB 原值，可含汉字）与调查小班号
- D10 GDB 属性导入的字符串数值（"2023.0"）经 _fmt_num 数值化，整数去 .0；含汉字的小班（如「红9」「新增17」）原样保留
- D11 坐标导出保留全精度浮点（打卡坐标列表1 AV/AW 等不做2位舍入）
- D12 导出范围：用户端仅当前分类当前小班；管理后台不传参保持整项目导出
- D13 tpl-base 模板示例行（R5）自带成活率/合格率分派公式（表1 O/P/Q/S/AF/AH/AL，表2 U/V/W/Y/AW/AY/BC，表3 P/Q/R/T/AJ/AL/AN/AP）；导出时公式按行偏移代入每个数据行；exporter 有录入值的列以录入值优先（值覆盖公式）——例外见 D20：store:false 派生列一律公式，录入值不导出（2026-08-24）
- D20 成活率等级三列（合格≥0.9/待补植0.4<x<0.9/失败≤0.4，互斥显示合格率）与面积分派列（合格面积/符合设计的施工面积/建档面积/管护面积[仅表3]/抚育面积，合格率≥0.9 时=上报面积原样，<0.9 留空）为 schema computed + store:false 派生字段（2026-08-24 起）：前端 computeFieldValue 实时算仅显示（s_survival_pass/replant/fail、s_qualified_area——上报面积取当前小班预填 currentPrefilled），PUT 端点剔除不落库，导出忽略残留值一律模板公式 =IF(合格率>=0.9,…) 现算；表1/表2 管护面积仍为手输 number（无公式）
- D13a 模板公式禁自引用（Excel 循环引用告警）：面积分派列统一 =IF(合格率>=0.9,施工面积,"")，不合格留空由录入值/人工填；2026-08-21 修复 9 处 =IF(cond,面积,自身) 自引用，另修封山育林 W5 失败列笔误（S5→BG5，失败列显示合格率非面积）；回归门禁 tests/test_exporter.py::TestTemplateFormulas
- D14 样地表 R1 标题 = 「{项目名}样地调查表（{分类}）调查小班号{号}」，小班原值≠调查号时括注（小班{原值}）；R2 = 年度+县+乡
- D15 样地汇总区（R27-39，2026-08-21 起块高 39）：B27 总样地个数优先手写值 data_json.sm_total_count（>0 生效，与前端同口径），无有效手写值回退实际样地数；B28-30 SUM / B31 成活率 =IF(B29=0,"",B30/B29) / B34 调查总株数 =IF(OR(B27=0,B29=0),"",ROUND(B29/B27/150*B32*B33,0)) 为模板公式（除0守卫，块复制时行号偏移；B34 引用 B27，手写个数参与计算）；B32 单个网格面积 / B33 种植网格数量 / B35 撑杆 / B36 覆膜 / B37 验收人 / B38 验收日期 / B39 备注 从 data_json.sm_* 键写入（sm_grid_area/sm_grid_count/sm_pole/sm_film/sm_inspector/sm_inspect_date/sm_remark）
- D16 样地页汇总区 13 项与模板 R27-39 一一对应：5 项自动计算（面积/总株数/成活株数/成活率/调查总株数，除0显示'--'）转纯文本放统计行（sm-stats-line，信息条下方，含"共 N 个样地"共 6 项文本）+ 8 项手写输入（sm_* 键落库，总样地个数 sm_total_count 默认自动填充实际样地数——仅空值或等于旧自动值时跟随增删同步，手改后以用户值为准；调查总株数公式中个数用手写值，与 B27/B34 同口径）；验收人默认当前用户、验收日期默认当天（仅填空值不覆盖，同打卡口径）；每张样地卡片尾部显示死亡株数（种植−成活，只读提示，导出由模板 E 列公式算）；样地页布局（2026-08-21 二次调整）：信息条→统计行（文本，不可输入）→滚动区 samples-scroll（样地列表 + 汇总表单在列表最下面随列表滚动，可输入控件不占列表上方空间防卡片被遮挡）→「+添加样地」按钮底部；统计值由 updateSmSummaryComputed 刷 data-sm-sum-val（不重渲染表单防焦点丢失）
- D17 网格工具栏小班 select 前有调查小班号搜索框（#gridScSearch，仅正整数触发）：在当前筛选结果内按 r.subcompartment 精确匹配，命中则设 select 值并派发 change（复用手动选中完整流程：停轨迹/载数据/刷工具栏/渲染网格）；无匹配 toast 提示不动选择
- D18 管理后台「分类下载」（项目管理 tab 展开行，2026-08-21，当日二次改版）：每分类三个下载，按钮顺序 基本信息→样地打包→轨迹打包——基本信息不打包直接下载 xlsx（export_base(category=)，仅该分类 sheet）；样地为 zip 且每小班一个 xlsx（export_samples_zip：单小班模式逐班导出再打包，784 小班约 26s）；轨迹为 zip（export_tracks_zip(category=)）。下载文件名 {分类}-{年度}-基本信息.xlsx / {分类}-{年度}-样地.zip / {分类}-{年度}-轨迹.zip（年度=项目名「(2023 年度)」→当前年，admin._dl_year）；zip 内文件名统一 {林班-小班|小班}号调查小班-{分类}-{年度}.xlsx/.gpx（exporter._sc_file_base：林班>0 带「林班-」防同号冲突，年度=小班 GDB 计划年度→项目名→当前年，同名追加序号；GPX 在 zip 内位于 tracks/ 目录）。GPX 文件名与 Excel 轨迹列同源 _track_gpx_filename（表1 AX/表2 BO/表3 BB），改版后两处一致。分类清单 GET /api/projects/<pid>/categories（懒加载缓存）；不传 cat 时整项目导出不变（实测对比）
- D19 GDB 文件列表不提供删除入口（2026-08-21 起移除 UI 按钮，防误删）：删除 GDB 只能随项目删除（DELETE /api/projects/<pid> 兜底清理关联 GDB）

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
- F4 照片保存走原生插件 savePhoto（MediaStore 写入 Pictures/{年度}年度/{分类}/{调查小班号}号调查小班/，2026-08-21 起多级子目录；旧目录 Pictures/验收照片/ 已弃用），不用 WebView <a download>；年度取小班计划年度（GDB，"2023.0" 去 .0）→ 项目名「(2023 年度)」正则 → 当前年；每段目录名经 _dirSeg/sanitizeSubdir 清理非法字符并防路径穿越；提示显示原生返回真实路径（跨小班时按 subdir 匹配防串显），且提示框须清理旧提示
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
- H9 认证 session cookie 必须全站唯一一条（Path=/）：survey/survey-admin 的 create_app 显式 SESSION_COOKIE_PATH='/' + SESSION_REFRESH_EACH_REQUEST=False（Flask 默认会用 APPLICATION_ROOT 即 /survey、/survey-admin 写出第二条同名 cookie，登出不清理且"最长路径优先"发送遮蔽新登录 → 换账号后 api/me 仍返回前一个用户，2026-08-24 修复 7082570）；两端 after_request 持续下发前缀 cookie 删除指令清历史遗留；forest-data 登录/登出同时 delete_cookie 前缀路径

## R 反转记录（已废弃约束，保留溯源）

- R1 已删：本地缓存/离线同步（syncDot）→ 见 A5
- R2 已删：table4/table5 水利水保/草原分类、tpl 多余 Sheet3 密点参考
- R3 已删：'userInput' 验收人输入框 → 见 C4
- R4 已删：旧一对多轨 save_record/get_records/delete_record → 见 B7
- R5 已删：样方切换按钮与样方数组模式 → 见 C12
- R6 已删：照片 WebView <a download> 保存方式 → 见 F4（鸿蒙落盘位置不可控）
- R7 已停用：prefilled 表；预填黄色列由 map_subcompartment_to_prefilled 从 subcompartment_rows 实时映射
- R8 已反转（2026-08-21）：小班查数株数 = Σ样地种植株数（B30 口径，"不依赖网格面积×种植网格数"）→ 改为调查总株数（B34 口径，round(Σ种植÷个数÷150×网格面积×网格数量)）→ 见 C9；反转原因：基本信息表按小班汇报应取全班估计值而非样地求和
- R9 模板手改提醒（2026-08-21）：用户手动编辑 tpl-samples.xlsx 加 I3 备注 表头时曾顺带删掉 B31/B34 除0守卫公式（#DIV/0! 风险），已恢复；手改模板后跑 `python -m pytest tests/ -q`（TestTemplateFormulas + 汇总公式断言会拦）
- R10 已反转（2026-08-21）：率类字段（合格率 computed + percent 输入列）存 ×100 值（如 98.44/95）→ 统一存比率 0-1（0.9844）；旧口径与 Excel 0.00% 百分比格式语义相反（导出显示 9844%、分派公式 0.9/0.4 阈值恒真错列）→ 见 C8/D3；另废弃 C8 旧成活率公式（round(平均成活株数)×666.67÷(样地面积×每亩设计株树)×100，代码已无引用），现口径 B31=Σ成活÷Σ种植 比率
- R11 已反转（2026-08-24）：成活率等级三列（O/P/Q 类）原为 percent 手输列（存比率、录入值导出覆盖公式），面积分派列（S/AF/AH/AL 类）原为 number 手输 → 均改为 computed + store:false 派生列（前端实时算不落库，导出一律模板公式现算，见 D20）；反转原因：模板本身带分派公式，手输/落库会产生双真相（录入值与公式结果不一致时无仲裁），且面积分派=IF(合格率≥0.9,上报面积) 口径由公式单一承担；生产库经查无这些 key 的历史录入值，无迁移需求
