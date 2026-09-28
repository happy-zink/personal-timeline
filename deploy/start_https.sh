#!/usr/bin/env bash
# 一键 HTTPS 启动（macOS / Linux）
# 优先用 openssl 生成自签证书；若已安装 Caddy 可用反代模式。
#
# 用法:
#   ./deploy/start_https.sh
#   ./deploy/start_https.sh --caddy --domain timeline.example.com
#   PORT=8765 ./deploy/start_https.sh

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PORT="${PORT:-8764}"
HOSTS="${HOSTS:-localhost,127.0.0.1}"
USE_CADDY=0
DOMAIN=""
CADDY_HTTPS_PORT="${CADDY_HTTPS_PORT:-8443}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --caddy) USE_CADDY=1; shift ;;
    --domain) DOMAIN="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --hosts) HOSTS="$2"; shift 2 ;;
    *) echo "未知参数: $1"; exit 1 ;;
  esac
done

find_python() {
  if [[ -n "${MIMO_PYTHON:-}" ]]; then echo "$MIMO_PYTHON"; return; fi
  if command -v python3 >/dev/null; then echo python3; return; fi
  if command -v python >/dev/null; then echo python; return; fi
  echo "未找到 Python" >&2; exit 1
}

if [[ "$USE_CADDY" == "1" ]]; then
  CADDY="$(command -v caddy || true)"
  if [[ -z "$CADDY" && -x "$ROOT/deploy/caddy" ]]; then CADDY="$ROOT/deploy/caddy"; fi
  if [[ -z "$CADDY" ]]; then
    echo "未找到 Caddy。可下载到 deploy/caddy" >&2
    exit 1
  fi
  CFG="$ROOT/deploy/Caddyfile.local"
  if [[ -n "$DOMAIN" ]]; then
    cat >"$CFG" <<EOF
$DOMAIN {
    encode gzip
    reverse_proxy 127.0.0.1:$PORT
}
EOF
    echo "访问: https://$DOMAIN"
  else
    cat >"$CFG" <<EOF
:$CADDY_HTTPS_PORT {
    tls internal
    reverse_proxy 127.0.0.1:$PORT
}
EOF
    echo "访问: https://localhost:$CADDY_HTTPS_PORT"
  fi
  PY="$(find_python)"
  "$PY" server.py --host 127.0.0.1 --port "$PORT" &
  sleep 1
  exec "$CADDY" run --config "$CFG"
fi

CERT_DIR="$ROOT/deploy/certs"
mkdir -p "$CERT_DIR"
CERT="$CERT_DIR/tls.crt"
KEY="$CERT_DIR/tls.key"

if [[ ! -f "$CERT" || ! -f "$KEY" ]]; then
  if command -v openssl >/dev/null; then
    echo "生成自签证书..."
    # build SAN
    IFS=',' read -ra HLIST <<<"$HOSTS"
    SAN=""
    i=1
    for h in "${HLIST[@]}"; do
      h="$(echo "$h" | xargs)"
      [[ -z "$h" ]] && continue
      if [[ "$h" =~ ^[0-9.]+$ ]]; then
        SAN+="IP.${i} = ${h}"$'\n'
      else
        SAN+="DNS.${i} = ${h}"$'\n'
      fi
      i=$((i+1))
    done
    cat >"$CERT_DIR/openssl.cnf" <<EOF
[req]
distinguished_name = dn
x509_extensions = v3_req
prompt = no
[dn]
CN = personal-timeline
[v3_req]
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names
[alt_names]
$SAN
EOF
    openssl req -x509 -nodes -newkey rsa:2048 \
      -keyout "$KEY" -out "$CERT" -days 730 \
      -config "$CERT_DIR/openssl.cnf"
  else
    echo "未找到 openssl，请安装后重试，或使用 --caddy" >&2
    exit 1
  fi
fi

PY="$(find_python)"
echo "访问: https://localhost:$PORT （自签证书）"
exec "$PY" server.py --host 0.0.0.0 --port "$PORT" --https --cert "$CERT" --key "$KEY"
