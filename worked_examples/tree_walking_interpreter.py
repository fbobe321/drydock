"""Worked example: tree-walking interpreter for a toy language.

Canonical shape for the lang_interp class of PRDs. The three-layer
separation (lexer → parser → interpreter) is the critical pattern —
the common failure mode is to try to evaluate during parsing, which
produces subtle ordering bugs (binary operators with wrong precedence,
variables resolved at parse time, etc.).

Key insights this example demonstrates:

1. **Three separate passes, three separate files.** Tokens are values.
   AST nodes are values. The Interpreter walks the AST. None of these
   layers share state. If the model writes `interpret_expression()` that
   tokenizes + parses + evaluates in one function, the PRD's "add a new
   operator" test fails because there's nowhere to inject.

2. **Operator precedence via parser method nesting.** The pattern is
   `expression → addition → multiplication → atom`. The OUTER method
   calls the INNER. Do NOT build a single `parse_expression` that
   branches on the operator — you get left-to-right evaluation that
   breaks `2 + 3 * 4`.

3. **Environment as an explicit object, not a dict of the Interpreter.**
   Scoping (function calls, let-bindings, closures) needs `Environment`
   to be a chainable object with a `parent` pointer. A plain dict on
   `Interpreter` cannot implement lexical scope.

4. **`visit_<NodeType>` dispatch.** Standard tree-walker shape. Makes
   adding a new node type (e.g., `IfExpr`) a one-method change.

5. **Interpreter lives in its own file.** NEVER define `class Interpreter`
   inline in cli.py with `pass` methods. The module is expected at
   `pkg/interpreter.py`. Write the REAL class there.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ─── Layer 1: Lexer ──────────────────────────────────────────────────

@dataclass
class Token:
    type: str
    value: Any
    line: int = 1


class Lexer:
    """Convert source text to a stream of Tokens.

    Single-char tokens are fast-path. Multi-char tokens (numbers,
    identifiers, keywords) need lookahead. Keywords are identifiers
    with a post-lex rewrite — easier than building keyword trie.
    """

    KEYWORDS = {"let", "if", "else", "fn", "return", "true", "false", "nil"}
    SINGLE = {
        "(": "LPAREN", ")": "RPAREN",
        "{": "LBRACE", "}": "RBRACE",
        ",": "COMMA", ";": "SEMI",
        "+": "PLUS", "-": "MINUS", "*": "STAR", "/": "SLASH",
    }

    def __init__(self, src: str):
        self.src = src
        self.pos = 0
        self.line = 1

    def tokenize(self) -> list[Token]:
        tokens: list[Token] = []
        while self.pos < len(self.src):
            c = self.src[self.pos]
            if c in " \t":
                self.pos += 1
            elif c == "\n":
                self.line += 1
                self.pos += 1
            elif c.isdigit():
                tokens.append(self._read_number())
            elif c.isalpha() or c == "_":
                tokens.append(self._read_ident())
            elif c == "=":
                # `==` vs `=`
                if self.pos + 1 < len(self.src) and self.src[self.pos + 1] == "=":
                    tokens.append(Token("EQ", "==", self.line)); self.pos += 2
                else:
                    tokens.append(Token("ASSIGN", "=", self.line)); self.pos += 1
            elif c in self.SINGLE:
                tokens.append(Token(self.SINGLE[c], c, self.line))
                self.pos += 1
            else:
                raise SyntaxError(f"line {self.line}: unexpected char {c!r}")
        tokens.append(Token("EOF", None, self.line))
        return tokens

    def _read_number(self) -> Token:
        start = self.pos
        while self.pos < len(self.src) and self.src[self.pos].isdigit():
            self.pos += 1
        return Token("INT", int(self.src[start:self.pos]), self.line)

    def _read_ident(self) -> Token:
        start = self.pos
        while self.pos < len(self.src) and (
            self.src[self.pos].isalnum() or self.src[self.pos] == "_"
        ):
            self.pos += 1
        text = self.src[start:self.pos]
        if text in self.KEYWORDS:
            return Token(text.upper(), text, self.line)
        return Token("IDENT", text, self.line)


# ─── Layer 2: AST node types ─────────────────────────────────────────

@dataclass
class IntLit: value: int
@dataclass
class BoolLit: value: bool
@dataclass
class Var: name: str
@dataclass
class BinOp: op: str; left: Any; right: Any
@dataclass
class Let: name: str; value: Any
@dataclass
class If: cond: Any; then_branch: list; else_branch: list
@dataclass
class FnDef: name: str; params: list[str]; body: list
@dataclass
class Call: fn: Any; args: list
@dataclass
class Return: value: Any


# ─── Layer 3: Parser (recursive descent with precedence climbing) ────

class Parser:
    """Build an AST from the token stream.

    The method call chain (`parse_expr` → `parse_add` → `parse_mul` →
    `parse_atom`) IS the precedence table. Do not try to make this
    one method.
    """

    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> Token:
        return self.tokens[self.pos]

    def eat(self, ttype: str | None = None) -> Token:
        tok = self.tokens[self.pos]
        if ttype is not None and tok.type != ttype:
            raise SyntaxError(
                f"line {tok.line}: expected {ttype}, got {tok.type}({tok.value!r})"
            )
        self.pos += 1
        return tok

    def parse_program(self) -> list:
        stmts = []
        while self.peek().type != "EOF":
            stmts.append(self.parse_stmt())
        return stmts

    def parse_stmt(self):
        tok = self.peek()
        if tok.type == "LET":
            return self._parse_let()
        if tok.type == "IF":
            return self._parse_if()
        if tok.type == "FN":
            return self._parse_fn()
        if tok.type == "RETURN":
            self.eat("RETURN")
            val = self.parse_expr()
            if self.peek().type == "SEMI":
                self.eat("SEMI")
            return Return(val)
        expr = self.parse_expr()
        if self.peek().type == "SEMI":
            self.eat("SEMI")
        return expr

    def _parse_let(self) -> Let:
        self.eat("LET")
        name = self.eat("IDENT").value
        self.eat("ASSIGN")
        value = self.parse_expr()
        if self.peek().type == "SEMI":
            self.eat("SEMI")
        return Let(name, value)

    def _parse_if(self) -> If:
        self.eat("IF")
        self.eat("LPAREN")
        cond = self.parse_expr()
        self.eat("RPAREN")
        then_branch = self._parse_block()
        else_branch = []
        if self.peek().type == "ELSE":
            self.eat("ELSE")
            else_branch = self._parse_block()
        return If(cond, then_branch, else_branch)

    def _parse_fn(self) -> FnDef:
        self.eat("FN")
        name = self.eat("IDENT").value
        self.eat("LPAREN")
        params: list[str] = []
        while self.peek().type != "RPAREN":
            params.append(self.eat("IDENT").value)
            if self.peek().type == "COMMA":
                self.eat("COMMA")
        self.eat("RPAREN")
        body = self._parse_block()
        return FnDef(name, params, body)

    def _parse_block(self) -> list:
        self.eat("LBRACE")
        stmts = []
        while self.peek().type != "RBRACE":
            stmts.append(self.parse_stmt())
        self.eat("RBRACE")
        return stmts

    # expression precedence chain — each level calls the level BELOW it
    def parse_expr(self):
        return self._parse_cmp()

    def _parse_cmp(self):
        left = self._parse_add()
        while self.peek().type == "EQ":
            op = self.eat().value
            right = self._parse_add()
            left = BinOp(op, left, right)
        return left

    def _parse_add(self):
        left = self._parse_mul()
        while self.peek().type in ("PLUS", "MINUS"):
            op = self.eat().value
            right = self._parse_mul()
            left = BinOp(op, left, right)
        return left

    def _parse_mul(self):
        left = self._parse_call()
        while self.peek().type in ("STAR", "SLASH"):
            op = self.eat().value
            right = self._parse_call()
            left = BinOp(op, left, right)
        return left

    def _parse_call(self):
        atom = self._parse_atom()
        while self.peek().type == "LPAREN":
            self.eat("LPAREN")
            args = []
            while self.peek().type != "RPAREN":
                args.append(self.parse_expr())
                if self.peek().type == "COMMA":
                    self.eat("COMMA")
            self.eat("RPAREN")
            atom = Call(atom, args)
        return atom

    def _parse_atom(self):
        tok = self.peek()
        if tok.type == "INT":
            self.eat()
            return IntLit(tok.value)
        if tok.type == "TRUE":
            self.eat()
            return BoolLit(True)
        if tok.type == "FALSE":
            self.eat()
            return BoolLit(False)
        if tok.type == "IDENT":
            self.eat()
            return Var(tok.value)
        if tok.type == "LPAREN":
            self.eat()
            expr = self.parse_expr()
            self.eat("RPAREN")
            return expr
        raise SyntaxError(f"line {tok.line}: unexpected token {tok.type}")


# ─── Layer 4: Environment + Interpreter ──────────────────────────────

@dataclass
class Environment:
    """Lexically-scoped bindings. Child envs chain to parent via `outer`."""
    bindings: dict[str, Any] = field(default_factory=dict)
    outer: "Environment | None" = None

    def get(self, name: str) -> Any:
        if name in self.bindings:
            return self.bindings[name]
        if self.outer is not None:
            return self.outer.get(name)
        raise NameError(f"undefined variable: {name}")

    def set(self, name: str, value: Any) -> None:
        self.bindings[name] = value


@dataclass
class Function:
    params: list[str]
    body: list
    closure: Environment


class ReturnSignal(Exception):
    """Control-flow exception — unwinds function stack to the Call site."""
    def __init__(self, value: Any):
        self.value = value


class Interpreter:
    """Walk the AST and evaluate it.

    This lives in `pkg/interpreter.py`, NOT inlined in cli.py. The
    `visit_<Type>` dispatcher is the one-add-at-a-time extension point.
    """

    def __init__(self):
        self.globals = Environment()

    def run(self, stmts: list) -> Any:
        result = None
        for s in stmts:
            result = self.visit(s, self.globals)
        return result

    def visit(self, node, env: Environment) -> Any:
        method = getattr(self, f"visit_{type(node).__name__}", None)
        if method is None:
            raise RuntimeError(f"no visitor for {type(node).__name__}")
        return method(node, env)

    def visit_IntLit(self, node, env): return node.value
    def visit_BoolLit(self, node, env): return node.value
    def visit_Var(self, node, env): return env.get(node.name)

    def visit_BinOp(self, node, env):
        l = self.visit(node.left, env)
        r = self.visit(node.right, env)
        ops = {
            "+": lambda a, b: a + b,
            "-": lambda a, b: a - b,
            "*": lambda a, b: a * b,
            "/": lambda a, b: a // b if isinstance(a, int) and isinstance(b, int) else a / b,
            "==": lambda a, b: a == b,
        }
        if node.op not in ops:
            raise RuntimeError(f"unknown op {node.op}")
        return ops[node.op](l, r)

    def visit_Let(self, node, env):
        env.set(node.name, self.visit(node.value, env))
        return None

    def visit_If(self, node, env):
        branch = node.then_branch if self.visit(node.cond, env) else node.else_branch
        result = None
        for s in branch:
            result = self.visit(s, env)
        return result

    def visit_FnDef(self, node, env):
        env.set(node.name, Function(node.params, node.body, env))
        return None

    def visit_Return(self, node, env):
        raise ReturnSignal(self.visit(node.value, env))

    def visit_Call(self, node, env):
        fn = self.visit(node.fn, env)
        args = [self.visit(a, env) for a in node.args]
        if not isinstance(fn, Function):
            raise RuntimeError(f"not callable: {fn!r}")
        if len(args) != len(fn.params):
            raise RuntimeError(
                f"arity mismatch: expected {len(fn.params)}, got {len(args)}"
            )
        # New env chains to CLOSURE, not caller (that's lexical scope)
        call_env = Environment(outer=fn.closure)
        for p, a in zip(fn.params, args):
            call_env.set(p, a)
        try:
            for s in fn.body:
                self.visit(s, call_env)
        except ReturnSignal as rs:
            return rs.value
        return None


# ─── Driver ──────────────────────────────────────────────────────────

def evaluate(source: str) -> Any:
    tokens = Lexer(source).tokenize()
    ast = Parser(tokens).parse_program()
    return Interpreter().run(ast)


if __name__ == "__main__":
    # Sanity checks — run: python3 tree_walking_interpreter.py
    assert evaluate("1 + 2") == 3
    assert evaluate("2 + 3 * 4") == 14, "precedence broken"
    assert evaluate("(2 + 3) * 4") == 20
    assert evaluate("let x = 5; x + 10") == 15
    assert evaluate("fn add(a, b) { return a + b; } add(2, 3)") == 5
    assert evaluate("fn fact(n) { if (n == 0) { return 1; } else { return n * fact(n - 1); } } fact(5)") == 120
    print("worked_examples/tree_walking_interpreter.py: 6 checks passed")
