#!/usr/bin/env bash
# 备份 MySQL 到 backups/，默认保留 7 天。
# 用法：bash tools/backup_mysql.sh
#       KEEP_DAYS=14 bash tools/backup_mysql.sh
set -euo pipefail
cd "$(dirname "$0")/.."

KEEP_DAYS="${KEEP_DAYS:-7}"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="backups/daily_quiz-$STAMP.sql.gz"
mkdir -p backups

echo "[backup] 导出 daily_quiz -> $OUT"
# 密码从容器环境变量读取，不出现在宿主机命令行与历史记录里
docker compose exec -T db sh -c 'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" \
  --single-transaction --quick --skip-lock-tables \
  --default-character-set=utf8mb4 "$MYSQL_DATABASE"' | gzip -9 > "$OUT"

gzip -t "$OUT"
SIZE="$(wc -c < "$OUT" | tr -d ' ')"
if [ "$SIZE" -lt 1024 ]; then
  echo "[backup] 备份文件异常（${SIZE}B），请检查数据库状态" >&2
  exit 1
fi
echo "[backup] 完成：$OUT（${SIZE} 字节）"

# 只清理本脚本生成的旧备份，不碰目录里的其他文件
find backups -maxdepth 1 -name 'daily_quiz-*.sql.gz' -type f -mtime "+${KEEP_DAYS}" -print -delete
echo "[backup] 已清理 ${KEEP_DAYS} 天前的旧备份"
