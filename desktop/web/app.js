"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const tokenKey = "lynkco-helper.local-token";
  const hashToken = location.hash.slice(1); if (hashToken) sessionStorage.setItem(tokenKey, hashToken);
  const token = hashToken || sessionStorage.getItem(tokenKey) || "";
  history.replaceState(null, "", location.pathname + location.search);
  let state = null,
    view = "overview",
    identityMode = "claim",
    platform = "IOS",
    busy = false;
  let proxyConfirmed = false, selectedBindingStep = null, activePair = null;
  let autoPrepareKey = null;
  let qrUrl = null,
    qrPair = null,
    settingsVersion = "",
    settingsBindingId = null,
    settingsDirty = false,
    historyItems = [],
    historyCursor = null;
  let recovery = null,
    recoverySaved = false,
    confirmResolve = null,
    forceRecover = false;
  const labels = {
    active: "运行中",
    paused: "已暂停",
    needs_rebind: "需要重新绑定",
    service_error: "服务异常",
    queued: "等待执行",
    pending: "等待执行",
    running: "执行中",
    success: "已完成",
    succeeded: "已完成",
    completed: "已完成",
    failed: "未完成",
    partial: "部分完成",
    skipped: "已跳过",
    signed: "已签到",
    already_signed: "已签到",
    retry_wait: "等待重试",
    inflight: "执行中",
    done: "已完成",
    unknown: "结果待确认",
    disabled: "未开启",
    not_started: "待执行",
    error: "异常",
  };
  const text = (id, value) => {
    $(id).textContent = value == null ? "--" : String(value);
  };
  const badge = (id, value, tone = "") => {
    const node = $(id);
    node.className = `badge ${tone}`.trim();
    node.replaceChildren(Object.assign(document.createElement("i"), { ariaHidden: "true" }), document.createTextNode(value == null ? "--" : String(value)));
  };
  function taskIcon(id, status) {
    const node = $(id);
    const success = ["success", "succeeded", "completed", "signed", "already_signed", "done"];
    const failed = ["failed", "error"];
    const running = ["running", "queued", "retry_wait", "inflight"];
    const disabled = ["disabled", "skipped"];
    const tone = success.includes(status) ? "success"
      : failed.includes(status) ? "error"
      : running.includes(status) ? "running"
      : disabled.includes(status) ? "disabled"
      : status === "unknown" ? "warning" : "pending";
    const icon = tone === "success" ? "check"
      : tone === "error" ? "x"
      : tone === "running" ? "loader-circle"
      : tone === "disabled" ? "minus"
      : tone === "warning" ? "circle-help" : "clock-3";
    node.setAttribute("class", `task-state-icon ${tone}`);
    node.setAttribute("data-lucide", icon);
  }
  const show = (id, visible) => {
    $(id).hidden = !visible;
  };
  const icons = () => window.lucide?.createIcons();
  const date = (value, withTime = true) =>
    value
      ? new Intl.DateTimeFormat("zh-CN", {
          timeZone: "Asia/Shanghai",
          month: "2-digit",
          day: "2-digit",
          ...(withTime
            ? { hour: "2-digit", minute: "2-digit", hour12: false }
            : {}),
        }).format(new Date(value))
      : "--";
  const shanghaiParts = (value = new Date()) =>
    Object.fromEntries(
      new Intl.DateTimeFormat("en-CA", {
        timeZone: "Asia/Shanghai",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
      })
        .formatToParts(value)
        .filter((part) => part.type !== "literal")
        .map((part) => [part.type, part.value]),
    );
  const businessDate = (value = new Date()) => {
    const parts = shanghaiParts(value);
    return `${parts.year}-${parts.month}-${parts.day}`;
  };
  const businessDateLabel = (value = new Date()) => {
    const parts = shanghaiParts(value);
    return `${parts.year} 年 ${parts.month} 月 ${parts.day} 日`;
  };
  function notice(message, good = false) {
    text("notice", message);
    $("notice").classList.toggle("good", good);
    show("notice", !!message);
  }
  async function api(path, body) {
    if (!token) throw new Error("页面已失去本机连接，请重新双击打开每日任务助手。");
    const response = await fetch(path, {
      method: body === undefined ? "GET" : "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(path === "/api/binding/run" ? 160000 : 60000),
    });
    const result = await response.json();
    if (!result.ok)
      throw new Error(result.error || "无法连接本机助手，请重新打开程序。");
    return result.data;
  }
  function setButtonLoading(button, loading, label = "处理中") {
    if (!button) return;
    if (loading) {
      button.classList.add("is-loading");
      button.dataset.loadingLabel = `${label}…`;
      button.setAttribute("aria-busy", "true");
    } else {
      button.classList.remove("is-loading");
      delete button.dataset.loadingLabel;
      button.removeAttribute("aria-busy");
    }
  }
  async function perform(operation, message, trigger, loadingLabel) {
    if (busy) return;
    busy = true;
    const buttons = [...document.querySelectorAll("button")];
    const disabled = new Map(buttons.map((button) => [button, button.disabled]));
    const activeButton = trigger?.closest?.("button") || document.activeElement?.closest?.("button");
    setButtonLoading(activeButton, true, loadingLabel);
    buttons.forEach((button) => (button.disabled = true));
    try {
      await operation();
      if (message) notice(message, true);
      state = await api("/api/status");
      render();
    } catch (error) {
      if (/管理凭证已失效|恢复码/.test(error.message || "")) {
        forceRecover = true;
        identityType("recover");
        view = "overview";
      }
      try {
        state = await api("/api/status");
        render();
      } catch {
        // Preserve the original operation error when the local helper is unavailable.
      }
      notice(
        error.name === "TimeoutError"
          ? "等待响应超时，请刷新状态后重试。"
          : error.message || "操作未完成，请重试。",
      );
    } finally {
      busy = false;
      buttons.forEach((button) => (button.disabled = disabled.get(button)));
      setButtonLoading(activeButton, false);
      if (state) render();
    }
  }
  function navigate(next) {
    view = next;
    document
      .querySelectorAll("[data-view]")
      .forEach((button) =>
        button.classList.toggle("active", button.dataset.view === view),
      );
    text(
      "page-title",
      {
        overview: "概览",
        bind: "绑定账号",
        history: "运行记录",
      }[view],
    );
    render();
  }
  function resetBindingFlow() {
    proxyConfirmed = false;
    selectedBindingStep = null;
    activePair = null;
    autoPrepareKey = null;
    qrPair = null;
    if (qrUrl) {
      URL.revokeObjectURL(qrUrl);
      qrUrl = null;
    }
    $("pair-qr").removeAttribute("src");
    $("upload-consent").checked = false;
    $("proxy-removed").checked = false;
  }
  function captureFingerprint() {
    if (state?.capture?.stage !== "captured") return null;
    const event = [...(state.capture.events || [])]
      .reverse()
      .find((item) => item.outcome === "captured");
    return event?.id || event?.at || `${state.proxy?.pairUrl || "local"}:${state.capture.events?.length || 0}`;
  }
  function silentlyPrepareCapture() {
    if (busy || state?.capture?.stage !== "captured" || state.candidate || !$('upload-consent').checked)
      return;
    const key = captureFingerprint();
    if (!key || autoPrepareKey === key) return;
    autoPrepareKey = key;
    text("auto-verify-status", "正在静默验证个人信息…");
    perform(async () => {
      await api("/api/candidates/prepare", { consent: true });
      notice("");
    });
  }
  function runTable(target, items) {
    const container = $(target);
    container.replaceChildren();
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "table-empty";
      empty.textContent = "暂无运行记录";
      container.append(empty);
      return;
    }
    const wrapper = document.createElement("div");
    wrapper.className = "table-wrap";
    const table = document.createElement("table");
    table.className = "run-table";
    const head = table.createTHead().insertRow();
    ["执行时间", "任务", "结果", "签到 / 分享", "奖励"].forEach((label) => {
      const cell = document.createElement("th");
      cell.textContent = label;
      head.append(cell);
    });
    const body = table.createTBody();
    items.forEach((run) => {
      const row = body.insertRow();
      row.insertCell().textContent = run.startedAt
        ? date(run.startedAt)
        : run.businessDate;
      row.insertCell().textContent = "每日任务";
      const result = row.insertCell();
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = labels[run.status] || run.status;
      if (["failed", "error"].includes(run.status))
        badge.classList.add("error");
      if (
        ["queued", "pending", "running", "partial", "unknown"].includes(
          run.status,
        )
      )
        badge.classList.add("warning");
      result.append(badge);
      if (run.message) {
        const detail = document.createElement("span");
        detail.className = "detail";
        detail.textContent = run.message;
        result.append(detail);
      }
      Object.entries(run.notifications || {}).forEach(([channel, status]) => {
        const detail = document.createElement("span");
        detail.className = "detail";
        detail.textContent = `${channel === "bark" ? "Bark" : "Server 酱"}：${{sent:"已推送",failed:"推送失败",sending:"推送待确认"}[status] || status}`;
        result.append(detail);
      });
      row.insertCell().textContent = `${labels[run.signStatus] || run.signStatus || "--"} / ${labels[run.shareStatus] || run.shareStatus || "--"}`;
      const rewardCell = row.insertCell();
      rewardCell.className = "reward-list";
      const rewards = run.rewards || {};
      const lines = [`积分：${run.pointsBefore ?? "暂无"} → ${run.pointsAfter ?? "暂无"}`];
      if (rewards.signEnergy != null) lines.push(`签到能量体：+${rewards.signEnergy}`);
      if (run.shareStatus === "success") lines.push(`分享后能量体：${rewards.sharePointsBefore ?? "暂无"} → ${rewards.sharePointsAfter ?? "暂无"}`);
      else lines.push(`分享：${labels[run.shareStatus] || "暂无"}`);
      if (rewards.cardsBefore != null && rewards.cardsAfter != null) {
        const change = rewards.cardsAfter - rewards.cardsBefore;
        lines.push(`签到卡：${rewards.cardsBefore} → ${rewards.cardsAfter}${change > 0 ? `（净增 ${change} 张）` : ""}`);
      } else lines.push("签到卡奖励：暂无数据");
      lines.forEach((value) => {
        const line = document.createElement("span");
        line.className = value.includes("能量") ? "reward energy" : value.includes("签到卡") ? "reward card" : value.includes("分享") ? "reward error" : "reward";
        line.textContent = value;
        rewardCell.append(line);
      });
    });
    wrapper.append(table);
    container.append(wrapper);
  }
  function applyCapabilities() {
    const binding = state?.binding,
      candidate = state?.candidate;
    $("bind-share").disabled = !candidate?.capabilities?.share;
    $("settings-share").disabled = !binding?.canShare;
    if (!candidate?.capabilities?.share) $("bind-share").checked = false;
    if (!binding?.canShare) $("settings-share").checked = false;
    text(
      "bind-share-hint",
      candidate?.capabilities?.share
        ? "每日分享默认关闭，可按需开启。"
        : "本次登录状态缺少分享所需设备信息，仅开启签到。",
    );
    text(
      "settings-share-hint",
      binding?.canShare ? "" : "当前账号缺少分享所需设备信息。",
    );
    $("settings-form")
      .querySelectorAll("input,select,button")
      .forEach((element) => {
        if (element.id !== "settings-share") element.disabled = !binding;
      });
    $("binding-unbind").disabled = !binding;
    ["bind", "settings"].forEach(prefix => {
      const available = selectedSlotAvailable(prefix);
      const form = $(prefix === "bind" ? "activate-form" : "settings-form");
      form.querySelector('[type="submit"]').disabled = !available || (prefix === "bind" ? !candidate : !binding);
    });
  }
  function selectedSlotAvailable(prefix) {
    const value = $(`${prefix}-window`).value;
    return !!state?.scheduleWindows?.items?.some(item =>
      item.value === value && (item.remaining > 0 || item.current));
  }
  function updatePushChoice() {
    const selected = document.querySelector('input[name="push-channel"]:checked')?.value || "none";
    show("push-fields", selected !== "none");
    ["bark", "serverchan"].forEach(channel => {
      const active = selected === channel;
      show(`${channel}-field`, active);
      $(`${channel}-key`).disabled = !active || !state?.binding;
      $(`${channel}-clear`).disabled = !active || !state?.binding;
    });
    $("push-save").disabled = !state?.binding;
    text("push-save-label", selected === "none" ? "保存设置" : "保存并测试");
  }
  function renderScheduleWindows() {
    const items = state.scheduleWindows?.items;
    ["bind", "settings"].forEach(prefix => {
      const select = $(`${prefix}-window`);
      const previous = select.value;
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = items ? "请选择执行区间" : "名额暂不可用";
      placeholder.disabled = true;
      const options = Array.from({length: 12}, (_, index) => {
        const value = hourWindow(index * 2);
        const slot = items?.find(item => item.value === value);
        const option = document.createElement("option");
        option.value = value;
        option.textContent = `${hourLabel(value)} · ${slot ? `剩余 ${slot.remaining}/${slot.limit}${slot.current ? " · 当前区间" : ""}` : "名额未知"}`;
        option.disabled = !slot || (slot.remaining <= 0 && !slot.current);
        return option;
      });
      select.replaceChildren(placeholder, ...options);
      const preferred = options.find(option => option.value === previous && !option.disabled)
        || options.find(option => !option.disabled);
      select.value = preferred?.value || "";
      text(`${prefix}-quota-hint`, items ? "暂停任务仍保留名额，保存时以云端剩余名额为准。" : "暂时无法读取剩余名额，请刷新云端状态后再保存。");
      if (prefix === "settings") {
        const selected = items?.find(item => item.value === select.value);
        const available = selected && (selected.remaining > 0 || selected.current);
        badge("quota-badge", available ? "名额充足" : items ? "名额已满" : "名额查询中", available ? "blue" : "warning");
      }
    });
  }
  function render() {
    if (!state) return;
    const binding = state.binding,
      capture = state.capture,
      proxy = state.proxy;
    show("identity-panel", !state.hasIdentity || forceRecover);
    ["overview", "bind", "history"].forEach((name) =>
      show(
        "view-" + name,
        name === view && state.hasIdentity && !forceRecover,
      ),
    );
    const online = state.connected && state.configured;
    $("cloud-status").replaceChildren();
    const dot = document.createElement("span");
    dot.className = "dot " + (online ? "online" : "offline");
    $("cloud-status").append(
      dot,
      document.createTextNode(
        online ? "云端已连接" : state.connected ? "云端待配置" : "云端未连接",
      ),
    );
    text(
      "last-sync",
      state.lastRefreshAt ? `${date(state.lastRefreshAt)} 更新` : "尚未同步",
    );
    show("empty-account", !binding);
    show("account-overview", !!binding);
    renderScheduleWindows();
    if (settingsBindingId !== binding?.id) {
      settingsBindingId = binding?.id;
      settingsVersion = "";
      settingsDirty = false;
      $("bark-key").value = $("serverchan-key").value = "";
    }
    if (binding) {
      text("account-label", binding.label);
      const avatarUrl = binding.avatarUrl || binding.avatarurl || binding.profile?.avatarUrl || binding.profile?.avatarurl;
      const avatar = $("account-avatar");
      avatar.replaceChildren();
      if (avatarUrl) {
        const image = document.createElement("img");
        image.src = avatarUrl;
        image.alt = "账号头像";
        image.onerror = () => { avatar.textContent = (binding.label || "账").slice(0, 1); };
        avatar.append(image);
      } else {
        avatar.textContent = (binding.label || "账").slice(0, 1);
      }
      badge("account-status", labels[binding.status] || binding.status, binding.status === "active" ? "success" : "warning");
      text("account-window", `每日 ${hourLabel(binding.scheduleTime)}`);
      text("account-streak", `连续签到 ${binding.inventory?.days ?? "--"} 天`);
      text(
        "next-run",
        binding.status === "active" ? date(binding.nextRunAt) : "--",
      );
      const latest = state.runs.items[0];
      const latestIsToday = latest?.businessDate === businessDate();
      const todayStatus = latestIsToday ? labels[latest.status] || latest.status : "待执行";
      const statusTone = latestIsToday && ["completed", "success", "succeeded", "already_signed", "signed"].includes(latest.status)
        ? "success"
        : latestIsToday && ["failed", "error"].includes(latest.status)
          ? "error"
          : latestIsToday
            ? "warning"
            : "";
      text("today-date", businessDateLabel());
      badge("today-status", todayStatus, statusTone);
      const pointsDelta = latest?.pointsBefore != null && latest?.pointsAfter != null
        ? Number(latest.pointsAfter) - Number(latest.pointsBefore)
        : null;
      text("sign-task-detail", latestIsToday && latest.signStatus === "already_signed" ? "今日已完成，重复触发会自动跳过" : "每天执行一次");
      text("sign-task-reward", pointsDelta == null ? "待执行" : `${pointsDelta >= 0 ? "+" : ""}${pointsDelta} 积分`);
      taskIcon("sign-task-icon", latestIsToday ? latest.signStatus : "pending");
      text("share-task-detail", binding.doShare ? "签到时同时完成分享" : "当前未开启分享");
      const shareEnergy = latest?.rewards?.signEnergy ?? latest?.shareEnergy;
      text("share-task-reward", !binding.doShare ? "已关闭" : shareEnergy == null ? "待执行" : `+${shareEnergy} 能量体`);
      taskIcon("share-task-icon", !binding.doShare ? "disabled" : latestIsToday ? latest.shareStatus : "pending");
      text("next-run-label", binding.status === "active" && binding.nextRunAt ? `下一次执行：${date(binding.nextRunAt)}` : "下一次执行：已暂停");
      const inventory = binding.inventory;
      text("points", inventory?.points ?? latest?.pointsAfter ?? latest?.pointsBefore ?? "--");
      text("sign-cards", inventory?.cards != null ? inventory.cards : binding.inventoryError ? "查询失败" : "暂无");
      text("energy", inventory?.energy != null ? `${inventory.energy}` : latest?.energyAfter != null ? `${latest.energyAfter}` : binding.inventoryError ? "查询失败" : "暂无");
      text("asset-updated", state.lastRefreshAt ? date(state.lastRefreshAt) : "暂无");
      text(
        "last-result",
        latest ? labels[latest.status] || latest.status : "暂无记录",
      );
      text(
        "last-run-date",
        latest ? date(latest.startedAt || latest.finishedAt) : "等待首次执行",
      );
      $("pause").querySelector("span").textContent =
        binding.status === "paused" ? "恢复任务" : "暂停任务";
      const version = JSON.stringify([
        binding.id,
        binding.label,
        binding.scheduleTime,
        binding.doShare,
        binding.notifications,
      ]);
      if (version !== settingsVersion && !settingsDirty) {
        $("settings-window").value = hourWindow(Number(binding.scheduleTime.slice(0, 2)));
        $("settings-share").checked = binding.doShare;
        let selectedPushChannel = null;
        ["bark", "serverchan"].forEach(channel => {
          const config = binding.notifications?.[channel];
          const enabled = !!config?.enabled && !selectedPushChannel;
          if (enabled) selectedPushChannel = channel;
          $(`${channel}-enabled`).checked = enabled;
          $(`${channel}-key`).placeholder = config?.configured ? "已保存，留空保持不变" : channel === "bark" ? "未配置" : "SCT 或 sctp 开头";
        });
        $("push-none").checked = !selectedPushChannel;
        settingsVersion = version;
      }
    }
    runTable("recent-runs", state.runs.items.slice(0, 5));
    if (!historyItems.length) {
      runTable("all-runs", state.runs.items);
      historyCursor = state.runs.nextCursor;
    }
    show("more-runs", !!historyCursor);
    if (activePair !== proxy.pairUrl) {
      activePair = proxy.pairUrl;
      proxyConfirmed = false;
      selectedBindingStep = null;
      autoPrepareKey = null;
    }
    const stage = capture.stage;
    const events = capture.events || [];
    if (["idle", "waiting"].includes(stage)) autoPrepareKey = null;
    const flowStep = stage === "cleanup" ? 4
      : stage === "verified" && state.candidate ? 3
      : !proxy.running || !proxy.paired ? 0
      : proxyConfirmed || events.length || ["captured", "verified"].includes(stage) ? 2 : 1;
    const accessibleSteps = new Set([0]);
    if (proxy.running) [1, 2, 4].forEach(item => accessibleSteps.add(item));
    if (state.candidate) accessibleSteps.add(3);
    if (selectedBindingStep != null && !accessibleSteps.has(selectedBindingStep)) selectedBindingStep = null;
    const step = selectedBindingStep ?? flowStep;
    document.querySelectorAll("[data-binding-step]").forEach((button) => {
      const item = button.closest("li");
      const itemStep = Number(button.dataset.bindingStep);
      button.disabled = !accessibleSteps.has(itemStep);
      item.classList.toggle("active", itemStep === step);
      item.classList.toggle("done", itemStep < flowStep);
    });
    show("bind-start", !proxy.running && step === 0 && stage !== "cleanup");
    show("bind-back", !!binding && !proxy.running && stage !== "cleanup");
    show("bind-connect", proxy.running && step <= 2);
    show("pairing-step", step === 0);
    show("proxy-step", step === 1);
    show("capture-step", step === 2);
    show("capture-details", step === 2);
    show("capture-wait", stage === "waiting");
    show("capture-ready", ["captured", "verified"].includes(stage));
    show("bind-confirm", step === 3 && !!state.candidate);
    show("bind-cleanup", step === 4);
    show("save-complete", stage === "cleanup");
    show("unsaved-exit", step === 4 && stage !== "cleanup");
    show("disconnect-panel", proxy.running && step === 4);
    text("traffic-status", events.length ? `代理已收到请求 · 当前 ${events.length} 条记录` : "尚未收到代理请求");
    text("proxy-address", proxy.address);
    text("proxy-port", proxy.port);
    text("phone-paired", proxy.paired ? "手机已配对" : "等待手机扫码配对");
    const loginReady = stage === "captured" || stage === "verified" || events.some((event) => event.outcome === "captured");
    const profileReady = stage === "verified" || !!state.candidate;
    $("capture-login-state").className = loginReady ? "capture-state ready" : "capture-state";
    $("capture-login-state").innerHTML = `<i class="state-dot"></i>${loginReady ? "已抓到" : "等待识别"}`;
    $("capture-profile-state").className = profileReady ? "capture-state ready" : "capture-state";
    $("capture-profile-state").innerHTML = `<i class="state-dot"></i>${profileReady ? "已抓到" : "等待验证"}`;
    if (stage === "captured") {
      text(
        "auto-verify-status",
        $("upload-consent").checked
          ? autoPrepareKey === captureFingerprint()
            ? "个人信息验证未完成；取消后重新勾选可重试。"
            : "已同意上传，正在准备静默验证。"
          : "勾选后会静默验证个人信息，无需再次点击。",
      );
      queueMicrotask(silentlyPrepareCapture);
    } else if (stage === "verified") {
      text("auto-verify-status", "个人信息验证完成，可以进入下一步。");
    }
    if (step === 0 && proxy.pairUrl && qrPair !== proxy.pairUrl) {
      const requestedPair = proxy.pairUrl;
      qrPair = requestedPair;
      fetch("/api/capture/qr", {
        headers: { Authorization: `Bearer ${token}` },
      })
        .then((response) => {
          if (!response.ok) throw new Error("二维码加载失败，请重新连接手机");
          return response.blob();
        })
        .then((blob) => {
          if (view !== "bind" || (selectedBindingStep ?? flowStep) !== 0 || state?.proxy?.pairUrl !== requestedPair) return;
          if (qrUrl) URL.revokeObjectURL(qrUrl);
          qrUrl = URL.createObjectURL(blob);
          $("pair-qr").src = qrUrl;
        })
        .catch((error) => {
          if (view === "bind" && (selectedBindingStep ?? flowStep) === 0 && state?.proxy?.pairUrl === requestedPair)
            notice(error.message);
        });
    }
    text(
      "phone-instructions-title",
      platform === "IOS" ? "iPhone 证书设置" : "安卓证书设置",
    );
    text(
      "phone-instructions",
      platform === "IOS"
        ? "下载后，在“设置 → 通用 → VPN 与设备管理”安装描述文件，再到“关于本机 → 证书信任设置”开启完全信任。"
        : "在“设置 → 安全 → 加密与凭据”中安装 CA 证书。不同机型的入口名称可能不同。",
    );
    if (state.candidate) {
      text("preview-points", state.candidate.preview.verified ? "已通过" : "待重新验证");
      text(
        "preview-sign",
        "云端",
      );
      text(
        "candidate-expiry",
        `请在 ${date(state.candidate.expiresAt)} 前确认保存。超时可重新验证；这不是账号登录态的有效期。`,
      );
    }
    applyCapabilities();
    updatePushChoice();
    icons();
  }
  document
    .querySelectorAll("[data-view]")
    .forEach((button) =>
      button.addEventListener("click", () => navigate(button.dataset.view)),
    );
  document
    .querySelectorAll("[data-go-bind]")
    .forEach((button) =>
      button.addEventListener("click", () => navigate("bind")),
    );
  document
    .querySelectorAll("[data-go-history]")
    .forEach((button) =>
      button.addEventListener("click", () => navigate("history")),
    );
  $("bind-back").addEventListener("click", () => navigate("overview"));
  document.querySelectorAll("[data-platform]").forEach((button) =>
    button.addEventListener("click", () => {
      platform = button.dataset.platform;
      document
        .querySelectorAll("[data-platform]")
        .forEach((item) => item.classList.toggle("selected", item === button));
      render();
    }),
  );
  function identityType(mode) {
    identityMode = mode;
    document
      .querySelectorAll("[data-identity]")
      .forEach((item) =>
        item.classList.toggle("selected", item.dataset.identity === mode),
      );
    text("identity-label", mode === "claim" ? "邀请码" : "恢复码或管理员恢复链接");
    $("identity-code").placeholder =
      mode === "claim" ? "粘贴管理员发来的邀请码" : "输入恢复码，或粘贴管理员恢复链接";
    text("identity-submit", mode === "claim" ? "领取并连接" : "恢复账号");
    text("identity-help", mode === "claim" ? "邀请码只用于本次领取；旧版完整领取链接也可以继续使用。" : "恢复码永久有效但使用后会轮换；管理员恢复链接 30 分钟内有效且只能兑换一次。");
  }
  document
    .querySelectorAll("[data-identity]")
    .forEach((button) =>
      button.addEventListener("click", () =>
        identityType(button.dataset.identity),
      ),
    );
  $("identity-form").addEventListener("submit", (event) => {
    event.preventDefault();
    perform(async () => {
      const result = await api(
        identityMode === "claim" ? "/api/claim" : "/api/recover",
        {
          [identityMode === "claim" ? "claimCode" : "recoveryCode"]:
            $("identity-code").value.trim(),
        },
      );
      $("identity-code").value = "";
      forceRecover = !result.saved;
      recovery = result.recoveryCode;
      recoverySaved = result.saved;
      text("recovery-value", recovery);
      $("recovery-saved").checked = false;
      show("recovery-warning", !result.saved);
      $("recovery-dialog").showModal();
      if (result.saved) await api("/api/refresh", {});
    }, null, event.submitter, identityMode === "claim" ? "领取中" : "恢复中");
  });
  $("recovery-dialog").addEventListener("cancel", (event) =>
    event.preventDefault(),
  );
  $("close-recovery").addEventListener("click", () => {
    if (!$("recovery-saved").checked) {
      notice("请先保存恢复码。");
      return;
    }
    $("recovery-dialog").close();
    recovery = null;
    text("recovery-value", "");
    if (!recoverySaved) identityType("recover");
    else navigate("bind");
  });
  $("copy-recovery").addEventListener("click", () => copy(recovery));
  $("download-recovery").addEventListener("click", () => {
    const blob = new Blob(
      [
        JSON.stringify(
          { application: "DailyTaskHelper", recoveryCode: recovery },
          null,
          2,
        ),
      ],
      { type: "application/json" },
    );
    const url = URL.createObjectURL(blob),
      anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "每日任务助手-恢复码.json";
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  async function copy(value) {
    try {
      await navigator.clipboard.writeText(value);
      notice("已复制", true);
    } catch {
      notice("无法访问剪贴板，请手动选择并复制。");
    }
  }
  document
    .querySelectorAll("[data-copy]")
    .forEach((button) =>
      button.addEventListener("click", () =>
        copy($(button.dataset.copy).textContent),
      ),
    );
  $("capture-start").addEventListener("submit", (event) => {
    event.preventDefault();
    perform(async () => {
      await api("/api/capture/start", {
        address: $("network").value,
        platform,
      });
      $("upload-consent").checked = false;
      $("proxy-removed").checked = false;
      autoPrepareKey = null;
      notice("");
    }, null, event.submitter, "连接中");
  });
  $("proxy-next").addEventListener("click", () => {
    proxyConfirmed = true;
    selectedBindingStep = 2;
    render();
  });
  document.querySelectorAll("[data-binding-step]").forEach((button) =>
    button.addEventListener("click", () => {
      selectedBindingStep = Number(button.dataset.bindingStep);
      render();
    }),
  );
  $("upload-consent").addEventListener("change", () => {
    if (!$("upload-consent").checked) {
      autoPrepareKey = null;
      text("auto-verify-status", "勾选后会静默验证个人信息，无需再次点击。");
      return;
    }
    silentlyPrepareCapture();
  });
  function chosenWindow(prefix) {
    return $(`${prefix}-window`).value;
  }
  function hourWindow(hour) {
    hour = Math.floor(hour / 2) * 2;
    return `${String(hour).padStart(2, "0")}:00-${String((hour + 2) % 24).padStart(2, "0")}:00`;
  }
  function hourLabel(value) {
    const hour = Number(value.slice(0, 2));
    return `${hour}-${hour + 2} 点`;
  }
  $("activate-form").addEventListener("submit", (event) => {
    event.preventDefault();
    perform(async () => {
      if (!selectedSlotAvailable("bind")) throw new Error("请选择仍有名额的执行区间，或刷新云端状态。");
      await api("/api/candidates/activate", {
        label: state.candidate.preview.displayName || "账号用户",
        scheduleTime: chosenWindow("bind"),
        doShare: $("bind-share").checked,
      });
      notice("");
    }, null, event.submitter, "保存中");
  });
  $("stop-proxy").addEventListener("click", (event) =>
    perform(async () => {
      if (!$("proxy-removed").checked)
        throw new Error("请先在手机上关闭 Wi-Fi 代理。");
      await api("/api/capture/stop", { proxyRemoved: true });
      qrPair = null;
      if (state.binding) navigate("overview");
    }, "手机连接已断开，现在可以退出助手。", event.currentTarget, "断开中"),
  );
  $("refresh").addEventListener("click", (event) =>
    perform(async () => {
      historyItems = [];
      await api("/api/refresh", {});
    }, "云端状态已更新", event.currentTarget, "刷新中"),
  );
  [$("run-now"), $("cleanup-run-now")].forEach((button) => button.addEventListener("click", (event) =>
    perform(async () => {
      notice("正在执行签到，请稍候。");
      const result = await api("/api/binding/run", {});
      await api("/api/refresh", {});
      const completed = result.status === "completed";
      notice(completed ? "今日签到已完成。" : result.message || `签到任务：${labels[result.status] || result.status}`, completed);
    }, null, event.currentTarget, "签到中"),
  ));
  $("pause").addEventListener("click", (event) =>
    perform(
      () =>
        api("/api/binding/settings", {
          status: state.binding.status === "paused" ? "active" : "paused",
        }),
      "任务状态已更新",
      event.currentTarget,
      state.binding.status === "paused" ? "恢复中" : "暂停中",
    ),
  );
  $("settings-form").addEventListener("input", () => { settingsDirty = true; });
  $("push-form").addEventListener("input", () => { settingsDirty = true; });
  document.querySelectorAll('input[name="push-channel"]').forEach((input) => input.addEventListener("change", () => {
    settingsDirty = true;
    updatePushChoice();
  }));
  ["bind", "settings"].forEach(prefix => $(`${prefix}-window`).addEventListener("change", applyCapabilities));
  $("settings-form").addEventListener("submit", (event) => {
    event.preventDefault();
    perform(
      async () => {
        if (!selectedSlotAvailable("settings")) throw new Error("请选择仍有名额的执行区间，或刷新云端状态。");
        await api("/api/binding/settings", {
          scheduleTime: chosenWindow("settings"),
          doShare: $("settings-share").checked,
        });
        settingsDirty = false;
        settingsVersion = "";
      },
      "设置已保存",
      event.submitter,
      "保存中",
    );
  });
  $("push-form").addEventListener("submit", (event) => {
    event.preventDefault();
    perform(async () => {
      const selected = document.querySelector('input[name="push-channel"]:checked')?.value || "none";
      const notifications = {};
      ["bark", "serverchan"].forEach(channel => {
        const enabled = selected === channel;
        const key = $(`${channel}-key`).value.trim();
        if (enabled && !key && !state.binding.notifications?.[channel]?.configured)
          throw new Error(`请填写 ${channel === "bark" ? "Bark Device Key" : "Server 酱 SendKey"}。`);
        notifications[channel] = {enabled, ...(key ? {key} : {})};
      });
      await api("/api/binding/settings", {notifications});
      if (selected !== "none") await api("/api/binding/notification-test", {});
      $("bark-key").value = $("serverchan-key").value = "";
      settingsDirty = false;
      settingsVersion = "";
    }, document.querySelector('input[name="push-channel"]:checked')?.value === "none" ? "推送设置已保存" : "推送设置已保存，测试消息已发送", event.submitter, "保存并测试");
  });
  ["bark", "serverchan"].forEach(channel => {
    $(`${channel}-clear`).addEventListener("click", (event) => perform(async () => {
      await api("/api/binding/settings", {notifications:{[channel]:{clear:true}}});
      $(`${channel}-key`).value = "";
      $(`${channel}-enabled`).checked = false;
      $("push-none").checked = true;
      updatePushChoice();
      $(`${channel}-key`).placeholder = channel === "bark" ? "未配置" : "SCT 或 sctp 开头";
    }, "推送配置已清除", event.currentTarget, "清除中"));
  });
  $("show-recover").addEventListener("click", () => {
    forceRecover = true;
    identityType("recover");
    show("identity-panel", true);
    $("identity-code").focus();
  });
  function confirmDelete() {
    return new Promise((resolve) => {
      confirmResolve = resolve;
      text("confirm-title", "解除账号绑定？");
      text("confirm-message", "云端登录状态将被删除，每日任务会停止。");
      $("confirm-dialog").showModal();
    });
  }
  $("binding-settings").addEventListener("click", () => $("binding-settings-dialog").showModal());
  $("binding-settings-cancel").addEventListener("click", () => $("binding-settings-dialog").close());
  $("binding-replace").addEventListener("click", (event) => {
    perform(async () => {
      await api("/api/capture/reset", {});
      resetBindingFlow();
      $("binding-settings-dialog").close();
      view = "bind";
      notice("");
    }, null, event.currentTarget, "准备中");
  });
  $("binding-unbind").addEventListener("click", async () => {
    $("binding-settings-dialog").close();
    if (await confirmDelete()) {
      perform(async () => {
        await api("/api/binding/delete", { confirmed: true });
        historyItems = [];
        navigate("overview");
      }, "已解除绑定", $("binding-unbind"), "解绑中");
    }
  });
  $("confirm-cancel").addEventListener("click", () => {
    $("confirm-dialog").close();
    confirmResolve?.(false);
  });
  $("confirm-accept").addEventListener("click", () => {
    $("confirm-dialog").close();
    confirmResolve?.(true);
  });
  $("confirm-dialog").addEventListener("cancel", () => confirmResolve?.(false));
  $("more-runs").addEventListener("click", (event) =>
    perform(async () => {
      const result = await api("/api/history", { cursor: historyCursor });
      if (!historyItems.length) historyItems = [...state.runs.items];
      historyItems.push(...result.items);
      historyCursor = result.nextCursor;
      runTable("all-runs", historyItems);
    }, null, event.currentTarget, "加载中"),
  );
  $("quit").addEventListener("click", async () => {
    if (state?.proxy.running) {
      navigate("bind");
      notice("退出前，请先关闭手机代理并断开手机连接。");
      return;
    }
    try {
      await api("/api/quit", {});
      notice("助手已退出，可以关闭此页面。", true);
      clearInterval(pollTimer);
      document
        .querySelectorAll("button")
        .forEach((button) => (button.disabled = true));
    } catch (error) {
      notice(error.message);
    }
  });
  let pollTimer;
  async function poll() {
    if (busy || document.hidden) return;
    try {
      const next = await api("/api/status");
      if (JSON.stringify(next) !== JSON.stringify(state)) {
        state = next;
        render();
      }
    } catch (error) {
      notice(error.message);
    }
  }
  async function start() {
    identityType(identityMode);
    ["bind-window", "settings-window"].forEach((id) => {
      $(id).replaceChildren(...Array.from({ length: 12 }, (_, index) => {
        const hour = index * 2;
        const option = document.createElement("option");
        option.value = hourWindow(hour);
        option.textContent = hourLabel(option.value);
        return option;
      }));
      $(id).value = hourWindow(8);
    });
    icons();
    if (!token) {
      notice("请重新双击打开每日任务助手，以恢复本机连接。");
      return;
    }
    await poll();
    try {
      const networks = await api("/api/networks");
      $("network").replaceChildren();
      networks.forEach((item) => {
        const option = document.createElement("option");
        option.value = item.address;
        option.textContent = `${item.address} · ${item.name}`;
        $("network").append(option);
      });
      if (!networks.length) {
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "未找到局域网，请连接 Wi-Fi";
        $("network").append(option);
      }
    } catch (error) {
      notice(error.message);
    }
    await perform(() => api("/api/refresh", {}));
    if (state?.proxy.running) navigate("bind");
    pollTimer = setInterval(() => {
      if (state?.proxy.running) poll();
    }, 2000);
  }
  start();
})();
