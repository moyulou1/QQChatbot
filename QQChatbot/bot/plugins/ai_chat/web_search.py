"""
联网搜索模块
基于 Bing HTML 搜索（国内稳定，无需 API Key）
智能判断是否需要搜索，把结果注入 LLM 上下文
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Optional
from urllib.parse import quote_plus

import httpx
from nonebot import logger


# 需要联网搜索的关键词模式
SEARCH_KEYWORDS = [
    # 时间相关
    r"今天", r"昨天", r"明天", r"最近", r"最新", r"现在", r"当前",
    r"今年", r"去年", r"明年", r"这几天", r"近期", r"刚刚", r"刚才",
    # 新闻事件
    r"新闻", r"事件", r"发生了什么", r"怎么回事", r"什么情况",
    r"热搜", r"头条", r"报道", r"消息", r"爆料",
    # 价格行情
    r"价格", r"多少钱", r"行情", r"股价", r"股票", r"基金", r"汇率",
    r"比特币", r"加密货币", r"黄金", r"油价", r"房价",
    # 天气
    r"天气", r"气温", r"下雨", r"晴天", r"温度", r"天气预报",
    # 体育赛事
    r"比赛", r"比分", r"直播", r"世界杯", r"奥运会", r"NBA", r"欧冠",
    r"中超", r"英超", r"西甲", r"意甲", r"德甲",
    # 影视娱乐
    r"上映", r"播出", r"新剧", r"新电影", r"新番", r"综艺",
    r"演唱会", r"票房", r"收视率",
    # 科技产品
    r"发布", r"新品", r"新款", r"iPhone", r"华为", r"小米",
    r"芯片", r"显卡", r"CPU", r"GPU",
    # 政策法规
    r"政策", r"规定", r"法律", r"法规", r"新规", r"出台",
    r"国务院", r"央行", r"发改委",
    # 人物动态
    r"谁", r"是谁", r"个人资料", r"简介", r"生平", r"经历",
    # 其他
    r"怎么", r"如何", r"为什么", r"是什么", r"哪些", r"哪里",
]

# 不需要搜索的模式（本地知识足够）
NO_SEARCH_PATTERNS = [
    r"^你好", r"^在吗", r"^在不在", r"^有人吗", r"^嗨", r"^嗨",
    r"^谢谢", r"^感谢", r"^再见", r"^拜拜", r"^晚安", r"^早安",
    r"^我想听", r"^唱歌", r"^讲故事", r"^猜谜语", r"^玩游戏",
    r"^你叫什么", r"^你是谁", r"^你多大", r"^你喜欢",
    r"^我是", r"^我叫", r"^我今年",
]


def should_search(text: str) -> bool:
    """判断是否需要联网搜索"""
    if not text or len(text) < 2:
        return False

    # 检查不需要搜索的模式
    for pattern in NO_SEARCH_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return False

    # 检查需要搜索的关键词
    for pattern in SEARCH_KEYWORDS:
        if re.search(pattern, text, re.IGNORECASE):
            return True

    return False


class SearchResult:
    """搜索结果"""
    def __init__(self, title: str, url: str, snippet: str):
        self.title = title
        self.url = url
        self.snippet = snippet

    def __repr__(self):
        return f"SearchResult(title={self.title[:30]}..., url={self.url[:50]}...)"


class WebSearcher:
    """联网搜索引擎"""

    def __init__(self, timeout: int = 15, max_results: int = 5):
        self.timeout = timeout
        self.max_results = max_results
        self._client: Optional[httpx.AsyncClient] = None
        self._last_search_time = 0
        self._search_interval = 1.0  # 搜索间隔（秒），避免被封

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
                follow_redirects=True,
            )
        return self._client

    async def search_bing(self, query: str) -> list[SearchResult]:
        """用 Bing 搜索"""
        # 限速
        elapsed = time.time() - self._last_search_time
        if elapsed < self._search_interval:
            await asyncio.sleep(self._search_interval - elapsed)

        url = f"https://www.bing.com/search?q={quote_plus(query)}&setlang=zh-CN&cc=CN"

        try:
            client = await self._get_client()
            response = await client.get(url)
            response.raise_for_status()
            self._last_search_time = time.time()

            html = response.text
            results = self._parse_bing_results(html)
            logger.info(f"[WebSearch] Bing 搜索 '{query[:30]}...' 返回 {len(results)} 条结果")
            return results[:self.max_results]

        except Exception as e:
            logger.warning(f"[WebSearch] Bing 搜索失败: {e}")
            return []

    def _parse_bing_results(self, html: str) -> list[SearchResult]:
        """解析 Bing 搜索结果页面"""
        results = []

        # Bing 搜索结果在 <li class="b_algo"> 中
        # 标题: <h2><a href="...">标题</a></h2>
        # 摘要: <p>摘要</p>

        # 用正则提取所有 b_algo 块
        algo_blocks = re.findall(
            r'<li class="b_algo".*?>(.*?)</li>',
            html,
            re.DOTALL
        )

        for block in algo_blocks:
            try:
                # 提取标题和链接
                title_match = re.search(
                    r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>\s*</h2>',
                    block,
                    re.DOTALL
                )
                if not title_match:
                    continue

                url = title_match.group(1)
                title = re.sub(r'<[^>]+>', '', title_match.group(2)).strip()

                # 提取摘要
                snippet_match = re.search(
                    r'<p[^>]*>(.*?)</p>',
                    block,
                    re.DOTALL
                )
                snippet = ""
                if snippet_match:
                    snippet = re.sub(r'<[^>]+>', '', snippet_match.group(1)).strip()

                if title and url:
                    results.append(SearchResult(title=title, url=url, snippet=snippet))

            except Exception:
                continue

        return results

    async def search(self, query: str) -> list[SearchResult]:
        """搜索（优先 Bing，失败可扩展其他引擎）"""
        results = await self.search_bing(query)
        return results

    def format_results(self, results: list[SearchResult], query: str) -> str:
        """把搜索结果格式化成 LLM 可用的文本"""
        if not results:
            return f"（联网搜索未找到关于「{query}」的相关信息）"

        lines = [f"【联网搜索结果 - 关于「{query}】"]
        for i, r in enumerate(results, 1):
            lines.append(f"\n{i}. {r.title}")
            if r.snippet:
                lines.append(f"   {r.snippet}")
            lines.append(f"   来源: {r.url}")
        lines.append("\n【请基于以上搜索结果回答，注意信息的时效性和准确性】")
        return "\n".join(lines)

    async def search_and_format(self, query: str) -> str:
        """搜索并格式化结果"""
        results = await self.search(query)
        return self.format_results(results, query)

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()


# 全局单例
_searcher: Optional[WebSearcher] = None


def init_searcher(timeout: int = 15, max_results: int = 5) -> WebSearcher:
    global _searcher
    _searcher = WebSearcher(timeout=timeout, max_results=max_results)
    return _searcher


def get_searcher() -> Optional[WebSearcher]:
    return _searcher
