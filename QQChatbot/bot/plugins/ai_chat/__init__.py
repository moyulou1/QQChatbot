"""
AI 群聊自动回复插件 - 主入口
"""
from __future__ import annotations

import os

# ========== 强制所有写入到 F 盘，避免 C 盘系统盘写爆 ==========
_F_DISK_TMP = r"F:\qq_chatbot\tmp"
_F_DISK_HF = r"F:\qq_chatbot\models\huggingface"
os.makedirs(_F_DISK_TMP, exist_ok=True)
os.makedirs(_F_DISK_HF, exist_ok=True)
os.environ["TEMP"] = _F_DISK_TMP
os.environ["TMP"] = _F_DISK_TMP
os.environ["HF_HOME"] = _F_DISK_HF
os.environ["HUGGINGFACE_HUB_CACHE"] = os.path.join(_F_DISK_HF, "hub")
os.environ["TRANSFORMERS_CACHE"] = os.path.join(_F_DISK_HF, "hub")
import tempfile
tempfile.tempdir = _F_DISK_TMP
# ==================================================================

import asyncio
import json
import random
import re
import time
from pathlib import Path
from typing import Optional

import requests

from nonebot import on_command, on_message, logger, require, get_driver
from nonebot.adapters.onebot.v11 import (
    Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment,
)
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.rule import to_me
from nonebot.exception import FinishedException

from .config import plugin_config
from .llm import ChatMessage, LLMConfig, init_llm, get_llm
from .memory import LongTermMemory, ShortTermMemoryManager
from .personality import PersonalityLoader
from .profiles import UserProfileStore
from .router import pick_model as _route_pick_model
from .safety import init_safety, get_safety
from .token_stats import TokenStats, TokenUsage
from .tts import configure_tts, synthesize_to_silk, save_silk_to_temp, is_enabled as tts_is_enabled
from .asr import configure_asr, transcribe_silk, is_enabled as asr_is_enabled
from .personality_check import configure_personality_check, check_and_fix, is_enabled as pc_is_enabled, get_stats as pc_get_stats
from .emotion import configure_emotion, detect_emotion, is_enabled as emotion_is_enabled, get_stats as emotion_get_stats
from .voice_manager import configure_voice, is_enabled as voice_is_enabled, list_available_voices, get_voice_for_emotion
from .vision import extract_image_segments, images_to_data_urls, build_multimodal_content
from .web_console import init_console, get_console
from .web_search import init_searcher, get_searcher, should_search
from .message_queue import init_queue_manager, get_queue_manager

# ========== 初始化 LLM ==========
llm_config = LLMConfig(
    api_key=plugin_config.deepseek_api_key,
    base_url=plugin_config.deepseek_base_url,
    model=plugin_config.deepseek_model,
    temperature=plugin_config.deepseek_temperature,
    max_tokens=plugin_config.deepseek_max_tokens,
    timeout=plugin_config.deepseek_timeout,
)
init_llm(llm_config)

# ========== 初始化联网搜索 ==========
init_searcher(timeout=15, max_results=5)
logger.info("[AI-Chat] 联网搜索模块已初始化 (Bing)")

# ========== 初始化人格 ==========
_personality_loader = PersonalityLoader(
    personality_dir=plugin_config.personality_dir,
    personality_file=plugin_config.personality_file,
)
try:
    _personality_loader.load()
    logger.info(f"[AI-Chat] 人格加载成功: {_personality_loader.get_name()}")
except Exception as e:
    logger.warning(f"[AI-Chat] 人格加载失败: {e}")

# ========== 初始化记忆 ==========
_short_term = ShortTermMemoryManager(max_rounds=plugin_config.context_rounds)

_long_term: Optional[LongTermMemory] = None
if plugin_config.enable_long_term_memory:
    _long_term = LongTermMemory(db_path=plugin_config.memory_db_path)

# ========== 群友画像 ==========
_profiles: Optional[UserProfileStore] = None
if plugin_config.enable_user_profile:
    _profile_db = plugin_config.memory_db_path.replace("long_term.db", "user_profiles.db")
    _profiles = UserProfileStore(db_path=_profile_db, extract_gap=plugin_config.profile_extract_gap)

# ========== 内容安全与防注入（P0-2） ==========
_safety = init_safety(
    input_enabled=plugin_config.safety_input_enabled,
    output_enabled=plugin_config.safety_output_enabled,
)

# ========== TTS 语音合成（P1） ==========
configure_tts(plugin_config)

# ========== ASR 语音识别（P1） ==========
configure_asr(plugin_config)

# ========== 人格一致性校验（P1 防OOC） ==========
configure_personality_check(plugin_config)

# ========== 情绪判定（纯LLM，每轮调用） ==========
configure_emotion(plugin_config)

# ========== 音色管理（多参考音频池 + 情绪映射） ==========
configure_voice(plugin_config)

# ========== 运行状态（回复总开关，持久化） ==========
_state_path = Path(plugin_config.state_file)
_runtime_state = {"reply_enabled": True, "reason_mode": plugin_config.reason_mode,
                  "paused_at": 0, "started_at": time.time()}
try:
    if _state_path.exists():
        _saved = json.loads(_state_path.read_text(encoding="utf-8"))
        _runtime_state["reply_enabled"] = bool(_saved.get("reply_enabled", True))
        if _saved.get("reason_mode"):
            _runtime_state["reason_mode"] = _saved["reason_mode"]
except Exception as _e:
    logger.warning(f"[AI-Chat] 状态文件读取失败，用默认: {_e}")

def _save_runtime_state():
    try:
        _state_path.parent.mkdir(parents=True, exist_ok=True)
        _state_path.write_text(json.dumps(_runtime_state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"[AI-Chat] 状态保存失败: {e}")

# ========== Token 统计 ==========
_token_stats = TokenStats(
    db_path=plugin_config.memory_db_path.replace("long_term.db", "token_stats.db")
)

def _make_usage_callback():
    async def _on_usage(usage: dict, metadata: dict):
        try:
            comp_details = usage.get("completion_tokens_details") or {}
            tu = TokenUsage(
                model=usage.get("used_model") or plugin_config.deepseek_model,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
                cache_read_input_tokens=usage.get("prompt_cache_hit_tokens", 0) or usage.get("cache_read_input_tokens", 0),
                reasoning_tokens=usage.get("reasoning_tokens", 0) or comp_details.get("reasoning_tokens", 0),
                latency_ms=usage.get("latency_ms", 0),
                group_id=metadata.get("group_id", ""),
                user_id=metadata.get("user_id", ""),
            )
            await _token_stats.record(tu)
        except Exception as e:
            logger.warning(f"[AI-Chat] token 统计记录失败: {e}")
    return _on_usage

_llm_client = get_llm()
if _llm_client:
    _llm_client.on_usage = _make_usage_callback()
    _llm_client.configure_vision(
        base_url=plugin_config.vision_base_url,
        api_key=plugin_config.vision_api_key,
        model=plugin_config.vision_model,
        temperature=plugin_config.vision_temperature,
        max_tokens=plugin_config.vision_max_tokens,
    )

# ========== Web 控制台 ==========
_console = None
if plugin_config.console_enabled:
    try:
        import secrets
        console_token = plugin_config.console_token
        if not console_token:
            console_token = secrets.token_hex(16)
            logger.warning(f"[AI-Chat] 未配置控制台令牌，自动生成: {console_token}")

        static_dir = Path(__file__).parent.parent.parent / "web_static"

        _console = init_console(
            host=plugin_config.console_host,
            port=plugin_config.console_port,
            token=console_token,
            static_dir=str(static_dir),
        )
        _console.personality_loader = _personality_loader
        _console.token_stats = _token_stats
        _console.short_term = _short_term
        _console.long_term = _long_term
        _console.llm_client = _llm_client
        _console.plugin_config = plugin_config
        _console.profiles = _profiles
        _console.safety_filter = _safety
        _console.runtime_state = _runtime_state
        _console.save_runtime_state = _save_runtime_state

        try:
            import uvicorn
            from nonebot import get_driver
            console_app = _console.app
            _console_host = plugin_config.console_host
            _console_port = plugin_config.console_port

            @get_driver().on_startup
            async def _start_console():
                config = uvicorn.Config(console_app, host=_console_host, port=_console_port, log_level="warning")
                server = uvicorn.Server(config)
                asyncio.create_task(server.serve())
                logger.info(f"[AI-Chat] Web控制台已启动: http://{_console_host}:{_console_port}/")
                logger.info(f"[AI-Chat] 控制台令牌: {console_token}")

        except ImportError:
            logger.warning("[AI-Chat] uvicorn 未安装，Web控制台不可用。运行: pip install uvicorn")
        except Exception as e:
            logger.error(f"[AI-Chat] Web控制台启动失败: {e}", exc_info=True)

    except ImportError:
        logger.warning("[AI-Chat] FastAPI 未安装，Web控制台不可用。运行: pip install fastapi uvicorn")
    except Exception as e:
        logger.error(f"[AI-Chat] Web控制台初始化失败: {e}", exc_info=True)

# ========== 运行时状态 ==========
_last_reply_time: dict[str, float] = {}
_processing_groups: set[str] = set()
_processing_lock = asyncio.Lock()
# 速率限制（P0-3）：滑动窗口，key=user_id 或 group_id，value=时间戳列表
_rate_user: dict[str, list[float]] = {}
_rate_group: dict[str, list[float]] = {}
# 长期记忆 LLM 抽取节流（P1）：key=group_id，value=上次抽取时间戳
_lt_extract_last: dict[str, float] = {}

# 全局昵称（.env 的 NICKNAME，nonebot 会用它在事件预处理阶段剥离开头称呼并置 to_me）
_NICKNAMES: set[str] = set()
try:
    _raw_nicks = getattr(get_driver().config, "nickname", set()) or set()
    _NICKNAMES = {str(x).strip() for x in _raw_nicks if str(x).strip()}
except Exception:
    _NICKNAMES = set()

_CQ_RE = re.compile(r"\[CQ:[^\]]*\]")

# 定时清理过期短期记忆
try:
    require("nonebot_plugin_apscheduler")
    from nonebot_plugin_apscheduler import scheduler

    @scheduler.scheduled_job("interval", minutes=30)
    async def _cleanup_memory():
        await _short_term.cleanup_expired()
except Exception:
    logger.info("[AI-Chat] 未安装 apscheduler，跳过定时记忆清理")


# ========== 工具函数 ==========
def _is_group_enabled(group_id: str) -> bool:
    gid = str(group_id)
    if plugin_config.disabled_groups and gid in plugin_config.disabled_groups:
        return False
    if plugin_config.enabled_groups and gid not in plugin_config.enabled_groups:
        return False
    return True

def _is_user_blacklisted(user_id: str) -> bool:
    return str(user_id) in plugin_config.blacklist_users

def _check_cooldown(group_id: str) -> bool:
    gid = str(group_id)
    now = time.time()
    last = _last_reply_time.get(gid, 0)
    return (now - last) >= plugin_config.reply_cooldown

def _update_cooldown(group_id: str):
    _last_reply_time[str(group_id)] = time.time()

def _check_rate(user_id: str, group_id: str) -> tuple[bool, str]:
    """
    滑动窗口速率限制（P0-3）。返回 (是否允许, 原因)。
    先判单用户，再判全群；任一超限即拒绝。
    """
    now = time.time()
    # 单用户窗口
    uw = plugin_config.rate_limit_user_window
    um = plugin_config.rate_limit_user_max
    ul = [t for t in _rate_user.get(user_id, []) if now - t < uw]
    if len(ul) >= um:
        _rate_user[user_id] = ul
        return False, f"单用户限速({len(ul)}/{um}次/{uw}s)"
    ul.append(now)
    _rate_user[user_id] = ul
    # 全群窗口
    gw = plugin_config.rate_limit_group_window
    gm = plugin_config.rate_limit_group_max
    gl = [t for t in _rate_group.get(group_id, []) if now - t < gw]
    if len(gl) >= gm:
        _rate_group[group_id] = gl
        return False, f"群限速({len(gl)}/{gm}次/{gw}s)"
    gl.append(now)
    _rate_group[group_id] = gl
    return True, ""

def _extract_text(message: Message) -> str:
    text_parts = []
    for seg in message:
        if seg.type == "text":
            text_parts.append(seg.data.get("text", ""))
    return "".join(text_parts).strip()


def _extract_voice_segments(message: Message) -> list[dict]:
    """从消息中提取语音段（record），返回 [{url, file, duration}]"""
    voices = []
    for seg in message:
        if seg.type == "record":
            voices.append({
                "url": seg.data.get("url", ""),
                "file": seg.data.get("file", ""),
                "duration": int(seg.data.get("duration", 0) or 0),
            })
    return voices


def _download_voice_silk(voice_seg: dict) -> Optional[bytes]:
    """下载语音 silk 文件，返回字节；优先 url，其次本地 file 路径"""
    url = voice_seg.get("url", "")
    file_path = voice_seg.get("file", "")
    logger.info(f"[ASR] 语音段: url={'有' if url else '无'}, file={file_path[:60] if file_path else '无'}")
    if url:
        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code == 200 and len(resp.content) > 50:
                logger.info(f"[ASR] URL下载成功: {len(resp.content)}字节, content-type={resp.headers.get('content-type','')}")
                return resp.content
        except Exception as e:
            logger.warning(f"[ASR] 语音URL下载失败: {e}")
    if file_path:
        # NapCat 的 file 可能是本地路径或 file:/// URL
        clean_path = file_path.replace("file:///", "").replace("file://", "")
        if os.path.exists(clean_path):
            with open(clean_path, "rb") as f:
                data = f.read()
            logger.info(f"[ASR] 本地文件读取成功: {clean_path[-40:]} {len(data)}字节")
            return data
        else:
            logger.warning(f"[ASR] 本地文件不存在: {clean_path}")
    return None

def _plain_raw(event) -> str:
    """取原始消息并去掉 CQ 码（nonebot 会剥离开头@/昵称，原始内容在 raw_message）。"""
    raw = getattr(event, "raw_message", "") or ""
    return _CQ_RE.sub("", raw).strip()

def _is_at_me(event: MessageEvent) -> bool:
    # 私聊就是在跟我说话，直接算@我
    if not isinstance(event, GroupMessageEvent):
        return True
    # 兜底1：消息段里仍带 at（部分场景框架未剥离）
    for seg in event.message:
        if seg.type == "at" and str(seg.data.get("qq", "")) == str(event.self_id):
            return True
    # 兜底2：原始 CQ 串里 @ 了自己
    raw = getattr(event, "raw_message", "") or ""
    if f"qq={event.self_id}" in raw.replace(" ", ""):
        return True
    return False

def _all_names() -> set[str]:
    names = set(_NICKNAMES)
    try:
        pn = _personality_loader.get_name()
        if pn:
            names.add(str(pn).strip())
    except Exception:
        pass
    return {n for n in names if n}


def _to_pinyin(text: str) -> str:
    """把中文转成拼音（不带声调，空格分隔），用于同音字模糊匹配"""
    if not text:
        return ""
    try:
        from pypinyin import lazy_pinyin
        return " ".join(lazy_pinyin(text))
    except ImportError:
        return text


def _fuzzy_match_name(text: str, names: set[str]) -> bool:
    """拼音模糊匹配：检查文本中是否包含昵称的拼音（容忍ASR同音字误差）"""
    if not text or not names:
        return False
    text_pinyin = _to_pinyin(text)
    if not text_pinyin:
        return False
    for name in names:
        name_pinyin = _to_pinyin(name)
        if name_pinyin and len(name_pinyin) >= 2 and name_pinyin in text_pinyin:
            return True
    return False

def _mentions_name(event: MessageEvent, text: str) -> bool:
    blob = f"{text} {_plain_raw(event)}"
    names = _all_names()
    if not names:
        return False
    # 1) 精确匹配
    if any(n in blob for n in names):
        return True
    # 2) 拼音模糊匹配（容忍ASR同音字误差，如"翩翩"→"偏偏"）
    if _fuzzy_match_name(blob, names):
        return True
    return False

def _contains_keyword(text: str) -> bool:
    return any(kw and kw in (text or "") for kw in plugin_config.trigger_keywords)

def _should_reply(event: MessageEvent, text: str, group_id: str = "", is_private: bool = False,
                  has_voice: bool = False) -> tuple[bool, str]:
    user_id = str(event.user_id)
    if is_private:
        # 私聊：黑名单检查后直接回复（不需要@/昵称/关键词/随机）
        if _is_user_blacklisted(user_id):
            return False, "用户黑名单"
        return True, "私聊直接回复"
    if not group_id:
        group_id = str(getattr(event, "group_id", ""))
    if not _is_group_enabled(group_id):
        return False, "群未启用"
    if _is_user_blacklisted(user_id):
        return False, "用户黑名单"
    if not _check_cooldown(group_id):
        return False, "冷却中"

    # 群聊语音自动触发：ASR转写成功的语音消息不需要唤醒词（发语音大概率是在跟机器人说话）
    if has_voice:
        return True, "语音消息自动触发"

    to_me = bool(getattr(event, "to_me", False))
    # 1) 被@ 或 开头呼唤（nonebot 已置 to_me；消息段/CQ 再兜底判一次）
    if plugin_config.reply_on_at and (to_me or _is_at_me(event)):
        return True, "被@/呼唤"
    # 2) 句中任意位置提到昵称/人格名（用原始消息，避免被开头昵称剥离误伤）
    if plugin_config.reply_on_name and _mentions_name(event, text):
        return True, "提及名字"
    # 3) 关键词（剥后文本 + 原始文本双查）
    if _contains_keyword(text) or _contains_keyword(_plain_raw(event)):
        return True, "关键词触发"
    # 4) 概率随机插话
    if plugin_config.random_reply_probability > 0 and random.random() < plugin_config.random_reply_probability:
        return True, "随机插话"
    return False, "未触发"

def _pick_model(text: str) -> tuple[str, str]:
    """根据推理模式与问题内容选择模型，返回 (模型名, 说明)。判定逻辑统一在 router.py。"""
    mode = _runtime_state.get("reason_mode", plugin_config.reason_mode)
    return _route_pick_model(text, mode, plugin_config.deepseek_model,
                             plugin_config.deepseek_reasoner_model)

async def _build_messages(event: MessageEvent, text: str, group_name: str = "",
                          group_id: str = "",
                          attach_images: Optional[list[str]] = None,
                          image_question: str = "") -> list[ChatMessage]:
    if not group_id:
        group_id = str(getattr(event, "group_id", f"private_{event.user_id}"))
    user_id = str(event.user_id)
    messages: list[ChatMessage] = []
    system_prompt = _personality_loader.build_system_prompt(group_name=group_name, group_id=group_id)
    if _long_term:
        try:
            memories = await _long_term.query(group_id=group_id, keyword=text[:50], limit=5)
            if memories:
                memory_text = _long_term.format_memories_for_prompt(memories)
                system_prompt += f"\n\n{memory_text}"
        except Exception as e:
            logger.warning(f"[AI-Chat] 长期记忆查询失败: {e}")
    # 注入群友画像（当前说话人 + 近期活跃群友）
    if _profiles:
        try:
            prof_text = await _profiles.format_for_prompt(
                group_id, user_id, limit=plugin_config.profile_max_inject)
            if prof_text:
                system_prompt += f"\n\n{prof_text}"
        except Exception as e:
            logger.warning(f"[AI-Chat] 群友画像注入失败: {e}")
    messages.append(ChatMessage(role="system", content=system_prompt))
    context = await _short_term.get_context(group_id)
    messages.extend(context)
    # 图片只在当前这一轮进入模型：把最后一条 user 消息升级为多模态；历史轮仍是纯文本
    if attach_images:
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].role == "user":
                clean_q = image_question or (
                    messages[i].content if isinstance(messages[i].content, str) else text)
                messages[i].content = build_multimodal_content(clean_q, attach_images)
                break
    return messages

def _clean_reply(text: str) -> str:
    text = text.strip()
    prefix_patterns = [r"^小助手[：:]\s*", r"^我[：:]\s*", r"^助手[：:]\s*", r"^AI[：:]\s*"]
    for pattern in prefix_patterns:
        text = re.sub(pattern, "", text)
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("「") and text.endswith("」")):
        text = text[1:-1]
    # 人格级输出后处理（如AI助手的现代词双保险过滤）
    try:
        text = _personality_loader.apply_output_filter(text)
    except Exception as e:
        logger.debug(f"[AI-Chat] 输出过滤跳过: {e}")
    if len(text) > plugin_config.max_message_length:
        text = text[:plugin_config.max_message_length] + "..."
    return text.strip()


# ========== 主消息处理器 ==========
group_msg_handler = on_message(priority=99, block=False)

def _create_message_handler():
    """创建消息处理函数（闭包，避免被 nonebot 自动注册为事件处理器）"""
    async def _handler(message_data: dict):
        """
        处理队列中的消息（实际的 LLM 调用和回复发送）
        由会话级消息队列调用，同一会话内串行处理，解决并发吞消息问题
        """
        event = message_data["event"]
        bot = message_data["bot"]
        user_id = message_data["user_id"]
        group_id = message_data["group_id"]
        group_name = message_data["group_name"]
        is_private = message_data["is_private"]
        text = message_data["text"]
        img_segs = message_data["img_segs"]
        has_image = message_data["has_image"]
        voice_segs = message_data["voice_segs"]
        is_voice_input = message_data["is_voice_input"]
        voice_texts = message_data["voice_texts"]
        user_name = message_data["user_name"]
        reason = message_data["reason"]

        try:
            hist_text = f"{text} [图片x{len(img_segs)}]" if has_image else text
            await _short_term.add_user_message(group_id, user_id, user_name, hist_text)
            # 总开关：暂停时只记录上下文，不调用模型、不回复
            if not _runtime_state.get("reply_enabled", True):
                logger.info(f"[AI-Chat] 已暂停自动回复，忽略 群{group_id} {user_name}: {text[:30]}")
                is_wake_attempt = _is_at_me(event) or _mentions_name(event, text) or any(
                    kw in text for kw in ["在吗", "在不在", "有人吗", "醒醒", "起床", "说话", "回复"]
                )
                if is_wake_attempt:
                    await bot.send(event, "更新中，对话监听已关闭")
                return
            # 速率限制
            allowed, rate_reason = _check_rate(user_id, group_id)
            if not allowed:
                logger.info(f"[AI-Chat] 触发限速({rate_reason})，群{group_id} {user_name}")
                await bot.send(event, "（好多人找我说话呀，等一下再说好不好～）")
                return
            # 下载并转码本轮图片
            attach_images: list[str] = []
            if has_image:
                attach_images = await images_to_data_urls(
                    img_segs, bot,
                    max_side=plugin_config.vision_max_side,
                    jpeg_quality=plugin_config.vision_jpeg_quality,
                    max_bytes=plugin_config.vision_max_bytes)
                logger.info(f"[AI-Chat] 群{group_id} 收到{len(img_segs)}张图，成功转码{len(attach_images)}张")
            messages = await _build_messages(event, text, group_name,
                                             group_id=group_id,
                                             attach_images=attach_images, image_question=text)
            llm = get_llm()
            if llm is None:
                await bot.send(event, "（内部错误：LLM未初始化）")
                return
            if has_image and not attach_images:
                await bot.send(event, "图我这边没加载出来，再发一次好不好嘛🥺")
                return
            if attach_images:
                use_model, model_note = plugin_config.vision_model, "视觉模型识图"
            else:
                use_model, model_note = _pick_model(text)
            logger.info(f"[AI-Chat] 群{group_id} 触发回复({reason}|{model_note})，用户{user_name}: {text[:50]}")
            # 联网搜索
            if not attach_images and should_search(text):
                searcher = get_searcher()
                if searcher:
                    try:
                        logger.info(f"[WebSearch] 触发联网搜索: {text[:50]}")
                        search_result = await searcher.search_and_format(text)
                        if search_result and "未找到" not in search_result:
                            if messages and messages[0].role == "system":
                                original_content = messages[0].content
                                messages[0].content = original_content + "\n\n" + search_result
                            logger.info(f"[WebSearch] 搜索结果已注入，长度: {len(search_result)}")
                    except Exception as search_e:
                        logger.warning(f"[WebSearch] 搜索失败，不影响主流程: {search_e}")
            try:
                if attach_images:
                    reply = await llm.chat_vision(messages, metadata={"group_id": group_id, "user_id": user_id})
                else:
                    reply = await llm.chat(messages, metadata={"group_id": group_id, "user_id": user_id},
                                           model=use_model)
                reply = _clean_reply(reply)
                if _safety:
                    reply, out_hits = _safety.filter_output(reply)
                    if out_hits:
                        logger.info(f"[AI-Chat] 输出过滤命中{out_hits}处敏感词，已替换")
            except Exception as e:
                err_msg = str(e)
                logger.error(f"[AI-Chat] LLM 调用失败: {err_msg}")
                if "Key 无效" in err_msg or "401" in err_msg or "Unauthorized" in err_msg:
                    reply = "（我的脑子好像出了点问题，玉楼帮看看API Key好不好...）"
                elif "余额不足" in err_msg or "402" in err_msg or "quota" in err_msg.lower():
                    reply = "（我今天想太多了，脑子不够用了，明天再来找我玩嘛🥺）"
                elif "限流" in err_msg or "429" in err_msg or "rate" in err_msg.lower():
                    reply = "（好多人找我说话呀，等一下再说好不好～）"
                elif "网络" in err_msg or "ConnectError" in err_msg or "Timeout" in err_msg or "超时" in err_msg:
                    reply = "（网络有点卡，我没听清，再说一遍好不好嘛～）"
                else:
                    reply = "（脑子有点乱，等一下再说吧...）"
            # 人格一致性校验
            if pc_is_enabled() and reply and reply != "...":
                reply, pc_info = await check_and_fix(reply, "AI助手")
                if pc_info.get("need_retry") and plugin_config.personality_check_retry:
                    logger.info(f"[PersonalityCheck] 严重OOC，带修正提示重试: {pc_info.get('llm_reason','')}")
                    try:
                        retry_note = ("注意：你是AI助手，20岁活泼可爱少女群友，不是AI助手！"
                                      "用自然的少女口吻回复，不要说'作为AI'、'我无法'、'本助手'等话术。")
                        retry_messages = messages + [ChatMessage(role="user", content=retry_note)]
                        if attach_images:
                            reply = await llm.chat_vision(retry_messages, metadata={"group_id": group_id, "user_id": user_id})
                        else:
                            reply = await llm.chat(retry_messages, metadata={"group_id": group_id, "user_id": user_id},
                                                   model=use_model)
                        reply = _clean_reply(reply)
                        if _safety:
                            reply, _ = _safety.filter_output(reply)
                        from .personality_check import _rule_check
                        _, _, reply = _rule_check(reply)
                        logger.info("[PersonalityCheck] 重试完成")
                    except Exception as retry_e:
                        logger.warning(f"[PersonalityCheck] 重试失败，用原回复: {retry_e}")
            if not reply:
                reply = "..."
            _update_cooldown(group_id)
            await _short_term.add_assistant_message(group_id, reply)
            if _long_term and plugin_config.enable_long_term_memory:
                asyncio.create_task(_maybe_save_long_term(group_id, user_id, text, reply))
            if _profiles:
                asyncio.create_task(_profiles.maybe_extract_with_llm(get_llm(), group_id, user_id, text, reply))
            # TTS 语音回复
            voice_sent = False
            if tts_is_enabled():
                tts_mode = getattr(plugin_config, "tts_trigger_mode", "off")
                should_voice = False
                if tts_mode == "always":
                    should_voice = True
                elif tts_mode == "on_demand" and (_is_voice_request(text) or is_voice_input):
                    should_voice = True
                if should_voice and reply and reply != "...":
                    emotion = "default"
                    if emotion_is_enabled():
                        try:
                            context = await _short_term.get_context(group_id) if _short_term else []
                            st_messages = []
                            for m in context:
                                content = m.content if isinstance(m.content, str) else str(m.content)
                                st_messages.append({"role": m.role, "content": content})
                            emotion, emo_info = await detect_emotion(reply, st_messages, group_id, user_id)
                            logger.info(f"[Emotion] 情绪判定: {emotion} ({emo_info.get('emotion_cn','')}) "
                                       f"conf={emo_info.get('confidence',0):.2f} latency={emo_info.get('latency_ms',0)}ms"
                                       f"{' [降级]' if emo_info.get('fallback') else ''}")
                        except Exception as e:
                            logger.warning(f"[Emotion] 情绪判定失败，用default: {e}")
                    voice_sent = await _send_voice_reply(bot, group_id, reply, emotion=emotion,
                                                            is_private=is_private, user_id=user_id)
            if voice_sent:
                logger.info(f"[AI-Chat] 语音回复已发送，跳过文字回复")
                return
            await bot.send(event, reply)
        except Exception as e:
            logger.error(f"[AI-Chat] 处理消息时出错: {e}", exc_info=True)


    return _handler

_process_queued_message = _create_message_handler()


@group_msg_handler.handle()
async def handle_group_message(event: MessageEvent, bot: Bot):
    user_id = str(event.user_id)
    if user_id == str(event.self_id):
        return
    # 群聊/私聊统一：私聊用 private_{user_id} 作为 group_id，记忆/画像/限速各自独立
    is_private = not isinstance(event, GroupMessageEvent)
    if is_private:
        group_id = f"private_{user_id}"
        group_name = "私聊"
    else:
        group_id = str(event.group_id)
        group_name = getattr(event, "group_name", "") or ""

    text = _extract_text(event.message)
    img_segs = extract_image_segments(event.message, plugin_config.vision_max_images) \
        if plugin_config.vision_enabled else []
    has_image = bool(img_segs)
    # ASR 语音识别（P1）：收到语音消息时转文字，拼接到用户消息交给 LLM
    voice_segs = _extract_voice_segments(event.message) if asr_is_enabled() else []
    is_voice_input = bool(voice_segs)  # 标记是否是语音输入（不管转写成功与否）
    voice_texts = []
    if voice_segs:
        voice_texts = []
        for vs in voice_segs:
            try:
                silk_bytes = _download_voice_silk(vs)
                if silk_bytes:
                    vtext = await transcribe_silk(silk_bytes)
                    if vtext:
                        voice_texts.append(vtext)
            except Exception as e:
                logger.warning(f"[ASR] 语音转写失败: {e}")
        if voice_texts:
            voice_combined = " ".join(voice_texts)
            if text:
                text = f"{text} [语音内容:{voice_combined}]"
            else:
                text = voice_combined
            logger.info(f"[ASR] 语音转写完成({len(voice_segs)}段): {voice_combined[:60]}")
        else:
            # 语音转写失败：设置占位文本，让LLM生成"没听清"的委婉回复
            logger.warning(f"[ASR] 语音转写失败或为空({len(voice_segs)}段)，使用占位文本")
            if text:
                text = f"{text} [语音内容:没听清]"
            else:
                text = "（对方发了一段语音，但我没听清内容）"
    card = getattr(event.sender, "card", "") or ""
    qq_nick = event.sender.nickname or f"用户{user_id}"
    user_name = card or qq_nick
    if _profiles:
        try:
            await _profiles.observe_seen(group_id, user_id, qq_nick, card)
        except Exception:
            pass
    if not text:
        if has_image:
            # 纯图片：用占位文本参与触发判定与历史记录
            text = "看看这张图"
        elif bool(getattr(event, "to_me", False)) or _is_at_me(event):
            # 只@/只唤名字却没打字：让人格自由应答一声
            text = "（对方唤了我一声，却不曾言语）"
        else:
            await _short_term.add_user_message(group_id, user_id, user_name, "[图片/表情]")
            return

    has_voice = bool(voice_texts)
    should, reason = _should_reply(event, text, group_id=group_id, is_private=is_private, has_voice=has_voice)
    logger.info(f"[AI-Chat-Debug] 消息处理: is_private={is_private}, group_id={group_id}, user={user_name}, text='{text[:30]}', should={should}, reason={reason}, to_me={getattr(event, 'to_me', False)}")
    if voice_segs:
        logger.info(f"[ASR] 触发判定: should={should}, reason={reason}, text={text[:40]}, names={list(_all_names())[:5]}")
    # 未触发时：仅当配置“无需触发也识图”且确有图片才放行；否则只记历史不回复（省成本）
    if not should and not (has_image and not plugin_config.vision_require_trigger):
        hist = f"{text} [图片x{len(img_segs)}]" if has_image else text
        await _short_term.add_user_message(group_id, user_id, user_name, hist)
        return

    # 输入安全（P0-2）：越狱/注入检测，命中则不进 LLM，直接回一句俏皮话挡掉
    if _safety:
        blocked, block_reason = _safety.check_input(text)
        if blocked:
            logger.info(f"[AI-Chat] 输入被安全过滤拦截({block_reason})，群{group_id} {user_name}: {text[:40]}")
            await _short_term.add_user_message(group_id, user_id, user_name, text)
            await bot.send(event, "（我听不懂你在说什么啦，换个话题好不好～）")
            return

    # ========== 入队处理（会话级消息队列，解决并发吞消息问题）==========
    message_data = {
        "event": event,
        "bot": bot,
        "user_id": user_id,
        "group_id": group_id,
        "group_name": group_name,
        "is_private": is_private,
        "text": text,
        "img_segs": img_segs,
        "has_image": has_image,
        "voice_segs": voice_segs,
        "is_voice_input": is_voice_input,
        "voice_texts": voice_texts,
        "user_name": user_name,
        "reason": reason,
    }
    queue_mgr = get_queue_manager()
    logger.info(f"[AI-Chat-Debug] 入队处理: queue_mgr={'存在' if queue_mgr else 'None'}, group_id={group_id}")
    if queue_mgr:
        enqueued = await queue_mgr.enqueue(group_id, message_data)
        logger.info(f"[AI-Chat-Debug] 入队结果: enqueued={enqueued}")
        if not enqueued:
            logger.warning(f"[AI-Chat] 会话 {group_id} 队列已满，消息被丢弃")
        return
    # 队列管理器未初始化时，降级为直接处理
    try:
        await _process_queued_message(message_data)
    except FinishedException:
        pass
    return



# ========== 初始化会话级消息队列（并发优化，解决多人对话吞消息问题）==========
try:
    _queue_max_concurrent = int(getattr(plugin_config, "queue_max_concurrent", 5))
    _queue_max_size = int(getattr(plugin_config, "queue_max_size", 10))
    _queue_timeout = float(getattr(plugin_config, "queue_processing_timeout", 120.0))
    init_queue_manager(
        handler=_process_queued_message,
        max_concurrent=_queue_max_concurrent,
        max_queue_size=_queue_max_size,
        processing_timeout=_queue_timeout,
    )
    logger.info(f"[AI-Chat] 会话级消息队列已启动：全局并发={_queue_max_concurrent}, "
                f"单会话队列长度={_queue_max_size}, 单条处理超时={_queue_timeout}s")
except Exception as _queue_init_e:
    logger.warning(f"[AI-Chat] 消息队列初始化失败，降级为直接处理: {_queue_init_e}")


# ========== TTS 语音回复 ==========
# 简化触发：只要消息中包含"我想听"，就输出语音回复
_VOICE_REQUEST_KEYWORDS = [
    "我想听",
]


def _is_voice_request(text: str) -> bool:
    """检测用户消息是否明确要求语音回复（按需触发，不默认发语音）"""
    if not text:
        return False
    text_lower = text.lower()
    for kw in _VOICE_REQUEST_KEYWORDS:
        if kw in text_lower:
            return True
    return False


async def _send_voice_reply(bot: Bot, group_id: str, text: str, emotion: str = "default",
                             is_private: bool = False, user_id: str = "") -> bool:
    """生成AI助手语音（带情绪音色）并发送。群聊发群，私聊发私聊。
    返回 True=发送成功，False=失败（调用方应补发文字）。
    """
    try:
        if not tts_is_enabled():
            return False
        silk_bytes = await synthesize_to_silk(text, emotion=emotion)
        if not silk_bytes or len(silk_bytes) < 100:
            logger.warning("[TTS] 语音合成失败或结果为空")
            return False
        silk_path = save_silk_to_temp(silk_bytes)
        # Windows 路径转 file:/// URL（\ → /）
        file_url = "file:///" + silk_path.replace("\\", "/")
        voice_msg = Message([MessageSegment.record(file=file_url)])
        if is_private and user_id:
            await bot.send_private_msg(user_id=int(user_id), message=voice_msg)
            logger.info(f"[TTS] 语音已发送到私聊{user_id}: {text[:30]} emotion={emotion} ({len(silk_bytes)}字节)")
        else:
            await bot.send_group_msg(group_id=int(group_id), message=voice_msg)
            logger.info(f"[TTS] 语音已发送到群{group_id}: {text[:30]} emotion={emotion} ({len(silk_bytes)}字节)")
        return True
    except Exception as e:
        logger.error(f"[TTS] 发送语音失败: {e}")
        return False


_LT_EXTRACT_INSTRUCTION = """你是一个「长期记忆抽取器」。根据群友的发言和AI的回复，判断是否有值得长期记住的信息。

值得记住的信息包括：
- 群友的身份、职业、年龄、所在地、家庭情况
- 群友的喜好、厌恶、习惯、口头禅
- 群友的重要经历、计划、约定（如"下周去扬州"、"11月考试"）
- 群内重要事件、共识、规则
- 群友与他人的关系变化

不值得记住的：纯闲聊、表情、无信息量的短句、已经知道的重复信息。

要求：
1. content 用简短中文概括，不超过50字，必须是陈述语气（如"用户计划11月去扬州拍照"）。
2. memory_type 从以下选择：用户身份/用户喜好/用户厌恶/用户经历/群内约定/重要事件/其他。
3. importance 1-10：个人核心信息和约定给7-9，普通事件给5-6，琐碎信息给3-4。
4. has_memory 仅当真的有值得记住的新信息时才为 true。

输出 JSON，字段固定：
{"has_memory":bool,"memory_type":str,"content":str,"importance":int}"""


async def _maybe_save_long_term(group_id: str, user_id: str, user_msg: str, ai_reply: str):
    """长期记忆保存（P1增强）：正则快速通道 + LLM智能抽取，带节流和去重。"""
    if not _long_term:
        return
    try:
        # ---- 通道1：正则快速匹配（明确的自我陈述，直接存，不调LLM）----
        patterns = [
            (r"我叫([^\s，。？?！!]{1,20})", "用户姓名"),
            (r"我是([^\s，。？?！!]{2,20})", "用户身份"),
            (r"我喜欢([^\s，。？?！!]{1,20})", "用户喜好"),
            (r"我不喜欢([^\s，。？?！!]{1,20})", "用户厌恶"),
            (r"我住在([^\s，。？?！!]{1,20})", "用户住址"),
            (r"我在([^\s，。？!！]{1,15})工作", "用户工作"),
            (r"我今年(\d{1,2})岁", "用户年龄"),
        ]
        for pattern, mem_type in patterns:
            match = re.search(pattern, user_msg)
            if match:
                captured = match.group(1).strip()
                # 过滤疑问/无意义捕获（如"我是谁"→"谁"）
                if len(captured) < 2 or captured in ("谁", "什么", "啥", "哪", "哪里", "哪个"):
                    continue
                content = f"{mem_type}: {captured}"
                if await _is_duplicate_memory(group_id, content):
                    return
                await _long_term.add(group_id=group_id, user_id=user_id, content=content,
                                     memory_type=mem_type, importance=7)
                logger.info(f"[AI-Chat] 长期记忆(正则): {content}")
                return

        # ---- 通道2：LLM智能抽取（正则没命中时，带节流）----
        now = time.time()
        gap = getattr(plugin_config, "long_term_extract_gap", 120)
        last = _lt_extract_last.get(group_id, 0)
        if now - last < gap:
            return
        if len(user_msg.strip()) < 6:
            return
        llm = get_llm()
        if not llm:
            return
        _lt_extract_last[group_id] = now
        content_input = f"【群友发言】{user_msg[:200]}\n【AI回复】{ai_reply[:150]}"
        result = await llm.chat_json(_LT_EXTRACT_INSTRUCTION, content_input, max_tokens=300)
        if not result or not result.get("has_memory"):
            return
        mem_type = str(result.get("memory_type", "其他")).strip() or "其他"
        content = str(result.get("content", "")).strip()
        importance = int(result.get("importance", 5))
        if not content or len(content) > 100:
            return
        if await _is_duplicate_memory(group_id, content):
            return
        await _long_term.add(group_id=group_id, user_id=user_id, content=content,
                             memory_type=mem_type, importance=importance)
        logger.info(f"[AI-Chat] 长期记忆(LLM): [{mem_type}|重要度{importance}] {content}")
    except Exception as e:
        logger.warning(f"[AI-Chat] 长期记忆保存失败: {e}")


async def _is_duplicate_memory(group_id: str, content: str) -> bool:
    """去重检查：最近20条记忆中，若content有60%以上的词重合则视为重复。"""
    try:
        recent = await _long_term.get_recent(group_id, limit=20)
        content_chars = set(content)
        for m in recent:
            old = m.get("content", "")
            if not old:
                continue
            # 简单字符重合度判断
            old_chars = set(old)
            if not old_chars:
                continue
            overlap = len(content_chars & old_chars) / max(len(content_chars), 1)
            if overlap > 0.7:
                return True
    except Exception:
        pass
    return False


# ========== 管理命令 ==========
clear_cmd = on_command("清空记忆", aliases={"清除记忆", "忘记一切"}, permission=SUPERUSER, priority=10)

@clear_cmd.handle()
async def handle_clear_memory(event: GroupMessageEvent):
    group_id = str(event.group_id)
    await _short_term.clear(group_id)
    if _long_term:
        await _long_term.clear_group(group_id)
    logger.info(f"[AI-Chat] 群{group_id} 记忆已清空")
    await clear_cmd.finish("好啦，我已经把刚才的事都忘了~")


status_cmd = on_command("机器人状态", aliases={"状态", "status"}, permission=SUPERUSER, priority=10)

@status_cmd.handle()
async def handle_status(event: GroupMessageEvent):
    group_id = str(event.group_id)
    context = await _short_term.get_context(group_id)
    long_count = await _long_term.count(group_id) if _long_term else 0
    prof_count = await _profiles.count(group_id) if _profiles else 0
    run_state = "运行中" if _runtime_state.get("reply_enabled", True) else "已暂停"
    import nonebot
    online = len(nonebot.get_bots())
    status_text = (
        f"=== 机器人状态 ===\n"
        f"自动回复: {run_state}\n"
        f"QQ连接: {'在线' if online else '离线'} ({online})\n"
        f"推理模式: {_runtime_state.get('reason_mode', plugin_config.reason_mode)}\n"
        f"人格: {_personality_loader.get_name()}\n"
        f"模型: {plugin_config.deepseek_model} / {plugin_config.deepseek_reasoner_model}\n"
        f"温度: {plugin_config.deepseek_temperature}\n"
        f"短期上下文: {len(context)} 条\n"
        f"长期记忆: {long_count} 条\n"
        f"群友画像: {prof_count} 人\n"
        f"随机回复概率: {plugin_config.random_reply_probability:.0%}\n"
        f"冷却时间: {plugin_config.reply_cooldown}s\n"
    )
    await status_cmd.finish(status_text)


pause_cmd = on_command("暂停机器人", aliases={"暂停回复", "别说话了"}, permission=SUPERUSER, priority=10)

@pause_cmd.handle()
async def handle_pause(event: MessageEvent):
    _runtime_state["reply_enabled"] = False
    _runtime_state["paused_at"] = time.time()
    _save_runtime_state()
    logger.info("[AI-Chat] 超级用户已暂停自动回复")
    await pause_cmd.finish("好嘛，那我先安静一会儿，需要我说话时发「恢复机器人」就行啦🥺")


resume_cmd = on_command("恢复机器人", aliases={"恢复回复", "继续说话"}, permission=SUPERUSER, priority=10)

@resume_cmd.handle()
async def handle_resume(event: MessageEvent):
    _runtime_state["reply_enabled"] = True
    _save_runtime_state()
    logger.info("[AI-Chat] 超级用户已恢复自动回复")
    await resume_cmd.finish("我回来啦😆我又可以说话咯～")


# ========== stop/start all 命令 ==========
stop_all_cmd = on_command("stop", aliases={"stop all"}, permission=SUPERUSER, priority=5)

@stop_all_cmd.handle()
async def handle_stop_all(event: MessageEvent):
    _runtime_state["reply_enabled"] = False
    _runtime_state["paused_at"] = time.time()
    _save_runtime_state()
    logger.info("[AI-Chat] /stop all 已暂停所有回复")
    await stop_all_cmd.finish("已暂停所有回复。用 /start all 恢复。")


start_all_cmd = on_command("start", aliases={"start all"}, permission=SUPERUSER, priority=5)

@start_all_cmd.handle()
async def handle_start_all(event: MessageEvent):
    _runtime_state["reply_enabled"] = True
    _save_runtime_state()
    logger.info("[AI-Chat] /start all 已恢复所有回复")
    await start_all_cmd.finish("已恢复所有回复。")


reload_cmd = on_command("重载人格", aliases={"刷新人格", "reload"}, permission=SUPERUSER, priority=10)

@reload_cmd.handle()
async def handle_reload_personality(event: GroupMessageEvent):
    try:
        _personality_loader.reload()
        name = _personality_loader.get_name()
        logger.info(f"[AI-Chat] 人格已重载: {name}")
        await reload_cmd.finish(f"人格已刷新，现在是「{name}」~")
    except Exception as e:
        await reload_cmd.finish(f"人格重载失败: {e}")


# ========== 人格切换命令 ==========
def _list_personalities() -> list[dict]:
    """列出所有可用人格"""
    import yaml as _yaml
    pdir = Path(_personality_loader.personality_dir)
    results = []
    for f in sorted(pdir.glob("*.yaml")):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = _yaml.safe_load(fh)
            name = data.get("basic", {}).get("name", f.stem)
            aliases = data.get("basic", {}).get("aliases", [])
            traits = data.get("personality_traits", [])[:3]
            results.append({
                "file": f.stem,
                "name": name,
                "aliases": aliases,
                "traits": traits,
                "active": (f.stem == _personality_loader.personality_file),
            })
        except Exception:
            continue
    return results


def _find_personality(query: str) -> Optional[str]:
    """根据名称/别名/文件名查找人格文件"""
    query = query.strip().lower()
    if not query:
        return None
    for p in _list_personalities():
        if p["file"].lower() == query:
            return p["file"]
        if p["name"].lower() == query:
            return p["file"]
        if any(a.lower() == query for a in p["aliases"]):
            return p["file"]
        if query in p["name"].lower():
            return p["file"]
    return None


personality_cmd = on_command("切换人格", aliases={"人格", "persona", "人格切换", "换人格"},
                              priority=10)

# 人格切换白名单（只有这些 QQ 号能切换人格）
PERSONALITY_SWITCH_WHITELIST = {"your_admin_qq_number"}

@personality_cmd.handle()
async def handle_switch_personality(event: MessageEvent, args: Message = CommandArg()):
    # QQ 号白名单检查
    user_id = str(event.user_id)
    if user_id not in PERSONALITY_SWITCH_WHITELIST:
        await personality_cmd.finish("切换人格？你也配？😒 只有玉楼能切换，笨蛋。")
        return

    arg_text = args.extract_plain_text().strip()

    # 不带参数：列出所有人格
    if not arg_text:
        personalities = _list_personalities()
        if not personalities:
            await personality_cmd.finish("没有找到任何人格文件~")
            return
        lines = ["=== 可用人格 ==="]
        for p in personalities:
            marker = "⭐" if p["active"] else "  "
            alias_str = f"（别名：{'、'.join(p['aliases'])}）" if p["aliases"] else ""
            traits_str = f"【{'、'.join(p['traits'])}】" if p["traits"] else ""
            lines.append(f"{marker} {p['name']}{alias_str} {traits_str}")
            if p["active"]:
                lines.append(f"   ↑ 当前激活")
        lines.append("\n用法：切换人格 <名称>")
        await personality_cmd.finish("\n".join(lines))
        return

    # 带参数：切换人格
    target_file = _find_personality(arg_text)
    if not target_file:
        personalities = _list_personalities()
        available = "、".join(p["name"] for p in personalities)
        await personality_cmd.finish(f"找不到「{arg_text}」这个人格呢~\n可用的人格有：{available}")
        return

    # 已经是当前人格
    if target_file == _personality_loader.personality_file:
        current_name = _personality_loader.get_name()
        await personality_cmd.finish(f"人家现在就是「{current_name}」呀，不用切换啦~")
        return

    # 执行切换
    try:
        old_name = _personality_loader.get_name()
        _personality_loader.personality_file = target_file
        _personality_loader.reload()
        new_name = _personality_loader.get_name()

        # 同步运行时配置
        try:
            plugin_config.personality_file = target_file
        except Exception:
            pass

        # 写回 .env（重启后保持）
        try:
            env_path = Path(plugin_config.personality_dir).parent / ".env"
            if env_path.exists():
                with open(env_path, "r", encoding="utf-8") as f:
                    content = f.read()
                content = re.sub(r'^PERSONALITY_FILE=.*$', f'PERSONALITY_FILE={target_file}',
                                 content, flags=re.MULTILINE)
                with open(env_path, "w", encoding="utf-8") as f:
                    f.write(content)
        except Exception as e:
            logger.warning(f"[Personality] .env 写回失败: {e}")

        logger.info(f"[Personality] 人格切换: {old_name} -> {new_name}")
        await personality_cmd.finish(
            f"人格已切换！\n从「{old_name}」变成了「{new_name}」~\n"
            f"接下来人家会用新的身份和你聊天哦~"
        )
    except Exception as e:
        await personality_cmd.finish(f"人格切换失败: {e}")


memories_cmd = on_command("查看记忆", aliases={"记忆列表", "memories"}, permission=SUPERUSER, priority=10)

@memories_cmd.handle()
async def handle_list_memories(event: GroupMessageEvent, args: Message = CommandArg()):
    group_id = str(event.group_id)
    if not _long_term:
        await memories_cmd.finish("长期记忆未启用")
        return
    limit = 10
    arg_text = args.extract_plain_text().strip()
    if arg_text.isdigit():
        limit = int(arg_text)
    memories = await _long_term.get_recent(group_id, limit=limit)
    if not memories:
        await memories_cmd.finish("还没有长期记忆呢~")
        return
    lines = [f"=== 最近 {len(memories)} 条记忆 ==="]
    for m in memories:
        lines.append(f"[{m['id']}] (重要度{m['importance']}) {m['content']}")
    await memories_cmd.finish("\n".join(lines))


del_mem_cmd = on_command("删除记忆", aliases={"忘掉", "delmem"}, permission=SUPERUSER, priority=10)

@del_mem_cmd.handle()
async def handle_delete_memory(event: GroupMessageEvent, args: Message = CommandArg()):
    if not _long_term:
        await del_mem_cmd.finish("长期记忆未启用")
        return
    arg_text = args.extract_plain_text().strip()
    if not arg_text.isdigit():
        await del_mem_cmd.finish("用法：删除记忆 <记忆ID>，用 查看记忆 看ID")
        return
    mem_id = int(arg_text)
    await _long_term.delete(mem_id)
    await del_mem_cmd.finish(f"已忘掉记忆 #{mem_id}")


logger.info("[AI-Chat] 插件加载完成")
