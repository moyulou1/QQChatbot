"""
AI 群聊插件配置
从 NoneBot 环境变量 / .env 读取配置
"""
from __future__ import annotations

from nonebot import get_driver
from pydantic import BaseModel, Field, field_validator


class PluginConfig(BaseModel):
    """插件配置"""

    @field_validator("tts_minimax_group_id", mode="before")
    @classmethod
    def _force_group_id_str(cls, v):
        """Group ID 是纯数字，可能被解析成 int，强制转字符串"""
        if v is None:
            return ""
        return str(v)
    # DeepSeek
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-chat"
    deepseek_reasoner_model: str = "deepseek-reasoner"
    # 推理模式：auto=按问题自动路由 / chat=始终通用模型 / reasoner=始终推理模型
    reason_mode: str = "auto"
    deepseek_temperature: float = 0.8
    deepseek_max_tokens: int = 1024
    deepseek_timeout: int = 60

    # 视觉识图（多模态 VLM）。key/base 留空时自动复用上面的 DeepSeek
    vision_enabled: bool = True
    vision_api_key: str = ""
    vision_base_url: str = ""
    vision_model: str = "deepseek-v4-flash-vision-exp"
    vision_temperature: float = 0.7
    vision_max_tokens: int = 1024
    vision_max_images: int = 2          # 单条消息最多识别几张图
    vision_max_side: int = 1280         # 压缩后最长边像素
    vision_jpeg_quality: int = 85       # JPEG 质量
    vision_max_bytes: int = 4194304     # 单图压缩后字节上限（4MB）
    vision_require_trigger: bool = True # 仅在本来就会回复(@/唤名/关键词/概率)时才识图，省成本

    # 人格
    personality_file: str = "default"
    personality_dir: str = "../personalities"

    # 回复策略
    reply_on_at: bool = True
    reply_on_name: bool = True
    trigger_keywords: list[str] = Field(default_factory=lambda: ["在吗", "在不在", "有人吗", "聊聊", "说句话"])
    random_reply_probability: float = 0.05
    reply_cooldown: int = 3
    max_message_length: int = 500
    context_rounds: int = 10
    enable_long_term_memory: bool = True
    memory_db_path: str = "../data/memory/long_term.db"
    long_term_extract_gap: int = 120     # LLM 智能抽取的最小间隔（秒），避免每轮都调LLM烧token
    # TTS 语音合成（P1）
    tts_enabled: bool = False
    tts_provider: str = "local"  # local=本地GPT-SoVITS / fish=FishAudio云端 / volc=火山引擎云端
    tts_api_url: str = "http://127.0.0.1:9880/tts"
    tts_ref_audio: str = r"your_reference_audio_path.wav"
    tts_ref_text: str = "你若是因此想自我了断，我也管不着。"
    tts_speed: float = 1.0
    tts_silk_encoder: str = r"F:\qq_chatbot\tools\silk\silk_v3_encoder.exe"
    tts_cache_dir: str = r"F:\qq_chatbot\data\tts_cache"
    tts_trigger_mode: str = "on_demand"  # off=关闭 / on_demand=明确要求语音时才发 / always=全部回复都发语音
    tts_timeout: int = 60
    tts_max_text_len: int = 100
    # Minimax 云端 TTS（国内可访问，语音克隆）
    tts_minimax_group_id: str = ""
    tts_minimax_api_key: str = ""
    tts_minimax_voice_id: str = ""
    tts_minimax_api_url: str = "https://api.minimax.cn/v1/t2a_v2"
    tts_minimax_model: str = "speech-2.8-hd"
    # Fish Audio 云端 TTS
    tts_fish_api_key: str = ""
    tts_fish_voice_id: str = ""
    tts_fish_api_url: str = "https://api.fish.audio/v1/tts"
    # 火山引擎云端 TTS
    tts_volc_app_id: str = ""
    tts_volc_access_token: str = ""
    tts_volc_cluster: str = "volcano_tts"
    tts_volc_voice_type: str = "BV115_streaming"  # 古风少御
    tts_volc_api_url: str = "https://openspeech.bytedance.com/api/v1/tts"
    # ASR 语音识别（P1）
    asr_enabled: bool = False
    asr_model_size: str = "base"       # tiny(75MB)/base(150MB)/small(500MB)，base中文够用
    asr_device: str = "cpu"             # cpu / cuda（A卡只能cpu）
    asr_compute_type: str = "int8"      # int8 省内存，float16更快但需GPU
    asr_language: str = "zh"
    asr_silk_decoder: str = r"F:\qq_chatbot\tools\silk\silk_v3_decoder.exe"
    asr_cache_dir: str = r"F:\qq_chatbot\data\asr_cache"
    # 人格一致性校验（P1 防OOC）
    personality_check_enabled: bool = True
    personality_check_rule: bool = True      # 规则检测（零成本，默认开）
    personality_check_llm: bool = False      # LLM深度校验（有成本，默认关）
    personality_check_llm_gap: int = 10      # 每N条消息LLM校验1次
    personality_check_retry: bool = True      # OOC时是否带修正提示重试
    personality_check_max_emoji: int = 3      # 单条消息最多emoji数

    # 情绪判定（纯LLM，最高准确率，每轮调用）
    emotion_enabled: bool = False            # 情绪判定总开关
    emotion_model: str = "deepseek-chat"     # 情绪判定用的模型（可用deepseek-reasoner）
    emotion_context_rounds: int = 5           # 带入最近几轮对话上下文
    emotion_timeout: int = 30                  # LLM调用超时（秒）
    emotion_fallback: str = "calm"             # LLM失败时的降级情绪

    # 音色管理（多参考音频池 + 情绪映射 + 变调后处理）
    voice_enabled: bool = False                # 音色管理总开关
    voice_default_character: str = "lin_pianpian"  # 默认角色
    voice_pitch_shift: bool = True             # 是否启用变调后处理（需要librosa）

    # 群友画像（自动记录每个群友的名字/年龄/性别等）
    enable_user_profile: bool = True
    profile_extract_gap: int = 90        # 同一群友两次 LLM 抽取的最小间隔（秒），省 token
    profile_max_inject: int = 12         # 每次回复最多注入多少位群友的画像
    state_file: str = "../data/memory/state.json"   # 运行状态（如暂停开关）持久化

    # 内容安全与防注入（P0-2）
    safety_input_enabled: bool = True    # 输入侧越狱/注入检测
    safety_output_enabled: bool = True   # 输出侧敏感词过滤

    # 速率限制（P0-3）：滑动窗口，防单用户刷屏 / 防群级刷爆 token
    rate_limit_user_window: int = 10     # 单用户统计窗口（秒）
    rate_limit_user_max: int = 3         # 单用户窗口内最多触发次数
    rate_limit_group_window: int = 60    # 全群统计窗口（秒）
    rate_limit_group_max: int = 25       # 全群窗口内最多 LLM 调用次数

    # 群管理
    enabled_groups: list[str] = Field(default_factory=list)
    disabled_groups: list[str] = Field(default_factory=list)
    blacklist_users: list[str] = Field(default_factory=list)

    # 超级用户（可以执行管理命令）
    superusers: list[str] = Field(default_factory=list)

    # Web 控制台
    console_enabled: bool = True
    console_host: str = "127.0.0.1"
    console_port: int = 18080
    console_token: str = ""


# 从 NoneBot driver 配置中读取
_driver_config = get_driver().config

plugin_config = PluginConfig(
    deepseek_api_key=getattr(_driver_config, "deepseek_api_key", ""),
    deepseek_base_url=getattr(_driver_config, "deepseek_base_url", "https://api.deepseek.com/v1"),
    deepseek_model=getattr(_driver_config, "deepseek_model", "deepseek-chat"),
    deepseek_reasoner_model=getattr(_driver_config, "deepseek_reasoner_model", "deepseek-reasoner"),
    reason_mode=getattr(_driver_config, "reason_mode", "auto"),
    deepseek_temperature=getattr(_driver_config, "deepseek_temperature", 0.8),
    deepseek_max_tokens=getattr(_driver_config, "deepseek_max_tokens", 1024),
    deepseek_timeout=getattr(_driver_config, "deepseek_timeout", 60),
    vision_enabled=getattr(_driver_config, "vision_enabled", True),
    vision_api_key=getattr(_driver_config, "vision_api_key", ""),
    vision_base_url=getattr(_driver_config, "vision_base_url", ""),
    vision_model=getattr(_driver_config, "vision_model", "deepseek-v4-flash-vision-exp"),
    vision_temperature=getattr(_driver_config, "vision_temperature", 0.7),
    vision_max_tokens=getattr(_driver_config, "vision_max_tokens", 1024),
    vision_max_images=getattr(_driver_config, "vision_max_images", 2),
    vision_max_side=getattr(_driver_config, "vision_max_side", 1280),
    vision_jpeg_quality=getattr(_driver_config, "vision_jpeg_quality", 85),
    vision_max_bytes=getattr(_driver_config, "vision_max_bytes", 4194304),
    vision_require_trigger=getattr(_driver_config, "vision_require_trigger", True),
    personality_file=getattr(_driver_config, "personality_file", "default"),
    personality_dir=getattr(_driver_config, "personality_dir", "../personalities"),
    reply_on_at=getattr(_driver_config, "reply_on_at", True),
    reply_on_name=getattr(_driver_config, "reply_on_name", True),
    trigger_keywords=getattr(_driver_config, "trigger_keywords", ["在吗", "在不在", "有人吗", "聊聊", "说句话"]),
    random_reply_probability=getattr(_driver_config, "random_reply_probability", 0.05),
    reply_cooldown=getattr(_driver_config, "reply_cooldown", 3),
    max_message_length=getattr(_driver_config, "max_message_length", 500),
    context_rounds=getattr(_driver_config, "context_rounds", 10),
    enable_long_term_memory=getattr(_driver_config, "enable_long_term_memory", True),
    memory_db_path=getattr(_driver_config, "memory_db_path", "../data/memory/long_term.db"),
    long_term_extract_gap=getattr(_driver_config, "long_term_extract_gap", 120),
    tts_enabled=getattr(_driver_config, "tts_enabled", False),
    tts_provider=getattr(_driver_config, "tts_provider", "local"),
    tts_api_url=getattr(_driver_config, "tts_api_url", "http://127.0.0.1:9880/tts"),
    tts_ref_audio=getattr(_driver_config, "tts_ref_audio", r"your_reference_audio_path.wav"),
    tts_ref_text=getattr(_driver_config, "tts_ref_text", "你若是因此想自我了断，我也管不着。"),
    tts_speed=getattr(_driver_config, "tts_speed", 1.0),
    tts_silk_encoder=getattr(_driver_config, "tts_silk_encoder", r"F:\qq_chatbot\tools\silk\silk_v3_encoder.exe"),
    tts_cache_dir=getattr(_driver_config, "tts_cache_dir", r"F:\qq_chatbot\data\tts_cache"),
    tts_trigger_mode=getattr(_driver_config, "tts_trigger_mode", "on_demand"),
    tts_timeout=getattr(_driver_config, "tts_timeout", 60),
    tts_max_text_len=getattr(_driver_config, "tts_max_text_len", 100),
    tts_minimax_group_id=getattr(_driver_config, "tts_minimax_group_id", ""),
    tts_minimax_api_key=getattr(_driver_config, "tts_minimax_api_key", ""),
    tts_minimax_voice_id=getattr(_driver_config, "tts_minimax_voice_id", ""),
    tts_minimax_api_url=getattr(_driver_config, "tts_minimax_api_url", "https://api.minimax.cn/v1/t2a_v2"),
    tts_minimax_model=getattr(_driver_config, "tts_minimax_model", "speech-2.8-hd"),
    tts_fish_api_key=getattr(_driver_config, "tts_fish_api_key", ""),
    tts_fish_voice_id=getattr(_driver_config, "tts_fish_voice_id", ""),
    tts_fish_api_url=getattr(_driver_config, "tts_fish_api_url", "https://api.fish.audio/v1/tts"),
    tts_volc_app_id=getattr(_driver_config, "tts_volc_app_id", ""),
    tts_volc_access_token=getattr(_driver_config, "tts_volc_access_token", ""),
    tts_volc_cluster=getattr(_driver_config, "tts_volc_cluster", "volcano_tts"),
    tts_volc_voice_type=getattr(_driver_config, "tts_volc_voice_type", "BV115_streaming"),
    tts_volc_api_url=getattr(_driver_config, "tts_volc_api_url", "https://openspeech.bytedance.com/api/v1/tts"),
    asr_enabled=getattr(_driver_config, "asr_enabled", False),
    asr_model_size=getattr(_driver_config, "asr_model_size", "base"),
    asr_device=getattr(_driver_config, "asr_device", "cpu"),
    asr_compute_type=getattr(_driver_config, "asr_compute_type", "int8"),
    asr_language=getattr(_driver_config, "asr_language", "zh"),
    asr_silk_decoder=getattr(_driver_config, "asr_silk_decoder", r"F:\qq_chatbot\tools\silk\silk_v3_decoder.exe"),
    asr_cache_dir=getattr(_driver_config, "asr_cache_dir", r"F:\qq_chatbot\data\asr_cache"),
    personality_check_enabled=getattr(_driver_config, "personality_check_enabled", True),
    personality_check_rule=getattr(_driver_config, "personality_check_rule", True),
    personality_check_llm=getattr(_driver_config, "personality_check_llm", False),
    personality_check_llm_gap=getattr(_driver_config, "personality_check_llm_gap", 10),
    personality_check_retry=getattr(_driver_config, "personality_check_retry", True),
    personality_check_max_emoji=getattr(_driver_config, "personality_check_max_emoji", 3),
    emotion_enabled=getattr(_driver_config, "emotion_enabled", False),
    emotion_model=getattr(_driver_config, "emotion_model", "deepseek-chat"),
    emotion_context_rounds=getattr(_driver_config, "emotion_context_rounds", 5),
    emotion_timeout=getattr(_driver_config, "emotion_timeout", 30),
    emotion_fallback=getattr(_driver_config, "emotion_fallback", "calm"),
    voice_enabled=getattr(_driver_config, "voice_enabled", False),
    voice_default_character=getattr(_driver_config, "voice_default_character", "lin_pianpian"),
    voice_pitch_shift=getattr(_driver_config, "voice_pitch_shift", True),
    enable_user_profile=getattr(_driver_config, "enable_user_profile", True),
    profile_extract_gap=getattr(_driver_config, "profile_extract_gap", 90),
    profile_max_inject=getattr(_driver_config, "profile_max_inject", 12),
    state_file=getattr(_driver_config, "state_file", "../data/memory/state.json"),
    safety_input_enabled=getattr(_driver_config, "safety_input_enabled", True),
    safety_output_enabled=getattr(_driver_config, "safety_output_enabled", True),
    rate_limit_user_window=getattr(_driver_config, "rate_limit_user_window", 10),
    rate_limit_user_max=getattr(_driver_config, "rate_limit_user_max", 3),
    rate_limit_group_window=getattr(_driver_config, "rate_limit_group_window", 60),
    rate_limit_group_max=getattr(_driver_config, "rate_limit_group_max", 25),
    enabled_groups=getattr(_driver_config, "enabled_groups", []),
    disabled_groups=getattr(_driver_config, "disabled_groups", []),
    blacklist_users=getattr(_driver_config, "blacklist_users", []),
    superusers=getattr(_driver_config, "superusers", []),
    console_enabled=getattr(_driver_config, "console_enabled", True),
    console_host=getattr(_driver_config, "console_host", "127.0.0.1"),
    console_port=getattr(_driver_config, "console_port", 18080),
    console_token=getattr(_driver_config, "console_token", ""),
)

# 处理 trigger_keywords 可能是字符串的情况
if isinstance(plugin_config.trigger_keywords, str):
    plugin_config.trigger_keywords = [
        kw.strip() for kw in plugin_config.trigger_keywords.split(",") if kw.strip()
    ]
