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
from typing import Optional, Tuple, List

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
  --ctl:#0f1729; --ctl2:#0b1220;
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

/* --- FIX: inputs não ficarem transparentes --- */
[data-baseweb="select"] > div{
  background: var(--ctl) !important;
  border-color: var(--line) !important;
}
[data-baseweb="input"] > div{
  background: var(--ctl) !important;
  border-color: var(--line) !important;
}
[data-baseweb="popover"]{
  background: var(--ctl) !important;
}
div[data-testid="stDateInput"] input{
  background: var(--ctl) !important;
  border-color: var(--line) !important;
  color: var(--txt) !important;
}
div[data-testid="stMultiSelect"] div[role="combobox"]{
  background: var(--ctl) !important;
  border-color: var(--line) !important;
}
div[data-testid="stSelectbox"] div[role="combobox"]{
  background: var(--ctl) !important;
  border-color: var(--line) !important;
}
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
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    # números (às vezes o Sheets vem como serial)
    if isinstance(x, (int, float, np.number)):
        try:
            dt = pd.to_datetime(float(x), unit="D", origin="1899-12-30", errors="coerce")
            return dt.date() if pd.notna(dt) else pd.NaT
        except Exception:
            return pd.NaT
    s = str(x).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
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
    # remove colunas vazias
    df = df.loc[:, [c for c in df.columns if str(c).strip() != ""]]
    # remove linhas totalmente vazias
    df = df.replace("", np.nan)
    df = df.dropna(how="all").fillna("")
    return df

# ====================== NORMALIZERS (COMPATÍVEL COM SEU EXCEL) ======================
def normalize_entradas(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    df = df.copy()
    cols_norm = [_norm_col(c) for c in df.columns]
    df.columns = cols_norm

    c_data = pick_col(cols_norm, "DATA RECEBIMENTO", "DATA", "RECEBIMENTO")
    c_venc = pick_col(cols_norm, "DATA VENCIMENTO", "VENCIMENTO")
    c_val  = pick_col(cols_norm, "VALOR", "R$ ENTRADA", "R$ENTRADA")

    # dimensões
    c_cliente = pick_col(cols_norm, "CLIENTE", "CLIENTES")
    c_plano   = pick_col(cols_norm, "PLANO DE CONTAS", "PLANO DE CONTA", "CONTA")
    c_desc    = pick_col(cols_norm, "DESCRICAO", "DESCRIÇÃO", "HISTORICO", "HISTÓRICO", "OBS", "OBSERVACAO", "OBSERVAÇÃO")
    c_meio    = pick_col(cols_norm, "MEIO")
    c_area    = pick_col(cols_norm, "AREA")
    c_prod    = pick_col(cols_norm, "PRODUTO")
    c_capt    = pick_col(cols_norm, "CAPTACAO", "CAPTAÇÃO")
    c_banco   = pick_col(cols_norm, "BANCO")

    df["DATA"] = df[c_data].apply(parse_date_any) if c_data else pd.NaT
    df["VENCIMENTO"] = df[c_venc].apply(parse_date_any) if c_venc else pd.NaT
    df["VALOR"] = df[c_val].apply(money_to_float) if c_val else 0.0

    df["CLIENTE"] = df[c_cliente].astype(str).map(_upper) if c_cliente else ""
    df["PLANO_CONTAS"] = df[c_plano].astype(str).map(_upper) if c_plano else ""
    df["DESCRICAO"] = df[c_desc].astype(str) if c_desc else ""
    df["MEIO"] = df[c_meio].astype(str).map(_upper) if c_meio else ""
    df["AREA"] = df[c_area].astype(str).map(_upper) if c_area else ""
    df["PRODUTO"] = df[c_prod].astype(str).map(_upper) if c_prod else ""
    df["CAPTACAO"] = df[c_capt].astype(str).map(_upper) if c_capt else ""
    df["BANCO"] = df[c_banco].astype(str).map(_upper) if c_banco else ""

    if (df["CAPTACAO"] == "").all():
        df["CAPTACAO"] = df["CLIENTE"]

    df["YM"] = df["DATA"].apply(to_ym)

    df = df[df["DATA"].notna()].copy()
    df = df[df["VALOR"] != 0].copy()

    keep = ["DATA", "YM", "VENCIMENTO", "CAPTACAO", "CLIENTE", "PLANO_CONTAS", "BANCO", "MEIO", "AREA", "PRODUTO", "DESCRICAO", "VALOR"]
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
    c_val  = pick_col(cols_norm, "VALOR", "R$ VALOR", "R$VALOR")

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
    c_val  = pick_col(cols_norm, "VALOR", "R$ VALOR", "R$VALOR")
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

def compute_venc_pag(df_sai: pd.DataFrame) -> pd.DataFrame:
    """Resumo diário: Vencimentos vs Pagamentos (para comparar Pag x Venc)."""
    if df_sai.empty:
        return pd.DataFrame(columns=["DATA","VENCIMENTOS","PAGAMENTOS","ABERTO"])
    venc = df_sai[df_sai["VENCIMENTO"].notna()].groupby("VENCIMENTO")["VALOR"].sum().reset_index().rename(columns={"VENCIMENTO":"DATA","VALOR":"VENCIMENTOS"})
    pag = df_sai[df_sai["PAGAMENTO"].notna()].groupby("PAGAMENTO")["VALOR"].sum().reset_index().rename(columns={"PAGAMENTO":"DATA","VALOR":"PAGAMENTOS"})
    aberto = df_sai[df_sai["PAGAMENTO"].isna() & df_sai["VENCIMENTO"].notna()].groupby("VENCIMENTO")["VALOR"].sum().reset_index().rename(columns={"VENCIMENTO":"DATA","VALOR":"ABERTO"})
    out = venc.merge(pag, on="DATA", how="outer").merge(aberto, on="DATA", how="outer").fillna(0.0)
    out = out.sort_values("DATA")
    return out

def compute_top(df: pd.DataFrame, col: str, n: int = 12) -> pd.DataFrame:
    if df.empty or col not in df.columns:
        return pd.DataFrame(columns=[col, "VALOR"])
    g = df.groupby(col)["VALOR"].sum().reset_index().sort_values("VALOR", ascending=False).head(n)
    return g

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

# ====================== KPIs ======================
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
    with cB: st_kpi("Despesas", fmt_brl(desp_total), sub="Saídas sem investimentos")
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
    daily = ent_f.groupby("DATA")["VALOR"].sum().reset_index().sort_values("DATA") if not ent_f.empty else pd.DataFrame()
    if not daily.empty:
        st.altair_chart(
            alt.Chart(daily).mark_line(point=True).encode(
                x=alt.X("DATA:T", title="Data"),
                y=alt.Y("VALOR:Q", title="R$"),
                tooltip=[alt.Tooltip("DATA:T", title="Data"), alt.Tooltip("VALOR:Q", format=",.2f", title="R$")],
            ).properties(height=320),
            use_container_width=True
        )
    out = ent_f.sort_values("DATA", ascending=False).copy() if not ent_f.empty else ent_f
    if not out.empty:
        out["R$"] = out["VALOR"].map(fmt_brl)
    st.dataframe(out.drop(columns=["VALOR"], errors="ignore"), use_container_width=True, hide_index=True)

elif page.startswith("💸"):
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Saídas — visão analítica")
    daily = sai_f.groupby("DATA_REF")["VALOR"].sum().reset_index().sort_values("DATA_REF") if not sai_f.empty else pd.DataFrame()
    if not daily.empty:
        st.altair_chart(
            alt.Chart(daily).mark_line(point=True).encode(
                x=alt.X("DATA_REF:T", title="Data"),
                y=alt.Y("VALOR:Q", title="R$"),
                tooltip=[alt.Tooltip("DATA_REF:T", title="Data"), alt.Tooltip("VALOR:Q", format=",.2f", title="R$")],
            ).properties(height=320),
            use_container_width=True
        )
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
    with c2: st_kpi("Lançamentos", str(int(len(inv))) if not inv.empty else "0")
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
        # KPIs do fluxo
        cK1, cK2, cK3, cK4, cK5 = st.columns(5)
        with cK1: st_kpi("Entradas (período)", fmt_brl(fluxo["ENTRADAS"].sum()))
        with cK2: st_kpi("Saídas (período)", fmt_brl(fluxo["SAIDAS"].sum()))
        with cK3:
            saldo = float(fluxo["SALDO_DIA"].sum())
            badge = ("positivo", "good") if saldo >= 0 else ("negativo", "bad")
            st_kpi("Saldo do período", fmt_brl(saldo), badge=badge)
        # contas em aberto (saídas sem pagamento)
        aberto = 0.0
        vencidas = 0.0
        if not sai_f.empty and "PAGAMENTO" in sai_f.columns and "VENCIMENTO" in sai_f.columns:
            aberto = float(sai_f[sai_f["PAGAMENTO"].isna()]["VALOR"].sum())
            hoje = date.today()
            vencidas = float(sai_f[(sai_f["PAGAMENTO"].isna()) & (sai_f["VENCIMENTO"].notna()) & (sai_f["VENCIMENTO"] < hoje)]["VALOR"].sum())
        with cK4: st_kpi("Contas a pagar (aberto)", fmt_brl(aberto))
        with cK5: st_kpi("Vencidas em aberto", fmt_brl(vencidas), badge=("atenção", "warn") if vencidas > 0 else ("ok", "good"))

        st.markdown("<div class='hr'></div>", unsafe_allow_html=True)

        # 1) Linhas: entradas/saídas/saldo-dia
        melt = fluxo.melt(id_vars=["DATA"], value_vars=["ENTRADAS","SAIDAS","SALDO_DIA"], var_name="Métrica", value_name="Valor")
        st.altair_chart(
            alt.Chart(melt).mark_line(point=True).encode(
                x=alt.X("DATA:T", title="Data"),
                y=alt.Y("Valor:Q", title="R$"),
                color=alt.Color("Métrica:N", legend=alt.Legend(title="")),
                tooltip=[alt.Tooltip("DATA:T", title="Data"), "Métrica", alt.Tooltip("Valor:Q", format=",.2f")]
            ).properties(height=320),
            use_container_width=True
        )

        # 2) Saldo acumulado
        st.markdown("### Saldo acumulado")
        st.altair_chart(
            alt.Chart(fluxo).mark_line(point=True).encode(
                x=alt.X("DATA:T", title="Data"),
                y=alt.Y("SALDO_ACUM:Q", title="R$"),
                tooltip=[alt.Tooltip("DATA:T", title="Data"), alt.Tooltip("SALDO_ACUM:Q", title="Saldo acumulado", format=",.2f")],
            ).properties(height=260),
            use_container_width=True
        )

        # 3) Pag x Venc (saídas) — vencimentos vs pagamentos
        st.markdown("### Pagamentos x Vencimentos (saídas)")
        pv = compute_venc_pag(sai_f)
        if pv.empty:
            st.caption("Sem dados de vencimento/pagamento suficientes.")
        else:
            pv_melt = pv.melt(id_vars=["DATA"], value_vars=["VENCIMENTOS","PAGAMENTOS","ABERTO"], var_name="Métrica", value_name="Valor")
            st.altair_chart(
                alt.Chart(pv_melt).mark_bar().encode(
                    x=alt.X("DATA:T", title="Data"),
                    y=alt.Y("Valor:Q", title="R$"),
                    color=alt.Color("Métrica:N", legend=alt.Legend(title="")),
                    tooltip=[alt.Tooltip("DATA:T", title="Data"), "Métrica", alt.Tooltip("Valor:Q", format=",.2f")],
                ).properties(height=320),
                use_container_width=True
            )

        st.markdown("<div class='hr'></div>", unsafe_allow_html=True)

        # 4) Top categorias (aproximação da visão analítica do "Pag x Venc")
        st.markdown("### Onde está o dinheiro (Top categorias)")
        cT1, cT2 = st.columns(2)
        with cT1:
            top_ent = compute_top(ent_f, "PLANO_CONTAS", n=12)
            if top_ent.empty:
                st.caption("Sem plano de contas nas entradas.")
            else:
                st.altair_chart(
                    alt.Chart(top_ent).mark_bar().encode(
                        y=alt.Y("PLANO_CONTAS:N", sort="-x", title=""),
                        x=alt.X("VALOR:Q", title="R$"),
                        tooltip=["PLANO_CONTAS", alt.Tooltip("VALOR:Q", format=",.2f")],
                    ).properties(height=360),
                    use_container_width=True
                )

        with cT2:
            top_sai = compute_top(sai_f, "CONTA", n=12)
            if top_sai.empty:
                st.caption("Sem conta/plano nas saídas.")
            else:
                st.altair_chart(
                    alt.Chart(top_sai).mark_bar().encode(
                        y=alt.Y("CONTA:N", sort="-x", title=""),
                        x=alt.X("VALOR:Q", title="R$"),
                        tooltip=["CONTA", alt.Tooltip("VALOR:Q", format=",.2f")],
                    ).properties(height=360),
                    use_container_width=True
                )

        st.markdown("<div class='hr'></div>", unsafe_allow_html=True)

        # 5) Detalhamento rápido (opcional)
        with st.expander("Detalhamento do fluxo (tabela diária)", expanded=False):
            show = fluxo.copy()
            for c in ["ENTRADAS","SAIDAS","SALDO_DIA","SALDO_ACUM"]:
                show[c] = show[c].map(fmt_brl)
            st.dataframe(show, use_container_width=True, hide_index=True)

elif page.startswith("🧾"):
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Conciliação (por banco + transferências)")
    if sai_f.empty:
        st.info("Sem saídas no período.")
    else:
        by_bank_out = sai_f.groupby("BANCO")["VALOR"].sum().reset_index().rename(columns={"VALOR":"SAÍDAS"}) if "BANCO" in sai_f.columns else pd.DataFrame(columns=["BANCO","SAÍDAS"])
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
