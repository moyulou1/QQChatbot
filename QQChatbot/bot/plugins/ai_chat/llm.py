"""
LLM 接入层 - DeepSeek API（OpenAI 兼容格式）
支持流式/非流式调用，自动重试，错误降级
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

import httpx
from nonebot import get_driver, logger
from pydantic import BaseModel, Field


class LLMConfig(BaseModel):
    """LLM 配置"""
    api_key: str = ""
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    temperature: float = 0.8
    max_tokens: int = 1024
    timeout: int = 60
    max_retries: int = 3
    retry_delay: float = 2.0


class ChatMessage(BaseModel):
    """聊天消息"""
    role: str  # system / user / assistant
    content: Any  # str（纯文本）或 list（OpenAI 多模态 content：text/image_url 段）


class LLMClient:
    """DeepSeek LLM 客户端"""

    def __init__(self, config: LLMConfig):
        self.config = config
        self._client: Optional[httpx.AsyncClient] = None
        self._semaphore = asyncio.Semaphore(5)
        self.on_usage: Optional[callable] = None
        self.total_calls = 0
        self.total_tokens = 0
        self.last_model = config.model
        self.last_latency_ms = 0
        self.last_reasoning = ""
        # 多模态视觉端点（configure_vision 注入；默认回退到文本端点）
        self.vision_base_url = config.base_url
        self.vision_api_key = config.api_key
        self.vision_model = ""
        self.vision_temperature = 0.7
        self.vision_max_tokens = 1024
        self._vision_client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.config.base_url,
                timeout=self.config.timeout,
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def chat(
        self,
        messages: list[ChatMessage],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        metadata: Optional[dict[str, Any]] = None,
        model: Optional[str] = None,
    ) -> str:
        """非流式聊天，返回完整回复文本。model 可临时覆盖（用于路由到推理模型）。"""
        if not self.config.api_key or self.config.api_key.startswith("sk-xxxx"):
            logger.warning("DeepSeek API Key 未配置，返回占位回复")
            return "（API Key 未配置，请在 .env 中设置 DEEPSEEK_API_KEY）"

        use_model = model or self.config.model
        self.last_model = use_model
        payload = {
            "model": use_model,
            "messages": [m.model_dump() for m in messages],
            "temperature": temperature if temperature is not None else self.config.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.config.max_tokens,
            "stream": False,
        }

        last_error: Optional[Exception] = None
        for attempt in range(1, self.config.max_retries + 1):
            try:
                t0 = time.time()
                async with self._semaphore:
                    client = await self._get_client()
                    response = await client.post("/chat/completions", json=payload)
                    response.raise_for_status()
                    data = response.json()
                    latency_ms = int((time.time() - t0) * 1000)
                    self.last_latency_ms = latency_ms

                    if not data.get("choices"):
                        raise ValueError(f"API 返回空 choices: {data}")

                    msg0 = data["choices"][0]["message"]
                    content = msg0.get("content")
                    # 推理模型的思维链单独留存（不发进群）
                    self.last_reasoning = msg0.get("reasoning_content") or ""
                    if not content or not content.strip():
                        raise ValueError("API 返回空内容")

                    usage = data.get("usage", {})
                    usage = dict(usage)
                    usage["latency_ms"] = latency_ms
                    usage["used_model"] = use_model
                    self.total_calls += 1
                    self.total_tokens += usage.get("total_tokens", 0)
                    if self.on_usage:
                        try:
                            await self.on_usage(usage, metadata or {})
                        except Exception as e:
                            logger.warning(f"[LLM] usage 回调失败: {e}")

                    return content.strip()

            except httpx.HTTPStatusError as e:
                last_error = e
                status = e.response.status_code
                resp_text = e.response.text[:500]
                logger.error(f"[LLM] HTTP {status} 错误 (尝试 {attempt}/{self.config.max_retries}): {resp_text}")

                if status == 401:
                    raise RuntimeError("DeepSeek API Key 无效，请检查 DEEPSEEK_API_KEY 配置") from e
                if status == 402:
                    raise RuntimeError("DeepSeek 账户余额不足，请充值") from e
                if status == 429:
                    wait_time = self.config.retry_delay * attempt * 2
                    logger.warning(f"[LLM] 触发限流，等待 {wait_time}s 后重试")
                    await asyncio.sleep(wait_time)
                    continue
                if status >= 500:
                    await asyncio.sleep(self.config.retry_delay * attempt)
                    continue
                raise

            except (httpx.ConnectError, httpx.ReadTimeout, asyncio.TimeoutError) as e:
                last_error = e
                logger.warning(f"[LLM] 网络错误 (尝试 {attempt}/{self.config.max_retries}): {e}")
                await asyncio.sleep(self.config.retry_delay * attempt)

            except Exception as e:
                last_error = e
                logger.error(f"[LLM] 未知错误 (尝试 {attempt}/{self.config.max_retries}): {e}")
                await asyncio.sleep(self.config.retry_delay)

        raise RuntimeError(f"LLM 调用失败，已重试 {self.config.max_retries} 次: {last_error}")

    async def ping(self) -> dict:
        """轻量探测 API 是否可达、Key 是否有效、延迟多少（GET /models）。"""
        t0 = time.time()
        try:
            client = await self._get_client()
            r = await client.get("/models")
            r.raise_for_status()
            ids = [m.get("id") for m in r.json().get("data", [])]
            return {"ok": True, "latency_ms": int((time.time() - t0) * 1000), "models": ids[:10]}
        except httpx.HTTPStatusError as e:
            return {"ok": False, "latency_ms": int((time.time() - t0) * 1000),
                    "error": f"HTTP {e.response.status_code}"}
        except Exception as e:
            return {"ok": False, "latency_ms": int((time.time() - t0) * 1000),
                    "error": f"{type(e).__name__}: {e}"}

    async def chat_json(self, instruction: str, content: str, max_tokens: int = 500) -> Optional[dict]:
        """要求模型只输出 JSON 对象并解析；失败返回 None。用于群友画像抽取等内部任务。"""
        import json as _json
        msgs = [
            ChatMessage(role="system",
                        content=instruction + "\n严格只输出一个 JSON 对象，不要输出任何多余文字，不要使用 markdown 代码块。"),
            ChatMessage(role="user", content=content),
        ]
        raw = await self.chat(msgs, temperature=0.1, max_tokens=max_tokens,
                              metadata={"group_id": "_internal", "user_id": "_profile"})
        raw = (raw or "").strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
        a, b = raw.find("{"), raw.rfind("}")
        if a != -1 and b > a:
            raw = raw[a:b + 1]
        try:
            return _json.loads(raw)
        except Exception:
            return None

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        _vc = getattr(self, "_vision_client", None)
        if _vc and not _vc.is_closed:
            await _vc.aclose()

    def configure_vision(self, base_url: str = "", api_key: str = "", model: str = "",
                         temperature: float = 0.7, max_tokens: int = 1024):
        """配置多模态视觉端点；base/key 留空则回退到文本端点。"""
        self.vision_base_url = base_url or self.config.base_url
        self.vision_api_key = api_key or self.config.api_key
        self.vision_model = model or "deepseek-v4-flash-vision-exp"
        self.vision_temperature = temperature
        self.vision_max_tokens = max_tokens
        if getattr(self, "_vision_client", None) is not None and not self._vision_client.is_closed:
            import asyncio as _aio
            _aio.create_task(self._vision_client.aclose())
        self._vision_client = None

    async def _get_vision_client(self) -> httpx.AsyncClient:
        if self._vision_client is None or self._vision_client.is_closed:
            self._vision_client = httpx.AsyncClient(
                base_url=self.vision_base_url,
                timeout=self.config.timeout,
                headers={
                    "Authorization": f"Bearer {self.vision_api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._vision_client

    async def chat_vision(
        self,
        messages: list[ChatMessage],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        model: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        """多模态对话：messages 中至少一条 user 的 content 为 [text段, image_url段...]。"""
        use_model = model or self.vision_model or self.config.model
        self.last_model = use_model
        payload = {
            "model": use_model,
            "messages": [m.model_dump() for m in messages],
            "temperature": temperature if temperature is not None else self.vision_temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.vision_max_tokens,
            "stream": False,
        }
        last_error: Optional[Exception] = None
        for attempt in range(1, 3):
            try:
                t0 = time.time()
                async with self._semaphore:
                    client = await self._get_vision_client()
                    response = await client.post("/chat/completions", json=payload)
                    response.raise_for_status()
                    data = response.json()
                    latency_ms = int((time.time() - t0) * 1000)
                    self.last_latency_ms = latency_ms
                    if not data.get("choices"):
                        raise ValueError(f"视觉 API 返回空 choices: {data}")
                    msg0 = data["choices"][0]["message"]
                    content = msg0.get("content")
                    self.last_reasoning = msg0.get("reasoning_content") or ""
                    if not content or not content.strip():
                        raise ValueError("视觉 API 返回空内容（可能 max_tokens 太小，思考链占满）")
                    usage = dict(data.get("usage", {}))
                    usage["latency_ms"] = latency_ms
                    usage["used_model"] = use_model
                    self.total_calls += 1
                    self.total_tokens += usage.get("total_tokens", 0)
                    if self.on_usage:
                        try:
                            await self.on_usage(usage, metadata or {})
                        except Exception as e:
                            logger.warning(f"[VLM] usage 回调失败: {e}")
                    return content.strip()
            except httpx.HTTPStatusError as e:
                last_error = e
                logger.error(f"[VLM] HTTP {e.response.status_code} (尝试 {attempt}/2): {e.response.text[:400]}")
                # 鉴权/参数/模型不存在这类错误重试无意义，直接抛
                if e.response.status_code in (400, 401, 402, 404, 422):
                    raise
                await asyncio.sleep(self.config.retry_delay * attempt)
            except (httpx.ConnectError, httpx.ReadTimeout, asyncio.TimeoutError) as e:
                last_error = e
                logger.warning(f"[VLM] 网络错误 (尝试 {attempt}/2): {e}")
                await asyncio.sleep(self.config.retry_delay * attempt)
            except Exception as e:
                last_error = e
                logger.error(f"[VLM] 未知错误 (尝试 {attempt}/2): {e}")
                await asyncio.sleep(self.config.retry_delay)
        raise RuntimeError(f"视觉模型调用失败: {last_error}")


_llm_client: Optional[LLMClient] = None


def init_llm(config: LLMConfig) -> LLMClient:
    global _llm_client
    _llm_client = LLMClient(config)
    return _llm_client


def get_llm() -> Optional[LLMClient]:
    return _llm_client


@get_driver().on_shutdown
async def _shutdown():
    if _llm_client:
        await _llm_client.close()
