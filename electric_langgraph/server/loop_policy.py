"""自定义事件循环工厂：强制 SelectorEventLoop。

uvicorn 在 Windows 上默认用 ProactorEventLoop（为 subprocess 支持），而 psycopg 的
异步模式（PostgreSQL checkpointer）不支持 ProactorEventLoop，会抛 InterfaceError。
通过 `--loop server.loop_policy:selector_loop_factory` 覆盖为 SelectorEventLoop。

注意：设置事件循环策略（sitecustomize.py / config.py）对 uvicorn ≥0.36 无效——
uvicorn 会直接按 loop factory 构造事件循环，不走默认策略。
"""
import asyncio


def selector_loop_factory() -> asyncio.AbstractEventLoop:
    """返回 SelectorEventLoop 实例（Windows 下 psycopg 异步必需）。"""
    return asyncio.SelectorEventLoop()
