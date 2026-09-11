"""运行时材料（settings.yaml / serve patch）生成单测。"""
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dsh_serve.sdk_runner import (DISABLED_BUILTIN_ROWS,  # noqa: E402
                                  write_runtime_files)

from .conftest import make_settings  # noqa: E402


def _load_patch(path):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def test_patch_disables_all_builtin_agent_tools(tmp_path):
    """shell/fs/vision/web/todo/goal/subagent/workflow 等内置行全部禁用。"""
    s = make_settings(tmp_path, mcp_enabled=False)
    _, patch_path = write_runtime_files(s)
    patch = _load_patch(patch_path)
    disabled = {r["id"] for r in patch if r.get("disabled")}
    assert disabled == set(DISABLED_BUILTIN_ROWS)
    # 抽查关键 id（对照 sdk profile dump 逐一确认过的）
    for rid in ("tool-bash", "tool-pwsh", "tool-fs", "tool-web",
                "tool-str-replace-editor", "tool-subagent"):
        assert rid in disabled
    # skill 三件套、turn 驱动器不在禁用列表——
    # goal-round-driver 是 turn 驱动器，禁用会挂死请求（2026-09-07 实证）
    for keep in ("tool-skill", "skill", "goal", "goal-round-driver",
                 "subagent"):
        assert keep not in disabled
    # plan-mode（exit_plan_mode 工具）在禁用列表——serve 无计划模式场景
    assert "plan-mode" in disabled
    assert not any("insert" in row for row in patch)  # MCP 未启用则无 insert


def test_patch_mcp_row_and_disables_together(tmp_path):
    s = make_settings(tmp_path, mcp_enabled=True,
                      mcp_command="C:/py/python.exe",
                      mcp_pythonpath="C:/code")
    _, patch_path = write_runtime_files(s)
    patch = _load_patch(patch_path)
    assert {"id": "tool-web", "disabled": True} in patch
    insert = [r for r in patch if "insert" in r][0]["insert"][0]
    cfg = insert["config"]
    assert insert["name"] == "@deepseek-ai/dsh-mcp-client"
    assert cfg["transport"] == "stdio" and cfg["serverName"] == "qvp"
    assert cfg["toolCallTimeoutMs"] == 1800000
    assert cfg["failOnStartupError"] is False
    assert cfg["env"]["PYTHONUTF8"] == "1" and cfg["env"]["PYTHONPATH"] == "C:/code"


def test_builtin_tools_can_be_kept(tmp_path):
    s = make_settings(tmp_path, mcp_enabled=False, disable_builtin_tools=False)
    _, patch_path = write_runtime_files(s)
    assert patch_path is None  # 无任何 patch 行则不生成文件


def test_settings_yaml_registers_kimi_provider(tmp_path):
    s = make_settings(tmp_path)
    settings_path, _ = write_runtime_files(s)
    raw = Path(settings_path).read_text(encoding="utf-8")
    doc = yaml.safe_load(raw)
    kimi = doc["llm-pi-ai"]["providers"]["kimi"]
    assert kimi["api"] == "openai-completions"   # 备1：开放平台（2026-09-10 起）
    assert kimi["baseURL"] == "https://api.moonshot.cn/v1"
    assert kimi["apiKeyEnv"] == "KIMI_API_KEY"
    assert kimi["models"] == [{"id": "kimi-k3", "contextWindow": 131072}]
    assert s.kimi_api_key not in raw  # 密钥不落盘


def test_settings_yaml_context_window_fix(tmp_path):
    """两条路由 contextWindow 都覆盖为 131072（对齐 nanobot preset）。"""
    s = make_settings(tmp_path)
    settings_path, _ = write_runtime_files(s)
    doc = yaml.safe_load(Path(settings_path).read_text(encoding="utf-8"))
    assert doc["llm-deepseek"]["defaultContextWindow"] == 131072
    kimi_models = doc["llm-pi-ai"]["providers"]["kimi"]["models"]
    assert kimi_models[0]["contextWindow"] == 131072


def test_settings_yaml_context_window_without_kimi_key(tmp_path):
    """无备 key：contextWindow 修正仍写，kimi provider 不注册，不写空壳。"""
    s = make_settings(tmp_path, kimi_api_key="")
    settings_path, _ = write_runtime_files(s)
    doc = yaml.safe_load(Path(settings_path).read_text(encoding="utf-8"))
    assert "llm-pi-ai" not in doc
    assert doc["llm-deepseek"]["defaultContextWindow"] == 131072


def test_settings_yaml_registers_kimi_code_provider(tmp_path):
    """备2：KIMI_CODE_API_KEY 引用、anthropic-messages、api.kimi.com/coding。"""
    s = make_settings(tmp_path, kimi_code_api_key="k2-secret")
    settings_path, _ = write_runtime_files(s)
    raw = Path(settings_path).read_text(encoding="utf-8")
    doc = yaml.safe_load(raw)
    providers = doc["llm-pi-ai"]["providers"]
    kc = providers["kimi-code"]
    assert kc["apiKeyEnv"] == "KIMI_CODE_API_KEY"
    assert kc["api"] == "anthropic-messages"
    assert kc["baseURL"] == "https://api.kimi.com/coding"
    assert kc["models"] == [{"id": "k3", "contextWindow": 131072}]
    assert "kimi" in providers  # 备1 仍注册
    assert "k2-secret" not in raw  # 密钥不落盘


def test_settings_yaml_no_fallback2_without_key(tmp_path):
    s = make_settings(tmp_path)  # 默认 kimi_code_api_key=""
    settings_path, _ = write_runtime_files(s)
    doc = yaml.safe_load(Path(settings_path).read_text(encoding="utf-8"))
    providers = doc["llm-pi-ai"]["providers"]
    assert "kimi-code" not in providers and "kimi" in providers
