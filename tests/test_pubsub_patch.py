"""
测试 Flet 0.23.2 pubsub_hub monkey-patch 修复
运行: python tests/test_pubsub_patch.py
"""
import threading
import logging

logging.basicConfig(level=logging.DEBUG)

import flet_core.pubsub.pubsub_hub as _psh

# ── 1. 验证补丁已生效 ──────────────────────────────────────────
# start_web.py / launcher.py 在 import flet 后会替换 unsubscribe_all
# 这里单独测试补丁逻辑本身


def apply_patch():
    """与 start_web.py / launcher.py 中相同的补丁"""
    _orig_unsubscribe_all = _psh.PubSubHub.unsubscribe_all

    def _patched_unsubscribe_all(self, session_id: str):
        import logging as _log
        _log.getLogger(__name__).debug(f"pubsub.unsubscribe_all({session_id})")
        with self._PubSubHub__lock:
            self._PubSubHub__unsubscribe(session_id)
            if session_id in self._PubSubHub__subscriber_topics:
                for topic in list(self._PubSubHub__subscriber_topics[session_id].keys()):
                    self._PubSubHub__unsubscribe_topic(session_id, topic)

    _psh.PubSubHub.unsubscribe_all = _patched_unsubscribe_all
    return _orig_unsubscribe_all


def test_original_code_raises_error():
    """原始代码在多 topic 订阅时应抛出 RuntimeError"""
    hub = _psh.PubSubHub()
    sid = "session_1"

    # 模拟 subscribe_topic 的内部数据结构
    hub._PubSubHub__subscribers[sid] = True
    hub._PubSubHub__subscriber_topics[sid] = {"topic_a": True, "topic_b": True}
    hub._PubSubHub__topic_subscribers = {
        "topic_a": {sid: True},
        "topic_b": {sid: True},
    }
    hub._PubSubHub__lock = threading.Lock()

    try:
        # 原始 unsubscribe_all 直接迭代 .keys()，__unsubscribe_topic 会删除键
        for topic in hub._PubSubHub__subscriber_topics[sid].keys():
            hub._PubSubHub__unsubscribe_topic(sid, topic)
        print("[FAIL] 原始代码未抛出 RuntimeError（可能 topic 数太少未触发）")
    except RuntimeError as e:
        if "dictionary changed size" in str(e):
            print("[PASS] 原始代码正确触发 RuntimeError: dictionary changed size during iteration")
        else:
            print(f"[WARN] 其他 RuntimeError: {e}")


def test_patched_code_works():
    """补丁后的代码应正常工作"""
    apply_patch()

    hub = _psh.PubSubHub()
    sid = "session_1"

    # 模拟 subscribe_topic 的内部数据结构
    hub._PubSubHub__subscribers[sid] = True
    hub._PubSubHub__subscriber_topics[sid] = {"topic_a": True, "topic_b": True, "topic_c": True}
    hub._PubSubHub__topic_subscribers = {
        "topic_a": {sid: True},
        "topic_b": {sid: True},
        "topic_c": {sid: True},
    }
    hub._PubSubHub__lock = threading.Lock()

    try:
        hub.unsubscribe_all(sid)
        # 验证清理干净
        assert sid not in hub._PubSubHub__subscriber_topics, "subscriber_topics 未清理"
        assert sid not in hub._PubSubHub__subscribers, "subscribers 未清理"
        assert not hub._PubSubHub__topic_subscribers, "topic_subscribers 未清理"
        print("[PASS] 补丁后 unsubscribe_all 正常执行，数据清理完整")
    except RuntimeError as e:
        print(f"[FAIL] 补丁后仍抛出 RuntimeError: {e}")


def test_patched_in_real_app():
    """验证通过 start_web.py 启动后补丁是否生效"""
    try:
        # 模拟 start_web.py 的导入流程
        import importlib
        import sys

        # 检查是否已经 import 过 flet（例如在当前进程中）
        if "flet" in sys.modules:
            # 补丁可能已生效，检查方法签名
            import inspect
            source = inspect.getsource(_psh.PubSubHub.unsubscribe_all)
            if "list(" in source:
                print("[PASS] 当前进程中补丁已生效（unsubscribe_all 包含 list() 修复）")
            else:
                print("[WARN] 当前进程中补丁未生效（unsubscribe_all 不包含 list() 修复）")
        else:
            print("[INFO] flet 未在当前进程加载，需要通过 start_web.py 启动后验证")
    except Exception as e:
        print(f"[INFO] 无法检查运行时补丁状态: {e}")


if __name__ == "__main__":
    print("=" * 60)
    print("Flet 0.23.2 pubsub_hub monkey-patch 测试")
    print("=" * 60)

    print("\n── 测试 1: 原始代码是否触发 bug ──")
    test_original_code_raises_error()

    print("\n── 测试 2: 补丁代码是否修复 bug ──")
    test_patched_code_works()

    print("\n── 测试 3: 检查运行时补丁状态 ──")
    test_patched_in_real_app()

    print("\n" + "=" * 60)
    print("测试完成")
