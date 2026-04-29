#!/bin/bash
# run_rebuild_v2.sh — 每周重建 v2 数据的 cron wrapper
# 调用方式: /Users/eason/.hermes/trading_study/scripts/run_rebuild_v2.sh
# cron 示例: 0 2 * * 1 /Users/eason/.hermes/trading_study/scripts/run_rebuild_v2.sh

LOGFILE="/tmp/rebuild_t1_v2_$(date +%Y%m%d_%H%M%S).log"
PROJECT_DIR="/Users/eason/.hermes/trading_study"
VENV_PYTHON="${PROJECT_DIR}/.venv/bin/python3"

if [ ! -f "$VENV_PYTHON" ]; then
    VENV_PYTHON="/Users/eason/.hermes/venv/bin/python3"
fi

cd "$PROJECT_DIR" || exit 1

echo "[$(date)] 开始重建 v2 数据..." >> "$LOGFILE"
$VENV_PYTHON "$PROJECT_DIR/scripts/rebuild_t1_v2.py" --days 90 >> "$LOGFILE" 2>&1

echo "[$(date)] 完成" >> "$LOGFILE"
