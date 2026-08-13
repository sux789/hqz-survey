#!/usr/bin/env bash
# survey 部署同步（gateway 模型）
set -euo pipefail
REMOTE=${REMOTE:-www@forest.bibook.top}
DEST=${DEST:-/home/www/bibook_deploy/apps/survey}
EXCLUDE="--exclude=__pycache__/ --exclude=*.pyc --exclude=*.db --exclude=.DS_Store"
# 同步 survey/ 到服务器
rsync -avz $EXCLUDE ./survey/ "$REMOTE:$DEST/survey/"
# 同步 appspec 到 gateway apps_root
scp ./survey/gateway/survey.appspec "$REMOTE:/home/www/bibook_deploy/apps/hqz-p2/gateway/apps_root/survey.appspec"
# 重启 gateway
ssh "$REMOTE" "systemctl --user restart gateway.service"
echo "[deploy] survey synced to $DEST, gateway restarted"
