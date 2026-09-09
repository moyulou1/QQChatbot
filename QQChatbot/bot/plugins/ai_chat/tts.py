"""
AI助手 TTS 模块（多 Provider 架构）
支持四种语音合成 Provider，控制台一键切换：
  - local:   本地 GPT-SoVITS API（AI助手训练模型，CPU/GPU推理）
  - minimax: Minimax 云端 API（语音克隆，国内可访问，低延迟）
  - fish:    Fish Audio 云端 API（语音克隆，国内需代理）
  - volc:    火山引擎云端 API（预设音色，稳定低延迟）

通用流程：Provider合成WAV → PCM → 重采样 → silk v3（QQ语音格式）
带文件缓存，相同文本+情绪+Provider不重复合成
"""
import asyncio
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import uuid
import wave
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

import numpy as np
import requests
from nonebot import logger

# ---------------- 全局配置（运行时从 plugin_config 注入） ----------------
_tts_config = {
    "enabled": False,
    "provider": "local",  # local / minimax / fish / volc
    # 本地 GPT-SoVITS
    "api_url": "http://127.0.0.1:9880/tts",
    "ref_audio": r"your_reference_audio_path.wav",
    "ref_text": "你若是因此想自我了断，我也管不着。",
    "speed": 1.0,
    # Minimax（国内可访问，语音克隆）
    "minimax_group_id": "",
    "minimax_api_key": "",
    "minimax_voice_id": "",
    "minimax_api_url": "https://api.minimax.cn/v1/t2a_v2",
    "minimax_model": "speech-2.8-hd",
    # Fish Audio
    "fish_api_key": "",
    "fish_voice_id": "",
    "fish_api_url": "https://api.fish.audio/v1/tts",
    # 火山引擎
    "volc_app_id": "",
    "volc_access_token": "",
    "volc_cluster": "volcano_tts",
    "volc_voice_type": "BV115_streaming",  # 古风少御
    "volc_api_url": "https://openspeech.bytedance.com/api/v1/tts",
    # 通用
    "silk_encoder": r"F:\qq_chatbot\tools\silk\silk_v3_encoder.exe",
    "cache_dir": r"F:\qq_chatbot\data\tts_cache",
    "timeout": 60,
    "max_text_len": 100,
}

_current_provider: Optional["TTSProvider"] = None


# ==================== Provider 基类 ====================
class TTSProvider(ABC):
    """TTS Provider 抽象基类，子类实现 synthesize_wav"""

    @abstractmethod
    def synthesize_wav(self, text: str, emotion: Optional[str] = None) -> bytes:
        """合成语音，返回 WAV 二进制数据"""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider 名称"""
        ...


# ==================== 本地 GPT-SoVITS Provider ====================
class LocalGPTSoVITSProvider(TTSProvider):
    """调用本地 GPT-SoVITS API 合成AI助手声音"""

    @property
    def name(self) -> str:
        return "local"

    def synthesize_wav(self, text: str, emotion: Optional[str] = None) -> bytes:
        clean = _clean_text(text)
        if not clean:
            raise ValueError("文本为空")
        params = _get_voice_params(emotion)
        payload = {
            "text": clean,
            "text_lang": "zh",
            "ref_audio_path": params["ref_audio"],
            "prompt_text": params["ref_text"],
            "prompt_lang": "zh",
            "media_type": "wav",
            "speed_factor": params["speed"],
        }
        resp = requests.post(_tts_config["api_url"], json=payload, timeout=_tts_config["timeout"])
        if resp.status_code != 200 or len(resp.content) < 1000:
            raise RuntimeError(f"GPT-SoVITS API失败: status={resp.status_code}, body={resp.text[:200]}")
        wav_bytes = resp.content
        # 变调+音量后处理
        if params.get("from_voice_manager") and (
            abs(params.get("pitch", 0)) > 0.01 or abs(params.get("volume", 1.0) - 1.0) > 0.01
        ):
            try:
                from . import voice_manager
                wav_bytes = voice_manager.apply_pitch_shift(wav_bytes, params["pitch"], params["volume"])
            except Exception as e:
                logger.warning(f"[TTS] 变调后处理失败: {e}")
        return wav_bytes


# ==================== Minimax Provider（国内可访问，语音克隆） ====================
class MinimaxProvider(TTSProvider):
    """调用 Minimax 云端 API 合成语音（支持语音克隆，国内可访问）"""

    @property
    def name(self) -> str:
        return "minimax"

    def synthesize_wav(self, text: str, emotion: Optional[str] = None) -> bytes:
        clean = _clean_text(text)
        if not clean:
            raise ValueError("文本为空")
        group_id = _tts_config["minimax_group_id"]
        api_key = _tts_config["minimax_api_key"]
        voice_id = _tts_config["minimax_voice_id"]
        if not group_id or not api_key:
            raise RuntimeError("Minimax Group ID / API Key 未配置")
        if not voice_id:
            raise RuntimeError("Minimax Voice ID 未配置（请先运行 minimax_clone_voice.py 克隆音色）")

        url = f"{_tts_config['minimax_api_url']}?GroupId={group_id}"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": _tts_config["minimax_model"],
            "text": clean,
            "stream": False,
            "voice_setting": {
                "voice_id": voice_id,
                "speed": _tts_config["speed"],
                "vol": 1.0,
                "pitch": 0,
            },
            "audio_setting": {
                "sample_rate": 32000,
                "bitrate": 128000,
                "format": "wav",
            },
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=_tts_config["timeout"])
        if resp.status_code != 200:
            raise RuntimeError(f"Minimax API失败: status={resp.status_code}, body={resp.text[:300]}")
        result = resp.json()
        # Minimax返回的audio可能是：hex编码的WAV二进制、base64编码、或音频URL
        audio_data = result.get("data", {}).get("audio") or result.get("audio")
        if not audio_data:
            raise RuntimeError(f"Minimax返回中没有audio数据: {json.dumps(result, ensure_ascii=False)[:300]}")
        # 判断类型并解码
        if isinstance(audio_data, str):
            if audio_data.startswith("http"):
                # URL类型，需要下载
                audio_resp = requests.get(audio_data, timeout=_tts_config["timeout"])
                if audio_resp.status_code != 200 or len(audio_resp.content) < 1000:
                    raise RuntimeError(f"Minimax音频下载失败: status={audio_resp.status_code}, size={len(audio_resp.content)}")
                wav_bytes = audio_resp.content
            elif all(c in "0123456789abcdefABCDEF" for c in audio_data[:200]):
                # hex编码的WAV二进制（Minimax默认返回格式）
                wav_bytes = bytes.fromhex(audio_data)
            else:
                # 尝试base64解码
                import base64
                try:
                    wav_bytes = base64.b64decode(audio_data)
                except Exception:
                    raise RuntimeError(f"Minimax返回audio格式无法识别，长度={len(audio_data)}")
        else:
            raise RuntimeError(f"Minimax返回audio类型异常: {type(audio_data)}")
        if len(wav_bytes) < 1000:
            raise RuntimeError(f"Minimax合成音频过小: {len(wav_bytes)}字节")
        return wav_bytes


# ==================== Fish Audio Provider ====================
class FishAudioProvider(TTSProvider):
    """调用 Fish Audio 云端 API 合成语音（支持语音克隆）"""

    @property
    def name(self) -> str:
        return "fish"

    def synthesize_wav(self, text: str, emotion: Optional[str] = None) -> bytes:
        clean = _clean_text(text)
        if not clean:
            raise ValueError("文本为空")
        api_key = _tts_config["fish_api_key"]
        if not api_key:
            raise RuntimeError("Fish Audio API Key 未配置")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        # 如果配置了 voice_id（预上传的克隆声音），直接用
        voice_id = _tts_config["fish_voice_id"]
        if voice_id:
            payload = {
                "text": clean,
                "reference_id": voice_id,
                "format": "wav",
                "latency": "normal",
                "speed": _tts_config["speed"],
            }
        else:
            # 用参考音频直接合成（base64编码）
            ref_audio = _tts_config["ref_audio"]
            if not os.path.exists(ref_audio):
                raise RuntimeError(f"参考音频不存在: {ref_audio}")
            import base64
            with open(ref_audio, "rb") as f:
                ref_b64 = base64.b64encode(f.read()).decode("utf-8")
            payload = {
                "text": clean,
                "references": [
                    {
                        "audio": ref_b64,
                        "text": _tts_config["ref_text"],
                    }
                ],
                "format": "wav",
                "latency": "normal",
                "speed": _tts_config["speed"],
            }

        resp = requests.post(_tts_config["fish_api_url"], headers=headers, json=payload, timeout=_tts_config["timeout"])
        if resp.status_code != 200 or len(resp.content) < 1000:
            raise RuntimeError(f"Fish Audio API失败: status={resp.status_code}, body={resp.text[:300]}")
        return resp.content


# ==================== 火山引擎 Provider ====================
class VolcengineProvider(TTSProvider):
    """调用火山引擎云端 API 合成语音（预设音色）"""

    @property
    def name(self) -> str:
        return "volc"

    def synthesize_wav(self, text: str, emotion: Optional[str] = None) -> bytes:
        clean = _clean_text(text)
        if not clean:
            raise ValueError("文本为空")
        app_id = _tts_config["volc_app_id"]
        access_token = _tts_config["volc_access_token"]
        if not app_id or not access_token:
            raise RuntimeError("火山引擎 app_id / access_token 未配置")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer; {access_token}",
        }

        # 情绪→音色映射（火山引擎预设音色，不同情绪用不同音色）
        voice_type = _emotion_to_volc_voice(emotion)

        payload = {
            "app": {
                "appid": app_id,
                "token": access_token,
                "cluster": _tts_config["volc_cluster"],
            },
            "user": {
                "uid": "qq_chatbot",
            },
            "audio": {
                "voice_type": voice_type,
                "encoding": "wav",
                "speed_ratio": _tts_config["speed"],
                "volume_ratio": 1.0,
                "pitch_ratio": 1.0,
            },
            "request": {
                "reqid": str(uuid.uuid4()),
                "text": clean,
                "text_type": "plain",
                "operation": "query",
            },
        }

        resp = requests.post(_tts_config["volc_api_url"], headers=headers, json=payload, timeout=_tts_config["timeout"])
        if resp.status_code != 200:
            raise RuntimeError(f"火山引擎API失败: status={resp.status_code}, body={resp.text[:300]}")
        result = resp.json()
        if result.get("code") != 3000:
            raise RuntimeError(f"火山引擎返回错误: code={result.get('code')}, msg={result.get('message')}")
        # 火山引擎返回 base64 编码的音频
        import base64
        audio_b64 = result.get("data", "")
        if not audio_b64:
            raise RuntimeError("火山引擎返回音频数据为空")
        return base64.b64decode(audio_b64)


def _emotion_to_volc_voice(emotion: Optional[str]) -> str:
    """情绪→火山引擎音色映射"""
    default_voice = _tts_config["volc_voice_type"]
    mapping = {
        "happy": "BV064_streaming",      # 小萝莉（开心用可爱音）
        "excited": "zh_female_qiaopinv_uranus_bigtts",  # 俏皮女声
        "sad": "BV104_streaming",         # 温柔淑女（悲伤用温柔音）
        "angry": "BV115_streaming",       # 古风少御（生气用清冷音）
        "calm": "BV115_streaming",        # 古风少御
        "coquettish": "zh_female_qiaopinv_uranus_bigtts",  # 俏皮女声
        "surprised": "BV064_streaming",   # 小萝莉
        "thinking": "BV115_streaming",    # 古风少御
        "gentle": "BV104_streaming",      # 温柔淑女
        "playful": "BV064_streaming",     # 小萝莉
    }
    return mapping.get(emotion or "default", default_voice)


# ==================== 工厂函数 ====================
def get_provider() -> TTSProvider:
    """根据配置获取当前 TTS Provider（单例）"""
    global _current_provider
    provider_name = _tts_config["provider"]
    if _current_provider is None or _current_provider.name != provider_name:
        if provider_name == "minimax":
            _current_provider = MinimaxProvider()
        elif provider_name == "fish":
            _current_provider = FishAudioProvider()
        elif provider_name == "volc":
            _current_provider = VolcengineProvider()
        else:
            _current_provider = LocalGPTSoVITSProvider()
        logger.info(f"[TTS] Provider切换为: {_current_provider.name}")
    return _current_provider


def reset_provider():
    """重置 Provider 单例（配置变更后调用）"""
    global _current_provider
    _current_provider = None


# ==================== 配置注入 ====================
def configure_tts(cfg):
    """从 plugin_config 注入配置"""
    _tts_config["enabled"] = getattr(cfg, "tts_enabled", False)
    _tts_config["provider"] = getattr(cfg, "tts_provider", "local")
    # 本地
    _tts_config["api_url"] = getattr(cfg, "tts_api_url", _tts_config["api_url"])
    _tts_config["ref_audio"] = getattr(cfg, "tts_ref_audio", _tts_config["ref_audio"])
    _tts_config["ref_text"] = getattr(cfg, "tts_ref_text", _tts_config["ref_text"])
    _tts_config["speed"] = getattr(cfg, "tts_speed", 1.0)
    # Minimax（国内可访问，语音克隆）
    _tts_config["minimax_group_id"] = getattr(cfg, "tts_minimax_group_id", "")
    _tts_config["minimax_api_key"] = getattr(cfg, "tts_minimax_api_key", "")
    _tts_config["minimax_voice_id"] = getattr(cfg, "tts_minimax_voice_id", "")
    _tts_config["minimax_api_url"] = getattr(cfg, "tts_minimax_api_url", _tts_config["minimax_api_url"])
    _tts_config["minimax_model"] = getattr(cfg, "tts_minimax_model", _tts_config["minimax_model"])
    # Fish Audio
    _tts_config["fish_api_key"] = getattr(cfg, "tts_fish_api_key", "")
    _tts_config["fish_voice_id"] = getattr(cfg, "tts_fish_voice_id", "")
    _tts_config["fish_api_url"] = getattr(cfg, "tts_fish_api_url", _tts_config["fish_api_url"])
    # 火山引擎
    _tts_config["volc_app_id"] = getattr(cfg, "tts_volc_app_id", "")
    _tts_config["volc_access_token"] = getattr(cfg, "tts_volc_access_token", "")
    _tts_config["volc_cluster"] = getattr(cfg, "tts_volc_cluster", _tts_config["volc_cluster"])
    _tts_config["volc_voice_type"] = getattr(cfg, "tts_volc_voice_type", _tts_config["volc_voice_type"])
    _tts_config["volc_api_url"] = getattr(cfg, "tts_volc_api_url", _tts_config["volc_api_url"])
    # 通用
    _tts_config["silk_encoder"] = getattr(cfg, "tts_silk_encoder", _tts_config["silk_encoder"])
    _tts_config["cache_dir"] = getattr(cfg, "tts_cache_dir", _tts_config["cache_dir"])
    _tts_config["timeout"] = getattr(cfg, "tts_timeout", 60)
    _tts_config["max_text_len"] = getattr(cfg, "tts_max_text_len", 100)
    os.makedirs(_tts_config["cache_dir"], exist_ok=True)
    reset_provider()
    logger.info(
        f"[TTS] 配置已注入: enabled={_tts_config['enabled']}, "
        f"provider={_tts_config['provider']}"
    )


def is_enabled() -> bool:
    return _tts_config["enabled"]


def get_provider_name() -> str:
    return _tts_config["provider"]


# ==================== 通用工具函数 ====================
def strip_narration(text: str) -> str:
    """去掉括号里的旁白/动作描述，只保留对话内容。
    处理中文括号（）、英文括号()、以及【】等常见旁白标记。
    例如："（微微一笑）玉楼，你回来了。" → "玉楼，你回来了。"
    """
    if not text:
        return text
    # 中文括号（...）
    text = re.sub(r"（[^）]*）", "", text)
    # 英文括号(...)
    text = re.sub(r"\([^)]*\)", "", text)
    # 【...】
    text = re.sub(r"【[^】]*】", "", text)
    # 「...」和『...』（日文引号，有时用作旁白）
    text = re.sub(r"「[^」]*」", "", text)
    text = re.sub(r"『[^』]*』", "", text)
    # 去掉多余空白
    text = re.sub(r"\s+", "", text)
    return text


def _clean_text(text: str) -> str:
    """清理文本：去掉旁白、QQ表情，截断到最大长度"""
    # 先去掉括号旁白
    text = strip_narration(text)
    # 去掉QQ表情
    text = re.sub(r"\[.*?\]", "", text)
    text = re.sub(r"\s+", "", text)
    if len(text) > _tts_config["max_text_len"]:
        text = text[: _tts_config["max_text_len"]]
    return text


def _cache_path(text: str, emotion: str = "default", provider: str = "local") -> Path:
    """根据文本+情绪+Provider生成缓存路径"""
    h = hashlib.md5(f"{provider}:{emotion}:{text}".encode("utf-8")).hexdigest()[:16]
    return Path(_tts_config["cache_dir"]) / f"{h}.silk"


def _get_voice_params(emotion: Optional[str] = None) -> dict:
    """获取本地GPT-SoVITS的音色参数（含情绪→音色映射）"""
    try:
        from . import voice_manager
        if voice_manager.is_enabled() and emotion and emotion != "default":
            v = voice_manager.get_voice_for_emotion(emotion)
            if v["ref_exists"]:
                return {
                    "ref_audio": v["ref_audio_path"],
                    "ref_text": v["ref_text"] or _tts_config["ref_text"],
                    "speed": v["speed"],
                    "pitch": v["pitch"],
                    "volume": v["volume"],
                    "emotion": v["emotion"],
                    "from_voice_manager": True,
                }
    except Exception as e:
        logger.debug(f"[TTS] voice_manager获取失败: {e}")
    return {
        "ref_audio": _tts_config["ref_audio"],
        "ref_text": _tts_config["ref_text"],
        "speed": _tts_config["speed"],
        "pitch": 0,
        "volume": 1.0,
        "emotion": "default",
        "from_voice_manager": False,
    }


# ==================== WAV → PCM → silk 通用转换 ====================
def _wav_to_pcm(wav_bytes: bytes) -> tuple[bytes, int]:
    """从 WAV 提取 PCM，强制单声道 16bit"""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(wav_bytes)
        wav_path = f.name
    try:
        with wave.open(wav_path, "rb") as w:
            n_channels = w.getnchannels()
            sampwidth = w.getsampwidth()
            framerate = w.getframerate()
            n_frames = w.getnframes()
            raw = w.readframes(n_frames)
        if sampwidth == 2:
            audio = np.frombuffer(raw, dtype=np.int16)
        elif sampwidth == 1:
            audio = (np.frombuffer(raw, dtype=np.uint8).astype(np.int16) - 128) * 256
        else:
            raise ValueError(f"不支持的位深: {sampwidth}")
        if n_channels == 2:
            audio = audio.reshape(-1, 2).mean(axis=1).astype(np.int16)
        elif n_channels > 2:
            audio = audio.reshape(-1, n_channels).mean(axis=1).astype(np.int16)
        return audio.tobytes(), framerate
    finally:
        os.unlink(wav_path)


def _resample_pcm(pcm: bytes, src_rate: int, dst_rate: int = 24000) -> bytes:
    """numpy 线性重采样 PCM（16bit 单声道）"""
    if src_rate == dst_rate:
        return pcm
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    src_len = len(audio)
    dst_len = int(src_len * dst_rate / src_rate)
    x_old = np.linspace(0, 1, src_len, endpoint=False)
    x_new = np.linspace(0, 1, dst_len, endpoint=False)
    return np.interp(x_new, x_old, audio).astype(np.int16).tobytes()


def _pcm_to_silk(pcm: bytes, sample_rate: int = 24000) -> bytes:
    """调用 silk_v3_encoder.exe 将 PCM 转为 silk v3（腾讯格式）"""
    encoder = _tts_config["silk_encoder"]
    if not os.path.exists(encoder):
        raise FileNotFoundError(f"silk编码器不存在: {encoder}")
    with tempfile.TemporaryDirectory() as tmpdir:
        pcm_path = os.path.join(tmpdir, "input.pcm")
        silk_path = os.path.join(tmpdir, "output.silk")
        with open(pcm_path, "wb") as f:
            f.write(pcm)
        cmd = [encoder, pcm_path, silk_path, "-Fs_API", str(sample_rate), "-tencent"]
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if not os.path.exists(silk_path) or os.path.getsize(silk_path) < 100:
            raise RuntimeError(
                f"silk编码失败: returncode={result.returncode}, "
                f"stdout={result.stdout.decode('gbk', errors='replace')[:200]}, "
                f"stderr={result.stderr.decode('gbk', errors='replace')[:200]}"
            )
        with open(silk_path, "rb") as f:
            return f.read()


# ==================== 主合成接口 ====================
def synthesize_to_silk_sync(text: str, emotion: Optional[str] = None) -> Optional[bytes]:
    """
    完整同步流程：Provider合成WAV → PCM → 重采样 → silk
    带缓存：相同文本+情绪+Provider直接返回缓存
    """
    if not _tts_config["enabled"]:
        return None
    clean = _clean_text(text)
    if not clean:
        return None
    provider = get_provider()
    cache_file = _cache_path(clean, emotion or "default", provider.name)
    if cache_file.exists() and cache_file.stat().st_size > 100:
        logger.debug(f"[TTS] 命中缓存: {clean[:20]} (provider={provider.name}, emotion={emotion})")
        return cache_file.read_bytes()
    try:
        t0 = time.time()
        # 1. Provider 合成 WAV
        wav_bytes = provider.synthesize_wav(clean, emotion)
        # 2. WAV → PCM
        pcm, src_rate = _wav_to_pcm(wav_bytes)
        # 3. 重采样到 24000Hz
        dst_rate = 24000 if src_rate > 24000 else src_rate
        if dst_rate not in (8000, 12000, 16000, 24000):
            dst_rate = 24000
        pcm = _resample_pcm(pcm, src_rate, dst_rate)
        # 4. PCM → silk
        silk_bytes = _pcm_to_silk(pcm, dst_rate)
        # 5. 写缓存
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(silk_bytes)
        elapsed = time.time() - t0
        logger.info(
            f"[TTS] 合成完成: '{clean[:20]}' {len(silk_bytes)}字节 "
            f"耗时{elapsed:.1f}s provider={provider.name} emotion={emotion or 'default'} "
            f"(源{src_rate}Hz→{dst_rate}Hz)"
        )
        return silk_bytes
    except Exception as e:
        logger.error(f"[TTS] 合成失败(provider={provider.name}): {e}")
        return None


async def synthesize_to_silk(text: str, emotion: Optional[str] = None) -> Optional[bytes]:
    """异步包装：在线程池里执行同步合成"""
    if not _tts_config["enabled"]:
        return None
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, synthesize_to_silk_sync, text, emotion)


def save_silk_to_temp(silk_bytes: bytes) -> str:
    """将 silk 字节保存到临时文件，返回路径（供 OneBot 发送）"""
    tmp_dir = os.path.join(tempfile.gettempdir(), "qqbot_tts")
    os.makedirs(tmp_dir, exist_ok=True)
    path = os.path.join(tmp_dir, f"tts_{int(time.time()*1000)}.silk")
    with open(path, "wb") as f:
        f.write(silk_bytes)
    return path
