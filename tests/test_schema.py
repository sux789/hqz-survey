# -*- coding: utf-8 -*-
"""schema 自动化测试 — 验证三张表定义的完整性和一致性。"""
import pytest
from survey.core import schema as S

class TestSchema:
    """验证三张表定义（人工造林/封山育林/退化林修复）。"""

    def test_has_three_tables(self):
        assert len(S.TABLES) == 3

    def test_table_ids(self):
        ids = [t["id"] for t in S.TABLES]
        assert ids == ["table1", "table2", "table3"]
        assert len(ids) == len(set(ids))

    def test_each_table_has_required_keys(self):
        for t in S.TABLES:
            assert "id" in t
            assert "name" in t
            assert "sheet_name" in t
            assert "prefilled_columns" in t
            assert "input_columns" in t

    def test_prefilled_columns_have_col(self):
        """每个预填列必须有 col/key/label 字段。"""
        for t in S.TABLES:
            for col in t["prefilled_columns"]:
                assert "col" in col, f"{t['id']} 预填列 {col.get('key')} 缺少 col 字段"
                assert "key" in col
                assert "label" in col

    def test_input_columns_have_type(self):
        """每个录入列必须有 type 字段。"""
        for t in S.TABLES:
            for col in t["input_columns"]:
                assert "type" in col, f"{t['id']} 录入列 {col.get('key')} 缺少 type"

    def test_enum_has_options(self):
        """enum 类型字段必须有 options。"""
        for t in S.TABLES:
            for col in t["input_columns"]:
                if col.get("type") == "enum":
                    assert col.get("options"), f"{col['key']} enum 缺少 options"

    def test_table1_prefilled_matches_green_cols(self):
        """表1 预填列 15 个（对应 tpl-base 表1 绿色列 A-N + AQ）。"""
        t1 = S.get_table("table1")
        assert len(t1["prefilled_columns"]) == 15
        keys = {c["key"] for c in t1["prefilled_columns"]}
        for k in ("city", "subcompartment", "subcompartment_orig", "forest_compartment",
                  "design_count", "reported_area"):
            assert k in keys, f"table1 预填缺 {k}"

    def test_table2_prefilled_matches_green_cols(self):
        """表2 预填列 24 个（对应 tpl-base 表2 绿色列）。"""
        t2 = S.get_table("table2")
        assert len(t2["prefilled_columns"]) == 24
        keys = {c["key"] for c in t2["prefilled_columns"]}
        for k in ("dominant_species", "pre_land_type", "canopy_cover",
                  "replant_area", "design_count"):
            assert k in keys, f"table2 预填缺 {k}"

    def test_table3_prefilled_matches_green_cols(self):
        """表3 预填列 19 个（对应 tpl-base 表3 绿色列）。"""
        t3 = S.get_table("table3")
        assert len(t3["prefilled_columns"]) == 19
        keys = {c["key"] for c in t3["prefilled_columns"]}
        for k in ("repair_measure", "repair_method", "auxiliary_measure", "design_count"):
            assert k in keys, f"table3 预填缺 {k}"

    def test_get_table(self):
        assert S.get_table("table1") is not None
        assert S.get_table("table5") is None
        assert S.get_table("nonexistent") is None

    def test_get_input_fields(self):
        """get_input_fields 返回全部录入字段（含样地子数组与统计 computed）。"""
        fields = S.get_input_fields("table1")
        keys = {f["key"] for f in fields}
        assert len(fields) > 0
        for k in ("samples", "planted_total", "qualified_count",
                  "qualified_rate", "sample_coord_x", "inspect_time"):
            assert k in keys, f"table1 input 缺 {k}"

    def test_samples_is_sample_array(self):
        """samples 字段类型为 sample_array（子数组结构）。"""
        t1 = S.get_table("table1")
        col = next(c for c in t1["input_columns"] if c["key"] == "samples")
        assert col["type"] == "sample_array"
        assert not col.get("required")

    def test_validate_row_valid(self):
        """测试有效数据通过校验。"""
        data = {
            "inspector": "张三",
            "inspect_time": "2026-08-10",
        }
        ok, errors = S.validate_row("table1", data)
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
        """测试百分比超范围（存比率 0-1，>1 即非法）。"""
        data = {
            "inspector": "张三",
            "inspect_time": "2026-08-10",
            "survival_pass": "1.5",  # 比率超过 1
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
