"""
payroll.methods.safe_tax_code

Sandboxed evaluation of the user-supplied federal-tax formula stored on
``FilingStatus.python_code``.

Historically the ``python_code`` field was executed with a bare ``exec()`` and
only a couple of string replacements as "sanitisation". That allowed any user
able to create/edit a filing status to run arbitrary Python (and therefore OS
commands) on the server whenever a payslip was generated -- a CWE-94 code
injection / RCE.

This module replaces that with a dependency-free sandbox that:

* parses the code with :func:`ast.parse` and rejects any disallowed construct
  (imports, attribute access to dunders, calls to dangerous builtins, lambdas
  that reach into internals, etc.) *before* anything is executed;
* executes the validated code with ``__builtins__`` reduced to a tiny, safe
  allow-list (numeric/sequence helpers only);
* exposes a single :func:`validate_tax_code` entry point used at *save time*
  (so bad code is rejected before it is ever stored) and a
  :func:`run_tax_code` entry point used at *evaluation time* (defence in
  depth).

The sandbox is intentionally strict: the contract is simply that the code
defines ``calculate_federal_tax(yearly_income)`` returning a number. No
imports, file access, attribute introspection, or I/O are permitted.
"""

import ast

__all__ = [
    "TaxCodeValidationError",
    "validate_tax_code",
    "run_tax_code",
]


class TaxCodeValidationError(ValueError):
    """Raised when user-supplied tax code violates the sandbox policy."""


# Builtins that are safe to expose to the formula. Deliberately minimal:
# numeric/sequence helpers only, nothing that touches the filesystem,
# imports, evaluation, or introspection.
_SAFE_BUILTINS = {
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
    "sum": sum,
    "len": len,
    "range": range,
    "float": float,
    "int": int,
    "bool": bool,
    "dict": dict,
    "list": list,
    "tuple": tuple,
    "set": set,
    "enumerate": enumerate,
    "sorted": sorted,
    "zip": zip,
    "map": map,
    "filter": filter,
    "pow": pow,
    "divmod": divmod,
}

# AST node types that are flat-out forbidden anywhere in the source.
_FORBIDDEN_NODES = (
    ast.Import,
    ast.ImportFrom,
    ast.Global,
    ast.Nonlocal,
    # Async constructs have no place in a synchronous tax formula and only add
    # surface area.
    ast.AsyncFunctionDef,
    ast.Await,
    ast.AsyncFor,
    ast.AsyncWith,
)

# Names that must never appear as identifiers, calls, or string-built
# attribute lookups -- these are the classic sandbox-escape primitives.
_FORBIDDEN_NAMES = frozenset(
    {
        "exec",
        "eval",
        "compile",
        "open",
        "__import__",
        "input",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
        "hasattr",
        "breakpoint",
        "memoryview",
        "classmethod",
        "staticmethod",
        "super",
        "type",
        "object",
    }
)

# The single required entry-point function.
ENTRY_POINT = "calculate_federal_tax"


class _PolicyVisitor(ast.NodeVisitor):
    """Walks the parsed AST and records any policy violation."""

    def __init__(self):
        self.errors = []

    def _fail(self, node, message):
        lineno = getattr(node, "lineno", "?")
        self.errors.append(f"line {lineno}: {message}")

    def generic_visit(self, node):
        if isinstance(node, _FORBIDDEN_NODES):
            self._fail(
                node,
                f"{type(node).__name__} is not allowed in tax code "
                "(imports, async, and global/nonlocal are forbidden).",
            )
            return
        super().generic_visit(node)

    def visit_Attribute(self, node):
        # Block any dunder attribute access -- this is the route to
        # __class__ / __subclasses__ / __globals__ sandbox escapes.
        if isinstance(node.attr, str) and node.attr.startswith("__"):
            self._fail(
                node, f"access to dunder attribute '{node.attr}' is not allowed."
            )
        self.generic_visit(node)

    def visit_Name(self, node):
        if node.id in _FORBIDDEN_NAMES:
            self._fail(node, f"use of '{node.id}' is not allowed.")
        if node.id.startswith("__") and node.id.endswith("__"):
            self._fail(node, f"use of dunder name '{node.id}' is not allowed.")
        self.generic_visit(node)


def _check(code: str):
    """Parse ``code`` and return the parsed module, raising on any violation."""
    if not isinstance(code, str) or not code.strip():
        raise TaxCodeValidationError("Tax code is empty.")

    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise TaxCodeValidationError(f"Syntax error in tax code: {exc}") from exc

    visitor = _PolicyVisitor()
    visitor.visit(tree)
    if visitor.errors:
        raise TaxCodeValidationError(
            "Tax code rejected by sandbox policy:\n  " + "\n  ".join(visitor.errors)
        )

    defines_entry = any(
        isinstance(node, ast.FunctionDef) and node.name == ENTRY_POINT
        for node in tree.body
    )
    if not defines_entry:
        raise TaxCodeValidationError(
            f"Tax code must define a top-level function '{ENTRY_POINT}(yearly_income)'."
        )

    return tree


def validate_tax_code(code: str) -> None:
    """Validate user-supplied tax code without executing it.

    Use this at *save time*. Raises :class:`TaxCodeValidationError` describing
    every policy violation found, or returns ``None`` if the code is safe.
    """
    _check(code)


def run_tax_code(code: str, yearly_income):
    """
    DISABLED - Python tax formulas no longer run.

    The sandbox this used to run them in (the AST checks above plus a
    reduced ``__builtins__``) is a denylist, and denylist sandboxes are
    escapable and have no resource limits: only names starting with "__"
    were blocked as attributes, so frame/generator attributes such as
    ``gi_frame``/``f_back``/``f_globals`` (which lead to the real builtins)
    were reachable, and ``while True: pass`` or ``pow(10, 10**9)`` would
    freeze a worker for every payslip being generated. Tax is already
    computed from the declarative bracket table (TaxBracket) for every
    filing status without ``use_py``, so that path is used instead - see
    payroll/methods/tax_calc.py.

    The original implementation is commented out below rather than
    deleted. Will be redesigned (an allow-list AST visitor plus a separate
    process with a CPU/memory/time limit, or dropping formulas entirely in
    favour of brackets) and re-enabled, if ever needed, in further
    development.
    """
    raise TaxCodeValidationError(
        "Python tax formulas are disabled. Use tax brackets instead."
    )

    # ------------------------------------------------------------------
    # Original implementation - disabled, see docstring above.
    # ------------------------------------------------------------------
    # _check(code)
    #
    # # No-op stand-in for the template's debug helpers so legacy formulas that
    # # call print()/formated_result() at module load do not blow up. They simply
    # # do nothing in production.
    # def _noop(*args, **kwargs):
    #     return None
    #
    # sandbox_globals = {
    #     "__builtins__": dict(_SAFE_BUILTINS),
    #     "print": _noop,
    #     "pass_print": _noop,
    #     "formated_result": _noop,
    # }
    # local_vars = {}
    #
    # # The AST check above has already guaranteed there are no imports, dunder
    # # escapes, or dangerous calls; execution happens with the restricted
    # # builtins only.
    # compiled = compile(code, "<tax_code>", "exec")
    # exec(
    #     compiled, sandbox_globals, local_vars
    # )  # noqa: S102 - sandboxed; see module docstring
    #
    # func = local_vars.get(ENTRY_POINT) or sandbox_globals.get(ENTRY_POINT)
    # if not callable(func):
    #     raise TaxCodeValidationError(
    #         f"Tax code did not define a callable '{ENTRY_POINT}'."
    #     )
    # return func(yearly_income)
