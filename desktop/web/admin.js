"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  let adminKey = "";
  let dashboard = null;
  let batches = [];
  const labels = { active: "运行中", paused: "已暂停", needs_rebind: "需重新绑定", service_error: "服务异常" };
  const date = (value) => value ? new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(new Date(value)) : "--";
  const show = (id, visible) => { $(id).hidden = !visible; };
  const notice = (message, good = false) => {
    $("admin-notice").textContent = message || "";
    $("admin-notice").classList.toggle("good", good);
    show("admin-notice", !!message);
  };
  const iconRefresh = () => window.lucide?.createIcons();
  async function request(path, options = {}) {
    if (!adminKey) throw new Error("请先输入管理员密钥");
    const method = (options.method || "GET").toUpperCase();
    const headers = { Accept: "application/json", Authorization: `Bearer ${adminKey}`, ...(options.body === undefined ? {} : { "Content-Type": "application/json" }), ...(method === "GET" ? {} : { "Idempotency-Key": crypto.randomUUID() }) };
    const response = await fetch(path, { ...options, headers, cache: "no-store" });
    let result;
    try { result = await response.json(); } catch { throw new Error("云端返回异常，请稍后重试"); }
    if (!response.ok || !result.ok) throw new Error(result.error?.message || result.error || "管理员操作未完成");
    return result.data;
  }
  function setStatus(online) {
    const status = $("admin-status");
    status.replaceChildren();
    const dot = document.createElement("span");
    dot.className = `dot ${online ? "online" : "offline"}`;
    status.append(dot, document.createTextNode(online ? "管理员已验证" : "未验证"));
  }
  function setMetric(id, value) { $(id).textContent = value == null ? "--" : String(value); }
  function renderDashboard() {
    const users = dashboard?.users || {}, batch = dashboard?.batches || {}, runs = dashboard?.runs || {};
    setMetric("metric-users", users.total);
    $("metric-users-detail").textContent = `绑定 ${users.bound ?? "--"} · 活跃 ${users.active ?? "--"}`;
    setMetric("metric-batches", batch.claimable);
    const remaining = batch.remaining ?? Math.max(0, (batch.quantity ?? 0) - (batch.claimed ?? 0));
    $("metric-batches-detail").textContent = `剩余 ${remaining} / ${batch.quantity ?? "--"}`;
    setMetric("metric-runs", runs.today);
    $("metric-runs-detail").textContent = `完成 ${runs.completed ?? "--"} · 失败 ${runs.failed ?? "--"}`;
    setMetric("metric-total-runs", runs.total);
  }
  function tableCell(row, value) { const cell = row.insertCell(); cell.textContent = value == null ? "--" : String(value); return cell; }
  function renderBatches() {
    const host = $("batch-table"); host.replaceChildren();
    if (!batches.length) { host.innerHTML = '<div class="table-empty">暂无批次</div>'; return; }
    const wrapper = document.createElement("div"); wrapper.className = "table-wrap";
    const table = document.createElement("table"); table.className = "admin-table";
    const head = table.createTHead().insertRow();
    ["创建时间", "名额", "已领取", "剩余", "有效期", "状态", "操作"].forEach((name) => { const th = document.createElement("th"); th.textContent = name; head.append(th); });
    const body = table.createTBody();
    batches.forEach((item) => {
      const row = body.insertRow();
      tableCell(row, date(item.createdAt)); tableCell(row, item.quantity); tableCell(row, item.claimed); tableCell(row, item.remaining);
      tableCell(row, date(item.expiresAt));
      const status = tableCell(row, item.status === "active" ? "可领取" : item.status === "expired" ? "已过期" : "已撤销");
      if (item.status !== "active") status.classList.add("muted");
      const actions = row.insertCell();
      if (item.status === "active") {
        const revoke = document.createElement("button"); revoke.className = "icon-button danger"; revoke.type = "button"; revoke.title = "撤销批次"; revoke.ariaLabel = "撤销批次"; revoke.innerHTML = '<i data-lucide="x-circle"></i>';
        revoke.addEventListener("click", () => revokeBatch(item.id)); actions.append(revoke);
      } else actions.textContent = "--";
    });
    wrapper.append(table); host.append(wrapper); iconRefresh();
  }
  function renderUsers() {
    const items = dashboard?.bindings || dashboard?.users?.items || [];
    const host = $("user-table"); host.replaceChildren();
    if (!Array.isArray(items) || !items.length) { host.innerHTML = '<div class="table-empty">暂无账号运行数据</div>'; return; }
    const wrapper = document.createElement("div"); wrapper.className = "table-wrap";
    const table = document.createElement("table"); table.className = "admin-table";
    const head = table.createTHead().insertRow(); ["账号", "状态", "执行区间", "最近运行", "最近结果"].forEach((name) => { const th = document.createElement("th"); th.textContent = name; head.append(th); });
    const body = table.createTBody();
    items.forEach((item) => { const row = body.insertRow(); tableCell(row, item.label || "领克账号"); tableCell(row, labels[item.status] || item.status); tableCell(row, item.scheduleTime); tableCell(row, date(item.lastRunAt)); tableCell(row, item.lastResult || "暂无记录"); });
    wrapper.append(table); host.append(wrapper);
  }
  async function load() {
    const [summary, list] = await Promise.all([request("/v1/admin/dashboard"), request("/v1/admin/invite-batches")]);
    dashboard = summary || {}; batches = Array.isArray(list?.items) ? list.items : [];
    renderDashboard(); renderBatches(); renderUsers();
    $("admin-last-sync").textContent = `刚刚更新 ${new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date())}`;
  }
  async function revokeBatch(id) {
    if (!window.confirm("确定撤销这个领取批次吗？已领取的用户不会受到影响。")) return;
    try { await request(`/v1/admin/invite-batches/${encodeURIComponent(id)}`, { method: "DELETE" }); await load(); notice("批次已撤销", true); }
    catch (error) { notice(error.message); }
  }
  $("admin-login-form").addEventListener("submit", async (event) => {
    event.preventDefault(); adminKey = $("admin-key").value.trim();
    if (!adminKey) return;
    try { await load(); show("admin-login", false); show("admin-console", true); $("admin-key").value = ""; $("admin-refresh").disabled = false; setStatus(true); notice("管理员身份验证成功", true); }
    catch (error) { adminKey = ""; setStatus(false); notice(error.message); }
  });
  $("admin-refresh").addEventListener("click", async () => { try { await load(); notice("看板已刷新", true); } catch (error) { notice(error.message); } });
  $("batch-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const quantity = Number($("batch-quantity").value), expiresInDays = Number($("batch-days").value);
    if (!Number.isInteger(quantity) || quantity < 1 || quantity > 1000 || !Number.isInteger(expiresInDays) || expiresInDays < 1 || expiresInDays > 30) { notice("请输入有效的名额数量和有效期"); return; }
    try {
      const result = await request("/v1/admin/invite-batches", { method: "POST", body: JSON.stringify({ quantity, expiresInDays }) });
      $("claim-url").textContent = result.claimUrl || "--"; show("claim-result", true); await load(); notice("领取批次已生成", true);
    } catch (error) { notice(error.message); }
  });
  $("copy-claim-url").addEventListener("click", async () => { try { await navigator.clipboard.writeText($("claim-url").textContent); notice("领取链接已复制", true); } catch { notice("无法访问剪贴板，请手动选择并复制领取链接"); } });
  iconRefresh();
})();
