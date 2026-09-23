# agent-hub「运行异常 / 页面 UI 异常」根因与收尾计划

日期：2026-09-23　主机：gztxt@zzst　生产：:3102 v0.13.6（boot=now=`04289890`，pid 377521）

## 1. 检索到的既往结论（先检索后动手）

| 命中项 | 用处 |
|---|---|
| `agent-knowledge/35`、场景档「工具评估与集成」09-22/09-23 | 同仓并发施工（A 路他会话改终端白遮挡、B 路改侧栏 240px）已是已知风险面 |
| PT-20260923-12 | 端侧结项口径：三条手机链路只能由用户确认 |
| 09-23 立「探活最多 2 次即停」 | 本轮全程只用 `verify_prod_smoke.py` 单轮复合探针取齐证据 |
| 09-20「窄屏内容区最大化」判据 | 窄屏类故障的验收基准 |

## 2. 运行层：实测无故障（排除）

`verify_prod_smoke.py` 生产体检 **20/20 全 PASS**（含真 LLM 文本环 18.0s `model=qwen3.8-flash reply='好的收到'`）：
- `/health` 语义绿、pid==systemd MainPID、hub.js 三处 md5 一致（served=disk=HEAD=`c6ec4dcc`）
- `.bak`→404、穿越→404、304 带 Vary、鉴权四组合、WS 死 sid→4404、启动后日志零 Traceback
- 桌面 1280×800 走真浏览器：0 个 4xx/5xx、0 未捕获异常、0 覆盖层、无横向溢出

⇒ **“运行异常”不在后端。异常是窄屏（手机）上的 UI 缺陷，且是存量 bug，不是 A/B 路引入。**

## 3. 页面 UI 异常：根因（已定位，真浏览器实测）

旧代码 `static/hub/06-manager-tasks.js` `initSidebar()`：

```js
localStorage.setItem('hub.sidebar', c ? '1' : '0');      // 键与视口宽度无关
const stored = localStorage.getItem('hub.sidebar');
apply(stored === '1' || (stored === null && narrow()));  // 且“加载即写盘”
```

三段因果（每一段都有实测）：
1. 桌面首次访问：`narrow()=false` → `apply(false)` → 把**桌面的“展开”**固化进全局键 `hub.sidebar='0'`；
2. 手机再打开：`stored==='0'` → 不收起 → 命中 CSS
   `@media (max-width:767px) .sidebar:not(.collapsed){position:fixed;width:236px;z-index:46}`
   → **390px 视口上被一块 236px 白底覆盖层盖住 61%**（CDP 实测
   `overlays:[{id:sidebar,bg:rgb(255,255,255),z:46,pct:61}]`）——用户所见即「整页被白板糊住」；
3. `resize` 只挂 `termRepaint()`，抽屉态永不重算 → 转屏/刷新都不会自愈，所以表现为“随机且顽固”。

配套第二条缺陷：JS 自己写 `innerWidth < 768`，CSS 写 `@media (max-width:767px)` —— **两套真相**，中间宽度必然打架。

## 4. 已落地的改动（工作区，**未提交、未重启**）

- `static/hub/06-manager-tasks.js`：偏好改为一档一键 `hub.sidebar.wide/.narrow`；加载 `persist=false` 不写盘；断点唯一化 `matchMedia('(max-width:767px)')`；跨断点 `change` 重算（含 Safari<14 `addListener` 回退）；判定抽成顶层纯函数 `sidebarWantCollapsed(narrow,stored,legacy)` 供探针原样抽取。
- 存量自愈：`legacy` 只在宽屏当一次性迁移，窄屏一律回默认收起 ⇒ 已污染的手机**无需清缓存**。
- `scripts/build_hubjs.sh` 重建 `static/hub.js`（2100 行，md5→`1fb67400`），`node --check` 通过。
- 新增 `tests/test_sidebar_breakpoint.py`：6 条 L0 静态不变量（含断点漂移闸门、加载不写盘）+ 3 条 L1 真 node 判定表，**红基线按内容回溯 git 取旧实现**（旧逻辑在同输入下必须判成“展开”，防自证式绿灯）。
- 备份：`static/hub/06-manager-tasks.js.bak-20260923_180843-sidebar-narrow-pollution`、`static/hub.js.bak-20260923_180843-sidebar-narrow-pollution`。

已有验证：L0 hermetic **119 ran / 0 skipped / OK**；`test_sidebar_breakpoint` 9 例 OK；`test_hubjs_split` OK（拼接==产物逐字相等）。

## 5. 待批准执行的收尾步骤

1. **补全量测试**：`bash scripts/run_tests.sh all`（L0+L1，含新闸门）+ 四只既有浏览器探针回归（replay_gate / heal_viewport / key_focus_guard / staticguard）。
2. **红-绿对照（生产零影响）**：`/tmp/hub_redgreen.py` —— CDP `Fetch` 拦截，只给 headless 客户端喂**改前字节**（取自 .bak）与生产**改后字节**，判据 =「390px 首屏，视口中心那一击是否被 fixed 白抽屉整个接走」。生产侧不写盘、不重启、不发写请求。
3. **端侧复验清单**（我不能自证，须你手机上确认）：
   - 手机直开首页：左侧应是 52px 图标条，不是一整块白板；
   - 点图标条 → 抽屉展开且点遮罩即收；
   - 桌面打开后再用手机 → 不应再被盖住；
   - 桌面侧栏仍展开、宽度仍 240px（B 路 v0.12.5 成果不回退）。
4. **提交**：`git add` 上述 2 改 1 新 → commit（v0.13.7 文案含根因三段与闸门名）。**不重启服务**：纯静态 JS，`/static` 每次按 mtime+size 出 ETag，改完即生效。
5. **沉淀记忆（“彻底杜绝”部分，三处）**：
   - `agent-knowledge/36-agent-hub窄屏抽屉白遮挡与偏好污染.md`：根因、判据、四条不变量、探针名；
   - 场景档 `系统运维基线规范.md` 加一条跨项目红线：**「任何按视口/设备分档的偏好，必须一档一键 + 加载不写盘 + 断点只允许一处定义（matchMedia 与 CSS 同源）」**——这是可复用的普适结论，不止 agent-hub；
   - `PENDING-TASKS.md` 登记 PT：`src/vitals.py` 未提交（见 §6）+ 本项端侧回填位。
6. 顺带把 `data/`、备份链核查：`agent-hub` 已在 `backup-git-repos.sh` 第 49 行清单内，提交即随每日 bundle 归档，无需加规则。

## 6. 发现但要**报请**的既存问题（不顺手修）

1. `src/vitals.py` 有**未提交**改动（他会话 11:49 改的门禁修复：`menu_noul` 判定去掉 `source=='jev'` 前提）。实测对当前菜单**无影响**（usable 项 noul 全 ≥0.87，not_installed 仍被前置分支拦），但它使 `code_matches_head=false`，且属“改了共享运行时未归档”。⇒ 建议单独提交，需你点头。
2. 侧栏 `collapsed` 与 B 路 240px 属同一文件族，同仓并发风险仍在（工作区另有 A 路未提交历史）。⇒ 提交前先 `git diff --stat` 复核归属。
3. **不确定项（须你确认）**：我这轮定位的白遮挡发生在**窄屏**。如果你实际看到的异常在桌面宽度，那是**另一个**缺陷，请给我宽度+现象（哪块区域、什么颜色/空白/错位），我按同一流程重查，不拿本结论交差。

## 7. 我不会做的事

不重启 agent-hub（纯静态生效）、不动 A 路/B 路已完成成果、不碰 CCR/FCC/pi 全局配置、不写 crontab、不 `sed -i` 共享配置、不在生产上做注入实验（红绿对照只在 headless 客户端侧）。
