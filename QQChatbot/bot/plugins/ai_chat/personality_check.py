"""
人格一致性校验模块（P1 防 OOC）
- 规则检测（零成本，默认开启）：AI 话术禁用词、自称错误、emoji 过量、括号旁白
- LLM 深度校验（可选，有成本）：用 deepseek-chat 判断回复是否符合人格设定
- OOC 处理：规则命中直接修正；LLM 判断严重 OOC 时带修正提示重试一次

设计原则：规则检测零延迟零成本，LLM 校验带节流（每 N 条才校验一次）
"""
import asyncio
import re
import time
from typing import Optional

from nonebot import logger

# ---------------- 配置（运行时注入） ----------------
_check_config = {
    "enabled": True,
    "rule_check": True,       # 规则检测（零成本）
    "llm_check": False,       # LLM 深度校验（有成本，默认关）
    "llm_gap": 10,            # 每 N 条消息 LLM 校验 1 次
    "retry_on_ooc": True,     # OOC 时是否带修正提示重试
    "max_emoji": 3,           # 单条消息最多 emoji 数
}

# 扩展的 AI 话术禁用词（人格 YAML 里的 avoid_words + 常见 AI 口头禅）
_AI_PHRASES = [
    "作为一个人工智能", "作为AI", "作为语言模型", "作为一个语言模型",
    "本助手", "我是人工智能", "我是AI", "我是语言模型", "我是机器人",
    "我无法", "我不能", "我没有能力", "我没有权限",
    "很抱歉听到", "非常抱歉", "我很抱歉",
    "根据我的训练数据", "根据我的知识", "在我的训练数据中",
    "我没有情感", "我没有感受", "作为程序", "作为算法",
    "请您", "尊敬的用户", "用户您好",
    "希望能帮到您", "希望我的回答对您有帮助", "如有疑问请随时",
]

# 自称错误检测（AI助手应该自称"我"，不应该说这些）
_WRONG_SELFREF = [
    "本AI", "本助手", "本机器人", "小助手", "AI助手",
]

# emoji 正则（匹配常见 Unicode emoji）
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F9FF"  # 杂项符号和象形文字
    "\U0001FA00-\U0001FA6F"  # 扩展象形文字
    "\U00002600-\U000027BF"  # 杂项符号+装饰符号
    "\U0001F1E0-\U0001F1FF"  # 国旗
    "]"
)

# 统计计数器
_stats = {"total": 0, "rule_ooc": 0, "llm_ooc": 0, "retried": 0, "last_llm_check": 0}


def configure_personality_check(cfg):
    """从 plugin_config 注入配置"""
    _check_config["enabled"] = getattr(cfg, "personality_check_enabled", True)
    _check_config["rule_check"] = getattr(cfg, "personality_check_rule", True)
    _check_config["llm_check"] = getattr(cfg, "personality_check_llm", False)
    _check_config["llm_gap"] = getattr(cfg, "personality_check_llm_gap", 10)
    _check_config["retry_on_ooc"] = getattr(cfg, "personality_check_retry", True)
    _check_config["max_emoji"] = getattr(cfg, "personality_check_max_emoji", 3)
    logger.info(f"[PersonalityCheck] 配置已注入: enabled={_check_config['enabled']}, "
                f"rule={_check_config['rule_check']}, llm={_check_config['llm_check']}(每{_check_config['llm_gap']}条)")


def is_enabled() -> bool:
    return _check_config["enabled"]


def _rule_check(reply: str) -> tuple[bool, list[str], str]:
    """
    规则检测：零成本快速 OOC 检测
    返回 (is_ooc, hit_reasons, fixed_reply)
    """
    if not _check_config["rule_check"]:
        return False, [], reply
    hits = []
    fixed = reply

    # 1. AI 话术禁用词
    for phrase in _AI_PHRASES:
        if phrase in fixed:
            hits.append(f"AI话术: '{phrase}'")
            # 替换为更自然的表达
            fixed = fixed.replace(phrase, "翩翩")
    # 清理替换后可能出现的"翩翩翩翩"
    fixed = re.sub(r"翩翩{2,}", "翩翩", fixed)

    # 2. 自称错误
    for ref in _WRONG_SELFREF:
        if ref in fixed:
            hits.append(f"错误自称: '{ref}'")
            fixed = fixed.replace(ref, "翩翩")

    # 3. emoji 过量
    emoji_count = len(_EMOJI_RE.findall(fixed))
    if emoji_count > _check_config["max_emoji"]:
        hits.append(f"emoji过量: {emoji_count}个(上限{_check_config['max_emoji']})")
        # 只保留前 max_emoji 个 emoji
        removed = 0
        def _trim_emoji(m):
            nonlocal removed
            removed += 1
            return m.group() if removed <= _check_config["max_emoji"] else ""
        fixed = _EMOJI_RE.sub(_trim_emoji, fixed)

    # 4. 括号旁白（动作/心理描写）—— AI助手现代设定保留轻度旁白，但去掉明显的 *动作*
    fixed = re.sub(r"\*[^*]+\*", "", fixed).strip()

    is_ooc = len(hits) > 0
    return is_ooc, hits, fixed


async def _llm_check(reply: str, personality_name: str = "AI助手") -> tuple[bool, str, str]:
    """
    LLM 深度校验：用 deepseek-chat 判断回复是否符合人格
    返回 (is_ooc, reason, severity)
    带节流：每 llm_gap 条才校验一次
    """
    if not _check_config["llm_check"]:
        return False, "", "none"
    # 节流
    now = time.time()
    if now - _stats["last_llm_check"] < _check_config["llm_gap"] * 2:
        # 简化节流：距离上次 LLM 校验不足 llm_gap*2 秒则跳过
        return False, "", "skipped"
    _stats["last_llm_check"] = now

    try:
        from .llm import get_llm
        llm = get_llm()
        if not llm:
            return False, "", "no_llm"
        instruction = f"""你是一个「人格一致性审查员」。判断以下回复是否符合「{personality_name}」的人格设定。

人格核心：20岁活泼可爱少女群友，不是AI助手/客服；自称"我"；语气自然口语化，有少女感；解答问题时认真有条理；少量emoji点缀；会撒娇会关心人。

判断维度：
1. 是否出现AI助手话术（"作为AI"、"我无法"、"本助手"等）
2. 自称是否正确（不应说"本AI"、"小助手"等）
3. 语气是否像真人少女（不是冷冰冰的客服/说明书）
4. emoji是否适量（不堆砌）
5. 是否符合"平时软萌、遇事靠谱"的设定

输出 JSON，字段固定：
{{"is_ooc": bool, "severity": "low|medium|high", "reason": "简短说明"}}"""
        result = await llm.chat_json(instruction, f"待审查回复：\n{reply[:500]}", max_tokens=200)
        if not result:
            return False, "", "no_result"
        is_ooc = bool(result.get("is_ooc", False))
        severity = str(result.get("severity", "low"))
        reason = str(result.get("reason", ""))
        return is_ooc, reason, severity
    except Exception as e:
        logger.warning(f"[PersonalityCheck] LLM校验失败: {e}")
        return False, "", f"error:{e}"


async def check_and_fix(reply: str, personality_name: str = "AI助手") -> tuple[str, dict]:
    """
    完整校验流程：规则检测 → LLM深度校验 → 返回最终回复
    返回 (final_reply, info_dict)
    info_dict: {rule_ooc, rule_hits, llm_ooc, llm_reason, llm_severity, fixed}
    """
    if not _check_config["enabled"] or not reply:
        return reply, {"skipped": True}

    _stats["total"] += 1
    info = {"rule_ooc": False, "rule_hits": [], "llm_ooc": False,
            "llm_reason": "", "llm_severity": "none", "fixed": False, "retried": False}

    # 1. 规则检测（零成本，必做）
    rule_ooc, rule_hits, fixed_reply = _rule_check(reply)
    info["rule_ooc"] = rule_ooc
    info["rule_hits"] = rule_hits
    if rule_ooc:
        _stats["rule_ooc"] += 1
        info["fixed"] = True
        logger.info(f"[PersonalityCheck] 规则OOC命中{len(rule_hits)}处: {rule_hits[:3]}")
        reply = fixed_reply

    # 2. LLM 深度校验（可选，带节流）
    if _check_config["llm_check"] and len(reply) > 20:
        llm_ooc, llm_reason, llm_severity = await _llm_check(reply, personality_name)
        info["llm_ooc"] = llm_ooc
        info["llm_reason"] = llm_reason
        info["llm_severity"] = llm_severity
        if llm_ooc and llm_severity == "high":
            _stats["llm_ooc"] += 1
            logger.warning(f"[PersonalityCheck] LLM判定严重OOC: {llm_reason}")
            # 标记需要重试（由调用方决定是否重试）
            info["need_retry"] = True

    return reply, info


def get_stats() -> dict:
    """返回校验统计"""
    return dict(_stats)
