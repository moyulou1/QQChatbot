"""
图片获取与转码
- 从 OneBot v11 消息里提取 image 段
- 下载（直链优先，失败回退 OneBot get_image）
- 用 Pillow 统一处理：EXIF 旋转、动图取首帧、转 RGB、长边等比缩放、JPEG 循环降质控体积
- 输出 base64 data URL，供多模态模型（OpenAI vision 格式）使用
"""
from __future__ import annotations

import asyncio
import base64
import io
from typing import Any, Optional

import httpx
from nonebot import logger


def extract_image_segments(message: Any, max_images: int) -> list[dict]:
    """从 OneBot Message 中提取标准 image 段（不含 mface/face 表情）。"""
    segs: list[dict] = []
    for seg in message:
        if seg.type == "image":
            d = seg.data or {}
            url = d.get("url") or ""
            file = d.get("file") or ""
            if url or file:
                segs.append({"url": url, "file": file, "sub_type": d.get("sub_type", "")})
        if len(segs) >= max_images:
            break
    return segs


def _transcode(raw: bytes, max_side: int, jpeg_quality: int, max_bytes: int) -> Optional[str]:
    """同步 CPU 操作：把原始图片字节压缩成不超限的 JPEG data URL。"""
    from PIL import Image, ImageOps

    im = Image.open(io.BytesIO(raw))
    im = ImageOps.exif_transpose(im)          # 纠正手机拍照方向
    try:
        if getattr(im, "is_animated", False):  # GIF/动图只取第一帧
            im.seek(0)
    except Exception:
        pass
    if im.mode != "RGB":
        im = im.convert("RGB")
    w, h = im.size
    longest = max(w, h)
    if longest > max_side:
        scale = max_side / float(longest)
        im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    quality = max(40, min(95, jpeg_quality))
    while True:
        out = io.BytesIO()
        im.save(out, format="JPEG", quality=quality, optimize=True)
        data = out.getvalue()
        if len(data) <= max_bytes or quality <= 40:
            return "data:image/jpeg;base64," + base64.b64encode(data).decode()
        quality -= 15


async def _download_one(seg: dict, bot: Any) -> Optional[bytes]:
    headers = {"User-Agent": "Mozilla/5.0"}
    url = seg.get("url") or ""
    if url:
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                r = await client.get(url, headers=headers)
                r.raise_for_status()
                if r.content:
                    return r.content
        except Exception as e:
            logger.warning(f"[VLM] 图片直链下载失败，尝试 get_image 兜底: {e}")
    if bot is not None and seg.get("file"):
        try:
            res = await bot.call_api("get_image", file=seg["file"])
            u = (res or {}).get("url")
            if u:
                async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                    r = await client.get(u, headers=headers)
                    r.raise_for_status()
                    return r.content
        except Exception as e:
            logger.warning(f"[VLM] get_image 兜底失败: {e}")
    return None


async def images_to_data_urls(
    segs: list[dict], bot: Any, max_side: int, jpeg_quality: int, max_bytes: int
) -> list[str]:
    """整批下载+转码，单张失败跳过，不影响其它。"""
    results: list[str] = []
    for seg in segs:
        raw = await _download_one(seg, bot)
        if not raw:
            continue
        try:
            data_url = await asyncio.to_thread(_transcode, raw, max_side, jpeg_quality, max_bytes)
            if data_url:
                results.append(data_url)
        except Exception as e:
            logger.warning(f"[VLM] 图片转码失败: {e}")
    return results


def build_multimodal_content(text: str, data_urls: list[str]) -> list[dict]:
    """组装 OpenAI 多模态 user content：一个 text 段 + 若干 image_url 段。"""
    content: list[dict] = [{"type": "text", "text": text or "请看看这张图并回应。"}]
    for u in data_urls:
        content.append({"type": "image_url", "image_url": {"url": u}})
    return content
