#!/bin/bash
# 导出口径基线快照 — 改动导出代码前先跑本脚本留底，改后重跑并用
# tools/compare_export.py 双跑对比（AGENTS.md「改动后全绿」第 2 条）。
#
# 用法: tools/make_baseline.sh [项目id]      # 缺省取数据库第一个项目
set -euo pipefail
cd "$(dirname "$0")/.."

PID="${1:-}"
TS=$(date +%Y%m%d-%H%M%S)
OUT="baseline/$TS"
mkdir -p "$OUT"

python3 - "$OUT" "$PID" <<'PY'
import sys
from survey.core import storage, exporter

out, pid = sys.argv[1], sys.argv[2]
if not pid:
    pid = storage.list_projects()[0]["id"]
name = storage.get_project(pid)["name"]
base, _ = exporter.export_base(pid, f"{out}/{name}_基本信息.xlsx")
samples, _ = exporter.export_samples(pid, f"{out}/{name}_样地.xlsx")
print(f"项目: {name} ({pid})")
print(f"  {base}")
print(f"  {samples}")
PY

ln -sfn "$TS" baseline/latest
echo "基线已生成: $OUT （baseline/latest 已指向）"
