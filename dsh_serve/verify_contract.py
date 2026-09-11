"""真实调用验证：缩小版 agent_production 长任务，验证主路由完整返回契约 JSON。"""
import io
import json
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import httpx  # noqa: E402

PROMPT = """你是「图文生产平台」的创作 Agent（缩小联调版）。

【任务】Query：新手第一台咖啡机怎么选；task_id=dsh-verify-001
【步骤】
1. 调用一次 mcp__qvp__web_search（query 自拟，task_id 用上面的）取证；
2. 写 200 字以内正文草稿；
3. 不要调用生图/OCR 工具（联调省钱），images/ocr_texts/references 用空数组；
4. 最后一轮只输出如下契约 JSON（字段齐全、合法 JSON、不要 markdown 代码围栏）：
{
  "evidence":  [{"title": "来源标题", "url": "https://...", "summary": "关键事实摘要"}],
  "content_style": "判定的内容风格（解读·经验分享/测评实测/攻略教程/避坑指南/观点杂谈）",
  "image_style": "选定的图片整体视觉风格",
  "draft":     "正文全文（200 字以内）",
  "pages":     ["第1页图上文案", "第2页图上文案", "第3页图上文案", "第4页图上文案", "第5页图上文案", "第6页图上文案"],
  "references": [],
  "images":    [],
  "ocr_texts": [],
  "notes":     "创作过程备注"
}"""

t0 = time.time()
r = httpx.post("http://127.0.0.1:8901/v1/chat/completions",
               json={"messages": [{"role": "user", "content": PROMPT}],
                     "session_id": "dsh-verify-001", "stream": False},
               timeout=600)
elapsed = time.time() - t0
resp = r.json()
print(f"http={r.status_code} {elapsed:.1f}s model={resp.get('model')}")
text = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
print("usage:", resp.get("usage"))
print("len(text):", len(text))
# 契约校验：正文可解析为 JSON 且字段齐全
clean = text.strip().removeprefix("```json").removesuffix("```").strip()
try:
    doc = json.loads(clean)
    keys = set(doc)
    expect = {"evidence", "content_style", "image_style", "draft", "pages",
              "references", "images", "ocr_texts", "notes"}
    print("JSON_OK:", expect <= keys, "| missing:", expect - keys)
    print("pages:", len(doc.get("pages", [])),
          "| evidence:", len(doc.get("evidence", [])),
          "| draft_len:", len(doc.get("draft", "")))
    print("draft head:", doc.get("draft", "")[:80])
except json.JSONDecodeError as e:
    print("JSON_FAIL:", e)
    print("text tail:", text[-300:])
