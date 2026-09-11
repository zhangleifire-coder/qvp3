"""构造三个微任务的请求 JSON（UTF-8 安全）。"""
import json
from pathlib import Path

HERE = Path(__file__).parent

DRAFT_600 = (
    "新手买第一台咖啡机，别急着看颜值和网红款，先想清楚三件事。"
    "第一，你每天喝几杯。每天一两杯奶咖，半自动入门机足够；"
    "一天三杯以上或者全家都喝，直接上全自动，省下的时间比差价值钱。"
    "第二，你想不想动手。半自动要自己磨豆、压粉、控制萃取，"
    "乐趣在折腾，学习曲线也真实存在；全自动按键出杯，"
    "风味上限略低但稳定性高，失败率几乎为零。"
    "第三，预算怎么分。三千以内别碰半自动套装，"
    "磨豆机就要占掉一半预算，不如买台胶囊机过渡；"
    "五千到八千是半自动甜点区，58mm 标准手柄配件多、好升级；"
    "一万以上优先考虑全自动的锅炉数和自清洁能力。"
    "萃取参数记住三个数：18 克粉、36 克液、25 到 30 秒，"
    "偏离太多就调磨度而不是调粉量。"
    "最后提醒两个坑：一是别买玩具机，泵压虚标、温控漂移，"
    "做出来的东西会劝退你；二是预算里留出三百块买新鲜豆子，"
    "机器决定下限，豆子才决定上限。"
    "先想清楚使用场景，再按预算对号入座，第一台机器就不会买错。"
)

tasks = {
    "mt-a.json": {
        "messages": [{"role": "user", "content": (
            "只调用一次 mcp__qvp__page_split 工具（draft=下面的正文，"
            "task_id=mt-a-001），不要调用任何其他工具。然后报告：返回的 "
            "pages 数组有几条、source 字段值、balance_issue 是否为空。\n\n"
            f"正文如下：\n{DRAFT_600}")}],
        "session_id": "mt-a-001", "stream": False},
    "mt-b.json": {
        "messages": [{"role": "user", "content": (
            "只调用一次 mcp__qvp__rule_check 工具（draft=下面的文案，"
            "task_id=mt-b-001），不要调用任何其他工具。然后报告："
            "all_passed 的值、rule_results 里未通过的 rule_name 和 details 摘要。\n\n"
            "文案如下：\n这是最便宜的咖啡机，全网第一性价比，绝对是新手最佳选择。"
            "用它做咖啡百分百成功，完全不会失败。" * 6)}],
        "session_id": "mt-b-001", "stream": False},
    "mt-c.json": {
        "messages": [{"role": "user", "content": (
            "只调用一次 mcp__qvp__draft_write 工具（query=\"便携榨汁杯怎么选\"，"
            "task_id=mt-c-001），不要调用任何其他工具。然后报告：返回正文的"
            "字数、content_style 字段值、正文开头两句话。")}],
        "session_id": "mt-c-001", "stream": False},
}

for name, body in tasks.items():
    (HERE / name).write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    print("wrote", name)
