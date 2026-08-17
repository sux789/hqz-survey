# -*- coding: utf-8 -*-
"""SQLite 数据层 — 项目/小班信息/录入记录。

小班最小粒度（一对一模型）：调查表1~5 与照片/轨迹/打卡均与小区一对一，
唯一键 (project_id, table_id, subcompartment_id)。预填黄色列由 schema
map_subcompartment_to_prefilled 从 subcompartment_rows 实时映射，不独立存储。

表结构:
  projects                 项目（名称/创建人/时间）
  subcompartment_batches   小班信息批次（一次上传 = 一个批次，关联 project）
  subcompartment_rows      小班信息行（每行一个独立小班，最小粒度锚点）
  subcompartment_extras    小班扩展数据（打卡/轨迹/照片，归属小班，一对一）
  records                  调查录入数据（白色列，每小班每表一行，含 subcompartment_id）

所有写入操作线程安全（SQLite check_same_thread=False + 全局锁）。
"""
import json
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path

_DB_PATH = Path(__file__).resolve().parent.parent / "survey.db"
_lock = threading.Lock()


def _to_uint(v):
    """转 unsigned int（林班/小班号专用）。空/None/无效/负数 → 0。

    GDB 中林班/小班常以 float(1.0) 或 str("1.0") 形式存在，
    本函数统一规范化为非负整数。
    """
    if v is None or v == "":
        return 0
    try:
        f = float(v)
    except (ValueError, TypeError):
        return 0
    if f != f or f < 0:  # NaN 或负数
        return 0
    return int(f)


def _connect():
    """获取数据库连接。"""
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """初始化表结构。"""
    with _lock:
        conn = _connect()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS projects (
                    id          TEXT PRIMARY KEY,
                    name        TEXT NOT NULL,
                    creator     TEXT NOT NULL,
                    township    TEXT DEFAULT '',
                    created_at  TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS records (
                    id                TEXT PRIMARY KEY,
                    project_id        TEXT NOT NULL,
                    table_id          TEXT NOT NULL,
                    subcompartment_id TEXT NOT NULL,
                    data_json         TEXT NOT NULL,
                    inspector         TEXT DEFAULT '',
                    created_at        TEXT NOT NULL,
                    updated_at        TEXT NOT NULL,
                    FOREIGN KEY (project_id) REFERENCES projects(id),
                    UNIQUE(project_id, table_id, subcompartment_id)
                );

                CREATE TABLE IF NOT EXISTS project_members (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id  TEXT NOT NULL,
                    user_id     INTEGER NOT NULL,
                    username    TEXT DEFAULT '',
                    added_at    TEXT NOT NULL,
                    UNIQUE(project_id, user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_members_user
                    ON project_members(user_id);

                CREATE TABLE IF NOT EXISTS subcompartment_batches (
                    id              TEXT PRIMARY KEY,
                    project_id      TEXT NOT NULL,
                    file_name       TEXT NOT NULL,
                    file_hash       TEXT NOT NULL UNIQUE,
                    project_name    TEXT DEFAULT '',
                    project_location TEXT DEFAULT '',
                    total_area      REAL DEFAULT 0,
                    start_date      TEXT DEFAULT '',
                    uploader        TEXT DEFAULT '',
                    row_count       INTEGER DEFAULT 0,
                    uploaded_at     TEXT NOT NULL,
                    FOREIGN KEY (project_id) REFERENCES projects(id)
                );
                CREATE INDEX IF NOT EXISTS idx_batches_project
                    ON subcompartment_batches(project_id);

                CREATE TABLE IF NOT EXISTS subcompartment_rows (
                    id                  TEXT PRIMARY KEY,
                    batch_id            TEXT NOT NULL,
                    row_index           INTEGER NOT NULL,
                    data_json           TEXT NOT NULL,
                    township            TEXT DEFAULT '',
                    village             TEXT DEFAULT '',
                    forest_compartment  INTEGER DEFAULT 0,
                    subcompartment      INTEGER DEFAULT 0,
                    subcompartment_label TEXT DEFAULT '',
                    tending_area        REAL DEFAULT 0,
                    UNIQUE(batch_id, row_index),
                    FOREIGN KEY (batch_id) REFERENCES subcompartment_batches(id)
                );
                CREATE INDEX IF NOT EXISTS idx_rows_batch
                    ON subcompartment_rows(batch_id);
                CREATE INDEX IF NOT EXISTS idx_rows_township
                    ON subcompartment_rows(township);

                CREATE TABLE IF NOT EXISTS subcompartment_extras (
                    id                      TEXT PRIMARY KEY,
                    subcompartment_row_id   TEXT NOT NULL UNIQUE,
                    checkin_at              TEXT DEFAULT '',
                    checkin_lng             TEXT DEFAULT '',
                    checkin_lat             TEXT DEFAULT '',
                    track_json              TEXT DEFAULT '[]',
                    photos_json             TEXT DEFAULT '[]',
                    updated_at              TEXT NOT NULL,
                    FOREIGN KEY (subcompartment_row_id) REFERENCES subcompartment_rows(id)
                );

                CREATE TABLE IF NOT EXISTS gdb_files (
                    id          TEXT PRIMARY KEY,
                    project_id  TEXT NOT NULL,
                    file_name   TEXT NOT NULL,
                    file_hash   TEXT NOT NULL,
                    layers_json TEXT NOT NULL,
                    uploaded_by TEXT NOT NULL,
                    uploaded_at TEXT NOT NULL,
                    FOREIGN KEY (project_id) REFERENCES projects(id)
                );
                CREATE INDEX IF NOT EXISTS idx_gdb_project
                    ON gdb_files(project_id);
            """)
            # records 表：旧库含 subtable_id/row_index（一对多旧轨）→ 重建为一对一
            # 新库已由 CREATE TABLE 建好正确结构；此处仅对旧库迁移。
            # 同一小班同表多条：取 updated_at 最新一条；subcompartment_id='' 的脏数据丢弃。
            old_cols = {r["name"] for r in conn.execute("PRAGMA table_info('records')")}
            if "subtable_id" in old_cols or "row_index" in old_cols:
                if "subcompartment_id" in old_cols:
                    # 旧库已有 subcompartment_id 列（一对一过渡期）→ 去重保留最新
                    conn.executescript("""
                        CREATE TABLE records_new (
                            id                TEXT PRIMARY KEY,
                            project_id        TEXT NOT NULL,
                            table_id          TEXT NOT NULL,
                            subcompartment_id TEXT NOT NULL,
                            data_json         TEXT NOT NULL,
                            inspector         TEXT DEFAULT '',
                            created_at        TEXT NOT NULL,
                            updated_at        TEXT NOT NULL,
                            FOREIGN KEY (project_id) REFERENCES projects(id),
                            UNIQUE(project_id, table_id, subcompartment_id)
                        );
                        INSERT INTO records_new
                            (id, project_id, table_id, subcompartment_id, data_json, inspector, created_at, updated_at)
                        SELECT id, project_id, table_id, subcompartment_id, data_json, inspector, created_at, updated_at
                        FROM records
                        WHERE subcompartment_id != ''
                          AND id IN (
                              SELECT id FROM records r2
                              WHERE subcompartment_id != ''
                              GROUP BY project_id, table_id, subcompartment_id
                              HAVING MAX(updated_at)
                          );
                        DROP TABLE records;
                        ALTER TABLE records_new RENAME TO records;
                    """)
                else:
                    # 最旧库无 subcompartment_id 列（纯一对多模型，数据不兼容）→ 直接重建空表
                    conn.executescript("""
                        DROP TABLE records;
                        CREATE TABLE records (
                            id                TEXT PRIMARY KEY,
                            project_id        TEXT NOT NULL,
                            table_id          TEXT NOT NULL,
                            subcompartment_id TEXT NOT NULL,
                            data_json         TEXT NOT NULL,
                            inspector         TEXT DEFAULT '',
                            created_at        TEXT NOT NULL,
                            updated_at        TEXT NOT NULL,
                            FOREIGN KEY (project_id) REFERENCES projects(id),
                            UNIQUE(project_id, table_id, subcompartment_id)
                        );
                    """)
            # records 索引（新库或已迁移库才创建；旧库迁移块内已建）
            conn.execute("CREATE INDEX IF NOT EXISTS idx_records_project_table ON records(project_id, table_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_records_subcompartment ON records(subcompartment_id)")
            # 旧 prefilled 表停用删除（预填数据统一由 map_subcompartment_to_prefilled 实时映射）
            conn.execute("DROP TABLE IF EXISTS prefilled")
            # subcompartment_rows 新增 GDB 关联列（已有库兼容）
            for col in ("gdb_id", "gdb_feature_id", "geom_geojson", "gdb_layer",
                        "project_name", "category"):
                try:
                    conn.execute(f"ALTER TABLE subcompartment_rows ADD COLUMN {col} TEXT DEFAULT ''")
                except sqlite3.OperationalError:
                    pass
            # 存量数据规范化：林班/小班列转 unsigned int（INTEGER 亲和），并重建 label
            # SQLite 列亲和一旦建立便无法用 CREATE TABLE IF NOT EXISTS 修改。
            # 旧库该列为 TEXT 亲和——CAST 后存回仍会被转回文本，故检测到非 INTEGER
            # 亲和时，通过「建新表→复制→删除→改名」重建为 INTEGER 列。
            # MAX(0, ...) 兜底保证非负（与 _to_uint 语义一致）。
            col_row = conn.execute(
                "SELECT type FROM pragma_table_info('subcompartment_rows') WHERE name='forest_compartment'"
            ).fetchone()
            col_type = col_row[0] if col_row else ""
            if col_type != "INTEGER":
                conn.executescript("""
                    CREATE TABLE subcompartment_rows_new (
                        id                   TEXT PRIMARY KEY,
                        batch_id             TEXT NOT NULL,
                        row_index            INTEGER NOT NULL,
                        data_json            TEXT NOT NULL,
                        township             TEXT DEFAULT '',
                        village              TEXT DEFAULT '',
                        forest_compartment   INTEGER DEFAULT 0,
                        subcompartment       INTEGER DEFAULT 0,
                        subcompartment_label TEXT DEFAULT '',
                        tending_area         REAL DEFAULT 0,
                        gdb_id               TEXT DEFAULT '',
                        gdb_feature_id       TEXT DEFAULT '',
                        geom_geojson         TEXT DEFAULT '',
                        gdb_layer            TEXT DEFAULT '',
                        project_name         TEXT DEFAULT '',
                        category             TEXT DEFAULT '',
                        UNIQUE(batch_id, row_index),
                        FOREIGN KEY (batch_id) REFERENCES subcompartment_batches(id)
                    );
                    INSERT INTO subcompartment_rows_new
                        (id, batch_id, row_index, data_json, township, village,
                         forest_compartment, subcompartment, subcompartment_label,
                         tending_area, gdb_id, gdb_feature_id, geom_geojson, gdb_layer,
                         project_name, category)
                    SELECT
                        id, batch_id, row_index, data_json, township, village,
                        MAX(0, COALESCE(CAST(forest_compartment AS INTEGER), 0)),
                        MAX(0, COALESCE(CAST(subcompartment AS INTEGER), 0)),
                        CASE WHEN MAX(0, COALESCE(CAST(forest_compartment AS INTEGER), 0)) > 0
                             THEN MAX(0, COALESCE(CAST(forest_compartment AS INTEGER), 0)) || '-' ||
                                  MAX(0, COALESCE(CAST(subcompartment AS INTEGER), 0))
                             ELSE CAST(MAX(0, COALESCE(CAST(subcompartment AS INTEGER), 0)) AS TEXT)
                        END,
                        tending_area,
                        COALESCE(gdb_id, ''), COALESCE(gdb_feature_id, ''), COALESCE(geom_geojson, ''),
                        COALESCE(gdb_layer, ''),
                        COALESCE(project_name, ''), COALESCE(category, '')
                    FROM subcompartment_rows;
                    DROP TABLE subcompartment_rows;
                    ALTER TABLE subcompartment_rows_new RENAME TO subcompartment_rows;
                    CREATE INDEX IF NOT EXISTS idx_rows_batch
                        ON subcompartment_rows(batch_id);
                    CREATE INDEX IF NOT EXISTS idx_rows_township
                        ON subcompartment_rows(township);
                """)
            else:
                # 类型已是 INTEGER：值规范化兜底（CAST 此时能真正生效）
                conn.executescript("""
                    UPDATE subcompartment_rows
                       SET forest_compartment = MAX(0, COALESCE(CAST(forest_compartment AS INTEGER), 0));
                    UPDATE subcompartment_rows
                       SET subcompartment = MAX(0, COALESCE(CAST(subcompartment AS INTEGER), 0));
                    UPDATE subcompartment_rows
                       SET subcompartment_label =
                           CAST(forest_compartment AS INTEGER) || '-' || CAST(subcompartment AS INTEGER);
                """)
            # 项目名称索引（按项目名称导出/调查筛选用）
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_rows_project_name "
                "ON subcompartment_rows(project_name)"
            )
            conn.commit()
        finally:
            conn.close()


# ── 项目 CRUD ──

def create_project(name, creator, township=""):
    """创建项目，返回 project dict。"""
    pid = uuid.uuid4().hex[:12]
    now = datetime.now().isoformat(timespec="seconds")
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO projects (id, name, creator, township, created_at) VALUES (?,?,?,?,?)",
                (pid, name, creator, township, now),
            )
            conn.commit()
        finally:
            conn.close()
    return {"id": pid, "name": name, "creator": creator, "township": township, "created_at": now}


def list_projects():
    """列出所有项目。"""
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_project(pid):
    """获取单个项目。"""
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ── 录入记录（白色列，小班一对一）──
# 每小班每表至多一条，唯一键 (project_id, table_id, subcompartment_id)。
# 预填数据（黄色列）不再独立存储，统一由 schema.map_subcompartment_to_prefilled
# 从 subcompartment_rows.data_json 实时映射。


def upsert_survey_row(pid, table_id, subcompartment_id, data, inspector=""):
    """小班单行 upsert：每小班每表一行，有则更新 data_json，无则插入。

    Args:
        pid: 项目 ID
        table_id: 表 ID（如 table1）
        subcompartment_id: 小班行 ID（subcompartment_rows.id）
        data: 该行所有 input 字段值的 dict
        inspector: 验收人

    Returns:
        record dict
    """
    now = datetime.now().isoformat(timespec="seconds")
    data_str = json.dumps(data, ensure_ascii=False)
    with _lock:
        conn = _connect()
        try:
            # 用 INSERT ... ON CONFLICT 原子 upsert，避免先 SELECT 再改的并发竞态
            rid = uuid.uuid4().hex[:12]
            conn.execute(
                """INSERT INTO records (id, project_id, table_id, subcompartment_id,
                   data_json, inspector, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(project_id, table_id, subcompartment_id) DO UPDATE SET
                     data_json=excluded.data_json,
                     inspector=excluded.inspector,
                     updated_at=excluded.updated_at""",
                (rid, pid, table_id, subcompartment_id, data_str, inspector, now, now),
            )
            # 取实际生效的 id（冲突时为原 id）
            row = conn.execute(
                "SELECT id FROM records WHERE project_id=? AND table_id=? AND subcompartment_id=?",
                (pid, table_id, subcompartment_id),
            ).fetchone()
            rid = row["id"] if row else rid
            conn.commit()
        finally:
            conn.close()
    return {"id": rid, "project_id": pid, "table_id": table_id,
            "subcompartment_id": subcompartment_id, "data": data,
            "inspector": inspector, "updated_at": now}


def get_survey_rows(pid, table_id, project_name=None):
    """获取某项目某表所有小班的调查行（网格模式）。

    Args:
        pid: 项目 ID
        table_id: 表 id（table1~table5）
        project_name: 可选，按项目名称过滤（仅返回该项目名称下的小班调查行）

    Returns:
        list[dict]：每项含 id/subcompartment_id/data/inspector/updated_at
    """
    conn = _connect()
    try:
        if project_name:
            rows = conn.execute(
                """SELECT r.* FROM records r
                   JOIN subcompartment_rows sr ON r.subcompartment_id = sr.id
                   LEFT JOIN gdb_files gf ON sr.gdb_id = gf.id
                   LEFT JOIN subcompartment_batches sb ON sr.batch_id = sb.id
                   WHERE r.project_id = ? AND r.table_id = ?
                     AND (gf.project_id = ? OR sb.project_id = ?) AND sr.project_name = ?
                   ORDER BY r.updated_at""",
                (pid, table_id, pid, pid, project_name),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM records WHERE project_id=? AND table_id=? "
                "ORDER BY updated_at",
                (pid, table_id),
            ).fetchall()
        result = []
        for r in rows:
            result.append({
                "id": r["id"],
                "subcompartment_id": r["subcompartment_id"],
                "data": json.loads(r["data_json"]),
                "inspector": r["inspector"],
                "updated_at": r["updated_at"],
            })
        return result
    finally:
        conn.close()


def get_all_records(pid):
    """获取某项目所有表的全部记录（导出用）。"""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM records WHERE project_id=? ORDER BY table_id, created_at",
            (pid,),
        ).fetchall()
        result = {}
        for r in rows:
            tid = r["table_id"]
            if tid not in result:
                result[tid] = []
            item = {
                "id": r["id"],
                "data": json.loads(r["data_json"]),
                "inspector": r["inspector"],
                "subcompartment_id": r["subcompartment_id"],
            }
            result[tid].append(item)
        return result
    finally:
        conn.close()


# ── 项目成员 ──

def add_project_member(pid, user_id, username=""):
    """添加项目成员。"""
    now = datetime.now().isoformat(timespec="seconds")
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO project_members (project_id, user_id, username, added_at) VALUES (?,?,?,?)",
                (pid, user_id, username, now),
            )
            conn.commit()
        finally:
            conn.close()


def remove_project_member(pid, user_id):
    """移除项目成员。"""
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "DELETE FROM project_members WHERE project_id=? AND user_id=?",
                (pid, user_id),
            )
            conn.commit()
        finally:
            conn.close()


def get_project_members(pid):
    """获取项目成员列表。"""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT user_id, username, added_at FROM project_members WHERE project_id=? ORDER BY added_at",
            (pid,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_user_projects(user_id):
    """获取某用户参与的项目列表。"""
    conn = _connect()
    try:
        rows = conn.execute(
            """SELECT p.* FROM projects p
               JOIN project_members m ON p.id = m.project_id
               WHERE m.user_id=? ORDER BY p.created_at DESC""",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ════════════════════════════════════════════
# 小班信息批次 / 行 / 扩展数据
# ════════════════════════════════════════════

def find_batch_by_hash(file_hash):
    """按文件哈希查重，返回 batch dict 或 None。"""
    conn = _connect()
    try:
        r = conn.execute(
            "SELECT * FROM subcompartment_batches WHERE file_hash=?", (file_hash,)
        ).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def create_batch(project_id, file_name, file_hash, project_name="",
                 project_location="", total_area=0, start_date="", uploader="", row_count=0):
    """创建小班信息批次，返回 batch dict。"""
    bid = uuid.uuid4().hex[:12]
    now = datetime.now().isoformat(timespec="seconds")
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """INSERT INTO subcompartment_batches
                   (id, project_id, file_name, file_hash, project_name, project_location,
                    total_area, start_date, uploader, row_count, uploaded_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (bid, project_id, file_name, file_hash, project_name, project_location,
                 total_area, start_date, uploader, row_count, now),
            )
            conn.commit()
        finally:
            conn.close()
    return {
        "id": bid, "project_id": project_id, "file_name": file_name, "file_hash": file_hash,
        "project_name": project_name, "project_location": project_location,
        "total_area": total_area, "start_date": start_date, "uploader": uploader,
        "row_count": row_count, "uploaded_at": now,
    }


def list_batches(project_id):
    """列出某项目下所有小班批次。"""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM subcompartment_batches WHERE project_id=? ORDER BY uploaded_at DESC",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_all_batches():
    """列出全部小班批次（跨项目，按上传时间倒序）。

    用于上传列表页：不再按项目过滤，所有批次统一展示。
    """
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM subcompartment_batches ORDER BY uploaded_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_batch(batch_id):
    """获取单个批次。"""
    conn = _connect()
    try:
        r = conn.execute(
            "SELECT * FROM subcompartment_batches WHERE id=?", (batch_id,)
        ).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def delete_batch(batch_id):
    """物理删除批次 + 所有小班 + 扩展数据 + 关联录入记录。

    一对一模型：小班是调查记录的锚点，小班删除则其调查记录一并删除。
    """
    with _lock:
        conn = _connect()
        try:
            # 获取该批次下所有小班 row_id
            row_ids = [r["id"] for r in conn.execute(
                "SELECT id FROM subcompartment_rows WHERE batch_id=?", (batch_id,)
            ).fetchall()]
            if row_ids:
                placeholders = ",".join("?" * len(row_ids))
                conn.execute(f"DELETE FROM subcompartment_extras WHERE subcompartment_row_id IN ({placeholders})", row_ids)
                # 一对一：小班删除则调查记录一并删除（subcompartment_id 为 NOT NULL，不可清空）
                conn.execute(f"DELETE FROM records WHERE subcompartment_id IN ({placeholders})", row_ids)
                conn.execute(f"DELETE FROM subcompartment_rows WHERE id IN ({placeholders})", row_ids)
            conn.execute("DELETE FROM subcompartment_batches WHERE id=?", (batch_id,))
            conn.commit()
        finally:
            conn.close()


def delete_project(project_id):
    """物理删除项目及其所有关联数据（GDB/批次/小班/扩展/录入记录/成员）。

    用于测试清理或管理员彻底删除项目。删除后数据不可恢复。
    小班行同时覆盖 gdb_id 关联（新 GDB 上传流程）和 batch_id 关联（旧批次流程）。
    """
    with _lock:
        conn = _connect()
        try:
            # 0. 项目名（用于兜底清理孤儿行）
            proj = conn.execute("SELECT name FROM projects WHERE id=?", (project_id,)).fetchone()
            proj_name = proj["name"] if proj else None
            # 1. 收集该项目下所有小班行（gdb_id 或 batch_id 两种关联方式）
            row_ids = [r["id"] for r in conn.execute(
                "SELECT sr.id FROM subcompartment_rows sr "
                "WHERE sr.gdb_id IN (SELECT id FROM gdb_files WHERE project_id=?) "
                "   OR sr.batch_id IN (SELECT id FROM subcompartment_batches WHERE project_id=?)",
                (project_id, project_id)
            ).fetchall()]
            # 兜底：gdb_files/batches 已删但行残留（孤儿行）的，按项目名清理
            # （项目名全局唯一，同名行必属本项目或孤儿行）
            if proj_name:
                row_ids += [r["id"] for r in conn.execute(
                    "SELECT id FROM subcompartment_rows WHERE project_name=? AND id NOT IN "
                    "(SELECT sr.id FROM subcompartment_rows sr WHERE sr.gdb_id IN "
                    "(SELECT id FROM gdb_files WHERE project_id<>?))",
                    (proj_name, project_id)
                ).fetchall()]
            # 去重
            row_ids = list(dict.fromkeys(row_ids))
            if row_ids:
                ph = ",".join("?" * len(row_ids))
                conn.execute(f"DELETE FROM subcompartment_extras WHERE subcompartment_row_id IN ({ph})", row_ids)
                conn.execute(f"DELETE FROM subcompartment_rows WHERE id IN ({ph})", row_ids)
            # 2. 删除该项目下所有批次、GDB 文件记录
            conn.execute("DELETE FROM subcompartment_batches WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM gdb_files WHERE project_id=?", (project_id,))
            # 3. 删除录入记录、成员（prefilled 表已停用）
            conn.execute("DELETE FROM records WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM project_members WHERE project_id=?", (project_id,))
            # 4. 删除项目本身
            conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
            conn.commit()
        finally:
            conn.close()


def insert_subcompartment_row(batch_id, row_index, data, township="", village="",
                              forest_compartment=0, subcompartment=0, label="", tending_area=0):
    """插入一行小班信息，返回 row dict。"""
    rid = uuid.uuid4().hex[:12]
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """INSERT INTO subcompartment_rows
                   (id, batch_id, row_index, data_json, township, village,
                    forest_compartment, subcompartment, subcompartment_label, tending_area)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (rid, batch_id, row_index, json.dumps(data, ensure_ascii=False),
                 township, village, forest_compartment, subcompartment, label, tending_area),
            )
            conn.commit()
        finally:
            conn.close()
    return {
        "id": rid, "batch_id": batch_id, "row_index": row_index,
        "data": data, "township": township, "village": village,
        "forest_compartment": forest_compartment, "subcompartment": subcompartment,
        "subcompartment_label": label, "tending_area": tending_area,
    }


def list_subcompartment_rows(batch_id):
    """列出某批次下所有小班行。"""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM subcompartment_rows WHERE batch_id=? ORDER BY row_index",
            (batch_id,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def search_subcompartment_rows(batch_id, query=""):
    """按乡镇/村/林班/小班号 模糊搜索小班。

    支持空格分词 AND 搜索：
      「平山 39 1」 → 3 个关键词，每行必须同时匹配全部关键词
      每个关键词在 township/village/forest_compartment/subcompartment/
      subcompartment_label/data_json 任一字段命中即可
    query 为空则返回全部（按 row_index 升序）。
    """
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM subcompartment_rows WHERE batch_id=? ORDER BY row_index",
            (batch_id,),
        ).fetchall()
        items = [_row_to_dict(r) for r in rows]
        if not query or not query.strip():
            return items

        # 空格/逗号/斜杠分词 — 每个关键词都必须命中
        import re
        keywords = [k.strip() for k in re.split(r"[\s,]+", query.strip()) if k.strip()]
        if not keywords:
            return items

        def match_one(item, kw):
            """单关键词匹配：检查所有可搜索字段。"""
            # 林班/小班常用「39-1」「39 1」「39/1」格式，自动拆分
            # 此处直接用 label 和各字段做包含匹配
            hay_fields = [
                item.get("township", ""),
                item.get("village", ""),
                item.get("forest_compartment", ""),
                item.get("subcompartment", ""),
                item.get("subcompartment_label", ""),
            ]
            data = item.get("data") or {}
            # 额外可搜索字段：州、优势树种、土地权属
            for k in ("州", "优势树种", "土地权属", "林种", "地类"):
                if k in data:
                    hay_fields.append(str(data[k]))
            # 林班-小班 / 林班 小班 组合字段
            fc = str(item.get("forest_compartment", ""))
            sc = str(item.get("subcompartment", ""))
            hay_fields.append(f"{fc}-{sc}")
            hay_fields.append(f"{fc} {sc}")
            hay_fields.append(f"{fc}/{sc}")
            kw_lower = kw.lower()
            for h in hay_fields:
                if h and kw_lower in str(h).lower():
                    return True
            return False

        return [it for it in items
                if all(match_one(it, kw) for kw in keywords)]
    finally:
        conn.close()


def get_subcompartment_row(row_id):
    """获取单个小班行（含完整字段）。"""
    conn = _connect()
    try:
        r = conn.execute(
            "SELECT * FROM subcompartment_rows WHERE id=?", (row_id,)
        ).fetchone()
        return _row_to_dict(r) if r else None
    finally:
        conn.close()


def update_subcompartment_row(row_id, data_updates, field_updates=None):
    """更新小班行的 data_json 及定位字段。

    Args:
        row_id: 小班行 ID
        data_updates: dict，要合并到 data_json 的字段（覆盖同 key）
        field_updates: dict，可选，更新 township/village/forest_compartment/
                       subcompartment/subcompartment_label/tending_area 等列

    Returns:
        更新后的 row dict，或 None（行不存在）
    """
    with _lock:
        conn = _connect()
        try:
            r = conn.execute(
                "SELECT * FROM subcompartment_rows WHERE id=?", (row_id,)
            ).fetchone()
            if not r:
                return None
            existing = json.loads(r["data_json"]) if r["data_json"] else {}
            existing.update(data_updates)
            conn.execute(
                "UPDATE subcompartment_rows SET data_json=? WHERE id=?",
                (json.dumps(existing, ensure_ascii=False), row_id),
            )
            if field_updates:
                allowed = ("township", "village", "forest_compartment",
                           "subcompartment", "subcompartment_label", "tending_area")
                sets, vals = [], []
                for k, v in field_updates.items():
                    if k in allowed:
                        sets.append(f"{k}=?")
                        vals.append(v)
                if sets:
                    vals.append(row_id)
                    conn.execute(
                        f"UPDATE subcompartment_rows SET {', '.join(sets)} WHERE id=?",
                        vals,
                    )
            conn.commit()
        finally:
            conn.close()
    return get_subcompartment_row(row_id)


def _row_to_dict(r):
    """把 DB row 转成业务 dict。"""
    if r is None:
        return None
    d = dict(r)
    try:
        d["data"] = json.loads(d.pop("data_json"))
    except (KeyError, json.JSONDecodeError):
        d["data"] = {}
    return d


# ── 小班扩展数据（打卡/轨迹/照片）──

def get_extras(row_id):
    """获取小班扩展数据，不存在则返回空模板。"""
    conn = _connect()
    try:
        r = conn.execute(
            "SELECT * FROM subcompartment_extras WHERE subcompartment_row_id=?",
            (row_id,),
        ).fetchone()
        if not r:
            return {
                "subcompartment_row_id": row_id,
                "checkin_at": "",
                "checkin_lng": "",
                "checkin_lat": "",
                "track": [],
                "photos": [],
                "updated_at": "",
            }
        d = dict(r)
        try:
            d["track"] = json.loads(d.pop("track_json") or "[]")
        except json.JSONDecodeError:
            d["track"] = []
        try:
            d["photos"] = json.loads(d.pop("photos_json") or "[]")
        except json.JSONDecodeError:
            d["photos"] = []
        return d
    finally:
        conn.close()


def save_checkin(row_id, lng, lat):
    """打卡：写入时间+经纬度。"""
    now = datetime.now().isoformat(timespec="seconds")
    with _lock:
        conn = _connect()
        try:
            _upsert_extras(conn, row_id, {
                "checkin_at": now, "checkin_lng": str(lng), "checkin_lat": str(lat),
                "updated_at": now,
            })
            conn.commit()
        finally:
            conn.close()
    return {"checkin_at": now, "checkin_lng": lng, "checkin_lat": lat}


def save_track(row_id, points):
    """保存轨迹（覆盖式）。points = [{lng, lat, t}, ...]"""
    now = datetime.now().isoformat(timespec="seconds")
    with _lock:
        conn = _connect()
        try:
            _upsert_extras(conn, row_id, {
                "track_json": json.dumps(points, ensure_ascii=False),
                "updated_at": now,
            })
            conn.commit()
        finally:
            conn.close()
    return {"track_count": len(points)}


def save_photos(row_id, photos):
    """保存照片列表（覆盖式）。photos = [{name, lng, lat, t, url}, ...]"""
    now = datetime.now().isoformat(timespec="seconds")
    with _lock:
        conn = _connect()
        try:
            _upsert_extras(conn, row_id, {
                "photos_json": json.dumps(photos, ensure_ascii=False),
                "updated_at": now,
            })
            conn.commit()
        finally:
            conn.close()
    return {"photo_count": len(photos)}


def _upsert_extras(conn, row_id, fields):
    """upsert 小班扩展数据。fields 是要更新的列。"""
    cols = list(fields.keys())
    # 先尝试插入空行（若不存在）
    conn.execute(
        "INSERT OR IGNORE INTO subcompartment_extras (id, subcompartment_row_id, updated_at) VALUES (?,?,?)",
        (uuid.uuid4().hex[:12], row_id, fields.get("updated_at", "")),
    )
    # 更新指定字段
    set_clause = ", ".join(f"{c}=?" for c in cols)
    values = [fields[c] for c in cols] + [row_id]
    conn.execute(
        f"UPDATE subcompartment_extras SET {set_clause} WHERE subcompartment_row_id=?",
        values,
    )


# ════════════════════════════════════════════
# GDB 文件管理
# ════════════════════════════════════════════

def create_gdb_file(project_id, file_name, file_hash, layers, uploaded_by, gid=None):
    """创建 GDB 文件记录，返回 gdb dict。

    gid 必须与上传处理时使用的 gid 一致（目录名、subcompartment_rows.gdb_id
    均以此为准），否则记录与实物/数据错位导致「查看图层/预览」404。
    未传入时自动生成。
    """
    if not gid:
        gid = uuid.uuid4().hex[:12]
    now = datetime.now().isoformat(timespec="seconds")
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """INSERT INTO gdb_files (id, project_id, file_name, file_hash, layers_json, uploaded_by, uploaded_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (gid, project_id, file_name, file_hash,
                 json.dumps(layers, ensure_ascii=False), uploaded_by, now),
            )
            conn.commit()
        finally:
            conn.close()
    return {
        "id": gid, "project_id": project_id, "file_name": file_name,
        "file_hash": file_hash, "layers": layers, "uploaded_by": uploaded_by,
        "uploaded_at": now,
    }


def list_gdb_files(project_id=None):
    """列出 GDB 文件（可按项目过滤）。"""
    conn = _connect()
    try:
        if project_id:
            rows = conn.execute(
                "SELECT * FROM gdb_files WHERE project_id=? ORDER BY uploaded_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM gdb_files ORDER BY uploaded_at DESC"
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["layers"] = json.loads(d.pop("layers_json") or "[]")
            except (KeyError, json.JSONDecodeError):
                d["layers"] = []
            result.append(d)
        return result
    finally:
        conn.close()


def get_gdb_file(gid):
    """获取单个 GDB 文件记录。"""
    conn = _connect()
    try:
        r = conn.execute("SELECT * FROM gdb_files WHERE id=?", (gid,)).fetchone()
        if not r:
            return None
        d = dict(r)
        try:
            d["layers"] = json.loads(d.pop("layers_json") or "[]")
        except json.JSONDecodeError:
            d["layers"] = []
        return d
    finally:
        conn.close()


def find_gdb_by_hash(file_hash):
    """按文件哈希查重。"""
    conn = _connect()
    try:
        r = conn.execute("SELECT * FROM gdb_files WHERE file_hash=?", (file_hash,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def delete_gdb_file(gid):
    """删除 GDB 文件记录（文件本身由调用方删除）。"""
    with _lock:
        conn = _connect()
        try:
            conn.execute("DELETE FROM gdb_files WHERE id=?", (gid,))
            conn.commit()
        finally:
            conn.close()


def list_project_subcompartment_rows(pid, project_name=None, category=None):
    """列出某项目下所有小班行（含 GDB 导入的 + 旧批次导入的）。

    用于 user 端地图渲染 / 网格调查筛选：返回所有小班 + geom_geojson（若有）。
    GDB 导入的行 gdb_id 非空、batch_id 为空；旧 xlsx 导入的 batch_id 非空。
    两种来源合并返回。

    Args:
        pid: 项目 ID
        project_name: 可选，按项目名称过滤（GDB 导入产生）
        category: 可选，按分类过滤（人工造林/封山育林/退化林修复/水利水保/草原）
    """
    conn = _connect()
    try:
        sql = """SELECT sr.* FROM subcompartment_rows sr
                 LEFT JOIN subcompartment_batches sb ON sr.batch_id = sb.id
                 LEFT JOIN gdb_files gf ON sr.gdb_id = gf.id
                 WHERE (sb.project_id = ? OR gf.project_id = ?)"""
        params = [pid, pid]
        if project_name:
            sql += " AND sr.project_name = ?"
            params.append(project_name)
        if category:
            sql += " AND sr.category = ?"
            params.append(category)
        sql += " ORDER BY sr.township, sr.forest_compartment, sr.subcompartment"
        rows = conn.execute(sql, params).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def project_name_exists(project_id, project_name):
    """判断某项目下是否已存在该项目名称（GDB 导入产生，project_name 非空）。

    用于上传时「项目名称重复报错」校验。
    """
    if not project_name:
        return False
    conn = _connect()
    try:
        r = conn.execute(
            """SELECT 1 FROM subcompartment_rows sr
               LEFT JOIN gdb_files gf ON sr.gdb_id = gf.id
               LEFT JOIN subcompartment_batches sb ON sr.batch_id = sb.id
               WHERE (gf.project_id = ? OR sb.project_id = ?) AND sr.project_name = ?
               LIMIT 1""",
            (project_id, project_id, project_name),
        ).fetchone()
        return r is not None
    finally:
        conn.close()


def project_name_exists_global(project_name):
    """判断项目名是否全局已存在。

    新上传流程：项目名由 GDB 自动读取并自动建项目，需全局唯一。
    以 projects 表为真相源；孤儿 subcompartment_rows（gdb_files 已删）
    不再阻塞上传。
    """
    if not project_name:
        return False
    conn = _connect()
    try:
        r = conn.execute(
            "SELECT 1 FROM projects WHERE name=? LIMIT 1",
            (project_name,)
        ).fetchone()
        return r is not None
    finally:
        conn.close()


def get_project_names(pid):
    """返回项目下所有项目名称及其分类清单（供前端两级选择 / 导出用）。

    Returns:
        [{"project_name", "categories": [分类...], "count"}, ...] 按名称+分类排序
    """
    conn = _connect()
    try:
        rows = conn.execute(
            """SELECT sr.project_name, sr.category, COUNT(*) c
               FROM subcompartment_rows sr
               LEFT JOIN gdb_files gf ON sr.gdb_id = gf.id
               LEFT JOIN subcompartment_batches sb ON sr.batch_id = sb.id
               WHERE (gf.project_id = ? OR sb.project_id = ?) AND sr.project_name != ''
               GROUP BY sr.project_name, sr.category
               ORDER BY sr.project_name, sr.category""",
            (pid, pid),
        ).fetchall()
        agg = {}
        for r in rows:
            pn = r["project_name"]
            cat = r["category"] or "未分类"
            if pn not in agg:
                agg[pn] = {"project_name": pn, "categories": [], "count": 0}
            if cat not in agg[pn]["categories"]:
                agg[pn]["categories"].append(cat)
            agg[pn]["count"] += r["c"]
        return list(agg.values())
    finally:
        conn.close()


# ════════════════════════════════════════════
# GDB → 小班行 物化导入
# ════════════════════════════════════════════

def import_gdb_subcompartments(gid, project_id, gdb_path, layer=None, layer_meta=None, file_name=None):
    """将 GDB 图层解析后物化写入 subcompartment_rows。

    每个图层行 → 一条 subcompartment_rows 记录：
      - data_json: GDB 该行全部属性（原样保留，并附 ``_layer``/``_category``/``_project_name``）
      - township/village/forest_compartment/subcompartment: 按字段别名解析提取
      - gdb_id: 关联 gdb_files.id
      - gdb_layer: 来源图层名（不同图层=不同调查类型）
      - gdb_feature_id: 图层内的小班标识字段
      - geom_geojson: 该小班面 GeoJSON（WGS84，单 Feature）
      - project_name: 项目名称（图层字段别名读取，回退文件名；见 layer_meta）
      - category: 分类（图层名前缀关键词：人工造林/封山育林/退化林修复/水利水保/草原）

    导入数据按 项目名称 + 分类 + 图层 三维切分：不同图层的 project_name/category
    可不同（同一 GDB 内多个项目/分类并存）。

    Args:
        gid: gdb_files.id
        project_id: 项目 ID
        gdb_path: GDB 文件路径
        layer: 指定图层名；None 表示导入全部图层
        layer_meta: {layer_name: {"category": str, "project_name": str}} 预分类结果；
                    为空时按图层名前缀 + 图层字段实时推导。
        file_name: 上传文件名（project_name 回退用）

    Returns:
        {"layers": {layer: {"imported": n, "skipped": m, "category": c, "project_name": p}},
         "imported": N, "skipped": M, "project_names": [..]}
    """
    import json as _json
    from survey.core import gdb as G

    if layer is None:
        layer_names = [l["name"] for l in G.list_layers(gdb_path)]
    else:
        layer_names = [layer]

    per_layer = {}
    total_imported = 0
    total_skipped = 0
    project_names_seen = []

    with _lock:
        conn = _connect()
        try:
            for lname in layer_names:
                # 分类 + 项目名称：优先用预分类结果，否则实时推导
                meta = (layer_meta or {}).get(lname, {})
                category = meta.get("category") or G.classify_layer(lname)
                layer_pn = meta.get("project_name")
                if not layer_pn:
                    layer_pn = G.read_project_name(gdb_path, lname)
                if not layer_pn:
                    layer_pn = G.derive_project_name(file_name) if file_name else ""
                if layer_pn and layer_pn not in project_names_seen:
                    project_names_seen.append(layer_pn)

                gdf = G.read_layer(gdb_path, lname)
                gdf84 = G.to_wgs84(gdf)
                cols = list(gdf84.columns)

                # 预解析本图层候选别名列（逐行再按实际值挑选非空列）
                f_sub_cands = [a for a in G.GDB_FIELD_ALIASES["subcompartment"] if a in cols] or \
                              [c for c in cols for a in G.GDB_FIELD_ALIASES["subcompartment"] if a and (a in c or c in a)]
                f_town_cands = [a for a in G.GDB_FIELD_ALIASES["township"] if a in cols] or \
                               [c for c in cols for a in G.GDB_FIELD_ALIASES["township"] if a and (a in c or c in a)]
                f_vill_cands = [a for a in G.GDB_FIELD_ALIASES["village"] if a in cols] or \
                               [c for c in cols for a in G.GDB_FIELD_ALIASES["village"] if a and (a in c or c in a)]
                f_for_cands = [a for a in G.GDB_FIELD_ALIASES["forest_compartment"] if a in cols] or \
                              [c for c in cols for a in G.GDB_FIELD_ALIASES["forest_compartment"] if a and (a in c or c in a)]
                f_area_cands = [a for a in G.GDB_FIELD_ALIASES["area"] if a in cols] or \
                               [c for c in cols for a in G.GDB_FIELD_ALIASES["area"] if a and (a in c or c in a)]
                f_fid_cands = [a for a in G.GDB_FIELD_ALIASES["feature_id"] if a in cols] or \
                              [c for c in cols for a in G.GDB_FIELD_ALIASES["feature_id"] if a and (a in c or c in a)]

                imported = 0
                skipped = 0
                base_idx = total_imported + total_skipped
                for idx, row in gdf84.iterrows():
                    # 提取属性（排除 geometry）
                    props = {}
                    for col in cols:
                        if col == "geometry":
                            continue
                        v = row[col]
                        if v is None or (hasattr(v, "__float__") and v != v):
                            props[col] = ""
                        else:
                            props[col] = str(v)
                    # 记录来源图层 / 分类 / 项目名称（不同图层=不同调查信息）
                    props["_layer"] = lname
                    props["_category"] = category
                    props["_project_name"] = layer_pn

                    # 逐行按实际值挑选非空别名列
                    f_sub = G._pick_candidate(props, f_sub_cands)
                    f_town = G._pick_candidate(props, f_town_cands)
                    f_vill = G._pick_candidate(props, f_vill_cands)
                    f_for = G._pick_candidate(props, f_for_cands)
                    f_area = G._pick_candidate(props, f_area_cands)
                    f_fid = G._pick_candidate(props, f_fid_cands)

                    # 林班/小班规范化为 unsigned int（根治 "1.0" 问题）
                    forest_compartment = _to_uint(props.get(f_for, "")) if f_for else 0
                    subcompartment = _to_uint(props.get(f_sub, "")) if f_sub else 0
                    if f_for:
                        props["林班"] = forest_compartment
                    if f_sub:
                        props["小班"] = subcompartment

                    # 提取定位字段
                    township = props.get(f_town, "") if f_town else ""
                    village = props.get(f_vill, "") if f_vill else ""
                    gdb_feature_id = props.get(f_fid, "") if f_fid else ""

                    if not subcompartment:
                        skipped += 1
                        continue

                    # 标签：有林班时「林班-小班」，无林班仅显示小班号（不出现 0- 前缀）
                    subcompartment_label = (
                        f"{forest_compartment}-{subcompartment}" if forest_compartment
                        else f"{subcompartment}"
                    )

                    # 提取面积（别名解析：小班面/小班面积/经营面/经营面积/上报面积/抚育面积）
                    tending_area = 0.0
                    if f_area:
                        raw = props.get(f_area, "")
                        if raw:
                            try:
                                tending_area = float(raw)
                            except (ValueError, TypeError):
                                pass

                    # 生成单小班面 GeoJSON
                    geom = row.geometry
                    geom_geojson = ""
                    if geom is not None:
                        try:
                            geom_geojson = _json.dumps({
                                "type": "Feature",
                                "geometry": _json.loads(_json.dumps(geom.__geo_interface__)),
                                "properties": {
                                    "New_ID": gdb_feature_id,
                                    "小班": subcompartment,
                                    "layer": lname,
                                },
                            }, ensure_ascii=False)
                        except Exception:
                            geom_geojson = ""

                    row_id = uuid.uuid4().hex[:12]
                    conn.execute(
                        """INSERT INTO subcompartment_rows
                           (id, batch_id, row_index, data_json, township, village,
                            forest_compartment, subcompartment, subcompartment_label,
                            tending_area, gdb_id, gdb_feature_id, geom_geojson, gdb_layer,
                            project_name, category)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (row_id, gid, base_idx + idx,
                         _json.dumps(props, ensure_ascii=False),
                         township, village, forest_compartment, subcompartment,
                         subcompartment_label, tending_area, gid, gdb_feature_id,
                         geom_geojson, lname, layer_pn, category),
                    )
                    imported += 1
                total_imported += imported
                total_skipped += skipped
                per_layer[lname] = {
                    "imported": imported, "skipped": skipped,
                    "category": category, "project_name": layer_pn,
                }
            conn.commit()
        finally:
            conn.close()

    return {
        "layers": per_layer,
        "imported": total_imported,
        "skipped": total_skipped,
        "project_names": project_names_seen,
    }
