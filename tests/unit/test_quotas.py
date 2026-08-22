"""MCP 工具配额单测：计数、超限报错、任务隔离、释放。"""
import pytest

from qvp_mcp.quotas import QuotaExceededError, quotas


def test_consume_within_limit():
    quotas.release("t1")
    for _ in range(3):
        quotas.check_and_consume("t1", "web_search")
    with pytest.raises(QuotaExceededError, match="配额"):
        quotas.check_and_consume("t1", "web_search")


def test_tasks_isolated():
    quotas.release("t2a"); quotas.release("t2b")
    for _ in range(3):
        quotas.check_and_consume("t2a", "web_search")
    # t2b 不受 t2a 影响
    quotas.check_and_consume("t2b", "web_search")


def test_release_resets():
    quotas.release("t3")
    with pytest.raises(QuotaExceededError):
        for _ in range(9):
            quotas.check_and_consume("t3", "image")
    quotas.release("t3")
    quotas.check_and_consume("t3", "image")  # 释放后可重新计数


def test_batch_consume_counts_n():
    quotas.release("t4")
    quotas.check_and_consume("t4", "image", n=6)
    # 默认上限 8：再批量 3 张应超限
    with pytest.raises(QuotaExceededError):
        quotas.check_and_consume("t4", "image", n=3)
