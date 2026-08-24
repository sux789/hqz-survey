# -*- coding: utf-8 -*-
"""一次性数据迁移：records.data_json 率类字段 ×100 → 比率（0-1）。

背景（2026-08-21，CONSTRAINTS C8/C9/D3 更新）：
  率类字段（合格率 computed + 成活率等级/施工率/建档率/管护率/抚育率
  percent 输入）旧口径存 ×100 值（如 "98.44"/95），与 Excel 模板
  百分比格式（0.00%）、分派公式阈值（0.9/0.4 按比率比较）语义相反，
  导出显示为 9844% 且分派错列。新口径统一存比率（0.9844）。

转换规则（>1 判定法）：
  数值 > 1 → 视为旧 ×100 值，÷100（"98.44"→0.9844，"100.00"→1）；
  0 ≤ 数值 ≤ 1 → 已是比率（或 0/1 边界），保持不变；
  非数值 → 原样保留（打印告警人工核对）。
  合格株树（qualified_count）数值口径不变（round(查数×率) 结果相同），
  不迁移。

用法:
    python tools/fix_rate_data.py [db路径]        # 缺省 survey/survey.db
    python tools/fix_rate_data.py --dry-run [db] # 只看不改

幂等：已是比率的值不会被二次缩放，可重复执行。
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from survey.core import schema as S  # noqa: E402

# 迁移键集：全部 percent 输入字段 + computed 合格率
RATE_KEYS = set()
for t in S.TABLES:
    for f in t.get("input_columns", []):
        if f.get("type") == "percent":
            RATE_KEYS.add(f["key"])
RATE_KEYS.add("qualified_rate")


def convert(v):
    """返回 (新值, 是否修改, 说明)。>1 视为旧 ×100 值。"""
    if v in ("", None):
        return v, False, ""
    if isinstance(v, (int, float)):
        if v > 1:
            return round(v / 100, 4), True, f"{v} → {round(v / 100, 4)}"
        return v, False, ""
    s = str(v).strip()
    try:
        n = float(s)
    except ValueError:
        return v, False, f"非数值原样保留: {v!r}"
    if n > 1:
        new = round(n / 100, 4)
        return new, True, f"{v!r} → {new}"
    return v, False, ""


def main():
    ap = argparse.ArgumentParser(description="率类字段 ×100 → 比率迁移")
    ap.add_argument("db", nargs="?", default="survey/survey.db", help="SQLite 库路径")
    ap.add_argument("--dry-run", action="store_true", help="只打印不修改")
    args = ap.parse_args()

    if not Path(args.db).exists():
        print(f"数据库不存在: {args.db}")
        sys.exit(1)
    db = sqlite3.connect(args.db)
    db.row_factory = sqlite3.Row

    rows = db.execute("SELECT id, table_id, subcompartment_id, data_json FROM records").fetchall()
    changed = 0
    for r in rows:
        try:
            d = json.loads(r["data_json"] or "{}")
        except json.JSONDecodeError:
            print(f"[WARN] 记录 {r['id'][:8]} data_json 解析失败，跳过")
            continue
        diffs = []
        for k in RATE_KEYS:
            if k in d:
                new, mod, note = convert(d[k])
                if mod:
                    diffs.append(f"{k}: {note}")
                    d[k] = new
                elif note:
                    diffs.append(f"{k}: {note}")
        if not diffs:
            continue
        print(f"[{r['table_id']}] {str(r['subcompartment_id'])[:8]}: " + "; ".join(diffs))
        if not args.dry_run:
            db.execute("UPDATE records SET data_json = ? WHERE id = ?",
                       (json.dumps(d, ensure_ascii=False), r["id"]))
        changed += 1

    if args.dry_run:
        print(f"dry-run：{changed} 条记录将被修改（未写入）")
    else:
        db.commit()
        print(f"完成：{changed} 条记录已迁移")
    db.close()


if __name__ == "__main__":
    main()
