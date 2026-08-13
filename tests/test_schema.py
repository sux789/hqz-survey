# -*- coding: utf-8 -*-
"""schema 自动化测试 — 验证五张表定义的完整性和一致性。"""
import pytest
from survey.core import schema as S

class TestSchema:
    """验证五张表定义。"""

    def test_has_five_tables(self):
        assert len(S.TABLES) == 5

    def test_table_ids_unique(self):
        ids = [t["id"] for t in S.TABLES]
        assert len(ids) == len(set(ids))

    def test_each_table_has_required_keys(self):
        for t in S.TABLES:
            assert "id" in t
            assert "name" in t
            assert "sheet_name" in t
            assert "prefilled_columns" in t or "subtables" in t
            assert "input_columns" in t or "subtables" in t

    def test_prefilled_columns_have_col(self):
        """每个预填列必须有 col 字段（用于 xlsx 列映射）。"""
        for t in S.TABLES:
            if "prefilled_columns" in t:
                for col in t["prefilled_columns"]:
                    assert "col" in col, f"{t['id']} 预填列 {col.get('key')} 缺少 col 字段"
                    assert "key" in col
                    assert "label" in col

    def test_input_columns_have_type(self):
        """每个录入列必须有 type 字段。"""
        for t in S.TABLES:
            cols = t.get("input_columns", [])
            if "subtables" in t:
                for st in t["subtables"]:
                    cols = st["input_columns"]
                    for col in cols:
                        assert "type" in col, f"{t['id']}/{st['id']} 录入列 {col.get('key')} 缺少 type"
            else:
                for col in cols:
                    assert "type" in col, f"{t['id']} 录入列 {col.get('key')} 缺少 type"

    def test_enum_has_options(self):
        """enum 类型字段必须有 options。"""
        for t in S.TABLES:
            cols = t.get("input_columns", [])
            if "subtables" in t:
                for st in t["subtables"]:
                    cols = st["input_columns"]
                    for col in cols:
                        if col.get("type") == "enum":
                            assert col.get("options"), f"{col['key']} enum 缺少 options"
            else:
                for col in cols:
                    if col.get("type") == "enum":
                        assert col.get("options"), f"{col['key']} enum 缺少 options"

    def test_get_table(self):
        assert S.get_table("table1") is not None
        assert S.get_table("table5") is not None
        assert S.get_table("nonexistent") is None

    def test_get_input_fields_table5(self):
        """table5 有子表，返回所有子表字段。"""
        fields = S.get_input_fields("table5")
        assert len(fields) > 0
        # 每个字段应有 subtable_id
        for f in fields:
            assert f.get("subtable_id") in ("table5a", "table5b")

    def test_validate_row_valid(self):
        """测试有效数据通过校验。"""
        data = {
            "inspector": "张三",
            "inspect_time": "2026-08-10",
        }
        ok, errors = S.validate_row("table1", data)
        # inspector 和 inspect_time 是 required，填了应该过
        assert "inspector" not in errors
        assert "inspect_time" not in errors

    def test_validate_row_missing_required(self):
        """测试缺少必填字段时校验失败。"""
        data = {}
        ok, errors = S.validate_row("table1", data)
        assert not ok
        assert "inspector" in errors
        assert "inspect_time" in errors

    def test_validate_row_invalid_number(self):
        """测试数值校验。"""
        data = {
            "inspector": "张三",
            "inspect_time": "2026-08-10",
            "survival_pass": "abc",  # 非数值
        }
        ok, errors = S.validate_row("table1", data)
        assert not ok
        assert "survival_pass" in errors

    def test_validate_row_percent_out_of_range(self):
        """测试百分比超范围。"""
        data = {
            "inspector": "张三",
            "inspect_time": "2026-08-10",
            "survival_pass": "150",  # 超过100
        }
        ok, errors = S.validate_row("table1", data)
        assert not ok
        assert "survival_pass" in errors

    def test_field_groups(self):
        """测试字段分组。"""
        groups = S.get_field_groups("table1")
        assert len(groups) > 0
        assert "成活率" in groups
        assert "验收" in groups
