/* VPN Browser — istemci tarafı mantığı */
(() => {
  const $ = (s) => document.querySelector(s);

  const state = {
    proxies: [],
    selected: "direct",
    history: [],
    hi: -1,
    current: null,
    currentTitle: null,
    exit: {}, // pid -> {ip, country, city}
    busy: false,
  };

  const view = $("#view");
  const errPage = $("#errPage");
  const loading = $("#loading");
  const loadingText = $("#loadingText");
  const addr = $("#addr");

  /* ---------------- API ---------------- */
  async function api(path, opts = {}) {
    const r = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...opts,
    });
    let j = {};
    try { j = await r.json(); } catch { j = { error: "HTTP " + r.status }; }
    if (!r.ok && !j.error) j.error = "HTTP " + r.status;
    return { ok: r.ok, ...j };
  }

  function flash(msg, ms = 5000) {
    const el = $("#stMsg");
    el.textContent = msg;
    clearTimeout(el._t);
    el._t = setTimeout(() => (el.textContent = ""), ms);
  }

  /* ---------------- proxy listesi ---------------- */
  function proxyTag(p) {
    if (p.url === "direct") return "DOĞRUDAN";
    const s = p.url.split("://")[0].toUpperCase();
    return s.replace("SOCKS5H", "SOCKS5");
  }

  function renderProxies() {
    const list = $("#proxyList");
    const sel = $("#proxySel");
    list.innerHTML = "";
    state.proxies.forEach((p) => {
      const row = document.createElement("div");
      row.className = "prow" + (p.id === state.selected ? " selected" : "");
      row.dataset.id = p.id;
      row.innerHTML = `
        <label class="pradio">
          <input type="radio" name="pselect" ${p.id === state.selected ? "checked" : ""}>
          <div class="pinfo">
            <div class="pname">${esc(p.name)} <span class="ptag ${p.url === "direct" ? "direct" : ""}">${proxyTag(p)}</span></div>
            <div class="purl">${esc(p.url)}</div>
            <div class="pstatus" data-role="status"></div>
          </div>
        </label>
        <div class="pacts">
          <button data-role="test" title="Test et (çıkış IP)">📡</button>
          ${p.id !== "direct" ? `<button data-role="del" title="Sil">✕</button>` : ""}
        </div>`;
      row.querySelector("input").addEventListener("change", () => selectProxy(p.id));
      row.querySelector('[data-role="test"]').addEventListener("click", () => testProxy(p.id, true));
      const del = row.querySelector('[data-role="del"]');
      if (del) del.addEventListener("click", () => deleteProxy(p.id));
      list.appendChild(row);
      statusEl(p.id);
    });
  }

  function statusEl(pid) {
    return document.querySelector(`.prow[data-id="${pid}"] [data-role="status"]`);
  }

  function paintStatus(pid) {
    const p = state.proxies.find((x) => x.id === pid);
    const el = statusEl(pid);
    if (!p || !el) return;
    const t = p.last_test;
    if (!t) { el.textContent = "test edilmedi"; el.className = "pstatus"; return; }
    if (t.ok) {
      const loc = [t.city, t.country].filter(Boolean).join(" · ");
      el.textContent = `${t.ip || "—"}${loc ? " · " + loc : ""}${t.note ? " · " + t.note : ""} · ${t.ms} ms`;
      el.className = "pstatus ok";
      if (t.ip) state.exit[pid] = { ip: t.ip, country: t.country, city: t.city };
    } else {
      el.textContent = "hata: " + (t.error || "bilinmiyor");
      el.className = "pstatus err";
      delete state.exit[pid];
    }
  }

  async function loadProxies() {
    const r = await api("/api/proxies");
    if (r.ok) state.proxies = r.proxies;
    renderProxies();
  }

  async function addProxy(name, url) {
    const r = await api("/api/proxies", { method: "POST", body: JSON.stringify({ name, url }) });
    if (!r.ok) return flash("Ekleme hatası: " + r.error);
    state.proxies.push(r);
    renderProxies();
    selectProxy(r.id);
    flash("Proxy eklendi: " + r.name);
  }

  async function deleteProxy(pid) {
    const p = state.proxies.find((x) => x.id === pid);
    if (!p) return;
    if (!confirm(`"${p.name}" proxy'si silinsin mi?`)) return;
    const r = await api("/api/proxies/" + pid, { method: "DELETE" });
    if (!r.ok) return flash("Silme hatası: " + r.error);
    state.proxies = state.proxies.filter((x) => x.id !== pid);
    if (state.selected === pid) selectProxy("direct", { reload: false });
    renderProxies();
  }

  async function testProxy(pid, userClicked = false) {
    const el = statusEl(pid);
    if (el) { el.textContent = "test ediliyor…"; el.className = "pstatus busy"; }
    const r = await api(`/api/proxies/${pid}/test`, { method: "POST" });
    const p = state.proxies.find((x) => x.id === pid);
    if (p && r && r.at !== undefined) { p.last_test = r; paintStatus(pid); updateExitBar(); }
    if (userClicked) flash(r && r.ok ? `Proxy çalışıyor${r.ip ? " — çıkış IP: " + r.ip : ""}` : "Proxy testi başarısız: " + (r && r.error));
  }

  function selectProxy(pid, { reload = true } = {}) {
    if (!state.proxies.some((p) => p.id === pid)) return;
    state.selected = pid;
    renderProxies();
    updateExitBar();
    const p = state.proxies.find((x) => x.id === pid);
    if (p && (!p.last_test || Date.now() - p.last_test.at > 5 * 60 * 1000)) testProxy(pid);
    if (reload && state.current) browse(state.current, { push: false });
  }

  function updateExitBar() {
    const p = state.proxies.find((x) => x.id === state.selected);
    $("#stProxy").innerHTML = "Proxy: <b>" + esc(p ? p.name : "—") + "</b>";
    const ex = state.exit[state.selected];
    $("#stExit").innerHTML = ex
      ? `Çıkış IP: <b>${esc(ex.ip)}</b>${ex.country ? " · " + esc(ex.country) : ""}`
      : "Çıkış IP: <b>—</b> (test etmek için 📡)";
  }

  /* ---------------- gezinme ---------------- */
  function normalize(raw) {
    let u = raw.trim();
    if (!u) return null;
    if (!/^https?:\/\//i.test(u)) {
      if (/^[\w-]+(\.[\w-]+)+([/?#]|$)/.test(u)) u = "https://" + u;
      else u = "https://duckduckgo.com/html/?q=" + encodeURIComponent(u);
    }
    return u;
  }

  function pushHistory(u) {
    state.history = state.history.slice(0, state.hi + 1);
    state.history.push(u);
    state.hi = state.history.length - 1;
    updateNavBtns();
  }

  function updateNavBtns() {
    $("#btnBack").disabled = state.hi <= 0;
    $("#btnFwd").disabled = state.hi >= state.history.length - 1;
  }

  async function browse(raw, { push = true } = {}) {
    const u = normalize(raw);
    if (!u || state.busy) return;
    state.busy = true;
    addr.value = u;
    if (push) pushHistory(u);
    else state.hi = Math.max(0, state.hi);

    const p = state.proxies.find((x) => x.id === state.selected);
    loadingText.textContent = `${p ? p.name : "proxy"} üzerinden alınıyor: ${u}`;
    loading.classList.remove("hidden");
    errPage.classList.add("hidden");

    try {
      const r = await api("/api/render?" + new URLSearchParams({ url: u, proxy_id: state.selected }));
      if (r.ok) {
        state.current = u;
        state.currentTitle = r.title;
        if (r.not_html) {
          // pdf, dosya indirimi vb.
          view.removeAttribute("srcdoc");
          view.src = r.fetch_url;
        } else {
          view.removeAttribute("src");
          view.srcdoc = r.html;
        }
        document.title = (r.title || u) + " — VPN Browser";
        $("#stInfo").innerHTML =
          `Son: <b>${esc(new URL(r.url).host)}</b> · HTTP ${r.status} · ${r.ms} ms`;
      } else {
        showError(r.error, u);
      }
    } catch (e) {
      showError(String(e), u);
    } finally {
      loading.classList.add("hidden");
      state.busy = false;
    }
  }

  function showError(msg, u) {
    loading.classList.add("hidden");
    view.classList.add("hidden");
    errPage.classList.remove("hidden");
    errPage.innerHTML = `
      <div class="errcard">
        <div class="big">🚫</div>
        <h3>Sayfa alınamadı</h3>
        <p><b>${esc(u)}</b></p>
        <p>${esc(msg)}</p>
        <div class="actions">
          <button class="primary" data-act="retry">↻ Tekrar dene</button>
          <button data-act="direct">Doğrudan dene (sunucu IP)</button>
        </div>
      </div>`;
    errPage.querySelector('[data-act="retry"]').addEventListener("click", () => browse(u, { push: false }));
    errPage.querySelector('[data-act="direct"]').addEventListener("click", () => {
      selectProxy("direct", { reload: false });
      browse(u, { push: false });
    });
  }

  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  /* ---------------- sayfa içi köprüden gelen mesajlar ---------------- */
  window.addEventListener("message", (e) => {
    const d = e.data;
    if (!d || d.__vpn !== "1") return;
    if (d.act === "nav") browse(d.url);
    else if (d.act === "newtab")
      window.open("/view?" + new URLSearchParams({ url: d.url, proxy_id: state.selected }), "_blank");
    else if (d.act === "form") {
      let base = d.base || state.current;
      if (base && d.query) base += (base.includes("?") ? "&" : "?") + d.query;
      browse(base);
    } else if (d.act === "msg") flash(d.text);
  });

  /* ---------------- olaylar ---------------- */
  $("#btnGo").addEventListener("click", () => browse(addr.value));
  addr.addEventListener("keydown", (e) => { if (e.key === "Enter") browse(addr.value); });
  $("#btnReload").addEventListener("click", () => state.current && browse(state.current, { push: false }));
  $("#btnHome").addEventListener("click", () => browse("https://pypi.org"));
  $("#btnBack").addEventListener("click", () => {
    if (state.hi > 0) { state.hi--; updateNavBtns(); browse(state.history[state.hi], { push: false }); }
  });
  $("#btnFwd").addEventListener("click", () => {
    if (state.hi < state.history.length - 1) { state.hi++; updateNavBtns(); browse(state.history[state.hi], { push: false }); }
  });
  $("#btnSide").addEventListener("click", () => {
    const sb = $("#sidebar");
    sb.classList.toggle("closed");
    $("#btnSide").textContent = sb.classList.contains("closed") ? "⟨" : "⟩";
  });
  $("#addForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const name = $("#pName").value.trim();
    const url = $("#pUrl").value.trim();
    if (!url) return;
    addProxy(name, url);
    e.target.reset();
  });

  /* ---------------- başlat ---------------- */
  (async function init() {
    updateNavBtns();
    await loadProxies();
    const w = await api("/api/whoami");
    $("#serverIp").textContent = w.ip
      ? `Sunucu IP: ${w.ip}${w.geo && w.geo.country ? " · " + w.geo.country : ""}`
      : "Sunucu IP: — (dış erişim kısıtlı)";
    if (!state.proxies.some((p) => p.id === state.selected)) state.selected = state.proxies[0].id;
    renderProxies();
    updateExitBar();
    const def = state.proxies.find((p) => p.id === state.selected);
    if (def && (!def.last_test || Date.now() - def.last_test.at > 5 * 60 * 1000)) testProxy(def.id);
    browse("https://pypi.org");
  })();
})();
