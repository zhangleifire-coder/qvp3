"""并行生图批次切分单测。"""
from qvp_mcp.server import _batches


def test_batches_even_split():
    items = list(range(6))
    assert _batches(items, 2) == [[0, 1], [2, 3], [4, 5]]


def test_batches_uneven_tail():
    items = list(range(7))
    assert _batches(items, 3) == [[0, 1, 2], [3, 4, 5], [6]]


def test_batches_serial_when_one():
    items = list(range(3))
    assert _batches(items, 1) == [[0], [1], [2]]


def test_batches_overflow_batch_size_clamped():
    assert _batches([1, 2], 10) == [[1, 2]]
    assert _batches([1, 2], 0) == [[1], [2]]  # 非法值钳到 1 = 串行
