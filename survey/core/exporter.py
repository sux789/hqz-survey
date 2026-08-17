# -*- coding: utf-8 -*-
"""xlsx 导出器 — 按 schema 定义写入数据，与原模板格式兼容。

设计原则：
  - 完全由 schema 驱动，不硬编码列号
  - 黄色列优先从小班信息反查（通过 records.subcompartment_id），
    若无则回退到 prefilled 表（旧逻辑）
  - 白色列为录入数据
  - 每张表一个 sheet，sheet 名与原模板一致
  - 末尾追加「小班扩展数据」sheet（打卡/轨迹/照片）
"""
import base64
import io
import json
import shutil
from pathlib import Path

import openpyxl
from openpyxl.drawing.image import Image as XlImage
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from survey.core import schema as S
from survey.core import storage
from survey.core import gdb as GDB

# 模板文件路径（survey/templates/ 下，随 deploy.sh 同步）
_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_TABLE1_TEMPLATE = _TEMPLATES_DIR / "tpl-table1-人工造林.xlsx"

# 人工造林模板列 → schema key 映射（基于 tpl-人工造林-验收录入.xlsx 第3-4行表头）
# value = (schema_key, source)  source: prefilled/input/extra/formula_*
_TABLE1_TEMPLATE_COL_MAP = {
    'A': ('city', 'prefilled'),
    'B': (None, 'skip'),           # 调查小班号（模板有，schema 无）
    'C': ('county', 'prefilled'),
    'D': ('township', 'prefilled'),
    'E': ('village', 'prefilled'),
    'F': ('forest_compartment', 'prefilled'),
    'G': ('subcompartment', 'prefilled'),
    'H': ('check_type', 'prefilled'),
    'I': ('project_name', 'prefilled'),
    'J': ('plan_year', 'prefilled'),
    'K': ('work_year', 'prefilled'),
    'L': ('ownership', 'prefilled'),
    'M': ('tree_species', 'prefilled'),
    'N': ('reported_area', 'prefilled'),
    'O': ('survival_pass', 'input'),
    'P': ('survival_replant', 'input'),
    'Q': ('survival_fail', 'input'),
    'R': ('verified_total', 'input'),
    'S': ('verified_pass', 'input'),
    'T': ('verified_replant', 'input'),
    'U': ('verified_fail', 'input'),
    'V': ('verified_loss', 'input'),
    'W': ('area_short_reason', 'input'),
    'X': ('unqualified_reason', 'input'),
    'Y': ('loss_reason', 'input'),
    'Z': ('mgmt_design', 'input'),
    'AA': ('mgmt_meeting', 'input'),
    'AB': ('mgmt_speech', 'input'),
    'AC': ('mgmt_survey', 'input'),
    'AD': ('mgmt_supervision', 'input'),
    'AE': ('mgmt_photo', 'input'),
    'AF': ('construction_area', 'input'),
    'AG': ('construction_rate', 'input'),
    'AH': ('archive_area', 'input'),
    'AI': ('archive_rate', 'input'),
    'AJ': ('protect_area', 'input'),
    'AK': ('protect_rate', 'input'),
    'AL': ('tend_area', 'input'),
    'AM': ('tend_rate', 'input'),
    'AN': ('inspector_sign', 'input'),
    'AO': ('inspect_time', 'input'),
    'AP': ('remark', 'input'),
    'AQ': ('co_inspector_sign', 'input'),
    'AR': ('track', 'extra'),
    'AS': ('photos', 'extra'),
    'AT': ('sample_plot_no', 'input'),
    'AU': ('sample_count', 'formula_count'),
    'AV': ('sample_coord_x', 'input'),
    'AW': ('sample_coord_y', 'input'),
    'AX': ('sample_area', 'input'),
    'AY': ('survival_1', 'input'),
    'AZ': ('survival_2', 'input'),
    'BA': ('survival_3', 'input'),
    'BB': ('survival_4', 'input'),
    'BC': ('survival_5', 'input'),
    'BD': ('avg_survival', 'formula_avg'),
    'BE': ('mu_area', 'input'),
    'BF': ('mu_design_count', 'input'),
    'BG': ('avg_survival_rate', 'formula_rate'),
    'BH': ('forest_ratio', 'input'),
    'BI': ('preserve_rate', 'input'),
    'BJ': ('sample_remark', 'input'),
}

# 黄色填充（与原模板一致）
_YELLOW = PatternFill(start_color="FFFF00", end_color="FFFF00", fill_type="solid")
_HEADER_FILL = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
_HEADER_FONT = Font(name="宋体", size=10, bold=True, color="FFFFFF")
_DATA_FONT = Font(name="宋体", size=10)
_THIN_BORDER = Border(
    left=Side(style="thin"), right=Side(style="thin"),
    top=Side(style="thin"), bottom=Side(style="thin"),
)
_CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
_LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)


def _fmt_percent(val):
    """格式化百分比：85 → '85%'，85.5 → '85.5%'。"""
    try:
        num = float(val)
        return f"{num:g}%"
    except (ValueError, TypeError):
        return val


def _col_letter(n):
    """数字转 Excel 列字母：1→A, 27→AA"""
    result = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


def _col_number(letter):
    """Excel 列字母转数字：A→1, AA→27"""
    n = 0
    for c in letter.upper():
        n = n * 26 + (ord(c) - 64)
    return n


def _write_header(ws, table_def, start_col=1):
    """写表头行：预填列(黄色标记) + 录入列。"""
    col = start_col
    # 预填列表头
    prefilled = table_def.get("prefilled_columns", [])
    for f in prefilled:
        cell = ws.cell(row=1, column=col, value=f["label"])
        cell.fill = _YELLOW
        cell.font = _DATA_FONT
        cell.alignment = _CENTER
        cell.border = _THIN_BORDER
        col += 1
    # 录入列表头
    for f in table_def.get("input_columns", []):
        label = f["label"]
        if f.get("unit"):
            label += f"({f['unit']})"
        cell = ws.cell(row=1, column=col, value=label)
        cell.font = _DATA_FONT
        cell.alignment = _CENTER
        cell.border = _THIN_BORDER
        col += 1
    return col - 1  # 返回最后一列号


def _write_row(ws, row_num, table_def, prefilled_data, input_data, start_col=1):
    """写一行数据：预填 + 录入。"""
    col = start_col
    # 预填数据
    for f in table_def.get("prefilled_columns", []):
        val = prefilled_data.get(f["key"], "") if prefilled_data else ""
        cell = ws.cell(row=row_num, column=col, value=val)
        cell.fill = _YELLOW
        cell.font = _DATA_FONT
        cell.alignment = _CENTER
        cell.border = _THIN_BORDER
        col += 1
    # 录入数据
    for f in table_def.get("input_columns", []):
        val = input_data.get(f["key"], "") if input_data else ""
        ftype = f.get("type")
        # sample_array 主表只写样方数（明细展开到独立 sheet）
        if ftype == "sample_array":
            n = len(val) if isinstance(val, list) else 0
            val = f"样方数: {n}" if n else ""
        # checkbox 转 有/无
        if ftype == "checkbox":
            val = "有" if val else "无"
        # percent 加 %
        if ftype == "percent" and val != "" and val is not None:
            val = _fmt_percent(val)
        cell = ws.cell(row=row_num, column=col, value=val)
        cell.font = _DATA_FONT
        cell.alignment = _LEFT if ftype in ("text", "textarea") else _CENTER
        cell.border = _THIN_BORDER
        col += 1


def _write_samples_sheet(wb, table_def, sample_field, records, sc_cache):
    """把所有小班的样方展开为独立 sheet（每样方一行）。

    列 = 小班预填列 + 样方字段列；同一小班的多个样方各占一行。
    """
    sheet_name = (table_def["sheet_name"] + "-样方明细")[:31]
    ws = wb.create_sheet(title=sheet_name)
    # 表头：预填列 + 样方字段列
    col = 1
    for f in table_def.get("prefilled_columns", []):
        cell = ws.cell(row=1, column=col, value=f["label"])
        cell.fill = _YELLOW
        cell.font = _DATA_FONT
        cell.alignment = _CENTER
        cell.border = _THIN_BORDER
        col += 1
    for sf in sample_field.get("sample_fields", []):
        label = sf["label"]
        if sf.get("unit"):
            label += f"({sf['unit']})"
        cell = ws.cell(row=1, column=col, value=label)
        cell.font = _DATA_FONT
        cell.alignment = _CENTER
        cell.border = _THIN_BORDER
        col += 1
    last_col = col - 1
    # 数据行
    row_num = 2
    for rec in records:
        data = rec.get("data", {})
        samples = data.get(sample_field["key"], [])
        if not isinstance(samples, list) or not samples:
            continue
        prefilled_data = _get_prefilled_for_record(rec, sc_cache)
        for sample in samples:
            if not isinstance(sample, dict):
                continue
            col = 1
            # 预填列
            for f in table_def.get("prefilled_columns", []):
                val = prefilled_data.get(f["key"], "")
                cell = ws.cell(row=row_num, column=col, value=val)
                cell.fill = _YELLOW
                cell.font = _DATA_FONT
                cell.border = _THIN_BORDER
                col += 1
            # 样方字段列
            for sf in sample_field.get("sample_fields", []):
                val = sample.get(sf["key"], "")
                if sf.get("type") == "percent" and val != "" and val is not None:
                    val = _fmt_percent(val)
                cell = ws.cell(row=row_num, column=col, value=val)
                cell.font = _DATA_FONT
                cell.alignment = _LEFT if sf.get("type") in ("text", "textarea") else _CENTER
                cell.border = _THIN_BORDER
                col += 1
            row_num += 1
    # 列宽 + 冻结表头
    for col_num in range(1, last_col + 1):
        ws.column_dimensions[_col_letter(col_num)].width = 14
    ws.row_dimensions[1].height = 30
    ws.freeze_panes = "A2"


def _insert_sign_image(ws, anchor_cell, sign_b64, row_num):
    """把 base64 签名 PNG 插入单元格（失败静默，不影响导出）。"""
    if not sign_b64 or not str(sign_b64).startswith("data:image"):
        return
    try:
        b64data = str(sign_b64).split(",", 1)[1]
        img = XlImage(io.BytesIO(base64.b64decode(b64data)))
        img.width = 120
        img.height = 40
        ws.add_image(img, anchor_cell)
        cur = ws.row_dimensions[row_num].height or 0
        ws.row_dimensions[row_num].height = max(cur, 32)
    except Exception:
        pass


def _append_sign_columns(ws, table_def, records, last_col):
    """schema 驱动 sheet 末尾追加「验收签名/配合验收签名」两列并插入图片。

    签名存于调查记录 data 的 inspector_sign/co_inspector_sign（base64 PNG）。
    """
    if not records:
        return
    c1, c2 = last_col + 1, last_col + 2
    for col, label in ((c1, "验收签名"), (c2, "配合验收签名")):
        cell = ws.cell(row=1, column=col, value=label)
        cell.font = _DATA_FONT
        cell.alignment = _CENTER
        cell.border = _THIN_BORDER
        ws.column_dimensions[_col_letter(col)].width = 18
    for i, rec in enumerate(records):
        row_num = i + 2
        data = rec.get("data", {}) or {}
        _insert_sign_image(ws, f"{_col_letter(c1)}{row_num}", data.get("inspector_sign"), row_num)
        _insert_sign_image(ws, f"{_col_letter(c2)}{row_num}", data.get("co_inspector_sign"), row_num)


def _get_prefilled_for_record(rec, sc_cache):
    """获取一条记录对应的预填数据（黄色列）。

    一对一模型：预填数据由 records.subcompartment_id 反查小班信息，
    经 map_subcompartment_to_prefilled 实时映射（prefilled 表已停用）。

    Args:
        rec: 录入记录 dict
        sc_cache: {subcompartment_id: prefilled_data} 缓存
    """
    sc_id = rec.get("subcompartment_id") or ""
    if not sc_id:
        return {}
    if sc_id not in sc_cache:
        sc_row = storage.get_subcompartment_row(sc_id)
        sc_cache[sc_id] = S.map_subcompartment_to_prefilled(sc_row["data"]) if sc_row else {}
    return sc_cache[sc_id]


def export_project(pid, output_path=None):
    """导出某项目的全部数据为 xlsx。

    Args:
        pid: 项目 ID
        output_path: 输出路径，None 则返回 BytesIO

    Returns:
        (output_path, stats) 元组
    """
    project = storage.get_project(pid)
    if not project:
        raise ValueError(f"项目 {pid} 不存在")

    all_records = storage.get_all_records(pid)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # 删默认 sheet

    stats = {"project": project["name"], "tables": {}}
    # 小班信息反查缓存（按 subcompartment_id 缓存 prefilled_data）
    sc_cache = {}

    for table_def in S.get_all_tables():
        tid = table_def["id"]
        sheet_name = table_def["sheet_name"]
        # Excel sheet 名最长 31 字符
        if len(sheet_name) > 31:
            sheet_name = sheet_name[:31]
        ws = wb.create_sheet(title=sheet_name)

        # 一对一模型：所有表统一处理（无子表）。
        # sample_array 字段在 _write_row 中特判为主表写"样方数: N"，
        # 样方明细随后展开为独立 sheet。
        last_col = _write_header(ws, table_def)
        records = all_records.get(tid, [])
        for i, rec in enumerate(records):
            row_num = i + 2  # 数据从第 2 行开始
            data = rec.get("data", {})
            prefilled_data = _get_prefilled_for_record(rec, sc_cache)
            _write_row(ws, row_num, table_def, prefilled_data, data)
        # 签名图片列（验收签名/配合验收签名）
        _append_sign_columns(ws, table_def, records, last_col)
        # 列宽自适应
        for col_num in range(1, last_col + 1):
            ws.column_dimensions[_col_letter(col_num)].width = 14
        ws.row_dimensions[1].height = 30
        # 冻结表头
        ws.freeze_panes = "A2"
        stats["tables"][tid] = len(records)

        # 样方明细展开为独立 sheet（每样方一行，含小班预填列）
        sample_field = next((f for f in table_def.get("input_columns", [])
                             if f.get("type") == "sample_array"), None)
        if sample_field:
            _write_samples_sheet(wb, table_def, sample_field, records, sc_cache)

    # ── 追加「小班扩展数据」sheet ──
    _write_extras_sheet(wb, pid, sc_cache)

    if output_path is None:
        import io
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        return output, stats
    else:
        wb.save(str(output_path))
        return str(output_path), stats


def _write_extras_sheet(wb, pid, sc_cache):
    """追加「小班扩展数据」sheet：打卡/轨迹/照片。"""
    ws = wb.create_sheet(title="小班扩展数据")
    # 收集该项目下所有引用过的小班 ID
    sc_ids = set()
    all_records = storage.get_all_records(pid)
    for tid, recs in all_records.items():
        for r in recs:
            sid = r.get("subcompartment_id") or ""
            if sid:
                sc_ids.add(sid)
    # 也加上该项目所有批次下的小班（即使没录入记录）
    for batch in storage.list_batches(pid):
        for row in storage.list_subcompartment_rows(batch["id"]):
            sc_ids.add(row["id"])

    # 表头
    headers = ["小班ID", "州(市)", "乡镇", "村", "林班", "小班号",
               "打卡时间", "打卡经度", "打卡纬度",
               "轨迹点数", "轨迹JSON",
               "照片数", "照片列表"]
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=c, value=h)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = _CENTER
        cell.border = _THIN_BORDER
    ws.row_dimensions[1].height = 28

    # 数据行
    row_num = 2
    for sid in sc_ids:
        sc_row = storage.get_subcompartment_row(sid)
        if not sc_row:
            continue
        extras = storage.get_extras(sid)
        data = sc_row.get("data", {})
        photos = extras.get("photos", [])
        photo_names = ";".join(p.get("name", "") for p in photos) if photos else ""
        track = extras.get("track", [])
        track_json = json.dumps(track, ensure_ascii=False) if track else ""
        row_data = [
            sid,
            data.get("州", ""),
            sc_row.get("township", ""),
            sc_row.get("village", ""),
            sc_row.get("forest_compartment", ""),
            sc_row.get("subcompartment", ""),
            extras.get("checkin_at", ""),
            extras.get("checkin_lng", ""),
            extras.get("checkin_lat", ""),
            len(track),
            track_json,
            len(photos),
            photo_names,
        ]
        for c, v in enumerate(row_data, 1):
            cell = ws.cell(row=row_num, column=c, value=v)
            cell.font = _DATA_FONT
            cell.alignment = _LEFT if c in (11, 13) else _CENTER
            cell.border = _THIN_BORDER
        row_num += 1

    # 列宽
    widths = [14, 22, 10, 10, 8, 8, 18, 12, 12, 10, 30, 8, 30]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[_col_letter(i)].width = w
    ws.freeze_panes = "A2"


def export_project_name(project_name, pid, output_path=None):
    """按项目名称导出 xlsx：每个项目名称一份文件，按分类（前缀）分 sheet。

    一个分类 = 一个 sheet（有几个分类几个 sheet），sheet 名取分类关键词
    （人工造林/封山育林/退化林修复/水利水保/草原）；每 sheet 用对应
    schema 表的预填+录入列，并填入该分类下小班的调查记录。
    另追加「小班扩展数据」sheet（仅本项目名称下的小班）。

    Args:
        project_name: 项目名称（必填）
        pid: 项目 ID（必填，限定导出范围）
        output_path: 输出路径，None 则返回 BytesIO

    Returns:
        (output_path, stats)
    """
    sc_rows = storage.list_project_subcompartment_rows(pid, project_name=project_name)
    # 按分类分组
    by_cat = {}
    for r in sc_rows:
        cat = r.get("category") or "未分类"
        by_cat.setdefault(cat, []).append(r)

    sc_cache = {}
    stats = {"project_name": project_name, "sheets": {}}

    # 人工造林分类优先用模板导出（保留模板原样式/合并/列宽）
    has_table1_tpl = "人工造林" in by_cat and _TABLE1_TEMPLATE.exists()
    if has_table1_tpl:
        wb = openpyxl.load_workbook(_TABLE1_TEMPLATE)
        _fill_table1_template(wb, by_cat["人工造林"], pid, project_name, sc_cache)
        stats["sheets"]["人工造林"] = len(by_cat["人工造林"])
        del by_cat["人工造林"]
    else:
        wb = openpyxl.Workbook()
        wb.remove(wb.active)

    for cat, rows in by_cat.items():
        table_id = GDB.GDB_CATEGORY_TO_TABLE.get(cat)
        table_def = S.get_table(table_id) if table_id else _generic_table_def(cat)
        sheet_name = (cat or "未分类")[:31]
        ws = wb.create_sheet(title=sheet_name)
        last_col = _write_header(ws, table_def)
        # 该分类对应表的调查记录（按项目名称过滤）
        survey_recs = []
        survey_map = {}
        if table_id:
            for rec in storage.get_survey_rows(pid, table_id, project_name=project_name):
                survey_map[rec["subcompartment_id"]] = rec.get("data", {})
                survey_recs.append(rec)
        for i, r in enumerate(rows):
            prefilled_data = S.map_subcompartment_to_prefilled(r.get("data", {}))
            _write_row(ws, i + 2, table_def, prefilled_data, survey_map.get(r["id"], {}))
        # 签名图片列（验收签名/配合验收签名）
        _append_sign_columns(ws, table_def, survey_recs, last_col)
        for col_num in range(1, last_col + 1):
            ws.column_dimensions[_col_letter(col_num)].width = 14
        ws.row_dimensions[1].height = 30
        ws.freeze_panes = "A2"
        stats["sheets"][cat] = len(rows)
        # table5 样方明细展开
        sample_field = next((f for f in table_def.get("input_columns", [])
                             if f.get("type") == "sample_array"), None)
        if sample_field:
            _write_samples_sheet(wb, table_def, sample_field,
                                 [{"subcompartment_id": r["id"], "data": survey_map.get(r["id"], {})}
                                  for r in rows], sc_cache)

    # 小班扩展数据 sheet（仅本项目名称下小班）
    _write_extras_sheet_rows(wb, sc_rows, sc_cache)

    if output_path is None:
        import io
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        return output, stats
    wb.save(str(output_path))
    return str(output_path), stats


def _fill_table1_template(wb, rows, pid, project_name, sc_cache):
    """基于模板填充人工造林数据（保留模板原样式、合并、列宽）。

    模板结构：第1-2行标题，第3-4行表头（合并），第5行起数据。
    公式列 AU/BD/BG 写 Excel 公式，由表格软件自动计算。
    """
    ws = wb.active
    # 清除模板示例数据（第5行之后，仅清值保留样式）
    max_row = ws.max_row
    if max_row >= 5:
        for row in ws.iter_rows(min_row=5, max_row=max_row):
            for cell in row:
                cell.value = None

    # 获取调查记录
    survey_map = {}
    for rec in storage.get_survey_rows(pid, "table1", project_name=project_name):
        survey_map[rec["subcompartment_id"]] = rec.get("data", {})

    for i, sc_row in enumerate(rows):
        row_num = i + 5  # 数据从第5行开始
        prefilled_data = S.map_subcompartment_to_prefilled(sc_row.get("data", {}))
        input_data = survey_map.get(sc_row["id"], {})
        extras = storage.get_extras(sc_row["id"])

        for col_letter, (key, source) in _TABLE1_TEMPLATE_COL_MAP.items():
            if source == 'skip':
                continue
            col_num = _col_number(col_letter)
            val = None

            if source == 'prefilled':
                val = prefilled_data.get(key, "")
            elif source == 'input':
                # 签名字段单独处理（插入图片）
                if key in ('inspector_sign', 'co_inspector_sign'):
                    continue
                val = input_data.get(key, "")
                # 只读样方列（每亩面积/每亩设计株树）回退预填值
                if key in ('mu_area', 'mu_design_count') and val in ("", None):
                    val = prefilled_data.get(key, "")
                ftype = None
                # 查 schema 字段类型以做格式转换
                for f in S.get_table("table1")["input_columns"]:
                    if f["key"] == key:
                        ftype = f.get("type")
                        break
                if ftype == "checkbox":
                    val = "有" if val else "无"
                elif ftype == "percent" and val not in ("", None):
                    val = _fmt_percent(val)
            elif source == 'extra':
                if key == 'track':
                    track = extras.get("track", [])
                    val = f"{len(track)}点" if track else ""
                elif key == 'photos':
                    # 照片列写文件名列表（分号分隔），与相册文件对应
                    photos = extras.get("photos", [])
                    val = ";".join(p.get("name", "") for p in photos if p.get("name")) if photos else ""
            elif source == 'formula_count':
                # AU 样地数量 = COUNT(AY:BC)
                val = f"=COUNT(AY{row_num}:BC{row_num})"
            elif source == 'formula_avg':
                # BD 平均样地成活株树 = SUM(AY:BC)/AU
                val = f'=IF(AU{row_num}=0,"",SUM(AY{row_num}:BC{row_num})/AU{row_num})'
            elif source == 'formula_rate':
                # BG 小班平均成活率 = (BD*BE)/(AX*BF)
                val = f'=IF(OR(AX{row_num}=0,BF{row_num}=0),"",(BD{row_num}*BE{row_num})/(AX{row_num}*BF{row_num}))'

            if val is not None and val != "":
                ws.cell(row=row_num, column=col_num, value=val)

        # 插入签名图片到 AN/AQ 单元格
        for col_letter, sign_key in (('AN', 'inspector_sign'), ('AQ', 'co_inspector_sign')):
            sign_b64 = input_data.get(sign_key, "")
            if sign_b64 and sign_b64.startswith('data:image'):
                try:
                    # 解码 base64
                    header, b64data = sign_b64.split(',', 1)
                    img_bytes = base64.b64decode(b64data)
                    img = XlImage(io.BytesIO(img_bytes))
                    # 缩放到适合单元格（宽约 120px，高约 40px）
                    img.width = 120
                    img.height = 40
                    anchor = f"{col_letter}{row_num}"
                    ws.add_image(img, anchor)
                    # 调整行高以容纳签名
                    ws.row_dimensions[row_num].height = max(
                        ws.row_dimensions[row_num].height or 0, 32
                    )
                except Exception:
                    # 图片插入失败不影响其他数据
                    pass

    # 冻结表头（第5行起可滚动）
    ws.freeze_panes = "A5"


def _generic_table_def(cat):
    """未知分类的兜底表定义（仅基础列，无录入列）。"""
    return {
        "id": "generic",
        "name": cat or "未分类",
        "sheet_name": cat or "未分类",
        "description": "",
        "data_rows": 0,
        "prefilled_columns": [
            {"key": "township", "label": "乡镇", "col": "A"},
            {"key": "village", "label": "村", "col": "B"},
            {"key": "forest_compartment", "label": "林班", "col": "C"},
            {"key": "subcompartment", "label": "小班", "col": "D"},
        ],
        "input_columns": [],
    }


def _write_extras_sheet_rows(wb, sc_rows, sc_cache):
    """按给定小班行列表追加「小班扩展数据」sheet（打卡/轨迹/照片）。

    与 _write_extras_sheet 类似，但范围限定为传入的小班行（按项目名称导出用）。
    """
    ws = wb.create_sheet(title="小班扩展数据")
    headers = ["小班ID", "州(市)", "乡镇", "村", "林班", "小班号",
               "打卡时间", "打卡经度", "打卡纬度",
               "轨迹点数", "轨迹JSON",
               "照片数", "照片列表"]
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=c, value=h)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = _CENTER
        cell.border = _THIN_BORDER
    ws.row_dimensions[1].height = 28

    row_num = 2
    for sr in sc_rows:
        sid = sr["id"]
        extras = storage.get_extras(sid)
        data = sr.get("data", {})
        photos = extras.get("photos", [])
        photo_names = ";".join(p.get("name", "") for p in photos) if photos else ""
        track = extras.get("track", [])
        track_json = json.dumps(track, ensure_ascii=False) if track else ""
        row_data = [
            sid,
            data.get("州", ""),
            sr.get("township", ""),
            sr.get("village", ""),
            sr.get("forest_compartment", ""),
            sr.get("subcompartment", ""),
            extras.get("checkin_at", ""),
            extras.get("checkin_lng", ""),
            extras.get("checkin_lat", ""),
            len(track),
            track_json,
            len(photos),
            photo_names,
        ]
        for c, v in enumerate(row_data, 1):
            cell = ws.cell(row=row_num, column=c, value=v)
            cell.font = _DATA_FONT
            cell.alignment = _LEFT if c in (11, 13) else _CENTER
            cell.border = _THIN_BORDER
        row_num += 1

    widths = [14, 22, 10, 10, 8, 8, 18, 12, 12, 10, 30, 8, 30]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[_col_letter(i)].width = w
    ws.freeze_panes = "A2"
