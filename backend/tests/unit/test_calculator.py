import math

import pytest

from app.tools.calculator import CalculatorError, calculate


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("1 + 2 * 3", 7),
        ("(1 + 2) * 3", 9),
        ("2 ** 10", 1024),
        ("2 ^ 10", 1024),
        ("-5 + 3", -2),
        ("7 // 2", 3),
        ("7 % 2", 1),
        ("sqrt(16)", 4),
        ("round(2.6)", 3),
        ("max(1, 5, 3)", 5),
        ("pi * 2", math.pi * 2),
        ("8849 * 3.28084", 8849 * 3.28084),
    ],
)
def test_valid_expressions(expression: str, expected: float) -> None:
    assert calculate(expression) == pytest.approx(expected)


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('id')",
        "open('/etc/passwd')",
        "().__class__.__bases__",
        "(1).real",
        "[x for x in (1, 2)]",
        "lambda: 1",
        "'a' * 5",
        "True + 1",
        "abs(x=1)",
        "exec('1')",
        "1 if 1 else 2",
        "pi.real",
    ],
)
def test_anything_but_arithmetic_is_refused(expression: str) -> None:
    with pytest.raises(CalculatorError):
        calculate(expression)


@pytest.mark.parametrize(
    "expression",
    ["9 ** 9 ** 9", "2 ** 100000", "10 ** 400", "1e308 * 10", "exp(100000)", "(" * 400 + "1"],
)
def test_expensive_expressions_are_refused(expression: str) -> None:
    with pytest.raises(CalculatorError):
        calculate(expression)


@pytest.mark.parametrize("expression", ["1 / 0", "sqrt(-1)", "log(0)", "1 +", "", "max()"])
def test_impossible_expressions_give_a_clean_error(expression: str) -> None:
    with pytest.raises(CalculatorError):
        calculate(expression)


def test_nesting_is_limited() -> None:
    with pytest.raises(CalculatorError):
        calculate("-" * 100 + "1")


def test_length_is_limited() -> None:
    with pytest.raises(CalculatorError, match="limited"):
        calculate("1+" * 300 + "1")
