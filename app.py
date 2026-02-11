# -*- coding: utf-8 -*-
"""
Dashboard Financeiro — Streamlit (Google Sheets) — versão SaaS (single-file)

Leitura das abas (mesmos nomes do Excel/Sheets):
- 1. Saldo Inicial (opcional)
- 4. Entradas
- 5. Saídas
- 6. Transferencias
- 7. Conciliação (opcional)

Secrets (Streamlit Cloud -> App -> Settings -> Secrets):
- company_name = "..."
- finance_sheet_id = "ID ou link"   (aceita também: sheet_id)
- logo_url = "https://..."          (opcional)
- [gcp_service_account] ...         (json inline ou json_path)

Observação crítica:
- st.set_page_config() precisa ser o PRIMEIRO comando Streamlit do arquivo.
"""

# ====================== STREAMLIT CONFIG (DEVE SER O PRIMEIRO) ======================
import streamlit as st
st.set_page_config(page_title="Dashboard Financeiro", layout="wide")

# ====================== IMPORTS ======================
import os
import re
import json
import unicodedata
from datetime import datetime, date
from typing import Optional, Tuple, List

import numpy as np
import pandas as pd
import altair as alt

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ====================== BRANDING / SECRETS ======================
COMPANY_NAME = st.secrets.get("company_name", "Dashboard Financeiro")
LOGO_URL = st.secrets.get("logo_url", "")

# ====================== UI (CSS) ======================
st.markdown(
    """
<style>
:root{
  --bg:#0b1220;--panel:#0f1729;--card:#111c33;--card2:#0f1729;
  --txt:#e8eefc;--mut:#9db0d5;--line:#1f2b45;
  --good:#23c55e;--bad:#ef4444;--warn:#f59e0b;--info:#3b82f6;
}
html, body, [data-testid="stAppViewContainer"]{background:var(--bg)!important;}
.block-container{padding-top:1.2rem; padding-bottom:2rem;}
h1,h2,h3,h4{color:var(--txt)!important;}
p,li,span,div,label{color:var(--txt);}
.small{color:var(--mut);font-size:12px;}
.hr{height:1px;background:var(--line);margin:10px 0 18px;}
/* cards */
.kpi{background:linear-gradient(180deg,var(--card),var(--card2));border:1px solid var(--line);border-radius:14px;
     padding:14px 16px;min-width:220px;box-shadow:0 4px 24px rgba(0,0,0,.25);}
.kpi .t{font-weight:800;color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.kpi .v{font-weight:900;font-size:28px;margin-top:6px}
.kpi .s{margin-top:6px;color:var(--mut);font-weight:700;font-size:12px}
.badge{display:inline-block;padding:4px 10px;border-radius:999px;border:1px solid var(--line);font-weight:800;font-size:12px}
.badge.good{background:rgba(35,197,94,.12);color:var(--good);border-color:rgba(35,197,94,.35)}
.badge.bad{background:rgba(239,68,68,.12);color:var(--bad);border-color:rgba(239,68,68,.35)}
.badge.warn{background:rgba(245,158,11,.12);color:var(--warn);border-color:rgba(245,158,11,.35)}
.badge.info{background:rgba(59,130,246,.12);color:var(--info);border-color:rgba(59,130,246,.35)}
.panel{background:linear-gradient(180deg,var(--card),var(--panel));border:1px solid var(--line);border-radius:14px;
       padding:14px 16px;margin-top:10px;}
.section-title{margin:2px 0 10px;font-weight:900;font-size:15px;color:var(--txt)}
/* sidebar */
[data-testid="stSidebar"]{background:#0a1020;border-right:1px solid var(--line);}
[data-testid="stSidebar"] *{color:var(--txt)!important;}
/* table */
[data-testid="stDataFrame"]{border:1px solid var(--line);border-radius:12px;overflow:hidden;}
</style>
""",
    unsafe_allow_html=True,
)

def st_kpi(title: str, value: str, sub: str = "", badge: Optional[Tuple[str, str]] = None):
    b = ""
    if badge:
        text, klass = badge
        b = f"<span class='badge {klass}'>{text}</span>"
    st.markdown(
        f"""
<div class="kpi">
  <div class="t">{title}</div>
  <div class="v">{value}</div>
  <div class="s">{sub} {b}</div>
</div>
""",
        unsafe_allow_html=True,
    )

# ====================== HELPERS ======================
ID_RE = re.compile(r"/d/([a-zA-Z0-9-_]+)")

def _sheet_id(s: str) -> Optional[str]:
    s = (s or "").strip()
    m = ID_RE.search(s)
    if m:
        return m.group(1)
    return s if re.fullmatch(r"[A-Za-z0-9-_]{20,}", s) else None

def _strip_accents(s: str) -> str:
    if s is None:
        return ""
    return "".join(ch for ch in unicodedata.normalize("NFKD", str(s)) if not unicodedata.combining(ch))

def _norm_col(c: str) -> str:
    c = _strip_accents(str(c)).upper().strip()
    c = re.sub(r"\s+", " ", c)
    return c

def _upper(x):
    return str(x).upper().strip() if pd.notna(x) else ""

def parse_date_any(x):
    if pd.isna(x) or x == "":
        return pd.NaT
    if isinstance(x, (datetime, date)):
        return x.date() if isinstance(x, datetime) else x
    s = str(x).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass
    try:
        dt = pd.to_datetime(s, dayfirst=True, errors="coerce")
        return dt.date() if pd.notna(dt) else pd.NaT
    except Exception:
        return pd.NaT

def money_to_float(x) -> float:
    if pd.isna(x) or x == "":
        return 0.0
    if isinstance(x, (int, float, np.number)):
        return float(x)
    s = str(x).strip().replace("R$", "").replace("\u00a0", " ").strip()
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except Exception:
        return 0.0

def fmt_brl(x) -> str:
    try:
        v = float(x)
    except Exception:
        v = 0.0
    s = f"{v:,.2f}"
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"

def month_label(ym: str) -> str:
    if not ym or len(ym) != 7:
        return ym
    return f"{ym[5:7]}/{ym[:4]}"

def to_ym(d: date) -> str:
    return f"{d.year}-{d.month:02d}"

def safe_cols(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    return df

# ====================== GOOGLE SHEETS CLIENT ======================
def _load_sa_info() -> dict:
    try:
        block = st.secrets["gcp_service_account"]
    except Exception:
        st.error("Não encontrei [gcp_service_account] no Secrets.")
        st.stop()

    if isinstance(block, dict) and "json_path" in block:
        path = block["json_path"]
        if not os.path.isabs(path):
            path = os.path.join(os.path.dirname(__file__), path)
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            st.error(f"Não consegui abrir o JSON da service account: {path}")
            st.exception(e)
            st.stop()

    return dict(block)

@st.cache_resource(show_spinner=False)
def make_client():
    info = _load_sa_info()
    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(info, scopes)
    return gspread.authorize(creds)

client = make_client()

SHEET_ID = _sheet_id(st.secrets.get("finance_sheet_id", "") or st.secrets.get("sheet_id", ""))
if not SHEET_ID:
    st.error("Faltou `finance_sheet_id` (ou `sheet_id`) no Secrets. Pode colar o LINK ou o ID.")
    st.stop()

TAB_ENT   = "4. Entradas"
TAB_SAI   = "5. Saídas"
TAB_TRF   = "6. Transferencias"

@st.cache_data(ttl=300, show_spinner=False)
def read_tab(sheet_id: str, tab: str) -> pd.DataFrame:
    sh = client.open_by_key(sheet_id)
    ws = sh.worksheet(tab)
    rows = ws.get_all_records()
    df = pd.DataFrame(rows) if rows else pd.DataFrame()
    return safe_cols(df)

# ====================== NORMALIZERS ======================
def normalize_entradas(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    df.columns = [_norm_col(c) for c in df.columns]

    col_data = "DATA" if "DATA" in df.columns else None
    col_capt = "CAPTACAO" if "CAPTACAO" in df.columns else None
    col_meio = "MEIO" if "MEIO" in df.columns else None
    col_area = "AREA" if "AREA" in df.columns else None
    col_prod = "PRODUTO" if "PRODUTO" in df.columns else None

    col_val = None
    if "R$ ENTRADA" in df.columns:
        col_val = "R$ ENTRADA"
    else:
        cands = [c for c in df.columns if ("ENTRADA" in c and "R$" in c)]
        if cands:
            col_val = cands[0]
        elif "VALOR" in df.columns:
            col_val = "VALOR"

    dist_cols = [c for c in df.columns if c.startswith("R$") and c != col_val]

    df["_DATA"] = df[col_data].apply(parse_date_any) if col_data else pd.NaT
    df["YM"] = df["_DATA"].apply(lambda d: to_ym(d) if isinstance(d, date) else None)

    df["CAPTACAO"] = df[col_capt].astype(str).map(_upper) if col_capt else ""
    df["MEIO"]     = df[col_meio].astype(str).map(_upper) if col_meio else ""
    df["AREA"]     = df[col_area].astype(str).map(_upper) if col_area else ""
    df["PRODUTO"]  = df[col_prod].astype(str).map(_upper) if col_prod else ""
    df["VALOR"]    = df[col_val].apply(money_to_float) if col_val else 0.0

    for c in dist_cols:
        df[c] = df[c].apply(money_to_float)

    df = df[df["_DATA"].notna()].copy()
    df = df[df["VALOR"] != 0].copy()

    keep_extra = [c for c in df.columns if c not in {"_DATA","YM","CAPTACAO","MEIO","AREA","PRODUTO","VALOR"}]
    base = df[["_DATA","YM","CAPTACAO","MEIO","AREA","PRODUTO","VALOR"] + dist_cols + keep_extra].copy()
    base = base.rename(columns={"_DATA":"DATA"})
    return base

def normalize_saidas(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    df.columns = [_norm_col(c) for c in df.columns]

    col_venc = "VENCIMENTO" if "VENCIMENTO" in df.columns else None
    col_pag  = "PAGAMENTO" if "PAGAMENTO" in df.columns else None
    col_cont = "CONTA" if "CONTA" in df.columns else None
    col_banc = "BANCO" if "BANCO" in df.columns else None
    col_obj  = "OBJETO" if "OBJETO" in df.columns else ("OBJETIVO" if "OBJETIVO" in df.columns else None)
    col_tipo = "TIPO" if "TIPO" in df.columns else None
    col_doc  = "DOCUMENTO" if "DOCUMENTO" in df.columns else None
    col_ind  = "INDIRETO" if "INDIRETO" in df.columns else None

    col_val = "R$ VALOR" if "R$ VALOR" in df.columns else None
    if not col_val:
        cands = [c for c in df.columns if ("VALOR" in c and "R$" in c)]
        if cands:
            col_val = cands[0]
        elif "VALOR" in df.columns:
            col_val = "VALOR"

    df["_VENC"] = df[col_venc].apply(parse_date_any) if col_venc else pd.NaT
    df["_PAG"]  = df[col_pag].apply(parse_date_any) if col_pag else pd.NaT
    df["DATA_REF"] = df["_PAG"].where(df["_PAG"].notna(), df["_VENC"])
    df["YM"] = df["DATA_REF"].apply(lambda d: to_ym(d) if isinstance(d, date) else None)

    df["CONTA"]     = df[col_cont].astype(str).map(_upper) if col_cont else ""
    df["BANCO"]     = df[col_banc].astype(str).map(_upper) if col_banc else ""
    df["TIPO"]      = df[col_tipo].astype(str).map(_upper) if col_tipo else ""
    df["DOCUMENTO"] = df[col_doc].astype(str).map(_upper) if col_doc else ""
    df["OBJETO"]    = df[col_obj].astype(str).map(_upper) if col_obj else ""
    df["INDIRETO"]  = df[col_ind].astype(str).map(_upper) if col_ind else ""
    df["VALOR"]     = df[col_val].apply(money_to_float) if col_val else 0.0

    df = df[df["DATA_REF"].notna()].copy()
    df = df[df["VALOR"] != 0].copy()
    return df

def normalize_transferencias(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    df.columns = [_norm_col(c) for c in df.columns]

    col_data = "DATA" if "DATA" in df.columns else None
    col_or   = "ORIGEM" if "ORIGEM" in df.columns else None
    col_de   = "DESTINO" if "DESTINO" in df.columns else None
    col_val  = "VALOR" if "VALOR" in df.columns else None
    if not col_val:
        cands = [c for c in df.columns if "VALOR" in c]
        col_val = cands[0] if cands else None

    df["DATA"] = df[col_data].apply(parse_date_any) if col_data else pd.NaT
    df["YM"] = df["DATA"].apply(lambda d: to_ym(d) if isinstance(d, date) else None)

    df["ORIGEM"]  = df[col_or].astype(str).map(_upper) if col_or else ""
    df["DESTINO"] = df[col_de].astype(str).map(_upper) if col_de else ""
    df["VALOR"]   = df[col_val].apply(money_to_float) if col_val else 0.0

    df = df[df["DATA"].notna()].copy()
    df = df[df["VALOR"] != 0].copy()
    return df

def compute_fluxo_caixa(df_ent: pd.DataFrame, df_sai: pd.DataFrame) -> pd.DataFrame:
    ent_day = (df_ent.groupby("DATA")["VALOR"].sum().reset_index().rename(columns={"VALOR":"ENTRADAS"})) if not df_ent.empty else pd.DataFrame(columns=["DATA","ENTRADAS"])
    sai_day = (df_sai.groupby("DATA_REF")["VALOR"].sum().reset_index().rename(columns={"DATA_REF":"DATA","VALOR":"SAIDAS"})) if not df_sai.empty else pd.DataFrame(columns=["DATA","SAIDAS"])
    base = ent_day.merge(sai_day, on="DATA", how="outer").fillna(0.0)
    base["SALDO_DIA"] = base["ENTRADAS"] - base["SAIDAS"]
    base = base.sort_values("DATA")
    base["YM"] = base["DATA"].apply(lambda d: to_ym(d) if isinstance(d, date) else None)
    return base

# ====================== LOAD DATA ======================
with st.spinner("Carregando planilha..."):
    df_ent_raw = read_tab(SHEET_ID, TAB_ENT)
    df_sai_raw = read_tab(SHEET_ID, TAB_SAI)
    df_trf_raw = read_tab(SHEET_ID, TAB_TRF)

df_ent = normalize_entradas(df_ent_raw)
df_sai = normalize_saidas(df_sai_raw)
df_trf = normalize_transferencias(df_trf_raw)

months = sorted(list(set([m for m in df_ent.get("YM", []) if m] + [m for m in df_sai.get("YM", []) if m])))
if not months:
    st.error("Não encontrei datas válidas nas abas de Entradas/Saídas.")
    st.stop()

# ====================== SIDEBAR ======================
st.sidebar.markdown(f"### {COMPANY_NAME}")
if LOGO_URL:
    st.sidebar.image(LOGO_URL, use_container_width=True)
st.sidebar.markdown("<div class='small'>Financeiro • Streamlit</div>", unsafe_allow_html=True)
st.sidebar.markdown("<div class='hr'></div>", unsafe_allow_html=True)

PAGES = [("Dashboard","📊"),("Entradas","💚"),("Saídas","💸"),("Investimentos","🟨"),("Fluxo de Caixa","💧"),("Conciliação","🧾"),("Exportar","⬇️")]
page = st.sidebar.radio("Menu", [f"{ico}  {name}" for name, ico in PAGES], index=0)

# ====================== FILTERS ======================
st.markdown(f"# {COMPANY_NAME}")
st.markdown("<div class='small'>Painel financeiro (Google Sheets) • Layout estilo sistema</div>", unsafe_allow_html=True)

c1, c2, c3, c4 = st.columns([2, 3, 3, 3])
with c1:
    month_label_map = {month_label(m): m for m in months}
    default_m = months[-1]
    sel_month_label = st.selectbox("Mês", options=list(month_label_map.keys()),
                                   index=list(month_label_map.values()).index(default_m))
    ym_sel = month_label_map[sel_month_label]

dates_in_month: List[date] = []
if not df_ent.empty:
    dates_in_month += [d for d in df_ent[df_ent["YM"] == ym_sel]["DATA"].tolist() if isinstance(d, date)]
if not df_sai.empty:
    dates_in_month += [d for d in df_sai[df_sai["YM"] == ym_sel]["DATA_REF"].tolist() if isinstance(d, date)]
dmin = min(dates_in_month) if dates_in_month else None
dmax = max(dates_in_month) if dates_in_month else None

with c2:
    if dmin and dmax:
        dr = st.date_input("Período", value=(dmin, dmax), format="DD/MM/YYYY")
        dt_ini, dt_fim = (dr if isinstance(dr, tuple) and len(dr) == 2 else (dmin, dmax))
    else:
        dt_ini, dt_fim = None, None
        st.caption("Sem datas suficientes para filtrar período.")

with c3:
    capt_opts = sorted(df_ent[df_ent["YM"] == ym_sel]["CAPTACAO"].dropna().unique().tolist()) if not df_ent.empty else []
    capt_sel = st.multiselect("Captação", options=capt_opts, default=capt_opts)

with c4:
    banco_opts = sorted(df_sai[df_sai["YM"] == ym_sel]["BANCO"].dropna().unique().tolist()) if not df_sai.empty else []
    banco_sel = st.multiselect("Banco", options=banco_opts, default=banco_opts)

def apply_filters():
    ent = df_ent[df_ent["YM"] == ym_sel].copy() if not df_ent.empty else df_ent.copy()
    sai = df_sai[df_sai["YM"] == ym_sel].copy() if not df_sai.empty else df_sai.copy()
    trf = df_trf[df_trf["YM"] == ym_sel].copy() if not df_trf.empty else df_trf.copy()

    if dt_ini and dt_fim:
        if not ent.empty:
            ent = ent[(ent["DATA"] >= dt_ini) & (ent["DATA"] <= dt_fim)].copy()
        if not sai.empty:
            sai = sai[(sai["DATA_REF"] >= dt_ini) & (sai["DATA_REF"] <= dt_fim)].copy()
        if not trf.empty:
            trf = trf[(trf["DATA"] >= dt_ini) & (trf["DATA"] <= dt_fim)].copy()

    if capt_sel and not ent.empty:
        ent = ent[ent["CAPTACAO"].isin([_upper(x) for x in capt_sel])].copy()
    if banco_sel and not sai.empty:
        sai = sai[sai["BANCO"].isin([_upper(x) for x in banco_sel])].copy()

    return ent, sai, trf

ent_f, sai_f, trf_f = apply_filters()

# KPIs
ent_total = float(ent_f["VALOR"].sum()) if not ent_f.empty else 0.0
sai_total = float(sai_f["VALOR"].sum()) if not sai_f.empty else 0.0
inv_mask = pd.Series([False] * len(sai_f))
if not sai_f.empty:
    inv_mask = (
        sai_f["CONTA"].astype(str).str.contains("INVEST", na=False) |
        sai_f["INDIRETO"].astype(str).str.contains("INVEST", na=False) |
        sai_f["OBJETO"].astype(str).str.contains("INVEST", na=False)
    )
inv_total = float(sai_f.loc[inv_mask, "VALOR"].sum()) if not sai_f.empty else 0.0
desp_total = sai_total - inv_total
lucro_liq = ent_total - sai_total

# ====================== PAGES ======================
if page.startswith("📊"):
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Resumo do período")
    cA, cB, cC, cD, cE = st.columns(5)
    with cA: st_kpi("Receita Total", fmt_brl(ent_total), sub=f"Mês {sel_month_label}")
    with cB: st_kpi("Despesas", fmt_brl(desp_total), sub="Saídas sem investimentos")
    with cC: st_kpi("Investimentos", fmt_brl(inv_total), sub="Detecção automática", badge=("revisável", "warn"))
    with cD: st_kpi("Total de Saídas", fmt_brl(sai_total), sub="Despesas + investimentos")
    with cE:
        badge = ("positivo", "good") if lucro_liq >= 0 else ("negativo", "bad")
        st_kpi("Resultado Líquido", fmt_brl(lucro_liq), sub="Receita - Saídas", badge=badge)

    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Evolução (mensal)")
    m_ent = (df_ent.groupby("YM")["VALOR"].sum().reset_index().rename(columns={"VALOR":"Receitas"})) if not df_ent.empty else pd.DataFrame(columns=["YM","Receitas"])
    m_sai = (df_sai.groupby("YM")["VALOR"].sum().reset_index().rename(columns={"VALOR":"Saídas"})) if not df_sai.empty else pd.DataFrame(columns=["YM","Saídas"])
    evo = m_ent.merge(m_sai, on="YM", how="outer").fillna(0.0)
    evo["Resultado"] = evo["Receitas"] - evo["Saídas"]
    evo = evo.sort_values("YM")
    evo["Mês"] = evo["YM"].map(month_label)
    evo_melt = evo.melt(id_vars=["YM","Mês"], value_vars=["Receitas","Saídas","Resultado"], var_name="Métrica", value_name="Valor")
    st.altair_chart(
        alt.Chart(evo_melt).mark_bar().encode(
            x=alt.X("Mês:N", sort=list(evo["Mês"]), title=""),
            y=alt.Y("Valor:Q", title="R$"),
            color=alt.Color("Métrica:N", legend=alt.Legend(title="")),
            tooltip=["Mês","Métrica",alt.Tooltip("Valor:Q", format=",.2f")],
        ).properties(height=320),
        use_container_width=True
    )

    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Detalhamento (amostra)")
    t1, t2 = st.columns(2)
    with t1:
        show_ent = ent_f.sort_values("DATA", ascending=False).head(250).copy()
        if not show_ent.empty:
            show_ent["R$ Entrada"] = show_ent["VALOR"].map(fmt_brl)
        st.dataframe(show_ent.drop(columns=["VALOR"], errors="ignore"), use_container_width=True, hide_index=True)
    with t2:
        show_sai = sai_f.sort_values("DATA_REF", ascending=False).head(250).copy()
        if not show_sai.empty:
            show_sai["R$ Valor"] = show_sai["VALOR"].map(fmt_brl)
        st.dataframe(show_sai.drop(columns=["VALOR"], errors="ignore"), use_container_width=True, hide_index=True)

elif page.startswith("💚"):
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Entradas — visão analítica")
    daily = ent_f.groupby("DATA")["VALOR"].sum().reset_index().sort_values("DATA")
    if not daily.empty:
        st.altair_chart(
            alt.Chart(daily).mark_line(point=True).encode(
                x=alt.X("DATA:T", title="Data"),
                y=alt.Y("VALOR:Q", title="R$"),
                tooltip=[alt.Tooltip("DATA:T", title="Data"), alt.Tooltip("VALOR:Q", format=",.2f", title="R$")],
            ).properties(height=320),
            use_container_width=True
        )
    out = ent_f.sort_values("DATA", ascending=False).copy()
    if not out.empty:
        out["R$ Entrada"] = out["VALOR"].map(fmt_brl)
    st.dataframe(out.drop(columns=["VALOR"], errors="ignore"), use_container_width=True, hide_index=True)

elif page.startswith("💸"):
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Saídas — visão analítica")
    daily = sai_f.groupby("DATA_REF")["VALOR"].sum().reset_index().sort_values("DATA_REF")
    if not daily.empty:
        st.altair_chart(
            alt.Chart(daily).mark_line(point=True).encode(
                x=alt.X("DATA_REF:T", title="Data"),
                y=alt.Y("VALOR:Q", title="R$"),
                tooltip=[alt.Tooltip("DATA_REF:T", title="Data"), alt.Tooltip("VALOR:Q", format=",.2f", title="R$")],
            ).properties(height=320),
            use_container_width=True
        )
    out = sai_f.sort_values("DATA_REF", ascending=False).copy()
    if not out.empty:
        out["R$ Valor"] = out["VALOR"].map(fmt_brl)
    st.dataframe(out.drop(columns=["VALOR"], errors="ignore"), use_container_width=True, hide_index=True)

elif page.startswith("🟨"):
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Investimentos (detecção automática)")
    inv = sai_f.loc[inv_mask].copy() if not sai_f.empty else pd.DataFrame()
    c1, c2 = st.columns(2)
    with c1: st_kpi("Total investimentos", fmt_brl(inv["VALOR"].sum() if not inv.empty else 0))
    with c2: st_kpi("Lançamentos", str(int(len(inv))))
    inv_out = inv.sort_values("DATA_REF", ascending=False).copy()
    if not inv_out.empty:
        inv_out["R$ Valor"] = inv_out["VALOR"].map(fmt_brl)
    st.dataframe(inv_out.drop(columns=["VALOR"], errors="ignore"), use_container_width=True, hide_index=True)

elif page.startswith("💧"):
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Fluxo de Caixa")
    fluxo = compute_fluxo_caixa(ent_f, sai_f)
    if fluxo.empty:
        st.info("Sem dados suficientes para fluxo.")
    else:
        melt = fluxo.melt(id_vars=["DATA"], value_vars=["ENTRADAS","SAIDAS","SALDO_DIA"], var_name="Métrica", value_name="Valor")
        st.altair_chart(
            alt.Chart(melt).mark_line(point=True).encode(
                x=alt.X("DATA:T", title="Data"),
                y=alt.Y("Valor:Q", title="R$"),
                color=alt.Color("Métrica:N", legend=alt.Legend(title="")),
                tooltip=[alt.Tooltip("DATA:T", title="Data"), "Métrica", alt.Tooltip("Valor:Q", format=",.2f")]
            ).properties(height=360),
            use_container_width=True
        )

elif page.startswith("🧾"):
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Conciliação (por banco)")
    if sai_f.empty:
        st.info("Sem saídas no período.")
    else:
        by_bank_out = sai_f.groupby("BANCO")["VALOR"].sum().reset_index().rename(columns={"VALOR":"SAÍDAS"})
        if not trf_f.empty:
            trf_out = trf_f.groupby("ORIGEM")["VALOR"].sum().reset_index().rename(columns={"ORIGEM":"BANCO","VALOR":"TRANSFER_OUT"})
            trf_in  = trf_f.groupby("DESTINO")["VALOR"].sum().reset_index().rename(columns={"DESTINO":"BANCO","VALOR":"TRANSFER_IN"})
        else:
            trf_out = pd.DataFrame(columns=["BANCO","TRANSFER_OUT"])
            trf_in  = pd.DataFrame(columns=["BANCO","TRANSFER_IN"])
        conc = by_bank_out.merge(trf_out, on="BANCO", how="outer").merge(trf_in, on="BANCO", how="outer").fillna(0.0)
        conc["MOV_LIQ_TRF"] = conc["TRANSFER_IN"] - conc["TRANSFER_OUT"]
        conc = conc.sort_values("SAÍDAS", ascending=False)
        conc_show = conc.copy()
        for c in ["SAÍDAS","TRANSFER_OUT","TRANSFER_IN","MOV_LIQ_TRF"]:
            conc_show[c] = conc_show[c].map(fmt_brl)
        st.dataframe(conc_show, use_container_width=True, hide_index=True)

else:
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Exportar (CSV)")
    ent_out = ent_f.copy()
    if not ent_out.empty:
        ent_out["R$ Entrada"] = ent_out["VALOR"].map(fmt_brl)
    st.download_button("Baixar Entradas (CSV)", data=ent_out.drop(columns=["VALOR"], errors="ignore").to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"entradas_{ym_sel}.csv", mime="text/csv")

    sai_out = sai_f.copy()
    if not sai_out.empty:
        sai_out["R$ Valor"] = sai_out["VALOR"].map(fmt_brl)
    st.download_button("Baixar Saídas (CSV)", data=sai_out.drop(columns=["VALOR"], errors="ignore").to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"saidas_{ym_sel}.csv", mime="text/csv")

    trf_out = trf_f.copy()
    if not trf_out.empty:
        trf_out["R$"] = trf_out["VALOR"].map(fmt_brl)
    st.download_button("Baixar Transferências (CSV)", data=trf_out.drop(columns=["VALOR"], errors="ignore").to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"transferencias_{ym_sel}.csv", mime="text/csv")
