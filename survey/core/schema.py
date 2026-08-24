# -*- coding: utf-8 -*-
"""五张验收表的字段定义 — 整个系统的唯一真相源。

高速路设计：
  - API 返回此 schema → 前端按 schema 动态渲染表单
  - exporter 按 schema 的 col 映射写入 xlsx 对应列
  - 测试按 schema 自动遍历每张表做校验
  - 新增/修改表只需改此文件，其余代码零改动

字段类型 (type):
  enum          单选下拉（options 必填）
  number        数值输入
  percent       百分比（存比率 0-1 不 ×100，如 0.95；前端输入 95 显示 95%，Excel 0.00% 格式）
  date          日期选择
  text          单行文本
  textarea      多行文本
  checkbox      有/无 切换
  gps           经纬度（一键获取）
  photo         拍照上传
  checkin       打卡（一键记录时间+GPS）
  track         轨迹（开始记录 / 上传 GPX）
  sample_array  样地子数组（一小班多条样地，存于 data_json.samples）
  computed      公式自动计算（只读，formula 指定计算函数名，前端自动算；
                store:false 表示仅显示不落库——如成活率等级/面积分派列，导出由模板公式承担）

数据模型（小班最小粒度，一对一）:
  三张验收表（人工造林/封山育林/退化林修复），每张表每个小班至多一条记录，
  唯一键 = (project_id, table_id, subcompartment_id)。
  样地等"一小班多值"数据作为该记录 data_json 内的数组字段（type=sample_array），
  不再独立成表/子表。水利水保/草原分类已下线（表4/表5 删除）。
"""
from collections import OrderedDict


# ════════════════════════════════════════════
# 小班信息 → 五表黄色列 字段映射（auto-map 标准化）
# ════════════════════════════════════════════
# key = 小班信息 xlsx 中的列名（经 normalize_column_name 标准化后）
#       例如 xlsx 列「州(市)」标准化后为「州」，「抚育面积(亩)」→「抚育面积」
# val = 五表 prefilled_columns 中的 key
SUBCOMPARTMENT_FIELD_MAP = {
    "州":         "city",                # 表1-4 州(市)（xlsx 必含，标准化后为「州」）
    "州（市）":   "city",                # GDB 全角括号列名
    "市":         "city",                # GDB 直名（2022-2024 年度矢量图层字段）
    "乡镇":       "township",            # 表1-5 乡/乡镇
    "乡":         "township",            # GDB 字段别名（与「乡镇」等价）
    "县":         "county",              # 表1-4 县
    "村":         "village",             # 表1-5 村
    "林班":       "forest_compartment",  # 表1/3 林班（表2 封山育林 GDB 无此字段）
    "小班":       "subcompartment_orig", # 小班：GDB 原值（可含汉字），仅导入/导出展示（F/G 列）
    "调查小班号": "subcompartment",      # 调查小班号：数字业务键（排序/标签/文件名/select 等全局使用）
    # 「小班原始」已废弃（2026-08-21）：与「小班」重复，新导入不再写入；
    # 旧数据行仍含此键，保留映射兼容读取（排在「小班」后，旧值优先生效）。
    "小班原始":   "subcompartment_orig",
    "土地权属":   "ownership",           # 表1-3 林地所有权
    "土地权":     "ownership",           # GDB 字段别名
    "林地所有权": "ownership",           # GDB 字段别名（直名）
    "造林树种":   "tree_species",        # 表1/2/3 造林树种（GDB 直名）
    "优势树":     "tree_species",        # GDB 字段别名
    "优势树种":   "dominant_species",    # 表2 优势树种（GDB 直名；表2 与造林树种并存两列）
    "抚育面积":   "reported_area",       # 表1-3 上报面积
    "上报面积":   "reported_area",       # GDB 字段别名（直名）
    "补植面积":   "replant_area",        # 表2/3 补植面积（GDB 直名）
    "验收类别":   "check_type",          # 表1 验收类别（GDB 直名）
    "计划年度":   "plan_year",           # 表1 计划年度（GDB 直名）
    "作业年度":   "work_year",           # 表1 作业年度（GDB 直名）
    "始封年度":   "start_year",          # 表2 始封年度（GDB 直名）
    "总需苗量":   "design_count",        # GDB 别名（需苗量+补植株数，兜底）
    "需苗量":     "design_count",        # 表1 小班设计株树（GDB 直名，导出 AQ；两者并存时需苗量优先，与模板示例一致）
    "小班设计株树": "design_count",      # GDB 直名（2022-2024 年度矢量图层字段）
    "封育对象":   "seal_target",         # 表2 封育对象（xlsx 可选列）
    "封育年限":   "seal_years",          # 表2 封育年限
    "封育类型":   "seal_type",           # 表2 封育类型
    "封育方式":   "seal_method",         # 表2 封育方式
    "封育措施":   "seal_measure",        # 表2 封育措施
    "育林措施":   "forest_measure",      # 表2 育林措施
    "封前地类":   "pre_land_type",       # 表2 封前地类（GDB 直名）
    "郁闭度":     "canopy_cover",        # 表2 郁闭度（GDB 直名）
    "修复措施":   "repair_measure",      # 表3 修复措施（GDB 直名）
    "修复方式":   "repair_method",       # 表3 修复方式（GDB 直名）
    "辅助措施":   "auxiliary_measure",   # 表3 辅助措施（GDB 直名）
    "小班面积":   "manage_area",         # 小班经营面积（别名）
    "项目名称":   "project_name",        # 表1-4 项目名称（来自项目信息 sheet）
}

# 反向映射：prefilled key → 小班信息列名
PREFILLED_TO_SUBCOMPARTMENT = {v: k for k, v in SUBCOMPARTMENT_FIELD_MAP.items()}

# 林班/小班号为正整数（历史数据可能存为 "1.0" 之类，映射时兜底转 int）
_UINT_PREFILLED_KEYS = {"forest_compartment", "subcompartment"}


def _to_uint(v):
    """转 unsigned int。空/None/无效/负数 → 0。"""
    if v is None or v == "":
        return 0
    try:
        f = float(v)
    except (ValueError, TypeError):
        return 0
    if f != f or f < 0:
        return 0
    return int(f)


# ════════════════════════════════════════════
# 小班扩展数据字段（打卡 / 轨迹 / 照片）
# ════════════════════════════════════════════
# 不属于五表录入列，独立展示在小班扩展面板。
# 归属小班（一个小班一组扩展数据）。
SUBCOMPARTMENT_EXTRA_FIELDS = [
    {
        "key": "checkin",
        "label": "打卡",
        "type": "checkin",
        "group": "外业凭证",
        "description": "记录到访时间和 GPS 坐标",
    },
    {
        "key": "track",
        "label": "轨迹",
        "type": "track",
        "group": "外业凭证",
        "description": "行走轨迹（可实时记录或上传 GPX）",
    },
    {
        "key": "photos",
        "label": "现场照片",
        "type": "photo",
        "group": "外业凭证",
        "multiple": True,
        "description": "带经纬度的现场照片（拍摄时自动嵌入 GPS）",
    },
]


def map_subcompartment_to_prefilled(sc_data):
    """把小班信息字段按映射表转成 prefilled_data。

    Args:
        sc_data: 小班信息原始字段 dict（key 为标准化后的列名）

    Returns:
        dict: {prefilled_key: value}，仅包含能映射上的字段。
              林班/小班号统一为 unsigned int。
    """
    result = {}
    for sc_col, pf_key in SUBCOMPARTMENT_FIELD_MAP.items():
        if sc_col in sc_data:
            val = sc_data[sc_col]
            if isinstance(val, str):
                val = val.strip()  # GDB 值可能为纯空格串（如造林树种 "  "）
            if val is None or val == "":
                continue
            if pf_key in _UINT_PREFILLED_KEYS:
                val = _to_uint(val)
            result[pf_key] = val
    return result

# ── 通用字段：验收人员/时间/备注（多表复用） ──
_INSPECTOR = {"key": "inspector", "label": "验收人员", "type": "text", "group": "验收", "required": True, "default": "", "col_span": "half"}
_INSPECT_TIME = {"key": "inspect_time", "label": "验收时间", "type": "date", "group": "验收", "required": True, "default": "today", "col_span": "half"}
_REMARK = {"key": "remark", "label": "备注", "type": "textarea", "group": "验收", "required": False, "default": "", "col_span": "full"}

# ── 通用管理情况 6 项（有/无 下拉，默认有；旧 checkbox 布尔数据导出时归一） ──
_MGMT_FIELDS = [
    {"key": "mgmt_design", "label": "作业设计", "type": "enum", "options": ["有", "无"], "default": "有", "group": "管理情况", "col_span": "half"},
    {"key": "mgmt_meeting", "label": "会议纪要", "type": "enum", "options": ["有", "无"], "default": "有", "group": "管理情况", "col_span": "half"},
    {"key": "mgmt_speech", "label": "讲话记录", "type": "enum", "options": ["有", "无"], "default": "有", "group": "管理情况", "col_span": "half"},
    {"key": "mgmt_survey", "label": "调研报告", "type": "enum", "options": ["有", "无"], "default": "有", "group": "管理情况", "col_span": "half"},
    {"key": "mgmt_supervision", "label": "监理报告", "type": "enum", "options": ["有", "无"], "default": "有", "group": "管理情况", "col_span": "half"},
    {"key": "mgmt_photo", "label": "图片", "type": "photo", "group": "管理情况", "required": False, "col_span": "half"},
]

# ── 调查完成标记（每表最后一列，默认否） ──
_SURVEY_DONE = {"key": "survey_completed", "label": "调查已经完成", "type": "enum", "options": ["是", "否"], "default": "否", "group": "验收", "required": False, "col_span": "half"}

# ── 样地调查（三表共用，存 data_json.samples；对应独立样地导出模板）──
# 样地号自动递增（auto：前端不渲染输入框，添加/删除时自动编号）；死亡株数=种植-成活
# 不录入（导出模板 E 列公式自动算）；坐标为样地 GPS 按钮一键获取；
# 照片仅存相册文件名（sample.photos），不上传、不参与统计运算。
_SAMPLES_FIELD = {
    "key": "samples",
    "label": "样地调查",
    "type": "sample_array",
    "group": "样地",
    "required": False,
    "sample_fields": [
        {"key": "no", "label": "样地号", "type": "number", "required": False, "auto": True},
        {"key": "area", "label": "样地面积(平方米)", "type": "number", "required": False},
        {"key": "planted", "label": "种植株数", "type": "number", "required": False},
        {"key": "alive", "label": "成活株数", "type": "number", "required": False},
        {"key": "remark", "label": "备注", "type": "text", "required": False},
        {"key": "x", "label": "坐标x(经度)", "type": "number", "required": False},
        {"key": "y", "label": "坐标y(纬度)", "type": "number", "required": False},
    ],
}

# ── 样地统计（computed，对应基本信息模板 苗木合格率 组：查数株数/合格株树/合格率）──
# 查数株数=调查总株数（样地模板 B34 同口径）=round(Σ种植÷个数÷150×网格面积×网格数量)；
# 合格率=Σ成活株数÷Σ种植株数×100；合格数=round(查数株数×合格率)
_SAMPLE_STATS = [
    {"key": "planted_total", "label": "小班查数株数", "type": "computed", "formula": "s_planted_total", "group": "苗木合格率", "col_span": "third"},
    {"key": "qualified_count", "label": "合格株树", "type": "computed", "formula": "s_qualified_count", "group": "苗木合格率", "col_span": "third"},
    {"key": "qualified_rate", "label": "合格率", "type": "computed", "formula": "s_qualified_rate", "group": "苗木合格率", "col_span": "third"},
]

# ── 打卡坐标（打卡时自动填充 GPS，只读展示；导出对应 表1 AV/AW）──
_CHECKIN_COORDS = [
    {"key": "sample_coord_x", "label": "打卡坐标x(经度)", "type": "number", "group": "打卡", "required": False, "readOnly": True, "col_span": "half"},
    {"key": "sample_coord_y", "label": "打卡坐标y(纬度)", "type": "number", "group": "打卡", "required": False, "readOnly": True, "col_span": "half"},
]

# ── 通用率类字段（施工率/建档率/管护率/抚育率） ──
def _rate_pair(key_area, key_rate, label_area, label_rate, group="管护抚育"):
    """面积 + 率 配对字段（手输）。key 保持英文（存储用），label 用中文（与 xlsx 模板表头一致）。"""
    return [
        {"key": key_area, "label": label_area, "type": "number", "group": group, "unit": "亩", "required": False, "col_span": "half"},
        {"key": key_rate, "label": label_rate, "type": "percent", "group": group, "required": False, "col_span": "half"},
    ]

# ── 合格分派派生字段（computed + store:false：前端实时计算显示，不落库；导出由
#    模板公式 =IF(合格率>=0.9,…) 承担，无录入值时公式保留、Excel 打开即计算）──
# 成活率等级三列（互斥）：合格率=Σ成活÷Σ种植（比率）——
#   ≥0.9 → 合格列显示合格率；0.4<x<0.9 → 待补植列；≤0.4 → 失败列；其余列空白
# 面积分派列：合格率 ≥0.9 → 上报面积原样填入；<0.9 → 留空
_DERIVED_SURVIVAL = [
    {"key": "survival_pass", "label": "成活率-合格", "type": "computed", "formula": "s_survival_pass", "store": False, "group": "成活率", "col_span": "third"},
    {"key": "survival_replant", "label": "成活率-待补植", "type": "computed", "formula": "s_survival_replant", "store": False, "group": "成活率", "col_span": "third"},
    {"key": "survival_fail", "label": "成活率-失败", "type": "computed", "formula": "s_survival_fail", "store": False, "group": "成活率", "col_span": "third"},
]
_DERIVED_QUALIFIED_AREA = {"key": "verified_pass", "label": "核实-合格面积", "type": "computed", "formula": "s_qualified_area", "store": False, "group": "核实面积", "unit": "亩", "col_span": "fifth"}

def _derived_area_pair(key_area, key_rate, label_area, label_rate, group="管护抚育"):
    """派生面积（computed 不落库，随合格率/上报面积联动）+ 率（手输 percent）配对字段。"""
    return [
        {"key": key_area, "label": label_area, "type": "computed", "formula": "s_qualified_area", "store": False, "group": group, "unit": "亩", "col_span": "half"},
        {"key": key_rate, "label": label_rate, "type": "percent", "group": group, "required": False, "col_span": "half"},
    ]


TABLES = [
    # ════════════════════════════════════════════
    # 表1 — 人工造林小班验收因子表
    # ════════════════════════════════════════════
    {
        "id": "table1",
        "name": "人工造林小班验收",
        "sheet_name": "表1－人工造林小班验收因子表",
        "description": "人工造林小班质量因子现场核查",
        "data_rows": 5,  # 模板预留数据行数
        "prefilled_columns": [
            {"key": "city", "label": "州(市)", "col": "A"},
            {"key": "subcompartment", "label": "调查小班号", "col": "B"},
            {"key": "county", "label": "县", "col": "C"},
            {"key": "township", "label": "乡", "col": "D"},
            {"key": "village", "label": "村", "col": "E"},
            {"key": "forest_compartment", "label": "林班", "col": "F"},
            {"key": "subcompartment_orig", "label": "小班", "col": "G"},
            {"key": "check_type", "label": "验收类别", "col": "H"},
            {"key": "project_name", "label": "项目名称", "col": "I"},
            {"key": "plan_year", "label": "计划年度", "col": "J"},
            {"key": "work_year", "label": "作业年度", "col": "K"},
            {"key": "ownership", "label": "林地所有权", "col": "L"},
            {"key": "tree_species", "label": "造林树种", "col": "M"},
            {"key": "reported_area", "label": "上报面积", "col": "N", "unit": "亩"},
            {"key": "design_count", "label": "小班设计株树", "col": "AQ"},  # GDB 需苗量/小班设计株树
        ],
        "input_columns": [
            # 成活率等级（派生：随合格率互斥分派，不落库）
            *_DERIVED_SURVIVAL,
            # 核实面积
            {"key": "verified_total", "label": "核实面积-计", "type": "number", "group": "核实面积", "unit": "亩", "col_span": "fifth"},
            _DERIVED_QUALIFIED_AREA,
            {"key": "verified_replant", "label": "核实-待补植面积", "type": "number", "group": "核实面积", "unit": "亩", "col_span": "fifth"},
            {"key": "verified_fail", "label": "核实-失败面积", "type": "number", "group": "核实面积", "unit": "亩", "col_span": "fifth"},
            {"key": "verified_loss", "label": "核实-损失面积", "type": "number", "group": "核实面积", "unit": "亩", "col_span": "fifth"},
            # 原因
            {"key": "area_short_reason", "label": "面积核实不足原因", "type": "text", "group": "原因", "col_span": "full"},
            {"key": "unqualified_reason", "label": "造林不合格原因", "type": "text", "group": "原因", "col_span": "half"},
            {"key": "loss_reason", "label": "损失原因", "type": "text", "group": "原因", "col_span": "half"},
        ] + _MGMT_FIELDS + _derived_area_pair("construction_area", "construction_rate", "符合设计的施工面积", "按作业设计施工率") + _derived_area_pair("archive_area", "archive_rate", "建档面积", "建档率") + _rate_pair("protect_area", "protect_rate", "管护面积", "管护率") + _derived_area_pair("tend_area", "tend_rate", "抚育面积", "抚育率") + [
            # ── 样地调查（弹窗录入，独立样地模板导出）──
            _SAMPLES_FIELD,
        ] + _SAMPLE_STATS + _CHECKIN_COORDS + [
            _INSPECTOR, _INSPECT_TIME, _REMARK,
            {"key": "co_inspector", "label": "配合验收人员", "type": "text", "group": "验收", "required": False, "default": "", "col_span": "half"},
            _SURVEY_DONE,
        ],
    },

    # ════════════════════════════════════════════
    # 表2 — 封山育林验收因子表
    # ════════════════════════════════════════════
    {
        "id": "table2",
        "name": "封山育林验收",
        "sheet_name": "表2－封山育林验收因子表",
        "description": "封山育林小班封育条件质量因子现场核查",
        "data_rows": 5,
        "prefilled_columns": [
            {"key": "city", "label": "州(市)", "col": "A"},
            {"key": "subcompartment", "label": "调查小班号", "col": "B"},
            {"key": "county", "label": "县", "col": "C"},
            {"key": "township", "label": "乡", "col": "D"},
            {"key": "village", "label": "村", "col": "E"},
            {"key": "subcompartment_orig", "label": "小班", "col": "F"},
            {"key": "check_type", "label": "验收类别", "col": "G"},
            {"key": "project_name", "label": "项目名称", "col": "H"},
            {"key": "plan_year", "label": "计划年度", "col": "I"},
            {"key": "start_year", "label": "始封年度", "col": "J"},
            {"key": "ownership", "label": "林地所有权", "col": "K"},
            {"key": "seal_target", "label": "封育对象", "col": "L"},
            {"key": "seal_years", "label": "封育年限", "col": "M"},
            {"key": "seal_type", "label": "封育类型", "col": "N"},
            {"key": "seal_method", "label": "封育方式", "col": "O"},
            {"key": "seal_measure", "label": "封育措施", "col": "P"},
            {"key": "forest_measure", "label": "育林措施", "col": "Q"},
            {"key": "tree_species", "label": "造林树种", "col": "R"},
            {"key": "reported_area", "label": "上报面积", "col": "S", "unit": "亩"},
            {"key": "replant_area", "label": "补植面积", "col": "T", "unit": "亩"},
            {"key": "dominant_species", "label": "优势树种", "col": "AF"},
            {"key": "pre_land_type", "label": "封前地类", "col": "AG"},
            {"key": "canopy_cover", "label": "郁闭度", "col": "AI"},
            {"key": "design_count", "label": "小班设计株树", "col": "BH"},
        ],
        "input_columns": [
            *_DERIVED_SURVIVAL,
            {"key": "verified_total", "label": "核实面积-计", "type": "number", "group": "核实面积", "unit": "亩", "col_span": "fifth"},
            _DERIVED_QUALIFIED_AREA,
            {"key": "verified_replant", "label": "核实-待补植面积", "type": "number", "group": "核实面积", "unit": "亩", "col_span": "fifth"},
            {"key": "verified_fail", "label": "核实-失败面积", "type": "number", "group": "核实面积", "unit": "亩", "col_span": "fifth"},
            {"key": "verified_loss", "label": "核实-损失面积", "type": "number", "group": "核实面积", "unit": "亩", "col_span": "fifth"},
            {"key": "area_short_reason", "label": "面积核实不足原因", "type": "text", "group": "原因", "col_span": "full"},
            {"key": "unqualified_reason", "label": "造林不合格原因", "type": "text", "group": "原因", "col_span": "half"},
            {"key": "loss_reason", "label": "损失原因", "type": "text", "group": "原因", "col_span": "half"},
            # 封育因子（现地类为现地调查录入；优势树种/封前地类/郁闭度为 GDB 绿色预填）
            {"key": "cur_land_type", "label": "现地类", "type": "text", "group": "封育因子", "col_span": "half"},
            # 针叶树株数
            {"key": "conifer_mother", "label": "针叶树-母树(株/亩)", "type": "number", "group": "株数调查", "col_span": "third"},
            {"key": "conifer_seedling", "label": "针叶树-幼苗(株/亩)", "type": "number", "group": "株数调查", "col_span": "third"},
            {"key": "conifer_sapling", "label": "针叶树-幼树(株/亩)", "type": "number", "group": "株数调查", "col_span": "third"},
            {"key": "broadleaf_mother", "label": "阔叶树-母树(株/亩)", "type": "number", "group": "株数调查", "col_span": "third"},
            {"key": "broadleaf_seedling", "label": "阔叶树-幼苗(株/亩)", "type": "number", "group": "株数调查", "col_span": "third"},
            {"key": "broadleaf_sapling", "label": "阔叶树-幼树(株/亩)", "type": "number", "group": "株数调查", "col_span": "third"},
            {"key": "bamboo_count", "label": "乔木根株或毛竹(株/亩)", "type": "number", "group": "株数调查", "col_span": "full"},
        ] + _MGMT_FIELDS + _derived_area_pair("construction_area", "construction_rate", "符合设计的施工面积", "按作业设计施工率") + _derived_area_pair("archive_area", "archive_rate", "建档面积", "建档率") + _rate_pair("protect_area", "protect_rate", "管护面积", "管护率") + _derived_area_pair("tend_area", "tend_rate", "抚育面积", "抚育率") + [
            # ── 样地调查（弹窗录入，独立样地模板导出）──
            _SAMPLES_FIELD,
        ] + _SAMPLE_STATS + _CHECKIN_COORDS + [
            _INSPECTOR, _INSPECT_TIME, _REMARK, _SURVEY_DONE,
        ],
    },

    # ════════════════════════════════════════════
    # 表3 — 退化林修复验收因子表
    # ════════════════════════════════════════════
    {
        "id": "table3",
        "name": "退化林修复验收",
        "sheet_name": "表3-退化林修复验收因子表",
        "description": "退化林修复小班质量因子现场核查",
        "data_rows": 6,
        "prefilled_columns": [
            {"key": "city", "label": "州(市)", "col": "A"},
            {"key": "subcompartment", "label": "调查小班号", "col": "B"},
            {"key": "county", "label": "县", "col": "C"},
            {"key": "township", "label": "乡", "col": "D"},
            {"key": "village", "label": "村", "col": "E"},
            {"key": "forest_compartment", "label": "林班", "col": "F"},
            {"key": "subcompartment_orig", "label": "小班", "col": "G"},
            {"key": "check_type", "label": "验收类别", "col": "H"},
            {"key": "project_name", "label": "项目名称", "col": "I"},
            {"key": "plan_year", "label": "计划年度", "col": "J"},
            {"key": "work_year", "label": "作业年度", "col": "K"},
            {"key": "ownership", "label": "林地所有权", "col": "L"},
            {"key": "tree_species", "label": "造林树种", "col": "M"},
            {"key": "reported_area", "label": "上报面积", "col": "N", "unit": "亩"},
            {"key": "replant_area", "label": "补植面积", "col": "O", "unit": "亩"},
            {"key": "repair_measure", "label": "修复措施", "col": "AA"},
            {"key": "repair_method", "label": "修复方式", "col": "AB"},
            {"key": "auxiliary_measure", "label": "辅助措施", "col": "AC"},
            {"key": "design_count", "label": "小班设计株树", "col": "AU"},
        ],
        "input_columns": [
            *_DERIVED_SURVIVAL,
            {"key": "verified_total", "label": "核实面积-计", "type": "number", "group": "核实面积", "unit": "亩", "col_span": "fifth"},
            _DERIVED_QUALIFIED_AREA,
            {"key": "verified_replant", "label": "核实-待补植面积", "type": "number", "group": "核实面积", "unit": "亩", "col_span": "fifth"},
            {"key": "verified_fail", "label": "核实-失败面积", "type": "number", "group": "核实面积", "unit": "亩", "col_span": "fifth"},
            {"key": "verified_loss", "label": "核实-损失面积", "type": "number", "group": "核实面积", "unit": "亩", "col_span": "fifth"},
            {"key": "area_short_reason", "label": "面积核实不足原因", "type": "text", "group": "原因", "col_span": "full"},
            {"key": "unqualified_reason", "label": "造林不合格原因", "type": "text", "group": "原因", "col_span": "half"},
            {"key": "loss_reason", "label": "损失原因", "type": "text", "group": "原因", "col_span": "half"},
        ] + _MGMT_FIELDS + _derived_area_pair("construction_area", "construction_rate", "符合设计的施工面积", "按作业设计施工率") + _derived_area_pair("archive_area", "archive_rate", "建档面积", "建档率") + _derived_area_pair("protect_area", "protect_rate", "管护面积", "管护率") + _derived_area_pair("tend_area", "tend_rate", "抚育面积", "抚育率") + [
            # ── 样地调查（弹窗录入，独立样地模板导出）──
            _SAMPLES_FIELD,
        ] + _SAMPLE_STATS + _CHECKIN_COORDS + [
            _INSPECTOR, _INSPECT_TIME, _REMARK, _SURVEY_DONE,
        ],
    },

]

# ── 索引：id → table dict ──
_TABLE_INDEX = {t["id"]: t for t in TABLES}


def get_table(table_id):
    """按 id 获取表定义。"""
    return _TABLE_INDEX.get(table_id)


def get_all_tables():
    """获取全部表定义（给前端用）。"""
    return TABLES


def get_input_fields(table_id):
    """获取某张表的所有可输入字段 flat list（含 sample_array 字段）。

    一对一模型下表无子表概念；sample_array 字段作为普通字段返回，
    其 sample_fields 供详情页渲染样方条目用。
    """
    table = get_table(table_id)
    if not table:
        return []
    return [dict(f) for f in table["input_columns"]]


def get_prefilled_fields(table_id):
    """获取某张表的预填字段（黄色列）。"""
    table = get_table(table_id)
    if not table:
        return []
    return table.get("prefilled_columns", [])


def get_field_groups(table_id):
    """获取某张表的字段分组列表（用于前端折叠面板）。"""
    fields = get_input_fields(table_id)
    seen = OrderedDict()
    for f in fields:
        g = f.get("group", "其他")
        if g not in seen:
            seen[g] = []
        seen[g].append(f)
    return list(seen.keys())


def _validate_single_field(f, val, errors):
    """校验单个普通字段（非 sample_array）。"""
    key = f["key"]
    # required 校验
    if f.get("required") and (val is None or val == ""):
        errors[key] = f"{f['label']}为必填项"
        return
    if val is None or val == "":
        return
    ftype = f.get("type")
    if ftype in ("number", "percent"):
        try:
            num = float(val)
            if f.get("min") is not None and num < f["min"]:
                errors[key] = f"{f['label']}不能小于{f['min']}"
            if f.get("max") is not None and num > f["max"]:
                errors[key] = f"{f['label']}不能大于{f['max']}"
            if ftype == "percent" and (num < 0 or num > 1):
                # 率类存比率 0-1（不 ×100，与 Excel 0.00% 百分比格式同语义）
                errors[key] = f"{f['label']}应在0-1之间（比率，如0.95）"
        except (ValueError, TypeError):
            errors[key] = f"{f['label']}应为数值"
    elif ftype == "enum":
        if f.get("options") and val not in f["options"]:
            errors[key] = f"{f['label']}取值无效"


def validate_row(table_id, row_data):
    """按 schema 校验一行数据，返回 (ok, errors)。

    一对一模型：每表一行，无子表。sample_array 字段校验其每个样方条目的
    sample_fields（必填/数值范围/百分比）。

    errors = {"field_key": "错误信息"}；样方错误用 "samples[i].field" 作 key。
    """
    table = get_table(table_id)
    if not table:
        return False, {"_": "表不存在"}
    errors = {}
    for f in table.get("input_columns", []):
        key = f["key"]
        val = row_data.get(key)
        if f.get("type") == "sample_array":
            # 样方子数组校验
            if not isinstance(val, list):
                continue
            sub_fields = f.get("sample_fields", [])
            for i, sample in enumerate(val):
                if not isinstance(sample, dict):
                    continue
                for sf in sub_fields:
                    sval = sample.get(sf["key"])
                    if sf.get("required") and (sval is None or sval == ""):
                        errors[f"samples[{i}].{sf['key']}"] = f"样方{i+1}：{sf['label']}为必填项"
                        continue
                    if sval is None or sval == "":
                        continue
                    sftype = sf.get("type")
                    if sftype in ("number", "percent"):
                        try:
                            num = float(sval)
                            if sf.get("min") is not None and num < sf["min"]:
                                errors[f"samples[{i}].{sf['key']}"] = f"样方{i+1}：{sf['label']}不能小于{sf['min']}"
                            if sf.get("max") is not None and num > sf["max"]:
                                errors[f"samples[{i}].{sf['key']}"] = f"样方{i+1}：{sf['label']}不能大于{sf['max']}"
                            if sftype == "percent" and (num < 0 or num > 1):
                                # 率类存比率 0-1（不 ×100）
                                errors[f"samples[{i}].{sf['key']}"] = f"样方{i+1}：{sf['label']}应在0-1之间（比率）"
                        except (ValueError, TypeError):
                            errors[f"samples[{i}].{sf['key']}"] = f"样方{i+1}：{sf['label']}应为数值"
        else:
            _validate_single_field(f, val, errors)
    return len(errors) == 0, errors
