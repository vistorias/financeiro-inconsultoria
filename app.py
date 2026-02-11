# -*- coding: utf-8 -*-
"""
Dashboard Financeiro — Streamlit (Google Sheets)

Lê as abas (mesmos nomes do Excel):
- 1. Saldo Inicial
- 4. Entradas
- 5. Saídas
- 6. Transferencias
- 7. Conciliação (opcional; o app calcula a conciliação também)

Obs:
- Você configura o ID da planilha no secrets.toml (finance_sheet_id).
- O nome da empresa/título também fica no secrets (company_name).
"""
import os
import re
import json
import unicodedata
from datetime import datetime, date
from typing import Optional, Dict, List, Tuple

import numpy as np
import pandas as pd
import altair as alt
import streamlit as st

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ------------------ PAGE CONFIG ------------------
COMPANY_NAME = st.secrets.get("company_name", "Dashboard Financeiro")
st.set_page_config(page_title=f"{COMPANY_NAME} — Financeiro", layout="wide")

# ------------------ UI / CSS ------------------
st.markdown(
    """
<style>
:root{--bg:#0b1220;--card:#121b2d;--card2:#0f1729;--txt:#e8eefc;--mut:#9db0d5;--line:#1f2b45;--good:#23c55e;--bad:#ef4444;--warn:#f59e0b;--info:#3b82f6;}
html, body, [data-testid="stAppViewContainer"]{background:var(--bg) !important;}
.block-container{padding-top:1.2rem;}
h1,h2,h3,h4{color:var(--txt)!important;}
p,li,span,div,label{color:var(--txt);}
.small{color:var(--mut);font-size:12px;}
.hr{height:1px;background:var(--line);margin:10px 0 18px;}
.card-row{display:flex;gap:14px;flex-wrap:wrap;}
.kpi{background:linear-gradient(180deg,var(--card),var(--card2));border:1px solid var(--line);border-radius:14px;padding:14px 16px;flex:1;min-width:220px;box-shadow:0 4px 24px rgba(0,0,0,.25);}
.kpi .t{font-weight:800;color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.kpi .v{font-weight:900;font-size:28px;margin-top:6px}
.kpi .s{margin-top:6px;color:var(--mut);font-weight:700;font-size:12px}
.badge{display:inline-block;padding:4px 10px;border-radius:999px;border:1px solid var(--line);font-weight:800;font-size:12px}
.badge.good{background:rgba(35,197,94,.12);color:var(--good);border-color:rgba(35,197,94,.35)}
.badge.bad{background:rgba(239,68,68,.12);color:var(--bad);border-color:rgba(239,68,68,.35)}
.badge.warn{background:rgba(245,158,11,.12);color:var(--warn);border-color:rgba(245,158,11,.35)}
.badge.info{background:rgba(59,130,246,.12);color:var(--info);border-color:rgba(59,130,246,.35)}
.panel{background:linear-gradient(180deg,var(--card),var(--card2));border:1px solid var(--line);border-radius:14px;padding:14px 16px;margin-top:10px;}
.section-title{margin:10px 0 6px;font-weight:900;font-size:16px;color:var(--txt)}
/* sidebar */
[data-testid="stSidebar"]{background:#0a1020;border-right:1px solid var(--line);}
[data-testid="stSidebar"] *{color:var(--txt)!important;}
/* inputs */
.stMultiSelect, .stSelectbox, .stDateInput, .stTextInput, .stNumberInput {color:var(--txt)!important;}
</style>
""",
    unsafe_allow_html=True,
)

# ------------------ HELPERS ------------------
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
        return pd.to_datetime(s, dayfirst=True, errors="coerce").date()
    except Exception:
        return pd.NaT

def money_to_float(x) -> float:
    if pd.isna(x) or x == "":
        return 0.0
    if isinstance(x, (int, float, np.number)):
        return float(x)
    s = str(x).strip()
    s = s.replace("R$", "").replace("\u00a0", " ").strip()
    # 1.234,56 -> 1234.56
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
    # ym: YYYY-MM
    if not ym or len(ym) != 7:
        return ym
    return f"{ym[5:7]}/{ym[:4]}"

def to_ym(d: date) -> str:
    return f"{d.year}-{d.month:02d}"

def safe_cols(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    return df

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

# ------------------ GOOGLE SHEETS CLIENT ------------------
def _load_sa_info() -> dict:
    try:
        block = st.secrets["gcp_service_account"]
    except Exception:
        st.error("Não encontrei [gcp_service_account] no secrets.toml.")
        st.stop()

    # aceita json_path ou bloco inline
    if "json_path" in block:
        path = block["json_path"]
        if not os.path.isabs(path):
            path = os.path.join(os.path.dirname(__file__), path)
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            st.error(f"Não consegui abrir o JSON da service account: {path}")
            with st.expander("Detalhes"):
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

SHEET_ID = _sheet_id(st.secrets.get("finance_sheet_id", ""))
if not SHEET_ID:
    st.error("Faltou `finance_sheet_id` no secrets.toml (pode colar o link ou o ID).")
    st.stop()

TAB_SALDO = "1. Saldo Inicial"
TAB_ENT   = "4. Entradas"
TAB_SAI   = "5. Saídas"
TAB_TRF   = "6. Transferencias"
TAB_CONC  = "7. Conciliação"

# ------------------ READERS ------------------
@st.cache_data(ttl=300, show_spinner=False)
def read_tab(sheet_id: str, tab: str) -> pd.DataFrame:
    sh = client.open_by_key(sheet_id)
    ws = sh.worksheet(tab)
    rows = ws.get_all_records()
    df = pd.DataFrame(rows) if rows else pd.DataFrame()
    return safe_cols(df)

def normalize_entradas(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    df.columns = [_norm_col(c) for c in df.columns]

    # Campos principais (com tolerância de nome)
    col_data = "DATA" if "DATA" in df.columns else None
    col_capt = "CAPTACAO" if "CAPTACAO" in df.columns else "CAPTAÇÃO" if "CAPTAÇÃO" in df.columns else None
    # como já normalizou, CAPTAÇÃO vira CAPTACAO
    col_capt = "CAPTACAO" if "CAPTACAO" in df.columns else None
    col_meio = "MEIO" if "MEIO" in df.columns else None
    col_area = "AREA" if "AREA" in df.columns else None
    col_prod = "PRODUTO" if "PRODUTO" in df.columns else None
    col_val  = "R$ ENTRADA" if "R$ ENTRADA" in df.columns else "R$ENTRADA" if "R$ENTRADA" in df.columns else "R$ENTRADA"  # fallback

    # Alguns arquivos podem vir sem "R$ ENTRADA" (normalização remove acento e mantém $ e espaço)
    if "R$ ENTRADA" not in df.columns:
        # tenta achar qualquer coluna contendo ENTRADA e R$
        cands = [c for c in df.columns if "ENTRADA" in c and "R$" in c]
        if cands:
            col_val = cands[0]
        else:
            # tenta "VALOR"
            col_val = "VALOR" if "VALOR" in df.columns else None

    # Normaliza colunas auxiliares (comissões etc.)
    # tudo que começar com R$ e não for o valor principal será tratado como "DISTRIBUICAO"
    dist_cols = [c for c in df.columns if c.startswith("R$") and c != col_val]

    df["_DATA"] = df[col_data].apply(parse_date_any) if col_data else pd.NaT
    df["YM"] = df["_DATA"].apply(lambda d: to_ym(d) if isinstance(d, date) else None)

    df["CAPTACAO"] = df[col_capt].astype(str).map(_upper) if col_capt else ""
    df["MEIO"] = df[col_meio].astype(str).map(_upper) if col_meio else ""
    df["AREA"] = df[col_area].astype(str).map(_upper) if col_area else ""
    df["PRODUTO"] = df[col_prod].astype(str).map(_upper) if col_prod else ""

    df["VALOR"] = df[col_val].apply(money_to_float) if col_val else 0.0

    # distribuições (se existirem)
    if dist_cols:
        for c in dist_cols:
            df[c] = df[c].apply(money_to_float)
    else:
        dist_cols = []

    # remove linhas vazias
    df = df[df["_DATA"].notna()].copy()
    df = df[df["VALOR"] != 0].copy()

    # mantém campos originais úteis (descricao/observacao etc.)
    keep_extra = [c for c in df.columns if c not in {"_DATA","YM","CAPTACAO","MEIO","AREA","PRODUTO","VALOR"}]
    base = df[["_DATA","YM","CAPTACAO","MEIO","AREA","PRODUTO","VALOR"] + dist_cols + keep_extra].copy()
    base = base.rename(columns={"_DATA":"DATA"})
    return base

def normalize_saidas(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    df.columns = [_norm_col(c) for c in df.columns]

    # Campos típicos
    col_venc = "VENCIMENTO" if "VENCIMENTO" in df.columns else None
    col_pag  = "PAGAMENTO" if "PAGAMENTO" in df.columns else None
    col_cont = "CONTA" if "CONTA" in df.columns else None
    col_banc = "BANCO" if "BANCO" in df.columns else None
    col_obj  = "OBJETO" if "OBJETO" in df.columns else "OBJETIVO" if "OBJETIVO" in df.columns else None
    col_tipo = "TIPO" if "TIPO" in df.columns else None
    col_doc  = "DOCUMENTO" if "DOCUMENTO" in df.columns else None
    col_val  = "R$ VALOR" if "R$ VALOR" in df.columns else None
    if not col_val:
        cands = [c for c in df.columns if "VALOR" in c and "R$" in c]
        col_val = cands[0] if cands else ("VALOR" if "VALOR" in df.columns else None)

    col_ind  = "INDIRETO" if "INDIRETO" in df.columns else None

    df["_VENC"] = df[col_venc].apply(parse_date_any) if col_venc else pd.NaT
    df["_PAG"]  = df[col_pag].apply(parse_date_any) if col_pag else pd.NaT

    # Data de competência para análises:
    # - se tiver pagamento, usa pagamento; senão usa vencimento
    df["_DATA_REF"] = df["_PAG"].where(df["_PAG"].notna(), df["_VENC"])
    df["YM"] = df["_DATA_REF"].apply(lambda d: to_ym(d) if isinstance(d, date) else None)

    df["CONTA"] = df[col_cont].astype(str).map(_upper) if col_cont else ""
    df["BANCO"] = df[col_banc].astype(str).map(_upper) if col_banc else ""
    df["TIPO"] = df[col_tipo].astype(str).map(_upper) if col_tipo else ""
    df["DOCUMENTO"] = df[col_doc].astype(str).map(_upper) if col_doc else ""
    df["OBJETO"] = df[col_obj].astype(str).map(_upper) if col_obj else ""
    df["INDIRETO"] = df[col_ind].astype(str).map(_upper) if col_ind else ""

    df["VALOR"] = df[col_val].apply(money_to_float) if col_val else 0.0

    df = df[df["_DATA_REF"].notna()].copy()
    df = df[df["VALOR"] != 0].copy()

    base = df[["_VENC","_PAG","_DATA_REF","YM","CONTA","INDIRETO","BANCO","OBJETO","TIPO","DOCUMENTO","VALOR"] + [c for c in df.columns if c not in {
        "_VENC","_PAG","_DATA_REF","YM","CONTA","INDIRETO","BANCO","OBJETO","TIPO","DOCUMENTO","VALOR"
    }]].copy()
    base = base.rename(columns={"_VENC":"VENCIMENTO","_PAG":"PAGAMENTO","_DATA_REF":"DATA_REF"})
    return base

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

    df["_DATA"] = df[col_data].apply(parse_date_any) if col_data else pd.NaT
    df["YM"] = df["_DATA"].apply(lambda d: to_ym(d) if isinstance(d, date) else None)

    df["ORIGEM"] = df[col_or].astype(str).map(_upper) if col_or else ""
    df["DESTINO"] = df[col_de].astype(str).map(_upper) if col_de else ""
    df["VALOR"] = df[col_val].apply(money_to_float) if col_val else 0.0

    df = df[df["_DATA"].notna()].copy()
    df = df[df["VALOR"] != 0].copy()

    base = df[["_DATA","YM","ORIGEM","DESTINO","VALOR"] + [c for c in df.columns if c not in {"_DATA","YM","ORIGEM","DESTINO","VALOR"}]].copy()
    base = base.rename(columns={"_DATA":"DATA"})
    return base

def compute_fluxo_caixa(df_ent: pd.DataFrame, df_sai: pd.DataFrame, df_trf: pd.DataFrame) -> pd.DataFrame:
    """
    Fluxo de caixa por DIA (baseado em:
    - Entradas: DATA, VALOR
    - Saídas:   DATA_REF (pagamento se houver, senão vencimento), VALOR
    - Transferências: não entram no total (movem entre bancos), mas podem entrar em relatórios por banco.
    """
    # diário consolidado (sem transferências no total)
    ent = df_ent.copy()
    sai = df_sai.copy()

    ent_day = (ent.groupby("DATA")["VALOR"].sum().reset_index().rename(columns={"VALOR":"ENTRADAS"})) if not ent.empty else pd.DataFrame(columns=["DATA","ENTRADAS"])
    sai_day = (sai.groupby("DATA_REF")["VALOR"].sum().reset_index().rename(columns={"DATA_REF":"DATA","VALOR":"SAIDAS"})) if not sai.empty else pd.DataFrame(columns=["DATA","SAIDAS"])
    base = ent_day.merge(sai_day, on="DATA", how="outer").fillna(0.0)
    base["SALDO_DIA"] = base["ENTRADAS"] - base["SAIDAS"]
    base = base.sort_values("DATA")
    base["YM"] = base["DATA"].apply(lambda d: to_ym(d) if isinstance(d, date) else None)
    return base

# ------------------ LOAD DATA ------------------
with st.spinner("Carregando planilha..."):
    df_ent_raw = read_tab(SHEET_ID, TAB_ENT)
    df_sai_raw = read_tab(SHEET_ID, TAB_SAI)
    df_trf_raw = read_tab(SHEET_ID, TAB_TRF)

df_ent = normalize_entradas(df_ent_raw)
df_sai = normalize_saidas(df_sai_raw)
df_trf = normalize_transferencias(df_trf_raw)

# universo de meses disponíveis (prioriza meses com dados)
months = sorted(list(set([m for m in df_ent.get("YM", []) if m] + [m for m in df_sai.get("YM", []) if m])))
if not months:
    st.error("Não encontrei dados de datas nas abas de Entradas/Saídas.")
    st.stop()

# ------------------ SIDEBAR NAV ------------------
st.sidebar.markdown(f"## {COMPANY_NAME}")
st.sidebar.markdown("<div class='small'>Financeiro • Streamlit</div>", unsafe_allow_html=True)
st.sidebar.markdown("<div class='hr'></div>", unsafe_allow_html=True)

PAGES = [
    ("Dashboard", "📊"),
    ("Entradas (Captação)", "💚"),
    ("Saídas (Despesas)", "💸"),
    ("Investimentos", "🟨"),
    ("Fluxo de Caixa", "💧"),
    ("Conciliação", "🧾"),
    ("Exportar", "⬇️"),
]
page = st.sidebar.radio("Menu", [f"{ico}  {name}" for name, ico in PAGES], index=0)

# ------------------ GLOBAL FILTERS ------------------
st.markdown(f"# {COMPANY_NAME}")
st.markdown("<div class='small'>Painel financeiro baseado nas abas do Google Sheets (mesmo padrão do Excel)</div>", unsafe_allow_html=True)

c1, c2, c3, c4 = st.columns([2, 2, 3, 3])
with c1:
    month_label_map = {month_label(m): m for m in months}
    default_m = months[-1]
    sel_month = st.selectbox("Mês", options=list(month_label_map.keys()), index=list(month_label_map.values()).index(default_m))
    ym_sel = month_label_map[sel_month]
with c2:
    # filtro de data (dentro do mês)
    # pega min/max do mês nos dados
    dates_in_month = []
    if not df_ent.empty:
        dates_in_month += [d for d in df_ent[df_ent["YM"] == ym_sel]["DATA"].tolist() if isinstance(d, date)]
    if not df_sai.empty:
        dates_in_month += [d for d in df_sai[df_sai["YM"] == ym_sel]["DATA_REF"].tolist() if isinstance(d, date)]
    if dates_in_month:
        dmin, dmax = min(dates_in_month), max(dates_in_month)
        dr = st.date_input("Período", value=(dmin, dmax), format="DD/MM/YYYY")
        if isinstance(dr, tuple) and len(dr) == 2:
            dt_ini, dt_fim = dr
        else:
            dt_ini, dt_fim = dmin, dmax
    else:
        dt_ini, dt_fim = None, None
        st.caption("Sem datas suficientes no mês para filtrar período.")
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

# ------------------ KPI BASE ------------------
ent_total = float(ent_f["VALOR"].sum()) if not ent_f.empty else 0.0
sai_total = float(sai_f["VALOR"].sum()) if not sai_f.empty else 0.0

# investimentos: regra prática (TIPO/CONTA/INDIRETO/OBJETO contém "INVEST")
inv_mask = pd.Series([False]*len(sai_f))
if not sai_f.empty:
    inv_mask = (
        sai_f["CONTA"].astype(str).str.contains("INVEST", na=False) |
        sai_f["INDIRETO"].astype(str).str.contains("INVEST", na=False) |
        sai_f["OBJETO"].astype(str).str.contains("INVEST", na=False)
    )
inv_total = float(sai_f.loc[inv_mask, "VALOR"].sum()) if not sai_f.empty else 0.0

desp_total = sai_total - inv_total
saida_total = sai_total
lucro_liq = ent_total - saida_total

# ------------------ PAGE: DASHBOARD ------------------
if page.startswith("📊"):
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Resumo do período")
    row = st.container()
    with row:
        cA, cB, cC, cD, cE = st.columns(5)
        with cA: st_kpi("Receita Total", fmt_brl(ent_total), sub=f"Mês {sel_month}")
        with cB: st_kpi("Total de Despesas", fmt_brl(desp_total), sub="Saídas sem investimentos")
        with cC: st_kpi("Investimentos", fmt_brl(inv_total), sub="Classificação automática", badge=("revisável", "warn"))
        with cD: st_kpi("Total de Saídas", fmt_brl(saida_total), sub="Despesas + investimentos")
        with cE:
            badge = ("positivo", "good") if lucro_liq >= 0 else ("negativo", "bad")
            st_kpi("Resultado Líquido", fmt_brl(lucro_liq), sub="Receita - Saídas", badge=badge)

    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)

    # Série mensal (12 meses)
    st.markdown("## Evolução (mensal)")
    m_ent = (df_ent.groupby("YM")["VALOR"].sum().reset_index().rename(columns={"VALOR":"Receitas"})) if not df_ent.empty else pd.DataFrame(columns=["YM","Receitas"])
    m_sai = (df_sai.groupby("YM")["VALOR"].sum().reset_index().rename(columns={"VALOR":"Saídas"})) if not df_sai.empty else pd.DataFrame(columns=["YM","Saídas"])
    evo = m_ent.merge(m_sai, on="YM", how="outer").fillna(0.0)
    evo["Resultado"] = evo["Receitas"] - evo["Saídas"]
    evo = evo.sort_values("YM")
    evo["Mês"] = evo["YM"].map(month_label)

    evo_melt = evo.melt(id_vars=["YM","Mês"], value_vars=["Receitas","Saídas","Resultado"], var_name="Métrica", value_name="Valor")
    chart = (
        alt.Chart(evo_melt)
        .mark_bar()
        .encode(
            x=alt.X("Mês:N", sort=list(evo["Mês"]), title=""),
            y=alt.Y("Valor:Q", title="R$"),
            color=alt.Color("Métrica:N", legend=alt.Legend(title="")),
            tooltip=["Mês","Métrica",alt.Tooltip("Valor:Q", format=",.2f")],
        )
        .properties(height=330)
    )
    st.altair_chart(chart, use_container_width=True)

    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    c1, c2 = st.columns([1.2, 1])
    with c1:
        st.markdown("## Captação por Produto (Top N)")
        topn = st.slider("Top N", min_value=5, max_value=30, value=10)
        by_prod = (
            ent_f.groupby("PRODUTO")["VALOR"].sum().reset_index().sort_values("VALOR", ascending=False)
            if not ent_f.empty else pd.DataFrame(columns=["PRODUTO","VALOR"])
        )
        by_prod = by_prod.head(topn)
        if by_prod.empty:
            st.info("Sem dados de entradas para os filtros atuais.")
        else:
            bar = (
                alt.Chart(by_prod)
                .mark_bar()
                .encode(
                    x=alt.X("PRODUTO:N", sort='-y', title=""),
                    y=alt.Y("VALOR:Q", title="R$"),
                    tooltip=["PRODUTO", alt.Tooltip("VALOR:Q", format=",.2f")],
                ).properties(height=350)
            )
            st.altair_chart(bar, use_container_width=True)

    with c2:
        st.markdown("## Mix de Captação (participação)")
        by_capt = (
            ent_f.groupby("CAPTACAO")["VALOR"].sum().reset_index().sort_values("VALOR", ascending=False)
            if not ent_f.empty else pd.DataFrame(columns=["CAPTACAO","VALOR"])
        )
        if by_capt.empty:
            st.info("Sem dados de captação para os filtros atuais.")
        else:
            pie = (
                alt.Chart(by_capt)
                .mark_arc()
                .encode(
                    theta="VALOR:Q",
                    color=alt.Color("CAPTACAO:N", legend=alt.Legend(title="")),
                    tooltip=["CAPTACAO", alt.Tooltip("VALOR:Q", format=",.2f")],
                ).properties(height=350)
            )
            st.altair_chart(pie, use_container_width=True)

    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Detalhamento (amostra do período)")
    t1, t2 = st.columns(2)
    with t1:
        st.markdown("<div class='panel'><div class='section-title'>Entradas</div></div>", unsafe_allow_html=True)
        show_ent = ent_f.sort_values("DATA", ascending=False).head(200).copy()
        if not show_ent.empty:
            show_ent["R$ Entrada"] = show_ent["VALOR"].map(fmt_brl)
        st.dataframe(show_ent.drop(columns=["VALOR"], errors="ignore"), use_container_width=True, hide_index=True)
    with t2:
        st.markdown("<div class='panel'><div class='section-title'>Saídas</div></div>", unsafe_allow_html=True)
        show_sai = sai_f.sort_values("DATA_REF", ascending=False).head(200).copy()
        if not show_sai.empty:
            show_sai["R$ Valor"] = show_sai["VALOR"].map(fmt_brl)
        st.dataframe(show_sai.drop(columns=["VALOR"], errors="ignore"), use_container_width=True, hide_index=True)

# ------------------ PAGE: ENTRADAS ------------------
elif page.startswith("💚"):
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Entradas (Captação) — visão analítica")
    c1, c2, c3 = st.columns(3)
    with c1:
        meio_opts = sorted(ent_f["MEIO"].dropna().unique().tolist()) if not ent_f.empty else []
        meio_sel = st.multiselect("Meio", options=meio_opts, default=meio_opts)
    with c2:
        area_opts = sorted(ent_f["AREA"].dropna().unique().tolist()) if not ent_f.empty else []
        area_sel = st.multiselect("Área", options=area_opts, default=area_opts)
    with c3:
        prod_opts = sorted(ent_f["PRODUTO"].dropna().unique().tolist()) if not ent_f.empty else []
        prod_sel = st.multiselect("Produto", options=prod_opts, default=prod_opts)

    ent_v = ent_f.copy()
    if meio_sel and not ent_v.empty:
        ent_v = ent_v[ent_v["MEIO"].isin([_upper(x) for x in meio_sel])]
    if area_sel and not ent_v.empty:
        ent_v = ent_v[ent_v["AREA"].isin([_upper(x) for x in area_sel])]
    if prod_sel and not ent_v.empty:
        ent_v = ent_v[ent_v["PRODUTO"].isin([_upper(x) for x in prod_sel])]

    st.markdown("<div class='panel'>", unsafe_allow_html=True)
    k1, k2, k3, k4 = st.columns(4)
    with k1: st_kpi("Receita (filtros)", fmt_brl(ent_v["VALOR"].sum() if not ent_v.empty else 0))
    with k2: st_kpi("Tickets (qtd)", f"{int(len(ent_v)):,}".replace(",", "."))
    with k3:
        ticket = (ent_v["VALOR"].sum()/len(ent_v)) if len(ent_v) else 0.0
        st_kpi("Ticket médio", fmt_brl(ticket))
    with k4:
        dias = ent_v["DATA"].nunique() if not ent_v.empty else 0
        st_kpi("Dias com entrada", str(int(dias)))
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("### Ranking")
    r1, r2 = st.columns(2)
    with r1:
        by_prod = ent_v.groupby("PRODUTO")["VALOR"].sum().reset_index().sort_values("VALOR", ascending=False)
        st.dataframe(by_prod.assign(**{"R$": by_prod["VALOR"].map(fmt_brl)}).drop(columns=["VALOR"]), use_container_width=True, hide_index=True)
    with r2:
        by_capt = ent_v.groupby("CAPTACAO")["VALOR"].sum().reset_index().sort_values("VALOR", ascending=False)
        st.dataframe(by_capt.assign(**{"R$": by_capt["VALOR"].map(fmt_brl)}).drop(columns=["VALOR"]), use_container_width=True, hide_index=True)

    st.markdown("### Evolução diária")
    daily = ent_v.groupby("DATA")["VALOR"].sum().reset_index().sort_values("DATA")
    if daily.empty:
        st.info("Sem entradas no período.")
    else:
        line = alt.Chart(daily).mark_line(point=True).encode(
            x=alt.X("DATA:T", title="Data"),
            y=alt.Y("VALOR:Q", title="R$"),
            tooltip=[alt.Tooltip("DATA:T", title="Data"), alt.Tooltip("VALOR:Q", format=",.2f", title="R$")],
        ).properties(height=320)
        st.altair_chart(line, use_container_width=True)

    st.markdown("### Detalhamento (linhas)")
    out = ent_v.sort_values("DATA", ascending=False).copy()
    if not out.empty:
        out["R$ Entrada"] = out["VALOR"].map(fmt_brl)
    st.dataframe(out.drop(columns=["VALOR"], errors="ignore"), use_container_width=True, hide_index=True)

# ------------------ PAGE: SAÍDAS ------------------
elif page.startswith("💸"):
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Saídas (Despesas) — visão analítica")

    c1, c2, c3 = st.columns(3)
    with c1:
        tipo_opts = sorted(sai_f["TIPO"].dropna().unique().tolist()) if not sai_f.empty else []
        tipo_sel = st.multiselect("Tipo", options=tipo_opts, default=tipo_opts)
    with c2:
        conta_opts = sorted(sai_f["CONTA"].dropna().unique().tolist()) if not sai_f.empty else []
        conta_sel = st.multiselect("Conta", options=conta_opts, default=[])
    with c3:
        ind_opts = sorted(sai_f["INDIRETO"].dropna().unique().tolist()) if not sai_f.empty else []
        ind_sel = st.multiselect("Centro de custo (Indireto)", options=ind_opts, default=[])

    sai_v = sai_f.copy()
    if tipo_sel and not sai_v.empty:
        sai_v = sai_v[sai_v["TIPO"].isin([_upper(x) for x in tipo_sel])]
    if conta_sel and not sai_v.empty:
        sai_v = sai_v[sai_v["CONTA"].isin([_upper(x) for x in conta_sel])]
    if ind_sel and not sai_v.empty:
        sai_v = sai_v[sai_v["INDIRETO"].isin([_upper(x) for x in ind_sel])]

    st.markdown("<div class='panel'>", unsafe_allow_html=True)
    k1, k2, k3, k4 = st.columns(4)
    with k1: st_kpi("Saídas (filtros)", fmt_brl(sai_v["VALOR"].sum() if not sai_v.empty else 0))
    with k2: st_kpi("Lançamentos", f"{int(len(sai_v)):,}".replace(",", "."))
    with k3:
        ticket = (sai_v["VALOR"].sum()/len(sai_v)) if len(sai_v) else 0.0
        st_kpi("Valor médio", fmt_brl(ticket))
    with k4:
        dias = sai_v["DATA_REF"].nunique() if not sai_v.empty else 0
        st_kpi("Dias com saída", str(int(dias)))
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("### Top contas / custos")
    r1, r2 = st.columns(2)
    with r1:
        by_conta = sai_v.groupby("CONTA")["VALOR"].sum().reset_index().sort_values("VALOR", ascending=False).head(25)
        st.dataframe(by_conta.assign(**{"R$": by_conta["VALOR"].map(fmt_brl)}).drop(columns=["VALOR"]), use_container_width=True, hide_index=True)
    with r2:
        by_ind = sai_v.groupby("INDIRETO")["VALOR"].sum().reset_index().sort_values("VALOR", ascending=False).head(25)
        st.dataframe(by_ind.assign(**{"R$": by_ind["VALOR"].map(fmt_brl)}).drop(columns=["VALOR"]), use_container_width=True, hide_index=True)

    st.markdown("### Evolução diária")
    daily = sai_v.groupby("DATA_REF")["VALOR"].sum().reset_index().sort_values("DATA_REF")
    if daily.empty:
        st.info("Sem saídas no período.")
    else:
        line = alt.Chart(daily).mark_line(point=True).encode(
            x=alt.X("DATA_REF:T", title="Data"),
            y=alt.Y("VALOR:Q", title="R$"),
            tooltip=[alt.Tooltip("DATA_REF:T", title="Data"), alt.Tooltip("VALOR:Q", format=",.2f", title="R$")],
        ).properties(height=320)
        st.altair_chart(line, use_container_width=True)

    st.markdown("### Detalhamento (linhas)")
    out = sai_v.sort_values("DATA_REF", ascending=False).copy()
    if not out.empty:
        out["R$ Valor"] = out["VALOR"].map(fmt_brl)
    st.dataframe(out.drop(columns=["VALOR"], errors="ignore"), use_container_width=True, hide_index=True)

# ------------------ PAGE: INVESTIMENTOS ------------------
elif page.startswith("🟨"):
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Investimentos (detecção automática)")
    st.markdown("<div class='small'>Regra atual: CONTA/INDIRETO/OBJETO contendo “INVEST”. Você pode ajustar depois para refletir exatamente o seu plano de contas.</div>", unsafe_allow_html=True)

    if sai_f.empty:
        st.info("Sem saídas no período.")
    else:
        inv = sai_f.loc[inv_mask].copy()
        st.markdown("<div class='panel'>", unsafe_allow_html=True)
        c1, c2, c3 = st.columns(3)
        with c1: st_kpi("Total investimentos", fmt_brl(inv["VALOR"].sum() if not inv.empty else 0))
        with c2: st_kpi("Lançamentos", str(int(len(inv))))
        with c3:
            top = inv.groupby("CONTA")["VALOR"].sum().reset_index().sort_values("VALOR", ascending=False).head(1)
            top_txt = f"{top.iloc[0]['CONTA']} ({fmt_brl(top.iloc[0]['VALOR'])})" if not top.empty else "—"
            st_kpi("Maior conta", top_txt)
        st.markdown("</div>", unsafe_allow_html=True)

        by_conta = inv.groupby("CONTA")["VALOR"].sum().reset_index().sort_values("VALOR", ascending=False)
        if by_conta.empty:
            st.info("Nenhum investimento identificado pelo filtro atual.")
        else:
            bar = alt.Chart(by_conta.head(30)).mark_bar().encode(
                x=alt.X("CONTA:N", sort='-y', title="Conta"),
                y=alt.Y("VALOR:Q", title="R$"),
                tooltip=["CONTA", alt.Tooltip("VALOR:Q", format=",.2f")]
            ).properties(height=360)
            st.altair_chart(bar, use_container_width=True)

        inv_out = inv.sort_values("DATA_REF", ascending=False).copy()
        inv_out["R$ Valor"] = inv_out["VALOR"].map(fmt_brl)
        st.dataframe(inv_out.drop(columns=["VALOR"], errors="ignore"), use_container_width=True, hide_index=True)

# ------------------ PAGE: FLUXO DE CAIXA ------------------
elif page.startswith("💧"):
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Fluxo de Caixa (Entradas x Saídas)")

    fluxo = compute_fluxo_caixa(ent_f, sai_f, trf_f)
    if fluxo.empty:
        st.info("Sem dados suficientes para fluxo.")
    else:
        # KPIs
        c1, c2, c3, c4 = st.columns(4)
        with c1: st_kpi("Entradas", fmt_brl(fluxo["ENTRADAS"].sum()))
        with c2: st_kpi("Saídas", fmt_brl(fluxo["SAIDAS"].sum()))
        with c3:
            saldo = float(fluxo["SALDO_DIA"].sum())
            st_kpi("Saldo do período", fmt_brl(saldo), badge=("positivo","good") if saldo>=0 else ("negativo","bad"))
        with c4:
            best = fluxo.sort_values("SALDO_DIA", ascending=False).head(1)
            txt = best["DATA"].iloc[0].strftime("%d/%m/%Y") if not best.empty else "—"
            st_kpi("Melhor dia", txt)

        # Gráfico linha: saldo diário
        melt = fluxo.melt(id_vars=["DATA"], value_vars=["ENTRADAS","SAIDAS","SALDO_DIA"], var_name="Métrica", value_name="Valor")
        line = alt.Chart(melt).mark_line(point=True).encode(
            x=alt.X("DATA:T", title="Data"),
            y=alt.Y("Valor:Q", title="R$"),
            color=alt.Color("Métrica:N", legend=alt.Legend(title="")),
            tooltip=[alt.Tooltip("DATA:T", title="Data"), "Métrica", alt.Tooltip("Valor:Q", format=",.2f")]
        ).properties(height=360)
        st.altair_chart(line, use_container_width=True)

        # Tabela diária
        tbl = fluxo.copy()
        tbl["R$ Entradas"] = tbl["ENTRADAS"].map(fmt_brl)
        tbl["R$ Saídas"] = tbl["SAIDAS"].map(fmt_brl)
        tbl["R$ Saldo"] = tbl["SALDO_DIA"].map(fmt_brl)
        st.dataframe(tbl[["DATA","R$ Entradas","R$ Saídas","R$ Saldo"]].sort_values("DATA", ascending=False), use_container_width=True, hide_index=True)

# ------------------ PAGE: CONCILIAÇÃO ------------------
elif page.startswith("🧾"):
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Conciliação (base do app)")
    st.markdown("<div class='small'>Esta tela é para validar se os lançamentos batem com o que foi pago/recebido. Se você usa a aba “7. Conciliação” com fórmulas, ela pode continuar existindo; aqui nós calculamos tudo a partir das abas 4/5/6.</div>", unsafe_allow_html=True)

    # Conciliação por banco (saídas pagas por banco + transferências por banco)
    if sai_f.empty:
        st.info("Sem saídas no período.")
    else:
        # Saídas pagas (considera DATA_REF como evento do caixa)
        by_bank_out = sai_f.groupby("BANCO")["VALOR"].sum().reset_index().rename(columns={"VALOR":"SAÍDAS"})
        # Entradas não têm banco na aba; se você quiser, dá para adicionar uma coluna "Banco" na aba 4.
        # Transferências: entradas e saídas por banco
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

        st.markdown("### Auditoria — transferências do período")
        if trf_f.empty:
            st.info("Sem transferências no período.")
        else:
            tt = trf_f.sort_values("DATA", ascending=False).copy()
            tt["R$"] = tt["VALOR"].map(fmt_brl)
            st.dataframe(tt.drop(columns=["VALOR"], errors="ignore"), use_container_width=True, hide_index=True)

# ------------------ PAGE: EXPORTAR ------------------
else:
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Exportar dados do período")
    st.markdown("<div class='small'>Baixa os dados já filtrados (mês + período + captação + banco). Você pode gerar o Power BI depois, se quiser.</div>", unsafe_allow_html=True)

    # Entradas
    ent_out = ent_f.copy()
    if not ent_out.empty:
        ent_out["R$ Entrada"] = ent_out["VALOR"].map(fmt_brl)
    csv_ent = ent_out.drop(columns=["VALOR"], errors="ignore").to_csv(index=False).encode("utf-8-sig")
    st.download_button("Baixar Entradas (CSV)", data=csv_ent, file_name=f"entradas_{ym_sel}.csv", mime="text/csv")

    # Saídas
    sai_out = sai_f.copy()
    if not sai_out.empty:
        sai_out["R$ Valor"] = sai_out["VALOR"].map(fmt_brl)
    csv_sai = sai_out.drop(columns=["VALOR"], errors="ignore").to_csv(index=False).encode("utf-8-sig")
    st.download_button("Baixar Saídas (CSV)", data=csv_sai, file_name=f"saidas_{ym_sel}.csv", mime="text/csv")

    # Transferências
    trf_out = trf_f.copy()
    if not trf_out.empty:
        trf_out["R$"] = trf_out["VALOR"].map(fmt_brl)
    csv_trf = trf_out.drop(columns=["VALOR"], errors="ignore").to_csv(index=False).encode("utf-8-sig")
    st.download_button("Baixar Transferências (CSV)", data=csv_trf, file_name=f"transferencias_{ym_sel}.csv", mime="text/csv")

    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("### Observações do modelo")
    st.markdown(
        """
- **Entradas**: usa **DATA** como evento de caixa.
- **Saídas**: usa **PAGAMENTO** quando existe; se estiver vazio, usa **VENCIMENTO** (DATA_REF).
- **Transferências**: não entram no resultado (lucro), mas aparecem em conciliação (movimentação entre bancos).
- **Investimentos**: regra automática por texto (“INVEST”). Ajustável para o seu plano de contas real.
""".strip()
    )
