"""Number + direction check for AI text (shared by ML-7a narrative.py and the bot's /ask, 2026-10-19).

An AI answer is shown only if every number in it appears in the facts it was given (any sign, rounded to
0/1/2 decimals; small counts 0..10 allowed) and no direction word ("fell", "inflow", …) contradicts the
sign of the number it introduces. Pure functions, standard library only — ships in the telegram image
and in the llm-narrative ConfigMap (ops-tools/apply-llm.sh).
"""
import re

SMALL_INTS = set(range(0, 11))       # "two indices", "3 sectors" — harmless counts
NUM = re.compile(r"(?<![\w.])[-+−–]?\d[\d,]*(?:\.\d+)?")   # − and – (models use both for negatives)
KEYNUM = re.compile(r"\d+")          # numbers inside metric-key names (ret_12m, sma50_ratio) — NUM's lookbehind misses them


def allowed_numbers(facts):
    out = set(SMALL_INTS)

    def walk(v, is_key=False):
        if isinstance(v, dict):
            for k, x in v.items():
                out.update(float(m) for m in KEYNUM.findall(str(k)))   # ret_12m -> 12, sma50_ratio -> 50
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            for x in (v, abs(v)):
                out.add(float(x))                                          # the exact value itself
                out.update({round(x, 4), round(x, 3), round(x, 2), round(x, 1), round(x)})  # models round to 2-4 dp
        elif isinstance(v, str):                 # numbers inside names: "Nifty 50", "S&P 500"
            out.update(float(m.replace(",", "")) for m in NUM.findall(v))
    walk(facts)
    return out


def bad_numbers(text, facts):
    """Numbers in `text` that are not in the facts (any sign, rounded to 0-4 decimals).

    A percent form of a fact value is allowed (0.0259 -> "2.59%") because models always express ratios as
    percentages — that is the same number, not a new one. Anything else (a difference, an average) is a new
    number and fails."""
    ok = allowed_numbers(facts)
    raw = []

    def vals(v):
        if isinstance(v, dict):
            for x in v.values():
                vals(x)
        elif isinstance(v, list):
            for x in v:
                vals(x)
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            raw.append(float(v))
    vals(facts)
    bad = []
    for m in NUM.findall(text):
        v = float(m.replace(",", "").replace("−", "-").replace("–", "-").lstrip("+"))
        if any(abs(abs(v) - abs(a)) < 1e-9 for a in ok):
            continue
        if any(abs(abs(v) - abs(f)) < 0.1 or abs(abs(v) - round(abs(f) * 100, 2)) < 0.1 for f in raw):
            continue            # same number, rounded or truncated (models do both); percent form allowed too
        bad.append(m)
    return bad


UP = re.compile(r"\b(rose|rises|rising|gain(?:ed|s)?|up|increas(?:ed|e|es)|higher|advanc(?:ed|es)|jump(?:ed|s)|climb(?:ed|s)|"
                r"surg(?:ed|es)|inflows?|bought|buying|buyers|added)\b", re.I)
DOWN = re.compile(r"\b(fell|falls|falling|declin(?:ed|e|es)|down|dropp?(?:ed|s)?|lower|slipp?(?:ed|s)?|slid|lost|loss(?:es)?|"
                  r"outflows?|sold|selling|sellers|shed|plung(?:ed|es)|tumbl(?:ed|es))\b", re.I)
# "depreciated/strengthened" left out on purpose: for FX the direction depends on which currency is the subject.


def wrong_directions(text, facts):
    """Numbers whose direction word contradicts the sign in the facts (2026-10-19: the number check is sign-blind).

    A direction word applies only to the FIRST number after it in the same clause ("fell 1.21% to 22,363" -> 1.21);
    a number right after "to"/"at" is a level and skipped. Numbers matching facts of both signs are skipped."""
    signs = {}

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, (int, float)) and not isinstance(v, bool) and v != 0:
            for r in {round(abs(v), 2), round(abs(v), 1), round(abs(v))}:
                signs.setdefault(r, set()).add(v > 0)
    walk(facts)
    bad = []
    for clause in re.split(r"[;.,](?!\d)|\bwhile\b|\bwhereas\b|\bbut\b", text):
        prev = 0
        for m in NUM.finditer(clause):
            gap = clause[prev:m.start()]
            prev = m.end()
            if re.search(r"\b(to|at)\s*(rs\.?|₹|\$)?\s*$", gap, re.I):
                continue
            up, down = bool(UP.search(gap)), bool(DOWN.search(gap))
            if up == down:
                continue
            s = signs.get(abs(float(m.group(0).replace(",", "").replace("−", "-").lstrip("+"))))
            if s and len(s) == 1 and (True in s) != up:
                bad.append(m.group(0).lstrip("+"))
    return bad


def strip_fences(text):
    """Some models wrap JSON in a ```json … ``` block (gemma-4, measured 2026-10-18)."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        t = t.rsplit("```", 1)[0]
    return t.strip()


def signed_numbers(text):
    """Every number in a text with its sign ("−1.2%" -> -1.2). Facts for checking an answer against text."""
    out = []
    for m in NUM.finditer(text or ""):
        try:
            out.append(float(m.group(0).replace(",", "").replace("−", "-").replace("+", "")))
        except ValueError:
            continue
    return out


def problems(text, facts):
    """All reasons to reject `text`: invented numbers and wrong directions (empty list = OK)."""
    return bad_numbers(text, facts) + [f"{x} (wrong direction)" for x in wrong_directions(text, facts)]
