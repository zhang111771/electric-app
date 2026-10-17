"""导出用户反馈为评测集 JSONL（L0 数据闭环第一步）。

把 PostgreSQL feedback 表导出为 JSONL，作为后续 router 分类器训练、
回归评测集、DPO 偏好对构造的原始数据。

用法：
    python scripts/export_eval_set.py                     # → data/eval/feedback.jsonl
    python scripts/export_eval_set.py --out my.jsonl      # 指定输出路径
    python scripts/export_eval_set.py --rating down       # 只导出点踩数据

输出格式（每行一条）：
    {"session_id", "query", "answer", "rating", "comment", "created_at"}
"""
import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 保证可从仓库根目录直接运行
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg

import config

OUT_DEFAULT = Path(__file__).resolve().parents[1] / "data" / "eval" / "feedback.jsonl"


def _fmt_time(t: datetime | None) -> str:
    """时间戳转北京时间字符串（服务器可能返回 naive UTC）。"""
    if t is None:
        return ""
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


async def export(conn: asyncpg.Connection, out_path: Path, rating: str | None) -> int:
    """把 feedback 表（可按评分过滤）逐行写入 JSONL，返回导出条数。"""
    rows = await conn.fetch(
        "SELECT session_id, query, answer, rating, comment, created_at "
        "FROM feedback ORDER BY created_at")
    if rating:
        rows = [r for r in rows if r["rating"] == rating]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({
                "session_id": r["session_id"],
                "query": r["query"],
                "answer": r["answer"],
                "rating": r["rating"],
                "comment": r["comment"],
                "created_at": _fmt_time(r["created_at"]),
            }, ensure_ascii=False) + "\n")
    return len(rows)


async def main_async(out_path: Path, rating: str | None) -> int:
    conn = await asyncpg.connect(config.POSTGRES_URI)
    try:
        return await export(conn, out_path, rating)
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="导出用户反馈为评测集 JSONL")
    parser.add_argument("--out", default=str(OUT_DEFAULT), help="输出 JSONL 路径")
    parser.add_argument("--rating", choices=["up", "down"], default=None,
                        help="只导出指定评分的数据")
    args = parser.parse_args()

    n = asyncio.run(main_async(Path(args.out), args.rating))
    print(f"已导出 {n} 条反馈 → {args.out}")


if __name__ == "__main__":
    main()
