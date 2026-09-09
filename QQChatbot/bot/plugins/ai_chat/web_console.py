"""
Web 管理控制台后端
基于 FastAPI，独立端口运行
提供：仪表盘、Token统计、人格管理、调试控制台、配置管理
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional

from nonebot import get_driver, logger
from pydantic import BaseModel

try:
    from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File, Form
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    logger.warning("[WebConsole] FastAPI 未安装，Web控制台将不可用")


class PersonalitySaveRequest(BaseModel):
    content: str


class ConfigUpdateRequest(BaseModel):
    key: str
    value: Any


class DebugSendRequest(BaseModel):
    group_id: str
    user_id: str = "10000"
    user_name: str = "测试用户"
    message: str
    model: str = "auto"   # auto / chat / reasoner


class MemoryClearRequest(BaseModel):
    group_id: str
    clear_long_term: bool = True


# 前端配置 key -> (plugin_config 属性名, .env 键, 是否需要重启后端才完全生效)
_CONFIG_FIELDS: dict[str, tuple[str, str, bool]] = {
    "temperature": ("deepseek_temperature", "DEEPSEEK_TEMPERATURE", False),
    "max_tokens": ("deepseek_max_tokens", "DEEPSEEK_MAX_TOKENS", False),
    "model": ("deepseek_model", "DEEPSEEK_MODEL", False),
    "base_url": ("deepseek_base_url", "DEEPSEEK_BASE_URL", True),
    "reply_on_at": ("reply_on_at", "REPLY_ON_AT", False),
    "reply_on_name": ("reply_on_name", "REPLY_ON_NAME", False),
    "random_reply_probability": ("random_reply_probability", "RANDOM_REPLY_PROBABILITY", False),
    "reply_cooldown": ("reply_cooldown", "REPLY_COOLDOWN", False),
    "max_message_length": ("max_message_length", "MAX_MESSAGE_LENGTH", False),
    "context_rounds": ("context_rounds", "CONTEXT_ROUNDS", False),
    "trigger_keywords": ("trigger_keywords", "TRIGGER_KEYWORDS", False),
    "enable_long_term_memory": ("enable_long_term_memory", "ENABLE_LONG_TERM_MEMORY", False),
    "enabled_groups": ("enabled_groups", "ENABLED_GROUPS", False),
    "disabled_groups": ("disabled_groups", "DISABLED_GROUPS", False),
    "blacklist_users": ("blacklist_users", "BLACKLIST_USERS", False),
    # 视觉识图
    "vision_enabled": ("vision_enabled", "VISION_ENABLED", False),
    "vision_model": ("vision_model", "VISION_MODEL", False),
    "vision_temperature": ("vision_temperature", "VISION_TEMPERATURE", False),
    "vision_max_tokens": ("vision_max_tokens", "VISION_MAX_TOKENS", False),
    "vision_max_images": ("vision_max_images", "VISION_MAX_IMAGES", False),
    "vision_require_trigger": ("vision_require_trigger", "VISION_REQUIRE_TRIGGER", False),
    # 内容安全与防注入（P0-2）
    "safety_input_enabled": ("safety_input_enabled", "SAFETY_INPUT_ENABLED", False),
    "safety_output_enabled": ("safety_output_enabled", "SAFETY_OUTPUT_ENABLED", False),
    # 速率限制（P0-3）
    "rate_limit_user_window": ("rate_limit_user_window", "RATE_LIMIT_USER_WINDOW", False),
    "rate_limit_user_max": ("rate_limit_user_max", "RATE_LIMIT_USER_MAX", False),
    "rate_limit_group_window": ("rate_limit_group_window", "RATE_LIMIT_GROUP_WINDOW", False),
    "rate_limit_group_max": ("rate_limit_group_max", "RATE_LIMIT_GROUP_MAX", False),
    # TTS 语音合成（P1）
    "tts_enabled": ("tts_enabled", "TTS_ENABLED", False),
    "tts_provider": ("tts_provider", "TTS_PROVIDER", False),
    "tts_api_url": ("tts_api_url", "TTS_API_URL", False),
    "tts_ref_audio": ("tts_ref_audio", "TTS_REF_AUDIO", False),
    "tts_ref_text": ("tts_ref_text", "TTS_REF_TEXT", False),
    "tts_speed": ("tts_speed", "TTS_SPEED", False),
    "tts_trigger_mode": ("tts_trigger_mode", "TTS_TRIGGER_MODE", False),
    "tts_timeout": ("tts_timeout", "TTS_TIMEOUT", False),
    "tts_max_text_len": ("tts_max_text_len", "TTS_MAX_TEXT_LEN", False),
    # Minimax 云端 TTS（国内可访问，语音克隆）
    "tts_minimax_group_id": ("tts_minimax_group_id", "TTS_MINIMAX_GROUP_ID", False),
    "tts_minimax_api_key": ("tts_minimax_api_key", "TTS_MINIMAX_API_KEY", False),
    "tts_minimax_voice_id": ("tts_minimax_voice_id", "TTS_MINIMAX_VOICE_ID", False),
    "tts_minimax_api_url": ("tts_minimax_api_url", "TTS_MINIMAX_API_URL", False),
    "tts_minimax_model": ("tts_minimax_model", "TTS_MINIMAX_MODEL", False),
    # Fish Audio 云端 TTS
    "tts_fish_api_key": ("tts_fish_api_key", "TTS_FISH_API_KEY", False),
    "tts_fish_voice_id": ("tts_fish_voice_id", "TTS_FISH_VOICE_ID", False),
    "tts_fish_api_url": ("tts_fish_api_url", "TTS_FISH_API_URL", False),
    # 火山引擎云端 TTS
    "tts_volc_app_id": ("tts_volc_app_id", "TTS_VOLC_APP_ID", False),
    "tts_volc_access_token": ("tts_volc_access_token", "TTS_VOLC_ACCESS_TOKEN", False),
    "tts_volc_cluster": ("tts_volc_cluster", "TTS_VOLC_CLUSTER", False),
    "tts_volc_voice_type": ("tts_volc_voice_type", "TTS_VOLC_VOICE_TYPE", False),
    "tts_volc_api_url": ("tts_volc_api_url", "TTS_VOLC_API_URL", False),
    # ASR 语音识别（P1）
    "asr_enabled": ("asr_enabled", "ASR_ENABLED", False),
    "asr_model_size": ("asr_model_size", "ASR_MODEL_SIZE", True),
    "asr_language": ("asr_language", "ASR_LANGUAGE", False),
    # 人格一致性校验（P1 防OOC）
    "personality_check_enabled": ("personality_check_enabled", "PERSONALITY_CHECK_ENABLED", False),
    "personality_check_rule": ("personality_check_rule", "PERSONALITY_CHECK_RULE", False),
    "personality_check_llm": ("personality_check_llm", "PERSONALITY_CHECK_LLM", False),
    "personality_check_max_emoji": ("personality_check_max_emoji", "PERSONALITY_CHECK_MAX_EMOJI", False),
    # 情绪判定（纯LLM，每轮调用）
    "emotion_enabled": ("emotion_enabled", "EMOTION_ENABLED", False),
    "emotion_model": ("emotion_model", "EMOTION_MODEL", False),
    "emotion_context_rounds": ("emotion_context_rounds", "EMOTION_CONTEXT_ROUNDS", False),
    # 音色管理（多参考音频池 + 情绪映射）
    "voice_enabled": ("voice_enabled", "VOICE_ENABLED", False),
    "voice_default_character": ("voice_default_character", "VOICE_DEFAULT_CHARACTER", False),
    "voice_pitch_shift": ("voice_pitch_shift", "VOICE_PITCH_SHIFT", False),
}


def _to_bool(v: Any) -> bool:
    """健壮布尔转换：避免 Python 里 bool('false') is True 的坑。"""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "on", "y", "是", "开")
    return bool(v)


class WebConsole:
    def __init__(self, host: str = "127.0.0.1", port: int = 18080,
                 token: str = "", static_dir: str = ""):
        self.host = host
        self.port = port
        self.token = token
        self.static_dir = Path(static_dir) if static_dir else None
        self.app: Optional[FastAPI] = None
        self._ws_clients: list[WebSocket] = []
        self._log_buffer: list[dict[str, Any]] = []
        self._max_log_buffer = 500
        self.personality_loader = None
        self.token_stats = None
        self.short_term = None
        self.long_term = None
        self.llm_client = None
        self.plugin_config = None
        self.profiles = None
        self.safety_filter = None
        self.runtime_state = None
        self.save_runtime_state = None
        self._last_ping: Optional[dict] = None
        # 配置持久化：运行时修改要真正写回 bot/.env，重启不丢
        # web_console.py 位于 bot/plugins/ai_chat/ 下，parents[2] 即 bot/
        self.env_path = Path(__file__).resolve().parents[2] / ".env"
        self._env_lock = threading.Lock()

    def setup(self):
        if not FASTAPI_AVAILABLE:
            logger.error("[WebConsole] 无法启动：FastAPI 未安装")
            return

        self.app = FastAPI(title="QQ Bot Admin Console", version="1.0.0")

        # 拦截 nonebot 运行日志推送到控制台 WebSocket（实时日志页）
        self._install_log_capture()

        if self.static_dir and self.static_dir.exists():
            self.app.mount("/static", StaticFiles(directory=str(self.static_dir)), name="static")

            @self.app.get("/")
            async def index():
                return FileResponse(str(self.static_dir / "index.html"))

        @self.app.middleware("http")
        async def auth_middleware(request, call_next):
            path = request.url.path
            if path in ("/", "/api/login", "/api/health") or path.startswith("/static/"):
                return await call_next(request)
            if path.startswith("/api/") and self.token:
                auth = request.headers.get("Authorization", "")
                x_token = request.headers.get("X-Console-Token", "")
                query_token = request.query_params.get("token", "")
                if not (auth == f"Bearer {self.token}" or x_token == self.token or query_token == self.token):
                    return JSONResponse(status_code=401, content={"error": "未授权"})
            return await call_next(request)

        @self.app.get("/api/health")
        async def health():
            return {"status": "ok", "time": time.time()}

        @self.app.post("/api/login")
        async def login(data: dict):
            token = data.get("token", "")
            if not self.token or token == self.token:
                return {"success": True}
            raise HTTPException(status_code=401, detail="令牌错误")

        @self.app.get("/api/dashboard")
        async def dashboard():
            today = await self.token_stats.get_today_summary() if self.token_stats else {}
            total = await self.token_stats.get_total_summary() if self.token_stats else {}
            llm_info = {}
            if self.llm_client:
                llm_info = {
                    "model": self.llm_client.config.model,
                    "temperature": self.llm_client.config.temperature,
                    "total_calls": self.llm_client.total_calls,
                    "total_tokens": self.llm_client.total_tokens,
                }
            return {
                "today": today, "total": total, "llm": llm_info,
                "personality": self.personality_loader.get_name() if self.personality_loader else "unknown",
                "uptime": time.time() - _start_time,
            }

        @self.app.get("/api/token/today")
        async def token_today():
            return await self.token_stats.get_today_summary() if self.token_stats else {}

        @self.app.get("/api/token/history")
        async def token_history(days: int = 30):
            return await self.token_stats.get_daily_history(days) if self.token_stats else []

        @self.app.get("/api/token/recent")
        async def token_recent(limit: int = 50):
            return await self.token_stats.get_recent_calls(limit) if self.token_stats else []

        @self.app.get("/api/token/group/{group_id}")
        async def token_group(group_id: str, days: int = 7):
            return await self.token_stats.get_group_stats(group_id, days) if self.token_stats else {}

        @self.app.get("/api/personalities")
        async def list_personalities():
            if not self.personality_loader:
                return []
            pdir = Path(self.personality_loader.personality_dir)
            files = list(pdir.glob("*.yaml")) + list(pdir.glob("*.yml"))
            result = []
            for f in sorted(files):
                name = f.stem
                active = (name == self.personality_loader.personality_file)
                try:
                    import yaml
                    with open(f, encoding="utf-8") as fh:
                        data = yaml.safe_load(fh)
                    display_name = data.get("basic", {}).get("name", name)
                    traits = data.get("personality_traits", [])[:3]
                except Exception:
                    display_name = name
                    traits = []
                result.append({
                    "file": name, "display_name": display_name,
                    "traits": traits, "active": active, "modified": f.stat().st_mtime,
                })
            return result

        @self.app.get("/api/personality/{name}")
        async def get_personality(name: str):
            if not self.personality_loader:
                raise HTTPException(404, "人格加载器未初始化")
            pdir = Path(self.personality_loader.personality_dir)
            fpath = pdir / f"{name}.yaml"
            if not fpath.exists():
                fpath = pdir / f"{name}.yml"
            if not fpath.exists():
                raise HTTPException(404, f"人格文件 {name} 不存在")
            with open(fpath, encoding="utf-8") as f:
                content = f.read()
            preview = ""
            try:
                if self.personality_loader:
                    preview = self.personality_loader.preview_from_content(content)
                else:
                    preview = "(人格加载器未初始化)"
            except Exception as e:
                preview = f"预览生成失败: {e}"
            return {"name": name, "content": content, "preview": preview, "preview_length": len(preview)}

        @self.app.post("/api/personality/{name}")
        async def save_personality(name: str, req: PersonalitySaveRequest):
            if not self.personality_loader:
                raise HTTPException(500, "人格加载器未初始化")
            pdir = Path(self.personality_loader.personality_dir)
            pdir.mkdir(parents=True, exist_ok=True)
            fpath = pdir / f"{name}.yaml"
            try:
                import yaml
                yaml.safe_load(req.content)
            except Exception as e:
                raise HTTPException(400, f"YAML 格式错误: {e}")
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(req.content)
            # 若保存的正是当前激活人格，立即 reload，让运行中的机器人马上用上新设定
            reloaded = False
            if self.personality_loader and self.personality_loader.personality_file == name:
                try:
                    self.personality_loader.reload()
                    reloaded = True
                except Exception as e:
                    raise HTTPException(400, f"文件已保存，但人格重载失败: {e}")
            logger.info(f"[WebConsole] 人格已保存: {name}{'（已即时重载）' if reloaded else ''}")
            return {"success": True, "path": str(fpath), "reloaded": reloaded}

        @self.app.post("/api/personality/{name}/activate")
        async def activate_personality(name: str):
            if not self.personality_loader:
                raise HTTPException(500, "人格加载器未初始化")
            pdir = Path(self.personality_loader.personality_dir)
            fpath = pdir / f"{name}.yaml"
            if not fpath.exists():
                fpath = pdir / f"{name}.yml"
            if not fpath.exists():
                raise HTTPException(404, f"人格文件 {name} 不存在")
            self.personality_loader.personality_file = name
            self.personality_loader.reload()
            # 同步内存配置并写回 .env，保证重启后仍使用该人格
            if self.plugin_config:
                self.plugin_config.personality_file = name
            persisted = True
            try:
                await asyncio.to_thread(self._write_env_updates, {"PERSONALITY_FILE": name})
            except Exception as e:
                logger.error(f"[WebConsole] 人格选择写盘失败: {e}")
                persisted = False
            logger.info(f"[WebConsole] 人格已切换: {self.personality_loader.get_name()}")
            self._push_log("info", f"人格已切换为: {self.personality_loader.get_name()}")
            return {"success": True, "active_name": self.personality_loader.get_name(),
                    "persisted": persisted}

        @self.app.delete("/api/personality/{name}")
        async def delete_personality(name: str):
            if name == "default":
                raise HTTPException(400, "不能删除默认人格")
            pdir = Path(self.personality_loader.personality_dir)
            fpath = pdir / f"{name}.yaml"
            if not fpath.exists():
                fpath = pdir / f"{name}.yml"
            if not fpath.exists():
                raise HTTPException(404, "人格文件不存在")
            fpath.unlink()
            logger.info(f"[WebConsole] 人格已删除: {name}")
            return {"success": True}

        @self.app.post("/api/debug/send")
        async def debug_send(req: DebugSendRequest):
            if not self.llm_client or not self.personality_loader:
                raise HTTPException(500, "LLM或人格未初始化")
            from .llm import ChatMessage
            group_id = req.group_id
            user_id = req.user_id
            if self.short_term:
                await self.short_term.add_user_message(group_id, user_id, req.user_name, req.message)
            messages = [ChatMessage(role="system", content=self.personality_loader.build_system_prompt())]
            if self.short_term:
                messages.extend(await self.short_term.get_context(group_id))
            start = time.time()
            try:
                from .router import pick_model
                mode = req.model
                if mode == "auto":
                    mode = (self.runtime_state or {}).get("reason_mode", "auto")
                reasoner = self.plugin_config.deepseek_reasoner_model if self.plugin_config else "deepseek-reasoner"
                use_model, note = pick_model(req.message, mode, self.llm_client.config.model, reasoner)
                reply = await self.llm_client.chat(
                    messages, metadata={"group_id": group_id, "user_id": user_id},
                    model=use_model,
                )
                latency = int((time.time() - start) * 1000)
                reasoning = getattr(self.llm_client, "last_reasoning", "") or ""
                # 与群聊路径一致，走人格输出后处理，保证调试所见即所得
                try:
                    reply = self.personality_loader.apply_output_filter(reply)
                except Exception:
                    pass
                if self.short_term:
                    await self.short_term.add_assistant_message(group_id, reply)
                self._push_log("info", f"[调试|{use_model}] 群{group_id}: {req.message[:50]} -> {reply[:50]} ({latency}ms)")
                return {"reply": reply, "latency_ms": latency, "used_model": use_model,
                        "route": note, "reasoning": reasoning}
            except Exception as e:
                logger.error(f"[WebConsole] 调试发送失败: {e}")
                raise HTTPException(500, str(e))

        @self.app.get("/api/debug/memory/{group_id}")
        async def debug_memory(group_id: str):
            if not self.short_term:
                return []
            context = await self.short_term.get_context(group_id)
            return [{"role": m.role, "content": m.content} for m in context]

        @self.app.post("/api/debug/memory/clear")
        async def debug_clear_memory(req: MemoryClearRequest):
            if self.short_term:
                await self.short_term.clear(req.group_id)
            if req.clear_long_term and self.long_term:
                await self.long_term.clear_group(req.group_id)
            self._push_log("info", f"已清空群 {req.group_id} 的记忆")
            return {"success": True}

        @self.app.get("/api/debug/long_memory/{group_id}")
        async def debug_long_memory(group_id: str, limit: int = 20):
            if not self.long_term:
                return []
            return await self.long_term.get_recent(group_id, limit)

        def _first_bot():
            import nonebot
            bots = nonebot.get_bots()
            if not bots:
                raise HTTPException(503, "QQ机器人未连接（NapCat未上线）")
            return next(iter(bots.values()))

        @self.app.get("/api/qq/groups")
        async def qq_groups():
            bot = _first_bot()
            groups = await bot.get_group_list()
            return {"self_id": str(bot.self_id), "count": len(groups), "groups": groups}

        @self.app.get("/api/qq/group/{group_id}")
        async def qq_group_info(group_id: str):
            bot = _first_bot()
            try:
                info = await bot.get_group_info(group_id=int(group_id))
                return {"in_group": True, "info": info}
            except Exception as e:
                return {"in_group": False, "error": str(e)}

        @self.app.post("/api/qq/send")
        async def qq_send(data: dict):
            bot = _first_bot()
            gid = data.get("group_id")
            msg = data.get("message", "")
            if not gid or not msg:
                raise HTTPException(400, "需要 group_id 和 message")
            result = await bot.call_api(
                "send_group_msg", group_id=int(gid), message=msg
            )
            return {"success": True, "result": result}

        # ================= 运行状态总览 / 启停控制 / 群友画像 =================
        @self.app.get("/api/status")
        async def get_status():
            import nonebot
            bots = nonebot.get_bots()
            qq = {"connected": bool(bots), "count": len(bots)}
            if bots:
                b = next(iter(bots.values()))
                qq["self_id"] = str(b.self_id)
                try:
                    qq["nickname"] = (await b.get_login_info()).get("nickname")
                except Exception:
                    pass
                try:
                    qq["group_count"] = len(await b.get_group_list())
                except Exception:
                    pass

            ds: dict = {"ok": None}
            if self.llm_client:
                now = time.time()
                if not self._last_ping or now - self._last_ping.get("_t", 0) > 10:
                    p = await self.llm_client.ping()
                    p["_t"] = now
                    self._last_ping = p
                ds = {k: v for k, v in (self._last_ping or {}).items() if k != "_t"}
                ds["chat_model"] = self.llm_client.config.model
                if self.plugin_config:
                    ds["reasoner_model"] = self.plugin_config.deepseek_reasoner_model

            dbstat: dict = {}
            try:
                dbstat["long_term"] = await self.long_term.count() if self.long_term else 0
            except Exception:
                dbstat["long_term"] = -1
            try:
                dbstat["profiles"] = await self.profiles.count() if self.profiles else 0
            except Exception:
                dbstat["profiles"] = -1

            today = await self.token_stats.get_today_summary() if self.token_stats else {}
            return {
                "backend": {"running": True, "uptime": time.time() - _start_time, "pid": os.getpid()},
                "qq": qq,
                "deepseek": ds,
                "database": dbstat,
                "personality": self.personality_loader.get_name() if self.personality_loader else "unknown",
                "runtime": dict(self.runtime_state) if self.runtime_state else {"reply_enabled": True},
                "today": today,
                "server_time": time.time(),
            }

        @self.app.post("/api/llm/test")
        async def llm_test():
            if not self.llm_client:
                raise HTTPException(500, "LLM 未初始化")
            p = await self.llm_client.ping()
            p["_t"] = time.time()
            self._last_ping = p
            return {k: v for k, v in p.items() if k != "_t"}

        @self.app.post("/api/control")
        async def control(data: dict):
            st = self.runtime_state
            if st is None:
                raise HTTPException(500, "运行状态未初始化")
            action = data.get("action", "")
            if action in ("start", "resume"):
                st["reply_enabled"] = True
                msg = "已启动 / 恢复自动回复"
            elif action == "pause":
                st["reply_enabled"] = False
                st["paused_at"] = time.time()
                msg = "已暂停自动回复（QQ 仍在线，只听不回）"
            elif action == "set_reason_mode":
                v = data.get("value", "auto")
                if v not in ("auto", "chat", "reasoner"):
                    raise HTTPException(400, "reason_mode 仅支持 auto/chat/reasoner")
                st["reason_mode"] = v
                msg = f"推理模式已设为 {v}"
            else:
                raise HTTPException(400, f"未知动作: {action}")
            if self.save_runtime_state:
                self.save_runtime_state()
            self._push_log("info", f"[控制台] {msg}")
            return {"success": True, "message": msg, "runtime": dict(st)}

        @self.app.get("/api/profiles")
        async def list_profiles(group_id: str = ""):
            if not self.profiles or not group_id:
                cnt = await self.profiles.count() if self.profiles else 0
                return {"count": cnt, "profiles": []}
            rows = await self.profiles.list_group(group_id, limit=500)
            return {"count": len(rows), "profiles": rows}

        @self.app.post("/api/profiles/update")
        async def update_profile(data: dict):
            if not self.profiles:
                raise HTTPException(500, "画像模块未启用")
            try:
                group_id, user_id = str(data["group_id"]), str(data["user_id"])
                field, value = str(data["field"]), str(data.get("value", ""))
            except KeyError as e:
                raise HTTPException(400, f"缺少参数: {e}")
            try:
                await self.profiles.update_field(group_id, user_id, field, value)
            except ValueError as e:
                raise HTTPException(400, str(e))
            return {"success": True}

        @self.app.post("/api/profiles/delete")
        async def delete_profile(data: dict):
            if not self.profiles:
                raise HTTPException(500, "画像模块未启用")
            await self.profiles.delete(data["group_id"], data["user_id"])
            return {"success": True}

        @self.app.get("/api/config")
        async def get_config():
            if not self.plugin_config:
                return {}
            cfg = self.plugin_config
            return {
                "model": cfg.deepseek_model, "base_url": cfg.deepseek_base_url,
                "temperature": cfg.deepseek_temperature, "max_tokens": cfg.deepseek_max_tokens,
                "personality_file": cfg.personality_file,
                "reply_on_at": cfg.reply_on_at, "reply_on_name": cfg.reply_on_name,
                "trigger_keywords": cfg.trigger_keywords,
                "random_reply_probability": cfg.random_reply_probability,
                "reply_cooldown": cfg.reply_cooldown, "max_message_length": cfg.max_message_length,
                "context_rounds": cfg.context_rounds,
                "enable_long_term_memory": cfg.enable_long_term_memory,
                "enabled_groups": cfg.enabled_groups, "disabled_groups": cfg.disabled_groups,
                "blacklist_users": cfg.blacklist_users,
                "vision_enabled": cfg.vision_enabled, "vision_model": cfg.vision_model,
                "vision_temperature": cfg.vision_temperature, "vision_max_tokens": cfg.vision_max_tokens,
                "vision_max_images": cfg.vision_max_images,
                "vision_require_trigger": cfg.vision_require_trigger,
                "safety_input_enabled": cfg.safety_input_enabled,
                "safety_output_enabled": cfg.safety_output_enabled,
                "rate_limit_user_window": cfg.rate_limit_user_window,
                "rate_limit_user_max": cfg.rate_limit_user_max,
                "rate_limit_group_window": cfg.rate_limit_group_window,
                "rate_limit_group_max": cfg.rate_limit_group_max,
                "tts_enabled": cfg.tts_enabled,
                "tts_provider": cfg.tts_provider,
                "tts_api_url": cfg.tts_api_url,
                "tts_ref_audio": cfg.tts_ref_audio,
                "tts_ref_text": cfg.tts_ref_text,
                "tts_speed": cfg.tts_speed,
                "tts_trigger_mode": cfg.tts_trigger_mode,
                "tts_timeout": cfg.tts_timeout,
                "tts_max_text_len": cfg.tts_max_text_len,
                "tts_minimax_group_id": cfg.tts_minimax_group_id,
                "tts_minimax_api_key": cfg.tts_minimax_api_key,
                "tts_minimax_voice_id": cfg.tts_minimax_voice_id,
                "tts_minimax_api_url": cfg.tts_minimax_api_url,
                "tts_minimax_model": cfg.tts_minimax_model,
                "tts_fish_api_key": cfg.tts_fish_api_key,
                "tts_fish_voice_id": cfg.tts_fish_voice_id,
                "tts_fish_api_url": cfg.tts_fish_api_url,
                "tts_volc_app_id": cfg.tts_volc_app_id,
                "tts_volc_access_token": cfg.tts_volc_access_token,
                "tts_volc_cluster": cfg.tts_volc_cluster,
                "tts_volc_voice_type": cfg.tts_volc_voice_type,
                "tts_volc_api_url": cfg.tts_volc_api_url,
                "asr_enabled": cfg.asr_enabled,
                "asr_model_size": cfg.asr_model_size,
                "asr_language": cfg.asr_language,
                "personality_check_enabled": cfg.personality_check_enabled,
                "personality_check_rule": cfg.personality_check_rule,
                "personality_check_llm": cfg.personality_check_llm,
                "personality_check_max_emoji": cfg.personality_check_max_emoji,
                "emotion_enabled": cfg.emotion_enabled,
                "emotion_model": cfg.emotion_model,
                "emotion_context_rounds": cfg.emotion_context_rounds,
                "voice_enabled": cfg.voice_enabled,
                "voice_default_character": cfg.voice_default_character,
                "voice_pitch_shift": cfg.voice_pitch_shift,
            }

        @self.app.post("/api/config/update")
        async def update_config(req: ConfigUpdateRequest):
            if not self.plugin_config:
                raise HTTPException(500, "配置未初始化")
            cfg = self.plugin_config
            key = req.key
            value = req.value
            if key not in _CONFIG_FIELDS:
                raise HTTPException(400, f"字段 {key} 不支持修改")
            actual_key, env_key, needs_restart = _CONFIG_FIELDS[key]
            if not hasattr(cfg, actual_key):
                raise HTTPException(404, f"配置项 {key} 不存在")

            # 按当前值的目标类型做转换（bool 必须先于 int 判断，bool 是 int 子类）
            current = getattr(cfg, actual_key)
            try:
                if isinstance(current, bool):
                    value = _to_bool(value)
                elif isinstance(current, int):
                    value = int(float(value)) if isinstance(value, str) else int(value)
                elif isinstance(current, float):
                    value = float(value)
                elif isinstance(current, list):
                    if isinstance(value, str):
                        value = [x.strip() for x in value.split(",") if x.strip()]
                    elif isinstance(value, (list, tuple)):
                        value = [str(x).strip() for x in value if str(x).strip()]
                    else:
                        raise ValueError("列表字段需要逗号分隔字符串或数组")
                else:
                    value = str(value)
            except (ValueError, TypeError):
                raise HTTPException(400, f"值类型转换失败: {req.value!r} 无法转为 {type(current).__name__}")

            # 1) 先改内存（主流程运行时直读 plugin_config，立即生效）
            setattr(cfg, actual_key, value)

            # 2) 联动那些在启动时做了快照的运行实例
            if actual_key in ("deepseek_temperature", "deepseek_max_tokens", "deepseek_model") and self.llm_client:
                setattr(self.llm_client.config,
                        {"deepseek_temperature": "temperature",
                         "deepseek_max_tokens": "max_tokens",
                         "deepseek_model": "model"}[actual_key], value)
            # 视觉参数运行时直读 llm_client 的 vision_* 属性，改内存即生效
            if actual_key in ("vision_model", "vision_temperature", "vision_max_tokens") and self.llm_client:
                setattr(self.llm_client,
                        {"vision_model": "vision_model",
                         "vision_temperature": "vision_temperature",
                         "vision_max_tokens": "vision_max_tokens"}[actual_key], value)
            if actual_key == "context_rounds" and self.short_term is not None \
                    and hasattr(self.short_term, "max_rounds"):
                self.short_term.max_rounds = value
            # 内容安全：改开关后同步更新运行时 SafetyFilter 实例
            if actual_key in ("safety_input_enabled", "safety_output_enabled") and self.safety_filter:
                setattr(self.safety_filter, actual_key, value)
            # TTS：改任何 tts_* 配置后重新注入运行时
            if actual_key.startswith("tts_"):
                try:
                    from . import tts as tts_mod
                    tts_mod.configure_tts(cfg)
                except Exception as e:
                    logger.warning(f"[WebConsole] TTS配置联动失败: {e}")
            # ASR：改任何 asr_* 配置后重新注入运行时
            if actual_key.startswith("asr_"):
                try:
                    from . import asr as asr_mod
                    asr_mod.configure_asr(cfg)
                except Exception as e:
                    logger.warning(f"[WebConsole] ASR配置联动失败: {e}")
            # 人格校验：改任何 personality_check_* 配置后重新注入运行时
            if actual_key.startswith("personality_check_"):
                try:
                    from . import personality_check as pc_mod
                    pc_mod.configure_personality_check(cfg)
                except Exception as e:
                    logger.warning(f"[WebConsole] 人格校验配置联动失败: {e}")
            # 情绪判定：改任何 emotion_* 配置后重新注入运行时
            if actual_key.startswith("emotion_"):
                try:
                    from . import emotion as emotion_mod
                    emotion_mod.configure_emotion(cfg)
                except Exception as e:
                    logger.warning(f"[WebConsole] 情绪判定配置联动失败: {e}")
            # 音色管理：改任何 voice_* 配置后重新注入运行时
            if actual_key.startswith("voice_"):
                try:
                    from . import voice_manager as voice_mod
                    voice_mod.configure_voice(cfg)
                except Exception as e:
                    logger.warning(f"[WebConsole] 音色管理配置联动失败: {e}")
            # 启动时未建立长期记忆对象的情况下，临时开启无法生效，需重启
            if actual_key == "enable_long_term_memory" and value and self.long_term is None:
                needs_restart = True

            # 3) 真正写回 .env，保证重启不丢
            try:
                await asyncio.to_thread(self._write_env_updates, {env_key: value})
                persisted = True
            except Exception as e:
                logger.error(f"[WebConsole] 写回 .env 失败: {e}")
                persisted = False
                self._push_log("error", f"配置已在内存生效但写盘失败: {e}")

            self._push_log("info", f"配置已更新并保存: {key} = {value}"
                                   + ("（需重启完全生效）" if needs_restart else ""))
            return {"success": True, "key": key, "value": value,
                    "persisted": persisted, "needs_restart": needs_restart}

        @self.app.get("/api/logs")
        async def get_logs(limit: int = 300, level: str = ""):
            """获取历史日志缓冲（实时日志页初始化时加载）"""
            logs = list(self._log_buffer)
            if level:
                logs = [l for l in logs if l["level"] == level]
            return {"logs": logs[-limit:], "total": len(self._log_buffer)}

        # ===== 音色管理 API =====
        @self.app.get("/api/voices")
        async def list_voices(character: str = ""):
            """列出角色的所有可用音色（参考音频文件是否存在）"""
            try:
                from . import voice_manager
                result = voice_manager.list_available_voices(character or None)
                return {"character": character or voice_manager._config["default_character"], "voices": result}
            except Exception as e:
                return {"error": str(e), "voices": {}}

        @self.app.get("/api/voices/config")
        async def get_voice_config():
            """获取voice_config.json完整内容"""
            try:
                from . import voice_manager
                config = voice_manager.load_voice_config(force_reload=True)
                return {"config": config, "config_file": str(voice_manager.CONFIG_FILE)}
            except Exception as e:
                return {"error": str(e), "config": {}}

        @self.app.post("/api/voices/config")
        async def update_voice_config(req: Request):
            """更新voice_config.json"""
            try:
                body = await req.json()
                config = body.get("config")
                if not isinstance(config, dict):
                    return {"success": False, "error": "config必须是对象"}
                from . import voice_manager
                ok = voice_manager.save_voice_config(config)
                return {"success": ok}
            except Exception as e:
                return {"success": False, "error": str(e)}

        @self.app.post("/api/voices/upload")
        async def upload_voice_audio(
            emotion: str = Form(...),
            audio_file: UploadFile = File(...),
            ref_text: str = Form(""),
            character: str = Form(""),
        ):
            """上传情绪参考音频到音色池"""
            try:
                from . import voice_manager
                char = character or voice_manager._config["default_character"]
                # 校验情绪标签合法性
                valid_emotions = ["default", "happy", "excited", "sad", "angry", "calm",
                                  "coquettish", "surprised", "thinking", "gentle", "playful"]
                if emotion not in valid_emotions:
                    return {"success": False, "error": f"无效的情绪标签: {emotion}"}
                # 读取文件内容
                content = await audio_file.read()
                if len(content) < 1000:
                    return {"success": False, "error": "文件太小，可能不是有效音频"}
                # 保存到音色池目录
                char_dir = voice_manager.VOICES_DIR / char
                char_dir.mkdir(parents=True, exist_ok=True)
                wav_path = char_dir / f"{emotion}.wav"
                with open(wav_path, "wb") as f:
                    f.write(content)
                # 更新voice_config.json里的ref_text
                if ref_text:
                    config = voice_manager.load_voice_config(force_reload=True)
                    if char in config and emotion in config[char]:
                        config[char][emotion]["ref_text"] = ref_text
                        voice_manager.save_voice_config(config)
                logger.info(f"[WebConsole] 上传参考音频: {char}/{emotion}.wav ({len(content)}字节)")
                return {
                    "success": True,
                    "emotion": emotion,
                    "character": char,
                    "file_size": len(content),
                    "file_path": str(wav_path),
                    "ref_text": ref_text,
                }
            except Exception as e:
                logger.error(f"[WebConsole] 上传参考音频失败: {e}")
                return {"success": False, "error": str(e)}

        @self.app.get("/api/emotion/stats")
        async def get_emotion_stats():
            """获取情绪判定统计"""
            try:
                from . import emotion
                return {"stats": emotion.get_stats(), "last_emotion": emotion.get_last_emotion()}
            except Exception as e:
                return {"error": str(e), "stats": {}}

        @self.app.websocket("/ws/logs")
        async def ws_logs(websocket: WebSocket):
            await websocket.accept()
            if self.token:
                try:
                    auth_data = await asyncio.wait_for(websocket.receive_json(), timeout=5)
                    if auth_data.get("token") != self.token:
                        await websocket.close(code=4401)
                        return
                except Exception:
                    await websocket.close(code=4401)
                    return
            self._ws_clients.append(websocket)
            for log in self._log_buffer[-100:]:
                await websocket.send_json(log)
            try:
                while True:
                    await websocket.receive_text()
            except WebSocketDisconnect:
                pass
            finally:
                if websocket in self._ws_clients:
                    self._ws_clients.remove(websocket)

        logger.info(f"[WebConsole] 路由已注册，控制台地址: http://{self.host}:{self.port}")

    @staticmethod
    def _serialize_env_value(v: Any) -> str:
        """把 Python 值序列化成可写回 .env 的字符串（与 NoneBot/python-dotenv 解析兼容）。"""
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (list, tuple, set)):
            return json.dumps([str(x) for x in v], ensure_ascii=False)
        s = str(v)
        # 含空格 / # / 换行等需要用双引号包裹，避免 dotenv 解析截断
        if s and (any(ch in s for ch in (" ", "#", "\n", "\t")) or s != s.strip()):
            s = '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'
        return s

    def _write_env_updates(self, updates: dict[str, Any]) -> str:
        """
        把若干 {ENV_KEY: value} 写回 .env：已存在的键原地替换整行，不存在的追加；
        其余行（注释/空行/其它配置）原样保留。原子替换，返回最终文件路径字符串。
        同步方法，调用处用 asyncio.to_thread 包裹以免阻塞事件循环。
        """
        if not updates:
            return str(self.env_path)
        serialized = {k: self._serialize_env_value(v) for k, v in updates.items()}
        with self._env_lock:
            lines = []
            if self.env_path.exists():
                lines = self.env_path.read_text(encoding="utf-8").splitlines()
            seen: set[str] = set()
            out: list[str] = []
            for line in lines:
                m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
                if m and m.group(1) in serialized:
                    key = m.group(1)
                    out.append(f"{key}={serialized[key]}")
                    seen.add(key)
                else:
                    out.append(line)
            for key, val in serialized.items():
                if key not in seen:
                    if out and out[-1].strip() != "":
                        pass
                    out.append(f"{key}={val}")
            new_text = "\n".join(out)
            if not new_text.endswith("\n"):
                new_text += "\n"
            tmp = self.env_path.with_suffix(".env.tmpwrite")
            tmp.write_text(new_text, encoding="utf-8")
            os.replace(str(tmp), str(self.env_path))
        return str(self.env_path)

    def _install_log_capture(self):
        """安装日志拦截器：把 nonebot(loguru) 运行日志推送到控制台 WebSocket（实时日志页）"""
        import logging
        from loguru import logger as loguru_logger

        console = self  # 闭包捕获

        def _loguru_sink(message):
            try:
                record = message.record
                # 过滤高频无意义日志
                name = record["name"]
                if name in ("uvicorn.access", "uvicorn.error", "httpx", "httpcore"):
                    return
                if "websocket" in name.lower():
                    return
                level = record["level"].name.lower()
                if level == "critical":
                    level = "error"
                if level not in ("info", "warning", "error", "debug"):
                    level = "info"
                msg = record["message"]
                # loguru 的 message 可能包含格式化后的完整字符串
                console._push_log(level, msg)
            except Exception:
                pass

        # loguru sink（nonebot 主日志）
        try:
            loguru_logger.add(_loguru_sink, level="INFO", filter=lambda r: r["level"].name != "TRACE")
        except Exception as e:
            logger.warning(f"[WebConsole] loguru sink 安装失败: {e}")

        # 标准 logging handler（兜底，捕获非 loguru 的日志）
        class _ConsoleLogHandler(logging.Handler):
            def emit(self, record):
                try:
                    if record.name in ("uvicorn.access", "uvicorn.error", "httpx", "httpcore"):
                        return
                    msg = self.format(record)
                    level = record.levelname.lower()
                    if level == "critical":
                        level = "error"
                    if level not in ("info", "warning", "error", "debug"):
                        level = "info"
                    console._push_log(level, msg)
                except Exception:
                    pass

        self._log_handler = _ConsoleLogHandler()
        self._log_handler.setLevel(logging.INFO)
        self._log_handler.setFormatter(logging.Formatter("%(message)s"))
        logging.getLogger().addHandler(self._log_handler)
        logging.getLogger("nonebot").addHandler(self._log_handler)
        logging.getLogger("ai_chat").addHandler(self._log_handler)
        logger.info("[WebConsole] 日志拦截器已安装(loguru+logging)，运行日志将实时推送到控制台")

    def _push_log(self, level: str, message: str):
        entry = {"time": time.strftime("%H:%M:%S"), "timestamp": time.time(), "level": level, "message": message}
        self._log_buffer.append(entry)
        if len(self._log_buffer) > self._max_log_buffer:
            self._log_buffer = self._log_buffer[-self._max_log_buffer:]
        for ws in self._ws_clients:
            try:
                asyncio.create_task(ws.send_json(entry))
            except Exception:
                pass

    def log(self, level: str, message: str):
        self._push_log(level, message)


_console: Optional[WebConsole] = None
_start_time = time.time()


def init_console(**kwargs) -> WebConsole:
    global _console
    _console = WebConsole(**kwargs)
    _console.setup()
    return _console


def get_console() -> Optional[WebConsole]:
    return _console
