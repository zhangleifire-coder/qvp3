"""qvp_mcp v2 能力工具单测：11 个新工具的返回结构 / 配额拒绝 / 成本回调。

mock 手法与 tests/unit 现有用例一致：
- 配额与成本回调在 qvp_mcp.tools_capabilities 模块边界 mock（不发起 HTTP）；
- LLM 调用按包装路径选 mock 点：走 nodes 共享函数的（draft_write/page_split/
  page_regen）patch src.pipeline.nodes.call_with_failover（同集成测试），
  模块内直调的（text_draft/prompt_analyze/page_subject）patch
  qvp_mcp.tools_capabilities.call_with_failover；服务层包装的
  （visual_write/visual_check）patch 模块内绑定的服务函数名。
"""
import json

import pytest
from unittest.mock import patch, AsyncMock

from qvp_mcp import tools_capabilities as tc

TID = "task-mcp-unit"

FAKE_LLM = {"text": "字" * 500, "model_version": "deepseek/deepseek-v4-pro",
            "cost_cny": 0.01, "degraded": False}


@pytest.fixture(autouse=True)
def quota_cost_mock():
    """默认放行配额、拦截成本回调；用例可改 mock 返回值/断言调用。"""
    with patch.object(tc, "check_and_consume",
                      new=AsyncMock(return_value=None)) as q, \
         patch.object(tc, "report_usage", new=AsyncMock()) as r:
        yield q, r


def _deny(q):
    q.return_value = {"allowed": False, "limit": 5, "used": 5}


# ── 工具目录冒烟：11 个新工具全部注册到 mcp 实例 ──

async def test_tool_catalog_registered():
    tools = {t.name for t in await tc.mcp.list_tools()}
    for name in ("draft_write", "page_split", "page_regen", "visual_write",
                 "text_draft", "prompt_analyze", "page_subject",
                 "rule_check", "cross_check", "risk_classify", "visual_check"):
        assert name in tools, f"工具未注册: {name}"


# ── 创作类 ──

async def test_draft_write_ok(quota_cost_mock):
    q, r = quota_cost_mock
    with patch("src.pipeline.nodes.call_with_failover", return_value=FAKE_LLM):
        out = await tc.draft_write(query="冰牛奶怎么选", task_id=TID)
    assert out["ok"] is True and out["error"] is None
    assert out["data"]["text"] == FAKE_LLM["text"]
    assert out["data"]["prompt_version"].startswith("draft_general_v1")
    r.assert_awaited_once()
    assert r.await_args.args[0] == TID and r.await_args.args[1] == "draft_write"
    # FAKE_LLM 正文 500 字 ≥200 → 触发校稿润色二段式，成本 = 两次调用之和
    assert r.await_args.args[2] == FAKE_LLM["cost_cny"] * 2


async def test_draft_write_injects_feedback(quota_cost_mock):
    """驳回意见注入提示词（与流水线驳回重生成同口径）。"""
    captured = []

    async def fake_llm(prompt, *a, **kw):
        captured.append(prompt)
        return {**FAKE_LLM, "text": "短"}   # <200 字，跳过润色二段

    with patch("src.pipeline.nodes.call_with_failover", side_effect=fake_llm):
        out = await tc.draft_write(query="选题", task_id=TID,
                                   feedback=["标题夸大", "缺少数据来源"])
    assert out["ok"] is True
    assert "审核驳回反馈" in captured[0]
    assert "1. 标题夸大" in captured[0] and "2. 缺少数据来源" in captured[0]
    assert out["data"]["prompt_version"] == "draft_general_v1_regen1"


async def test_page_split_ok(quota_cost_mock):
    _, r = quota_cost_mock
    pages = [f"第{i}页小标题。" + "字" * 95 for i in range(1, 7)]
    fake = {**FAKE_LLM, "text": json.dumps(pages, ensure_ascii=False)}
    with patch("src.pipeline.nodes.call_with_failover", return_value=fake):
        out = await tc.page_split(draft="整篇正文" * 100, task_id=TID)
    assert out["ok"] is True
    assert out["data"]["source"] == "llm"
    assert len(out["data"]["pages"]) == 6
    assert out["data"]["balance_issue"] == ""     # 100 字上下、均衡 → 合格
    r.assert_awaited_once()


async def test_page_split_mechanical_fallback(quota_cost_mock):
    """LLM 输出不可解析 → 退回机械切割（source=mechanical，仍返回 6 页）。"""
    with patch("src.pipeline.nodes.call_with_failover",
               return_value={**FAKE_LLM, "text": "不是 JSON"}):
        out = await tc.page_split(draft="段落一。\n\n段落二。" * 30, task_id=TID)
    assert out["ok"] is True
    assert out["data"]["source"] == "mechanical"
    # 与节点同口径：LLM 已调用成功（仅解析失败），model_version 保留 LLM 型号
    assert out["data"]["model_version"] == FAKE_LLM["model_version"]
    assert len(out["data"]["pages"]) == 6


async def test_page_regen_ok(quota_cost_mock):
    _, r = quota_cost_mock
    fake = {**FAKE_LLM, "text": "重写后的新页文案"}
    with patch("src.pipeline.nodes.call_with_failover", return_value=fake) as m:
        out = await tc.page_regen(
            task_id=TID, page_index=2, old_copy="旧页文案", draft_body="整篇正文",
            feedback=["这页太水"],
            sibling_pages=[{"page_index": 1, "body": "字" * 100},
                           {"page_index": 3, "body": "字" * 110}])
    assert out["ok"] is True
    assert out["data"]["body"] == "重写后的新页文案"
    assert out["data"]["page_index"] == 2
    prompt = m.call_args.args[0]
    assert "旧页文案" in prompt and "1. 这页太水" in prompt
    assert "第1页100字" in prompt and "第3页110字" in prompt  # 均衡参照注入
    r.assert_awaited_once()


async def test_visual_write_ok(quota_cost_mock):
    _, r = quota_cost_mock
    visuals = {"style_en": "warm tone", "pages": [f"visual {i}" for i in range(6)]}
    with patch.object(tc, "write_page_visuals",
                      new=AsyncMock(return_value=visuals)):
        out = await tc.visual_write(pages=["页文案"] * 6, task_id=TID,
                                    style_name="自然写实暖调")
    assert out["ok"] is True
    assert out["data"]["visuals"] == visuals
    assert out["data"]["fallback_to_cn_skeleton"] is False
    r.assert_awaited_once()


async def test_visual_write_fallback_none(quota_cost_mock):
    """两级扩写均未产出可解析结果 → visuals=None（流水线回退语义，不算失败）。"""
    with patch.object(tc, "write_page_visuals", new=AsyncMock(return_value=None)):
        out = await tc.visual_write(pages=["页文案"] * 6, task_id=TID)
    assert out["ok"] is True
    assert out["data"]["visuals"] is None
    assert out["data"]["fallback_to_cn_skeleton"] is True


async def test_text_draft_ok(quota_cost_mock):
    _, r = quota_cost_mock
    payload = {"query_clean": {"issues": [], "suggested": ""},
               "body_draft": "字" * 500,
               "pages_draft": [f"P{i}" for i in range(1, 7)],
               "image_prompt_draft": [f"IP{i}" for i in range(1, 7)]}
    fake = {**FAKE_LLM, "text": json.dumps(payload, ensure_ascii=False)}
    with patch.object(tc, "call_with_failover",
                      new=AsyncMock(return_value=fake)):
        out = await tc.text_draft(query="冰牛奶怎么选", task_id=TID)
    assert out["ok"] is True
    d = out["data"]
    assert d["body_draft"] == "字" * 500
    assert d["pages_draft"] == [f"P{i}" for i in range(1, 7)]
    assert d["auto_ok"] is True and d["body_issues"] == []
    r.assert_awaited_once()


async def test_text_draft_parse_retry(quota_cost_mock):
    """首次输出非法 JSON → 加强约束重试一次；仍非法 → ok=False。"""
    good = json.dumps({"query_clean": {"issues": [], "suggested": ""},
                       "body_draft": "字" * 500, "pages_draft": [],
                       "image_prompt_draft": []}, ensure_ascii=False)
    with patch.object(tc, "call_with_failover", new=AsyncMock(
            side_effect=[{**FAKE_LLM, "text": "非法"},
                         {**FAKE_LLM, "text": good}])) as m:
        out = await tc.text_draft(query="选题测试", task_id=TID)
    assert out["ok"] is True and m.await_count == 2
    with patch.object(tc, "call_with_failover", new=AsyncMock(
            return_value={**FAKE_LLM, "text": "一直非法"})):
        out = await tc.text_draft(query="选题测试", task_id=TID)
    assert out["ok"] is False and "无法解析" in out["error"]


async def test_prompt_analyze_ok(quota_cost_mock):
    _, r = quota_cost_mock
    fake = {**FAKE_LLM, "text": "1. 冰牛奶怎么选\n2. 冰牛奶能放多久\n3. 冰牛奶热量高吗"}
    with patch.object(tc, "call_with_failover",
                      new=AsyncMock(return_value=fake)):
        out = await tc.prompt_analyze(query="冰牛奶的选购与保存", task_id=TID)
    assert out["ok"] is True
    assert out["data"]["questions"] == ["冰牛奶怎么选", "冰牛奶能放多久", "冰牛奶热量高吗"]
    r.assert_awaited_once()


async def test_prompt_analyze_short_query(quota_cost_mock):
    out = await tc.prompt_analyze(query="短", task_id=TID)
    assert out["ok"] is False and "太短" in out["error"]


async def test_page_subject_ok(quota_cost_mock):
    _, r = quota_cost_mock
    subjects = [f"主体{i}" for i in range(1, 7)]
    fake = {**FAKE_LLM, "text": json.dumps(subjects, ensure_ascii=False)}
    with patch.object(tc, "call_with_failover",
                      new=AsyncMock(return_value=fake)):
        out = await tc.page_subject(pages=["页文案"] * 6, task_id=TID)
    assert out["ok"] is True
    assert out["data"]["subjects"] == subjects
    assert out["data"]["cost_cny"] == FAKE_LLM["cost_cny"]
    r.assert_awaited_once()


# ── 校验类 ──

async def test_rule_check_ok(quota_cost_mock):
    _, r = quota_cost_mock
    out = await tc.rule_check(draft="字" * 500, task_id=TID)
    assert out["ok"] is True
    names = {x["rule_name"] for x in out["data"]["rule_results"]}
    assert {"word_count_400_700", "title_max_25",
            "no_absolute_words"} <= names
    assert out["data"]["all_passed"] is True
    # 含绝对化用语 → 判不合格
    out2 = await tc.rule_check(draft="这是最好的选择。" + "字" * 500, task_id=TID)
    assert out2["data"]["all_passed"] is False
    r.assert_awaited()


async def test_cross_check_ok(quota_cost_mock):
    _, r = quota_cost_mock
    out = await tc.cross_check(
        pages=["2024年销量100个", "普通文案"],
        ocr_texts=["2024年销量", ""], task_id=TID)
    assert out["ok"] is True
    mm = out["data"]["mismatches"]
    # 第 1 页：100个 缺失；第 2 页：OCR 识别失败
    assert any(m["field_name"] == "numbers" and m["matched"] is False
               for m in mm)
    assert any(m["field_name"] == "ocr" and m["actual"] == "识别失败"
               for m in mm)
    assert out["data"]["mismatch_count"] == len(mm)
    r.assert_awaited_once()


async def test_risk_classify_ok(quota_cost_mock):
    _, r = quota_cost_mock
    out = await tc.risk_classify(
        task_id=TID,
        rule_results=[{"passed": False, "rule_name": "word_count_400_700"}],
        cross_results=[{"matched": True}])
    assert out["ok"] is True
    assert out["data"]["level"] == "yellow"
    assert out["data"]["reasons"] == ["word_count_400_700"]
    out2 = await tc.risk_classify(task_id=TID, has_p0_issue=True)
    assert out2["data"]["level"] == "red"
    r.assert_awaited()


async def test_visual_check_ok(quota_cost_mock):
    _, r = quota_cost_mock
    with patch.object(tc, "check_subject_match", new=AsyncMock(
            return_value={"ok": True, "actual": "咖啡机"})):
        out = await tc.visual_check(image_url="/static/generated/x.png",
                                    page_copy="咖啡机怎么选", task_id=TID)
    assert out["ok"] is True
    assert out["data"]["verdict"] == {"ok": True, "actual": "咖啡机"}
    assert out["data"]["skipped"] is False
    # VL 未启用/不可用 → verdict=None（跳过语义，不算失败）
    with patch.object(tc, "check_subject_match", new=AsyncMock(return_value=None)):
        out2 = await tc.visual_check(image_url="/static/generated/x.png",
                                     page_copy="文案", task_id=TID)
    assert out2["ok"] is True and out2["data"]["skipped"] is True
    r.assert_awaited()


# ── 配额拒绝路径（11 工具参数化） ──

_TOOL_CALLS = {
    "draft_write": lambda: tc.draft_write(query="选题测试", task_id=TID),
    "page_split": lambda: tc.page_split(draft="正文" * 100, task_id=TID),
    "page_regen": lambda: tc.page_regen(task_id=TID, page_index=1,
                                        old_copy="旧", draft_body="正文"),
    "visual_write": lambda: tc.visual_write(pages=["页"] * 6, task_id=TID),
    "text_draft": lambda: tc.text_draft(query="选题测试", task_id=TID),
    "prompt_analyze": lambda: tc.prompt_analyze(query="选题测试", task_id=TID),
    "page_subject": lambda: tc.page_subject(pages=["页"] * 6, task_id=TID),
    "rule_check": lambda: tc.rule_check(draft="字" * 500, task_id=TID),
    "cross_check": lambda: tc.cross_check(pages=["a"], ocr_texts=["a"],
                                          task_id=TID),
    "risk_classify": lambda: tc.risk_classify(task_id=TID),
    "visual_check": lambda: tc.visual_check(image_url="/static/x.png",
                                            page_copy="文案", task_id=TID),
}


@pytest.mark.parametrize("tool_name", sorted(_TOOL_CALLS))
async def test_quota_denied(quota_cost_mock, tool_name):
    """配额拒绝（后端判定 allowed=False）→ ok=False + 配额说明，不记账。"""
    q, r = quota_cost_mock
    _deny(q)
    out = await _TOOL_CALLS[tool_name]()
    assert out["ok"] is False and out["data"] is None
    assert "配额" in out["error"]
    r.assert_not_awaited()


@pytest.mark.parametrize("tool_name", sorted(_TOOL_CALLS))
async def test_quota_exception_denied(quota_cost_mock, tool_name):
    """check_and_consume 抛 QuotaExceededError（真实拒绝路径）同样收敛为
    ok=False 结构化返回，不抛给 Agent。"""
    q, r = quota_cost_mock
    q.side_effect = tc.QuotaExceededError("任务 x 配额已用尽（上限 5，已用 5）")
    out = await _TOOL_CALLS[tool_name]()
    assert out["ok"] is False and "配额" in out["error"]
    r.assert_not_awaited()
