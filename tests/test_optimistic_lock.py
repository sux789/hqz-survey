# -*- coding: utf-8 -*-
"""乐观锁（并发控制）自动化测试 — records 版本号 CAS 机制。

场景：两人同时编辑同一个小班同一条记录，
读取时拿到 version，保存时带 base_version 检查，
版本不匹配 → RecordConflict（携带库内最新记录）。
"""
import pytest

from survey.core import storage


@pytest.fixture(autouse=True)
def setup_db(tmp_path, monkeypatch):
    """每个测试用独立临时数据库。"""
    monkeypatch.setattr(storage, "_DB_PATH", tmp_path / "test.db")
    storage.init_db()


@pytest.fixture
def project():
    return storage.create_project("并发测试项目", "测试人", "测试乡镇")


def _save(pid, data, base_version=None, inspector="甲"):
    return storage.upsert_survey_row(
        pid, "table1", "sc-1", data, inspector=inspector, base_version=base_version
    )


class TestVersionBookkeeping:
    """版本号基础记账。"""

    def test_first_create_version_1(self, project):
        rec = _save(project["id"], {"a": 1})
        assert rec["version"] == 1

    def test_compatible_mode_version_increments(self, project):
        """不传 base_version（兼容模式）版本仍递增。"""
        pid = project["id"]
        r1 = _save(pid, {"a": 1})
        r2 = _save(pid, {"a": 2})
        assert r1["version"] == 1
        assert r2["version"] == 2

    def test_get_survey_rows_returns_version(self, project):
        _save(project["id"], {"a": 1})
        rows = storage.get_survey_rows(project["id"], "table1")
        assert len(rows) == 1
        assert rows[0]["version"] == 1


class TestCasUpdate:
    """CAS 更新：版本匹配才写入。"""

    def test_update_with_matching_version(self, project):
        pid = project["id"]
        r1 = _save(pid, {"a": 1})
        r2 = _save(pid, {"a": 2}, base_version=r1["version"])
        assert r2["version"] == 2
        assert r2["data"] == {"a": 2}

    def test_update_with_stale_version_raises(self, project):
        """他人先保存（版本已到 2），我用旧版本 1 保存 → 冲突。"""
        pid = project["id"]
        r1 = _save(pid, {"a": 1})
        _save(pid, {"a": 99}, base_version=r1["version"], inspector="乙")
        with pytest.raises(storage.RecordConflict) as ei:
            _save(pid, {"a": 2}, base_version=r1["version"])
        # 冲突携带库内最新记录（乙的数据 + 新版本号）
        assert ei.value.record["version"] == 2
        assert ei.value.record["data"] == {"a": 99}
        assert ei.value.record["inspector"] == "乙"

    def test_conflict_does_not_overwrite(self, project):
        """冲突抛异常后，库内数据保持他人版本不被破坏。"""
        pid = project["id"]
        r1 = _save(pid, {"a": 1})
        _save(pid, {"a": 99}, base_version=r1["version"], inspector="乙")
        with pytest.raises(storage.RecordConflict):
            _save(pid, {"a": 2}, base_version=r1["version"])
        rows = storage.get_survey_rows(pid, "table1")
        assert rows[0]["data"] == {"a": 99}
        assert rows[0]["version"] == 2

    def test_sequential_updates_chain_versions(self, project):
        """连续用最新版本保存，版本号逐次递增。"""
        pid = project["id"]
        r = _save(pid, {"n": 0})
        for i in range(1, 4):
            r = _save(pid, {"n": i}, base_version=r["version"])
            assert r["version"] == i + 1
        assert storage.get_survey_rows(pid, "table1")[0]["data"] == {"n": 3}


class TestFirstCreateRace:
    """首次创建竞态：base_version=0。"""

    def test_create_when_absent(self, project):
        rec = _save(project["id"], {"a": 1}, base_version=0)
        assert rec["version"] == 1

    def test_create_when_already_exists_raises(self, project):
        """两人同时首次创建：乙先写入，甲 base_version=0 → 冲突。"""
        pid = project["id"]
        _save(pid, {"a": 1}, base_version=0, inspector="乙")
        with pytest.raises(storage.RecordConflict) as ei:
            _save(pid, {"a": 2}, base_version=0)
        assert ei.value.record["version"] == 1
        assert ei.value.record["inspector"] == "乙"


class TestRecordRecreate:
    """记录不存在但带 base_version>=1 的罕见场景。"""

    def test_update_missing_record_inserts_new(self, project):
        """记录不存在（如项目重建）→ 按新建写入，版本 1。"""
        rec = _save(project["id"], {"a": 1}, base_version=5)
        assert rec["version"] == 1
        assert rec["data"] == {"a": 1}


class TestApiConflictResponse:
    """API 层 409 冲突响应（Flask test client）。

    SURVEY_LOCAL_DEV=1 必须在 import survey.user.app 之前设置
    （app 模块级求值 LOCAL_DEV，本地开发模式跳过登录）。
    路由注册在 app 根路径（/api/...），/survey 前缀由部署网关剥离。
    """

    def test_put_409_with_conflict_payload(self, project, monkeypatch):
        import os
        monkeypatch.setenv("SURVEY_LOCAL_DEV", "1")
        from survey.user.app import create_app

        app = create_app(prefix=None)
        app.config["TESTING"] = True
        client = app.test_client()

        pid = project["id"]
        # 保存第一版
        r = client.put(f"/api/projects/{pid}/survey/table1/rows", json={
            "subcompartment_id": "sc-1", "data": {"a": 1},
            "inspector": "甲", "base_version": 0,
        })
        assert r.status_code == 200
        v1 = r.get_json()["version"]

        # 乙基于 v1 保存 → 200，版本 2
        r = client.put(f"/api/projects/{pid}/survey/table1/rows", json={
            "subcompartment_id": "sc-1", "data": {"a": 99},
            "inspector": "乙", "base_version": v1,
        })
        assert r.status_code == 200

        # 甲仍基于 v1 保存 → 409 + conflict 详情
        r = client.put(f"/api/projects/{pid}/survey/table1/rows", json={
            "subcompartment_id": "sc-1", "data": {"a": 2},
            "inspector": "甲", "base_version": v1,
        })
        assert r.status_code == 409
        body = r.get_json()
        assert "conflict" in body
        cf = body["conflict"]
        assert cf["version"] == 2
        assert cf["data"] == {"a": 99}
        assert cf["inspector"] == "乙"
        assert "updated_at" in cf


class TestApiStoreFalseStrip:
    """store:false 派生字段（成活率等级/面积分派）不落库：
    PUT 端点剔除这些 key，普通 computed（如合格率统计）与手输字段保留。
    """

    def test_put_strips_derived_keys(self, project, monkeypatch):
        monkeypatch.setenv("SURVEY_LOCAL_DEV", "1")
        from survey.user.app import create_app

        app = create_app(prefix=None)
        app.config["TESTING"] = True
        client = app.test_client()

        pid = project["id"]
        r = client.put(f"/api/projects/{pid}/survey/table1/rows", json={
            "subcompartment_id": "sc-1",
            "data": {
                "remark": "保留",
                "survival_pass": "0.95",     # store:false → 剔除
                "survival_replant": "",       # store:false → 剔除
                "verified_pass": "999",       # store:false → 剔除
                "construction_area": "888",   # store:false → 剔除
                "qualified_rate": 0.95,       # 普通 computed 统计 → 保留
            },
            "inspector": "甲", "base_version": 0,
        })
        assert r.status_code == 200
        rows = storage.get_survey_rows(pid, "table1")
        assert rows[0]["data"] == {"remark": "保留", "qualified_rate": 0.95}
