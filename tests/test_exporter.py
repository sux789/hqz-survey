# -*- coding: utf-8 -*-
"""exporter 自动化测试 — 双模板导出（基本信息 + 样地）。

模拟 GDB 导入后的数据形态：subcompartment_rows 带 category/data_json，
records 通过 upsert_survey_row 写入（含 samples 子数组）。
"""
import io
import json
import sqlite3
import uuid

import pytest
import openpyxl

from survey.core import storage, exporter, schema as S


@pytest.fixture(autouse=True)
def setup_db(tmp_path, monkeypatch):
    """每个测试用独立临时数据库（避免污染开发库 survey.db）。"""
    monkeypatch.setattr(storage, "_DB_PATH", tmp_path / "test.db")
    storage.init_db()


def _insert_sc_row(pid, data, forest_compartment=0, subcompartment=1,
                   category="人工造林", row_index=0):
    """直接写一条 GDB 形态的小班行（category + data_json）。

    需先建 gdb_files 记录（list_project_subcompartment_rows 按 JOIN 过滤）。
    """
    rid = uuid.uuid4().hex[:12]
    conn = storage._connect()
    try:
        gid = "gdb-test"
        exists = conn.execute("SELECT 1 FROM gdb_files WHERE id=?", (gid,)).fetchone()
        if not exists:
            conn.execute(
                """INSERT INTO gdb_files
                   (id, project_id, file_name, file_hash, layers_json,
                    uploaded_by, uploaded_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (gid, pid, "test.gdb", "hash", "[]", "tester", "2026-08-20 00:00:00"),
            )
        conn.execute(
            """INSERT INTO subcompartment_rows
               (id, batch_id, row_index, data_json, township, village,
                forest_compartment, subcompartment, subcompartment_label,
                tending_area, gdb_id, project_name, category)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rid, "batch-none", row_index, json.dumps(data, ensure_ascii=False),
             data.get("乡镇", ""), data.get("村", ""),
             forest_compartment, subcompartment,
             f"{forest_compartment}-{subcompartment}", 0,
             gid, "测试项目", category),
        )
        conn.commit()
    finally:
        conn.close()
    return rid


@pytest.fixture
def sample_project():
    """创建测试项目。"""
    return storage.create_project("测试项目", "测试人", "测试乡镇")


@pytest.fixture
def sample_data(sample_project):
    """构造测试数据：2 个人工造林小班，1 条录入记录（含样地）。"""
    pid = sample_project["id"]
    sc1 = _insert_sc_row(pid, {
        "市": "测试市", "县": "测试县", "乡镇": "测试乡", "村": "测试村",
        "林班": 1, "小班": 5, "调查小班号": 5,
        "验收类别": "省级验收", "项目名称": "测试项目",
        "计划年度": 2023, "作业年度": 2023, "林地所有权": "国有",
        "造林树种": "云杉", "上报面积": 100.5, "小班设计株树": 11000,
    }, forest_compartment=1, subcompartment=5, row_index=1)
    sc2 = _insert_sc_row(pid, {
        "市": "测试市", "县": "测试县", "乡镇": "测试乡", "村": "测试村",
        "林班": 1, "小班": 3, "调查小班号": 3,
        "造林树种": "华山松", "上报面积": 80, "需苗量": 9000,
    }, forest_compartment=1, subcompartment=3, row_index=0)

    # sc1 录入：样地 2 个（种植 100+100，成活 90+85）
    storage.upsert_survey_row(pid, "table1", sc1, {
        "survival_pass": "85",
        "remark": "测试备注",
        "inspect_time": "2026-08-10",
        "samples": [
            {"no": 1, "area": 100, "planted": 100, "alive": 90,
             "x": 102.123456, "y": 25.654321},
            {"no": 2, "area": 100, "planted": 100, "alive": 85, "x": 102.2, "y": 25.6},
        ],
    }, "张三")
    return {"pid": pid, "sc1": sc1, "sc2": sc2}


class TestSampleStats:
    def test_stats_formula(self):
        """合格率 = Σ成活÷Σ种植×100（数值），合格株树 = round(查数×率)。"""
        stats = exporter._sample_stats([
            {"planted": 100, "alive": 90},
            {"planted": 100, "alive": 85},
        ])
        assert stats["planted_total"] == 200
        assert stats["qualified_rate"] == 87.5
        assert stats["qualified_count"] == 175

    def test_stats_empty(self):
        stats = exporter._sample_stats([])
        assert stats["planted_total"] is None
        assert stats["qualified_rate"] is None


class TestExportBase:
    def test_returns_bytesio(self, sample_data):
        output, stats = exporter.export_base(sample_data["pid"])
        assert isinstance(output, io.BytesIO)

    def test_stats(self, sample_data):
        _, stats = exporter.export_base(sample_data["pid"])
        assert stats["project"] == "测试项目"
        assert stats["sheets"]["人工造林"] == 2

    def test_three_sheets(self, sample_data):
        output, _ = exporter.export_base(sample_data["pid"])
        wb = openpyxl.load_workbook(output)
        assert len(wb.sheetnames) == 3

    def test_prefilled_green_cols_filled(self, sample_data):
        """绿色预填列从 GDB 数据填充（表1：B调查小班号/M造林树种/AQ设计株树）。"""
        output, _ = exporter.export_base(sample_data["pid"])
        wb = openpyxl.load_workbook(output)
        ws = wb["2023年度人工造林"]
        # 排序（林班,小班）：sc2(小班3) 在前 → 行5；sc1(小班5) → 行6
        assert ws.cell(row=5, column=2).value == 3       # B 调查小班号
        assert ws.cell(row=5, column=13).value == "华山松"  # M 造林树种
        assert ws.cell(row=5, column=43).value == 9000     # AQ 小班设计株树
        assert ws.cell(row=6, column=2).value == 5
        assert ws.cell(row=6, column=43).value == 11000

    def test_input_and_sample_stats(self, sample_data):
        """白色录入列 + 样地统计列（AN/AO/AP）。"""
        output, _ = exporter.export_base(sample_data["pid"])
        wb = openpyxl.load_workbook(output)
        ws = wb["2023年度人工造林"]
        # sc1 在行6：O 成活率 85
        assert ws.cell(row=6, column=15).value == 85
        # AN 查数株数 = 200，AO 合格株树 175，AP 合格率 87.5
        assert ws.cell(row=6, column=40).value == 200
        assert ws.cell(row=6, column=41).value == 175
        assert ws.cell(row=6, column=42).value == 87.5
        # AT 备注
        assert ws.cell(row=6, column=46).value == "测试备注"

    def test_unsurveyed_row_leaves_input_blank(self, sample_data):
        """未录入小班（sc2 行5）白色列留空。"""
        output, _ = exporter.export_base(sample_data["pid"])
        wb = openpyxl.load_workbook(output)
        ws = wb["2023年度人工造林"]
        assert ws.cell(row=5, column=15).value in (None, "")
        assert ws.cell(row=5, column=40).value in (None, "")

    def test_category_filter(self, sample_data):
        """按分类导出：仅保留当前分类 sheet，其余分类 sheet 移除。"""
        output, stats = exporter.export_base(sample_data["pid"], category="人工造林")
        wb = openpyxl.load_workbook(output)
        assert wb.sheetnames == ["2023年度人工造林"]
        assert stats["sheets"] == {"人工造林": 2}
        ws = wb["2023年度人工造林"]
        assert ws.cell(row=5, column=2).value == 3   # 数据仍按序填充

    def test_category_filter_invalid(self, sample_data):
        """未知分类报错（端点转 404）。"""
        with pytest.raises(ValueError, match="未知分类"):
            exporter.export_base(sample_data["pid"], category="水利水保")

    def test_int_like_no_trailing_dot_zero(self, sample_data):
        """小班/年度/株数不可能有小数："2023.0"/"11000.0" → 整数 2023/11000。"""
        pid = sample_data["pid"]
        _insert_sc_row(pid, {
            "市": "测试市", "县": "测试县", "乡镇": "测试乡", "村": "测试村",
            "林班": 1, "小班": 7, "调查小班号": 7,
            "计划年度": "2023.0", "作业年度": "2023.0", "需苗量": "11000.0",
        }, forest_compartment=1, subcompartment=7, row_index=9)
        output, _ = exporter.export_base(pid)
        wb = openpyxl.load_workbook(output)
        ws = wb["2023年度人工造林"]
        # 排序（林班,小班）：3→行5，5→行6，7→行7
        assert ws.cell(row=7, column=10).value == 2023       # J 计划年度
        assert not isinstance(ws.cell(row=7, column=10).value, str)
        assert ws.cell(row=7, column=11).value == 2023       # K 作业年度
        assert ws.cell(row=7, column=43).value == 11000      # AQ 株树

    def test_orig_subcompartment_chinese(self, sample_data):
        """原始小班可能包含汉字：非数值原样保留（G 列）。"""
        pid = sample_data["pid"]
        _insert_sc_row(pid, {
            "市": "测试市", "县": "测试县", "乡镇": "测试乡", "村": "测试村",
            "林班": 1, "小班": 9, "调查小班号": 9, "小班原始": "红9",
        }, forest_compartment=1, subcompartment=9, row_index=9)
        output, _ = exporter.export_base(pid)
        wb = openpyxl.load_workbook(output)
        ws = wb["2023年度人工造林"]
        assert ws.cell(row=7, column=7).value == "红9"   # G 小班原始

    def test_checkin_coord_precision(self, sample_data):
        """打卡坐标全精度（GPS 6 位小数），不被 _fmt_num 舍入到 2 位。"""
        pid = sample_data["pid"]
        storage.upsert_survey_row(pid, "table1", sample_data["sc2"], {
            "sample_coord_x": "102.123456", "sample_coord_y": "25.654321",
        }, "李四")
        output, _ = exporter.export_base(pid)
        wb = openpyxl.load_workbook(output)
        ws = wb["2023年度人工造林"]
        assert ws.cell(row=5, column=48).value == 102.123456   # AV 打卡坐标x
        assert ws.cell(row=5, column=49).value == 25.654321    # AW 打卡坐标y

    def test_mgmt_enum_defaults_exported(self, sample_data):
        """管理情况 5 项（Z-AD）空值导出 schema 默认「有」；AR/AU 手写签字留空。"""
        output, _ = exporter.export_base(sample_data["pid"])
        wb = openpyxl.load_workbook(output)
        ws = wb["2023年度人工造林"]
        # sc1 未录入任何管理情况字段 → 全部默认「有」
        for col in range(26, 31):  # Z=26 ... AD=30
            assert ws.cell(row=5, column=col).value == "有", f"col {col}"
        # AR/AU 手写签字不录入 → 留空
        assert ws.cell(row=5, column=44).value in (None, "")  # AR
        assert ws.cell(row=5, column=47).value in (None, "")  # AU

    def test_mgmt_enum_saved_value_wins(self, sample_data):
        """已录入值优先于默认：mgmt_design=「无」导出「无」。"""
        pid = sample_data["pid"]
        # sc2=小班3 → 排序后行5
        storage.upsert_survey_row(pid, "table1", sample_data["sc2"], {
            "mgmt_design": "无", "mgmt_meeting": "有",
        }, "张三")
        output, _ = exporter.export_base(pid)
        wb = openpyxl.load_workbook(output)
        ws = wb["2023年度人工造林"]
        assert ws.cell(row=5, column=26).value == "无"   # Z 作业设计
        assert ws.cell(row=5, column=27).value == "有"   # AA 会议纪要

    @staticmethod
    def _sign_data_url():
        """构造 120x60 白底黑迹 PNG data URL（模拟签字 canvas）。"""
        import base64 as _b64
        from PIL import Image, ImageDraw
        im = Image.new("RGB", (120, 60), "white")
        d = ImageDraw.Draw(im)
        d.line([(10, 40), (30, 15), (50, 40), (70, 15), (90, 40)], fill="black", width=3)
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return "data:image/png;base64," + _b64.b64encode(buf.getvalue()).decode()

    def test_sign_images_inserted(self, sample_data):
        """手写签字导出为图片：AR=inspector_sign、AU=co_inspector_sign，格内不写文本。"""
        pid = sample_data["pid"]
        url = self._sign_data_url()
        storage.upsert_survey_row(pid, "table1", sample_data["sc2"], {
            "inspector_sign": url, "co_inspector_sign": url,
        }, "张三")
        output, _ = exporter.export_base(pid)
        wb = openpyxl.load_workbook(output)
        ws = wb["2023年度人工造林"]
        # 行5（小班3）两列签字 → 2 张图片
        assert len(ws._images) == 2
        cells = sorted((img.anchor._from.row + 1, img.anchor._from.col + 1)
                       for img in ws._images)
        assert cells == [(5, 44), (5, 47)]   # AR5 / AU5
        # 固定显示范围：宽 ≤200px、高 ≤64px
        for img in ws._images:
            assert img.width <= 200 and img.height <= 64
        # 单元格本身不写 data URL 文本
        assert ws.cell(row=5, column=44).value in (None, "")
        assert ws.cell(row=5, column=47).value in (None, "")

    def test_sign_blank_skipped(self, sample_data):
        """无签字 → 不插图片、单元格留空。"""
        output, _ = exporter.export_base(sample_data["pid"])
        wb = openpyxl.load_workbook(output)
        for sn in wb.sheetnames:
            assert len(wb[sn]._images) == 0


class TestExportSamples:
    def test_returns_bytesio(self, sample_data):
        output, stats = exporter.export_samples(sample_data["pid"])
        assert isinstance(output, io.BytesIO)
        assert stats["project"] == "测试项目"

    def test_block_structure(self, sample_data):
        """每小班一个 39 行块：sc2 块1（行1-39），sc1 块2（行40-78）。"""
        output, _ = exporter.export_samples(sample_data["pid"])
        wb = openpyxl.load_workbook(output)
        assert "人工造林" in wb.sheetnames
        ws = wb["人工造林"]
        # 块2 起始行 40 的标题 + 块内样地数据（行 44-45，数据起始 40+4）
        assert "样地调查表" in str(ws.cell(row=40, column=1).value)
        # sc1 样地1：行 40+4=44，A=样圆号1，C=种植100，D=成活90
        assert ws.cell(row=44, column=1).value == 1
        assert ws.cell(row=44, column=3).value == 100
        assert ws.cell(row=44, column=4).value == 90
        # 死亡株数公式（E 列）
        assert ws.cell(row=44, column=5).value == "=C44-D44"

    def test_empty_category_skipped(self, sample_data):
        """无数据的分类跳过（不建 sheet，不崩）。"""
        output, stats = exporter.export_samples(sample_data["pid"])
        assert "封山育林" not in stats["sheets"]
        wb = openpyxl.load_workbook(output)
        assert "封山育林" not in wb.sheetnames
        assert wb.sheetnames == ["人工造林"]

    def test_single_subcompartment(self, sample_data):
        """单小班模式：仅当前小班当前分类，sheet 名「分类-调查小班号」，只有一个块。"""
        output, stats = exporter.export_samples(
            sample_data["pid"], subcompartment_id=sample_data["sc1"])
        wb = openpyxl.load_workbook(output)
        assert wb.sheetnames == ["人工造林-5"]
        ws = wb["人工造林-5"]
        # 只有块1：sc1 样地1 在行 5（块1数据起始），行 44（块2）应为空
        assert ws.cell(row=5, column=1).value == 1
        assert ws.cell(row=5, column=3).value == 100
        assert ws.cell(row=5, column=4).value == 90
        assert ws.cell(row=44, column=1).value is None
        assert stats["sheets"] == {"人工造林-5": 1}

    def test_a2_and_coord_precision(self, sample_data):
        """A2 含小班和调查小班号；坐标写全精度浮点（不被 _fmt_num 舍入到 2 位）。"""
        output, _ = exporter.export_samples(
            sample_data["pid"], subcompartment_id=sample_data["sc1"])
        wb = openpyxl.load_workbook(output)
        ws = wb["人工造林-5"]
        a2 = str(ws.cell(row=2, column=1).value)
        assert "项目类型：人工造林" in a2
        assert "小班：5" in a2          # GDB 小班（原始号，导入归一后同调查小班号）
        assert "调查小班号：5" in a2
        # 坐标全精度：x=102.123456 不能被舍成 102.12
        assert ws.cell(row=5, column=6).value == 102.123456
        assert ws.cell(row=5, column=7).value == 25.654321

    def test_single_subcompartment_not_found(self, sample_data):
        """单小班模式：小班不存在或不属于该项目时 404 语义（ValueError）。"""
        with pytest.raises(ValueError, match="小班不存在"):
            exporter.export_samples(sample_data["pid"], subcompartment_id="no_such_id")


class TestExportTracks:
    def test_no_tracks_raises(self, sample_data):
        """无轨迹时抛 ValueError（端点转 404）。"""
        with pytest.raises(ValueError, match="暂无轨迹"):
            exporter.export_tracks_zip(sample_data["pid"])
