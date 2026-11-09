"""The real sky over a place at a moment, drawn as a planisphere in SVG.

Star positions and constellation lines: data/stars.json (from d3-celestial, BSD-3-Clause).
Sun, Moon and planets: Paul Schlyter's low-precision orbital elements ("How to compute planetary positions"),
good to about a degree, which is far finer than a pixel on this chart.
"""
import json, math, os

R2D, D2R = 180 / math.pi, math.pi / 180
sind = lambda x: math.sin(x * D2R)
cosd = lambda x: math.cos(x * D2R)
atan2d = lambda y, x: math.atan2(y, x) * R2D
asind = lambda x: math.asin(max(-1, min(1, x))) * R2D

_CAT = None


def catalogue():
    global _CAT
    if _CAT is None:
        _CAT = json.load(open(os.path.join(os.path.dirname(__file__), "..", "data", "stars.json")))
    return _CAT


def day_number(t):
    """Days since 2000 Jan 0.0 UT (Schlyter's d)."""
    jd = t.timestamp() / 86400 + 2440587.5
    return jd - 2451543.5


def obliquity(d):
    return 23.4393 - 3.563e-7 * d


def ecl_to_eq(lon, lat, d):
    e = obliquity(d)
    x = cosd(lon) * cosd(lat)
    y = sind(lon) * cosd(lat) * cosd(e) - sind(lat) * sind(e)
    z = sind(lon) * cosd(lat) * sind(e) + sind(lat) * cosd(e)
    return atan2d(y, x) % 360, asind(z)


def kepler(M, e):
    E = M + e * R2D * sind(M) * (1 + e * cosd(M))
    for _ in range(8):
        E = E - (E - e * R2D * sind(E) - M) / (1 - e * cosd(E))
    return E


ELEMENTS = {  # N, i, w, a, e, M as (constant, rate per day)
    "Mercury": ((48.3313, 3.24587e-5), (7.0047, 5.0e-8), (29.1241, 1.01444e-5), 0.387098, (0.205635, 5.59e-10), (168.6562, 4.0923344368)),
    "Venus": ((76.6799, 2.46590e-5), (3.3946, 2.75e-8), (54.8910, 1.38374e-5), 0.723330, (0.006773, -1.302e-9), (48.0052, 1.6021302244)),
    "Mars": ((49.5574, 2.11081e-5), (1.8497, -1.78e-8), (286.5016, 2.92961e-5), 1.523688, (0.093405, 2.516e-9), (18.6021, 0.5240207766)),
    "Jupiter": ((100.4542, 2.76854e-5), (1.3030, -1.557e-7), (273.8777, 1.64505e-5), 5.20256, (0.048498, 4.469e-9), (19.8950, 0.0830853001)),
    "Saturn": ((113.6634, 2.38980e-5), (2.4886, -1.081e-7), (339.3939, 2.97661e-5), 9.55475, (0.055546, -9.499e-9), (316.9670, 0.0334442282)),
}


def sun(d):
    w = 282.9404 + 4.70935e-5 * d
    e = 0.016709 - 1.151e-9 * d
    M = (356.0470 + 0.9856002585 * d) % 360
    E = kepler(M, e)
    x, y = cosd(E) - e, math.sqrt(1 - e * e) * sind(E)
    v, r = atan2d(y, x), math.hypot(x, y)
    lon = (v + w) % 360
    return lon, r, M


def helio(name, d):
    (N0, N1), (i0, i1), (w0, w1), a, (e0, e1), (M0, M1) = ELEMENTS[name]
    N, i, w, e, M = N0 + N1 * d, i0 + i1 * d, w0 + w1 * d, e0 + e1 * d, (M0 + M1 * d) % 360
    E = kepler(M, e)
    x, y = a * (cosd(E) - e), a * math.sqrt(1 - e * e) * sind(E)
    v, r = atan2d(y, x), math.hypot(x, y)
    xh = r * (cosd(N) * cosd(v + w) - sind(N) * sind(v + w) * cosd(i))
    yh = r * (sind(N) * cosd(v + w) + cosd(N) * sind(v + w) * cosd(i))
    zh = r * sind(v + w) * sind(i)
    return xh, yh, zh


def planet(name, d):
    """Geocentric RA/Dec of a planet."""
    xh, yh, zh = helio(name, d)
    slon, sr, _ = sun(d)
    xg, yg, zg = xh + sr * cosd(slon), yh + sr * sind(slon), zh
    lon, lat = atan2d(yg, xg) % 360, atan2d(zg, math.hypot(xg, yg))
    return ecl_to_eq(lon, lat, d)


def moon(d):
    """Geocentric RA/Dec of the Moon (with the largest perturbations) and its elongation from the Sun."""
    N = 125.1228 - 0.0529538083 * d
    i, w, a, e = 5.1454, 318.0634 + 0.1643573223 * d, 60.2666, 0.054900
    M = (115.3654 + 13.0649929509 * d) % 360
    E = kepler(M, e)
    x, y = a * (cosd(E) - e), a * math.sqrt(1 - e * e) * sind(E)
    v, r = atan2d(y, x), math.hypot(x, y)
    xe = r * (cosd(N) * cosd(v + w) - sind(N) * sind(v + w) * cosd(i))
    ye = r * (sind(N) * cosd(v + w) + cosd(N) * sind(v + w) * cosd(i))
    ze = r * sind(v + w) * sind(i)
    lon, lat = atan2d(ye, xe), atan2d(ze, math.hypot(xe, ye))
    slon, _, Ms = sun(d)
    Lm, Ls = (N + w + M), slon
    D, F = Lm - Ls, Lm - N
    lon += (-1.274 * sind(M - 2 * D) + 0.658 * sind(2 * D) - 0.186 * sind(Ms) - 0.059 * sind(2 * M - 2 * D)
            - 0.057 * sind(M - 2 * D + Ms) + 0.053 * sind(M + 2 * D) + 0.046 * sind(2 * D - Ms) + 0.041 * sind(M - Ms)
            - 0.035 * sind(D) - 0.031 * sind(M + Ms))
    lat += -0.173 * sind(F - 2 * D) - 0.055 * sind(M - F - 2 * D) - 0.046 * sind(M + F - 2 * D)
    elong = math.acos(max(-1, min(1, cosd(lon - slon) * cosd(lat)))) * R2D
    ra, dec = ecl_to_eq(lon % 360, lat, d)
    return ra, dec, elong, r  # r in Earth radii


class Sky:
    def __init__(self, when, lat, lon):
        self.d = day_number(when)
        self.lat = lat
        jd = when.timestamp() / 86400 + 2440587.5
        self.lst = (280.46061837 + 360.98564736629 * (jd - 2451545) + lon) % 360

    def altaz(self, ra, dec):
        H = self.lst - ra
        alt = asind(sind(self.lat) * sind(dec) + cosd(self.lat) * cosd(dec) * cosd(H))
        az = atan2d(-cosd(dec) * sind(H), sind(dec) * cosd(self.lat) - cosd(dec) * cosd(H) * sind(self.lat)) % 360
        return alt, az


def chart(T, cx, cy, R, when, lat, lon, glow=False):
    """SVG for the sky inside a horizon circle of radius R, looking up: north at the top, east on the left.
    Returns (svg, info) where info says whether the Sun was up."""
    sky = Sky(when, lat, lon)
    d = sky.d
    ink, muted, accent = T["ink"], T["muted"], T["accent"]

    def xy(alt, az, clamp=False):
        z = min(90 - alt, 172) if clamp else 90 - alt
        rho = R * math.tan(z * D2R / 2)
        return cx - rho * sind(az), cy - rho * cosd(az)

    slon, _, _ = sun(d)
    sra, sdec = ecl_to_eq(slon, 0, d)
    salt, saz = sky.altaz(sra, sdec)
    # how much daylight washes out the stars: full night below -12 degrees, full day above +4
    day = max(0.0, min(1.0, (salt + 12) / 16))
    out = []
    if day > 0:
        out.append(f'<circle cx="{cx}" cy="{cy}" r="{R}" fill="{T["accent2"]}" opacity="{.04 + .1 * day:.3f}"/>')
    # constellation lines
    cat = catalogue()
    segs = []
    for name, parts in cat["lines"].items():
        for part in parts:
            pts = [sky.altaz(ra, dec) for ra, dec in part]
            for (a1, z1), (a2, z2) in zip(pts, pts[1:]):
                if a1 > 0 and a2 > 0:
                    (x1, y1), (x2, y2) = xy(a1, z1), xy(a2, z2)
                    segs.append(f"M{x1:.1f} {y1:.1f}L{x2:.1f} {y2:.1f}")
    if segs:
        out.append(f'<path d="{"".join(segs)}" fill="none" stroke="{ink}" stroke-width=".55" opacity="{.5 - .3 * day:.2f}"/>')
    # stars, brightest drawn last
    limit = 5.0 - 2.6 * day
    for ra, dec, mag in sorted(cat["stars"], key=lambda s: -s[2]):
        if mag > limit:
            continue
        alt, az = sky.altaz(ra, dec)
        if alt <= 0:
            continue
        x, y = xy(alt, az)
        r = max(.45, min(2.9, 2.5 - .4 * mag))
        op = max(.35, min(1, 1.1 - .12 * mag)) * (1 - .45 * day)
        tw = ""
        if glow and mag < 1.6:
            tw = (f'<animate attributeName="opacity" values="{op:.2f};{op * .55:.2f};{op:.2f}" dur="{3 + (ra % 4):.1f}s" '
                  f'begin="-{dec % 3:.1f}s" repeatCount="indefinite"/>')
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.2f}" fill="{ink}" opacity="{op:.2f}">{tw}</circle>' if tw else
                   f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.2f}" fill="{ink}" opacity="{op:.2f}"/>')
    # the ecliptic: the Sun's path, where the Moon and planets also travel
    run, runs = [], []
    for k in range(0, 361, 3):
        ra, dec = ecl_to_eq(k, 0, d)
        alt, az = sky.altaz(ra, dec)
        if alt > -0.5:
            run.append(xy(max(alt, 0), az))
        elif run:
            runs.append(run); run = []
    if run:
        runs.append(run)
    if len(runs) > 1 and runs[0] and runs[-1] and math.dist(runs[0][0], runs[-1][-1]) < 12:
        runs[0] = runs.pop() + runs[0]
    for r_ in runs:
        if len(r_) > 1:
            out.append(f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in r_)}" fill="none" stroke="{accent}" '
                       f'stroke-width=".9" stroke-dasharray="4 3"/>')
    labels, taken = [], []

    def label(x, y, text, dy=-6):
        """Place a label above, below, right or left of a body; drop it if every spot is taken."""
        w, h = len(text) * 4.3 + 2, 9
        for lx, ly, anchor in ((x, y + dy, "middle"), (x, y - dy + 7, "middle"), (x + 7, y + 3, "start"), (x - 7, y + 3, "end")):
            x0 = lx - w / 2 if anchor == "middle" else (lx if anchor == "start" else lx - w)
            box = (x0, ly - 7, x0 + w, ly - 7 + h)
            inside = math.hypot((box[0] + box[2]) / 2 - cx, (box[1] + box[3]) / 2 - cy) < R - 12
            if inside and not any(box[0] < b[2] and b[0] < box[2] and box[1] < b[3] and b[1] < box[3] for b in taken):
                taken.append(box)
                labels.append(f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" font-family="Georgia, \'Times New Roman\', serif" '
                              f'font-style="italic" font-size="8.5" fill="{muted}">{text}</text>')
                return

    bodies = []
    # planets
    for name in ELEMENTS:
        ra, dec = planet(name, d)
        alt, az = sky.altaz(ra, dec)
        if alt > 0:
            x, y = xy(alt, az)
            out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.4" fill="{T["bg"]}" stroke="{accent}" stroke-width="1.1"/>'
                       f'<circle cx="{x:.1f}" cy="{y:.1f}" r=".9" fill="{accent}"/>')
            bodies.append((x, y, name, -6))
    # the Moon, lit on the side that faces the Sun
    mra, mdec, elong, mdist = moon(d)
    malt, maz = sky.altaz(mra, mdec)
    malt -= asind(cosd(malt) / mdist)  # parallax: seen from Bengaluru, not from Earth's centre
    # separation of Sun and Moon as seen from here (solar eclipse), and of the Moon from Earth's shadow (lunar)
    sep = math.acos(max(-1, min(1, sind(salt) * sind(malt) + cosd(salt) * cosd(malt) * cosd(saz - maz)))) * R2D
    solar = sep < 0.27 + 0.26 and salt > 0 and malt > 0
    lunar = 180 - elong < 0.75 + 0.26 and malt > 0  # umbra about 0.7 degrees across the Moon's path
    events = []
    if salt > 0:
        x, y = xy(salt, saz)
        rays = "".join(f'<line x1="{x + 7 * cosd(a):.1f}" y1="{y + 7 * sind(a):.1f}" x2="{x + 10.5 * cosd(a):.1f}" y2="{y + 10.5 * sind(a):.1f}" '
                       f'stroke="{accent}" stroke-width="1"/>' for a in range(0, 360, 45))
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="{accent}"/>{rays}')
        bodies.append((x, y, "Solar eclipse" if solar else "Sun", -13))
    if malt > 0:
        x, y = xy(malt, maz)
        sx, sy = xy(salt, saz, clamp=True)
        ang = math.atan2(sy - y, sx - x) * R2D  # direction to the Sun
        r = 5.2
        if solar:  # the Moon's dark disc over the Sun, offset by the true separation (the Sun is 5 px for 0.27 degrees)
            px = sep * 5 / 0.267
            out.append(f'<circle cx="{sx - px * cosd(ang):.1f}" cy="{sy - px * sind(ang):.1f}" r="{r * 0.98:.2f}" fill="{T["bg"]}" '
                       f'stroke="{ink}" stroke-width=".5"/>')
            events.append("solar eclipse")
        elif lunar:  # in Earth's shadow the Moon turns copper red
            out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="#b5523b" stroke="{ink}" stroke-width=".7"/>')
            bodies.insert(0, (x, y, "Lunar eclipse", -8.5))
            events.append("lunar eclipse")
        else:
            k = cosd(elong)  # 1 new, -1 full
            rx = abs(k) * r
            # lit part: half disc on the Sun side plus (gibbous) or minus (crescent) a half ellipse
            sweep = 1 if k < 0 else 0
            lit = (f'M0 {-r} A{r} {r} 0 0 1 0 {r} A{rx:.2f} {r} 0 0 {sweep} 0 {-r} Z')
            out.append(f'<g transform="translate({x:.1f} {y:.1f}) rotate({ang:.1f})">'
                       f'<circle r="{r}" fill="{T["bg"]}" stroke="{ink}" stroke-width=".7"/>'
                       f'<path d="{lit}" fill="{ink}"/></g>')
            bodies.insert(0, (x, y, "Moon", -8.5))
    bodies.sort(key=lambda b: b[2] not in ("Sun", "Solar eclipse", "Moon", "Lunar eclipse"))
    for x, y, name, _ in bodies:  # bodies keep their own space clear of labels
        k = {"Sun": 11, "Moon": 6}.get(name, 3.5)
        taken.append((x - k, y - k, x + k, y + k))
    for b in bodies:
        label(*b)
    # horizon letters, inside the disc
    for txt, az in (("N", 0), ("E", 90), ("S", 180), ("W", 270)):
        x, y = cx - (R - 9) * sind(az), cy - (R - 9) * cosd(az)
        out.append(f'<text x="{x:.1f}" y="{y + 3.2:.1f}" text-anchor="middle" font-family="Georgia, serif" font-size="9" '
                   f'letter-spacing="0" fill="{muted}">{txt}</text>')
    return "".join(out) + "".join(labels), {"sun_up": salt > 0, "sun_alt": salt, "events": events}
