"""编辑弹窗与设置页不回显明文凭据 + 养号间隔保存校验（2026-11-24 全项目体检整改）

此前账号编辑弹窗把解密后的 BDUSS/STOKEN、代理编辑弹窗把解密后的密码、设置页把
AI Key 与 License Key 直接作为输入框的值下发到浏览器（password=True 只是前端显示打码，
can_reveal_password=True 还带显示按钮）——任何能打开控制台的人都能取走全部凭据。
现在这些框默认留空（留空=不修改），已存值只在服务端使用。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

_SRC = str(Path(__file__).parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# 模块顶层导入：保证被测页面绑定到真实 flet（见 test_access_control 的说明）
import tieba_mecha.core.account as account_core  # noqa: E402
import tieba_mecha.core.daemon as daemon_core  # noqa: E402
from tieba_mecha.core.daemon import sanitize_maint_interval  # noqa: E402
from tieba_mecha.web.pages import accounts as accounts_mod  # noqa: E402
from tieba_mecha.web.pages import proxy as proxy_mod  # noqa: E402
from tieba_mecha.web.pages import settings as settings_mod  # noqa: E402


class _FakePage:
    def __init__(self):
        self.controls = []
        self.overlay = []
        self.update = MagicMock()
        self.open = MagicMock()
        self.close = MagicMock()
        self.run_task = MagicMock()


def iter_controls(root):
    yield root
    for attr in ("controls", "actions"):
        for child in getattr(root, attr, None) or []:
            yield from iter_controls(child)
    content = getattr(root, "content", None)
    if content is not None and not isinstance(content, (str, list)):
        yield from iter_controls(content)


def _opened_dialog(page):
    page.open.assert_called_once()
    return page.open.call_args[0][0]


def _labeled(tree):
    """label → 控件（TextField / Dropdown 等带 label 的输入控件）"""
    return {c.label: c for c in iter_controls(tree) if isinstance(getattr(c, "label", None), str) and hasattr(c, "value")}


def _button(tree, text):
    return next(c for c in iter_controls(tree) if getattr(c, "text", None) == text and getattr(c, "on_click", None))


def _values_in(tree):
    return [c.value for c in iter_controls(tree) if hasattr(c, "value")]


@pytest.fixture
def fake_crypto(monkeypatch):
    """可逆的假加解密：密文 = 'enc:' + 明文，便于断言「下发/写库的是什么」"""
    monkeypatch.setattr(account_core, "decrypt_value", lambda v: v[len("enc:"):])
    monkeypatch.setattr(account_core, "encrypt_value", lambda v: "enc:" + v)


# ---------- 账号编辑弹窗 ----------

PLAIN_BDUSS = "P" * 192
PLAIN_STOKEN = "S" * 64


def _accounts_page():
    db = MagicMock()
    db.get_account = AsyncMock(return_value=SimpleNamespace(id=7, bduss="enc:" + PLAIN_BDUSS, stoken="enc:" + PLAIN_STOKEN))
    db.update_account = AsyncMock()
    db.update_account_status = AsyncMock()
    page = _FakePage()
    ap = accounts_mod.AccountsPage(page=page, db=db)
    ap._show_snackbar = MagicMock()
    ap.load_data = AsyncMock()
    return ap, db, page


async def _open_account_edit(ap, page):
    await ap._show_edit_dialog(SimpleNamespace(id=7, name="备注", user_name="显示名", user_id=42, proxy_id=None, post_weight=5))
    dialog = _opened_dialog(page)
    return dialog, _labeled(dialog)


async def test_account_edit_dialog_ships_no_credentials(fake_crypto):
    ap, db, page = _accounts_page()
    dialog, fields = await _open_account_edit(ap, page)

    assert not fields["BDUSS"].value            # 留空，不回显
    assert not fields["STOKEN (可选)"].value
    # 整棵弹窗控件树里没有任何控件带着凭据（明文或密文）
    for v in _values_in(dialog):
        assert PLAIN_BDUSS not in str(v) and PLAIN_STOKEN not in str(v)
        assert "enc:" not in str(v)
    assert fields["账号备注"].value == "备注"    # 非敏感字段照常预填
    db.update_account.assert_not_awaited()      # 打开弹窗不写库


async def test_account_edit_without_touching_credentials_keeps_them(fake_crypto, monkeypatch):
    verify = AsyncMock()
    monkeypatch.setattr(accounts_mod, "verify_account", verify)
    ap, db, page = _accounts_page()
    dialog, fields = await _open_account_edit(ap, page)

    fields["账号备注"].value = "新备注"
    await _button(dialog, "保存修改").on_click(None)

    verify.assert_not_awaited()                 # 凭据没动就不重新验证
    db.update_account.assert_awaited_once_with(7, name="新备注", proxy_id=None, post_weight=5)
    db.update_account_status.assert_not_awaited()


async def test_account_edit_with_new_credentials_writes_identity_back(fake_crypto, monkeypatch):
    """换凭据验证通过后：新凭据入库，验证得到的 UID/昵称/状态一并写回"""
    verify = AsyncMock(return_value=(True, 999, "新昵称", ""))
    monkeypatch.setattr(accounts_mod, "verify_account", verify)
    ap, db, page = _accounts_page()
    dialog, fields = await _open_account_edit(ap, page)

    fields["BDUSS"].value = "N" * 192
    await _button(dialog, "保存修改").on_click(None)

    verify.assert_awaited_once_with("N" * 192, PLAIN_STOKEN)   # 未改的 STOKEN 沿用库内值参与验证
    kwargs = db.update_account.await_args.kwargs
    assert kwargs["bduss"] == "enc:" + "N" * 192
    assert kwargs["stoken"] == "enc:" + PLAIN_STOKEN           # 未改动的 STOKEN 原样保留
    assert kwargs["user_id"] == 999 and kwargs["user_name"] == "新昵称"
    db.update_account_status.assert_awaited_once_with(7, "active")


async def test_account_edit_failed_verification_writes_nothing(fake_crypto, monkeypatch):
    verify = AsyncMock(return_value=(False, 0, "", "凭证无效"))
    monkeypatch.setattr(accounts_mod, "verify_account", verify)
    ap, db, page = _accounts_page()
    dialog, fields = await _open_account_edit(ap, page)

    fields["BDUSS"].value = "N" * 192
    save = _button(dialog, "保存修改")
    await save.on_click(None)

    db.update_account.assert_not_awaited()
    page.close.assert_not_called()              # 弹窗不关，可改正后重试
    assert save.disabled is False and save.text == "保存修改"
    assert ap._show_snackbar.call_args[0][0].startswith("新凭据验证失败")


# ---------- 代理编辑弹窗 ----------

def _proxy_page():
    db = MagicMock()
    db.update_proxy = AsyncMock()
    page = _FakePage()
    pp = proxy_mod.ProxyPage(page=page, db=db)
    pp._show_snackbar = MagicMock()
    pp.load_data = AsyncMock()
    pp._show_edit_dialog(SimpleNamespace(id=3, host="1.2.3.4", port=8080, protocol="http",
                                         username="enc:user1", password="enc:secret-pass"))
    return pp, db, _opened_dialog(page)


def test_proxy_edit_dialog_ships_no_password(fake_crypto):
    pp, db, dialog = _proxy_page()
    fields = _labeled(dialog)
    assert not fields["密码 (可选)"].value
    assert "secret-pass" not in "".join(str(v) for v in _values_in(dialog))
    assert fields["用户名 (可选)"].value == "user1"   # 用户名不是密钥，保留预填便于核对


async def test_proxy_edit_without_new_password_keeps_stored_one(fake_crypto):
    pp, db, dialog = _proxy_page()
    await _button(dialog, "保存修改").on_click(None)
    kwargs = db.update_proxy.await_args.kwargs
    assert "password" not in kwargs                 # 不传 = 库内密码原样保留
    assert kwargs["username"] == "user1" and kwargs["host"] == "1.2.3.4"


async def test_proxy_edit_with_new_password_saves_it(fake_crypto):
    pp, db, dialog = _proxy_page()
    _labeled(dialog)["密码 (可选)"].value = "brand-new-pass"
    await _button(dialog, "保存修改").on_click(None)
    assert db.update_proxy.await_args.kwargs["password"] == "brand-new-pass"


# ---------- 设置页：AI Key / License Key ----------

def _settings_page(monkeypatch):
    sp = settings_mod.SettingsPage.__new__(settings_mod.SettingsPage)
    sp.page = _FakePage()
    sp.db = MagicMock()
    sp.db.set_settings_bulk = AsyncMock()
    sp._show_snackbar = MagicMock()
    sp._jobs_info = []
    sp._settings = {
        "ai_api_key": "sk-stored-key", "license_key": "LIC-STORED",
        "obfuscator_density": "0.1", "obfuscator_symbols": True, "obfuscator_spacing": True, "obfuscator_shuffling": True,
    }
    sp._maint_config = {key: "1" for key in ("maint_acc_delay_min", "maint_acc_delay_max", "maint_interval_hours")}
    sp._maint_config.update({
        "maint_autofollow_enabled": "false", "maint_autofollow_hot_enabled": "false", "maint_harvest_enabled": "true",
    })
    monkeypatch.setattr(settings_mod, "daemon_instance", MagicMock(reload=AsyncMock()))
    monkeypatch.setattr(settings_mod, "get_auth_manager", AsyncMock(return_value=SimpleNamespace(status=None, license_info=None)))
    sp.build()
    # 养号间隔框给出合法值（保存前会校验该字段，空值会被拦下）
    sp.maint_fields["maint_interval_hours"].value = "4"
    return sp


async def test_settings_refresh_keeps_secret_fields_blank(monkeypatch):
    sp = _settings_page(monkeypatch)
    await sp.refresh_ui()
    assert sp.ai_key_field.value == "" and "已配置" in sp.ai_key_field.hint_text
    assert sp.license_key_field.value == "" and "已配置" in sp.license_key_field.hint_text
    for v in _values_in(sp.tabs):
        assert "sk-stored-key" not in str(v) and "LIC-STORED" not in str(v)


async def test_settings_save_keeps_untouched_secrets(monkeypatch):
    sp = _settings_page(monkeypatch)
    await sp._do_save(None)
    config = sp.db.set_settings_bulk.await_args.args[0]
    assert "ai_api_key" not in config and "license_key" not in config   # 没改动的密钥不写入
    assert "ai_base_url" in config


async def test_settings_save_writes_only_edited_secrets(monkeypatch):
    sp = _settings_page(monkeypatch)
    # 用户在 AI Key 框里输入了新值（on_change 置改动标记）
    sp.ai_key_field.value = "sk-new-key"
    sp.ai_key_field.on_change(SimpleNamespace(control=sp.ai_key_field))
    await sp._do_save(None)
    config = sp.db.set_settings_bulk.await_args.args[0]
    assert config["ai_api_key"] != "sk-new-key" and config["ai_api_key"]   # 入库的是密文
    assert "license_key" not in config


async def test_settings_license_verify_keeps_key_when_untouched(monkeypatch):
    sp = _settings_page(monkeypatch)
    sp.db.set_setting = AsyncMock()
    await sp._verify_license_online(SimpleNamespace(control=MagicMock()))
    sp.db.set_setting.assert_not_awaited()   # 空框不再把库内 License 冲掉


# ---------- 养号间隔 ----------

@pytest.mark.parametrize("raw, expected", [
    ("4", 4.0), (4, 4.0), (" 0.5 ", 0.5), ("0.25", 0.25), ("744", 744.0),
    ("0", 4.0), (0, 4.0), ("-1", 4.0), ("-0.001", 4.0), ("abc", 4.0), (None, 4.0),
    ("nan", 4.0), ("inf", 4.0), ("-inf", 4.0), ("1e9", 4.0), ("745", 4.0),
])
def test_sanitize_maint_interval(raw, expected):
    assert sanitize_maint_interval(raw) == expected
    assert expected >= daemon_core.MIN_MAINT_INTERVAL_HOURS


@pytest.mark.parametrize("bad", ["0", "-1", "0.01", "abc", "", "inf", "nan", "1e9", "745"])
async def test_settings_rejects_invalid_maint_interval(monkeypatch, bad):
    sp = _settings_page(monkeypatch)
    sp.maint_fields["maint_interval_hours"].value = bad
    await sp._do_save(None)
    sp.db.set_settings_bulk.assert_not_awaited()   # 不合法的值不落库
    assert "养号间隔" in sp._show_snackbar.call_args[0][0]


async def test_settings_accepts_valid_maint_interval(monkeypatch):
    sp = _settings_page(monkeypatch)
    sp.maint_fields["maint_interval_hours"].value = "6"
    await sp._do_save(None)
    assert sp.db.set_settings_bulk.await_args.args[0]["maint_interval_hours"] == "6"


# ---------- AI API Key：加密失败不得回退明文 ----------

def test_api_key_encryption_failure_raises_instead_of_storing_plaintext(monkeypatch):
    """此前加密失败会把明文 key 原样返回落库，只留一条 warning（2026-11-24 体检）"""
    import pytest

    from tieba_mecha.core import account as account_module
    from tieba_mecha.core.ai_optimizer import _encrypt_api_key

    monkeypatch.delenv("TIEBA_MECHA_SALT", raising=False)
    monkeypatch.delenv("TIEBA_MECHA_SECRET_KEY", raising=False)
    monkeypatch.setattr(account_module, "_ENCRYPTION_KEY", None)

    with pytest.raises(ValueError):
        _encrypt_api_key("sk-plain-secret-key")           # 不再返回明文


def test_api_key_encrypt_decrypt_round_trip_still_works():
    from tieba_mecha.core.ai_optimizer import _decrypt_api_key, _encrypt_api_key

    token = _encrypt_api_key("sk-abc-123")
    assert token != "sk-abc-123"                           # 库里是密文
    assert _decrypt_api_key(token) == "sk-abc-123"
    assert _decrypt_api_key("legacy-plaintext-key") == "legacy-plaintext-key"   # 兼容旧版明文


async def test_settings_save_reports_failure_when_key_cannot_be_encrypted(monkeypatch):
    """设置页保存时 key 加不了密：明文绝不进写库参数，用户看到「保存失败」"""
    from tieba_mecha.core import account as account_module

    sp = _settings_page(monkeypatch)
    monkeypatch.delenv("TIEBA_MECHA_SALT", raising=False)
    monkeypatch.delenv("TIEBA_MECHA_SECRET_KEY", raising=False)
    monkeypatch.setattr(account_module, "_ENCRYPTION_KEY", None)
    sp.ai_key_field.value = "sk-plain-secret-key"
    sp.ai_key_field.on_change(SimpleNamespace(control=sp.ai_key_field))

    await sp._do_save(None)                                # _do_save 自己兜底成 snackbar，不向外抛

    sp.db.set_settings_bulk.assert_not_awaited()           # 明文没有进入写库参数
    msgs = [str(c.args[0]) for c in sp._show_snackbar.call_args_list]
    assert any(m.startswith("保存失败") for m in msgs)
    assert not any("sk-plain-secret-key" in m for m in msgs)   # 失败提示里也不回显明文
