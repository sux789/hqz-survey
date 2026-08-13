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
import json
from pathlib import Path

import openpyxl
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side

from survey.core import schema as S
from survey.core import storage

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
        # 列宽自适应
        for col_num in range(1, last_col + 1):
            ws.column_dimensions[_col_letter(col_num)].width = 14
        ws.row_dimensions[1].height = 30
        # 冻结表头
        ws.freeze_panes = "A2"
        stats["tables"][tid] = len(records)

        # table5 样方明细展开为独立 sheet（每样方一行，含小班预填列）
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
