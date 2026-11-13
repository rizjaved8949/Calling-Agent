"""
The agent's arithmetic, from the figures the evaluation report got wrong.
"""
from __future__ import annotations

import pytest

from app.services.agent import calculator
from app.services.agent.calculator import CalculationError, evaluate


@pytest.mark.parametrize("expression, answer", [
    ("1500*12.2/100*55", "10065"),
    ("8,990,000-1,750,000", "7240000"),
    ("5% of 14,790,000", "739500"),
    ("14790000*5/100", "739500"),
    ("20% of 8,990,000 minus 1,500,000", "298000"),
    ("20/100*8990000-1500000", "298000"),
    ("8990000 - 1750000", "7240000"),
    ("16,990,000 × 0.2", "3398000"),
    ("Rs 14,790,000 - Rs 2,950,000", "11840000"),
    ("max(1, 2, 3) + min(4, 5)", "7"),
    ("round(10/3, 2)", "3.33"),
    ("abs(-5)", "5"),
    ("(1 + 2) * 3", "9"),
    ("10 % 3", "1"),
    ("2 ** 10", "1024"),
    ("7 / 2", "3.5"),
    ("1,00,000 + 1", "100001"),
    ("100 divided by 8", "12.5"),
])
def test_the_reports_sums_come_out_right(expression, answer):
    assert evaluate(expression) == answer


@pytest.mark.parametrize("expression", [
    "__import__('os').system('ls')",
    "os.system('ls')",
    "(1).real",
    "open('x')",
    "x + 1",
    "2 ** 100000",
    "9 ** 9 ** 9",
    "1 / 0",
    "[1, 2]",
    "1 if 2 else 3",
    "lambda: 1",
    "'a' * 3",
    "",
    "1 +",
    "x" * 300,
    "min()",
    "round(1, 2, 3)",
])
def test_anything_that_is_not_arithmetic_is_refused(expression):
    with pytest.raises(CalculationError):
        evaluate(expression)


def test_there_is_no_eval_in_the_calculator():
    import inspect

    source = inspect.getsource(calculator)
    assert "eval(" not in source.replace("_evaluate(", "").replace("evaluate(", "")
    assert "exec(" not in source


async def test_the_tool_answers_with_a_plain_number():
    from app.models.call import Call, Channel, Direction
    from app.models.tenant import Tenant
    from app.services.agent import live

    handle = live._tool_handler(
        Tenant(phoneNumberId="t-1", name="Acme"),
        Call(tenantId="t-1", channel=Channel.PHONE, direction=Direction.INBOUND,
             counterparty="+923001112222"),
    )
    assert await handle("calculate", {"expression": "8,990,000-1,750,000"}) == (
        "7240000. Say exactly: 72 lakh 40 hazar rupay. Never re-word or round it.")
    answer = await handle("calculate", {"expression": "open('x')"})
    assert answer.startswith("Could not calculate that:")
