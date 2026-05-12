#!/usr/bin/env python3
"""
Feed ERD Analyzer — Streamlit App
Reads a feed spec Excel file, detects PK/FK relationships via heuristics
and optionally a local Qwen 2.5 LLM, and renders an interactive ERD.

Install:
    pip install streamlit pandas openpyxl pyvis networkx plotly requests

Run:
    streamlit run feed_erd_app.py
"""

import streamlit as st
import pandas as pd
import networkx as nx
from pyvis.network import Network
import streamlit.components.v1 as components
import plotly.graph_objects as go
import requests
import json
import re
import colorsys
import tempfile
import os
from collections import defaultdict
from io import BytesIO

# ─────────────────────────────────────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Feed ERD Analyzer",
    page_icon="🔗",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    [data-testid="stAppViewContainer"] { background: #0b0d14; }
    [data-testid="stSidebar"] { background: #13151f; border-right: 1px solid #1e2130; }
    [data-testid="stSidebar"] * { color: #c8cce0 !important; }
    .block-container { padding-top: 1.5rem; }
    .stMetric { background: #13151f; border: 1px solid #1e2130; border-radius: 8px; padding: 12px 16px; }
    .stMetric label { color: #6670a0 !important; font-size: 11px !important; text-transform: uppercase; letter-spacing: 1px; }
    .stMetric [data-testid="stMetricValue"] { color: #028090 !important; font-size: 28px !important; font-weight: 700 !important; }
    .stTabs [data-baseweb="tab-list"] { background: #13151f; border-radius: 8px; padding: 4px; gap: 4px; }
    .stTabs [data-baseweb="tab"] { background: transparent; color: #6670a0 !important; border-radius: 6px; font-size: 13px; }
    .stTabs [aria-selected="true"] { background: #1e2840 !important; color: #028090 !important; }
    .stDataFrame { border: 1px solid #1e2130; border-radius: 8px; }
    div[data-testid="stSelectbox"] label,
    div[data-testid="stMultiSelect"] label,
    div[data-testid="stSlider"] label,
    div[data-testid="stTextInput"] label { color: #8890b0 !important; font-size: 12px !important; }
    .info-banner {
        background: #0d1f28; border: 1px solid #028090; border-radius: 8px;
        padding: 14px 18px; margin-bottom: 16px; color: #80c8d0;
        font-size: 13px; line-height: 1.6;
    }
    .feed-pill {
        display: inline-block; padding: 3px 10px; border-radius: 20px;
        font-size: 11px; font-weight: 600; margin: 2px;
    }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────
PK_SUFFIXES = [
    '_NUMBER', '_NUM', '_NBR', '_ID', '_KEY', '_CODE', '_REF',
    '_SEQ', '_IDENTIFIER', '_ACCT', '_CUSIP', '_ISIN', '_SEDOL'
]
PK_PREFIXES = ['ID_', 'KEY_', 'CODE_', 'REF_']

SKIP_POSITIONS = {'header record', 'trailer record', 'header', 'trailer', 'nan', ''}

COL_CANDIDATES = {
    'feed':        ['feed', 'feed name', 'interface', 'source', 'source feed'],
    'position':    ['position', 'pos', 'field position', 'seq', 'sequence'],
    'field_name':  ['field name', 'field_name', 'column name', 'column', 'field'],
    'description': ['field description', 'description', 'desc', 'field desc'],
    'data_type':   ['data type', 'datatype', 'type', 'data_type'],
    'nullable':    ['nullable', 'null', 'required', 'mandatory', 'not null'],
    'reference':   ['reference', 'ref', 'fk reference', 'foreign key', 'fk ref'],
}

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def generate_colors(n: int) -> list:
    """Generate n visually distinct hex colors via golden-angle HSL spacing."""
    out = []
    for i in range(n):
        h = (i * 0.618033988749895) % 1.0
        r, g, b = colorsys.hls_to_rgb(h, 0.42, 0.62)
        out.append(f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}")
    return out


def find_col(df_cols: list, candidates: list):
    """Case-insensitive column resolver."""
    norm = {c.strip().lower().replace('_', ' '): c for c in df_cols}
    for cand in candidates:
        match = norm.get(cand.strip().lower().replace('_', ' '))
        if match:
            return match
    return None


def safe_str(val) -> str:
    s = str(val).strip()
    return '' if s.lower() == 'nan' else s

# ─────────────────────────────────────────────────────────────────────────────
# EXCEL PARSING  (supports single-sheet multi-feed OR multi-sheet per-feed)
# ─────────────────────────────────────────────────────────────────────────────
def parse_excel(uploaded_file) -> dict:
    """
    Returns: {feed_name: [{'position','field_name','description',
                            'data_type','nullable','reference'}, ...]}
    """
    xl = pd.ExcelFile(uploaded_file)
    feeds = {}

    def extract_rows(df, feed_label=None):
        """Pull field rows from a dataframe."""
        col_pos  = find_col(df.columns.tolist(), COL_CANDIDATES['position'])
        col_fn   = find_col(df.columns.tolist(), COL_CANDIDATES['field_name'])
        col_desc = find_col(df.columns.tolist(), COL_CANDIDATES['description'])
        col_type = find_col(df.columns.tolist(), COL_CANDIDATES['data_type'])
        col_null = find_col(df.columns.tolist(), COL_CANDIDATES['nullable'])
        col_ref  = find_col(df.columns.tolist(), COL_CANDIDATES['reference'])
        col_feed = find_col(df.columns.tolist(), COL_CANDIDATES['feed'])

        if not col_fn:
            return {}

        result = defaultdict(list)
        for _, row in df.iterrows():
            pos = safe_str(row.get(col_pos, '')) if col_pos else ''
            if pos.lower() in SKIP_POSITIONS:
                continue
            fn = safe_str(row.get(col_fn, ''))
            if not fn:
                continue

            fname = feed_label
            if not fname and col_feed:
                fname = safe_str(row.get(col_feed, ''))
            if not fname:
                fname = 'Unknown Feed'

            result[fname].append({
                'position':    pos,
                'field_name':  fn.upper(),
                'description': safe_str(row.get(col_desc, '')) if col_desc else '',
                'data_type':   safe_str(row.get(col_type, '')) if col_type else '',
                'nullable':    safe_str(row.get(col_null, '')) if col_null else '',
                'reference':   safe_str(row.get(col_ref, ''))  if col_ref  else '',
            })
        return dict(result)

    # Try first sheet; if it has a Feed column with multiple values → single sheet mode
    df0 = xl.parse(xl.sheet_names[0])
    col_feed = find_col(df0.columns.tolist(), COL_CANDIDATES['feed'])

    if col_feed and df0[col_feed].dropna().nunique() > 1:
        # Single sheet, Feed column differentiates feeds
        feeds = extract_rows(df0)
    else:
        # Multi-sheet: each sheet = one feed
        for sheet in xl.sheet_names:
            df_s = xl.parse(sheet)
            sheet_feeds = extract_rows(df_s, feed_label=sheet)
            for fname, rows in sheet_feeds.items():
                feeds[fname] = feeds.get(fname, []) + rows

    return {k: v for k, v in feeds.items() if v}

# ─────────────────────────────────────────────────────────────────────────────
# PK DETECTION
# ─────────────────────────────────────────────────────────────────────────────
def is_pk_candidate(field: dict, position: int) -> bool:
    fn  = field['field_name']
    nul = field['nullable'].lower()
    if 'null' in nul and 'not' not in nul:  # explicitly nullable → skip
        return False
    for sfx in PK_SUFFIXES:
        if fn.endswith(sfx):
            return True
    for pfx in PK_PREFIXES:
        if fn.startswith(pfx):
            return True
    if position == 1 and field['nullable'].upper() in ('NOT NULL', 'N', 'NO', 'FALSE', '0'):
        return True
    return False


def detect_pks(feeds: dict) -> dict:
    """Returns {feed_name: [pk_field, ...]}"""
    return {
        fname: [
            f['field_name'] for i, f in enumerate(fields)
            if is_pk_candidate(f, i + 1)
        ]
        for fname, fields in feeds.items()
    }

# ─────────────────────────────────────────────────────────────────────────────
# HEURISTIC FK DETECTION
# ─────────────────────────────────────────────────────────────────────────────
def detect_fks_heuristic(feeds: dict, pks: dict) -> list:
    """
    Returns [{'from_feed','from_field','to_feed','to_field','confidence','method'}]
    """
    rels, seen = [], set()

    # ① Explicit Reference column
    for fname, fields in feeds.items():
        for f in fields:
            ref = f['reference']
            if not ref:
                continue
            for other in feeds:
                if other == fname:
                    continue
                if other.lower() in ref.lower() or ref.lower() in other.lower():
                    # Try exact field match in referenced feed, else fall back to first PK
                    to_field = next(
                        (x['field_name'] for x in feeds[other]
                         if x['field_name'] == f['field_name']),
                        (pks.get(other) or [None])[0]
                    )
                    key = (fname, f['field_name'], other)
                    if key not in seen and to_field:
                        seen.add(key)
                        rels.append({
                            'from_feed': fname, 'from_field': f['field_name'],
                            'to_feed': other, 'to_field': to_field,
                            'confidence': 0.95, 'method': 'Reference Column'
                        })

    # ② Exact field-name match across feeds where one side is a PK
    field_index = defaultdict(list)   # field_name → [(feed, is_pk)]
    for fname, fields in feeds.items():
        feed_pks = set(pks.get(fname, []))
        for f in fields:
            field_index[f['field_name']].append((fname, f['field_name'] in feed_pks))

    for fn, occurrences in field_index.items():
        if len(occurrences) < 2:
            continue
        pk_feeds  = [f for f, is_pk in occurrences if is_pk]
        non_feeds = [f for f, is_pk in occurrences if not is_pk]
        for pk_f in pk_feeds:
            for nf in non_feeds:
                key = (nf, fn, pk_f)
                if key not in seen:
                    seen.add(key)
                    rels.append({
                        'from_feed': nf, 'from_field': fn,
                        'to_feed': pk_f, 'to_field': fn,
                        'confidence': 0.80, 'method': 'Exact Name Match'
                    })

    # ③ Partial suffix match (e.g. ACCT_NUM ↔ ACCOUNT_NUMBER)
    pk_field_map = {}   # normalised_stem → (feed, field_name)
    def norm_stem(fn):
        for sfx in PK_SUFFIXES:
            if fn.endswith(sfx):
                return fn[:-len(sfx)]
        return fn

    for fname, pk_fields in pks.items():
        for pf in pk_fields:
            stem = norm_stem(pf)
            pk_field_map[stem] = (fname, pf)

    for fname, fields in feeds.items():
        feed_pks = set(pks.get(fname, []))
        for f in fields:
            if f['field_name'] in feed_pks:
                continue
            stem = norm_stem(f['field_name'])
            if stem in pk_field_map:
                pk_feed, pk_field = pk_field_map[stem]
                if pk_feed == fname:
                    continue
                key = (fname, f['field_name'], pk_feed)
                if key not in seen:
                    seen.add(key)
                    rels.append({
                        'from_feed': fname, 'from_field': f['field_name'],
                        'to_feed': pk_feed, 'to_field': pk_field,
                        'confidence': 0.65, 'method': 'Stem Match'
                    })

    return rels

# ─────────────────────────────────────────────────────────────────────────────
# LLM INTEGRATION  (Qwen 2.5 via llama.cpp OpenAI-compat API)
# ─────────────────────────────────────────────────────────────────────────────
def call_llm(prompt: str, endpoint: str, model: str, temperature: float = 0.05) -> str | None:
    try:
        url  = endpoint.rstrip('/') + '/v1/chat/completions'
        resp = requests.post(url, json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": 2048,
        }, timeout=90)
        resp.raise_for_status()
        return resp.json()['choices'][0]['message']['content']
    except Exception:
        return None


def detect_fks_llm(feeds: dict, pks: dict, endpoint: str, model: str,
                   progress_cb=None) -> list:
    """
    Ask Qwen 2.5 to semantically match FK fields.
    Sends one batched prompt per target feed to keep token usage manageable.
    """
    rels, seen = [], set()

    # Build compact PK catalogue (cap at 120 entries to stay inside context)
    pk_lines = []
    for fname, pk_fields in pks.items():
        for pf in pk_fields:
            info = next((x for x in feeds[fname] if x['field_name'] == pf), {})
            pk_lines.append(f"[{fname}] {pf}: {info.get('description','')}")
            if len(pk_lines) >= 120:
                break
        if len(pk_lines) >= 120:
            break
    pk_catalogue = "\n".join(pk_lines)

    feed_names = list(feeds.keys())
    for idx, fname in enumerate(feed_names):
        if progress_cb:
            progress_cb(idx / len(feed_names), f"LLM → {fname}")

        feed_pks = set(pks.get(fname, []))
        candidates = [f for f in feeds[fname] if f['field_name'] not in feed_pks]
        if not candidates:
            continue

        field_lines = "\n".join(
            f"  {f['field_name']}: {f['description']} ({f['data_type']})"
            for f in candidates[:40]
        )

        prompt = f"""You are a senior data architect.

PRIMARY KEYS across all feeds (catalogue):
{pk_catalogue}

TASK: For each field in feed "{fname}" listed below, decide if it is a foreign key
referencing one of the primary keys in the catalogue above — either by EXACT name match
or SEMANTIC equivalence (same concept, different naming convention).

Fields to analyse in "{fname}":
{field_lines}

Rules:
- Only include fields that clearly reference a PK from ANOTHER feed (not "{fname}" itself).
- Be conservative: confidence < 0.6 means skip.
- Return ONLY valid JSON, no markdown, no commentary:

[
  {{
    "from_field": "FIELD_NAME_IN_{fname.upper().replace(' ','_')}",
    "to_feed": "Exact feed name from catalogue",
    "to_field": "Exact PK field name from catalogue",
    "confidence": 0.0,
    "reason": "one-line explanation"
  }}
]

If none found, return: []"""

        raw = call_llm(prompt, endpoint, model)
        if not raw:
            continue

        try:
            clean = re.sub(r'```(?:json)?|```', '', raw).strip()
            items = json.loads(clean)
            if not isinstance(items, list):
                continue
            for item in items:
                ff  = str(item.get('from_field', '')).upper().strip()
                tf  = str(item.get('to_feed', '')).strip()
                tfd = str(item.get('to_field', '')).upper().strip()
                conf = float(item.get('confidence', 0.7))
                reason = str(item.get('reason', ''))

                if conf < 0.55 or not ff or not tf:
                    continue

                # Fuzzy-match feed name
                if tf not in feeds:
                    matches = [f for f in feeds
                               if f.lower() in tf.lower() or tf.lower() in f.lower()]
                    tf = matches[0] if matches else None
                if not tf:
                    continue

                key = (fname, ff, tf)
                if key not in seen:
                    seen.add(key)
                    rels.append({
                        'from_feed': fname, 'from_field': ff,
                        'to_feed': tf, 'to_field': tfd,
                        'confidence': conf,
                        'method': f'LLM — {reason}'
                    })
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            continue

    if progress_cb:
        progress_cb(1.0, "Done")
    return rels

# ─────────────────────────────────────────────────────────────────────────────
# GRAPH
# ─────────────────────────────────────────────────────────────────────────────
def build_graph(feeds: dict, rels: list, pks: dict) -> nx.DiGraph:
    G = nx.DiGraph()
    for fname, fields in feeds.items():
        G.add_node(fname, field_count=len(fields), pks=pks.get(fname, []))
    for r in rels:
        if r['confidence'] >= 0.5:
            G.add_edge(r['from_feed'], r['to_feed'],
                       from_field=r['from_field'], to_field=r['to_field'],
                       confidence=r['confidence'], method=r['method'])
    return G


def render_pyvis(G: nx.DiGraph, feeds: dict, pks: dict, color_map: dict) -> str:
    net = Network(height="660px", width="100%", directed=True,
                  bgcolor="#0b0d14", font_color="#ccd0e8")

    physics_opts = {
        "physics": {
            "enabled": True,
            "solver": "forceAtlas2Based",
            "forceAtlas2Based": {
                "gravitationalConstant": -90,
                "centralGravity": 0.004,
                "springLength": 220,
                "springConstant": 0.07,
                "damping": 0.5
            },
            "stabilization": {"iterations": 200, "updateInterval": 25}
        },
        "edges": {
            "arrows": {"to": {"enabled": True, "scaleFactor": 0.7}},
            "color": {"color": "#2a3060", "highlight": "#028090", "hover": "#04b8cc"},
            "font": {"size": 9, "color": "#8890b8", "align": "middle"},
            "smooth": {"type": "curvedCW", "roundness": 0.18},
            "hoverWidth": 2
        },
        "nodes": {
            "shape": "box",
            "margin": 10,
            "font": {"size": 12, "face": "monospace", "color": "#ffffff"},
            "borderWidth": 1.5,
            "shadow": {"enabled": True, "color": "rgba(0,0,0,0.5)", "size": 8}
        },
        "interaction": {"hover": True, "tooltipDelay": 80, "navigationButtons": True}
    }
    net.set_options(json.dumps(physics_opts))

    for node in G.nodes():
        fields     = feeds.get(node, [])
        pk_fields  = pks.get(node, [])
        color      = color_map.get(node, '#028090')
        in_deg     = G.in_degree(node)
        out_deg    = G.out_degree(node)

        # Build rich tooltip
        fk_out = [(d['from_field'], d['to_feed']) for _, _, d in G.out_edges(node, data=True)]
        fk_in  = [(d['from_feed'], d['from_field']) for _, _, d in G.in_edges(node, data=True)]
        tip = (
            f"<b style='color:{color}'>{node}</b><br>"
            f"Fields: {len(fields)} | PKs: {len(pk_fields)}<br>"
            f"FK out: {out_deg} | Referenced by: {in_deg}<br>"
        )
        if pk_fields:
            tip += f"<b>PKs:</b> {', '.join(pk_fields[:4])}<br>"
        if fk_out:
            tip += "<b>References:</b><br>" + "".join(f"  {ff}→{tf}<br>" for ff, tf in fk_out[:5])

        size = max(20, min(55, 18 + len(fields) * 0.4))
        net.add_node(
            node,
            label=f"◈ {node}\n({len(fields)} fields)",
            title=tip,
            color={"background": color + "cc",
                   "border": color,
                   "highlight": {"background": color, "border": "#ffffff"},
                   "hover":     {"background": color, "border": "#028090"}},
            size=size,
        )

    for u, v, d in G.edges(data=True):
        conf = d.get('confidence', 0.7)
        label = f"{d.get('from_field','')}→{d.get('to_field','')}"
        tip = (
            f"FK: <b>{d.get('from_field')}</b> → <b>{d.get('to_field')}</b><br>"
            f"Feed: {u} → {v}<br>"
            f"Confidence: {conf:.0%}<br>"
            f"Method: {d.get('method','')}"
        )
        edge_color = "#028090" if conf >= 0.85 else ("#e6a817" if conf >= 0.65 else "#664466")
        net.add_edge(u, v, label=label, title=tip,
                     width=1 + conf * 3,
                     color={"color": edge_color, "highlight": "#04d4e8"})

    tmp = tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode='w')
    net.save_graph(tmp.name)
    tmp.close()
    return tmp.name

# ─────────────────────────────────────────────────────────────────────────────
# DEPENDENCY MATRIX
# ─────────────────────────────────────────────────────────────────────────────
def build_matrix(feeds: dict, rels: list) -> pd.DataFrame:
    names = sorted(feeds.keys())
    idx   = {n: i for i, n in enumerate(names)}
    mat   = [[0] * len(names) for _ in range(len(names))]
    for r in rels:
        i, j = idx.get(r['from_feed']), idx.get(r['to_feed'])
        if i is not None and j is not None:
            mat[i][j] += 1
    return pd.DataFrame(mat, index=names, columns=names)

# ─────────────────────────────────────────────────────────────────────────────
# EXCEL EXPORT
# ─────────────────────────────────────────────────────────────────────────────
def to_excel(feeds: dict, pks: dict, rels: list) -> bytes:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        pd.DataFrame(rels).to_excel(writer, sheet_name='Relationships', index=False)
        pk_rows = [{'Feed': f, 'PK Field': pf}
                   for f, pflist in pks.items() for pf in pflist]
        pd.DataFrame(pk_rows).to_excel(writer, sheet_name='Primary Keys', index=False)
        summary = [{'Feed': f, 'Field Count': len(flds),
                    'PK Count': len(pks.get(f, [])),
                    'FK Out': sum(1 for r in rels if r['from_feed'] == f),
                    'Referenced By': sum(1 for r in rels if r['to_feed'] == f)}
                   for f, flds in feeds.items()]
        pd.DataFrame(summary).to_excel(writer, sheet_name='Feed Summary', index=False)
    return buf.getvalue()

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    # ── SIDEBAR ────────────────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown("## 🔗 Feed ERD Analyzer")
        st.caption("Upload feed spec → detect PK/FK → explore ERD")
        st.divider()

        uploaded = st.file_uploader(
            "📂 Feed Spec Excel",
            type=['xlsx', 'xls'],
            help="Columns expected: Feed, Position, Field Name, Field Description, Data Type, Nullable, Reference"
        )

        st.divider()
        st.markdown("#### 🤖 Qwen 2.5 (Local LLM)")
        use_llm = st.toggle("Enable semantic matching", value=False)
        llm_endpoint = st.text_input("llama.cpp endpoint",
                                     value="http://localhost:8080",
                                     disabled=not use_llm)
        llm_model = st.text_input("Model name",
                                   value="qwen2.5-coder",
                                   disabled=not use_llm)
        if use_llm:
            if st.button("🔌 Test connection"):
                r = call_llm("Reply with the single word OK.", llm_endpoint, llm_model)
                if r:
                    st.success(f"✅ Connected — model replied")
                else:
                    st.error("❌ No response — check endpoint/model")

        st.divider()
        st.markdown("#### ⚙️ Detection")
        min_conf = st.slider("Min FK confidence", 0.0, 1.0, 0.60, 0.05)
        merge_llm = st.checkbox("Merge LLM + heuristic", value=True,
                                help="When both methods detect the same FK, keep the higher confidence score")

    # ── LANDING ────────────────────────────────────────────────────────────────
    if not uploaded:
        st.markdown("""
        <div style="text-align:center; padding:80px 20px 40px;">
            <div style="font-size:72px; margin-bottom:20px; opacity:.9;">🔗</div>
            <h2 style="color:#028090; font-weight:700; letter-spacing:-0.5px; margin:0 0 12px;">
                Feed ERD Analyzer
            </h2>
            <p style="color:#5560a0; font-size:15px; max-width:520px; margin:0 auto 28px; line-height:1.7;">
                Upload your feed spec Excel to automatically map primary keys, detect
                foreign key relationships across 100+ feeds, and explore the full
                entity-relationship graph interactively.
            </p>
            <div class="info-banner" style="max-width:520px; margin:0 auto; text-align:left;">
                <b>Supported Excel formats:</b><br>
                • Single sheet with a <code>Feed</code> column (your current format)<br>
                • Multi-sheet where each sheet = one feed<br><br>
                <b>Optional:</b> enable <b>Qwen 2.5</b> in the sidebar for semantic
                FK detection that catches cross-naming-convention matches.
            </div>
        </div>
        """, unsafe_allow_html=True)
        return

    # ── PARSE ──────────────────────────────────────────────────────────────────
    with st.spinner("Parsing Excel…"):
        try:
            feeds = parse_excel(uploaded)
        except Exception as exc:
            st.error(f"Failed to parse: {exc}")
            return

    if not feeds:
        st.error("No feeds found. Verify that column names match: Feed, Position, Field Name, …")
        return

    feed_names  = list(feeds.keys())
    colors      = generate_colors(len(feed_names))
    color_map   = dict(zip(feed_names, colors))

    # ── DETECT PKs ─────────────────────────────────────────────────────────────
    pks = detect_pks(feeds)

    # ── DETECT FKs ─────────────────────────────────────────────────────────────
    all_rels: list = []

    with st.spinner("Heuristic FK detection…"):
        h_rels = detect_fks_heuristic(feeds, pks)
        all_rels.extend(h_rels)

    if use_llm:
        prog_bar = st.progress(0.0, "Initialising Qwen 2.5 analysis…")
        status   = st.empty()

        def cb(pct, msg):
            prog_bar.progress(pct, msg)
            status.caption(msg)

        llm_rels = detect_fks_llm(feeds, pks, llm_endpoint, llm_model, cb)
        prog_bar.empty()
        status.empty()

        if merge_llm:
            existing = {(r['from_feed'], r['from_field'], r['to_feed']): i
                        for i, r in enumerate(all_rels)}
            for lr in llm_rels:
                key = (lr['from_feed'], lr['from_field'], lr['to_feed'])
                if key in existing:
                    if lr['confidence'] > all_rels[existing[key]]['confidence']:
                        all_rels[existing[key]]['confidence'] = lr['confidence']
                        all_rels[existing[key]]['method'] += f" + {lr['method']}"
                else:
                    all_rels.append(lr)
                    existing[key] = len(all_rels) - 1
        else:
            all_rels.extend(llm_rels)

    filtered = [r for r in all_rels if r['confidence'] >= min_conf]

    # ── METRICS ────────────────────────────────────────────────────────────────
    total_fields = sum(len(f) for f in feeds.values())
    total_pks    = sum(len(p) for p in pks.values())
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Feeds",         len(feeds))
    c2.metric("Total Fields",  total_fields)
    c3.metric("Detected PKs",  total_pks)
    c4.metric("FK Relationships", len(filtered))
    c5.metric("Connected Feeds",
              len({r['from_feed'] for r in filtered} | {r['to_feed'] for r in filtered}))

    # ── GRAPH ──────────────────────────────────────────────────────────────────
    G = build_graph(feeds, filtered, pks)

    st.divider()

    # ── TABS ───────────────────────────────────────────────────────────────────
    t1, t2, t3, t4 = st.tabs([
        "🗺️  ERD Graph",
        "🔍  Feed Browser",
        "📊  Dependency Matrix",
        "📋  Relationships"
    ])

    # ── TAB 1 — ERD ────────────────────────────────────────────────────────────
    with t1:
        col_ctrl, col_info = st.columns([4, 1])
        with col_ctrl:
            focus = st.multiselect(
                "Focus on feeds (leave empty = show all)",
                options=feed_names, default=[],
                help="Selected feeds + their direct neighbours are shown"
            )
        with col_info:
            st.caption(f"{'Showing full graph' if not focus else f'Focused: {len(focus)} feed(s) + neighbours'}")

        if focus:
            sub_nodes = set(focus)
            for n in focus:
                sub_nodes.update(G.predecessors(n))
                sub_nodes.update(G.successors(n))
            sub_G     = G.subgraph(sub_nodes)
            sub_feeds = {f: feeds[f] for f in sub_nodes if f in feeds}
        else:
            sub_G, sub_feeds = G, feeds

        html_path = render_pyvis(sub_G, sub_feeds, pks, color_map)
        with open(html_path) as fh:
            html_src = fh.read()
        os.unlink(html_path)

        st.caption("🖱 Drag nodes · 🖲 Scroll to zoom · Hover for field details")
        components.html(html_src, height=680, scrolling=False)

        # Feed colour legend (compact)
        with st.expander("Feed colour legend", expanded=False):
            cols = st.columns(4)
            for i, fn in enumerate(feed_names):
                cols[i % 4].markdown(
                    f'<span class="feed-pill" style="background:{color_map[fn]}44;'
                    f'border:1px solid {color_map[fn]};color:{color_map[fn]}">'
                    f'{fn}</span>',
                    unsafe_allow_html=True
                )

    # ── TAB 2 — Feed Browser ───────────────────────────────────────────────────
    with t2:
        col_sel, col_srch = st.columns([3, 2])
        with col_sel:
            sel_feed = st.selectbox("Select feed", feed_names)
        with col_srch:
            srch = st.text_input("🔍 Search field name / description", "")

        if sel_feed:
            fields    = feeds[sel_feed]
            feed_pks  = set(pks.get(sel_feed, []))
            fk_out    = {r['from_field']: (r['to_feed'], r['to_field'])
                         for r in filtered if r['from_feed'] == sel_feed}
            ref_by    = [(r['from_feed'], r['from_field'])
                         for r in filtered if r['to_feed'] == sel_feed]

            c = color_map.get(sel_feed, '#028090')
            st.markdown(
                f'<div style="background:{c}18;border-left:3px solid {c};'
                f'padding:10px 16px;border-radius:4px;margin-bottom:12px;">'
                f'<b style="color:{c}">{sel_feed}</b>&nbsp;&nbsp;'
                f'<span style="color:#8890b0;font-size:12px;">'
                f'{len(fields)} fields &bull; {len(feed_pks)} PKs &bull; '
                f'{len(fk_out)} FK out &bull; referenced by {len(ref_by)} fields</span></div>',
                unsafe_allow_html=True
            )
            if ref_by:
                st.caption("Referenced by: " + ", ".join(f"{f}.{fn}" for f, fn in ref_by[:8]))

            rows = []
            for f in fields:
                fn = f['field_name']
                if srch and srch.upper() not in fn and srch.lower() not in f['description'].lower():
                    continue
                role = []
                if fn in feed_pks:
                    role.append("🔑 PK")
                if fn in fk_out:
                    tf, tfd = fk_out[fn]
                    role.append(f"🔗 FK → {tf}.{tfd}")
                rows.append({
                    'Pos':         f['position'],
                    'Field Name':  fn,
                    'Description': f['description'],
                    'Type':        f['data_type'],
                    'Nullable':    f['nullable'],
                    'Role':        ' | '.join(role),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True,
                         height=520, hide_index=True,
                         column_config={
                             'Description': st.column_config.TextColumn(width=300),
                             'Role':        st.column_config.TextColumn(width=220),
                         })

    # ── TAB 3 — Dependency Matrix ──────────────────────────────────────────────
    with t3:
        mat_df = build_matrix(feeds, filtered)

        if mat_df.values.sum() == 0:
            st.info("No relationships above threshold. Lower Min FK Confidence or enable LLM.")
        else:
            active = mat_df.index[(mat_df.sum(axis=1) > 0) | (mat_df.sum(axis=0) > 0)]
            sub    = mat_df.loc[active, active]

            fig = go.Figure(data=go.Heatmap(
                z=sub.values,
                x=sub.columns.tolist(),
                y=sub.index.tolist(),
                colorscale=[[0, '#0b0d14'], [0.001, '#0d1828'], [0.3, '#015060'], [1, '#028090']],
                showscale=True,
                hovertemplate='<b>%{y}</b> → <b>%{x}</b>: %{z} FK(s)<extra></extra>',
                xgap=2, ygap=2,
                colorbar=dict(title="FK count", tickfont=dict(color='#888'))
            ))
            h = max(420, len(active) * 24)
            fig.update_layout(
                title="Feed-to-Feed FK Dependency (row → col)",
                paper_bgcolor='#0b0d14', plot_bgcolor='#0b0d14',
                font_color='#c8cce0', height=h,
                xaxis=dict(tickangle=-50, tickfont=dict(size=10)),
                yaxis=dict(tickfont=dict(size=10), autorange='reversed'),
                margin=dict(l=20, r=20, t=50, b=120)
            )
            st.plotly_chart(fig, use_container_width=True)

    # ── TAB 4 — Relationships ──────────────────────────────────────────────────
    with t4:
        if not filtered:
            st.info("No relationships detected above the confidence threshold.")
        else:
            fa, fb, fc = st.columns(3)
            with fa:
                methods     = sorted({r['method'].split(' — ')[0].split(' +')[0] for r in filtered})
                meth_filter = st.multiselect("Method", methods, default=[])
            with fb:
                feed_filter = st.multiselect("From feed", feed_names, default=[])
            with fc:
                conf_filter = st.slider("Min confidence", 0.0, 1.0, min_conf, 0.05, key="rf_conf")

            disp = [r for r in filtered
                    if (not meth_filter or any(m in r['method'] for m in meth_filter))
                    and (not feed_filter or r['from_feed'] in feed_filter)
                    and r['confidence'] >= conf_filter]

            df_disp = pd.DataFrame(disp).copy()
            if not df_disp.empty:
                df_disp['confidence'] = df_disp['confidence'].map('{:.0%}'.format)

            st.dataframe(df_disp, use_container_width=True, height=520,
                         hide_index=True,
                         column_config={
                             'confidence': st.column_config.TextColumn('Confidence', width=100),
                             'method':     st.column_config.TextColumn('Detection Method', width=220),
                         })
            st.caption(f"{len(disp)} relationships shown")

            col_dl1, col_dl2 = st.columns([1, 4])
            with col_dl1:
                st.download_button(
                    "⬇️ Export Excel",
                    data=to_excel(feeds, pks, filtered),
                    file_name="feed_erd_analysis.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )


if __name__ == "__main__":
    main()
