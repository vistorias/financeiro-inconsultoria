# -*- coding: utf-8 -*-
"""
Dashboard Financeiro — Streamlit (Google Sheets) — versão SaaS (single-file)

Abas (nomes iguais ao Excel/Sheets):
- 4. Entradas
- 5. Saídas
- 6. Transferencias

Secrets (Streamlit Cloud -> App -> Settings -> Secrets):
- company_name = "In Consultoria"         (opcional)
- finance_sheet_id = "ID ou link"         (obrigatório)
- logo_url = "https://..."                (opcional)
- [gcp_service_account] ...               (obrigatório)
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
from typing import Optional, Tuple, List, Dict

import numpy as np
import pandas as pd
import altair as alt

import gspread
from google.oauth2.service_account import Credentials

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
  --ctrl:#0f1729; --ctrl2:#0a1020; --accent:#ff3b3b;
}
html, body, [data-testid="stAppViewContainer"]{background:var(--bg)!important;}
.block-container{padding-top:1.2rem; padding-bottom:2rem; max-width: 1500px;}
h1,h2,h3,h4{color:var(--txt)!important;}
p,li,span,div,label{color:var(--txt);}
.small{color:var(--mut);font-size:12px;}
.hr{height:1px;background:var(--line);margin:10px 0 18px;}
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
[data-testid="stSidebar"]{background:#0a1020;border-right:1px solid var(--line);}
[data-testid="stSidebar"] *{color:var(--txt)!important;}

/* ---------- Controles (selectbox, multiselect, date_input) com fundo sólido ---------- */
div[data-baseweb="select"] > div{background:var(--ctrl)!important;border-color:var(--line)!important;}
div[data-baseweb="select"] *{color:var(--txt)!important;}
div[data-baseweb="popover"]{background:var(--ctrl)!important; border:1px solid var(--line)!important; border-radius:12px!important;}
ul[role="listbox"]{background:var(--ctrl)!important;}
li[role="option"]{background:var(--ctrl)!important; color:var(--txt)!important;}
li[role="option"]:hover{background:#111c33!important;}
div[data-baseweb="calendar"]{background:var(--ctrl)!important;}
div[data-testid="stDateInput"] input{background:var(--ctrl)!important; color:var(--txt)!important; border-color:var(--line)!important;}
div[data-testid="stMultiSelect"] div[data-baseweb="tag"]{background:rgba(255,59,59,.15)!important;border:1px solid rgba(255,59,59,.35)!important;}
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
    """Converte o que vier do Sheets/Excel em date (ou NaT)."""
    if pd.isna(x) or x == "":
        return pd.NaT
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    if isinstance(x, pd.Timestamp):
        try:
            return x.to_pydatetime().date()
        except Exception:
            return pd.NaT
    # números (às vezes o Sheets vem como serial)
    if isinstance(x, (int, float, np.number)) and not pd.isna(x):
        try:
            dt = pd.to_datetime(float(x), unit="D", origin="1899-12-30", errors="coerce")
            return dt.date() if pd.notna(dt) else pd.NaT
        except Exception:
            return pd.NaT
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
    s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"

def month_label(ym: str) -> str:
    if not ym or len(ym) != 7:
        return ym
    return f"{ym[5:7]}/{ym[:4]}"

def to_ym(d) -> Optional[str]:
    """Aceita date/datetime/Timestamp; retorna YYYY-MM (ou None)."""
    if d is None or pd.isna(d):
        return None
    try:
        y = int(getattr(d, "year"))
        m = int(getattr(d, "month"))
        if m < 1 or m > 12:
            return None
        return f"{y}-{m:02d}"
    except Exception:
        return None

def pick_col(cols_norm: List[str], *names: str) -> Optional[str]:
    for n in names:
        if n in cols_norm:
            return n
    return None

def safe_num(v):
    try:
        return float(v)
    except Exception:
        return 0.0

# ====================== GOOGLE SHEETS CLIENT ======================
def _load_sa_info() -> dict:
    try:
        block = st.secrets["gcp_service_account"]
    except Exception:
        st.error("Não encontrei [gcp_service_account] no Secrets do Streamlit.")
        st.stop()
    if isinstance(block, dict) and "json_path" in block:
        path = block["json_path"]
        if not os.path.isabs(path):
            path = os.path.join(os.path.dirname(__file__), path)
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return dict(block)

@st.cache_resource(show_spinner=False)
def make_client():
    info = _load_sa_info()
    creds = Credentials.from_service_account_info(
        info,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive.readonly",
        ],
    )
    return gspread.authorize(creds)

client = make_client()

SHEET_ID = _sheet_id(st.secrets.get("finance_sheet_id", "") or st.secrets.get("sheet_id", ""))
if not SHEET_ID:
    st.error("Faltou `finance_sheet_id` (ou `sheet_id`) no Secrets. Cole o LINK ou o ID.")
    st.stop()

TAB_ENT = "4. Entradas"
TAB_SAI = "5. Saídas"
TAB_TRF = "6. Transferencias"

@st.cache_data(ttl=300, show_spinner=False)
def read_tab(sheet_id: str, tab: str) -> pd.DataFrame:
    """Leitura robusta (evita erros do get_all_records quando há cabeçalhos duplicados/vazios)."""
    sh = client.open_by_key(sheet_id)
    ws = sh.worksheet(tab)
    values = ws.get_all_values()
    if not values or len(values) < 2:
        return pd.DataFrame()
    header = [h.strip() for h in values[0]]
    rows = values[1:]
    df = pd.DataFrame(rows, columns=header)
    df = df.loc[:, [c for c in df.columns if str(c).strip() != ""]]
    df = df.replace("", np.nan).dropna(how="all").fillna("")
    return df

# ====================== NORMALIZERS ======================
def normalize_entradas(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    cols_norm = [_norm_col(c) for c in df.columns]
    df.columns = cols_norm

    col_data = pick_col(cols_norm, "DATA RECEBIMENTO", "DATA", "RECEBIMENTO")
    col_venc = pick_col(cols_norm, "DATA VENCIMENTO", "VENCIMENTO")
    col_val  = pick_col(cols_norm, "VALOR", "R$ ENTRADA", "R$ENTRADA", "R$")

    c_cliente = pick_col(cols_norm, "CLIENTE", "CLIENTES")
    c_plano   = pick_col(cols_norm, "PLANO DE CONTAS", "PLANO DE CONTA", "CONTA")
    c_desc    = pick_col(cols_norm, "DESCRICAO", "DESCRIÇÃO", "HISTORICO", "HISTÓRICO", "OBS", "OBSERVACAO", "OBSERVAÇÃO")
    c_meio    = pick_col(cols_norm, "MEIO")
    c_area    = pick_col(cols_norm, "AREA")
    c_prod    = pick_col(cols_norm, "PRODUTO")
    c_capt    = pick_col(cols_norm, "CAPTACAO", "CAPTAÇÃO")

    df["DATA"] = df[col_data].apply(parse_date_any) if col_data else pd.NaT
    df["VENCIMENTO"] = df[col_venc].apply(parse_date_any) if col_venc else pd.NaT
    df["VALOR"] = df[col_val].apply(money_to_float) if col_val else 0.0

    df["CLIENTE"] = df[c_cliente].astype(str).map(_upper) if c_cliente else ""
    df["PLANO_CONTAS"] = df[c_plano].astype(str).map(_upper) if c_plano else ""
    df["DESCRICAO"] = df[c_desc].astype(str) if c_desc else ""
    df["MEIO"] = df[c_meio].astype(str).map(_upper) if c_meio else ""
    df["AREA"] = df[c_area].astype(str).map(_upper) if c_area else ""
    df["PRODUTO"] = df[c_prod].astype(str).map(_upper) if c_prod else ""
    df["CAPTACAO"] = df[c_capt].astype(str).map(_upper) if c_capt else ""

    if (df["CAPTACAO"] == "").all():
        df["CAPTACAO"] = df["CLIENTE"]

    df["YM"] = df["DATA"].apply(to_ym)

    df = df[df["DATA"].notna()].copy()
    df = df[df["VALOR"] != 0].copy()

    keep = ["DATA", "YM", "VENCIMENTO", "CAPTACAO", "CLIENTE", "PLANO_CONTAS", "MEIO", "AREA", "PRODUTO", "DESCRICAO", "VALOR"]
    keep = [c for c in keep if c in df.columns]
    return df[keep].copy()

def normalize_saidas(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    cols_norm = [_norm_col(c) for c in df.columns]
    df.columns = cols_norm

    c_venc = pick_col(cols_norm, "DATA VENCIMENTO", "VENCIMENTO")
    c_pag  = pick_col(cols_norm, "DATA PAGAMENTO", "PAGAMENTO")
    c_val  = pick_col(cols_norm, "VALOR", "R$ VALOR", "R$VALOR", "R$")

    c_banco = pick_col(cols_norm, "BANCO")
    c_plano = pick_col(cols_norm, "PLANO DE CONTAS", "PLANO DE CONTA", "CONTA")
    c_tipo  = pick_col(cols_norm, "TIPO")
    c_cc    = pick_col(cols_norm, "CENTRO DE CUSTO", "INDIRETO")
    c_forn  = pick_col(cols_norm, "FORNECEDOR")
    c_desc  = pick_col(cols_norm, "DESCRICAO", "DESCRIÇÃO", "HISTORICO", "HISTÓRICO", "OBS", "OBSERVACAO", "OBSERVAÇÃO")

    df["VENCIMENTO"] = df[c_venc].apply(parse_date_any) if c_venc else pd.NaT
    df["PAGAMENTO"] = df[c_pag].apply(parse_date_any) if c_pag else pd.NaT
    df["DATA_REF"] = df["PAGAMENTO"].where(df["PAGAMENTO"].notna(), df["VENCIMENTO"])
    df["VALOR"] = df[c_val].apply(money_to_float) if c_val else 0.0

    df["BANCO"] = df[c_banco].astype(str).map(_upper) if c_banco else ""
    df["CONTA"] = df[c_plano].astype(str).map(_upper) if c_plano else ""
    df["TIPO"] = df[c_tipo].astype(str).map(_upper) if c_tipo else ""
    df["CENTRO_CUSTO"] = df[c_cc].astype(str).map(_upper) if c_cc else ""
    df["FORNECEDOR"] = df[c_forn].astype(str).map(_upper) if c_forn else ""
    df["DESCRICAO"] = df[c_desc].astype(str) if c_desc else ""

    df["YM"] = df["DATA_REF"].apply(to_ym)

    df = df[df["DATA_REF"].notna()].copy()
    df = df[df["VALOR"] != 0].copy()

    keep = ["DATA_REF", "YM", "VENCIMENTO", "PAGAMENTO", "BANCO", "CONTA", "TIPO", "CENTRO_CUSTO", "FORNECEDOR", "DESCRICAO", "VALOR"]
    keep = [c for c in keep if c in df.columns]
    return df[keep].copy()

def normalize_transferencias(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    cols_norm = [_norm_col(c) for c in df.columns]
    df.columns = cols_norm

    c_data = pick_col(cols_norm, "DATA")
    c_or   = pick_col(cols_norm, "BANCO SAIDA", "BANCO SAÍDA", "ORIGEM")
    c_de   = pick_col(cols_norm, "BANCO ENTRADA", "DESTINO")
    c_val  = pick_col(cols_norm, "VALOR", "R$ VALOR", "R$VALOR", "R$")
    c_desc = pick_col(cols_norm, "DESCRICAO", "DESCRIÇÃO")

    df["DATA"] = df[c_data].apply(parse_date_any) if c_data else pd.NaT
    df["ORIGEM"] = df[c_or].astype(str).map(_upper) if c_or else ""
    df["DESTINO"] = df[c_de].astype(str).map(_upper) if c_de else ""
    df["DESCRICAO"] = df[c_desc].astype(str) if c_desc else ""
    df["VALOR"] = df[c_val].apply(money_to_float) if c_val else 0.0
    df["YM"] = df["DATA"].apply(to_ym)

    df = df[df["DATA"].notna()].copy()
    df = df[df["VALOR"] != 0].copy()

    keep = ["DATA", "YM", "ORIGEM", "DESTINO", "DESCRICAO", "VALOR"]
    keep = [c for c in keep if c in df.columns]
    return df[keep].copy()

def compute_fluxo_caixa(df_ent: pd.DataFrame, df_sai: pd.DataFrame) -> pd.DataFrame:
    ent_day = (df_ent.groupby("DATA")["VALOR"].sum().reset_index().rename(columns={"VALOR":"ENTRADAS"})) if not df_ent.empty else pd.DataFrame(columns=["DATA","ENTRADAS"])
    sai_day = (df_sai.groupby("DATA_REF")["VALOR"].sum().reset_index().rename(columns={"DATA_REF":"DATA","VALOR":"SAIDAS"})) if not df_sai.empty else pd.DataFrame(columns=["DATA","SAIDAS"])
    base = ent_day.merge(sai_day, on="DATA", how="outer").fillna(0.0)
    base["SALDO_DIA"] = base["ENTRADAS"] - base["SAIDAS"]
    base = base.sort_values("DATA")
    base["SALDO_ACUM"] = base["SALDO_DIA"].cumsum()
    base["YM"] = base["DATA"].apply(to_ym)
    return base

def add_value_labels_bar(chart, x_field: str, y_field: str, fmt: str = ",.0f", dy: int = -6):
    txt = chart.mark_text(dy=dy).encode(
        text=alt.Text(y_field, format=fmt)
    )
    return chart + txt

def last_point_label(df: pd.DataFrame, xcol: str, ycol: str, label: str = None):
    if df.empty:
        return pd.DataFrame(columns=[xcol, ycol, "LABEL"])
    d = df.sort_values(xcol).tail(1).copy()
    d["LABEL"] = d[ycol].apply(lambda v: fmt_brl(v) if isinstance(v, (int,float,np.number)) else str(v))
    if label is not None:
        d["SÉRIE"] = label
    return d

# ====================== LOAD DATA ======================
st.sidebar.markdown(f"### {COMPANY_NAME}")
if LOGO_URL:
    st.sidebar.image(LOGO_URL, use_container_width=True)
st.sidebar.markdown("<div class='small'>Financeiro • Streamlit</div>", unsafe_allow_html=True)
st.sidebar.markdown("<div class='hr'></div>", unsafe_allow_html=True)

PAGES = [("Dashboard","📊"),("Entradas","💚"),("Saídas","💸"),("Investimentos","🟨"),("Fluxo de Caixa","💧"),("Conciliação","🧾"),("Exportar","⬇️")]
page = st.sidebar.radio("Menu", [f"{ico}  {name}" for name, ico in PAGES], index=0)

with st.spinner("Carregando planilha..."):
    df_ent_raw = read_tab(SHEET_ID, TAB_ENT)
    df_sai_raw = read_tab(SHEET_ID, TAB_SAI)
    df_trf_raw = read_tab(SHEET_ID, TAB_TRF)

df_ent = normalize_entradas(df_ent_raw)
df_sai = normalize_saidas(df_sai_raw)
df_trf = normalize_transferencias(df_trf_raw)

months = sorted(list(set([m for m in df_ent.get("YM", []) if m] + [m for m in df_sai.get("YM", []) if m])))
if not months:
    st.error("Não encontrei datas válidas nas abas 4. Entradas / 5. Saídas.")
    st.stop()

# ====================== HEADER + FILTERS ======================
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
    capt_opts = sorted(df_ent[df_ent["YM"] == ym_sel]["CAPTACAO"].dropna().unique().tolist()) if (not df_ent.empty and "CAPTACAO" in df_ent.columns) else []
    capt_sel = st.multiselect("Captação", options=capt_opts, default=capt_opts)

with c4:
    banco_opts = sorted(df_sai[df_sai["YM"] == ym_sel]["BANCO"].dropna().unique().tolist()) if (not df_sai.empty and "BANCO" in df_sai.columns) else []
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

    if capt_sel and (not ent.empty) and ("CAPTACAO" in ent.columns):
        ent = ent[ent["CAPTACAO"].isin([_upper(x) for x in capt_sel])].copy()
    if banco_sel and (not sai.empty) and ("BANCO" in sai.columns):
        sai = sai[sai["BANCO"].isin([_upper(x) for x in banco_sel])].copy()

    return ent, sai, trf

ent_f, sai_f, trf_f = apply_filters()

# ====================== KPIs (geral do período filtrado) ======================
ent_total = float(ent_f["VALOR"].sum()) if (not ent_f.empty and "VALOR" in ent_f.columns) else 0.0
sai_total = float(sai_f["VALOR"].sum()) if (not sai_f.empty and "VALOR" in sai_f.columns) else 0.0

inv_total = 0.0
inv_mask = pd.Series([False] * len(sai_f))
if (not sai_f.empty) and ("CONTA" in sai_f.columns):
    inv_mask = sai_f["CONTA"].astype(str).str.contains("INVEST", na=False)
    inv_total = float(sai_f.loc[inv_mask, "VALOR"].sum()) if "VALOR" in sai_f.columns else 0.0

desp_total = max(sai_total - inv_total, 0.0)
lucro_liq = ent_total - sai_total

# ====================== PAGES ======================
if page.startswith("📊"):
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Resumo do período")
    cA, cB, cC, cD, cE = st.columns(5)
    with cA: st_kpi("Receita Total", fmt_brl(ent_total), sub=f"Mês {sel_month_label}")
    with cB: st_kpi("Despesas", fmt_brl(desp_total), sub="Saídas (sem investimentos)")
    with cC: st_kpi("Investimentos", fmt_brl(inv_total), sub="Regra: CONTA contém 'INVEST'", badge=("revisável", "warn"))
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

    bars = alt.Chart(evo_melt).mark_bar().encode(
        x=alt.X("Mês:N", sort=list(evo["Mês"]), title=""),
        y=alt.Y("Valor:Q", title="R$"),
        color=alt.Color("Métrica:N", legend=alt.Legend(title="")),
        tooltip=["Mês","Métrica",alt.Tooltip("Valor:Q", format=",.2f")],
    ).properties(height=320)
    st.altair_chart(bars, use_container_width=True)

    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Detalhamento (amostra)")
    t1, t2 = st.columns(2)
    with t1:
        show_ent = ent_f.sort_values("DATA", ascending=False).head(250).copy() if not ent_f.empty else ent_f
        if not show_ent.empty:
            show_ent["R$"] = show_ent["VALOR"].map(fmt_brl)
        st.dataframe(show_ent.drop(columns=["VALOR"], errors="ignore"), use_container_width=True, hide_index=True)
    with t2:
        show_sai = sai_f.sort_values("DATA_REF", ascending=False).head(250).copy() if not sai_f.empty else sai_f
        if not show_sai.empty:
            show_sai["R$"] = show_sai["VALOR"].map(fmt_brl)
        st.dataframe(show_sai.drop(columns=["VALOR"], errors="ignore"), use_container_width=True, hide_index=True)

elif page.startswith("💚"):
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Entradas — visão analítica")

    # cards
    qtd = int(len(ent_f)) if not ent_f.empty else 0
    dias = int(ent_f["DATA"].nunique()) if (not ent_f.empty and "DATA" in ent_f.columns) else 0
    media_dia = (ent_total / dias) if dias > 0 else 0.0
    maior_dia = 0.0
    if not ent_f.empty:
        maior_dia = float(ent_f.groupby("DATA")["VALOR"].sum().max())
    cA, cB, cC, cD = st.columns(4)
    with cA: st_kpi("Total Entradas", fmt_brl(ent_total), sub=f"{qtd} lançamentos")
    with cB: st_kpi("Média por dia", fmt_brl(media_dia), sub=f"{dias} dias com movimento")
    with cC: st_kpi("Maior dia", fmt_brl(maior_dia), sub="Pico de entradas no período")
    with cD:
        top_capt = ""
        if (not ent_f.empty) and ("CAPTACAO" in ent_f.columns):
            s = ent_f.groupby("CAPTACAO")["VALOR"].sum().sort_values(ascending=False)
            if len(s) > 0:
                top_capt = f"{s.index[0]} • {fmt_brl(s.iloc[0])}"
        st_kpi("Top captação", top_capt or "-", sub="Maior origem no período")

    daily = ent_f.groupby("DATA")["VALOR"].sum().reset_index().sort_values("DATA") if not ent_f.empty else pd.DataFrame()
    if not daily.empty:
        line = alt.Chart(daily).mark_line(point=True).encode(
            x=alt.X("DATA:T", title="Data", axis=alt.Axis(format="%d/%m")),
            y=alt.Y("VALOR:Q", title="R$"),
            tooltip=[alt.Tooltip("DATA:T", title="Data", format="%d/%m/%Y"), alt.Tooltip("VALOR:Q", format=",.2f", title="R$")],
        ).properties(height=320)
        # rótulo: último ponto
        last = last_point_label(daily, "DATA", "VALOR")
        lbl = alt.Chart(last).mark_text(align="left", dx=8, dy=-8).encode(
            x="DATA:T", y="VALOR:Q", text="LABEL:N"
        )
        st.altair_chart(line + lbl, use_container_width=True)

    out = ent_f.sort_values("DATA", ascending=False).copy() if not ent_f.empty else ent_f
    if not out.empty:
        out["R$"] = out["VALOR"].map(fmt_brl)
    st.dataframe(out.drop(columns=["VALOR"], errors="ignore"), use_container_width=True, hide_index=True)

elif page.startswith("💸"):
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Saídas — visão analítica")

    qtd = int(len(sai_f)) if not sai_f.empty else 0
    dias = int(sai_f["DATA_REF"].nunique()) if (not sai_f.empty and "DATA_REF" in sai_f.columns) else 0
    media_dia = (sai_total / dias) if dias > 0 else 0.0
    maior_dia = 0.0
    if not sai_f.empty:
        maior_dia = float(sai_f.groupby("DATA_REF")["VALOR"].sum().max())
    aberto = 0.0
    if (not sai_f.empty) and ("VENCIMENTO" in sai_f.columns):
        mask_aberto = sai_f["PAGAMENTO"].isna() if "PAGAMENTO" in sai_f.columns else pd.Series([False]*len(sai_f))
        aberto = float(sai_f.loc[mask_aberto, "VALOR"].sum()) if "VALOR" in sai_f.columns else 0.0

    cA, cB, cC, cD = st.columns(4)
    with cA: st_kpi("Total Saídas", fmt_brl(sai_total), sub=f"{qtd} lançamentos")
    with cB: st_kpi("Média por dia", fmt_brl(media_dia), sub=f"{dias} dias com movimento")
    with cC: st_kpi("Maior dia", fmt_brl(maior_dia), sub="Pico de saídas no período")
    with cD:
        badge = ("atenção", "warn") if aberto > 0 else ("ok", "good")
        st_kpi("Em aberto", fmt_brl(aberto), sub="Saídas sem pagamento", badge=badge)

    daily = sai_f.groupby("DATA_REF")["VALOR"].sum().reset_index().sort_values("DATA_REF") if not sai_f.empty else pd.DataFrame()
    if not daily.empty:
        line = alt.Chart(daily).mark_line(point=True).encode(
            x=alt.X("DATA_REF:T", title="Data", axis=alt.Axis(format="%d/%m")),
            y=alt.Y("VALOR:Q", title="R$"),
            tooltip=[alt.Tooltip("DATA_REF:T", title="Data", format="%d/%m/%Y"), alt.Tooltip("VALOR:Q", format=",.2f", title="R$")],
        ).properties(height=320)
        last = last_point_label(daily, "DATA_REF", "VALOR")
        lbl = alt.Chart(last).mark_text(align="left", dx=8, dy=-8).encode(
            x="DATA_REF:T", y="VALOR:Q", text="LABEL:N"
        )
        st.altair_chart(line + lbl, use_container_width=True)

    out = sai_f.sort_values("DATA_REF", ascending=False).copy() if not sai_f.empty else sai_f
    if not out.empty:
        out["R$"] = out["VALOR"].map(fmt_brl)
    st.dataframe(out.drop(columns=["VALOR"], errors="ignore"), use_container_width=True, hide_index=True)

elif page.startswith("🟨"):
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Investimentos (regra inicial)")
    inv = sai_f.loc[inv_mask].copy() if not sai_f.empty else pd.DataFrame()
    c1, c2 = st.columns(2)
    with c1: st_kpi("Total investimentos", fmt_brl(inv["VALOR"].sum() if not inv.empty else 0))
    with c2: st_kpi("Lançamentos", str(int(len(inv))))
    inv_out = inv.sort_values("DATA_REF", ascending=False).copy() if not inv.empty else inv
    if not inv_out.empty:
        inv_out["R$"] = inv_out["VALOR"].map(fmt_brl)
    st.dataframe(inv_out.drop(columns=["VALOR"], errors="ignore"), use_container_width=True, hide_index=True)

elif page.startswith("💧"):
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Fluxo de Caixa")

    fluxo = compute_fluxo_caixa(ent_f, sai_f)
    if fluxo.empty:
        st.info("Sem dados suficientes para fluxo.")
    else:
        # 1) Linhas: Entradas / Saídas / Saldo do dia
        melt = fluxo.melt(id_vars=["DATA"], value_vars=["ENTRADAS","SAIDAS","SALDO_DIA"], var_name="Métrica", value_name="Valor")
        melt["Métrica"] = melt["Métrica"].replace({"ENTRADAS":"Entradas","SAIDAS":"Saídas","SALDO_DIA":"Saldo do dia"})
        chart = alt.Chart(melt).mark_line(point=True).encode(
            x=alt.X("DATA:T", title="Data", axis=alt.Axis(format="%d/%m")),
            y=alt.Y("Valor:Q", title="R$"),
            color=alt.Color("Métrica:N", legend=alt.Legend(title="")),
            tooltip=[alt.Tooltip("DATA:T", title="Data", format="%d/%m/%Y"), "Métrica", alt.Tooltip("Valor:Q", format=",.2f", title="R$")]
        ).properties(height=320)

        # rótulos: último ponto de cada métrica
        last_rows = []
        for met, col in [("Entradas","ENTRADAS"),("Saídas","SAIDAS"),("Saldo do dia","SALDO_DIA")]:
            df_tmp = fluxo[["DATA", col]].rename(columns={col:"VAL"})
            df_last = last_point_label(df_tmp.rename(columns={"VAL":"VALOR"}), "DATA", "VALOR", label=met)
            if not df_last.empty:
                df_last = df_last.rename(columns={"LABEL":"RÓTULO"})
                df_last["Métrica"] = met
                df_last["Valor"] = df_last["VALOR"]
                last_rows.append(df_last[["DATA","Métrica","Valor","RÓTULO"]])
        last_df = pd.concat(last_rows, ignore_index=True) if last_rows else pd.DataFrame(columns=["DATA","Métrica","Valor","RÓTULO"])
        lbl = alt.Chart(last_df).mark_text(align="left", dx=8, dy=-8).encode(
            x="DATA:T", y="Valor:Q", color=alt.Color("Métrica:N", legend=None), text="RÓTULO:N"
        )
        st.altair_chart(chart + lbl, use_container_width=True)

        # cards rápidos
        st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
        cA, cB, cC, cD = st.columns(4)
        with cA: st_kpi("Entradas", fmt_brl(fluxo["ENTRADAS"].sum()), sub="Somatório no período")
        with cB: st_kpi("Saídas", fmt_brl(fluxo["SAIDAS"].sum()), sub="Somatório no período")
        with cC:
            saldo = float(fluxo["SALDO_DIA"].sum())
            badge = ("positivo","good") if saldo >= 0 else ("negativo","bad")
            st_kpi("Saldo no período", fmt_brl(saldo), sub="Entradas - Saídas", badge=badge)
        with cD:
            final = float(fluxo["SALDO_ACUM"].iloc[-1])
            badge = ("positivo","good") if final >= 0 else ("negativo","bad")
            st_kpi("Saldo acumulado (final)", fmt_brl(final), sub="Cumulativo", badge=badge)

        # 2) Saldo acumulado
        st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
        st.markdown("### Saldo acumulado")
        acc = fluxo[["DATA","SALDO_ACUM"]].copy()
        acc_line = alt.Chart(acc).mark_line(point=True).encode(
            x=alt.X("DATA:T", title="Data", axis=alt.Axis(format="%d/%m")),
            y=alt.Y("SALDO_ACUM:Q", title="R$"),
            tooltip=[alt.Tooltip("DATA:T", title="Data", format="%d/%m/%Y"), alt.Tooltip("SALDO_ACUM:Q", format=",.2f", title="R$")]
        ).properties(height=260)
        last_acc = last_point_label(acc.rename(columns={"SALDO_ACUM":"VALOR"}), "DATA", "VALOR")
        lbl_acc = alt.Chart(last_acc).mark_text(align="left", dx=8, dy=-8).encode(x="DATA:T", y="VALOR:Q", text="LABEL:N")
        st.altair_chart(acc_line + lbl_acc, use_container_width=True)

        # 3) Pagamentos x Vencimentos (saídas)
        if (not sai_f.empty) and ("VENCIMENTO" in sai_f.columns):
            st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
            st.markdown("### Pagamentos x Vencimentos (saídas)")
            dfp = sai_f.copy()
            dfp["VENC"] = dfp["VENCIMENTO"].apply(parse_date_any)
            dfp["PAG"] = dfp["PAGAMENTO"].apply(parse_date_any) if "PAGAMENTO" in dfp.columns else pd.NaT

            # filtra pelo período (usando vencimento/pagamento)
            if dt_ini and dt_fim:
                dfp = dfp[(dfp["VENC"].between(dt_ini, dt_fim)) | (dfp["PAG"].between(dt_ini, dt_fim))].copy()

            venc = dfp[dfp["VENC"].notna()].groupby("VENC")["VALOR"].sum().reset_index().rename(columns={"VENC":"DATA","VALOR":"Vencimentos"})
            pag = dfp[dfp["PAG"].notna()].groupby("PAG")["VALOR"].sum().reset_index().rename(columns={"PAG":"DATA","VALOR":"Pagamentos"})
            aberto = dfp[(dfp["VENC"].notna()) & ((dfp["PAG"].isna()) | (dfp["PAG"] > (dt_fim if dt_fim else date.max)))].groupby("VENC")["VALOR"].sum().reset_index().rename(columns={"VENC":"DATA","VALOR":"Em aberto"})

            pv = venc.merge(pag, on="DATA", how="outer").merge(aberto, on="DATA", how="outer").fillna(0.0).sort_values("DATA")
            pv_melt = pv.melt(id_vars=["DATA"], value_vars=["Em aberto","Pagamentos","Vencimentos"], var_name="Métrica", value_name="Valor")

            bars = alt.Chart(pv_melt).mark_bar().encode(
                x=alt.X("DATA:T", title="Data", axis=alt.Axis(format="%d/%m")),
                y=alt.Y("Valor:Q", title="R$"),
                color=alt.Color("Métrica:N", legend=alt.Legend(title="")),
                tooltip=[alt.Tooltip("DATA:T", title="Data", format="%d/%m/%Y"), "Métrica", alt.Tooltip("Valor:Q", format=",.2f", title="R$")]
            ).properties(height=320)
            # rótulo (só quando valor > 0 pra não poluir)
            txt = alt.Chart(pv_melt[pv_melt["Valor"] > 0]).mark_text(dy=-6).encode(
                x="DATA:T", y="Valor:Q", color=alt.Color("Métrica:N", legend=None),
                text=alt.Text("Valor:Q", format=",.0f")
            )
            st.altair_chart(bars + txt, use_container_width=True)

        # 4) Análise Vertical & Horizontal (estilo AH/AV da base)
        st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
        st.markdown("## Análise Vertical e Horizontal")

        # base mensal (últimos 6 meses para ficar legível)
        all_months = sorted(list(set([m for m in df_ent.get("YM", []) if m] + [m for m in df_sai.get("YM", []) if m])))
        last_n = 6
        sel_months = all_months[-last_n:] if len(all_months) > last_n else all_months

        def _monthly_table(df: pd.DataFrame, ym_col: str, acc_col: str, val_col: str, kind: str):
            if df.empty or ym_col not in df.columns or val_col not in df.columns:
                return pd.DataFrame()
            if acc_col not in df.columns:
                df = df.copy()
                df[acc_col] = "SEM_CONTA"
            t = df[df[ym_col].isin(sel_months)].groupby([acc_col, ym_col])[val_col].sum().reset_index()
            piv = t.pivot(index=acc_col, columns=ym_col, values=val_col).fillna(0.0)
            piv.index.name = "CONTA"
            piv = piv.reset_index()
            piv["TIPO"] = kind
            return piv

        ent_piv = _monthly_table(df_ent, "YM", "PLANO_CONTAS", "VALOR", "Entradas")
        sai_piv = _monthly_table(df_sai, "YM", "CONTA", "VALOR", "Saídas")
        if not sai_piv.empty:
            # Saídas como valores positivos para comparação de participação
            pass

        combo = pd.concat([ent_piv, sai_piv], ignore_index=True) if (not ent_piv.empty or not sai_piv.empty) else pd.DataFrame()

        if combo.empty or len(sel_months) < 2:
            st.caption("Sem histórico suficiente para calcular análise vertical/horizontal.")
        else:
            # mantém top contas por último mês (por tipo)
            last_m = sel_months[-1]
            def _top(df, typ, n=8):
                sub = df[df["TIPO"]==typ].copy()
                if sub.empty:
                    return sub
                sub["__LAST"] = sub[last_m]
                return sub.sort_values("__LAST", ascending=False).head(n).drop(columns=["__LAST"])
            top_ent = _top(combo, "Entradas", 8)
            top_sai = _top(combo, "Saídas", 8)

            def _calc_ah_av(df_typ: pd.DataFrame) -> pd.DataFrame:
                df_typ = df_typ.copy()
                # total por mês (para AV%)
                totals = {m: float(df_typ[m].sum()) for m in sel_months}
                # AH% do último mês vs mês anterior
                prev_m = sel_months[-2]
                df_typ["AH_%"] = df_typ.apply(lambda r: ((r[last_m]/r[prev_m]) - 1.0) if r[prev_m] != 0 else np.nan, axis=1)
                df_typ["AV_%"] = df_typ.apply(lambda r: (r[last_m]/totals[last_m]) if totals[last_m] != 0 else np.nan, axis=1)
                return df_typ, totals

            ent_calc, ent_totals = _calc_ah_av(top_ent) if not top_ent.empty else (pd.DataFrame(), {})
            sai_calc, sai_totals = _calc_ah_av(top_sai) if not top_sai.empty else (pd.DataFrame(), {})

            # --- Vertical (composição do mês selecionado) ---
            v1, v2 = st.columns(2)
            with v1:
                st.markdown("### Vertical — composição de Entradas (mês selecionado)")
                if ent_calc.empty:
                    st.caption("Sem dados de entradas.")
                else:
                    d = ent_calc[["CONTA", last_m]].copy().rename(columns={last_m:"Valor"})
                    total = float(d["Valor"].sum()) if len(d) else 0.0
                    d["%"] = d["Valor"].apply(lambda x: (x/total) if total else 0.0)
                    bars = alt.Chart(d).mark_bar().encode(
                        x=alt.X("%:Q", title="% do total", axis=alt.Axis(format=".0%")),
                        y=alt.Y("CONTA:N", sort='-x', title=""),
                        tooltip=["CONTA", alt.Tooltip("Valor:Q", format=",.2f"), alt.Tooltip("%:Q", format=".1%")]
                    ).properties(height=320)
                    txt = alt.Chart(d).mark_text(dx=6, align="left").encode(
                        x="%:Q", y=alt.Y("CONTA:N", sort='-x'),
                        text=alt.Text("%:Q", format=".0%")
                    )
                    st.altair_chart(bars + txt, use_container_width=True)

            with v2:
                st.markdown("### Vertical — composição de Saídas (mês selecionado)")
                if sai_calc.empty:
                    st.caption("Sem dados de saídas.")
                else:
                    d = sai_calc[["CONTA", last_m]].copy().rename(columns={last_m:"Valor"})
                    total = float(d["Valor"].sum()) if len(d) else 0.0
                    d["%"] = d["Valor"].apply(lambda x: (x/total) if total else 0.0)
                    bars = alt.Chart(d).mark_bar().encode(
                        x=alt.X("%:Q", title="% do total", axis=alt.Axis(format=".0%")),
                        y=alt.Y("CONTA:N", sort='-x', title=""),
                        tooltip=["CONTA", alt.Tooltip("Valor:Q", format=",.2f"), alt.Tooltip("%:Q", format=".1%")]
                    ).properties(height=320)
                    txt = alt.Chart(d).mark_text(dx=6, align="left").encode(
                        x="%:Q", y=alt.Y("CONTA:N", sort='-x'),
                        text=alt.Text("%:Q", format=".0%")
                    )
                    st.altair_chart(bars + txt, use_container_width=True)

            # --- Horizontal (evolução por mês) ---
            st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
            st.markdown("### Horizontal — evolução (últimos meses)")

            # totais gerais por mês
            tot = pd.DataFrame({"YM": sel_months})
            tot["Entradas"] = tot["YM"].map(lambda m: float(df_ent[df_ent["YM"]==m]["VALOR"].sum()) if not df_ent.empty else 0.0)
            tot["Saídas"] = tot["YM"].map(lambda m: float(df_sai[df_sai["YM"]==m]["VALOR"].sum()) if not df_sai.empty else 0.0)
            tot["Resultado"] = tot["Entradas"] - tot["Saídas"]
            tot["Mês"] = tot["YM"].map(month_label)

            tot_melt = tot.melt(id_vars=["YM","Mês"], value_vars=["Entradas","Saídas","Resultado"], var_name="Métrica", value_name="Valor")
            line = alt.Chart(tot_melt).mark_line(point=True).encode(
                x=alt.X("Mês:N", sort=list(tot["Mês"]), title=""),
                y=alt.Y("Valor:Q", title="R$"),
                color=alt.Color("Métrica:N", legend=alt.Legend(title="")),
                tooltip=["Mês","Métrica",alt.Tooltip("Valor:Q", format=",.2f")]
            ).properties(height=320)
            st.altair_chart(line, use_container_width=True)

            # tabela AH/AV consolidada (Entradas + Saídas)
            st.markdown("### Tabela (AH/AV) — top contas")
            def _table_out(df_calc: pd.DataFrame, typ: str) -> pd.DataFrame:
                if df_calc.empty:
                    return pd.DataFrame()
                out = df_calc[["CONTA"] + sel_months + ["AH_%","AV_%"]].copy()
                out.insert(0, "TIPO", typ)
                # formatação amigável
                for m in sel_months:
                    out[m] = out[m].apply(lambda v: safe_num(v))
                out["AH_%"] = out["AH_%"].apply(lambda v: "" if pd.isna(v) else f"{v*100:.1f}%")
                out["AV_%"] = out["AV_%"].apply(lambda v: "" if pd.isna(v) else f"{v*100:.1f}%")
                return out

            table = pd.concat([_table_out(ent_calc, "Entradas"), _table_out(sai_calc, "Saídas")], ignore_index=True)
            # exibe com BRL nos meses
            show = table.copy()
            for m in sel_months:
                show[m] = show[m].apply(fmt_brl)
            st.dataframe(show, use_container_width=True, hide_index=True)

elif page.startswith("🧾"):
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Conciliação (por banco + transferências)")
    if sai_f.empty:
        st.info("Sem saídas no período.")
    else:
        by_bank_out = sai_f.groupby("BANCO")["VALOR"].sum().reset_index().rename(columns={"VALOR":"Saídas"}) if "BANCO" in sai_f.columns else pd.DataFrame(columns=["BANCO","Saídas"])
        if not trf_f.empty:
            trf_out = trf_f.groupby("ORIGEM")["VALOR"].sum().reset_index().rename(columns={"ORIGEM":"BANCO","VALOR":"Transfer. Saída"})
            trf_in  = trf_f.groupby("DESTINO")["VALOR"].sum().reset_index().rename(columns={"DESTINO":"BANCO","VALOR":"Transfer. Entrada"})
        else:
            trf_out = pd.DataFrame(columns=["BANCO","Transfer. Saída"])
            trf_in  = pd.DataFrame(columns=["BANCO","Transfer. Entrada"])

        conc = by_bank_out.merge(trf_out, on="BANCO", how="outer").merge(trf_in, on="BANCO", how="outer").fillna(0.0)
        conc["Mov. Líq. Transferências"] = conc["Transfer. Entrada"] - conc["Transfer. Saída"]
        conc = conc.sort_values("Saídas", ascending=False)

        conc_show = conc.copy()
        for c in ["Saídas","Transfer. Saída","Transfer. Entrada","Mov. Líq. Transferências"]:
            conc_show[c] = conc_show[c].map(fmt_brl)
        st.dataframe(conc_show, use_container_width=True, hide_index=True)

        st.markdown("### Transferências (linhas)")
        tt = trf_f.sort_values("DATA", ascending=False).copy() if not trf_f.empty else trf_f
        if not tt.empty:
            tt["R$"] = tt["VALOR"].map(fmt_brl)
        st.dataframe(tt.drop(columns=["VALOR"], errors="ignore"), use_container_width=True, hide_index=True)

else:
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Exportar (CSV)")
    ent_out = ent_f.copy()
    if not ent_out.empty:
        ent_out["R$"] = ent_out["VALOR"].map(fmt_brl)
    st.download_button("Baixar Entradas (CSV)", data=ent_out.drop(columns=["VALOR"], errors="ignore").to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"entradas_{ym_sel}.csv", mime="text/csv")

    sai_out = sai_f.copy()
    if not sai_out.empty:
        sai_out["R$"] = sai_out["VALOR"].map(fmt_brl)
    st.download_button("Baixar Saídas (CSV)", data=sai_out.drop(columns=["VALOR"], errors="ignore").to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"saidas_{ym_sel}.csv", mime="text/csv")

    trf_out = trf_f.copy()
    if not trf_out.empty:
        trf_out["R$"] = trf_out["VALOR"].map(fmt_brl)
    st.download_button("Baixar Transferências (CSV)", data=trf_out.drop(columns=["VALOR"], errors="ignore").to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"transferencias_{ym_sel}.csv", mime="text/csv")
