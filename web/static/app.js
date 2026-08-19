const state = {
  date: "",
  items: [],
  selected: new Set(),
  activeId: null,
  jobTimer: null,
  autoCollected: false,
  jobRunning: false,
};

const $ = (id) => document.getElementById(id);

function fmt(n) {
  return Number(n || 0).toLocaleString("zh-CN");
}

async function api(path, options) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json();
}

function sourceLabel(source) {
  if (source === "trending-zh") return "中文趋势";
  if (source === "rising") return "新晋高星";
  if ((source || "").startsWith("trending")) return "全球趋势";
  return source || "其它";
}

function statusLabel(status) {
  return { new: "未处理", useful: "有用", skipped: "已忽略", downloaded: "已下载" }[status] || status;
}

function renderStats(stats) {
  $("stats").innerHTML = [
    ["今日项目", stats.total],
    ["已标有用", stats.useful],
    ["已下载", stats.downloaded],
    ["已接到我的 GitHub", stats.linked],
    ["历史已下载", stats.all_downloaded],
  ]
    .map(
      ([label, value]) =>
        `<div class="stat" ${label === "已标有用" ? 'id="stat-useful" style="cursor:pointer" title="点击只看已标有用"' : ""}><b>${fmt(value)}</b><span>${label}</span></div>`
    )
    .join("");
  const s = $("stat-useful");
  if (s) s.onclick = () => {
    $("status").value = $("status").value === "useful" ? "" : "useful";
    loadDigest();
  };
}

function renderLangs(stats) {
  const sel = $("language");
  const current = sel.value;
  const langs = (stats.languages || []).map((x) => x.language);
  sel.innerHTML = `<option value="">全部语言</option>` + langs.map((l) => `<option>${l}</option>`).join("");
  if (current && langs.includes(current)) sel.value = current;
}

function renderJob(job) {
  const el = $("job");
  const wasRunning = state.jobRunning;
  state.jobRunning = Boolean(job && job.status === "running");
  if (!job || job.status === "idle" || (job.status === "done" && !job.message)) {
    el.hidden = true;
    return;
  }
  el.hidden = false;
  el.classList.toggle("error", job.status === "error");
  const extra = job.error ? ` ${job.error.replace(/\s+/g, " ").slice(0, 360)}` : "";
  el.textContent = `${job.message || ""} ${job.status === "running" ? `(${job.progress || 0}%)` : ""}${extra}`;
  if (state.jobRunning && !wasRunning) {
    el.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

function flash(message) {
  const el = $("job");
  el.hidden = false;
  el.classList.remove("error");
  el.textContent = message;
  state.jobRunning = true;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
}

function renderList() {
  const box = $("list");
  if (!state.items.length) {
    box.innerHTML = `<div class="empty-list">今天还没有简报。点右上角「重新采集今日」，一两分钟后就能挑项目。</div>`;
    return;
  }
  box.innerHTML = state.items
    .map((item) => {
      const checked = state.selected.has(item.repo_id) ? "checked" : "";
      const active = state.activeId === item.repo_id ? "active" : "";
      const delta = item.stars_today
        ? `今日 +${fmt(item.stars_today)}`
        : item.stars_delta
          ? `较上次 ${item.stars_delta > 0 ? "+" : ""}${fmt(item.stars_delta)}`
          : "";
      const tags = (item.tags || []).map((t) => `<span class="tag">${escapeHtml(t)}</span>`).join("");
      const badge = item.status === "useful"
        ? `<span class="badge useful">✓ 已标有用</span>`
        : item.status === "downloaded"
          ? `<span class="badge downloaded">⬇ 已下载</span>`
          : item.status === "skipped"
            ? `<span class="badge skipped">已忽略</span>`
            : "";
      return `
        <article class="card ${item.status} ${active}" data-id="${item.repo_id}">
          <input class="check" type="checkbox" data-check="${item.repo_id}" ${checked} />
          <div>
            <h3>${escapeHtml(item.full_name)} ${badge}</h3>
            <p class="meta">${escapeHtml(item.language || "未知语言")} · ${sourceLabel(item.source)} · ${statusLabel(item.status)}${item.fork_full_name ? " · 已接入 " + escapeHtml(item.fork_full_name) : ""}</p>
            <p class="summary">${escapeHtml(item.summary_zh || "暂无中文摘要")}</p>
            <p class="why">${escapeHtml(item.why_useful || "")}</p>
            <div class="tags">${tags}</div>
          </div>
          <div class="stars">★ ${fmt(item.stars)}<small>${delta}</small></div>
        </article>`;
    })
    .join("");
  box.querySelectorAll(".card").forEach((card) => {
    card.addEventListener("click", (ev) => {
      if (ev.target.matches("input[type=checkbox]")) return;
      const id = Number(card.dataset.id);
      state.activeId = id;
      renderList();
      renderDetail();
    });
  });
  box.querySelectorAll("input[data-check]").forEach((boxEl) => {
    boxEl.addEventListener("change", () => {
      const id = Number(boxEl.dataset.check);
      if (boxEl.checked) state.selected.add(id);
      else state.selected.delete(id);
      renderDock();
    });
  });
}

function currentItem() {
  return state.items.find((x) => x.repo_id === state.activeId) || null;
}

function renderDetail() {
  const item = currentItem();
  const el = $("detail");
  if (!item) {
    el.innerHTML = `<div class="empty">点左边一条，看完整摘要和 README。</div>`;
    return;
  }
  el.innerHTML = `
    <h2>${escapeHtml(item.full_name)}</h2>
    <p class="meta">${escapeHtml(item.language || "未知")} · ★ ${fmt(item.stars)} · fork ${fmt(item.forks)} · ${sourceLabel(item.source)}</p>
    <p>${escapeHtml(item.summary_zh || "暂无中文摘要。点「用 DeepSeek 写中文摘要」。")}</p>
    <p class="why">${escapeHtml(item.why_useful || "")}</p>
    <div class="detail-actions">
      <button class="btn" data-act="useful">标为有用</button>
      <button class="btn" data-act="skipped">忽略</button>
      <button class="btn primary" data-act="download">下载这个</button>
      <a class="btn ghost" href="${item.html_url}" target="_blank" rel="noreferrer">打开原仓库</a>
      ${item.fork_url ? `<a class="btn ghost" href="${item.fork_url}" target="_blank" rel="noreferrer">打开我的 fork</a>` : ""}
      ${item.local_path ? `<button class="btn ghost" data-act="folder">打开本地</button>` : ""}
    </div>
    <pre class="readme">${escapeHtml(item.readme_excerpt || "还没有抓到 README。")}</pre>
  `;
  el.querySelectorAll("[data-act]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const act = btn.dataset.act;
      if (act === "folder") {
        await api("/api/open-folder?path=" + encodeURIComponent(item.local_path), { method: "POST" });
        return;
      }
      if (act === "download") {
        state.selected.add(item.repo_id);
        renderDock();
        await startDownload([item.repo_id]);
        return;
      }
      const label = btn.textContent;
      btn.disabled = true;
      btn.textContent = act === "useful" ? "标记中…" : "处理中…";
      try {
        flash(act === "useful" ? "正在标记为有用…" : "正在忽略…");
        await api(`/api/repo/${item.repo_id}/status`, {
          method: "POST",
          body: JSON.stringify({ status: act }),
        });
        await loadDigest();
        flash(act === "useful" ? "已标为有用" : "已忽略");
      } catch (err) {
        renderJob({ status: "error", message: err.message || "操作失败" });
      } finally {
        btn.disabled = false;
        btn.textContent = label;
      }
    });
  });
}

function renderDock() {
  const ids = [...state.selected];
  $("picked-count").textContent = String(ids.length);
  const names = state.items.filter((x) => state.selected.has(x.repo_id)).map((x) => x.name);
  $("picked-names").textContent = names.slice(0, 4).join("、") + (names.length > 4 ? "…" : "");
}

function escapeHtml(s) {
  return String(s || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function loadDigest() {
  const params = new URLSearchParams({
    date: $("date").value || state.date,
    q: $("q").value.trim(),
    language: $("language").value,
    source: $("source").value,
    status: $("status").value,
    min_stars: $("min_stars").value || "0",
  });
  const data = await api("/api/digest?" + params.toString());
  state.date = data.date;
  state.items = data.items;
  if (!$("date").value) $("date").value = data.date;
  if (data.dates && data.dates.length && !$("date").value) {
    $("date").value = data.dates[0];
  }
  if (!state.activeId && state.items[0]) state.activeId = state.items[0].repo_id;
  if (state.activeId && !state.items.some((x) => x.repo_id === state.activeId)) {
    state.activeId = state.items[0] ? state.items[0].repo_id : null;
  }
  renderStats(data.stats);
  renderLangs(data.stats);
  renderJob(data.job);
  renderList();
  renderDetail();
  renderDock();
  fillSettings(data.settings);
  renderConnect(data.settings);
  if (!data.items.length && data.job.status !== "running" && !state.autoCollected) {
    state.autoCollected = true;
    await startCollect(false);
  }
}

function fillSettings(s) {
  if (!s) return;
  $("download_dir").value = s.download_dir || "";
  $("hub_repo").value = s.hub_repo || "github-star-picks";
  $("github_token").placeholder = s.has_github_token ? "已保存，留空不改" : "ghp_… 需要 repo 权限";
  $("deepseek_api_key").placeholder = s.has_deepseek_key ? "已保存，留空不改" : "sk-… 到 platform.deepseek.com 创建";
  $("xai_api_key").placeholder = s.has_xai_key ? "已保存，留空不改" : "xai-… 可留空";
  $("rising_days").value = s.rising_days;
  $("rising_min_stars").value = s.rising_min_stars;
  if (s.min_stars !== undefined && $("min_stars")) {
    const allowed = ["0", "1000", "2000", "5000", "10000"];
    const v = String(s.min_stars);
    $("min_stars").value = allowed.includes(v) ? v : "2000";
  }
  $("max_repos_per_day").value = s.max_repos_per_day;
  const status = $("github-status");
  if (s.github_connected) {
    status.innerHTML = `已连接 <a href="${s.github_url}" target="_blank" rel="noreferrer">@${escapeHtml(s.github_login)}</a> · 总册 <a href="${s.hub_url}" target="_blank" rel="noreferrer">${escapeHtml(s.hub_repo)}</a> · 日报程序 <a href="${s.app_url}" target="_blank" rel="noreferrer">${escapeHtml(s.app_repo)}</a>`;
  } else {
    status.textContent = "还没连上你的 GitHub。请用 Classic Token（勾选 repo），填好后点「连接并同步到我的 GitHub」。";
  }
}

function renderConnect(s) {
  const bar = $("connect-bar");
  if (!s) return;
  const bits = [];
  if (s.github_connected) {
    bits.push(`GitHub 已接 <a href="${s.github_url}" target="_blank" rel="noreferrer">@${escapeHtml(s.github_login)}</a>`);
  } else {
    bits.push(`还没连 GitHub <button class="btn" id="connect-open" type="button">去连接</button>`);
  }
  if (s.has_deepseek_key) {
    bits.push("DeepSeek V4 Flash 已接入，中文摘要用它来写");
  } else {
    bits.push(`还没填 DeepSeek Key，摘要只是简要说明 <button class="btn" id="deepseek-open" type="button">去填写</button>`);
  }
  bar.hidden = false;
  bar.classList.toggle("warn", !s.github_connected || !s.has_deepseek_key);
  bar.innerHTML = bits.join(" · ");
  const connectBtn = $("connect-open");
  if (connectBtn) connectBtn.onclick = () => $("settings").showModal();
  const deepseekBtn = $("deepseek-open");
  if (deepseekBtn) deepseekBtn.onclick = () => $("settings").showModal();
}

async function startCollect(force) {
  renderJob({ status: "running", progress: 1, message: "开始采集今日高星项目…" });
  await api("/api/collect", { method: "POST", body: JSON.stringify({ force }) });
  pollJob();
}

async function startDownload(ids) {
  renderJob({ status: "running", progress: 1, message: "开始下载并接到你的 GitHub…" });
  try {
    await api("/api/download", { method: "POST", body: JSON.stringify({ ids }) });
    pollJob();
  } catch (err) {
    renderJob({ status: "error", message: err.message || "下载失败" });
    if (String(err.message || "").includes("连接")) $("settings").showModal();
  }
}

function pollJob() {
  if (state.jobTimer) clearInterval(state.jobTimer);
  state.jobTimer = setInterval(async () => {
    const job = await api("/api/job");
    renderJob(job);
    if (job.status !== "running") {
      clearInterval(state.jobTimer);
      state.jobTimer = null;
      await loadDigest();
    }
  }, 1200);
}

function bind() {
  $("refresh-btn").onclick = async () => {
    flash("正在刷新列表…");
    await loadDigest();
    flash("列表已刷新");
  };
  $("collect-btn").onclick = () => startCollect(true);
  $("settings-btn").onclick = () => $("settings").showModal();
  $("save-settings").onclick = async () => {
    const payload = {
      download_dir: $("download_dir").value.trim(),
      hub_repo: $("hub_repo").value.trim() || "github-star-picks",
      rising_days: Number($("rising_days").value),
      rising_min_stars: Number($("rising_min_stars").value),
      max_repos_per_day: Number($("max_repos_per_day").value),
    };
    if ($("github_token").value.trim()) payload.github_token = $("github_token").value.trim();
    if ($("deepseek_api_key").value.trim()) payload.deepseek_api_key = $("deepseek_api_key").value.trim();
    if ($("xai_api_key").value.trim()) payload.xai_api_key = $("xai_api_key").value.trim();
    flash("正在保存设置…");
    await api("/api/settings", { method: "POST", body: JSON.stringify(payload) });
    $("settings").close();
    $("github_token").value = "";
    $("deepseek_api_key").value = "";
    $("xai_api_key").value = "";
    await loadDigest();
    flash("设置已保存");
  };
  $("summarize-btn").onclick = async () => {
    renderJob({ status: "running", progress: 1, message: "正在用 DeepSeek 写中文摘要…" });
    try {
      await api("/api/summarize", {
        method: "POST",
        body: JSON.stringify({ date: $("date").value || state.date }),
      });
      pollJob();
    } catch (err) {
      renderJob({ status: "error", message: err.message || "DeepSeek 摘要失败" });
      $("settings").showModal();
    }
  };
  $("connect-btn").onclick = async () => {
    const payload = {};
    if ($("github_token").value.trim()) payload.github_token = $("github_token").value.trim();
    if ($("hub_repo").value.trim()) {
      await api("/api/settings", {
        method: "POST",
        body: JSON.stringify({ hub_repo: $("hub_repo").value.trim() }),
      });
    }
    renderJob({ status: "running", progress: 1, message: "正在连接你的 GitHub…" });
    await api("/api/github/connect", { method: "POST", body: JSON.stringify(payload) });
    $("settings").close();
    $("github_token").value = "";
    pollJob();
  };
  $("date").onchange = () => loadDigest();
  $("q").oninput = debounce(loadDigest, 250);
  $("language").onchange = loadDigest;
  $("source").onchange = loadDigest;
  $("status").onchange = loadDigest;
  $("min_stars").onchange = async () => {
    flash("正在更新最低 star 筛选…");
    await api("/api/settings", {
      method: "POST",
      body: JSON.stringify({ min_stars: Number($("min_stars").value) }),
    });
    await loadDigest();
    flash("最低 star 筛选已更新");
  };
  $("download-btn").onclick = async () => {
    const ids = [...state.selected];
    if (!ids.length) {
      alert("先勾选左边几个有用的项目。如果还没连 GitHub，先去右上角设置里连接。");
      return;
    }
    await startDownload(ids);
  };
  $("mark-useful-btn").onclick = async () => {
    const ids = [...state.selected];
    if (!ids.length) {
      alert("先勾选左边几个有用的项目。");
      return;
    }
    const btn = $("mark-useful-btn");
    btn.disabled = true;
    try {
      flash(`正在标记 ${ids.length} 个项目为有用…`);
      for (const id of ids) {
        await api(`/api/repo/${id}/status`, {
          method: "POST",
          body: JSON.stringify({ status: "useful" }),
        });
      }
      await loadDigest();
      flash(`已将 ${ids.length} 个项目标为有用`);
    } catch (err) {
      renderJob({ status: "error", message: err.message || "操作失败" });
    } finally {
      btn.disabled = false;
    }
  };
  $("open-folder-btn").onclick = async () => {
    flash("正在打开下载目录…");
    await api("/api/open-folder", { method: "POST" });
    flash("已打开下载目录");
  };
}

function debounce(fn, wait) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), wait);
  };
}

bind();
loadDigest().catch((err) => {
  $("job").hidden = false;
  $("job").classList.add("error");
  $("job").textContent = "界面加载失败：" + err.message;
});
