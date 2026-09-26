/* ── 本机项目页（v0.13.30）────────────────────────────────────────
 * 分片头注释（军规：分片源，不是 build 产物；产物在 static/hub.js 由
 * scripts/build_hubjs.sh 按字典序拼接，直接改产物会被 test_hubjs_split 判红）。
 *
 * 数据源：GET /api/localprojects（后端多根 git 扫描 + cloudcli auth.db 合并，
 * 只读不鉴权）。列表三态（busy/数据/失败含重试）；errors 里点名的降级源
 * 用 hint 展示——「查不了」不能糊成「没有」。
 * 新建会话：复用 startAgent 的链路（POST /api/term/sessions），仅多传 cwd；
 * 后端 _cwd_or_none 校验后只进 os.chdir，命令仍出自画像白名单。
 * 状态变量用 var：go()（01 分片，拼接序在前）会经顶层 go(lsGet('hub.page'))
 * 同步走到本分片函数体，let 的 TDZ 静态序风险会被 test_tdz_order 判红——
 * 08-runlog 同教训（RL_FIRST/RL_CUR）。
 */

var LP = [];         // 全量项目（过滤前的缓存）
var lpSel = '';      // 当前选中项目 path（'' = 未选）
var lpSelName = '';
var lpLoaded = false;   // go() 懒加载标志（与 memLoaded/skillsLoaded 同型；go() 分支在 01）

function lpTime(la) {
  if (!la) return '';
  return String(la).slice(5, 16).replace('T', ' ');
}

function lpRowHtml(p, i) {
  const on = p.path === lpSel ? ' on' : '';
  const src = (p.git ? '<span class="tag agent">git</span>' : '') +
              (p.cloudcli ? '<span class="tag">cc</span>' : '');
  return '<div class="mem-item' + on + '" data-i="' + i + '" style="gap:6px;cursor:pointer"' +
    ' onclick="lpSelect(' + i + ')"' +
    ' title="' + escapeHtml(p.path) + '">' +
    '<p style="min-width:0"><b>' + escapeHtml(p.name) + '</b>' + src +
    (p.sessions ? ' <span class="hint">' + p.sessions + ' 会话</span>' : '') +
    (p.last_activity ? ' <span class="hint">' + escapeHtml(lpTime(p.last_activity)) + '</span>' : '') +
    '<br><span class="hint" style="font-family:var(--font-mono);font-size:var(--fs-xs)">' +
    escapeHtml(String(p.path).slice(0, 72)) + '</span></p></div>';
}

function lpSelect(i) {
  const p = LP[i];
  if (!p) return;
  lpSel = p.path;
  lpSelName = p.name;
  const nameEl = $('lpSelName');
  if (nameEl) { nameEl.textContent = p.name; nameEl.title = p.path; }
  const btn = $('lpStartBtn');
  if (btn) btn.disabled = false;
  lpRenderList();   // 重画选中态（.on）
}

function lpRenderList() {
  const box = $('lpList');
  if (!box) return;
  const q = ($('lpQ') ? $('lpQ').value.trim() : '').toLowerCase();
  const idx = q ? LP.map((p, i) => [p, i]).filter(([p]) =>
    String(p.name || '').toLowerCase().includes(q) || String(p.path || '').toLowerCase().includes(q))
    : LP.map((p, i) => [p, i]);
  box.innerHTML = idx.map(([p, i]) => lpRowHtml(p, i)).join('') ||
    '<div class="hint" style="padding:10px">' + (q ? '无匹配项目' : '无项目') + '</div>';
}

function lpRenderAgents() {
  const sel = $('lpAgent');
  if (!sel) return;
  const cur = sel.value;
  const items = AGENTS.filter(a => a.kind === 'agent' && (a.entries || []).some(e => e.type === 'term'));
  sel.innerHTML = items.map(a =>
    '<option value="' + escapeHtml(a.id) + '">' + escapeHtml(a.name) + '</option>').join('');
  if (cur && items.some(a => a.id === cur)) sel.value = cur;   // 轮询重绘不覆盖用户选择
  else if (!cur && items.length) sel.value = items[0].id;
}

async function loadLocalProjects(force) {
  const box = $('lpList');
  const hint = $('lpHint');
  if (!box) return;
  if (force) LP = [];
  if (!LP.length) box.innerHTML = '<div class="hint" style="padding:10px">扫描本机项目中…</div>';
  if (hint) hint.textContent = '加载中…';
  try {
    const d = await api('/api/localprojects');
    LP = d.projects || [];
    lpRenderList();
    lpRenderAgents();
    if (hint) hint.textContent = (d.count || 0) + ' 个项目';
    const meta = $('lpMeta');
    if (meta) meta.textContent = (d.roots || []).join(' · ') +
      ((d.errors || []).length ? ' ｜ 降级源：' + d.errors.join('；') : '') +
      (d.took_ms != null ? ' ｜ ' + d.took_ms + 'ms' : '');
  } catch (e) {
    if (hint) hint.textContent = '加载失败';
    box.innerHTML = '<div class="hint" style="padding:10px">加载失败：' + escapeHtml(e.message || '') +
      ' <button class="btn sm" onclick="loadLocalProjects(true)">重试</button></div>';
  }
}

/* 核心：选中项目 + agent → 拉起 pty 会话 → 跳该 agent 终端工作台。
   逐字仿 startAgent（05 分片），仅多传 cwd。 */
async function lpStart() {
  const agent = $('lpAgent') ? $('lpAgent').value : '';
  if (!lpSel || !agent) { toast('先选项目与 Agent', 'err'); return; }
  try {
    const d = await api('/api/term/sessions', { method: 'POST',
      headers: termHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ agent_id: agent, cwd: lpSel }) });
    toast('已在 ' + lpSelName + ' 拉起 ' + agent + ' 终端会话', 'ok');
    gotoChat(agent, 'term');
    setTimeout(() => termConnect(d.session.id, agent, { user: true }), 100);
  } catch (e) { toast('新建会话失败：' + e.message, 'err'); }
}
