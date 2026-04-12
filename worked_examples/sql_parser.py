"""Worked example: SQL tokenizer + recursive-descent parser (stdlib only).

This is a REFERENCE implementation showing the correct patterns. Copy the
STRUCTURE (not necessarily the exact code) to solve PRDs that require
parsing a SQL-like language.

Key patterns demonstrated:
  1. Tokenizer produces a list of (type, value, pos) tuples
  2. Parser keeps a `pos` cursor and a `peek()` method
  3. `consume(expected_type)` advances the cursor and validates
  4. CRITICAL: after `FROM <table>`, check if next identifier is a SQL
     KEYWORD before treating it as a table alias. Otherwise `WHERE` gets
     eaten as an alias. This bug has bitten Gemma 4 on every mini_db build.

Supported syntax (minimal):
  SELECT * FROM users
  SELECT col1, col2 FROM t WHERE age > 18 ORDER BY name LIMIT 10
  INSERT INTO users VALUES (1, 'alice', 30)
  UPDATE users SET age = 31 WHERE id = 1
  DELETE FROM users WHERE id = 2
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ── SQL keyword set — used to avoid "WHERE eaten as alias" bug ──
SQL_KEYWORDS = frozenset({
    "SELECT", "FROM", "WHERE", "INSERT", "INTO", "VALUES",
    "UPDATE", "SET", "DELETE", "CREATE", "TABLE", "DROP",
    "ORDER", "BY", "LIMIT", "OFFSET", "GROUP", "HAVING",
    "JOIN", "INNER", "LEFT", "RIGHT", "OUTER", "ON", "AS",
    "AND", "OR", "NOT", "NULL", "TRUE", "FALSE", "LIKE", "IN",
    "ASC", "DESC", "IF", "EXISTS",
})


# ── Token types ──
@dataclass
class Token:
    type: str  # "KEYWORD" | "IDENT" | "NUMBER" | "STRING" | "PUNCT" | "OP"
    value: str
    pos: int


def tokenize(sql: str) -> list[Token]:
    """Break SQL text into tokens. Handles quoted strings, identifiers,
    numbers, punctuation, operators. Case-folds keywords to upper."""
    tokens = []
    i = 0
    n = len(sql)
    while i < n:
        c = sql[i]
        # Whitespace
        if c.isspace():
            i += 1
            continue
        # Single-quoted string literal
        if c == "'":
            j = i + 1
            while j < n and sql[j] != "'":
                if sql[j] == "\\" and j + 1 < n:
                    j += 2
                else:
                    j += 1
            tokens.append(Token("STRING", sql[i+1:j], i))
            i = j + 1
            continue
        # Numeric literal
        if c.isdigit() or (c == "." and i + 1 < n and sql[i+1].isdigit()):
            j = i
            has_dot = False
            while j < n and (sql[j].isdigit() or (sql[j] == "." and not has_dot)):
                if sql[j] == ".":
                    has_dot = True
                j += 1
            tokens.append(Token("NUMBER", sql[i:j], i))
            i = j
            continue
        # Identifier or keyword
        if c.isalpha() or c == "_":
            j = i
            while j < n and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            word = sql[i:j]
            upper = word.upper()
            if upper in SQL_KEYWORDS:
                tokens.append(Token("KEYWORD", upper, i))
            else:
                tokens.append(Token("IDENT", word, i))
            i = j
            continue
        # Two-char operators
        if i + 1 < n:
            two = sql[i:i+2]
            if two in (">=", "<=", "!=", "<>"):
                tokens.append(Token("OP", two, i))
                i += 2
                continue
        # Single-char operators / punctuation
        if c in "=<>":
            tokens.append(Token("OP", c, i))
            i += 1
            continue
        if c in ",();*":
            tokens.append(Token("PUNCT", c, i))
            i += 1
            continue
        raise ValueError(f"Unexpected character {c!r} at position {i}")
    return tokens


# ── AST nodes ──
@dataclass
class SelectStmt:
    columns: list[str]   # ["*"] or list of column names
    table: str
    table_alias: str | None
    where: "Expr | None"
    order_by: tuple[str, str] | None  # (col, "ASC" | "DESC")
    limit: int | None


@dataclass
class InsertStmt:
    table: str
    values: list  # list of literal values (str, int, float, None)


@dataclass
class UpdateStmt:
    table: str
    assignments: list[tuple[str, "Expr"]]
    where: "Expr | None"


@dataclass
class DeleteStmt:
    table: str
    where: "Expr | None"


@dataclass
class Expr:
    """Either a BinOp, Ident, or Literal wrapped up."""
    kind: str  # "binop" | "ident" | "literal"
    # For binop:
    op: str | None = None
    left: "Expr | None" = None
    right: "Expr | None" = None
    # For ident / literal:
    value: str | int | float | None = None


# ── Parser ──
class Parser:
    def __init__(self, sql: str):
        self.tokens = tokenize(sql)
        self.pos = 0

    def peek(self, offset: int = 0) -> Token | None:
        idx = self.pos + offset
        return self.tokens[idx] if idx < len(self.tokens) else None

    def consume(self, expected_type: str | None = None,
                expected_value: str | None = None) -> Token:
        tok = self.peek()
        if tok is None:
            raise ValueError(f"Unexpected end of input; expected {expected_type}")
        if expected_type and tok.type != expected_type:
            raise ValueError(
                f"Expected {expected_type}, got {tok.type} ({tok.value!r}) at pos {tok.pos}"
            )
        if expected_value and tok.value != expected_value:
            raise ValueError(
                f"Expected {expected_value!r}, got {tok.value!r} at pos {tok.pos}"
            )
        self.pos += 1
        return tok

    def parse(self):
        """Dispatch on first keyword."""
        first = self.peek()
        if first is None:
            raise ValueError("Empty query")
        if first.type != "KEYWORD":
            raise ValueError(f"Expected SQL keyword, got {first.value!r}")
        if first.value == "SELECT":
            return self._parse_select()
        if first.value == "INSERT":
            return self._parse_insert()
        if first.value == "UPDATE":
            return self._parse_update()
        if first.value == "DELETE":
            return self._parse_delete()
        raise ValueError(f"Unsupported statement: {first.value}")

    def _parse_select(self) -> SelectStmt:
        self.consume("KEYWORD", "SELECT")

        # Columns: '*' or identifier list
        columns: list[str] = []
        if self.peek() and self.peek().value == "*":
            self.consume("PUNCT", "*")
            columns = ["*"]
        else:
            columns.append(self.consume("IDENT").value)
            while self.peek() and self.peek().value == ",":
                self.consume("PUNCT", ",")
                columns.append(self.consume("IDENT").value)

        self.consume("KEYWORD", "FROM")
        table = self.consume("IDENT").value

        # ── CRITICAL: alias detection ──
        # After FROM <table>, the next IDENT could be an alias OR the
        # start of a clause (WHERE / ORDER / LIMIT / JOIN / ...). Check
        # if the next token is a KEYWORD before consuming as alias.
        # If this check is missing, `WHERE` gets eaten as alias and
        # the SELECT parses with no WHERE clause.
        table_alias = None
        nxt = self.peek()
        if nxt and nxt.type == "IDENT":
            table_alias = self.consume("IDENT").value
        # Note: a KEYWORD after FROM is NOT consumed here — it's the start
        # of the next clause.

        # Optional WHERE
        where = None
        if self.peek() and self.peek().type == "KEYWORD" and self.peek().value == "WHERE":
            self.consume("KEYWORD", "WHERE")
            where = self._parse_expr()

        # Optional ORDER BY
        order_by = None
        if self.peek() and self.peek().value == "ORDER":
            self.consume("KEYWORD", "ORDER")
            self.consume("KEYWORD", "BY")
            col = self.consume("IDENT").value
            direction = "ASC"
            if self.peek() and self.peek().value in ("ASC", "DESC"):
                direction = self.consume("KEYWORD").value
            order_by = (col, direction)

        # Optional LIMIT
        limit = None
        if self.peek() and self.peek().value == "LIMIT":
            self.consume("KEYWORD", "LIMIT")
            limit = int(self.consume("NUMBER").value)

        return SelectStmt(
            columns=columns,
            table=table,
            table_alias=table_alias,
            where=where,
            order_by=order_by,
            limit=limit,
        )

    def _parse_insert(self) -> InsertStmt:
        self.consume("KEYWORD", "INSERT")
        self.consume("KEYWORD", "INTO")
        table = self.consume("IDENT").value
        self.consume("KEYWORD", "VALUES")
        self.consume("PUNCT", "(")
        values = [self._parse_literal()]
        while self.peek() and self.peek().value == ",":
            self.consume("PUNCT", ",")
            values.append(self._parse_literal())
        self.consume("PUNCT", ")")
        return InsertStmt(table=table, values=values)

    def _parse_update(self) -> UpdateStmt:
        self.consume("KEYWORD", "UPDATE")
        table = self.consume("IDENT").value
        self.consume("KEYWORD", "SET")
        assignments = [self._parse_assignment()]
        while self.peek() and self.peek().value == ",":
            self.consume("PUNCT", ",")
            assignments.append(self._parse_assignment())
        where = None
        if self.peek() and self.peek().value == "WHERE":
            self.consume("KEYWORD", "WHERE")
            where = self._parse_expr()
        return UpdateStmt(table=table, assignments=assignments, where=where)

    def _parse_delete(self) -> DeleteStmt:
        self.consume("KEYWORD", "DELETE")
        self.consume("KEYWORD", "FROM")
        table = self.consume("IDENT").value
        where = None
        if self.peek() and self.peek().value == "WHERE":
            self.consume("KEYWORD", "WHERE")
            where = self._parse_expr()
        return DeleteStmt(table=table, where=where)

    def _parse_assignment(self) -> tuple[str, Expr]:
        """Parse `col = <value>` — where <value> is a single literal/ident,
        NOT a full comparison expression. (In SET clauses there's no op.)"""
        col = self.consume("IDENT").value
        self.consume("OP", "=")
        return col, self._parse_literal_or_ident()

    def _parse_expr(self) -> Expr:
        """Single comparison: <ident> <op> <literal>."""
        left_tok = self.consume()
        if left_tok.type == "IDENT":
            left = Expr(kind="ident", value=left_tok.value)
        else:
            left = self._literal_from_token(left_tok)
        op_tok = self.consume("OP")
        right = self._parse_literal_or_ident()
        return Expr(kind="binop", op=op_tok.value, left=left, right=right)

    def _parse_literal(self):
        tok = self.consume()
        return self._literal_value(tok)

    def _parse_literal_or_ident(self) -> Expr:
        tok = self.consume()
        if tok.type == "IDENT":
            return Expr(kind="ident", value=tok.value)
        return self._literal_from_token(tok)

    def _literal_from_token(self, tok: Token) -> Expr:
        return Expr(kind="literal", value=self._literal_value(tok))

    @staticmethod
    def _literal_value(tok: Token):
        if tok.type == "STRING":
            return tok.value
        if tok.type == "NUMBER":
            return float(tok.value) if "." in tok.value else int(tok.value)
        if tok.type == "KEYWORD":
            if tok.value == "NULL":
                return None
            if tok.value == "TRUE":
                return True
            if tok.value == "FALSE":
                return False
        raise ValueError(f"Expected literal, got {tok.type}({tok.value!r})")


# ── Demo ──
if __name__ == "__main__":
    import json
    samples = [
        "SELECT * FROM users",
        "SELECT name, age FROM users WHERE age > 18 ORDER BY name LIMIT 10",
        "INSERT INTO users VALUES (1, 'alice', 30)",
        "UPDATE users SET age = 31 WHERE id = 1",
        "DELETE FROM users WHERE id = 2",
    ]
    for sql in samples:
        print(f"=== {sql} ===")
        stmt = Parser(sql).parse()
        print(stmt)
        print()
