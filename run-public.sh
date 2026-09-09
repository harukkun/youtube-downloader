#!/bin/zsh
set -eu
cd "$(dirname "$0")"
if [[ -z "${PUBLIC_IP:-}" ]]; then
  read 'PUBLIC_IP?공유기 WAN 공인 IPv4 주소: '
fi
export PUBLIC_IP
.venv/bin/python -c 'import ipaddress, os; ip=ipaddress.IPv4Address(os.environ["PUBLIC_IP"]); assert ip.is_global, "공인 IPv4 주소가 필요합니다."'
command -v caddy >/dev/null || { print '먼저 brew install caddy 를 실행해 주세요.'; exit 1; }
.venv/bin/python -c 'import waitress' || { print '먼저 .venv/bin/pip install -r requirements.txt 를 실행해 주세요.'; exit 1; }
if [[ -z "${APP_PASSWORD:-}" ]]; then
  read -s 'APP_PASSWORD?접속 비밀번호 (12자 이상): '
  print
fi
[[ ${#APP_PASSWORD} -ge 12 ]] || { print '비밀번호는 12자 이상이어야 합니다.'; exit 1; }
export APP_PASSWORD
export APP_PUBLIC_ORIGIN="https://${PUBLIC_IP}"
caddy adapt --config deploy/Caddyfile --adapter caddyfile >/dev/null
.venv/bin/python serve.py &
app_pid=$!
proxy_pid=''
awake_pid=''
cleanup() {
  [[ -z "$proxy_pid" ]] || kill "$proxy_pid" 2>/dev/null || true
  [[ -z "$awake_pid" ]] || kill "$awake_pid" 2>/dev/null || true
  kill "$app_pid" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT TERM
sleep 1
kill -0 "$app_pid" || exit 1
# The TLS process does not need the application password.
env -u APP_PASSWORD caddy run --config deploy/Caddyfile --adapter caddyfile &
proxy_pid=$!
caffeinate -i -w $$ &
awake_pid=$!
print "외부 주소: ${APP_PUBLIC_ORIGIN} · 종료: Ctrl+C"
while kill -0 "$app_pid" 2>/dev/null && kill -0 "$proxy_pid" 2>/dev/null; do
  sleep 2
done
exit 1
