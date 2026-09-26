/* ── 本机项目页（v0.13.30）────────────────────────────────────────
 * 分片头注释（军规：分片源，不是 build 产物；产物在 static/hub.js 由
 * scripts/build_hubjs.sh 按字典序拼接，直接改产物会被 test_hubjs_split 判红）。
 *
 * 数据源：GET /api/localprojects（后端多根 git 扫描 + cloudcli auth.db 合并，
 * 只读不鉴权）。列表三态（busy/数据/失败含重试）；errors 里点名的降级源
 * 用 hint 展示——「查不了」不能糊成「没有」。
 * 新建会话：复用 startAgent 的链路（POST /api/term/sessions），仅多传 cwd；
 * 后端 _cwd_or_none 校验后只进 os.chdir，命令仍出自画像白名单。
 * v0.13.32 行内动作：
 *   收藏（★）——lpStars 集合（localStorage 持久，键 = 项目 path）；收藏行
 *   置顶（多条按原序在前），行首星标高亮，再点取消；
 *   隐藏（👁off 图标）——lpHiddenSet（localStorage 持久）；隐藏行不再出现，
 *   勾选顶部 #lpHidden「显示隐藏」才回列（回列时半透明以示状态）。
 * 状态变量用 var：go()（01 分片，拼接序在前）会经顶层 go(lsGet('hub.page'))
 * 同步走到本分片函数体，let 的 TDZ 静态序风险会被 test_tdz_order 判红——
 * 08-runlog 同教训（RL_FIRST/RL_CUR）。localStorage 一律走 lsGet/lsSet 守卫
 * （test_ls_guard R1：裸调用判红）。 */

var LP = [];         // 全量项目（过滤前的缓存）
var lpSel = '';      // 当前选中项目 path（'' = 未选）
var lpSelName = '';
var lpLoaded = false;   // go() 懒加载标志（与 memLoaded/skillsLoaded 同型；go() 分支在 01）

/* v0.13.32 收藏/隐藏状态：localStorage 里的 JSON 数组（path 列表）。
   解析失败（旧值形状漂移）按空集处理，绝不让坏存档打断渲染。 */
function _lpSet(key) {
  try { return new Set(JSON.parse(lsGet(key) || '[]')); } catch (e) { return new Set(); }
}
var lpStars = _lpSet('hub.lp.stars');
var lpHiddenSet = _lpSet('hub.lp.hidden');

function _lpSave(key, set) {
  lsSet(key, JSON.stringify([...set]));
}

/* v0.13.36 收藏/隐藏落服务端（跨浏览器/端侧一致）：载入后拉一次后端偏好，
   命中即以后端为准并回写 localStorage（离线兜底）；行内切换后 fire-and-forget
   PUT（api() 对写方法自动带 x-hub-token）。后端未升级（404）或没配 token 时
   静默沿用本机存档——降级不报错，本机语义与 v0.13.32 完全一致。 */
var lpPrefSynced = false;
var lpPrefErrShown = false;

function lpCountsText() {
  return (lpStars.size ? ' · 收藏 ' + lpStars.size : '') +
    (lpHiddenSet.size ? ' · 已隐藏 ' + lpHiddenSet.size : '');
}

async function lpSyncPrefs() {
  if (lpPrefSynced) return;
  lpPrefSynced = true;
  try {
    const d = await api('/api/prefs/projects.lp');
    if (d && d.value) {
      lpStars = new Set(d.value.stars || []);
      lpHiddenSet = new Set(d.value.hidden || []);
      _lpSave('hub.lp.stars', lpStars);
      _lpSave('hub.lp.hidden', lpHiddenSet);
      lpRenderList();
      const hint = $('lpHint');
      if (hint && LP.length) hint.textContent = LP.length + ' 个项目' + lpCountsText();
    }
  } catch (e) { /* 404=后端未升级；网络失败=离线。两种都沿用本机存档 */ }
}

async function lpPushPref() {
  try {
    await api('/api/prefs/projects.lp', { method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stars: [...lpStars], hidden: [...lpHiddenSet] }) });
    lpPrefErrShown = false;
  } catch (e) {
    if (!lpPrefErrShown) { lpPrefErrShown = true; toast('收藏/隐藏已存本机，云端同步失败', 'err'); }
  }
}

function lpTime(la) {
  if (!la) return '';
  return String(la).slice(5, 16).replace('T', ' ');
}

/* 行内动作图标：act-btn（既有 CSS）。stopPropagation 防触发行选中。 */
function lpRowHtml(p, i) {
  const on = p.path === lpSel ? ' on' : '';
  const starred = lpStars.has(p.path);
  const hidden = lpHiddenSet.has(p.path);
  const src = (p.git ? '<span class="tag agent">git</span>' : '') +
              (p.cloudcli ? '<span class="tag">cc</span>' : '');
  const acts = '<span class="nav-acts" style="display:inline-flex;gap:2px;align-self:center">' +
    '<span class="act-btn" style="' + (starred ? 'color:var(--warn,#d90)' : '') + '"' +
    ' onclick="event.stopPropagation();lpToggleStar(' + i + ')"' +
    ' title="' + (starred ? '取消收藏' : '收藏——置顶排序') + '">' + ico('star') + '</span>' +
    '<span class="act-btn" onclick="event.stopPropagation();lpToggleHide(' + i + ')"' +
    ' title="' + (hidden ? '取消隐藏' : '隐藏——不再显示（可勾选顶部「显示隐藏」找回）') + '">' +
    ico('eye') + '</span></span>';
  return '<div class="mem-item' + on + (hidden ? ' lp-hidden-row' : '') + '" data-i="' + i +
    '" style="gap:6px;cursor:pointer' + (hidden ? ';opacity:.45' : '') + '"' +
    ' onclick="lpSelect(' + i + ')"' +
    ' title="' + escapeHtml(p.path) + '">' +
    '<p style="min-width:0">' + (starred ? '<span style="color:var(--warn,#d90)">★ </span>' : '') +
    '<b>' + escapeHtml(p.name) + '</b>' + src +
    (p.sessions ? ' <span class="hint">' + p.sessions + ' 会话</span>' : '') +
    (p.last_activity ? ' <span class="hint">' + escapeHtml(lpTime(p.last_activity)) + '</span>' : '') +
    '<br><span class="hint" style="font-family:var(--font-mono);font-size:var(--fs-xs)">' +
    escapeHtml(String(p.path).slice(0, 72)) + '</span></p>' + acts + '</div>';
}

function lpToggleStar(i) {
  const p = LP[i];
  if (!p) return;
  if (lpStars.has(p.path)) lpStars.delete(p.path);
  else lpStars.add(p.path);
  _lpSave('hub.lp.stars', lpStars);
  lpRenderList();
  lpPushPref();
}

function lpToggleHide(i) {
  const p = LP[i];
  if (!p) return;
  if (lpHiddenSet.has(p.path)) lpHiddenSet.delete(p.path);
  else {
    lpHiddenSet.add(p.path);
    if (lpSel === p.path) { lpSel = ''; lpSelName = ''; }   // 隐藏选中项即解除选中
  }
  _lpSave('hub.lp.hidden', lpHiddenSet);
  lpRenderList();
  lpPushPref();
  const hint = $('lpHint');
  if (hint && lpHiddenSet.size) hint.textContent += '（已隐藏 ' + lpHiddenSet.size + ' 项）';
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
  const showHidden = !!($('lpHidden') && $('lpHidden').checked);
  /* 顺序：收藏置顶（保持原相对序）→ 未收藏；隐藏行仅在勾选「显示隐藏」时出现。 */
  const visible = LP.filter(p => !lpHiddenSet.has(p.path) || showHidden);
  const ordered = visible.filter(p => lpStars.has(p.path))
    .concat(visible.filter(p => !lpStars.has(p.path)));
  const idx = ordered.map(p => [p, LP.indexOf(p)])
    .filter(([p]) => !q || String(p.name || '').toLowerCase().includes(q) ||
    String(p.path || '').toLowerCase().includes(q));
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
    if (hint) hint.textContent = (d.count || 0) + ' 个项目' + lpCountsText();
    lpSyncPrefs();
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
