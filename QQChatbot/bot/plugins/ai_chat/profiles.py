"""
群友画像系统（需求3）
- 按 群 + QQ 维度为每个群友建立结构化档案：称呼、性别、年龄、职业、所在地、爱好、性格、关系、事实
- 每轮对话后用 LLM 从发言中抽取稳定信息（带节流，省 token），增量合并入库
- 回复前把相关群友档案格式化注入 system prompt，让机器人像真的认识每个群友
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Optional

import aiosqlite
from nonebot import logger

from .llm import ChatMessage


_EXTRACT_INSTRUCTION = """你是一个「群友信息抽取器」。根据某群友的【最新发言】和【已有档案】，抽取关于这个群友本人的、相对稳定的信息（名字/称呼、性别、年龄或年龄段、职业或身份、所在地、爱好、性格、与他人的关系、其他值得记住的事实）。

要求：
1. 只记录该群友本人明确透露、或能合理推断的信息；不要臆测，不确定就留空字符串或空数组。
2. has_new_info 仅当本次发言带来了可更新的信息时才为 true；纯闲聊、表情、@机器人、无信息量内容一律 false。
3. display_name 填“希望被怎么称呼”（自报姓名优先，否则留空）。
4. age 用简短文本，如 "23" / "约20" / "大学生(约20)"；gender 取 "男"/"女"/"未知"。
5. hobbies/facts 用简短中文短语数组；summary 用一句话概括这个人（第二/三人称均可，不超过40字）。
输出 JSON，字段固定为：
{"has_new_info":bool,"display_name":str,"gender":str,"age":str,"occupation":str,"location":str,"hobbies":[str],"personality":str,"relationship":str,"facts":[str],"summary":str}"""


class UserProfileStore:
    def __init__(self, db_path: str, extract_gap: int = 90):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.extract_gap = extract_gap
        self._initialized = False
        self._lock = asyncio.Lock()
        self._last_extract: dict[tuple[str, str], float] = {}

    async def _init_db(self):
        if self._initialized:
            return
        async with self._lock:
            if self._initialized:
                return
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS user_profiles (
                        group_id TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        display_name TEXT DEFAULT '',
                        gender TEXT DEFAULT '',
                        age TEXT DEFAULT '',
                        occupation TEXT DEFAULT '',
                        location TEXT DEFAULT '',
                        personality TEXT DEFAULT '',
                        relationship TEXT DEFAULT '',
                        hobbies TEXT DEFAULT '[]',
                        facts TEXT DEFAULT '[]',
                        summary TEXT DEFAULT '',
                        qq_nickname TEXT DEFAULT '',
                        first_seen REAL NOT NULL,
                        last_seen REAL NOT NULL,
                        msg_count INTEGER DEFAULT 0,
                        PRIMARY KEY (group_id, user_id)
                    )
                """)
                await db.execute("CREATE INDEX IF NOT EXISTS idx_profiles_group ON user_profiles(group_id, last_seen)")
                await db.commit()
            self._initialized = True
            logger.info(f"[Profile] 群友画像库已初始化: {self.db_path}")

    @staticmethod
    def _row_to_dict(row) -> dict[str, Any]:
        d = dict(row)
        for k in ("hobbies", "facts"):
            try:
                d[k] = json.loads(d.get(k) or "[]")
            except Exception:
                d[k] = []
        return d

    async def observe_seen(self, group_id: str, user_id: str, qq_nickname: str = "", card: str = ""):
        """每次该群友发言都调用：更新活跃时间/发言数，并用 QQ 昵称兜底初始化称呼。"""
        await self._init_db()
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO user_profiles(group_id,user_id,display_name,qq_nickname,first_seen,last_seen,msg_count)
                   VALUES(?,?,?,?,?,?,1)
                   ON CONFLICT(group_id,user_id) DO UPDATE SET
                     last_seen=excluded.last_seen,
                     msg_count=msg_count+1,
                     qq_nickname=CASE WHEN user_profiles.qq_nickname='' THEN excluded.qq_nickname ELSE user_profiles.qq_nickname END""",
                (group_id, user_id, (card or qq_nickname or ""), qq_nickname or "", now, now),
            )
            await db.commit()

    async def get(self, group_id: str, user_id: str) -> Optional[dict[str, Any]]:
        await self._init_db()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM user_profiles WHERE group_id=? AND user_id=?",
                                   (group_id, user_id))
            row = await cur.fetchone()
            return self._row_to_dict(row) if row else None

    async def list_group(self, group_id: str, limit: int = 200) -> list[dict[str, Any]]:
        await self._init_db()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM user_profiles WHERE group_id=? ORDER BY last_seen DESC LIMIT ?",
                (group_id, limit))
            return [self._row_to_dict(r) for r in await cur.fetchall()]

    async def count(self, group_id: str = "") -> int:
        await self._init_db()
        async with aiosqlite.connect(self.db_path) as db:
            if group_id:
                cur = await db.execute("SELECT COUNT(*) FROM user_profiles WHERE group_id=?", (group_id,))
            else:
                cur = await db.execute("SELECT COUNT(*) FROM user_profiles")
            return (await cur.fetchone())[0]

    async def delete(self, group_id: str, user_id: str):
        await self._init_db()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM user_profiles WHERE group_id=? AND user_id=?", (group_id, user_id))
            await db.commit()

    async def clear_group(self, group_id: str):
        await self._init_db()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM user_profiles WHERE group_id=?", (group_id,))
            await db.commit()

    async def update_field(self, group_id: str, user_id: str, field: str, value: str):
        allowed = {"display_name", "gender", "age", "occupation", "location", "personality",
                   "relationship", "summary"}
        if field not in allowed:
            raise ValueError(f"字段 {field} 不允许手动修改")
        await self._init_db()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(f"UPDATE user_profiles SET {field}=? WHERE group_id=? AND user_id=?",
                             (value, group_id, user_id))
            await db.commit()

    @staticmethod
    def _merge_list(old: list[str], new: list[str], cap: int = 12) -> list[str]:
        out, seen = [], set()
        for x in (old or []) + (new or []):
            x = (str(x) or "").strip()
            if x and x not in seen:
                seen.add(x); out.append(x)
        return out[:cap]

    async def apply_extract(self, group_id: str, user_id: str, e: dict[str, Any]) -> bool:
        """把 LLM 抽取结果合并进既有档案。返回是否真的写入。"""
        if not isinstance(e, dict) or not e.get("has_new_info"):
            return False
        cur = await self.get(group_id, user_id)
        if cur is None:
            await self.observe_seen(group_id, user_id)
            cur = await self.get(group_id, user_id) or {}

        def pick(key, fallback=""):
            v = e.get(key, fallback)
            # None / 空串 / “未知” 都表示本次没提到，必须保留旧值，绝不能用空值覆盖
            if v is None or (isinstance(v, str) and (not v.strip() or v.strip() in ("未知", "null", "无"))):
                return fallback
            return str(v).strip()

        gender = pick("gender", cur.get("gender", ""))
        if gender == "未知":
            gender = cur.get("gender", "")
        merged = {
            "display_name": pick("display_name", cur.get("display_name", "")),
            "gender": gender,
            "age": pick("age", cur.get("age", "")),
            "occupation": pick("occupation", cur.get("occupation", "")),
            "location": pick("location", cur.get("location", "")),
            "personality": pick("personality", cur.get("personality", "")),
            "relationship": pick("relationship", cur.get("relationship", "")),
            "summary": pick("summary", cur.get("summary", "")),
            "hobbies": json.dumps(self._merge_list(cur.get("hobbies", []), e.get("hobbies", [])),
                                  ensure_ascii=False),
            "facts": json.dumps(self._merge_list(cur.get("facts", []), e.get("facts", [])),
                                ensure_ascii=False),
        }
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                UPDATE user_profiles SET display_name=?,gender=?,age=?,occupation=?,location=?,
                  personality=?,relationship=?,summary=?,hobbies=?,facts=? WHERE group_id=? AND user_id=?""",
                (merged["display_name"], merged["gender"], merged["age"], merged["occupation"],
                 merged["location"], merged["personality"], merged["relationship"], merged["summary"],
                 merged["hobbies"], merged["facts"], group_id, user_id))
            await db.commit()
        return True

    async def maybe_extract_with_llm(self, llm, group_id: str, user_id: str,
                                     user_text: str, current_reply: str = "") -> bool:
        """带节流的画像抽取。返回是否抽取并写入。"""
        if not llm or not user_text or len(user_text.strip()) < 4:
            return False
        now = time.time()
        last = self._last_extract.get((group_id, user_id), 0)
        if now - last < self.extract_gap:
            return False
        self._last_extract[(group_id, user_id)] = now
        try:
            prof = await self.get(group_id, user_id)
            existing = json.dumps(prof, ensure_ascii=False) if prof else "{}"
            content = f"【已有档案】{existing}\n【最新发言】{user_text[:300]}"
            e = await llm.chat_json(_EXTRACT_INSTRUCTION, content, max_tokens=500)
            ok = await self.apply_extract(group_id, user_id, e or {})
            if ok:
                logger.info(f"[Profile] 更新群友画像 {group_id}/{user_id}: "
                            f"{(e or {}).get('display_name','')}/{(e or {}).get('gender','')}/"
                            f"{(e or {}).get('age','')}")
            return ok
        except Exception as ex:
            logger.warning(f"[Profile] 画像抽取失败: {ex}")
            return False

    @staticmethod
    def _brief(p: dict[str, Any]) -> str:
        name = p.get("display_name") or p.get("qq_nickname") or f"用户{p.get('user_id')}"
        tags = [x for x in [p.get("gender"), p.get("age"), p.get("occupation"), p.get("location")] if x]
        hob = p.get("hobbies") or []
        seg = "、".join(tags)
        if hob:
            seg += ("，爱好" + "/".join(hob[:3]))
        tail = f"；{p['summary']}" if p.get("summary") else ""
        return f"{name}（QQ{p.get('user_id')}）：{seg}{tail}"

    async def format_for_prompt(self, group_id: str, current_user_id: str, limit: int = 12) -> str:
        """生成注入 system prompt 的群友档案文本；当前说话人排最前并最详细。"""
        rows = await self.list_group(group_id, limit=limit + 5)
        if not rows:
            return ""
        cur, others = None, []
        for r in rows:
            if str(r.get("user_id")) == str(current_user_id):
                cur = r
            else:
                others.append(r)
        lines = ["## 群友档案（你认识的群友，请结合这些信息自然交流，称呼要对得上，但不要生硬念档案）"]
        if cur:
            lines.append("- 【正在和你说话的人】" + self._brief(cur))
            facts = cur.get("facts") or []
            if facts:
                lines.append("  关于TA你记得：" + "；".join(facts[:6]))
        for r in others[:max(0, limit - 1)]:
            lines.append("- " + self._brief(r))
        return "\n".join(lines)
