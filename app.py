#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VPN Browser — Farklı proxy'ler üzerinden gezinen web proxy tarayıcı (demo).

Her sayfa istemci tarayıcısında değil, sunucu tarafında SEÇİLEN PROXY üzerinden
indirilir; HTML'deki bağlantı/kaynaklar sunucuya yönlendirilip tarayıcıya
gösterilir. Böylece aynı sayfayı farklı proxy'lerle (farklı kimliklerden)
gezebilirsiniz.

Uçlar:
  GET    /                     -> Tek sayfa uygulaması
  GET    /view                 -> Yeni sekmede bağımsız sayfa görünümü
  GET    /api/proxies          -> Proxy listesi
  POST   /api/proxies          -> Proxy ekle
  DELETE /api/proxies/{id}     -> Proxy sil
  POST   /api/proxies/{id}/test-> Proxy test et (çıkış IP, ülke, gecikme)
  GET    /api/whoami           -> Sunucunun kendi (doğrudan) IP'si
  GET    /api/render           -> Sayfayı proxy ile indir + HTML'i yeniden yaz
  GET    /api/fetch            -> Tekil kaynağı (css/js/görsel) proxy ile indir
"""

import json
import re
import time
import uuid
from html import unescape
from pathlib import Path
from urllib.parse import urlencode, urljoin

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
PROXIES_FILE = DATA_DIR / "proxies.json"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
MAX_HTML_BYTES = 15_000_000
MAX_RESOURCE_BYTES = 25_000_000

app = FastAPI(title="VPN Browser")

# ---------------------------------------------------------------------------
# Proxy deposu (basit JSON kalıcılığı)
# ---------------------------------------------------------------------------


def _load_proxies() -> list:
    if PROXIES_FILE.exists():
        try:
            return json.loads(PROXIES_FILE.read_text())
        except Exception:
            pass
    seed = [
        {
            "id": "direct",
            "name": "Doğrudan Bağlantı (sunucu IP'si)",
            "url": "direct",
            "created": time.time(),
            "last_test": None,
        }
    ]
    PROXIES_FILE.write_text(json.dumps(seed, ensure_ascii=False, indent=2))
    return seed


PROXIES = {p["id"]: p for p in _load_proxies()}


def _save() -> None:
    PROXIES_FILE.write_text(
        json.dumps(list(PROXIES.values()), ensure_ascii=False, indent=2)
    )


def get_proxy(pid: str | None) -> dict:
    pid = pid or "direct"
    p = PROXIES.get(pid)
    if not p:
        raise HTTPException(404, "Proxy bulunamadı")
    return p


def proxy_url(p: dict) -> str | None:
    return None if p.get("url") == "direct" else p.get("url")


def make_client(p: dict, **kw) -> httpx.AsyncClient:
    headers = kw.pop("headers", {})
    headers.setdefault("User-Agent", UA)
    headers.setdefault("Accept-Language", "tr-TR,tr;q=0.9,en;q=0.8")
    headers.setdefault("Accept", "*/*")
    kw.setdefault("follow_redirects", True)
    kw.setdefault("timeout", httpx.Timeout(20, connect=10))
    pu = proxy_url(p)
    if pu:
        return httpx.AsyncClient(proxy=pu, headers=headers, **kw)
    return httpx.AsyncClient(headers=headers, **kw)


def friendly(e: Exception) -> str:
    if isinstance(e, httpx.ProxyError):
        return "Proxy'ye bağlanılamadı. Adres, port ve kimlik bilgisini kontrol edin."
    if isinstance(e, httpx.ConnectError):
        return "Hedefe bağlanılamadı (proxy üzerinden erişilemiyor)."
    if isinstance(e, (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.PoolTimeout)):
        return "İstek zaman aşımına uğradı."
    s = str(e).lower()
    if isinstance(e, httpx.TLSError) or "ssl" in s:
        return "TLS/SSL bağlantı hatası."
    return str(e)[:300] or e.__class__.__name__


# ---------------------------------------------------------------------------
# Proxy API
# ---------------------------------------------------------------------------


@app.get("/api/proxies")
async def list_proxies():
    return {"proxies": list(PROXIES.values())}


@app.post("/api/proxies")
async def add_proxy(payload: dict):
    url = (payload.get("url") or "").strip()
    name = (payload.get("name") or "").strip()
    if not url:
        raise HTTPException(400, "Proxy adresi gerekli (örn: socks5://host:1080)")
    if url == "direct":
        raise HTTPException(400, "'direct' değeri saklıdır")
    scheme = url.split("://", 1)[0].lower() if "://" in url else ""
    if scheme not in ("http", "https", "socks5", "socks5h", "socks4", "socks4a"):
        raise HTTPException(400, "Desteklenmeyen şema: %r (http/https/socks5 kullanın)" % scheme)
    host = url.split("://", 1)[1].split("@")[-1].split(":")
    if len(host) < 2 or not host[1]:
        raise HTTPException(400, "Port gerekli (örn: socks5://host:1080)")
    pid = uuid.uuid4().hex[:8]
    p = {
        "id": pid,
        "name": name or f"{scheme}://{host[0]}:{host[1]}",
        "url": url,
        "created": time.time(),
        "last_test": None,
    }
    PROXIES[pid] = p
    _save()
    return p


@app.delete("/api/proxies/{pid}")
async def delete_proxy(pid: str):
    if pid == "direct":
        raise HTTPException(400, "Doğrudan bağlantı silinemez")
    p = PROXIES.pop(pid, None)
    if not p:
        raise HTTPException(404, "Proxy bulunamadı")
    _save()
    return {"ok": True}


@app.post("/api/proxies/{pid}/test")
async def test_proxy(pid: str):
    """Proxy'yi dene: çıkış IP'si + coğrafya + gecikme.

    IP servisi erişilemezse (örn. kısıtlı dış ağ) pypi.org ile
    ulaşılabilirlik kontrolü yapılır.
    """
    p = get_proxy(pid)
    t0 = time.time()
    result = {"ok": False, "ms": None, "ip": None, "country": None, "city": None,
              "note": None, "error": None}
    try:
        async with make_client(p, timeout=httpx.Timeout(12, connect=8)) as c:
            r = await c.get("https://api.ipify.org?format=json")
            r.raise_for_status()
            result["ip"] = r.json().get("ip")
            result["ok"] = True
        if result["ip"]:
            try:
                async with make_client(p, timeout=httpx.Timeout(8, connect=6)) as c:
                    g = await c.get(f"https://ipinfo.io/{result['ip']}/json")
                    j = g.json()
                    result["country"] = j.get("country")
                    result["city"] = j.get("city")
            except Exception:
                pass
    except Exception as e:
        # Yedek: IP servisi yerine ulaşılabilirlik kontrolü
        try:
            async with make_client(p, timeout=httpx.Timeout(10, connect=8)) as c:
                r2 = await c.get("https://pypi.org", timeout=httpx.Timeout(10, connect=8))
            if r2.status_code < 400:
                result["ok"] = True
                result["note"] = "Proxy çalışıyor; IP servisi bu ağdan erişilemedi"
            else:
                result["error"] = friendly(e)
        except Exception:
            result["error"] = friendly(e)
    result["ms"] = int((time.time() - t0) * 1000)
    p["last_test"] = {"at": time.time(), **result}
    _save()
    return p["last_test"]


@app.get("/api/whoami")
async def whoami():
    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get("https://api.ipify.org?format=json")
            ip = r.json().get("ip")
        geo = None
        try:
            async with httpx.AsyncClient(timeout=6) as c:
                g = await c.get(f"https://ipinfo.io/{ip}/json")
                geo = {"country": g.json().get("country"), "city": g.json().get("city")}
        except Exception:
            pass
        return {"ip": ip, "geo": geo, "ms": int((time.time() - t0) * 1000)}
    except Exception as e:
        return {"ip": None, "geo": None, "error": friendly(e)}


# ---------------------------------------------------------------------------
# HTML yeniden yazma
# ---------------------------------------------------------------------------

ATTR_RE = re.compile(
    r"(\s(?:href|src|action|poster|data-src|data-lazy-src)\s*=\s*)([\"'])(.*?)\2",
    re.I | re.S,
)
SRCSET_RE = re.compile(r'(\ssrcset\s*=\s*)"(.*?)"', re.I | re.S)
CSS_URL_RE = re.compile(r"url\(\s*([^)]*?)\s*\)", re.I)
SKIP_PREFIXES = ("javascript:", "data:", "mailto:", "tel:", "about:", "blob:", "ftp:", "#")


def _target_url(pid: str, abs_url: str) -> str:
    return "/api/fetch?" + urlencode({"url": abs_url, "proxy_id": pid})


def _rewrite_body(body: str, base_url: str, pid: str) -> str:
    # <base> ve meta-refresh tarayıcıyı beklediğimiz yerden çıkarır
    body = re.sub(r"<base[^>]*>", "", body, flags=re.I)
    body = re.sub(r"<meta[^>]*http-equiv\s*=\s*[\"']?refresh[\"']?[^>]*>", "", body, flags=re.I)

    def attr_repl(m):
        val = m.group(3).strip()
        if not val or val.lower().startswith(SKIP_PREFIXES):
            return m.group(0)
        try:
            target = urljoin(base_url, val)
        except ValueError:
            return m.group(0)
        if not target.lower().startswith(("http://", "https://")):
            return m.group(0)
        return f"{m.group(1)}{m.group(2)}{_target_url(pid, target)}{m.group(2)}"

    body = ATTR_RE.sub(attr_repl, body)

    def srcset_repl(m):
        parts = []
        for part in m.group(2).split(","):
            part = part.strip()
            if not part:
                continue
            bits = part.split(" ")
            url, desc = bits[0], bits[1:]
            if url.lower().startswith(("data:", "javascript:", "blob:")):
                parts.append(part)
                continue
            try:
                target = urljoin(base_url, url)
            except ValueError:
                parts.append(part)
                continue
            if target.lower().startswith(("http://", "https://")):
                parts.append(_target_url(pid, target) + (" " + " ".join(desc) if desc else ""))
            else:
                parts.append(part)
        return f'{m.group(1)}"{", ".join(parts)}"'

    body = SRCSET_RE.sub(srcset_repl, body)

    def css_repl(m):
        url = m.group(1).strip().strip("\"'")
        if not url or url.lower().startswith("data:"):
            return m.group(0)
        try:
            target = urljoin(base_url, url)
        except ValueError:
            return m.group(0)
        if target.lower().startswith(("http://", "https://")):
            return f"url({_target_url(pid, target)})"
        return m.group(0)

    body = CSS_URL_RE.sub(css_repl, body)
    return body


# ---------------------------------------------------------------------------
# Sayfa içi köprü scripti (link/form tıklamalarını ana uygulamaya iletir,
# sayfanın kendi fetch/XHR isteklerini de proxy'den geçirir)
# ---------------------------------------------------------------------------

BRIDGE_TEMPLATE = r"""
<script>
(function () {
  var PID = __PID__;
  var CURRENT = __CURRENT__;
  var MODE = __MODE__;      // "parent" | "view"
  var APPHOST = __APPHOST__;

  function appFetchUrl(u) {
    return "/api/fetch?url=" + encodeURIComponent(u) + "&proxy_id=" + PID;
  }
  function ext(u) {
    if (typeof u !== "string" || !u) return null;
    if (u.indexOf("/api/fetch") === 0) return null;
    var base = (u.charAt(0) === "/" || u.charAt(0) === "." || u.indexOf("//") === 0)
      ? CURRENT : null;
    try {
      var x = new URL(u, base || location.href);
      if (x.hostname === APPHOST) return null;
      if (x.protocol === "http:" || x.protocol === "https:") return x.href;
    } catch (e) {}
    return null;
  }
  // Sayfanın kendi JS istekleri (fetch/XHR) da proxy üzerinden gitsin
  var F = window.fetch;
  if (F) {
    window.fetch = function (input, init) {
      if (typeof input === "string") {
        var a = ext(input);
        if (a) input = appFetchUrl(a);
      } else if (input && typeof input.url === "string") {
        var b = ext(input.url);
        if (b) input = new Request(appFetchUrl(b), input);
      }
      return F.call(this, input, init);
    };
  }
  var XO = window.XMLHttpRequest;
  if (XO) {
    var oopen = XO.prototype.open;
    XO.prototype.open = function (m, u) {
      var x = ext(typeof u === "string" ? u : null);
      if (x) u = appFetchUrl(x);
      var args = [m, u];
      for (var i = 2; i < arguments.length; i++) args.push(arguments[i]);
      return oopen.apply(this, args);
    };
  }

  // Yeniden yazılmış href'ler içindeki ORİJİNAL hedef URL'yi geri çıkar
  function orig(h) {
    h = (h || "").trim();
    if (!h || h.charAt(0) === "#") return null;
    if (/^(javascript:|data:|mailto:|tel:|blob:)/i.test(h)) return null;
    try {
      var u = new URL(h, CURRENT);
      if (u.pathname.indexOf("/api/fetch") === 0) {
        var t = u.searchParams.get("url");
        if (t) return t;
      }
      return u.href;
    } catch (e) { return h; }
  }
  function post(o) {
    try { parent.postMessage(Object.assign({ __vpn: "1" }, o), "*"); } catch (e) {}
  }
  function goView(u, q) {
    var p = new URLSearchParams({ url: u, proxy_id: PID });
    if (q) p.set("q", q);
    return "/view?" + p.toString();
  }
  document.addEventListener("click", function (e) {
    var t = e.composedPath ? e.composedPath()[0] : e.target;
    while (t && t.nodeType !== 1) t = t.parentNode;
    while (t && t.tagName !== "A") t = t.parentNode;
    if (!t) return;
    var h = t.getAttribute("href");
    if (h && h.charAt(0) === "#") { e.preventDefault(); return; }
    var u = orig(h);
    if (!u) return;
    e.preventDefault();
    if (MODE === "view") {
      if (t.target === "_blank" || e.metaKey || e.ctrlKey) window.open(goView(u), "_blank");
      else location.href = goView(u);
      return;
    }
    if (t.target === "_blank" || e.metaKey || e.ctrlKey) post({ act: "newtab", url: u });
    else post({ act: "nav", url: u });
  }, true);
  document.addEventListener("submit", function (e) {
    e.preventDefault();
    var f = e.target;
    if (!f) return;
    var m = (f.method || "get").toLowerCase();
    if (m !== "get") {
      if (MODE === "parent") post({ act: "msg", text: "Bu demo yalnızca GET formlarını destekler." });
      return;
    }
    var p = new URLSearchParams();
    for (var i = 0; i < f.elements.length; i++) {
      var el = f.elements[i];
      if (el.name && (el.tagName !== "INPUT" || (el.type && el.type !== "submit")))
        p.append(el.name, el.value);
    }
    var base = orig(f.getAttribute("action")) || CURRENT;
    if (MODE === "view") { location.href = goView(base, p.toString()); return; }
    post({ act: "form", base: base, query: p.toString() });
  }, true);
})();
</script>
"""


def _bridge(pid: str, current: str, mode: str, apphost: str) -> str:
    return (
        BRIDGE_TEMPLATE
        .replace("__PID__", json.dumps(pid))
        .replace("__CURRENT__", json.dumps(current))
        .replace("__MODE__", json.dumps(mode))
        .replace("__APPHOST__", json.dumps(apphost))
    )


def inject_bridge(body: str, pid: str, current: str, mode: str, apphost: str) -> str:
    script = _bridge(pid, current, mode, apphost)
    m = re.search(r"</body>", body, re.I)
    if m:
        return body[: m.start()] + script + body[m.start():]
    return body + script


# ---------------------------------------------------------------------------
# Gezinme uçları
# ---------------------------------------------------------------------------


@app.get("/api/render")
async def render(url: str, proxy_id: str = "direct", request: Request = None):
    """Sayfayı seçilen proxy ile indirir, HTML'i yeniden yazar."""
    u = (url or "").strip()
    if not u.lower().startswith(("http://", "https://")):
        return JSONResponse({"ok": False, "error": "URL http(s) ile başlamalı."})
    p = get_proxy(proxy_id)
    pid = p["id"]
    apphost = request.url.hostname if request else ""
    t0 = time.time()
    try:
        async with make_client(p) as c:
            r = await c.get(u)
    except Exception as e:
        return JSONResponse({
            "ok": False, "error": friendly(e), "proxy": p["name"],
            "proxy_id": pid, "ms": int((time.time() - t0) * 1000),
        })
    ct = r.headers.get("content-type", "")
    ms = int((time.time() - t0) * 1000)
    final_url = str(r.url)
    out = {"ok": True, "url": final_url, "status": r.status_code, "ms": ms,
           "proxy": p["name"], "proxy_id": pid}
    if len(r.content) > MAX_RESOURCE_BYTES:
        return JSONResponse({"ok": False, "error": "Yanıt çok büyük (25MB limiti).",
                             "proxy": p["name"], "proxy_id": pid, "ms": ms})
    if "html" not in ct.lower():
        out["not_html"] = True
        out["fetch_url"] = _target_url(pid, final_url)
        return JSONResponse(out)
    body = r.text
    m = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
    out["title"] = unescape(m.group(1)).strip()[:200] if m else final_url
    body = _rewrite_body(body, final_url, pid)
    body = inject_bridge(body, pid, final_url, "parent", apphost)
    out["html"] = body
    return JSONResponse(out)


@app.get("/api/fetch")
async def fetch_resource(url: str, proxy_id: str = "direct"):
    """Tekil kaynak (css/js/görsel/font/pdf) — proxy üzerinden."""
    if not (url or "").lower().startswith(("http://", "https://")):
        raise HTTPException(400, "Geçersiz url")
    p = get_proxy(proxy_id)
    try:
        async with make_client(p, timeout=httpx.Timeout(25, connect=10)) as c:
            r = await c.get(url)
    except Exception as e:
        return HTMLResponse(
            "<pre>Proxy üzerinden kaynak alınamadı:\n" + str(friendly(e)),
            status_code=502,
        )
    if len(r.content) > MAX_RESOURCE_BYTES:
        return HTMLResponse("<pre>Kaynak çok büyük (25MB limiti).</pre>", status_code=502)
    ct = r.headers.get("content-type")
    return Response(
        content=r.content,
        status_code=r.status_code,
        media_type=ct or "application/octet-stream",
        headers={"Cache-Control": "private, max-age=300"},
    )


VIEW_BAR = (
    '<div style="position:sticky;top:0;z-index:2147483647;background:#0f1115;color:#e6ebf4;'
    'font:12px/1.6 system-ui,sans-serif;padding:7px 14px;display:flex;gap:10px;align-items:center;'
    'border-bottom:1px solid #2a3140">'
    '<span>🌐</span><b>VPN Browser</b>'
    '<span style="opacity:.65">proxy üzerinden: '
    "__NAME__</span>"
    '<span style="opacity:.6;margin-left:auto;max-width:50%;overflow:hidden;'
    'text-overflow:ellipsis;white-space:nowrap" dir="ltr">__URL__</span></div>'
)


@app.get("/view", response_class=HTMLResponse)
async def view_page(url: str, proxy_id: str = "direct", q: str = "", request: Request = None):
    """Yeni sekme için bağımsız görünüm."""
    u = (url or "").strip()
    if not u.lower().startswith(("http://", "https://")):
        raise HTTPException(400, "Geçersiz url")
    if q:
        u += ("&" if "?" in u else "?") + q
    p = get_proxy(proxy_id)
    pid = p["id"]
    apphost = request.url.hostname if request else ""
    try:
        async with make_client(p) as c:
            r = await c.get(u)
    except Exception as e:
        return HTMLResponse(
            f"<pre style='font:14px monospace;padding:24px'>Proxy üzerinden erişilemedi:\n"
            f"{friendly(e)}</pre>"
        )
    ct = r.headers.get("content-type", "")
    if "html" not in ct.lower():
        return Response(content=r.content, media_type=ct or "application/octet-stream")
    body = r.text
    body = _rewrite_body(body, str(r.url), pid)
    body = inject_bridge(body, pid, str(r.url), "view", apphost)
    bar = VIEW_BAR.replace("__NAME__", p["name"]).replace("__URL__", str(r.url))
    m = re.search(r"<body[^>]*>", body, re.I)
    if m:
        body = body[: m.end()] + bar + body[m.end():]
    else:
        body = bar + body
    return HTMLResponse(body)


# ---------------------------------------------------------------------------
# Uygulama
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/healthz")
async def healthz():
    return {"ok": True, "proxies": len(PROXIES)}


app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
