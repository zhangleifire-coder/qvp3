"""reasoningEffort 调优试验客户端：同一创作任务打 dsh_serve，对比 usage/时延/契约达标。

用法：
  python dsh_serve/re_effort_test.py --base http://127.0.0.1:8903 --effort high --tag A1
输出 JSON 一行到 stdout：elapsed/usage/text_len/page_chars/contract_ok。
"""
import argparse
import json
import sys
import time
import urllib.request

# 与生产 Agent 契约一致的收敛任务（oss-01 基准题）：正文 + 6 页图上文案，
# 字数契约 80-130/页、六页差≤40、每页 1-2 个信息点。
PROMPT = """你是小红书科普图文创作助手。围绕选题「洗衣机能效等级怎么看，一级和三级差多少电」产出契约 JSON：

{"draft": "正文 400-600 字，口语化、有真人感、信息密度高",
 "pages": [{"page": 1, "title": "小标题", "body": "图上文案"}, ... 共 6 页]}

图上文案硬契约（必须逐条满足）：
- 每页 body 含小标题在内 80-130 字（含标点）；
- 任意两页字数差 ≤40；
- 每页 1-2 个具体信息点（数字/价格/参数/步骤）；
- 只输出 JSON，不要任何解释。"""


def run(base: str, effort: str, tag: str) -> dict:
    payload = {
        "model": "dsh",
        "messages": [{"role": "user", "content": PROMPT}],
        "stream": True,
        "session_id": f"re-effort-{tag}",
        "max_tokens": 32768,
    }
    req = urllib.request.Request(
        f"{base}/v1/chat/completions", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.time()
    text_parts, usage, finished = [], None, None
    with urllib.request.urlopen(req, timeout=3000) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "ignore").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            if chunk.get("usage"):
                usage = chunk["usage"]
            for ch in chunk.get("choices", []):
                delta = ch.get("delta") or {}
                if delta.get("content"):
                    text_parts.append(delta["content"])
                if ch.get("finish_reason"):
                    finished = ch["finish_reason"]
    elapsed = time.time() - t0
    text = "".join(text_parts)

    # 契约校验：尽力解析 JSON 的 pages body 字数
    page_chars, contract_ok = [], None
    m = text.find("{")
    if m >= 0:
        try:
            doc = json.loads(text[m:text.rfind("}") + 1])
            pages = doc.get("pages") or []
            page_chars = [len((p.get("title", "") + p.get("body", "")))
                          for p in pages]
            ok = all(80 <= c <= 130 for c in page_chars) if len(page_chars) == 6 else False
            ok = ok and (max(page_chars) - min(page_chars) <= 40)
            contract_ok = ok
        except Exception:
            page_chars = []
    return {
        "tag": tag, "effort": effort, "elapsed_s": round(elapsed, 1),
        "usage": usage, "finish_reason": finished,
        "text_len": len(text), "page_chars": page_chars,
        "contract_ok": contract_ok,
        "reasoning_share": (
            round((usage.get("reasoning_tokens") or 0) / max(usage.get("output_tokens") or 1, 1), 3)
            if usage else None),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8903")
    ap.add_argument("--effort", default="high")
    ap.add_argument("--tag", default="A1")
    args = ap.parse_args()
    print(json.dumps(run(args.base, args.effort, args.tag), ensure_ascii=False))
