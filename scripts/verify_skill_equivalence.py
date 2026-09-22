"""提示词资产（skills/）重构等价性验证。

用法（顺序不可颠倒）：
1. 重构前：PYTHONUTF8=1 python scripts/verify_skill_equivalence.py --baseline
   → 把当前代码的全部取词结果快照到 scripts/.skill_equiv_baseline.json
2. 重构后：PYTHONUTF8=1 python scripts/verify_skill_equivalence.py --verify
   → 重新生成同一快照并与 baseline 逐键对比，零差异退出码 0；
   另校验 skills/page-split/contract.txt 解析出的字数契约常量
   （PAGE_MIN_CHARS=80 / PAGE_MAX_CHARS=130 / PAGE_MAX_DIFF=40 /
   INFO_POINTS_MIN=1 / INFO_POINTS_MAX=2）与 baseline 记录一致。

覆盖范围：prompt_versions 全常量与全 stage×mode 取词/渲染矩阵、
text_check 起草三式、visual_writer、page_subject、
combo.analyze_prompt、agent_production 指令组装。
（ai_review 快照块已随孤儿模块删除——v0.1.4 P0.3，其职责由
visual_check.comprehensive_page_check 承接）
"""
import argparse
import difflib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASELINE_PATH = Path(__file__).resolve().parent / ".skill_equiv_baseline.json"

_MODES = ["general", "single", "compare"]
# 字数契约（baseline 期记录为字面量；verify 期与 skill_loader 常量对撞）
_EXPECTED_CONTRACT = {"PAGE_MIN_CHARS": 80, "PAGE_MAX_CHARS": 130,
                      "PAGE_MAX_DIFF": 40, "INFO_POINTS_MIN": 1,
                      "INFO_POINTS_MAX": 2}


def collect() -> dict:
    """从当前代码收集全部提示词取词/渲染结果（纯函数，不触 DB/网络）。"""
    snap: dict = {}

    from src.gateway import prompt_versions as pv
    snap["pv.constants"] = {
        "DRAFT_PROMPTS": pv.DRAFT_PROMPTS,
        "_DRAFT_SHARED": pv._DRAFT_SHARED,
        "DRAFT_POLISH_PROMPT": pv.DRAFT_POLISH_PROMPT,
        "PAGES_PROMPT": pv.PAGES_PROMPT,
        "PAGE_REGEN_PROMPT": pv.PAGE_REGEN_PROMPT,
        "IMAGE_PROMPTS": pv.IMAGE_PROMPTS,
        "IMAGE_PROMPTS_EN": pv.IMAGE_PROMPTS_EN,
        "_SHARED_IMAGE_STYLE": pv._SHARED_IMAGE_STYLE,
        "_SUBJECT_ANCHOR": pv._SUBJECT_ANCHOR,
        "_IMAGE_CONSTRAINTS_EN": pv._IMAGE_CONSTRAINTS_EN,
        "_PAGE_LAYOUTS": pv._PAGE_LAYOUTS,
        "_PAGE_LAYOUTS_EN": pv._PAGE_LAYOUTS_EN,
        "PROMPT_VERSIONS": pv.PROMPT_VERSIONS,
        "STAGE_CATALOG": pv.STAGE_CATALOG,
    }

    rendered: dict = {}
    for st in pv.STAGE_CATALOG:
        for m in st["modes"]:
            rendered[f"default_prompt/{st['stage']}/{m}"] = pv.default_prompt(
                st["stage"], m)
    for m in _MODES + ["nope"]:
        rendered[f"get_draft_prompt/{m}"] = pv.get_draft_prompt(m)
    rendered["get_pages_prompt"] = pv.get_pages_prompt("样例正文AAA")
    for name in ("draft", "page_split", "evidence"):
        rendered[f"get_prompt/{name}"] = pv.get_prompt(name)
        rendered[f"get_prompt/{name}/v1"] = pv.get_prompt(name, "v1")
    # draft_gen 默认模板 + 人设共享段（nodes/agent_production 注入路径）
    for m in _MODES:
        rendered[f"draft_with_shared/{m}"] = (
            pv.default_prompt("draft_gen", m) + "\n" + pv._DRAFT_SHARED)
    rendered["page_regen_filled"] = pv.PAGE_REGEN_PROMPT.format(
        page_index=2, feedback="意见样例", body="正文样例", old_copy="旧文案样例")
    # get_image_prompt 全矩阵：中文回退 / 英文视觉 / 布局轮换 / 主体锚定替换
    for m in _MODES:
        for pi in (None, 1, 2, 6, 7):
            rendered[f"image/cn/{m}/{pi}"] = pv.get_image_prompt(
                m, "本页文案样例", pi)
        rendered[f"image/cn_style/{m}"] = pv.get_image_prompt(
            m, "本页文案样例", 3, style_block="风格段样例")
        rendered[f"image/cn_subject/{m}"] = pv.get_image_prompt(
            m, "本页文案样例", 2, page_subject="一杯冰牛奶")
        rendered[f"image/cn_tpl/{m}"] = pv.get_image_prompt(
            m, "本页文案样例", 1, template="自定义模板{page_body}")
        rendered[f"image/en/{m}"] = pv.get_image_prompt(
            m, "中文文案样例", 4, visual="EN visual direction sample",
            style_en="EN unified style sample")
        rendered[f"image/en_noidx/{m}"] = pv.get_image_prompt(
            m, "中文文案样例", None, visual="EN visual", style_en=None)
    snap["pv.rendered"] = rendered

    from src.pipeline import text_check as tc
    snap["text_check"] = {
        "_TEXT_CHECK_PROMPT": tc._TEXT_CHECK_PROMPT,
        "_TEXT_REWRITE_PROMPT": tc._TEXT_REWRITE_PROMPT,
        "_TEXT_FEEDBACK_PROMPT": tc._TEXT_FEEDBACK_PROMPT,
        "_STRICT_JSON_SUFFIX": tc._STRICT_JSON_SUFFIX,
        "_MODE_DESC": tc._MODE_DESC,
        "check_filled": tc._TEXT_CHECK_PROMPT.format(query="Q样例", mode_desc="M样例"),
        "rewrite_filled": tc._TEXT_REWRITE_PROMPT.format(
            query="Q样例", mode_desc="M样例", user_body="U样例"),
        "feedback_filled": tc._TEXT_FEEDBACK_PROMPT.format(
            marks_block="MB样例", query="Q样例", body="B样例", pages="P样例",
            image_prompts="IP样例", manual_section="MS样例"),
        "draft_new_full": (tc._TEXT_CHECK_PROMPT.format(query="Q样例", mode_desc="M样例")
                           + "\n" + pv._DRAFT_SHARED),
    }

    from src.services import visual_writer as vw
    snap["visual_writer"] = {
        "_VISUAL_PROMPT": vw._VISUAL_PROMPT,
        "built_message": vw._build_message("风格名样例", "风格描述样例",
                                           ["页文"] * 6, ["笔记样例"]),
    }

    from src.services import page_subject as ps
    snap["page_subject"] = {
        "_SUBJECT_PROMPT": ps._SUBJECT_PROMPT,
        "filled": ps._SUBJECT_PROMPT.replace("{pages}", "第1页：文样例"),
    }

    from src.services import combo
    snap["combo"] = {
        "analyze_default": combo.analyze_prompt(" 样例提问 "),
        "analyze_n5": combo.analyze_prompt("样例提问", 5),
    }

    from src.pipeline import agent_production as ap
    snap["agent_production"] = {
        "_AGENT_INSTRUCTIONS": ap._AGENT_INSTRUCTIONS,
        "_OUTPUT_CONTRACT": ap._OUTPUT_CONTRACT,
        "_MODE_DESC": ap._MODE_DESC,
        "composed_full": ap._compose_message(
            "tid1", "Q样例", "compare", "DRAFT_TPL", "PAGES_TPL", "IMG_TPL",
            ["意见样例"], combo_section="组合段样例",
            fixed_style="测评实测", style_kb_text="KB样例"),
        "composed_min": ap._compose_message(
            "tid2", "Q样例2", "general", "D", "P", "I", []),
    }

    snap["expected_contract"] = dict(_EXPECTED_CONTRACT)
    return snap


def _flatten(obj, prefix="") -> dict:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            out.update(_flatten(v, f"{prefix}{k}."))
        return out
    if isinstance(obj, list):
        out = {}
        for i, v in enumerate(obj):
            out.update(_flatten(v, f"{prefix}{i}."))
        return out
    return {prefix.rstrip("."): obj}


def main() -> int:
    ap_ = argparse.ArgumentParser()
    g = ap_.add_mutually_exclusive_group(required=True)
    g.add_argument("--baseline", action="store_true", help="生成重构前快照")
    g.add_argument("--verify", action="store_true", help="与快照对比")
    args = ap_.parse_args()

    snap = collect()

    if args.baseline:
        BASELINE_PATH.write_text(
            json.dumps(snap, ensure_ascii=False, indent=1, sort_keys=True),
            encoding="utf-8")
        flat = _flatten(snap)
        print(f"baseline 已写入 {BASELINE_PATH}（{len(flat)} 个叶子值）")
        return 0

    if not BASELINE_PATH.exists():
        print(f"缺少 baseline：{BASELINE_PATH}，请先以 --baseline 运行", file=sys.stderr)
        return 2
    base = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))

    diffs = []
    fb, fs = _flatten(base), _flatten(snap)
    for key in sorted(set(fb) | set(fs)):
        if key not in fb:
            diffs.append(f"+ 新增键 {key}")
        elif key not in fs:
            diffs.append(f"- 缺失键 {key}")
        elif fb[key] != fs[key]:
            diffs.append(f"~ 内容不同 {key}")
    if diffs:
        print(f"发现 {len(diffs)} 处差异：")
        for d in diffs[:50]:
            print(" ", d)
        for key in sorted(set(fb) & set(fs)):
            if fb[key] != fs[key]:
                print(f"\n===== {key} =====")
                a = str(fb[key]).splitlines()
                b = str(fs[key]).splitlines()
                print("\n".join(list(difflib.unified_diff(a, b, "baseline",
                                                          "current"))[:40]))
        return 1

    # 契约常量单点校验（重构后 skill_loader 才存在）
    try:
        from src.gateway import skill_loader
    except ImportError:
        print("警告：src.gateway.skill_loader 不存在（尚未重构？），跳过契约常量校验")
        return 1
    got = {k: getattr(skill_loader, k, None) for k in _EXPECTED_CONTRACT}
    if got != base["expected_contract"]:
        print(f"契约常量不符：期望 {base['expected_contract']}，实际 {got}")
        return 1

    print(f"等价性验证通过：{len(fs)} 个叶子值零差异；"
          f"契约常量 {got} 与 baseline 一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
