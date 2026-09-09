"""
音色管理模块
多参考音频池 + 情绪映射 + 变调后处理。

目录结构：
  F:\\qq_chatbot\\voices\\
    lin_pianpian\\           # 角色目录
      default.wav            # 默认参考音频
      default.txt            # 参考文本
      happy.wav              # 开心语气参考
      happy.txt
      ...
    voice_config.json        # 音色配置（语速/音调/音量映射）

voice_config.json 格式：
{
  "lin_pianpian": {
    "default":     {"ref": "default.wav",     "ref_text": "...", "speed": 1.0,  "pitch": 0,    "volume": 1.0},
    "happy":       {"ref": "happy.wav",       "ref_text": "...", "speed": 1.15, "pitch": 1.5,  "volume": 1.1},
    ...
  }
}
"""
from __future__ import annotations
import json
import os
import tempfile
from pathlib import Path
from typing import Optional

# 音色池根目录
VOICES_DIR = Path(r"F:\qq_chatbot\voices")
CONFIG_FILE = VOICES_DIR / "voice_config.json"

# 全局配置
_config = {
    "enabled": False,
    "default_character": "lin_pianpian",
    "pitch_shift_enabled": True,   # 是否启用变调后处理（需要librosa）
}

# 运行时缓存
_voice_config_cache: Optional[dict] = None
_librosa_available: Optional[bool] = None  # None=未检测, True/False


def configure_voice(cfg):
    """注入配置"""
    global _config
    _config["enabled"] = getattr(cfg, "voice_enabled", False)
    _config["default_character"] = getattr(cfg, "voice_default_character", "lin_pianpian")
    _config["pitch_shift_enabled"] = getattr(cfg, "voice_pitch_shift", True)
    print(f"[VoiceManager] 音色管理配置已注入: enabled={_config['enabled']}, "
          f"character={_config['default_character']}, pitch_shift={_config['pitch_shift_enabled']}")


def is_enabled() -> bool:
    return _config["enabled"]


def _check_librosa() -> bool:
    """检测librosa是否可用（变调后处理依赖）"""
    global _librosa_available
    if _librosa_available is not None:
        return _librosa_available
    try:
        import librosa  # noqa: F401
        import soundfile  # noqa: F401
        _librosa_available = True
        print("[VoiceManager] librosa + soundfile 可用，变调后处理已启用")
    except ImportError:
        _librosa_available = False
        print("[VoiceManager] librosa 未安装，变调后处理不可用（仅用speed_factor控制语速）")
    return _librosa_available


def load_voice_config(force_reload: bool = False) -> dict:
    """加载voice_config.json，不存在则创建默认配置"""
    global _voice_config_cache
    if _voice_config_cache is not None and not force_reload:
        return _voice_config_cache

    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                _voice_config_cache = json.load(f)
            return _voice_config_cache
        except Exception as e:
            print(f"[VoiceManager] 加载voice_config.json失败: {e}，使用默认配置")

    # 创建默认配置
    default_config = {
        "lin_pianpian": {
            "default": {
                "ref": "default.wav",
                "ref_text": "你若是因此想自我了断，我也管不着。",
                "speed": 1.0, "pitch": 0, "volume": 1.0,
            },
            "happy": {
                "ref": "happy.wav", "ref_text": "",
                "speed": 1.15, "pitch": 1.5, "volume": 1.1,
            },
            "excited": {
                "ref": "excited.wav", "ref_text": "",
                "speed": 1.25, "pitch": 2.0, "volume": 1.15,
            },
            "sad": {
                "ref": "sad.wav", "ref_text": "",
                "speed": 0.85, "pitch": -1.0, "volume": 0.9,
            },
            "angry": {
                "ref": "angry.wav", "ref_text": "",
                "speed": 1.1, "pitch": 0.5, "volume": 1.2,
            },
            "calm": {
                "ref": "calm.wav", "ref_text": "",
                "speed": 0.95, "pitch": 0, "volume": 0.95,
            },
            "coquettish": {
                "ref": "coquettish.wav", "ref_text": "",
                "speed": 0.95, "pitch": 2.0, "volume": 1.0,
            },
            "surprised": {
                "ref": "surprised.wav", "ref_text": "",
                "speed": 1.1, "pitch": 1.0, "volume": 1.1,
            },
            "thinking": {
                "ref": "thinking.wav", "ref_text": "",
                "speed": 0.9, "pitch": -0.5, "volume": 0.95,
            },
            "gentle": {
                "ref": "gentle.wav", "ref_text": "",
                "speed": 0.9, "pitch": 0.5, "volume": 0.9,
            },
            "playful": {
                "ref": "playful.wav", "ref_text": "",
                "speed": 1.1, "pitch": 1.5, "volume": 1.05,
            },
        }
    }

    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(default_config, f, ensure_ascii=False, indent=2)
        print(f"[VoiceManager] 已创建默认voice_config.json: {CONFIG_FILE}")
    except Exception as e:
        print(f"[VoiceManager] 创建voice_config.json失败: {e}")

    _voice_config_cache = default_config
    return default_config


def save_voice_config(config: dict) -> bool:
    """保存voice_config.json"""
    global _voice_config_cache
    try:
        VOICES_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        _voice_config_cache = config
        return True
    except Exception as e:
        print(f"[VoiceManager] 保存voice_config.json失败: {e}")
        return False


def get_voice_for_emotion(emotion: str, character: Optional[str] = None) -> dict:
    """
    根据情绪标签获取音色配置。
    如果该情绪的参考音频文件不存在，自动降级到default。
    
    Returns:
        {
            "ref_audio_path": 绝对路径,
            "ref_text": 参考文本,
            "speed": 语速,
            "pitch": 变调（半音）,
            "volume": 音量,
            "emotion": 实际使用的情绪（可能降级为default）,
            "ref_exists": 参考音频文件是否存在,
        }
    """
    character = character or _config["default_character"]
    config = load_voice_config()
    char_config = config.get(character, {})

    # 尝试获取该情绪的配置
    emotion_cfg = char_config.get(emotion)
    actual_emotion = emotion

    # 如果该情绪没有配置，或参考音频不存在，降级到default
    if not emotion_cfg:
        emotion_cfg = char_config.get("default", {})
        actual_emotion = "default"

    ref_file = emotion_cfg.get("ref", "default.wav")
    ref_path = VOICES_DIR / character / ref_file
    ref_exists = ref_path.exists()

    # 如果参考音频不存在，降级到default
    if not ref_exists and actual_emotion != "default":
        default_cfg = char_config.get("default", {})
        default_ref = default_cfg.get("ref", "default.wav")
        default_path = VOICES_DIR / character / default_ref
        if default_path.exists():
            emotion_cfg = default_cfg
            ref_path = default_path
            ref_exists = True
            actual_emotion = "default"

    return {
        "ref_audio_path": str(ref_path),
        "ref_text": emotion_cfg.get("ref_text", ""),
        "speed": float(emotion_cfg.get("speed", 1.0)),
        "pitch": float(emotion_cfg.get("pitch", 0)),
        "volume": float(emotion_cfg.get("volume", 1.0)),
        "emotion": actual_emotion,
        "ref_exists": ref_exists,
    }


def list_available_voices(character: Optional[str] = None) -> dict:
    """列出角色的所有可用音色（参考音频文件是否存在）"""
    character = character or _config["default_character"]
    config = load_voice_config()
    char_config = config.get(character, {})
    char_dir = VOICES_DIR / character

    result = {}
    for emotion, cfg in char_config.items():
        ref_file = cfg.get("ref", "")
        ref_path = char_dir / ref_file if ref_file else None
        result[emotion] = {
            "ref": ref_file,
            "ref_text": cfg.get("ref_text", ""),
            "speed": cfg.get("speed", 1.0),
            "pitch": cfg.get("pitch", 0),
            "volume": cfg.get("volume", 1.0),
            "file_exists": ref_path.exists() if ref_path else False,
            "file_size": ref_path.stat().st_size if (ref_path and ref_path.exists()) else 0,
        }
    return result


def apply_pitch_shift(wav_data: bytes, pitch_semitones: float, volume: float = 1.0) -> bytes:
    """
    对WAV二进制数据做变调和音量后处理。
    需要librosa + soundfile。如果不可用，直接返回原数据。
    
    Args:
        wav_data: WAV文件二进制内容
        pitch_semitones: 变调半音数（正数升调，负数降调）
        volume: 音量缩放系数
    
    Returns:
        处理后的WAV二进制数据
    """
    if not _config["pitch_shift_enabled"]:
        return wav_data
    if abs(pitch_semitones) < 0.01 and abs(volume - 1.0) < 0.01:
        return wav_data  # 无需处理
    if not _check_librosa():
        return wav_data  # librosa不可用，跳过

    try:
        import librosa
        import soundfile as sf
        import numpy as np

        # 写到临时文件
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(wav_data)
            tmp_path = tmp.name

        try:
            # 加载WAV
            y, sr = librosa.load(tmp_path, sr=None)

            # 变调
            if abs(pitch_semitones) >= 0.01:
                y = librosa.effects.pitch_shift(y, sr=sr, n_steps=pitch_semitones)

            # 音量
            if abs(volume - 1.0) >= 0.01:
                y = y * volume
                # 防止削波
                max_val = np.max(np.abs(y))
                if max_val > 0.99:
                    y = y * (0.99 / max_val)

            # 写回临时文件
            out_path = tmp_path + "_out.wav"
            sf.write(out_path, y, sr)

            with open(out_path, "rb") as f:
                result = f.read()

            os.unlink(out_path)
            return result
        finally:
            os.unlink(tmp_path)

    except Exception as e:
        print(f"[VoiceManager] 变调后处理失败: {e}，使用原音频")
        return wav_data
