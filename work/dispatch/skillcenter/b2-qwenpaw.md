# b2 · qwenpaw 技能发现面取证（只读）

> 落盘者：主会话（单写者，`agent-dispatch` §1.2）。内容由 pi 子代理 `qwenpaw-recon`（Explore，只读）产出，
> 主会话已对 4 条关键断言逐条自验证（§4.3），见文末 §9。
> 取证时间：2026-10-03｜主机 gztxt@zzst｜全部只读命令，未发任何 HTTP 请求

## 0. 结论

**存在技能发现面，且已在本机启用**：发现根 = `~/.qwenpaw/skill_pool`（主池）
+ 配置键 `skill_paths` 声明的额外只读根（本机 = `~/.qwenpaw/skills`）。
🔴 **但已铺的 2 条软链不在清单里 ⇒ 经池 API 不可见**，详见 §5。

## 1. 声明键
- `/home/gztxt/.qwenpaw/config.json:1073-1075`：
  ```json
  "skill_paths": ["/home/gztxt/.qwenpaw/skills"]
  ```
- 键定义：`/vol1/qwenpaw/lib/python3.11/site-packages/qwenpaw/config/config.py:3139-3145`
  `skill_paths: List[str]`，default `[]`，说明「Additional read-only skill pool roots,
  scanned after the primary skill_pool in order. Paths support ~ expansion.
  Skills found here are read-only (no edit/create)」
- 佐证该键为显式写入而非残留：`config.json.bak-20260930_010626-add-skill-paths-agent-dispatch:1073`
  原值为 `"skill_paths": []`。

## 2. 读取链（运行中安装 `/vol1/qwenpaw`，非仅源码仓）
| 环节 | 位置 |
|---|---|
| 额外根解析 | `agents/skill_system/store.py:129-152` `get_extra_skill_dirs()` → L134 `list(load_config().skill_paths or [])`；L144-149 逐条 `expanduser().resolve()`，非目录则跳过 |
| 根列表 | `store.py:155-157` `get_skill_pool_dirs()` = `[get_skill_pool_dir(), *get_extra_skill_dirs()]`（主池优先） |
| 主池目录 | `store.py:82-86` → `Path(WORKING_DIR)/"skill_pool"` |
| WORKING_DIR | `constant.py:113` → `~/.qwenpaw`（可被 `QWENPAW_WORKING_DIR`/`COPAWA_WORKING_DIR` 覆盖，见 `constant.py:101-113`） |

## 3. 发现判据
- `agents/skill_system/registry.py:910-933` `_discover_pool_skill_dirs()`
  遍历全部根，`sorted(root.iterdir())`；命中条件 **L923** `path.is_dir() and (path/"SKILL.md").exists()`；
  同名以先到者胜，被遮蔽者 `logger.warning("... is shadowed by ...")`
- 按名解析：`store.py:160-172` `resolve_pool_skill_dir()` —— 首个含 `SKILL.md` 的 `<root>/<name>` 命中
- 目录名净化：`store.py:808-828` `normalize_skill_dir_name` 拒空名/控制字符/`.`/`..`/`/`/`\`；
  `store.py:830-844` `safe_skill_dir` 追加 `is_relative_to` 越界检查

## 4. 本机现状：已铺 2 条软链

```
/home/gztxt/.qwenpaw/skills/agent-dispatch -> /fs/1000/ftp/技术文档/skills/agent-dispatch   (Sep 30 00:26)
/home/gztxt/.qwenpaw/skills/unified-memory -> /fs/1000/ftp/技术文档/skills/unified-memory   (Oct  3 00:17)
```
`find -L … -maxdepth 2 -name SKILL.md` 穿透软链命中 2 条（**主会话已复核 = 2**）。
即 `PT-20261002-11` 批次②（技能分发）已覆盖 qwenpaw。

## 5. 🔴 缺口：软链建好 ≠ 生效

- `GET /skills/pool` 实现 `app/routers/skills.py:764-789` `_build_pool_skill_specs()`：
  **只遍历清单** `read_skill_pool_manifest()` 的 `manifest["skills"]`（L765），
  逐条 `resolve_pool_skill_dir()`（L769/778）—— **从不遍历磁盘目录**。
- `~/.qwenpaw/skill_pool/skill.json` 的 `skills[]` 共 **16 条，全部为 builtin**
  （`QA_source_index` / `browser` / `channel_message` / `chat_with_agent` / `cron` /
  `dingtalk_channel` / `docx` / `file_reader` / `guidance` / `himalaya` …），
  **零命中** `agent-dispatch` / `unified-memory`。
- ⇒ **这 2 条软链经池 API 不可见**，需触发一次 reconcile 把 discovered 写回清单。
  入口：`registry.py:994` `reconcile_pool_manifest()`（L1021 调 `_discover_pool_skill_dirs()`，
  L1046-1047 删已消失条目）；调用方 `pool_service.py:1434-1438` `refresh_pool_automation()`
  （HTTP `POST /skills/pool/refresh`，`skills.py:1045`）或 CLI `qwenpaw skills list|config|enable|…`
  （`cli/skills_cmd.py:175/447/557/740`）。
- **口径**：`registry.py:910-933` 的**发现**是遍历磁盘的（`~/.qwenpaw/skills` 在根列表内），
  清单只是**缓存**。所以「reconcile 一跑就会被收录」；但**qwenpaw 推理时到底读发现结果还是读缓存清单，
  本轮无运行时证据 ⇒ 判「不可判定」，不得写成已生效。**

## 6. API 面（供技能中心验收）
`app/routers/skills.py:72` `router = APIRouter(prefix="/skills", tags=["skills"])`

| 端点 | 行号 | 用途 |
|---|---|---|
| `GET /skills` | 934 | 当前工作区技能 |
| `GET /skills/workspaces` | 968 | 工作区列表 |
| `GET /skills/pool` | 1040 | 跨全部根的池清单（**受 §5 清单门约束**） |
| `POST /skills/pool/refresh` | 1045 | reconcile，落盘清单 |
| `GET｜DELETE /skills/pool/{skill_name}` | 1515 / 1523 | 单条取用 / 移除 |

## 7. 次级/等价面
- 工作区级可编辑技能目录：`store.py:88-100` `get_workspace_skill_source_dir()` → `<workspace>/skills`
  （legacy 名 `skill`，存在则自动 rename）；工作区清单 `<workspace>/skill.json`；
  reconcile `registry.py:1054+` `reconcile_workspace_manifest()`
- 本机工作区：`~/.qwenpaw/workspaces/{default, QwenPaw_QA_Agent_0.2}`
- 工具侧：`config.json:717-724` `materialize_skill`（enabled=true）
- 安全侧：`config.json:987-991` `security.skill_scanner` `{mode:"warn", timeout:30, whitelist:[]}`
  ⇒ 额外根技能会被扫描，`whitelist` 为空
- 独立于 skill 机制的 rules/prompt 注入面：**未取证**

## 8. 未取证项
- 未发任何 HTTP 请求（无 curl 探活）⇒ §5「是否已生效」无运行时证据
- `~/.qwenpaw/qwenpaw.log` 中 `grep -c -i skill` = **0**，日志不含技能加载记录 ⇒ 不能用日志证实发现行为
- 源码仓 `/vol1/1000/技术文档/QwenPaw/src/.../store.py`(41777B) 与运行安装
  `/vol1/qwenpaw/.../store.py`(41982B) 字节不同，但所引函数逐行一致（已分别取证），未逐字节 diff
- `~/.qwenpaw/skill_pool/*` 各内置技能目录 mode=000，本账号无权读取其内容

## 9. 主会话自验证（§4.3）

| 断言 | 复核方式 | 结果 |
|---|---|---|
| `config.json:1073-1075` 声明 `skill_paths` | `sed -n` 直读 | ✅ `["/home/gztxt/.qwenpaw/skills"]` |
| 2 条软链存在且含 `SKILL.md` | `ls -la` + `find -L` | ✅ 2/2 |
| 清单零命中 | `grep -E 'agent-dispatch\|unified-memory' skill.json` | ✅ **rc=1 零命中**；`skills[]` 实为 16 条 builtin |
| `/skills/pool` 只读清单 | `sed -n '764,770p' skills.py` | ✅ `manifest.get("skills", {})` 循环，**无目录遍历** |

## 10. 🔴 带出的台账纠正（已加横幅，未删原条目）

`PENDING-TASKS.md` 的 `PT-20261002-11` 状态栏写「分发层已闭合（**9/9 链接 + 9/9 体检 PASS**）」。
就 qwenpaw 一家而言，该体检是**链接存在性**检查，**不是生效性**检查：
链接在、清单无、`/skills/pool` 不返回 ⇒ 「纳管 ≠ 可用」第三次复现
（前两次：09-23 opencode 入册却不能干活、jcode 在册却挂死 11 天）。
**未修**：`POST /skills/pool/refresh` 是对 qwenpaw 的写操作，且 qwenpaw 现有配置在本批禁改清单内 ⇒ 停手报请。
