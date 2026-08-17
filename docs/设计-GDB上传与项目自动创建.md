# 设计：GDB 上传与项目自动创建

> 范围：本轮先实施「上传」。「调查显示」「导出」为后续两轮，本文仅顺带说明接口契约。

## 一、现状 vs 新需求

| 维度 | 现状 | 新需求 |
|---|---|---|
| 项目建立 | 前端手建项目 → 上传 GDB 关联 project_id | **不手建项目**，上传时从 GDB 读项目名自动建项目 |
| 目录遍历 | `rglob("*.gdb")` 递归全目录 | 遍历**一二层目录**找 .gdb |
| 图层筛选 | 全部图层都尝试导入 | 仅读含分类关键词的图层，**非分类图层提示不读取** |
| 项目名校验 | 按 project_id 检查重复 | **全局**检查：无项目名/不唯一/已上传 → 报错 |
| 分类来源 | 固定 table1~5 | 按项目实际含有的分类图层动态显示 |

## 二、分类关键词（沿用现有常量）

```
人工造林 / 封山育林 / 退化林修复 / 水利水保 / 草原
```

图层名**以这些关键词开头** → 归入对应分类。未命中的图层 → 列入 `skipped_layers` 返回提示「不读取」。

## 三、上传流程

```
用户上传 zip（无需 project_id）
    ↓
1. 解压到 data/gdb/<gid>/
    ↓
2. 遍历一二层目录，找出 .gdb 目录
   （只看 解压根目录 + 其下一级子目录，不深层递归）
    ↓
3. 对每个 .gdb：list_layers → 筛选分类图层
   分类图层 = 图层名命中 5 个关键词之一
   非分类图层 → skipped_layers[{layer, reason:"非分类图层"}]
    ↓
4. 从分类图层属性读项目名（read_project_name，别名匹配）
   收集所有分类图层的项目名 → project_names_seen
    ↓
5. 校验（任一失败 → 清理临时文件 → 返回错误）
   ① 无分类图层      → 错误："未找到分类图层（人工造林/封山育林/退化林修复/水利水保/草原）"
   ② 无项目名        → 错误："无法从图层属性读取项目名称"
   ③ 项目名不唯一    → 错误："图层间项目名称不一致：A vs B"
   ④ 项目已上传      → 错误："项目「X」已上传，请勿重复上传"（全局 project_name 查重）
    ↓
6. 自动创建 project（name = 读出的项目名）
    ↓
7. 按分类导入各图层小班 → subcompartment_rows
   （每行带 project_name + category + data_json 原始属性）
    ↓
8. 返回结果
   {
     project: {id, name},
     categories: ["人工造林","草原"],     // 该项目实际含有的分类
     layers: {图层名: {imported, category, project_name}},
     skipped_layers: [{layer, reason}],  // 非分类图层
     imported: 总数
   }
```

## 四、校验规则细则

| # | 条件 | 结果 | HTTP |
|---|---|---|---|
| 1 | 压缩包内无 .gdb 目录 | 错误 | 400 |
| 2 | 无分类图层（5 个关键词均未命中） | 错误 | 400 |
| 3 | 分类图层均读不到项目名 | 错误 | 400 |
| 4 | 多图层项目名不一致 | 错误 | 400 |
| 5 | 项目名全局已存在 | 错误 | 409 |
| 6 | 非分类图层存在 | **非错误**，列入 skipped_layers 返回 | 200 |

## 五、代码改动点

### 5.1 gdb.py

**改 `save_gdb_upload`**：解压后遍历一二层目录找 .gdb（替代 `rglob`）：

```python
def find_gdb_dirs(gdb_dir):
    """在一二层目录内找 .gdb 目录（不深层递归）。"""
    found = []
    # 第一层
    for p in gdb_dir.iterdir():
        if p.is_dir() and p.suffix == ".gdb":
            found.append(p)
        elif p.is_dir():
            # 第二层
            for p2 in p.iterdir():
                if p2.is_dir() and p2.suffix == ".gdb":
                    found.append(p2)
    return found
```

**新增 `scan_classified_layers(gdb_path)`**：扫描一个 .gdb 的所有图层，返回分类图层 + 非分类图层：

```python
def scan_classified_layers(gdb_path):
    """扫描 GDB 图层，按分类关键词筛选。

    Returns:
        {
          classified: [{name, category, row_count}],
          skipped: [{name, reason:"非分类图层"}]
        }
    """
```

### 5.2 storage.py

**新增 `project_name_exists_global(project_name)`**：全局查重（不再按 project_id）：

```python
def project_name_exists_global(project_name):
    """判断项目名是否全局已存在（任一 project 下有该 project_name）。"""
    if not project_name:
        return False
    conn = _connect()
    try:
        r = conn.execute(
            "SELECT 1 FROM subcompartment_rows WHERE project_name=? LIMIT 1",
            (project_name,)
        ).fetchone()
        return r is not None
    finally:
        conn.close()
```

### 5.3 admin/app.py

**重写 `api_gdb_upload`**：去掉 `project_id` 入参，改为自动读项目名建项目。

```
POST /admin/api/gdb/upload
  入参: file (zip)              ← 不再需要 project_id
  返回: {project, categories, layers, skipped_layers, imported}
```

流程：
1. 保存 zip → 解压 → `find_gdb_dirs` 找 .gdb
2. 对每个 .gdb `scan_classified_layers` 汇总
3. 读项目名 → 四项校验
4. `create_project(项目名, 上传人)` 自动建项目
5. `import_gdb_subcompartments` 按分类导入
6. 返回结果

### 5.4 前端 admin.js

上传页改造：
- 去掉「项目选择」下拉框（不再需要预建项目）
- 上传框只留「选择 zip 文件」+「上传」按钮
- 上传后展示结果卡片：项目名 + 分类清单 + 各分类小班数 + 非分类图层提示

## 六、后续两轮接口契约（本次不实施）

### 调查显示（第二轮）
- `GET /survey/api/projects` → 项目列表（自动创建的）
- `GET /survey/api/projects/<pid>/categories` → 该项目含有的分类清单
- `GET /survey/api/projects/<pid>/subcompartments?category=X` → 按分类筛选小班
- 前端：级联选择 项目→县→乡→村→林班→小班；选定小班后 Excel 两列表格（label|值）

### 导出（第三轮）
- `GET /survey/api/projects/<pid>/export` → 按项目导出 xlsx（含所有分类 sheet）

## 七、实施顺序

1. `gdb.py`：`find_gdb_dirs` + `scan_classified_layers`
2. `storage.py`：`project_name_exists_global`
3. `admin/app.py`：重写 `api_gdb_upload`
4. `admin.js`：上传页去项目选择、展示结果
5. 验证：上传一个含多分类的 zip → 检查自动建项目 + 分类导入 + 重复上传报错
