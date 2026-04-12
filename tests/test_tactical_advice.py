import pytest
from tieba_mecha.core.batch_post import BatchPostManager

def test_get_tactical_advice_known_errors():
    """测试已知报错的情报转换逻辑"""
    manager = BatchPostManager(None)
    
    # 测试权限不足
    advice = manager.get_tactical_advice("错误代码: 3250004, 用户没有权限")
    assert "描述: 等级不足" in advice["reason"] or "等级不足" in advice["reason"]
    assert "运行【全域签到】" in advice["action"]
    
    # 测试吧务拦截
    advice = manager.get_tactical_advice("发帖失败: 由于吧务设置，您暂时无法在该吧发帖")
    assert "触发了该贴吧吧务" in advice["reason"]
    assert "AI 强力改写" in advice["action"]
    
    # 测试百度词库拦截
    advice = manager.get_tactical_advice("内容中含有敏感词...")
    assert "百度平台级敏感词" in advice["reason"]
    assert "增加零宽字符密度" in advice["action"]

def test_get_tactical_advice_unknown_errors():
    """测试未知报错的兜底逻辑"""
    manager = BatchPostManager(None)
    
    advice = manager.get_tactical_advice("莫名其妙的百度错误 999")
    assert "未知干扰" in advice["reason"]
    assert "检查代理节点" in advice["action"]
