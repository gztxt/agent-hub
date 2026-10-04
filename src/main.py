"""Agent Hub - 主入口（Agent_Manager 融合版 v0.3.0）

融合自 Zafer-Liu/Agent_Manager (Apache-2.0) 的设计与语义：
- Hook 遥测端点（agent_http.rs → src/hook.py）
- 三层记忆中心（memory 子系统 → src/memory.py）
- 端口管理（ports.rs → src/ports.py，只读，无 kill —— NAS 军规）
- 项目类型自动识别（agent_sources.rs → src/sources.py）
"""
import asyncio
import hmac
import json
import os
import re
import sys
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

# 必须在导入 config 前加载 .env
from dotenv import load_dotenv
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path)
    print(f"[Agent Hub] 已加载 .env: {env_path}")

import aiohttp
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent))
import db
import modelcfg
from config import config
from discovery import AgentDiscovery, AgentInfo
from ports import list_listeners, port_in_use
from sources import detect_project
import scanner
from writeauth import write_gate
from registry import build_adapters, get_adapter
import hook as hook_mod
import memory as memory_mod
import memfed as memfed_mod
import kb as kb_mod
import skill as skill_mod
import tasks as tasks_mod
import mcpgw as mcpgw_mod
import cronjobs as cronjobs_mod
import term as term_mod
import profiles as profiles_mod
import vitals as vitals_mod
import embed_proxy as embed_proxy_mod
import selfattest
import staticguard
import tdai_client
import gwprobe                      # 上游网关（CCR）连通性 + 模型注册清单的缓存式体检
import healthx                      # /health 派生量的纯函数层（L0 不 import src.main，故抽出来）
import sessions_export as export_mod
import writeauth                    # 导出端点按写端点同等鉴权（复用 decide 的 fail-closed）
import audit as audit_mod           # 资产变更审计的只读查询门面（GET /api/audit/list）
import runlog as runlog_mod         # 运行日志：三中心检索留痕 + GET /api/runlog 查询门面
import cloudcli as cloudcli_mod     # CloudCLI 项目直达：项目清单（直读 auth.db）+ 会话启动代理
import localprojects as localprojects_mod  # 本机项目清单（多根 git 扫描 + cloudcli 合并）
import resources as resources_mod          # 资源监控：Agent 进程资源列表 + 一键结束
import githubprojects as github_mod  # GitHub 远端仓库清单 + 即时克隆（本机项目页的远端半程）
import prefs as prefs_mod           # 应用级偏好 KV（v0.13.36）：两项目页收藏/隐藏落服务端
import modelcfg as modelcfg_mod     # 各 Agent 默认模型统一设置（v0.13.41：设置→模型子菜单）
import ghsettings as ghsettings_mod  # GitHub 地址/key/落点（v0.13.42：设置→GitHub 子菜单）
import hublog as hublog_mod        # 日志中心（v0.13.46：设置→日志子菜单，journald + 操作事件聚合）

print(f"[Agent Hub] 配置: PORT={config.port}, HOST={config.host}")

# 单一版本源：/health、FastAPI 元数据、启动横幅与页脚都取这里
VERSION = "0.13.77"   # 行内 markdown 白名单渲染（mdInline）+ 收起态徽标未隐藏。两条：
                      #   ① **技能描述按不可信输入处理，只渲染两个行内标记**。
                      #     描述取自 `SKILL.md` 的 frontmatter，而这些文件来自 **20+ 个发现点**，
                      #     含第三方仓（mattpocock-skills / hallmark / Agent-Reach / crawl4ai）
                      #     —— 它们是**外部内容**，不是本仓自己写的文案。hub 的 origin 里有
                      #     **终端**（能起 pty），所以「渲染 markdown」必须按处理不可信输入做：
                      #     能写进 frontmatter 的恶意描述一旦渲染成 `<img onerror=…>` 或
                      #     `<a href="javascript:…">`，就是**存储型 XSS** 且能直接摸到终端。
                      #     口径：只认 `**粗体**` 与 `` `行内代码` ``；链接/图片/标题/列表/
                      #     原始 HTML 一律**不渲染**（保持转义后的字面文本）。
                      #     **顺序不可颠倒**：先 `escapeHtml` 再替换 —— 串里不再有裸 `< > & "`，
                      #     此后插入的 `<strong>`/`<code>` 是**唯一**由我们放进去的标签。
                      #     **不做斜体**：`_italic_` 会把 `snake_case_name`、`*.py` 吃成斜体，
                      #     技能描述里标识符与路径很常见，是实打实的误伤。
                      #   ② **收起态侧栏的「总览 10」徽标没隐藏**（用户 2026-10-04 报）：
                      #     收起态隐藏规则写的是 `.badge`，而侧栏徽标的**真实类名是 `.nav-badge`**
                      #     ⇒ 选择器对不上，CSS **静默不生效**，48px 图标条上一直挤着个「10」。
                      #     与 09-24 那次方向相反：那次是**写出来的**类名不存在，这次是
                      #     **规则里的**类名不存在。两者都不报错，都只是安静地什么都不做。
                      # ── 以下为 v0.13.76 的根因，保留供追溯，非本版条目 ──
                      #   ① **抽屉永远打不开、而遮罩照亮 = 整页锁死**（用户 2026-10-04 报
                      #     「窄屏左侧菜单栏展开不正常」）。实为 v0.13.75 的回归：
                      #     `<head>` 里的 `narrow-rail` 是**首帧专用**标记（它让窄屏首屏
                      #     就是图标条，而不是 236px 白板盖住 60~74% 视口），
                      #     但我当初**打完就没再摘**，于是它变成永久标记。
                      #     `html.narrow-rail .sidebar:not(.collapsed)` 的**特异性高于**
                      #     `.sidebar:not(.collapsed)` ⇒ 用户点「展开」时 JS 确实移除了
                      #     `collapsed`，几何却仍被按回 52px 图标条。
                      #     实测：展开后 `class=sidebar` 但 `width=52px / position:relative`
                      #     （应为 236px/fixed），而遮罩 `on` ⇒ **点哪都点不到**。
                      #     修法：**JS 一接管就摘标记**，语义回到本意「JS 还没跑（或没跑起来）」；
                      #     失败模式也是对的 —— bundle 挂了则标记留下，窄屏仍是可读图标条，
                      #     而不是一块盖住大半屏的白板。
                      #   ② **探针上一版为什么没抓到**：它只量**首帧**，从来没点过开。
                      #     首帧是对的、交互是坏的 —— 这正是「只验一个时刻」的盲区。
                      #     已给 `probe_narrow_layout.py` 加第五组量「点开后抽屉(宽/定位/遮罩)」，
                      #     验证它会咬：修前五档全红（52px/relative），修后 236px/fixed 全绿。
                      #     另加静态闸门 `test_marker_must_be_removed_when_js_takes_over`
                      #     （只管「打」不管「摘」的补丁等于永久补丁）。
                      # ── 以下为 v0.13.75 的根因，保留供追溯，非本版条目 ──
                      #   ① **技能中心在窄屏每个字一行**（2026-10-04 用户真机截图）：
                      #     窄屏 CSS 是 `.sidebar:not(.collapsed)` 那类**flex 挤压**的同族 ——
                      #     `.mem-item` 是 `display:flex` + **nowrap**，`<p>` 是 `flex:1`(1 1 0%)。
                      #     实测 390px：容器 296 = 名字 141 + 操作区 128 + gap 16 ⇒ 描述只剩 **3px**
                      #     （900px 时是 275px），于是「行)、小 / 字母描 / 客转文 / 字、」这样一行一字。
                      #     **本仓对同类结构修过一次**（`.sp-list > .mem-item > p{min-width:0}`，
                      #     第 618 行），但**技能中心列表不在 `.sp-list` 里** ⇒ 只修了一处。
                      #     这是「按选择器修 bug」的典型漏网：修的是那一个选择器，不是**那一类形态**。
                      #     窄屏口径：`flex-wrap:wrap` + 描述 `flex:1 1 100%;min-width:0` 独占整行
                      #     + 操作区右对齐 + 空描述 `:empty{display:none}`。
                      #     实测 390px 描述 **3px → 292px**；assets 页无回归（两版 266px）。
                      #   ② **真渲染探针入库** `tests/probe_narrow_layout.py`（CDP）：
                      #     量首帧几何 / 技能描述宽 / 逐页最窄描述 / 底部留白四组可断言的量。
                      #     为什么要它：静态闸门只判「机制写对了没」，**判不了像素**——
                      #     CSS 写错类名、flex 基准给错、首帧竞态，这三类**都不会让任何静态测试变红**。
                      #     已验证它会咬：修前 `EXIT=1`（320/360/390/412 四档红，描述 0~20px），
                      #     修后 `EXIT=0`（五档全 OK）。
                      #     探针自身也踩了两次坑（已修）：就绪判定把字符串 `'{}'` 当有效值（truthy）
                      #     ⇒ 假绿；底部留白在切到 200 行技能列表**之后**才量，`.page.on` 仍是那页
                      #     ⇒ 输出 -50811px 废话。
                      #     底部留白**只报不判**：上一轮试过把它均匀分布到各块之间，
                      #     截图一看**更丑**（空白跑到页面中段，把图例和操作按钮割开），已回退。
                      #     「短页面底部有留白」是正常形态，不是缺陷。
                      # ── 以下为 v0.13.74 的根因，保留供追溯，非本版条目 ──
                      #   ① **窄屏第一眼是一块白板**（09-23 事故形态复现，生产实测）：
                      #   ① **窄屏第一眼是一块白板**（09-23 事故形态复现，生产实测）：
                      #     窄屏 CSS 是 `.sidebar:not(.collapsed){position:fixed;width:236px;z-index:46}`，
                      #     而 `<aside>` 初始**没有** `collapsed` 类 —— 那要等 bundle 末尾的
                      #     `initSidebar()` 才加上。实测用户第一眼看到 **236px 盖住
                      #     60.5%(390px)~73.8%(320px)** 的视口，正文被从中间切断。
                      #     既有闸门 `verify_narrow_default_iconbar.py` 断言的是**稳定后**
                      #     collapsed=true，所以这段一直没人管 —— 缺的不是某条规则的正确性，
                      #     是**「首帧」这个时刻压根没有判据**（新缺口，不在原五条不变量内）。
                      #     修法：`<head>` 里按当前视口给 `<html>` 挂 `narrow-rail`，
                      #     CSS 用 `html.narrow-rail .sidebar:not(.collapsed)` 把**首帧**那一格
                      #     从抽屉改成图标条。**为什么必须在 <head>**：第一版写在 `<aside>` 的
                      #     首个子节点，320px 生效但 **390px 仍闪** —— 浏览器可在解析到开标签后、
                      #     跑完该脚本前完成首次绘制（竞态，两档结果不一致）。移进 <head> 后
                      #     body 尚不存在，无物可绘 ⇒ 竞态从根上不存在。
                      #     真渲染复验：首帧 **236px/fixed → 52px/relative**，五档全 OK。
                      #     另修：顶栏状态字被视口右缘**切断**（无省略号）、窄屏字号/行长、
                      #     图标栏按钮**没有 title/aria-label**（收起态 .lbl 是 display:none，
                      #     手机又无 hover ⇒ 既无可见文字也无可访问名）、
                      #     以及那段描述「手风琴 + 顶部搜索框」的文案在窄屏白占 1/3 屏高
                      #     （窄屏根本没有手风琴）—— 改为 CSS 断点分两份文案。
                      #   ② `/api/skill/list?q=`：匹配字段补 `realpath`（此前搜不到
                      #     「按来源仓名找技能」，`q=mattpocock` 命中 0，因 `path` 是软链那一侧），
                      #     并改为按分数排序（精确在前、近似在后，与前端一致）。
                      #     对账闸门同步加强为**比对分数**而非只比命中/不命中。
                      #     过程中抓到一处真漂移：JS 的分隔符类含 `，`/`、`/`，Python 早先没有
                      #     ⇒ 少切词。已补齐，并把「夹具必须判别」也做成闸门
                      #     （第一版中文标点夹具走的是精确子串快路，压根没进切词逻辑，删掉漂移照样绿）。
                      # ── 以下为 v0.13.73 的根因，保留供追溯，非本版条目 ──
                      #   ① **同一台机器两套口径**：v0.13.72 把前端搜索框改成模糊后，`q` 仍是纯子串
                      #   ① **同一台机器两套口径**：v0.13.72 把前端搜索框改成模糊后，`q` 仍是纯子串
                      #     ⇒ UI 里搜得到，而走 `/api/skill/list?q=` 的两条链路
                      #     （**MCP 门面 `hubmcp.py`——pi/Claude 注入真正走的那条**、
                      #     资产面板三路检索 `07-asset-panel.js`）搜不到。
                      #     `q` 已改为：先精确子串，再**逐字段**算编辑距离，容忍度阶梯
                      #     （≤4 不容忍 / ≥5 容忍 1 / ≥8 容忍 2）与前端逐字一致。
                      #     刻意**不**把四字段拼成大串再算距离：拼接后分隔符会抵掉失配。
                      #     **只改「在不在结果里」，不改排序**——改排序会影响 MCP 门面既有
                      #     消费方，属另一个决定，不在本版悄悄带上。
                      #   ② **两份实现靠闸门锁住，不靠自觉**：Python 与 JS 各写一份（语言不通，
                      #     无法共用），「口径同源」的可执行定义是「两侧对**同一批夹具**给出
                      #     同一个判定」。`tests/test_skill_list_q_parity.py` 把 12 条夹具分别喂给
                      #     node 与 Python 逐条比对，两侧一致**且**都要符合夹具声明的期望
                      #     （防「两侧一起错」也绿）。
                      #     已用注入回归验证它真会咬：把 Python 退回纯子串 ⇒ 6 红；
                      #     把容忍度阶梯改错一格 ⇒ 1 红；还原 ⇒ 全绿。
                      # ── 以下为 v0.13.72 的根因，保留供追溯，非本版条目 ──
                      #   ① **模糊匹配不再是「技能中心专属」**：搜 `crawl1ai`（数字 1）找不到
                      #     `crawl4ai` 这类手误，此前只有技能中心改了；本版把 `fuzzyMatch` 从
                      #     `04-terminal-ws` 搬进 `01-core-boot`（它被 5 个分片用，挂在 terminal
                      #     那一片会让下一个找它的人以为「只有终端用」），并接入全部搜索框：
                      #     侧栏搜索 / agents 命令面板 / 端口表 / 本地项目 / GitHub 项目 / 技能中心。
                      #     **口径不变**：精确子串恒 1000 分压倒近似，短查询(<5字)不容忍编辑距离，
                      #     近似行标 `≈近似` 徽标——面板必须能回答「为什么这条出现在这里」。
                      #     ⚠ 连带改判上版说法：上版汇报称「还有 6 处纯 includes()」，实测只有
                      #     **5 处**——「终端历史」与「经理任务」**根本没有客户端搜索框**
                      #     （前者只有成员判断、后者无输入框），那个 6 是我数错的。
                      #   ② **一个恒真的 L0 用例**：`test_skill_visibility_sync` 的干跑用例原先
                      #     直接 `os.listdir($HOME/.claude/skills)`（→ `hermetic-clean` 档报
                      #     `FileNotFoundError`，因为假 HOME 里那个目录本就不存在）。改成「不存在
                      #     就当空」虽然不报错，但**仍是恒真**：把 `apply_plan` 挪到 `--apply`
                      #     判断之前（制造「干跑却真写了」的回归）重跑，测试依然绿——
                      #     因为 `os.symlink` 在父目录不存在时抛 `FileNotFoundError`，被
                      #     `except OSError` 吞进 `failed`，那个环境下**想写也写不成**。
                      #     ⇒ 改为「测试自建源与目标目录，且源里真有 2 条待链技能」，
                      #     并补一条**前提守卫**先证明真写确实写得进去。
                      #     已用注入回归验证：改坏时红、还原时绿（不验证就会把假绿当修好）。
                      # ── 以下为 v0.13.71 的根因，保留供追溯，非本版条目 ──
                      #   ① **归档根与发现点是两张表**：`_DEFAULT_DIRS` 答「各 CLI 自己会读哪」，
                      #     而 09-25 装的 hallmark / mattpocock-skills / Agent-Reach 与既有的
                      #     crawl4ai，其 `SKILL.md` 都在 `技术文档/<仓>/…`。`_inside()` 按 realpath
                      #     判 ⇒ 这四仓的软链被自己的防越界闸门拒读，**实测 62 条**（与
                      #     PT-20261002-13 记的数字逐条对上，现已清零）。口径依据归档军规
                      #     「源码唯一权威副本必须落在 `技术文档/<项目名>/`」。
                      #     刻意**不**把整个 `技术文档/` 当根：那会架空 `EXCLUDED_DIRS` 已定的
                      #     snapshots(664) / 全量备份(247) / `.orca-audit` / `Hermes-backup` 四条结论。
                      #   ② **搜索框的纯 includes() 让手误等于不存在**：搜 `crawl1ai`（数字 1）
                      #     找不到 `crawl4ai`。改为「精确优先（恒 1000 分，压倒近似）+ 编辑距离
                      #     近似」，近似行标 `≈近似` 徽标 —— 面板必须能回答「为什么这条出现在这里」。
                      # ── 以下为 v0.13.70（D5/D6）时的根因，保留供追溯，非本版条目 ──
                      #   ① **退出码会让 Claude 拒绝输入**：UserPromptSubmit 同步阻塞在用户
                      #     输入之前，hook 返回非零就是把「技能没检索到」升级成「用户发不出
                      #     消息」⇒ 脚本吞掉一切异常并 exit 0。实测 6 种形态（正常 / 不可达 /
                      #     404 旧后端 / 垃圾 stdin / 空 stdin / 斜杠命令）全部 exit=0 stderr=0；
                      #     对生产 v0.13.65 打过去是 404⇒静默⇒升级前不干扰 Claude。
                      #     注入链路此前只有 pi 一条，Claude 连检索出口都没有（skill.read 恒 0）。
                      #     与 D4 同参同源：都打 /api/skill/relevant、都 rerank=false
                      #     （rerank=true 实测 took_ms=1064.5ms，两条通道预算都小于它）。
                      #     改法：外科式文本插入 + 写前在内存里验「别人的每个键的值未变」——
                      #     该文件今早被 3 个别的会话写过（06:50/07:00/09:22）。
                      #   ② **窄授权不得读成宽授权**：`pi` 路主动扣下。`~/.pi/agent/**` 是受
                      #     保护面，用户给的是单文件授权（hub-facade.ts）而非整棵树。
                      #   ③ **禁改面拒绝**并说明原因（jcode/hermes/qwenpaw/codebuddy），
                      #     不是静默跳过。
                      #   ④ **报冲突不覆盖**：已存在但指向别处的一律跳过——覆盖会毁掉别人
                      #     手工做的链接。只软链不复制、同 realpath 幂等、默认 dry-run。
                      #     opencode 由 B3 证伪（不扫 ~/.config/opencode/skill），不作目标；
                      #     它真正扫的 claude+agents 两路已在表里 ⇒ 顺带覆盖。
                      #     实测四路建成 66 条，幂等复跑 0 新增 / 72 已就位 / 0 冲突。
                      #   闸门 tests/test_skill_visibility_sync.py（L0 12 例，含「干跑不得改动
                      #     真实 Agent 目录」与「冲突不得被覆盖」两条负向用例）。
                      #
                      #   （以下为 0.13.69 D7 技能中心前端改版）
                      #                      #   病根：页面里**存着第二个真相**——技能页长期写死「7 路发现点」，
                      #     而 D1 早已把路由改成 20 路。第二个真相比第一个危险，因为它
                      #     看起来永远正确、不会随后端变，只能靠人去发现它过期。
                      #   本版：① 删掉写死文案，路数/条数一律由 /api/skill/list 的
                      #     SKILL_ROUTES.length 与 items.length 现算；
                      #     ② 新增发现点自检（/api/skill/status：四态 + 排除段 + 拒读数）；
                      #     ③ 新增相关性实验室（/api/skill/relevant：命中词 + 耗时 + jev 状态）；
                      #     ④ 注入预算卡补进度条与「被裁 = agent 看不到它」；
                      #     ⑤ 列表加「N 天零调用」徽章与「只看零调用」筛选。
                      #   **置信度封顶必须在界面上写出来**：零调用榜的 medium 藏起来
                      #     就会被读成 high（d.direct_source=not-implemented 同理）。
                      #   **闸门 tests/test_skill_center_ui.py 改为 L0**（计划书原写 verify_*）：
                      #     仓内 README 的 verify_/probe_ 属 L2 live、需服务、手工单跑、
                      #     不被 discover -p "test_*.py" 收进来 ⇒ 按那个名字写的东西
                      #     **在提交时根本不会跑**，那不叫闸门叫摆设。
                      #   **闸门自己蒙对过一次，已修**：首版 rerank 断言拿注释里的
                      #     「默认 rerank=false」字样去过，而代码里是
                                      #     `'&n=5&rerank=' + (useJev ? 'true' : 'false')`，并无该字面量。
                      #     「文字存在 ≠ 已生效」那一族（TDZ / vitals_loop / 跨档镜像声明 同族）——
                      #     **蒙对的闸门比没闸门更危险**。现改为剥注释后判代码，
                      #     并补「jev 开关出厂不得带 checked」一条；两条均已反向验证会红。
                      #   窄屏：未新增任何断点值，复用 01-core-boot.js 的 HUB_NARROW_MQ
                      #     （分档偏好不变量③）；新增 .sk-budget 预算条用独立类名，
                      #     不复用 .bar/.chip 以免改坏别处。
                      #
                      #   （以下为 0.13.68 D3 技能调用记账与零调用僵尸榜）
                      #                      #   病根：本批只接了 hub 通道（profile_events 里 source='rest' 的
                      #     skill.read / skill.inject），各家 agent **直读自己技能目录的旁路
                      #     统计未实现**。设计书 §7 的口径是「两源皆零 → confidence=high」，
                      #     而第二源不存在时那条口径不成立——照抄会得到一个看着确定、实际是
                      #     仪表盘盲区自欺的榜。故本批**把封顶值做成可断言字段**：
                      #     每行 confidence 恒 medium + direct_source="not-implemented"，
                      #     顶层再回显 counted（账里有痕迹的技能数，0 = 压根没记账），
                      #     前端与测试据此区分「真的没人用」和「没有仪表盘」。
                      #   src/skill_usage.py  counts()/zombies()/snapshot()：
                      #     读不到 DB **绝不抛**（面板要能开），回落方向保守——多提醒不漏提醒；
                      #     零调用技能**绝不自动删**，只出 suggested_action，且单路可见优先
                      #     widen_visibility（它可能压根没机会被选中，不能先判它该退）。
                      #   闸门 tests/test_skill_usage.py（L0 29 例，用假 db 模块驱动 counts()
                      #     顺带断言 SQL 参数形状：真 API 是 db.query，计划书里的 db.fetchall
                      #     并不存在，照抄会直接 AttributeError）。
                      #   runlog.SUBJECTS 增 skill.relevant / skill.inject（D4/D5 注入链记账用）。
                      #   B3 侦察反证 D1 第 20 路 opencode 是**假发现点**（opencode 1.18.34
                      #     只扫 ~/.claude/skills、~/.agents/skills、项目 {skill,skills} 与配置键
                      #     skills.paths；opencode.json 无 skills 键 ⇒ 接线不存在），
                      #     顺带量出 62 条第三方技能对 agent-hub 不可见 ⇒ 已登记
                      #     **PT-20261002-13**，本批不夹带（它动生产常量与安全白名单）。
                      #   生产未重启（重启授权留到 D3 之后一次执行）。
                      #
                      #   （以下为 0.13.67 D2 技能相关性检索：GET /api/skill/relevant）   病根：/api/skill/list?q= 只是大小写不敏感**子串**过滤，
                      #     「说一段任务描述 → 找回对的技能」机制上不存在；而 hub-facade.ts
                      #     的 input→context 通道只能拿到记忆（skill.read=0），没有技能候选可注入。
                      #   src/skill_relevance.py  零新依赖 BM25F：ASCII 按词 + 连字符名额外拆子词，
                      #     CJK 只出相邻二字（单字查询走长度为 1 的回落）；W_NAME 2.5 / W_DESC 1.0。
                      #   src/jev_client.py  jev 异步精排：8s 超时、600s 缓存、连续 2 次失败熔断，
                      #     **不进必成功关键路径**。依据实测：choice 中文置信常 1.00 而 score
                      #     主观刻度掉到 ~0.3 ⇒ jev 只逐行回传分数与置信度，**不改写排序**，
                      #     排序权威仍是 BM25。
                      #   闸门 tests/test_skill_relevance.py（L0 37 例，含端点级 tmp 根 TestClient）。
                      #   **两处真实盘取证修正**（分词器的错不抛异常，只安静地少召回）：
                      #     ① 同时出单字+二字时高频字（能/不/登）各带 IDF 累加成噪声，把
                      #       arkcli-auth/deploy 抬进“登录态加载不出但能新建”的前四名；
                      #     ② _LATIN_RE 把 '-' 当词内字符 ⇒ agent-dispatch 整名不可分，
                      #       查询里写 dispatch 则**全部 365 条技能 name命中恒为空**。
                      #   旁证：codex/skill-creator 在真实结果里各出现两次 = PT-20261002-12
                      #     记的 realpath 去重缺陷，正在输出里显形。**生产重启未执行**（沿用禁重启边界）。
                      # 上一版 v0.13.66 技能中心统一列表（D1 补齐 20 路发现点 + 排除清单 + state 四态）：
                      #   病根：技能门面只扫 7 路 ⇒ 家长 10 家里 6 家约 300 条技能不在册，
                      #     「按任务自动发现技能」从机制上就漏（PT-20261002-11 的 R1 可见性缺口）。
                      #   D1  src/skill.py：_DEFAULT_DIRS 7→20 路；EXCLUDED_DIRS 15 条**带理由**
                      #     （marketplace 缓存/安装暂存/备份/快照/厂商同源副本），排除项进 /api/skill/status
                      #     不静默；_scan_one 加 reason 三态 + skill_state() 四态（ok/empty/missing/error）
                      #     ——原 available 布尔把「这家没装」与「我们配错」混成一句话；
                      #     expand_roots/route_roots 支持 glob（qoder-alpha 扩展目录名是内容哈希，
                      #     写死必然升级即 missing），_scan_many 多根部分失败不判整路失败。
                      #   取证修正三处曾记错的实况：find 不带 -L 不跟随软链 ⇒ opencode 实为 2 条非 0；
                      #     qoderwake 的 runtime-generations/{A,B} 与 resources/builtin-skills 是
                      #     realpath 不同的同源副本（各 11 条）⇒ 列入排除否则 11 报成 44；
                      #     hermes 120 与 hermes-agent 58 实测**零重叠**（去重机制另在 L0 造重叠验证）。
                      #   闸门 tests/test_skill_routes.py（L0 23 例 + L1 4 例）。**生产重启未执行**（沿用禁重启边界）。
                      # 上一版 v0.13.65 统一记忆注入通道（D1 接联邦 + D1.5 开默认快路口径）：
                      #   病根实测：/api/memory/context 收了 9 路联邦源 ID、过白名单校验不报 400，
                      #     却在函数体里被静默丢弃 ⇒ 回包 backends 只有 tdai_profile/local，
                      #     fed.sources=0、正文 1585 字符、联邦段完全缺席。HTTP 200 无异常无告警
                      #     （本仓反复警告的「全指标绿而功能层已死」同族）。
                      #   D1  src/memory.py：注入包接联邦 + 第 4 段（预算内轮转取，未返回/失败的
                      #     逐路点名写进段里，不静默丢弃）；src/memfed.py：search_fed 加**可选**
                      #     wall_s 墙钟（默认 None＝既有 27 例语义逐字不变），超预算记 skipped_budget
                      #     且不取消（to_thread 不可取消）——坐在会话起始链路上，宁可少一路也不能卡死开局。
                      #   D1.5 /api/memory/search 与前端兜底默认由 local,tdai 升到快路联邦集
                      #     （memory.FED_FAST_SOURCES 单一真源）；慢三路 pi/codex/archived 实测
                      #     rg 1.9~2.5s 且必然超时 ⇒ 不进默认，等 A4 索引投影。
                      #   src/hubmcp.py：hub_memory_context 开 sources 口子（空串取唯一真源），
                      #     工具层不抄第二份默认字符串。
                      #   闸门 tests/test_memfeed_inject.py（L0 16 例 G1~G8，含 AST 零写与
                      #     「预算掐掉的源必须写在段里」）。**生产重启未执行**（沿用禁重启边界）。
                      # 上一版 v0.13.64 后端：终端移动端三零件（借鉴 cloudcli 行为规格，不复制其 AGPL 代码）：
                      #   P1 触摸层 static/hub/13-term-touch.js：惯性滚 / 长按选区 / 双指缩放，
                      #     宽屏不绑定；P2/P3 src/term.py：auth_url 逐观看者旁路 + ANSI 去重，
                      #     API 只收 agent_id、命令取画像白名单（不退化为 bash -c）。
                      #   10-02 复核补记：惯性尾巴曾被 ttScrollByPx 每帧 Math.round 逐帧取整吞掉
                      #     （v 衰减到 <750px/s 后每帧不足半行，衰减段≈7 成路程整段消失：理论 ≈11 行、
                      #     实测 2 行）⇒ 改为跨帧余量 ttResidPx；L2 判据 T2b 同步收紧为「松手再滑 ≥3 行」。
                      #   证据：docs/TERM-MOBILE-IMPROVE-PLAN-20261001.md §9.1；台账 PT-20261002-10。
                      #   上一版（v0.13.63）根因档案保留在下方，它被 L0 闸门 test_term_scroll_sensitivity
                      #   钉住（改这里之前先读那段注释）：
                      # 终端滚轮「无法上翻 / 到不了页顶」：xterm 6.0 的 scrollSensitivity 仍取默认 1
                      #   6.0 的 consumeWheelEvent 里有 `if (|deltaY| < 50) r *= 0.3` 再
                      #   Math.floor 取整 ⇒ 标准一格滚轮（deltaY=120、行高 24px）只走
                      #   120/24*0.3=1.5 → 1~2 行。CDP 真派发实测：sens=1 → 2.1 行/格、
                      #   3 → 6.2、5 → 10.5、10 → 20.8（严格线性，与 deltaY 不成比例）。
                      #   2000 行 scrollback 从底部滚到顶要 ~940 格 ⇒ 体感就是「滚不动」。
                      #   A/B 实测 5.5.0 与 6.0.0 行为一致 ⇒ 非升级引入；5.5 按 deltaY/行高
                      #   走（自然 5 行/格），6.0 的 0.3 折把体验砍到 1/5。
                      #   修法：Terminal 构造显式 scrollSensitivity: 5（对齐自然值），
                      #   端到端实测滚到顶 1979 行只需 190 格（-80%），Alt/Ctrl/Shift 仍走
                      #   fastScrollSensitivity(=5) 不丢快速滚动。
                      #   注：滚动条「看不见」是 6.0 Auto 档设计（hover 才显、离开 500ms 淡出），
                      #   真渲染量到 opacity=1 / pointer-events=auto，非缺陷，故不动样式。
                      #   ↑ v0.13.62：历史「点得进去」——列表与续聊校验共用同一套 codex source 判据
                      #   ↑ v0.13.62：v0.13.61 的半边修复收尾 ——
                      #     列表侧放了 vscode 会话，校验侧仍写死 source='cli' ⇒ 点「续聊」
                      #     必 404「session_id 不在实盘清单内」（列表能看见却点不动）。
                      #     抽 _codex_real_user_sql() 作唯一判据真源，两侧共用一份。
                      #   ↑ v0.13.61：侧栏 agent 名下「最新会话」停在 09-28 的根因 ——
                      #     sessions_store._t_codex 写死 where source='cli'，而 09-29 起
                      #     用户在 IDE 扩展里开的会话 source 记为 'vscode' ⇒ 最新会话被整体
                      #     过滤。改成「排除噪音」（exec 探针 + subagent 子线程）而非白名单，
                      #     免得 codex 下次新增入口再次静默漏（详见 CHANGELOG v0.13.61）。
                      #   ↑ v0.13.60：xterm 5.5.0 → 6.0.0 整组升级（core + 6 addon），
                      #     canvas addon 随 6.0 移除（peerDeps 仍锁 ^5.0.0，取证见
                      #     static/vendor/README.md），回落链收敛为 webgl → dom；
                      #     另补 WebGL 纹理图集定时清理（clearTextureAtlas，显存不再单调涨）。
                      #   ↑ v0.13.59：P2-B 跨 Agent 活动指示 + P2-D 文档/密钥纵深批。
                      #   ↑ v0.13.58：终端状态跨客户端连续（TTL 判据=无生命迹象）+ P0 止血批
                      #   ↑ v0.13.56：尺寸所有权 claim/update + resize 100ms 去抖 —— 后台那一端
                      #     偷不走 PTY 尺寸（桌面开着 vim、手机端在后台唤醒的典型坑）。
                      #   ↑ v0.13.55：输出合并 coalescer（5ms 前后沿），WS 帧数 2602→7。
                      #   ↑ v0.13.54：xterm addon 补齐（webgl/canvas 渲染器、Unicode11、
                      #     终端内查找、bracketed paste 安全包装）。
                      #   ↑ v0.13.51：① drift 体检从「只报」升级为「按持久化值写回」（落笔前
                      #     时间戳备份，HUB_MODEL_DRIFT_REPAIR=0 可退回只报）；因 CCR 与 hub
                      #     开机同秒启动，启动那一次会被 CCR 盖掉 ⇒ 再加 300s 定期巡检。
                      #     ② 续聊会话（`claude --resume <id>`）也按白名单追加 --model：不带
                      #     flag 的启动读的是会被 CCR 改写的配置文件。
                      #   ↑ v0.13.50：对话/协同子任务/定时任务以前从不读 hub 侧持久化模型
                      #     （adapter.default_model 是启动时常量 ⇒ 实测仍发 qwen3.8-flash），
                      #     现统一走 modelcfg.chat_model()；并加只读 drift 体检（CCR 重启会
                      #     把 ~/.claude/settings.json 的 env 三兄弟改回旧值）。
                      #   ↑ v0.13.49：侧栏历史会话：条数 8 / 去标题行 / 时间只留日期 / 左边距对齐状态图标
                      #   三个子项与系统页同口径（data-sys ⇒ 委托 ⇒ go(page) ⇒ 正文出页），
                      #   设置抽屉整体拆除 ⇒ 09-23「手机上被浮层糊住」的形态不再存在。
                      #   ↑ v0.13.42：设置→GitHub 子菜单（远程地址/key/归属/克隆落点不再硬编码）
                      #   ↑ v0.13.41：设置→模型子菜单（选 agent → 选 CCR 模型 → 预览 → 口令落笔；
                      #     Hub 侧 per-agent 模型用于拉起终端注入 --model + 写该 agent 自己的
                      #     配置文件；CCR Router 五场景只读；写前预览+备份+口令三件套）
                      #   ↑ v0.13.40：系统子菜单页：删页顶标题/分割线（renderPageCrumb 只清空）
                      #     + 十页统一骨架重排（标题+分割线来自 #opBar：renderPageCrumb 是全站
                      #     唯一写 #crumb 的地方，它不写字 ⇒ syncOpBar 判 void ⇒ 整条 opBar 收起；
                      #     chat 早退交给 renderModeBar。排版统一到 .sp/.sp-card 骨架）
                      #   ↑ v0.13.39：修「选 pi 起会话 ⇒ 终端一屏 JS 堆栈」：终端子进程 PATH 前置 nvm node bin
                      #   （pi 的 shebang 是 #!/usr/bin/env node，服务 PATH 无 nvm ⇒ 内核把系统
                      #     node v20.20.2 交给它，而 pi v0.85.1 的 bundle 用 node:fs 的 globSync
                      #    （Node 22+）⇒ SyntaxError 启动即崩。which() 的 nvm 兜底只管 hub 找
                      #     得到 pi，管不到 pi 自己再找解释器 —— 两层都得补）
                      #   ↑ v0.13.38：本机项目/GitHub 项目页 agent 候选框补 pi 与 codebuddy：两张 Web 型卡补终端入口
                      #   （候选框口径=entries 含 term；pi 有 CLI v0.85.1、codebuddy CLI 用 WorkBuddy
                      #     包内绝对路径——裸名 which 落空，必须带目录分隔符；qwenpaw 无 CLI 故不在列）
                      #   ↑ v0.13.37：Agents 菜单补 CodeBuddy Code 卡：`codebuddy --serve` 的原生遥控界面 :35431
                      #   （画像唯一改动：src/profiles.py；无 cli/terminal ⇒ vitals 按 web-service
                      #     形态以自有端口应答为存在证据。卡片入口=嵌入会话+新窗口+详情）
                      #   ↑ v0.13.36：两项目页收藏/隐藏落服务端：app_prefs KV 表 + GET/PUT /api/prefs/{key}
                      #   （键白名单 projects.lp/gh，写走 write_gate+显式 decide 双保险）；
                      #   前端载入拉后端偏好为准、切换回写，localStorage 降级为离线兜底。
                      #   + GitHub 项目页：GET /api/github/repos（远端清单+strict remote 本地匹配）+ POST /api/github/clone
                      #   （白名单 slug→服务端重构 URL→浅克隆到 GITHUB_CLONE_BASE，审计 action=create）
                      #   + POST /start 铸 JWT 转调创建会话 → 详情抽屉项目列表 + iframe 直达 /session/{id}；
                      #   MCP +hub_cloudcli_projects
                      #   （--embed-line / --embed-bg / --font-display / --on-accent），fr 轨道一律
                      #   minmax(0,…) 防内容顶破容器，数字列 tabular-nums 兜字体回退；DESIGN.md 新增
                      #   「视觉系统」语义索引章（权威源仍是 templates/index.html 的 :root，不复制取值）。
                      #   取值与原字面量逐字相同 ⇒ 渲染零变化；取证见 agent-knowledge/57（真渲染四档 + gate 50）。
                      #   上一版 v0.13.25 后端：终端进程退出时把「为什么没了」说清楚。waitpid 的退出状态原先被
                      #   `_st` 直接丢弃（src/term.py 的 _cleanup / _force_kill）⇒ 崩溃原因永远上不了屏，
                      #   用户只看到一句「[会话结束]」。新增 describe_exit() 把信号/退出码解成人话：
                      #   SIGILL/SIGSEGV/SIGBUS/SIGABRT/SIGKILL 点名「疑似内存不足」；并用 hub_killed
                      #   区分「hub 自己发的 SIGTERM/SIGKILL」（点 × / 空闲 TTL / 服务退出）与内核
                      #   OOM-killer ⇒ 绝不把用户主动关会话报成内存不足。API 侧 to_dict() 透出 exit_reason。
                      #   起因：2026-09-25 排查「菜单点 OpenCode 秒退」，只能靠 dmesg(trap invalid opcode)
                      #   + objdump(ud2) + ulimit -v 三步反推出 bun/JSC 的 MemoryExhaustion 主动 abort。
                      #   真因是整机 swap 耗尽（/vol1/.swap/swap2 那 4G 因开机顺序 + nofail 静默失效），
                      #   hub 代码本身无 bug —— 本次只补「可观测性」。详见 CHANGELOG。
                      #   上一版 v0.13.24 后端+前端：asset_audit 资产变更审计（append-only 表 + log_asset_event 写口径 + /api/audit/list）；
                      #   本地记忆便签 staleness 观测（memstats，**只报告不清理**）挂 /api/kb/status.local_memory；
                      #   chat 会话工具条加导出按钮（blob 下载、token 只走头、四态文案互斥）。
                      #   上一版 v0.13.23 后端：/health 补上游网关(CCR)连通性与模型注册清单 + 画像最近检测时间；
                      #   会话批量导出端点（JSON/CSV，默认脱敏，按写端点同等鉴权）；
                      #   MANAGER_LLM_BASE_URL 默认值由已退役的 FCC :8082 改回 CCR :3456。
                      #   版本号让位：本批原自命名 0.13.22，但 master 上 ff53581（终端页空格接力，纯前端）已占用该标签
                      #   ⇒ 本批改 0.13.23，避免两批共用一个版本号（详见 CHANGELOG）。
                      #   v0.13.21/22 均为纯前端批次，按项目口径
                      #   「VERSION 与清 code_stale 随下次后端改动同批」⇒ 本次一并 bump。
                      #   上一版（v0.13.20 FCC 退役收尾 / v0.13.19 P3 工具注册表 + P4 资产面板）明细见 CHANGELOG.md。

#: 常驻后台任务统一登记处。裸 create_task 不持引用 ⇒ 事件循环只持弱引用，
#: GC 可能在任意时刻回收掉这些循环任务（表现为「跑着跑着某功能静默停摆」），
#: 且 shutdown 时无法 cancel，进程退出要等事件循环超时。
_bg_tasks: set = set()


def _spawn(coro) -> asyncio.Task:
    t = asyncio.create_task(coro)
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)
    return t


app = FastAPI(title="Agent Hub", version=VERSION)


def _parse_cors_origins() -> list:
    """CORS_ORIGINS 逗号分隔解析；异常/为空时退化为仅回环来源（不放松默认安全）"""
    # 原默认值写死了三个内网 IP。换个网段（Tailscale 重新分配、或搬到别的 LAN）就静默失效：
    # 页面能开但所有写端点 400 CORS，用户只会看到「按钮点了没反应」。
    # 改为 env 驱动、**默认只给回环**（更严，且本机自用不受影响），跨网访问显式配 CORS_ORIGINS。
    # 绝不放宽到 "*"：allow_credentials=True 时浏览器会直接拒绝通配来源。
    raw = os.getenv("CORS_ORIGINS", "http://127.0.0.1:3102,http://localhost:3102,http://[::1]:3102")
    try:
        origins = [o.strip() for o in raw.split(",") if o.strip()]
        for o in origins:
            if not (o.startswith("http://") or o.startswith("https://")):
                raise ValueError(f"invalid origin scheme: {o}")
        if not origins:
            raise ValueError("empty CORS_ORIGINS")
        return origins
    except Exception as e:  # noqa: BLE001
        print(f"[Agent Hub] CORS_ORIGINS 解析失败（{e}），退化为仅回环来源")
        return ["http://127.0.0.1:3102", "http://localhost:3102", "http://[::1]:3102"]


app.add_middleware(CORSMiddleware, allow_origins=_parse_cors_origins(),
                   allow_methods=["*"], allow_headers=["*"], allow_credentials=False)

# ── 非 MCP 写路径按 IP 滑动窗口限流（风格对齐 mcpgw._check_rate）──────
API_RATE_PER_MIN = int(os.getenv("API_RATE_PER_MIN", "60"))
# ⚙设置口令：查看 TERM_TOKEN 等敏感配置时要求提供（为空则设置页不可用）
HUB_PASSCODE = os.getenv("HUB_PASSCODE", "")
_api_rate: Dict[str, deque] = defaultdict(deque)
_RATE_EXCLUDED_PREFIXES = ("/telemetry/events/", "/health", "/mcp")
_RATE_WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")
#: P0-6：_api_rate 是进程级字典，每个见过的 IP 留一个 deque **永不删除**。
#: 唯一来源是内网/反代，但 IP 会随设备休眠、DHCP 续租、容器重建不断变化 ⇒ 长时间运行
#: 内存单调增长（mcpgw 同型缺陷）。上限按「记住多少个 IP 还在限流窗口内」取，
#: 超出就整条丢弃（该 IP 下次请求会以 0 计数重新开始，等价于放过一次，超了仍会 429）。
_RATE_MAX_IPS = int(os.getenv("API_RATE_MAX_IPS", "4096"))


def _rate_prune(now: float) -> None:
    """回收 _api_rate 里已经过窗口的 IP 条目（每个 IP 至少来过一次才会被记）。"""
    if len(_api_rate) <= _RATE_MAX_IPS:
        return
    cut = now - 60
    stale = [ip for ip, w in _api_rate.items() if not w or w[-1] < cut]
    for ip in stale:
        _api_rate.pop(ip, None)
    # 极端情况：所有 IP 都在窗口内（真实 DDoS 或超大 NAT）⇒ 超额部分按最旧的整条丢，
    # 保证表本身有硬上界，不因来客太多而无限长。
    while len(_api_rate) > _RATE_MAX_IPS:
        oldest = min(_api_rate, key=lambda ip: _api_rate[ip][-1])
        _api_rate.pop(oldest, None)


@app.middleware("http")
async def api_rate_limit(request: Request, call_next):
    # 只拦写方法；GET（前端 30s 轮询）与外部推送/健康检查/MCP（自带限流）不拦
    if request.method in _RATE_WRITE_METHODS:
        path = request.url.path
        if not any(path.startswith(p) for p in _RATE_EXCLUDED_PREFIXES):
            ip = request.client.host if request.client else "unknown"
            _rate_prune(time.monotonic())
            window = _api_rate[ip]
            cut = time.monotonic() - 60
            while window and window[0] < cut:
                window.popleft()
            if len(window) >= API_RATE_PER_MIN:
                print(f"[rate] 429：{ip} 超过 {API_RATE_PER_MIN}/min（{request.method} {path}）")
                return JSONResponse(
                    {"detail": f"API 限流：超过 {API_RATE_PER_MIN}/min"}, status_code=429)
            window.append(time.monotonic())
    return await call_next(request)


# ── P1-7 扩展：写端点鉴权闸门（实测 34 条写路由里 32 条此前不设防，6 条对匿名写回 200）。
#    Starlette 里后注册的中间件在最外层 ⇒ 本闸门先于 api_rate_limit：被拒的请求既不该占限流预算，
#    更不该走到 handler 里产生副作用（rebuild 重写记忆就是这类副作用）。 ──
app.middleware("http")(write_gate)

templates_dir = Path(__file__).parent.parent / "templates"
static_path = Path(__file__).parent.parent / "static"
templates = Jinja2Templates(directory=str(templates_dir))

# v0.5.2.7 自定义静态资源路由（替代原 StaticFiles mount）；v0.12.4 改口径：
# 原来是 no-store ⇒ 浏览器每进一次终端页都要重下 290KB 的 vendor/xterm.js，而且从不压缩。
# 现在 no-cache（每次仍回源校验）+ 自己处理 If-None-Match ⇒ 文件没变只回 304 空响应，
# 文件一改 ETag 就变 ⇒ 拿不到旧 JS（当初写 no-store 就是怕这个，304 同样防得住）。
if static_path.exists():
    import gzip
    import hashlib
    import mimetypes
    from fastapi import Request
    from fastapi.responses import FileResponse, Response

    _GZ_SUFFIX = {".js", ".css", ".svg", ".json", ".map"}
    _GZ_MIN = 1024
    _gz_cache: dict = {}   # "路径|mtime_ns|size" -> gzip 字节；键随文件变，天然失效

    @app.get("/static/{file_path:path}")
    async def _static_no_cache(file_path: str, request: Request):
        # 判据全部下沉到 src/staticguard.py（纯函数）：内联在路由里时，单测无法覆盖
        # —— import src.main 会触发 lifespan（开真库、起后台任务）。历史三条判据与
        # 实测红-绿见该模块 docstring。
        f = staticguard.resolve_serveable(static_path, file_path)
        if f is None:
            raise HTTPException(404)
        st = f.stat()
        # 与 FileResponse 同一套算法（md5("mtime-size")）仅用于 revalidate 分支。
        # 判据在 staticguard.cache_policy（纯函数、可单测）：URL 的 ?v= 等于文件内容哈希
        # 才许 immutable。09-23 自研 APP 事故的根治——旧口径下 ETag 由 mtime+size 算出，
        # 与 query、与 content-encoding 都无关 ⇒ 不同 ?v= 与 gzip/identity 共用同一个 ETag，
        # 条件请求可让端侧继续执行旧体（实测手机对 hub.js 先 200 后 304，且 ?v=22c 与 ?v=23a
        # 同 ETag）。immutable 分支不发校验器、永不回 304：换新体的唯一途径是 URL 变化本身。
        policy, want_tok = staticguard.cache_policy(
            (request.query_params.get("v") or "").strip(), f)
        headers = {"Vary": "Accept-Encoding", "X-Asset-Token": want_tok}
        if policy == "immutable":
            headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            etag = '"%s"' % hashlib.md5(
                f"{st.st_mtime}-{st.st_size}".encode(),
                usedforsecurity=False).hexdigest()
            headers.update({"Cache-Control": "no-cache", "Pragma": "no-cache", "ETag": etag})
            # 304 分支此前漏了 Vary：共享缓存可能把 gzip 版回给不接受 gzip 的客户端
            if request.headers.get("if-none-match") == etag:
                return Response(status_code=304, headers=headers)
        media = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        if (f.suffix.lower() in _GZ_SUFFIX and st.st_size >= _GZ_MIN
                and "gzip" in (request.headers.get("accept-encoding") or "")):
            key = f"{f}|{st.st_mtime_ns}|{st.st_size}"
            body = _gz_cache.get(key)
            if body is None:
                body = gzip.compress(f.read_bytes(), 6)
                if len(_gz_cache) > 32:
                    _gz_cache.clear()
                _gz_cache[key] = body
            return Response(content=body, media_type=media,
                            headers={**headers, "Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
        # 09-24：immutable 分支**不走 FileResponse**。它会在 __call__ 里 stat 并调
        # `set_stat_headers()`，自动补 `etag`/`last-modified`——两者都是 mtime+size 的函数，
        # 与 URL 上的内容哈希提手无关 ⇒ 客户端可能拿 304 复用"提手对不上"的旧副本
        # （09-23 APP 事故实测：?v=…22c 与 ?v=…23a 共用同一 ETag，先 200 后 304）。
        # 曾试图覆写 FileResponse.set_headers —— 那个钩子在本 Starlette 版本里不存在，
        # 方法永不执行（假动作，影子实测当场抓出）。这里直接给全量字节，与 gzip 分支同一做法，
        # 不依赖任何私有方法名。代价：不支持 Range；静态资源最大约 300KB，可接受。
        if policy == "immutable":
            return Response(content=f.read_bytes(), headers=headers, media_type=media)
        # revalidate 分支照旧：发 ETag、可回 304（提手不对/没提手时的安全阀）。
        return FileResponse(str(f), headers=headers, media_type=media)
    # 不再 mount StaticFiles；自定义路由接管 /static/

# 子路由（Hook / 记忆 / 指挥官）
app.include_router(hook_mod.router)
app.include_router(memory_mod.router)
app.include_router(memfed_mod.router)
app.include_router(kb_mod.router)
app.include_router(skill_mod.router)
app.include_router(tasks_mod.router)
app.include_router(mcpgw_mod.router)
app.include_router(cronjobs_mod.router)
app.include_router(term_mod.router)
app.include_router(audit_mod.router)
app.include_router(runlog_mod.router)
app.include_router(cloudcli_mod.router)
app.include_router(localprojects_mod.router)
app.include_router(resources_mod.router)
app.include_router(github_mod.router)
app.include_router(prefs_mod.router)
app.include_router(modelcfg_mod.router)
app.include_router(ghsettings_mod.router)
app.include_router(hublog_mod.router)

# D2：Hub MCP Server —— 把本机事实源以 MCP 暴露给 Hermes 等外部 Agent。
# 端点为 /hub-mcp/mcp（streamable_http_app 自带 /mcp 子路由，故挂在 /hub-mcp 下，避免与 mcpgw 的 /mcp/* REST 冲突）。
# 用 try 包裹：挂载失败不得影响主服务启动。
try:
    import contextlib

    import hubmcp

    # session_manager 是懒创建的：必须先 build_app() 再取。
    _mcp_app = hubmcp.build_app()
    _mcp_sm = hubmcp.server.session_manager

    # Starlette 不会自动运行 mount 子应用的 lifespan，需手动并入主应用 lifespan，
    # 否则报 "Task group is not initialized. Make sure to use run()."
    _orig_lifespan = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def _hub_lifespan(app_):
        async with _mcp_sm.run():
            async with _orig_lifespan(app_):
                yield

    app.router.lifespan_context = _hub_lifespan
    app.mount("/hub-mcp", _mcp_app)
    print("[Agent Hub] Hub MCP server 已挂载：/hub-mcp/mcp（lifespan 已并入）")
except Exception as e:  # noqa: BLE001
    print(f"[Agent Hub] Hub MCP server 挂载失败（主服务不受影响）：{type(e).__name__}: {e}")

discovery: Optional[AgentDiscovery] = None


class ChatRequest(BaseModel):
    agent_id: Optional[str] = None  # 以路径参数为准（v0.1 遗留必填校验是 bug）
    message: str
    session_id: Optional[str] = None
    model: Optional[str] = None
    cwd: Optional[str] = None


class AgentRegisterRequest(BaseModel):
    dir: str
    name: Optional[str] = None
    port: Optional[int] = None
    command: Optional[str] = None
    args: Optional[list] = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@app.get("/health")
async def health():
    """自证端点（P0-2）：不只看活没活，还要能看出「跑的是哪份代码」。

    旧口径只回 {status,service,version,port} ⇒ 版本漂移（进程报 0.13.2 / HEAD 已 0.13.3）
    与「对话端点必 500」这类**静默不可用**全都看不见。
    新增字段全 additive；前端 pollHealth 只读 status，不会被改坏。
    code_stale 只兑情报、不改 status：代码改了没重启不等于服务坏了。
    """
    try:
        db.query("SELECT 1 FROM sqlite_master LIMIT 1")
        db_ok = True
    except Exception as e:  # noqa: BLE001
        db_ok = False
        print(f"[health] db 自检失败：{type(e).__name__}: {str(e)[:120]}", flush=True)
    out = {"status": "ok", "service": "agent-hub", "version": VERSION, "port": config.port,
           "db_ok": db_ok,
           "term_sessions": term_mod.alive_count(),
           "term_idle_max_s": term_mod.idle_max_s()}
    out.update(selfattest.snapshot())
    # 记忆后端体检（P0-6）：权威库在 TDAI，它挂不挂必须从 /health 能看出来。
    # 旧态是「/api/memory/search 永远回 count:0 且无任何错误字段」——全绿而功能层已死。
    # 纯读缓存不起网络（见 tdai_client.backend_status 注释），且**不改 status**：
    # 记忆后端不可用不等于 hub 坏了，同 code_stale 只兑情报的设计意图。
    out["memory_backend"] = tdai_client.backend_status()
    # 上游网关（CCR）连通性 + 模型注册清单 —— 0924 方案档 §三「health 增强（运维 P3→P2）」收口。
    # 为什么必须有：本机三次同源事故都是**模型 ID 失效而 /health 全绿**（09-06 `minimax-m3:free`
    # HTTP 400、09-19 `'ultra'` 无效、09-23 `qwen3.8-flash` 缺 provider 前缀）——即 09-22 定名的
    # 「静默不可用」家族。现在 watch 里的每个写死 ID 是否仍在册，直接是 /health 的可断言字段。
    # 纯读缓存（gwprobe 自己 stale-while-revalidate，TTL 300s），**不改 status**：
    # 上游网关不可达不等于 hub 坏了，与 code_stale / memory_backend 同一设计意图。
    out["ccr_gateway"] = gwprobe.status()
    # 画像最近检测时间（同属「health 增强」的另一半：CCR 连通性 + 画像检测时间 + DB 状态）。
    # 只给 last_sweep 会被「新一轮扫了 6 家、漏了第 7 家」骗过 ⇒ 必须给最坏值 oldest_check_age_s
    # 与 unchecked（在册却从没被扫到的家数，正是 09-22「在册却静默不可用 21 天」的形态）。
    try:
        out["profiles_last_check"] = healthx.profiles_last_check(
            vitals_mod.vitals.snapshot(), vitals_mod.vitals.last_sweep, vitals_mod.SWEEP_EVERY)
    except Exception as e:  # noqa: BLE001  # 情报字段不得把 /health 打挂
        out["profiles_last_check"] = {"state": "error",
                                     "error": f"{type(e).__name__}: {str(e)[:120]}"}
    if not db_ok:
        out["status"] = "degraded"
    return out


# ── Agents ────────────────────────────────────────────────────────────

@app.get("/api/agents")
async def list_agents():
    if discovery is None:
        raise HTTPException(status_code=503, detail="Discovery not initialized")
    agents = await discovery.discover_all()
    return {"agents": [a.to_dict() for a in agents], "count": len(agents)}


@app.get("/api/agents/{agent_id}")
async def get_agent(agent_id: str):
    if discovery is None:
        raise HTTPException(503, "Discovery not initialized")
    agent = discovery.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, f"Agent {agent_id} not found")
    return agent.to_dict()


def _agent_profs_for_vitals():
    """只体检 kind=agent 的画像（gateway/service/tool 不进菜单闸门），
    且**含已被摘掉的候补** —— 不重新探测就会被永久固化，用户装好了也回不来。"""
    return [p for p in profiles_mod.all_profiles(include_blocked=True)
            if p.get("kind") == "agent"]


@app.get("/api/vitals")
async def get_vitals():
    """判定台账（可审计）：每个 agent 的证据 + 裁决 + 来源 + 概率"""
    snap = vitals_mod.vitals.snapshot()
    return {"count": len(snap), "last_sweep": vitals_mod.vitals.last_sweep,
            "jev_key_present": bool(vitals_mod.profiles_jev_key()),
            "menu_min": vitals_mod.MENU_MIN, "sweep_every_sec": vitals_mod.SWEEP_EVERY,
            "detail": snap}


@app.post("/api/vitals/sweep")
async def post_sweep():
    """跑一轮完整体检：先全量 L1/L2（不碰模型），再对「上次真请求实测已过期」的
    候补补 L4（每人每 24h 最多一次）。VITALS_RT_SWEEP=0 可退成纯廉价轮。"""
    return await asyncio.to_thread(vitals_mod.vitals.sweep, _agent_profs_for_vitals())


@app.post("/api/agents/{agent_id}/verify")
async def verify_agent(agent_id: str):
    """L4 体检：跑一次真实一次性请求。要耗 token 且慢（冷启动可达 60s），
    所以只给显式动作触发，不进自动周期。"""
    p = profiles_mod.get_profile(agent_id)
    if not p:
        raise HTTPException(404, f"未知 agent: {agent_id}")
    if not p.get("verify_argv"):
        raise HTTPException(400, {"error": "该 Agent 未声明 verify_argv（无法做真实应答实测）",
                                 "shape": (vitals_mod.collect(p)).get("agent_shape")})
    rec = await asyncio.to_thread(vitals_mod.vitals.verify, p)
    ev = rec.get("evidence", {})
    out = {k: rec.get(k) for k in ("verdict", "source", "rule_verdict", "confidence",
                                   "menu_noul", "present_noul", "roundtrip_noul",
                                   "block_noul", "jev_error", "rt_state", "rt_flaky")}
    # 应答态与生死判定分两栏回：前端拿 rt_state 说明模型层，拿 verdict 决定颜色
    out["evidence"] = {kk: ev.get(kk) for kk in
                       ("agent_shape", "resolved_path", "version_rc", "run_rc", "run_ok",
                        "run_output", "run_note", "run_model", "run_evidence",
                        "evidence_sha", "endpoint_serving")}
    return out


@app.post("/api/agents/detect")
async def detect(body: AgentRegisterRequest):
    """目录 → 自动识别类型/启动命令/端口（Agent_Manager 添加 Agent 的第一步）"""
    return detect_project(body.dir)


@app.post("/api/agents")
async def register_agent(body: AgentRegisterRequest):
    """注册自定义 Agent（识别 + 入库；进程管理交由原守护，hub 只做视图与对话）"""
    info = detect_project(body.dir)
    if info["type"] == "unknown" and not body.command:
        raise HTTPException(400, {"error": "无法识别项目类型且未提供 command", "detected": info})
    now = _now()
    agent_id = re.sub(r"[^a-z0-9_-]", "-", (body.name or Path(body.dir).name).lower())
    port = body.port or info.get("port")
    endpoint = f"http://127.0.0.1:{port}" if port else None
    db.execute(
        """INSERT INTO custom_agents(id,name,type,command,args,working_dir,env,port,endpoint,description,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET name=excluded.name,type=excluded.type,
             command=excluded.command,args=excluded.args,working_dir=excluded.working_dir,
             port=excluded.port,endpoint=excluded.endpoint,description=excluded.description,
             updated_at=excluded.updated_at""",
        (agent_id, body.name or Path(body.dir).name, info["type"],
         body.command or (info.get("command") and " ".join([info["command"]] + info["args"])),
         json.dumps(info.get("args") or []), str(Path(body.dir).expanduser()),
         "{}", port, endpoint, info.get("entry") or "", now, now))
    if discovery:
        discovery.reload()
    return {"status": "registered", "id": agent_id, "detected": info}


@app.delete("/api/agents/{agent_id}")
async def unregister_agent(agent_id: str):
    n = db.execute("DELETE FROM custom_agents WHERE id=?", (agent_id,))
    if not n:
        raise HTTPException(404, "custom agent not found (内置 Agent 不可删)")
    if discovery:
        discovery.reload()
    return {"status": "deleted", "id": agent_id}


# ── 统一对话（Phase 2：真实适配器直连）──────────────────────────────

async def _chat_dispatch(agent_id: str, message: str,
                         session_id: Optional[str] = None,
                         model: Optional[str] = None,
                         cwd: Optional[str] = None,
                         trace_id: Optional[str] = None) -> Dict:
    import time as _time
    t0 = _time.monotonic()
    # v0.13.50：模型取值的唯一入口 —— 请求显式带了就用请求的，没带就回落「设置 → 模型」
    # 落库的那个持久化默认值。以前这条通道完全不知道它的存在，adapter 的
    # default_model 是启动时算出的常量（实测发出去的是 qwen3.8-flash）⇒ 设置页改了
    # 模型，对话页 / 协同子任务 / 定时任务照旧用旧模型。
    model = modelcfg.chat_model(agent_id, model)
    adapter = get_adapter(agent_id)
    if adapter is None:
        rows = db.query("SELECT * FROM custom_agents WHERE id=?", (agent_id,))
        if rows and rows[0]["port"]:
            from adapters.openai_compat import OpenAICompatAdapter
            adapter = OpenAICompatAdapter(config, agent_id,
                                          base_url=f"http://127.0.0.1:{rows[0]['port']}",
                                          default_model=model or "")
        if adapter is None:
            return {"success": False, "error": f"agent {agent_id} 无可用适配器",
                    "hint": "CLI/TUI Agent 请通过原生界面访问"}
    # 取历史
    history = []
    if session_id:
        hist = db.query("SELECT role,content FROM chat_messages WHERE session_id=? "
                        "AND role IN ('user','assistant') ORDER BY id DESC LIMIT 20",
                        (session_id,))
        history = [{"role": r["role"], "content": r["content"]} for r in reversed(hist)]
    result = await adapter.chat(message, session_id=session_id, model=model,
                                history=history, cwd=cwd)
    # S2 画像埋点：append-only，成功率/耗时统计源
    dur = int((_time.monotonic() - t0) * 1000)
    db.log_profile_event("hub_chat", agent_id,
                         "success" if result.get("success") else "fail", dur,
                         trace_id=trace_id,
                         detail={"session_id": session_id,
                                 "usage": result.get("usage"),
                                 "error": (result.get("error") or "")[:200] or None})
    # 会话落盘
    if session_id:
        now = _now()
        db.execute("INSERT OR IGNORE INTO chat_sessions(id,agent_id,title,created_at,updated_at) "
                   "VALUES(?,?,?,?,?)",
                   (session_id, agent_id, message[:60], now, now))
        db.execute("INSERT INTO chat_messages(session_id,role,content,created_at) VALUES(?,?,?,?)",
                   (session_id, "user", message, now))
        reply = result.get("response") or result.get("error") or ""
        db.execute("INSERT INTO chat_messages(session_id,role,content,meta,created_at) VALUES(?,?,?,?,?)",
                   (session_id, "assistant" if result.get("success") else "error", reply,
                    json.dumps(result.get("usage"), ensure_ascii=False) if result.get("usage") else None, now))
        db.execute("UPDATE chat_sessions SET updated_at=? WHERE id=?", (now, session_id))
    return result


@app.post("/api/agents/{agent_id}/chat")
async def chat(agent_id: str, request: Request, req: ChatRequest):
    session_id = req.session_id or uuid.uuid4().hex[:12]
    trace_id = request.headers.get("x-trace-id")
    # P0-1 回归（实测取证）：v0.10.0 commit 2cf96fa 把 tools/repair_mode 从 ChatRequest
    # 字段表里删了，但调用点仍写 req.tools ⇒ 本端点从 09-20 起每请求必 500
    # （AttributeError），而 /health 全程 200、vitals 全绿 —— 直连对话框静默不可用三天。
    # 这两个参数在 v0.10.0 移除 hub-self 工具环后已无实体，**别再往回加**。
    # 钉死它的测：tests/test_pydantic_attr_drift.py（AST 静态取证，不导 main）
    result = await _chat_dispatch(agent_id, req.message, session_id, req.model,
                                  cwd=req.cwd, trace_id=trace_id)
    return {"agent_id": agent_id, "session_id": session_id,
            "message": req.message, "timestamp": _now(), **result}


@app.post("/api/agents/{agent_id}/chat/stream")
async def chat_stream(agent_id: str, request: ChatRequest):
    adapter = get_adapter(agent_id)
    if adapter is None:
        raise HTTPException(404, f"agent {agent_id} 不支持流式对话")
    session_id = request.session_id or uuid.uuid4().hex[:12]

    async def gen():
        yield f"data: {json.dumps({'session_id': session_id})}\n\n"
        async for chunk in adapter.chat_stream(request.message, session_id=session_id,
                                               model=modelcfg.chat_model(agent_id, request.model),
                                               cwd=request.cwd):
            yield chunk
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/sessions")
async def list_sessions(agent_id: Optional[str] = None, limit: int = 20):
    sessions = []
    sql = ("SELECT s.*, (SELECT COUNT(*) FROM chat_messages m WHERE m.session_id=s.id) "
           "AS messages FROM chat_sessions s")
    params: list = []
    if agent_id:
        sql += " WHERE s.agent_id=?"
        params.append(agent_id)
    sql += " ORDER BY s.updated_at DESC LIMIT ?"
    params.append(limit)
    sessions = db.query(sql, tuple(params))
    # 合并 hub 外部会话（pi）
    if not agent_id or agent_id == "pi":
        pi_sessions = await _get_pi_sessions(max(0, limit - len(sessions)))
        sessions.extend([{"agent": "pi", "id": s.get("id"), **s} for s in pi_sessions])
    return {"sessions": sessions[:limit], "count": len(sessions)}


@app.get("/api/sessions/{session_id}/messages")
async def session_messages(session_id: str, limit: int = 100):
    return {"messages": db.query(
        "SELECT role,content,meta,created_at FROM chat_messages "
        "WHERE session_id=? ORDER BY id ASC LIMIT ?", (session_id, limit))}


@app.get("/api/sessions/export")
async def export_sessions(request: Request, format: str = "json", agent_id: Optional[str] = None,
                          limit: int = 1000, with_messages: int = 1, redact: int = 1):
    """批量导出 hub 自己的会话（0924 方案档 §三「会话导出 P2.5」）。

    三个刻意的设计决定：
      1) **按写端点同等鉴权**：服务绑 0.0.0.0:3102，批量导出正文是数据外流动作，影响面比
         单条 `/messages` 大一个量级。复用 `writeauth.decide`（fail-closed：服务端没配口令
         ⇒ 503 而不是放行），与 09-23「31 个写端点不设防」的收口同一口径。
      2) **默认脱敏**（`redact=1`）：本工作区三次被凭据外流打过（备份镜像 82 个活凭据文件、
         `wiki/log.md` 历史含 CCR web token、外发净仓被闸门拦下 3 个抄了真 token 的文档）。
         导出件正是最容易被顺手 commit/转发的形态；命中数在 meta 里如实回报，**不静默改数据**，
         要原始字节须显式 `redact=0`。
      3) **不导出外部 CLI 的历史会话**（claude/jcode/codex/opencode/grok/hermes 的 session store）：
         那是别的工具链的私有存档，批量外流属另一层隐私裁定，须用户点名；本端点只覆盖
         hub 自己库里的 `chat_sessions` / `chat_messages`。
    """
    verdict, reason = writeauth.decide(
        "POST", request.url.path,                       # 强制按写方法判：导出=数据外流
        writeauth.provided_token(request.headers.raw, request.url.query),
        writeauth.secrets_from_env())
    if verdict not in ("allow", "exempt"):
        print(f"[export] 拒绝 {verdict}：{request.url.path} "
              f"来源={request.client.host if request.client else '?'} —— {reason}", flush=True)
        raise HTTPException(status_code=503 if verdict == "misconfig" else 401, detail=reason)

    fmt = "csv" if str(format).lower() == "csv" else "json"
    n_lim = max(1, min(int(limit or 1000), 5000))       # 上限防一次性拖库打爆内存
    sql = ("SELECT s.id, s.agent_id, s.title, s.created_at, s.updated_at, "
           "(SELECT COUNT(*) FROM chat_messages m WHERE m.session_id=s.id) AS messages "
           "FROM chat_sessions s")
    params: list = []
    if agent_id:
        sql += " WHERE s.agent_id=?"
        params.append(agent_id)
    sql += " ORDER BY s.updated_at DESC LIMIT ?"
    params.append(n_lim)
    rows = [dict(r) for r in db.query(sql, tuple(params))]

    meta = {"agent_id": agent_id or "*", "limit": n_lim, "with_messages": bool(with_messages)}
    if fmt == "csv" and with_messages:
        # CSV 是扁平表 ⇒ 导出正文时以「一行一条消息」呈现（表头恒定，下游可断言）
        mrows: list = []
        for r in rows:
            for m in db.query("SELECT role,content,created_at FROM chat_messages "
                              "WHERE session_id=? ORDER BY id ASC", (r.get("id"),)):
                mrows.append({"session_id": r.get("id"), "role": m.get("role"),
                              "created_at": m.get("created_at"), "content": m.get("content")})
        body, ctype, fname, meta = export_mod.render(
            mrows, export_mod.MESSAGE_COLUMNS, "csv", "messages", meta=meta, redact=bool(redact))
    else:
        if with_messages and fmt == "json":
            for r in rows:
                r["transcript"] = [
                    {"role": m.get("role"), "created_at": m.get("created_at"),
                     "content": m.get("content")}
                    for m in db.query("SELECT role,content,created_at FROM chat_messages "
                                      "WHERE session_id=? ORDER BY id ASC", (r.get("id"),))]
        body, ctype, fname, meta = export_mod.render(
            rows, export_mod.SESSION_COLUMNS, fmt, "sessions", meta=meta, redact=bool(redact))

    print(f"[export] {meta.get('kind')} fmt={fmt} rows={meta.get('count')} "
          f"redacted={'yes' if redact else 'no'} hits={meta.get('redacted_hits')} "
          f"来源={request.client.host if request.client else '?'}", flush=True)
    return Response(content=body, media_type=ctype,
                    headers={"Content-Disposition": f'attachment; filename="{fname}"',
                             "X-Export-Count": str(meta.get("count", 0)),
                             "X-Export-Redacted-Hits": str(meta.get("redacted_hits", 0))})


class SessionPatch(BaseModel):
    title: Optional[str] = None


@app.patch("/api/sessions/{session_id}")
async def rename_session(session_id: str, req: SessionPatch):
    if req.title is None:
        raise HTTPException(400, "no fields to update")
    n = db.execute("UPDATE chat_sessions SET title=?, updated_at=? WHERE id=?",
                   (req.title.strip()[:120], _now(), session_id))
    if not n:
        raise HTTPException(404, "session not found")
    return {"status": "renamed", "id": session_id, "title": req.title}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    n1 = db.execute("DELETE FROM chat_messages WHERE session_id=?", (session_id,))
    n2 = db.execute("DELETE FROM chat_sessions WHERE id=?", (session_id,))
    if not n2:
        raise HTTPException(404, "session not found")
    return {"status": "deleted", "id": session_id, "messages": n1}


# ── ⚙设置（口令保护的敏感配置查看）────────────────────────

class PasscodeRequest(BaseModel):
    passcode: str = ""


@app.post("/api/settings/term-token")
async def settings_term_token(request: Request, req: PasscodeRequest):
    """口令正确时返回 TERM_TOKEN。

    有意用 POST 而非 GET：现有 api_rate_limit 只拦写方法，
    口令爆破会被 429 限流拦住（60 次/分钟/IP）。
    """
    if not HUB_PASSCODE:
        raise HTTPException(503, "未设置 HUB_PASSCODE，请先在 .env 配置后再使用设置页")
    if not hmac.compare_digest(req.passcode, HUB_PASSCODE):
        ip = request.client.host if request.client else "?"
        print(f"[settings] 口令错误：{ip}")
        raise HTTPException(401, "口令错误")
    tok = os.getenv("TERM_TOKEN", "")
    return {"term_token": tok, "set": bool(tok)}


# ── 模型代理（前端动态加载：CCR/jcode 等 OpenAI 兼容 /v1/models）────

@app.get("/api/models")
async def list_models(agent_id: Optional[str] = None):
    """统一模型列表端点。

    默认拉 claude(CCR:3456) 的 /v1/models 作为"全局可对话模型"
    （v0.10.0 起 hub-self 已移除，claude/jcode 共用同一 CCR 模型表）。
    按 vendor/display_name 去重后返回；带分组（qwen/deepseek/nvidia/openrouter/agnes）。"""
    adapter_id = agent_id or "claude"
    adapter = get_adapter(adapter_id)
    base = None
    if hasattr(adapter, "base_url"):
        base = adapter.base_url
    if not base:
        return {"models": [], "groups": {}, "error": "no compatible adapter"}
    import aiohttp as _aio
    out: list = []
    try:
        async with _aio.ClientSession() as s:
            async with s.get(f"{base}/v1/models",
                             headers=adapter.build_headers() if hasattr(adapter, "build_headers") else {},
                             timeout=_aio.ClientTimeout(total=8)) as r:
                if r.status == 200:
                    data = await r.json()
                    out = data.get("data") or []
    except Exception as e:  # noqa: BLE001
        return {"models": out, "groups": {}, "error": str(e)[:200]}
    # 去重 + 分组
    seen = set()
    groups: dict = {}
    cleaned = []
    for m in out:
        mid = m.get("id")
        if not mid or mid in seen:
            continue
        seen.add(mid)
        cleaned.append({"id": mid, "name": m.get("display_name") or mid,
                        "owner": m.get("owned_by") or "?"})
        # 分组键 = mid 第一段（vendor/）
        gk = mid.split("/", 1)[0] if "/" in mid else "default"
        groups.setdefault(gk, []).append(mid)
    return {"models": cleaned, "groups": groups, "count": len(cleaned),
            "source": adapter_id}


# ── 端口管理 ──────────────────────────────────────────────────────────

# ── 只读发现扫描（S2）────────────────────────────────

class ScanIn(BaseModel):
    auto_register: bool = True


@app.post("/api/scan/run")
async def scan_run(body: ScanIn):
    """发现源扫描（docker/systemd/CLI 名单，全部只读探测）

    2026-09-24：扫描收尾补一次「定向重判」。原实现只跑 scanner.run_scan，
    而它「仅报告安装状态（不注册）」（见 scanner.py 顶部注释），卡片补发由
    profiles._dynamic_cli_agents 负责，其闸门读的是**上一轮 vitals 结论**
    ⇒ 点「自动扫描」永远刷不动一枚陈旧 not_installed，新装 CLI 出不来（opencode 实例）。
    重判只走 L1/L2（which / --version / --help），不碰模型、不烧 token。"""
    result = await asyncio.to_thread(scanner.run_scan, body.auto_register, db)
    rejudge: dict = {}
    try:
        rejudge = await asyncio.to_thread(
            vitals_mod.vitals.rejudge_stale, _agent_profs_for_vitals())
    except Exception as e:  # noqa: BLE001  重判挂了不能把扫描本身弄失败
        rejudge = {"error": "%s: %s" % (type(e).__name__, str(e)[:160])}
    result["vitals_rejudge"] = rejudge
    need_reload = bool(rejudge.get("changed")) or bool(
        body.auto_register and result.get("added"))
    if need_reload and discovery:
        if rejudge.get("changed"):
            profiles_mod.invalidate_cli_cache()   # 30s 候补缓存不得压住刚翻案的卡
        discovery.reload()
    return result


@app.get("/api/ports")
async def api_ports():
    rows = list_listeners()
    # 标注归属已知 Agent
    known = {}
    if discovery:
        for a in await discovery.discover_all():
            if a.port:
                known[a.port] = a.id
    for r in rows:
        r["agent"] = known.get(r["port"])
    return {"listeners": rows, "count": len(rows)}


@app.get("/api/ports/{port}")
async def api_port_detail(port: int):
    return {"port": port, "in_use": port_in_use(port)}


# ── Dashboard ─────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    resp = templates.TemplateResponse(request, "index.html", {"version": VERSION})
    # 2026-09-26：HTML 原先不带任何缓存头/验证器 ⇒ 浏览器与已开标签页长期不自愈，
    # 部署新版后用户看到的仍是旧 UI。no-cache = 可存，但每次导航必须回源校验。
    resp.headers["Cache-Control"] = "no-cache"
    return resp


# ── 兼容旧端点：/api/memory（别名到 L1 列表）──────────────────────────

@app.get("/api/memory")
async def get_memory_alias(query: Optional[str] = None, limit: int = 10):
    if not query:
        rows = db.query("SELECT * FROM memories WHERE status='active' ORDER BY id DESC LIMIT ?",
                        (limit,))
        return {"memories": rows, "count": len(rows)}
    return await memory_mod.search_memory(query, limit)


async def _get_pi_sessions(limit: int) -> list:
    if limit <= 0:
        return []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{config.pi_url}/api/sessions",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("sessions", [])[:limit]
    except Exception:  # noqa: BLE001
        pass
    return []


_embed_proxy = None      # 外框注入代理句柄（EMBED_UNIFY=0 时保持 None）


async def prov_loop():
    """代码溯源刷新：每 30s 一次 git status（走线程，不进任何请求路径）。

    为什么不搭 vitals 的慢拍：VITALS_SWEEP_SEC 默认 900s，而“工作区改了没”是
    健康台账里最想要分钟级响应的字段；搭慢拍会让它在 15 分钟里拿着旧值讲现测。
    """
    while True:
        try:
            await asyncio.to_thread(selfattest.refresh)
        except Exception as e:  # noqa: BLE001
            print("[prov] 溯源刷新异常（下轮重试）%s: %s" % (
                type(e).__name__, str(e)[:160]), flush=True)
        await asyncio.sleep(float(os.getenv("HUB_PROV_SEC", "30")))


MODEL_DRIFT_SWEEP_SEC = int(os.getenv("HUB_MODEL_DRIFT_SWEEP_SEC", "300"))
#: 漂移写回开关（用户 09-28 授权默认开；置 0/false/no ⇒ 退回 v0.13.50 的只读告警）
MODEL_DRIFT_REPAIR = os.getenv("HUB_MODEL_DRIFT_REPAIR", "1") not in ("0", "false", "no")


def _drift_tick() -> dict:
    """一轮「体检 +（授权时）写回」，返回值即证据。纯同步，跑在 to_thread 里。"""
    out: dict = {"repaired": [], "skipped": [], "drift": modelcfg.drift_report()}
    if MODEL_DRIFT_REPAIR:
        out.update(modelcfg.repair_drift())
    return out


def _drift_log(out: dict, where: str) -> None:
    for _d in out.get("drift") or []:
        _bad = "，".join(f"{k}={v}" for k, v in (_d.get("owned") or {}).items())
        print(f"[modelcfg] {where} 配置漂移：{_d['id']} 持久化={_d['hub_model']} 但文件里 {_bad}",
              flush=True)
    for _x in out.get("repaired") or []:
        print(f"[modelcfg] {where} 漂移已写回：{_x['id']} → {_x['model']}"
              + (f"（备份 {len(_x['backups'])} 份）" if not _x.get("dry_run") else "（dry-run）"),
              flush=True)
    for _x in out.get("skipped") or []:
        print(f"[modelcfg] {where} 漂移未写回：{_x['id']} —— {_x['reason']}", flush=True)


async def model_drift_loop():
    """配置漂移巡检。为什么不能只靠启动那一次：CCR 与 hub 都在开机时起来（今日实测
    同秒），CCR 落笔改写 claude 的 env 三兄弟可能**晚于** hub 的启动修复 ⇒ 修完又被
    盖回去。所以按 MODEL_DRIFT_SWEEP_SEC（默认 300s）定期复检并写回。异常不打死循环。"""
    while True:
        await asyncio.sleep(MODEL_DRIFT_SWEEP_SEC)
        try:
            _drift_log(await asyncio.to_thread(_drift_tick), "巡检")
        except Exception as e:  # noqa: BLE001
            print(f"[modelcfg] 巡检异常（下轮重试）{type(e).__name__}: {str(e)[:160]}", flush=True)


async def vitals_loop():
    """可用心跳慢周期：首轮延后 2s（先让 hub 开接请求），之后每 VITALS_SWEEP_SEC 一轮。
    只跑 L1/L2（which / 文件头 / --version / --help / 端点探活），不碰模型；
    任何异常都不打死循环（否则一次偶发就把菜单永久冻在旧结论上）。"""
    await asyncio.sleep(2)
    while True:
        try:
            r = await asyncio.to_thread(vitals_mod.vitals.sweep, _agent_profs_for_vitals())
            print("[vitals] sweep %s" % r, flush=True)
        except Exception as e:  # noqa: BLE001
            print("[vitals] sweep 异常（下轮重试）%s: %s" % (
                type(e).__name__, str(e)[:160]), flush=True)
        await asyncio.sleep(vitals_mod.SWEEP_EVERY)


@app.on_event("startup")
async def startup():
    global discovery
    global _embed_proxy
    db.init_db(config.db_path)
    discovery = AgentDiscovery(config, db=db)
    build_adapters(config)
    # v0.13.50 起的漂移体检 + v0.13.51 的授权写回：启动先修一轮（CCR 可能还没改写，
    # 所以另有定期巡检兜底）。写回属共享配置写入 ⇒ 落笔前时间戳备份，见 repair_drift。
    try:
        _drift_log(_drift_tick(), "启动")
    except Exception as e:  # noqa: BLE001 —— 体检/写回出岔子不许拖垮整个启动
        print(f"[modelcfg] 启动漂移处理异常：{type(e).__name__}: {str(e)[:160]}", flush=True)
    # 上游网关注入（/health 的 ccr_gateway 情报源）。watch 放两个「写死在配置里的模型 ID」：
    # manager 用的那个 + vitals L4 探针用的那个。上游一旦改名/下架，watch.<id>=false 当天可见，
    # 不必等探活烧一轮 token 才发现（09-23 的 M1 阻塞「拿不到在线清单」就此长期解除）。
    gwprobe.configure(config.manager_llm_base, config.manager_llm_key,
                      watch=[config.manager_llm_model, vitals_mod.RT_MODEL])
    tasks_mod.ensure_schema()
    tasks_mod.set_context(
        chat_fn=lambda a, m, s=None, mo=None, tr=None: _chat_dispatch(a, m, s, mo, tr),
        agent_ids_fn=lambda: [c["id"] for c in discovery.all_configs()])
    _spawn(tasks_mod.sweep_stale_tasks())
    _spawn(vitals_loop())
    _spawn(model_drift_loop())   # 配置漂移巡检（CCR 重启会把模型改回旧值）
    _spawn(prov_loop())   # 代码溯源（工作区脏度）刷新
    # P0-4：终端会话回收必须有独立心跳，不能寄生在前端轮询上
    _spawn(term_mod.reap_loop())
    mcpgw_mod.ensure_schema()
    cronjobs_mod.ensure_schema()
    cronjobs_mod.set_context(
        chat_fn=lambda a, m, s=None, mo=None, tr=None: _chat_dispatch(a, m, s, mo, tr))
    cronjobs_mod.start_engine()
    # 外框统一注入代理（用户 09-20 方案 b）：:3103 → qwenpaw :8088，HTML 出栈前插 <style>。
    # 起不来也不能影响 hub 本体：裹 try/except，顶多外框不统一（嵌入视图仍直连可用）。
    if profiles_mod.EMBED_UNIFY:
        try:
            qp = next((p for p in profiles_mod.PROFILES if p.get("id") == "qwenpaw"), None)
            if qp and qp.get("port"):
                _embed_proxy = embed_proxy_mod.EmbedProxy(
                    "QwenPaw", "127.0.0.1", qp["port"],
                    listen_host=os.getenv("EMBED_PROXY_HOST", config.host),
                    listen_port=profiles_mod.EMBED_PROXY_PORT)
                await _embed_proxy.start()
        except Exception as e:
            print(f"[Agent Hub] 注入代理启动失败（不影响其他功能）：{type(e).__name__}: {e}")
            _embed_proxy = None
    sa = selfattest.boot()   # 记下启动那一刻的 sha，供 /health 判 code_stale
    print(f"[Agent Hub] 启动完成 v{VERSION} sha={sa['git_sha_boot'] or '?'}，"
          f"监听 {config.host}:{config.port}")
    print(f"[Agent Hub] CCR: {config.ccr_url} | pi: {config.pi_url} | "
          f"jcode: {config.jcode_url} | TDAI: {config.tdaI_url}")
    print(f"[Agent Hub] LLM（记忆 L2 重建 / DAG 拆解）: {config.manager_llm_base} "
          f"model={config.manager_llm_model}")


@app.on_event("shutdown")
async def shutdown():
    for t in list(_bg_tasks):
        t.cancel()
    if _bg_tasks:
        await asyncio.gather(*_bg_tasks, return_exceptions=True)
        _bg_tasks.clear()
    if _embed_proxy is not None:
        await _embed_proxy.stop()
    term_mod.kill_all()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.main:app", host=config.host, port=config.port, log_level="info")
