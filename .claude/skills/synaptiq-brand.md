# Synaptiq Brand Styling

Apply Synaptiq corporate branding to a Streamlit app in this project.

## When to use

Invoke `/synaptiq-brand` when:
- A new Streamlit page or app file needs the Synaptiq look
- You want to refresh an existing page to match the current brand
- The user says "brand this", "apply Synaptiq styling", "style this page"

## What to do

### 1. Ensure `.streamlit/config.toml` exists

If it doesn't exist at the project root, create it:

```toml
[theme]
# Synaptiq brand palette — extracted from AIQ deck (April 2022)
primaryColor     = "#C8956A"   # Synaptiq amber — buttons, links, highlights
backgroundColor  = "#FFFFFF"   # Main canvas
secondaryBackgroundColor = "#EEF3F8"  # Sidebar + expanders — Synaptiq blue tinted
textColor        = "#2D3748"   # Dark charcoal
font             = "sans serif"
```

If it already exists, merge — only add keys that aren't already present.

### 2. Add the CSS block

After `st.set_page_config(...)`, inject this exact CSS block via `st.markdown(..., unsafe_allow_html=True)`:

```python
# ---------------------------------------------------------------------------
# Synaptiq brand styling
# Palette: Blue #8BA4BD | Amber #C8956A | Charcoal #2D3748 | Off-white #EEF3F8

st.markdown("""
<style>
/* ── Brand header bar ─────────────────────────────────────────── */
.synaptiq-header {
    background: linear-gradient(135deg, #8BA4BD 0%, #6B8EAD 100%);
    padding: 1.1rem 2rem 0.9rem 2rem;
    border-radius: 8px;
    margin-bottom: 1.2rem;
    display: flex;
    align-items: center;
    gap: 1rem;
}
.synaptiq-logo-mark {
    width: 38px; height: 38px;
    border: 2.5px solid rgba(200,149,106,0.9);
    border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-size: 18px; color: #C8956A;
    flex-shrink: 0;
}
.synaptiq-wordmark {
    color: #FFFFFF;
    font-size: 1.35rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    line-height: 1;
}
.synaptiq-tagline {
    color: rgba(255,255,255,0.72);
    font-size: 0.72rem;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    margin-top: 2px;
}
.synaptiq-product {
    margin-left: auto;
    text-align: right;
}
.synaptiq-product-name {
    color: #FFFFFF;
    font-size: 0.85rem;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
}

/* ── Tab styling ──────────────────────────────────────────────── */
div[data-testid="stTabs"] button[role="tab"] {
    font-weight: 600;
    font-size: 0.92rem;
    letter-spacing: 0.03em;
    color: #6B8EAD;
}
div[data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
    color: #C8956A !important;
    border-bottom: 3px solid #C8956A;
}

/* ── Metric tiles ─────────────────────────────────────────────── */
div[data-testid="metric-container"] {
    background: #EEF3F8;
    border-left: 4px solid #8BA4BD;
    border-radius: 6px;
    padding: 0.6rem 0.8rem;
}
div[data-testid="metric-container"] label {
    color: #6B8EAD;
    font-size: 0.78rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}
div[data-testid="metric-container"] div[data-testid="metric-value"] {
    color: #2D3748;
    font-weight: 700;
}

/* ── Sidebar ──────────────────────────────────────────────────── */
section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #8BA4BD 0%, #7A96B0 100%);
}
section[data-testid="stSidebar"] * {
    color: #FFFFFF !important;
}
section[data-testid="stSidebar"] .streamlit-expanderHeader {
    color: rgba(255,255,255,0.85) !important;
}

/* ── Buttons ──────────────────────────────────────────────────── */
div[data-testid="stButton"] > button[kind="primary"] {
    background: #C8956A;
    border: none;
    color: white;
    font-weight: 600;
    border-radius: 6px;
}
div[data-testid="stButton"] > button[kind="primary"]:hover {
    background: #B8845A;
    border: none;
}

/* ── Section subheaders ───────────────────────────────────────── */
h3 { color: #8BA4BD; }
h4 { color: #6B8EAD; }

/* ── Divider accent ───────────────────────────────────────────── */
hr { border-top: 1px solid #C8956A33; }

/* ── Success / info boxes ─────────────────────────────────────── */
div[data-testid="stAlert"][data-type="success"] {
    border-left: 4px solid #C8956A;
    background: #FDF5EE;
}
</style>
""", unsafe_allow_html=True)
```

### 3. Replace `st.title(...)` with the branded header

Replace any plain `st.title(...)` call with this HTML block, substituting the product name as appropriate:

```python
st.markdown("""
<div class="synaptiq-header">
  <div class="synaptiq-logo-mark">&#9678;</div>
  <div>
    <div class="synaptiq-wordmark">Synaptiq</div>
    <div class="synaptiq-tagline">The Humankind of AI</div>
  </div>
  <div class="synaptiq-product">
    <div class="synaptiq-product-name">Data Profiling Tool</div>
  </div>
</div>
""", unsafe_allow_html=True)
```

### 4. Brand the sidebar

Replace any plain `st.sidebar.title(...)` with:

```python
st.sidebar.markdown("""
<div style='text-align:center; padding: 0.5rem 0 0.8rem 0;'>
  <div style='font-size:1.1rem; font-weight:700; letter-spacing:0.05em;
              color:#FFFFFF;'>Synaptiq</div>
  <div style='font-size:0.62rem; letter-spacing:0.14em; text-transform:uppercase;
              color:rgba(255,255,255,0.65); margin-top:2px;'>YOUR PRODUCT NAME</div>
</div>
""", unsafe_allow_html=True)
```

## Brand palette reference

| Token | Hex | Usage |
|---|---|---|
| Synaptiq Blue | `#8BA4BD` | Header gradient, sidebar, metric borders, `h3` |
| Synaptiq Blue Dark | `#6B8EAD` | Inactive tabs, `h4`, label text |
| Synaptiq Amber | `#C8956A` | Primary buttons, active tab underline, logo mark border, product name |
| Synaptiq Amber Dark | `#B8845A` | Button hover state |
| Off-white | `#EEF3F8` | Metric tile backgrounds, secondary background |
| Charcoal | `#2D3748` | Primary body text, metric values |
| White | `#FFFFFF` | Header and sidebar text, canvas background |

## Source

Palette extracted from Synaptiq AIQ deck (April 2022):
- Slides use a steel blue-grey (`#8BA4BD`) as the dominant background colour
- Warm copper-orange (`#C8956A`) appears as the accent in the AIQ logo ring and date text
- Clean white text on dark backgrounds; dark charcoal on white backgrounds
