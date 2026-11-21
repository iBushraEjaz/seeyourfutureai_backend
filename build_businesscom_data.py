import json
import re
import sys
from pathlib import Path

SRC = Path(r"C:\Users\DELL\Documents\bushra cv\Seeyourfuture Ai\backend\businesscom.txt")
OUT = Path(r"C:\Users\DELL\Documents\bushra cv\Seeyourfuture Ai\backend\business_compatibility_data.json")
BACKUP = Path(r"C:\Users\DELL\Documents\bushra cv\Seeyourfuture Ai\backend\business_compatibility_data.json.bak")

BANDS = ["strong_match", "good_match", "uphill", "not_worth_it"]
BAND_MAP = {
    "Strong match": "strong_match",
    "Good match": "good_match",
    "Uphill": "uphill",
    "Not really worth it": "not_worth_it",
}
BAND_Q1_RE = re.compile(
    r"^(Strong match|Good match|Uphill|Not really worth it)\s*\((80 and above|65 to 79|50 to 64|below 50)\):\s*(.*)$"
)
BAND_Q2_RE = re.compile(
    r"^(Strong match|Good match|Uphill|Not really worth it)\s*\((80 and above|65 to 79|50 to 64|below 50)\)\s*$"
)
CAT_RE = re.compile(r"^\s*(.+?)\*?\s+(\d+)\s*%\s*$")
KEY_RE = re.compile(r"Internal key:\s*([A-Z0-9-]+_to_[A-Z0-9-]+)")


def fail(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def parse_options(lines_section):
    text = " ".join(l.strip() for l in lines_section if l.strip())
    out = []
    for part in text.split("|"):
        part = part.strip()
        m = CAT_RE.match(part)
        if m:
            out.append({"category": m.group(1).strip(), "percent": int(m.group(2))})
    return out


def parse_roles(lines_section):
    text = " ".join(l.strip() for l in lines_section if l.strip())
    text = text.replace("Best roles in the top options:", "", 1)
    out = {}
    for part in text.split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        cat, roles = part.split(":", 1)
        cat = cat.strip()
        role_list = [r.strip() for r in roles.split(",") if r.strip()]
        out[cat] = role_list
    return out


def split_abilities(part):
    part = part.strip()
    if not part:
        return []
    tokens = [t.strip() for t in part.split(". ")]
    items = []
    i = 0
    while i + 1 < len(tokens):
        name = tokens[i]
        desc = tokens[i + 1]
        if desc.endswith("."):
            desc = desc[:-1]
        items.append({"name": name, "description": desc})
        i += 2
    if i < len(tokens):
        items.append({"name": tokens[i].rstrip("."), "description": ""})
    return items


def parse_abilities(text):
    good_at = []
    needs_support = []
    if "Needs support:" in text:
        good_part, support_part = text.split("Needs support:", 1)
    else:
        good_part, support_part = text, ""
    if good_part.startswith("Good at:"):
        good_part = good_part[len("Good at:"):]
    good_at = split_abilities(good_part)
    needs_support = split_abilities(support_part)
    return good_at, needs_support


def find_section(block, substr, from_idx=0):
    for j in range(from_idx, len(block)):
        if substr in block[j]:
            return j
    return -1


def parse_q1(block, start_idx, end_idx):
    band_positions = []
    for j in range(start_idx, end_idx):
        m = BAND_Q1_RE.match(block[j].strip())
        if m:
            band_positions.append((j, BAND_MAP[m.group(1)], m.group(3).strip()))
    result = {}
    for k, (pos, band_key, headline) in enumerate(band_positions):
        end_pos = band_positions[k + 1][0] if k + 1 < len(band_positions) else end_idx
        para_lines = []
        started = False
        for j in range(pos + 1, end_pos):
            l = block[j]
            if not started:
                if CAT_RE.match(l):
                    continue
                if not l.strip():
                    continue
                started = True
            para_lines.append(l.strip())
        result[band_key] = {"headline": headline, "body": " ".join(para_lines)}
    return result


def parse_q2(block, start_idx, end_idx):
    band_positions = []
    for j in range(start_idx, end_idx):
        m = BAND_Q2_RE.match(block[j].strip())
        if m:
            band_positions.append((j, BAND_MAP[m.group(1)]))
    result = {}
    for k, (pos, band_key) in enumerate(band_positions):
        end_pos = band_positions[k + 1][0] if k + 1 < len(band_positions) else end_idx
        para_lines = []
        for j in range(pos + 1, end_pos):
            l = block[j].strip()
            if not l:
                continue
            if l.startswith("Page ") or l == "Business compatibility content, review copy":
                continue
            if CAT_RE.match(l):
                continue
            para_lines.append(l)
        result[band_key] = {"headline": "", "body": " ".join(para_lines)}
    return result


def main():
    if not SRC.exists():
        fail(f"source file not found: {SRC}")

    lines = SRC.read_text(encoding="utf-8").splitlines()

    group_starts = []
    for i in range(len(lines)):
        if re.fullmatch(r"Group \d+ of 121", lines[i].strip()):
            group_starts.append(i)

    if len(group_starts) != 121:
        fail(f"expected 121 groups, found {len(group_starts)}")

    records = {}
    for bi, gs in enumerate(group_starts):
        ge = group_starts[bi + 1] if bi + 1 < len(group_starts) else len(lines)
        block = lines[gs:ge]

        m = KEY_RE.search(block[1] if len(block) > 1 else "")
        if not m:
            # scan whole block for internal key
            for l in block:
                m = KEY_RE.search(l)
                if m:
                    break
        if not m:
            fail(f"group {bi + 1}: missing internal key")
        internal_key = m.group(1)

        gnum_match = re.match(r"Group (\d+) of 121", block[0].strip())
        group_number = int(gnum_match.group(1)) if gnum_match else bi + 1

        idx_best_options = find_section(block, "Best options (from source file)")
        idx_best_roles = find_section(block, "Best roles in the top options:")
        idx_abilities = find_section(block, "Abilities")
        idx_allcats = find_section(block, "All category scores")
        idx_q1 = find_section(block, "Question 1:")
        idx_q2 = find_section(block, "Question 2:")

        if idx_best_options < 0 or idx_best_roles < 0 or idx_abilities < 0:
            fail(f"group {internal_key}: missing best options / roles / abilities section")
        if idx_q1 < 0 or idx_q2 < 0:
            fail(f"group {internal_key}: missing Question 1 / Question 2 section")

        opt_lines = block[idx_best_options + 1:idx_best_roles] if idx_best_roles > idx_best_options else []
        best_options = parse_options(opt_lines)
        if len(best_options) != 5:
            fail(f"group {internal_key}: expected 5 best options, found {len(best_options)}")

        role_lines = block[idx_best_roles:idx_abilities] if idx_abilities > idx_best_roles else []
        best_roles = parse_roles(role_lines)
        if len(best_roles) != 5:
            fail(f"group {internal_key}: expected 5 best role categories, found {len(best_roles)}")

        ab_lines = block[idx_abilities + 1:idx_allcats] if idx_allcats > idx_abilities else block[idx_abilities + 1:]
        ab_text = " ".join(l.strip() for l in ab_lines if l.strip())
        good_at, needs_support = parse_abilities(ab_text)
        if len(good_at) != 3:
            fail(f"group {internal_key}: expected 3 good_at abilities, found {len(good_at)}")
        if len(needs_support) != 2:
            fail(f"group {internal_key}: expected 2 needs_support abilities, found {len(needs_support)}")

        # all categories: every CAT_RE line in the block, excluding best_options lines
        opt_line_set = set(range(idx_best_options + 1, idx_best_roles)) if idx_best_roles > idx_best_options else set()
        all_categories = {}
        all_categories_source = {}
        for j in range(len(block)):
            if j in opt_line_set:
                continue
            m = CAT_RE.match(block[j])
            if m:
                name = m.group(1).strip()
                pct = int(m.group(2))
                all_categories[name] = pct
                all_categories_source[name] = ("*" in block[j])
        if len(all_categories) != 35:
            fail(f"group {internal_key}: expected 35 categories, found {len(all_categories)}")
        if sum(1 for v in all_categories_source.values() if v) != 5:
            fail(f"group {internal_key}: expected 5 from_source categories, found {sum(1 for v in all_categories_source.values() if v)}")

        q1 = parse_q1(block, idx_q1, idx_q2) if idx_q2 > idx_q1 else {}
        q2 = parse_q2(block, idx_q2, len(block)) if idx_q2 >= 0 else {}

        for band in BANDS:
            if band not in q1 or not q1[band].get("body"):
                fail(f"group {internal_key}: question1 {band} missing or empty")
            if band not in q2 or not q2[band].get("body"):
                fail(f"group {internal_key}: question2 {band} missing or empty")

        records[internal_key] = {
            "group_number": group_number,
            "best_options": best_options,
            "best_roles": best_roles,
            "abilities": {
                "good_at": good_at,
                "needs_support": needs_support,
            },
            "all_categories": all_categories,
            "all_categories_source": all_categories_source,
            "question1": q1,
            "question2": q2,
        }

    if len(records) != 121:
        fail(f"expected 121 records, built {len(records)}")
    if len({k for k in records}) != 121:
        fail("duplicate internal keys detected")

    # back up existing output if present
    if OUT.exists():
        BACKUP.write_text(OUT.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"backed up existing {OUT.name} to {BACKUP.name}")

    OUT.write_text(json.dumps({"groups": records}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"WROTE {OUT} ({len(records)} groups)")
    print(f"first key: {list(records.keys())[0]}, last key: {list(records.keys())[-1]}")


if __name__ == "__main__":
    main()
