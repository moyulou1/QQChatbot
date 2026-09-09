"""
情绪判定模块（纯LLM，最高准确率方案）
每轮AI回复生成后，调用DeepSeek API判定当前回复应有的情绪标签。
不做节流、不做规则快速通道，每轮都调LLM。

情绪标签（10个细粒度）：
  happy       开心
  excited     兴奋
  sad         悲伤
  angry       生气
  calm        平静
  coquettish  撒娇
  surprised   惊讶
  thinking    思考
  gentle      温柔
  playful     调皮
"""
from __future__ import annotations
import asyncio
import time
from typing import Optional

from .llm import get_llm

# 10个细粒度情绪标签
EMOTIONS = [
    "happy", "excited", "sad", "angry", "calm",
    "coquettish", "surprised", "thinking", "gentle", "playful",
]

# 情绪中文映射（用于日志和调试）
EMOTION_CN = {
    "happy": "开心", "excited": "兴奋", "sad": "悲伤", "angry": "生气",
    "calm": "平静", "coquettish": "撒娇", "surprised": "惊讶", "thinking": "思考",
    "gentle": "温柔", "playful": "调皮",
}

# 全局配置
_config = {
    "enabled": False,
    "model": "deepseek-chat",       # 情绪判定用的模型，可用 deepseek-reasoner
    "context_rounds": 5,             # 带入最近几轮对话上下文
    "timeout": 30,                   # LLM调用超时（秒）
    "fallback_emotion": "calm",      # LLM失败时的降级情绪
}

# 统计
_stats = {
    "total": 0,
    "success": 0,
    "failed": 0,
    "avg_latency_ms": 0.0,
    "latency_sum": 0.0,
    "emotion_dist": {},  # 情绪分布统计
}

_last_emotion = "calm"  # 上一次判定的情绪（用于上下文延续参考）


def configure_emotion(cfg):
    """注入配置"""
    global _config
    _config["enabled"] = getattr(cfg, "emotion_enabled", False)
    _config["model"] = getattr(cfg, "emotion_model", "deepseek-chat")
    _config["context_rounds"] = getattr(cfg, "emotion_context_rounds", 5)
    _config["timeout"] = getattr(cfg, "emotion_timeout", 30)
    _config["fallback_emotion"] = getattr(cfg, "emotion_fallback", "calm")
    print(f"[Emotion] 情绪判定配置已注入: enabled={_config['enabled']}, model={_config['model']}, "
          f"context_rounds={_config['context_rounds']}")


def is_enabled() -> bool:
    return _config["enabled"]


def get_last_emotion() -> str:
    return _last_emotion


def get_stats() -> dict:
    return dict(_stats)


# 情绪判定系统提示词
_EMOTION_SYSTEM = """你是一个专业的情绪分析专家。请分析给定的对话上下文和AI回复，判断AI回复最准确的情绪标签。

## 情绪标签定义（只能选一个）
- happy: 开心、愉快、轻松、满足
- excited: 兴奋、激动、热情高涨、迫不及待
- sad: 悲伤、难过、失落、沮丧、同情
- angry: 生气、愤怒、不满、烦躁、赌气
- calm: 平静、淡定、中性、客观、陈述事实
- coquettish: 撒娇、嗲、卖萌、亲昵、讨好
- surprised: 惊讶、震惊、意外、难以置信
- thinking: 思考、犹豫、分析、计算、不确定
- gentle: 温柔、体贴、安慰、关心、呵护
- playful: 调皮、开玩笑、逗趣、捉弄、嬉闹

## 判定原则
1. 以AI回复本身的语气和用词为主要依据
2. 结合对话上下文理解情绪产生的原因
3. 优先选择最具体的情绪，而不是笼统的calm
4. 如果回复同时有多种情绪，选最主导的那个
5. AI助手是20岁活泼可爱少女，回复通常带有情绪色彩，很少完全中性

## 输出格式（严格JSON）
{"emotion": "happy", "confidence": 0.85, "reason": "回复中使用了'嘿嘿'和感叹号，语气轻快开心"}
"""


def _build_context(short_term_messages: list, current_reply: str) -> str:
    """构建情绪判定的上下文文本"""
    rounds = _config["context_rounds"]
    lines = []
    # 最近N轮对话
    recent = short_term_messages[-rounds * 2:] if short_term_messages else []
    for msg in recent:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if isinstance(content, list):
            # 多模态消息，提取文本部分
            text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
            content = " ".join(text_parts)
        if role == "user":
            lines.append(f"群友: {content}")
        elif role == "assistant":
            lines.append(f"AI助手: {content}")
    lines.append(f"AI助手(当前回复): {current_reply}")
    return "\n".join(lines)


async def detect_emotion(
    current_reply: str,
    short_term_messages: Optional[list] = None,
    group_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> tuple[str, dict]:
    """
    纯LLM情绪判定。每轮调用，不做节流。
    
    Args:
        current_reply: AI生成的回复文本
        short_term_messages: 短期记忆消息列表（用于上下文）
        group_id: 群ID
        user_id: 用户ID
    
    Returns:
        (emotion_label, info_dict)
        info_dict包含: confidence, reason, latency_ms, from_llm(bool)
    """
    global _last_emotion, _stats
    _stats["total"] += 1
    
    if not _config["enabled"]:
        return _config["fallback_emotion"], {"from_llm": False, "reason": "情绪判定未启用"}
    
    if not current_reply or not current_reply.strip():
        return _config["fallback_emotion"], {"from_llm": False, "reason": "空回复"}
    
    start = time.time()
    context_text = _build_context(short_term_messages or [], current_reply)
    
    user_prompt = f"""请分析以下对话，判定AI助手当前回复的情绪。

{context_text}

请输出JSON格式的判定结果。"""
    
    try:
        llm_client = get_llm()
        if not llm_client:
            raise RuntimeError("LLM客户端未初始化")
        result = await asyncio.wait_for(
            llm_client.chat_json(
                instruction=_EMOTION_SYSTEM,
                content=user_prompt,
                max_tokens=300,
            ),
            timeout=_config["timeout"] + 5,
        )
        
        latency_ms = int((time.time() - start) * 1000)
        emotion = (result.get("emotion") or "").lower().strip()
        confidence = float(result.get("confidence", 0.5))
        reason = result.get("reason", "")
        
        # 校验情绪标签合法性
        if emotion not in EMOTIONS:
            emotion = _config["fallback_emotion"]
        
        _last_emotion = emotion
        _stats["success"] += 1
        _stats["latency_sum"] += latency_ms
        _stats["avg_latency_ms"] = _stats["latency_sum"] / _stats["success"]
        _stats["emotion_dist"][emotion] = _stats["emotion_dist"].get(emotion, 0) + 1
        
        info = {
            "from_llm": True,
            "confidence": confidence,
            "reason": reason,
            "latency_ms": latency_ms,
            "emotion_cn": EMOTION_CN.get(emotion, emotion),
        }
        return emotion, info
        
    except Exception as e:
        latency_ms = int((time.time() - start) * 1000)
        _stats["failed"] += 1
        # LLM失败时降级：用上一次情绪，如果没有则用fallback
        fallback = _last_emotion or _config["fallback_emotion"]
        info = {
            "from_llm": False,
            "confidence": 0.0,
            "reason": f"LLM判定失败: {e}",
            "latency_ms": latency_ms,
            "fallback": True,
        }
        return fallback, info
