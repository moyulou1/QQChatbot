"""
对话记忆系统
- 短期记忆：每个群维护最近 N 轮对话上下文（内存中）
- 长期记忆：将重要信息持久化到 SQLite，按群+用户维度存储
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import aiosqlite
from nonebot import logger

from .llm import ChatMessage


@dataclass
class ShortTermMemory:
    """短期记忆：单个群的对话上下文"""
    group_id: str
    max_rounds: int = 10
    messages: deque = field(default_factory=deque)
    last_active: float = field(default_factory=time.time)

    def add_user(self, user_id: str, user_name: str, content: str):
        tagged_content = f"[{user_name}({user_id})] {content}"
        self.messages.append(ChatMessage(role="user", content=tagged_content))
        self.last_active = time.time()
        self._trim()

    def add_assistant(self, content: str):
        self.messages.append(ChatMessage(role="assistant", content=content))
        self.last_active = time.time()
        self._trim()

    def _trim(self):
        max_messages = self.max_rounds * 2
        while len(self.messages) > max_messages:
            self.messages.popleft()

    def get_context(self) -> list[ChatMessage]:
        return list(self.messages)

    def clear(self):
        self.messages.clear()


class ShortTermMemoryManager:
    """短期记忆管理器"""

    def __init__(self, max_rounds: int = 10, ttl_seconds: int = 3600):
        self.max_rounds = max_rounds
        self.ttl_seconds = ttl_seconds
        self._memories: dict[str, ShortTermMemory] = {}
        self._lock = asyncio.Lock()

    async def get(self, group_id: str) -> ShortTermMemory:
        async with self._lock:
            if group_id not in self._memories:
                self._memories[group_id] = ShortTermMemory(
                    group_id=group_id, max_rounds=self.max_rounds,
                )
            return self._memories[group_id]

    async def add_user_message(self, group_id: str, user_id: str, user_name: str, content: str):
        mem = await self.get(group_id)
        mem.add_user(user_id, user_name, content)

    async def add_assistant_message(self, group_id: str, content: str):
        mem = await self.get(group_id)
        mem.add_assistant(content)

    async def get_context(self, group_id: str) -> list[ChatMessage]:
        mem = await self.get(group_id)
        return mem.get_context()

    async def clear(self, group_id: str):
        async with self._lock:
            if group_id in self._memories:
                self._memories[group_id].clear()

    async def cleanup_expired(self):
        now = time.time()
        async with self._lock:
            expired = [gid for gid, mem in self._memories.items() if now - mem.last_active > self.ttl_seconds]
            for gid in expired:
                del self._memories[gid]
            if expired:
                logger.info(f"[Memory] 清理了 {len(expired)} 个过期群的短期记忆")


class LongTermMemory:
    """长期记忆：SQLite 持久化存储"""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._initialized = False

    async def _init_db(self):
        if self._initialized:
            return
        async with self._lock:
            if self._initialized:
                return
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS memories (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        group_id TEXT NOT NULL,
                        user_id TEXT NOT NULL DEFAULT '',
                        memory_type TEXT NOT NULL DEFAULT 'general',
                        content TEXT NOT NULL,
                        importance INTEGER DEFAULT 5,
                        created_at REAL NOT NULL,
                        last_accessed REAL NOT NULL,
                        access_count INTEGER DEFAULT 1
                    )
                """)
                await db.execute("CREATE INDEX IF NOT EXISTS idx_memories_group ON memories(group_id)")
                await db.execute("CREATE INDEX IF NOT EXISTS idx_memories_group_user ON memories(group_id, user_id)")
                # 清理旧版 FTS5 残留（升级时执行一次，之后不再使用 FTS）
                for trig in ("memories_ai", "memories_ad", "memories_au"):
                    await db.execute(f"DROP TRIGGER IF EXISTS {trig}")
                await db.execute("DROP TABLE IF EXISTS memories_fts")
                await db.commit()
            self._initialized = True
            logger.info(f"[Memory] 长期记忆数据库已初始化: {self.db_path}")

    async def add(self, group_id: str, content: str, user_id: str = "",
                  memory_type: str = "general", importance: int = 5):
        await self._init_db()
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO memories (group_id, user_id, memory_type, content, importance, created_at, last_accessed)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (group_id, user_id, memory_type, content, importance, now, now),
            )
            await db.commit()

    @staticmethod
    def _extract_search_keywords(text: str, max_kw: int = 8) -> list[str]:
        """
        从查询文本中提取搜索关键词：2字滑动窗口，过滤停用词和重复。
        中文友好，无需额外分词依赖。
        """
        if not text:
            return []
        clean = re.sub(r"[^\w\u4e00-\u9fff]", "", text)
        if len(clean) <= 2:
            return [clean] if clean else []
        # 停用字（含这些字的2字词大概率无意义）
        stop_chars = set("的了是我你他她它在有和与或啊呀吧呢吗么这那个们说看做要就都也还又再很最更不没无非未把被让给向从到对为以于上下中里前后")
        kws: list[str] = []
        seen: set[str] = set()
        for i in range(len(clean) - 1):
            kw = clean[i:i + 2]
            if kw[0] in stop_chars or kw[1] in stop_chars:
                continue
            if kw not in seen:
                seen.add(kw)
                kws.append(kw)
                if len(kws) >= max_kw:
                    break
        # 2字词太少时，加入完整文本兜底
        if len(kws) < 2 and clean:
            kws.insert(0, clean)
        return kws

    async def query(self, group_id: str, keyword: str = "", user_id: str = "", limit: int = 5) -> list[dict]:
        await self._init_db()
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if not keyword:
                # 无关键词：按重要度+最近访问排序
                conditions = ["group_id = ?"]
                params: list[Any] = [group_id]
                if user_id:
                    conditions.append("user_id = ?")
                    params.append(user_id)
                where = " AND ".join(conditions)
                cursor = await db.execute(
                    f"SELECT * FROM memories WHERE {where} ORDER BY importance DESC, last_accessed DESC LIMIT ?",
                    params + [limit],
                )
                rows = await cursor.fetchall()
            else:
                # 有关键词：2字滑动窗口分词，多关键词 OR 匹配，按匹配数(相关性)+重要度排序
                kws = self._extract_search_keywords(keyword)
                if not kws:
                    kws = [keyword]
                like_params = [f"%{kw}%" for kw in kws]
                like_clauses = " OR ".join(["content LIKE ?"] * len(kws))
                where = f"group_id = ? AND ({like_clauses})"
                where_params: list[Any] = [group_id] + like_params
                if user_id:
                    where += " AND user_id = ?"
                    where_params.append(user_id)
                # ORDER BY 里的 CASE WHEN 也需要传一次 like_params
                match_cases = " + ".join(["CASE WHEN content LIKE ? THEN 1 ELSE 0 END"] * len(kws))
                order_sql = f"ORDER BY ({match_cases}) DESC, importance DESC, last_accessed DESC LIMIT ?"
                sql = f"SELECT * FROM memories WHERE {where} {order_sql}"
                cursor = await db.execute(sql, where_params + like_params + [limit])
                rows = await cursor.fetchall()
            # 更新访问计数
            for row in rows:
                await db.execute(
                    "UPDATE memories SET last_accessed = ?, access_count = access_count + 1 WHERE id = ?",
                    (now, row["id"]),
                )
            await db.commit()
            result = [dict(row) for row in rows]
            if keyword:
                logger.debug(f"[Memory] 查询关键词='{keyword}' 分词={kws} 命中{len(result)}条")
            return result

    async def get_recent(self, group_id: str, limit: int = 10) -> list[dict]:
        await self._init_db()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM memories WHERE group_id = ? ORDER BY created_at DESC LIMIT ?",
                (group_id, limit),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def delete(self, memory_id: int):
        await self._init_db()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            await db.commit()

    async def clear_group(self, group_id: str):
        await self._init_db()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM memories WHERE group_id = ?", (group_id,))
            await db.commit()

    async def count(self, group_id: str = "") -> int:
        await self._init_db()
        async with aiosqlite.connect(self.db_path) as db:
            if group_id:
                cursor = await db.execute("SELECT COUNT(*) FROM memories WHERE group_id = ?", (group_id,))
            else:
                cursor = await db.execute("SELECT COUNT(*) FROM memories")
            row = await cursor.fetchone()
            return row[0] if row else 0

    def format_memories_for_prompt(self, memories: list[dict]) -> str:
        if not memories:
            return ""
        lines = ["## 关于本群的长期记忆（仅供参考，不要逐条提及）"]
        for m in memories:
            content = m["content"]
            user = m.get("user_id", "")
            if user:
                lines.append(f"- (用户{user}) {content}")
            else:
                lines.append(f"- {content}")
        return "\n".join(lines)
