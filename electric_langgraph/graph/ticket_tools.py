"""两票智能审核工具（工作票 / 操作票）。

**铁律（工程化保证，不依赖提示词）：**
- 本文件只输出「差异项 + 建议 + 疑问」，返回结构不设 verdict/结论字段——
  结构上就不存在"合格/不合格/通过/不通过/符合要求"。
- 缺失项措辞统一为"疑似缺项：未检出 'X'，请人工核对票面"。
- 顺序存疑措辞统一为"顺序存疑：…，请人工复核"。
- 输出固定以 DISCLAIMER 免责声明结尾。

规则硬编码在 _WORK_RULES / _OPERATION_RULES，条款出处锚定《安全工作规程》。
"""
from __future__ import annotations

import re

from langchain_core.tools import tool

# 固定免责声明（两票铁律：AI 只标差异，不替人做结论）
DISCLAIMER = (
    "以上仅为辅助检查，票面是否有效请以值班调度/工作负责人人工复核签字为准。"
)

# 禁词清单（输出兜底过滤 + 端点/前端校验）
FORBIDDEN_WORDS = ("合格", "不合格", "通过", "不通过", "符合要求")

# 顺序动词判定（判断一个步骤是否含"检查/确认/操作"）
_STEP_VERB = re.compile(r"检查|确认|核对|操作|断开|合上|拉开|合闸|分闸|验电|接地|装设|拆除|悬挂|取下|切换|投入|退出|测量|监视")

# 步骤编号识别：1. 2. 3. / 1) 2) / （1）（2）/ ①②③
_STEP_NO = re.compile(r"^[\s*\-]*\d{1,2}[.、)）]\s*|^[（(]?\d{1,2}[）)]?\s*|^[①②③④⑤⑥⑦⑧⑨⑩]")

_CLAUDE_SAFETY = "《安全工作规程》保证安全的技术措施"


def sanitize_forbidden(text: str) -> str:
    """兜底过滤（两票铁律）：把禁词替换为安全表述，防止 LLM 偶发越线下结论。"""
    out = text or ""
    if "合格" in out:
        out = out.replace("不合格", "不满足疑似项").replace("合格", "疑似合规项")
    if "通过" in out:
        out = out.replace("不通过", "疑似未满足").replace("通过", "疑似满足")
    if "符合要求" in out:
        out = out.replace("符合要求", "未检出异常")
    return out


def _clean(text: str) -> str:
    """归一化用于关键词匹配：去空白/换行。"""
    return re.sub(r"[\s　]+", "", (text or ""))


def _has(text: str, kws) -> bool:
    t = _clean(text)
    return any(k in t for k in kws)


# ------------------------------------------------------------
# 工作票规则
# ------------------------------------------------------------
def _check_work_ticket(text: str) -> list[dict]:
    t = _clean(text)
    items: list[dict] = []

    # 1) 必填项（票头）
    required = [
        ("票号/编号", ["编号", "票号", "序号"], "票面应有唯一编号便于追溯"),
        ("工作负责人", ["工作负责人", "负责人"], "第一种工作票必须指定工作负责人"),
        ("工作许可人", ["工作许可人", "许可人"], "应有许可人对工作票进行许可"),
        ("工作班成员", ["工作班成员", "工作班", "成员"], "应列出工作班成员"),
        ("工作内容", ["工作内容", "工作任务", "工作项目"], "应明确工作任务与范围"),
    ]
    for name, kws, why in required:
        if not _has(t, kws):
            items.append({
                "level": "missing",
                "item": f"疑似缺项：未检出『{name}』",
                "detail": why,
                "clause": "《安全工作规程》工作票制度",
                "suggestion": "请人工核对票面是否遗漏该栏目并补全。",
            })

    # 2) 保证安全的技术措施五项
    measures = [
        ("停电", ["停电"]),
        ("验电", ["验电"]),
        ("接地", ["接地"]),
        ("标示牌", ["标示牌", "禁止合闸"]),
        ("遮栏", ["遮栏", "围栏"]),
    ]
    for name, kws in measures:
        if not _has(t, kws):
            items.append({
                "level": "missing",
                "item": f"疑似缺项：未检出『{name}』安全措施",
                "detail": f"停电、验电、接地、悬挂标示牌、装设遮栏是保证安全的五项技术措施。",
                "clause": _CLAUDE_SAFETY,
                "suggestion": f"请确认票面是否已写明『{name}』措施，未写全需补填。",
            })

    # 3) 顺序规则：验电应在接地之前
    if _has(t, ["验电"]) and _has(t, ["接地"]):
        if t.find("验电") > t.find("接地"):
            items.append({
                "level": "warning",
                "item": "顺序存疑：『验电』出现在『接地』之后",
                "detail": "规程要求先验电、确认无电压后再装设接地线。",
                "clause": _CLAUDE_SAFETY,
                "suggestion": "请人工复核票面措施顺序：应先验电、后接地。",
            })

    # 4) 顺序规则：接地线应先接接地端
    if _has(t, ["接地"]):
        if not _has(t, ["先接接地端"]) and not _has(t, ["接接地端"]):
            items.append({
                "level": "question",
                "item": "需人工确认：未检出『接地线先接接地端』表述",
                "detail": "装设接地线应先接接地端、后接导体端，拆除顺序相反。",
                "clause": _CLAUDE_SAFETY,
                "suggestion": "请确认票面或作业指导是否包含接地线接线顺序要求。",
            })

    # 5) 签名完整性
    sign_kws = ["签发", "许可", "终结", "工作票签发人", "工作许可人"]
    if not _has(t, sign_kws):
        items.append({
            "level": "question",
            "item": "需人工确认：未检出『签发/许可/终结』签字位",
            "detail": "工作票应包含签发、许可、终结等环节的签名栏。",
            "clause": "《安全工作规程》工作票制度",
            "suggestion": "请人工核对票面签字栏是否齐全（签发人/许可人/工作负责人）。",
        })

    return items


# ------------------------------------------------------------
# 操作票规则
# ------------------------------------------------------------
def _check_operation_ticket(text: str) -> list[dict]:
    t = _clean(text)
    items: list[dict] = []

    # 1) 必填项
    required = [
        ("票号/编号", ["编号", "票号", "序号"], "操作票应有唯一编号便于追溯"),
        ("操作任务", ["操作任务", "操作项目", "操作内容"], "应写明操作任务"),
        ("操作人", ["操作人"], "应指定操作人"),
        ("监护人", ["监护人", "操作监护人"], "倒闸操作必须设监护人"),
    ]
    for name, kws, why in required:
        if not _has(t, kws):
            items.append({
                "level": "missing",
                "item": f"疑似缺项：未检出『{name}』",
                "detail": why,
                "clause": "《安全工作规程》操作票制度",
                "suggestion": "请人工核对票面是否遗漏该栏目并补全。",
            })

    # 2) 五防/安全关键词
    five_prevention = [
        ("核对设备双重名称/编号", ["核对", "双重名称", "编号"]),
        ("验电", ["验电"]),
        ("接地", ["接地"]),
        ("标示牌", ["标示牌", "禁止合闸"]),
        ("遮栏/围栏", ["遮栏", "围栏"]),
    ]
    for name, kws in five_prevention:
        if not _has(t, kws):
            items.append({
                "level": "missing",
                "item": f"疑似缺项：未检出『{name}』相关步骤",
                "detail": "倒闸操作票应包含核对设备、验电、接地、悬挂标示牌等防误措施步骤。",
                "clause": _CLAUDE_SAFETY,
                "suggestion": f"请确认操作票是否包含『{name}』步骤，未写全需补填。",
            })

    # 3) 步骤编号连续性 + 每步动词
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    numbered = [(i, ln.strip()) for i, ln in enumerate(lines) if _STEP_NO.match(ln.strip())]
    if not numbered:
        items.append({
            "level": "missing",
            "item": "疑似缺项：未检出连续编号的操作步骤",
            "detail": "操作票应按 1. 2. 3.… 顺序编号每一步操作。",
            "clause": "《安全工作规程》操作票制度",
            "suggestion": "请人工核对是否以编号分步列出全部操作项目。",
        })
    else:
        nums = []
        for idx, ln in numbered:
            m = re.match(r"^[\s*\-]*(\d{1,2})[.、)）]", ln)
            nums.append(int(m.group(1)) if m else None)
        if nums and nums[0] != 1:
            items.append({
                "level": "warning",
                "item": "顺序存疑：步骤编号并非从 1 开始",
                "detail": f"检测到首个步骤编号为 {nums[0]}，疑似缺步骤或编号不连续。",
                "clause": "《安全工作规程》操作票制度",
                "suggestion": "请人工复核步骤编号是否连续、无缺号。",
            })
        prev = None
        for n in nums:
            if n is not None and prev is not None and n != prev + 1:
                items.append({
                    "level": "warning",
                    "item": "顺序存疑：步骤编号不连续",
                    "detail": f"编号 {prev} 之后出现 {n}，疑似中间缺号。",
                    "clause": "《安全工作规程》操作票制度",
                    "suggestion": "请人工复核步骤编号是否连续、无缺号。",
                })
            if n is not None:
                prev = n
        no_verb = [idx for idx, ln in numbered if not _STEP_VERB.search(ln)]
        if no_verb:
            items.append({
                "level": "question",
                "item": f"需人工确认：{len(no_verb)} 个步骤未检出明确操作动词（检查/确认/断开/合上等）",
                "detail": "每个操作步骤应包含明确的动作词。",
                "clause": "《安全工作规程》操作票制度",
                "suggestion": "请人工核对相关步骤措辞是否完整可执行。",
            })

    # 4) 顺序规则：停电先断断路器再拉隔离开关
    if _has(t, ["断路器"]) and _has(t, ["隔离开关"]):
        if t.find("断路器") > t.find("隔离开关"):
            items.append({
                "level": "warning",
                "item": "顺序存疑：『隔离开关』出现在『断路器』之前",
                "detail": "停电操作应先断开断路器、再拉开隔离开关，防止带负荷拉隔离开关。",
                "clause": _CLAUDE_SAFETY,
                "suggestion": "请人工复核停电操作顺序：先断断路器，后拉隔离开关。",
            })

    # 5) 签字位
    if not _has(t, ["操作人", "监护人", "审核人"]) and not _has(t, ["签字"]):
        items.append({
            "level": "question",
            "item": "需人工确认：未检出『操作人/监护人/审核人』签字位",
            "detail": "操作票应包含操作人、监护人、审核人的签字栏。",
            "clause": "《安全工作规程》操作票制度",
            "suggestion": "请人工核对签字栏是否齐全。",
        })

    return items


@tool
async def check_ticket_rules(ticket_text: str, ticket_type: str = "work") -> dict:
    """两票合规性辅助检查（只标差异、出建议、提疑问，不下结论）。

    工作票（ticket_type='work'）检查：票头必填项、保证安全的五项技术措施
    （停电/验电/接地/标示牌/遮栏）、验电先于接地的顺序、接地线先接接地端、
    签发/许可/终结签字栏。

    操作票（ticket_type='operation'）检查：票头必填项、防误步骤（核对/验电/
    接地/标示牌/遮栏）、步骤编号连续性、每步操作动词、停电先断断路器再拉
    隔离开关的顺序、操作人/监护人/审核人签字位。

    返回 items 中的 level：missing=疑似缺项(红)，warning=顺序存疑(橙)，
    question=需人工确认(蓝)。**本工具永不返回合格/不合格结论。**

    Args:
        ticket_text: 票面全文
        ticket_type: 'work' 工作票 / 'operation' 操作票

    Returns:
        dict: mode='ticket', summary, items, questions, disclaimer
    """
    if not ticket_text or not ticket_text.strip():
        return {
            "mode": "ticket", "ticket_type": ticket_type,
            "summary": "未收到票面内容，无法进行辅助检查。",
            "items": [], "questions": [],
            "disclaimer": DISCLAIMER,
        }

    items = _check_operation_ticket(ticket_text) if ticket_type == "operation" \
        else _check_work_ticket(ticket_text)

    questions = [it for it in items if it["level"] == "question"]
    n_missing = sum(1 for it in items if it["level"] == "missing")
    n_warning = sum(1 for it in items if it["level"] == "warning")

    if not items:
        summary = "按规则逐项核对，未检出明显缺项或顺序问题；仍需人工按规程复核票面有效性。"
    else:
        parts = [f"辅助检查发现 {len(items)} 处需关注点"]
        if n_missing:
            parts.append(f"疑似缺项 {n_missing} 处")
        if n_warning:
            parts.append(f"顺序存疑 {n_warning} 处")
        if questions:
            parts.append(f"需人工确认 {len(questions)} 处")
        summary = "、".join(parts) + "；以下差异请人工核对确认，本检查不下结论，仅供人工复核参考。"

    return {
        "mode": "ticket",
        "ticket_type": ticket_type,
        "summary": summary,
        "items": items,
        "questions": questions,
        "disclaimer": DISCLAIMER,
    }


ALL_TICKET_TOOLS = [check_ticket_rules]
