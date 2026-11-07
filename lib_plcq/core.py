"""Pure logic for Prompt Line Combo Queue (no webui imports, unit-testable).

Expression syntax (one queue item per line; spaces are ignored):

    3          line 3 of the Prompt field
    n2         line 2 of the Negative prompt field
    a+b        use both, in the written order
    (3-6)      range: same as 3+4+5+6   (n1-n3 for negative lines)
    (3,4,5)    alternatives: one image per alternative, in order
    {3|4|5}    random: one of them is picked for every image
    :N         image count (optional, at the very end)

Groups nest: 1+((3-5),7)+{n1|n2}. An alternative or random option can be a
whole sequence: (2+3,4+(5-6)).

Image count: omitted -> every combination once. :N -> N images, cycling
through the combinations from the first one.

Queue line format (whitespace separated, as saved to .txt):

    <expression>[:N]  <size>  <seed>  [off]

    <size> is 832x1216, or {832x1216|1216x832|...} to pick one at random for
    every image.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

SIZE_RE = re.compile(r"^(\d+)\s*[x×*]\s*(\d+)$", re.IGNORECASE)
SEED_RE = re.compile(r"^-?\d+$")
COUNT_RE = re.compile(r"^(.*?):(\d+)$")
TOKEN_RE = re.compile(r"n?\d+|[-+,|(){}]")

MIN_SIZE = 64
MAX_SIZE = 8192
MAX_SEED = 2**32 - 1
MAX_COMBOS = 10000
MAX_RANGE = 500
OFF = "off"


# ---------------------------------------------------------------- errors

class ComboError(ValueError):
    """Error with an English template + params so the UI can translate it."""

    def __init__(self, template: str, **params):
        self.template = template
        self.params = params
        super().__init__(self.render())

    def render(self, translate=lambda s: s) -> str:
        params = {k: (v.render(translate) if isinstance(v, ComboError) else v) for k, v in self.params.items()}
        return translate(self.template).format(**params)


class ComboErrors(ValueError):
    def __init__(self, errors: list[ComboError]):
        self.errors = errors
        super().__init__("\n".join(e.render() for e in errors))

    def render(self, translate=lambda s: s) -> str:
        return "\n".join(e.render(translate) for e in self.errors)


# ---------------------------------------------------------------- AST

@dataclass(frozen=True)
class Line:
    neg: bool
    idx: int

    def label(self) -> str:
        return f"n{self.idx}" if self.neg else str(self.idx)


class Node:
    combos: int = 1

    def resolve(self, k: int, rng: random.Random) -> list[Line]:
        raise NotImplementedError

    def lines(self) -> set[Line]:
        raise NotImplementedError


class LineNode(Node):
    def __init__(self, line: Line):
        self.line = line

    def resolve(self, k, rng):
        return [self.line]

    def lines(self):
        return {self.line}


class RangeNode(Node):
    def __init__(self, a: Line, b: Line):
        step = 1 if b.idx >= a.idx else -1
        self.items = [Line(a.neg, i) for i in range(a.idx, b.idx + step, step)]

    def resolve(self, k, rng):
        return list(self.items)

    def lines(self):
        return set(self.items)


class SeqNode(Node):
    def __init__(self, items: list[Node]):
        self.items = items
        c = 1
        for it in items:
            c *= it.combos
            c = min(c, MAX_COMBOS + 1)
        self.combos = c

    def resolve(self, k, rng):
        # mixed radix, leftmost group changes slowest
        digits = []
        for it in reversed(self.items):
            digits.append(k % it.combos)
            k //= it.combos
        out: list[Line] = []
        for it, d in zip(self.items, reversed(digits)):
            out.extend(it.resolve(d, rng))
        return out

    def lines(self):
        s: set[Line] = set()
        for it in self.items:
            s |= it.lines()
        return s


class AltNode(Node):
    """(a,b,c): enumerated alternatives."""

    def __init__(self, options: list[SeqNode]):
        self.options = options
        self.combos = min(sum(o.combos for o in options), MAX_COMBOS + 1)

    def resolve(self, k, rng):
        for o in self.options:
            if k < o.combos:
                return o.resolve(k, rng)
            k -= o.combos
        return self.options[-1].resolve(0, rng)

    def lines(self):
        s: set[Line] = set()
        for o in self.options:
            s |= o.lines()
        return s


class RandNode(Node):
    """{a|b|c}: one option at random for every image (anything inside is random too)."""

    combos = 1

    def __init__(self, options: list[SeqNode]):
        self.options = options

    def resolve(self, k, rng):
        o = rng.choice(self.options)
        return o.resolve(rng.randrange(min(o.combos, MAX_COMBOS)), rng)

    def lines(self):
        s: set[Line] = set()
        for o in self.options:
            s |= o.lines()
        return s


# ---------------------------------------------------------------- parser

class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.toks: list[str] = []
        pos = 0
        for m in TOKEN_RE.finditer(text):
            if m.start() != pos:
                raise ComboError("Unexpected character “{ch}” in “{expr}”", ch=text[pos], expr=text)
            self.toks.append(m.group())
            pos = m.end()
        if pos != len(text):
            raise ComboError("Unexpected character “{ch}” in “{expr}”", ch=text[pos], expr=text)
        self.i = 0
        self.has_random = False

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self, expected):
        t = self.peek()
        if t is None:
            raise ComboError("Expected “{want}” but the expression ended (“{expr}”)", want=expected, expr=self.text)
        if t != expected:
            raise ComboError("Expected “{want}” but found “{got}” (“{expr}”)", want=expected, got=t, expr=self.text)
        self.i += 1
        return t

    def parse(self) -> SeqNode:
        if not self.toks:
            raise ComboError("The expression is empty")
        node = self.seq(())
        if self.peek() is not None:
            raise ComboError("Unexpected “{tok}” in “{expr}”", tok=self.peek(), expr=self.text)
        return node

    def seq(self, stops) -> SeqNode:
        items = [self.term()]
        while self.peek() == "+":
            self.take("+")
            items.append(self.term())
        if self.peek() not in (None, *stops):
            raise ComboError("Unexpected “{tok}” in “{expr}”", tok=self.peek(), expr=self.text)
        return SeqNode(items)

    def line(self, neg_default: bool = False) -> Line:
        t = self.peek()
        if t is None:
            raise ComboError("Expected a line number but the expression ended (“{expr}”)", expr=self.text)
        if not re.fullmatch(r"n?\d+", t):
            raise ComboError("Expected a line number but found “{got}” (“{expr}”)", got=t, expr=self.text)
        self.i += 1
        if neg_default and not t.startswith("n"):
            t = "n" + t  # n1-3 == n1-n3
        idx = int(t.lstrip("n"))
        if idx < 1:
            raise ComboError("Line numbers start at 1 (“{tok}” in “{expr}”)", tok=t, expr=self.text)
        return Line(t.startswith("n"), idx)

    def term(self) -> Node:
        t = self.peek()
        if t == "(":
            self.take("(")
            opts = [self.seq((",", ")"))]
            while self.peek() == ",":
                self.take(",")
                opts.append(self.seq((",", ")")))
            self.take(")")
            return opts[0] if len(opts) == 1 else AltNode(opts)
        if t == "{":
            self.take("{")
            self.has_random = True
            opts = [self.seq(("|", "}"))]
            while self.peek() == "|":
                self.take("|")
                opts.append(self.seq(("|", "}")))
            self.take("}")
            return RandNode(opts)
        a = self.line()
        if self.peek() == "-":
            self.take("-")
            b = self.line(neg_default=a.neg)
            if a.neg != b.neg:
                raise ComboError("A range cannot mix prompt and negative lines (“{a}-{b}” in “{expr}”)", a=a.label(), b=b.label(), expr=self.text)
            if abs(b.idx - a.idx) + 1 > MAX_RANGE:
                raise ComboError("Range “{a}-{b}” is too long (max {max} lines)", a=a.label(), b=b.label(), max=MAX_RANGE)
            return RangeNode(a, b)
        return LineNode(a)


# ---------------------------------------------------------------- combo

@dataclass
class Combo:
    expr: str  # normalized expression without count
    count: int | None  # None -> every combination once
    root: SeqNode
    has_random: bool

    @property
    def combos(self) -> int:
        return self.root.combos

    @property
    def images(self) -> int:
        return self.count if self.count is not None else self.combos

    def text(self) -> str:
        return self.expr if self.count is None else f"{self.expr}:{self.count}"

    def lines(self) -> set[Line]:
        return self.root.lines()

    def resolve(self, i: int, rng: random.Random) -> list[Line]:
        return self.root.resolve(i % self.combos, rng)


def parse_combo(expr: str) -> Combo:
    s = re.sub(r"\s+", "", expr or "")
    count = None
    m = COUNT_RE.match(s)
    if m:
        s, count = m.group(1), int(m.group(2))
        if count < 1:
            raise ComboError("The image count must be 1 or more (“{expr}”)", expr=expr.strip())
    if ":" in s:
        raise ComboError("“:” must be followed by the image count at the end (“{expr}”)", expr=expr.strip())
    p = _Parser(s)
    root = p.parse()
    if root.combos > MAX_COMBOS:
        raise ComboError("Too many combinations in “{expr}” (max {max})", expr=s, max=MAX_COMBOS)
    return Combo(s, count, root, p.has_random)


def check_ranges(combo: Combo, n_pos: int, n_neg: int) -> None:
    bad = sorted(
        (l for l in combo.lines() if l.idx > (n_neg if l.neg else n_pos)),
        key=lambda l: (l.neg, l.idx),
    )
    if bad:
        raise ComboError(
            "Lines that do not exist: {lines} (“{expr}” / Prompt has {np} lines, Negative has {nn})",
            lines=", ".join(l.label() for l in bad), expr=combo.text(), np=n_pos, nn=n_neg,
        )


# ---------------------------------------------------------------- queue items

@dataclass
class Job:
    combo: Combo
    sizes: list[tuple[int, int]]  # one entry, or several to pick from at random
    seed: int
    enabled: bool = True

    def to_line(self) -> str:
        s = f"{self.combo.text()}  {format_size_token(self.sizes)}  {self.seed}"
        return s if self.enabled else f"{s}  {OFF}"


def _norm(text: str | None) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def split_lines(text: str | None) -> list[str]:
    """Lines exactly as a text editor numbers them (line 1 = first line)."""
    return _norm(text).split("\n")


def queue_lines(text: str | None) -> list[str]:
    return [l.strip() for l in split_lines(text) if l.strip() and not l.strip().startswith("#")]


def parse_size_token(text: str) -> list[tuple[int, int]]:
    """'832x1216' -> [(832, 1216)];  '{832x1216|1216x832}' -> both (random per image)."""
    s = re.sub(r"\s+", "", text or "")
    if s.startswith("{") and s.endswith("}"):
        parts = [x for x in s[1:-1].split("|")]
        if not parts or any(not x for x in parts):
            raise ComboError("Invalid size “{size}” (e.g. 832x1216)", size=text)
        return [parse_size(x) for x in parts]
    return [parse_size(s)]


def format_size_token(sizes: list[tuple[int, int]]) -> str:
    items = [f"{w}x{h}" for w, h in sizes]
    return items[0] if len(items) == 1 else "{" + "|".join(items) + "}"


def parse_size(text: str) -> tuple[int, int]:
    m = SIZE_RE.match((text or "").strip())
    if not m:
        raise ComboError("Invalid size “{size}” (e.g. 832x1216)", size=text)
    w, h = int(m.group(1)), int(m.group(2))
    if not all(MIN_SIZE <= v <= MAX_SIZE for v in (w, h)):
        raise ComboError("Size must be between {lo} and {hi}: “{size}”", lo=MIN_SIZE, hi=MAX_SIZE, size=text)
    return w, h


def parse_seed(text) -> int:
    s = str(text if text is not None else "").strip()
    if s == "":
        return -1
    if not SEED_RE.match(s) or not (-1 <= int(s) <= MAX_SEED):
        raise ComboError("Seed must be -1 or a whole number from 0 to {max}: “{seed}”", max=MAX_SEED, seed=s)
    return int(s)


def make_jobs(combo_text: str, size: str, seed, prompt: str | None = None, negative: str | None = None) -> list[Job]:
    """"Add to queue" input -> jobs. Raises ComboErrors listing every bad line."""
    errors: list[ComboError] = []
    try:
        sizes = parse_size_token(size)
        sd = parse_seed(seed)
    except ComboError as e:
        raise ComboErrors([e])
    jobs: list[Job] = []
    for raw in queue_lines(combo_text):
        try:
            c = parse_combo(raw)
            if prompt is not None:
                check_ranges(c, len(split_lines(prompt)), len(split_lines(negative)))
            jobs.append(Job(c, list(sizes), sd))
        except ComboError as e:
            errors.append(e)
    if errors:
        raise ComboErrors(errors)
    if not jobs:
        raise ComboErrors([ComboError("Write at least one line combination (e.g. 1+2+(3,4)+n1)")])
    return jobs


def parse_queue_line(line: str) -> Job:
    parts = line.split()
    if len(parts) < 2:
        raise ComboError("Missing image size in “{line}”", line=line)
    enabled = True
    if len(parts) >= 4:
        if parts[3].lower() not in (OFF, "on"):
            raise ComboError("Unexpected “{tok}” in “{line}”", tok=parts[3], line=line)
        enabled = parts[3].lower() != OFF
    if len(parts) > 4:
        raise ComboError("Unexpected “{tok}” in “{line}”", tok=parts[4], line=line)
    c = parse_combo(parts[0])
    sizes = parse_size_token(parts[1])
    sd = parse_seed(parts[2]) if len(parts) > 2 else -1
    return Job(c, sizes, sd, enabled)


def queue_meta(text: str | None, prompt: str | None = None, negative: str | None = None, translate=lambda s: s) -> list[dict]:
    """Per queue line: images, combos, random?, enabled, error (translated)."""
    out = []
    n_pos = len(split_lines(prompt)) if prompt is not None else None
    n_neg = len(split_lines(negative)) if negative is not None else None
    for line in queue_lines(text):
        try:
            j = parse_queue_line(line)
            err = None
            if n_pos is not None:
                try:
                    check_ranges(j.combo, n_pos, n_neg)
                except ComboError as e:
                    err = e.render(translate)
            out.append({"images": j.combo.images, "combos": j.combo.combos, "random": j.combo.has_random,
                        "sizes": len(j.sizes), "enabled": j.enabled, "error": err})
        except ComboError as e:
            out.append({"images": 0, "combos": 0, "random": False, "sizes": 0, "enabled": False,
                        "error": e.render(translate)})
    return out


def format_queue(jobs: list[Job]) -> str:
    return "\n".join(j.to_line() for j in jobs)


@dataclass
class Plan:
    prompts: list[str]
    negatives: list[str]
    seeds: list[int]
    sizes: list[tuple[int, int]]
    resolved: list[str]  # e.g. "2+4+7+n1"
    sources: list[str]  # e.g. "#1 2+(3,4,5)+7 (2/3)"
    jobs_used: int


def plan_queue(text: str, prompt: str, negative: str, rng: random.Random | None = None) -> Plan:
    """Queue text -> per-image lists for the enabled items. Raises ComboErrors."""
    rng = rng or random.Random()
    p_lines, n_lines = split_lines(prompt), split_lines(negative)
    errors: list[ComboError] = []
    jobs: list[tuple[int, Job]] = []
    for no, line in enumerate(queue_lines(text), 1):
        try:
            j = parse_queue_line(line)
            if j.enabled:
                check_ranges(j.combo, len(p_lines), len(n_lines))
                jobs.append((no, j))
        except ComboError as e:
            # a disabled line that does not even parse is ignored as well
            if line.split()[-1].lower() != OFF:
                errors.append(ComboError("Queue item #{no}: {msg}", no=no, msg=e))
    if errors:
        raise ComboErrors(errors)

    plan = Plan([], [], [], [], [], [], len(jobs))
    for no, j in jobs:
        n = j.combo.images
        for k in range(n):
            ls = j.combo.resolve(k, rng)
            plan.prompts.append("\n".join(p_lines[l.idx - 1] for l in ls if not l.neg))
            plan.negatives.append("\n".join(n_lines[l.idx - 1] for l in ls if l.neg))
            plan.seeds.append(rng.randint(0, MAX_SEED) if j.seed == -1 else (j.seed + k) % (MAX_SEED + 1))
            plan.sizes.append(j.sizes[0] if len(j.sizes) == 1 else rng.choice(j.sizes))
            plan.resolved.append("+".join(l.label() for l in ls))
            plan.sources.append(f"#{no} {j.combo.text()} ({k + 1}/{n})")
    return plan


# ---------------------------------------------------------------- size list (dropdown)

DEFAULT_SIZE_LISTS: dict[str, list[tuple[int, int]]] = {
    "SDXL": [(832, 1216), (1216, 832), (1024, 1024), (896, 1152), (1152, 896), (768, 1344), (1344, 768)],
    "SD 1.5": [(512, 768), (768, 512), (768, 768), (576, 768), (768, 576), (512, 912), (912, 512)],
    "Anima": [(832, 1216), (1216, 832), (1024, 1024), (832, 832), (1024, 768), (768, 1024), (896, 896)],
}

ASPECT_PRESETS: dict[str, float] = {  # width / height
    "Square (1:1)": 1.0,
    "Portrait (2:3)": 2 / 3,
    "Landscape (3:2)": 3 / 2,
    "Portrait (3:4)": 3 / 4,
    "Landscape (4:3)": 4 / 3,
    "Tall (9:16)": 9 / 16,
    "Wide (16:9)": 16 / 9,
    "Ultrawide (21:9)": 21 / 9,
    "Phone (9:19)": 9 / 19,
}


def aspect_size(ratio: float, megapixels: float, multiple: int = 8) -> tuple[int, int]:
    """Size with the given width/height ratio and about `megapixels` x 1024 x 1024 pixels."""
    px = megapixels * 1024 * 1024
    w = (px * ratio) ** 0.5
    h = w / ratio
    return max(multiple, round(w / multiple) * multiple), max(multiple, round(h / multiple) * multiple)


def parse_size_list(text: str | None) -> tuple[list[tuple[int, int]], list[str]]:
    """'832x1216, 1216x832' / one per line / 'w,h;w,h' -> (unique sizes in order, bad items)."""
    t = _norm(text)
    if re.search(r"[x×*]", t, re.IGNORECASE):
        items = [x.strip() for x in re.split(r"[,;\n]", t)]  # 832x1216, 1216x832 / one per line
    else:
        items = [x.strip().replace(",", "x") for x in re.split(r"[;\n]", t)]  # 768,1280;512,512
    out: list[tuple[int, int]] = []
    bad: list[str] = []
    for it in items:
        if not it:
            continue
        try:
            wh = parse_size(it)
        except ComboError:
            bad.append(it)
            continue
        if wh not in out:
            out.append(wh)
    return out, bad


def format_size_list(sizes: list[tuple[int, int]]) -> str:
    return "\n".join(f"{w}x{h}" for w, h in sizes)
