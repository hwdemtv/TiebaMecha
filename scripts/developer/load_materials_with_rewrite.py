#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""装填物料 JSON + GLM 逐条改写（服务器端运行，配合 gen_paste_materials.py）

用法（服务器，需在 /home/hw/TiebaMecha 下）:
    .venv/bin/python scripts/developer/load_materials_with_rewrite.py /tmp/materials_batch.json

流程：每条先经 AIOptimizer.optimize_post(normal 人格) 改写标题与正文，
成功则以 ai_status='rewritten' 落库（保留 original_* 备份）；
失败保留种子原文 ai_status='none'（发帖时任务侧 use_ai=1 仍会兜底改写）。
"""
import asyncio
import json
import os
import sys
from pathlib import Path

BASE = Path("/home/hw/TiebaMecha")
sys.path.insert(0, str(BASE / "src"))

# 载入 .env（加密密钥），与 start_web.py 同源
for _line in (BASE / ".env").read_text(encoding="utf-8").splitlines():
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())

from tieba_mecha.core.ai_optimizer import AIOptimizer  # noqa: E402
from tieba_mecha.db.crud import Database  # noqa: E402
from tieba_mecha.db.models import MaterialPool  # noqa: E402


async def main(json_path: str):
    entries = json.loads(Path(json_path).read_text(encoding="utf-8"))
    print(f"待装填 {len(entries)} 条，开始逐条改写…", flush=True)

    db = Database(str(BASE / "data" / "tieba_mecha.db"))
    await db.init_db()

    ok_cnt = fail_cnt = 0
    skip_rewrite = os.getenv("SKIP_REWRITE", "") == "1"
    if skip_rewrite:
        print("SKIP_REWRITE=1：跳过 GLM 改写，直接装填种子（发帖时任务侧 use_ai 会再改写）", flush=True)
    async with AIOptimizer(db) as optimizer:
        for i, e in enumerate(entries, 1):
            title, content, link = e["title"], e["content"], e["link_url"]
            ai_status = "none"
            orig_t = orig_c = None
            if not skip_rewrite:
                try:
                    res = await optimizer.optimize_post(title, content, persona="normal")
                    reason = res[3] if res and len(res) > 3 else res
                    if res and res[0]:
                        orig_t, orig_c = title, content
                        title, content = res[1], res[2]
                        ai_status = "rewritten"
                        ok_cnt += 1
                    else:
                        fail_cnt += 1
                        print(f"[{i}] 改写失败: {reason}", flush=True)
                        if i == 1:
                            print("首条即失败，判定为系统性问题，中止装填（未写入任何数据）", flush=True)
                            return
                except Exception as ex:
                    fail_cnt += 1
                    print(f"[{i}] 改写异常保留种子: {type(ex).__name__}: {ex}", flush=True)
                    if i == 1:
                        print("首条即异常，中止装填（未写入任何数据）", flush=True)
                        return

            async with db.async_session() as session:
                session.add(MaterialPool(
                    title=title, content=content, link_url=link,
                    status="pending", ai_status=ai_status,
                    original_title=orig_t, original_content=orig_c,
                ))
                await session.commit()

            if i % 20 == 0:
                print(f"进度 {i}/{len(entries)} | 改写成功 {ok_cnt} | 失败 {fail_cnt}", flush=True)
            await asyncio.sleep(0.3)

    print(f"完成: 装填 {len(entries)} | GLM改写成功 {ok_cnt} | 失败 {fail_cnt}"
          f"（失败条保留种子，发帖时任务 use_ai=1 仍会改写）", flush=True)
    await db.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
