import re


def extract_key_fields(page_body: str) -> dict:
    fields = {}
    nums = re.findall(r"\d{4}年|\d+\.?\d*[%公里元㎡个台件]|型号[\w-]+", page_body)
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
