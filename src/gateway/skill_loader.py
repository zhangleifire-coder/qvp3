"""skills/ 提示词资产加载器：全部生成类提示词的单一事实来源（2026-09-03 阶段2重构）。

目录约定（code/skills/<name>/）：
- SKILL.md：frontmatter（--- 包裹的 name/description/notes 单行键值）+ 正文；
  单模板包的正文即模板全文（读取时 strip 首尾空白——入库模板均无首尾空白，
  与代码内联原值字节一致）；多片段包的正文为片段清单说明，不作模板用。
- 片段文件：<name>/<frag>.txt（可带一层子目录，如 prompts/general.txt），
  读取原样返回、不做任何裁剪。
- 列表型片段（如 6 条排版轮换）：单文件内以单独一行 --- 分隔，用
  fragment_list() 读取拆分。

首次读取落内存缓存（模块级 dict），运行期不重读文件；测试可 reload() 清缓存。
"""
from pathlib import Path

SKILLS_ROOT = Path(__file__).resolve().parents[2] / "skills"
LIST_SEP = "\n---\n"

_CACHE: dict = {}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_skill(name: str) -> dict:
    if name not in _CACHE:
        skill_dir = SKILLS_ROOT / name
        meta, body = _parse_frontmatter(_read(skill_dir / "SKILL.md"))
        _CACHE[name] = {"dir": skill_dir, "meta": meta, "body": body,
                        "fragments": {}}
    return _CACHE[name]


def _parse_frontmatter(text: str) -> tuple:
    """解析 --- 包裹的单行键值 frontmatter；返回 (meta, body)。"""
    meta: dict = {}
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].strip("\n").splitlines():
                key, _, value = line.partition(":")
                if value.strip():
                    meta[key.strip()] = value.strip()
            body = text[end + 4:].strip()
    return meta, body


def reload() -> None:
    """清空缓存（测试或运行期更新 skills/ 后调用）。"""
    _CACHE.clear()


def skill_meta(name: str) -> dict:
    """SKILL.md frontmatter（name/description/notes）。"""
    return dict(_load_skill(name)["meta"])


def skill_body(name: str) -> str:
    """单模板包正文模板（SKILL.md frontmatter 之后的内容，strip 首尾空白）。"""
    return _load_skill(name)["body"]


def fragment(name: str, frag: str, default=None) -> str:
    """读片段文件 <name>/<frag>.txt，原样返回；缺失且有 default 时返回 default。"""
    skill = _load_skill(name)
    if frag not in skill["fragments"]:
        path = skill["dir"] / f"{frag}.txt"
        if not path.exists():
            if default is not None:
                return default
            raise FileNotFoundError(path)
        skill["fragments"][frag] = _read(path)
    return skill["fragments"][frag]


def fragment_list(name: str, frag: str) -> list:
    """列表型片段：单文件内以单独一行 --- 分隔，拆分返回字符串列表。"""
    return fragment(name, frag).split(LIST_SEP)


def mode_fragment(name: str, mode: str, default_mode: str = "general") -> str:
    """按 mode 读 prompts/<mode>.txt；未知 mode 回退 default_mode。"""
    skill = _load_skill(name)
    path = skill["dir"] / "prompts" / f"{mode}.txt"
    if not path.exists():
        mode = default_mode
    return fragment(name, f"prompts/{mode}")


def _load_contract() -> dict:
    """page-split/contract.txt → {KEY: int}（# 开头为注释）。"""
    out: dict = {}
    for line in _read(SKILLS_ROOT / "page-split" / "contract.txt").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = int(value.strip())
    return out


_CONTRACT = _load_contract()

# 图上文案字数契约（单点：skills/page-split/contract.txt）
PAGE_MIN_CHARS: int = _CONTRACT["PAGE_MIN_CHARS"]      # 每页字数下限（含小标题与标点）
PAGE_MAX_CHARS: int = _CONTRACT["PAGE_MAX_CHARS"]      # 每页字数上限
PAGE_MAX_DIFF: int = _CONTRACT["PAGE_MAX_DIFF"]        # 任意两页字数差上限
INFO_POINTS_MIN: int = _CONTRACT["INFO_POINTS_MIN"]    # 每页具体信息点下限
INFO_POINTS_MAX: int = _CONTRACT["INFO_POINTS_MAX"]    # 每页具体信息点上限
