# AGENTS — AI 协作协议

> 任何 AI/人类改动本仓库前必读。目标：改动可追溯、回归可发现、约束不丢失。

## 改动前

1. 先读 [BLOCKS.md](BLOCKS.md) 按编号定位模块，不盲读大文件。
2. 再读 [CONSTRAINTS.md](CONSTRAINTS.md)，确认改动不违反任何硬约束；涉及导出/模板/GDB 时逐条核对 C/D/H 组。
3. 不懂结构先跑探测，不猜：
   - Excel 模板结构 → `python tools/inspect_tpl.py [xlsx路径]`（sheet/合并区/表头行）
   - GDB 结构 → `python tools/verify_gdb.py <.gdb目录路径>`（图层/字段别名/坐标转换）
4. 指令精确到三级坐标：积木编号 → 文件 → 单元格/列字母/行号。不确定时先列待确认清单，不臆测。

## 改动中

- 最小改动：不顺手重构、不改与任务无关的代码、不新增未要求的抽象。
- 模板（tpl/*.xlsx）是数据不是代码：换官方新模板直接替换同名文件；官方原版归档 tpl/official/，不删。
- 业务口径（成活率/合格率/查数株数/排序规则）只认 CONSTRAINTS.md 与 schema.py，不从模板示例值反推。

## 改动后（全绿才算完成）

1. `python -m pytest tests/ -q` 全通过。
2. 涉及导出逻辑：双跑对比 + 勾稽断言——
   - 改动前 `tools/make_baseline.sh` 留基线，改动后重跑导出，
     `python tools/compare_export.py baseline/latest <新导出目录>` 全一致；
   - `python tools/check_invariants.py` 六项勾稽全 PASS；
   - 再人工抽查关键单元格（合格率数值、坐标全精度、A2 小班号、sheet 名）。
3. 涉及 GDB 上传：本地走一遍 zip→解压→图层分类→导入，确认错误消息可读（不允许静默吞异常）。
4. 涉及 Android：APK 只靠 GitHub Actions 重打（G2），验证新 APK 含改动（二进制 manifest/dex 搜字符串，不靠 grep 计数）。
5. 约束被推翻时：把旧约束移入 CONSTRAINTS.md「反转记录」，写明新规则与日期——不静默删除。

## 部署

- 部署只走 `./deploy.sh`；服务器环境事实见 CONSTRAINTS.md H 组。
- 部署后在生产验证一次导出与 GDB 上传，再宣布完成。
