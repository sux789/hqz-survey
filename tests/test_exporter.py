# -*- coding: utf-8 -*-
"""exporter 自动化测试。"""
import io
import pytest
import openpyxl
from survey.core import storage, exporter, schema as S

@pytest.fixture(autouse=True)
def setup_db():
    """每个测试前初始化数据库。"""
    storage.init_db()

@pytest.fixture
def sample_project():
    """创建测试项目。"""
    return storage.create_project("测试项目", "测试人", "测试乡镇")

@pytest.fixture
def sample_records(sample_project):
    """创建测试记录。"""
    pid = sample_project["id"]
    # table1 一条记录
    storage.save_record(pid, "table1", "", 0, {
        "survival_pass": "85",
        "verified_total": "100",
        "inspector": "张三",
        "inspect_time": "2026-08-10",
        "remark": "测试备注",
    }, "张三")
    return pid

class TestExporter:
    def test_export_returns_bytesio(self, sample_records):
        """测试导出返回 BytesIO。"""
        output, stats = exporter.export_project(sample_records)
        assert output is not None
        assert isinstance(output, io.BytesIO)

    def test_export_stats(self, sample_records):
        """测试导出统计信息。"""
        output, stats = exporter.export_project(sample_records)
        assert stats["project"] == "测试项目"
        assert stats["tables"]["table1"] == 1

    def test_export_xlsx_readable(self, sample_records):
        """测试导出的 xlsx 可被 openpyxl 读取。"""
        output, stats = exporter.export_project(sample_records)
        wb = openpyxl.load_workbook(output)
        # 应有5个 sheet
        assert len(wb.sheetnames) >= 5

    def test_export_xlsx_has_data(self, sample_records):
        """测试导出的 xlsx 有数据。"""
        output, stats = exporter.export_project(sample_records)
        wb = openpyxl.load_workbook(output)
        # 找到 table1 的 sheet
        ws = None
        for sn in wb.sheetnames:
            if "表1" in sn:
                ws = wb[sn]
                break
        assert ws is not None
        # table1 有13个预填列(A-M)，录入数据从第14列(N)开始
        # survival_pass=85 → 导出为 "85%"，位于第14列
        assert ws.cell(row=2, column=14).value == "85%", \
            f"第14列应为85%，实际为 {ws.cell(row=2, column=14).value!r}"

    def test_export_has_extras_sheet(self, sample_records):
        """测试导出包含「小班扩展数据」sheet。"""
        output, stats = exporter.export_project(sample_records)
        wb = openpyxl.load_workbook(output)
        assert "小班扩展数据" in wb.sheetnames
        ws = wb["小班扩展数据"]
        # 表头应有小班ID等列
        assert ws.cell(row=1, column=1).value == "小班ID"
