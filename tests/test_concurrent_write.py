# -*- coding: utf-8 -*-
"""跨进程并发写测试 — busy_timeout 修复验证。

背景：生产 gunicorn -w 2 多进程，进程内 threading.Lock 挡不住跨进程并发写。
SQLite 默认 busy_timeout=0 → 两进程同时写立即抛 database is locked。
修复：_connect() 设 timeout=5 / PRAGMA busy_timeout=5000 → 写冲突排队等待。

测试方法：multiprocessing spawn 两个独立 Python 进程模拟 gunicorn 双 worker，
同时高频写同一临时库，断言全部成功、无 OperationalError(database is locked)。
"""
import multiprocessing as mp
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from survey.core import storage


def _worker_write(proc_id, db_path, n_writes, out_q):
    """子进程入口：独立 import（模拟独立 gunicorn worker 进程）。"""
    try:
        # 重新 import 确保 init_db 由本进程执行一遍（真实部署形态）
        import survey.core.storage as st
        st._DB_PATH = db_path
        st.init_db()
        pid = st.create_project(f"进程{proc_id}项目", f"测试员{proc_id}", "测试乡镇")["id"]
        locked = 0
        for i in range(n_writes):
            try:
                st.save_checkin("sc-x", f"{proc_id}.0", f"{proc_id}.1")
                # 轨迹覆盖式写：模拟每 15s 全量上传（点数递增）
                st.save_track("sc-x", [{"lng": i, "lat": i, "t": str(i)}] * (i + 1))
            except Exception as e:
                if "database is locked" in str(e):
                    locked += 1
                else:
                    raise
        out_q.put((proc_id, "ok", locked))
    except Exception as e:
        out_q.put((proc_id, "error", f"{type(e).__name__}: {e}"))


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "_DB_PATH", tmp_path / "conc.db")
    storage.init_db()
    return tmp_path / "conc.db"


class TestConcurrentWrite:
    """多进程并发写不再抛 database is locked。"""

    def test_two_processes_concurrent_writes(self, tmp_db):
        mp.set_start_method("spawn", force=True)
        out_q = mp.Queue()
        n = 40  # 每进程 40 组写（打卡+轨迹各一次）= 双进程 80 组高频写
        procs = [
            mp.Process(target=_worker_write, args=(i, str(tmp_db), n, out_q))
            for i in range(2)
        ]
        for p in procs:
            p.start()
        results = [out_q.get(timeout=60) for _ in procs]
        for p in procs:
            p.join(timeout=30)
        for r in results:
            assert r[1] == "ok", f"子进程异常: {r}"
        # 修复生效标志：零次 database is locked
        locked_total = sum(r[2] for r in results)
        assert locked_total == 0, f"仍有 {locked_total} 次 database is locked"
        # 数据完整性：最终轨迹可读、打卡时间已写入
        extras = storage.get_extras("sc-x")
        assert extras["checkin_at"], "打卡数据丢失"
        assert len(extras["track"]) == n, "轨迹写入不完整"

    def test_busy_timeout_configured(self, tmp_db):
        """连接层 busy_timeout 已启用（防回归）。"""
        conn = storage._connect()
        try:
            v = conn.execute("PRAGMA busy_timeout").fetchone()[0]
            assert v == 5000, f"busy_timeout={v}，应为 5000"
        finally:
            conn.close()
