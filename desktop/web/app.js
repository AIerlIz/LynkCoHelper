"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const token = location.hash.slice(1);
  history.replaceState(null, "", location.pathname);
  let state = null,
    view = "overview",
    identityMode = "claim",
    platform = "IOS",
    busy = false;
  let proxyConfirmed = false, exitCapture = false, activePair = null;
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
  function notice(message, good = false) {
    text("notice", message);
    $("notice").classList.toggle("good", good);
    show("notice", !!message);
  }
  async function api(path, body) {
    if (!token) throw new Error("页面已失去本机连接，请重新双击打开领克助手。");
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
  async function perform(operation, message) {
    if (busy) return;
    busy = true;
    const buttons = [...document.querySelectorAll("button:not([disabled])")];
    buttons.forEach((button) => (button.disabled = true));
    try {
      await operation();
      if (message) notice(message, true);
      state = await api("/api/status");
      render();
    } catch (error) {
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
      buttons.forEach((button) => (button.disabled = false));
      applyCapabilities();
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
    ["执行时间", "结果", "签到 / 分享", "奖励"].forEach((label) => {
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
      const rewards = run.rewards || {};
      const lines = [`积分：${run.pointsBefore ?? "暂无"} → ${run.pointsAfter ?? "暂无"}`];
      if (rewards.signEnergy != null) lines.push(`签到能量体：+${rewards.signEnergy}`);
      if (run.shareStatus === "success") lines.push(`分享后能量体：${rewards.sharePointsBefore ?? "暂无"} → ${rewards.sharePointsAfter ?? "暂无"}`);
      else lines.push(`分享：${labels[run.shareStatus] || "暂无"}`);
      if (rewards.cardsBefore != null && rewards.cardsAfter != null) {
        const change = rewards.cardsAfter - rewards.cardsBefore;
        lines.push(`签到卡：${rewards.cardsBefore} → ${rewards.cardsAfter}${change > 0 ? `（净增 ${change} 张）` : ""}`);
      } else lines.push("签到卡奖励：暂无数据");
      lines.forEach((value) => { const line = document.createElement("div"); line.textContent = value; rewardCell.append(line); });
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
    $("delete-binding").disabled = !binding;
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
    ["bark", "serverchan"].forEach(channel => {
      const active = selected === channel;
      $(`${channel}-key`).disabled = !active || !state?.binding;
      $(`${channel}-clear`).disabled = !state?.binding;
    });
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
      select.value = options.some(option => option.value === previous && !option.disabled) ? previous : "";
      text(`${prefix}-quota-hint`, items ? "暂停任务仍保留名额，保存时以云端剩余名额为准。" : "暂时无法读取剩余名额，请刷新云端状态后再保存。");
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
        name === view && state.hasIdentity,
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
      text("account-status", labels[binding.status] || binding.status);
      $("account-status").className =
        "badge " + (binding.status === "active" ? "" : "warning");
      text(
        "account-schedule",
        `每日 ${hourLabel(binding.scheduleTime)} · ${binding.doShare ? "签到与分享" : "每日签到"}`,
      );
      text(
        "next-run",
        binding.status === "active" ? date(binding.nextRunAt) : "--",
      );
      const latest = state.runs.items[0];
      text("points", latest?.pointsAfter ?? latest?.pointsBefore ?? "--");
      const inventory = binding.inventory;
      text("sign-cards", inventory?.cards != null ? `${inventory.cards} 张` : binding.inventoryError ? "查询失败" : "暂无");
      text("continue-days", inventory?.days != null ? `${inventory.days} 天` : binding.inventoryError ? "查询失败" : "暂无");
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
      exitCapture = false;
    }
    const stage = capture.stage;
    const events = capture.events || [];
    const step = stage === "cleanup" || exitCapture ? 4
      : stage === "verified" && state.candidate ? 3
      : !proxy.running || !proxy.paired ? 0
      : proxyConfirmed || events.length || ["captured", "verified"].includes(stage) ? 2 : 1;
    document.querySelectorAll("[data-step]").forEach((item) => {
      item.classList.toggle("active", Number(item.dataset.step) === step);
      item.classList.toggle("done", Number(item.dataset.step) < step);
    });
    show("bind-start", !proxy.running && stage !== "cleanup");
    show(
      "bind-connect",
      proxy.running && step < 3 && stage !== "verified",
    );
    show("pairing-step", step === 0);
    show("proxy-step", step === 1);
    show("capture-step", step === 2);
    show("capture-details", step === 2);
    show("capture-wait", stage === "waiting");
    show("capture-ready", stage === "captured");
    show("bind-confirm", step === 3);
    show("bind-cleanup", step === 4);
    show("save-complete", stage === "cleanup");
    show("unsaved-exit", step === 4 && stage !== "cleanup");
    show("disconnect-panel", proxy.running && step === 4);
    show("cancel-capture", proxy.running && step < 4);
    show("continue-capture", proxy.running && exitCapture && stage !== "cleanup");
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
    if (proxy.pairUrl && qrPair !== proxy.pairUrl) {
      qrPair = proxy.pairUrl;
      fetch("/api/capture/qr", {
        headers: { Authorization: `Bearer ${token}` },
      })
        .then((response) => {
          if (!response.ok) throw new Error("二维码加载失败，请重新连接手机");
          return response.blob();
        })
        .then((blob) => {
          if (qrUrl) URL.revokeObjectURL(qrUrl);
          qrUrl = URL.createObjectURL(blob);
          $("pair-qr").src = qrUrl;
        })
        .catch((error) => notice(error.message));
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
    text("identity-label", mode === "claim" ? "领取链接" : "恢复码");
    $("identity-code").placeholder =
      mode === "claim" ? "粘贴管理员发来的领取链接" : "输入已保存的恢复码";
    text("identity-submit", mode === "claim" ? "领取并连接" : "恢复账号");
    text("identity-help", mode === "claim" ? "链接只用于本次领取，不会保存到电脑。" : "恢复码用于换电脑或管理凭证失效时恢复助手权限。");
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
          [identityMode === "claim" ? "claimUrl" : "recoveryCode"]:
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
    });
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
          { application: "LynkCoHelper", recoveryCode: recovery },
          null,
          2,
        ),
      ],
      { type: "application/json" },
    );
    const url = URL.createObjectURL(blob),
      anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "领克助手-恢复码.json";
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
      notice("");
    });
  });
  $("proxy-next").addEventListener("click", () => {
    proxyConfirmed = true;
    render();
  });
  $("cancel-capture").addEventListener("click", () => {
    exitCapture = true;
    render();
  });
  $("continue-capture").addEventListener("click", () => {
    exitCapture = false;
    render();
  });
  $("prepare").addEventListener("click", () =>
    perform(async () => {
      if (!$("upload-consent").checked)
        throw new Error("请先确认上传登录状态。");
      await api("/api/candidates/prepare", { consent: true });
      notice("");
    }),
  );
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
        label: state.candidate.preview.displayName || "领克账号",
        scheduleTime: chosenWindow("bind"),
        doShare: $("bind-share").checked,
      });
      notice("");
    });
  });
  $("stop-proxy").addEventListener("click", () =>
    perform(async () => {
      if (!$("proxy-removed").checked)
        throw new Error("请先在手机上关闭 Wi-Fi 代理。");
      await api("/api/capture/stop", { proxyRemoved: true });
      qrPair = null;
      if (state.binding) navigate("overview");
    }, "手机连接已断开，现在可以退出助手。"),
  );
  $("refresh").addEventListener("click", () =>
    perform(async () => {
      historyItems = [];
      await api("/api/refresh", {});
    }, "云端状态已更新"),
  );
  [$("run-now"), $("cleanup-run-now")].forEach((button) => button.addEventListener("click", () =>
    perform(async () => {
      notice("正在执行签到，请稍候。");
      const result = await api("/api/binding/run", {});
      await api("/api/refresh", {});
      const completed = result.status === "completed";
      notice(completed ? "今日签到已完成。" : result.message || `签到任务：${labels[result.status] || result.status}`, completed);
    }),
  ));
  $("pause").addEventListener("click", () =>
    perform(
      () =>
        api("/api/binding/settings", {
          status: state.binding.status === "paused" ? "active" : "paused",
        }),
      "任务状态已更新",
    ),
  );
  $("settings-form").addEventListener("input", () => { settingsDirty = true; });
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
        const notifications = {};
        const enabledChannels = ["bark", "serverchan"].filter(channel => $(`${channel}-enabled`).checked);
        if (enabledChannels.length > 1) throw new Error("Bark 和 Server 酱只能选择一个推送渠道。");
        ["bark", "serverchan"].forEach(channel => {
          const enabled = $(`${channel}-enabled`).checked;
          const key = $(`${channel}-key`).value.trim();
          if (enabled && !key && !state.binding.notifications?.[channel]?.configured)
            throw new Error(`请填写 ${channel === "bark" ? "Bark Device Key" : "Server 酱 SendKey"}。`);
          notifications[channel] = {enabled, ...(key ? {key} : {})};
        });
        await api("/api/binding/settings", {
          scheduleTime: chosenWindow("settings"),
          doShare: $("settings-share").checked,
          notifications,
        });
        $("bark-key").value = $("serverchan-key").value = "";
        settingsDirty = false;
        settingsVersion = "";
      },
      "设置已保存",
    );
  });
  ["bark", "serverchan"].forEach(channel => {
    $(`${channel}-clear`).addEventListener("click", () => perform(async () => {
      await api("/api/binding/settings", {notifications:{[channel]:{clear:true}}});
      $(`${channel}-key`).value = "";
      $(`${channel}-enabled`).checked = false;
      $("push-none").checked = true;
      updatePushChoice();
      $(`${channel}-key`).placeholder = channel === "bark" ? "未配置" : "SCT 或 sctp 开头";
    }, "推送配置已清除"));
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
      text("confirm-title", "解除领克账号绑定？");
      text("confirm-message", "云端登录状态将被删除，每日任务会停止。");
      $("confirm-dialog").showModal();
    });
  }
  $("binding-settings").addEventListener("click", () => $("binding-settings-dialog").showModal());
  $("binding-settings-cancel").addEventListener("click", () => $("binding-settings-dialog").close());
  $("binding-replace").addEventListener("click", () => {
    $("binding-settings-dialog").close();
    navigate("bind");
  });
  $("binding-unbind").addEventListener("click", async () => {
    $("binding-settings-dialog").close();
    if (await confirmDelete()) {
      perform(async () => {
        await api("/api/binding/delete", { confirmed: true });
        historyItems = [];
        navigate("overview");
      }, "已解除绑定");
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
  $("more-runs").addEventListener("click", () =>
    perform(async () => {
      const result = await api("/api/history", { cursor: historyCursor });
      if (!historyItems.length) historyItems = [...state.runs.items];
      historyItems.push(...result.items);
      historyCursor = result.nextCursor;
      runTable("all-runs", historyItems);
    }),
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
      notice("请重新双击打开领克助手，以恢复本机连接。");
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
    pollTimer = setInterval(() => {
      if (state?.proxy.running) poll();
    }, 2000);
  }
  start();
})();
