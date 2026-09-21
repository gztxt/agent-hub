"""Agent 发现（画像驱动版：按本机实际运行检测，非端口判定）"""
import asyncio
import json
import re
import socket
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import aiohttp

import profiles
import vitals


def _verdict_view(p: dict, vrec: Optional[dict]):
    """把 vitals 记录翻译成前端字段（usable / 中文理由）。没结论时 usable=None，不装懂。"""
    if not vrec:
        return {"usable": None, "verdict": "pending", "reason": "尚未体检（等首轮探针跑完）",
                "source": "pending", "confidence": None, "at": None, "attested": None}
    v = vrec.get("verdict", "unknown")
    ev = vrec.get("evidence", {})
    if v == "usable":
        if ev.get("agent_shape") == "terminal-cli":
            if ev.get("run_ok") or vrec.get("l4_run_ok"):
                reason = "实测应答正常"
            elif ev.get("run_rc") is not None:
                reason = "真实请求已跑但未见正常应答（rc=%s），只能算自述正常" % ev.get("run_rc")
            else:
                reason = "可执行文件自述正常（本轮未做真实应答实测）"
        else:
            code = ev.get("endpoint_http_v4") or ev.get("endpoint_http_v6")
            reason = "自有端点应声 HTTP %s" % code
    else:
        reason = {
            "not_installed": ("假卡：命中的是入口壳 %s，它打印「找不到目标命令」后就退出，"
                              "该 Agent 本体未安装" % (ev.get("resolved_path") or "-")),
            "blocked_by_account": "已安装但被账号条件拦住（额度/登录未就绪），需人工恢复",
            "broken": "已安装但启动失败/超时无响应",
            "stopped": "服务未运行（端口无应答），程序本身没坏",
        }.get(v, "证据不足，未能判定")
    l4at = vrec.get("l4_at")
    if vrec.get("flaky"):
        reason += "（本轮真实请求没成功，先当抖动；连错 %s 次才改判）" % (
            vrec.get("neg_streak") or 1)
    if vrec.get("probe_defect"):
        reason += "（注：上轮真请求探针被 CLI 参数/信任检查拒过，该结论未拿它作证）"
    if l4at and v not in ("usable", "pending"):
        reason += "（依据 %s 的真实请求实测）" % datetime.fromtimestamp(
            l4at, timezone.utc).strftime("%m-%d %H:%M")
    usable = v == "usable"
    # 凭据：真应答跑通过（L4），或服务型 Agent 自有端口在应声
    attested = bool(usable and (vrec.get("l4_run_ok") or ev.get("run_ok") or
                                (ev.get("agent_shape") == "web-service" and
                                 ev.get("endpoint_serving"))))
    ts = vrec.get("checked_at")
    return {"usable": usable, "verdict": v, "reason": reason,
            "source": vrec.get("source", "rule"), "confidence": vrec.get("confidence"),
            "at": datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None,
            "attested": attested}


@dataclass
class AgentInfo:
    id: str
    name: str
    kind: str          # agent|gateway|service|memory|tool
    status: str        # running|installed|stopped
    endpoint: str
    port: Optional[int]
    auth_required: bool
    description: str = ""
    config_path: Optional[str] = None
    last_seen: Optional[str] = None
    builtin: bool = True
    working_dir: Optional[str] = None
    entries: List[dict] = field(default_factory=list)
    # ── vitals 判定层（2026-09-21）：status 说「在不在」，verdict 说「能不能用」
    usable: Optional[bool] = None
    verdict: str = ""
    verdict_reason: str = ""
    verdict_source: str = ""      # jev | jev-lowconf | rule | pending
    verdict_confidence: Optional[float] = None
    verdict_at: Optional[str] = None
    # attested：这条「可用」是有实测凭据支撑的（真应答 / 自有端点应声）。
    # 只跑了 L2 自述探针时为 False —— 前端据此显示「未见异常」而不是「可用」。
    attested: Optional[bool] = None

    def to_dict(self):
        return asdict(self)


class AgentDiscovery:
    def __init__(self, config, db=None):
        self.config = config
        self.db = db
        self._cache: Dict[str, AgentInfo] = {}

    async def discover_all(self) -> List[AgentInfo]:
        procs, units, dockers = await asyncio.to_thread(self._probe_env)
        # 第一遍：全部状态（供跨画像联动，如 claude 的 Web UI 宿主 cloudcli）
        prof_list = profiles.all_profiles()
        status_map: Dict[str, str] = {}
        for p in prof_list:
            st = profiles.detect_status(p, procs, units, dockers)
            port = p.get("port")
            if st == "stopped" and port and self._sync_check_port(port):
                st = "running"
            status_map[p["id"]] = st
        agents: List[AgentInfo] = []
        for p in prof_list:
            status = status_map[p["id"]]
            ui = p.get("ui")
            # vitals 闸门放在发现层（每个请求都算）：profiles 那边的同名单子有 30s 缓存，
            # 只靠它会造成「每次重启后假卡再活 30 秒」的反复。
            # 判定为不可用的统一跳过不出卡（静态画像、动态发现一律处理）。
            # 异常时判定层挂掉也不能把菜单清空。
            vrec = None
            try:
                vrec = vitals.vitals.get(p["id"])
            except Exception:  # noqa: BLE001
                vrec = None
            # 只有 truly gone（not_installed / broken）才跳过；
            # blocked_by_account 是额度/登录问题，agent 本身可用，保留卡片标状态。
            if vrec and vrec.get("verdict") in ("not_installed", "broken"):
                continue
            entries = profiles.entries_for(p, status)
            # 通用宿主探测：dict ui = 独立 Web 界面宿主（如 claude←cloudcli :3010），
            # 端口活才出「原生会话」入口，且计入实体 running 证据
            if isinstance(ui, dict):
                ui_alive = self._sync_check_port(ui.get("port"))
                if ui_alive:
                    entries = ([{"type": "embed", "label": "原生会话", "url": ui["url"]},
                                {"type": "open", "label": "↗UI", "url": ui["url"]}]
                               + entries)
                    if status in ("installed", "stopped"):
                        status = "running"
                endpoint = ui["url"] if ui_alive else (p.get("panel") or "")
            else:
                endpoint = ui or p.get("panel") or (f"http://127.0.0.1:{p['port']}" if p.get("port") else "")
            # 死面板不出按钮（probe_port 过滤）
            entries = [e for e in entries
                       if not (e.get("probe_port") and not self._sync_check_port(e["probe_port"]))]
            # 判定层异常 / 无记录：正常出卡，前端会显示 null 字段
            vv = _verdict_view(p, vrec)
            agents.append(AgentInfo(
                id=p["id"], name=p["name"], kind=p["kind"], status=status,
                endpoint=endpoint, port=p.get("port"),
                auth_required=bool(p.get("panel_auth")) or
                bool(p.get("panel") and re.search(r"18083|12700", str(p.get("panel")))),
                description=p.get("desc", ""),
                config_path=self._find_config_path(p["id"]),
                last_seen=self._iso_now(), builtin=True,
                working_dir=(p.get("terminal") or {}).get("cwd"),
                entries=entries,
                usable=vv["usable"], verdict=vv["verdict"],
                verdict_reason=vv["reason"], verdict_source=vv["source"],
                verdict_confidence=vv["confidence"], verdict_at=vv["at"],
                attested=vv["attested"]))
        # 动态注册/扫描项（一律 service 类，仅快捷方式）
        for c in self._custom_agents():
            port = c.get("port")
            alive = bool(port and self._sync_check_port(port))
            agents.append(AgentInfo(
                id=c["id"], name=c["name"], kind="service",
                status="running" if alive else "stopped",
                endpoint=c.get("endpoint") or "", port=port, auth_required=False,
                description=c.get("description") or "", last_seen=self._iso_now(),
                builtin=False, working_dir=c.get("working_dir"),
                entries=([{"type": "open", "label": "打开面板", "url": c["endpoint"]}]
                         if c.get("endpoint") else []) + [{"type": "detail", "label": "详情"}]))
        for a in agents:
            self._cache[a.id] = a
        return agents

    def _probe_env(self):
        return (profiles.list_processes(),
                profiles.running_systemd_units(),
                profiles.docker_states())

    def _custom_agents(self) -> List[Dict]:
        if not self.db:
            return []
        try:
            rows = self.db.query("SELECT * FROM custom_agents ORDER BY created_at")
        except Exception:  # noqa: BLE001
            return []
        # 与画像去重：端口命中任一画像 port/panel 端口的 scan 项跳过
        prof_ports = set()
        for p in profiles.all_profiles():
            for k in ("port", "ui", "panel"):
                v = p.get(k)
                if isinstance(v, int):
                    prof_ports.add(v)
                elif isinstance(v, str):
                    m = re.search(r":(\d{2,5})", v)
                    if m:
                        prof_ports.add(int(m.group(1)))
        out = []
        for r in rows:
            if r["port"] and r["port"] in prof_ports:
                continue
            out.append(r)
        return out

    def reload(self):
        self._cache.clear()

    def _sync_check_port(self, port: Optional[int]) -> bool:
        """双栈探活（2026-09-21）：127.0.0.1 拒连不等于服务没起。
        实测 qwenpaw 2.2.1 只监听 IPv6（:8088 上 127.0.0.1=拒连、::1=通），
        单栈探测会把它误报成离线。"""
        if not port:
            return False
        return vitals.port_open(port) or vitals.port_open(port, "::1")

    def _find_config_path(self, agent_id: str) -> Optional[str]:
        paths = {
            "claude": Path.home() / ".claude" / "settings.json",
            "pi": Path.home() / ".pi" / "settings.json",
            "jcode": Path.home() / ".config" / "jcode" / "config.toml",
            "grok": Path.home() / ".grok" / "config.toml",
            "hermes": Path.home() / ".hermes" / "config.yaml",
            "ccr": Path.home() / ".ccr" / "config.json",
        }
        path = paths.get(agent_id)
        return str(path) if path and path.exists() else None

    def _iso_now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def get_agent(self, agent_id: str) -> Optional[AgentInfo]:
        return self._cache.get(agent_id)
