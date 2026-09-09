"""
会话级消息队列 + 全局并发限制

解决问题：
1. 多人对话时消息被吞（原来直接 return）
2. 私聊与群聊混合处理互相影响
3. 高并发时资源耗尽

设计：
- 每个会话（群/私聊）独立队列，消息不丢失
- 每个会话独立处理协程，同一会话内串行保证顺序
- 全局 Semaphore 限制同时处理的会话数
- 队列长度限制，超过时丢弃最旧消息
- 处理超时控制，防止单个消息阻塞
"""
import asyncio
import time
from typing import Callable, Awaitable, Any
from loguru import logger


class SessionMessageQueue:
    """会话级消息队列管理器"""

    def __init__(
        self,
        handler: Callable[[Any], Awaitable[None]],
        max_concurrent: int = 5,
        max_queue_size: int = 10,
        processing_timeout: float = 120.0,
    ):
        """
        Args:
            handler: 消息处理函数，接收一个参数（消息数据）
            max_concurrent: 全局最大并发处理会话数
            max_queue_size: 每个会话最大队列长度，超过丢弃最旧
            processing_timeout: 单条消息处理超时（秒）
        """
        self._handler = handler
        self._max_concurrent = max_concurrent
        self._max_queue_size = max_queue_size
        self._processing_timeout = processing_timeout

        # 会话ID -> (queue, worker_task)
        self._sessions: dict[str, tuple[asyncio.Queue, asyncio.Task | None]] = {}
        self._sessions_lock = asyncio.Lock()

        # 全局并发限制
        self._semaphore = asyncio.Semaphore(max_concurrent)

        # 统计
        self._stats = {
            "total_enqueued": 0,
            "total_processed": 0,
            "total_dropped": 0,
            "total_timeouts": 0,
            "total_errors": 0,
        }

    async def enqueue(self, session_id: str, message_data: Any) -> bool:
        """
        将消息加入会话队列。

        Args:
            session_id: 会话ID（群号或 private_用户ID）
            message_data: 消息数据，会传给 handler

        Returns:
            True: 成功入队
            False: 队列已满，消息被丢弃
        """
        async with self._sessions_lock:
            if session_id not in self._sessions:
                queue = asyncio.Queue(maxsize=self._max_queue_size)
                task = asyncio.create_task(self._worker(session_id, queue))
                self._sessions[session_id] = (queue, task)
                logger.debug(f"[Queue] 创建会话队列: {session_id}")

            queue, _ = self._sessions[session_id]

        # 入队（非阻塞，如果满了丢弃最旧的）
        if queue.full():
            try:
                queue.get_nowait()  # 丢弃最旧的
                queue.task_done()
                self._stats["total_dropped"] += 1
                logger.warning(f"[Queue] 会话 {session_id} 队列已满，丢弃最旧消息")
            except asyncio.QueueEmpty:
                pass

        try:
            queue.put_nowait(message_data)
            self._stats["total_enqueued"] += 1
            return True
        except asyncio.QueueFull:
            self._stats["total_dropped"] += 1
            logger.error(f"[Queue] 会话 {session_id} 入队失败（队列满）")
            return False

    async def _worker(self, session_id: str, queue: asyncio.Queue):
        """会话处理协程：从队列取消息，串行处理"""
        while True:
            try:
                message_data = await queue.get()
                start_time = time.time()

                # 全局并发限制
                async with self._semaphore:
                    try:
                        # 超时控制
                        await asyncio.wait_for(
                            self._handler(message_data),
                            timeout=self._processing_timeout,
                        )
                        self._stats["total_processed"] += 1
                        elapsed = time.time() - start_time
                        logger.debug(
                            f"[Queue] 会话 {session_id} 处理完成，耗时 {elapsed:.2f}s，"
                            f"队列剩余 {queue.qsize()}"
                        )
                    except asyncio.TimeoutError:
                        self._stats["total_timeouts"] += 1
                        logger.error(
                            f"[Queue] 会话 {session_id} 处理超时（>{self._processing_timeout}s）"
                        )
                    except Exception as e:
                        self._stats["total_errors"] += 1
                        logger.error(
                            f"[Queue] 会话 {session_id} 处理异常: {e}", exc_info=True
                        )
                    finally:
                        queue.task_done()

            except asyncio.CancelledError:
                logger.info(f"[Queue] 会话 {session_id} 处理协程被取消")
                break
            except Exception as e:
                logger.error(f"[Queue] 会话 {session_id} worker 异常: {e}", exc_info=True)
                await asyncio.sleep(1)  # 避免异常时忙等

    async def remove_session(self, session_id: str):
        """移除会话（取消协程，清空队列）"""
        async with self._sessions_lock:
            if session_id in self._sessions:
                _, task = self._sessions.pop(session_id)
                if task:
                    task.cancel()
                logger.debug(f"[Queue] 移除会话: {session_id}")

    def get_session_queue_size(self, session_id: str) -> int:
        """获取会话队列当前长度"""
        if session_id in self._sessions:
            return self._sessions[session_id][0].qsize()
        return 0

    def get_active_sessions(self) -> list[str]:
        """获取当前活跃会话列表"""
        return list(self._sessions.keys())

    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            **self._stats,
            "active_sessions": len(self._sessions),
            "max_concurrent": self._max_concurrent,
            "max_queue_size": self._max_queue_size,
            "semaphore_available": self._semaphore._value,
        }

    async def shutdown(self):
        """关闭所有会话队列"""
        async with self._sessions_lock:
            for session_id, (_, task) in list(self._sessions.items()):
                if task:
                    task.cancel()
            self._sessions.clear()
        logger.info("[Queue] 所有会话队列已关闭")


# 全局单例
_queue_manager: SessionMessageQueue | None = None


def init_queue_manager(
    handler: Callable[[Any], Awaitable[None]],
    max_concurrent: int = 5,
    max_queue_size: int = 10,
    processing_timeout: float = 120.0,
) -> SessionMessageQueue:
    """初始化全局队列管理器"""
    global _queue_manager
    if _queue_manager is None:
        _queue_manager = SessionMessageQueue(
            handler=handler,
            max_concurrent=max_concurrent,
            max_queue_size=max_queue_size,
            processing_timeout=processing_timeout,
        )
        logger.info(
            f"[Queue] 队列管理器已初始化：并发={max_concurrent}, "
            f"单队列长度={max_queue_size}, 超时={processing_timeout}s"
        )
    return _queue_manager


def get_queue_manager() -> SessionMessageQueue | None:
    """获取全局队列管理器"""
    return _queue_manager
