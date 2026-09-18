"""专门验证发帖引擎在处理中文及特殊字符时的编码安全性测试。"""

import asyncio
import pytest
import urllib.parse
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from tieba_mecha.core.batch_post import BatchPostManager, BatchPostTask
from tieba_mecha.core.obfuscator import Obfuscator
from tieba_mecha.core.post import add_thread

@pytest.mark.asyncio
async def test_batch_post_encoding_safety():
    """验证批量发帖引擎在处理中文贴吧名时是否执行了 URL 转义。"""
    mock_db = MagicMock()
    # 模拟环境：中文贴吧名
    target_fname = "游戏"
    quoted_fname = urllib.parse.quote(target_fname)
    
    # 模拟物料
    mock_material = MagicMock()
    mock_material.id = 1
    mock_material.title = "测试标题"
    mock_material.content = "测试内容"
    mock_db.get_materials_for_task = AsyncMock(return_value=[mock_material])
    mock_db.update_material_status = AsyncMock()
    mock_db.update_target_pool_status = AsyncMock()
    mock_db.update_material_ai = AsyncMock()
    mock_db.get_proxy = AsyncMock(return_value=None)
    mock_db.get_accounts = AsyncMock(return_value=[MagicMock(id=1)])
    mock_db.get_materials = AsyncMock(return_value=[mock_material])
    
    # 模拟账号凭证
    mock_db.get_account_credentials = AsyncMock(return_value=(1, "bduss", "stoken", None, "cuid", "ua"))
    
    # 模拟 Task
    task = BatchPostTask(id="test", fname=target_fname, accounts=[1], total=1)
    
    # 模拟 async_session 和 execute
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [1]
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_db.async_session = MagicMock(return_value=mock_session)
    
    manager = BatchPostManager(db=mock_db)
    
    # 模拟 Client
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get_self_info = AsyncMock()
    mock_client.get_forum = AsyncMock(return_value=MagicMock(fid=123))
    mock_client.account = MagicMock(tbs="test_tbs")

    # 模拟 httpx
    mock_response = MagicMock()
    mock_response.json = MagicMock(return_value={"err_code": 0, "data": {"tid": 12345}})
    
    # Mock
    mock_auth = MagicMock()
    mock_auth.status = "pro"
    mock_auth.check_local_status = AsyncMock()
    
    with patch("tieba_mecha.core.batch_post.get_auth_manager", return_value=mock_auth):
        with patch("tieba_mecha.core.batch_post.get_account_credentials", AsyncMock(return_value=(1, "bduss", "stoken", None, "cuid", "ua"))):
            with patch("tieba_mecha.core.batch_post.Obfuscator.from_db", AsyncMock(return_value=Obfuscator({}))):
                with patch("tieba_mecha.core.batch_post.create_client", AsyncMock(return_value=mock_client)):
                    with patch("httpx.AsyncClient") as MockClient:
                        mock_http = AsyncMock()
                        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
                        mock_http.__aexit__ = AsyncMock(return_value=None)
                        mock_http.get = AsyncMock(return_value=mock_response)
                        mock_http.post = AsyncMock(return_value=mock_response)
                        MockClient.return_value = mock_http

                        # 执行任务
                        results = []
                        async for res in manager.execute_task(task):
                            results.append(res)

                        print(f"Results: {results}")

                        # --- 断言验证 ---
            
            # 1. 验证 HTTP POST 已执行，并以原始字节表单 (content=) 传递
            assert mock_http.post.call_count > 0
            post_call_args = mock_http.post.call_args
            assert post_call_args is not None
            # 现行实现: 手动 urlencode 后以 content= 原始字节发送 (避免 httpx 对 <> 等字符过度百分号编码)
            assert "content" in post_call_args.kwargs
            body = post_call_args.kwargs["content"]
            assert isinstance(body, bytes)
            # UTF-8 百分号编码往返解析, 验证中文无乱码
            payload = urllib.parse.parse_qs(body.decode("utf-8"), encoding="utf-8")
            assert payload["kw"][0] == target_fname
            zero_width = ("​", "‌", "‍", "﻿")
            assert "".join(ch for ch in payload["title"][0] if ch not in zero_width) == "测试标题"
            normalized_content = "".join(ch for ch in payload["content"][0] if ch not in zero_width)
            assert "测试内容" in normalized_content

@pytest.mark.asyncio
async def test_manual_post_encoding_safety():
    """验证手动发帖模块 (post.py) 是否执行了 URL 转义。"""
    mock_db = MagicMock()
    target_fname = "影视大全"
    quoted_fname = urllib.parse.quote(target_fname)
    
    # 模拟账号凭证
    with patch("tieba_mecha.core.post.get_account_credentials", AsyncMock(return_value=(1, "bduss", "stoken", None, "cuid", "ua"))):
        with patch("tieba_mecha.core.obfuscator.Obfuscator.from_db", AsyncMock(return_value=Obfuscator({}))):
            # 模拟 Client
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get_self_info = AsyncMock()
            mock_client.get_forum = AsyncMock(return_value=MagicMock(fid=456))
            mock_client.account = MagicMock(tbs="manual_tbs")
            
            # 模拟 httpx
            mock_response = MagicMock()
            mock_response.json = MagicMock(return_value={"err_code": 0, "data": {"tid": 67890}})
            
            with patch("tieba_mecha.core.post.create_client", AsyncMock(return_value=mock_client)):
                with patch("httpx.AsyncClient") as MockClient:
                    mock_http = AsyncMock()
                    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
                    mock_http.__aexit__ = AsyncMock(return_value=None)
                    mock_http.get = AsyncMock(return_value=mock_response)
                    mock_http.post = AsyncMock(return_value=mock_response)
                    MockClient.return_value = mock_http

                    # 执行发帖
                    success, msg, tid = await add_thread(mock_db, target_fname, "手动标题", "手动内容")
                    
                    assert success is True
                    assert tid == 67890
                    
                    # 验证 Referer 转义
                    get_call = mock_http.get.call_args
                    assert f"kw={quoted_fname}" in get_call.args[0]
                    assert get_call.kwargs["headers"]["Referer"] == f"https://tieba.baidu.com/f?kw={quoted_fname}"
                    
                    # 验证 POST 字段内容 (现行实现: 手动 urlencode 后以 content= 原始字节发送)
                    post_call = mock_http.post.call_args
                    assert "content" in post_call.kwargs
                    body = post_call.kwargs["content"]
                    assert isinstance(body, bytes)
                    payload = urllib.parse.parse_qs(body.decode("utf-8"), encoding="utf-8")
                    assert payload["kw"][0] == target_fname
                    normalized_content = payload["content"][0].replace("\u200b", "").replace("\u200c", "").replace("\u200d", "").replace("\ufeff", "")
                    assert "手动内容" in normalized_content
    mock_db = MagicMock()
    target_fname = "影视大全"
    quoted_fname = urllib.parse.quote(target_fname)
    content = "段落一\r\n\r\n段落二\r段落三\n段落四"

    with patch("tieba_mecha.core.post.get_account_credentials", AsyncMock(return_value=(1, "bduss", "stoken", None, "cuid", "ua"))):
        with patch("tieba_mecha.core.obfuscator.Obfuscator.from_db", AsyncMock(return_value=Obfuscator({}))):
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get_self_info = AsyncMock()
            mock_client.get_forum = AsyncMock(return_value=MagicMock(fid=456))
            mock_client.account = MagicMock(tbs="manual_tbs")

            mock_response = MagicMock()
            mock_response.json = MagicMock(return_value={"err_code": 0, "data": {"tid": 67890}})

            with patch("tieba_mecha.core.post.create_client", AsyncMock(return_value=mock_client)):
                with patch("httpx.AsyncClient") as MockClient:
                    mock_http = AsyncMock()
                    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
                    mock_http.__aexit__ = AsyncMock(return_value=None)
                    mock_http.get = AsyncMock(return_value=mock_response)
                    mock_http.post = AsyncMock(return_value=mock_response)
                    MockClient.return_value = mock_http

                    success, msg, tid = await add_thread(mock_db, target_fname, "手动标题", content)

                    assert success is True
                    assert tid == 67890

                    post_call = mock_http.post.call_args
                    # 现行实现: 手动 urlencode 后以 content= 原始字节发送
                    assert "content" in post_call.kwargs
                    body = post_call.kwargs["content"]
                    assert isinstance(body, bytes)
                    payload = urllib.parse.parse_qs(body.decode("utf-8"), encoding="utf-8")
                    content_value = payload["content"][0]
                    # 换行规范化为 LF 后原样发送（贴吧 Web 表单 API 保留 LF 分段），
                    # 不得残留 \r，也不得转换为 [br]（会被服务端丢弃导致连成一行）
                    assert "\r" not in content_value
                    assert "[br]" not in content_value
                    zero_width = ("​", "‌", "‍", "﻿")
                    cleaned = "".join(ch for ch in content_value if ch not in zero_width)
                    # 原文 4 处换行 (\r\n\r\n / \r / \n) 均应保留为 LF
                    assert cleaned.count("\n") == 4
