"""
ASR 语音识别模块（P1 语音收发）
- 收到 QQ 语音消息（MP3/silk/WAV/AMR 多种格式）→ 解码为 16kHz numpy → faster-whisper 转文字
- 懒加载模型（第一次使用时才加载，避免启动慢）
- 带文件缓存，相同语音不重复转写
- CPU int8 推理，A 卡可用

依赖：faster-whisper, librosa, soundfile, silk_v3_decoder.exe（仅silk格式需要）
"""
import asyncio
import hashlib
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
from nonebot import logger

# ---------------- 配置（运行时从 plugin_config 注入） ----------------
_asr_config = {
    "enabled": False,
    "model_size": "base",  # tiny/base/small/medium，base 约150MB，中文够用
    "device": "cpu",
    "compute_type": "int8",
    "language": "zh",
    "silk_decoder": r"F:\qq_chatbot\tools\silk\silk_v3_decoder.exe",
    "cache_dir": r"F:\qq_chatbot\data\asr_cache",
    "timeout": 120,
}

_model = None  # 懒加载的 faster-whisper 模型实例
_model_lock = asyncio.Lock()
WHISPER_SR = 16000  # whisper 标准采样率


def configure_asr(cfg):
    """从 plugin_config 注入配置"""
    _asr_config["enabled"] = getattr(cfg, "asr_enabled", False)
    _asr_config["model_size"] = getattr(cfg, "asr_model_size", "base")
    _asr_config["device"] = getattr(cfg, "asr_device", "cpu")
    _asr_config["compute_type"] = getattr(cfg, "asr_compute_type", "int8")
    _asr_config["language"] = getattr(cfg, "asr_language", "zh")
    _asr_config["silk_decoder"] = getattr(cfg, "asr_silk_decoder", _asr_config["silk_decoder"])
    _asr_config["cache_dir"] = getattr(cfg, "asr_cache_dir", _asr_config["cache_dir"])
    _asr_config["timeout"] = getattr(cfg, "asr_timeout", 120)
    os.makedirs(_asr_config["cache_dir"], exist_ok=True)
    logger.info(f"[ASR] 配置已注入: enabled={_asr_config['enabled']}, model={_asr_config['model_size']}, device={_asr_config['device']}")


def is_enabled() -> bool:
    return _asr_config["enabled"]


def _load_model():
    """加载 faster-whisper 模型（同步，在线程池里调用）"""
    global _model
    if _model is not None:
        return _model
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError("faster-whisper 未安装，请运行: pip install faster-whisper")
    t0 = time.time()
    # 国内镜像加速模型下载
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    _model = WhisperModel(
        _asr_config["model_size"],
        device=_asr_config["device"],
        compute_type=_asr_config["compute_type"],
    )
    logger.info(f"[ASR] 模型加载完成: {_asr_config['model_size']} ({_asr_config['device']}/{_asr_config['compute_type']}) 耗时{time.time()-t0:.1f}s")
    return _model


def _detect_format(data: bytes) -> str:
    """检测音频格式：mp3/silk/wav/amr/unknown"""
    if len(data) < 4:
        return "unknown"
    head = data[:16]
    # MP3: ID3 标签 或 MPEG 同步字 0xFFFB/0xFFF3/0xFFF2
    if head[:3] == b"ID3" or (head[0] == 0xFF and head[1] in (0xFB, 0xF3, 0xF2, 0xFA)):
        return "mp3"
    # Silk: #!SILK_V3 或 #!SILK
    if head[:8] in (b"#!SILK_V3", b"#!SILK_V2") or head[:6] == b"#!SILK":
        return "silk"
    # WAV: RIFF....WAVE
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    # AMR: #!AMR
    if head[:5] == b"#!AMR":
        return "amr"
    # OGG: OggS
    if head[:4] == b"OggS":
        return "ogg"
    return "unknown"


def _silk_to_wav_file(silk_bytes: bytes) -> str:
    """用 silk_v3_decoder.exe 将 silk 字节解码为 WAV 文件，返回 WAV 文件路径"""
    decoder = _asr_config["silk_decoder"]
    if not os.path.exists(decoder):
        raise FileNotFoundError(f"silk解码器不存在: {decoder}")
    with tempfile.TemporaryDirectory() as tmpdir:
        silk_path = os.path.join(tmpdir, "input.silk")
        wav_path = os.path.join(tmpdir, "output.wav")
        with open(silk_path, "wb") as f:
            f.write(silk_bytes)
        cmd = [decoder, silk_path, wav_path]
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if not os.path.exists(wav_path) or os.path.getsize(wav_path) < 100:
            raise RuntimeError(
                f"silk解码失败: returncode={result.returncode}, "
                f"stdout={result.stdout.decode('gbk', errors='replace')[:200]}, "
                f"stderr={result.stderr.decode('gbk', errors='replace')[:200]}"
            )
        # 读到内存，因为 TemporaryDirectory 会删
        with open(wav_path, "rb") as f:
            wav_bytes = f.read()
        # 写到持久临时文件
        persistent_path = os.path.join(tempfile.gettempdir(), f"asr_silk_{int(time.time()*1000)}.wav")
        with open(persistent_path, "wb") as f:
            f.write(wav_bytes)
        return persistent_path


def _get_ffmpeg_path() -> str:
    """获取 ffmpeg 可执行文件路径（优先 F 盘项目自带的）"""
    ffmpeg_f = r"F:\qq_chatbot\tools\ffmpeg\ffmpeg.exe"
    if os.path.exists(ffmpeg_f):
        return ffmpeg_f
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"  # fallback 到系统 PATH


def _ffmpeg_to_wav(audio_bytes: bytes) -> str:
    """用 ffmpeg 将任意音频字节转为 16kHz 单声道 WAV 文件，返回 WAV 路径"""
    ffmpeg = _get_ffmpeg_path()
    ext = "audio"
    tmp_in = os.path.join(tempfile.gettempdir(), f"asr_ffmpeg_in_{int(time.time()*1000)}.{ext}")
    tmp_out = os.path.join(tempfile.gettempdir(), f"asr_ffmpeg_out_{int(time.time()*1000)}.wav")
    with open(tmp_in, "wb") as f:
        f.write(audio_bytes)
    try:
        cmd = [
            ffmpeg, "-y", "-i", tmp_in,
            "-ar", str(WHISPER_SR), "-ac", "1",
            "-f", "wav", tmp_out,
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if not os.path.exists(tmp_out) or os.path.getsize(tmp_out) < 100:
            raise RuntimeError(
                f"ffmpeg转码失败: returncode={result.returncode}, "
                f"stderr={result.stderr.decode('utf-8', errors='replace')[:300]}"
            )
        return tmp_out
    finally:
        try:
            os.unlink(tmp_in)
        except OSError:
            pass


def _audio_to_numpy(audio_bytes: bytes) -> Tuple[np.ndarray, int]:
    """
    将任意格式音频字节转为 16kHz 单声道 float32 numpy 数组。
    返回 (audio_array, sample_rate)
    """
    import soundfile as sf

    fmt = _detect_format(audio_bytes)
    logger.info(f"[ASR] 音频格式检测: {fmt}, {len(audio_bytes)}字节")

    # silk 需要先解码为 WAV
    if fmt == "silk":
        wav_path = _silk_to_wav_file(audio_bytes)
        try:
            audio, sr = sf.read(wav_path, dtype="float32")
            if sr != WHISPER_SR:
                # 重采样
                import librosa
                audio = librosa.resample(audio, orig_sr=sr, target_sr=WHISPER_SR)
                sr = WHISPER_SR
            return audio.astype(np.float32), WHISPER_SR
        finally:
            try:
                os.unlink(wav_path)
            except OSError:
                pass

    # 其他格式（mp3/amr/wav/ogg）用 ffmpeg 转为 16kHz WAV，再用 soundfile 读取
    wav_path = _ffmpeg_to_wav(audio_bytes)
    try:
        audio, sr = sf.read(wav_path, dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)  # 多声道转单声道
        return audio.astype(np.float32), sr
    finally:
        try:
            os.unlink(wav_path)
        except OSError:
            pass


def _transcribe_numpy(audio: np.ndarray, sample_rate: int) -> str:
    """用 faster-whisper 转写 numpy 数组为文字（同步，在线程池里调用）"""
    model = _load_model()
    t0 = time.time()
    segments, info = model.transcribe(
        audio,
        language=_asr_config["language"],
        beam_size=5,
        vad_filter=True,  # 语音活动检测，过滤静音
        vad_parameters=dict(min_silence_duration_ms=500),
    )
    text = "".join(seg.text.strip() for seg in segments).strip()
    elapsed = time.time() - t0
    logger.info(f"[ASR] 转写完成: {len(text)}字 耗时{elapsed:.1f}s (语言={info.language}, 概率={info.language_probability:.2f})")
    return text


def _cache_path(audio_bytes: bytes) -> Path:
    """根据音频内容 hash 生成缓存路径"""
    h = hashlib.md5(audio_bytes).hexdigest()[:16]
    return Path(_asr_config["cache_dir"]) / f"{h}.txt"


def transcribe_audio_sync(audio_bytes: bytes) -> Optional[str]:
    """
    完整同步流程：任意格式音频 → 16kHz numpy → 转写文字
    带缓存：相同音频内容直接返回缓存的文字
    """
    if not _asr_config["enabled"]:
        return None
    if not audio_bytes or len(audio_bytes) < 50:
        return None
    # 检查缓存
    cache_file = _cache_path(audio_bytes)
    if cache_file.exists():
        text = cache_file.read_text(encoding="utf-8").strip()
        if text:
            logger.debug(f"[ASR] 命中缓存: {text[:30]}")
            return text
    try:
        # 1. 解码为 numpy 数组
        audio, sr = _audio_to_numpy(audio_bytes)
        if len(audio) < 1600:  # 少于0.1秒
            logger.warning("[ASR] 音频太短，跳过转写")
            return None
        # 2. 转写
        text = _transcribe_numpy(audio, sr)
        if text:
            # 写缓存
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(text, encoding="utf-8")
        return text if text else None
    except Exception as e:
        logger.error(f"[ASR] 转写失败: {e}")
        return None


# 兼容旧接口名
def transcribe_silk_sync(silk_bytes: bytes) -> Optional[str]:
    """兼容旧接口：transcribe_silk → transcribe_audio"""
    return transcribe_audio_sync(silk_bytes)


async def transcribe_audio(audio_bytes: bytes) -> Optional[str]:
    """异步包装：在线程池里执行同步转写，避免阻塞事件循环"""
    if not _asr_config["enabled"]:
        return None
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, transcribe_audio_sync, audio_bytes)


# 兼容旧接口名
async def transcribe_silk(silk_bytes: bytes) -> Optional[str]:
    """兼容旧接口：transcribe_silk → transcribe_audio"""
    return await transcribe_audio(silk_bytes)
