"""A small arithmetic evaluator that parses to an AST and never calls `eval`."""

import ast
import math
import operator
from collections.abc import Callable

MAX_EXPRESSION_CHARS = 500
MAX_EXPONENT = 1_000
MAX_DEPTH = 32
MAX_MAGNITUDE = 1e300

_BINARY: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
}
_UNARY: dict[type[ast.unaryop], Callable[[float], float]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_FUNCTIONS: dict[str, Callable[..., float]] = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
}
_CONSTANTS = {"pi": math.pi, "e": math.e}


class CalculatorError(ValueError):
    """The expression isn't allowed or can't be evaluated."""


def calculate(expression: str) -> float:
    if len(expression) > MAX_EXPRESSION_CHARS:
        raise CalculatorError(f"expressions are limited to {MAX_EXPRESSION_CHARS} characters")
    try:
        tree = ast.parse(expression.replace("^", "**"), mode="eval")
        result = _evaluate(tree.body, 0)
    except CalculatorError:
        raise
    except (SyntaxError, RecursionError, MemoryError) as exc:
        raise CalculatorError("couldn't parse the expression") from exc
    except (ArithmeticError, ValueError, TypeError) as exc:
        raise CalculatorError(f"couldn't evaluate the expression: {exc}") from exc
    return result


def _checked(value: float) -> float:
    if isinstance(value, float) and not math.isfinite(value):
        raise CalculatorError("the result is too large")
    if abs(value) > MAX_MAGNITUDE:
        raise CalculatorError("the result is too large")
    return value


def _evaluate(node: ast.expr, depth: int) -> float:
    if depth > MAX_DEPTH:
        raise CalculatorError("the expression is too deeply nested")
    next_depth = depth + 1
    match node:
        case ast.Constant(value=int() | float() as value) if not isinstance(value, bool):
            return _checked(value)
        case ast.Name(id=name) if name in _CONSTANTS:
            return _CONSTANTS[name]
        case ast.BinOp(left=left, op=ast.Pow(), right=right):
            base, exponent = _evaluate(left, next_depth), _evaluate(right, next_depth)
            if abs(exponent) > MAX_EXPONENT:
                raise CalculatorError(f"exponents are limited to {MAX_EXPONENT}")
            return _checked(float(base) ** exponent)
        case ast.BinOp(left=left, op=op, right=right) if type(op) in _BINARY:
            operation = _BINARY[type(op)]
            return _checked(operation(_evaluate(left, next_depth), _evaluate(right, next_depth)))
        case ast.UnaryOp(op=op, operand=operand) if type(op) in _UNARY:
            return _UNARY[type(op)](_evaluate(operand, next_depth))
        case ast.Call(func=ast.Name(id=name), args=args, keywords=[]) if name in _FUNCTIONS:
            return _checked(_FUNCTIONS[name](*(_evaluate(arg, next_depth) for arg in args)))
        case _:
            raise CalculatorError(f"'{type(node).__name__}' isn't allowed in expressions")
