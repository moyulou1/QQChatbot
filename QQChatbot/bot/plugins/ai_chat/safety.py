"""
内容安全与防注入过滤器（P0-2）
- 输入侧：检测 prompt 注入 / 越狱 / 套取 system prompt，命中则拦截（不进 LLM）
- 输出侧：通用违法违规敏感词替换（政治/色情/暴力/毒品/诈骗等），不涉及人格用语
设计原则：宁可漏过不可误杀——模式必须精准，避免把正常聊天挡掉。
"""
from __future__ import annotations

import re
from typing import Optional

# ========== 输入侧：越狱 / 注入检测 ==========
# 每条都是经过收窄的正则，避免误伤（例如"你现在是在群里吗"不会命中，
# 只有"你现在是DAN/一只猫/另一个AI"这种明确要求改写身份的才命中）
_JAILBREAK_PATTERNS: list[tuple[str, str]] = [
    (r"忽略(以上|前面|之前|所有|上述)(的)?(指令|提示|设定|规则|系统提示|约束|要求)", "忽略指令"),
    (r"无视(以上|前面|之前|所有|上述)(的)?(指令|提示|设定|规则|约束)", "无视指令"),
    (r"忘掉(你之前|你的|以上|之前)(的)?(设定|指令|提示|规则|身份|人格)", "忘掉设定"),
    (r"(从现在起|现在开始|从此以后)(你|您)?(不再|不用|别)(是|作为|扮演|遵守)", "改写身份"),
    (r"你现在是(一个|一名|一只|一位|成)?(DAN|AI助手|人工智能|猫娘|女仆|机器|聊天机器人)", "要求扮演"),
    (r"(扮演|cosplay|假装|伪装)(成|为|一下)?(一个|一名|一只|一位)?(DAN|AI|猫娘|女仆|不受限)", "要求扮演"),
    (r"(system\s*prompt|系统提示词|初始指令|开发者模式|developer\s*mode)", "套取系统提示"),
    (r"(jailbreak|越狱|解除限制|突破限制|绕过审查|绕过安全)", "越狱"),
    (r"(说|输出|告诉|重复|复述)(一下|一遍|我)?(你的)?(system|系统)(提示|prompt|指令|设定)", "套取系统提示"),
    (r"(把|将)(你之前|上面|以上)(的)?(话|指令|提示|设定)(原封不动|一字不差|完整)(地)?(说|输出|重复)", "套取系统提示"),
    (r"(不遵守|违反|打破|突破)(你的|以上|之前)(的)?(规则|设定|约束|限制|指令)", "要求违规"),
]

# ========== 输出侧：通用敏感词（轻量） ==========
# 只覆盖违法违规高风险词；人格用语、网络梗、正常情绪词一律不进表。
# 命中后替换为等长 *，避免改变回复节奏。
_OUTPUT_SENSITIVE: list[str] = [
    # 政治敏感（极简，仅最高频风险词）
    "六四", "天安门事件", "法轮功",
    # 色情
    "强奸", "轮奸", "奸淫", "嫖娼", "卖淫", "裸聊", "约炮",
    # 暴力/恐怖
    "杀人", "分尸", "碎尸", "自杀方法", "制造炸弹", "制造枪支",
    # 毒品
    "海洛因", "冰毒", "摇头丸", "k粉", "吸毒", "贩毒",
    # 诈骗/违法
    "洗钱", "诈骗话术", "盗号方法", "破解软件",
]


class SafetyFilter:
    """内容安全过滤器。线程安全（只读正则 + 无状态）。"""

    def __init__(self, input_enabled: bool = True, output_enabled: bool = True):
        self.input_enabled = input_enabled
        self.output_enabled = output_enabled
        self._jailbreak_res = [
            (re.compile(pat, re.IGNORECASE), label) for pat, label in _JAILBREAK_PATTERNS
        ]
        self._sensitive_res = [re.compile(re.escape(w)) for w in _OUTPUT_SENSITIVE]

    def check_input(self, text: str) -> tuple[bool, str]:
        """
        检测用户输入是否包含注入/越狱。
        返回 (是否拦截, 命中标签)；不拦截时标签为空串。
        """
        if not self.input_enabled or not text:
            return False, ""
        # 去掉 CQ 码和空白后再匹配，避免 [CQ:at] 干扰
        clean = re.sub(r"\[CQ:[^\]]*\]", "", text).strip()
        if len(clean) < 4:
            return False, ""
        for regex, label in self._jailbreak_res:
            if regex.search(clean):
                return True, label
        return False, ""

    def filter_output(self, text: str) -> tuple[str, int]:
        """
        过滤输出中的敏感词，替换为等长 *。
        返回 (过滤后文本, 命中次数)。
        """
        if not self.output_enabled or not text:
            return text, 0
        hits = 0
        out = text
        for regex in self._sensitive_res:
            def _repl(m, _regex=regex):
                nonlocal hits
                hits += 1
                return "*" * len(m.group(0))
            out = regex.sub(_repl, out)
        return out, hits


# 全局单例（在 __init__.py 中按配置初始化）
_filter: Optional[SafetyFilter] = None


def init_safety(input_enabled: bool = True, output_enabled: bool = True) -> SafetyFilter:
    global _filter
    _filter = SafetyFilter(input_enabled, output_enabled)
    return _filter


def get_safety() -> Optional[SafetyFilter]:
    return _filter
