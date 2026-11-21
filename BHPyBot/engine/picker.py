"""engine/picker.py - the number-list reader, one module with its own tests.

Used by every list that shows numbers: Invasion WAVE, Expedition DIFFICULTY, Trials / Gauntlet number lists. Pure functions (no game, no
bot object): a picture goes in, rows come out, so they can be tested with screenshots alone (`bhbot.py selftest`, tests/numberlists/).
The wheel logic (`Afk.list_pick`) still lives in bhbot.py and uses these.
"""
import cv2
import numpy as np


def read_rows(img, samples, y0=140, y1=445, x0=330, x1=450, min_h=13, bright=False):
    """Every number in a box of an RGB picture: [(y, value or None)], top to bottom. Whole numbers are 14 px tall (row borders are 3 px, rows
    cut off by the edge of the list are shorter); commas and dots are tiny and skipped, so '7,445' reads as 7445. `samples` = [(char, 14x10 bool)].
    bright=True also accepts coloured digits (yellow, light blue, red ... lists), not only white ones."""
    reg = img[y0:y1, x0:x1].astype(int)
    m = reg.min(axis=2) >= 235
    if bright:
        mx, mn = reg.max(axis=2), reg.min(axis=2)
        m = m | ((mx >= 200) & (mx - mn > 60))
    bands, start = [], None
    for i, v in enumerate(list(m.any(axis=1)) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= min_h:
                bands.append((start, i))
            start = None
    reads = []
    for a, b in bands:
        row, text, c0, cols = m[a:b], "", None, []
        for x, v in enumerate(list(row.any(axis=0)) + [False]):
            if v and c0 is None:
                c0 = x
            elif not v and c0 is not None:
                cols.append((c0, x))
                c0 = None
        for c0, c1 in cols:
            cut = row[:, c0:c1]
            ys = np.nonzero(cut.any(axis=1))[0]
            cut = cut[ys.min():ys.max() + 1]
            if cut.shape[0] < 8:                                  # a comma / dot between the digit groups
                continue
            g = cv2.resize(cut.astype(np.uint8) * 255, (10, 14), interpolation=cv2.INTER_AREA) > 127
            d, ch = min((int((g != r).sum()), ch) for ch, r in samples) if samples else (999, "?")
            text += ch if d <= 35 else "?"
        reads.append(((a + b) // 2 + y0, int(text) if text.isdigit() else None))
    return reads


def infer_rows(reads, default_step=1):
    """From raw reads [(y, value|None)] to clean rows [(y, value)] and the list's step. The list counts down by a fixed step (1 for waves,
    5 for expedition difficulty ...), worked out from the rows themselves; every row is rebuilt from the most common 'value + row index * step',
    so one misread digit cannot send the bot to the wrong row. Returns ([], step) when the reads do not agree."""
    diffs = {}
    for (_, v1), (_, v2) in zip(reads, reads[1:]):
        if v1 is not None and v2 is not None and v1 > v2:
            diffs[v1 - v2] = diffs.get(v1 - v2, 0) + 1
    step = max(diffs.items(), key=lambda kv: (kv[1], -kv[0]))[0] if diffs else default_step
    votes = {}
    for i, (y, v) in enumerate(reads):
        if v is not None:
            votes[v + i * step] = votes.get(v + i * step, 0) + 1
    if not votes:
        return [], step
    base, n = max(votes.items(), key=lambda kv: kv[1])
    if n < 2 and len(reads) > 1:
        return [], step
    return [(y, base - i * step) for i, (y, v) in enumerate(reads)], step


def selftest(digit_samples, root):
    """-> list of problems (empty = fine). Logic tests plus every screenshot in tests/numberlists/ (<name>_<first>_step<n>.png)."""
    bad = []
    rows, step = infer_rows([(160, 7620), (220, 7615), (280, 7610), (340, 7605)])
    if step != 5 or [v for _, v in rows] != [7620, 7615, 7610, 7605]:
        bad.append("infer_rows: clean list -> %s step %s" % ([v for _, v in rows], step))
    rows, step = infer_rows([(160, 7620), (220, 7415), (280, 7610), (340, 7605)])      # one misread row is repaired
    if [v for _, v in rows] != [7620, 7615, 7610, 7605]:
        bad.append("infer_rows: misread row not repaired -> %s" % [v for _, v in rows])
    rows, step = infer_rows([(160, 12), (220, 11), (280, 10)])
    if step != 1 or [v for _, v in rows] != [12, 11, 10]:
        bad.append("infer_rows: step 1 list -> %s step %s" % ([v for _, v in rows], step))
    if infer_rows([(160, None), (220, None)])[0]:
        bad.append("infer_rows: unreadable list should give no rows")
    samples = digit_samples()
    for png in sorted((root / "tests" / "numberlists").glob("*.png")):
        parts = png.stem.split("_")
        try:
            first, st = int(parts[-2]), int(parts[-1].replace("step", ""))
        except (ValueError, IndexError):
            continue
        img = cv2.cvtColor(cv2.imread(str(png)), cv2.COLOR_BGR2RGB)
        for tint in (None, (255, 240, 40), (40, 230, 235), (255, 120, 120), (170, 170, 255), (140, 255, 140)):      # white and the five coloured kinds
            im = img.copy()
            if tint:
                m = im.min(axis=2) >= 235
                m[:140], m[445:], m[:, :330], m[:, 450:] = False, False, False, False
                im[m] = tint
            rows, step = infer_rows(read_rows(im, samples, bright=bool(tint)))
            want = [first - i * st for i in range(len(rows))]
            if len(rows) < 3 or [v for _, v in rows] != want or step != st:
                bad.append("%s (%s): read %s step %s, expected %s step %s" % (png.name, tint or "white", [v for _, v in rows][:6], step, want[:6], st))
    return bad
