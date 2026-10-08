#!/usr/bin/env bash
set -euo pipefail

web_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd "$web_dir/.." && pwd)"
python_bin="$project_dir/.venv/bin/python"
boss_credentials="$project_dir/../bossHunter/.config.credentials.yaml"

node_compatible() {
  command -v node >/dev/null 2>&1 && node -e '
    const [major, minor] = process.versions.node.split(".").map(Number);
    process.exit((major === 20 && minor >= 19) || (major === 22 && minor >= 12) || major >= 23 ? 0 : 1);
  ' >/dev/null 2>&1
}

if ! node_compatible; then
  for candidate in "$HOME"/.nvm/versions/node/v*/bin; do
    if [[ -x "$candidate/node" ]]; then
      PATH="$candidate:$PATH"
    fi
  done
  export PATH
fi

if ! node_compatible || ! command -v npm >/dev/null 2>&1; then
  echo "热更新需要 Node.js 20.19+ 或 22.12+，请先安装新版 Node.js。" >&2
  exit 1
fi

web_port=8000
api_port=""
state_db=""
while (($#)); do
  case "$1" in
    --port) web_port="${2:?--port 需要端口号}"; shift 2 ;;
    --api-port) api_port="${2:?--api-port 需要端口号}"; shift 2 ;;
    --state-db) state_db="${2:?--state-db 需要路径}"; shift 2 ;;
    -h|--help)
      echo "用法：./web/start.sh [--port 前端端口] [--api-port API端口] [--state-db 数据库路径]"
      exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 1 ;;
  esac
done

if [[ ! "$web_port" =~ ^[0-9]+$ ]] || ((web_port < 1 || web_port > 65534)); then
  echo "--port 须在 1～65534。" >&2
  exit 1
fi
api_port="${api_port:-$((web_port + 1))}"
if [[ ! "$api_port" =~ ^[0-9]+$ ]] || ((api_port < 1 || api_port > 65535 || api_port == web_port)); then
  echo "--api-port 须在 1～65535，且不能与前端端口相同。" >&2
  exit 1
fi

if [[ ! -x "$python_bin" ]]; then
  if ! command -v python3 >/dev/null 2>&1; then
    echo "未找到 python3。" >&2
    exit 1
  fi
  echo "创建 Python 虚拟环境…"
  python3 -m venv "$project_dir/.venv"
fi

if ! "$python_bin" -c 'import patchright, httpx, fontTools' >/dev/null 2>&1; then
  echo "安装 Python 依赖…"
  "$python_bin" -m pip install -r "$project_dir/requirements.txt"
fi

if [[ -z "${DEEPSEEK_API_KEY:-}" && -f "$boss_credentials" ]]; then
  deepseek_key="$($python_bin - "$boss_credentials" <<'PY'
from pathlib import Path
import re
import sys

for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    match = re.fullmatch(r"\s+api_key:\s*(\S+)\s*", line)
    if match:
        print(match.group(1).strip("\"'"))
        break
PY
)"
  if [[ -n "$deepseek_key" ]]; then
    export DEEPSEEK_API_KEY="$deepseek_key"
    unset deepseek_key
    echo "已从 BossHunter 本地凭据加载 DeepSeek Key。"
  fi
fi

echo "安装前端依赖…"
cd "$web_dir"
npm install --no-audit --no-fund --loglevel=error

api_args=(--port "$api_port")
if [[ -n "$state_db" ]]; then
  api_args+=(--state-db "$state_db")
fi

if ! "$python_bin" - "$api_port" <<'PY'
import socket
import sys

with socket.socket() as probe:
    try:
        probe.bind(("127.0.0.1", int(sys.argv[1])))
    except OSError:
        sys.exit(1)
PY
then
  echo "API 端口 $api_port 已被占用。请先停止旧服务，或用 --api-port 指定其他端口。" >&2
  exit 1
fi

cleanup() {
  if [[ -n "${api_pid:-}" ]]; then
    kill "$api_pid" 2>/dev/null || true
    wait "$api_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

cd "$project_dir"
"$python_bin" api/server.py "${api_args[@]}" &
api_pid=$!
api_ready=0
for ((attempt=0; attempt<100; attempt++)); do
  if ! kill -0 "$api_pid" 2>/dev/null; then
    wait "$api_pid" || true
    echo "API 服务启动失败。" >&2
    exit 1
  fi
  if "$python_bin" - "$api_port" <<'PY'
import http.client
import json
import sys

try:
    connection = http.client.HTTPConnection("127.0.0.1", int(sys.argv[1]), timeout=0.3)
    connection.request("GET", "/api/monitoring/current")
    response = connection.getresponse()
    value = json.load(response)
    valid = response.status == 200 and "message_limit" in value
    connection.close()
    sys.exit(0 if valid else 1)
except (OSError, ValueError):
    sys.exit(1)
PY
  then
    api_ready=1
    break
  fi
  sleep 0.1
done
if ((api_ready == 0)); then
  echo "API 服务启动后仍未就绪。" >&2
  exit 1
fi

echo "前端热更新地址：http://127.0.0.1:$web_port"
cd "$web_dir"
JHH_API_PORT="$api_port" ./node_modules/.bin/vite --host 127.0.0.1 --port "$web_port" --strictPort
