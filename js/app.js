/* 私人数据时光轴 — 前端应用 */
(function () {
  const state = {
    page: "home",
    metrics: [],
    meta: {},
    selectedDate: todayISO(),
    calendar: null,
    entryDraft: null,
    trendKeys: ["weight", "sleep_hours", "mood"],
    trendGranularity: "day",
    trendStart: addDays(todayISO(), -29),
    trendEnd: todayISO(),
    reportYear: new Date().getFullYear(),
    reportMonth: new Date().getMonth() + 1,
    report: null,
    importStep: "upload",
    importCsvText: "",
    importMapping: null,
    importPreview: null,
    firstRun: false,
  };

  // ---------- utils ----------
  function todayISO() {
    const d = new Date();
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
  }
  function addDays(iso, n) {
    const d = new Date(iso + "T00:00:00");
    d.setDate(d.getDate() + n);
    return d.toISOString().slice(0, 10);
  }
  function fmt(n, digits = 1) {
    if (n == null || n === undefined || Number.isNaN(Number(n))) return "—";
    return Number(n).toFixed(digits).replace(/\.0+$/, (m) => (m.includes(".") ? "" : m));
  }
  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
  }
  function $(sel, root = document) { return root.querySelector(sel); }
  function $all(sel, root = document) { return Array.from(root.querySelectorAll(sel)); }

  async function api(path, options = {}) {
    const opts = {
      headers: { "Content-Type": "application/json" },
      ...options,
      credentials: "same-origin",
    };
    if (opts.body && typeof opts.body !== "string") opts.body = JSON.stringify(opts.body);
    const res = await fetch(path, opts);
    let data = null;
    const ctype = res.headers.get("content-type") || "";
    if (ctype.includes("application/json")) data = await res.json();
    else data = await res.text();
    if (res.status === 401 && !path.startsWith("/api/auth/")) {
      // session expired
      showAuthScreen();
      const err = new Error("请先登录");
      err.data = data;
      throw err;
    }
    if (!res.ok) {
      const msg = (data && data.error) || `请求失败 ${res.status}`;
      const err = new Error(msg);
      err.data = data;
      throw err;
    }
    return data;
  }

  function toast(msg, type = "") {
    const root = $("#toast-root");
    const el = document.createElement("div");
    el.className = `toast ${type}`;
    el.textContent = msg;
    root.appendChild(el);
    setTimeout(() => el.remove(), 3200);
  }

  function modal(html) {
    const root = $("#modal-root");
    root.innerHTML = `<div class="modal-backdrop"><div class="modal">${html}</div></div>`;
    root.querySelector(".modal-backdrop").addEventListener("click", (e) => {
      if (e.target.classList.contains("modal-backdrop")) closeModal();
    });
    return root.querySelector(".modal");
  }
  function closeModal() { $("#modal-root").innerHTML = ""; }

  // ---------- boot ----------
  async function boot() {
    try {
      const health = await api("/api/health");
      const pill = $("#status-pill");
      if (pill) pill.innerHTML = `<span class="dot"></span>本地服务 · 端口 ${health.port}`;
      const dbPathEl = $("#db-path");
      if (dbPathEl) dbPathEl.textContent = "多用户 · 分库隔离";

      // auth gate
      let me = null;
      try {
        me = await api("/api/auth/me");
      } catch (e) {
        me = null;
      }
      if (!me || !me.user) {
        showAuthScreen();
        return;
      }
      state.user = me.user;

      const meta = await api("/api/meta");
      state.meta = meta;
      state.firstRun = !meta.first_run_done;
      await loadMetrics();
      bindNav();
      if (state.firstRun) showOnboarding();
      else await navigate("home");
    } catch (e) {
      console.error("boot failed", e);
      const page = $("#page");
      if (page) page.innerHTML = `<div class="error-box">启动失败：${esc(e.message)}</div>`;
      toast("无法连接本地服务：" + e.message, "error");
    }
  }

  function showAuthScreen() {
    const page = $("#page");
    $("#page-title").textContent = "登录";
    const app = $("#app");
    app.style.gridTemplateColumns = "1fr";
    const side = document.querySelector(".sidebar");
    if (side) side.style.display = "none";
    page.innerHTML = `
      <section class="card hero" style="max-width:520px;margin:40px auto;width:100%">
        <div class="sub">私人数据时光轴 · 多用户</div>
        <h2>登录 / 注册</h2>
        <p class="sub">每位成员数据独立存放（data/users/&lt;id&gt;/timeline.db）。首个账号自动成为管理员。</p>
        <div class="tabs" style="margin-top:14px">
          <button class="tab active" id="tab-login">登录</button>
          <button class="tab" id="tab-reg">注册管理员</button>
        </div>
        <div id="auth-form"></div>
      </section>
    `;
    const renderLogin = () => {
      $("#auth-form").innerHTML = `
        <div class="field"><label>用户名</label><input id="au-user" placeholder="username" /></div>
        <div class="field"><label>密码</label><input id="au-pass" type="password" placeholder="至少 6 位" /></div>
        <button class="btn on-hero" id="au-go">登录</button>
        <div id="au-msg"></div>
      `;
      $("#au-go").onclick = async () => {
        try {
          const r = await api("/api/auth/login", {
            method: "POST",
            body: { username: $("#au-user").value, password: $("#au-pass").value }
          });
          if (r.ok) {
            toast("欢迎 " + (r.user.display_name || r.user.username), "ok");
            location.reload();
          }
        } catch (e) {
          $("#au-msg").innerHTML = `<div class="error-box" style="margin-top:10px">${esc(e.message)}</div>`;
        }
      };
    };
    const renderReg = () => {
      $("#auth-form").innerHTML = `
        <div class="warn-box" style="margin-bottom:10px">
          库内已有账号时，新注册需要 <b>邀请码</b>（向管理员索取）。<br/>
          若是全新安装且还没有任何用户，可直接创建首个管理员。
        </div>
        <div class="field"><label>邀请码（已有用户时必填）</label><input id="au-invite" placeholder="INV-XXXXXXXX" /></div>
        <div class="field"><label>用户名</label><input id="au-user" /></div>
        <div class="field"><label>显示名</label><input id="au-name" /></div>
        <div class="field"><label>密码（≥6）</label><input id="au-pass" type="password" /></div>
        <button class="btn on-hero" id="au-go">注册</button>
        <div id="au-msg"></div>
      `;
      $("#au-go").onclick = async () => {
        try {
          const r = await api("/api/auth/register", {
            method: "POST",
            body: {
              invite_code: $("#au-invite").value.trim(),
              username: $("#au-user").value,
              password: $("#au-pass").value,
              display_name: $("#au-name").value
            }
          });
          if (r.ok) {
            toast(r.message || "注册成功", "ok");
            location.reload();
          }
        } catch (e) {
          $("#au-msg").innerHTML = `<div class="error-box" style="margin-top:10px">${esc(e.message)}</div>`;
        }
      };
    };
    $("#tab-login").onclick = () => {
      $("#tab-login").classList.add("active");
      $("#tab-reg").classList.remove("active");
      renderLogin();
    };
    $("#tab-reg").onclick = () => {
      $("#tab-reg").classList.add("active");
      $("#tab-login").classList.remove("active");
      renderReg();
    };
    renderLogin();
  }

  function bindNav() {
    $all("#nav .nav-item").forEach(btn => {
      btn.addEventListener("click", () => navigate(btn.dataset.page));
    });
    $("#btn-quick-today").addEventListener("click", () => {
      state.selectedDate = todayISO();
      navigate("day");
    });
    const aiQuick = $("#btn-ai-quick");
    if (aiQuick) aiQuick.addEventListener("click", () => navigate("home"));
  }

  async function loadMetrics() {
    const data = await api("/api/metrics");
    state.metrics = data.metrics || [];
  }

  async function navigate(page) {
    state.page = page;
    $all("#nav .nav-item").forEach(b => b.classList.toggle("active", b.dataset.page === page));
    const titles = {
      home: "首页", day: "日记录", calendar: "日历", trends: "趋势",
      report: "月报", import: "导入导出", metrics: "指标设置",
      members: "成员", settings: "账号与密钥", api: "AI 接口", help: "帮助"
    };
    $("#page-title").textContent = titles[page] || page;
    const el = $("#page");
    el.innerHTML = `<div class="card"><div class="muted">加载中…</div></div>`;
    try {
      if (page === "home") await renderHome(el);
      else if (page === "day") await renderDay(el);
      else if (page === "calendar") await renderCalendar(el);
      else if (page === "trends") await renderTrends(el);
      else if (page === "report") await renderReport(el);
      else if (page === "import") await renderImport(el);
      else if (page === "metrics") await renderMetrics(el);
      else if (page === "members") await renderMembers(el);
      else if (page === "settings") await renderSettings(el);
      else if (page === "api") renderApiDocs(el);
      else if (page === "help") renderHelp(el);
    } catch (e) {
      el.innerHTML = `<div class="error-box">页面加载失败：${esc(e.message)}</div>`;
    }
  }

  // ---------- onboarding ----------
  function showOnboarding() {
    const m = modal(`
      <h3>欢迎使用私人数据时光轴</h3>
      <p class="muted">数据只保存在本机，不上传云端。你可以随时导出 CSV / JSON 备份。</p>
      <ul class="checklist">
        <li><div class="box done"></div><div><b>记录一天</b><div class="small muted">体重、睡眠、心情、运动与备注，支持自定义指标</div></div></li>
        <li><div class="box"></div><div><b>导入 CSV</b><div class="small muted">预览、字段映射、日期格式与重复记录策略，不会静默覆盖</div></div></li>
        <li><div class="box"></div><div><b>看趋势与相关</b><div class="small muted">缺失日期显示为空隙，不当成 0</div></div></li>
        <li><div class="box"></div><div><b>月报导出</b><div class="small muted">Markdown 与可打印页面；Obsidian 可直接导入 Markdown</div></div></li>
      </ul>
      <div class="warn-box" style="margin:14px 0">
        <b>睡眠跨午夜约定：</b>日期按本地日历日记录；跨午夜的睡眠记在<b>起床日</b>。时区可在记录中备注。
      </div>
      <div class="row" style="justify-content:flex-end">
        <button class="btn ghost" id="ob-later">稍后再说</button>
        <button class="btn primary" id="ob-start">开始记录今天</button>
      </div>
    `);
    m.querySelector("#ob-start").onclick = async () => {
      await api("/api/meta/first-run", { method: "POST", body: { timezone: Intl.DateTimeFormat().resolvedOptions().timeZone } });
      state.firstRun = false;
      closeModal();
      state.selectedDate = todayISO();
      navigate("day");
    };
    m.querySelector("#ob-later").onclick = async () => {
      await api("/api/meta/first-run", { method: "POST", body: {} });
      state.firstRun = false;
      closeModal();
      navigate("home");
    };
  }

  // ---------- home ----------
  async function renderHome(root) {
    const today = todayISO();
    const startMissing = addDays(today, -13);
    const [missingRes, calRes, entryRes] = await Promise.all([
      api(`/api/missing?start=${startMissing}&end=${today}`),
      api(`/api/calendar?year=${today.slice(0,4)}&month=${Number(today.slice(5,7))}`),
      api(`/api/entries/${today}`),
    ]);
    const missing = missingRes.missing_dates || [];
    const entry = entryRes.entry;
    const cal = calRes.calendar;

    // recent 7-day mini trend for weight/mood
    const trends = await api(`/api/trends?metrics=weight,sleep_hours,mood&start=${addDays(today,-13)}&end=${today}&granularity=day`);

    root.innerHTML = `
      <section class="card hero">
        <div class="sub">今日 · ${esc(today)} · 数据只在本机</div>
        <h2>${entry ? "今天已有记录，可继续改" : "用一句话记录今天"}</h2>
        <p class="sub">不用逐项点填。直接说人话，AI 接口解析成结构化指标；也支持给外部 Agent 调用。</p>
        <div class="ai-composer">
          <textarea id="ai-text" placeholder="例如：今天体重68.5公斤，睡了7个半小时，心情不错，跑步了30分钟，有点累但还行"></textarea>
          <div class="row">
            <button class="btn on-hero" id="ai-parse">解析预览</button>
            <button class="btn on-hero-ghost" id="ai-save">解析并写入</button>
            <button class="btn on-hero-ghost" id="go-today">${entry ? "打开表单" : "表单录入"}</button>
            <button class="btn on-hero-ghost" id="go-import">导入 CSV</button>
            <button class="btn on-hero-ghost" id="go-trends">看趋势</button>
          </div>
          <div class="ai-hint">支持：今天/昨天/前天 · 68kg / 136斤 · 睡了7个半小时 · 心情很差/不错 · 跑了30分钟 · 鼾声12。缺失项不会被写成 0。</div>
          <div id="ai-result"></div>
        </div>
      </section>

      <section class="grid cols-3">
        <div class="card">
          <h3>今日快照</h3>
          <div class="stat-grid" id="today-snapshot"></div>
        </div>
        <div class="card">
          <h3>最近趋势（14 天）</h3>
          <div class="sub">缺失数据显示为空隙，不按 0 计算</div>
          <div class="chart-box" id="home-chart"></div>
          <div class="legend" id="home-legend"></div>
        </div>
        <div class="card">
          <h3>待补记录</h3>
          <div class="sub">最近 14 天中还没有记录的日期</div>
          <div class="missing-list" id="home-missing"></div>
        </div>
      </section>

      <section class="card">
        <div class="row between">
          <div>
            <h3>本月日历速览</h3>
            <div class="sub">有记录 ${cal.entry_count} 天 · 缺失 ${cal.missing_dates.length} 天</div>
          </div>
          <button class="btn small" id="go-calendar">打开完整日历</button>
        </div>
        <div class="calendar" id="home-calendar"></div>
      </section>

      <section class="card">
        <h3>为什么不是又一个手账 App</h3>
        <div class="diff-grid">
          <div class="diff-card"><div class="t">本地优先</div><div class="d">SQLite 落在项目 <code>data/timeline.db</code>，无账号无云盘。断网也能记，导出即备份。</div></div>
          <div class="diff-card"><div class="t">AI 可写入</div><div class="d">开放 <code>/api/ai/ingest</code>：自然语言或 JSON。手机备忘录、语音转文字、外部 Agent 都能喂进来。</div></div>
          <div class="diff-card"><div class="t">缺失诚实</div><div class="d">没记就是没记。图表断开、统计不计入分母，绝不拿 0 糊弄趋势和相关分析。</div></div>
          <div class="diff-card"><div class="t">时间语义清楚</div><div class="d">跨午夜睡眠记起床日、日期按本地日历日。导入有预览、字段映射、重复策略，不静默覆盖。</div></div>
        </div>
      </section>
    `;

    const bindAi = () => {
      const out = $("#ai-result");
      const text = () => ($("#ai-text").value || "").trim();
      const renderDraft = (draft, savedMsg) => {
        const vals = draft.values || {};
        const rows = Object.entries(vals).map(([k, v]) =>
          `<div class="kv"><span>${esc(k)}</span><b>${esc(v)}</b></div>`
        ).join("");
        out.innerHTML = `
          <div class="ai-result">
            <div class="row between">
              <b>解析结果 · ${esc(draft.date)}</b>
              ${savedMsg ? `<span class="tag ok">${esc(savedMsg)}</span>` : `<span class="tag accent2">未写入</span>`}
            </div>
            ${rows || `<div class="muted small">没有识别到指标值</div>`}
            <div class="kv"><span>备注</span><span class="small" style="max-width:60%">${esc(draft.note || "")}</span></div>
            ${(draft.suggestions || []).length ? `<div class="warn-box" style="margin-top:8px">${draft.suggestions.map(esc).join("<br/>")}</div>` : ""}
          </div>`;
      };
      $("#ai-parse").onclick = async () => {
        const t = text();
        if (!t) return toast("先写一句话", "error");
        try {
          const draft = await api("/api/ai/parse", { method: "POST", body: { text: t } });
          renderDraft(draft, null);
        } catch (e) { toast(e.message, "error"); }
      };
      $("#ai-save").onclick = async () => {
        const t = text();
        if (!t) return toast("先写一句话", "error");
        try {
          const res = await api("/api/ai/ingest", { method: "POST", body: { text: t, confirm: true } });
          renderDraft(res.draft || { date: res.date, values: {}, note: "" }, res.message || "已写入");
          toast(res.message || "已写入", "ok");
          // refresh snapshot quickly
          const again = await api(`/api/entries/${today}`);
          const snap = $("#today-snapshot");
          if (snap && again.entry) {
            const keys = [["weight","体重","kg"],["sleep_hours","睡眠","小时"],["mood","心情","分"],["exercise_minutes","运动","分钟"]];
            snap.innerHTML = keys.map(([k, label, unit]) => {
              const item = again.entry.values && again.entry.values[k];
              return `<div class="stat-item"><div class="k">${label}</div><div class="v">${item ? esc(fmt(item.value)) : "—"}</div><div class="k">${unit}</div></div>`;
            }).join("");
          }
        } catch (e) { toast(e.message, "error"); }
      };
    };
    bindAi();

    // snapshot
    const snap = $("#today-snapshot");
    const keys = [
      ["weight", "体重", "kg"],
      ["sleep_hours", "睡眠", "小时"],
      ["mood", "心情", "分"],
      ["exercise_minutes", "运动", "分钟"],
    ];
    snap.innerHTML = keys.map(([k, label, unit]) => {
      const item = entry && entry.values && entry.values[k];
      return `<div class="stat-item"><div class="k">${label}</div><div class="v">${item ? esc(fmt(item.value)) : "—"}</div><div class="k">${unit}</div></div>`;
    }).join("");

    // chart
    const seriesMeta = [
      { key: "weight", name: "体重", color: "#1f7a68" },
      { key: "sleep_hours", name: "睡眠", color: "#3d5a80" },
      { key: "mood", name: "心情", color: "#c45c26" },
    ];
    const series = seriesMeta.map(s => ({
      name: s.name,
      color: s.color,
      points: (trends.series[s.key] || []).map(p => ({ date: p.date, value: p.value })),
    }));
    TimelineCharts.lineChart($("#home-chart"), { series, height: 230, title: "体重 / 睡眠 / 心情" });
    $("#home-legend").innerHTML = seriesMeta.map(s =>
      `<span class="legend-item"><span class="legend-swatch" style="background:${s.color}"></span>${s.name}</span>`
    ).join("");

    // missing chips
    const missEl = $("#home-missing");
    if (!missing.length) {
      missEl.innerHTML = `<div class="ok-box">最近 14 天记录齐全，很好！</div>`;
    } else {
      missEl.innerHTML = missing.map(d => `<button class="chip" data-date="${d}">${d}</button>`).join("");
      $all(".chip", missEl).forEach(btn => {
        btn.onclick = () => {
          state.selectedDate = btn.dataset.date;
          navigate("day");
        };
      });
    }

    // mini calendar
    renderCalendarGrid($("#home-calendar"), cal);

    $("#go-today").onclick = () => { state.selectedDate = todayISO(); navigate("day"); };
    $("#go-import").onclick = () => navigate("import");
    $("#go-trends").onclick = () => navigate("trends");
    $("#go-calendar").onclick = () => navigate("calendar");
  }

  function renderCalendarGrid(container, cal) {
    const heads = ["一", "二", "三", "四", "五", "六", "日"];
    // weekday of first day; Mon=0 in our heads
    const first = new Date(cal.start + "T00:00:00");
    let lead = (first.getDay() + 6) % 7; // Mon-based
    let html = heads.map(h => `<div class="cal-head">${h}</div>`).join("");
    for (let i = 0; i < lead; i++) html += `<div></div>`;
    cal.days.forEach(d => {
      const cls = [
        "cal-day",
        d.has_entry ? "" : "missing",
        d.date === todayISO() ? "today" : "",
      ].join(" ");
      html += `
        <div class="${cls}" data-date="${d.date}">
          <div class="dnum">${Number(d.date.slice(8))}</div>
          ${d.has_entry ? `<div class="dot"></div>` : `<div class="small muted" style="margin-top:8px">待补</div>`}
          ${d.note ? `<div class="note">${esc(d.note)}</div>` : ""}
        </div>
      `;
    });
    container.innerHTML = html;
    $all(".cal-day", container).forEach(el => {
      el.onclick = () => {
        state.selectedDate = el.dataset.date;
        navigate("day");
      };
    });
  }

  // ---------- day ----------
  async function renderDay(root) {
    const d = state.selectedDate;
    await loadMetrics();
    const res = await api(`/api/entries/${d}`);
    const entry = res.entry;
    const metrics = state.metrics;

    root.innerHTML = `
      <div class="card">
        <div class="row between">
          <div>
            <h3>${esc(d)} 的记录</h3>
            <div class="sub">跨午夜睡眠请记在<b>起床日</b>。空值表示缺失，不会按 0 保存。</div>
          </div>
          <div class="row">
            <button class="btn small" id="day-prev">前一天</button>
            <button class="btn small" id="day-today">今天</button>
            <button class="btn small" id="day-next">后一天</button>
          </div>
        </div>
        <div class="field-row" style="margin-top:8px">
          <div class="field">
            <label>选择日期</label>
            <input type="date" id="day-date" value="${d}" />
          </div>
          <div class="field">
            <label>时区（可选，仅作备注）</label>
            <input type="text" id="day-tz" placeholder="例如 Asia/Shanghai" value="${esc(entry && entry.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone)}" />
          </div>
        </div>
      </div>

      <div class="card">
        <h3>指标</h3>
        <div class="grid cols-2" id="day-fields"></div>
      </div>

      <div class="card">
        <h3>当日备注</h3>
        <div class="field">
          <label>写点什么（可搜索）</label>
          <textarea id="day-note" rows="4" placeholder="今天的状态、事件、想法…">${esc(entry && entry.note || "")}</textarea>
        </div>
        <div class="row">
          <button class="btn primary" id="day-save">保存记录</button>
          ${entry ? `<button class="btn danger" id="day-delete">删除当日记录</button>` : ""}
          <span class="muted small">保存后立即写入本机 SQLite，重启仍在。</span>
        </div>
      </div>
    `;

    const fields = $("#day-fields");
    fields.innerHTML = metrics.map(m => {
      const item = entry && entry.values && entry.values[m.key];
      let val = item ? item.value : "";
      if (m.value_type !== "text" && val !== "" && val != null) {
        const n = Number(val);
        if (Number.isFinite(n)) val = String(Math.round(n * 1e6) / 1e6);
      }
      const rangeHint = m.min_value != null && m.max_value != null
        ? `合理范围 ${m.min_value}–${m.max_value}${m.unit ? " " + m.unit : ""}`
        : (m.unit ? `单位 ${m.unit}` : "");
      if (m.value_type === "text") {
        return `
          <div class="field" data-key="${m.key}">
            <label>${esc(m.name)}${m.unit ? `（${esc(m.unit)}）` : ""}</label>
            <input type="text" data-metric="${m.key}" value="${esc(val)}" placeholder="${esc(m.name)}" />
            <div class="hint">${esc(rangeHint)}</div>
          </div>`;
      }
      return `
        <div class="field" data-key="${m.key}">
          <label>${esc(m.name)}${m.unit ? `（${esc(m.unit)}）` : ""}</label>
          <input type="number" step="any" data-metric="${m.key}" value="${val === "" || val == null ? "" : esc(val)}"
                 ${m.min_value != null ? `min="${m.min_value}"` : ""} ${m.max_value != null ? `max="${m.max_value}"` : ""}
                 placeholder="留空表示缺失" />
          <div class="hint">${esc(rangeHint)}</div>
        </div>`;
    }).join("");

    $("#day-prev").onclick = () => { state.selectedDate = addDays(state.selectedDate, -1); navigate("day"); };
    $("#day-next").onclick = () => { state.selectedDate = addDays(state.selectedDate, 1); navigate("day"); };
    $("#day-today").onclick = () => { state.selectedDate = todayISO(); navigate("day"); };
    $("#day-date").onchange = (e) => { state.selectedDate = e.target.value; navigate("day"); };

    $("#day-save").onclick = async () => {
      const values = {};
      $all("[data-metric]", fields).forEach(input => {
        const key = input.dataset.metric;
        const raw = input.value.trim();
        values[key] = raw === "" ? null : (input.type === "number" ? Number(raw) : raw);
      });
      const payload = {
        note: $("#day-note").value,
        timezone: $("#day-tz").value,
        values,
      };
      try {
        await api(`/api/entries/${state.selectedDate}`, { method: "PUT", body: payload });
        toast("已保存 " + state.selectedDate, "ok");
        navigate("day");
      } catch (e) {
        toast(e.message, "error");
      }
    };

    const del = $("#day-delete");
    if (del) {
      del.onclick = async () => {
        if (!confirm(`确定删除 ${state.selectedDate} 的记录？此操作不可自动恢复（可先导出备份）。`)) return;
        try {
          await api(`/api/entries/${state.selectedDate}`, { method: "DELETE" });
          toast("已删除", "ok");
          navigate("day");
        } catch (e) {
          toast(e.message, "error");
        }
      };
    }
  }

  // ---------- calendar ----------
  async function renderCalendar(root) {
    const now = new Date();
    let year = state.calYear || now.getFullYear();
    let month = state.calMonth || now.getMonth() + 1;
    const cal = await api(`/api/calendar?year=${year}&month=${month}`);
    state.calendar = cal.calendar;

    root.innerHTML = `
      <div class="card">
        <div class="row between">
          <div>
            <h3>${year} 年 ${month} 月</h3>
            <div class="sub">有记录 ${cal.calendar.entry_count} 天 · 待补 ${cal.calendar.missing_dates.length} 天</div>
          </div>
          <div class="row">
            <button class="btn small" id="cal-prev">上一月</button>
            <button class="btn small" id="cal-now">本月</button>
            <button class="btn small" id="cal-next">下一月</button>
          </div>
        </div>
        <div class="calendar" id="cal-grid"></div>
        <div class="row" style="margin-top:14px">
          <button class="btn small" id="cal-fill">去补今天</button>
          <button class="btn small" id="cal-missing-only">查看待补列表</button>
        </div>
      </div>
      <div class="card" id="cal-detail"></div>
    `;
    renderCalendarGrid($("#cal-grid"), cal.calendar);

    $("#cal-prev").onclick = () => {
      month -= 1; if (month < 1) { month = 12; year -= 1; }
      state.calYear = year; state.calMonth = month; navigate("calendar");
    };
    $("#cal-next").onclick = () => {
      month += 1; if (month > 12) { month = 1; year += 1; }
      state.calYear = year; state.calMonth = month; navigate("calendar");
    };
    $("#cal-now").onclick = () => {
      state.calYear = now.getFullYear(); state.calMonth = now.getMonth() + 1; navigate("calendar");
    };
    $("#cal-fill").onclick = () => { state.selectedDate = todayISO(); navigate("day"); };
    $("#cal-missing-only").onclick = () => {
      const list = cal.calendar.missing_dates || [];
      $("#cal-detail").innerHTML = `
        <h3>待补日期</h3>
        <div class="missing-list">${list.length ? list.map(d => `<button class="chip" data-date="${d}">${d}</button>`).join("") : "<div class='ok-box'>本月记录齐全</div>"}</div>
      `;
      $all("#cal-detail .chip").forEach(b => b.onclick = () => { state.selectedDate = b.dataset.date; navigate("day"); });
    };

    $("#cal-detail").innerHTML = `
      <h3>本月说明</h3>
      <p class="muted">点击任意日期查看/编辑当天记录。斜纹背景表示尚无记录。</p>
    `;
  }

  // ---------- trends ----------
  async function renderTrends(root) {
    await loadMetrics();
    const numericMetrics = state.metrics.filter(m => m.value_type !== "text");
    root.innerHTML = `
      <div class="card">
        <h3>趋势设置</h3>
        <div class="sub">可叠加多个指标；缺失日期画成空隙，不当成 0。</div>
        <div class="row">
          <div class="field" style="min-width:160px">
            <label>开始日期</label>
            <input type="date" id="tr-start" value="${state.trendStart}" />
          </div>
          <div class="field" style="min-width:160px">
            <label>结束日期</label>
            <input type="date" id="tr-end" value="${state.trendEnd}" />
          </div>
          <div class="field" style="min-width:140px">
            <label>粒度</label>
            <select id="tr-gran">
              <option value="day" ${state.trendGranularity === "day" ? "selected" : ""}>按日</option>
              <option value="week" ${state.trendGranularity === "week" ? "selected" : ""}>按周</option>
              <option value="month" ${state.trendGranularity === "month" ? "selected" : ""}>按月</option>
            </select>
          </div>
          <button class="btn primary" id="tr-apply">应用</button>
          <button class="btn" id="tr-7">近 7 天</button>
          <button class="btn" id="tr-30">近 30 天</button>
          <button class="btn" id="tr-90">近 90 天</button>
        </div>
        <div class="tabs" id="tr-keys" style="margin-top:8px">
          ${numericMetrics.map(m => `
            <button class="tab ${state.trendKeys.includes(m.key) ? "active" : ""}" data-key="${m.key}">
              ${esc(m.name)}${m.unit ? `（${esc(m.unit)}）` : ""}
            </button>`).join("")}
        </div>
      </div>

      <div class="card">
        <h3>叠加趋势</h3>
        <div class="chart-box" id="tr-chart"></div>
        <div class="legend" id="tr-legend"></div>
        <div class="warn-box" style="margin-top:12px">
          不同指标量纲不同，叠加时主要看走势而非绝对高度。完整数值可在下方表格查看。
        </div>
      </div>

      <div class="card">
        <h3>指定日期放大</h3>
        <div class="row">
          <div class="field" style="min-width:160px">
            <label>选择日期</label>
            <input type="date" id="tr-zoom-date" value="${state.trendEnd}" />
          </div>
          <button class="btn" id="tr-zoom">查看该日</button>
        </div>
        <div id="tr-zoom-out"></div>
      </div>

      <div class="grid cols-2">
        <div class="card">
          <h3>相关性分析（描述性）</h3>
          <div class="row">
            <div class="field" style="flex:1">
              <label>指标 A</label>
              <select id="corr-a">${numericMetrics.map(m => `<option value="${m.key}" ${m.key === "weight" ? "selected" : ""}>${esc(m.name)}</option>`).join("")}</select>
            </div>
            <div class="field" style="flex:1">
              <label>指标 B</label>
              <select id="corr-b">${numericMetrics.map(m => `<option value="${m.key}" ${m.key === "sleep_hours" ? "selected" : ""}>${esc(m.name)}</option>`).join("")}</select>
            </div>
            <button class="btn primary" id="corr-run">计算</button>
          </div>
          <div id="corr-out"></div>
        </div>
        <div class="card">
          <h3>备注搜索</h3>
          <div class="row">
            <div class="field" style="flex:1">
              <label>关键词</label>
              <input type="text" id="note-q" placeholder="搜索备注…" />
            </div>
            <button class="btn" id="note-run">搜索</button>
          </div>
          <div id="note-out"></div>
        </div>
      </div>

      <div class="card">
        <h3>数据明细</h3>
        <div style="overflow:auto" id="tr-table"></div>
      </div>
    `;

    const selected = state.trendKeys.filter(k => numericMetrics.some(m => m.key === k));
    if (!selected.length) selected.push(numericMetrics[0]?.key);

    async function applyTrends() {
      state.trendStart = $("#tr-start").value;
      state.trendEnd = $("#tr-end").value;
      state.trendGranularity = $("#tr-gran").value;
      const keys = $all("#tr-keys .tab.active").map(b => b.dataset.key);
      state.trendKeys = keys.length ? keys : selected;
      const data = await api(`/api/trends?metrics=${state.trendKeys.join(",")}&start=${state.trendStart}&end=${state.trendEnd}&granularity=${state.trendGranularity}`);
      const colors = TimelineCharts.COLORS;
      const series = state.trendKeys.map((k, i) => ({
        name: data.metrics[k]?.name || k,
        color: colors[i % colors.length],
        points: (data.series[k] || []).map(p => ({ date: p.date, value: p.value })),
      }));
      TimelineCharts.lineChart($("#tr-chart"), {
        series,
        height: 320,
        title: `${state.trendStart} ~ ${state.trendEnd} · ${state.trendGranularity}`
      });
      $("#tr-legend").innerHTML = series.map(s =>
        `<span class="legend-item"><span class="legend-swatch" style="background:${s.color}"></span>${esc(s.name)}</span>`
      ).join("");

      // table
      const dates = series[0] ? series[0].points.map(p => p.date) : [];
      let html = `<table class="table"><thead><tr><th>日期</th>${state.trendKeys.map(k => `<th>${esc(data.metrics[k]?.name || k)}</th>`).join("")}</tr></thead><tbody>`;
      dates.forEach((d, i) => {
        html += `<tr><td>${d}</td>${state.trendKeys.map((k, si) => {
          const p = series[si].points[i];
          return `<td>${p && p.value != null ? esc(p.value) : `<span class="tag warn">缺失</span>`}</td>`;
        }).join("")}</tr>`;
      });
      html += `</tbody></table>`;
      $("#tr-table").innerHTML = html;
    }

    $all("#tr-keys .tab").forEach(tab => {
      tab.onclick = () => {
        tab.classList.toggle("active");
      };
    });
    $("#tr-apply").onclick = applyTrends;
    $("#tr-7").onclick = () => {
      $("#tr-start").value = addDays(todayISO(), -6);
      $("#tr-end").value = todayISO();
      applyTrends();
    };
    $("#tr-30").onclick = () => {
      $("#tr-start").value = addDays(todayISO(), -29);
      $("#tr-end").value = todayISO();
      applyTrends();
    };
    $("#tr-90").onclick = () => {
      $("#tr-start").value = addDays(todayISO(), -89);
      $("#tr-end").value = todayISO();
      applyTrends();
    };

    $("#tr-zoom").onclick = async () => {
      const d = $("#tr-zoom-date").value;
      const entryRes = await api(`/api/entries/${d}`);
      const e = entryRes.entry;
      if (!e) {
        $("#tr-zoom-out").innerHTML = `<div class="warn-box">${d} 尚无记录。</div>`;
        return;
      }
      const rows = Object.entries(e.values || {}).map(([k, v]) =>
        `<tr><td>${esc(v.name)}</td><td>${v.value == null ? "—" : esc(v.value)} ${esc(v.unit || "")}</td></tr>`
      ).join("");
      $("#tr-zoom-out").innerHTML = `
        <div class="ok-box">${d}</div>
        <table class="table"><tbody>${rows || "<tr><td class='muted'>无指标值</td></tr>"}</tbody></table>
        <p class="muted small">备注：${esc(e.note || "（无）")}</p>
        <button class="btn small" id="zoom-edit">编辑这一天</button>
      `;
      $("#zoom-edit").onclick = () => { state.selectedDate = d; navigate("day"); };
    };

    $("#corr-run").onclick = async () => {
      const a = $("#corr-a").value, b = $("#corr-b").value;
      const r = await api(`/api/correlation?a=${a}&b=${b}&start=${state.trendStart}&end=${state.trendEnd}`);
      $("#corr-out").innerHTML = `
        <div class="ok-box">
          <div><b>相关系数 r：</b>${r.r == null ? "—" : r.r}</div>
          <div><b>有效样本数 n：</b>${r.n}</div>
          ${r.message ? `<div>${esc(r.message)}</div>` : ""}
          <div class="small" style="margin-top:8px">${esc(r.disclaimer)}</div>
        </div>
      `;
    };

    $("#note-run").onclick = async () => {
      const q = $("#note-q").value;
      const notes = await api(`/api/notes?q=${encodeURIComponent(q)}&start=${state.trendStart}&end=${state.trendEnd}`);
      $("#note-out").innerHTML = notes.notes.length
        ? `<table class="table"><thead><tr><th>日期</th><th>备注</th></tr></thead><tbody>
            ${notes.notes.map(n => `<tr><td>${n.date}</td><td>${esc(n.note)}</td></tr>`).join("")}
          </tbody></table>`
        : `<div class="empty">没有匹配备注</div>`;
    };

    await applyTrends();
  }

  // ---------- report ----------
  async function renderReport(root) {
    const y = state.reportYear, m = state.reportMonth;
    const data = await api(`/api/report/${y}/${m}`);
    state.report = data.report;
    const report = data.report;

    root.innerHTML = `
      <div class="card">
        <div class="row between">
          <div>
            <h3>${y} 年 ${m} 月 月报</h3>
            <div class="sub">${report.start} ~ ${report.end} · 有记录 ${report.entry_count} 天 · 缺失 ${report.missing_dates.length} 天</div>
          </div>
          <div class="row">
            <button class="btn small" id="rp-prev">上一月</button>
            <button class="btn small" id="rp-next">下一月</button>
            <button class="btn small" id="rp-print">打印 / PDF</button>
            <button class="btn small primary" id="rp-md">导出 Markdown</button>
          </div>
        </div>
        <div class="warn-box">
          <b>统计口径：</b>仅对有数值的日期计算均值/中位数/最值；缺失日期不计入分母，也绝不按 0 处理。
          ${esc(report.timezone_note || "")}
        </div>
      </div>

      <div class="card">
        <h3>统计摘要</h3>
        <div style="overflow:auto">
          <table class="table">
            <thead>
              <tr>
                <th>指标</th><th>单位</th><th>样本数</th><th>缺失天数</th>
                <th>均值</th><th>中位数</th><th>最小</th><th>最大</th>
              </tr>
            </thead>
            <tbody>
              ${report.stats.map(s => `
                <tr>
                  <td>${esc(s.name)}</td>
                  <td>${esc(s.unit || "-")}</td>
                  <td>${s.n}</td>
                  <td>${s.missing_days}</td>
                  <td>${s.mean ?? "—"}</td>
                  <td>${s.median ?? "—"}</td>
                  <td>${s.min ?? "—"}</td>
                  <td>${s.max ?? "—"}</td>
                </tr>`).join("")}
            </tbody>
          </table>
        </div>
      </div>

      <div class="grid cols-2">
        <div class="card">
          <h3>体重走势</h3>
          <div class="chart-box" id="rp-weight"></div>
        </div>
        <div class="card">
          <h3>睡眠时长</h3>
          <div class="chart-box" id="rp-sleep"></div>
        </div>
      </div>

      <div class="card">
        <h3>本月备注</h3>
        ${report.notes.length ? `
          <table class="table"><thead><tr><th>日期</th><th>备注</th></tr></thead>
          <tbody>${report.notes.map(n => `<tr><td>${n.date}</td><td>${esc(n.note)}</td></tr>`).join("")}</tbody></table>
        ` : `<div class="empty">本月没有文字备注</div>`}
      </div>

      <div class="card">
        <h3>可编辑总结</h3>
        <div class="field">
          <label>写给自己的月度总结</label>
          <textarea id="rp-summary" rows="6">${esc(report.summary_text || "")}</textarea>
        </div>
        <button class="btn primary" id="rp-save">保存总结</button>
        <span class="muted small">保存后导出 Markdown / 打印页会包含这段文字。</span>
      </div>
    `;

    // charts
    const wKey = "weight", sKey = "sleep_hours";
    const seriesMap = report.series || {};
    TimelineCharts.lineChart($("#rp-weight"), {
      series: [{
        name: "体重",
        color: "#1f7a68",
        points: (seriesMap[wKey] || []).map(p => ({ date: p.date, value: p.value }))
      }],
      height: 240,
      unitLabel: "kg"
    });
    TimelineCharts.barChart($("#rp-sleep"), {
      labels: (seriesMap[sKey] || []).map(p => p.date.slice(5)),
      values: (seriesMap[sKey] || []).map(p => p.value),
      height: 240,
      unitLabel: "h",
      color: "#3d5a80"
    });

    $("#rp-prev").onclick = () => {
      let mm = m - 1, yy = y;
      if (mm < 1) { mm = 12; yy -= 1; }
      state.reportYear = yy; state.reportMonth = mm; navigate("report");
    };
    $("#rp-next").onclick = () => {
      let mm = m + 1, yy = y;
      if (mm > 12) { mm = 1; yy += 1; }
      state.reportYear = yy; state.reportMonth = mm; navigate("report");
    };
    $("#rp-print").onclick = () => window.print();
    $("#rp-md").onclick = () => {
      window.open(`/api/export/markdown?year=${y}&month=${m}`, "_blank");
    };
    $("#rp-save").onclick = async () => {
      try {
        await api(`/api/report/${y}/${m}`, {
          method: "PUT",
          body: { summary_text: $("#rp-summary").value }
        });
        toast("总结已保存", "ok");
      } catch (e) {
        toast(e.message, "error");
      }
    };
  }

  // ---------- import / export ----------
  async function renderImport(root) {
    root.innerHTML = `
      <div class="card">
        <h3>导入 CSV</h3>
        <div class="sub">支持：日常记录 CSV、项目 01 打鼾 CSV。导入前会预览并要求确认；不会静默覆盖已有数据。</div>
        <div class="row">
          <input type="file" id="imp-file" accept=".csv,text/csv" />
          <button class="btn" id="imp-sample">载入问题样例（重复/空值/跨月）</button>
          <button class="btn" id="imp-snore-sample">载入打鼾样例</button>
        </div>
        <div class="field" style="margin-top:12px">
          <label>或直接粘贴 CSV 文本</label>
          <textarea id="imp-text" rows="6" placeholder="date,weight,sleep_hours,mood,note"></textarea>
        </div>
        <div class="row">
          <button class="btn" id="imp-snore-map">自动映射（项目01打鼾）</button>
          <button class="btn primary" id="imp-next">下一步：字段映射</button>
        </div>
        <div id="imp-msg"></div>
      </div>

      <div class="card" id="imp-mapping-card" style="display:none">
        <h3>字段映射与导入选项</h3>
        <div id="imp-mapping"></div>
      </div>

      <div class="card" id="imp-preview-card" style="display:none">
        <h3>预览</h3>
        <div id="imp-preview"></div>
      </div>

      <div class="card">
        <h3>导出与备份</h3>
        <div class="sub">全部导出仅在本机生成，可直接下载。</div>
        <div class="row">
          <button class="btn primary" id="exp-csv">导出完整 CSV</button>
          <button class="btn" id="exp-json">导出 JSON 备份</button>
          <label class="btn">
            从 JSON 备份恢复
            <input type="file" id="restore-file" accept="application/json,.json" style="display:none" />
          </label>
          <button class="btn" id="exp-report-md">导出本月 Markdown</button>
        </div>
        <div class="warn-box" style="margin-top:12px">
          <b>恢复说明：</b>默认「replace」会用备份内容替换当前全部日记录（先确认）。也可选择「merge」按日期合并覆盖。
        </div>
        <div id="restore-msg"></div>
      </div>

      <div class="card">
        <h3>样例模板</h3>
        <div class="row">
          <a class="btn small" href="/samples/daily_template.csv" download>每日记录模板</a>
          <a class="btn small" href="/samples/week_sample.csv" download>一周样例</a>
          <a class="btn small" href="/samples/snore_project01_sample.csv" download>项目01打鼾样例</a>
          <a class="btn small" href="/samples/messy_import_sample.csv" download>问题样例（测试导入）</a>
        </div>
        <p class="muted small" style="margin-top:10px">
          样例是测试用虚构数据，不代表真实个人分析结论。
        </p>
      </div>
    `;

    const textArea = $("#imp-text");
    $("#imp-file").onchange = async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      textArea.value = await file.text();
      toast("已读取文件：" + file.name, "ok");
    };

    $("#imp-sample").onclick = async () => {
      textArea.value = await fetch("/samples/messy_import_sample.csv").then(r => r.text());
      toast("已载入问题样例", "ok");
    };
    $("#imp-snore-sample").onclick = async () => {
      textArea.value = await fetch("/samples/snore_project01_sample.csv").then(r => r.text());
      toast("已载入打鼾样例", "ok");
    };

    function showMapping(csvText, prefill) {
      const headers = (() => {
        const first = csvText.trim().split(/\r?\n/)[0] || "";
        return first.split(",").map(s => s.replace(/^"|"$/g, "").trim());
      })();

      const metrics = state.metrics;
      const mapping = prefill || {
        date: headers.includes("date") ? "date" : headers[0],
        note: headers.includes("note") ? "note" : "",
        fields: {},
        units: {}
      };
      // default field guesses
      if (!prefill) {
        headers.forEach(h => {
          const lower = h.toLowerCase();
          if (lower === "date" || lower === "note") return;
          const hit = metrics.find(m => m.key === lower);
          if (hit) mapping.fields[h] = hit.key;
        });
      }

      $("#imp-mapping-card").style.display = "block";
      $("#imp-mapping").innerHTML = `
        <div class="field-row cols-3">
          <div class="field">
            <label>日期列</label>
            <select id="map-date">
              ${headers.map(h => `<option value="${esc(h)}" ${mapping.date === h ? "selected" : ""}>${esc(h)}</option>`).join("")}
            </select>
          </div>
          <div class="field">
            <label>日期格式</label>
            <select id="map-datefmt">
              <option value="%Y-%m-%d">YYYY-MM-DD</option>
              <option value="%Y/%m/%d">YYYY/MM/DD</option>
              <option value="%d/%m/%Y">DD/MM/YYYY</option>
              <option value="%m/%d/%Y">MM/DD/YYYY</option>
              <option value="%Y%m%d">YYYYMMDD</option>
            </select>
          </div>
          <div class="field">
            <label>备注列（可选）</label>
            <select id="map-note">
              <option value="">（不导入备注）</option>
              ${headers.map(h => `<option value="${esc(h)}" ${mapping.note === h ? "selected" : ""}>${esc(h)}</option>`).join("")}
            </select>
          </div>
        </div>

        <h3 style="margin-top:8px">列 → 指标</h3>
        <table class="table">
          <thead><tr><th>CSV 列</th><th>映射到指标</th><th>原单位（若需换算）</th></tr></thead>
          <tbody>
            ${headers.filter(h => h !== mapping.date && h !== mapping.note).map(h => `
              <tr>
                <td>${esc(h)}</td>
                <td>
                  <select class="map-field" data-col="${esc(h)}">
                    <option value="">（忽略）</option>
                    ${metrics.map(m => `<option value="${m.key}" ${mapping.fields[h] === m.key ? "selected" : ""}>${esc(m.name)} [${m.key}]</option>`).join("")}
                  </select>
                </td>
                <td>
                  <select class="map-unit" data-col="${esc(h)}">
                    <option value="">默认</option>
                    <option value="kg" ${mapping.units[h] === "kg" ? "selected" : ""}>kg</option>
                    <option value="lb" ${mapping.units[h] === "lb" ? "selected" : ""}>lb</option>
                    <option value="斤" ${mapping.units[h] === "斤" ? "selected" : ""}>斤</option>
                    <option value="小时" ${mapping.units[h] === "小时" ? "selected" : ""}>小时</option>
                    <option value="分钟" ${mapping.units[h] === "分钟" ? "selected" : ""}>分钟</option>
                  </select>
                </td>
              </tr>`).join("")}
          </tbody>
        </table>

        <div class="field-row cols-3" style="margin-top:12px">
          <div class="field">
            <label>重复日期策略</label>
            <select id="map-dup">
              <option value="skip">跳过（保留已有）</option>
              <option value="overwrite">覆盖已有字段</option>
              <option value="merge_empty">仅补空值</option>
              <option value="fail">发现重复则取消导入</option>
            </select>
            <div class="hint">默认跳过，绝不静默覆盖</div>
          </div>
          <div class="field">
            <label>时区备注（可选）</label>
            <input type="text" id="map-tz" placeholder="Asia/Shanghai" value="${esc(Intl.DateTimeFormat().resolvedOptions().timeZone)}" />
          </div>
          <div class="field">
            <label>操作</label>
            <button class="btn primary" id="map-preview">预览导入</button>
          </div>
        </div>
      `;

      $("#map-preview").onclick = async () => {
        const fields = {};
        const units = {};
        $all(".map-field").forEach(sel => {
          if (sel.value) fields[sel.dataset.col] = sel.value;
        });
        $all(".map-unit").forEach(sel => {
          if (sel.value) units[sel.dataset.col] = sel.value;
        });
        const mapping2 = {
          date: $("#map-date").value,
          note: $("#map-note").value,
          fields,
          units
        };
        // convert unit keys to metric keys for server
        const unitByMetric = {};
        Object.entries(fields).forEach(([col, metricKey]) => {
          const u = $all(".map-unit").find(s => s.dataset.col === col);
          if (u && u.value) unitByMetric[metricKey] = u.value;
        });
        const options = {
          date_format: $("#map-datefmt").value,
          duplicate_strategy: $("#map-dup").value,
          timezone: $("#map-tz").value
        };
        try {
          const preview = await api("/api/import/preview", {
            method: "POST",
            body: {
              csv_text: csvText,
              mapping: { ...mapping2, units: unitByMetric },
              options
            }
          });
          state.importPreview = preview;
          state.importMapping = mapping2;
          state.importUnits = unitByMetric;
          state.importOptions = options;
          renderPreview(preview);
        } catch (e) {
          $("#imp-preview-card").style.display = "block";
          $("#imp-preview").innerHTML = `<div class="error-box">${esc(e.message)}</div>`;
        }
      };
    }

    function renderPreview(preview) {
      $("#imp-preview-card").style.display = "block";
      const issues = preview.issues || [];
      const conflicts = preview.conflicts || [];
      $("#imp-preview").innerHTML = `
        <div class="${issues.length ? "warn-box" : "ok-box"}">
          共 ${preview.row_count} 行，计划解析 ${preview.planned_count} 行，实际将写入约 ${preview.apply_count} 条。
          冲突/重复 ${preview.conflict_count} 处。日期格式：<code>${esc(preview.date_format)}</code>
        </div>
        ${issues.length ? `
          <h3 style="margin-top:12px">问题行（${issues.length}）</h3>
          <table class="table"><thead><tr><th>行</th><th>类型</th><th>说明</th></tr></thead>
          <tbody>${issues.slice(0, 20).map(i => `<tr><td>${i.row}</td><td><span class="tag warn">${esc(i.type)}</span></td><td>${esc(i.message)}</td></tr>`).join("")}</tbody></table>
        ` : ""}
        <h3 style="margin-top:12px">预览（前 20 行）</h3>
        <div style="overflow:auto">
          <table class="table">
            <thead><tr><th>行</th><th>日期</th><th>值</th><th>备注</th><th>文件内重复</th></tr></thead>
            <tbody>
              ${(preview.preview || []).map(p => `
                <tr>
                  <td>${p.row}</td>
                  <td>${p.date}</td>
                  <td><code class="small">${esc(JSON.stringify(p.values))}</code></td>
                  <td>${esc(p.note || "")}</td>
                  <td>${p.duplicate_index > 1 ? `<span class="tag danger">是</span>` : `<span class="tag ok">否</span>`}</td>
                </tr>`).join("")}
            </tbody>
          </table>
        </div>
        ${conflicts.length ? `
          <div class="warn-box" style="margin-top:12px">
            检测到 ${conflicts.length} 处与已有数据或文件内重复。当前策略：<b>${esc(state.importOptions?.duplicate_strategy || "skip")}</b>。
            请确认后再提交。不会在未确认时覆盖。
          </div>
        ` : ""}
        <div class="row" style="margin-top:12px">
          <button class="btn primary" id="imp-commit">确认导入</button>
          <button class="btn" id="imp-back">返回修改映射</button>
        </div>
        <div id="imp-commit-msg"></div>
      `;
      $("#imp-back").onclick = () => {
        if (state.importCsvText) showMapping(state.importCsvText, {
          date: state.importMapping.date,
          note: state.importMapping.note,
          fields: Object.fromEntries(Object.entries(state.importMapping.fields).map(([c, m]) => [c, m])),
          units: {}
        });
      };
      $("#imp-commit").onclick = async () => {
        const btn = $("#imp-commit");
        btn.disabled = true;
        btn.textContent = "导入中…";
        try {
          const result = await api("/api/import/commit", {
            method: "POST",
            body: {
              csv_text: state.importCsvText,
              mapping: {
                date: state.importMapping.date,
                note: state.importMapping.note,
                fields: state.importMapping.fields,
                units: state.importUnits || {}
              },
              options: state.importOptions
            }
          });
          $("#imp-commit-msg").innerHTML = `
            <div class="ok-box" style="margin-top:12px">
              ${esc(result.message || "导入完成")}<br/>
              写入 ${result.applied}，跳过 ${result.skipped}，合并 ${result.merged}，覆盖 ${result.overwritten}，错误 ${ (result.errors||[]).length }。
            </div>
          `;
          toast("导入完成", "ok");
        } catch (e) {
          $("#imp-commit-msg").innerHTML = `<div class="error-box" style="margin-top:12px">${esc(e.message)}</div>`;
          toast("导入失败：" + e.message, "error");
        } finally {
          btn.disabled = false;
          btn.textContent = "确认导入";
        }
      };
    }

    $("#imp-next").onclick = async () => {
      const csvText = textArea.value.trim();
      if (!csvText) { toast("请先选择或粘贴 CSV", "error"); return; }
      state.importCsvText = csvText;
      await loadMetrics();
      showMapping(csvText, null);
    };

    $("#imp-snore-map").onclick = async () => {
      const csvText = textArea.value.trim();
      if (!csvText) { toast("请先选择或粘贴 CSV", "error"); return; }
      state.importCsvText = csvText;
      await loadMetrics();
      try {
        const mapped = await api("/api/import/snore-map", { method: "POST", body: { csv_text: csvText } });
        if (!mapped.ok && mapped.error) {
          $("#imp-msg").innerHTML = `<div class="error-box" style="margin-top:12px">${esc(mapped.error)}<br/>${(mapped.steps||[]).map(esc).join("<br/>")}</div>`;
          showMapping(csvText, null);
          return;
        }
        showMapping(csvText, {
          date: mapped.mapping.date,
          note: mapped.mapping.note,
          fields: mapped.mapping.fields || {},
          units: {}
        });
        $("#imp-msg").innerHTML = `<div class="ok-box" style="margin-top:12px">${esc(mapped.message || "已自动映射")}</div>`;
      } catch (e) {
        $("#imp-msg").innerHTML = `<div class="error-box">${esc(e.message)}</div>`;
        showMapping(csvText, null);
      }
    };

    $("#exp-csv").onclick = () => { window.open("/api/export/csv", "_blank"); };
    $("#exp-json").onclick = () => { window.open("/api/export/json", "_blank"); };
    $("#exp-report-md").onclick = () => {
      const now = new Date();
      window.open(`/api/export/markdown?year=${now.getFullYear()}&month=${now.getMonth() + 1}`, "_blank");
    };

    $("#restore-file").onchange = async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      const text = await file.text();
      let backup;
      try { backup = JSON.parse(text); }
      catch { toast("JSON 解析失败", "error"); return; }
      const m = modal(`
        <h3>从备份恢复</h3>
        <p class="muted">文件：${esc(file.name)} · 导出时间：${esc(backup.exported_at || "未知")}</p>
        <div class="field">
          <label>恢复模式</label>
          <select id="restore-mode">
            <option value="replace">replace：替换全部日记录</option>
            <option value="merge">merge：按日期合并覆盖</option>
          </select>
        </div>
        <div class="warn-box">「replace」会删除当前所有日记录后写入备份内容。建议先导出当前 JSON 备份。</div>
        <div class="row" style="justify-content:flex-end; margin-top:12px">
          <button class="btn" id="restore-cancel">取消</button>
          <button class="btn danger" id="restore-go">确认恢复</button>
        </div>
      `);
      m.querySelector("#restore-cancel").onclick = closeModal;
      m.querySelector("#restore-go").onclick = async () => {
        const mode = m.querySelector("#restore-mode").value;
        try {
          const result = await api("/api/restore", {
            method: "POST",
            body: { backup, mode }
          });
          closeModal();
          $("#restore-msg").innerHTML = `<div class="ok-box" style="margin-top:12px">恢复完成：${result.restored_entries} 条记录，模式 ${result.mode}</div>`;
          toast("恢复完成", "ok");
          await loadMetrics();
        } catch (err) {
          toast(err.message, "error");
        }
      };
      e.target.value = "";
    };
  }

  // ---------- metrics ----------
  async function renderMetrics(root) {
    await loadMetrics();
    root.innerHTML = `
      <div class="card">
        <h3>自定义指标</h3>
        <div class="sub">内置指标不可删除但可停用。自定义指标会出现在日记录与导入映射中。</div>
        <div class="row">
          <button class="btn primary" id="metric-new">新建指标</button>
        </div>
      </div>
      <div class="card">
        <div class="metric-grid" id="metric-grid"></div>
      </div>
    `;

    const grid = $("#metric-grid");
    grid.innerHTML = state.metrics.map(m => `
      <div class="metric-card">
        <div class="name">${esc(m.name)} ${m.is_builtin ? `<span class="tag">内置</span>` : `<span class="tag ok">自定义</span>`}</div>
        <div class="muted small">key: ${esc(m.key)}</div>
        <div class="small">单位：${esc(m.unit || "无")} · 类型：${esc(m.value_type)}</div>
        ${m.min_value != null ? `<div class="small muted">范围 ${m.min_value}–${m.max_value}</div>` : ""}
        <div class="row" style="margin-top:8px">
          <button class="btn small" data-edit="${m.id}">编辑</button>
          ${m.is_builtin ? "" : `<button class="btn small danger" data-del="${m.id}">删除</button>`}
        </div>
      </div>
    `).join("");

    $all("[data-edit]", grid).forEach(btn => {
      btn.onclick = () => {
        const m = state.metrics.find(x => x.id == btn.dataset.edit);
        openMetricEditor(m);
      };
    });
    $all("[data-del]", grid).forEach(btn => {
      btn.onclick = async () => {
        if (!confirm("删除该自定义指标？相关历史值也会删除。")) return;
        try {
          await api(`/api/metrics/${btn.dataset.del}`, { method: "DELETE" });
          toast("已删除", "ok");
          navigate("metrics");
        } catch (e) {
          toast(e.message, "error");
        }
      };
    });

    $("#metric-new").onclick = () => openMetricEditor(null);

    function openMetricEditor(m) {
      const el = modal(`
        <h3>${m ? "编辑指标" : "新建指标"}</h3>
        <div class="field">
          <label>名称</label>
          <input id="m-name" value="${esc(m?.name || "")}" placeholder="例如 静息心率" />
        </div>
        <div class="field-row">
          <div class="field">
            <label>key（英文标识）</label>
            <input id="m-key" value="${esc(m?.key || "")}" placeholder="resting_hr" ${m ? "disabled" : ""} />
          </div>
          <div class="field">
            <label>单位</label>
            <input id="m-unit" value="${esc(m?.unit || "")}" placeholder="bpm / 小时 / 分…" />
          </div>
        </div>
        <div class="field-row">
          <div class="field">
            <label>类型</label>
            <select id="m-type">
              <option value="number" ${m?.value_type === "number" ? "selected" : ""}>数字</option>
              <option value="scale" ${m?.value_type === "scale" ? "selected" : ""}>量表（1-5 等）</option>
              <option value="text" ${m?.value_type === "text" ? "selected" : ""}>文本</option>
            </select>
          </div>
          <div class="field">
            <label>小数位</label>
            <input id="m-dec" type="number" value="${m?.decimals ?? 1}" />
          </div>
        </div>
        <div class="field-row">
          <div class="field">
            <label>最小值（可选）</label>
            <input id="m-min" type="number" step="any" value="${m?.min_value ?? ""}" />
          </div>
          <div class="field">
            <label>最大值（可选）</label>
            <input id="m-max" type="number" step="any" value="${m?.max_value ?? ""}" />
          </div>
        </div>
        <div class="row" style="justify-content:flex-end">
          <button class="btn" id="m-cancel">取消</button>
          <button class="btn primary" id="m-save">保存</button>
        </div>
      `);
      el.querySelector("#m-cancel").onclick = closeModal;
      el.querySelector("#m-save").onclick = async () => {
        const payload = {
          name: el.querySelector("#m-name").value.trim(),
          key: el.querySelector("#m-key").value.trim(),
          unit: el.querySelector("#m-unit").value.trim(),
          value_type: el.querySelector("#m-type").value,
          decimals: Number(el.querySelector("#m-dec").value || 2),
          min_value: el.querySelector("#m-min").value === "" ? null : Number(el.querySelector("#m-min").value),
          max_value: el.querySelector("#m-max").value === "" ? null : Number(el.querySelector("#m-max").value),
        };
        try {
          if (m) await api(`/api/metrics/${m.id}`, { method: "PUT", body: payload });
          else await api("/api/metrics", { method: "POST", body: payload });
          closeModal();
          toast("指标已保存", "ok");
          navigate("metrics");
        } catch (e) {
          toast(e.message, "error");
        }
      };
    }
  }

  // ---------- members ----------
  async function renderMembers(root) {
    const isAdmin = state.user && state.user.role === "admin";
    let users = [];
    try {
      const r = await api("/api/users");
      users = r.users || [];
    } catch (e) {
      root.innerHTML = `<div class="error-box">${esc(e.message)}</div>`;
      return;
    }
    root.innerHTML = `
      <div class="card">
        <h3>成员与数据隔离</h3>
        <div class="sub">
          每位成员拥有独立数据库目录 <code>data/users/&lt;id&gt;/timeline.db</code> 与独立备份。
          管理员可添加家人账号；成员只能看自己的数据。
        </div>
        ${isAdmin ? `
          <div class="row">
            <button class="btn primary" id="mem-new">添加成员</button>
            <button class="btn" id="mem-invite">生成邀请码</button>
            <button class="btn" id="mem-backup">一键导出全家备份</button>
            <label class="btn">
              恢复全家备份
              <input type="file" id="mem-restore" accept=".zip" style="display:none" />
            </label>
          </div>
        ` : `<div class="warn-box">只有管理员可以添加成员、发邀请码、导出全家备份。</div>`}
      </div>
      <div class="card">
        <table class="table">
          <thead><tr><th>用户名</th><th>显示名</th><th>角色</th><th>状态</th><th>创建</th><th></th></tr></thead>
          <tbody>
            ${users.map(u => `
              <tr>
                <td><code>${esc(u.username)}</code>${u.id === (state.user||{}).id ? ' <span class="tag">我</span>' : ''}</td>
                <td>${esc(u.display_name || "")}</td>
                <td>${u.role === "admin" ? '<span class="tag">管理员</span>' : '<span class="tag ok">成员</span>'}</td>
                <td>${esc(u.status)}</td>
                <td class="small muted">${esc((u.created_at||"").slice(0,10))}</td>
                <td>${isAdmin && u.id !== (state.user||{}).id ? `<button class="btn small danger" data-del="${u.id}">删除</button>` : ""}</td>
              </tr>`).join("")}
          </tbody>
        </table>
      </div>
      ${isAdmin ? `
      <div class="card" id="invite-card">
        <h3>邀请码</h3>
        <div class="sub">把邀请码发给家人，对方在登录页「注册」填写即可自助开通（数据独立）。</div>
        <div id="invite-once"></div>
        <table class="table" style="margin-top:8px">
          <thead><tr><th>邀请码</th><th>角色</th><th>用途</th><th>使用</th><th>状态</th><th>过期</th><th></th></tr></thead>
          <tbody id="invite-rows"><tr><td colspan="7" class="muted">加载中…</td></tr></tbody>
        </table>
      </div>` : ""}
    `;
    if (isAdmin) {
      const btn = $("#mem-new");
      if (btn) btn.onclick = () => {
        const el = modal(`
          <h3>添加成员</h3>
          <div class="field"><label>用户名</label><input id="m-u" placeholder="如 mom / dad / kid" /></div>
          <div class="field"><label>显示名</label><input id="m-n" placeholder="如 妈妈" /></div>
          <div class="field"><label>初始密码（≥6）</label><input id="m-p" type="text" /></div>
          <div class="field"><label>角色</label>
            <select id="m-r"><option value="member">成员</option><option value="admin">管理员</option></select>
          </div>
          <div class="row" style="justify-content:flex-end">
            <button class="btn" id="m-cancel">取消</button>
            <button class="btn primary" id="m-save">创建</button>
          </div>
        `);
        el.querySelector("#m-cancel").onclick = closeModal;
        el.querySelector("#m-save").onclick = async () => {
          try {
            await api("/api/users", {
              method: "POST",
              body: {
                username: el.querySelector("#m-u").value,
                display_name: el.querySelector("#m-n").value,
                password: el.querySelector("#m-p").value,
                role: el.querySelector("#m-r").value,
              }
            });
            closeModal();
            toast("成员已创建", "ok");
            navigate("members");
          } catch (e) { toast(e.message, "error"); }
        };
      };
      $all("[data-del]", root).forEach(b => {
        b.onclick = async () => {
          if (!confirm("删除该成员及其数据目录？此操作不可恢复。")) return;
          try {
            await api("/api/users/" + b.dataset.del, { method: "DELETE" });
            toast("已删除", "ok");
            navigate("members");
          } catch (e) { toast(e.message, "error"); }
        };
      });

      // ---- family backup ----
      const bk = $("#mem-backup");
      if (bk) bk.onclick = () => {
        // cookie-auth download
        window.open("/api/admin/backup/family", "_blank");
        toast("已开始下载全家备份 zip", "ok");
      };
      const rs = $("#mem-restore");
      if (rs) rs.onchange = async (e) => {
        const file = e.target.files[0];
        if (!file) return;
        if (!confirm("恢复会覆盖用户数据库文件。建议先「一键导出全家备份」。继续？")) {
          e.target.value = "";
          return;
        }
        try {
          const buf = await file.arrayBuffer();
          let binary = "";
          const bytes = new Uint8Array(buf);
          const chunk = 0x8000;
          for (let i = 0; i < bytes.length; i += chunk) {
            binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
          }
          const b64 = btoa(binary);
          const r = await api("/api/admin/backup/family/restore", {
            method: "POST",
            body: { zip_base64: b64, mode: "merge" }
          });
          toast(r.message || "恢复完成", "ok");
          alert(r.message + "\n恢复用户: " + (r.restored_users || []).join(", "));
        } catch (err) {
          toast(err.message, "error");
        } finally {
          e.target.value = "";
        }
      };

      // ---- invites ----
      const loadInvites = async () => {
        try {
          const r = await api("/api/invites");
          const rows = r.invites || [];
          const tb = $("#invite-rows");
          if (!tb) return;
          if (!rows.length) {
            tb.innerHTML = `<tr><td colspan="7" class="muted">还没有邀请码</td></tr>`;
            return;
          }
          tb.innerHTML = rows.map(i => `
            <tr>
              <td><code>${esc(i.code)}</code></td>
              <td>${esc(i.role)}</td>
              <td>${esc(i.note || "—")}</td>
              <td>${i.used_count}/${i.max_uses}</td>
              <td><span class="tag ${i.status === 'active' ? 'ok' : 'warn'}">${esc(i.status)}</span></td>
              <td class="small muted">${esc((i.expires_at || "—").slice(0, 10))}</td>
              <td>${i.status === "active" ? `<button class="btn small danger" data-inv-revoke="${esc(i.code)}">作废</button>
                <button class="btn small" data-inv-copy="${esc(i.code)}">复制</button>` : ""}</td>
            </tr>`).join("");
          $all("[data-inv-copy]", tb).forEach(b => {
            b.onclick = async () => {
              try { await navigator.clipboard.writeText(b.dataset.invCopy); toast("邀请码已复制", "ok"); }
              catch { toast(b.dataset.invCopy); }
            };
          });
          $all("[data-inv-revoke]", tb).forEach(b => {
            b.onclick = async () => {
              if (!confirm("作废该邀请码？")) return;
              await api("/api/invites/" + b.dataset.invRevoke, { method: "DELETE" });
              toast("已作废", "ok");
              loadInvites();
            };
          });
        } catch (e) { /* ignore */ }
      };
      loadInvites();
      const invBtn = $("#mem-invite");
      if (invBtn) invBtn.onclick = () => {
        const el = modal(`
          <h3>生成邀请码</h3>
          <div class="field"><label>角色</label>
            <select id="inv-role"><option value="member">成员（默认）</option><option value="admin">管理员</option></select>
          </div>
          <div class="field"><label>备注</label><input id="inv-note" placeholder="给妈妈 / 测试" /></div>
          <div class="field-row">
            <div class="field"><label>可用次数</label><input id="inv-max" type="number" value="1" min="1" /></div>
            <div class="field"><label>有效天数</label><input id="inv-days" type="number" value="30" min="1" /></div>
          </div>
          <div class="row" style="justify-content:flex-end">
            <button class="btn" id="inv-cancel">取消</button>
            <button class="btn primary" id="inv-ok">生成</button>
          </div>
        `);
        el.querySelector("#inv-cancel").onclick = closeModal;
        el.querySelector("#inv-ok").onclick = async () => {
          try {
            const r = await api("/api/invites", {
              method: "POST",
              body: {
                role: el.querySelector("#inv-role").value,
                note: el.querySelector("#inv-note").value,
                max_uses: Number(el.querySelector("#inv-max").value || 1),
                expires_in_days: Number(el.querySelector("#inv-days").value || 30),
              }
            });
            closeModal();
            const inv = r.invite;
            $("#invite-once").innerHTML = `
              <div class="ok-box" style="margin:10px 0">
                新邀请码：<b><code>${esc(inv.code)}</code></b>
                （${esc(inv.role)}，可用 ${inv.max_uses} 次）
                <button class="btn small" id="inv-copy-new" style="margin-left:8px">复制</button>
              </div>`;
            $("#inv-copy-new").onclick = async () => {
              try { await navigator.clipboard.writeText(inv.code); toast("已复制", "ok"); } catch { toast(inv.code); }
            };
            loadInvites();
          } catch (e) { toast(e.message, "error"); }
        };
      };
    }
  }

  // ---------- account / keys / AI settings ----------
  async function renderSettings(root) {
    let keys = [], settings = {}, ai = {};
    try {
      const [k, s] = await Promise.all([
        api("/api/keys"),
        api("/api/settings"),
      ]);
      keys = k.keys || [];
      settings = s.settings || {};
      ai = s.ai || {};
    } catch (e) {
      root.innerHTML = `<div class="error-box">${esc(e.message)}</div>`;
      return;
    }

    root.innerHTML = `
      <div class="card">
        <h3>当前账号</h3>
        <div class="row">
          <div class="stat-item"><div class="k">用户名</div><div class="v" style="font-size:18px">${esc((state.user||{}).username||"")}</div></div>
          <div class="stat-item"><div class="k">显示名</div><div class="v" style="font-size:18px">${esc((state.user||{}).display_name||"")}</div></div>
          <div class="stat-item"><div class="k">角色</div><div class="v" style="font-size:18px">${esc((state.user||{}).role||"")}</div></div>
        </div>
        <div class="row" style="margin-top:12px">
          <button class="btn danger" id="logout">退出登录</button>
        </div>
      </div>

      <div class="card">
        <h3>API Key（给脚本 / Agent / MCP 用）</h3>
        <div class="sub">
          调用时加请求头：<code>Authorization: Bearer tk_...</code>。
          明文只在创建时显示一次。可在 MCP 服务中配置 <code>TIMELINE_API_KEY</code>。
        </div>
        <button class="btn primary" id="key-new">新建 API Key</button>
        <div id="key-once"></div>
        <table class="table" style="margin-top:12px">
          <thead><tr><th>名称</th><th>前缀</th><th>权限</th><th>创建</th><th>最近使用</th><th></th></tr></thead>
          <tbody>
            ${keys.map(k => `
              <tr>
                <td>${esc(k.name)}</td>
                <td><code>${esc(k.key_prefix)}…</code></td>
                <td><span class="tag">${esc(k.scopes)}</span>${k.revoked ? ' <span class="tag danger">已吊销</span>' : ''}</td>
                <td class="small muted">${esc((k.created_at||"").slice(0,19))}</td>
                <td class="small muted">${esc((k.last_used_at||"—").slice(0,19))}</td>
                <td>${!k.revoked ? `<button class="btn small danger" data-revoke="${k.id}">吊销</button>` : ""}</td>
              </tr>`).join("")}
          </tbody>
        </table>
      </div>

      <div class="card">
        <h3>AI 接入（真实模型）</h3>
        <div class="sub">
          配置任意 <b>OpenAI 兼容</b> 接口后，自然语言录入会走真模型；
          未配置或调用失败时自动回退离线规则解析。密钥仅存本机，按用户隔离。
        </div>
        <div class="ok-box">
          当前状态：${ai.configured
            ? `<b>已接入模型</b> · ${esc(ai.model)} @ ${esc(ai.api_base)}`
            : `未配置 API Key，正在使用离线规则解析（仍可用）。`}
        </div>
        <div class="field-row cols-3" style="margin-top:12px">
          <div class="field">
            <label>API Base</label>
            <input id="ai-base" placeholder="https://api.openai.com/v1" value="${esc(settings.ai_api_base || "")}" />
          </div>
          <div class="field">
            <label>API Key</label>
            <input id="ai-key" type="password" placeholder="sk-..." value="${esc(settings.ai_api_key || "")}" />
            <div class="hint">当前保存值：${esc(settings.ai_api_key || "（未设置）")}</div>
          </div>
          <div class="field">
            <label>模型</label>
            <input id="ai-model" placeholder="gpt-4o-mini / deepseek-chat / ..." value="${esc(settings.ai_model || "")}" />
          </div>
        </div>
        <button class="btn primary" id="ai-save-cfg">保存 AI 配置</button>
        <div id="ai-test-out"></div>
      </div>
    `;

    $("#logout").onclick = async () => {
      await api("/api/auth/logout", { method: "POST", body: {} });
      location.reload();
    };
    $("#key-new").onclick = async () => {
      try {
        const r = await api("/api/keys", { method: "POST", body: { name: "key-" + new Date().toISOString().slice(0,10), scopes: "read,write" } });
        $("#key-once").innerHTML = `
          <div class="warn-box" style="margin-top:12px">
            <b>请立即保存（只显示一次）：</b>
            <div class="code-block" style="margin-top:8px" id="key-once-val">${esc(r.key.api_key)}</div>
            <div class="row" style="margin-top:8px"><button class="btn small" id="key-copy">复制</button></div>
          </div>`;
        $("#key-copy").onclick = async () => {
          try { await navigator.clipboard.writeText(r.key.api_key); toast("已复制", "ok"); }
          catch { toast(r.key.api_key); }
        };
        toast("API Key 已创建", "ok");
        setTimeout(() => navigate("settings"), 1500);
      } catch (e) { toast(e.message, "error"); }
    };
    $all("[data-revoke]", root).forEach(b => {
      b.onclick = async () => {
        if (!confirm("吊销该 API Key？使用该 Key 的脚本将立即失效。")) return;
        try {
          await api("/api/keys/" + b.dataset.revoke, { method: "DELETE" });
          toast("已吊销", "ok");
          navigate("settings");
        } catch (e) { toast(e.message, "error"); }
      };
    });
    $("#ai-save-cfg").onclick = async () => {
      try {
        const r = await api("/api/settings", {
          method: "POST",
          body: {
            ai_api_base: $("#ai-base").value.trim(),
            ai_api_key: $("#ai-key").value.trim(),
            ai_model: $("#ai-model").value.trim(),
          }
        });
        toast("AI 配置已保存", "ok");
        $("#ai-test-out").innerHTML = `<div class="ok-box" style="margin-top:10px">已启用：${r.ai.configured ? "是" : "否"} · ${esc(r.ai.model)}</div>`;
      } catch (e) { toast(e.message, "error"); }
    };
  }

  // ---------- API docs for AI / external agents ----------
  function renderApiDocs(root) {
    root.innerHTML = `
      <div class="card">
        <h3>给 AI / 外部 Agent 的写入口</h3>
        <div class="sub">
          本机服务已在 <code>http://127.0.0.1:8764</code> 提供 HTTP 接口。
          外部 AI（Claude、脚本、快捷指令、语音转文字流水线）可以直接写入，无需操作界面。
        </div>
        <div class="ok-box">
          <b>数据契约：</b>日期为本地日历日 <code>YYYY-MM-DD</code>；缺失写 <code>null</code> 或省略，不要写 0；
          跨午夜睡眠记在<b>起床日</b>；体重默认 kg（斤会自动×0.5，lb×0.4536）。
        </div>
      </div>

      <div class="card">
        <h3>1. 自然语言写入（推荐）</h3>
        <div class="code-block">curl -X POST http://127.0.0.1:8764/api/ai/ingest \\
  -H "Content-Type: application/json" \\
  -d '{"text":"今天体重68.5公斤，睡了7个半小时，心情不错，跑步30分钟","confirm":true}'</div>
        <p class="muted small">也可只解析不写入：<code>POST /api/ai/parse</code>，或 <code>confirm:false</code>。</p>
      </div>

      <div class="card">
        <h3>2. 结构化写入（Agent 已理解时）</h3>
        <div class="code-block">curl -X POST http://127.0.0.1:8764/api/ai/ingest \\
  -H "Content-Type: application/json" \\
  -d '{"date":"2026-09-28","values":{"weight":68.5,"sleep_hours":7.5,"mood":4,"exercise_minutes":30},"note":"AI 写入"}'</div>
      </div>

      <div class="card">
        <h3>3. 批量补历史</h3>
        <div class="code-block">curl -X POST http://127.0.0.1:8764/api/ai/batch \\
  -H "Content-Type: application/json" \\
  -d '{"items":[{"text":"前天体重68kg睡了6小时"},{"date":"2026-09-20","values":{"mood":3}}]}'</div>
      </div>

      <div class="card">
        <h3>4. 机器可读契约</h3>
        <p class="muted small">OpenAPI：<a href="/api/openapi.json" target="_blank">/api/openapi.json</a></p>
        <div class="row">
          <button class="btn" id="api-copy-ingest">复制 ingest 示例</button>
          <button class="btn" id="api-try">在本页试一句</button>
        </div>
        <div class="field" style="margin-top:12px">
          <label>试写入文本</label>
          <textarea id="api-demo-text" rows="3">今天体重68kg，睡了7小时，心情不错</textarea>
        </div>
        <div id="api-demo-out"></div>
      </div>

      <div class="card">
        <h3>5. 并发与多用户</h3>
        <div class="stack">
          <div class="warn-box">
            <b>这是单用户本地工具，没有注册/登录。</b>
            「注册账号」不是本产品能力——数据默认属于这台机器上的你。
            多设备同步、多成员账号属于另一套服务端方案，当前刻意不做，以免破坏「数据不离开本机」。
          </div>
          <div class="ok-box">
            <b>并发怎么做：</b>SQLite 开了 WAL + busy_timeout，HTTP 服务为多线程；
            同一时刻多个标签页 / 多个本地 Agent 并发读写是安全的（后写覆盖按记录合并）。
            外部脚本并发调用 <code>/api/ai/ingest</code> 同样可用。
          </div>
          <div class="muted small">
            若以后要多用户：应在前面加反向代理 + Token/Basic Auth，再按用户分库或加 user_id。
            本版接口保持简单可脚本化。
          </div>
        </div>
      </div>
    `;
    $("#api-copy-ingest").onclick = async () => {
      const sample = `curl -X POST http://127.0.0.1:8764/api/ai/ingest -H "Content-Type: application/json" -d '{"text":"今天体重68公斤，睡了7小时，心情不错","confirm":true}'`;
      try { await navigator.clipboard.writeText(sample); toast("已复制", "ok"); }
      catch { toast(sample); }
    };
    $("#api-try").onclick = async () => {
      const t = $("#api-demo-text").value;
      try {
        const draft = await api("/api/ai/parse", { method: "POST", body: { text: t } });
        $("#api-demo-out").innerHTML = `<div class="ai-result"><pre class="code-block" style="margin:0">${esc(JSON.stringify(draft, null, 2))}</pre></div>`;
      } catch (e) {
        $("#api-demo-out").innerHTML = `<div class="error-box">${esc(e.message)}</div>`;
      }
    };
  }

  // ---------- help ----------
  function renderHelp(root) {
    root.innerHTML = `
      <div class="card">
        <h3>如何使用</h3>
        <ol class="small" style="line-height:1.9">
          <li>点左侧「日记录」填写每天的体重、睡眠、心情、运动和备注。</li>
          <li>历史日期可在日历点进去修改；留空表示缺失，不是 0。</li>
          <li>「导入导出」支持 CSV 预览与字段映射，重复记录需要你选择策略。</li>
          <li>「趋势」可叠加指标、切换日/周/月、搜索备注、看描述性相关。</li>
          <li>「月报」生成统计、图表、你的总结，并可导出 Markdown / 打印。</li>
        </ol>
      </div>
      <div class="card">
        <h3>时区与跨午夜睡眠</h3>
        <p class="muted small" style="line-height:1.8">
          所有日期按<b>本地日历日</b>存储（YYYY-MM-DD），不做 UTC 日切换，避免记录“跑到前一天”。
          睡眠时长按一次完整睡眠的小时数填写；<b>跨午夜的睡眠记在起床日</b>。
          例如周一 23:30 睡、周二 7:00 醒，记在周二，睡眠时长 7.5 小时。
          时区信息可写在备注或时区字段中，仅作文档说明。
        </p>
      </div>
      <div class="card">
        <h3>导入项目 01 打鼾数据</h3>
        <p class="muted small" style="line-height:1.8">
          导入页点击「自动映射（项目01打鼾）」，会把常见列名（date / snore_burden / weight / sleep_quality）
          映射到对应指标。若列名不匹配，请手动映射。格式不匹配时会给出映射步骤与错误行信息，不会半途静默写入。
        </p>
      </div>
      <div class="card">
        <h3>数据与隐私</h3>
        <p class="muted small" style="line-height:1.8">
          数据库位于本项目 <code>data/timeline.db</code>。服务只监听 127.0.0.1，不对外网开放。
          导出文件由浏览器下载到你指定的位置。Obsidian 首版通过导出的 Markdown 导入；
          任何外部同步都需要你主动配置与确认，本应用不会自动上传。
        </p>
      </div>
      <div class="card">
        <h3>已知限制</h3>
        <ul class="small muted" style="line-height:1.8">
          <li>图表叠加不同量纲时只看走势，不比较绝对高度。</li>
          <li>相关系数为描述性结果，样本少时不稳定，不代表因果。</li>
          <li>CSV 单位换算支持 kg/lb/斤 与 小时/分钟；其他单位请先换算。</li>
          <li>不提供云同步与多用户；是单机个人工具。</li>
        </ul>
      </div>
    `;
  }

  // go
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
