"""The rows behind one cell of an aggregate result, as SQL.

A result like ``SELECT fname, COUNT(*) FILTER (WHERE x) AS n FROM visits WHERE w
GROUP BY 1`` says, for each row, how many visits met some conditions. The visits
behind one cell are those that meet all of them:

* the query's own ``WHERE`` (``w``);
* the row's group: each ``GROUP BY`` expression equal to that row's value of it
  (``IS NULL`` for a null group);
* the clicked column's own condition: the ``FILTER (WHERE x)`` of an aggregate,
  the ``WHEN`` of ``SUM(CASE WHEN c THEN 1 ...)``, or ``x IS NOT NULL`` for
  ``COUNT(x)``, ``SUM(x)``, ``AVG(x)``... A plain ``COUNT(*)`` adds nothing.

So the answer is ``SELECT * FROM <relation> WHERE <all of those>``. It is only
offered for a shape it is certainly right for: one SELECT over one relation, no
join, no subquery or CTE in FROM, no window, no DISTINCT. Anything else says why
not, rather than guessing. The SQL it returns is checked and scoped like any
other query when it runs -- this module only writes it.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

DIALECT = "postgres"

#: Aggregates whose cell is a count of the rows behind it -- so the drill's row
#: count can be checked against the cell.
COUNTING = "counting"


class NotDrillable(Exception):
    """This query (or this cell) has no rows-behind-it SQL this module is sure of."""


@dataclass(frozen=True)
class Shape:
    """A query parsed for drilling: what each result column is, and what it groups by."""

    statement: exp.Select
    relation: str
    #: For each result column: "group" (a GROUP BY value), "aggregate", or None (not drillable).
    kinds: list[str | None]
    #: The GROUP BY expressions, each with the result column that shows its value.
    group_columns: list[tuple[exp.Expression, int]]


def _parse(sql: str) -> exp.Select:
    try:
        statements = sqlglot.parse(sql, read=DIALECT)
    except (sqlglot.errors.ParseError, sqlglot.errors.TokenError) as e:
        raise NotDrillable("the query could not be parsed") from e
    statements = [s for s in statements if s is not None]
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise NotDrillable("only a single SELECT can be drilled into")
    return statements[0]


def _same(a: exp.Expression, b: exp.Expression) -> bool:
    return a.sql(dialect=DIALECT) == b.sql(dialect=DIALECT)


def _aggregate(expression: exp.Expression) -> exp.Expression | None:
    """The aggregate at the top of ``expression`` (under a FILTER), else None."""
    node = expression.this if isinstance(expression, exp.Filter) else expression
    return node if isinstance(node, exp.AggFunc) else None


def shape(sql: str) -> Shape:
    """Parse ``sql`` and decide which of its result columns can be drilled into."""
    statement = _parse(sql)
    if statement.args.get("with_") or statement.args.get("with"):
        raise NotDrillable("queries with a WITH clause cannot be drilled into yet")
    if statement.args.get("distinct"):
        raise NotDrillable("SELECT DISTINCT cannot be drilled into")
    if statement.args.get("joins"):
        raise NotDrillable("queries with a JOIN cannot be drilled into")
    source = statement.args.get("from_") or statement.args.get("from")
    table = source.this if source is not None else None
    if not isinstance(table, exp.Table) or not isinstance(table.this, exp.Identifier) or table.db:
        raise NotDrillable("only a query over one table (visits or a pipeline) can be drilled into")
    if any(statement.find_all(exp.Window)):
        raise NotDrillable("queries with window functions cannot be drilled into")
    projections = statement.expressions
    if any(isinstance(p, exp.Star) for p in projections):
        raise NotDrillable("SELECT * rows are already the rows")

    group = statement.args.get("group")
    group_exprs: list[exp.Expression] = []
    for g in group.expressions if group is not None else []:
        if isinstance(g, exp.Literal) and not g.is_string:
            position = int(g.this) - 1
            if not 0 <= position < len(projections):
                raise NotDrillable("GROUP BY refers to a column that is not there")
            group_exprs.append(projections[position].unalias())
        elif isinstance(g, exp.Column) and not g.table:
            named = [p for p in projections if isinstance(p, exp.Alias) and p.alias == g.name]
            group_exprs.append(named[0].unalias() if named else g)
        else:
            group_exprs.append(g)

    has_aggregate = any(_aggregate(p.unalias()) is not None or p.find(exp.AggFunc) for p in projections)
    if not group_exprs and not has_aggregate:
        raise NotDrillable("each row is already one row of the table")

    group_columns = []
    for g in group_exprs:
        shown = [i for i, p in enumerate(projections) if _same(p.unalias(), g)]
        if not shown:
            raise NotDrillable(
                f"the result does not show its group {g.sql(dialect=DIALECT)!r}, so a row cannot name it"
            )
        group_columns.append((g, shown[0]))

    kinds: list[str | None] = []
    grouped = {i for _g, i in group_columns}
    for i, p in enumerate(projections):
        if i in grouped:
            kinds.append("group")
        elif p.find(exp.AggFunc):
            kinds.append("aggregate")
        else:
            kinds.append(None)
    return Shape(statement=statement, relation=table.name, kinds=kinds, group_columns=group_columns)


def _literal(value) -> exp.Expression:
    if isinstance(value, bool):
        return exp.Boolean(this=value)
    if isinstance(value, int | float):
        return exp.Literal.number(value)
    return exp.Literal.string(str(value))


def _condition(expression: exp.Expression) -> str:
    """A condition joined to others by AND: bracketed only if it is an OR."""
    text = expression.sql(dialect=DIALECT)
    return f"({text})" if isinstance(expression, exp.Or) else text


def _operand(expression: exp.Expression) -> str:
    """An operand of = or IS: bracketed if it is itself a comparison or a connective."""
    text = expression.sql(dialect=DIALECT)
    return f"({text})" if isinstance(expression, exp.Connector | exp.Predicate | exp.Not) else text


def _not_null(expression: exp.Expression) -> str:
    return f"{_operand(expression)} IS NOT NULL"


def _equals(expression: exp.Expression, value) -> str:
    if value is None:
        return f"{_operand(expression)} IS NULL"
    return f"{_operand(expression)} = {_literal(value).sql(dialect=DIALECT)}"


def _cell_condition(expression: exp.Expression) -> tuple[list[str], str]:
    """The condition a row meets to count toward this aggregate, and what kind of cell it is.

    Returns ``(conditions, kind)``: ``COUNTING`` when the cell is a count of exactly
    the rows the conditions select, else ``"group"`` (the cell summarises the group's
    rows -- a sum, an average, a ratio -- and the drill shows those rows).
    """
    conditions: list[str] = []
    node = expression
    while isinstance(node, exp.Paren):
        node = node.this
    if isinstance(node, exp.Filter):
        where = node.expression
        conditions.append(_condition(where.this if isinstance(where, exp.Where) else where))
        node = node.this
    if not isinstance(node, exp.AggFunc):
        # Arithmetic over aggregates (a rate, a ratio): the group's rows are what it is about.
        return [], "group"
    argument = node.this
    distinct = isinstance(argument, exp.Distinct)
    if distinct:
        argument = argument.expressions[0] if argument.expressions else None
    kind = "group"
    if isinstance(node, exp.Count):
        if argument is None or isinstance(argument, exp.Star):
            kind = COUNTING
        else:
            conditions.append(_not_null(argument))
            kind = "group" if distinct else COUNTING
    elif isinstance(node, exp.Sum) and isinstance(argument, exp.Case) and _counts_one(argument):
        ifs = argument.args.get("ifs") or []
        conditions.append(_condition(ifs[0].this))
        kind = COUNTING
    elif argument is not None and not isinstance(argument, exp.Star):
        conditions.append(_not_null(argument))
    return conditions, kind


def _counts_one(case: exp.Case) -> bool:
    """``CASE WHEN c THEN 1 [ELSE 0] END``: one WHEN, counting 1, else 0 or nothing."""
    ifs = case.args.get("ifs") or []
    default = case.args.get("default")
    if len(ifs) != 1:
        return False
    then = ifs[0].args.get("true")
    one = isinstance(then, exp.Literal) and then.this == "1"
    zero = default is None or (isinstance(default, exp.Literal) and default.this == "0")
    return one and zero


@dataclass(frozen=True)
class Drill:
    sql: str
    #: COUNTING: the drill's row count should equal the cell.
    kind: str
    description: str


def drill(sql: str, row: list, column: int) -> Drill:
    """The SQL selecting the rows behind ``row[column]`` of ``sql``'s result."""
    parsed = shape(sql)
    if not 0 <= column < len(parsed.kinds) or len(row) != len(parsed.kinds):
        raise NotDrillable("that cell is not in this result")
    if parsed.kinds[column] is None:
        raise NotDrillable("that column is neither a group nor an aggregate")

    conditions: list[str] = []
    where = parsed.statement.args.get("where")
    if where is not None:
        conditions.append(_condition(where.this))
    for g, i in parsed.group_columns:
        conditions.append(_equals(g, row[i]))
    kind, described = "group", "the rows of this group"
    if parsed.kinds[column] == "aggregate":
        cell, kind = _cell_condition(parsed.statement.expressions[column].unalias())
        conditions += cell
        described = "the rows this cell counts" if kind == COUNTING else "the rows this cell summarises"

    text = f"SELECT *\nFROM {parsed.relation}"
    if conditions:
        text += "\nWHERE " + "\n  AND ".join(conditions)
    return Drill(sql=text, kind=kind, description=described)
