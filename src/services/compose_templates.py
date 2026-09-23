"""页型模板库服务（2026-09-24 P1/P2）：spec 校验 / 预置模板加载与幂等同步 /
模板选择器（页角色轮换 + 题材打分 + 同角色去重）。

单一事实源纪律（沿用风格库 SSOT 教训）：预置模板只改
data/compose_templates.json；管理页改预置模板=改仓库文件（sync 幂等覆盖），
VL 提取/手工模板归个人库（source=vl_extracted/manual，sync 不碰）。
"""
import hashlib
import json
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "compose_templates.json"

_ARRANGEMENTS = ("single", "side_by_side", "grid_2x2", "triple_row",
                 "one_big_two_small", "none")
_FRAMES = ("none", "polaroid", "tape")
_TBG = ("theme_light", "theme_deep", "scrim")
_TITLE_STYLES = ("accent_bar", "center", "overlay_on_photo", "center_quote",
                 "")
_SECTION_STYLES = ("number_badge", "plain", "")
_EL_TYPES = ("title", "section_title", "paragraph", "points_col",
             "points_highlight", "capsule", "separator", "tip_box")
_DECOR = ("sticker_tl", "sticker_tr", "sticker_bl", "sticker_br", "banner",
          "vs_badge", "icon_row")
_ROLES = ("cover", "content", "ending")


def validate_spec(spec: dict) -> list[str]:
    """封闭词汇表校验：返回错误清单（空=合法）。"""
    errs: list[str] = []
    if not isinstance(spec, dict):
        return ["spec 必须是对象"]
    for k in ("template_id", "name", "page_role"):
        if not str(spec.get(k) or "").strip():
            errs.append(f"缺字段 {k}")
    role = spec.get("page_role")
    if role and role not in _ROLES:
        errs.append(f"page_role 非法：{role}（{_ROLES}）")
    photo = spec.get("photo") or {}
    arr = photo.get("arrangement", "single")
    if arr not in _ARRANGEMENTS:
        errs.append(f"photo.arrangement 非法：{arr}")
    if photo.get("frame", "none") not in _FRAMES:
        errs.append(f"photo.frame 非法：{photo.get('frame')}")
    tf = photo.get("top_fraction", 0.57)
    if arr != "none" and not (isinstance(tf, (int, float))
                              and 0.45 <= float(tf) <= 0.75):
        errs.append(f"photo.top_fraction 越界：{tf}（0.45-0.75）")
    tspec = spec.get("text") or {}
    if tspec.get("bg", "theme_light") not in _TBG:
        errs.append(f"text.bg 非法：{tspec.get('bg')}")
    if tspec.get("bg") == "theme_deep" and role != "ending":
        errs.append("theme_deep 仅限 ending 页（全套深底≤1 页约束）")
    els = tspec.get("elements")
    if not isinstance(els, list) or not els:
        errs.append("text.elements 必须为非空数组")
    else:
        for el in els:
            et = (el or {}).get("type", "")
            if et not in _EL_TYPES:
                errs.append(f"元素 type 非法：{et}")
            esty = (el or {}).get("style", "")
            if et == "title" and esty not in _TITLE_STYLES:
                errs.append(f"title.style 非法：{esty}")
            if et == "section_title" and esty not in _SECTION_STYLES:
                errs.append(f"section_title.style 非法：{esty}")
    for d in spec.get("decor") or []:
        if d not in _DECOR:
            errs.append(f"decor 非法：{d}")
    return errs


def load_seeds() -> list[dict]:
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    tpls = data.get("templates") or []
    bad = [t.get("template_id") for t in tpls if validate_spec(t)]
    if bad:
        raise ValueError(f"预置模板 spec 非法：{bad}")
    return tpls


async def sync_seeds() -> int:
    """预置模板幂等同步到 compose_templates 表（seed_* 按 template_id 覆盖；
    个人库 vl_extracted/manual 不碰）。返回同步条数。"""
    from sqlalchemy import text as _text
    from src.db.session import SessionLocal

    n = 0
    async with SessionLocal() as session:
        for t in load_seeds():
            await session.execute(_text("""
                INSERT INTO compose_templates
                  (template_id, name, spec, page_role, tags, source,
                   enabled, version)
                VALUES (:tid, :name, CAST(:spec AS jsonb), :role,
                        CAST(:tags AS text[]), 'seed_11', true, 1)
                ON CONFLICT (template_id) DO UPDATE SET
                  name = EXCLUDED.name, spec = EXCLUDED.spec,
                  page_role = EXCLUDED.page_role, tags = EXCLUDED.tags
            """), {"tid": t["template_id"], "name": t["name"],
                   "spec": json.dumps(t, ensure_ascii=False),
                   "role": t["page_role"],
                   "tags": t.get("tags") or ["通用"]})
            n += 1
        await session.commit()
    return n


async def list_templates(enabled_only: bool = False,
                         source: str | None = None) -> list[dict]:
    """从 DB 读模板（DB 不可用/空时回退种子文件）。"""
    from sqlalchemy import text as _text
    from src.db.session import SessionLocal
    q = "SELECT template_id, name, spec, page_role, tags, source, enabled" \
        " FROM compose_templates"
    conds, params = [], {}
    if enabled_only:
        conds.append("enabled")
    if source:
        conds.append("source = :src")
        params["src"] = source
    if conds:
        q += " WHERE " + " AND ".join(conds)
    q += " ORDER BY source, template_id"
    try:
        async with SessionLocal() as session:
            rows = (await session.execute(_text(q), params)).mappings().all()
        if rows:
            return [dict(r) for r in rows]
    except Exception:
        pass
    seeds = load_seeds()
    return [dict(t, enabled=True) for t in seeds
            if (not enabled_only) and (not source or source == "seed_11")]


def _score(tpl: dict, topic_tags: list[str]) -> int:
    tags = set(tpl.get("tags") or [])
    return sum(1 for t in topic_tags if t in tags)


async def template_select(task_id: str, page_index: int, total_pages: int,
                          topic_tags: list[str] | None = None) -> dict | None:
    """按页角色 + 题材打分 + 确定性轮换选模板。

    页角色：第 1 页 cover；其余页 content ∪ ending 池（0923 语料实证：
    150 张参考案例第 5 页 100% 为普通内容形态，无深底结尾页——故末页
    不强制 ending，ending 模板作为池内可选项轮换）。
    返回 spec dict；无可用模板返回 None（调用方回退 compose_page 旧路径）。
    """
    if page_index == 1:
        roles = ("cover",)
    elif page_index == total_pages:
        roles = ("content", "ending")
    else:
        roles = ("content",)
    raw = await list_templates(enabled_only=True)
    # 归一化：DB 行带独立 spec 键；种子文件回退行本身就是 spec
    pool = []
    for t in raw:
        if t.get("page_role") not in roles:
            continue
        spec = t.get("spec")
        if isinstance(spec, str):
            spec = json.loads(spec)
        if not isinstance(spec, dict):
            spec = t
        pool.append({"template_id": t.get("template_id", ""),
                     "page_role": t.get("page_role") or spec.get("page_role"),
                     "tags": t.get("tags") or spec.get("tags") or [],
                     "spec": spec})
    # 同结构去重（同签名保留首個，防 ending/content 双份同款）
    seen: set = set()
    cands = []
    for t in pool:
        sig = spec_signature(t["spec"])
        if sig not in seen:
            seen.add(sig)
            cands.append(t)
    if not cands:
        return None
    for t in cands:
        t["_score"] = _score(t, topic_tags or [])
    seed = int(hashlib.md5(
        f"{task_id}:{page_index}".encode()).hexdigest()[:8], 16)
    # 排序：题材分优先，同分按 hash 轮换打散
    cands.sort(key=lambda t: (-(t["_score"]), (seed + hash(t["template_id"]))
                              % 997))
    return cands[0]["spec"]


def spec_signature(spec: dict) -> str:
    """结构签名（去重聚类用）：排布+比例档+元素流+装饰。"""
    photo = spec.get("photo") or {}
    els = [(e.get("type"), e.get("style", "")) for e in
           (spec.get("text") or {}).get("elements") or []]
    tf = int(float(photo.get("top_fraction", 0.57)) / 0.05) * 0.05
    return "|".join([
        str(photo.get("arrangement", "single")), f"{tf:.2f}",
        str(photo.get("frame", "none")),
        str((spec.get("text") or {}).get("bg", "theme_light")),
        ";".join(f"{a}:{b}" for a, b in els),
        ",".join(sorted(spec.get("decor") or []))])
