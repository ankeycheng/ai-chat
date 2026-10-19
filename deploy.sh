#!/usr/bin/env bash
# One-command deployment: ./deploy.sh   (re-run to update; ./deploy.sh down to stop)
set -euo pipefail
cd "$(dirname "$0")"

ENV_FILE="${ENV_FILE:-.env}"   # may point outside the repo, e.g. ENV_FILE=/etc/ai-chat/prod.env
if docker info >/dev/null 2>&1; then
  DOCKER=(docker)
elif sudo -n docker info >/dev/null 2>&1; then
  DOCKER=(sudo docker)
else
  echo "无法访问 Docker（需要 docker 组或免密 sudo）"; exit 1
fi
dc() { "${DOCKER[@]}" compose --env-file "$ENV_FILE" "$@"; }
if [[ "${1:-}" == "down" ]]; then dc down; exit 0; fi
command -v docker >/dev/null || { echo "docker 未安装"; exit 1; }
docker compose version >/dev/null || { echo "需要 docker compose v2"; exit 1; }

if [[ ! -f "$ENV_FILE" ]]; then
  cp .env.example "$ENV_FILE"
  echo "已生成 $ENV_FILE，请填写 DEEPSEEK_API_KEY 与 POSTGRES_PASSWORD 后重新运行 ./deploy.sh"; exit 1
fi
set -a; source "$ENV_FILE"; set +a
[[ -n "${DEEPSEEK_API_KEY:-}" ]] || { echo ".env 中 DEEPSEEK_API_KEY 为空"; exit 1; }

dc up -d --build --remove-orphans
PORT="${HTTP_PORT:-9123}"
echo "等待服务就绪 http://127.0.0.1:${PORT}/api/ready ..."
for i in $(seq 1 90); do
  if curl -fsS "http://127.0.0.1:${PORT}/api/ready" >/dev/null 2>&1; then
    dc ps
    echo "✅ 部署完成：http://<服务器IP>:${PORT}"
    exit 0
  fi
  sleep 2
done
echo "❌ 服务未在 180 秒内就绪"; dc ps; dc logs --tail=50 backend; exit 1
