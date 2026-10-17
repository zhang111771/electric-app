"""FastAPI 服务：/chat 接口（SSE 流式）+ 对话页面 + 知识库管理 + 会话管理 + 电缆诊断 + 用户反馈。"""
import asyncio
import hashlib
import json
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from langchain_core.messages import AIMessageChunk, HumanMessage
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import BaseModel

import config
from graph.build_graph import get_graph
from graph.cable_tools import (
    fault_location_tdoa, fault_type_classify, get_yolo_module,
    line_defect_detect, prpd_defect_classify,
)
from graph.db import connect, ensure_pgvector
from graph.defect_db import ensure_defect_tables
from graph.defect_tools import (
    get_defect, query_defects, register_defect, update_defect_status,
)
from graph.dispatch_db import ensure_dispatch_tables
from graph.dispatch_tools import (
    analyze_fault_event, list_fault_events, update_fault_event,
)
from graph.flisr_db import ensure_flisr_tables
from graph.flisr_tools import (
    list_feeders, run_flisr,
)
from graph.line_loss_db import ensure_line_loss_tables
from graph.line_loss_tools import (
    analyze_area_loss, calc_line_loss, query_area_loss,
)
from graph.substation_tools import (
    ir_temperature_anomaly, oil_dga_three_ratio, sf6_gas_check,
)
from graph.ticket_tools import (
    DISCLAIMER as TICKET_DISCLAIMER, check_ticket_rules, sanitize_forbidden,
)
from graph.llm import get_llm
from server.report import build_detect_report
from graph.embeddings import embed_texts, vector_to_literal

app = FastAPI(title="电网智能体", version="0.1.0")
STATIC_DIR = Path(__file__).resolve().parent / "static"
UPLOAD_DIR = Path(__file__).resolve().parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
REPORTS_DIR = Path(__file__).resolve().parent / "reports"
REPORTS_DIR.mkdir(exist_ok=True)


class ChatBody(BaseModel):
    message: str
    session_id: str = "default"


class DeleteSessionBody(BaseModel):
    session_id: str


class TicketCheckBody(BaseModel):
    ticket_text: str
    ticket_type: str = "work"  # work 工作票 / operation 操作票


class DispatchJudgeBody(BaseModel):
    event_no: str


class DispatchStatusBody(BaseModel):
    action: str = "已处置"
    note: str = ""


class FlisrRunBody(BaseModel):
    feeder_id: str
    fault_section: str
    fault_type: str = "永久"


class LineLossAnalyzeBody(BaseModel):
    area_no: str
    days: int = 30


class ChunkAddBody(BaseModel):
    source: str
    section: str = ""
    text: str


class ChunkUpdateBody(BaseModel):
    source: str | None = None
    section: str | None = None
    text: str | None = None


class FeedbackBody(BaseModel):
    session_id: str
    answer: str
    rating: str  # "up" | "down"
    query: str = ""  # 触发该回答的用户问题（便于后续构造评测集）
    comment: str = ""


def _sse(event: str, **data) -> str:
    return f"data: {json.dumps({'type': event, **data}, ensure_ascii=False)}\n\n"


def _checkpoint_time(uid_str) -> str:
    """把 LangGraph 的 checkpoint_id（UUIDv6）解码为北京时间字符串。

    UUIDv6 前 48 位为 100ns 精度时间戳（自 1582-10-15 格里高利历纪元）：
    time_high(32bit) | time_mid(16bit) | version(4bit) + time_low_high(12bit)。
    """
    try:
        u = uuid.UUID(str(uid_str))
        b = u.bytes
        ts_100ns = (int.from_bytes(b[0:4], "big") << 28) | \
                   (int.from_bytes(b[4:6], "big") << 12) | \
                   (int.from_bytes(b[6:7], "big") & 0x0FFF)
        utc = datetime(1582, 10, 15, tzinfo=timezone.utc) + timedelta(seconds=ts_100ns / 1e7)
        return utc.astimezone(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
    except (ValueError, OSError):
        return ""


async def stream_chat(message: str, session_id: str):
    """把 LangGraph 的流式输出转成 SSE 事件：token / error / done。"""
    runtime_config = {"configurable": {"thread_id": session_id}}
    payload = {"messages": [HumanMessage(content=message)]}
    final_chart = None
    final_form = None
    try:
        graph = await get_graph()

        async def _process():
            nonlocal final_chart, final_form
            gen = graph.astream(payload, config=runtime_config,
                                stream_mode=["messages", "updates"])
            # 首 token 超时保护（120s）：捕获 graph.astream() 整体挂起
            first = await asyncio.wait_for(gen.__anext__(), timeout=120)
            mode, item = first
            if mode == "messages":
                chunk = item[0]
                if isinstance(chunk, AIMessageChunk) and chunk.content:
                    yield _sse("token", content=chunk.content)
            elif mode == "updates":
                for diff in item.values():
                    if isinstance(diff, dict):
                        if diff.get("chart"):
                            final_chart = diff["chart"]
                        if diff.get("form"):
                            final_form = diff["form"]
            # 首 token 已到，LLM 正常响应，后续迭代不再超时
            async for mode, item in gen:
                if mode == "messages":
                    chunk = item[0]
                    if isinstance(chunk, AIMessageChunk) and chunk.content:
                        yield _sse("token", content=chunk.content)
                elif mode == "updates":
                    for diff in item.values():
                        if isinstance(diff, dict):
                            if diff.get("chart"):
                                final_chart = diff["chart"]
                            if diff.get("form"):
                                final_form = diff["form"]

        try:
            async for sse_chunk in _process():
                yield sse_chunk
        except asyncio.TimeoutError:
            yield _sse("error", content="请求超时（120s），请检查 LLM API 连接或稍后重试。")
            return
    except Exception as e:
        yield _sse("error", content=f"出错了：{e}\n\n请检查 .env 中 LLM_API_KEY / POSTGRES_URI 配置是否正确。")
        return
    yield _sse("done", chart=final_chart, form=final_form)


@app.post("/chat")
async def chat(body: ChatBody):
    sid = body.session_id or "default"
    return StreamingResponse(stream_chat(body.message, sid),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/session")
async def new_session():
    """前端用于创建一个新会话 id。"""
    return {"session_id": uuid.uuid4().hex}


@app.get("/api/threads")
async def list_threads():
    """列出所有历史会话（对话记忆已持久化到 PostgreSQL）。"""
    serializer = JsonPlusSerializer()
    conn = await asyncpg.connect(config.POSTGRES_URI)
    try:
        rows = await conn.fetch("""
            SELECT thread_id, MAX(checkpoint_id) AS latest
            FROM checkpoints
            WHERE checkpoint_ns = ''
            GROUP BY thread_id
            ORDER BY latest DESC
        """)
        threads = []
        for row in rows:
            thread_id = row["thread_id"]
            last_message, updated_at = "", ""
            if row["latest"]:
                updated_at = _checkpoint_time(row["latest"])
            blob = await conn.fetchrow("""
                SELECT blob FROM checkpoint_blobs
                WHERE thread_id = $1 AND channel = 'messages'
                ORDER BY version DESC LIMIT 1
            """, thread_id)
            if blob and blob["blob"]:
                try:
                    messages = serializer.loads_typed(("msgpack", bytes(blob["blob"])))
                    if isinstance(messages, list):
                        for msg in messages:
                            if type(msg).__name__ in ("HumanMessage", "HumanMessageChunk") \
                                    and isinstance(msg.content, str) and msg.content:
                                last_message = msg.content[:50] + ("…" if len(msg.content) > 50 else "")
                                break
                except Exception:
                    pass
            threads.append({"session_id": thread_id, "last_message": last_message,
                            "updated_at": updated_at})
        return {"threads": threads}
    finally:
        await conn.close()


@app.get("/api/threads/{session_id}")
async def get_thread_detail(session_id: str):
    """获取指定会话的完整消息历史（用户/AI 消息）。"""
    serializer = JsonPlusSerializer()
    conn = await asyncpg.connect(config.POSTGRES_URI)
    try:
        blob = await conn.fetchrow("""
            SELECT blob FROM checkpoint_blobs
            WHERE thread_id = $1 AND channel = 'messages'
            ORDER BY version DESC LIMIT 1
        """, session_id)
        if not blob or not blob["blob"]:
            return {"session_id": session_id, "messages": []}
        messages = serializer.loads_typed(("msgpack", bytes(blob["blob"])))
        formatted = []
        if isinstance(messages, list):
            for msg in messages:
                content = getattr(msg, "content", "")
                if isinstance(content, str) and content:
                    cls = type(msg).__name__
                    if cls.startswith("HumanMessage"):
                        formatted.append({"role": "user", "content": content})
                    elif cls.startswith("AIMessage"):
                        entry: dict = {"role": "assistant", "content": content}
                        # 从 additional_kwargs 恢复 chart/form（持久化时存于此）
                        extra = getattr(msg, "additional_kwargs", None) or {}
                        if extra.get("chart"):
                            entry["chart"] = extra["chart"]
                        if extra.get("form"):
                            entry["form"] = extra["form"]
                        formatted.append(entry)
        return {"session_id": session_id, "messages": formatted}
    finally:
        await conn.close()


@app.post("/api/session/delete")
async def delete_session(body: DeleteSessionBody):
    """删除指定会话的对话历史（释放 PostgreSQL 存储空间）。"""
    graph = await get_graph()
    try:
        await graph.checkpointer.adelete_thread(body.session_id)
        return {"success": True, "message": f"已清除会话 {body.session_id}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除会话失败：{e}")


# ================= 电缆故障诊断 =================


async def _save_upload(file: UploadFile) -> Path:
    """保存上传文件到 uploads/ 目录，返回绝对路径。"""
    suffix = Path(file.filename or ".csv").suffix or ".csv"
    dest = UPLOAD_DIR / f"{uuid.uuid4().hex[:12]}{suffix}"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return dest


@app.post("/api/diagnose/upload")
async def diagnose_upload(files: list[UploadFile] = File(...)):
    """上传 CSV 文件用于诊断，返回文件路径列表（供后续诊断接口使用）。"""
    if not files:
        raise HTTPException(status_code=400, detail="未上传文件")
    paths = []
    for f in files:
        path = await _save_upload(f)
        paths.append(str(path))
    return {"files": paths}


@app.post("/api/diagnose/tdoa")
async def diagnose_tdoa(
    src_csv: UploadFile = File(...),
    ldr_csv: UploadFile = File(...),
    dt_us: float = 0.2,
):
    """TDOA 双端电流行波故障定位（直接调用，不走 LangGraph）。"""
    src_path = await _save_upload(src_csv)
    ldr_path = await _save_upload(ldr_csv)
    try:
        result = await fault_location_tdoa.ainvoke({
            "src_csv": str(src_path),
            "ldr_csv": str(ldr_path),
            "dt_us": dt_us,
        })
        return result
    finally:
        # 诊断完成后清理临时文件
        src_path.unlink(missing_ok=True)
        ldr_path.unlink(missing_ok=True)


@app.post("/api/diagnose/classify")
async def diagnose_classify(
    waveform_csv: UploadFile = File(None),
    rule_node: str = "LDR",
):
    """4 类短路故障类型识别（直接调用，不走 LangGraph）。"""
    if waveform_csv is None:
        raise HTTPException(status_code=400, detail="请上传 waveform_csv")
    path = await _save_upload(waveform_csv)
    try:
        result = await fault_type_classify.ainvoke({
            "waveform_csv": str(path),
            "rule_node": rule_node,
        })
        return result
    finally:
        path.unlink(missing_ok=True)


@app.post("/api/diagnose/prpd")
async def diagnose_prpd(
    prpd_csv: UploadFile = File(None),
    engine: str = "gbdt",
):
    """4 类局放缺陷类型识别（直接调用，不走 LangGraph）。"""
    if prpd_csv is None:
        raise HTTPException(status_code=400, detail="请上传 prpd_csv")
    path = await _save_upload(prpd_csv)
    try:
        result = await prpd_defect_classify.ainvoke({
            "prpd_csv": str(path),
            "engine": engine,
        })
        return result
    finally:
        path.unlink(missing_ok=True)


@app.post("/api/diagnose/detect")
async def diagnose_detect(
    image: UploadFile = File(None),
    conf: float = 0.25,
    iou: float = 0.45,
):
    """YOLO 线路缺陷检测（直接调用，不走 LangGraph）。

    返回检测结果 + 标注图 URL（标注图保存在 uploads/ 持久化，供前端展示）。
    """
    if image is None:
        raise HTTPException(status_code=400, detail="请上传 image")
    src_path = await _save_upload(image)
    try:
        result = await line_defect_detect.ainvoke({
            "image_path": str(src_path),
            "conf": conf,
            "iou": iou,
        })
        if "error" in result:
            return result
        # 生成标注图并保存（持久化），只删临时上传，保留标注图供前端引用
        yd = get_yolo_module()
        out = UPLOAD_DIR / f"{uuid.uuid4().hex[:12]}_annotated.jpg"
        viz = await asyncio.to_thread(
            lambda: yd.visualize(str(src_path), output_path=str(out),
                                 conf=conf, iou=iou))
        result["image_url"] = f"/api/diagnose/image/{Path(viz['output_path']).name}"
        return result
    finally:
        src_path.unlink(missing_ok=True)


@app.post("/api/diagnose/detect_batch")
async def diagnose_detect_batch(
    files: list[UploadFile] = File(...),
    conf: float = 0.25,
    iou: float = 0.45,
):
    """YOLO 线路缺陷批量检测（直接调用，不走 LangGraph）。

    一次上传多张图片，返回批次汇总 + 每张图检测结果（含标注图 URL）+
    自包含 HTML 检测报告 URL（可打印/另存 PDF）。单张失败不影响其余。
    """
    if not files:
        raise HTTPException(status_code=400, detail="未上传图片")
    # 保存所有上传文件（保留原始文件名用于报告/前端展示）
    saved: list[tuple[Path, str]] = []
    for f in files:
        path = await _save_upload(f)
        saved.append((path, Path(f.filename or "未命名").name))
    try:
        paths = [str(p) for p, _ in saved]
        yd = get_yolo_module()
        batch = await asyncio.to_thread(
            lambda: yd.detect_visualize_batch(paths, output_dir=UPLOAD_DIR,
                                              conf=conf, iou=iou))
        # 原名回填（含失败项）+ 成功项标注图 URL
        name_map = {str(p.resolve()): orig for p, orig in saved}
        for r in batch["results"]:
            r["filename"] = name_map.get(r["image_path"], Path(r["image_path"]).name)
            if "error" in r:
                continue
            r["image_url"] = f"/api/diagnose/image/{Path(r['output_path']).name}"
        # 生成自包含 HTML 报告（依赖 output_path 内嵌 base64 图）
        html_doc = await asyncio.to_thread(
            lambda: build_detect_report(batch, conf=conf, iou=iou))
        report_path = REPORTS_DIR / f"{uuid.uuid4().hex[:12]}.html"
        report_path.write_text(html_doc, encoding="utf-8")
        batch["report_url"] = f"/api/diagnose/report/{report_path.name}"
        batch["report_name"] = "输电线路缺陷检测报告.html"
        # 服务端 output_path 不外泄给前端
        for r in batch["results"]:
            r.pop("output_path", None)
        return batch
    finally:
        # 删除临时上传源图；标注图与报告持久化保留供前端引用
        for p, _ in saved:
            p.unlink(missing_ok=True)


@app.get("/api/diagnose/image/{name}")
async def diagnose_image(name: str):
    """返回诊断标注图（uploads/ 下），带路径穿越防护。"""
    path = UPLOAD_DIR / Path(name).name
    if path.resolve().parent != UPLOAD_DIR.resolve() or not path.is_file():
        raise HTTPException(status_code=404, detail="图片不存在")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/api/diagnose/report/{name}")
async def diagnose_report(name: str):
    """返回自包含 HTML 检测报告（reports/ 下），带路径穿越防护。"""
    path = REPORTS_DIR / Path(name).name
    if path.resolve().parent != REPORTS_DIR.resolve() or not path.is_file():
        raise HTTPException(status_code=404, detail="报告不存在")
    return FileResponse(path, media_type="text/html")


# ================= 变电设备诊断 =================


@app.post("/api/diagnose/substation/dga")
async def diagnose_substation_dga(
    h2: float = Form(...),
    ch4: float = Form(...),
    c2h6: float = Form(...),
    c2h4: float = Form(...),
    c2h2: float = Form(...),
    co: float = Form(0),
    co2: float = Form(0),
    voltage_level: int = Form(110),
):
    """变压器油色谱三比值法诊断（DL/T 722-2014，直接调用，不走 LangGraph）。"""
    return await oil_dga_three_ratio.ainvoke({
        "h2": h2, "ch4": ch4, "c2h6": c2h6, "c2h4": c2h4, "c2h2": c2h2,
        "co": co, "co2": co2, "voltage_level": voltage_level,
    })


@app.post("/api/diagnose/substation/sf6")
async def diagnose_substation_sf6(
    measured_pressure: float = Form(...),
    moisture_ppmv: float = Form(0),
    gas_chamber: str = Form("arc"),
    rated_pressure: float = Form(0.60),
    alarm_pressure: float = Form(0.55),
    lockout_pressure: float = Form(0.50),
):
    """SF6 气体压力与微水状态检查（DL/T 596，直接调用，不走 LangGraph）。"""
    return await sf6_gas_check.ainvoke({
        "measured_pressure": measured_pressure,
        "moisture_ppmv": moisture_ppmv,
        "gas_chamber": gas_chamber,
        "rated_pressure": rated_pressure,
        "alarm_pressure": alarm_pressure,
        "lockout_pressure": lockout_pressure,
    })


@app.post("/api/diagnose/substation/ir")
async def diagnose_substation_ir(
    hot_temp: float = Form(...),
    ref_temp: float = Form(...),
    ambient_temp: float = Form(...),
    device_category: str = Form("接头和线夹"),
):
    """红外测温相对温差法诊断（DL/T 664-2016，直接调用，不走 LangGraph）。"""
    return await ir_temperature_anomaly.ainvoke({
        "hot_temp": hot_temp,
        "ref_temp": ref_temp,
        "ambient_temp": ambient_temp,
        "device_category": device_category,
    })


# ================= 缺陷闭环管理 =================

@app.get("/api/defect/list")
async def defect_list(
    status: str = "", severity: str = "", substation: str = "",
    keyword: str = "", limit: int = 20,
):
    """按条件查询缺陷台账（首次调用惰性建表）。"""
    await ensure_defect_tables()
    return await query_defects.ainvoke({
        "status": status, "severity": severity, "substation": substation,
        "keyword": keyword, "limit": limit,
    })


@app.post("/api/defect/register")
async def defect_register(
    substation: str = Form(...),
    device: str = Form(...),
    defect_type: str = Form(...),
    description: str = Form(...),
    voltage_level: str = Form(""),
    severity: str = Form("一般"),
    source: str = Form("巡检"),
    discoverer: str = Form(""),
    plan_time: str = Form(""),
):
    """登记一条缺陷并进入闭环管理。"""
    await ensure_defect_tables()
    return await register_defect.ainvoke({
        "substation": substation, "device": device, "defect_type": defect_type,
        "description": description, "voltage_level": voltage_level,
        "severity": severity, "source": source, "discoverer": discoverer,
        "plan_time": plan_time,
    })


@app.post("/api/defect/status")
async def defect_status(
    defect_no: str = Form(...),
    action: str = Form(...),
    severity: str = Form(""),
    assignee: str = Form(""),
    result_note: str = Form(""),
    plan_time: str = Form(""),
):
    """推进缺陷状态（状态机校验，非法跳转返回 error）。"""
    await ensure_defect_tables()
    return await update_defect_status.ainvoke({
        "defect_no": defect_no, "action": action, "severity": severity,
        "assignee": assignee, "result_note": result_note, "plan_time": plan_time,
    })


@app.get("/api/defect/detail/{defect_no}")
async def defect_detail(defect_no: str):
    """查询单条缺陷详情及关联工单。"""
    await ensure_defect_tables()
    return await get_defect.ainvoke({"defect_no": defect_no})


# ================= 两票智能审核 =================

_TICKET_NARRATIVE_SYSTEM = (
    "你是电网两票（工作票/操作票）智能审核助手。下面是规则引擎对票面的确定性差异清单"
    "（items：missing=疑似缺项，warning=顺序存疑，question=需人工确认）与票面原文。\n\n"
    "【铁律】\n"
    "1. 只输出三类内容：差异项 + 建议 + 疑问。\n"
    "2. 绝对禁止使用『合格/不合格/通过/不通过/符合要求』，一律用"
    "『疑似缺项/顺序存疑/请人工确认/请人工核对票面』。\n"
    "3. 你不作合格性结论，不代替任何人签字。\n"
    "4. 用简洁的中文分条转述差异与建议，最后附免责声明："
    "『以上仅为辅助检查，票面是否有效请以值班调度/工作负责人人工复核签字为准。』\n\n"
    "【票面原文】\n{ticket_text}\n\n"
    "【规则引擎差异清单】\n{items_json}"
)


async def _ticket_narrative(ticket_text: str, ticket_type: str, result: dict) -> str:
    """LLM 生成审核叙述（best-effort，禁词兜底；失败返回空串走默认文案）。"""
    llm = get_llm(temperature=0.2)
    items_json = json.dumps(result.get("items", []), ensure_ascii=False, indent=1)
    sys_msg = _TICKET_NARRATIVE_SYSTEM.format(
        ticket_text=ticket_text[:3000], items_json=items_json[:4000])
    parts = []
    async for chunk in llm.astream([sys_msg, HumanMessage(content="请给出审核要点。")]):
        if chunk.content:
            parts.append(chunk.content)
    return sanitize_forbidden("".join(parts))


@app.post("/api/ticket/check")
async def ticket_check(body: TicketCheckBody):
    """两票智能审核：确定性规则逐项核对 + LLM 建议叙述。

    返回结构不设 verdict 字段（物理上无合格/不合格结论），叙述做禁词兜底过滤，
    固定带免责声明。
    """
    if not body.ticket_text or not body.ticket_text.strip():
        return {
            "mode": "ticket", "ticket_type": body.ticket_type,
            "summary": "未收到票面内容，无法进行辅助检查。",
            "items": [], "questions": [],
            "disclaimer": TICKET_DISCLAIMER,
        }
    result = await check_ticket_rules.ainvoke({
        "ticket_text": body.ticket_text,
        "ticket_type": body.ticket_type,
    })
    result["disclaimer"] = TICKET_DISCLAIMER
    try:
        narrative = await _ticket_narrative(body.ticket_text, body.ticket_type, result)
        if narrative and narrative.strip():
            result["narrative"] = narrative
    except Exception:
        pass  # 叙述失败不影响确定性差异结果
    return result


# ================= 调度事故研判 =================


@app.post("/api/dispatch/judge")
async def dispatch_judge(body: DispatchJudgeBody):
    """故障事件自动研判：保护动作 → 故障类型/区段 → 处理建议。

    规则引擎确定性输出，只出推理链 + 建议 + 疑问（辅助不拍板），
    最终事故定性以人工复核为准。
    """
    await ensure_dispatch_tables()
    return await analyze_fault_event.ainvoke({"event_no": body.event_no})


@app.get("/api/dispatch/events")
async def dispatch_events(
    status: str = "", substation: str = "", keyword: str = "", limit: int = 20,
):
    """按条件查询故障事件台账（首次调用惰性建表+播种）。"""
    await ensure_dispatch_tables()
    return await list_fault_events.ainvoke({
        "status": status, "substation": substation, "keyword": keyword, "limit": limit,
    })


@app.post("/api/dispatch/event/{event_no}/status")
async def dispatch_status(event_no: str, body: DispatchStatusBody):
    """推进故障事件状态（已处置）；待研判事件必须先研判，非法跳转返回 error。"""
    await ensure_dispatch_tables()
    return await update_fault_event.ainvoke({
        "event_no": event_no, "action": body.action, "note": body.note,
    })


# ================= 配网 FLISR =================


@app.get("/api/flisr/feeders")
async def flisr_feeders():
    """查询配网馈线拓扑清单（首次调用惰性建表+播种）。"""
    await ensure_flisr_tables()
    return await list_feeders.ainvoke({})


@app.post("/api/flisr/run")
async def flisr_run(body: FlisrRunBody):
    """馈线故障定位→隔离→恢复（FLISR）辅助决策。

    规则算法确定性输出建议操作步骤（定位/隔离/恢复）+ 转供容量校验，
    所有开关操作仅为辅助建议，需值班调度确认后执行。
    """
    await ensure_flisr_tables()
    return await run_flisr.ainvoke({
        "feeder_id": body.feeder_id,
        "fault_section": body.fault_section,
        "fault_type": body.fault_type,
    })


# ================= 台区线损 =================


@app.get("/api/line-loss/areas")
async def line_loss_areas():
    """查询台区清单（首次调用惰性建表+播种）。"""
    await ensure_line_loss_tables()
    return await query_area_loss.ainvoke({"area_no": ""})


@app.get("/api/line-loss/readings")
async def line_loss_readings(area_no: str = "", date_from: str = "", date_to: str = ""):
    """查询台区清单（不带 area_no）或某台区日电量明细（带 area_no）。"""
    await ensure_line_loss_tables()
    return await query_area_loss.ainvoke({
        "area_no": area_no, "date_from": date_from, "date_to": date_to,
    })


@app.post("/api/line-loss/calc")
async def line_loss_calc(
    supply_kwh: float = Form(...),
    sales_kwh: float = Form(...),
    capacity_kva: float = Form(315),
):
    """计算线损率并判定（客观计算可明确判级；窃电/漏电用疑似措辞）。"""
    await ensure_line_loss_tables()
    return await calc_line_loss.ainvoke({
        "supply_kwh": supply_kwh, "sales_kwh": sales_kwh, "capacity_kva": capacity_kva,
    })


@app.post("/api/line-loss/analyze")
async def line_loss_analyze(body: LineLossAnalyzeBody):
    """分析台区近 N 天线损：均值/趋势/突增/持续超标/负线损 + 疑点（辅助不拍板）。"""
    await ensure_line_loss_tables()
    return await analyze_area_loss.ainvoke({"area_no": body.area_no, "days": body.days})


# ================= 知识库管理 =================

async def _embed_off(texts: list[str]) -> list[list[float]]:
    """在独立线程里跑同步 embed（openai 客户端阻塞调用，避免卡事件循环）。"""
    return await asyncio.to_thread(embed_texts, texts)


@app.get("/api/kb/stats")
async def kb_stats():
    """知识库统计：总块数 + 各来源数量。"""
    await ensure_pgvector()
    conn = await connect()
    try:
        total = await conn.fetchval(
            f"SELECT count(*) FROM {config.KB_VECTOR_TABLE}")
        rows = await conn.fetch(
            f"SELECT source, count(*) AS c FROM {config.KB_VECTOR_TABLE} "
            "GROUP BY source ORDER BY c DESC")
        return {"total": total, "by_source": [{"source": r["source"], "count": r["c"]} for r in rows]}
    finally:
        await conn.close()


@app.get("/api/kb/sources")
async def kb_sources():
    """所有已入库的来源名（下拉选择用）。"""
    await ensure_pgvector()
    conn = await connect()
    try:
        rows = await conn.fetch(
            f"SELECT DISTINCT source FROM {config.KB_VECTOR_TABLE} ORDER BY source")
        return {"sources": [r["source"] for r in rows]}
    finally:
        await conn.close()


@app.get("/api/kb/chunks")
async def kb_chunks(q: str = "", source: str = "", limit: int = 200):
    """列出知识块，支持按内容搜索 / 按来源筛选（倒序，最新在前）。"""
    limit = min(max(limit, 1), 500)
    await ensure_pgvector()
    conn = await connect()
    try:
        like = f"%{q.strip()}%"
        if q.strip():
            rows = await conn.fetch(
                f"SELECT id, source, section, text, length(text) AS len "
                f"FROM {config.KB_VECTOR_TABLE} "
                "WHERE (text ILIKE $1 OR section ILIKE $1) AND ($2 = '' OR source = $2) "
                "ORDER BY id DESC LIMIT $3", like, source, limit)
        else:
            rows = await conn.fetch(
                f"SELECT id, source, section, text, length(text) AS len "
                f"FROM {config.KB_VECTOR_TABLE} "
                "WHERE ($1 = '' OR source = $1) ORDER BY id DESC LIMIT $2",
                source, limit)
        return {"chunks": [dict(r) for r in rows]}
    finally:
        await conn.close()


@app.post("/api/kb/chunks")
async def kb_add_chunk(body: ChunkAddBody):
    """新增单个知识块：编码向量 → 增量写入（不动已有数据）。"""
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="正文不能为空")
    if not body.source.strip():
        raise HTTPException(status_code=400, detail="来源不能为空")
    await ensure_pgvector()
    vec = (await _embed_off([text]))[0]
    conn = await connect()
    try:
        row = await conn.fetchrow(
            f"INSERT INTO {config.KB_VECTOR_TABLE} (source, section, text, embedding) "
            "VALUES ($1, $2, $3, $4::vector) RETURNING id",
            body.source.strip(), body.section.strip(), text, vector_to_literal(vec))
        return {"id": row["id"]}
    finally:
        await conn.close()


@app.put("/api/kb/chunks/{chunk_id}")
async def kb_update_chunk(chunk_id: int, body: ChunkUpdateBody):
    """编辑知识块：改了正文就重新编码向量。"""
    await ensure_pgvector()
    conn = await connect()
    try:
        cur = await conn.fetchrow(
            f"SELECT source, section, text FROM {config.KB_VECTOR_TABLE} WHERE id = $1", chunk_id)
        if not cur:
            raise HTTPException(status_code=404, detail="知识块不存在")
        source = body.source.strip() if body.source is not None else cur["source"]
        section = body.section.strip() if body.section is not None else cur["section"]
        text = body.text.strip() if body.text is not None else cur["text"]
        if not text:
            raise HTTPException(status_code=400, detail="正文不能为空")
        if not source:
            raise HTTPException(status_code=400, detail="来源不能为空")
        vec = (await _embed_off([text]))[0]
        await conn.execute(
            f"UPDATE {config.KB_VECTOR_TABLE} SET source = $1, section = $2, "
            "text = $3, embedding = $4::vector WHERE id = $5",
            source, section, text, vector_to_literal(vec), chunk_id)
        return {"id": chunk_id}
    finally:
        await conn.close()


@app.delete("/api/kb/chunks/{chunk_id}")
async def kb_delete_chunk(chunk_id: int):
    """删除单个知识块。"""
    await ensure_pgvector()
    conn = await connect()
    try:
        await conn.execute(
            f"DELETE FROM {config.KB_VECTOR_TABLE} WHERE id = $1", chunk_id)
        return {"ok": True}
    finally:
        await conn.close()


@app.post("/api/kb/rebuild")
async def kb_rebuild():
    """从 data/assets/ 的 markdown 全量重建知识库（先清空再入库，幂等）。"""
    def _rebuild() -> int:
        from data.seed_knowledge import build_chunks, save_embeddings_pgvector
        chunks = build_chunks()
        config.KB_CHUNKS_PATH.write_text(
            json.dumps(chunks, ensure_ascii=False), encoding="utf-8")
        save_embeddings_pgvector(chunks)
        return len(chunks)
    try:
        n = await asyncio.to_thread(_rebuild)
        return {"count": n, "message": f"重建完成，共 {n} 块"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"重建失败：{e}")


# ================= 用户反馈（评测数据闭环） =================

async def ensure_feedback_table() -> None:
    """feedback 表幂等创建：同会话同回答只保留最新一条评分（answer_hash 唯一）。"""
    conn = await connect()
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id SERIAL PRIMARY KEY,
                session_id TEXT NOT NULL,
                answer_hash TEXT NOT NULL,
                query TEXT NOT NULL DEFAULT '',
                answer TEXT NOT NULL,
                rating TEXT NOT NULL CHECK (rating IN ('up', 'down')),
                comment TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (session_id, answer_hash)
            )""")
    finally:
        await conn.close()


@app.post("/api/feedback")
async def add_feedback(body: FeedbackBody):
    """记录用户对 AI 回答的评分；重复提交同一条回答会覆盖为最新评分。"""
    if body.rating not in ("up", "down"):
        raise HTTPException(status_code=400, detail="rating 必须是 up 或 down")
    if not body.answer.strip():
        raise HTTPException(status_code=400, detail="answer 不能为空")
    await ensure_feedback_table()
    answer_hash = hashlib.md5(body.answer.encode("utf-8")).hexdigest()
    conn = await connect()
    try:
        await conn.execute("""
            INSERT INTO feedback (session_id, answer_hash, query, answer, rating, comment)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (session_id, answer_hash)
            DO UPDATE SET rating = EXCLUDED.rating,
                          comment = EXCLUDED.comment,
                          created_at = now()
        """, body.session_id, answer_hash, body.query, body.answer,
            body.rating, body.comment)
        return {"ok": True}
    finally:
        await conn.close()


@app.get("/api/feedback")
async def list_feedback(limit: int = 200):
    """查看已收集的反馈（最新在前）+ up/down 统计。"""
    limit = min(max(limit, 1), 1000)
    await ensure_feedback_table()
    conn = await connect()
    try:
        rows = await conn.fetch(
            "SELECT session_id, query, answer, rating, comment, created_at "
            "FROM feedback ORDER BY created_at DESC LIMIT $1", limit)
        up = await conn.fetchval("SELECT count(*) FROM feedback WHERE rating = 'up'")
        down = await conn.fetchval("SELECT count(*) FROM feedback WHERE rating = 'down'")
        return {"stats": {"up": up, "down": down},
                "items": [dict(r) for r in rows]}
    finally:
        await conn.close()


# ================= 诊断工具客观评测 =================

_EVAL_REPORT_DIR = Path(__file__).resolve().parents[1] / "data" / "eval" / "reports"


def _load_latest_eval() -> dict | None:
    """读取最新一份评测报告（latest.json），不存在返回 None。"""
    latest = _EVAL_REPORT_DIR / "latest.json"
    if not latest.exists():
        return None
    try:
        return json.loads(latest.read_text(encoding="utf-8"))
    except Exception:
        return None


def _list_eval_history(limit: int = 20) -> list[dict]:
    """列出历史评测报告（只返回元信息，不加载完整内容，最新在前）。"""
    if not _EVAL_REPORT_DIR.exists():
        return []
    files = sorted(_EVAL_REPORT_DIR.glob("eval_*.json"), reverse=True)
    out = []
    for f in files[:limit]:
        if f.name == "latest.json":
            continue
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
            out.append({
                "name": f.stem,
                "timestamp": raw.get("timestamp"),
                "version": raw.get("version"),
                "tools_tested": raw.get("tools_tested", []),
            })
        except Exception:
            continue
    return out


@app.get("/api/eval")
async def get_eval(history: int = 10):
    """获取最新评测报告 + 历史记录元信息。"""
    latest = _load_latest_eval()
    history = min(max(history, 0), 100)
    return {
        "latest": latest,
        "history": _list_eval_history(history) if history else [],
    }


@app.post("/api/eval/run")
async def run_eval_endpoint(tools: str = ""):
    """触发一次全量或指定工具的评测，返回最新报告。

    tools 可选，逗号分隔：tdoa,classify,prpd,detect；留空则跑全部。
    注意：detect 依赖 ultralytics + TensorRT，环境不支持时会报错。
    """
    import importlib
    import sys
    from pathlib import Path as _Path

    # 动态导入评测模块（避免启动时引入 torch 等重依赖）
    scripts_dir = str(Path(__file__).resolve().parents[1] / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    run_eval_mod = importlib.import_module("run_eval")

    tool_list = [t.strip() for t in tools.split(",") if t.strip()] if tools else None
    report = await run_eval_mod.run_eval(tool_list)
    run_eval_mod.save_report(report)
    return report
