#!/usr/bin/env bash
# survey 部署同步（gateway 模型）
set -euo pipefail
REMOTE=${REMOTE:-www@forest.bibook.top}
DEST=${DEST:-/home/www/bibook_deploy/apps/survey}
GATEWAY_APPS_ROOT=${GATEWAY_APPS_ROOT:-/home/www/bibook_deploy/apps/gateway/apps_root}
EXCLUDE="--exclude=__pycache__/ --exclude=*.pyc --exclude=*.db --exclude=*.db-wal --exclude=*.db-shm --exclude=.DS_Store"
# 同步 survey/ 到服务器
rsync -avz $EXCLUDE ./survey/ "$REMOTE:$DEST/survey/"
# 同步 appspec 到 gateway apps_root（实际 gateway 在 apps/gateway/）
scp ./survey/gateway/survey.appspec "$REMOTE:$GATEWAY_APPS_ROOT/survey.appspec"
scp ./survey/gateway/admin.appspec "$REMOTE:$GATEWAY_APPS_ROOT/admin.appspec"
# 重启 gateway
ssh "$REMOTE" "systemctl --user restart gateway.service"
echo "[deploy] survey synced to $DEST, gateway restarted"
