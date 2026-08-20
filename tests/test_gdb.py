# -*- coding: utf-8 -*-
"""gdb.py 纯函数测试（不依赖 pyogrio/geopandas）。"""
import io
import zipfile

import pytest

from survey.core import gdb as GDB


class TestClassifyLayer:
    def test_prefix_hit(self):
        assert GDB.classify_layer("人工造林2023") == "人工造林"
        assert GDB.classify_layer("封山育林") == "封山育林"
        assert GDB.classify_layer("退化林修复_澄江") == "退化林修复"

    def test_miss(self):
        assert GDB.classify_layer("抚育区") is None
        assert GDB.classify_layer("小班界") is None
        assert GDB.classify_layer("") is None
        assert GDB.classify_layer(None) is None

    def test_category_table_map_covers_keywords(self):
        for kw in GDB.GDB_CATEGORY_KEYWORDS:
            assert kw in GDB.GDB_CATEGORY_TO_TABLE


class TestResolveGdbField:
    def test_alias_priority(self):
        # subcompartment 别名优先级：调查小班号 > 小班 > 新小班号
        cols = ["小班", "调查小班号", "面积"]
        assert GDB.resolve_gdb_field(cols, "subcompartment") == "调查小班号"

    def test_empty_column_not_blocking(self):
        # props 提供时，空「小班」列不阻塞，挑有值的候选列
        cols = ["小班", "调查小班号"]
        props = {"小班": None, "调查小班号": 5}
        assert GDB.resolve_gdb_field(cols, "subcompartment", props) == "调查小班号"

    def test_miss_returns_none(self):
        assert GDB.resolve_gdb_field(["面积"], "subcompartment") is None

    def test_township_dual_alias(self):
        # 「乡」和「乡镇」都应映射到 township
        assert GDB.resolve_gdb_field(["乡镇"], "township") == "乡镇"
        assert GDB.resolve_gdb_field(["乡"], "township") == "乡"


class TestFixZipName:
    def test_gbk_mojibake_recovered(self):
        # Windows 中文版压缩产物：无 UTF-8 标记，zipfile 按 cp437 误读
        mojibake = "玉溪项目.gdb/a.gdbtable".encode("gbk").decode("cp437")
        info = zipfile.ZipInfo(mojibake)
        info.flag_bits = 0
        assert GDB._fix_zip_name(info) == "玉溪项目.gdb/a.gdbtable"

    def test_utf8_untouched(self):
        info = zipfile.ZipInfo("玉溪项目.gdb/a.gdbtable")
        info.flag_bits = 0x800
        assert GDB._fix_zip_name(info) == "玉溪项目.gdb/a.gdbtable"

    def test_ascii_untouched(self):
        info = zipfile.ZipInfo("a.gdb/a00000001.gdbtable")
        info.flag_bits = 0
        assert GDB._fix_zip_name(info) == "a.gdb/a00000001.gdbtable"


def _make_zip(entries):
    """entries: [(name, bytes)]"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries:
            zf.writestr(name, data)
    buf.seek(0)
    return zipfile.ZipFile(buf)


class TestSafeExtract:
    def test_normal(self, tmp_path):
        zf = _make_zip([("x.gdb/a.gdbtable", b"123")])
        GDB._safe_extract(zf, tmp_path)
        assert (tmp_path / "x.gdb" / "a.gdbtable").read_bytes() == b"123"

    def test_path_traversal_rejected(self, tmp_path):
        zf = _make_zip([("../evil.txt", b"x")])
        with pytest.raises(ValueError, match="非法路径"):
            GDB._safe_extract(zf, tmp_path)

    def test_member_limit(self, tmp_path, monkeypatch):
        monkeypatch.setattr(GDB, "_ZIP_MAX_MEMBERS", 2)
        zf = _make_zip([("a", b""), ("b", b""), ("c", b"")])
        with pytest.raises(ValueError, match="文件数过多"):
            GDB._safe_extract(zf, tmp_path)

    def test_size_limit(self, tmp_path, monkeypatch):
        monkeypatch.setattr(GDB, "_ZIP_MAX_TOTAL_SIZE", 3)
        zf = _make_zip([("a", b"1234")])
        with pytest.raises(ValueError, match="解压后过大"):
            GDB._safe_extract(zf, tmp_path)


class TestFindGdbDirs:
    def test_first_and_second_level(self, tmp_path):
        (tmp_path / "a.gdb").mkdir()
        (tmp_path / "wrapper" / "b.gdb").mkdir(parents=True)
        (tmp_path / "wrapper" / "deep" / "c.gdb").mkdir(parents=True)
        found = GDB.find_gdb_dirs(tmp_path)
        names = sorted(p.name for p in found)
        assert names == ["a.gdb", "b.gdb"]   # 三层 c.gdb 不深挖

    def test_empty(self, tmp_path):
        (tmp_path / "plain").mkdir()
        assert GDB.find_gdb_dirs(tmp_path) == []


class TestScanClassifiedLayers:
    def test_unreadable_gdb_raises_readable_error(self):
        # 不存在的路径 → 可读 ValueError，不再静默返回空 classified
        with pytest.raises(ValueError, match="无法读取 GDB 图层"):
            GDB.scan_classified_layers("/nonexistent/path.gdb")
