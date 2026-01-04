import re

IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
INT_LITERAL = r"-?\d+"
HEX_LITERAL = r"0x[0-9A-Fa-f]+"

SIMPLE_RHS = rf"(?:{IDENT}|{INT_LITERAL}|{HEX_LITERAL})"


FUNCTION_SIGNATURES = {
    "ByteRead": ["address"],
}

# ===============================
# Helpers
# ===============================

def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//.*", "", text)
    return text


def mask_to_bits(mask: int):
    bits = [i for i in range(mask.bit_length()) if mask & (1 << i)]
    if bits and bits == list(range(bits[0], bits[-1] + 1)):
        return f"<{bits[0]}..{bits[-1]}>"
    return ", ".join(f"bit{i}" for i in bits)


def simplify_lhs(lhs: str) -> str:
    lhs = lhs.strip()
    while lhs.startswith("(") and lhs.endswith(")"):
        lhs = lhs[1:-1].strip()

    m = re.fullmatch(rf"\*\s*\(\s*&\s*({IDENT})\s*\)", lhs)
    if m:
        return m.group(1)

    return lhs


def describe_lhs(lhs: str) -> str:
    lhs = simplify_lhs(lhs)
    if lhs.startswith("*"):
        return f"value at address {lhs[1:].strip()}"
    return lhs


def format_argument(arg: str) -> str:
    arg = arg.strip()
    if re.fullmatch(IDENT, arg):
        return arg
    return f"({arg})"


def parenthesize_expr(expr: str) -> str:
    expr = expr.strip()

    # Simple identifier or literal → no parentheses
    if re.fullmatch(SIMPLE_RHS, expr):
        return expr

    # No operator → no parentheses
    if not re.search(r"[+\-*/%|&^<>]", expr):
        return expr

    return f"({expr})"



# ===============================
# Function calls
# ===============================

def parse_function_call(lhs, func, args):
    params = FUNCTION_SIGNATURES.get(func)
    if not params:
        return None

    bindings = []
    for p, a in zip(params, args):
        bindings.append(f"{p} <- {format_argument(a)}")

    if lhs:
        bindings.append(f"RETURN_VALUE -> {lhs}")

    return f"PERFORM {func}({', '.join(bindings)})"


# ===============================
# Compound assignments (++ += etc)
# ===============================

def parse_compound_assignment(line: str):

    m = re.fullmatch(rf"({IDENT})\s*\+\+", line)
    if m:
        v = m.group(1)
        return f"SET {v} to {parenthesize_expr(f'{v} + 1')}"

    m = re.fullmatch(rf"({IDENT})\s*--", line)
    if m:
        v = m.group(1)
        return f"SET {v} to {parenthesize_expr(f'{v} - 1')}"

    # a += b, a -= b, a *= b, a /= b
    m = re.fullmatch(rf"({IDENT})\s*([\+\-\*/])=\s*(.+)", line)
    if m:
        v, op, rhs = m.groups()
        return f"SET {v} to {parenthesize_expr(f'{v} {op} {rhs.strip()}')}"

    return None


# ===============================
# Assignments
# ===============================

def parse_assignment(line: str):
    if "=" not in line:
        return None

    lhs, rhs = map(str.strip, line.split("=", 1))
    lhs_desc = describe_lhs(lhs)

    m = re.fullmatch(rf"({IDENT})\((.*)\)", rhs)
    if m:
        func, argstr = m.groups()
        args = [a.strip() for a in argstr.split(",") if a.strip()]
        return parse_function_call(simplify_lhs(lhs), func, args)

    if re.fullmatch(IDENT, rhs):
        return f"SET {lhs_desc} to {parenthesize_expr(rhs)}"

    m = re.fullmatch(rf"({IDENT})\s*&\s*(0x[0-9A-Fa-f]+)", rhs)
    if m:
        var, mask_hex = m.groups()
        return f"SET {lhs_desc} to {mask_to_bits(int(mask_hex,16))} bits of {var}"

    m = re.fullmatch(
        rf"\(?({IDENT})\s*>>\s*(\d+)\)?\s*&\s*(0x[0-9A-Fa-f]+)",
        rhs
    )
    if m:
        var, shift, mask_hex = m.groups()
        shift = int(shift)
        bits = mask_to_bits(int(mask_hex, 16))
        start, end = map(int, bits[1:-1].split(".."))
        return f"SET {lhs_desc} to <{start+shift}..{end+shift}> bits of {var}"

    return f"SET {lhs_desc} to {rhs}"


# ===============================
# Statements
# ===============================

def parse_statement(line: str):
    line = line.rstrip(";")

    compound = parse_compound_assignment(line)
    if compound:
        return compound

    m = re.fullmatch(rf"({IDENT})\s*=\s*({IDENT})\((.*)\)", line)
    if m:
        ret, func, argstr = m.groups()
        args = [a.strip() for a in argstr.split(",") if a.strip()]
        return parse_function_call(ret, func, args)

    m = re.fullmatch(rf"({IDENT})\((.*)\)", line)
    if m:
        func, argstr = m.groups()
        args = [a.strip() for a in argstr.split(",") if a.strip()]
        return parse_function_call(None, func, args)

    return parse_assignment(line)


# ===============================
# Condition parsing
# ===============================

def normalize_condition(cond: str) -> str:
    return cond.replace("=>", ">=").strip()


def split_outside_parens(expr, operator):
    parts = []
    depth = 0
    start = 0
    i = 0

    while i < len(expr):
        if expr[i] == "(":
            depth += 1
        elif expr[i] == ")":
            depth -= 1
        elif depth == 0 and expr.startswith(operator, i):
            parts.append(expr[start:i].strip())
            start = i + len(operator)
            i += len(operator) - 1
        i += 1

    parts.append(expr[start:].strip())
    return parts


def describe_comparison(expr: str) -> str:
    expr = normalize_condition(expr)

    patterns = [
        (r"(.+?)\s*==\s*(.+)", "is equal to"),
        (r"(.+?)\s*!=\s*(.+)", "is not equal to"),
        (r"(.+?)\s*>=\s*(.+)", "is greater than or equal to"),
        (r"(.+?)\s*<=\s*(.+)", "is less than or equal to"),
        (r"(.+?)\s*>\s*(.+)", "is greater than"),
        (r"(.+?)\s*<\s*(.+)", "is less than"),
    ]

    for pat, text in patterns:
        m = re.fullmatch(pat, expr)
        if m:
            a, b = m.groups()
            return f"{a.strip()} {text} {b.strip()}"

    return expr


def parse_condition(cond: str):
    cond = normalize_condition(cond)

    or_parts = split_outside_parens(cond, "||")
    if len(or_parts) > 1:
        return ("ANY", [parse_condition(p) for p in or_parts])

    and_parts = split_outside_parens(cond, "&&")
    if len(and_parts) > 1:
        return ("ALL", [parse_condition(p) for p in and_parts])

    return describe_comparison(cond)


# ===============================
# Label helpers
# ===============================

def to_roman(n):
    vals = [
        (1000,"M"),(900,"CM"),(500,"D"),(400,"CD"),
        (100,"C"),(90,"XC"),(50,"L"),(40,"XL"),
        (10,"X"),(9,"IX"),(5,"V"),(4,"IV"),(1,"I")
    ]
    res = ""
    for v,s in vals:
        while n >= v:
            res += s
            n -= v
    return res


def label_for_index(i, depth):
    if depth == 0:
        return chr(65 + i) + "."
    if depth == 1:
        return chr(97 + i) + "."
    if depth == 2:
        return to_roman(i+1).lower() + "."
    return str(i+1) + "."


# ===============================
# IF blocks
# ===============================

def flatten_conditions(tree):
    if isinstance(tree, str):
        return "ALL", [tree]

    kind, parts = tree
    flat = []
    for p in parts:
        _, sub = flatten_conditions(p)
        flat.extend(sub)

    return kind, flat


def parse_if_block(lines, i, depth):
    m = re.fullmatch(r"if\s*\((.*)\)\s*\{?", lines[i])
    condition = m.group(1)

    output = []

    kind, conds = flatten_conditions(parse_condition(condition))

    output.append((depth, f"IF {kind} of the following conditions are satisfied:", True))
    for c in conds:
        output.append((depth + 1, c, True))

    output.append((depth, "Following operations are performed:", False))

    i += 1
    while i < len(lines) and "}" not in lines[i]:
        line = lines[i]

        if line.startswith("if"):
            nested, i = parse_if_block(lines, i, depth + 1)
            output.extend(nested)
            continue

        stmt = parse_statement(line)
        if stmt:
            output.append((depth + 1, stmt, True))

        i += 1

    return output, i


# ===============================
# Document parsing
# ===============================

def parse_document(text: str):
    text = strip_comments(text)
    raw_lines = [l.strip() for l in text.splitlines() if l.strip()]

    entries = []
    i = 0

    while i < len(raw_lines):
        line = raw_lines[i]

        if line.startswith("if"):
            block, i = parse_if_block(raw_lines, i, 0)
            entries.extend(block)
            i += 1
            continue

        stmt = parse_statement(line)
        if stmt:
            entries.append((0, stmt, True))

        i += 1

    counters = {}
    result = []

    for depth, text, numbered in entries:
        indent = "    " * depth

        if not numbered:
            result.append(f"{indent}{text}")
            counters[depth + 1] = 0
            continue

        counters.setdefault(depth, 0)
        label = label_for_index(counters[depth], depth)
        counters[depth] += 1

        for d in list(counters):
            if d > depth:
                counters[d] = 0

        if text.endswith(":"):
            result.append(f"{indent}{label} {text}")
        else:
            result.append(f"{indent}{label} {text}.")

    return result


# ===============================
# Example
# ===============================

if __name__ == "__main__":
    code = """
    a++;
    b -= 3;
    c *= d;
    x = y & 0x0F;

    if (ready == 1 && error == 0) {
        retry++;
        temp = ByteRead(pcan + 1000);
    }
    a + 0x10;
    value = data & 0x0F;
    value2 = (data >> 4) & 0x03;
    *result = ByteRead(address);
    x = ByteRead(address);
    """

    for line in parse_document(code):
        print(line)
