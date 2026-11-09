#!/usr/bin/env python3
"""Rebuilds this profile's README.md and its SVGs from the GitHub API, in three editions.

  classy  near-black, serif type, hairline rules, one brass accent
  retro   cream paper, star-atlas plates: double rules, hatching, vermilion. Its SVGs are slices of one sheet
          (side rules only, top rule on the hero, bottom rule on the visitors), stacked with align="top" and <br>
          inside a single <p> so no page background shows between them, and each slice keeps its own link.
  paper   the whole profile as one engraved folio SVG

The active edition (data/theme.txt) becomes README.md; the other is written to alt/README-<name>.md so it can be
previewed on GitHub. Runs in .github/workflows/rebuild.yml every 6 hours, and with `--add-star <login>` when a
visitor opens a "✨ Leave a star" issue. Locally: `python3 build/build.py` with the gh CLI signed in.
Standard library only.
"""
import base64, datetime as dt, hashlib, html, json, math, os, random, re, subprocess, sys, urllib.request
from collections import Counter

ME = "SwastikTripathi"
SKIP_OWNERS = {"swastiktripathi", "nammayatri", "rostring36222"}  # own and employer repos aren't contributions
SITE = "https://swastiktripathi.dev"
X_HANDLE = "SwastikT_space"
LINKEDIN = "https://www.linkedin.com/in/swastik-tripathi/"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
NOW = dt.datetime.now(dt.timezone.utc)

SERIF = "'Iowan Old Style','Palatino Linotype',Palatino,'Book Antiqua',Georgia,serif"
SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace"

THEMES = {
    "classy": dict(name="classy", bg="#0c0c0d", rule="#2b2b2e", ink="#ecebe7", muted="#8f8c86", faint="#4a494d",
                   accent="#c9a45c", logo="gray", labelfont=MONO, numfont=SERIF,
                   shades=["#ecebe7", "#c9a45c", "#a19e97", "#6f6d68", "#4c4b4f", "#343337"],
                   badge="161617", badge_logo="ecebe7"),
    "retro": dict(name="retro", bg="#f2ead8", rule="#1f1b16", ink="#1f1b16", muted="#6e6556", faint="#c9bc9c",
                  accent="#b4412c", accent2="#2d4a6b", logo="sepia", labelfont=SERIF, numfont=SERIF,
                  shades=["#1f1b16", "#b4412c", "#2d4a6b", "#8a7f6b", "#c9bc9c", "#5a5246"],
                  badge="f2ead8", badge_logo="1f1b16", print=True, sheet=True),
}
# paper: the whole profile as one continuous engraved folio (Swastik liked the retro star chart)
THEMES["paper"] = dict(THEMES["retro"], name="paper", bg="#efe6d0", folio=True)
LANG_ORDER = ["TypeScript", "Swift", "Python", "Go", "JavaScript", "Jupyter Notebook"]


# ---------------------------------------------------------------- data

def run_gh(args):
    r = subprocess.run(["gh", "api", *args], capture_output=True, text=True, timeout=90)
    if r.returncode:
        raise RuntimeError(f"gh api {args[0]} failed: {r.stderr.strip()[:300]}")
    return json.loads(r.stdout)


def gh(path, **params):
    args = ["-X", "GET", path]
    for k, v in params.items():
        args += ["-f", f"{k}={v}"]
    return run_gh(args)


def search(q):
    items, page = [], 1
    while True:
        res = gh("search/issues", q=q, per_page=100, page=page)
        items += res["items"]
        if len(res["items"]) < 100 or len(items) >= res["total_count"] or page == 10:
            break
        page += 1
    out = []
    for i in items:
        slug = i["repository_url"].split("/repos/")[1]
        if slug.split("/")[0].lower() in SKIP_OWNERS:
            continue
        when = (i.get("pull_request") or {}).get("merged_at") or i.get("closed_at") or i["created_at"]
        out.append({"repo": slug, "title": i["title"], "url": i["html_url"], "created": i["created_at"], "when": when})
    return out


def owner_stars(login):
    """Stars across the owner's 100 most-starred public repos: how big the organization behind a project is."""
    q = ("query($l:String!){repositoryOwner(login:$l){repositories(first:100,privacy:PUBLIC,isFork:false,"
         "orderBy:{field:STARGAZERS,direction:DESC}){nodes{stargazerCount}}}}")
    try:
        res = run_gh(["graphql", "-f", f"query={q}", "-f", f"l={login}"])
        return sum(n["stargazerCount"] for n in res["data"]["repositoryOwner"]["repositories"]["nodes"])
    except Exception:
        return 0


def data_uri(url):
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            kind = r.headers.get("Content-Type", "image/png").split(";")[0]
            return f"data:{kind};base64," + base64.b64encode(r.read()).decode()
    except Exception:
        return ""


def review_states():
    """GitHub's review status for each open PR, as its pull request list shows it."""
    q = ("query($q:String!){search(query:$q,type:ISSUE,first:100){nodes{... on PullRequest{url isDraft reviewDecision}}}}")
    try:
        nodes = run_gh(["graphql", "-f", f"query={q}", "-f", f"q=author:{ME} is:pr is:open"])["data"]["search"]["nodes"]
    except (RuntimeError, KeyError, TypeError):
        return {}
    words = {"APPROVED": "APPROVED", "CHANGES_REQUESTED": "REVIEWING", "REVIEW_REQUIRED": "REVIEWING"}
    return {n["url"]: "DRAFT" if n.get("isDraft") else words.get(n.get("reviewDecision"), "OPEN") for n in nodes if n}


def own_repos():
    """The Workshop cards, from data/my-repos.txt ("repo | Title | note"). Private repos are fine: whatever a local
    build can read is saved to data/my-repos.json, and the GitHub Action (whose token can't see private repos)
    falls back to that."""
    cache_path = os.path.join(DATA, "my-repos.json")
    try:
        cache = json.load(open(cache_path))
    except (OSError, ValueError):
        cache = {}
    try:
        lines = [l.strip() for l in open(os.path.join(DATA, "my-repos.txt")) if l.strip() and not l.startswith("#")]
    except OSError:
        lines = []
    own = []
    for line in lines[:4]:
        name, title, note = (x.strip() for x in (line.split("|") + ["", ""])[:3])
        try:
            info = gh(f"repos/{ME}/{name}")
            cache[name] = {"lang": info.get("language") or "", "stars": info["stargazers_count"], "private": info["private"],
                           "description": info.get("description") or "", "pushed": info.get("pushed_at") or info["created_at"]}
        except RuntimeError:
            if name not in cache:
                continue
        c = cache[name]
        own.append({"name": title or name, "lang": c["lang"], "stars": c["stars"], "private": c["private"],
                    "about": note or c["description"], "pushed": c["pushed"]})
    json.dump(cache, open(cache_path, "w"), indent=1, sort_keys=True)
    return own


def collect():
    merged = search(f"author:{ME} is:pr is:merged")
    unmerged = search(f"author:{ME} is:pr is:closed is:unmerged")
    open_ = search(f"author:{ME} is:pr is:open")
    if not merged:
        raise SystemExit("no merged PRs found; refusing to overwrite the README with an empty one")
    repos, owners = {}, {}
    for slug in sorted({p["repo"] for p in merged + open_}):
        try:
            info = gh(f"repos/{slug}")
        except RuntimeError:
            continue
        repos[slug] = {"name": info["full_name"], "stars": info["stargazers_count"], "lang": info.get("language") or "",
                       "owner": info["owner"]["login"], "avatar_url": info["owner"]["avatar_url"]}
    merged = [p for p in merged if p["repo"] in repos]
    for slug in {p["repo"] for p in merged}:
        info = repos[slug]
        if info["owner"] not in owners:
            owners[info["owner"]] = owner_stars(info["owner"])
        info["owner_stars"] = max(owners[info["owner"]], info["stars"])
        sep = "&" if "?" in info["avatar_url"] else "?"
        info["avatar"] = data_uri(info["avatar_url"] + sep + "s=64")
    states = review_states()
    for p in open_:
        p["state"] = states.get(p["url"], "OPEN")
    own = own_repos()
    me = gh(f"users/{ME}")
    return {"merged": sorted(merged, key=lambda p: p["when"], reverse=True), "unmerged": unmerged,
            "open": [p for p in open_ if p["repo"] in repos], "repos": repos,
            "me_avatar": data_uri(me["avatar_url"] + "&s=128"), "own": own}


def week_streak(merged):
    """Consecutive weeks (Monday-based, ending this week or last) with at least one merge."""
    weeks = {(dt.datetime.fromisoformat(p["when"].replace("Z", "+00:00")).date().isocalendar()[:2]) for p in merged}
    day = NOW.date()
    if day.isocalendar()[:2] not in weeks:
        day -= dt.timedelta(weeks=1)
    n = 0
    while day.isocalendar()[:2] in weeks:
        n += 1
        day -= dt.timedelta(weeks=1)
    return n


# ---------------------------------------------------------------- svg helpers

def short(name):
    """Display name for a repo: drop "eslint-plugin-", and use the owner when the repo name says nothing alone."""
    owner, repo = name.split("/")
    return owner if repo.lower() in {"extensions", "core", "app", "monorepo"} else re.sub(r"^eslint-plugin-", "", repo)


def headline(title):
    """A PR title without its conventional-commit prefix ("fix(utils): ")."""
    return re.sub(r"^\w+(\([^)]*\))?!?:\s*", "", title)


def esc(s):
    return html.escape(str(s), quote=True)


def kstars(n):
    return f"{n / 1000:.1f}k".replace(".0k", "k") if n < 100_000 else f"{round(n / 1000)}k"


def big(n):
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{round(n / 1000)}k" if n >= 10_000 else str(n)


def roman(n):
    out = ""
    for v, s in [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
                 (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]:
        while n >= v:
            out, n = out + s, n - v
    return out


def kf(points):
    """(time 0..1, value) points -> keyTimes, values (adds the 0 and 1 ends SMIL wants)."""
    pts = []
    for t, v in points:
        t = min(1.0, max(0.0, t))
        if pts and t < pts[-1][0]:
            t = pts[-1][0]
        pts.append((t, v))
    if pts[0][0] > 0:
        pts.insert(0, (0.0, pts[0][1]))
    if pts[-1][0] < 1:
        pts.append((1.0, pts[-1][1]))
    return ";".join(f"{t:.4f}" for t, _ in pts), ";".join(str(v) for _, v in pts)


def anim(attr, points, dur, mode="linear"):
    times, values = kf(points)
    return (f'<animate attributeName="{attr}" keyTimes="{times}" values="{values}" dur="{dur}s" '
            f'calcMode="{mode}" repeatCount="indefinite"/>')


def intro(attr, frm, to, delay, dur=.9, tag="animate"):
    """Plays once on load. The element's own attributes hold the final state, so it still shows if a
    renderer doesn't run the animation."""
    k = delay / (delay + dur)
    kind = ' type="translate"' if tag == "animateTransform" else ""
    return (f'<{tag} attributeName="{attr}"{kind} values="{frm};{frm};{to}" keyTimes="0;{k:.3f};1" dur="{delay + dur:.2f}s" '
            f'fill="freeze" calcMode="spline" keySplines="0 0 1 1;.2 .8 .2 1"/>')


def spin(cx, cy, dur, reverse=False):
    a, b = (360, 0) if reverse else (0, 360)
    return (f'<animateTransform attributeName="transform" type="rotate" from="{a} {cx} {cy}" to="{b} {cx} {cy}" '
            f'dur="{dur}s" repeatCount="indefinite"/>')


def defs_common(T):
    hatch = lambda i, c: (f'<pattern id="{i}" width="3.6" height="3.6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
                          f'<rect width="3.6" height="3.6" fill="{T["bg"]}"/><line x1="0" y1="0" x2="0" y2="3.6" stroke="{c}" stroke-width="1.3"/></pattern>')
    return ('<filter id="gray"><feColorMatrix type="saturate" values="0"/></filter>'
            '<filter id="sepia"><feColorMatrix type="matrix" values=".39 .77 .19 0 0  .35 .69 .17 0 0  .27 .53 .13 0 0  0 0 0 1 0"/></filter>'
            '<filter id="paper" x="0" y="0" width="100%" height="100%"><feTurbulence type="fractalNoise" baseFrequency=".9" numOctaves="2" seed="4" stitchTiles="stitch"/>'
            '<feColorMatrix type="matrix" values="0 0 0 0 .2  0 0 0 0 .15  0 0 0 0 .08  .5 0 0 0 -.18"/></filter>'
            + hatch("hatch", T["ink"]) + hatch("hatchA", T["accent"]))


def sheet_rules(T, w, h, sides):
    """Rules on all four edges of a sheet slice. Where two slices meet, the browser blends each image's fractional
    edge with the page background, which shows as a hairline; a band of ink on the join hides it. So each edge
    carries ink right up to the image's edge, heavier on the sheet's outside (sides: l, r, t, b), plus a thin
    rule just inside. Two slices together print as thin, thick, thin."""
    ink, out = T["ink"], []
    for side in "lrtb":
        k = 2.4 if side in sides else 1.2
        out.append({"l": f'<rect x="0" y="0" width="{k}" height="{h}"/>', "r": f'<rect x="{w - k}" y="0" width="{k}" height="{h}"/>',
                    "t": f'<rect x="0" y="0" width="{w}" height="{k}"/>', "b": f'<rect x="0" y="{h - k}" width="{w}" height="{k}"/>'}[side])
    return (f'<g fill="{ink}">{"".join(out)}</g>'
            f'<rect x="5" y="5" width="{w - 10}" height="{h - 10}" fill="none" stroke="{ink}" stroke-width=".5"/>')


def frame(T, w, h, body, label, defs="", sides="lr"):
    if T.get("folio"):  # stacked into one sheet by folio()
        return {"w": w, "h": h, "body": body, "defs": defs, "label": label}
    retro = T.get("print")
    rx = 0 if T.get("sheet") else 4 if retro else 16
    if T.get("sheet"):
        rules = f'<rect width="{w}" height="{h}" filter="url(#paper)" opacity=".9"/>{sheet_rules(T, w, h, sides)}'
    else:
        rules = (f'<rect width="{w}" height="{h}" filter="url(#paper)" opacity=".9"/>'
             f'<rect x="7.5" y="7.5" width="{w - 15}" height="{h - 15}" fill="none" stroke="{T["ink"]}" stroke-width="1.4"/>'
             f'<rect x="11.5" y="11.5" width="{w - 23}" height="{h - 23}" fill="none" stroke="{T["ink"]}" stroke-width=".5"/>'
             if retro else
             f'<rect x=".5" y=".5" width="{w - 1}" height="{h - 1}" rx="{rx}" fill="none" stroke="{T["rule"]}"/>')
    # sheet slices fill their box exactly: "meet" would letterbox a fraction of a pixel when the browser rounds
    # the image's height, and the page background would show as a hairline between slices
    fill = ' preserveAspectRatio="none"' if T.get("sheet") else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}"{fill} role="img" '
            f'aria-label="{esc(label)}"><title>{esc(label)}</title>'
            f'<defs>{defs_common(T)}{defs}<clipPath id="frame"><rect width="{w}" height="{h}" rx="{rx}"/></clipPath></defs>'
            f'<g clip-path="url(#frame)"><rect width="{w}" height="{h}" fill="{T["bg"]}"/>{body}{rules}</g></svg>\n')


def heading(T, x, y, text, sub="", plate=None):
    if T.get("print"):
        t = (f"Plate {roman(plate)}  ·  {text}" if plate else text).upper()
        s = f'<text x="{x}" y="{y}" font-family="{SERIF}" font-size="12" letter-spacing="3.2" fill="{T["ink"]}">{esc(t)}</text>'
    else:
        s = f'<text x="{x}" y="{y}" font-family="{MONO}" font-size="11" letter-spacing="2.6" fill="{T["muted"]}">{esc(text.upper())}</text>'
    if sub:
        s += f'<text x="{x}" y="{y + 24}" font-family="{SERIF}" font-style="italic" font-size="15" fill="{T["muted"]}">{esc(sub)}</text>'
    return s


def starfield(T, rng, w, h, n, avoid=None):
    out = []
    for _ in range(n):
        x, y = rng.uniform(16, w - 16), rng.uniform(16, h - 16)
        if avoid and avoid(x, y):
            continue
        r = rng.choice([.5, .6, .7, .8, .9, 1.1, 1.4])
        if T.get("print"):
            out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r * .9:.2f}" fill="{T["ink"]}" opacity="{rng.uniform(.35, .8):.2f}"/>')
        else:
            o = rng.uniform(.2, .75)
            out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="#fff" opacity="{o:.2f}">'
                       f'<animate attributeName="opacity" values="{o:.2f};{o * .2:.2f};{o:.2f}" dur="{rng.uniform(3, 8):.1f}s" '
                       f'begin="-{rng.uniform(0, 6):.1f}s" repeatCount="indefinite"/></circle>')
    return "".join(out)


def ticks(T, cx, cy, r, step=5, major=30, short=4, long=9, color=None, width=.7):
    c = color or (T["ink"] if T.get("print") else T["faint"])
    d = []
    for a in range(0, 360, step):
        L = long if a % major == 0 else short
        ca, sa = math.cos(math.radians(a)), math.sin(math.radians(a))
        d.append(f"M{cx + r * ca:.1f} {cy + r * sa:.1f}L{cx + (r - L) * ca:.1f} {cy + (r - L) * sa:.1f}")
    return f'<path d="{"".join(d)}" stroke="{c}" stroke-width="{width}"/>'


# ---------------------------------------------------------------- hero

def hero(T, d):
    W, H = 840, 310
    retro = T.get("print")
    merged, repos = d["merged"], d["repos"]
    projects = {p["repo"] for p in merged}
    since = min(p["when"] for p in merged)[:4]
    cx, cy = 668, 158
    if retro:
        rng = random.Random(31)
        inner = []
        for _ in range(70):
            a, rr = rng.uniform(0, 2 * math.pi), 96 * math.sqrt(rng.random())
            inner.append(f'<circle cx="{cx + rr * math.cos(a):.1f}" cy="{cy + rr * math.sin(a):.1f}" r="{rng.choice([.6, .8, 1, 1.3, 1.8]):.1f}" fill="{T["ink"]}"/>')
        figures = []
        for k in range(3):
            pts = []
            a0, r0 = rng.uniform(0, 2 * math.pi), rng.uniform(25, 70)
            for j in range(4 + k):
                a, rr = a0 + j * rng.uniform(.25, .5), r0 + rng.uniform(-18, 18)
                pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
            figures.append(f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}" fill="none" stroke="{T["ink"]}" stroke-width=".7"/>'
                           + "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.2" fill="{T["bg"]}" stroke="{T["ink"]}" stroke-width=".9"/>' for x, y in pts))
        chart = (f'<circle cx="{cx}" cy="{cy}" r="118" fill="none" stroke="{T["ink"]}" stroke-width="1.3"/>'
                 f'<circle cx="{cx}" cy="{cy}" r="104" fill="none" stroke="{T["ink"]}" stroke-width=".6"/>'
                 + ticks(T, cx, cy, 118, 5, 15, 5, 13) +
                 f'<clipPath id="disc"><circle cx="{cx}" cy="{cy}" r="103"/></clipPath>'
                 f'<g clip-path="url(#disc)"><g>{spin(cx, cy, 360)}'
                 + "".join(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{T["faint"]}" stroke-width=".6"/>' for r in (35, 70))
                 + "".join(f'<line x1="{cx}" y1="{cy}" x2="{cx + 103 * math.cos(math.radians(a)):.1f}" y2="{cy + 103 * math.sin(math.radians(a)):.1f}" stroke="{T["faint"]}" stroke-width=".6"/>' for a in range(0, 360, 30))
                 + "".join(inner) + "".join(figures) + '</g>'
                 f'<ellipse cx="{cx}" cy="{cy}" rx="103" ry="38" transform="rotate(-23 {cx} {cy})" fill="none" stroke="{T["accent"]}" stroke-width=".9" stroke-dasharray="4 3"/>'
                 '</g>'
                 + "".join(f'<text x="{cx + 129 * math.cos(math.radians(a - 90)):.1f}" y="{cy + 129 * math.sin(math.radians(a - 90)) + 3.5:.1f}" text-anchor="middle" '
                           f'font-family="{SERIF}" font-size="9.5" fill="{T["ink"]}">{roman(a // 30 + 1)}</text>' for a in range(0, 360, 30)))
        left = (f'<text x="48" y="54" font-family="{SERIF}" font-size="11.5" letter-spacing="3.2" fill="{T["ink"]}">PLATE I</text>'
                f'<line x1="48" y1="64" x2="470" y2="64" stroke="{T["ink"]}" stroke-width=".6"/>'
                f'<text x="48" y="102" font-family="{SERIF}" font-size="12" letter-spacing="3.2" fill="{T["muted"]}">OPEN SOURCE  ·  MACOS &amp; IOS  ·  INDIA</text>'
                f'<text x="44" y="158" font-family="{SERIF}" font-size="56" fill="{T["ink"]}">Swastik Tripathi</text>'
                f'<line x1="48" y1="184" x2="96" y2="184" stroke="{T["ink"]}" stroke-width=".8"/>'
                f'<path d="M103 184 l5 -5 l5 5 l-5 5 z" fill="{T["accent"]}"/>'
                f'<line x1="120" y1="184" x2="168" y2="184" stroke="{T["ink"]}" stroke-width=".8"/>'
                f'<text x="48" y="222" font-family="{SERIF}" font-style="italic" font-size="18" fill="{T["ink"]}">'
                f'{len(merged)} pull requests merged across {len(projects)} open-source projects.</text>'
                f'<text x="48" y="256" font-family="{SERIF}" font-size="11" letter-spacing="2.6" fill="{T["muted"]}">'
                + f'SINCE {roman(int(since))}  ·  REVISED FROM THE GITHUB API, {NOW:%d %b %Y}'.upper() + '</text>')
        body = starfield(T, random.Random(7), W, H, 40, lambda x, y: x < 500 or math.hypot(x - cx, y - cy) < 140) + chart + left
    else:
        rings = [(34, 3, 19, T["ink"]), (58, 4, 31, T["muted"]), (84, 5, 47, T["accent"]), (110, 3.5, 76, T["ink"])]
        orrery = (ticks(T, cx, cy, 128, 5, 30, 3, 8) +
                  "".join(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{T["rule"]}"/>' for r, *_ in rings) +
                  f'<circle cx="{cx}" cy="{cy}" r="7" fill="{T["ink"]}"/><circle cx="{cx}" cy="{cy}" r="12" fill="none" stroke="{T["faint"]}"/>')
        rng = random.Random(5)
        for r, size, dur, color in rings:
            a = rng.uniform(0, 360)
            moon = (f'<g>{spin(cx + r, cy, 6)}<circle cx="{cx + r + 11}" cy="{cy}" r="1.5" fill="{T["muted"]}"/></g>'
                    if color == T["accent"] else "")
            orrery += (f'<g transform="rotate({a:.0f} {cx} {cy})"><g>{spin(cx, cy, dur)}'
                       f'<circle cx="{cx + r}" cy="{cy}" r="{size}" fill="{color}"/>{moon}</g></g>')
        left = (f'<g>{intro("opacity", 0, 1, .1, 1.1)}'
                f'<text x="48" y="92" font-family="{MONO}" font-size="11" letter-spacing="2.8" fill="{T["muted"]}">OPEN SOURCE  ·  MACOS &amp; IOS  ·  INDIA</text>'
                f'<text x="44" y="152" font-family="{SERIF}" font-size="58" fill="{T["ink"]}">Swastik Tripathi</text>'
                f'<line x1="48" y1="180" x2="104" y2="180" stroke="{T["accent"]}" stroke-width="1.4"/>'
                f'<text x="48" y="216" font-family="{SERIF}" font-style="italic" font-size="18" fill="{T["ink"]}" opacity=".88">'
                f'{len(merged)} pull requests merged across {len(projects)} open-source projects.</text>'
                f'<text x="48" y="252" font-family="{MONO}" font-size="10.5" letter-spacing="1.6" fill="{T["faint"]}">'
                + f'SINCE {since}  ·  REBUILT FROM THE GITHUB API  ·  {NOW:%d %b %Y}'.upper() + '</text></g>')
        body = starfield(T, random.Random(7), W, H, 80, lambda x, y: math.hypot(x - cx, y - cy) < 136) + orrery + left
    return frame(T, W, H, body, f"Swastik Tripathi. {len(merged)} pull requests merged across {len(projects)} open-source projects.", sides="lrt")


# ---------------------------------------------------------------- ledger (stats + languages)

def ledger(T, d):
    W, H = 840, 262
    retro = T.get("print")
    merged, repos = d["merged"], d["repos"]
    projects = {p["repo"] for p in merged}
    decided = len(merged) + len(d["unmerged"])
    streak = week_streak(merged)
    tiles = [(str(len(merged)), "PRs merged"), (str(len(projects)), "projects"),
             (big(sum(repos[r]["stars"] for r in projects)), "stars across them"),
             (f"{round(100 * len(merged) / decided)}%" if decided else "-", "merge rate"),
             (f"{streak}", "week streak")]
    x0, x1, top = 28, W - 28, 66
    cw = (x1 - x0) / len(tiles)
    out = [heading(T, 32, 44, "The ledger" if retro else "At a glance", plate=2)]
    for i, (num, label) in enumerate(tiles):
        x = x0 + i * cw
        if i:
            out.append(f'<line x1="{x:.1f}" y1="{top + 6}" x2="{x:.1f}" y2="{top + 74}" stroke="{T["rule"] if not retro else T["faint"]}"/>')
        delay = .1 + i * .1
        out.append(f'<g>{intro("opacity", 0, 1, delay, .8)}'
                   f'<text x="{x + cw / 2:.1f}" y="{top + 46}" text-anchor="middle" font-family="{SERIF}" font-size="38" '
                   f'fill="{T["accent"] if i == 0 else T["ink"]}">{esc(num)}</text>'
                   f'<text x="{x + cw / 2:.1f}" y="{top + 70}" text-anchor="middle" font-family="{T["labelfont"]}" '
                   f'font-size="{10.5 if retro else 10}" letter-spacing="{1.8 if retro else 1.6}" fill="{T["muted"]}">{esc(label.upper())}</text></g>')
    if retro:
        out.append(f'<line x1="{x0}" y1="{top - 4}" x2="{x1}" y2="{top - 4}" stroke="{T["ink"]}" stroke-width=".6"/>'
                   f'<line x1="{x0}" y1="{top + 88}" x2="{x1}" y2="{top + 88}" stroke="{T["ink"]}" stroke-width=".6"/>')
    # languages of the merged work, weighted by number of merged PRs
    langs = Counter(repos[p["repo"]]["lang"] or "Other" for p in merged)
    total = sum(langs.values())
    by = top + 122
    out.append(f'<text x="32" y="{by - 12}" font-family="{T["labelfont"]}" font-size="{11 if retro else 10}" letter-spacing="2" fill="{T["muted"]}">'
               f'{"LANGUAGES OF THE MERGED WORK" if retro else "LANGUAGES I SHIP IN"}</text>')
    x, lx = x0 + 4, x0 + 4
    bw = x1 - x0 - 8
    edge = f' stroke="{T["ink"]}" stroke-width=".7"' if retro else ""
    for j, (lang, n) in enumerate(langs.most_common(6)):
        w = bw * n / total
        color = T["shades"][j]
        fill = ("url(#hatch)" if j == 0 else "url(#hatchA)" if j == 1 else color) if retro else color
        out.append(f'<rect x="{x:.1f}" y="{by}" width="{max(w - 3, 1.5):.1f}" height="{9 if retro else 5}" fill="{fill}"{edge}>{intro("width", 0, f"{max(w - 3, 1.5):.1f}", .7 + j * .08)}</rect>')
        x += w
        pct = f"{lang}  {round(100 * n / total)}%"
        out.append(f'<rect x="{lx}" y="{by + 25}" width="8" height="8" fill="{fill}"{edge}/>'
                   f'<text x="{lx + 14}" y="{by + 33}" font-family="{SERIF if retro else SANS}" font-size="12.5" fill="{T["ink"]}">{esc(pct)}</text>')
        lx += 14 + len(pct) * 6.9 + 24
    body = (starfield(T, random.Random(3), W, H, 30 if not retro else 0) + "".join(out))
    return frame(T, W, H, body, f"{len(merged)} pull requests merged across {len(projects)} projects")


# ---------------------------------------------------------------- orrery

def orrery(T, d):
    paper = T["name"] in ("paper", "retro")  # the planisphere orrery
    W, H, cx, cy = (840, 890, 420, 474) if paper else (840, 760, 420, 404)
    rim = 374 if paper else 336  # planisphere: heavy rim at rim, thin circle at rim - 16, numerals at rim + 18
    retro = T.get("print")
    merged, repos = d["merged"], d["repos"]
    counts = Counter(p["repo"] for p in merged)
    order = sorted(counts, key=lambda r: (-counts[r], -repos[r]["stars"]))
    radii, durs = ([138, 224, 306] if paper else [122, 207, 292]), [240, 240, 240]
    n = len(order)
    sizes = [max(1, round(n * r / sum(radii))) for r in radii[:2]]
    rings = [order[:sizes[0]], order[sizes[0]:sizes[0] + sizes[1]], order[sizes[0] + sizes[1]:]]
    clips, orbits, bodies = [], [], []
    rng = random.Random(11)
    pid = 0
    dash = ' stroke-dasharray="1 4"' if retro else ""
    italic = 'font-style="italic" ' if retro else ""
    size = lambda slug: min(28, max(9, 9 + (.55 * math.log10(max(repos[slug]["stars"], 500))
                                         + .45 * math.log10(max(repos[slug]["owner_stars"], 500)) - 3.05) * 9))
    # every project gets its own angle, alternating rings, and all rings turn together: no two planets ever meet
    a0, ang, queues = rng.uniform(0, 360), {}, [list(m) for m in rings]
    while any(queues):
        for q in queues:
            if q:
                ang[q.pop(0)] = a0 + len(ang) * 360 / n
    for k, (R, members, dur) in enumerate(zip(radii, rings, durs)):
        if not members:
            continue
        orbits.append(f'<circle cx="{cx}" cy="{cy}" r="{R}" fill="none" stroke="{T["ink"] if retro else T["rule"]}" '
                      f'stroke-width="{.6 if retro else 1}"{dash}/>')
        g = [f'<g>{spin(cx, cy, dur)}']
        for j, slug in enumerate(members):
            info = repos[slug]
            a = math.radians(ang[slug])
            px, py = cx + R * math.cos(a), cy + R * math.sin(a)
            # size: the project's own stars and the stars of the organization behind it
            r = size(slug)
            clips.append(f'<clipPath id="p{pid}"><circle r="{r:.1f}"/></clipPath>')
            logo = (f'<image href="{info["avatar"]}" x="{-r:.1f}" y="{-r:.1f}" width="{2 * r:.1f}" height="{2 * r:.1f}" '
                    f'clip-path="url(#p{pid})"/>' if info.get("avatar") else "")
            name = short(info["name"])
            ring = (f'<circle r="{r + 4:.1f}" fill="none" stroke="{T["ink"]}" stroke-width=".5"/>'
                    f'<circle r="{r + .8:.1f}" fill="{T["bg"]}" stroke="{T["ink"]}" stroke-width="1.1"/>' if retro else
                    f'<circle r="{r + 5:.1f}" fill="{T["bg"]}" stroke="{T["rule"]}"/>'
                    f'<circle r="{r + 1:.1f}" fill="{T["bg"]}" stroke="{T["accent"] if counts[slug] >= 10 else T["faint"]}"/>')
            g.append(
                f'<g transform="translate({px:.1f} {py:.1f})"><g>{spin(0, 0, dur, reverse=True)}{ring}{logo}'
                f'<text y="{r + 18:.1f}" text-anchor="middle" font-family="{SERIF}" font-size="13" fill="{T["ink"]}">{esc(name)}</text>'
                f'<text y="{r + 31:.1f}" text-anchor="middle" font-family="{SERIF if retro else MONO}" font-size="{10.5 if retro else 9.5}" '
                f'{italic}fill="{T["muted"]}">{counts[slug]} merged · {kstars(info["stars"])}★</text>'
                f'</g></g>')
            pid += 1
        g.append('</g>')
        bodies.append("".join(g))
    if retro:
        rays = "".join(f'<line x1="{cx + 44 * math.cos(math.radians(a)):.1f}" y1="{cy + 44 * math.sin(math.radians(a)):.1f}" '
                       f'x2="{cx + (58 if a % 30 == 0 else 51) * math.cos(math.radians(a)):.1f}" y2="{cy + (58 if a % 30 == 0 else 51) * math.sin(math.radians(a)):.1f}" '
                       f'stroke="{T["ink"]}" stroke-width=".8"/>' for a in range(0, 360, 15))
        sun = (f'<g>{spin(cx, cy, 90)}{rays}</g><circle cx="{cx}" cy="{cy}" r="40" fill="{T["bg"]}" stroke="{T["ink"]}" stroke-width="1.2"/>')
    else:
        sun = (ticks(T, cx, cy, 52, 10, 30, 3, 6) +
               f'<circle cx="{cx}" cy="{cy}" r="40" fill="{T["bg"]}" stroke="{T["accent"]}"/>')
    sun += ((f'<image href="{d["me_avatar"]}" x="{cx - 36}" y="{cy - 36}" width="72" height="72" clip-path="url(#me)"/>'
             if d["me_avatar"] else "") +
            "")
    outer = (f'<circle cx="{cx}" cy="{cy}" r="336" fill="none" stroke="{T["ink"] if retro else T["rule"]}" stroke-width="{.8 if retro else 1}"/>'
             + ticks(T, cx, cy, 336, 5, 30, 4, 10))
    if paper:  # drawn like a planisphere: heavy rim, tick band, numerals, meridians, a dashed ecliptic
        outer = (f'<circle cx="{cx}" cy="{cy}" r="{rim}" fill="none" stroke="{T["ink"]}" stroke-width="2.2"/>'
                 f'<circle cx="{cx}" cy="{cy}" r="{rim - 16}" fill="none" stroke="{T["ink"]}" stroke-width=".7"/>'
                 + ticks(T, cx, cy, rim, 5, 15, 6, 16, width=.9)
                 + "".join(f'<text x="{cx + (rim + 18) * math.cos(math.radians(a - 90)):.1f}" y="{cy + (rim + 18) * math.sin(math.radians(a - 90)) + 5:.1f}" '
                           f'text-anchor="middle" font-family="{SERIF}" font-size="14" fill="{T["ink"]}">{roman(a // 30 + 1)}</text>' for a in range(0, 360, 30))
                 + "".join(f'<line x1="{cx + 64 * math.cos(math.radians(a)):.1f}" y1="{cy + 64 * math.sin(math.radians(a)):.1f}" '
                           f'x2="{cx + (rim - 16) * math.cos(math.radians(a)):.1f}" y2="{cy + (rim - 16) * math.sin(math.radians(a)):.1f}" stroke="{T["faint"]}" stroke-width=".7"/>'
                           for a in range(0, 360, 30)))
    body = (starfield(T, random.Random(5), W, H, 150 if not retro else 90, lambda x, y: math.hypot(x - cx, y - cy) < 60) +
            outer + "".join(orbits) + sun + "".join(bodies) +
            heading(T, 32, 44, "An orrery of merged work" if retro else "The orrery", "Every body is a project that merged my work.", plate=3) +
            f'<text x="{W - 32}" y="44" text-anchor="end" font-family="{SERIF}" font-style="italic" font-size="12.5" fill="{T["muted"]}">size: the project’s and its organization’s stars</text>'
            f'<text x="{W - 32}" y="62" text-anchor="end" font-family="{SERIF}" font-style="italic" font-size="12.5" fill="{T["muted"]}">inner orbits: more merges</text>')
    defs = "".join(clips) + f'<clipPath id="me"><circle cx="{cx}" cy="{cy}" r="36"/></clipPath>'
    return frame(T, W, H, body, f"An orrery of the {len(counts)} open-source projects that merged Swastik's pull requests", defs)


# ---------------------------------------------------------------- almanac (merges per week)

def almanac(T, d):
    W, H, weeks = 840, 250, 20
    retro = T.get("print")
    start = (NOW - dt.timedelta(days=NOW.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    starts = [start - dt.timedelta(weeks=weeks - 1 - i) for i in range(weeks)]
    counts = [0] * weeks
    for p in d["merged"]:
        t = dt.datetime.fromisoformat(p["when"].replace("Z", "+00:00"))
        for i in range(weeks - 1, -1, -1):
            if t >= starts[i]:
                if t < starts[i] + dt.timedelta(weeks=1):
                    counts[i] += 1
                break
    top = max(counts) or 1
    x0, x1, base, hmax = 40, W - 32, 200, 104
    step = (x1 - x0) / weeks
    bw = step * .56
    out, last_month = [], None
    best = counts.index(max(counts))
    for i, (c, s) in enumerate(zip(counts, starts)):
        x = x0 + i * step + (step - bw) / 2
        h = max(1.5, hmax * c / top)
        if retro:
            fill, extra = ("url(#hatchA)" if i == best else "url(#hatch)"), f' stroke="{T["ink"]}" stroke-width=".8"'
        else:
            fill, extra = (T["accent"] if i == best else T["ink"]), ' fill-opacity=".9"'
        if not c:
            fill, extra = T["faint"], ""
        delay = .15 + i * .04
        out.append(f'<rect x="{x:.1f}" y="{base - h:.1f}" width="{bw:.1f}" height="{h:.1f}" fill="{fill}"{extra}>'
                   + intro("height", 0, f"{h:.1f}", delay) + intro("y", base, f"{base - h:.1f}", delay) + '</rect>')
        if c:
            out.append(f'<text x="{x + bw / 2:.1f}" y="{base - h - 7:.1f}" text-anchor="middle" font-family="{SERIF}" font-size="12" fill="{T["ink"]}">{c}'
                       + intro("opacity", 0, 1, .8 + i * .04, .4) + '</text>')
        if s.month != last_month:
            out.append(f'<text x="{x:.1f}" y="{base + 22}" font-family="{SERIF}" font-style="italic" font-size="12" fill="{T["muted"]}">{s:%B}</text>')
            last_month = s.month
    total = sum(counts)
    body = (heading(T, 32, 44, "Almanac of merges, by week" if retro else "Merged, week by week",
                    f"{total} in the last {weeks} weeks. The best week had {max(counts)}.", plate=8) +
            f'<line x1="{x0}" y1="{base + .5}" x2="{x1}" y2="{base + .5}" stroke="{T["ink"] if retro else T["rule"]}" stroke-width="{.8 if retro else 1}"/>'
            + "".join(out))
    return frame(T, W, H, body, f"{total} merged pull requests in the last {weeks} weeks")


# ---------------------------------------------------------------- split-flap boards

FLAP_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def flap_text(s):
    s = s.upper().replace("’", "'")
    s = re.sub(r"[^A-Z0-9 .,:'#/+&()-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def fit(s, n):
    s = flap_text(s)
    if len(s) <= n:
        return s
    cut = s[:n + 1].rsplit(" ", 1)[0]
    return (cut if len(cut) >= n * .6 else s[:n]).rstrip(" ,.:;-&/")


def board(T, rows, title, sub, plate, statuses, cols):
    """A split-flap board. rows: (date, project, title, status); cols: (header, tiles), 57 tiles in all.
    Each row flips through random letters into place on load, and again every 18 seconds."""
    retro = T.get("print")
    W, x0, pitch = 840, 30, (840 - 60) / 60
    tw, th, rp, y0 = pitch - 1.6, 22, 31, 104
    H = y0 + len(rows) * rp + 22
    D = 18.0
    tile, split, ink = ("#e7ddc5", T["ink"], T["ink"]) if retro else ("#18181a", "#0a0a0b", T["ink"])
    out = [heading(T, 32, 44, title, sub, plate=plate)]
    cx = x0
    starts = []
    for name, n in cols:
        starts.append(cx)
        out.append(f'<text x="{cx + 1:.1f}" y="{y0 - 12}" font-family="{T["labelfont"]}" font-size="9.5" letter-spacing="1.6" fill="{T["muted"]}">{name.upper()}</text>')
        cx += (n + 1) * pitch
    rng = random.Random(len(rows) * 7 + plate)
    tiles = []
    for i, row in enumerate(rows):
        y = y0 + i * rp
        for (name, n), sx, value in zip(cols, starts, row):
            for k in range(n):
                tiles.append(f'<rect x="{sx + k * pitch:.1f}" y="{y}" width="{tw:.1f}" height="{th}" rx="1.5" fill="{tile}"'
                             f'{f" stroke=" + chr(34) + T["ink"] + chr(34) + " stroke-width=" + chr(34) + ".35" + chr(34) if retro else ""}/>')
            tiles.append(f'<line x1="{sx:.1f}" y1="{y + th / 2:.1f}" x2="{sx + n * pitch - 1.6:.1f}" y2="{y + th / 2:.1f}" '
                         f'stroke="{split}" stroke-width=".7" opacity="{.25 if retro else 1}"/>')
        t0 = .25 + i * .22
        for (name, n), sx, value in zip(cols, starts, row):
            value = fit(value, n)
            color = statuses.get(value, T["accent"]) if (name, n) == cols[-1] else ink
            xs = " ".join(f"{sx + k * pitch + tw / 2:.1f}" for k in range(len(value)))
            attrs = f'y="{y + 15.5}" font-family="{MONO}" font-size="13" font-weight="600" text-anchor="middle" fill="{color}"'
            if not value.strip():
                continue
            frames = 4
            for f in range(frames):
                junk = "".join(c if c == " " else rng.choice(FLAP_CHARS) for c in value)
                a, b = (t0 + f * .07) / D, (t0 + (f + 1) * .07) / D
                out.append(f'<text x="{xs}" {attrs} opacity="0">{esc(junk)}'
                           f'{anim("opacity", [(0, 0), (a, 1), (b, 0)], D, "discrete")}</text>')
            done = (t0 + frames * .07) / D
            out.append(f'<text x="{xs}" {attrs}>{esc(value)}{anim("opacity", [(0, 0), (done, 1)], D, "discrete")}</text>')
    body = "".join(tiles) + "".join(out)
    if not retro:
        body = starfield(T, random.Random(plate), W, H, 25, lambda x, y: y > 80) + body
    return frame(T, W, H, body, f"{title}: {sub}")


def state_colors(T):
    if T["name"] == "classy":
        return {"APPROVED": "#8fb573", "REVIEWING": T["accent"], "OPEN": T["ink"], "DRAFT": T["muted"]}
    return {"APPROVED": T["accent2"], "REVIEWING": T["accent"], "OPEN": T["ink"], "DRAFT": T["muted"]}


def merged_board(T, d):
    rows = [(dt.datetime.fromisoformat(p["when"].replace("Z", "+00:00")).strftime("%b %d"),
             short(d["repos"][p["repo"]]["name"]), headline(p["title"]), "MERGED")
            for p in d["merged"][:7]]
    return board(T, rows, "Recently merged", "The latest of my pull requests merged into other projects.", 5,
                 {"MERGED": T["accent"]}, [("merged", 6), ("project", 16), ("pull request", 29), ("status", 6)])


def review_queue(d):
    out, per = [], Counter()
    for p in sorted(d["open"], key=lambda p: (-d["repos"][p["repo"]]["stars"], p["created"])):
        if per[p["repo"]] < 2 and len(out) < 7:  # variety: at most 2 per project
            out.append(p)
            per[p["repo"]] += 1
    return out


def open_board(T, d):
    rows = [(dt.datetime.fromisoformat(p["created"].replace("Z", "+00:00")).strftime("%b %d"), short(d["repos"][p["repo"]]["name"]),
             headline(p["title"]), p.get("state", "OPEN")) for p in review_queue(d)]
    return board(T, rows or [("", "", "NONE OPEN RIGHT NOW", "")], "Open pull requests",
                 "Waiting on maintainers, with the review status GitHub shows.", 6, state_colors(T),
                 [("opened", 6), ("project", 16), ("pull request", 26), ("review", 9)])


# ---------------------------------------------------------------- the workshop

def wrap(text, width, lines):
    out, cur = [], ""
    for w in text.split():
        if len(cur) + len(w) + 1 > width:
            out.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    out.append(cur)
    out = [l for l in out if l]
    if len(out) > lines:
        out = out[:lines]
        out[-1] = out[-1][:width - 1].rstrip(" ,.;:") + "…"
    return out


def workshop(T, d):
    """My own repos as specimen cards pinned to a board, each with a wax seal, catalogue number and notes."""
    own = d.get("own", [])
    W, H = 840, 348
    retro = T.get("print")
    ink, acc, muted = T["ink"], T["accent"], T["muted"]
    card_bg = "#f8f2e4" if retro else "#141415"
    n = max(len(own), 1)
    gap = 22
    cw = (W - 64 - gap * (n - 1)) / n
    ch = 222
    rng = random.Random(len(own) * 13 + 5)
    out = [heading(T, 32, 44, "The workshop" if retro else "Workshop", "Things I make on my own, between pull requests.", plate=4)]
    for i, o in enumerate(own):
        x, y = 32 + i * (cw + gap), 94
        tilt = rng.uniform(-1.8, 1.8)
        mx = x + cw / 2
        when = dt.datetime.fromisoformat(o["pushed"].replace("Z", "+00:00"))
        about = re.sub(r"\s*[—–]\s*", ", ", o["about"]) or f"A personal {o['lang'] or 'side'} project."
        chars = int((cw - 32) / 5.3)
        lines = wrap(about, chars, 4)
        name = o["name"] if len(o["name"]) * 8 < cw - 30 else o["name"][:int((cw - 38) / 8)] + "…"
        meta = "  ·  ".join(t for t in [f"touched {when:%b %Y}", "private" if o["private"] else f"★ {o['stars']}" if o["stars"] else ""] if t)
        seal = (f'<g transform="translate({x + 34:.1f} {y + 40})">'
                f'<g><animateTransform attributeName="transform" type="rotate" values="0;{6 if i % 2 else -6};0" dur="{7 + i}s" repeatCount="indefinite"/>'
                + "".join(f'<circle cx="{19 * math.cos(math.radians(a)):.1f}" cy="{19 * math.sin(math.radians(a)):.1f}" r="3.2" fill="{acc}"/>' for a in range(0, 360, 30))
                + f'<circle r="19" fill="{acc}"/><circle r="14.5" fill="none" stroke="{T["bg"] if retro else "#0c0c0d"}" stroke-width=".7"/>'
                f'<text y="7" text-anchor="middle" font-family="{SERIF}" font-style="italic" font-size="20" fill="{card_bg}">{esc(o["name"][0].upper())}</text></g></g>')
        text = (f'<text x="{x + 64:.1f}" y="{y + 36}" font-family="{SERIF}" font-size="10" letter-spacing="2" fill="{muted}">No. {roman(i + 1)}</text>'
                f'<text x="{x + 64:.1f}" y="{y + 52}" font-family="{SERIF}" font-style="italic" font-size="11" fill="{muted}">{esc(o["lang"] or "")}</text>'
                f'<text x="{x + 16:.1f}" y="{y + 88}" font-family="{SERIF}" font-size="15.5" fill="{ink}">{esc(name)}</text>'
                f'<line x1="{x + 16:.1f}" y1="{y + 99}" x2="{x + cw - 16:.1f}" y2="{y + 99}" stroke="{ink}" stroke-width=".5"/>'
                f'<line x1="{x + 16:.1f}" y1="{y + 102}" x2="{x + cw - 16:.1f}" y2="{y + 102}" stroke="{ink}" stroke-width=".3"/>'
                + "".join(f'<text x="{x + 16:.1f}" y="{y + 124 + k * 17}" font-family="{SERIF}" font-style="italic" font-size="12.5" fill="{ink}">{esc(l)}</text>'
                          for k, l in enumerate(lines))
                + f'<line x1="{x + 16:.1f}" y1="{y + ch - 34}" x2="{x + cw - 16:.1f}" y2="{y + ch - 34}" stroke="{T["faint"]}" stroke-width=".6" stroke-dasharray="2 3"/>'
                f'<text x="{x + 16:.1f}" y="{y + ch - 16}" font-family="{SERIF}" font-size="10.5" letter-spacing=".6" fill="{muted}">{esc(meta)}</text>')
        card = (f'<rect x="{x + 4:.1f}" y="{y + 5}" width="{cw:.1f}" height="{ch}" fill="{T["faint"]}" opacity=".55"/>'
                f'<rect x="{x:.1f}" y="{y}" width="{cw:.1f}" height="{ch}" fill="{card_bg}" stroke="{ink}" stroke-width=".8"/>'
                f'<rect x="{x + 4:.1f}" y="{y + 4}" width="{cw - 8:.1f}" height="{ch - 8}" fill="none" stroke="{ink}" stroke-width=".3"/>'
                + seal + text +
                f'<circle cx="{mx:.1f}" cy="{y + 2}" r="5" fill="{acc}" stroke="{ink}" stroke-width=".6"/><circle cx="{mx - 1.5:.1f}" cy="{y + .5}" r="1.4" fill="{card_bg}" opacity=".8"/>')
        out.append(f'<g transform="rotate({tilt:.2f} {mx:.1f} {y})">{intro("opacity", 0, 1, .2 + i * .25, .6)}{card}</g>')
    if not own:
        out.append(f'<text x="32" y="140" font-family="{SERIF}" font-style="italic" font-size="14" fill="{muted}">List repos in data/my-repos.txt to pin them here.</text>')
    body = "".join(out)
    if not retro:
        body = starfield(T, random.Random(4), W, H, 30, lambda x, y: y > 80) + body
    return frame(T, W, H, body, "The workshop: " + ", ".join(o["name"] for o in own))


# ---------------------------------------------------------------- milestones

def milestones(T, d):
    W, H = 840, 268
    retro = T.get("print")
    merged = sorted(d["merged"], key=lambda p: p["when"])
    repos = d["repos"]
    when = lambda p: dt.datetime.fromisoformat(p["when"].replace("Z", "+00:00"))
    name = lambda slug: short(repos[slug]["name"])
    counts = Counter(p["repo"] for p in merged)
    weekly = Counter(when(p).date() - dt.timedelta(days=when(p).weekday()) for p in merged)
    best_week, best_n = max(weekly.items(), key=lambda kv: kv[1])
    largest = max(counts, key=lambda r: repos[r]["stars"])
    home, home_n = counts.most_common(1)[0]
    medals = [("First merge", str(when(merged[0]).year), when(merged[0]).strftime("%b %-d"), f"first merge · {name(merged[0]['repo'])}")]
    if len(merged) >= 100:
        medals.append(("100th merge", "100", when(merged[99]).strftime("%b %-d, %Y"), f"one hundred merged · {name(merged[99]['repo'])}"))
    else:
        medals.append(("Merged so far", str(len(merged)), "and counting", "pull requests merged"))
    medals += [("Largest project", kstars(repos[largest]["stars"]) + "★", name(largest), f"largest project · {name(largest)}"),
               ("Most merged", str(home_n), name(home), f"most merged · {name(home)}"),
               ("Best week", str(best_n), f"week of {best_week:%b %-d}", f"best week · {best_n} merged")]
    out, defs = [heading(T, 32, 44, "Milestones", "", plate=7)], []
    step = (W - 64) / len(medals)
    for i, (label, big_text, small, ring) in enumerate(medals):
        cx, cy, R = 32 + step * (i + .5), 146, 58
        pid = f"ring{i}"
        defs.append(f'<path id="{pid}" d="M{cx - R + 7:.1f} {cy} a{R - 7} {R - 7} 0 1 1 {2 * (R - 7)} 0 a{R - 7} {R - 7} 0 1 1 {-2 * (R - 7)} 0"/>')
        ring_text = f"{ring} · {ring} · ".upper()
        circ = 2 * math.pi * (R - 7)
        if retro:
            frame_ = (f'<circle cx="{cx:.1f}" cy="{cy}" r="{R}" fill="{T["bg"]}" stroke="{T["ink"]}" stroke-width="1.4"/>'
                      f'<circle cx="{cx:.1f}" cy="{cy}" r="{R - 3}" fill="none" stroke="{T["ink"]}" stroke-width=".5"/>'
                      f'<circle cx="{cx:.1f}" cy="{cy}" r="{R - 15}" fill="none" stroke="{T["ink"]}" stroke-width=".8"/>')
            ring_font, ring_fill, num_fill = SERIF, T["ink"], T["accent"]
        else:
            frame_ = (f'<circle cx="{cx:.1f}" cy="{cy}" r="{R}" fill="{T["bg"]}" stroke="{T["rule"]}"/>'
                      f'<circle cx="{cx:.1f}" cy="{cy}" r="{R - 15}" fill="none" stroke="{T["accent"]}" stroke-width=".8"/>')
            ring_font, ring_fill, num_fill = MONO, T["muted"], T["ink"]
        size = 26 if len(big_text) <= 4 else 21
        tilt = f' transform="rotate({(-7 + i * 4) if retro else 0} {cx:.1f} {cy})"'
        out.append(
            f'<g{tilt}><g>{intro("opacity", 0, 1, .2 + i * .15, .9)}{frame_}'
            f'<g>{spin(cx, cy, 50 + i * 6, reverse=i % 2 == 1)}'
            f'<text font-family="{ring_font}" font-size="8.2" fill="{ring_fill}" textLength="{circ - 6:.0f}" lengthAdjust="spacing">'
            f'<textPath href="#{pid}">{esc(ring_text)}</textPath></text></g>'
            f'<text x="{cx:.1f}" y="{cy + 7}" text-anchor="middle" font-family="{SERIF}" font-size="{size}" fill="{num_fill}">{esc(big_text)}</text>'
            f'<text x="{cx:.1f}" y="{cy + 23}" text-anchor="middle" font-family="{SERIF}" font-style="italic" font-size="10" fill="{T["muted"]}">{esc(small[:16])}</text>'
            f'</g></g>'
            f'<text x="{cx:.1f}" y="{cy + R + 30}" text-anchor="middle" font-family="{T["labelfont"]}" font-size="10" letter-spacing="1.8" fill="{T["muted"]}">{esc(label.upper())}</text>')
    body = (starfield(T, random.Random(41), W, H, 40 if not retro else 0) + "".join(out))
    return frame(T, W, H, body, "Milestones: " + ", ".join(f"{m[0]} {m[1]}" for m in medals), "".join(defs))


# ---------------------------------------------------------------- link pills

GITHUB_MARK = ("M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49"
               "-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52"
               ".28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18"
               " 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87"
               " 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z")


def pill(T, icon, text, strip=None, sides=""):
    retro = T.get("print")
    w, h = round(46 + len(text) * (7.4 if retro else 7.1)), 32
    c, bg = T["ink"], T["bg"]
    icons = {
        "web": f'<circle cx="8" cy="8" r="7" fill="none" stroke="{c}" stroke-width="1.2"/><ellipse cx="8" cy="8" rx="3" ry="7" fill="none" stroke="{c}" stroke-width="1"/>'
               f'<line x1="1" y1="8" x2="15" y2="8" stroke="{c}" stroke-width="1"/>',
        "linkedin": f'<rect width="16" height="16" rx="2.5" fill="{c}"/><text x="8" y="12.4" text-anchor="middle" font-family="{SANS}" font-weight="700" font-size="10" fill="{bg}">in</text>',
        "x": f'<path d="M1.5 1.5L14.5 14.5" stroke="{c}" stroke-width="2.2" stroke-linecap="round"/><path d="M14.5 1.5L1.5 14.5" stroke="{c}" stroke-width="1.1" stroke-linecap="round"/>',
        "github": f'<path d="{GITHUB_MARK}" fill="{c}"/>',
    }
    border = (f'<rect x="1" y="1" width="{w - 2}" height="{h - 2}" fill="{bg}" stroke="{c}" stroke-width="1.2"/>'
              f'<rect x="3.5" y="3.5" width="{w - 7}" height="{h - 7}" fill="none" stroke="{c}" stroke-width=".4"/>' if retro else
              f'<rect x=".5" y=".5" width="{w - 1}" height="{h - 1}" rx="{h / 2 - .5}" fill="{bg}" stroke="{T["rule"]}"/>')
    inner = (f'{border}<g transform="translate(15 8)">{icons[icon]}</g>'
             f'<text x="39" y="20.5" font-family="{SERIF if retro else SANS}" font-size="13" fill="{c}">{esc(text)}</text>')
    if strip:  # one cell of the sheet's link row: icon and name centred, the cell's rules frame it
        sw, sh = strip
        inner = inner.split("<g transform", 1)[1]
        body = f'<g transform="translate({(sw - w + 12) / 2:.1f} {(sh - h) / 2:.1f})"><g transform{inner}</g>'
        return frame(T, sw, sh, body, text, sides=sides)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="{esc(text)}">'
            f'<title>{esc(text)}</title>{inner}</svg>\n')


# ---------------------------------------------------------------- visitors

PREFILL = "Write a short note for the sky on the line below (optional, up to 80 characters), then press Create."


def clean_message(body):
    lines = [l.strip() for l in (body or "").splitlines() if l.strip() and not l.strip().startswith(PREFILL[:20])]
    text = re.sub(r"<[^>]*>", " ", " ".join(lines))
    text = re.sub(r"https?://\S+|www\.\S+|\S+\.(com|io|dev|net|org|xyz)\S*", "", text)
    text = re.sub(r"[<>\[\]`*_#|@\\{}~]", "", text)
    return re.sub(r"\s+", " ", text).strip()[:80]


def visitors_svg(T, visitors):
    W, H = 840, 300
    retro = T.get("print")
    shown = visitors[-90:]
    pts = []
    for v in shown:
        h = hashlib.sha256(v["login"].lower().encode()).digest()
        pts.append((48 + int.from_bytes(h[:2], "big") / 65535 * (W - 96), 104 + int.from_bytes(h[2:4], "big") / 65535 * (H - 150)))
    grid = ""
    if retro:
        grid = ("".join(f'<line x1="{x}" y1="84" x2="{x}" y2="{H - 24}" stroke="{T["faint"]}" stroke-width=".6"/>' for x in range(60, W - 20, 60)) +
                "".join(f'<line x1="24" y1="{y}" x2="{W - 24}" y2="{y}" stroke="{T["faint"]}" stroke-width=".6"/>' for y in range(104, H - 20, 40)))
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f} {y:.1f}" for i, (x, y) in enumerate(pts))
    stars, rng = [], random.Random(21)
    for v, (x, y) in zip(shown, pts):
        owner = v["login"].lower() == ME.lower()
        s = 6.5 if owner else 4.5
        color = T["accent"] if owner else T["ink"]
        anchor, lx = ("end", x - 10) if x > W - 200 else ("start", x + 10)
        shape = (f'<path d="M0 {-s} L{s * .28:.2f} {-s * .28:.2f} L{s} 0 L{s * .28:.2f} {s * .28:.2f} L0 {s} L{-s * .28:.2f} {s * .28:.2f} '
                 f'L{-s} 0 L{-s * .28:.2f} {-s * .28:.2f} Z" fill="{color}"/>')
        twinkle = (f'<animateTransform attributeName="transform" type="scale" values="1;.6;1" dur="{rng.uniform(3, 6):.1f}s" '
                   f'begin="-{rng.uniform(0, 5):.1f}s" repeatCount="indefinite"/>' if not retro else "")
        note = v.get("message") if v.get("approved") else ""
        stars.append(f'<g transform="translate({x:.1f} {y:.1f})"><g>{twinkle}{shape}</g></g>'
                     f'<text x="{lx:.1f}" y="{y + 4:.1f}" text-anchor="{anchor}" font-family="{SERIF}" font-size="12" fill="{T["ink"]}">{esc(v["login"])}</text>'
                     + (f'<text x="{lx:.1f}" y="{y + 19:.1f}" text-anchor="{anchor}" font-family="{SERIF}" font-style="italic" font-size="11" '
                        f'fill="{T["muted"]}">“{esc(note[:44] + ("…" if len(note) > 44 else ""))}”</text>' if note else ""))
    n = len(visitors) - 1
    sub = f"{n} visitor{'s have' if n != 1 else ' has'} left a star. Leave yours, with a note." if n > 0 else "No visitors yet. Leave the first star, and a note."
    line = (f'<path d="{path}" fill="none" stroke="{T["ink"] if retro else T["muted"]}" stroke-opacity="{.6 if retro else .45}" '
            f'stroke-width=".8" stroke-dasharray="3 4"><animate attributeName="stroke-dashoffset" from="0" to="-70" dur="8s" repeatCount="indefinite"/></path>'
            if len(pts) > 1 else "")
    body = (starfield(T, random.Random(17), W, H, 110 if not retro else 50) + grid + line + "".join(stars) +
            heading(T, 32, 44, "Constellation of visitors" if retro else "Visitors", sub, plate=9))
    if T.get("sheet"):  # the sheet has no badge below it, so the plate carries its own button (the whole plate is the link)
        ink, acc = T["ink"], T["accent"]
        body += (f'<g><rect x="636" y="26" width="172" height="34" fill="{T["bg"]}" stroke="{acc}" stroke-width="1.5"/>'
                 f'<rect x="640" y="30" width="164" height="26" fill="none" stroke="{acc}" stroke-width=".5"/>'
                 f'<path d="M662 36 l1.6 5.4 5.4 1.6 -5.4 1.6 -1.6 5.4 -1.6 -5.4 -5.4 -1.6 5.4 -1.6z" fill="{acc}">'
                 f'<animateTransform attributeName="transform" type="rotate" values="0 662 43;90 662 43" dur="6s" repeatCount="indefinite"/></path>'
                 f'<text x="682" y="47.5" font-family="{SERIF}" font-size="12" letter-spacing="2.6" fill="{ink}">LEAVE A STAR</text></g>')
    return frame(T, W, H, body, f"Visitors' constellation: {n} visitors left a star", sides="lrb")


# ---------------------------------------------------------------- paper edition: one engraved folio

FINE_HATCH = ('<pattern id="fine" width="3" height="3" patternUnits="userSpaceOnUse" patternTransform="rotate(35)">'
              '<line x1="0" y1="0" x2="0" y2="3" stroke="#1f1b16" stroke-width=".45"/></pattern>'
              '<pattern id="vfine" width="2.6" height="4" patternUnits="userSpaceOnUse">'
              '<line x1="0" y1="0" x2="0" y2="4" stroke="#1f1b16" stroke-width=".5"/></pattern>')


def column(T, cx, top, bottom):
    ink = T["ink"]
    tw, bw = 21, 25  # entasis: the shaft narrows towards the top
    shaft = f"M{cx - bw} {bottom} L{cx - tw} {top + 22} L{cx + tw} {top + 22} L{cx + bw} {bottom} Z"
    flutes = "".join(f'<line x1="{cx + f * bw:.1f}" y1="{bottom}" x2="{cx + f * tw:.1f}" y2="{top + 22}" stroke="{ink}" stroke-width=".55"/>'
                     for f in (-.66, -.33, 0, .33, .66))
    return (f'<clipPath id="shaft{cx}"><path d="{shaft}"/></clipPath>'
            f'<rect x="{cx - 34}" y="{top}" width="68" height="8" fill="{T["bg"]}" stroke="{ink}" stroke-width="1.1"/>'          # abacus
            f'<path d="M{cx - 30} {top + 8} L{cx + 30} {top + 8} L{cx + 23} {top + 18} L{cx - 23} {top + 18} Z" fill="{T["bg"]}" stroke="{ink}" stroke-width="1"/>'
            f'<line x1="{cx - 22}" y1="{top + 21}" x2="{cx + 22}" y2="{top + 21}" stroke="{ink}" stroke-width=".7"/>'
            f'<path d="{shaft}" fill="{T["bg"]}" stroke="{ink}" stroke-width="1.1"/>{flutes}'
            f'<rect x="{cx + 6}" y="{top + 22}" width="30" height="{bottom - top - 22}" fill="url(#vfine)" opacity=".55" clip-path="url(#shaft{cx})"/>'
            f'<rect x="{cx - 30}" y="{bottom}" width="60" height="6" rx="3" fill="{T["bg"]}" stroke="{ink}" stroke-width="1"/>'      # torus
            f'<rect x="{cx - 34}" y="{bottom + 6}" width="68" height="8" fill="{T["bg"]}" stroke="{ink}" stroke-width="1"/>')       # plinth


def facade(T, d):
    """Plate I: a temple front. Pediment with a turning star chart in the oculus, a lettered frieze, two fluted
    Doric columns, the name between them, and three steps."""
    W, H = 840, 452
    ink, acc = T["ink"], T["accent"]
    merged = d["merged"]
    projects = {p["repo"] for p in merged}
    since = int(min(p["when"] for p in merged)[:4])
    ox, oy = 420, 70
    rng = random.Random(9)
    chart = "".join(f'<circle cx="{ox + rr * math.cos(a):.1f}" cy="{oy + rr * math.sin(a):.1f}" r="{rng.choice([.5, .7, .9, 1.2])}" fill="{ink}"/>'
                    for a, rr in ((rng.uniform(0, 6.3), 15 * math.sqrt(rng.random())) for _ in range(28)))
    oculus = (f'<circle cx="{ox}" cy="{oy}" r="22" fill="{T["bg"]}" stroke="{ink}" stroke-width="1.3"/>'
              f'<circle cx="{ox}" cy="{oy}" r="17.5" fill="none" stroke="{ink}" stroke-width=".5"/>' + ticks(T, ox, oy, 22, 10, 30, 2, 4, width=.6) +
              f'<clipPath id="oculus"><circle cx="{ox}" cy="{oy}" r="17"/></clipPath><g clip-path="url(#oculus)"><g>{spin(ox, oy, 120)}{chart}'
              f'<ellipse cx="{ox}" cy="{oy}" rx="16" ry="6" transform="rotate(-20 {ox} {oy})" fill="none" stroke="{acc}" stroke-width=".7" stroke-dasharray="2 1.5"/></g></g>')
    dentils = "".join(f'<rect x="{x}" y="112" width="6" height="6" fill="{ink}"/>' for x in range(46, 792, 11))
    triglyph = lambda x: "".join(f'<line x1="{x + k * 5}" y1="122" x2="{x + k * 5}" y2="146" stroke="{ink}" stroke-width="1.1"/>' for k in range(3))
    body = (
        f'<path d="M40 100 L420 22 L800 100 Z" fill="{T["bg"]}" stroke="{ink}" stroke-width="1.5"/>'
        f'<path d="M74 94 L420 33 L766 94 Z" fill="url(#fine)" stroke="{ink}" stroke-width=".6" opacity=".55"/>'
        f'<path d="M74 94 L420 33 L766 94 Z" fill="none" stroke="{ink}" stroke-width=".6"/>' + oculus +
        f'<rect x="32" y="100" width="776" height="11" fill="{T["bg"]}" stroke="{ink}" stroke-width="1.2"/>' + dentils +
        f'<rect x="44" y="119" width="752" height="30" fill="{T["bg"]}" stroke="{ink}" stroke-width="1"/>'
        + triglyph(84) + triglyph(744) +
        f'<text x="420" y="139" text-anchor="middle" font-family="{SERIF}" font-size="12.5" letter-spacing="6" fill="{ink}">OPEN SOURCE  ·  MACOS &amp; IOS  ·  INDIA</text>'
        f'<rect x="44" y="149" width="752" height="7" fill="{T["bg"]}" stroke="{ink}" stroke-width=".8"/>'
        f'<rect x="44" y="156" width="752" height="6" fill="{T["bg"]}" stroke="{ink}" stroke-width=".8"/>'
        + column(T, 92, 162, 386) + column(T, 748, 162, 386) +
        f'<rect x="36" y="400" width="768" height="9" fill="url(#fine)" stroke="{ink}" stroke-width="1" opacity=".9"/>'
        f'<rect x="28" y="409" width="784" height="9" fill="{T["bg"]}" stroke="{ink}" stroke-width="1"/>'
        f'<rect x="20" y="418" width="800" height="9" fill="url(#fine)" stroke="{ink}" stroke-width="1" opacity=".9"/>'
        f'<g>{intro("opacity", 0, 1, .2, 1.4)}'
        f'<text x="420" y="246" text-anchor="middle" font-family="{SERIF}" font-size="52" fill="{ink}">Swastik Tripathi</text>'
        f'<line x1="330" y1="270" x2="404" y2="270" stroke="{ink}" stroke-width=".8"/><path d="M412 270 l8 -6 l8 6 l-8 6 z" fill="{acc}"/>'
        f'<line x1="436" y1="270" x2="510" y2="270" stroke="{ink}" stroke-width=".8"/>'
        f'<text x="420" y="308" text-anchor="middle" font-family="{SERIF}" font-style="italic" font-size="18" fill="{ink}">'
        f'{len(merged)} pull requests merged across {len(projects)} open-source projects.</text>'
        f'<text x="420" y="344" text-anchor="middle" font-family="{SERIF}" font-size="11" letter-spacing="3.4" fill="{T["muted"]}">'
        + f"SINCE {roman(since)}  ·  ANNO {roman(NOW.year)}".upper() + '</text></g>')
    return {"w": W, "h": H, "body": body, "defs": "", "label": "Swastik Tripathi"}


def ledger_book(T, d):
    """Plate II: entries in a ruled account book with a red margin, each inked in on load."""
    W, H = 840, 318
    ink = T["ink"]
    merged, repos = d["merged"], d["repos"]
    projects = {p["repo"] for p in merged}
    decided = len(merged) + len(d["unmerged"])
    entries = [("PRs merged", str(len(merged))), ("Projects", str(len(projects))),
               ("Stars across them", big(sum(repos[r]["stars"] for r in projects))),
               ("Merge rate", f"{round(100 * len(merged) / decided)}%" if decided else "-"),
               ("Week streak", str(week_streak(merged))), ("First merge", min(p["when"] for p in merged)[:4])]
    out = [heading(T, 32, 44, "The ledger", plate=2)]
    out += [f'<line x1="28" y1="{y}" x2="812" y2="{y}" stroke="{T["faint"]}" stroke-width=".8"/>' for y in range(96, 214, 36)]
    out.append(f'<line x1="66" y1="74" x2="66" y2="214" stroke="{T["accent"]}" stroke-width=".8"/><line x1="69" y1="74" x2="69" y2="214" stroke="{T["accent"]}" stroke-width=".8"/>')
    for i, (label, value) in enumerate(entries):
        col, row = i // 3, i % 3
        x0, x1 = (84, 404) if col == 0 else (452, 796)
        y = 126 + row * 36
        lead0, lead1 = x0 + len(label) * 8.2 + 10, x1 - len(value) * 13 - 10
        out.append(f'<clipPath id="ink{i}"><rect x="{x0 - 4}" y="{y - 30}" width="{x1 - x0 + 8}" height="40">'
                   f'{intro("width", 0, x1 - x0 + 8, .2 + i * .18, .9)}</rect></clipPath>'
                   f'<g clip-path="url(#ink{i})"><text x="{x0}" y="{y}" font-family="{SERIF}" font-style="italic" font-size="17" fill="{ink}">{esc(label)}</text>'
                   f'<line x1="{lead0:.0f}" y1="{y - 1}" x2="{lead1:.0f}" y2="{y - 1}" stroke="{ink}" stroke-width="1.3" stroke-dasharray=".1 5" stroke-linecap="round"/>'
                   f'<text x="{x1}" y="{y + 1}" text-anchor="end" font-family="{SERIF}" font-size="25" fill="{T["accent"] if i == 0 else ink}">{esc(value)}</text></g>')
    langs = Counter(repos[p["repo"]]["lang"] or "Other" for p in merged)
    total = sum(langs.values())
    out.append(f'<text x="32" y="246" font-family="{SERIF}" font-size="11" letter-spacing="2.6" fill="{T["muted"]}">PROPORTION OF THE WORK, BY LANGUAGE</text>')
    x, fills = 32.0, ["url(#hatch)", "url(#hatchA)", T["accent2"], T["muted"], T["faint"], T["ink"]]
    legend = []
    for j, (lang, n) in enumerate(langs.most_common(6)):
        w = 776 * n / total
        out.append(f'<rect x="{x:.1f}" y="258" width="{max(w - 3, 1.5):.1f}" height="10" fill="{fills[j]}" stroke="{ink}" stroke-width=".7">'
                   f'{intro("width", 0, f"{max(w - 3, 1.5):.1f}", 1.4 + j * .08)}</rect>')
        x += w
        legend.append(f"{lang} {round(100 * n / total)}%")
    out.append(f'<text x="32" y="292" font-family="{SERIF}" font-style="italic" font-size="13" fill="{T["muted"]}">{esc("  ·  ".join(legend))}</text>')
    return {"w": W, "h": H, "body": "".join(out), "defs": "", "label": "The ledger"}


def register(T, rows, title, sub, plate, stamps):
    """A hand-ruled register: date, project, pull request, and a rubber stamp."""
    W = 840
    ink = T["ink"]
    H = 120 + len(rows) * 34 + 16
    out = [heading(T, 32, 44, title, sub, plate=plate)]
    cols = [(34, "DATE"), (112, "PROJECT"), (268, "PULL REQUEST")]
    out += [f'<text x="{x}" y="98" font-family="{SERIF}" font-size="10.5" letter-spacing="2.4" fill="{T["muted"]}">{t}</text>' for x, t in cols]
    out.append(f'<line x1="28" y1="106" x2="812" y2="106" stroke="{ink}" stroke-width=".9"/><line x1="28" y1="109" x2="812" y2="109" stroke="{ink}" stroke-width=".4"/>'
               f'<line x1="100" y1="84" x2="100" y2="{H - 14}" stroke="{T["accent"]}" stroke-width=".7"/>')
    rng = random.Random(plate)
    for i, (date, project, text, status) in enumerate(rows):
        y = 136 + i * 34
        sw = max(92, len(status) * 8.6 + 22)
        room = int((808 - sw - 290) / 6.6)
        text = text if len(text) <= room else text[:room - 2].rsplit(" ", 1)[0] + "…"
        out.append(f'<line x1="28" y1="{y + 11}" x2="812" y2="{y + 11}" stroke="{T["faint"]}" stroke-width=".7"/>'
                   f'<clipPath id="row{plate}{i}"><rect x="28" y="{y - 22}" width="680" height="32">{intro("width", 0, 680, .3 + i * .16, .8)}</rect></clipPath>'
                   f'<g clip-path="url(#row{plate}{i})">'
                   f'<text x="34" y="{y}" font-family="{SERIF}" font-style="italic" font-size="13.5" fill="{T["muted"]}">{esc(date)}</text>'
                   f'<text x="112" y="{y}" font-family="{SERIF}" font-size="14" fill="{ink}">{esc(project[:20])}</text>'
                   f'<text x="268" y="{y}" font-family="{SERIF}" font-size="13.5" fill="{ink}">{esc(text)}</text></g>')
        if status:
            color = stamps.get(status, T["accent"])
            tilt = rng.uniform(-5, 5)
            sx, mid = 808 - sw, 808 - sw / 2
            out.append(f'<g transform="rotate({tilt:.1f} {mid:.1f} {y - 5})" opacity=".85">{intro("opacity", 0, 1, .7 + i * .16, .25)}'
                       f'<rect x="{sx:.1f}" y="{y - 17}" width="{sw:.1f}" height="22" rx="3" fill="none" stroke="{color}" stroke-width="1.5"/>'
                       f'<rect x="{sx + 3:.1f}" y="{y - 14}" width="{sw - 6:.1f}" height="16" rx="2" fill="none" stroke="{color}" stroke-width=".5"/>'
                       f'<text x="{mid:.1f}" y="{y - 2}" text-anchor="middle" font-family="{SERIF}" font-size="10.5" letter-spacing="2" fill="{color}">{esc(status)}</text></g>')
    return {"w": W, "h": H, "body": "".join(out), "defs": "", "label": title}


def register_arrivals(T, d):
    rows = [(dt.datetime.fromisoformat(p["when"].replace("Z", "+00:00")).strftime("%-d %b"),
             short(d["repos"][p["repo"]]["name"]), headline(p["title"]), "MERGED") for p in d["merged"][:7]]
    return register(T, rows, "Register of merged pull requests", "The latest merged into other projects.", 5, {})


def register_departures(T, d):
    rows = [(dt.datetime.fromisoformat(p["created"].replace("Z", "+00:00")).strftime("%-d %b"), short(d["repos"][p["repo"]]["name"]),
             headline(p["title"]), p.get("state", "OPEN")) for p in review_queue(d)]
    return register(T, rows or [("", "", "None open right now.", "")], "Register of open pull requests",
                    "Waiting on maintainers, with the review status GitHub shows.", 6, state_colors(T))


def visitors_chart(T, visitors):
    """Plate VIII: the visitors' stars inside a planisphere."""
    W, H = 840, 500
    ink, cx, cy, R = T["ink"], 420, 282, 168
    rng = random.Random(23)
    dots = "".join(f'<circle cx="{cx + rr * math.cos(a):.1f}" cy="{cy + rr * math.sin(a):.1f}" r="{rng.choice([.5, .7, .9, 1.2, 1.6])}" fill="{ink}" opacity=".7"/>'
                   for a, rr in ((rng.uniform(0, 6.3), (R - 14) * math.sqrt(rng.random())) for _ in range(90)))
    shown = visitors[-40:]
    pts = []
    for v in shown:
        h = hashlib.sha256(v["login"].lower().encode()).digest()
        a = int.from_bytes(h[:2], "big") / 65535 * 2 * math.pi
        rr = 24 + int.from_bytes(h[2:4], "big") / 65535 * (R - 50)
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    stars = []
    for v, (x, y) in zip(shown, pts):
        owner = v["login"].lower() == ME.lower()
        s = 6.5 if owner else 4.5
        color = T["accent"] if owner else ink
        anchor, lx = ("end", x - 9) if x > cx + 40 else ("start", x + 9)
        note = v.get("message") if v.get("approved") else ""
        stars.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{s * .55:.1f}" fill="{T["bg"]}" stroke="{color}" stroke-width="1.2"/>'
                     f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{s * .22:.1f}" fill="{color}"/>'
                     f'<text x="{lx:.1f}" y="{y + 4:.1f}" text-anchor="{anchor}" font-family="{SERIF}" font-size="11.5" fill="{ink}">{esc(v["login"])}</text>'
                     + (f'<text x="{lx:.1f}" y="{y + 17:.1f}" text-anchor="{anchor}" font-family="{SERIF}" font-style="italic" font-size="10.5" fill="{T["muted"]}">'
                        f'“{esc(note[:34] + ("…" if len(note) > 34 else ""))}”</text>' if note else ""))
    figure = (f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}" fill="none" stroke="{ink}" stroke-width=".8"/>' if len(pts) > 1 else "")
    n = len(visitors) - 1
    sub = f"{n} visitor{'s have' if n != 1 else ' has'} left a star. Leave yours, with a note." if n > 0 else "No visitors yet. Leave the first star, and a note."
    body = (heading(T, 32, 44, "Constellation of visitors", sub, plate=9) +
            f'<circle cx="{cx}" cy="{cy}" r="{R}" fill="none" stroke="{ink}" stroke-width="2"/>'
            f'<circle cx="{cx}" cy="{cy}" r="{R - 14}" fill="none" stroke="{ink}" stroke-width=".7"/>' + ticks(T, cx, cy, R, 5, 15, 5, 13, width=.9) +
            "".join(f'<text x="{cx + (R + 18) * math.cos(math.radians(a - 90)):.1f}" y="{cy + (R + 18) * math.sin(math.radians(a - 90)) + 4.5:.1f}" '
                    f'text-anchor="middle" font-family="{SERIF}" font-size="12" fill="{ink}">{roman(a // 30 + 1)}</text>' for a in range(0, 360, 30)) +
            f'<clipPath id="vdisc"><circle cx="{cx}" cy="{cy}" r="{R - 15}"/></clipPath><g clip-path="url(#vdisc)">'
            + "".join(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{T["faint"]}" stroke-width=".7"/>' for r in (50, 100))
            + "".join(f'<line x1="{cx}" y1="{cy}" x2="{cx + R * math.cos(math.radians(a)):.1f}" y2="{cy + R * math.sin(math.radians(a)):.1f}" stroke="{T["faint"]}" stroke-width=".7"/>' for a in range(0, 360, 30))
            + f'<g>{spin(cx, cy, 500)}{dots}</g>'
            f'<ellipse cx="{cx}" cy="{cy}" rx="{R - 16}" ry="58" transform="rotate(-22 {cx} {cy})" fill="none" stroke="{T["accent"]}" stroke-width="1" stroke-dasharray="5 3.5"/>'
            + figure + "".join(stars) + '</g>')
    return {"w": W, "h": H, "body": body, "defs": "", "label": f"Visitors' constellation: {n} visitors"}


def colophon(T):
    W, H, cx, cy = 840, 196, 420, 70
    ink = T["ink"]
    pts = []
    for k in range(16):
        a = math.radians(k * 22.5 - 90)
        r = 44 if k % 4 == 0 else 28 if k % 2 == 0 else 15
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    rose = []
    for k in range(0, 16, 2):  # each point split into a light and a dark half, like an engraving
        tip, left, right = pts[k], pts[k - 1], pts[(k + 1) % 16]
        rose.append(f'<path d="M{cx} {cy} L{tip[0]:.1f} {tip[1]:.1f} L{left[0]:.1f} {left[1]:.1f} Z" fill="{ink if k % 4 == 0 else "url(#fine)"}" stroke="{ink}" stroke-width=".7"/>'
                    f'<path d="M{cx} {cy} L{tip[0]:.1f} {tip[1]:.1f} L{right[0]:.1f} {right[1]:.1f} Z" fill="{T["bg"]}" stroke="{ink}" stroke-width=".7"/>')
    body = (f'<circle cx="{cx}" cy="{cy}" r="54" fill="none" stroke="{ink}" stroke-width="1.1"/>' + ticks(T, cx, cy, 54, 10, 90, 3, 7, width=.7) +
            f'<g><animateTransform attributeName="transform" type="rotate" values="-5 {cx} {cy};5 {cx} {cy};-5 {cx} {cy}" dur="9s" repeatCount="indefinite"/>'
            + "".join(rose) + f'<circle cx="{cx}" cy="{cy}" r="3" fill="{T["accent"]}"/></g>'
            f'<text x="{cx}" y="{cy - 60}" text-anchor="middle" font-family="{SERIF}" font-size="11" fill="{ink}">N</text>'
            f'<text x="{cx}" y="156" text-anchor="middle" font-family="{SERIF}" font-style="italic" font-size="14" fill="{ink}">'
            f'Printed every six hours from the GitHub API.</text>'
            f'<text x="{cx}" y="178" text-anchor="middle" font-family="{SERIF}" font-size="10.5" letter-spacing="2.8" fill="{T["muted"]}">'
            + f"LAST IMPRESSION  ·  {NOW.day} {NOW:%B} {roman(NOW.year)}".upper() + '</text>')
    return {"w": W, "h": H, "body": body, "defs": "", "label": "Colophon"}


def slab(T, part):
    """A folio part as a standalone plate."""
    return frame(T, part["w"], part["h"], part["body"], part["label"], part["defs"])


def fleuron(T):
    ink, acc = T["ink"], T["accent"]
    body = (f'<line x1="250" y1="22" x2="392" y2="22" stroke="{ink}" stroke-width=".7"/><line x1="448" y1="22" x2="590" y2="22" stroke="{ink}" stroke-width=".7"/>'
            + "".join(f'<path d="M{x} 22 l{s} -{s} l{s} {s} l-{s} {s} z" fill="{acc if x == 413 else ink}"/>' for x, s in ((398, 5), (413, 7), (432, 5))))
    return {"w": 840, "h": 44, "body": body, "defs": "", "label": ""}


def folio(T, d, visitors):
    parts = [facade(T, d), fleuron(T), ledger_book(T, d), fleuron(T), orrery(T, d), fleuron(T), workshop(T, d), fleuron(T),
             register_arrivals(T, d), register_departures(T, d), fleuron(T), milestones(T, d), almanac(T, d),
             fleuron(T), visitors_chart(T, visitors), colophon(T)]
    W, y, top = 840, 30, 30
    body, defs = [], []
    for p in parts:
        body.append(f'<g transform="translate(0 {y})">{p["body"]}</g>')
        defs.append(p["defs"])
        y += p["h"]
    H = y + 24
    ink = T["ink"]
    corner = lambda x, y, sx, sy: (f'<path d="M{x} {y + sy * 34} L{x} {y} L{x + sx * 34} {y}" fill="none" stroke="{ink}" stroke-width="1.2"/>'
                                   f'<path d="M{x + sx * 6} {y + sy * 6} m{-sx * 4} 0 l{sx * 4} {-sy * 4} l{sx * 4} {sy * 4} l{-sx * 4} {sy * 4} z" fill="{T["accent"]}"/>')
    border = (f'<rect x="8.5" y="8.5" width="{W - 17}" height="{H - 17}" fill="none" stroke="{ink}" stroke-width="1.6"/>'
              f'<rect x="13.5" y="13.5" width="{W - 27}" height="{H - 27}" fill="none" stroke="{ink}" stroke-width=".5"/>'
              + corner(20, 20, 1, 1) + corner(W - 20, 20, -1, 1) + corner(20, H - 20, 1, -1) + corner(W - 20, H - 20, -1, -1))
    label = (f"Swastik Tripathi. {len(d['merged'])} pull requests merged across {len({p['repo'] for p in d['merged']})} open-source projects. "
             "An engraved folio: ledger, orrery of projects, registers of merged and pending pull requests, milestones, almanac and visitors.")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="{esc(label)}">'
            f'<title>{esc(label)}</title><defs>{defs_common(T)}{FINE_HATCH}{"".join(defs)}</defs>'
            f'<rect width="{W}" height="{H}" fill="{T["bg"]}"/>{"".join(body)}'
            f'<rect width="{W}" height="{H}" filter="url(#paper)" opacity=".9"/>{border}</svg>\n')


# ---------------------------------------------------------------- README

def STRIP(T):
    """The sheet's link row: (key, href, alt, percent of the width). Percentages add up to 100."""
    return [("web", SITE, "swastiktripathi.dev", 27), ("linkedin", LINKEDIN, "LinkedIn", 25),
            ("x", "https://x.com/" + X_HANDLE, "X", 24), ("github", f"https://github.com/{ME}", "GitHub", 24)]


def readme(T, d, visitors, prefix, active):
    merged, repos = d["merged"], d["repos"]
    projects = Counter(p["repo"] for p in merged)
    a = lambda name: f"{prefix}assets/{T['name']}/{name}"
    issue = (f"https://github.com/{ME}/{ME}/issues/new?title=%E2%9C%A8+Leave+a+star&body="
             + urllib.request.quote(PREFILL + "\n\n"))
    merged_search = f"https://github.com/search?q=author%3A{ME}+is%3Apr+is%3Amerged+-user%3A{ME}&type=pullrequests"
    repos_tab = f"https://github.com/{ME}?tab=repositories"
    open_search = f"https://github.com/search?q=author%3A{ME}+is%3Apr+is%3Aopen+-user%3A{ME}&type=pullrequests"
    av = lambda slug, s=40: f"https://github.com/{repos[slug]['owner']}.png?size={s}"
    day = lambda iso: dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%b %-d")
    cell = lambda t: t.replace("|", "\\|").replace("<", "&lt;")
    merged_rows = "\n".join(f'| <img src="{av(p["repo"])}" width="18" height="18"> | {repos[p["repo"]]["name"]} | '
                       f'[{cell(p["title"])}]({p["url"]}) | {day(p["when"])} |' for p in merged[:7])
    open_rows = "\n".join(f'| <img src="{av(p["repo"])}" width="18" height="18"> | {repos[p["repo"]]["name"]} | '
                          f'[{cell(p["title"])}]({p["url"]}) | {day(p["created"])} | {p.get("state", "OPEN").capitalize()} |'
                          for p in review_queue(d))
    notes = [v for v in visitors if v.get("approved") and v.get("message")][-5:]
    notes_md = ("\n".join(f'> “{html.escape(v["message"])}”<br><sub>[{v["login"]}](https://github.com/{v["login"]})</sub>\n'
                          for v in reversed(notes)) if notes else "")
    pills = " ".join(f'<a href="{link}"><img src="{a(f"link-{key}.svg")}" alt="{alt}" height="32"></a>' for key, link, alt in [
        ("web", SITE, "swastiktripathi.dev"), ("linkedin", LINKEDIN, "LinkedIn"),
        ("x", "https://x.com/" + X_HANDLE, "X"), ("github", f"https://github.com/{ME}", "GitHub")])
    up = "../" if prefix else ""
    editions = " and ".join(f'<a href="{up}{"README.md" if n == active else f"alt/README-{n}.md"}">{n}</a>' for n in THEMES if n != T["name"])
    boards = f"""<details>
<summary><b>Open the boards</b>: every pull request above, with links</summary>
<br>

**Recently merged**

| | Project | Pull request | Merged |
|:-:|---|---|---|
{merged_rows}

**Open pull requests**

| | Project | Pull request | Opened | Review |
|:-:|---|---|---|---|
{open_rows or "| | | None open right now. | | |"}

</details>"""
    if T.get("sheet") and not T.get("folio"):
        # one <p>, every image align="top" (no baseline gap) and joined by <br>: the slices meet edge to edge
        img = lambda name, alt, w="100%": f'<img src="{a(name)}" width="{w}" align="top" alt="{alt}">'
        link = lambda href, inner: f'<a href="{href}">{inner}</a>'
        strip = "".join(link(href, img(f"strip-{key}.svg", alt, f"{pct}%")) for key, href, alt, pct in STRIP(T))
        rows = [link(SITE, img("hero.svg", f"Swastik Tripathi. {len(merged)} pull requests merged across {len(projects)} open-source projects.")),
                strip,
                link(merged_search, img("ledger.svg", f"{len(merged)} PRs merged across {len(projects)} projects")),
                link(merged_search, img("orrery.svg", f"An orrery of the {len(projects)} projects that merged my pull requests")),
                link(repos_tab, img("workshop.svg", "The workshop: my own repos")),
                link(merged_search, img("merged.svg", "Recently merged pull requests")),
                link(open_search, img("open.svg", "Open pull requests and their review status")),
                img("milestones.svg", "Milestones"),
                link(merged_search, img("almanac.svg", "Merged pull requests per week")),
                link(issue, img("visitors.svg", "Visitors' constellation: click to leave a star"))]
        sections = '<p align="center">' + "<br>\n".join(rows) + "</p>\n\n" + boards
    elif T.get("folio"):
        sections = f"""<a href="{merged_search}"><img src="{a('folio.svg')}" width="100%" alt="An engraved folio: Swastik Tripathi's open-source work"></a>

{boards}"""
    else:
        sections = f"""<a href="{merged_search}"><img src="{a('ledger.svg')}" width="100%" alt="{len(merged)} PRs merged across {len(projects)} projects"></a>

<a href="{merged_search}"><img src="{a('orrery.svg')}" width="100%" alt="An orrery of the {len(projects)} projects that merged my pull requests"></a>

<a href="{repos_tab}"><img src="{a('workshop.svg')}" width="100%" alt="The workshop: my own repos"></a>

<a href="{merged_search}"><img src="{a('merged.svg')}" width="100%" alt="Recently merged pull requests"></a>

<a href="{open_search}"><img src="{a('open.svg')}" width="100%" alt="Open pull requests and their review status"></a>

{boards}

<img src="{a('milestones.svg')}" width="100%" alt="Milestones">

<a href="{merged_search}"><img src="{a('almanac.svg')}" width="100%" alt="Merged pull requests per week"></a>

<a href="{issue}"><img src="{a('visitors.svg')}" width="100%" alt="Visitors' constellation: every star is someone who stopped by"></a>"""
    top = "" if T.get("folio") or T.get("sheet") else f"""<a href="{SITE}"><img src="{a('hero.svg')}" width="100%" alt="Swastik Tripathi. {len(merged)} pull requests merged across {len(projects)} open-source projects."></a>

"""
    star_badge = f'https://img.shields.io/badge/Leave_a_star-{T["accent"].lstrip("#")}?style=flat-square&logo=githubsponsors&logoColor=white'
    if T.get("sheet") and not T.get("folio"):
        pills, button, how = "", '<p align="center">', "Leave a star: click the constellation above"
    else:
        pills = f'<p align="center">{pills}</p>\n\n'
        button = f'<p align="center"><a href="{issue}"><img src="{star_badge}" alt="Leave a star" height="26"></a><br>\n'
        how = "Click"
    return f"""<!-- Generated by build/build.py ({T['name']} edition). Edit that, not this file: it is rebuilt every 6 hours. -->
{top}{pills}{sections}

{button}<sub>{how}, write a short note if you like, and press <b>Create</b>. A GitHub Action puts your star in the sky within a minute; notes appear once I've read them.</sub></p>

{notes_md}
<p align="center"><sub>Rebuilt every six hours from the GitHub API by <a href="{prefix}build/build.py">build/build.py</a> · last run {NOW:%b %-d, %Y, %H:%M} UTC · also in the {editions} editions</sub></p>
"""


# ---------------------------------------------------------------- main

def read_theme():
    try:
        t = open(os.path.join(DATA, "theme.txt")).read().strip()
    except OSError:
        t = "classy"
    return t if t in THEMES else "classy"


def load_visitors():
    try:
        v = json.load(open(os.path.join(DATA, "sky.json")))
    except (OSError, ValueError):
        v = []
    if not any(x["login"].lower() == ME.lower() for x in v):
        v.insert(0, {"login": ME, "at": NOW.isoformat(timespec="seconds")})
    return v


def arg(flag):
    if flag not in sys.argv:
        return None
    login = sys.argv[sys.argv.index(flag) + 1].strip()
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", login):
        raise SystemExit(f"not a GitHub login: {login!r}")
    return login


def main():
    os.makedirs(DATA, exist_ok=True)
    visitors = load_visitors()
    find = lambda login: next((x for x in visitors if x["login"].lower() == login.lower()), None)
    login = arg("--add-star")
    if login:
        # the issue body comes in through an env var, never the command line
        msg = clean_message(os.environ.get("STAR_MESSAGE", ""))
        v = find(login)
        if v:
            if msg and not v.get("approved"):
                v["message"] = msg
            print("already-there" + ("-with-note" if msg else ""))
        else:
            visitors.append({"login": login, "at": NOW.isoformat(timespec="seconds"), "message": msg, "approved": False})
            print("added" + ("-with-note" if msg else ""))
    login = arg("--approve")
    if login:
        v = find(login)
        if v and v.get("message"):
            v["approved"] = True
            print("approved")
        else:
            print("nothing-to-approve")
    json.dump(visitors, open(os.path.join(DATA, "sky.json"), "w"), indent=1)
    d = collect()
    active = read_theme()
    os.makedirs(os.path.join(ROOT, "alt"), exist_ok=True)
    for name, T in THEMES.items():
        folder = os.path.join(ROOT, "assets", name)
        os.makedirs(folder, exist_ok=True)
        if T.get("folio"):
            files = {"folio.svg": folio(T, d, visitors)}
        else:
            files = {"hero.svg": hero(T, d), "ledger.svg": ledger(T, d), "orrery.svg": orrery(T, d),
                     "workshop.svg": workshop(T, d),
                     "merged.svg": slab(T, register_arrivals(T, d)) if T.get("sheet") else merged_board(T, d),
                     "open.svg": slab(T, register_departures(T, d)) if T.get("sheet") else open_board(T, d), "milestones.svg": milestones(T, d),
                     "almanac.svg": almanac(T, d), "visitors.svg": visitors_svg(T, visitors)}
        if not T.get("sheet") or T.get("folio"):
            files |= {"link-web.svg": pill(T, "web", "swastiktripathi.dev"), "link-linkedin.svg": pill(T, "linkedin", "swastik-tripathi"),
                      "link-x.svg": pill(T, "x", "@" + X_HANDLE), "link-github.svg": pill(T, "github", "@" + ME)}
        if T.get("sheet") and not T.get("folio"):
            texts = {"web": "swastiktripathi.dev", "linkedin": "swastik-tripathi", "x": "@" + X_HANDLE, "github": "@" + ME}
            strip = STRIP(T)
            for i, (key, _, _, pct) in enumerate(strip):
                sides = ("l" if i == 0 else "") + ("r" if i == len(strip) - 1 else "")
                files[f"strip-{key}.svg"] = pill(T, key, texts[key], strip=(round(840 * pct / 100, 1), 50), sides=sides)
        for fname, text in files.items():
            open(os.path.join(folder, fname), "w").write(text)
        if name == active:
            open(os.path.join(ROOT, "README.md"), "w").write(readme(T, d, visitors, "", active))
        else:
            open(os.path.join(ROOT, "alt", f"README-{name}.md"), "w").write(readme(T, d, visitors, "../", active))


if __name__ == "__main__":
    main()
