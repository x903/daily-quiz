#!/usr/bin/env bash
# 从 backups/*.sql.gz 恢复 MySQL（会覆盖当前库，务必先确认）。
# 用法：bash tools/restore_mysql.sh backups/daily_quiz-20260101-030000.sql.gz --yes
set -euo pipefail
cd "$(dirname "$0")/.."

FILE="${1:-}"
if [ -z "$FILE" ] || [ ! -f "$FILE" ]; then
  echo "用法：bash tools/restore_mysql.sh backups/xxx.sql.gz --yes" >&2
  exit 1
fi
if [ "${2:-}" != "--yes" ]; then
  echo "该操作会覆盖 ${FILE} 中的表数据，确认后追加 --yes 再执行。" >&2
  exit 1
fi

gzip -t "$FILE"
echo "[restore] 恢复 $FILE -> daily_quiz"
gunzip -c "$FILE" | docker compose exec -T db sh -c \
  'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" --default-character-set=utf8mb4 "$MYSQL_DATABASE"'
echo "[restore] 完成，建议执行：docker compose restart web"
