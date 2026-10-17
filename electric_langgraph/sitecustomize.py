"""Windows 下强制使用 SelectorEventLoop（sitecustomize 在解释器启动时自动加载）。

langgraph.checkpoint.postgres 基于 psycopg 3 的异步模式，不支持 Windows 默认的
ProactorEventLoop，会抛 ``InterfaceError``。需要在**事件循环创建之前**设置策略。

本项目入口都从项目根目录运行（`python -m uvicorn ...` 时 cwd 在 sys.path 上），
sitecustomize.py 位于根目录即可被 site 模块在启动早期自动导入。
"""
import sys

if sys.platform == "win32":
    try:
        import asyncio

        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass
