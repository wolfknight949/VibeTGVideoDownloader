# ── Colour palette ──────────────────────────────────────────────────────────
BG       = "#0d1117"
CARD     = "#161b22"
SURFACE  = "#1c2128"
BORDER   = "#21262d"
TEXT     = "#e6edf3"
MUTED    = "#7d8590"
DIM      = "#484f58"
BLUE     = "#388bfd"
GREEN    = "#3fb950"
RED      = "#f85149"
YELLOW   = "#d29922"
PURPLE   = "#bc8cff"
ORANGE   = "#e3b341"

CSS = f"""
html, body, .q-page {{ background:{BG} !important; }}

.q-header {{
    background:#010409 !important;
    border-bottom:1px solid {BORDER};
    box-shadow:none !important;
}}

.q-drawer {{
    background:#010409 !important;
    border-right:1px solid {BORDER} !important;
}}
.q-drawer .q-separator {{ background:{BORDER}; opacity:1; }}

.q-card {{ background:{CARD} !important; border:1px solid {BORDER}; border-radius:10px !important; }}

/* Expansion */
.q-expansion-item {{
    border:1px solid {BORDER};
    border-radius:8px !important;
    overflow:hidden;
    margin-bottom:2px;
}}
.q-expansion-item__container .q-item {{ background:{SURFACE} !important; }}
.q-expansion-item__container .q-expansion-item__content {{ background:{CARD} !important; }}

/* Inputs */
.q-field--outlined .q-field__control::before {{ border-color:{BORDER} !important; }}
.q-field--outlined:hover .q-field__control::before {{ border-color:{MUTED} !important; }}
.q-field--outlined.q-field--focused .q-field__control::before {{ border-color:{BLUE} !important; }}
.q-field__label {{ color:{MUTED} !important; }}
.q-field__native, .q-field__input {{ color:{TEXT} !important; }}
.q-field__control {{ background:{BG} !important; }}

/* Progress */
.q-linear-progress {{ border-radius:3px; overflow:hidden; }}
.q-linear-progress__track {{ background:{SURFACE} !important; }}
.q-linear-progress__model {{ border-radius:3px; }}

/* Scrollbar */
::-webkit-scrollbar {{ width:5px; height:5px; }}
::-webkit-scrollbar-track {{ background:transparent; }}
::-webkit-scrollbar-thumb {{ background:{BORDER}; border-radius:3px; }}

/* App-specific */
.section-lbl {{
    font-size:0.6rem; font-weight:700; letter-spacing:.12em;
    text-transform:uppercase; color:{DIM};
}}
.dl-strip {{
    background:{SURFACE};
    border:1px solid {BORDER};
    border-radius:8px;
    padding:12px 16px;
}}
.post-row {{
    width:100%;
    box-sizing:border-box;
    border-radius:6px;
    padding:6px 10px;
    border:1px solid {BORDER};
    background:{SURFACE};
    margin-bottom:4px;
}}
.q-expansion-item__container .q-expansion-item__content {{
    padding:8px !important;
    box-sizing:border-box;
}}
.video-row {{
    border-radius:4px;
    padding:3px 8px 3px 32px;
    transition:background .12s;
}}
.video-row:hover {{ background:rgba(255,255,255,.04) !important; }}
.video-row-thumb {{
    border-radius:4px;
    padding:3px 8px;
    transition:background .12s;
}}
.video-row-thumb:hover {{ background:rgba(255,255,255,.04) !important; }}
.video-row-thumb .q-img {{ border-radius:3px; overflow:hidden; }}
.chip {{
    display:inline-flex; align-items:center;
    padding:1px 7px; border-radius:12px;
    font-size:.65rem; font-weight:600; white-space:nowrap;
}}
.chip-grey   {{ background:rgba(125,133,144,.12); color:{MUTED}; border:1px solid rgba(125,133,144,.2); }}
.chip-green  {{ background:rgba(63,185,80,.12);   color:{GREEN};  border:1px solid rgba(63,185,80,.25); }}
.chip-blue   {{ background:rgba(56,139,253,.12);  color:{BLUE};   border:1px solid rgba(56,139,253,.25); }}
.chip-red    {{ background:rgba(248,81,73,.12);   color:{RED};    border:1px solid rgba(248,81,73,.25); }}
.chip-orange {{ background:rgba(227,179,65,.12);  color:{ORANGE}; border:1px solid rgba(227,179,65,.25); }}
.topic-hdr {{
    padding:8px 12px;
    border-radius:6px;
    background:{SURFACE};
    margin-bottom:2px;
    cursor:pointer;
}}
"""
