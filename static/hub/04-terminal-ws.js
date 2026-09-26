function termAuthOk() { termAuthWarned = false; }

async function termRefreshList() {
  try {
    const d = await api('/api/term/sessions', { headers: termHeaders() });
    // 只显示当前选中 agent 的会话，避免显示其他 agent 的终端
    const live = (d.sessions || []).filter(s => s.alive && s.agent_id === chatPick);
    termAuthOk();   // 拿到清单 = 口令可用，重置去重位
    const el = $('termSessList');
    if (!el) return live;
    // 空清单就什么都不画（用户 09-20：拿掉「暂无活会话」占位字）
    el.innerHTML = live.map(termChipHtml).join('');
    // 当前正看的会话已被服务端回收（进程退出/超时）→ 清绑并提示，避免对着幽灵 sid 重连
    if (termSid && !live.some(s => s.id === termSid)) {
      termDetach();
      if (term) term.write('\r\n\x1b[90m[当前会话已结束——点「新会话」重新开始]\x1b[0m');
    }
    return live;
  } catch (e) {
    // 改前 GET 不鉴权，走不到这条路；P1-7 之后这里是 401 的唯一出口。
    // 全吐成 null 就是「静默不可用」—— 这是用户自己能修的一种失败，必须上屏。
    termAuthWarn(e);
    return null;
  }
  // 出错回 null（= 没拿到清单），与「清单为空」分开：空清单才清屏给提示，
  // 取不到清单不能把好好一块画面抹掉
}

function termAutoAttach(live) {
  // 只接本实体的活会话；没有就清屏给提示（确保不残留上一实体画面）
  // live = termRefreshList() 已经拿到的清单，别再为同一件事发第二个 GET
  const got = live ? Promise.resolve(live)
    : api('/api/term/sessions', { headers: termHeaders() })
        .then(d => (d.sessions || []).filter(s => s.agent_id === chatPick && s.alive));
  got.then(mine => {
    if (mine.length) {
      const last = mine[mine.length - 1].id;
      /* 已经接在这条会话上就别拆线重连：重连 = 清屏 + 只回放 ring 尾巴，
         用户看到的「离开当前页再回来就白屏」正是这么来的。补一次重绘即可。 */
      if (termSid === last && termWs && termWs.readyState === 1) { termRepaint(); return; }
      termConnect(last, chatPick);   // 自动挂载：刻意不带 user:true，否则手机一进页面就弹键盘
      return;
    }
    // 确保 detached：切换实体时清除旧绑定，避免显示错误会话
    if (termSid && termSidAgent !== chatPick) termDetach();
    term.clear();
    const a = entityById(chatPick);
    term.write('\x1b[90m' + chatPick + ' · ' + (a?.name || chatPick) + ' 终端\x1b[0m\r\n');
    term.write('\x1b[90m提示：点「新会话」拉起 ' + (a?.name || chatPick) + ' 的原生终端\x1b[0m\r\n');
  }).catch(e => { termAuthWarn(e); });
  // 这条 .catch 不是装饰：termAutoAttach 会为拿不到清单而自己补发一次 GET，
  // 无 catch 就只剩控制台里一条没人认领的 rejection，用户侧表现为「什么都没发生」
}

/* termKill()（「销毁当前」整块按钮）已随 v0.10.1 外框统一拿掉；销毁只保留芯片上的 × = termKillOne。 */
async function termKillOne(sid) {
  // 乐观移除：先摘 DOM 芯片，用户即时看到"删掉了"，再与后端对账
  const el = $('termSessList');
  const chip = el && el.querySelector('.sess-item[data-sid="' + sid + '"]');
  if (chip) chip.remove();
  try {
    await api('/api/term/sessions/' + sid, { method: 'DELETE', headers: termHeaders() });
    toast('会话已销毁', 'ok');
    if (sid === termSid) termDetach();
    termRefreshList().then(list => termAutoAttach(list));   // 一次 GET：重绘芯片并接上剩下的会话
  } catch (e) {
    toast(e.message, 'err');
    termRefreshList();  // 404 等异常：以服务端真实状态重绘，DOM 不骗人
  }
}

async function openChatSession() {
  const box = $('chatMsgs');
  box.innerHTML = '';
  chatSessLoad();
  chatCwdLoad();
  const sid = lsGet(sessKey(chatPick));
  if (!sid) { box.innerHTML = '<div class="hint" style="margin:auto">开始新会话（' + escapeHtml(chatPick) + '）</div>'; return; }
  try {
    const d = await api('/api/sessions/' + encodeURIComponent(sid) + '/messages');
    for (const m of d.messages || []) {
      box.appendChild(renderChatMsg(m));
    }
  } catch (e) { lsRemove(sessKey(chatPick)); }
}

function renderChatMsg(m) {
  const el = document.createElement('div');
  el.className = 'msg ' + (m.role === 'user' ? 'user' : m.role === 'error' ? 'err' : 'assistant');
  el.textContent = m.content || '';
  return el;
}

async function chatSend() {
  const input = $('chatInput');
  const model = $('chatModel')?.value || '';
  const cwd = $('chatCwd')?.value || '';
  const msg = input.value.trim();
  if (!msg) return;
  input.value = '';
  const box = $('chatMsgs');
  const me = document.createElement('div');
  me.className = 'msg user'; me.textContent = msg;
  box.appendChild(me); box.scrollTop = box.scrollHeight;
  let sid = lsGet(sessKey(chatPick));
  if (!sid) { sid = Math.random().toString(36).slice(2, 14); lsSet(sessKey(chatPick), sid); }
  const busy = document.createElement('div');
  busy.className = 'msg assistant'; busy.textContent = '回复中…';
  box.appendChild(busy);
  const body = { message: msg, session_id: sid, model: model, cwd: cwd || null };
  // 统一走 /api/agents/{id}/chat
  try {
    const d = await api('/api/agents/' + encodeURIComponent(chatPick) + '/chat',
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    busy.className = 'msg ' + (d.success ? 'assistant' : 'err');
    let txt = (d.response || d.error || JSON.stringify(d.hint || d));
    if (d.model) txt += '\n· model: ' + d.model;
    if (d.usage) txt += '\n· tokens: in ' + (d.usage.prompt_tokens || d.usage.input_tokens || '-') + ' / out ' + (d.usage.completion_tokens || d.usage.output_tokens || '-');
    busy.textContent = txt;
    chatSessLoad();  // 刷新会话列表（title 等信息更新）
  } catch (e) { busy.className = 'msg err'; busy.textContent = '[错误] ' + e.message; }
  box.scrollTop = box.scrollHeight;
}

/* ── 模型选择器（动态拉取 CCR /v1/models）── */
let CHAT_MODELS = [];
async function loadChatModels(force) {
  const sel = $('chatModel');
  if (!sel) return;
  const cached = !force && CHAT_MODELS.length && (Date.now() - (CHAT_MODELS._ts || 0) < 60000);
  if (!cached) {
    try {
      const d = await api('/api/models');
      CHAT_MODELS = (d.models || []).map(m => ({ ...m, _groups: d.groups || {} }));
      CHAT_MODELS._ts = Date.now();
      CHAT_MODELS._src = d.source;
    } catch (e) {
      sel.innerHTML = '<option value="">模型加载失败</option>';
      return;
    }
  }
  // 当前 agent 默认 model（localStorage 记忆）
  const saved = lsGet('hub.model.' + chatPick) || '';
  // 按 vendor 分组
  const groups = (CHAT_MODELS._groups) || {};
  const groupedKeys = Object.keys(groups);
  let html = '<option value="">默认（网关路由）</option>';
  for (const gk of groupedKeys) {
    html += '<optgroup label="' + escapeHtml(gk) + '">';
    for (const mid of groups[gk]) {
      const m = CHAT_MODELS.find(x => x.id === mid);
      const label = m && m.name && m.name !== mid ? (mid + ' — ' + m.name) : mid;
      html += '<option value="' + escapeHtml(mid) + '"' + (mid === saved ? ' selected' : '') + '>' + escapeHtml(label) + '</option>';
    }
    html += '</optgroup>';
  }
  // 兜底：未分组的也展示
  const grouped = new Set(groupedKeys.flatMap(k => groups[k]));
  const leftovers = CHAT_MODELS.filter(m => !grouped.has(m.id));
  if (leftovers.length) {
    html += '<optgroup label="其他">';
    for (const m of leftovers) {
      html += '<option value="' + escapeHtml(m.id) + '"' + (m.id === saved ? ' selected' : '') + '>' + escapeHtml(m.name || m.id) + '</option>';
    }
    html += '</optgroup>';
  }
  sel.innerHTML = html;
}
$('chatModel')?.addEventListener?.('change', e => {
  lsSet('hub.model.' + chatPick, e.target.value);
});

/* ── 工作目录（CWD 选择器）── */
const DEFAULT_CWDS = ['/fs/1000/ftp/技术文档', '/home/gztxt', '/home/gztxt/agent-hub', '/vol1/1000/技术文档', '/tmp'];
function chatCwdLoad() {
  const sel = $('chatCwd');
  if (!sel) return;
  // 1) agent 画像默认 cwd（claude/jcode/hermes/bash）
  const a = entityById(chatPick);
  const profCwd = a && a.working_dir;
  // 2) localStorage 历史选择
  const saved = lsGet('hub.cwd.' + chatPick) || '';
  // 3) 合并去重
  const seen = new Set();
  const list = [];
  if (profCwd) { list.push({ v: profCwd, label: profCwd + ' ★画像' }); seen.add(profCwd); }
  if (saved && !seen.has(saved)) { list.push({ v: saved, label: saved + ' ★最近' }); seen.add(saved); }
  for (const d of DEFAULT_CWDS) {
    if (!seen.has(d)) { list.push({ v: d, label: d }); seen.add(d); }
  }
  sel.innerHTML = list.map(x => '<option value="' + escapeHtml(x.v) + '"' +
    (x.v === saved || (!saved && x.v === profCwd) ? ' selected' : '') + '>' + escapeHtml(x.label) + '</option>').join('');
}
function chatCwdCustom() {
  const cur = $('chatCwd')?.value || '';
  const v = prompt('自定义工作目录（绝对路径）', cur);
  if (v && v.trim()) {
    $('chatCwd').value = v.trim();
    lsSet('hub.cwd.' + chatPick, v.trim());
    toast('已设 cwd: ' + v.trim(), 'ok');
  }
}
$('chatCwd')?.addEventListener?.('change', e => {
  lsSet('hub.cwd.' + chatPick, e.target.value);
});

/* ── 会话管理 ── */
let CHAT_SESSIONS = [];
async function chatSessLoad() {
  const sel = $('chatSessList');
  if (!sel) return;
  const cur = lsGet(sessKey(chatPick)) || '';
  try {
    const d = await api('/api/sessions?agent_id=' + encodeURIComponent(chatPick) + '&limit=30');
    CHAT_SESSIONS = d.sessions || [];
    const opts = ['<option value="">当前会话</option>'];
    opts.push('<option value="__new__">新会话</option>');
    for (const s of CHAT_SESSIONS) {
      const title = (s.title || '未命名').slice(0, 24);
      const ts = (s.updated_at || '').slice(5, 16).replace('T', ' ');
      opts.push('<option value="' + escapeHtml(s.id) + '"' + (s.id === cur ? ' selected' : '') + '>' +
        escapeHtml(ts + ' · ' + title) + '</option>');
    }
    sel.innerHTML = opts.join('');
  } catch (e) { /* ignore */ }
}
function chatSessNew() {
  lsRemove(sessKey(chatPick));
  openChatSession();
  toast('已开新会话', 'ok');
}
async function chatSessRename() {
  const sid = lsGet(sessKey(chatPick));
  if (!sid) return toast('当前无会话', 'err');
  const cur = CHAT_SESSIONS.find(s => s.id === sid);
  const title = prompt('新标题', (cur && cur.title) || '');
  if (!title) return;
  try {
    await api('/api/sessions/' + encodeURIComponent(sid), { method: 'PATCH',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: title }) });
    chatSessLoad();
    toast('已重命名', 'ok');
  } catch (e) { toast(e.message, 'err'); }
}
$('chatSessList')?.addEventListener?.('change', async e => {
  const v = e.target.value;
  if (v === '__new__') { chatSessNew(); return; }
  if (!v) return;
  lsSet(sessKey(chatPick), v);
  openChatSession();
});
// T7：删除当前会话（后端 DELETE /api/sessions/{id} 连同消息一并清理）
async function chatSessDel() {
  const sid = lsGet(sessKey(chatPick));
  if (!sid) return toast('当前无会话', 'err');
  if (!confirm('删除当前会话 ' + sid.slice(0, 8) + ' 及其全部消息？不可恢复。')) return;
  try {
    await api('/api/sessions/' + encodeURIComponent(sid), { method: 'DELETE' });
    lsRemove(sessKey(chatPick));
    openChatSession();
    toast('会话已删除', 'ok');
  } catch (e) { toast(e.message, 'err'); }
}

/* applyChatMode 已内置：切换到 chat 模式时三联动刷新 */

/* ── 记忆中心 ─────────────────────────────────────── */

/* ── CloudCLI 项目直达（v0.13.29）──────────────────────────────────────
 * 用户诉求：「cloudcli 项目检索要完善、无法加载本机所有项目、精确显示项目名称、
 * 点击对应项目快速开始」。
 * 列表：GET /api/cloudcli/projects（直读 auth.db，与 cloudcli 服务活死解耦）；
 * 启动：POST /api/cloudcli/start {path} → {sessionId, url} → iframe 直达
 * /session/{id}（先 gotoChat('claude') 进 embed 模式再覆写 src——dataset 同步
 * 防 applyChatMode 重置；embed 顶栏地址行同步）。
 * 嵌入 iframe 的鉴权态由 cloudcli 自己的 localStorage 管（跨源但同浏览器持久，
 * 知识文档 50 号已证）；hub 不传 token 不越权。 */
async function loadCloudcliProjects() {
  const body = $('detailBody');
  if (!body) return;
  const holder = document.createElement('div');
  holder.id = 'ccProjects';
  holder.innerHTML = '<div class="hint" style="margin:10px 0 4px">CloudCLI 项目（本机全部）</div><div class="hint">加载中…</div>';
  body.appendChild(holder);
  try {
    const d = await api('/api/cloudcli/projects');
    if (!d.ok) {
      holder.innerHTML = '<div class="hint" style="margin:10px 0 4px">CloudCLI 项目</div>' +
        '<div class="hint" style="color:var(--st-error,var(--danger))">加载失败：' + escapeHtml(d.error || '?') + '</div>';
      return;
    }
    const rows = (d.projects || []).map(p =>
      '<div class="mem-item" style="gap:6px">' +
      '<p style="min-width:0"><b>' + escapeHtml(p.name) + '</b>' + (p.starred ? ' ★' : '') +
      (p.sessions ? ' <span class="hint">' + p.sessions + ' 会话</span>' : '') +
      '<br><span class="hint" style="font-family:var(--font-mono);font-size:var(--fs-xs)">' +
      escapeHtml(String(p.path).slice(0, 48)) +
      (p.last_activity ? ' · ' + String(p.last_activity).slice(5, 16).replace('T', ' ') : '') + '</span></p>' +
      '<button class="btn sm" style="align-self:center" onclick="cloudcliStart(\'' +
      escapeHtml(String(p.path).replace(/'/g, "\\'")) + '\')" title="在 CloudCLI 开始此项目的新会话">▶ 开始会话</button></div>').join('');
    holder.innerHTML = '<div class="hint" style="margin:10px 0 4px">CloudCLI 项目（' + (d.count || 0) + ' 个 · 点「开始会话」直达）</div>' +
      '<div class="tscroll" style="max-height:300px;overflow-y:auto">' + (rows || '<div class="hint">无项目</div>') + '</div>';
  } catch (e) {
    holder.innerHTML = '<div class="hint" style="margin:10px 0 4px">CloudCLI 项目</div>' +
      '<div class="hint" style="color:var(--st-error,var(--danger))">加载失败：' + escapeHtml(e.message || '') + '</div>';
  }
}

async function cloudcliStart(path) {
  toast('CloudCLI 创建会话中…');
  try {
    const d = await api('/api/cloudcli/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: path })
    });
    const full = 'http://127.0.0.1:3010' + (d.url || '');
    /* 先进 embed 模式（gotoChat 会触发 applyChatMode 重置 iframe src），
       再覆写 src 到 /session/{id}——dataset 必须同步，否则下次 applyChatMode
       会把它重置回实体 ui.url。地址行（embedUrlHint 的 span）同步显示。 */
    if (typeof gotoChat === 'function') gotoChat('claude');
    const f = $('embedFrame');
    if (f) {
      const url = lanUrl(full);
      f.dataset.src = url;
      f.src = url;
      const hintTxt = $('embedUrlHint') ? $('embedUrlHint').querySelector('span') : null;
      if (hintTxt) hintTxt.textContent = url;
    }
    closeOverlay('detailDrawer');
    toast('会话已创建：' + (d.session_name || d.sessionId), 'ok');
  } catch (e) { toast('创建会话失败：' + e.message, 'err'); }
}

/* ── v0.13.28 源标识与逐路徽标（记忆页/kb 页共用渲染器）────────────────
 * SRC_ABBR：源名缩写（结果卡 tag 位宽恒定，不因长源名换行）。
 * srcTag()：三变体 .tag.src-doc/.src-session/.src-index（明度区分，不彩虹——
 *   v0.9 设计系统「色相只出现在语义上」的约束；裸拼 `tag turbovec` 无 CSS 定义
 *   静默灰底的根因修复）。kb 路名短别名也入表（tdai/workspace/archived）。 */
const SRC_ABBR = {
  claude_mem: 'CM', claude_projects: 'CP', pi_sessions: 'PI', codex_sessions: 'CX',
  grok_memory: 'GK', hermes_memory: 'HM', workbuddy_memory: 'WB',
  workspace_files: 'WS', archived_sessions: 'AR',
  turbovec: 'TV', tdai_l1: 'TD', tdai: 'TD', local: 'L1'
};
function srcTag(srcName) {
  const key = String(srcName || '');
  const abbr = SRC_ABBR[key] || (key.slice(0, 2).toUpperCase() || '??');
  const cls = (key === 'turbovec' || key === 'local') ? 'src-index'
    : (key.indexOf('session') >= 0 || key === 'claude_mem') ? 'src-session'
    : 'src-doc';
  return '<span class="tag src-' + cls + '" title="' + escapeHtml(key) + '" style="align-self:flex-start">' +
    escapeHtml(abbr) + '</span>';
}
/* fedBadges：逐路健康徽标行（绿=ok 有命中 / 黄=ok 零命中 / 红=挂了点名+错误 tooltip）。
 * 记忆页与 kb 页两处同构消费——从 memFedSearch 内联抽出（DRY，两页口径归一）。 */
function fedBadges(d) {
  return (d.backends || []).map(b => {
    const col = b.ok ? (b.count ? 'var(--st-running)' : 'var(--st-installed)') : 'var(--st-error)';
    const nm = b.ok ? b.name : b.name + ' 挂了';
    return '<span style="display:inline-flex;align-items:center;gap:3px;margin-right:10px">' +
      '<span class="hdot" style="background:' + col + '"></span>' +
      '<span class="hint">' + escapeHtml(nm) + (b.ok && b.count != null ? ' ' + b.count : '') +
      (b.error ? ' <span title="' + escapeHtml(String(b.error).slice(0, 100)) + '">?</span>' : '') + '</span></span>';
  }).join('');
}

/* ── 联邦检索（v0.13.27 E 缺口）：6 路 RRF，逐路 backends 徽标（四态口径
 *   与资产面板一致：ok/空/降级/端点挂，不把「挂了」渲染成「没有」）。
 *   按需触发，进页不自动跑。 ─────────────────────────────────────────── */
async function memFedSearch() {
  const q = ($('memFedQ') ? $('memFedQ').value : '').trim();
  if (!q) { toast('输入检索词', 'err'); return; }
  const sources = Array.from($('memFedSrcs').selectedOptions).map(o => o.value).join(',');
  const hint = $('memFedHint');
  if (hint) hint.textContent = '检索中…';
  boxBusy('memFedResults', '联邦检索中…');
  const badges = $('memFedBadges');
  try {
    const d = await api('/api/memory/search?q=' + encodeURIComponent(q) + '&sources=' + encodeURIComponent(sources || 'local,tdai'));
    if (hint) hint.textContent = (d.count || 0) + ' 条 · ' + (d.took_ms || '?') + 'ms · ' + (d.engine || 'none');
    /* 逐路徽标：绿=ok 有命中 / 黄=ok 零命中 / 红=挂了（点名，不糊成绿） */
    if (badges) badges.innerHTML = fedBadges(d);
    renderQueryBar('memFedQueryBar', q, d.count || 0, 'memFedClear');
    $('memFedResults').innerHTML = (d.memories || []).map(m =>
      '<div class="mem-item"><span class="tag ' + (m.category || m.type || 'fact') + '" style="align-self:flex-start">' +
      escapeHtml(m.category || m.type || m.layer || '?') + '</span>' +
      '<p>' + escapeHtml(String(m.content || '').slice(0, 300)) +
      '<br><span class="hint">' + escapeHtml(m.source || m.id || '') + '</span></p></div>').join('') ||
      '<div class="hint">零命中。' + ((d.degraded || []).length ? '降级路：' + d.degraded.join(',') + '（清单不完整，不代表没资产）' : '全部路可用且零命中（真没有）') + '</div>';
  } catch (e) {
    if (hint) hint.textContent = '加载失败';
    boxFail('memFedResults', e, 'memFedSearch');
  }
}

/* v0.13.28 批3：检索回退。用户痛点「搜索完成后无法回退到记忆主界面」——
 * 检索结果覆写结果区后没有清空出口，只能刷新页面。设计三条：
 * ① QueryBar（当前检索 q · N 条 · 清空↺）由检索成功尾部渲染，失败态也挂（可撤销失败展示）；
 * ② Clear 还原初始文案+清 hint/badges/输入框——结果区回到「输入关键词开始检索」；
 * ③ **不碰 CENTER_HEALTH**：侧栏健康点是三中心体检态（上次加载成败的记录），
 *   与「用户清空本次检索结果」语义无关，清空不许把 ok 洗成未知。 */
function renderQueryBar(id, q, n, clearFn) {
  const bar = $(id);
  if (!bar) return;
  bar.style.display = '';
  bar.innerHTML = '<span class="hint">当前检索「<b>' + escapeHtml(q) + '</b>」· ' + n + ' 条</span>' +
    '<button class="btn ghost sm" onclick="' + clearFn + '()">清空 ↺</button>';
}
function memFedClear() {
  const bar = $('memFedQueryBar');
  if (bar) { bar.style.display = 'none'; bar.innerHTML = ''; }
  const results = $('memFedResults');
  if (results) results.innerHTML = '输入关键词开始联邦检索';
  const badges = $('memFedBadges');
  if (badges) badges.innerHTML = '';
  const hint = $('memFedHint');
  if (hint) hint.textContent = '';
  const input = $('memFedQ');
  if (input) input.value = '';
  /* 注意：CENTER_HEALTH 故意不清（见函数头注释③） */
}

async function loadMemories() {
  const p = new URLSearchParams();
  if ($('memQ').value.trim()) p.set('q', $('memQ').value.trim());
  if ($('memCat').value) p.set('category', $('memCat').value);
  boxBusy('memList');
  try {
    const d = await api('/api/memory/l1?' + p.toString());
    CENTER_HEALTH.memory = 'ok';
    $('memList').innerHTML = (d.memories || []).map(m =>
      '<div class="mem-item"><span class="tag ' + m.category + '" style="align-self:flex-start">' + m.category + '</span>' +
      '<p>' + escapeHtml(m.content) + '<br><span class="hint">' + escapeHtml(m.source || '') + ' · ' + (m.created_at || '').slice(0, 10) + '</span></p>' +
      '<button class="btn sm danger" onclick="delMemory(' + m.id + ')">' + ico('x') + '</button></div>').join('') ||
      '<div class="hint">空空如也。可手动添加，或由指挥官/外部 Hook 写入。</div>';
  } catch (e) {
    CENTER_HEALTH.memory = 'err';
    boxFail('memList', e, 'loadMemories');   // 失败上屏（不再只 toast——列表区停旧内容=分不清挂没挂）
  }
}
async function addMemory() {
  const c = $('memNew').value.trim();
  if (!c) return;
  try {
    await api('/api/memory/l1', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content: c, category: 'fact', source: 'web' }) });
    $('memNew').value = ''; toast('已保存', 'ok'); loadMemories();
  } catch (e) { toast(e.message, 'err'); }
}
async function delMemory(id) {
  if (!confirm('删除这条记忆？')) return;
  try { await api('/api/memory/l1/' + id, { method: 'DELETE' }); loadMemories(); } catch (e) { toast(e.message, 'err'); }
}

async function loadDoc(lv) {
  try {
    const d = await api('/api/memory/' + lv);
    $(lv + 'content').value = d.content || '';
    $(lv + 'manual').value = d.manual || '';
  } catch (e) { /* 首次为空 */ }
}
async function saveDoc(lv) {
  const body = {};
  if (lv === 'l3') body.content = $(lv + 'content').value;
  body.manual = $(lv + 'manual').value;
  try { await api('/api/memory/' + lv, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); toast(lv.toUpperCase() + ' 已保存', 'ok'); }
  catch (e) { toast(e.message, 'err'); }
}
async function rebuildL2() {
  toast('LLM 压缩近30天 L1 → L2，请稍候…');
  try { const d = await api('/api/memory/l2/rebuild', { method: 'POST' }); toast('L2 重建完成（' + d.items + ' 条，LLM: ' + d.llm + '）', 'ok'); loadDoc('l2'); }
  catch (e) { toast(e.message, 'err'); }
}
async function previewCtx() {
  try {
    const d = await api('/api/memory/context?q=' + encodeURIComponent($('memQ').value.trim()));
    $('ctxPanel').style.display = 'block';
    $('ctxBody').textContent = d.context || '（空）';
  } catch (e) { toast(e.message, 'err'); }
}


/* ── 技能中心（v0.13.26 批4）────────────────────────── */

let SKILLS = [], SKILL_ROUTES = [];

async function loadSkills() {
  boxBusy('skillList');
  try {
    const d = await api('/api/skill/list');
    CENTER_HEALTH.skills = (d.degraded || []).length ? 'warn' : 'ok';
    SKILLS = d.items || d || [];
    // 发现点下拉（过滤器 + 安装选择器共用）
    SKILL_ROUTES = (d.backends ? d.backends.filter(b => b.name !== 'tdai').map(b => b.name) : []) || [];
    const opt = SKILL_ROUTES.map(r => '<option value="' + r + '">' + r + '</option>').join('');
    $('skillRoute').innerHTML = '<option value="">全部发现点</option>' + opt;
    $('instFrom').innerHTML = opt;
    $('instTo').innerHTML = opt;
    $('instName').innerHTML = SKILLS.map(s => '<option value="' + escapeHtml(s.name) + '">' + escapeHtml(s.name) + '</option>').join('');
    $('skillHint').textContent = SKILLS.length + ' 个技能 · ' + SKILL_ROUTES.length + ' 路发现点';
    if ((d.degraded || []).length) $('skillHint').textContent += ' · 降级路：' + d.degraded.join(',');
    renderSkillList();
  } catch (e) {
    CENTER_HEALTH.skills = 'err';
    boxFail('skillList', e, 'loadSkills');
  }
}

function renderSkillList() {
  const q = ($('skillQ').value || '').toLowerCase();
  const route = $('skillRoute').value;
  const rows = SKILLS.filter(s =>
    (!q || String(s.name || '').toLowerCase().includes(q) || String(s.description || '').toLowerCase().includes(q)) &&
    (!route || (s.routes || []).includes(route) || s.route === route));
  $('skillList').innerHTML = rows.map(s => {
    const rt = (s.routes && s.routes.length) ? s.routes[0] : s.route;   // 首路作为查看默认 route
    return '<div class="mem-item"><span class="tag agent" style="align-self:flex-start">' + escapeHtml(s.name) + '</span>' +
    '<p>' + escapeHtml(String(s.description || '').slice(0, 160)) +
    '<br><span class="hint">' + escapeHtml((s.routes || [s.route]).join(', ')) + '</span></p>' +
    '<button class="btn sm" title="读全文（脱敏）" onclick="skillRead(\'' + escapeHtml(String(s.name || '')).replace(/'/g, "\\'") + '\',\'' + escapeHtml(String(rt || '')) + '\')">查看</button></div>';
  }).join('') ||
    '<div class="hint">没有匹配的技能（' + SKILLS.length + ' 总数）</div>';
}

/* 技能正文查看（C 缺口）：调 /api/skill/read 弹 skillDocDrawer 抽屉。
 * 409 多路冲突时读 err.payload.detail.candidates 渲染候选按钮（api() 已挂 payload）。
 * 抽屉只经 openOverlay 开（浮层三条红线：禁止裸 classList.add('on')）。 */
async function skillRead(name, route) {
  const title = $('skillDocTitle'), body = $('skillDocBody');
  if (title) title.textContent = name + (route ? ' · ' + route : '');
  if (body) body.textContent = '读取中…';
  openOverlay('skillDocDrawer');
  try {
    const d = await api('/api/skill/read?name=' + encodeURIComponent(name) + (route ? '&route=' + encodeURIComponent(route) : ''));
    let txt = d.content || '';
    if (d.truncated) {
      txt += '\n\n[正文 ' + (d.bytes_total || '?') + 'B 超过读取上限已截断；需要全文请直接读磁盘路径 ' + (d.realpath || '?') + ']';
    }
    if (body) body.textContent = txt;
  } catch (e) {
    const cands = (e.payload && e.payload.detail && e.payload.detail.candidates) || [];
    if (e.http === 409 && cands.length && body) {
      body.innerHTML = '<div class="hint">同名多路且指向不同文件，请选一路：</div>' +
        cands.map(c => '<div class="mem-item" style="cursor:pointer" onclick="skillRead(\'' +
          escapeHtml(String(name)).replace(/'/g, "\\'") + '\',\'' + escapeHtml(String(c.route || '')) + '\')">' +
          '<b>' + escapeHtml(String(c.route || '')) + '</b> <span class="hint">' +
          escapeHtml(String(c.path || '')) + (c.via_symlink ? '（软链）' : '') + '</span></div>').join('');
    } else if (body) {
      body.textContent = '读取失败：' + (e.message || e);
    }
  }
}
function closeSkillDoc() { closeOverlay('skillDocDrawer'); }

async function skillInstall() {
  const name = $('instName').value;
  const from = $('instFrom').value;
  const to = Array.from($('instTo').selectedOptions).map(o => o.value).filter(t => t !== from);
  if (!name || !from || !to.length) { toast('选择技能、源与至少一个目标', 'err'); return; }
  try {
    const d = await api('/api/skill/install', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name, from_route: from, targets: to }) });
    toast('已软链：' + (d.created || []).join(', ') + (d.existed && d.existed.length ? '（幂等跳过 ' + d.existed.join(', ') + '）' : ''), 'ok');
    loadSkills();
  } catch (e) { toast(e.message, 'err'); }
}

async function loadSkillBudget() {
  const tok = parseInt($('skillTok').value, 10) || 800;
  boxBusy('skillBudget');
  try {
    const d = await api('/api/skill/budget?max_tokens=' + tok);
    const full = (d.full || []).map(f => escapeHtml(f.name) + (f.description ? ' — ' + escapeHtml(f.description.slice(0, 80)) : ''));
    const names = (d.name_only || []).map(n => escapeHtml(n));
    $('skillBudget').innerHTML =
      '预算 ' + d.budget + ' tok · 实际约 ' + d.used_est + ' · 全条目 ' + full.length + ' / 仅名 ' + names.length +
      (d.truncated ? ' / <b>被裁 ' + d.truncated + '</b>' : '') + '（共 ' + d.total + '）<br>' +
      full.concat(names).map(s => '· ' + s).join('<br>');
  } catch (e) { boxFail('skillBudget', e, 'loadSkillBudget'); }
}

/* ── 知识库中心（v0.13.26 批4）──────────────────── */

async function kbSearch() {
  const q = $('kbQ').value.trim();
  if (!q) { toast('输入检索词', 'err'); return; }
  const routes = Array.from($('kbRoutes').selectedOptions).map(o => o.value).join(',');
  $('kbHint').textContent = '检索中…';
  boxBusy('kbResults');
  try {
    const d = await api('/api/kb/search?q=' + encodeURIComponent(q) + '&routes=' + encodeURIComponent(routes) + '&k=12');
    CENTER_HEALTH.kb = 'ok';
    $('kbHint').textContent = d.count + ' 条 · ' + d.took_ms + 'ms · ' + (d.engine || '');
    /* v0.13.28 批3/批4：逐路徽标（与记忆页 fedBadges 同构——替换只 toast 的半吊子表态） */
    const badges = $('kbBadges');
    if (badges) badges.innerHTML = fedBadges(d);
    renderQueryBar('kbQueryBar', q, d.count || 0, 'kbClear');
    $('kbResults').innerHTML = (d.results || []).map(r => {
      /* v0.13.28 批4：源徽标走 SRC_ABBR + src-* 三变体（替换裸拼 `tag turbovec`——
         CSS 无定义静默灰底的根因）；路径行可溯源，workspace/archived 命中且首段
         是 KB 四根之一 ⇒ 下钻按钮（复用 kbBrowse 单层能力，零后端改动）。 */
      const srcName = String(r.source || r.from || '');
      const path = String(r.path || (r.id || '').replace(/:\d+$/, '') || '');
      const seg = path.split('/').filter(Boolean)[0] || '';
      const KB_ROOTS = ['MEMORY.md', 'memory', 'agent-knowledge', 'digest'];
      const drill = (srcName === 'workspace_files' || srcName === 'archived_sessions') &&
                    KB_ROOTS.indexOf(seg) >= 0
        ? ' <button class="btn ghost sm" onclick="kbBrowse(\'' + escapeHtml(seg) + '\')" title="在文档树中查看">↖ ' + escapeHtml(seg) + '</button>' : '';
      return '<div class="mem-item">' + srcTag(srcName) +
      '<p>' + escapeHtml(String(r.title || r.path || r.id || '').slice(0, 100)) +
      '<br><span class="hint">' + escapeHtml(String(r.content || r.preview || '').slice(0, 200)) + '</span>' +
      (path ? '<br><span class="hint" style="font-family:var(--font-mono);font-size:var(--fs-xs)">' + escapeHtml(path.slice(0, 120)) + '</span>' + drill : '') +
      '</p></div>';
    }).join('') ||
      '<div class="hint">零命中。' + (d.note || '') + '</div>';
    // 逐路表态：哪路挂了要说出来（不许全绿假装没挂）——徽标行已表达，toast 保留作主动提醒
    const bad = (d.backends || []).filter(b => !b.ok);
    if (bad.length) {
      CENTER_HEALTH.kb = 'warn';
      toast('降级路：' + bad.map(b => b.name).join(', '), 'err');
    }
  } catch (e) {
    CENTER_HEALTH.kb = 'err';
    boxFail('kbResults', e, 'kbSearch');
  }
}
function kbClear() {
  const bar = $('kbQueryBar');
  if (bar) { bar.style.display = 'none'; bar.innerHTML = ''; }
  const results = $('kbResults');
  if (results) results.innerHTML = '输入关键词开始检索';
  const badges = $('kbBadges');
  if (badges) badges.innerHTML = '';
  const hint = $('kbHint');
  if (hint) hint.textContent = '';
  const input = $('kbQ');
  if (input) input.value = '';
  /* CENTER_HEALTH 故意不清（与 memFedClear 同口径） */
}

async function loadKbStatus() {
  boxBusy('kbStatus');
  try {
    const d = await api('/api/kb/status');
    const seg = (name, v) => '<div><b>' + name + '</b>：' +
      (v.available ? '✅ 可用' : '❌ 不可用') +
      (v.count != null ? ' · ' + v.count : '') +
      (v.chunks ? ' chunks' : '') +
      (v.index_mtime_age_days != null ? ' · 索引 ' + v.index_mtime_age_days + ' 天前' : '') +
      (v.ms != null ? ' · ' + v.ms + 'ms' : '') +
      (v.error ? ' · <span style="color:var(--red)">' + escapeHtml(String(v.error).slice(0, 120)) + '</span>' : '') + '</div>';
    /* local_memory 用 state（fresh/stale/empty）而非 available——旧行硬套
       available 三元判 undefined ⇒ 永远渲染 ❌ 不可用，把「陈旧便签」演成「挂了」
       （2026-09-26 用户误报记忆模块不可用的直接观感来源）。如实三态：
       fresh=✅ / stale=⚠ 陈旧（不参与决策，非故障） / empty=⭕ 空。 */
    const segState = (name, v) => {
      const st = v.state || 'unknown';
      const icon = st === 'fresh' ? '✅' : st === 'stale' ? '⚠️' : '⭕';
      const tag = st === 'stale' ? ' 陈旧便签（非故障，主源在 TDAI）' : st === 'empty' ? ' 空' : '';
      return '<div><b>' + name + '</b>：' + icon + st + tag +
        (v.rows != null ? ' · ' + v.rows + ' 行' : '') +
        (v.authoritative_source ? ' · 主源 ' + v.authoritative_source : '') +
        (v.reason ? ' · <span class="hint">' + escapeHtml(String(v.reason).slice(0, 100)) + '</span>' : '') + '</div>';
    };
    $('kbStatus').innerHTML =
      seg('TDAI L1', d.tdai || {}) +
      seg('turbovec 语义', d.turbovec || {}) +
      seg('workspace 全文', d.workspace || {}) +
      seg('archived 会话备份', d.archived || {}) +
      segState('local 便签', d.local_memory || {});
  } catch (e) { boxFail('kbStatus', e, 'loadKbStatus'); }
}

/* 文档树浏览：sub=根名单层下钻（后端 kb.py 白名单校验，只支持一层——
 * 根内子目录如实渲染为不可点+hint，不扩后端）。面包屑 KB_SUB 记当前层。
 * var 防外层引用时的 TDZ 静态序风险（test_tdz_order 钉着）。 */
var KB_SUB = '';
async function kbBrowse(sub) {
  KB_SUB = sub || '';
  boxBusy('kbBrowse');
  try {
    const d = await api('/api/kb/browse' + (KB_SUB ? '?sub=' + encodeURIComponent(KB_SUB) : ''));
    const crumb = $('kbCrumb');
    if (crumb) crumb.innerHTML = KB_SUB
      ? '<button class="btn ghost sm" onclick="kbBrowse()">知识库根</button> ▸ ' + escapeHtml(KB_SUB)
      : '<span class="hint">知识库根（点目录名下钻一层）</span>';
    $('kbBrowse').innerHTML = (d.entries || []).map(e => {
      const meta = e.files != null ? e.files + ' 文件' : Math.round((e.size || 0) / 1024) + 'KB';
      // 顶层（无 sub）目录条目可点下钻；进了 sub 之后，后端只支持单层——如实说
      const clickable = !KB_SUB && e.dir;
      return clickable
        ? '<div class="mem-item" style="cursor:pointer" onclick="kbBrowse(\'' + escapeHtml(e.name) + '\')" title="下钻 ' + escapeHtml(e.name) + '">' +
          '📁 <b>' + escapeHtml(e.name) + '</b> <span class="hint">' + meta + '</span></div>'
        : '<div class="mem-item">' + (e.dir ? '📁' : '📄') + ' ' + escapeHtml(e.name) +
          ' <span class="hint">' + meta + (KB_SUB && e.dir ? '（暂只支持下钻一层）' : '') + '</span></div>';
    }).join('') || '空目录';
    if (d.errors && d.errors.length) {
      $('kbBrowse').innerHTML += '<div class="hint" style="color:var(--st-error,var(--danger))">' +
        d.errors.map(escapeHtml).join('<br>') + '</div>';
    }
  } catch (e) { boxFail('kbBrowse', e, 'kbBrowse'); }
}

/* ── 端口 ─────────────────────────────────────────── */

async function loadPorts() {
  try { const d = await api('/api/ports'); PORTS = d.listeners || []; renderPorts(); }
  catch (e) { toast(e.message, 'err'); }
}
function renderPorts() {
  const f = ($('portFilter') ? $('portFilter').value.trim() : '').toLowerCase();
  const rows = PORTS.filter(r => !f || (r.port + ' ' + (r.process || '') + ' ' + r.address + ' ' + (r.agent || '')).toLowerCase().includes(f));
  $('portsBody').innerHTML = rows.map(r =>
    '<tr><td>' + r.proto + '</td><td><b>' + r.port + '</b></td><td style="font-family:var(--font-mono);font-size:var(--fs-sm)">' + escapeHtml(r.address) + '</td>' +
    '<td>' + (r.pid || '-') + '</td><td>' + escapeHtml(r.process || '-') + '</td>' +
    '<td>' + (r.agent ? '<span class="tag agent">' + escapeHtml(r.agent) + '</span>' : '') + '</td></tr>').join('');
}

/* ── 遥测 ─────────────────────────────────────────── */

async function loadTelemetry() {
  $('curlExample').textContent =
    '# Claude/pi 等 Agent 会话结束后上报用量（同 session 重报=覆盖）\n' +
    "curl -X POST http://" + location.hostname + ":3102/telemetry/events/claude \\\n" +
    "  -H 'Content-Type: application/json' \\\n" +
    "  -d '{\"session_id\":\"abc123\",\"event\":\"session_usage\",\"cwd\":\"/path/proj\",\n" +
    "       \"usage\":{\"input_tokens\":1088,\"output_tokens\":256,\"cached_tokens\":1024},\n" +
    "       \"usage_scope\":\"session\"}'";
  try {
    const u = await api('/telemetry/usage/summary');
    const rows = Object.entries(u.by_source || {});
    $('usageTable').innerHTML = rows.length ?
      '<table><thead><tr><th>来源</th><th>会话</th><th>输入</th><th>输出</th><th>缓存命中</th></tr></thead><tbody>' +
      rows.map(([k, v]) => '<tr><td><b>' + k + '</b></td><td>' + v.sessions + '</td><td>' + v.input_tokens + '</td><td>' + v.output_tokens + '</td><td>' + v.cached_tokens + '</td></tr>').join('') +
      '</tbody></table>' : '<div class="hint">暂无 session_usage 数据</div>';
    const prof = u.profile || [];
    $('profTable').innerHTML = prof.length ?
      '<table><thead><tr><th>来源</th><th>对象</th><th>次数</th><th>成功率</th><th>均耗时</th><th>峰值</th></tr></thead><tbody>' +
      prof.map(p => '<tr><td>' + p.source + '</td><td><b>' + escapeHtml(p.subject) + '</b></td><td>' + p.calls +
        '</td><td>' + (p.success_rate >= 99 ? ico('check-circle', 'xs') : p.success_rate >= 80 ? ico('circle', 'xs') : ico('alert', 'xs')) + ' ' + p.success_rate + '%</td>' +
        '<td>' + p.avg_ms + 'ms</td><td>' + (p.max_ms || '-') + 'ms</td></tr>').join('') + '</tbody></table>'
      : '<div class="hint">暂无画像数据（对话/指挥官/DAG/定时执行后自动生成）</div>';
    const e = await api('/telemetry/events?limit=20');
    $('eventTable').innerHTML = '<table><thead><tr><th>时间</th><th>来源</th><th>会话</th><th>事件</th></tr></thead><tbody>' +
      (e.events || []).map(x => '<tr><td>' + (x.created_at || '').slice(5, 16).replace('T', ' ') + '</td><td>' + x.source + '</td><td style="font-family:var(--font-mono);font-size:var(--fs-sm)">' + escapeHtml((x.session_id || '').slice(0, 14)) + '</td><td>' + x.event + '</td></tr>').join('') +
      '</tbody></table>';
  } catch (err) { toast(err.message, 'err'); }
}

/* ── S2 自动扫描 ──────────────────────────────────── */

async function runScan() {
  toast('扫描发现源（docker / systemd / CLI 名单）…');
  try {
    const d = await api('/api/scan/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{"auto_register":true}' });
    toast('发现 ' + d.found.length + '，新增 ' + d.added.length + '，已存在 ' + d.skipped_existing.length, 'ok');
    loadAgents();
  } catch (e) { toast(e.message, 'err'); }
}

/* ── S3 协同 DAG ──────────────────────────────────── */

let currentRun = null;

async function decomposeRun() {
  const goal = $('taskGoal').value.trim();
  if (!goal) return toast('请输入目标', 'err');
  const btn = $('btnDecompose'); btn.disabled = true; btn.textContent = '拆解中…';
  try {
    const body = { goal: goal, auto_run: true };
    if ($('taskAgent').value) body.default_agent = $('taskAgent').value;
    const d = await api('/api/tasks/decompose', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    currentRun = d.run_id;
    toast('已拆解 ' + d.tasks.length + ' 个子任务并启动', 'ok');
    loadRuns(); openRun(d.run_id);
  } catch (e) { toast(e.message, 'err'); }
  btn.disabled = false; btn.textContent = '拆解并启动';
}

async function loadRuns() {
  try {
    const d = await api('/api/tasks/runs');
    $('runsBody').innerHTML = (d.runs || []).map(r =>
      '<tr><td style="font-family:var(--font-mono)">' + r.run_id + '</td>' +
      '<td style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="' + escapeHtml(r.goal || '') + '">' + escapeHtml((r.goal || '').slice(0, 40)) + '</td>' +
      '<td>' + ({ running: ico('play', 'xs') + '执行中', success: ico('check-circle', 'xs') + '成功', failed: ico('alert', 'xs') + '失败', partial: ico('circle', 'xs') + '部分', pending: ico('clock', 'xs') + '待跑' }[r.state] || r.state) + '</td>' +
      '<td>' + r.success + '/' + r.total + '</td><td class="hint">' + (r.created_at || '').slice(5, 16).replace('T', ' ') + '</td>' +
      '<td><button class="btn sm ghost" onclick="openRun(\'' + r.run_id + '\')">查看</button></td></tr>').join('') ||
      '<tr><td colspan="6" class="hint">尚无协同任务</td></tr>';
  } catch (e) { /* ignore */ }
}

async function openRun(runId) {
  currentRun = runId;
  try {
    const d = await api('/api/tasks/' + runId);
    $('runPanel').style.display = 'block';
    $('runTitle').textContent = 'Run ' + runId + ' · ' + (d.goal || '').slice(0, 60);
    renderTaskTable(d.tasks);
    renderDag(d.tasks);
  } catch (e) { toast(e.message, 'err'); }
}

// 色值必须是具体十六进制：下面 fill = 描边色 + '22' 透明度派生，拿到 "var(--x)" 会变成非法色值
const TASK_COLORS = {
  pending:  cssToken('--muted', '#6b6b6b'),
  running:  cssToken('--busy', '#2c5f8a'),
  success:  cssToken('--ok', '#2f7a4f'),
  failed:   cssToken('--danger', '#a3342c'),
  blocked:  cssToken('--warn', '#8a5a00')
};

function renderTaskTable(tasks) {
  $('taskBody').innerHTML = tasks.map(t =>
    '<tr><td><b>' + t.task_id + '</b></td><td>' + (t.agent_id || '-') + '</td>' +
    '<td>' + (t.deps || []).join(',') + '</td><td>' + t.status + '</td>' +
    '<td>' + (t.duration_ms != null ? t.duration_ms + 'ms' : '-') + '</td>' +
    '<td><details><summary class="hint">' + escapeHtml((t.output || t.error || '').slice(0, 50)) + '</summary><pre style="white-space:pre-wrap;font-size:var(--fs-sm);max-height:220px;overflow:auto">' + escapeHtml(t.output || t.error || '') + '</pre></details></td></tr>').join('');
}

function renderDag(tasks) {
  const byId = {};
  tasks.forEach(t => byId[t.task_id] = t);
  const depthMemo = {};
  function depth(id, guard) {
    guard = guard || new Set();
    if (depthMemo[id] != null) return depthMemo[id];
    if (guard.has(id)) return 0;
    guard.add(id);
    const t = byId[id];
    const dv = (t && t.deps && t.deps.length) ? 1 + Math.max(...t.deps.map(d => depth(d, guard))) : 0;
    depthMemo[id] = dv;
    return dv;
  }
  const layers = {};
  tasks.forEach(t => { const L = depth(t.task_id); (layers[L] = layers[L] || []).push(t); });
  const NW = 170, NH = 56, GX = 220, GY = 86;
  const maxRows = Math.max(1, ...Object.values(layers).map(l => l.length));
  const W = (Math.max(...Object.keys(layers).map(Number), 0) + 1) * GX + 40;
  const H = maxRows * GY + 20;
  const pos = {};
  Object.entries(layers).forEach(([L, list]) => list.forEach((t, i) => { pos[t.task_id] = { x: 30 + L * GX, y: 20 + i * GY }; }));
  let svg = '<svg width="' + W + '" height="' + H + '" style="min-width:' + W + 'px">';
  const DAG_LINE = cssToken('--muted', '#6b6b6b');
  const DAG_ST = n => cssNum(n, 13);
  svg += '<defs><marker id="arw" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0L8,4L0,8z" fill="' + DAG_LINE + '"/></marker></defs>';
  tasks.forEach(t => (t.deps || []).forEach(dp => {
    const a = pos[dp], b = pos[t.task_id];
    if (a && b) svg += '<line x1="' + (a.x + NW) + '" y1="' + (a.y + NH / 2) + '" x2="' + b.x + '" y2="' + (b.y + NH / 2) + '" stroke="' + DAG_LINE + '" stroke-width="1.5" marker-end="url(#arw)"/>';
  }));
  tasks.forEach(t => {
    const p = pos[t.task_id];
    svg += '<g><rect x="' + p.x + '" y="' + p.y + '" width="' + NW + '" height="' + NH + '" rx="10" fill="' + (TASK_COLORS[t.status] || cssToken('--border')) + '22" stroke="' + (TASK_COLORS[t.status] || cssToken('--border')) + '" stroke-width="1.5"/>' +
      '<text x="' + (p.x + 8) + '" y="' + (p.y + 18) + '" fill="' + cssToken('--text-1') + '" font-size="' + DAG_ST('--fs-xs') + '" font-weight="bold">' + t.task_id + ' · ' + (t.agent_id || '') + '</text>' +
      '<text x="' + (p.x + 8) + '" y="' + (p.y + 34) + '" fill="' + cssToken('--text-2') + '" font-size="' + DAG_ST('--fs-xs') + '">' + escapeHtml((t.prompt || '').slice(0, 18)) + '</text>' +
      '<text x="' + (p.x + 8) + '" y="' + (p.y + 48) + '" fill="' + cssToken('--info') + '" font-size="' + DAG_ST('--fs-xs') + '">' + t.status + (t.duration_ms != null ? ' · ' + Math.round(t.duration_ms / 100) / 10 + 's' : '') + '</text></g>';
  });
  svg += '</svg>';
  $('dagSvg').innerHTML = svg;
}

async function startRun(id) { if (!id) return; try { await api('/api/tasks/' + id + '/start', { method: 'POST' }); toast('已调度', 'ok'); setTimeout(() => openRun(id), 1200); } catch (e) { toast(e.message, 'err'); } }
async function retryRun(id) { if (!id) return; try { await api('/api/tasks/' + id + '/retry', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }); toast('失败项已重置', 'ok'); setTimeout(() => openRun(id), 1200); } catch (e) { toast(e.message, 'err'); } }
async function delRun(id) { if (!id || !confirm('删除 run ' + id + '？')) return; try { await api('/api/tasks/' + id, { method: 'DELETE' }); currentRun = null; $('runPanel').style.display = 'none'; loadRuns(); } catch (e) { toast(e.message, 'err'); } }

/* ── S4 定时任务 ──────────────────────────────────── */

async function createJob() {
  const body = { name: $('jobName').value.trim(), cron: $('jobCron').value.trim(), kind: $('jobKind').value, payload: $('jobPayload').value.trim() };
  if (!$('jobName').value.trim() || !body.cron || !body.payload) return toast('名称/cron/载荷必填', 'err');
  if ($('jobAgent').value) body.agent_id = $('jobAgent').value;
  try {
    await api('/api/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    toast('已创建', 'ok'); $('jobName').value = $('jobCron').value = $('jobPayload').value = '';
    loadJobs();
  } catch (e) { toast(e.message, 'err'); }
}

/* ── 会话导出（件 1 / spec §3）──────────────────────────────────────────
   三个实测出来的坑，决定了这里为什么长这样：
   ① 不能走 api()：01-core-boot.js 的 isWriteMethod() 只给 POST/PUT/PATCH/DELETE 带 token，
      而 /api/sessions/export 是 **GET 却要写级鉴权**（后端显式 writeauth.decide("POST",…)）⇒ 必 401。
   ② 不能用 api() 取体：它会 JSON.parse 成对象，CSV/JSON **文件字节**就毁了 ⇒ 必须 blob。
   ③ token 不走 ?token=：那会进服务端访问日志与浏览器历史（本工作区三次凭据外流前例）⇒ 只走头。
   文案四态互斥（照抄 07-asset-panel.js 的红向口径）：**被拒绝不许说成"没有会话"**。
   窄屏口径：只加 1 个图标按钮，不做 format/redact 的一排开关（chrome 单行化优先级更高）；
   CSV 与 redact=0 走 API 参数，不在本轮做 UI（YAGNI，且给原文出口做 UI 需单独裁定）。 */
function exportStateOf(status, count, detail) {
  if (status === 503) return 'misconfig';
  if (status === 401) return /缺少凭据/.test(String(detail || '')) ? 'need-token' : 'bad-token';
  if (status !== 200) return 'error';
  return (count > 0) ? 'ok' : 'empty';
}

function exportStateText(state, count, extra) {
  const n = (count == null || count < 0) ? '?' : String(count);
  const code = (extra && extra.status) ? String(extra.status) : '?';
  const hits = (extra && typeof extra.hits === 'number') ? extra.hits : 0;
  if (state === 'ok') {
    return '已导出 ' + n + ' 条会话（默认脱敏，命中 ' + hits + ' 处' +
           (hits > 0 ? '，正文已打码）' : '）');
  }
  if (state === 'empty') return '导出成功，但这个范围内确实是 0 条会话（文件是空的，不是被拒）';
  if (state === 'need-token') return '导出被拒：未提供凭据 —— 不是没有会话。请在「设置」里应用 TERM_TOKEN 后重试';
  if (state === 'bad-token') return '导出被拒：凭据不匹配 —— 不是没有会话。存量口令可能已换过，已清除，请重新输入';
  if (state === 'misconfig') return '导出被拒：服务端未配置口令（fail-closed）—— 不是没有会话';
  // 'error' 必须是**显式分支**而不是掉进兜底：闸门按状态名逐个点名（缺一个即红），
  // 而兜底留给"将来新增状态忘了配文案"这种情况 —— 那时也要说清不是没有会话。
  if (state === 'error') return '导出失败（HTTP ' + code + '）—— 不是没有会话';
  return '导出失败（未知状态 ' + state + '）—— 不是没有会话';
}

async function chatSessExport() {
  const tk = termToken();
  if (!tk) { toast(exportStateText('need-token', -1, {}), 'err'); return; }
  const p = new URLSearchParams({ format: 'json', limit: '1000',
                                  with_messages: '1', redact: '1' });
  if (chatPick) p.set('agent_id', chatPick);
  let r;
  try {
    r = await fetch('/api/sessions/export?' + p.toString(),
                    { headers: { 'X-TERM-TOKEN': tk } });   // 头，不是 URL
  } catch (e) {
    toast('导出失败：网络不可达（' + e.message + '）—— 不是没有会话', 'err');
    return;
  }
  const cnt = parseInt(r.headers.get('X-Export-Count') || '-1', 10);
  const hits = parseInt(r.headers.get('X-Export-Redacted-Hits') || '0', 10);
  if (r.status !== 200) {
    let detail = '';
    try { const d = await r.json(); detail = (d && d.detail) || ''; } catch (e) { detail = ''; }
    const st = exportStateOf(r.status, cnt, detail);
    if (st === 'bad-token') lsRemove('hub.term.token');   // 存量失效口令：清掉，下次重新问
    toast(exportStateText(st, cnt, { status: r.status, hits: hits }), 'err');
    return;
  }
  let body;
  try { body = await r.blob(); } catch (e) {
    toast('导出失败：读不到响应体 —— 不是没有会话', 'err'); return;
  }
  const cd = r.headers.get('Content-Disposition') || '';
  const m = cd.match(/filename="?([^";]+)"?/);
  const name = (m && m[1]) || 'agent-hub-sessions.json';   // 后端保证纯 ASCII 文件名
  const obj = URL.createObjectURL(body);
  const a = document.createElement('a');
  a.href = obj;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  setTimeout(function () { URL.revokeObjectURL(obj); a.remove(); }, 0);
  toast(exportStateText(exportStateOf(200, cnt, ''), cnt, { hits: hits }), 'ok');
}

/* data-export 委托：新按钮一律走委托，不用 inline onclick（AGENTS.md 浮层配套红线，口径取最严）。
   挂在 document 上而不是某个面板里 ⇒ 工具条重渲染后不必重新绑定。 */
document.addEventListener('click', function (e) {
  const b = e.target && e.target.closest ? e.target.closest('[data-export]') : null;
  if (!b) return;
  if (b.getAttribute('data-export') === 'sessions') chatSessExport();
});
