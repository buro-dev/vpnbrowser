# 🌐 VPN Browser (demo)

Farklı proxy'ler üzerinden gezinebileceğiniz bir **web proxy tarayıcı** örneği.
Her sayfa; seçtiğiniz proxy (HTTP/HTTPS/SOCKS5) üzerinden sunucu tarafında
indirilir, içindeki bağlantılar/kaynaklar yeniden yazılır ve tarayıcınızda
gösterilir. Aynı sayfayı **farklı proxy'lerden** açıp karşılaştırabilirsiniz.

![akış](https://img.shields.io/badge/python-3.10%2B-blue) ![web](https://img.shields.io/badge/fastapi-%20-009688)

## Özellikler

- **Proxy yönetimi:** `http://`, `https://`, `socks5://` (user:pass destekli) proxy ekleme / silme / test etme
  - Her proxy için **çıkış IP'si**, şehir/ülke ve gecikme ölçümü
- **Gezinme:** adres çubuğu, geri/ileri/yenile, yeni sekme açma, GET formlar, PDF gibi dosyalar
- **Proxy geçişi:** listeden başka bir proxy seçtiğinizde sayfa o proxy üzerinden yeniden yüklenir
- **Sayfa içi JS istekleri** (fetch/XHR) de seçili proxy üzerinden yönlendirilir
- Örnek **SOCKS5 proxy sunucusu** (`demo_proxy.py`) — kendi proxy'niz yoksa test için

## Kurulum

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Çalıştırma

```bash
# 1) (opsiyonel) yerel test proxy'sini başlat
.venv/bin/python demo_proxy.py            # 127.0.0.1:1080 dinler

# 2) uygulamayı başlat
.venv/bin/uvicorn app:app --host 0.0.0.0 --port 8000
```

Tarayıcıda `http://localhost:8000` adresini açın.
Örneğin `socks5://127.0.0.1:1080` proxy'sini ekleyip test edin, ardından
bir sayfayı farklı proxy'lerle gezin (alt çubuktaki *Çıkış IP* değeri değişecektir).

## Nasıl çalışır?

```
Tarayıcınız ──▶ Uygulama sunucusu ──(seçili proxy)──▶ Hedef site
      ▲                │
      └── yeniden yazılmış HTML / kaynaklar
```

1. `GET /api/render?url=...&proxy_id=...` sayfası seçili proxy ile indirir.
2. HTML'deki `href/src/action/srcset/css-url()` değerleri `/api/fetch?url=...`
   (proxy üzerinden kaynak indirme) adreslerine yeniden yazılır.
3. Sayfaya eklenen küçük bir köprü scripti; link tıklamalarını, GET formlarını
   ve sayfanın kendi `fetch/XHR` isteklerini proxy hattına taşır.

## API

| Uç | Açıklama |
|---|---|
| `GET /api/proxies` | Proxy listesi |
| `POST /api/proxies` `{"name":"?","url":"socks5://user:pass@host:1080"}` | Proxy ekle |
| `DELETE /api/proxies/{id}` | Proxy sil |
| `POST /api/proxies/{id}/test` | Çıkış IP / coğrafya / gecikme testi |
| `GET /api/whoami` | Sunucunun kendi (doğrudan) IP'si |
| `GET /api/render?url=...&proxy_id=...` | Sayfayı proxy ile indir + yeniden yaz |
| `GET /api/fetch?url=...&proxy_id=...` | Tekil kaynağı proxy ile indir |
| `GET /view?url=...&proxy_id=...` | Yeni sekmede bağımsız görünüm |

## demo_proxy.py — örnek SOCKS5 proxy

Bağımlılıksız, asyncio tabanlı minimal SOCKS5 sunucusu (yalnızca `CONNECT`):

```bash
.venv/bin/python demo_proxy.py 1080 0.0.0.0
```

Herhangi bir SOCKS5 istemcisiyle deneyebilirsiniz:

```bash
curl -x socks5://127.0.0.1:1080 https://pypi.org -o /dev/null -w '%{http_code}\n'
```

> ⚠️ Bu proxy'de kimlik doğrulama **yoktur**; sadece yerel demo/test içindir.

## Sınırlamalar & güvenlik notları

- **Demo amaçlıdır.** Proxy kimlik bilgileri sunucuda düz metin (`data/proxies.json`) saklanır.
- Sunucu, verdiğiniz adreslere istek gönderir (sunucu taraflı istek). Bu yüzden
  uygulamayı yalnızca **kendi kontrolünüzdeki** makinede çalıştırın; herkese açık
  internete açmayın (yoksa bir açık proxy/SSRF aracı olur).
- POST formlar, iframe'ler ve bazı JS navigasyonları demo kapsamı dışındadır.
- `direct` seçeneği = proxy olmadan sunucunun kendi IP'si ile erişim.
