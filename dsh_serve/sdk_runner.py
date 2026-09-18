"""dsh SDK 运行时管理：profile 初始化材料生成 + 分路由 harness 进程池。

- 主路由 deepseek-official/deepseek-v4-pro，备路由 kimi/k3（llm-pi-ai 自定义
  provider，anthropic-messages 协议）；每个路由一个懒启动的 dsh 子进程，
  路由内以锁串行，路由间并行（dsh 会话状态落盘共享于 DSH_HOME）。
- MCP 以 patch 层注入 dsh-mcp-client（stdio 拉起 qvp_mcp）；failOnStartupError
  = false，挂不上不阻断启动（对齐 failOnToolError:false 的容错语义）。
"""
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

from .config import Settings

logger = logging.getLogger("dsh_serve.runner")


class HarnessStartError(RuntimeError):
    """dsh 子进程启动/初始化失败。"""


@dataclass
class Route:
    """一条模型路由（主或备）。"""
    name: str
    provider: str
    model: str
    harness: object = None          # deepseek_harness.DeepSeekHarness，懒启动
    lock: threading.Lock = field(default_factory=threading.Lock)
    start_error: str | None = None
    requests_served: int = 0        # 本路由成功服务请求数
    last_activity_at: float | None = None   # 最近一次 run 开始/结束时间
    last_success_at: float | None = None    # 最近一次成功返回时间


def write_runtime_files(settings: Settings) -> tuple[str | None, str | None]:
    """在 DSH_HOME 写 settings.yaml（注册 kimi 备 provider + contextWindow
    口径修正）与 serve patch（禁用内置工具 + 插入 qvp MCP stdio 行）。

    返回 (settings_yaml_path, serve_patch_path)；patch 无任何行时为 None。
    """
    home = Path(settings.dsh_home)
    home.mkdir(parents=True, exist_ok=True)

    settings_path = home / "settings.yaml"
    # dsh 模型注册表默认 1M 窗口，与真实 131072 不符会让 compaction/截断阈值
    # 失真（对比分析差距 4）：两条路由都显式覆盖。
    doc: dict = {"llm-deepseek": {"defaultContextWindow": 131072}}
    providers: dict = {}
    if settings.fallback_enabled and settings.kimi_api_key:
        providers[settings.fallback_provider] = {
            "apiKeyEnv": "KIMI_API_KEY",
            "api": settings.fallback_api,
            "baseURL": settings.kimi_base_url,
            "models": [{"id": settings.fallback_model,
                        "contextWindow": 131072}],
        }
    if settings.fallback2_enabled and settings.kimi_code_api_key:
        providers[settings.fallback2_provider] = {
            "apiKeyEnv": "KIMI_CODE_API_KEY",
            "api": settings.fallback2_api,
            "baseURL": settings.kimi_code_base_url,
            "models": [{"id": settings.fallback2_model,
                        "contextWindow": 131072}],
        }
    if providers:
        doc["llm-pi-ai"] = {"providers": providers}
    settings_path.write_text(
        yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")

    patch: list[dict] = []
    if settings.disable_builtin_tools:
        patch.extend({"id": rid, "disabled": True} for rid in DISABLED_BUILTIN_ROWS)
    if settings.mcp_enabled and settings.mcp_command:
        env = {"PYTHONPATH": settings.mcp_pythonpath, "PYTHONUTF8": "1"}
        env.update(json.loads(settings.mcp_extra_env or "{}"))
        patch.append({
            "insert": [{
                "id": "mcp-qvp",
                "name": "@deepseek-ai/dsh-mcp-client",
                "config": {
                    "transport": "stdio",
                    "serverName": settings.mcp_server_name,
                    "command": settings.mcp_command,
                    "args": json.loads(settings.mcp_args),
                    "env": env,
                    "cwd": settings.dsh_workspace,
                    "toolCallTimeoutMs": settings.mcp_tool_timeout_ms,
                    "failOnStartupError": False,
                },
            }],
        })
    patch_path: str | None = None
    if patch:
        patch_path = str(home / "dsh-serve.mcp.patch.yml")
        Path(patch_path).write_text(
            yaml.safe_dump(patch, allow_unicode=True), encoding="utf-8")
    return str(settings_path), patch_path


#: serve patch 禁用的 base 内置工具行（id 对照 sdk profile dump 逐一确认，
#: 2026-09-07）：shell、fs 读写/检索（含 read_image）、编辑器、todo/goal/
#: ralph、子代理、workflow、web 检索——全是模型面 tool-* 行。保留：skill
#: 三件套、MCP 客户端，以及 goal/goal-round-driver/plan-mode/subagent 等
#: 行为插件（实测：禁用 goal-round-driver 会导致 turn 无人驱动、请求挂死，
#: 2026-09-07 实证，勿再加入禁用列表）。
DISABLED_BUILTIN_ROWS = (
    "tool-bash", "tool-pwsh", "tool-jobs",
    "tool-fs", "tool-fs-search", "tool-str-replace-editor",
    "tool-todo", "tool-goal", "tool-ralph",
    "tool-subagent-control", "tool-subagent-list-agents",
    "tool-subagent", "tool-subagent-fork", "tool-workflow",
    "tool-web",
    "plan-mode",
    # agent preset 层（standard/ptc）在 planning 组下又挂了一份 plan-mode
    # （exit_plan_mode 的残留来源，2026-09-07 实证）；嵌套 id 路径禁用。
    "planning:plan-mode",
)


class HarnessPool:
    """按路由管理 DeepSeekHarness 实例（每路由一个常驻 dsh 子进程）。"""

    #: 路由重启兜底：每服务 N 次请求后主动重建 harness，避免子进程长期运行累积死锁
    RESTART_INTERVAL_REQUESTS: int = 30
    #: 路由重启兜底：超过 N 秒没有成功返回则主动重建
    RESTART_INTERVAL_SECONDS: float = 1800.0

    def __init__(self, settings: Settings,
                 harness_factory: Callable | None = None) -> None:
        self.settings = settings
        _, self.mcp_patch = write_runtime_files(settings)
        self._factory = harness_factory or self._default_factory
        self._boot_lock = threading.Lock()  # 首启串行：两个路由并发初始化会同抢
        # DSH_HOME/profiles/node_modules.lock（fresh home 首次链接依赖时必现）
        self.routes: dict[str, Route] = {
            "primary": Route("primary", settings.primary_provider, settings.primary_model),
            "fallback": Route("fallback", settings.fallback_provider, settings.fallback_model),
            "fallback2": Route("fallback2", settings.fallback2_provider, settings.fallback2_model),
        }
        self._closed = False

    # --- 构造 ---
    def _default_factory(self, route: Route):
        from deepseek_harness import DeepSeekHarness

        patches = (self.mcp_patch,) if self.mcp_patch else ()
        env = {}
        if self.settings.deepseek_api_key:
            env["DEEPSEEK_API_KEY"] = self.settings.deepseek_api_key
        if self.settings.kimi_api_key:
            env["KIMI_API_KEY"] = self.settings.kimi_api_key
        if self.settings.kimi_code_api_key:
            env["KIMI_CODE_API_KEY"] = self.settings.kimi_code_api_key
        env["DSH_TELEMETRY_MODE"] = "DISABLED"
        kwargs = dict(
            dsh_home=self.settings.dsh_home,
            cwd=self.settings.dsh_workspace,
            provider=route.provider,
            model=route.model,
            max_tokens=self.settings.dsh_max_tokens,
            patches=patches,
            env=env,
            request_timeout_seconds=self.settings.request_timeout_seconds,
            initialize_timeout_seconds=self.settings.dsh_initialize_timeout_seconds,
        )
        if route.name == "primary":
            kwargs["base_url"] = self.settings.deepseek_base_url
        if self.settings.dsh_reasoning_effort:
            kwargs["reasoning_effort"] = self.settings.dsh_reasoning_effort
        return DeepSeekHarness(**kwargs)

    def _ensure_started(self, route: Route) -> None:
        if route.harness is not None:
            return
        with self._boot_lock:  # 串行化首启，避免 node_modules.lock 竞争
            if route.harness is not None:
                return
            try:
                h = self._factory(route)
                h.start()
                route.harness = h
                route.start_error = None
                logger.info("route started route=%s model=%s", route.name, route.model)
            except Exception as e:  # noqa: BLE001
                try:
                    h.close()  # 收尸半启动的子进程，允许下一请求重试
                except Exception:  # noqa: BLE001
                    pass
                route.harness = None
                route.start_error = f"{type(e).__name__}: {e}"  # 仅诊断展示，不阻断重试
                raise HarnessStartError(
                    f"dsh 子进程启动失败（route={route.name}）：{e}") from e

    def run(self, route_name: str, prompt: str, session_id: str,
            on_event: Callable[[dict], None]):
        """同步执行一轮（在 worker 线程里调用）；返回 RunResult。

        on_event 回调收到的是 session.event 的 event 字典（根会话事件流）。
        执行前会按请求数/时间兜底重建 harness，减少子进程长期运行死锁风险。
        """
        route = self.routes[route_name]
        with route.lock:  # 同路由串行；进程崩溃后由调用方决定是否换路由
            now = time.time()
            # 兜底重建：请求数或空闲时间达到阈值时主动换 harness
            if route.harness is not None and (
                route.requests_served >= self.RESTART_INTERVAL_REQUESTS
                or (route.last_success_at is not None
                    and now - route.last_success_at >= self.RESTART_INTERVAL_SECONDS)
            ):
                self._close_route(route)
            self._ensure_started(route)

            def on_notification(n) -> None:
                if getattr(n, "method", None) != "session.event":
                    return
                payload = getattr(n, "payload", {}) or {}
                if payload.get("sessionId") != session_id:
                    return
                ev = payload.get("event")
                if isinstance(ev, dict):
                    on_event(ev)

            route.last_activity_at = now
            try:
                result = route.harness.run(prompt, session_id=session_id,
                                           on_notification=on_notification)
                route.requests_served += 1
                route.last_success_at = time.time()
                return result
            except Exception:
                route.last_activity_at = time.time()
                raise

    def _close_route(self, route: Route) -> None:
        """关闭并清理单个路由的 harness，允许下一次 _ensure_started 重建。"""
        if route.harness is None:
            return
        try:
            route.harness.close()
            logger.info("route closed for restart route=%s model=%s "
                        "requests_served=%d",
                        route.name, route.model, route.requests_served)
        except Exception:  # noqa: BLE001
            logger.warning("route close failed on restart route=%s", route.name)
        finally:
            route.harness = None
            route.requests_served = 0
            route.start_error = None

    def restart_route(self, route_name: str) -> None:
        """外部探针/看门狗调用：强制重建指定路由。"""
        route = self.routes.get(route_name)
        if route is None:
            return
        if route.lock.locked():
            logger.info("route restart skipped (in-flight) route=%s", route_name)
            return
        with route.lock:
            self._close_route(route)

    def maybe_restart_stale_routes(self) -> None:
        """看门狗调用：重启长时间无成功响应或请求数超限的路由。"""
        now = time.time()
        for route in self.routes.values():
            if route.harness is None:
                continue
            if route.lock.locked():
                continue
            if route.requests_served >= self.RESTART_INTERVAL_REQUESTS:
                with route.lock:
                    self._close_route(route)
                continue
            if (route.last_success_at is not None
                    and now - route.last_success_at >= self.RESTART_INTERVAL_SECONDS):
                with route.lock:
                    self._close_route(route)

    # --- 健康与生命周期 ---
    @staticmethod
    def _route_alive(route: Route) -> bool:
        if route.harness is None:
            return False
        try:
            proc = route.harness.client._proc  # SDK 未暴露存活探测，读 Popen 句柄
            return proc is not None and proc.poll() is None
        except Exception:  # noqa: BLE001
            return False

    def _route_snapshot(self, route: Route) -> dict:
        return {
            "model": route.model,
            "started": route.harness is not None,
            "alive": self._route_alive(route),
            "last_start_error": route.start_error,
            "requests_served": route.requests_served,
            "last_success_at": route.last_success_at,
            "last_activity_at": route.last_activity_at,
        }

    def health(self) -> dict:
        s = self.settings
        return {
            "primary": self._route_snapshot(self.routes["primary"]),
            "fallback": {
                **self._route_snapshot(self.routes["fallback"]),
                "enabled": s.fallback_enabled and bool(s.kimi_api_key),
            },
            "fallback2": {
                **self._route_snapshot(self.routes["fallback2"]),
                "enabled": s.fallback2_enabled and bool(s.kimi_code_api_key),
            },
            "mcp": {"enabled": s.mcp_enabled and bool(s.mcp_command),
                    "server_name": s.mcp_server_name,
                    "command": s.mcp_command,
                    "command_exists": bool(s.mcp_command) and Path(s.mcp_command).exists(),
                    "tool_timeout_ms": s.mcp_tool_timeout_ms,
                    "fail_on_startup_error": False},
            "dsh_home": s.dsh_home,
        }

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for route in self.routes.values():
            if route.harness is not None:
                try:
                    route.harness.close()
                except Exception:  # noqa: BLE001
                    logger.warning("route close failed route=%s", route.name)
