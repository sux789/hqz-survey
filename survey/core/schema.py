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
  percent       百分比 0-100
  date          日期选择
  text          单行文本
  textarea      多行文本
  checkbox      有/无 切换
  gps           经纬度（一键获取）
  photo         拍照上传
  checkin       打卡（一键记录时间+GPS）
  track         轨迹（开始记录 / 上传 GPX）
  sample_array  样方子数组（一小班多条样方，存于 data_json.samples）
  computed      公式自动计算（只读，formula 指定计算函数名，前端自动算）

数据模型（小班最小粒度，一对一）:
  每张表每个小班至多一条记录，唯一键 = (project_id, table_id, subcompartment_id)。
  样方等"一小班多值"数据作为该记录 data_json 内的数组字段（type=sample_array），
  不再独立成表/子表。详见 docs/设计-小班一对一数据模型.md。
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
    "乡镇":       "township",            # 表1-5 乡/乡镇
    "乡":         "township",            # GDB 字段别名（与「乡镇」等价）
    "县":         "county",              # 表1-4 县
    "村":         "village",             # 表1-5 村
    "林班":       "forest_compartment",  # 表1/3/5 林班
    "小班":       "subcompartment",      # 表1-5 小班号
    "土地权属":   "ownership",           # 表1-3 林地所有权
    "土地权":     "ownership",           # GDB 字段别名
    "林地所有权": "ownership",           # GDB 字段别名（直名）
    "优势树种":   "tree_species",        # 表1 造林树种
    "优势树":     "tree_species",        # GDB 字段别名
    "造林树种":   "tree_species",        # GDB 字段别名（直名）
    "抚育面积":   "reported_area",       # 表1-3 上报面积
    "上报面积":   "reported_area",       # GDB 字段别名（直名）
    "验收类别":   "check_type",          # 表1 验收类别（GDB 直名）
    "计划年度":   "plan_year",           # 表1 计划年度（GDB 直名）
    "作业年度":   "work_year",           # 表1 作业年度（GDB 直名）
    "每亩面积":   "mu_area",             # 表1 样方只读列（GDB 直名）
    "每亩设计株树": "mu_design_count",   # 表1 样方只读列（GDB 直名）
    "封育对象":   "seal_target",         # 表2 封育对象（xlsx 可选列）
    "封育年限":   "seal_years",          # 表2 封育年限
    "封育类型":   "seal_type",           # 表2 封育类型
    "封育方式":   "seal_method",         # 表2 封育方式
    "封育措施":   "seal_measure",        # 表2 封育措施
    "育林措施":   "forest_measure",      # 表2 育林措施
    "小班面积":   "manage_area",         # 表5 小班经营面积
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

# ── 通用管理情况 6 项复选 ──
_MGMT_FIELDS = [
    {"key": "mgmt_design", "label": "作业设计", "type": "checkbox", "group": "管理情况", "default": True, "col_span": "half"},
    {"key": "mgmt_meeting", "label": "会议纪要", "type": "checkbox", "group": "管理情况", "default": True, "col_span": "half"},
    {"key": "mgmt_speech", "label": "讲话记录", "type": "checkbox", "group": "管理情况", "default": False, "col_span": "half"},
    {"key": "mgmt_survey", "label": "调研报告", "type": "checkbox", "group": "管理情况", "default": False, "col_span": "half"},
    {"key": "mgmt_supervision", "label": "监理报告", "type": "checkbox", "group": "管理情况", "default": False, "col_span": "half"},
    {"key": "mgmt_photo", "label": "图片", "type": "photo", "group": "管理情况", "required": False, "col_span": "half"},
]

# ── 通用率类字段（施工率/建档率/管护率/抚育率） ──
def _rate_pair(key_area, key_rate, label_area, label_rate, group="管护抚育"):
    """面积 + 率 配对字段。key 保持英文（存储用），label 用中文（与 xlsx 模板表头一致）。"""
    return [
        {"key": key_area, "label": label_area, "type": "number", "group": group, "unit": "亩", "required": False, "col_span": "half"},
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
            {"key": "county", "label": "县", "col": "B"},
            {"key": "township", "label": "乡", "col": "C"},
            {"key": "village", "label": "村", "col": "D"},
            {"key": "forest_compartment", "label": "林班", "col": "E"},
            {"key": "subcompartment", "label": "小班", "col": "F"},
            {"key": "check_type", "label": "验收类别", "col": "G"},
            {"key": "project_name", "label": "项目名称", "col": "H"},
            {"key": "plan_year", "label": "计划年度", "col": "I"},
            {"key": "work_year", "label": "作业年度", "col": "J"},
            {"key": "ownership", "label": "林地所有权", "col": "K"},
            {"key": "tree_species", "label": "造林树种", "col": "L"},
            {"key": "reported_area", "label": "上报面积", "col": "M", "unit": "亩"},
        ],
        "input_columns": [
            # 成活率等级
            {"key": "survival_pass", "label": "成活率-合格", "type": "percent", "group": "成活率", "col_span": "third"},
            {"key": "survival_replant", "label": "成活率-待补植", "type": "percent", "group": "成活率", "col_span": "third"},
            {"key": "survival_fail", "label": "成活率-失败", "type": "percent", "group": "成活率", "col_span": "third"},
            # 核实面积
            {"key": "verified_total", "label": "核实面积-计", "type": "number", "group": "核实面积", "unit": "亩", "col_span": "fifth"},
            {"key": "verified_pass", "label": "核实-合格面积", "type": "number", "group": "核实面积", "unit": "亩", "col_span": "fifth"},
            {"key": "verified_replant", "label": "核实-待补植面积", "type": "number", "group": "核实面积", "unit": "亩", "col_span": "fifth"},
            {"key": "verified_fail", "label": "核实-失败面积", "type": "number", "group": "核实面积", "unit": "亩", "col_span": "fifth"},
            {"key": "verified_loss", "label": "核实-损失面积", "type": "number", "group": "核实面积", "unit": "亩", "col_span": "fifth"},
            # 原因
            {"key": "area_short_reason", "label": "面积核实不足原因", "type": "text", "group": "原因", "col_span": "full"},
            {"key": "unqualified_reason", "label": "造林不合格原因", "type": "text", "group": "原因", "col_span": "half"},
            {"key": "loss_reason", "label": "损失原因", "type": "text", "group": "原因", "col_span": "half"},
        ] + _MGMT_FIELDS + _rate_pair("construction_area", "construction_rate", "符合设计的施工面积", "按作业设计施工率") + _rate_pair("archive_area", "archive_rate", "建档面积", "建档率") + _rate_pair("protect_area", "protect_rate", "管护面积", "管护率") + _rate_pair("tend_area", "tend_rate", "抚育面积", "抚育率") + [
            # ── 样方调查（宽表 1:1，参考 AT3:BJ3）──
            # 调查样地号/每亩面积/每亩设计株树 从密点文件读取（prefilled，只读）
            # 样1~样5 成活株树为输入；样地数量/平均成活株树/小班平均成活率为公式自动计算
            {"key": "sample_plot_no", "label": "调查样地号", "type": "text", "group": "样方", "required": False, "readOnly": True, "col_span": "half"},
            {"key": "sample_coord_x", "label": "样地坐标x", "type": "number", "group": "样方", "required": False, "col_span": "half"},
            {"key": "sample_coord_y", "label": "样地坐标y", "type": "number", "group": "样方", "required": False, "col_span": "half"},
            {"key": "sample_area", "label": "样地面积(m2)", "type": "number", "group": "样方", "required": False, "col_span": "half"},
            {"key": "survival_1", "label": "样地成活株树-样1", "type": "number", "group": "样方", "required": False, "col_span": "fifth"},
            {"key": "survival_2", "label": "样地成活株树-样2", "type": "number", "group": "样方", "required": False, "col_span": "fifth"},
            {"key": "survival_3", "label": "样地成活株树-样3", "type": "number", "group": "样方", "required": False, "col_span": "fifth"},
            {"key": "survival_4", "label": "样地成活株树-样4", "type": "number", "group": "样方", "required": False, "col_span": "fifth"},
            {"key": "survival_5", "label": "样地成活株树-样5", "type": "number", "group": "样方", "required": False, "col_span": "fifth"},
            {"key": "sample_count", "label": "样地数量", "type": "computed", "formula": "t1_sample_count", "group": "样方", "col_span": "half"},
            {"key": "avg_survival", "label": "平均样地成活株树", "type": "computed", "formula": "t1_avg_survival", "group": "样方", "col_span": "half"},
            {"key": "mu_area", "label": "每亩面积", "type": "number", "group": "样方", "required": False, "readOnly": True, "col_span": "half"},
            {"key": "mu_design_count", "label": "每亩设计株树", "type": "number", "group": "样方", "required": False, "readOnly": True, "col_span": "half"},
            {"key": "avg_survival_rate", "label": "小班平均成活率", "type": "computed", "formula": "t1_avg_survival_rate", "group": "样方", "col_span": "half"},
            {"key": "forest_ratio", "label": "造林比例", "type": "percent", "group": "样方", "required": False, "col_span": "half"},
            {"key": "preserve_rate", "label": "保存率", "type": "percent", "group": "样方", "required": False, "col_span": "half"},
            {"key": "sample_remark", "label": "备注1", "type": "text", "group": "样方", "required": False, "col_span": "full"},
            _INSPECTOR, _INSPECT_TIME, _REMARK,
            {"key": "co_inspector", "label": "配合验收人员", "type": "text", "group": "验收", "required": False, "default": "", "col_span": "half"},
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
            {"key": "county", "label": "县", "col": "B"},
            {"key": "township", "label": "乡", "col": "C"},
            {"key": "village", "label": "村", "col": "D"},
            {"key": "subcompartment", "label": "小班", "col": "E"},
            {"key": "check_type", "label": "验收类别", "col": "F"},
            {"key": "project_name", "label": "项目名称", "col": "G"},
            {"key": "plan_year", "label": "计划年度", "col": "H"},
            {"key": "start_year", "label": "始封年度", "col": "I"},
            {"key": "ownership", "label": "林地所有权", "col": "J"},
            {"key": "seal_target", "label": "封育对象", "col": "K"},
            {"key": "seal_years", "label": "封育年限", "col": "L"},
            {"key": "seal_type", "label": "封育类型", "col": "M"},
            {"key": "seal_method", "label": "封育方式", "col": "N"},
            {"key": "seal_measure", "label": "封育措施", "col": "O"},
            {"key": "forest_measure", "label": "育林措施", "col": "P"},
            {"key": "reported_area", "label": "上报面积", "col": "Q", "unit": "亩"},
        ],
        "input_columns": [
            {"key": "survival_pass", "label": "成活率-合格", "type": "percent", "group": "成活率", "col_span": "third"},
            {"key": "survival_replant", "label": "成活率-待补植", "type": "percent", "group": "成活率", "col_span": "third"},
            {"key": "survival_fail", "label": "成活率-失败", "type": "percent", "group": "成活率", "col_span": "third"},
            {"key": "verified_total", "label": "核实面积-计", "type": "number", "group": "核实面积", "unit": "亩", "col_span": "fifth"},
            {"key": "verified_pass", "label": "核实-合格面积", "type": "number", "group": "核实面积", "unit": "亩", "col_span": "fifth"},
            {"key": "verified_replant", "label": "核实-待补植面积", "type": "number", "group": "核实面积", "unit": "亩", "col_span": "fifth"},
            {"key": "verified_fail", "label": "核实-失败面积", "type": "number", "group": "核实面积", "unit": "亩", "col_span": "fifth"},
            {"key": "verified_loss", "label": "核实-损失面积", "type": "number", "group": "核实面积", "unit": "亩", "col_span": "fifth"},
            {"key": "area_short_reason", "label": "面积核实不足原因", "type": "text", "group": "原因", "col_span": "full"},
            {"key": "unqualified_reason", "label": "造林不合格原因", "type": "text", "group": "原因", "col_span": "half"},
            {"key": "loss_reason", "label": "损失原因", "type": "text", "group": "原因", "col_span": "half"},
            # 封育因子
            {"key": "dominant_species", "label": "优势树种(组)", "type": "text", "group": "封育因子", "col_span": "half"},
            {"key": "pre_land_type", "label": "封前地类", "type": "text", "group": "封育因子", "col_span": "half"},
            {"key": "cur_land_type", "label": "现地类", "type": "text", "group": "封育因子", "col_span": "half"},
            {"key": "canopy_cover", "label": "郁闭度(覆盖度)", "type": "number", "group": "封育因子", "col_span": "half", "min": 0, "max": 1, "step": 0.01},
            # 针叶树株数
            {"key": "conifer_mother", "label": "针叶树-母树(株/亩)", "type": "number", "group": "株数调查", "col_span": "third"},
            {"key": "conifer_seedling", "label": "针叶树-幼苗(株/亩)", "type": "number", "group": "株数调查", "col_span": "third"},
            {"key": "conifer_sapling", "label": "针叶树-幼树(株/亩)", "type": "number", "group": "株数调查", "col_span": "third"},
            {"key": "broadleaf_mother", "label": "阔叶树-母树(株/亩)", "type": "number", "group": "株数调查", "col_span": "third"},
            {"key": "broadleaf_seedling", "label": "阔叶树-幼苗(株/亩)", "type": "number", "group": "株数调查", "col_span": "third"},
            {"key": "broadleaf_sapling", "label": "阔叶树-幼树(株/亩)", "type": "number", "group": "株数调查", "col_span": "third"},
            {"key": "bamboo_count", "label": "乔木根株或毛竹(株/亩)", "type": "number", "group": "株数调查", "col_span": "full"},
        ] + _MGMT_FIELDS + _rate_pair("construction_area", "construction_rate", "符合设计的施工面积", "按作业设计施工率") + _rate_pair("archive_area", "archive_rate", "建档面积", "建档率") + _rate_pair("protect_area", "protect_rate", "管护面积", "管护率") + _rate_pair("tend_area", "tend_rate", "抚育面积", "抚育率") + [
            # ── 样方调查（宽表 1:1，参照 table1 结构）──
            {"key": "sample_plot_no", "label": "调查样地号", "type": "text", "group": "样方", "required": False, "readOnly": True, "col_span": "half"},
            {"key": "sample_coord_x", "label": "样地坐标x", "type": "number", "group": "样方", "required": False, "col_span": "half"},
            {"key": "sample_coord_y", "label": "样地坐标y", "type": "number", "group": "样方", "required": False, "col_span": "half"},
            {"key": "sample_area", "label": "样地面积(m2)", "type": "number", "group": "样方", "required": False, "col_span": "half"},
            {"key": "survival_1", "label": "样地成活株树-样1", "type": "number", "group": "样方", "required": False, "col_span": "fifth"},
            {"key": "survival_2", "label": "样地成活株树-样2", "type": "number", "group": "样方", "required": False, "col_span": "fifth"},
            {"key": "survival_3", "label": "样地成活株树-样3", "type": "number", "group": "样方", "required": False, "col_span": "fifth"},
            {"key": "survival_4", "label": "样地成活株树-样4", "type": "number", "group": "样方", "required": False, "col_span": "fifth"},
            {"key": "survival_5", "label": "样地成活株树-样5", "type": "number", "group": "样方", "required": False, "col_span": "fifth"},
            {"key": "sample_count", "label": "样地数量", "type": "computed", "formula": "t1_sample_count", "group": "样方", "col_span": "half"},
            {"key": "avg_survival", "label": "平均样地成活株树", "type": "computed", "formula": "t1_avg_survival", "group": "样方", "col_span": "half"},
            {"key": "mu_area", "label": "每亩面积", "type": "number", "group": "样方", "required": False, "readOnly": True, "col_span": "half"},
            {"key": "mu_design_count", "label": "每亩设计株树", "type": "number", "group": "样方", "required": False, "readOnly": True, "col_span": "half"},
            {"key": "avg_survival_rate", "label": "小班平均成活率", "type": "computed", "formula": "t1_avg_survival_rate", "group": "样方", "col_span": "half"},
            {"key": "forest_ratio", "label": "造林比例", "type": "percent", "group": "样方", "required": False, "col_span": "half"},
            {"key": "preserve_rate", "label": "保存率", "type": "percent", "group": "样方", "required": False, "col_span": "half"},
            {"key": "sample_remark", "label": "备注1", "type": "text", "group": "样方", "required": False, "col_span": "full"},
            _INSPECTOR, _INSPECT_TIME, _REMARK,
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
            {"key": "county", "label": "县", "col": "B"},
            {"key": "township", "label": "乡", "col": "C"},
            {"key": "village", "label": "村", "col": "D"},
            {"key": "forest_compartment", "label": "林班", "col": "E"},
            {"key": "subcompartment", "label": "小班", "col": "F"},
            {"key": "check_type", "label": "验收类别", "col": "G"},
            {"key": "project_name", "label": "项目名称", "col": "H"},
            {"key": "plan_year", "label": "计划年度", "col": "I"},
            {"key": "work_year", "label": "作业年度", "col": "J"},
            {"key": "ownership", "label": "林地所有权", "col": "K"},
            {"key": "reported_area", "label": "上报面积", "col": "L", "unit": "亩"},
        ],
        "input_columns": [
            {"key": "survival_pass", "label": "成活率-合格", "type": "percent", "group": "成活率", "col_span": "third"},
            {"key": "survival_replant", "label": "成活率-待补植", "type": "percent", "group": "成活率", "col_span": "third"},
            {"key": "survival_fail", "label": "成活率-失败", "type": "percent", "group": "成活率", "col_span": "third"},
            {"key": "verified_total", "label": "核实面积-计", "type": "number", "group": "核实面积", "unit": "亩", "col_span": "fifth"},
            {"key": "verified_pass", "label": "核实-合格面积", "type": "number", "group": "核实面积", "unit": "亩", "col_span": "fifth"},
            {"key": "verified_replant", "label": "核实-待补植面积", "type": "number", "group": "核实面积", "unit": "亩", "col_span": "fifth"},
            {"key": "verified_fail", "label": "核实-失败面积", "type": "number", "group": "核实面积", "unit": "亩", "col_span": "fifth"},
            {"key": "verified_loss", "label": "核实-损失面积", "type": "number", "group": "核实面积", "unit": "亩", "col_span": "fifth"},
            {"key": "area_short_reason", "label": "面积核实不足原因", "type": "text", "group": "原因", "col_span": "full"},
            {"key": "unqualified_reason", "label": "造林不合格原因", "type": "text", "group": "原因", "col_span": "half"},
            {"key": "loss_reason", "label": "损失原因", "type": "text", "group": "原因", "col_span": "half"},
            # 修复措施
            {"key": "repair_measure", "label": "修复措施", "type": "text", "group": "修复方式", "col_span": "full"},
            {"key": "repair_method", "label": "修复方式", "type": "text", "group": "修复方式", "col_span": "half"},
            {"key": "auxiliary_measure", "label": "辅助措施", "type": "text", "group": "修复方式", "col_span": "half"},
        ] + _MGMT_FIELDS + _rate_pair("construction_area", "construction_rate", "符合设计的施工面积", "按作业设计施工率") + _rate_pair("archive_area", "archive_rate", "建档面积", "建档率") + _rate_pair("protect_area", "protect_rate", "管护面积", "管护率") + _rate_pair("tend_area", "tend_rate", "抚育面积", "抚育率") + [
            # ── 样方调查（宽表 1:1，参照 table1 结构）──
            {"key": "sample_plot_no", "label": "调查样地号", "type": "text", "group": "样方", "required": False, "readOnly": True, "col_span": "half"},
            {"key": "sample_coord_x", "label": "样地坐标x", "type": "number", "group": "样方", "required": False, "col_span": "half"},
            {"key": "sample_coord_y", "label": "样地坐标y", "type": "number", "group": "样方", "required": False, "col_span": "half"},
            {"key": "sample_area", "label": "样地面积(m2)", "type": "number", "group": "样方", "required": False, "col_span": "half"},
            {"key": "survival_1", "label": "样地成活株树-样1", "type": "number", "group": "样方", "required": False, "col_span": "fifth"},
            {"key": "survival_2", "label": "样地成活株树-样2", "type": "number", "group": "样方", "required": False, "col_span": "fifth"},
            {"key": "survival_3", "label": "样地成活株树-样3", "type": "number", "group": "样方", "required": False, "col_span": "fifth"},
            {"key": "survival_4", "label": "样地成活株树-样4", "type": "number", "group": "样方", "required": False, "col_span": "fifth"},
            {"key": "survival_5", "label": "样地成活株树-样5", "type": "number", "group": "样方", "required": False, "col_span": "fifth"},
            {"key": "sample_count", "label": "样地数量", "type": "computed", "formula": "t1_sample_count", "group": "样方", "col_span": "half"},
            {"key": "avg_survival", "label": "平均样地成活株树", "type": "computed", "formula": "t1_avg_survival", "group": "样方", "col_span": "half"},
            {"key": "mu_area", "label": "每亩面积", "type": "number", "group": "样方", "required": False, "readOnly": True, "col_span": "half"},
            {"key": "mu_design_count", "label": "每亩设计株树", "type": "number", "group": "样方", "required": False, "readOnly": True, "col_span": "half"},
            {"key": "avg_survival_rate", "label": "小班平均成活率", "type": "computed", "formula": "t1_avg_survival_rate", "group": "样方", "col_span": "half"},
            {"key": "forest_ratio", "label": "造林比例", "type": "percent", "group": "样方", "required": False, "col_span": "half"},
            {"key": "preserve_rate", "label": "保存率", "type": "percent", "group": "样方", "required": False, "col_span": "half"},
            {"key": "sample_remark", "label": "备注1", "type": "text", "group": "样方", "required": False, "col_span": "full"},
            _INSPECTOR, _INSPECT_TIME, _REMARK,
        ],
    },

    # ════════════════════════════════════════════
    # 表4 — 水利水保设施验收因子表
    # ════════════════════════════════════════════
    {
        "id": "table4",
        "name": "水利水保设施验收",
        "sheet_name": "表4-水利水保设施验收因子表",
        "description": "小型水利水保设施质量抽查因子现场核查",
        "data_rows": 5,
        "prefilled_columns": [
            {"key": "city", "label": "州(市)", "col": "A"},
            {"key": "county", "label": "县", "col": "B"},
            {"key": "township", "label": "乡", "col": "C"},
            {"key": "village", "label": "村", "col": "D"},
            {"key": "check_type", "label": "验收类别", "col": "E"},
            {"key": "project_name", "label": "项目名称", "col": "F"},
            {"key": "plan_year", "label": "计划年度", "col": "G"},
            {"key": "work_year", "label": "作业年度", "col": "H"},
        ],
        "input_columns": [
            # 6组 设计/验收/是否一致
            {"key": "radiation_design", "label": "辐射小班-设计", "type": "text", "group": "验收抽查内容", "col_span": "third"},
            {"key": "radiation_actual", "label": "辐射小班-验收", "type": "text", "group": "验收抽查内容", "col_span": "third"},
            {"key": "radiation_match", "label": "辐射小班-是否一致", "type": "enum", "options": ["一致", "不一致"], "group": "验收抽查内容", "col_span": "third"},
            {"key": "content_design", "label": "建设内容-设计", "type": "text", "group": "验收抽查内容", "col_span": "third"},
            {"key": "content_actual", "label": "建设内容-验收", "type": "text", "group": "验收抽查内容", "col_span": "third"},
            {"key": "content_match", "label": "建设内容-是否一致", "type": "enum", "options": ["一致", "不一致"], "group": "验收抽查内容", "col_span": "third"},
            {"key": "scale_design", "label": "建设规模-设计", "type": "text", "group": "验收抽查内容", "col_span": "third"},
            {"key": "scale_actual", "label": "建设规模-验收", "type": "text", "group": "验收抽查内容", "col_span": "third"},
            {"key": "scale_match", "label": "建设规模-是否一致", "type": "enum", "options": ["一致", "不一致"], "group": "验收抽查内容", "col_span": "third"},
            {"key": "location_design", "label": "建设位置-设计", "type": "text", "group": "验收抽查内容", "col_span": "third"},
            {"key": "location_actual", "label": "建设位置-验收", "type": "text", "group": "验收抽查内容", "col_span": "third"},
            {"key": "location_match", "label": "建设位置-是否一致", "type": "enum", "options": ["一致", "不一致"], "group": "验收抽查内容", "col_span": "third"},
            {"key": "material_design", "label": "建设材料-设计", "type": "text", "group": "验收抽查内容", "col_span": "third"},
            {"key": "material_actual", "label": "建设材料-验收", "type": "text", "group": "验收抽查内容", "col_span": "third"},
            {"key": "material_match", "label": "建设材料-是否一致", "type": "enum", "options": ["一致", "不一致"], "group": "验收抽查内容", "col_span": "third"},
            {"key": "irrigation_design", "label": "灌溉面积-设计", "type": "number", "group": "验收抽查内容", "unit": "亩", "col_span": "third"},
            {"key": "irrigation_actual", "label": "灌溉面积-验收", "type": "number", "group": "验收抽查内容", "unit": "亩", "col_span": "third"},
            {"key": "irrigation_match", "label": "灌溉面积-是否一致", "type": "enum", "options": ["一致", "不一致"], "group": "验收抽查内容", "col_span": "third"},
            {"key": "qualified", "label": "是否合格", "type": "enum", "options": ["合格", "不合格"], "group": "验收结论", "required": True, "col_span": "full"},
            _INSPECTOR, _INSPECT_TIME, _REMARK,
        ],
    },

    # ════════════════════════════════════════════
    # 表5 — 草原现场验收表（小班级字段 + 样方子数组）
    # 一对一模型：每小班一行；样方收编为 samples 子数组（type=sample_array）。
    # 原子表 table5a 字段 → table5 顶层字段；table5b 字段 → samples.sample_fields。
    # ════════════════════════════════════════════
    {
        "id": "table5",
        "name": "草原现场验收",
        "sheet_name": "表5-草原现场验收表",
        "description": "草原现场验收（小班级调查 + 样方级调查）",
        "data_rows": 5,
        "prefilled_columns": [
            {"key": "city", "label": "州(市)", "col": "A"},
            {"key": "county", "label": "县", "col": "B"},
            {"key": "township", "label": "乡镇", "col": "C"},
            {"key": "village", "label": "村", "col": "D"},
            {"key": "subcompartment", "label": "小班号", "col": "E"},
            {"key": "manage_area", "label": "小班经营面积", "col": "F", "unit": "亩"},
        ],
        "input_columns": [
            # ── 小班级字段（原 table5a）──
            {"key": "verified_area", "label": "核实面积", "type": "number", "group": "基本", "unit": "亩", "required": True, "col_span": "half"},
            {"key": "area_score", "label": "面积得分", "type": "number", "group": "基本", "min": 0, "max": 100, "col_span": "half"},
            {"key": "sow_score", "label": "播种完成情况得分", "type": "number", "group": "得分", "min": 0, "max": 100, "col_span": "third"},
            {"key": "land_prep_score", "label": "整地完成情况得分", "type": "number", "group": "得分", "min": 0, "max": 100, "col_span": "third"},
            {"key": "weed_score", "label": "除杂完成情况得分", "type": "number", "group": "得分", "min": 0, "max": 100, "col_span": "third"},
            {"key": "fence_status", "label": "围栏建设情况", "type": "text", "group": "基本", "col_span": "full"},
            {"key": "completion_time", "label": "项目完成时间", "type": "date", "group": "基本", "col_span": "half"},
            {"key": "planned_species", "label": "计划种植品种", "type": "text", "group": "基本", "col_span": "half"},
            # ── 样方字段（1:1 宽表，原 table5b 字段平铺）──
            {"key": "sample_id", "label": "样方(线)编号", "type": "text", "group": "样方", "required": True, "col_span": "half"},
            {"key": "longitude", "label": "经度", "type": "number", "group": "样方", "col_span": "half"},
            {"key": "latitude", "label": "纬度", "type": "number", "group": "样方", "col_span": "half"},
            {"key": "seedling_species", "label": "出苗品种", "type": "text", "group": "样方", "col_span": "half"},
            {"key": "seedling_score", "label": "出苗品种得分", "type": "number", "group": "样方", "min": 0, "max": 100, "col_span": "half"},
            {"key": "actual_cover", "label": "实际盖度", "type": "percent", "group": "样方", "col_span": "half"},
            {"key": "cover_score", "label": "实际盖度得分", "type": "number", "group": "样方", "min": 0, "max": 100, "col_span": "half"},
            {"key": "sample_score", "label": "样方(线)得分", "type": "number", "group": "样方", "min": 0, "max": 100, "col_span": "half"},
            {"key": "unqualified_reason", "label": "不合格原因", "type": "text", "group": "样方", "col_span": "full"},
            _INSPECTOR, _INSPECT_TIME, _REMARK,
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
            if ftype == "percent" and (num < 0 or num > 100):
                errors[key] = f"{f['label']}应在0-100之间"
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
                            if sftype == "percent" and (num < 0 or num > 100):
                                errors[f"samples[{i}].{sf['key']}"] = f"样方{i+1}：{sf['label']}应在0-100之间"
                        except (ValueError, TypeError):
                            errors[f"samples[{i}].{sf['key']}"] = f"样方{i+1}：{sf['label']}应为数值"
        else:
            _validate_single_field(f, val, errors)
    return len(errors) == 0, errors
