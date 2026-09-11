"""OCR 对撞（图文一致性）：从文案/OCR 文本提取关键字段（数字/型号）逐一比对。

2026-09-11（错别字/异体字 100% 审核标准）：提取前先按同一口径归一化
（繁简/异体字映射 + 全半角统一 + 去空白，见 src/quality/text_norm.py）——
全角数字「３」与半角「3」、繁体「臺」与简体「台」在两侧同向归一后再比对，
消除字符形态差异造成的假阴性/假阳性。
"""
import re

from src.quality.text_norm import normalize_text


def extract_key_fields(page_body: str) -> dict:
    fields = {}
    body = normalize_text(page_body)
    nums = re.findall(r"\d{4}年|\d+\.?\d*[%公里元㎡个台件]|型号[\w-]+", body)
    fields["numbers"] = list(set(nums))
    return fields


def compare_field(expected: dict, actual: dict) -> list:
    mismatches = []
    for k, v in expected.items():
        if k not in actual:
            mismatches.append({"field_name": k, "expected": str(v), "actual": "missing", "matched": False})
            continue
        if isinstance(v, list):
            missing = set(v) - set(actual[k])
            if missing:
                mismatches.append({"field_name": k, "expected": str(v), "actual": str(actual[k]), "matched": False})
            else:
                mismatches.append({"field_name": k, "expected": str(v), "actual": str(actual[k]), "matched": True})
    return mismatches
