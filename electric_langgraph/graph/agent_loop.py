"""ReAct 风格的工具调用循环（供专职智能体复用）。

流程：模型 bind_tools → 若返回工具调用则执行工具 → 把工具结果回填 → 再次调用模型，
直到模型不再请求工具（最多 max_iters 轮）。
返回以“没有工具调用”的 AIMessage 结尾的消息列表。
"""
import json
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.runnables import RunnableConfig


async def run_tool_loop(llm, messages: list[BaseMessage], tools: list[Any],
                        config: RunnableConfig | None = None, max_iters: int = 6) -> list[BaseMessage]:
    llm_with_tools = llm.bind_tools(tools)
    msgs = list(messages)
    for _ in range(max_iters):
        ai = await llm_with_tools.ainvoke(msgs, config=config)
        msgs.append(ai)
        if not ai.tool_calls:
            break
        for tc in ai.tool_calls:
            tool_name = tc.get("name", "")
            args = tc.get("args") or {}
            tool = next((t for t in tools if t.name == tool_name), None)
            if tool is None:
                result = {"error": f"未找到工具 {tool_name}"}
            else:
                try:
                    result = await tool.ainvoke(args)
                except Exception as e:
                    result = {"error": f"工具调用出错: {e}"}
            if not isinstance(result, str):
                result = json.dumps(result, ensure_ascii=False, default=str)
            msgs.append(ToolMessage(content=result, tool_call_id=tc["id"], name=tool_name))
    return msgs


async def stream_tool_loop(llm, messages: list[BaseMessage], tools: list[Any],
                           config: RunnableConfig | None = None, max_iters: int = 4,
                           meta: dict | None = None):
    """流式工具循环：模型 astream 逐 token 输出，同时累积 tool_calls。

    用法：`async for token in stream_tool_loop(...)`，token 是文本片段，调用方可
    透传给前端 SSE。有工具调用时执行并回填 ToolMessage 后继续下一轮，直到模型
    不再请求工具（最多 max_iters 轮），此时流出的即最终回答。

    与 run_tool_loop 的区别：本函数整条链路只走一次 astream（不额外 ainvoke），
    输出即前端收到的全部内容——不会像 ainvoke + 二次 astream 那样把同一段话
    重复推给前端。注意：调用方提示词应要求模型"不要输出思考过程"，避免工具
    调用前的文字混入最终回答。

    meta：可选 dict，循环结束时写入 `meta["run_id"]` = 最后一个含文本 chunk 的 id。
    LangGraph 在 stream_mode="messages" 下会把节点返回的 AIMessage 整体再重发一遍，
    调用方用这个 run_id 给最终 AIMessage 打上相同 id，重发即被消息流去重（dedupe）。
    """
    llm_with_tools = llm.bind_tools(tools)
    msgs = list(messages)
    last_id = None
    for _ in range(max_iters):
        acc = None
        async for chunk in llm_with_tools.astream(msgs, config=config):
            acc = chunk if acc is None else acc + chunk
            if chunk.content:
                last_id = chunk.id or last_id
                yield chunk.content
        if acc is None or not acc.tool_calls:
            if meta is not None:
                meta["run_id"] = last_id
            return
        msgs.append(acc)
        for tc in acc.tool_calls:
            tool_name = tc.get("name", "")
            args = tc.get("args") or {}
            tool = next((t for t in tools if t.name == tool_name), None)
            if tool is None:
                result = {"error": f"未找到工具 {tool_name}"}
            else:
                try:
                    result = await tool.ainvoke(args)
                except Exception as e:
                    result = {"error": f"工具调用出错: {e}"}
            if not isinstance(result, str):
                result = json.dumps(result, ensure_ascii=False, default=str)
            msgs.append(ToolMessage(content=result, tool_call_id=tc["id"], name=tool_name))
    # max_iters 轮都用完了仍有工具调用：再流式生成一次收尾，避免回答为空
    async for chunk in llm.astream(msgs, config=config):
        if chunk.content:
            last_id = chunk.id or last_id
            yield chunk.content
    if meta is not None:
        meta["run_id"] = last_id
