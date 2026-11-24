"""未定义名称门禁（2026-11-24 全项目体检整改）

体检中两处「点击即崩」的 NameError 都是最朴素的一类——名字在作用域里根本没绑定（ruff F821）：
- accounts.py：import 写在包装方法里，使用它的却是平级的另一个方法（智能权重计算，坏了两个多月）；
- batch_post_page.py：except 分支里的 log_error 没导入（保存自顶配置失败时错误处理本身崩溃）。
项目没有 CI、ruff 全量有近两千条风格噪音无法整体设卡，这里用 AST 只查这一类高信号错误，
随每次 pytest 跑批执行，不依赖 ruff 是否安装。

判定刻意保守（宁可漏报不误报）：不区分赋值先后与分支，只要名字在该函数、其外层函数、
模块顶层或内置名里「任何位置」绑定过就算已定义；函数头（装饰器/注解/默认值）与类型注解不查。
"""

from __future__ import annotations

import ast
import builtins
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SCAN_PATHS = [
    ROOT / "src",
    ROOT / "start_web.py",
    ROOT / "launcher.py",
    ROOT / "scripts" / "templates" / "launcher.py",
]

_BUILTINS = set(dir(builtins))
# 类体/模块体里由解释器隐式提供、或 dataclass 约定的隐式名
_IMPLICIT = {
    "__name__", "__file__", "__doc__", "__package__", "__spec__", "__loader__", "__builtins__",
    "__annotations__", "__path__", "__all__", "__module__", "__qualname__", "__dict__", "__class__",
    "__weakref__", "__slots__", "__cached__", "__init_subclass__", "__set_name__", "__class_getitem__",
    "__post_init__", "__post_init_posargs__", "__dataclass_fields__",
}
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def _scope_nodes(scope) -> list:
    """作用域体内「属于本作用域」的顶层节点（不含函数头；AnnAssign 只留目标与值，注解不求值）"""
    nodes = [scope.body] if isinstance(scope, ast.Lambda) else list(scope.body)
    return nodes


def _add_target(target: ast.expr, bound: set[str]) -> None:
    if isinstance(target, ast.Name):
        bound.add(target.id)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            _add_target(elt, bound)
    elif isinstance(target, ast.Starred):
        _add_target(target.value, bound)
    # Attribute / Subscript 作为赋值目标不绑定名字


def _add_args(args: ast.arguments, bound: set[str]) -> None:
    for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
        bound.add(a.arg)
    if args.vararg:
        bound.add(args.vararg.arg)
    if args.kwarg:
        bound.add(args.kwarg.arg)


def _walk_scope(scope):
    """深度遍历作用域体内属于本作用域的全部节点：遇到嵌套作用域就停，不下钻任何函数头/注解。

    每个作用域只收集「自己」绑定的名字；嵌套函数能看见外层局部名（闭包）这一点由
    find_undefined_names 逐层把父作用域的绑定传下去实现。若在这里连嵌套作用域一起走，
    模块层就会把所有函数内部的 import 都当成全局可见——门禁随即漏报。
    """
    stack = _scope_nodes(scope)
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, _SCOPES):
            continue                   # 嵌套作用域不在这里下钻（函数头也随之跳过）
        if isinstance(node, ast.AnnAssign):
            if node.value is not None:
                stack.append(node.value)
            stack.append(node.target)
        else:
            stack.extend(ast.iter_child_nodes(node))


def _bindings(scope) -> set[str]:
    """收集一个作用域自身绑定过的所有名字（不区分赋值先后与分支，保守放大）"""
    bound: set[str] = set()
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        _add_args(scope.args, bound)
    for node in _walk_scope(scope):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, ast.alias):
            bound.add((node.asname or node.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound.update(node.names)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.MatchAs) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.MatchStar) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            bound.add(node.rest)
    return bound


def _own_loads(scope) -> list[tuple[int, str]]:
    """本作用域自己读取的名字 → [(行号, 名字)]；更内层的函数/类/lambda 作用域不展开"""
    return [
        (n.lineno, n.id)
        for n in _walk_scope(scope)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
    ]


def _nested_scopes(scope):
    """本作用域内直接定义的嵌套作用域（不含更深一层）"""
    return [n for n in _walk_scope(scope) if isinstance(n, _SCOPES)]


def _has_star_import(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names)
        for node in ast.walk(tree)
    )


def find_undefined_names(source: str, filename: str = "<snippet>") -> list[tuple[int, str, str]]:
    """返回 [(行号, 未定义名字, 所在函数/类的限定名 或 '<module>'), ...]"""
    tree = ast.parse(textwrap.dedent(source).strip("\n"), filename=filename)
    if _has_star_import(tree):
        return []  # 有星号导入时无法判定，整体跳过
    module_visible = _bindings(tree) | _BUILTINS | _IMPLICIT
    findings: list[tuple[int, str, str]] = []

    for lineno, name in _own_loads(tree):
        if name not in module_visible:
            findings.append((lineno, name, "<module>"))

    def walk(scope, visible: set[str], qualname: str) -> None:
        for nested in _nested_scopes(scope):
            own = _bindings(nested)
            label = f"{qualname}.{getattr(nested, 'name', '<lambda>')}"
            for lineno, name in _own_loads(nested):
                if name not in own | visible:
                    findings.append((lineno, name, label))
            walk(nested, visible | own, label)

    walk(tree, module_visible, "")
    return findings


def scan_project() -> list[str]:
    problems: list[str] = []
    for path in sorted(SCAN_PATHS):
        files = [path] if path.is_file() else sorted(path.rglob("*.py"))
        for py in files:
            if "__pycache__" in py.parts:
                continue
            rel = py.relative_to(ROOT).as_posix()
            try:
                source = py.read_text(encoding="utf-8")
                findings = find_undefined_names(source, filename=rel)
            except (OSError, SyntaxError, ValueError) as e:
                problems.append(f"{rel}: 无法分析（{e}）")
                continue
            for lineno, name, where in findings:
                problems.append(f"{rel}:{lineno}: `{name}` 在 {where or '<module>'} 中未定义")
    return problems


# ---------- 门禁本身的自测：必须抓得住体检里出过事的两种形态 ----------

def test_gate_catches_import_left_in_sibling_method():
    """accounts.py 智能权重计算的原型：import 在包装方法里，使用在平级的另一个方法里"""
    src = '''
        class Page:
            async def wrapper(self):
                from x import Calculator
                await self._impl()

            async def _impl(self):
                ratios = await Calculator.get_ratios()
        '''
    assert [(n, w) for _, n, w in find_undefined_names(src)] == [("Calculator", ".Page._impl")]


def test_gate_catches_unimported_name_in_except_branch():
    """batch_post_page._save_bump_config 的原型：except 分支里用到没导入的日志函数"""
    src = '''
        import asyncio

        async def handler(self):
            try:
                await asyncio.sleep(0)
            except Exception as ex:
                await log_error(f"failed: {ex}")
        '''
    assert [n for _, n, _ in find_undefined_names(src)] == ["log_error"]


def test_gate_reports_undefined_module_level_name():
    assert [n for _, n, _ in find_undefined_names("import os\nprint(undefined_thing)\n")] == ["undefined_thing"]


@pytest.mark.parametrize("src", [
    "import os\n\nasync def f(self):\n    if os.sep:\n        helper = 1\n    return helper\n",       # 分支内赋值也算已定义（保守）
    "def outer():\n    x = 1\n    def inner():\n        return x\n    return inner\n",                   # 闭包变量
    "def f(a, *args, b=1, **kw):\n    return a, args, b, kw\n",                                        # 各类参数
    "def f():\n    global g\n    g = 1\n    return g\n",                                                 # global 声明
    "def f():\n    with open('x') as fh:\n        return fh\n",                                          # with-as
    "def f():\n    try:\n        pass\n    except ValueError as e:\n        return e\n",                 # except-as
    "def f(items):\n    return [y for y in items if y]\n",                                               # 推导式目标
    "def f(items):\n    return sorted(items, key=lambda v: v.name)\n",                                  # lambda 参数
    "def f():\n    match 1:\n        case [a, *rest]:\n            return a, rest\n",                    # match 捕获
    "def f(n):\n    if (m := n + 1):\n        return m\n",                                              # 海象运算符
    "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    from x import Y\n\ndef f(a) -> Y:\n    return a\n",  # 仅注解使用的名字
    "def f(x):\n    y: SomeType = x\n    return y\n",                                                   # 局部变量注解不求值
    "@decorator\ndef f(x=DEFAULT):\n    return x\n",                                                    # 装饰器与默认值属外层作用域，不查
    "class C:\n    X = 1\n    Y = X + 1\n",                                                               # 类体
    "class C:\n    def m(self):\n        return __class__\n",                                           # 隐式名
    "class C:\n    def m(self):\n        def inner():\n            return self\n        return inner\n",     # 方法里的嵌套函数
    "from os.path import *\n\n\nasync def f(self):\n    return join('a', 'b')\n",                       # 星号导入：整体跳过
    "def f():\n    (a, b), *c = (1, 2), 3, 4\n    return a, b, c\n",                                    # 嵌套解包
    "def f():\n    import a.b as ab\n    from x.y import z as zz\n    return ab, zz\n",                  # 函数内导入
    "def f():\n    if True:\n        async def g():\n            return helper\n    return g\n\nhelper = 1\n",  # if 块里的嵌套函数
])
def test_gate_has_no_false_positives_on_legit_code(src):
    assert find_undefined_names(src) == []


# ---------- 真正的门禁 ----------

def test_no_undefined_names_in_source():
    problems = scan_project()
    assert not problems, "发现未定义名称（点击即崩的 NameError）:\n" + "\n".join(problems)
