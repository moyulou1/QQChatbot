"""
Token 消耗统计模块
- 记录每次 LLM 调用的 token 用量
- 按日/周/月/群 维度统计
- 估算费用（DeepSeek 官方定价）
- SQLite 持久化
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import aiosqlite
from nonebot import logger

DEEPSEEK_PRICING = {
    "deepseek-chat": {"input": 1.0, "output": 2.0, "cache_input": 0.1},
    "deepseek-reasoner": {"input": 4.0, "output": 16.0, "cache_input": 0.4},
}
DEFAULT_PRICING = {"input": 2.0, "output": 4.0, "cache_input": 0.2}


@dataclass
class TokenUsage:
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    reasoning_tokens: int = 0
    group_id: str = ""
    user_id: str = ""
    latency_ms: int = 0
    timestamp: float = 0

    def estimate_cost(self) -> float:
        pricing = DEEPSEEK_PRICING.get(self.model, DEFAULT_PRICING)
        normal_input = max(0, self.prompt_tokens - self.cache_read_input_tokens)
        cost = (
            normal_input * pricing["input"]
            + self.cache_read_input_tokens * pricing.get("cache_input", 0.1)
            + self.completion_tokens * pricing["output"]
        ) / 1_000_000
        return round(cost, 6)


class TokenStats:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._initialized = False
        self._today_cache: dict[str, Any] = {}
        self._today_date: str = ""

    async def _init_db(self):
        if self._initialized:
            return
        async with self._lock:
            if self._initialized:
                return
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS token_usage (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp REAL NOT NULL,
                        date TEXT NOT NULL,
                        model TEXT NOT NULL,
                        group_id TEXT DEFAULT '',
                        user_id TEXT DEFAULT '',
                        prompt_tokens INTEGER DEFAULT 0,
                        completion_tokens INTEGER DEFAULT 0,
                        total_tokens INTEGER DEFAULT 0,
                        cache_read_tokens INTEGER DEFAULT 0,
                        reasoning_tokens INTEGER DEFAULT 0,
                        latency_ms INTEGER DEFAULT 0,
                        cost REAL DEFAULT 0
                    )
                """)
                await db.execute("CREATE INDEX IF NOT EXISTS idx_token_date ON token_usage(date)")
                await db.execute("CREATE INDEX IF NOT EXISTS idx_token_group ON token_usage(group_id, date)")
                await db.execute("CREATE INDEX IF NOT EXISTS idx_token_model ON token_usage(model, date)")
                await db.commit()
            self._initialized = True
            logger.info(f"[TokenStats] 数据库已初始化: {self.db_path}")

    async def record(self, usage: TokenUsage):
        await self._init_db()
        if usage.timestamp == 0:
            usage.timestamp = time.time()
        date_str = time.strftime("%Y-%m-%d", time.localtime(usage.timestamp))
        cost = usage.estimate_cost()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO token_usage
                   (timestamp, date, model, group_id, user_id,
                    prompt_tokens, completion_tokens, total_tokens,
                    cache_read_tokens, reasoning_tokens, latency_ms, cost)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (usage.timestamp, date_str, usage.model, usage.group_id, usage.user_id,
                 usage.prompt_tokens, usage.completion_tokens, usage.total_tokens,
                 usage.cache_read_input_tokens, usage.reasoning_tokens,
                 usage.latency_ms, cost),
            )
            await db.commit()
        self._today_cache = {}

    async def get_today_summary(self) -> dict[str, Any]:
        today = time.strftime("%Y-%m-%d")
        if self._today_date == today and self._today_cache:
            return self._today_cache
        await self._init_db()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT COUNT(*) as call_count,
                    COALESCE(SUM(prompt_tokens),0) as prompt_tokens,
                    COALESCE(SUM(completion_tokens),0) as completion_tokens,
                    COALESCE(SUM(total_tokens),0) as total_tokens,
                    COALESCE(SUM(cost),0) as total_cost,
                    COALESCE(AVG(latency_ms),0) as avg_latency
                   FROM token_usage WHERE date = ?""",
                (today,),
            )
            row = await cursor.fetchone()
            cursor2 = await db.execute(
                """SELECT model, COUNT(*) as cnt, COALESCE(SUM(total_tokens),0) as tokens, COALESCE(SUM(cost),0) as cost
                   FROM token_usage WHERE date = ? GROUP BY model ORDER BY tokens DESC""",
                (today,),
            )
            by_model = [dict(r) for r in await cursor2.fetchall()]
            cursor3 = await db.execute(
                """SELECT group_id, COUNT(*) as cnt, COALESCE(SUM(total_tokens),0) as tokens, COALESCE(SUM(cost),0) as cost
                   FROM token_usage WHERE date = ? AND group_id != ''
                   GROUP BY group_id ORDER BY tokens DESC LIMIT 10""",
                (today,),
            )
            by_group = [dict(r) for r in await cursor3.fetchall()]

        result = {
            "date": today,
            "call_count": row["call_count"] if row else 0,
            "prompt_tokens": row["prompt_tokens"] if row else 0,
            "completion_tokens": row["completion_tokens"] if row else 0,
            "total_tokens": row["total_tokens"] if row else 0,
            "total_cost": round(row["total_cost"], 4) if row else 0,
            "avg_latency_ms": round(row["avg_latency"], 1) if row else 0,
            "by_model": by_model,
            "by_group": by_group,
        }
        self._today_cache = result
        self._today_date = today
        return result

    async def get_daily_history(self, days: int = 30) -> list[dict]:
        await self._init_db()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT date, COUNT(*) as call_count,
                    COALESCE(SUM(prompt_tokens),0) as prompt_tokens,
                    COALESCE(SUM(completion_tokens),0) as completion_tokens,
                    COALESCE(SUM(total_tokens),0) as total_tokens,
                    COALESCE(SUM(cost),0) as total_cost
                   FROM token_usage WHERE date >= date('now', ?)
                   GROUP BY date ORDER BY date ASC""",
                (f"-{days} days",),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_recent_calls(self, limit: int = 50) -> list[dict]:
        await self._init_db()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM token_usage ORDER BY timestamp DESC LIMIT ?", (limit,),
            )
            rows = await cursor.fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["time_str"] = time.strftime("%H:%M:%S", time.localtime(d["timestamp"]))
                result.append(d)
            return result

    async def get_group_stats(self, group_id: str, days: int = 7) -> dict:
        await self._init_db()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT COUNT(*) as call_count, COALESCE(SUM(total_tokens),0) as total_tokens,
                    COALESCE(SUM(cost),0) as total_cost
                   FROM token_usage WHERE group_id = ? AND date >= date('now', ?)""",
                (group_id, f"-{days} days"),
            )
            row = await cursor.fetchone()
            return dict(row) if row else {"call_count": 0, "total_tokens": 0, "total_cost": 0}

    async def get_total_summary(self) -> dict:
        await self._init_db()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT COUNT(*) as call_count, COALESCE(SUM(total_tokens),0) as total_tokens,
                    COALESCE(SUM(cost),0) as total_cost, MIN(date) as first_date, MAX(date) as last_date
                   FROM token_usage"""
            )
            row = await cursor.fetchone()
            return dict(row) if row else {}
