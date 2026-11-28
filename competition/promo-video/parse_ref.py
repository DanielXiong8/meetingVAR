"""Extract chapter markers + description from a saved YouTube watch page.

Usage: python -I parse_ref.py <watch.html>
"""
import json
import re
import sys


def find_json_objects(text, marker, limit=40):
    """Yield balanced JSON objects that start at each occurrence of marker."""
    out = []
    idx = 0
    while len(out) < limit:
        i = text.find(marker, idx)
        if i < 0:
            break
        # back up to the opening brace of the enclosing object
        start = text.rfind("{", 0, i)
        depth = 0
        j = start
        in_str = False
        esc = False
        while j < len(text):
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        out.append(text[start:j + 1])
                        idx = j + 1
                        break
            j += 1
        else:
            break
    return out


def main():
    html = open(sys.argv[1], encoding="utf-8", errors="ignore").read()

    chapters = []
    for blob in find_json_objects(html, "macroMarkersListItemRenderer", 40):
        try:
            data = json.loads(blob)
        except Exception:
            continue
        r = data.get("macroMarkersListItemRenderer", {})
        title = (r.get("title", {}).get("simpleText")
                 or "".join(x.get("text", "") for x in r.get("title", {}).get("runs", [])))
        stamp = r.get("timeDescription", {}).get("simpleText", "")
        if title:
            chapters.append((stamp, title))

    seen = set()
    print("=== CHAPTERS ===")
    for stamp, title in chapters:
        key = (stamp, title)
        if key in seen:
            continue
        seen.add(key)
        print(f"{stamp:>8}  {title}")

    m = re.search(r'"attributedDescription":\{"content":"(.{0,3000}?)","commandRuns"', html, re.S)
    if not m:
        m = re.search(r'"attributedDescription":\{"content":"(.{0,3000})', html, re.S)
    print("\n=== DESCRIPTION ===")
    if m:
        desc = m.group(1).encode().decode("unicode_escape", errors="ignore")
        print(desc[:2500])
    else:
        print("(not found)")


if __name__ == "__main__":
    main()