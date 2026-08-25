#!/usr/bin/env python3
"""Regenerates the README architecture diagram (light + dark SVG).

    python3 scripts/gen_architecture_diagram.py

Self-contained: no dependencies, no network. The four brand glyphs below are
Simple Icons path data (CC0-1.0, https://github.com/simple-icons/simple-icons).
Edit the layout tables, re-run, and commit both SVGs.
"""

import html
import pathlib

OUT = pathlib.Path(__file__).resolve().parent.parent / "docs" / "assets"

PG = "M23.5594 14.7228a.5269.5269 0 0 0-.0563-.1191c-.139-.2632-.4768-.3418-1.0074-.2321-1.6533.3411-2.2935.1312-2.5256-.0191 1.342-2.0482 2.445-4.522 3.0411-6.8297.2714-1.0507.7982-3.5237.1222-4.7316a1.5641 1.5641 0 0 0-.1509-.235C21.6931.9086 19.8007.0248 17.5099.0005c-1.4947-.0158-2.7705.3461-3.1161.4794a9.449 9.449 0 0 0-.5159-.0816 8.044 8.044 0 0 0-1.3114-.1278c-1.1822-.0184-2.2038.2642-3.0498.8406-.8573-.3211-4.7888-1.645-7.2219.0788C.9359 2.1526.3086 3.8733.4302 6.3043c.0409.818.5069 3.334 1.2423 5.7436.4598 1.5065.9387 2.7019 1.4334 3.582.553.9942 1.1259 1.5933 1.7143 1.7895.4474.1491 1.1327.1441 1.8581-.7279.8012-.9635 1.5903-1.8258 1.9446-2.2069.4351.2355.9064.3625 1.39.3772a.0569.0569 0 0 0 .0004.0041 11.0312 11.0312 0 0 0-.2472.3054c-.3389.4302-.4094.5197-1.5002.7443-.3102.064-1.1344.2339-1.1464.8115-.0025.1224.0329.2309.0919.3268.2269.4231.9216.6097 1.015.6331 1.3345.3335 2.5044.092 3.3714-.6787-.017 2.231.0775 4.4174.3454 5.0874.2212.5529.7618 1.9045 2.4692 1.9043.2505 0 .5263-.0291.8296-.0941 1.7819-.3821 2.5557-1.1696 2.855-2.9059.1503-.8707.4016-2.8753.5388-4.1012.0169-.0703.0357-.1207.057-.1362.0007-.0005.0697-.0471.4272.0307a.3673.3673 0 0 0 .0443.0068l.2539.0223.0149.001c.8468.0384 1.9114-.1426 2.5312-.4308.6438-.2988 1.8057-1.0323 1.5951-1.6698zM2.371 11.8765c-.7435-2.4358-1.1779-4.8851-1.2123-5.5719-.1086-2.1714.4171-3.6829 1.5623-4.4927 1.8367-1.2986 4.8398-.5408 6.108-.13-.0032.0032-.0066.0061-.0098.0094-2.0238 2.044-1.9758 5.536-1.9708 5.7495-.0002.0823.0066.1989.0162.3593.0348.5873.0996 1.6804-.0735 2.9184-.1609 1.1504.1937 2.2764.9728 3.0892.0806.0841.1648.1631.2518.2374-.3468.3714-1.1004 1.1926-1.9025 2.1576-.5677.6825-.9597.5517-1.0886.5087-.3919-.1307-.813-.5871-1.2381-1.3223-.4796-.839-.9635-2.0317-1.4155-3.5126zm6.0072 5.0871c-.1711-.0428-.3271-.1132-.4322-.1772.0889-.0394.2374-.0902.4833-.1409 1.2833-.2641 1.4815-.4506 1.9143-1.0002.0992-.126.2116-.2687.3673-.4426a.3549.3549 0 0 0 .0737-.1298c.1708-.1513.2724-.1099.4369-.0417.156.0646.3078.26.3695.4752.0291.1016.0619.2945-.0452.4444-.9043 1.2658-2.2216 1.2494-3.1676 1.0128zm2.094-3.988-.0525.141c-.133.3566-.2567.6881-.3334 1.003-.6674-.0021-1.3168-.2872-1.8105-.8024-.6279-.6551-.9131-1.5664-.7825-2.5004.1828-1.3079.1153-2.4468.079-3.0586-.005-.0857-.0095-.1607-.0122-.2199.2957-.2621 1.6659-.9962 2.6429-.7724.4459.1022.7176.4057.8305.928.5846 2.7038.0774 3.8307-.3302 4.7363-.084.1866-.1633.3629-.2311.5454zm7.3637 4.5725c-.0169.1768-.0358.376-.0618.5959l-.146.4383a.3547.3547 0 0 0-.0182.1077c-.0059.4747-.054.6489-.115.8693-.0634.2292-.1353.4891-.1794 1.0575-.11 1.4143-.8782 2.2267-2.4172 2.5565-1.5155.3251-1.7843-.4968-2.0212-1.2217a6.5824 6.5824 0 0 0-.0769-.2266c-.2154-.5858-.1911-1.4119-.1574-2.5551.0165-.5612-.0249-1.9013-.3302-2.6462.0044-.2932.0106-.5909.019-.8918a.3529.3529 0 0 0-.0153-.1126 1.4927 1.4927 0 0 0-.0439-.208c-.1226-.4283-.4213-.7866-.7797-.9351-.1424-.059-.4038-.1672-.7178-.0869.067-.276.1831-.5875.309-.9249l.0529-.142c.0595-.16.134-.3257.213-.5012.4265-.9476 1.0106-2.2453.3766-5.1772-.2374-1.0981-1.0304-1.6343-2.2324-1.5098-.7207.0746-1.3799.3654-1.7088.5321a5.6716 5.6716 0 0 0-.1958.1041c.0918-1.1064.4386-3.1741 1.7357-4.4823a4.0306 4.0306 0 0 1 .3033-.276.3532.3532 0 0 0 .1447-.0644c.7524-.5706 1.6945-.8506 2.802-.8325.4091.0067.8017.0339 1.1742.081 1.939.3544 3.2439 1.4468 4.0359 2.3827.8143.9623 1.2552 1.9315 1.4312 2.4543-1.3232-.1346-2.2234.1268-2.6797.779-.9926 1.4189.543 4.1729 1.2811 5.4964.1353.2426.2522.4522.2889.5413.2403.5825.5515.9713.7787 1.2552.0696.087.1372.1714.1885.245-.4008.1155-1.1208.3825-1.0552 1.717-.0123.1563-.0423.4469-.0834.8148-.0461.2077-.0702.4603-.0994.7662zm.8905-1.6211c-.0405-.8316.2691-.9185.5967-1.0105a2.8566 2.8566 0 0 0 .135-.0406 1.202 1.202 0 0 0 .1342.103c.5703.3765 1.5823.4213 3.0068.1344-.2016.1769-.5189.3994-.9533.6011-.4098.1903-1.0957.333-1.7473.3636-.7197.0336-1.0859-.0807-1.1721-.151zm.5695-9.2712c-.0059.3508-.0542.6692-.1054 1.0017-.055.3576-.112.7274-.1264 1.1762-.0142.4368.0404.8909.0932 1.3301.1066.887.216 1.8003-.2075 2.7014a3.5272 3.5272 0 0 1-.1876-.3856c-.0527-.1276-.1669-.3326-.3251-.6162-.6156-1.1041-2.0574-3.6896-1.3193-4.7446.3795-.5427 1.3408-.5661 2.1781-.463zm.2284 7.0137a12.3762 12.3762 0 0 0-.0853-.1074l-.0355-.0444c.7262-1.1995.5842-2.3862.4578-3.4385-.0519-.4318-.1009-.8396-.0885-1.2226.0129-.4061.0666-.7543.1185-1.0911.0639-.415.1288-.8443.1109-1.3505.0134-.0531.0188-.1158.0118-.1902-.0457-.4855-.5999-1.938-1.7294-3.253-.6076-.7073-1.4896-1.4972-2.6889-2.0395.5251-.1066 1.2328-.2035 2.0244-.1859 2.0515.0456 3.6746.8135 4.8242 2.2824a.908.908 0 0 1 .0667.1002c.7231 1.3556-.2762 6.2751-2.9867 10.5405zm-8.8166-6.1162c-.025.1794-.3089.4225-.6211.4225a.5821.5821 0 0 1-.0809-.0056c-.1873-.026-.3765-.144-.5059-.3156-.0458-.0605-.1203-.178-.1055-.2844.0055-.0401.0261-.0985.0925-.1488.1182-.0894.3518-.1226.6096-.0867.3163.0441.6426.1938.6113.4186zm7.9305-.4114c.0111.0792-.049.201-.1531.3102-.0683.0717-.212.1961-.4079.2232a.5456.5456 0 0 1-.075.0052c-.2935 0-.5414-.2344-.5607-.3717-.024-.1765.2641-.3106.5611-.352.297-.0414.6111.0088.6356.1851z"

REDIS = "M22.71 13.145c-1.66 2.092-3.452 4.483-7.038 4.483-3.203 0-4.397-2.825-4.48-5.12.701 1.484 2.073 2.685 4.214 2.63 4.117-.133 6.94-3.852 6.94-7.239 0-4.05-3.022-6.972-8.268-6.972-3.752 0-8.4 1.428-11.455 3.685C2.59 6.937 3.885 9.958 4.35 9.626c2.648-1.904 4.748-3.13 6.784-3.744C8.12 9.244.886 17.05 0 18.425c.1 1.261 1.66 4.648 2.424 4.648.232 0 .431-.133.664-.365a100.49 100.49 0 0 0 5.54-6.765c.222 3.104 1.748 6.898 6.014 6.898 3.819 0 7.604-2.756 9.33-8.965.2-.764-.73-1.361-1.261-.73zm-4.349-5.013c0 1.959-1.926 2.922-3.685 2.922-.941 0-1.664-.247-2.235-.568 1.051-1.592 2.092-3.225 3.21-4.973 1.972.334 2.71 1.43 2.71 2.619z"

FASTAPI = "M12 .0387C5.3729.0384.0003 5.3931 0 11.9988c-.001 6.6066 5.372 11.9628 12 11.9625 6.628.0003 12.001-5.3559 12-11.9625-.0003-6.6057-5.3729-11.9604-12-11.96m-.829 5.4153h7.55l-7.5805 5.3284h5.1828L5.279 18.5436q2.9466-6.5444 5.892-13.0896"

PYTHON = "M14.25.18l.9.2.73.26.59.3.45.32.34.34.25.34.16.33.1.3.04.26.02.2-.01.13V8.5l-.05.63-.13.55-.21.46-.26.38-.3.31-.33.25-.35.19-.35.14-.33.1-.3.07-.26.04-.21.02H8.77l-.69.05-.59.14-.5.22-.41.27-.33.32-.27.35-.2.36-.15.37-.1.35-.07.32-.04.27-.02.21v3.06H3.17l-.21-.03-.28-.07-.32-.12-.35-.18-.36-.26-.36-.36-.35-.46-.32-.59-.28-.73-.21-.88-.14-1.05-.05-1.23.06-1.22.16-1.04.24-.87.32-.71.36-.57.4-.44.42-.33.42-.24.4-.16.36-.1.32-.05.24-.01h.16l.06.01h8.16v-.83H6.18l-.01-2.75-.02-.37.05-.34.11-.31.17-.28.25-.26.31-.23.38-.2.44-.18.51-.15.58-.12.64-.1.71-.06.77-.04.84-.02 1.27.05zm-6.3 1.98l-.23.33-.08.41.08.41.23.34.33.22.41.09.41-.09.33-.22.23-.34.08-.41-.08-.41-.23-.33-.33-.22-.41-.09-.41.09zm13.09 3.95l.28.06.32.12.35.18.36.27.36.35.35.47.32.59.28.73.21.88.14 1.04.05 1.23-.06 1.23-.16 1.04-.24.86-.32.71-.36.57-.4.45-.42.33-.42.24-.4.16-.36.09-.32.05-.24.02-.16-.01h-8.22v.82h5.84l.01 2.76.02.36-.05.34-.11.31-.17.29-.25.25-.31.24-.38.2-.44.17-.51.15-.58.13-.64.09-.71.07-.77.04-.84.01-1.27-.04-1.07-.14-.9-.2-.73-.25-.59-.3-.45-.33-.34-.34-.25-.34-.16-.33-.1-.3-.04-.25-.02-.2.01-.13v-5.34l.05-.64.13-.54.21-.46.26-.38.3-.32.33-.24.35-.2.35-.14.33-.1.3-.06.26-.04.21-.02.13-.01h5.84l.69-.05.59-.14.5-.21.41-.28.33-.32.27-.35.2-.36.15-.36.1-.35.07-.32.04-.28.02-.21V6.07h2.09l.14.01zm-6.47 14.25l-.23.33-.08.41.08.41.23.33.33.23.41.08.41-.08.33-.23.23-.33.08-.41-.08-.41-.23-.33-.33-.23-.41-.08-.41.08z"

# hand-drawn 24x24 glyphs for things with no brand mark
GLYPH = {
    "tenant": '<circle cx="12" cy="8" r="3.6"/><path d="M4.5 21c0-4.1 3.4-7 7.5-7s7.5 2.9 7.5 7" fill="none" stroke-width="2"/>',
    "console": '<rect x="2" y="4" width="20" height="13.5" rx="2" fill="none" stroke-width="2"/><path d="M12 17.5V21M8 21h8" fill="none" stroke-width="2"/>',
    "shield": '<path d="M12 1.8 3.6 5.2v6.4c0 5.1 3.5 9.4 8.4 10.7 4.9-1.3 8.4-5.6 8.4-10.7V5.2z" fill="none" stroke-width="2"/><path d="M8.3 12.1l2.6 2.6 4.8-4.8" fill="none" stroke-width="2.2"/>',
    "globe": '<circle cx="12" cy="12" r="9.4" fill="none" stroke-width="2"/><ellipse cx="12" cy="12" rx="4.1" ry="9.4" fill="none" stroke-width="2"/><path d="M2.9 9h18.2M2.9 15h18.2" fill="none" stroke-width="2"/>',
}

THEMES = {
    "light": {
        "bg": "#ffffff",
        "grp": "#f6f8fa",
        "grpline": "#d5dbe2",
        "grptxt": "#57606a",
        "node": "#ffffff",
        "line": "#c2cbd6",
        "txt": "#1f2328",
        "mut": "#5b636d",
        "store": "#eef4fb",
        "storeline": "#a3c0dd",
        "ext": "#fff6e9",
        "extline": "#dda15e",
        "flow": "#1f6feb",
        "sec": "#7a838d",
        "bnd": "#cf222e",
        "pyc": "#7a838d",
        "band": "#fdf3f0",
        "bandline": "#e6bdb5",
    },
    "dark": {
        "bg": "#0d1117",
        "grp": "#151b23",
        "grpline": "#2f3742",
        "grptxt": "#9198a1",
        "node": "#1b2029",
        "line": "#3b434e",
        "txt": "#e6edf3",
        "mut": "#9aa3ad",
        "store": "#16202c",
        "storeline": "#2f4a6b",
        "ext": "#2a2114",
        "extline": "#8a6116",
        "flow": "#4493f8",
        "sec": "#8b949e",
        "bnd": "#f85149",
        "pyc": "#8b949e",
        "band": "#241a1a",
        "bandline": "#5c2f2c",
    },
}

FONT = "ui-sans-serif,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,monospace"
W, H = 1160, 872

# ---- layout ---------------------------------------------------------------
GROUPS = [
    ("Ingress & console", 24, 100, 236, 392),
    ("Durable state", 284, 100, 236, 392),
    ("Delivery workers", 544, 100, 244, 392),
    ("Redis — three roles", 848, 100, 248, 392),
]
# name, x, y, w, h, kind, icon, iconcolor, title, sub[]
NODES = [
    ("client", 44, 144, 196, 60, "proc", "tenant", None, "Tenant", ["client system"]),
    (
        "api",
        44,
        228,
        196,
        100,
        "proc",
        "fastapi",
        "#009688",
        "FastAPI",
        ["REST · sandbox", "/healthz /readyz /metrics"],
    ),
    (
        "console",
        44,
        356,
        196,
        96,
        "proc",
        "console",
        None,
        "Demo console",
        ["live attempt", "timeline"],
    ),
    (
        "pg",
        304,
        144,
        196,
        180,
        "store",
        "pg",
        "#4169E1",
        "PostgreSQL",
        ["events · outbox", "deliveries · attempts", "endpoints · tenants"],
    ),
    ("rw", 564, 144, 200, 72, "proc", "python", None, "relay-worker", ["outbox → stream fan-out"]),
    (
        "disp",
        564,
        232,
        200,
        72,
        "proc",
        "python",
        None,
        "dispatcher \u00d7N",
        ["breaker · sign · send"],
    ),
    ("sched", 564, 320, 200, 72, "proc", "python", None, "scheduler", ["fires due retries"]),
    ("reaper", 564, 408, 200, 72, "proc", "python", None, "reaper", ["recovers orphaned work"]),
    (
        "stream",
        868,
        160,
        208,
        88,
        "store",
        "redis",
        "#FF4438",
        "Stream + group",
        ["the delivery queue"],
    ),
    (
        "zset",
        868,
        276,
        208,
        88,
        "store",
        "redis",
        "#FF4438",
        "Sorted set",
        ["retry wheel, by", "next_retry_at"],
    ),
    ("pubsub", 868, 392, 208, 88, "store", "redis", "#FF4438", "Pub/Sub", ["live attempt events"]),
    (
        "guard",
        552,
        556,
        250,
        84,
        "proc",
        "shield",
        None,
        "SSRF guard",
        ["DNS resolve → CIDR", "deny-list → pinned IP"],
    ),
    ("cust", 852, 556, 236, 84, "ext", "globe", None, "Customer endpoint", ["untrusted URL"]),
]
NODE = {n[0]: n for n in NODES}

# edges: (num, points, kind)  kind: flow | sec
EDGES = [
    (1, [(142, 204), (142, 228)], "flow"),
    (2, [(260, 278), (304, 278)], "flow"),
    (3, [(500, 180), (564, 180)], "flow"),
    (4, [(764, 190), (868, 190)], "flow"),
    (5, [(868, 240), (764, 240)], "flow"),
    (6, [(564, 286), (532, 286), (532, 598), (552, 598)], "flow"),
    (7, [(802, 598), (852, 598)], "flow"),
    (8, [(764, 290), (868, 290)], "sec"),
    (9, [(868, 344), (764, 344)], "sec"),
    (10, [(764, 376), (800, 376), (800, 222), (868, 222)], "sec"),
    (11, [(764, 452), (812, 452), (812, 234), (868, 234)], "sec"),
    (12, [(564, 258), (500, 258)], "sec"),
    (13, [(764, 258), (826, 258), (826, 436), (868, 436)], "sec"),
    (14, [(972, 480), (972, 504), (142, 504), (142, 452)], "sec"),
]
BADGE = {
    1: (142, 216),
    2: (282, 278),
    3: (532, 180),
    4: (816, 190),
    5: (816, 240),
    6: (532, 440),
    7: (815, 598),
    8: (780, 290),
    9: (780, 344),
    10: (782, 376),
    11: (788, 452),
    12: (532, 258),
    13: (795, 258),
    14: (560, 504),
}

LEGEND = [
    (1, "POST /v1/events with Idempotency-Key"),
    (2, "event row + outbox row, one transaction"),
    (3, "claim due outbox rows, then write delivery rows"),
    (4, "XADD one message per subscribed endpoint"),
    (5, "XREADGROUP; breaker checked before attempt"),
    (6, "HMAC-sign, then resolve and vet the host"),
    (7, "connect to the pinned IP; 2xx = delivered"),
    (8, "failure or open breaker → next_retry_at"),
    (9, "pop the retries whose time has come"),
    (10, "re-XADD: retries take the first-attempt path"),
    (11, "XAUTOCLAIM work orphaned by a dead dispatcher"),
    (12, "attempt row + delivery state"),
    (13, "publish attempt event"),
    (14, "SSE live timeline to the console"),
]


def esc(s):
    return html.escape(s, quote=True)


def build(theme):
    c = THEMES[theme]
    o = []
    a = o.append
    a(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="{FONT}">'
    )
    a(f'<rect width="{W}" height="{H}" fill="{c["bg"]}"/>')
    a(
        f'<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6.5" markerHeight="6.5" orient="auto-start-reverse"><path d="M0 0 10 5 0 10z" fill="{c["flow"]}"/></marker>'
        f'<marker id="ahs" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0 10 5 0 10z" fill="{c["sec"]}"/></marker></defs>'
    )
    # title
    a(
        f'<text x="24" y="42" font-size="21" font-weight="650" fill="{c["txt"]}">Relay — webhook delivery architecture</text>'
    )
    a(
        f'<text x="24" y="68" font-size="13.5" fill="{c["mut"]}">At-least-once fan-out · HMAC-signed · retried with jittered backoff · dead deliveries are replayable</text>'
    )
    # groups
    for name, x, y, w, h in GROUPS:
        a(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{c["grp"]}" stroke="{c["grpline"]}"/>'
        )
        a(
            f'<text x="{x + 16}" y="{y + 26}" font-size="11.5" font-weight="600" letter-spacing="0.9" fill="{c["grptxt"]}">{esc(name.upper())}</text>'
        )
    # egress band
    a(
        f'<rect x="24" y="520" width="1112" height="144" rx="12" fill="{c["band"]}" stroke="{c["bandline"]}"/>'
    )
    a(
        f'<text x="40" y="546" font-size="11.5" font-weight="600" letter-spacing="0.9" fill="{c["grptxt"]}">EGRESS — THE ONLY OUTBOUND CALL</text>'
    )
    for i, ln in enumerate(
        [
            "Every delivery leaves through the guard.",
            "It is the one place Relay opens a connection",
            "to a URL a customer controls.",
        ]
    ):
        a(f'<text x="40" y="{578 + i * 19}" font-size="12.5" fill="{c["mut"]}">{esc(ln)}</text>')
    # trust boundary
    a(f'<path d="M828 532V652" stroke="{c["bnd"]}" stroke-width="1.8" stroke-dasharray="6 5"/>')
    a(
        f'<text x="822" y="668" font-size="11" text-anchor="end" fill="{c["bnd"]}">Relay-controlled</text>'
    )
    a(f'<text x="834" y="668" font-size="11" fill="{c["bnd"]}">customer-controlled</text>')
    # notes under postgres
    for i, ln in enumerate(
        ["A dead delivery is the DLQ —", "a row state, not a table.", "Replay re-enqueues it."]
    ):
        a(f'<text x="304" y="{356 + i * 19}" font-size="12" fill="{c["mut"]}">{esc(ln)}</text>')
    # edges
    for num, pts, kind in EDGES:
        col = c["flow"] if kind == "flow" else c["sec"]
        wdt = "2.1" if kind == "flow" else "1.5"
        dash = "" if kind == "flow" else ' stroke-dasharray="5 4"'
        d = "M" + " L".join(f"{x} {y}" for x, y in pts)
        mk = "ah" if kind == "flow" else "ahs"
        start = f' marker-start="url(#{mk})"' if num == 3 else ""
        a(
            f'<path d="{d}" fill="none" stroke="{col}" stroke-width="{wdt}"{dash} marker-end="url(#{mk})"{start}/>'
        )
    # nodes
    for _key, x, y, w, h, kind, icon, icol, title, subs in NODES:
        fill = {"proc": c["node"], "store": c["store"], "ext": c["ext"]}[kind]
        stroke = {"proc": c["line"], "store": c["storeline"], "ext": c["extline"]}[kind]
        dash = ' stroke-dasharray="6 4"' if kind == "ext" else ""
        a(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}" stroke="{stroke}" stroke-width="1.3"{dash}/>'
        )
        iy = y + h / 2 - 13
        col = icol or c["pyc"]
        paths = {"pg": PG, "redis": REDIS, "fastapi": FASTAPI, "python": PYTHON}
        if icon in paths:
            a(f'<g transform="translate({x + 16} {iy}) scale(1.08)" fill="{col}" stroke="none">')
            a(f'<path d="{paths[icon]}"/>')
        else:
            a(
                f'<g transform="translate({x + 16} {iy}) scale(1.08)" fill="{col}" stroke="{col}" stroke-linejoin="round" stroke-linecap="round">'
            )
            a(GLYPH[icon])
        a("</g>")
        tx = x + 54
        ty = y + (h - (17 + 16 * len(subs))) / 2 + 15
        a(
            f'<text x="{tx}" y="{ty}" font-size="14.5" font-weight="640" fill="{c["txt"]}">{esc(title)}</text>'
        )
        for i, s in enumerate(subs):
            f = MONO if any(ch in s for ch in "/_") else FONT
            a(
                f'<text x="{tx}" y="{ty + 18 + i * 16}" font-size="11.5" font-family="{f}" fill="{c["mut"]}">{esc(s)}</text>'
            )
    # badges
    for num, (bx, by) in BADGE.items():
        kind = next(k for n, p, k in EDGES if n == num)
        col = c["flow"] if kind == "flow" else c["sec"]
        a(f'<circle cx="{bx}" cy="{by}" r="10" fill="{col}"/>')
        a(
            f'<text x="{bx}" y="{by + 3.9}" font-size="11.5" font-weight="700" text-anchor="middle" fill="{c["bg"]}">{num}</text>'
        )
    # legend
    a(
        f'<text x="24" y="700" font-size="11.5" font-weight="600" letter-spacing="0.9" fill="{c["grptxt"]}">THE PATH OF ONE EVENT</text>'
    )
    for i, (num, txt) in enumerate(LEGEND):
        cx = 24 + (i // 5) * 378
        cy = 726 + (i % 5) * 28
        kind = next(k for n, p, k in EDGES if n == num)
        col = c["flow"] if kind == "flow" else c["sec"]
        a(f'<circle cx="{cx + 10}" cy="{cy - 4}" r="9.5" fill="{col}"/>')
        a(
            f'<text x="{cx + 10}" y="{cy - 0.3}" font-size="11" font-weight="700" text-anchor="middle" fill="{c["bg"]}">{num}</text>'
        )
        a(f'<text x="{cx + 27}" y="{cy}" font-size="12.3" fill="{c["mut"]}">{esc(txt)}</text>')
    # shape key
    ky = 866
    kx = 628

    def sw(x, fill, stroke, dash=""):
        return f'<rect x="{x}" y="{ky - 11}" width="14" height="11" rx="3" fill="{fill}" stroke="{stroke}"{dash}/>'

    a(sw(kx, c["node"], c["line"]))
    a(f'<text x="{kx + 20}" y="{ky - 1}" font-size="11.5" fill="{c["mut"]}">process</text>')
    a(sw(kx + 82, c["store"], c["storeline"]))
    a(f'<text x="{kx + 102}" y="{ky - 1}" font-size="11.5" fill="{c["mut"]}">datastore</text>')
    a(sw(kx + 178, c["ext"], c["extline"], ' stroke-dasharray="4 3"'))
    a(f'<text x="{kx + 198}" y="{ky - 1}" font-size="11.5" fill="{c["mut"]}">untrusted</text>')
    a(f'<path d="M{kx + 274} {ky - 6}h22" stroke="{c["flow"]}" stroke-width="2.1"/>')
    a(f'<text x="{kx + 302}" y="{ky - 1}" font-size="11.5" fill="{c["mut"]}">delivery path</text>')
    a(
        f'<path d="M{kx + 382} {ky - 6}h22" stroke="{c["sec"]}" stroke-width="1.5" stroke-dasharray="5 4"/>'
    )
    a(
        f'<text x="{kx + 410}" y="{ky - 1}" font-size="11.5" fill="{c["mut"]}">retry &amp; recovery</text>'
    )
    a("</svg>")
    return "\n".join(o)


OUT.mkdir(parents=True, exist_ok=True)
for theme in THEMES:
    dest = OUT / f"architecture-{theme}.svg"
    dest.write_text(build(theme))
    print("wrote", dest)
