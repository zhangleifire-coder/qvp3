"""会话落盘镜像与跨进程恢复。

dsh sdk profile 的 JSON-RPC 协议无 resume 方法：会话在进程内复用正常，
但进程重启后用同一 session_id 新建会话会被持久化层拒绝
（"id collision"——新会话 seed 覆盖不了盘上已有日志前缀，dsh 的
cold-resume 只在 web profile 的 session-controller 里，sdk 路径没有）。

薄层对策（全部在本组件内，不改 dsh）：
- 每轮成功后把 user/assistant 正文追加到本地 transcript
  （$DSH_HOME/dsh-serve/transcripts/<sid>.jsonl）；
- session_id → 当前别名 映射存 session-map.json；
- 撞上 id collision 时（重启后首次续聊）：派生新别名 `<sid>--r<N>`，
  把 transcript 历史作为前言拼进 prompt 重发——会话连续性在薄层补齐。
"""
import json
import re
import threading
from pathlib import Path

_LOCK = threading.Lock()
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


def _safe(sid: str) -> str:
    return _UNSAFE.sub("_", sid)[:80]


class SessionStore:
    def __init__(self, dsh_home: str) -> None:
        self.root = Path(dsh_home) / "dsh-serve"
        self.transcripts = self.root / "transcripts"
        self.transcripts.mkdir(parents=True, exist_ok=True)
        self._map_path = self.root / "session-map.json"

    # --- 别名映射 ---
    def _load_map(self) -> dict:
        if self._map_path.exists():
            try:
                return json.loads(self._map_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                return {}
        return {}

    def _save_map(self, m: dict) -> None:
        tmp = self._map_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._map_path)

    def resolve_alias(self, sid: str) -> str:
        with _LOCK:
            return self._load_map().get(sid, {}).get("alias", sid)

    def fork(self, sid: str) -> str:
        """派生新一代别名并持久化（碰撞恢复时调用）。"""
        with _LOCK:
            m = self._load_map()
            gen = int(m.get(sid, {}).get("gen", 0)) + 1
            alias = f"{sid}--r{gen}"
            m[sid] = {"alias": alias, "gen": gen}
            self._save_map(m)
            return alias

    # --- transcript ---
    def append_turn(self, sid: str, user: str, assistant: str) -> None:
        path = self.transcripts / f"{_safe(sid)}.jsonl"
        with _LOCK, path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"role": "user", "content": user},
                               ensure_ascii=False) + "\n")
            f.write(json.dumps({"role": "assistant", "content": assistant},
                               ensure_ascii=False) + "\n")

    def history_text(self, sid: str, max_chars: int = 20000) -> str:
        path = self.transcripts / f"{_safe(sid)}.jsonl"
        if not path.exists():
            return ""
        lines = path.read_text(encoding="utf-8").splitlines()
        parts = []
        for line in lines:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            tag = "用户" if rec.get("role") == "user" else "助手"
            parts.append(f"{tag}：{rec.get('content', '')}")
        text = "\n".join(parts)
        return text[-max_chars:]  # 超长只留最近部分


def build_resume_prompt(history: str, prompt: str) -> str:
    if not history.strip():
        return prompt
    return (
        "【会话恢复】本会话在此前的服务进程中进行过，以下是既有对话历史，"
        "请在其基础上继续，不要重复已完成的工作：\n"
        f"---\n{history}\n---\n（历史结束）\n\n当前消息：\n{prompt}")
