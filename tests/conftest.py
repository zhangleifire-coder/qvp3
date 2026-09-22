import asyncio
import os
from pathlib import Path
from unittest.mock import patch, AsyncMock

import asyncpg
import pytest

# ============ 测试库隔离 ============
# 必须在导入 app 之前设置，让 settings / SessionLocal 指向独立测试库，
# 避免集成测试向开发/生产库（qvp）写入测试数据。
# 测试库跟随 DATABASE_URL 的主机/端口（本地专用 PG 容器可能是 5433 等非默认端口）。
import re

TEST_DB = "qvp_test"
_DEV_URL = os.environ.get("DATABASE_URL") or "postgresql+asyncpg://qvp:qvp@localhost:5432/qvp"
_m = re.match(r"postgresql(?:\+asyncpg)?://[^@]*@([^:/]+):(\d+)/", _DEV_URL)
_HOST, _PORT = (_m.group(1), _m.group(2)) if _m else ("localhost", "5432")
TEST_DB_URL = f"postgresql+asyncpg://qvp:qvp@{_HOST}:{_PORT}/{TEST_DB}"
os.environ["DATABASE_URL"] = TEST_DB_URL
os.environ["IMAGE_GEN_DELAY_SECONDS"] = "0"  # 测试不 sleep，加速
os.environ["MOCK_IMAGE_GEN"] = "false"       # 测试默认关 mock，路由逻辑走真函数
os.environ["AGENT_PIPELINE_ENABLED"] = "false"  # 测试默认直连路径；Agent 路径有专测
# v0.1.4 P0.1 起 config 默认 variant=staged（生产事实标准），但测试进程默认
# 钉 monolith：test_agent_pipeline 专测 monolith 节点序列，staged 由
# test_agent_stages_pipeline 专测（其用例内 monkeypatch 显式设 staged）
os.environ["AGENT_PIPELINE_VARIANT"] = "monolith"
# 页数（v0.1.4 0922 改造）：生产默认 5（参考样式段落式）；测试进程钉 6——
# 既有守卫与集成夹具按 6 页契约编写（与 monolith 钉法同理）
os.environ["PAGE_COUNT"] = "6"
# VL 主体审核默认关：fetch_image_bytes 被 mock 后 _dedupe_and_validate 会走到
# check_subject_match，不能让它对 dashscope 发起真实 VL 调用（服务本身的解析
# 逻辑由 test_visual_check 在启用开关后专测）。
os.environ["VISUAL_SUBJECT_CHECK_ENABLED"] = "false"

_ADMIN_DSN = f"postgresql://qvp:qvp@{_HOST}:{_PORT}/postgres"
_TEST_DSN = f"postgresql://qvp:qvp@{_HOST}:{_PORT}/{TEST_DB}"

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
            await conn.execute(m.read_text(encoding="utf-8"))
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


def _fake_fetch_image_bytes():
    """mock 取图，语义对齐真实函数在断网环境的表现：
    - data: URI 按真实逻辑解 base64（utf8 SVG 等非 base64 负载同样容错解码）；
    - /static/... 读真实落盘文件（ctype 按扩展名映射，同真实函数）；
    - 远程 http(s) URL 一律抛错——测试不发起真实网络请求，且与 example.com
      恒 404 的历史行为一致（ref_collect 剔除下载失败候选、_dedupe_and_validate
      跳过去重/校验但不阻塞，均依赖该表现）。"""
    import base64 as _b64

    async def _fetch(url):
        if url.startswith("data:"):
            ctype = url.split(";")[0].split(":")[1]
            if ctype == "image/jpg":
                ctype = "image/jpeg"
            return _b64.b64decode(url.split(",", 1)[1]), ctype
        if url.startswith("/static/"):
            from pathlib import Path
            ctype = {".png": "image/png", ".jpg": "image/jpeg",
                     ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(
                         Path(url).suffix.lower(), "image/png")
            local = Path(__file__).resolve().parent.parent / url.lstrip("/")
            return local.read_bytes(), ctype
        raise RuntimeError(f"mock fetch_image_bytes: 测试不访问远程图片 {url[:60]}")
    return _fetch


@pytest.fixture(autouse=True)
def mock_external_calls():
    # 测试环境不真实调用生图/联网搜索/搜图/OCR API
    with patch("src.gateway.image_gen.generate_image", new=AsyncMock(return_value=FAKE_IMAGE)), \
         patch("src.gateway.web_search.web_search", return_value=FAKE_SEARCH), \
         patch("src.gateway.web_search.deepseek_verify", return_value=FAKE_VERIFY), \
         patch("src.gateway.image_search.search_image", return_value=FAKE_IMAGES), \
         patch("src.gateway.ocr.ocr_image", new=AsyncMock(return_value=FAKE_OCR)), \
         patch("src.gateway.ocr.fetch_image_bytes",
               new=_fake_fetch_image_bytes()), \
         patch("src.services.visual_writer.write_page_visuals",
               new=AsyncMock(return_value=None)), \
         patch("src.gateway.dsh_client.call_agent",
               new=AsyncMock(return_value={"text": "", "prompt_tokens": 0,
                                           "completion_tokens": 0})):
        yield
