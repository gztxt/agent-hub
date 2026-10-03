/**
 * hub-facade.ts — agent-hub 门面检索接入（批5 ③ 路，2026-09-26）
 *
 * 与 claude/codex 走 MCP（/hub-mcp/mcp + token）不同，pi 走 REST 直连：
 *   · 只做 GET 检索（writeauth 只拦写方法）⇒ 零凭据、零配置；
 *   · 延迟比 MCP 握手低（一次 fetch vs initialize+tools/list 两轮）；
 *   · 对生产旧码（v0.13.25）向后兼容：只调批1 之前就存在的端点
 *     （/api/memory/search、/api/kb/search、/api/skill/list、/api/kb/status），
 *     批2~4 新端点不依赖——生产重启后自动获得全路，无需改本文件。
 *
 * 性能口径沿用 claude-mem-bridge.ts：input 事件检索有超时预算、不阻塞，
 * context 事件注入一次性消费；任何失败静默（绝不影响用户输入）。
 *
 * v0.13.62 D1.5：input 预算 150ms → 600ms。理由：后端 `/api/memory/search` 的默认
 * sources 已从 local,tdai 升到快路联邦集（并联 ≈300ms），150ms 会让 pi 每次预检索
 * **必然超时静默丢弃**——那等于把分裂从「没接」变成「接了但永远拿不到」。
 * 600ms 覆盖快路并联墙钟且留余量；「不卡住用户输入」这条口径不变（超时仍静默）。
 *
 * v0.13.69 D4：input 钩子除记忆外**并联**检索 `/api/skill/relevant`，把 top-3 技能候选
 * 追加进同一个注入块（名字 + 一句用途 + 命中词，**不推正文**）。三条口径：
 *   ① **不新增第二个检索出口**——仍走 hubGet 这一个通道，保持「失败静默、不阻塞用户输入」。
 *   ② **rerank=false 是硬要求，不是偏好**。实测该端点 rerank=true 时 took_ms=1064.5ms
 *      （jev 外部调用），而本钩子预算 600ms ⇒ 开 jev 必然每次超时静默丢弃，
 *      等于「接了但永远拿不到」。BM25 层实测 12~53ms。
 *   ③ **并联而非串行**：串行墙钟 = 600+500 = 1100ms，600ms 预算口径名存实亡。
 *      且必须 **Promise.all 等两边都落定再写 pendingHit**——pi 的 context 钩子在
 *      input 返回后立即消费它，用 `.then()` 挂后台会错过那一拍（第一版就踩了）。
 *
 * rev 自证：pi extensionCache 不看 mtime，长命进程里旧代码活到重启——
 * 加版本号让 pi 启动日志可断言「进程内存里是哪一版」。
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const HUB = process.env.HUB_FACADE_URL || "http://127.0.0.1:3102";

/** hub REST GET（超时 ms，失败返回 null） */
async function hubGet(path: string, timeoutMs = 150): Promise<Record<string, unknown> | null> {
  try {
    const c = new AbortController();
    const t = setTimeout(() => c.abort(), timeoutMs);
    const r = await fetch(HUB + path, { signal: c.signal });
    clearTimeout(t);
    if (!r.ok) return null;
    return (await r.json()) as Record<string, unknown>;
  } catch { return null; }
}

/** 通用 404 友好降级：旧后端没这个端点时不说「挂了」，说「后端未更新」 */
async function hubGetFriendly(path: string, timeoutMs = 3000): Promise<{ data: Record<string, unknown> | null; stale: boolean }> {
  try {
    const c = new AbortController();
    const t = setTimeout(() => c.abort(), timeoutMs);
    const r = await fetch(HUB + path, { signal: c.signal });
    clearTimeout(t);
    if (r.status === 404) return { data: null, stale: true };
    if (!r.ok) return { data: null, stale: false };
    return { data: (await r.json()) as Record<string, unknown>, stale: false };
  } catch { return { data: null, stale: false }; }
}

// ─── 自动注入（input → context，与 claude-mem-bridge 同构）───

let pendingHit: string | null = null;

function fmtMem(items: unknown[], n: number): string {
  return items.slice(0, n).map((it) => {
    const m = it as { content?: string; summary?: string; category?: string; created_at?: string };
    const text = String(m.content || m.summary || "").slice(0, 160);
    const meta = [m.category, m.created_at ? String(m.created_at).slice(0, 10) : ""].filter(Boolean).join(" ");
    return `· [${meta}] ${text}`;
  }).join("\n");
}

/** D4：技能候选块。**只推「名字 + 一句用途 + 命中词」，不推正文**——
 * 正文由 agent 自己决定要不要读（/hub-skills <名字> 或直接 Read 磁盘路径）。
 * 推正文等于替 agent 做「这条技能值不值得读」的判断，而 hub 只知道词面相关。 */
function fmtSkills(d: Record<string, unknown>): string {
  const bm25 = d.bm25 as { items?: Array<{ name?: string; description?: string; matched?: string[] }> } | undefined;
  const items = bm25?.items || [];
  if (!items.length) return "";
  const lines = items.map((s) => {
    const desc = String(s.description || "").split(/[。\n]/)[0].slice(0, 70);
    const hit = (s.matched || []).slice(0, 4).join(" ");
    return `· ${s.name} — ${desc}${hit ? `（命中：${hit}）` : ""}`;
  }).join("\n");
  return `## 可能用得上的技能（hub BM25 top-${items.length} · 只给线索，正文自己读：/hub-skills <名字>）\n${lines}`;
}

function fmtKb(items: unknown[], n: number): string {
  return items.slice(0, n).map((it) => {
    const r = it as { source?: string; path?: string; title?: string; snippet?: string; text?: string };
    const from = r.source || r.path || r.title || "?";
    const text = String(r.snippet || r.text || "").slice(0, 140);
    return `· [${from}] ${text}`;
  }).join("\n");
}

export default function (pi: ExtensionAPI) {
  // 用户输入：抓关键词异步检索 hub 记忆（600ms 预算，超时静默放弃）
  pi.on("input", async (event) => {
    try {
      const prompt = (event.text ?? "").trim();
      if (prompt.length < 4 || prompt.startsWith("/")) return;
      const kw = prompt.replace(/[的了吗呢是吧在给把被让向从对到和与或]/g, " ")
        .split(/\s+/).filter((w) => w.length >= 2);
      if (kw.length === 0) return;
      // **不传 sources**：由后端默认（memory.FED_FAST_SOURCES）说了算。两处各写一份默认值
      // 就是一处会漂的常量，而 D1.5 要修的病根正是「各端默认值不一致」。
      // D4：记忆与技能**并联**，等两边都落定再写 pendingHit。
      // 第一版写成了「记忆 await 完 + 技能 .then() 挂后台」——那是**假并联**：
      // pi 的 context 钩子在 input 返回后就会消费 pendingHit，技能块大概率在
      // 它被读走之后才 resolve，等于接了个寂寞。Promise.all 才能保证两段都在同一拍落地。
      const [mem, sk] = await Promise.all([
        hubGet(`/api/memory/search?q=${encodeURIComponent(kw[0])}&limit=2`, 600),
        hubGet(`/api/skill/relevant?q=${encodeURIComponent(prompt)}&n=3&max_tokens=600&rerank=false`, 500),
      ]);
      const parts: string[] = [];
      if (mem && Array.isArray(mem.memories) && (mem.memories as unknown[]).length > 0) {
        parts.push(`## hub 相关记忆（${kw[0]}）\n${fmtMem(mem.memories as unknown[], 2)}`);
      }
      // 技能检索失败/超时只是少一条线索，静默丢弃即可（不弹错、不重试、不计时）。
      if (sk) {
        const block = fmtSkills(sk);
        if (block) parts.push(block);
      }
      if (parts.length) pendingHit = parts.join("\n\n");
    } catch { /* 检索失败绝不影响用户输入 */ }
  });

  // LLM 调用前：一次性注入（每次输入只注入一轮，与蓝本口径一致）
  pi.on("context", async (event) => {
    if (!pendingHit) return;
    const mem = pendingHit;
    pendingHit = null;
    for (let i = event.messages.length - 1; i >= 0; i--) {
      const m = event.messages[i] as { role?: string; content?: unknown };
      if (m?.role === "user" && Array.isArray(m.content)) {
        (m.content as Array<Record<string, unknown>>).push({ type: "text", text: mem });
        break;
      }
    }
    return { messages: event.messages };
  });

  // 命令：/hub-recall <关键词> —— 跨记忆+知识库联邦检索
  pi.registerCommand("hub-recall", {
    description: "hub 联邦检索（记忆+知识库）: /hub-recall <关键词>",
    handler: async (args, ctx) => {
      const q = (args ?? "").trim();
      if (!q) { ctx.ui.notify("用法: /hub-recall <关键词>", "warning"); return; }
      ctx.ui.notify("🔍 hub 检索中…", "info");
      const [mem, kb] = await Promise.all([
        hubGetFriendly(`/api/memory/search?q=${encodeURIComponent(q)}&limit=4`, 3000),
        hubGetFriendly(`/api/kb/search?q=${encodeURIComponent(q)}&k=5`, 3000),
      ]);
      const parts: string[] = [];
      const memN = mem.data ? ((mem.data.memories as unknown[]) || []).length : 0;
      if (mem.data && memN > 0) {
        parts.push(`### 记忆（${memN} 条）\n${fmtMem(mem.data.memories as unknown[], 4)}`);
      } else if (mem.data) {
        parts.push("### 记忆：零命中");
      } else {
        parts.push(`### 记忆：不可用${mem.stale ? "（后端未更新，需重启 agent-hub.service）" : "（hub 不可达）"}`);
      }
      const kbN = kb.data ? ((kb.data.results as unknown[]) || []).length : 0;
      const kbEngine = kb.data ? String(kb.data.engine || "") : "";
      if (kb.data && kbN > 0) {
        parts.push(`### 知识库（${kbN} 条 · ${kbEngine}）\n${fmtKb(kb.data.results as unknown[], 5)}`);
      } else if (kb.data) {
        parts.push(`### 知识库：零命中（${kbEngine}）`);
      } else {
        parts.push(`### 知识库：不可用${kb.stale ? "（后端未更新）" : "（hub 不可达）"}`);
      }
      pi.sendMessage(
        { customType: "hub-recall", content: `## hub 检索：${q}\n\n${parts.join("\n\n")}`, display: true },
        { deliverAs: "nextTurn" },
      );
      ctx.ui.notify(`✅ 记忆 ${memN} 条 / 知识库 ${kbN} 条`, "info");
    },
  });

  // 命令：/hub-skills [关键词] —— 本机技能清单（走批2 端点，4/7 路按后端版本自适应）
  pi.registerCommand("hub-skills", {
    description: "hub 技能清单: /hub-skills [过滤词]",
    handler: async (args, ctx) => {
      const q = (args ?? "").trim();
      const { data, stale } = await hubGetFriendly(`/api/skill/list${q ? `?q=${encodeURIComponent(q)}` : "?limit=30"}&limit=30`, 3000);
      if (!data) {
        ctx.ui.notify(stale ? "后端未更新（需重启 agent-hub.service 后生效）" : "hub 不可达", "warning");
        return;
      }
      const items = (data.items as Array<{ name?: string; description?: string; route?: string }>) || [];
      if (items.length === 0) { ctx.ui.notify("零命中", "warning"); return; }
      const lines = items.slice(0, 30).map((s) =>
        `· [${s.route || "?"}] ${s.name} — ${String(s.description || "").slice(0, 100)}`).join("\n");
      pi.sendMessage(
        { customType: "hub-skills", content: `## hub 技能清单（${items.length} 条）\n${lines}`, display: true },
        { deliverAs: "nextTurn" },
      );
      ctx.ui.notify(`✅ ${items.length} 个技能`, "info");
    },
  });

  // 命令：/hub-health —— 门面各路健康（一次拉全，降级路点名）
  pi.registerCommand("hub-health", {
    description: "hub 门面健康（记忆/知识库/技能各路表态）",
    handler: async (_args, ctx) => {
      const [h, kb, sk] = await Promise.all([
        hubGetFriendly("/health", 2000),
        hubGetFriendly("/api/kb/status", 2000),
        hubGetFriendly("/api/skill/status", 2000),
      ]);
      const seg: string[] = [];
      const mb = h.data ? (h.data as { memory_backend?: { state?: string; l1_total?: number } }).memory_backend : null;
      seg.push(`### 记忆：${mb ? `${mb.state ?? "?"}（L1=${mb.l1_total ?? "?"}）` : h.stale ? "端点缺失" : "不可达"}`);
      const kbSt = kb.data ? Object.entries(kb.data)
        .filter(([, v]) => v && typeof v === "object" && "available" in (v as object))
        .map(([k, v]) => `${k}:${(v as { available?: boolean }).available ? "✓" : "✗"}`).join(" ") : "";
      seg.push(`### 知识库：${kbSt || (kb.stale ? "端点缺失" : "不可达")}`);
      const skSt = sk.data ? Object.entries(sk.data)
        .filter(([, v]) => v && typeof v === "object" && "ok" in (v as object))
        .map(([k, v]) => `${k}:${(v as { ok?: boolean }).ok ? "✓" : "✗"}`).join(" ") : "";
      seg.push(`### 技能：${skSt || (sk.stale ? "端点缺失" : "不可达")}`);
      pi.sendMessage(
        { customType: "hub-health", content: `## hub 门面健康\n\n${seg.join("\n\n")}`, display: true },
        { deliverAs: "nextTurn" },
      );
      ctx.ui.notify("✅ 健康快照已注入", "info");
    },
  });

  console.log("[hub-facade] ✅ hub 门面已加载 (REST 直连 127.0.0.1:3102 | rev=20261003a-D4 | input预算=600ms | 技能注入=rerank:false n:3 并联)");
}
