"""
Arithmetic the agent can trust.

On real calls the model's own sums were off by a third, and a buyer was told
a car fitted a budget it did not. So every figure the agent works out goes
through this instead: a small evaluator over Python's own syntax tree, which
accepts numbers, `+ - * / % **`, brackets and `min`, `max`, `round`, `abs`,
and nothing else. There is no `eval` here, and no name reaches anything.

Lenient on the way in, because the expression is dictated by a model mid-call:
thousands separators, `×` and `÷`, "5% of 14,790,000", "minus" and "plus".
"""
from __future__ import annotations

import ast
import math
import operator
import re

#: Longer than any sum a call needs, and short enough that nothing clever fits.
MAX_LENGTH = 200
#: `2 ** 1000` is a denial of service; nothing on a phone call needs it.
MAX_EXPONENT = 12
MAX_MAGNITUDE = 1e15


class CalculationError(ValueError):
    """The expression is not arithmetic this will do."""


_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: None,          # handled apart, for the bound
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}
_FUNCTIONS = {"min": min, "max": max, "round": round, "abs": abs}

_WORDS = (
    (r"\bdivided\s+by\b", "/"), (r"\btimes\b", "*"), (r"\bmultiplied\s+by\b", "*"),
    (r"\bminus\b", "-"), (r"\bplus\b", "+"), (r"\binto\b", "*"),
)


def _clean(expression: str) -> str:
    text = (expression or "").strip()
    if not text:
        raise CalculationError("there is nothing to calculate")
    if len(text) > MAX_LENGTH:
        raise CalculationError("that expression is too long")
    text = text.replace("×", "*").replace("÷", "/").replace("−", "-").replace("^", "**")
    text = re.sub(r"(?i)\b(?:pkr|rs\.?|rupees?)\b", " ", text)
    text = text.replace("₨", " ")
    for pattern, symbol in _WORDS:
        text = re.sub(pattern, symbol, text, flags=re.I)
    # "5% of 14,790,000" is a share, not a modulo.
    text = re.sub(r"(\d+(?:\.\d+)?)\s*%\s*of\s*", r"(\1/100)*", text, flags=re.I)
    # "20% " with nothing to be "of" is still a share.
    text = re.sub(r"(\d+(?:\.\d+)?)\s*%(?!\s*[\d(])", r"(\1/100)", text)
    # Thousands separators, in either grouping.
    text = re.sub(r"(?<=\d),(?=\d{2,3}\b)", "", text)
    # "x" between numbers is a times sign on a phone.
    text = re.sub(r"(?<=[\d)\s])x(?=[\s\d(])", "*", text, flags=re.I)
    # A currency word stripped from the front leaves a space, which the
    # parser reads as an indent.
    return text.strip()


def _evaluate(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise CalculationError("only numbers are allowed")
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in (ast.USub, ast.UAdd):
        return _OPERATORS[type(node.op)](_evaluate(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _OPERATORS:
        left, right = _evaluate(node.left), _evaluate(node.right)
        if isinstance(node.op, ast.Pow):
            if abs(right) > MAX_EXPONENT:
                raise CalculationError(f"powers above {MAX_EXPONENT} are not allowed")
            result = left ** right
        else:
            try:
                result = _OPERATORS[type(node.op)](left, right)
            except ZeroDivisionError as exc:
                raise CalculationError("division by zero") from exc
        if isinstance(result, complex) or abs(result) > MAX_MAGNITUDE:
            raise CalculationError("that number is too large")
        return result
    if isinstance(node, ast.Call):
        name = node.func.id if isinstance(node.func, ast.Name) else ""
        if name not in _FUNCTIONS or node.keywords:
            raise CalculationError("only min, max, round and abs may be called")
        args = [_evaluate(arg) for arg in node.args]
        if not args or len(args) > 10:
            raise CalculationError(f"{name} needs between one and ten numbers")
        if name == "round" and len(args) > 2:
            raise CalculationError("round takes a number and, optionally, decimal places")
        if name == "round" and len(args) == 2:
            return round(args[0], int(args[1]))
        return _FUNCTIONS[name](*args)
    if isinstance(node, ast.Name):
        raise CalculationError(f"'{node.id}' is not a number")
    raise CalculationError("only numbers and + - * / % ** are allowed")


def _plain(result: float) -> str:
    if isinstance(result, float) and (math.isnan(result) or math.isinf(result)):
        raise CalculationError("that does not come to a number")
    if isinstance(result, float) and result.is_integer():
        return str(int(result))
    if isinstance(result, float):
        text = f"{round(result, 2):.2f}".rstrip("0").rstrip(".")
        return text or "0"
    return str(result)


def evaluate(expression: str) -> str:
    """The value of `expression`, as a plain number string.

    Raises `CalculationError` with a sentence the agent can act on.
    """
    cleaned = _clean(expression)
    try:
        tree = ast.parse(cleaned, mode="eval")
    except SyntaxError as exc:
        raise CalculationError("that is not a sum I can read; write it with numbers "
                               "and + - * / only") from exc
    return _plain(_evaluate(tree))
