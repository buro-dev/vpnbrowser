#!/usr/bin/env bash
# VPN Browser — ortamı sıfırdan kur + başlat (workspace reset sonrası için)
set -e
cd "$(dirname "$0")"

# 1) bağımlılıklar (venv yoksa kur)
if [ ! -x .venv/bin/uvicorn ]; then
  echo "== venv kuruluyor =="
  python3 -m venv .venv
  .venv/bin/pip install --quiet -r requirements.txt
fi

# 2) eski süreçleri temizle
pkill -f "uvicorn app:app" 2>/dev/null || true
pkill -f "demo_proxy.py 1080" 2>/dev/null || true
sleep 1

# 3) başlat
mkdir -p data
nohup .venv/bin/python demo_proxy.py 1080 > data/demo_proxy.log 2>&1 &
nohup .venv/bin/uvicorn app:app --host 0.0.0.0 --port 8000 > data/uvicorn.log 2>&1 &

# 4) demo proxy'si kayıtlı değilse ekle
sleep 3
if ! grep -q "socks5://127.0.0.1:1080" data/proxies.json 2>/dev/null; then
  curl -s -X POST localhost:8000/api/proxies -H 'Content-Type: application/json' \
    -d '{"url":"socks5://127.0.0.1:1080","name":"Demo SOCKS5 (yerel)"}' > /dev/null
fi

# 5) doğrula
curl -s localhost:8000/healthz && echo
echo "VPN Browser hazır: http://localhost:8000"
