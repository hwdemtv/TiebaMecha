#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对已入库的 pending 物料原地 GLM 改写正文（2026-09-22 网关证书修复后补改写用）

用法（服务器，/home/hw/TiebaMecha 下）:
    .venv/bin/python /tmp/rewrite_pending_materials.py

口径：只采纳改写正文，标题保持《片名》（年份）原样（用户确认 2026-09-22）。
成功：content=改写稿、original_content=种子、ai_status='rewritten'；
失败：保留原状，累计计数；首条失败即中止（系统性问题保护）。
"""
import asyncio
import os
import sys
from pathlib import Path

BASE = Path("/home/hw/TiebaMecha")
sys.path.insert(0, str(BASE / "src"))

for _line in (BASE / ".env").read_text(encoding="utf-8").splitlines():
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())

from sqlalchemy import select, and_  # noqa: E402

from tieba_mecha.core.ai_optimizer import AIOptimizer  # noqa: E402
from tieba_mecha.db.crud import Database  # noqa: E402
from tieba_mecha.db.models import MaterialPool  # noqa: E402


async def main():
    db = Database(str(BASE / "data" / "tieba_mecha.db"))
    await db.init_db()

    async with db.async_session() as session:
        rows = list((await session.execute(select(MaterialPool).where(and_(
            MaterialPool.status == "pending",
            MaterialPool.ai_status != "rewritten",
        )))).scalars().all())
    print(f"待改写 {len(rows)} 条", flush=True)
    if not rows:
        return

    ok_cnt = fail_cnt = 0
    async with AIOptimizer(db) as optimizer:
        for i, m in enumerate(rows, 1):
            try:
                res = await optimizer.optimize_post(m.title, m.content, persona="normal")
                reason = res[3] if res and len(res) > 3 else res
                if res and res[0]:
                    new_content = res[2]
                else:
                    print(f"[{i}] id={m.id} 改写失败: {reason}", flush=True)
                    if i == 1:
                        print("首条即失败，判定为系统性问题，中止（未改动任何数据）", flush=True)
                        return
                    fail_cnt += 1
                    continue
            except Exception as ex:
                print(f"[{i}] id={m.id} 改写异常: {type(ex).__name__}: {ex}", flush=True)
                if i == 1:
                    print("首条即异常，中止（未改动任何数据）", flush=True)
                    return
                fail_cnt += 1
                continue

            # 只采纳正文；标题保持《片名》（年份）原样
            async with db.async_session() as session:
                row = await session.get(MaterialPool, m.id)
                if row:
                    row.original_content = m.content
                    row.content = new_content
                    row.ai_status = "rewritten"
                    await session.commit()
            ok_cnt += 1
            if i % 20 == 0:
                print(f"进度 {i}/{len(rows)} | 成功 {ok_cnt} | 失败 {fail_cnt}", flush=True)
            await asyncio.sleep(0.3)

    print(f"完成: 成功 {ok_cnt} | 失败 {fail_cnt}（失败条保留种子，发帖时任务 use_ai 仍会改写）", flush=True)
    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
