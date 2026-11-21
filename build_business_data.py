"""Rebuild business_data.json from data.txt.

Reads every sign/part section in data.txt and emits:
  date_ranges : one entry per 3 day group  -> "Sign|MM-DD|MM-DD"
  texts       : one entry per sign/part    -> "Sign|<part>" with Q1/Q2 x High/Mid/Low
  growth_plans: one entry per decan         -> "Sign|decan" with month{1,2}_week{1..4}_step{1,2}

Run: py build_business_data.py
"""

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
DATA_PATH = BACKEND / "data.txt"
OUT_PATH = BACKEND / "business_data.json"

MONTHS = {
    "January": 1, "February": 2, "March": 3, "April": 4,
    "May": 5, "June": 6, "July": 7, "August": 8,
    "September": 9, "October": 10, "November": 11, "December": 12,
}

CATEGORIES = [
    "Software & IT Services", "Construction", "Food Service", "Real Estate",
    "Online Content", "Wholesale Trade", "Food & Beverage Manufacturing",
    "Textile & Apparel", "Wood, Paper & Printing", "Chemicals, Metals & Machinery",
    "Utilities", "Horticulture & Floristry", "Quarrying & Extraction", "Retail Trade",
    "Transportation", "Storage & Logistics", "Accommodation", "Forestry",
    "Publishing & Writing", "Agriculture", "Mining", "Finance", "Insurance",
    "Fishing & Aquaculture", "Legal Services", "Marketing Services",
    "Design & Architecture", "Scientific & Technical Services", "Veterinary Services",
    "Administrative & Support Services", "Education", "Health & Social Work",
    "Arts & Entertainment", "Sports & Recreation", "Other Personal & Community Services",
]

DEFAULT_SCORE = 52

PAGE_RE = re.compile(r"^Business Compatibility: Complete Content Page \d+$", re.M)

HEADER_RE = re.compile(
    r"(?P<sign>[A-Z][a-z]+)\s*\((?P<datestr>[^)]*)\),\s*part\s*(?P<part>\d)\s*of\s*3\.\s*"
    r"Sign ruler:\s*(?P<ruler>[A-Za-z]+)\.\s*"
    r"Part ruler:\s*(?P<pruler>[A-Za-z]+)\.?\s*"
    r"(?:Element:\s*(?P<element>[A-Za-z]+))?"
)

ROW_RE = re.compile(
    r"(?P<sd>\d{1,2})\s+(?:(?P<sm>[A-Z][a-z]+)\s+)?(?:to|of)\s+"
    r"(?P<ed>\d{1,2})\s+(?P<em>[A-Z][a-z]+)\b"
)

TEXT_HEAD_RE = re.compile(r"^(High|Mid|Low) match: (Q1|Q2)\..*$", re.M)
PLAN_HEAD_RE = re.compile(r"^Business Growth plan \(2 months\)", re.M)
MONTH_RE = re.compile(r"^Month (\d+), Week (\d+): (.+)$")
STEP_RE = re.compile(r"^Step (\d+)$")
LINE4_RE = re.compile(r"^Line 4 \((High|Mid|Low)\): (.+)$")

BANDS = ("High", "Mid", "Low")


def clean(lines, merge=True):
    """Strip page headers and blank lines from a block of source lines.

    When merge is True, a line that does not end with sentence punctuation is
    treated as wrapped and joined to the next line.
    """
    out = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if merge and out and not out[-1].endswith((".", "?", "!")):
            out[-1] = out[-1] + " " + ln
        else:
            out.append(ln)
    return out


def parse_table(table_text, problems, where):
    """Return list of groups: (label, start_md, end_md, items, options)."""
    rows = []
    matches = list(ROW_RE.finditer(table_text))
    for idx, m in enumerate(matches):
        sd = int(m.group("sd"))
        sm_name = m.group("sm")
        ed = int(m.group("ed"))
        em_name = m.group("em")
        if sm_name is None:
            sm_name = em_name
        sm = MONTHS.get(sm_name)
        em = MONTHS.get(em_name)
        if sm is None or em is None:
            problems.append(f"{where}: unknown month in row {m.group(0)!r}")
            continue

        body = table_text[m.end(): matches[idx + 1].start() if idx + 1 < len(matches) else len(table_text)]
        tokens = body.split("%")
        items = []
        for tok in tokens[:5]:
            tok = tok.strip().lstrip(";").strip()
            if not tok:
                continue
            name, _, num = tok.rpartition(" ")
            name = name.strip()
            num = num.strip()
            if not name or not num.isdigit():
                problems.append(f"{where}: unparsed percent token {tok!r}")
                continue
            items.append({"category": name, "percent": int(num)})

        if len(items) != 5:
            problems.append(f"{where}: row {sd} has {len(items)} categories")

        options_text = tokens[5].strip() if len(tokens) > 5 else ""
        label = f"{sd} to {ed} {em_name}"
        if sm_name != em_name:
            label = f"{sd} {sm_name} to {ed} {em_name}"

        rows.append({
            "label": label,
            "start": (sm, sd),
            "end": (em, ed),
            "items": items,
            "options": options_text,
        })
    return rows


def parse_texts(section_text, where, problems):
    """Return {"Q1": {band: [5 lines]}, "Q2": {...}}."""
    heads = list(TEXT_HEAD_RE.finditer(section_text))
    if not heads:
        problems.append(f"{where}: no Q1/Q2 headings found")
        return {"Q1": {}, "Q2": {}}

    out = {"Q1": {}, "Q2": {}}
    for idx, m in enumerate(heads):
        band, q = m.group(1), m.group(2)
        end = heads[idx + 1].start() if idx + 1 < len(heads) else len(section_text)
        block = section_text[m.end():end]
        lines = clean(block.splitlines())
        if len(lines) != 5:
            problems.append(f"{where}: {band} {q} has {len(lines)} lines")
            lines = (lines + [""] * 5)[:5]
        out[q].setdefault(band, lines)
    for q in ("Q1", "Q2"):
        for band in BANDS:
            if band not in out[q]:
                problems.append(f"{where}: missing {band} {q}")
    return out


def parse_plan(section_text, where, problems):
    """Return {"month<M>_week<W>_step<S>": [4 lines]} with week 5..8 mapped to 1..4."""
    plan_idx = PLAN_HEAD_RE.search(section_text)
    if plan_idx is None:
        problems.append(f"{where}: no growth plan heading")
        return {}

    lines = clean(section_text[plan_idx.end():].splitlines(), merge=False)
    steps = {}
    order = []
    month = week = step = None
    base = []

    def flush():
        if step is None:
            return
        key = f"month{month}_week{week}_step{step}"
        steps[key] = base[:]
        order.append(key)

    for ln in lines:
        mm = MONTH_RE.match(ln)
        if mm:
            flush()
            month, week, step, base = int(mm.group(1)), int(mm.group(2)), None, []
            continue
        ms = STEP_RE.match(ln)
        if ms:
            flush()
            step, base = int(ms.group(1)), []
            continue
        m4 = LINE4_RE.match(ln)
        if m4:
            base.append((m4.group(1), m4.group(2)))
            continue
        if step is not None:
            base.append(ln)
    flush()

    out = {}
    for key in order:
        raw = steps[key]
        plain = [x for x in raw if isinstance(x, str)]
        cond = {}
        for item in raw:
            if isinstance(item, tuple):
                cond[item[0]] = item[1]
        if len(plain) != 3:
            problems.append(f"{where}: {key} has {len(plain)} base lines")
        missing = [b for b in BANDS if b not in cond]
        if missing:
            problems.append(f"{where}: {key} missing Line 4 for {', '.join(missing)}")
        line4 = " ".join(f"{b}: {cond[b]}" for b in BANDS if b in cond)
        out[key] = (plain + [line4])[:4]
    return out


def validate(data, problems):
    for key, g in data["date_ranges"].items():
        if len(g["top5"]) != 5:
            problems.append(f"{key}: top5 has {len(g['top5'])} items")
        if len(g["all_categories"]) != len(CATEGORIES):
            problems.append(f"{key}: all_categories has {len(g['all_categories'])} items")
        if sum(1 for v in g["all_categories"].values() if v != DEFAULT_SCORE) != 5:
            problems.append(f"{key}: expected 5 scored categories")
    for key, t in data["texts"].items():
        for q in ("Q1", "Q2"):
            for band in BANDS:
                lines = (t.get(q) or {}).get(band) or []
                if len(lines) != 5:
                    problems.append(f"{key}: {band} {q} has {len(lines)} lines")
    for key, plan in data["growth_plans"].items():
        if len(plan) != 16:
            problems.append(f"{key}: {len(plan)} plan steps")
        for k, lines in plan.items():
            if len(lines) != 4:
                problems.append(f"{key}/{k}: {len(lines)} lines")


def main():
    text = DATA_PATH.read_text(encoding="utf-8")
    text = PAGE_RE.sub("", text)

    headers = list(HEADER_RE.finditer(text))
    if not headers:
        raise SystemExit("no section headers found in data.txt")

    problems = []
    date_ranges = {}
    texts = {}
    plans_by_key = {}
    section_count = 0
    row_count = 0

    for idx, h in enumerate(headers):
        section_text = text[h.start(): headers[idx + 1].start() if idx + 1 < len(headers) else len(text)]
        sign = h.group("sign")
        part = int(h.group("part"))
        where = f"{sign} part {part}"
        section_count += 1

        tbl = section_text.find("3 day group")
        if tbl < 0:
            problems.append(f"{where}: no table header")
            continue
        q1 = TEXT_HEAD_RE.search(section_text)
        if q1 is None:
            problems.append(f"{where}: no Q1 heading (table unterminated)")
            continue
        table_text = re.sub(r"\s+", " ", section_text[tbl:q1.start()])
        rows = parse_table(table_text, problems, where)

        texts_key = f"{sign}|{part}"
        plan_m = PLAN_HEAD_RE.search(section_text)
        texts_end = plan_m.start() if plan_m else len(section_text)
        texts[texts_key] = parse_texts(section_text[:texts_end], where, problems)

        for r in rows:
            row_count += 1
            key = f"{sign}|{r['start'][0]:02d}-{r['start'][1]:02d}|{r['end'][0]:02d}-{r['end'][1]:02d}"
            if key in date_ranges:
                problems.append(f"{where}: duplicate group key {key}")
                continue
            items = r["items"]
            if len(items) >= 3:
                expected = ", ".join(i["category"] for i in items[:3])
                got = " ".join(r["options"].split())
                if got and got != expected:
                    problems.append(f"{where}: options mismatch\n    src: {got}\n    top: {expected}")
            all_categories = {c: DEFAULT_SCORE for c in CATEGORIES}
            for i in items:
                if i["category"] in all_categories:
                    all_categories[i["category"]] = i["percent"]
                else:
                    problems.append(f"{where}: unknown category {i['category']!r}")
            date_ranges[key] = {
                "sign": sign,
                "decan": part,
                "sign_ruler": h.group("ruler"),
                "part_ruler": h.group("pruler"),
                "texts_key": texts_key,
                "label": r["label"],
                "top5": items,
                "all_categories": all_categories,
            }

        plans_by_key[texts_key] = parse_plan(section_text, where, problems)

    growth_plans = {}
    for texts_key, plan in plans_by_key.items():
        normalized = {}
        for key, lines in plan.items():
            m = re.fullmatch(r"month(\d)_week(\d)_step(\d)", key)
            if not m:
                problems.append(f"{texts_key}: bad plan key {key}")
                continue
            month, week, step = (int(x) for x in m.groups())
            if month == 2:
                week -= 4
            normalized[f"month{month}_week{week}_step{step}"] = lines
        if len(normalized) != 16:
            problems.append(f"{texts_key}: expected 16 plan steps, got {len(normalized)}")
        growth_plans[texts_key] = {k: normalized[k] for k in sorted(normalized)}

    data = {
        "notes": {
            "bands": {"high": "80 to 97", "mid": "65 to 79", "low": "52 to 64"},
            "placeholders": ["[%] percentage of the selected category", "[business] the role the user selected"],
            "usage": "find group by month and day, read all_categories, pick band, read texts_key, replace [%] and [business]",
        },
        "date_ranges": date_ranges,
        "texts": texts,
        "growth_plans": growth_plans,
    }

    validate(data, problems)

    if OUT_PATH.exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = BACKEND / f"business_data.json.bak_{stamp}"
        shutil.copy2(OUT_PATH, backup)
        print(f"backup written      : {backup.name}")

    OUT_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"sections            : {section_count}")
    print(f"date ranges         : {len(date_ranges)}")
    print(f"texts               : {len(texts)}")
    print(f"growth plans        : {len(growth_plans)}")
    print(f"growth plan keys    : {sorted(growth_plans)[:5]} ...")
    print(f"plan steps/decan    : {sorted({len(v) for v in growth_plans.values()})}")
    print(f"rows parsed         : {row_count}")
    print(f"file size bytes     : {OUT_PATH.stat().st_size}")
    if problems:
        print(f"problems            : {len(problems)}")
        for p in problems[:40]:
            print("  - " + p)
    else:
        print("problems            : 0")


if __name__ == "__main__":
    main()