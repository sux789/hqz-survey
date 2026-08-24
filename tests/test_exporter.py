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


class TestTemplateFormulas:
    """模板公式质量门禁（tpl-base 示例行 / tpl-samples 全表）。"""

    @pytest.mark.parametrize("tpl_path", [
        exporter._BASE_TEMPLATE, exporter._SAMPLE_TEMPLATE,
    ], ids=["tpl-base", "tpl-samples"])
    def test_no_circular_reference(self, tpl_path):
        """公式不得引用自身单元格（Excel 打开会提示循环引用）。

        2026-08-21 模板曾出现 =IF(AP5>=0.9,N5,AF5)（AF 列引用自身，
        本意"不合格时保留手填值"），已统一改为 =IF(cond,N5,"")。
        """
        import re
        wb = openpyxl.load_workbook(tpl_path)
        for sn in wb.sheetnames:
            ws = wb[sn]
            for row in ws.iter_rows():
                for c in row:
                    v = c.value
                    if isinstance(v, str) and v.startswith("="):
                        pat = rf"(?<![A-Z$]){re.escape(c.coordinate)}(?!\d)"
                        assert not re.search(pat, v), \
                            f"{tpl_path.name}[{sn}]!{c.coordinate} 公式自引用: {v}"
        wb.close()


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

    # sc1 录入：样地 2 个（种植 100+100，成活 90+85）+ 汇总手写项
    # survival_pass 存比率 0-1（新口径，2026-08-21：率类不 ×100）
    storage.upsert_survey_row(pid, "table1", sc1, {
        "survival_pass": "0.85",
        "remark": "测试备注",
        "inspect_time": "2026-08-10",
        "samples": [
            {"no": 1, "area": 100, "planted": 100, "alive": 90,
             "x": 102.123456, "y": 25.654321, "remark": "坡上部"},
            {"no": 2, "area": 100, "planted": 100, "alive": 85, "x": 102.2, "y": 25.6},
        ],
        "sm_grid_area": "5000",
        "sm_grid_count": "4",
        "sm_pole": "完好",
        "sm_film": "无膜",
        "sm_inspector": "李四",
        "sm_inspect_date": "2026-08-21",
        "sm_remark": "汇总备注测试",
    }, "张三")
    return {"pid": pid, "sc1": sc1, "sc2": sc2}


class TestSampleStats:
    """查数株数 = 调查总株数（B34 口径）：round(Σ种植÷个数÷150×网格面积×网格数量)。"""

    BASE = {"samples": [
        {"planted": 100, "alive": 90},
        {"planted": 100, "alive": 85},
    ]}

    def test_stats_formula(self):
        """网格 5000×4、实际个数 2：查数=13333，率=0.875（比率），合格=11666。"""
        stats = exporter._sample_stats(
            dict(self.BASE, sm_grid_area="5000", sm_grid_count="4"))
        assert stats["planted_total"] == 13333
        assert stats["qualified_rate"] == 0.875
        assert stats["qualified_count"] == 11666

    def test_stats_manual_count(self):
        """手写个数 sm_total_count=5 优先：查数=5333，合格=4666。"""
        stats = exporter._sample_stats(
            dict(self.BASE, sm_grid_area="5000", sm_grid_count="4",
                 sm_total_count="5"))
        assert stats["planted_total"] == 5333
        assert stats["qualified_count"] == 4666

    def test_stats_no_grid(self):
        """网格未填（同模板 B32/B33 空参与乘法）：查数=0，合格=0，率不变。"""
        stats = exporter._sample_stats(dict(self.BASE))
        assert stats["planted_total"] == 0
        assert stats["qualified_rate"] == 0.875
        assert stats["qualified_count"] == 0

    def test_stats_empty(self):
        stats = exporter._sample_stats({})
        assert stats["planted_total"] is None
        assert stats["qualified_rate"] is None
        assert stats["qualified_count"] is None


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
        """白色录入列 + 样地统计列（AN/AO/AP）——率类比率 + 0.00% 格式。"""
        output, _ = exporter.export_base(sample_data["pid"])
        wb = openpyxl.load_workbook(output)
        ws = wb["2023年度人工造林"]
        # sc1 在行6：O 成活率分派列 store:false —— 录入值不导出，模板公式代入
        # （AP6=0.875 <0.9 → Excel 打开显示空白）
        assert ws.cell(row=6, column=15).value == '=IF(AP6>=0.9,AP6,"")'
        assert ws.cell(row=6, column=15).number_format == '0.00%'
        # AN 查数株数 = 调查总株数 13333（B34 口径），AO 合格株树 11666，AP 合格率 0.875
        assert ws.cell(row=6, column=40).value == 13333
        assert ws.cell(row=6, column=41).value == 11666
        assert ws.cell(row=6, column=42).value == 0.875
        # 行6 为模板行5 之后的导出行：率类单元格须补设 0.00% 格式（显示 87.50%）
        assert ws.cell(row=6, column=42).number_format == '0.00%'
        # AT 备注
        assert ws.cell(row=6, column=46).value == "测试备注"

    def test_derived_dispatch_columns_formula_only(self, sample_data):
        """store:false 分派列（成活率等级 O/P/Q + 面积 S/AF/AH/AL）一律模板公式：
        即使 data_json 残留旧值也不导出，由 Excel 现算（合格率≥0.9 → 上报面积）。
        """
        pid = sample_data["pid"]
        sc1 = sample_data["sc1"]
        # 残留值模拟（store:false 字段正常情况下不会落库）
        rec = storage.get_survey_rows(pid, "table1")
        base = next((r["data"] or {} for r in rec if r["subcompartment_id"] == sc1), {})
        base.update({
            "survival_replant": "0.875",   # P 列残留
            "verified_pass": "999.9",      # S 列残留
            "construction_area": "888.8",  # AF 列残留
        })
        storage.upsert_survey_row(pid, "table1", sc1, base, "张三",
                                  base_version=(next(r["version"] for r in rec
                                                     if r["subcompartment_id"] == sc1)))
        output, _ = exporter.export_base(pid)
        wb = openpyxl.load_workbook(output)
        ws = wb["2023年度人工造林"]
        # sc1 行6：残留值全部忽略，公式代入（AP6=0.875 在 0.4~0.9 → 待补植列显示率）
        assert ws.cell(row=6, column=15).value == '=IF(AP6>=0.9,AP6,"")'    # O 合格
        assert ws.cell(row=6, column=16).value == '=IF(AND(AP6<0.9,AP6>0.4),AP6,"")'  # P 待补植
        assert ws.cell(row=6, column=17).value == '=IF(AP6<=0.4,AP6,"")'    # Q 失败
        # 面积分派：合格率≥0.9 才回填上报面积 N 列 —— 同样公式承担
        assert ws.cell(row=6, column=19).value == '=IF(AP6>=0.9,N6,"")'     # S 合格面积
        assert ws.cell(row=6, column=32).value == '=IF(AP6>=0.9,N6,"")'     # AF 施工面积
        assert ws.cell(row=6, column=34).value == '=IF(AP6>=0.9,N6,"")'     # AH 建档面积
        assert ws.cell(row=6, column=38).value == '=IF(AP6>=0.9,N6,"")'     # AL 抚育面积

    def test_unsurveyed_row_leaves_input_blank(self, sample_data):
        """未录入小班（sc2 行5）：纯数据列留空，模板公式列代入公式。"""
        output, _ = exporter.export_base(sample_data["pid"])
        wb = openpyxl.load_workbook(output)
        ws = wb["2023年度人工造林"]
        # AN（查数株数）无模板公式 → 留空
        assert ws.cell(row=5, column=40).value in (None, "")
        # O 列模板公式（成活率分派）代入数据行（行5 偏移 0）
        assert ws.cell(row=5, column=15).value == '=IF(AP5>=0.9,AP5,"")'

    def test_percent_ratio_precision(self, sample_data):
        """率类比率保留 4 位小数：0.9524 不被 _fmt_num 的 2 位舍入截成 0.95。"""
        pid = sample_data["pid"]
        sc2 = sample_data["sc2"]
        storage.upsert_survey_row(pid, "table1", sc2, {
            "survival_pass": "0.9524",     # O 列（store:false 残留 → 导出忽略，公式承担）
            "construction_rate": "0.9844",  # AG 列（施工率，手输 percent 落库）
        }, "张三")
        output, _ = exporter.export_base(pid)
        wb = openpyxl.load_workbook(output)
        ws = wb["2023年度人工造林"]
        # sc2 排序后行5：O 分派列公式代入（Excel 打开现算），残留值 0.9524 不导出
        assert ws.cell(row=5, column=15).value == '=IF(AP5>=0.9,AP5,"")'
        assert ws.cell(row=5, column=15).number_format == '0.00%'
        assert ws.cell(row=5, column=33).value == 0.9844   # AG
        assert ws.cell(row=5, column=33).number_format == '0.00%'

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
        """旧数据（含「小班原始」键）：汉字原样保留（G 列，兼容读取）。"""
        pid = sample_data["pid"]
        _insert_sc_row(pid, {
            "市": "测试市", "县": "测试县", "乡镇": "测试乡", "村": "测试村",
            "林班": 1, "小班": 9, "调查小班号": 9, "小班原始": "红9",
        }, forest_compartment=1, subcompartment=9, row_index=9)
        output, _ = exporter.export_base(pid)
        wb = openpyxl.load_workbook(output)
        ws = wb["2023年度人工造林"]
        assert ws.cell(row=7, column=7).value == "红9"   # G 小班

    def test_subcompartment_chinese_new_import(self, sample_data):
        """新导入形态（无「小班原始」）：小班=GDB 原值含汉字（G 列原样），
        调查小班号=数字业务键（B 列）。"""
        pid = sample_data["pid"]
        _insert_sc_row(pid, {
            "市": "测试市", "县": "测试县", "乡镇": "测试乡", "村": "测试村",
            "林班": 1, "小班": "红9", "调查小班号": 9,
        }, forest_compartment=1, subcompartment=9, row_index=9)
        output, _ = exporter.export_base(pid)
        wb = openpyxl.load_workbook(output)
        ws = wb["2023年度人工造林"]
        assert ws.cell(row=7, column=2).value == 9        # B 调查小班号（数字）
        assert ws.cell(row=7, column=7).value == "红9"   # G 小班（汉字原样）

    def test_subcompartment_mapping(self):
        """字段映射：小班→subcompartment_orig（原值），调查小班号→subcompartment（uint）；
        旧键「小班原始」兼容优先。"""
        pf = S.map_subcompartment_to_prefilled(
            {"小班": "红9", "调查小班号": 9})
        assert pf["subcompartment_orig"] == "红9"
        assert pf["subcompartment"] == 9

        # 旧数据：小班被旧导入覆写为数字，小班原始保留原值 → 原值优先
        pf_old = S.map_subcompartment_to_prefilled(
            {"小班": 5, "调查小班号": "5.0", "小班原始": "71"})
        assert pf_old["subcompartment_orig"] == "71"
        assert pf_old["subcompartment"] == 5

        # 无小班字段：subcompartment_orig 缺省，导出回退调查小班号
        pf_min = S.map_subcompartment_to_prefilled({"调查小班号": "12.0"})
        assert pf_min["subcompartment"] == 12
        assert "subcompartment_orig" not in pf_min

    def test_derive_uint_with_digits(self):
        """调查小班号推导：数值直转；含汉字提取数字；无数字→0。"""
        f = storage._derive_uint_with_digits
        assert f("5.0") == 5
        assert f(7) == 7
        assert f("红9") == 9
        assert f("新增17") == 17
        assert f("红") == 0
        assert f("") == 0
        assert f(None) == 0

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
        # 块2 起始行 40 的标题 + 块内样地数据（行 43-44，数据起始 40+3）
        assert "样地调查表" in str(ws.cell(row=40, column=1).value)
        # 块1 的 R39 为备注行（模板自带标签，块2 覆盖为标题）
        assert ws.cell(row=39, column=1).value == "备注"
        # sc1 样地1：行 40+3=43，A=样地号1，C=种植100，D=成活90
        assert ws.cell(row=43, column=1).value == 1
        assert ws.cell(row=43, column=3).value == 100
        assert ws.cell(row=43, column=4).value == 90
        # 死亡株数公式（E 列）
        assert ws.cell(row=43, column=5).value == "=C43-D43"
        # 样地备注（I 列）；样地2 无备注 → 空
        assert ws.cell(row=43, column=9).value == "坡上部"
        assert ws.cell(row=44, column=9).value in (None, "")

    def test_summary_fields(self, sample_data):
        """汇总区 R27-R39：个数/手写项写入，公式带除0守卫并按块偏移。"""
        output, _ = exporter.export_samples(
            sample_data["pid"], subcompartment_id=sample_data["sc1"])
        wb = openpyxl.load_workbook(output)
        ws = wb["人工造林-5"]
        # B27 总样地个数 = 样地数（代码计算写入）
        assert ws.cell(row=27, column=2).value == 2
        # B28-30 SUM 公式保留；B31/B34 带 IF 除0守卫
        assert ws.cell(row=28, column=2).value == "=SUM(B4:B26)"
        assert ws.cell(row=31, column=2).value == '=IF(B29=0,"",B30/B29)'
        assert ws.cell(row=34, column=2).value == \
            '=IF(OR(B27=0,B29=0),"",ROUND(B29/B27/150*B32*B33,0))'
        # 手写录入项（sm_* → B32-B39）
        assert ws.cell(row=32, column=2).value == 5000.0
        assert ws.cell(row=33, column=2).value == 4.0
        assert ws.cell(row=35, column=2).value == "完好"
        assert ws.cell(row=36, column=2).value == "无膜"
        assert ws.cell(row=37, column=2).value == "李四"
        assert ws.cell(row=38, column=2).value == "2026-08-21"
        assert ws.cell(row=39, column=2).value == "汇总备注测试"

    def test_total_count_manual_override(self, sample_data):
        """B27 总样地个数：手写 sm_total_count 优先（>0 生效），无效/空回退样地数。"""
        pid = sample_data["pid"]
        sc1 = sample_data["sc1"]
        # 手写覆盖：个数写 5（≠ 实际样地数 2）
        rec = storage.get_survey_rows(pid, "table1")
        d = next(r["data"] for r in rec if r["subcompartment_id"] == sc1)
        d["sm_total_count"] = "5"
        storage.upsert_survey_row(pid, "table1", sc1, d, "张三")
        output, _ = exporter.export_samples(pid, subcompartment_id=sc1)
        wb = openpyxl.load_workbook(output)
        assert wb["人工造林-5"].cell(row=27, column=2).value == 5

        # 无效值（0/非数字）回退实际样地数
        for bad in ("0", "-1", "abc", ""):
            d["sm_total_count"] = bad
            storage.upsert_survey_row(pid, "table1", sc1, d, "张三")
            output, _ = exporter.export_samples(pid, subcompartment_id=sc1)
            wb = openpyxl.load_workbook(output)
            assert wb["人工造林-5"].cell(row=27, column=2).value == 2, bad

    def test_summary_block2_shift(self, sample_data):
        """块2 汇总公式行号偏移 + 手写项按块写入（块2=sc1 有录入）。"""
        output, _ = exporter.export_samples(sample_data["pid"])
        wb = openpyxl.load_workbook(output)
        ws = wb["人工造林"]
        # 排序后 sc2(小班3)=块1、sc1(小班5)=块2（行40起）
        # 块2 B31 公式偏移为 =IF(B68=0,"",B69/B68)
        assert ws.cell(row=40 + 30, column=2).value == '=IF(B68=0,"",B69/B68)'
        # 块2 = sc1：总样地个数 2、网格面积/备注照常写入
        assert ws.cell(row=40 + 26, column=2).value == 2
        assert ws.cell(row=40 + 31, column=2).value == 5000.0
        assert ws.cell(row=40 + 38, column=2).value == "汇总备注测试"

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
        # 只有块1：sc1 样地1 在行 4（块1数据起始），行 40（块2标题位）应为空
        assert ws.cell(row=4, column=1).value == 1
        assert ws.cell(row=4, column=3).value == 100
        assert ws.cell(row=4, column=4).value == 90
        assert ws.cell(row=40, column=1).value is None
        assert stats["sheets"] == {"人工造林-5": 1}

    def test_title_and_coord_precision(self, sample_data):
        """R1 标题含项目类型+调查小班号；R2 年度县乡；坐标写全精度浮点。"""
        output, _ = exporter.export_samples(
            sample_data["pid"], subcompartment_id=sample_data["sc1"])
        wb = openpyxl.load_workbook(output)
        ws = wb["人工造林-5"]
        # R1 标题：项目名称+样地调查表（类型）+ 调查小班号
        r1 = str(ws.cell(row=1, column=1).value)
        assert "样地调查表（人工造林）" in r1
        assert "调查小班号5" in r1
        # R2 年度县乡（A2:E2 合并区）
        r2 = str(ws.cell(row=2, column=1).value)
        assert "年度" in r2
        # 坐标全精度：x=102.123456 不能被舍成 102.12（数据起始 R4）
        assert ws.cell(row=4, column=6).value == 102.123456
        assert ws.cell(row=4, column=7).value == 25.654321

    def test_single_subcompartment_not_found(self, sample_data):
        """单小班模式：小班不存在或不属于该项目时 404 语义（ValueError）。"""
        with pytest.raises(ValueError, match="小班不存在"):
            exporter.export_samples(sample_data["pid"], subcompartment_id="no_such_id")

    def test_category_filter(self, sample_data):
        """分类过滤（admin 分类下载）：仅导出该分类一个 sheet，每分类一个文件。"""
        output, stats = exporter.export_samples(sample_data["pid"], category="人工造林")
        wb = openpyxl.load_workbook(output)
        assert wb.sheetnames == ["人工造林"]
        assert stats["sheets"] == {"人工造林": 2}

    def test_category_filter_empty_raises(self, sample_data):
        """分类过滤：该分类无小班时 ValueError（端点转 404）。"""
        with pytest.raises(ValueError, match="暂无小班数据"):
            exporter.export_samples(sample_data["pid"], category="封山育林")

    def test_samples_zip_per_subcompartment(self, sample_data):
        """分类下载样地 zip：每小班一个 xlsx，命名 {林班-小班}号调查小班-{分类}-{年度}.xlsx。"""
        import zipfile
        from datetime import date
        buf, stats = exporter.export_samples_zip(sample_data["pid"], category="人工造林")
        assert stats["files"] == 2
        with zipfile.ZipFile(buf) as zf:
            names = sorted(zf.namelist())
        # sc1：林班1小班5 计划年度2023；sc2：林班1小班3 无年度→项目名无年度→当前年
        assert names == [
            "1-3号调查小班-人工造林-%d.xlsx" % date.today().year,
            "1-5号调查小班-人工造林-2023.xlsx",
        ]
        # 每个 xlsx 可打开且为单小班模式（一个 sheet 一个块）
        with zipfile.ZipFile(buf) as zf:
            wb = openpyxl.load_workbook(io.BytesIO(
                zf.read("1-5号调查小班-人工造林-2023.xlsx")))
        assert wb.sheetnames == ["人工造林-5"]
        assert wb["人工造林-5"].cell(row=4, column=3).value == 100  # 样地1 面积

    def test_samples_zip_empty_category_raises(self, sample_data):
        """样地 zip：该分类无小班时 ValueError（端点转 404）。"""
        with pytest.raises(ValueError, match="暂无小班数据"):
            exporter.export_samples_zip(sample_data["pid"], category="封山育林")


class TestExportTracks:
    def test_no_tracks_raises(self, sample_data):
        """无轨迹时抛 ValueError（端点转 404）。"""
        with pytest.raises(ValueError, match="暂无轨迹"):
            exporter.export_tracks_zip(sample_data["pid"])

    def test_category_filter(self, sample_data):
        """分类过滤：仅打包该分类小班的轨迹 GPX（新命名：小班号-分类-年度.gpx）。"""
        import zipfile
        from datetime import date
        pid = sample_data["pid"]
        sc3 = _insert_sc_row(pid, {
            "乡镇": "测试乡", "村": "测试村", "林班": 2, "小班": 7, "调查小班号": 7,
        }, forest_compartment=2, subcompartment=7, category="封山育林", row_index=2)
        storage.save_track(sample_data["sc1"], [
            {"lng": 102.1, "lat": 25.6, "t": "2026-08-21T10:00:00"},
        ])
        storage.save_track(sc3, [
            {"lng": 102.3, "lat": 25.7, "t": "2026-08-21T11:00:00"},
        ])
        buf, stats = exporter.export_tracks_zip(pid, category="封山育林")
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
        # sc3：林班2小班7，无计划年度，项目名无年度 → 当前年
        assert names == [f"tracks/2-7号调查小班-封山育林-{date.today().year}.gpx"]
        # 不过滤则两类各一条：sc1 计划年度 2023
        buf_all, stats_all = exporter.export_tracks_zip(pid)
        with zipfile.ZipFile(buf_all) as zf:
            assert sorted(zf.namelist()) == [
                "tracks/1-5号调查小班-人工造林-2023.gpx",
                f"tracks/2-7号调查小班-封山育林-{date.today().year}.gpx",
            ]
        # 过滤无轨迹分类 → ValueError
        sc4 = _insert_sc_row(pid, {
            "乡镇": "测试乡", "村": "测试村", "林班": 3, "小班": 9, "调查小班号": 9,
        }, forest_compartment=3, subcompartment=9, category="退化林修复", row_index=3)
        with pytest.raises(ValueError, match="暂无轨迹"):
            exporter.export_tracks_zip(pid, category="退化林修复")

    def test_kml_single_file(self, sample_data):
        """KML：全部小班合并单文件，每小班一个 Placemark（LineString）。"""
        import zipfile
        from datetime import date
        pid = sample_data["pid"]
        sc3 = _insert_sc_row(pid, {
            "乡镇": "测试乡", "村": "测试村", "林班": 2, "小班": 7, "调查小班号": 7,
        }, forest_compartment=2, subcompartment=7, category="封山育林", row_index=2)
        # sc1 两点成线；sc3 单点 → KML 跳过
        storage.save_track(sample_data["sc1"], [
            {"lng": 102.1, "lat": 25.6, "t": "2026-08-21T10:00:00"},
            {"lng": 102.11, "lat": 25.61, "t": "2026-08-21T10:01:00"},
        ])
        storage.save_track(sc3, [
            {"lng": 102.3, "lat": 25.7, "t": "2026-08-21T11:00:00"},
        ])
        buf, stats = exporter.export_tracks_zip(pid, fmt="kml")
        assert stats["fmt"] == "kml" and stats["tracks"] == 1
        with zipfile.ZipFile(buf) as zf:
            assert zf.namelist() == ["tracks/测试项目.kml"]  # 项目名主干
            kml = zf.read("tracks/测试项目.kml").decode("utf-8")
        assert kml.startswith('<?xml version="1.0" encoding="UTF-8"?>')
        assert '<kml xmlns="http://www.opengis.net/kml/2.2">' in kml
        assert kml.count("<Placemark>") == 1          # 单点轨迹跳过
        assert "102.1,25.6 102.11,25.61" in kml       # 坐标串
        assert "分类: 人工造林" in kml and "点数: 2" in kml

    def test_shp_single_file(self, sample_data):
        """SHP：单 shapefile（每小班一条线要素 + 属性表），geopandas 读回验证。"""
        import zipfile
        pid = sample_data["pid"]
        storage.save_track(sample_data["sc1"], [
            {"lng": 102.1, "lat": 25.6, "t": "2026-08-21T10:00:00"},
            {"lng": 102.11, "lat": 25.61, "t": "2026-08-21T10:01:00"},
            {"lng": 102.12, "lat": 25.62, "t": "2026-08-21T10:02:00"},
        ])
        buf, stats = exporter.export_tracks_zip(pid, fmt="shp")
        assert stats["fmt"] == "shp" and stats["tracks"] == 1
        import geopandas as gpd
        import tempfile, os
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
            # shapefile 组件齐全（.shp/.shx/.dbf/.prj/.cpg）
            assert any(n.endswith(".shp") for n in names)
            assert any(n.endswith(".dbf") for n in names)
            assert any(n.endswith(".prj") for n in names)
            with tempfile.TemporaryDirectory() as td:
                for n in names:
                    zf.extract(n, td)
                stem = next(n for n in names if n.endswith(".shp"))
                gdf = gpd.read_file(os.path.join(td, stem))
        assert len(gdf) == 1
        row = gdf.iloc[0]
        assert row.geometry.geom_type == "LineString"
        assert round(row.geometry.coords[0][0], 4) == 102.1
        assert row["category"] == "人工造林"
        assert row["township"] == "测试乡" and row["village"] == "测试村"
        assert row["pts"] == 3
        assert str(row["start"]).startswith("2026-08-21")
        assert gdf.crs is not None and gdf.crs.to_epsg() == 4326  # WGS84

    def test_fmt_invalid_raises(self, sample_data):
        """非法 fmt → ValueError。"""
        with pytest.raises(ValueError, match="不支持的轨迹格式"):
            exporter.export_tracks_zip(sample_data["pid"], fmt="dwg")

    def test_kml_all_single_point_raises(self, sample_data):
        """全部轨迹不足 2 点 → ValueError（线要素无法生成）。"""
        storage.save_track(sample_data["sc1"], [
            {"lng": 102.1, "lat": 25.6, "t": "2026-08-21T10:00:00"},
        ])
        with pytest.raises(ValueError, match="不足 2 个有效点"):
            exporter.export_tracks_zip(sample_data["pid"], fmt="kml")
        # GPX 不受单点限制
        buf, stats = exporter.export_tracks_zip(sample_data["pid"], fmt="gpx")
        assert stats["tracks"] == 1
