import asyncio
import os
from pathlib import Path
from unittest.mock import patch, AsyncMock

import asyncpg
import pytest

# ============ 测试库隔离 ============
# 必须在导入 app 之前设置，让 settings / SessionLocal 指向独立测试库，
# 避免集成测试向开发/生产库（qvp）写入测试数据。
TEST_DB = "qvp_test"
TEST_DB_URL = f"postgresql+asyncpg://qvp:qvp@localhost:5432/{TEST_DB}"
os.environ["DATABASE_URL"] = TEST_DB_URL
os.environ["IMAGE_GEN_DELAY_SECONDS"] = "0"  # 测试不 sleep，加速
os.environ["MOCK_IMAGE_GEN"] = "false"       # 测试默认关 mock，路由逻辑走真函数

_ADMIN_DSN = "postgresql://qvp:qvp@localhost:5432/postgres"
_TEST_DSN = f"postgresql://qvp:qvp@localhost:5432/{TEST_DB}"

_ALL_TABLES = [
    "organizations", "users", "tasks", "entity_snapshots", "claims", "evidence",
    "drafts", "page_copies", "assets", "ocr_results", "rule_results",
    "cross_checks", "risk_classifications", "review_sessions", "review_actions",
    "issues", "batches", "batch_members", "approvals", "publish_snapshots",
    "node_events", "prompt_templates", "activity_logs", "reject_marks",
]

_MIGRATION_DIR = Path(__file__).resolve().parent.parent / "migrations"


def _run(coro):
    return asyncio.run(coro)


async def _ensure_test_db():
    admin = await asyncpg.connect(_ADMIN_DSN)
    try:
        exists = await admin.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", TEST_DB)
        if not exists:
            await admin.execute(f'CREATE DATABASE "{TEST_DB}" OWNER qvp')
    finally:
        await admin.close()

    conn = await asyncpg.connect(_TEST_DSN)
    try:
        # 按文件名顺序应用全部迁移（均为幂等写法）；排除 macOS AppleDouble（._*）
        for m in sorted(_MIGRATION_DIR.glob("*.sql")):
            if m.name.startswith("."):
                continue
            await conn.execute(m.read_text())
    finally:
        await conn.close()


async def _truncate_all():
    conn = await asyncpg.connect(_TEST_DSN)
    try:
        await conn.execute(f"TRUNCATE TABLE {', '.join(_ALL_TABLES)} CASCADE")
    finally:
        await conn.close()


@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    """会话级：创建测试库并建表（幂等）。"""
    _run(_ensure_test_db())
    yield


@pytest.fixture(autouse=True)
def clean_db(setup_test_db):
    """每个测试前清空测试库，保证用例互不干扰。"""
    _run(_truncate_all())
    yield


# ============ 外部调用 mock ============
FAKE_IMAGE = {"hash": "abc123", "image_url": "https://example.com/i.png", "model_version": "gpt-image-1.5"}
FAKE_SEARCH = [{"title": "来源", "url": "https://example.com/src", "summary": "成立于1990年"}]
FAKE_VERIFY = ("成立于1990年", 0.001)
FAKE_IMAGES = [{"title": "实景图", "image_url": "https://example.com/real.png", "source": "bing", "engine": "bing"}]
FAKE_OCR = {"raw_text": "成立于1990年 测试文字", "cost_cny": 0.001, "model": "qwen-vl-ocr"}


@pytest.fixture(autouse=True)
def mock_external_calls():
    # 测试环境不真实调用生图/联网搜索/搜图/OCR API
    with patch("src.gateway.image_gen.generate_image", new=AsyncMock(return_value=FAKE_IMAGE)), \
         patch("src.gateway.web_search.web_search", return_value=FAKE_SEARCH), \
         patch("src.gateway.web_search.deepseek_verify", return_value=FAKE_VERIFY), \
         patch("src.gateway.image_search.search_image", return_value=FAKE_IMAGES), \
         patch("src.gateway.ocr.ocr_image", new=AsyncMock(return_value=FAKE_OCR)):
        yield
