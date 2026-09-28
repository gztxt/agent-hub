/** 资源监控页（v0.13.52）——列出运行中 Agent 的进程资源，支持 Kill。
 *
 *  v0.13.54（2026-09-29 报障「资源页面还是无法加载」的真身）：删掉原第 5 行
 *  `if (resLoaded && !force) return;`。go()（01 分片）写的是
 *  `if (page === 'resources' && !resLoaded) { resLoaded = true; loadResources(); }`
 *  —— **先置位、后调用**，所以首次进页这道内部闸门必然命中，函数直接空返回：
 *  既不发请求也不写 hint，页面就永远停在「加载中…」，而且**控制台零报错**
 *  （没抛异常，什么都没发生）。probe 实测量到的正是：page_on=true、
 *  resLoaded=true、cards=0、hint=""。
 *  同型的 lpLoaded / ghLoaded 两个页面没炸，是因为 loadLocalProjects /
 *  loadGithubRepos 内部**没有**这道闸门 —— 「进页只由 go() 一处把关」
 *  是本仓既定纪律，资源页是唯一一个在加载函数里又关了一道的。 */
var resLoaded = false;

async function loadResources(force = false) {
    const listEl = document.getElementById("resList");
    const hintEl = document.getElementById("resHint");
    const summaryEl = document.getElementById("resSummary");
    if (!listEl) return;

    hintEl && (hintEl.textContent = "采样中…");
    listEl.innerHTML = '<div class="hint" style="padding:10px">采样中…</div>';

    try {
        const resp = await api("/api/resources");
        if (!resp.ok) throw new Error(resp.error || "请求失败");
        renderResources(resp);
        hintEl && (hintEl.textContent = "共 " + resp.total_agents + " 个 Agent · 总 CPU " + resp.total_cpu + "% · 总内存 " + resp.total_rss_mb + " MB");
        if (summaryEl) summaryEl.textContent = "总计：" + resp.total_agents + " 个 Agent · CPU " + resp.total_cpu + "% · 内存 " + resp.total_rss_mb + " MB";
        resLoaded = true;
    } catch (e) {
        console.error("[Resources] load failed:", e);
        hintEl && (hintEl.textContent = "加载失败：" + e.message);
        listEl.innerHTML = '<div class="hint" style="padding:10px;color:var(--danger)">加载失败：' + e.message + '</div>';
    }
}

function renderResources(data) {
    const listEl = document.getElementById("resList");
    if (!listEl) return;

    if (!data.agents || data.agents.length === 0) {
        listEl.innerHTML = '<div class="hint" style="padding:20px;text-align:center">当前没有运行中的 Agent 进程</div>';
        return;
    }

    let html = "";
    for (const agent of data.agents) {
        const agentId = agent.agent_id;
        const agentName = agent.agent_name;
        const kind = agent.kind;
        const summary = agent.summary;
        const processes = agent.processes;

        // 根据 kind 给不同颜色标记
        const kindBadge = {
            agent: '<span class="s-badge running"></span>',
            gateway: '<span class="s-badge" style="background:var(--accent);color:#fff">网关</span>',
            service: '<span class="s-badge" style="background:var(--primary);color:#fff">服务</span>',
            tool: '<span class="s-badge" style="background:#6b7280;color:#fff">工具</span>',
            memory: '<span class="s-badge" style="background:#8b5cf6;color:#fff">记忆</span>'
        }[kind] || '<span class="s-badge"></span>';

        html +=
        '<div class="agent-card" data-agent="' + agentId + '" style="border:1px solid var(--divider);border-radius:8px;margin-bottom:8px;background:var(--card-bg);overflow:hidden">' +
            '<div class="agent-header" style="display:flex;align-items:center;gap:8px;padding:10px 12px;background:var(--panel-bg);cursor:pointer;border-bottom:1px solid var(--divider)" onclick="toggleAgentProcs(\'' + agentId + '\')">' +
                kindBadge +
                '<span class="agent-name" style="flex:1;font-weight:500">' + escapeHtml(agentName) + '</span>' +
                '<span class="agent-meta" style="font-size:12px;color:var(--text-2)">' +
                    'CPU <b>' + summary.cpu_percent + '%</b> · 内存 <b>' + summary.rss_mb + ' MB</b> · <b>' + summary.count + '</b> 进程' +
                '</span>' +
                '<svg class="i xs chevron" aria-hidden="true" style="transition:transform .15s;flex:none"><use href="#i-chevron-down"/></svg>' +
            '</div>' +
            '<div class="agent-procs" id="procs-' + agentId + '" style="display:none;padding:8px 12px;max-height:300px;overflow-y:auto">' +
                '<table style="width:100%;border-collapse:collapse;font-size:12px">' +
                    '<thead>' +
                        '<tr style="position:sticky;top:0;background:var(--panel-bg);z-index:1">' +
                            '<th style="text-align:left;padding:4px 8px;border-bottom:1px solid var(--divider)">PID</th>' +
                            '<th style="text-align:left;padding:4px 8px;border-bottom:1px solid var(--divider)">CPU%</th>' +
                            '<th style="text-align:left;padding:4px 8px;border-bottom:1px solid var(--divider)">内存</th>' +
                            '<th style="text-align:left;padding:4px 8px;border-bottom:1px solid var(--divider)">命令行</th>' +
                            '<th style="text-align:center;padding:4px 8px;border-bottom:1px solid var(--divider);width:80px">操作</th>' +
                        '</tr>' +
                    '</thead>' +
                    '<tbody>';

        for (const proc of processes) {
            html +=
                        '<tr>' +
                            '<td style="padding:4px 8px;border-bottom:1px solid var(--divider)">' + proc.pid + '</td>' +
                            '<td style="padding:4px 8px;border-bottom:1px solid var(--divider)">' + proc.cpu_percent + '%</td>' +
                            '<td style="padding:4px 8px;border-bottom:1px solid var(--divider)">' + proc.rss_mb + ' MB</td>' +
                            '<td style="padding:4px 8px;border-bottom:1px solid var(--divider);max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="' + escapeHtml(proc.cmdline) + '">' + escapeHtml(proc.cmdline) + '</td>' +
                            '<td style="padding:4px 8px;border-bottom:1px solid var(--divider);text-align:center">' +
                                '<button class="btn xs danger" onclick="event.stopPropagation();killProc(\'' + agentId + '\', ' + proc.pid + ', \'SIGTERM\')" title="优雅结束 (SIGTERM)">结束</button>' +
                                '<button class="btn xs danger" style="margin-left:4px" onclick="event.stopPropagation();killProc(\'' + agentId + '\', ' + proc.pid + ', \'SIGKILL\')" title="强制结束 (SIGKILL)">强杀</button>' +
                            '</td>' +
                        '</tr>';
        }

        html +=
                    '</tbody>' +
                '</table>' +
            '</div>' +
        '</div>';
    }
    listEl.innerHTML = html;
}

function toggleAgentProcs(agentId) {
    const procEl = document.getElementById("procs-" + agentId);
    const chevron = document.querySelector('[data-agent="' + agentId + '"] .chevron');
    if (!procEl) return;
    const isHidden = procEl.style.display === "none";
    procEl.style.display = isHidden ? "block" : "none";
    if (chevron) chevron.style.transform = isHidden ? "rotate(180deg)" : "";
}

async function killProc(agentId, pid, signal) {
    if (!confirm("确定要 " + (signal === "SIGTERM" ? "结束" : "强制结束") + " 进程 PID " + pid + " 吗？")) return;

    try {
        const resp = await api("/api/resources/kill", {
            method: "POST",
            body: JSON.stringify({ agent_id: agentId, pid: pid, signal_name: signal })
        });
        if (!resp.ok) throw new Error(resp.error || "Kill 失败");
        alert((signal === "SIGTERM" ? "结束" : "强杀") + " 成功");
        loadResources(true); // 刷新
    } catch (e) {
        console.error("[Resources] kill failed:", e);
        alert("操作失败：" + e.message);
    }
}

function escapeHtml(s) {
    return (s || "").replace(/&/g, "\u0026amp;").replace(/</g, "\u0026lt;").replace(/>/g, "\u0026gt;").replace(/"/g, "\u0026quot;").replace(/'/g, "\u0026#39;");
}

// 供外部调用（如从其它页面跳转）
window.loadResources = loadResources;