function toggleGroup(g) {
  navOpen = (navOpen === g) ? '' : g;   // 单开：展开一个自动收起其他
  localStorage.setItem('hub.nav.open', navOpen);
  renderNav();
}
/* 点左侧实体 → 右侧加载该实体工作台（复用既有 gotoChat / showDetail，不另起炉灶） */
function openEntity(id) {
  const a = entityById(id);
  if (!a) return;
  const es = a.entries || [];
  const has = t => es.some(e => e.type === t);
  if (has('embed')) return gotoChat(id, 'embed');
  if (has('term')) return gotoChat(id, 'term');
  if (has('chat')) return gotoChat(id, 'chat');
  if (has('detail')) return showDetail(id);
  const o = es.find(e => e.type === 'open');
  if (o) return window.open(lanUrl(o.url), '_blank');
  showDetail(id);
}
/* 右侧操作区：系统页面包屑（实体页由 renderModeBar 写） */
function renderPageCrumb(page) {
  const crumb = $('crumb'), tabs = $('opTabs');
  if (!crumb) return;
  if (page === 'chat') return;   // 实体工作台由 renderModeBar 接管，别互相覆盖
  if (tabs) tabs.innerHTML = '';
  const label = escapeHtml(PAGE_LABELS[page] || page);
  const top = '';
  crumb.innerHTML = top + '<b>' + label + '</b>';
  syncOpBar();
}
/* v0.10.1：#opBar 无标题也无 tab 时整条收起，窄屏省 37px，宽屏也不留空带 */
function syncOpBar() {
  const ob = $('opBar'), c = $('crumb'), t = $('opTabs');
  if (!ob) return;
  const empty = !((c && c.textContent.trim()) || (t && t.textContent.trim()));
  ob.classList.toggle('void', empty);
}
/* 模式 tab 点击：embed/term/chat 走既有 switchMode，open/detail 各自直行 */
function navMode(m) {
  if (m === 'detail') { showDetail(chatPick); return; }
  if (m === 'open') {
    const a = entityById(chatPick);
    const e = ((a && a.entries) || []).find(x => x.type === 'open');
    if (e) window.open(lanUrl(e.url), '_blank');
    return;
  }
  switchMode(m);
}

/* ── 侧栏：手风琴导航委托绑定 + 折叠记忆 ───────────── */
function initSidebar() {
  const sb = document.getElementById('sidebar'), btn = document.getElementById('btnSideToggle');
  if (!sb || !btn) return;
  const narrow = () => window.innerWidth < 768;
  const apply = c => {
    sb.classList.toggle('collapsed', c);
    btn.innerHTML = c ? ico('panel-expand', 'xs') : ico('panel-collapse', 'xs') + '<span class="lbl">收起</span>';
    localStorage.setItem('hub.sidebar', c ? '1' : '0');
    const m = document.getElementById('sideMask');
    if (m) m.classList.toggle('on', !c && narrow());   // 窄屏展开时才有遮罩
  };
  const stored = localStorage.getItem('hub.sidebar');
  apply(stored === '1' || (stored === null && narrow()));   // 窄屏首次默认收成图标条
  btn.onclick = () => apply(!sb.classList.contains('collapsed'));
  // 事件委托：静态常驻项 + 手风琴动态项（含系统页）统一走这里
  sb.addEventListener('click', e => {
    let el = e.target.closest('button[data-page]');
    if (el) { go(el.dataset.page); if (narrow()) apply(true); return; }
    el = e.target.closest('button[data-sys]');
    if (el) { go(el.dataset.sys); if (narrow()) apply(true); return; }
    el = e.target.closest('.hh-row');                     // 历史条目：续聊，窄屏顺手收抽屉
    if (el) { termResume(el.dataset.agent, el.dataset.sid); if (narrow()) apply(true); return; }
    el = e.target.closest('button[data-entity]');
    if (el) {
      const aid = el.dataset.entity;
      if (TERM_HIST_AGENTS.includes(aid)) {
        if (histOpen === aid) histOpen = '';              // 再点当前行 = 只收起，不离开页面
        else { histOpen = aid; histLoad(aid); }           // 展开新的（自动收起上一个）
        localStorage.setItem('hub.hist', histOpen);
        renderNav();
      }
      openEntity(aid);                                    // 进工作台照旧（A：两件事一次点击）
      if (narrow() && !histOpen) apply(true);
      return;
    }
    el = e.target.closest('button[data-group]');
    if (el) {
      // 收起态下点组图标 = 先展开侧栏并定位到该组（否则手风琴体被 display:none，点了没反应）
      if (sb.classList.contains('collapsed')) {
        navOpen = el.dataset.group;
        localStorage.setItem('hub.nav.open', navOpen);
        apply(false);
        renderNav();
      } else toggleGroup(el.dataset.group);
    }
  });
  const mask = document.getElementById('sideMask');
  if (mask) mask.addEventListener('click', () => apply(true));
  const si = document.getElementById('navSearch');
  if (si) {
    let _t;
    si.addEventListener('input', () => {
      clearTimeout(_t);
      _t = setTimeout(renderNav, 250);  // 防抖 250ms
    });
  }
}

function setBadge(page, n) {
  const el = document.getElementById('badge-' + page);
  if (el) el.textContent = (n > 0 ? String(n) : '');
}
/* 徽章只取现有接口的现成数据，不新增后端 */
async function updateBadges() {
  try {
    const d = await (await fetch('/api/agents')).json();
    const all = d.agents || [];
    setBadge('classroom', all.filter(a => a.kind === 'agent').length);
    setBadge('chat', all.length);
  } catch (e) { /* 静默：徽章是增强，失败不影响主流程 */ }
  try {
    const d = await (await fetch('/mcp/servers')).json();
    setBadge('mcp', (d.servers || []).length);
  } catch (e) {}
  try {
    const d = await (await fetch('/api/jobs')).json();
    setBadge('jobs', (d.jobs || []).filter(j => j.enabled).length);
  } catch (e) {}
}
initSidebar();
updateBadges();
setInterval(updateBadges, 60000);
$('regMask').addEventListener('click', ev => { if (ev.target === $('regMask')) closeRegister(); });
// T8：chip 键盘可达（Enter/Space 等价点击）
const HOME_CHIPS = $('homeChips');   // 首页快捷问句 chip 键盘可达（原 infraGrid，随座位矩阵一并移除）
if (HOME_CHIPS) HOME_CHIPS.addEventListener('keydown', e => {
  if ((e.key === 'Enter' || e.key === ' ') && e.target.classList && e.target.classList.contains('chip')) {
    e.preventDefault(); e.target.click();
  }
});
// T8：命令面板输入/键盘
$('cmdInput').addEventListener('input', e => renderCmdList(e.target.value.trim()));
$('cmdInput').addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    e.preventDefault();
    const q = e.target.value.trim();
    const first = $('cmdList').querySelector('.cmd-item');
    if (first && !q) closeCmd(); else if (first) first.click();
  } else if (e.key === 'Escape') closeCmd();
});
/* 快捷键不许在"正在输入"的地方劫持按键（改前实测的两种破坏）：
   · 终端里敲 `/`（路径分隔符，一天几百次）→ preventDefault + 焦点跳搜索框，
     之后所有输入都进了搜索框，画面看起来像"打字没反应"；
   · bash 的 Ctrl+K（kill-line）→ 被拿去开命令面板；
   · vim 的 Esc → 归 pty 的用，不能顺手去关我的浮层。
   规则：输入位（含 xterm 自己的 textarea）里只放行"搜索框的 Esc 清空"这一条，
   其余一律 return；非输入位维持原有行为。 */
function keyTargetIsEditing(e) {
  const t = e && e.target;
  if (!t || !t.closest) return false;
  if (t.closest('.xterm')) return true;
  const tag = t.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || t.isContentEditable === true;
}
document.addEventListener('keydown', e => {
  const editing = keyTargetIsEditing(e);
  if (e.key === 'Escape') {
    // 终端里的 Esc 原样给 pty；只有搜索框自己认领"Esc 清空"
    if (editing && !(e.target && e.target.id === 'navSearch')) return;
    closeDetail();
    closeSettings();
    const si = $('navSearch');
    if (si && si.value) { si.value = ''; renderNav(); return; }
    return;
  }
  if (editing) return;
  if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
    e.preventDefault();
    $('cmdMask').classList.contains('on') ? closeCmd() : openCmd();
  } else if (e.key === '/') {
    const si = $('navSearch');
    if (si) { e.preventDefault(); si.focus(); si.select(); }
  }
});

function tick() {
  const t = new Date().toLocaleString('zh-CN', { hour12: false });
  // // $('hTime').textContent = t; // 已移除  // 已移除
  $('ftTime').textContent = t;
}
setInterval(tick, 1000); tick();
setInterval(loadAgents, 30000);  // T4：8s→30s（左栏手风琴与首页摘要随 loadAgents 一起刷新，无需高频）
pollHealth(); setInterval(pollHealth, 15000);  // T5：健康灯独立于 agent 列表轮询
setInterval(() => {
  if (document.getElementById('page-tasks').classList.contains('on')) { loadRuns(); if (currentRun) openRun(currentRun); }
  if (document.getElementById('page-jobs').classList.contains('on')) loadJobs();
  // 终端面板可见时轮询会话记录：进程退出/超时/TTL 回收都会让死条目自动消失，无需手动点「会话」
  // 只看 termPane 的 on 不够：整块 page-chat 被 display:none 藏起来时它仍是 on，白轮询
  if (document.getElementById('page-chat').classList.contains('on')
      && document.getElementById('termPane').classList.contains('on')) termRefreshList();
}, 6000);
loadAgents();
go(localStorage.getItem('hub.page') || 'classroom');  // T9：默认落点 = 上次所在页（chatPick/chatMode 已在声明处恢复）
