# -*- coding: utf-8 -*-
"""Dashboard Financeiro — Streamlit (Google Sheets) — single-file

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
from datetime import datetime, date, timedelta
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
  --bg:#F7F8FA; --card:#FFFFFF; --ink:#111827; --muted:#6B7280; --line:#E5E7EB;
  --green:#16A34A; --red:#DC2626; --amber:#D97706; --blue:#2563EB;
}
html, body, [class*="css"] { font-family: Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Helvetica Neue, Arial, sans-serif; }
body{ background:var(--bg); color:var(--ink); }
.block-container{ padding-top: 1.2rem; padding-bottom: 2rem; max-width: 1400px; }
.app-header{
  display:flex; align-items:center; justify-content:space-between; gap:16px;
  background: linear-gradient(135deg, #0F172A, #111827 60%, #1F2937);
  color:white; border-radius:20px; padding:18px 20px; margin-bottom:14px;
  box-shadow: 0 10px 30px rgba(17,24,39,.14);
}
.app-title{ display:flex; align-items:center; gap:14px; }
.app-title img{ width:48px; height:48px; object-fit:contain; border-radius:12px; background:#fff; padding:6px; }
.app-title h1{ font-size:1.35rem; line-height:1.2; margin:0; font-weight:800; }
.app-title p{ margin:2px 0 0 0; color:#D1D5DB; font-size:.92rem; }
.pill{
  display:inline-flex; align-items:center; gap:6px; padding:7px 10px; border-radius:999px;
  background:rgba(255,255,255,.08); border:1px solid rgba(255,255,255,.12); color:#fff;
  font-size:.83rem; font-weight:600;
}
.card{
  background:var(--card); border:1px solid var(--line); border-radius:18px; padding:16px 16px 14px 16px;
  box-shadow: 0 6px 18px rgba(17,24,39,.05);
}
.kpi{ padding:14px 14px 12px 14px; border-radius:18px; background:var(--card); border:1px solid var(--line); box-shadow: 0 6px 18px rgba(17,24,39,.05); }
.kpi .label{ color:var(--muted); font-size:.86rem; font-weight:600; margin-bottom:6px; }
.kpi .value{ font-size:1.45rem; font-weight:800; letter-spacing:-.02em; }
.kpi .sub{ color:var(--muted); font-size:.82rem; margin-top:2px; }
.badge{ display:inline-block; padding:3px 8px; border-radius:999px; font-size:.74rem; font-weight:700; margin-left:8px; vertical-align:middle; }
.badge.good{ background:#DCFCE7; color:#166534; border:1px solid #BBF7D0; }
.badge.bad{ background:#FEE2E2; color:#991B1B; border:1px solid #FECACA; }
.badge.warn{ background:#FEF3C7; color:#92400E; border:1px solid #FDE68A; }
.section-title{ font-size:1.02rem; font-weight:800; margin: 2px 0 10px 0; }
.hr{ height:1px; background:var(--line); margin: 8px 0 14px 0; }
.small{ color:var(--muted); font-size:.82rem; }
[data-testid="stSidebar"]{
  background: linear-gradient(180deg, #0B1220, #111827 45%, #0B1220);
  border-right:1px solid rgba(255,255,255,.06);
}
[data-testid="stSidebar"] * { color:#F9FAFB; }
.sidebar-logo{
  display:flex; align-items:center; gap:10px; margin-bottom:6px;
}
.sidebar-logo img{ width:38px; height:38px; object-fit:contain; border-radius:10px; background:#fff; padding:5px; }
.sidebar-logo h2{ font-size:1rem; margin:0; font-weight:800; }
.sidebar-logo p{ margin:0; font-size:.8rem; color:#CBD5E1; }
.stTabs [data-baseweb="tab-list"]{ gap:8px; }
.stTabs [data-baseweb="tab"]{
  height:38px; background:#F3F4F6; border-radius:12px; padding: 0 12px; border:1px solid #E5E7EB;
}
.stTabs [aria-selected="true"]{
  background:#EEF2FF !important; border-color:#C7D2FE !important; color:#1D4ED8 !important; font-weight:700;
}
div[data-testid="stMetric"]{
  background:#fff; border:1px solid var(--line); border-radius:16px; padding:12px 14px;
}
</style>
""",
    unsafe_allow_html=True,
)

# ====================== HELPERS ======================
def _strip_accents(s: str) -> str:
    s = "" if s is None else str(s)
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")

def _upper(s: str) -> str:
    return _strip_accents(str(s)).strip().upper()

def _norm_col(s: str) -> str:
    s = _upper(s)
    s = re.sub(r"[^A-Z0-9]+", " ", s).strip()
    return s

def fmt_brl(x):
    try:
        return f"R$ {float(x):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return "R$ 0,00"

def safe_num(x):
    try:
        return float(x)
    except:
        return 0.0

def month_label(ym: str) -> str:
    try:
        y, m = ym.split("-")
        nomes = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
        return f"{nomes[int(m)-1]}/{y[2:]}"
    except:
        return str(ym)

def to_ym(dt) -> Optional[str]:
    if pd.isna(dt):
        return None
    try:
        d = pd.to_datetime(dt)
        return f"{d.year:04d}-{d.month:02d}"
    except:
        return None

def parse_date_any(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return pd.NaT
    s = str(x).strip()
    if not s:
        return pd.NaT

    # excel serial
    try:
        if re.fullmatch(r"\d+(\.0+)?", s):
            n = float(s)
            if 20000 <= n <= 60000:
                return pd.Timestamp("1899-12-30") + pd.to_timedelta(int(n), unit="D")
    except:
        pass

    s = s.replace(".", "/").replace("-", "/")
    for dayfirst in [True, False]:
        try:
            d = pd.to_datetime(s, dayfirst=dayfirst, errors="raise")
            return d.normalize()
        except:
            pass
    return pd.NaT

def money_to_float(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return 0.0
    if isinstance(x, (int, float, np.number)):
        return float(x)
    s = str(x).strip()
    if not s:
        return 0.0
    s = s.replace("R$", "").replace("r$", "").replace(" ", "")
    s = s.replace(".", "").replace(",", ".")
    s = re.sub(r"[^0-9\.-]", "", s)
    try:
        return float(s)
    except:
        return 0.0

def pick_col(cols: List[str], *cands) -> Optional[str]:
    norm = {_norm_col(c): c for c in cols}
    for cand in cands:
        nc = _norm_col(cand)
        if nc in norm:
            return norm[nc]
    # contains fallback
    for cand in cands:
        nc = _norm_col(cand)
        for k, v in norm.items():
            if nc in k:
                return v
    return None

def st_kpi(label, value, sub=None, badge=None):
    badge_html = ""
    if badge:
        text, kind = badge
        badge_html = f"<span class='badge {kind}'>{text}</span>"
    st.markdown(
        f"""
        <div class="kpi">
          <div class="label">{label}</div>
          <div class="value">{value}{badge_html}</div>
          <div class="sub">{sub or ""}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

# ====================== GOOGLE SHEETS ======================
def _extract_sheet_id(sheet_ref: str) -> str:
    if not sheet_ref:
        return ""
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", str(sheet_ref))
    return m.group(1) if m else str(sheet_ref).strip()

@st.cache_resource(show_spinner=False)
def get_gspread_client():
    info = st.secrets["gcp_service_account"]
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ]
    creds = Credentials.from_service_account_info(dict(info), scopes=scopes)
    return gspread.authorize(creds)

@st.cache_data(ttl=300, show_spinner=False)
def read_worksheet_as_df(sheet_id: str, worksheet_name: str) -> pd.DataFrame:
    gc = get_gspread_client()
    sh = gc.open_by_key(sheet_id)
    ws = sh.worksheet(worksheet_name)
    vals = ws.get_all_values()
    if not vals:
        return pd.DataFrame()
    header = vals[0]
    rows = vals[1:]
    # pad rows
    width = max(len(header), max((len(r) for r in rows), default=0))
    header = list(header) + [f"COL_{i}" for i in range(len(header)+1, width+1)]
    rows2 = []
    for r in rows:
        rr = list(r) + [""] * (width - len(r))
        rows2.append(rr[:width])
    df = pd.DataFrame(rows2, columns=header[:width])
    return df

@st.cache_data(ttl=300, show_spinner=False)
def list_worksheets(sheet_id: str) -> List[str]:
    gc = get_gspread_client()
    sh = gc.open_by_key(sheet_id)
    return [ws.title for ws in sh.worksheets()]

# ====================== LOAD DATA ======================
def normalize_entradas(df_raw: pd.DataFrame) -> pd.DataFrame:
    if df_raw is None or df_raw.empty:
        return pd.DataFrame(columns=["DATA", "YM", "CAPTACAO", "BANCO", "CLIENTE", "VALOR"])

    df = df_raw.copy()
    df.columns = [_norm_col(c) for c in df.columns]

    c_data = pick_col(list(df.columns), "DATA", "DATA ENTRADA", "DATA RECEBIMENTO", "RECEBIMENTO")
    c_val = pick_col(list(df.columns), "VALOR", "R$ ENTRADA", "R$ENTRADA", "R$")
    c_banco = pick_col(list(df.columns), "BANCO", "CONTA BANCARIA", "CONTA BANCÁRIA")
    c_cliente = pick_col(list(df.columns), "CLIENTE", "CLIENTES", "NOME")
    c_capt = pick_col(list(df.columns), "CAPTACAO", "CAPTAÇÃO", "FORMA DE CAPTACAO", "FORMA DE CAPTAÇÃO")

    out = pd.DataFrame()
    out["DATA"] = df[c_data].apply(parse_date_any) if c_data else pd.NaT
    out["YM"] = out["DATA"].apply(to_ym)
    out["VALOR"] = df[c_val].apply(money_to_float) if c_val else 0.0
    out["BANCO"] = df[c_banco].astype(str).map(_upper) if c_banco else ""
    out["CLIENTE"] = df[c_cliente].astype(str).map(_upper) if c_cliente else ""
    out["CAPTACAO"] = df[c_capt].astype(str).map(_upper) if c_capt else out["CLIENTE"]

    # mantém colunas úteis extras
    extras = {}
    for cand in ["OBS", "OBSERVACAO", "OBSERVAÇÃO", "UNIDADE", "CIDADE", "CATEGORIA"]:
        c = pick_col(list(df.columns), cand)
        if c:
            extras[cand] = df[c]
    for k, v in extras.items():
        out[_norm_col(k)] = v

    out = out[~out["DATA"].isna()].copy()
    return out

def normalize_saidas(df_raw: pd.DataFrame) -> pd.DataFrame:
    if df_raw is None or df_raw.empty:
        return pd.DataFrame(columns=["DATA_REF", "YM", "VENCIMENTO", "PAGAMENTO", "BANCO", "FORNECEDOR", "CONTA", "VALOR"])

    df = df_raw.copy()
    df.columns = [_norm_col(c) for c in df.columns]

    c_venc = pick_col(list(df.columns), "VENCIMENTO", "DATA VENCIMENTO")
    c_pag = pick_col(list(df.columns), "PAGAMENTO", "DATA PAGAMENTO", "PAGO EM")
    c_data = pick_col(list(df.columns), "DATA", "COMPETENCIA", "COMPETÊNCIA")
    c_val = pick_col(list(df.columns), "VALOR", "R$ SAIDA", "R$SAIDA", "R$")
    c_banco = pick_col(list(df.columns), "BANCO", "CONTA BANCARIA", "CONTA BANCÁRIA")
    c_forn = pick_col(list(df.columns), "FORNECEDOR", "NOME FORNECEDOR", "FAVORECIDO")
    c_conta = pick_col(list(df.columns), "CONTA", "CATEGORIA", "TIPO", "DESPESA")

    out = pd.DataFrame()
    out["VENCIMENTO"] = df[c_venc].apply(parse_date_any) if c_venc else pd.NaT
    out["PAGAMENTO"] = df[c_pag].apply(parse_date_any) if c_pag else pd.NaT
    comp = df[c_data].apply(parse_date_any) if c_data else pd.NaT

    out["DATA_REF"] = out["PAGAMENTO"]
    if isinstance(comp, pd.Series):
        out["DATA_REF"] = out["DATA_REF"].where(out["DATA_REF"].notna(), comp)
    out["DATA_REF"] = out["DATA_REF"].where(out["DATA_REF"].notna(), out["VENCIMENTO"])
    out["YM"] = out["DATA_REF"].apply(to_ym)

    out["VALOR"] = df[c_val].apply(money_to_float) if c_val else 0.0
    out["BANCO"] = df[c_banco].astype(str).map(_upper) if c_banco else ""
    out["FORNECEDOR"] = df[c_forn].astype(str).map(_upper) if c_forn else ""
    out["CONTA"] = df[c_conta].astype(str).map(_upper) if c_conta else ""

    extras = {}
    for cand in ["OBS", "OBSERVACAO", "OBSERVAÇÃO", "UNIDADE", "CIDADE", "STATUS"]:
        c = pick_col(list(df.columns), cand)
        if c:
            extras[cand] = df[c]
    for k, v in extras.items():
        out[_norm_col(k)] = v

    out = out[~out["DATA_REF"].isna()].copy()
    return out

def normalize_transferencias(df_raw: pd.DataFrame) -> pd.DataFrame:
    if df_raw is None or df_raw.empty:
        return pd.DataFrame(columns=["DATA", "YM", "ORIGEM", "DESTINO", "VALOR"])

    df = df_raw.copy()
    df.columns = [_norm_col(c) for c in df.columns]

    c_data = pick_col(list(df.columns), "DATA", "DATA TRANSFERENCIA", "DATA TRANSFERÊNCIA")
    c_ori = pick_col(list(df.columns), "ORIGEM", "BANCO ORIGEM", "DE")
    c_des = pick_col(list(df.columns), "DESTINO", "BANCO DESTINO", "PARA")
    c_val = pick_col(list(df.columns), "VALOR", "R$ TRANSFERENCIA", "R$TRANSFERENCIA", "R$")

    out = pd.DataFrame()
    out["DATA"] = df[c_data].apply(parse_date_any) if c_data else pd.NaT
    out["YM"] = out["DATA"].apply(to_ym)
    out["ORIGEM"] = df[c_ori].astype(str).map(_upper) if c_ori else ""
    out["DESTINO"] = df[c_des].astype(str).map(_upper) if c_des else ""
    out["VALOR"] = df[c_val].apply(money_to_float) if c_val else 0.0

    extras = {}
    for cand in ["OBS", "OBSERVACAO", "OBSERVAÇÃO"]:
        c = pick_col(list(df.columns), cand)
        if c:
            extras[cand] = df[c]
    for k, v in extras.items():
        out[_norm_col(k)] = v

    out = out[~out["DATA"].isna()].copy()
    return out

def read_optional_balance_table(sheet_id: str) -> Tuple[pd.DataFrame, Optional[date]]:
    """
    Lê uma aba opcional de saldo inicial.
    Procura por nomes comuns: '3. Saldo Inicial', 'Saldo Inicial', 'Saldos', etc.
    Retorna df com colunas BANCO, SALDO_INICIAL e a data-base (se houver).
    """
    names = list_worksheets(sheet_id)
    prefer = [
        "3. Saldo Inicial", "3. SALDO INICIAL", "Saldo Inicial", "SALDO INICIAL",
        "Saldos", "SALDOS", "3. Saldos"
    ]
    target = None
    for p in prefer:
        if p in names:
            target = p
            break
    if target is None:
        # tenta contains
        for n in names:
            if "SALDO" in _upper(n):
                target = n
                break
    if not target:
        return pd.DataFrame(columns=["BANCO", "SALDO_INICIAL"]), None

    raw = read_worksheet_as_df(sheet_id, target)
    if raw.empty:
        return pd.DataFrame(columns=["BANCO", "SALDO_INICIAL"]), None

    df = raw.copy()
    df.columns = [_norm_col(c) for c in df.columns]

    c_banco = pick_col(list(df.columns), "BANCO", "CONTA", "CONTA BANCARIA", "CONTA BANCÁRIA")
    c_saldo = pick_col(list(df.columns), "SALDO INICIAL", "SALDO", "VALOR", "R$")
    c_data = pick_col(list(df.columns), "DATA BASE", "DATA", "COMPETENCIA", "COMPETÊNCIA", "MES", "MÊS")

    out = pd.DataFrame()
    out["BANCO"] = df[c_banco].astype(str).map(_upper) if c_banco else ""
    out["SALDO_INICIAL"] = df[c_saldo].apply(money_to_float) if c_saldo else 0.0
    out = out[(out["BANCO"] != "")].copy()

    base_date = None
    if c_data:
        vals = df[c_data].dropna().astype(str).tolist()
        for v in vals:
            d = parse_date_any(v)
            if pd.notna(d):
                base_date = d.date()
                break

    return out, base_date

def parse_conciliacao(df_raw: pd.DataFrame):
    """
    Tenta interpretar a aba 7. Conciliação no formato:
    DIA | ENTRADAS | SAIDAS | SALDO DIA | SALDO ACUMULADO MÊS
    e também captura o 'SALDO ACUMULADO BANCOS'.

    Retorna:
      conc_tbl: DataFrame com colunas DIA, ENTRADAS, SAIDAS, SALDO_DIA, SALDO_ACUM
      saldo_bancos_total: float ou None
      df_raw_original: para leitura livre se necessário
    """
    if df_raw is None or df_raw.empty:
        return pd.DataFrame(columns=["DIA", "ENTRADAS", "SAIDAS", "SALDO_DIA", "SALDO_ACUM"]), None, df_raw

    # preserva original para busca livre
    raw = df_raw.copy()

    # versão normalizada
    df = df_raw.copy()
    df.columns = [_norm_col(c) for c in df.columns]

    # Tenta leitura direta por cabeçalho
    c_dia = pick_col(list(df.columns), "DIA")
    c_ent = pick_col(list(df.columns), "ENTRADAS")
    c_sai = pick_col(list(df.columns), "SAIDAS", "SAÍDAS")
    c_sdia = pick_col(list(df.columns), "SALDO DIA")
    c_sac = pick_col(list(df.columns), "SALDO ACUMULADO MES", "SALDO ACUMULADO MÊS", "SALDO ACUMULADO")

    conc_tbl = pd.DataFrame()
    if c_dia and c_ent and c_sai and c_sdia and c_sac:
        conc_tbl["DIA"] = pd.to_numeric(df[c_dia], errors="coerce")
        conc_tbl["ENTRADAS"] = df[c_ent].apply(money_to_float)
        conc_tbl["SAIDAS"] = df[c_sai].apply(money_to_float)
        conc_tbl["SALDO_DIA"] = df[c_sdia].apply(money_to_float)
        conc_tbl["SALDO_ACUM"] = df[c_sac].apply(money_to_float)
        conc_tbl = conc_tbl.dropna(subset=["DIA"]).copy()
        conc_tbl["DIA"] = conc_tbl["DIA"].astype(int)
        conc_tbl = conc_tbl.sort_values("DIA")

    # Se falhar, tenta localizar a tabela manualmente dentro do sheet
    if conc_tbl.empty:
        arr = raw.fillna("").astype(str).values.tolist()
        header_row = None
        col_idx = {}
        for i, row in enumerate(arr):
            normrow = [_norm_col(x) for x in row]
            try:
                idx_d = normrow.index("DIA")
                # procura as demais em qualquer posição
                poss = {v: j for j, v in enumerate(normrow)}
                if "ENTRADAS" in poss and ("SAIDAS" in poss or "SAÍDAS" in poss) and "SALDO DIA" in poss:
                    header_row = i
                    col_idx = {
                        "DIA": idx_d,
                        "ENTRADAS": poss.get("ENTRADAS"),
                        "SAIDAS": poss.get("SAIDAS", poss.get("SAÍDAS")),
                        "SALDO_DIA": poss.get("SALDO DIA"),
                        "SALDO_ACUM": poss.get("SALDO ACUMULADO MES", poss.get("SALDO ACUMULADO MÊS", poss.get("SALDO ACUMULADO")))
                    }
                    if col_idx["SALDO_ACUM"] is not None:
                        break
            except ValueError:
                continue

        if header_row is not None and col_idx.get("SALDO_ACUM") is not None:
            rows = []
            for row in arr[header_row + 1:]:
                dia = row[col_idx["DIA"]] if col_idx["DIA"] < len(row) else ""
                if not str(dia).strip():
                    continue
                try:
                    dia_int = int(float(str(dia).replace(",", ".")))
                except:
                    continue
                rows.append({
                    "DIA": dia_int,
                    "ENTRADAS": money_to_float(row[col_idx["ENTRADAS"]]) if col_idx["ENTRADAS"] is not None and col_idx["ENTRADAS"] < len(row) else 0.0,
                    "SAIDAS": money_to_float(row[col_idx["SAIDAS"]]) if col_idx["SAIDAS"] is not None and col_idx["SAIDAS"] < len(row) else 0.0,
                    "SALDO_DIA": money_to_float(row[col_idx["SALDO_DIA"]]) if col_idx["SALDO_DIA"] is not None and col_idx["SALDO_DIA"] < len(row) else 0.0,
                    "SALDO_ACUM": money_to_float(row[col_idx["SALDO_ACUM"]]) if col_idx["SALDO_ACUM"] is not None and col_idx["SALDO_ACUM"] < len(row) else 0.0,
                })
            conc_tbl = pd.DataFrame(rows).sort_values("DIA") if rows else pd.DataFrame()

    # Captura "SALDO ACUMULADO BANCOS"
    saldo_bancos_total = None
    arr = raw.fillna("").astype(str).values.tolist()
    for i, row in enumerate(arr):
        for j, val in enumerate(row):
            txt = _upper(val)
            if "SALDO ACUMULADO" in txt and "BANCO" in txt:
                # valor tende a estar na célula ao lado
                if j + 1 < len(row):
                    v = money_to_float(row[j + 1])
                    saldo_bancos_total = v
                    break
        if saldo_bancos_total is not None:
            break

    return conc_tbl, saldo_bancos_total, raw

# ====================== FLUXO / SALDO POR BANCO ======================
def compute_saldo_bancos(
    ent_hist: pd.DataFrame,
    sai_hist: pd.DataFrame,
    trf_hist: pd.DataFrame,
    df_saldo_ini: pd.DataFrame,
    saldo_base_date: Optional[date] = None,
):
    """
    Calcula saldos por banco ao longo do tempo:
      saldo_real = saldo_inicial + entradas - saídas + transfer in - transfer out

    Retorna:
      mv_banks_daily: DataFrame diário por BANCO com colunas
         DATA, BANCO, ENTRADAS, SAIDAS, TRF_IN, TRF_OUT, SALDO_MOV, SALDO_INICIAL, SALDO_REAL
      resumo_banks: DataFrame final por BANCO
    """
    frames = []

    if ent_hist is not None and not ent_hist.empty:
        x = ent_hist[["DATA", "BANCO", "VALOR"]].copy()
        x["ENTRADAS"] = x["VALOR"]
        x["SAIDAS"] = 0.0
        x["TRF_IN"] = 0.0
        x["TRF_OUT"] = 0.0
        frames.append(x[["DATA", "BANCO", "ENTRADAS", "SAIDAS", "TRF_IN", "TRF_OUT"]])

    if sai_hist is not None and not sai_hist.empty:
        x = sai_hist[["DATA_REF", "BANCO", "VALOR"]].copy()
        x = x.rename(columns={"DATA_REF": "DATA"})
        x["ENTRADAS"] = 0.0
        x["SAIDAS"] = x["VALOR"]
        x["TRF_IN"] = 0.0
        x["TRF_OUT"] = 0.0
        frames.append(x[["DATA", "BANCO", "ENTRADAS", "SAIDAS", "TRF_IN", "TRF_OUT"]])

    if trf_hist is not None and not trf_hist.empty:
        # saída na origem
        xo = trf_hist[["DATA", "ORIGEM", "VALOR"]].copy()
        xo = xo.rename(columns={"ORIGEM": "BANCO"})
        xo["ENTRADAS"] = 0.0
        xo["SAIDAS"] = 0.0
        xo["TRF_IN"] = 0.0
        xo["TRF_OUT"] = xo["VALOR"]
        frames.append(xo[["DATA", "BANCO", "ENTRADAS", "SAIDAS", "TRF_IN", "TRF_OUT"]])

        # entrada no destino
        xd = trf_hist[["DATA", "DESTINO", "VALOR"]].copy()
        xd = xd.rename(columns={"DESTINO": "BANCO"})
        xd["ENTRADAS"] = 0.0
        xd["SAIDAS"] = 0.0
        xd["TRF_IN"] = xd["VALOR"]
        xd["TRF_OUT"] = 0.0
        frames.append(xd[["DATA", "BANCO", "ENTRADAS", "SAIDAS", "TRF_IN", "TRF_OUT"]])

    if not frames:
        return pd.DataFrame(columns=["DATA", "BANCO", "ENTRADAS", "SAIDAS", "TRF_IN", "TRF_OUT", "SALDO_MOV", "SALDO_INICIAL", "SALDO_REAL"]), pd.DataFrame(columns=["BANCO", "SALDO_INICIAL", "SALDO_MOV", "SALDO_REAL_FINAL"])

    mv = pd.concat(frames, ignore_index=True)
    mv["DATA"] = pd.to_datetime(mv["DATA"]).dt.date
    mv["BANCO"] = mv["BANCO"].astype(str).map(_upper)
    mv = mv[(mv["BANCO"] != "") & mv["DATA"].notna()].copy()

    daily = (
        mv.groupby(["DATA", "BANCO"], as_index=False)[["ENTRADAS", "SAIDAS", "TRF_IN", "TRF_OUT"]]
        .sum()
        .sort_values(["BANCO", "DATA"])
    )
    daily["SALDO_MOV"] = daily["ENTRADAS"] - daily["SAIDAS"] + daily["TRF_IN"] - daily["TRF_OUT"]

    sal = df_saldo_ini.copy() if (df_saldo_ini is not None and not df_saldo_ini.empty) else pd.DataFrame(columns=["BANCO", "SALDO_INICIAL"])
    if not sal.empty:
        sal["BANCO"] = sal["BANCO"].astype(str).map(_upper)
        sal["SALDO_INICIAL"] = sal["SALDO_INICIAL"].apply(money_to_float)

    daily = daily.merge(sal[["BANCO", "SALDO_INICIAL"]] if not sal.empty else sal, on="BANCO", how="left")
    daily["SALDO_INICIAL"] = daily["SALDO_INICIAL"].fillna(0.0)

    # saldo real acumulado por banco
    daily["SALDO_REAL"] = (
        daily.groupby("BANCO")["SALDO_MOV"].cumsum() + daily["SALDO_INICIAL"]
    )

    resumo = daily.sort_values(["BANCO", "DATA"]).groupby("BANCO", as_index=False).agg(
        SALDO_INICIAL=("SALDO_INICIAL", "max"),
        SALDO_MOV=("SALDO_MOV", "sum"),
        SALDO_REAL_FINAL=("SALDO_REAL", "last"),
    )

    return daily, resumo

def build_fluxo_total_from_mv(mv_banks_daily: pd.DataFrame, banco_sel: List[str], dt_ini: date, dt_fim: date):
    """
    Consolida o fluxo diário total (somando os bancos selecionados ou todos).
    """
    if mv_banks_daily is None or mv_banks_daily.empty:
        return pd.DataFrame(columns=["DATA", "ENTRADAS", "SAIDAS", "SALDO_DIA", "SALDO_REAL"])

    x = mv_banks_daily.copy()
    if banco_sel:
        bset = [_upper(b) for b in banco_sel]
        x = x[x["BANCO"].isin(bset)].copy()

    if dt_ini and dt_fim:
        x = x[(x["DATA"] >= dt_ini) & (x["DATA"] <= dt_fim)].copy()

    if x.empty:
        return pd.DataFrame(columns=["DATA", "ENTRADAS", "SAIDAS", "SALDO_DIA", "SALDO_REAL"])

    fluxo = (
        x.groupby("DATA", as_index=False)[["ENTRADAS", "SAIDAS", "TRF_IN", "TRF_OUT", "SALDO_MOV"]]
        .sum()
        .sort_values("DATA")
    )

    fluxo["SALDO_DIA"] = fluxo["ENTRADAS"] - fluxo["SAIDAS"]
    # saldo real acumulado no período selecionado = soma dos saldos reais por banco ao final de cada dia
    saldo_real = (
        x.groupby(["DATA", "BANCO"], as_index=False)["SALDO_REAL"].last()
        .groupby("DATA", as_index=False)["SALDO_REAL"].sum()
        .sort_values("DATA")
    )
    fluxo = fluxo.merge(saldo_real, on="DATA", how="left")
    return fluxo[["DATA", "ENTRADAS", "SAIDAS", "SALDO_DIA", "SALDO_REAL"]]

# ====================== APP START ======================
sheet_ref = st.secrets.get("finance_sheet_id", "")
sheet_id = _extract_sheet_id(sheet_ref)

if not sheet_id:
    st.error("Defina 'finance_sheet_id' nos Secrets do Streamlit.")
    st.stop()

# Sidebar branding
with st.sidebar:
    if LOGO_URL:
        st.markdown(
            f"""
            <div class="sidebar-logo">
              <img src="{LOGO_URL}" alt="logo"/>
              <div>
                <h2>{COMPANY_NAME}</h2>
                <p>Painel Financeiro</p>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f"""
            <div class="sidebar-logo">
              <div>
                <h2>{COMPANY_NAME}</h2>
                <p>Painel Financeiro</p>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

# Header
logo_html = f'<img src="{LOGO_URL}" alt="logo"/>' if LOGO_URL else ""
st.markdown(
    f"""
    <div class="app-header">
      <div class="app-title">
        {logo_html}
        <div>
          <h1>{COMPANY_NAME} — Dashboard Financeiro</h1>
          <p>Consolidado, comparativos, fluxo de caixa, receber/pagar e conciliação.</p>
        </div>
      </div>
      <div class="pill">Atualização via Google Sheets</div>
    </div>
    """,
    unsafe_allow_html=True,
)

# Read core worksheets
try:
    df_ent_raw = read_worksheet_as_df(sheet_id, "4. Entradas")
except Exception as e:
    st.error(f"Erro ao ler a aba '4. Entradas': {e}")
    st.stop()

try:
    df_sai_raw = read_worksheet_as_df(sheet_id, "5. Saídas")
except Exception as e:
    st.error(f"Erro ao ler a aba '5. Saídas': {e}")
    st.stop()

try:
    df_trf_raw = read_worksheet_as_df(sheet_id, "6. Transferencias")
except Exception:
    # tolera ausência
    df_trf_raw = pd.DataFrame()

# opcionais
df_saldo_ini, saldo_base_date = read_optional_balance_table(sheet_id)

try:
    df_conc_raw = read_worksheet_as_df(sheet_id, "7. Conciliação")
except Exception:
    df_conc_raw = pd.DataFrame()

conc_tbl_all, saldo_bancos_total, df_conc_raw = parse_conciliacao(df_conc_raw)

# normalize
df_ent = normalize_entradas(df_ent_raw)
df_sai = normalize_saidas(df_sai_raw)
df_trf = normalize_transferencias(df_trf_raw)

# month universe
months = sorted(set([m for m in pd.concat([
    df_ent["YM"] if not df_ent.empty else pd.Series(dtype=str),
    df_sai["YM"] if not df_sai.empty else pd.Series(dtype=str),
    df_trf["YM"] if not df_trf.empty else pd.Series(dtype=str),
], ignore_index=True).dropna().astype(str).tolist()]))
months = [m for m in months if m]

if not months:
    st.warning("Não há meses válidos nas abas lidas.")
    st.stop()

# ====================== SIDEBAR FILTERS ======================
with st.sidebar:
    st.markdown("### Filtros")

    page = st.radio(
        "Página",
        [
            "📊 Visão Geral",
            "📈 Entradas",
            "📉 Saídas",
            "🟨 Investimentos",
            "💧 Fluxo de Caixa",
            "⏳ Receber / Pagar",
            "🧾 Conciliação",
            "⬇️ Exportar",
        ],
        index=0,
    )

    ym_focus = st.selectbox("Mês de referência", options=months, index=len(months)-1, format_func=month_label)

    multi_months = st.multiselect(
        "Comparar meses",
        options=months,
        default=[ym_focus],
        format_func=month_label,
    )
    ym_sels = multi_months if multi_months else [ym_focus]

    # datas finas
    # usa o mês focal como padrão do date_input
    y0, m0 = map(int, ym_focus.split("-"))
    start_default = date(y0, m0, 1)
    if m0 == 12:
        end_default = date(y0 + 1, 1, 1) - timedelta(days=1)
    else:
        end_default = date(y0, m0 + 1, 1) - timedelta(days=1)

    dt_ini, dt_fim = st.date_input(
        "Período dentro do mês focal",
        value=(start_default, end_default),
        format="DD/MM/YYYY",
    )

    if isinstance(dt_ini, tuple) or isinstance(dt_ini, list):
        # streamlit antigo pode retornar tuple
        dt_ini, dt_fim = dt_ini[0], dt_ini[1]

    # Captação
    capts = []
    if not df_ent.empty and "CAPTACAO" in df_ent.columns:
        capts = sorted(df_ent[df_ent["YM"].isin(ym_sels)]["CAPTACAO"].dropna().astype(str).map(_upper).unique().tolist())
    capt_sel = st.multiselect("Captação", options=capts, default=[])

    # Banco
    bancos_set = set()

    if (not df_ent.empty) and ("BANCO" in df_ent.columns):
        bancos_set.update(
            df_ent[df_ent["YM"].isin(ym_sels)]["BANCO"]
            .dropna()
            .astype(str)
            .map(_upper)
            .tolist()
        )

    if (not df_sai.empty) and ("BANCO" in df_sai.columns):
        bancos_set.update(
            df_sai[df_sai["YM"].isin(ym_sels)]["BANCO"]
            .dropna()
            .astype(str)
            .map(_upper)
            .tolist()
        )

    if (not df_trf.empty):
        if "ORIGEM" in df_trf.columns:
            bancos_set.update(
                df_trf[df_trf["YM"].isin(ym_sels)]["ORIGEM"]
                .dropna()
                .astype(str)
                .map(_upper)
                .tolist()
            )
        if "DESTINO" in df_trf.columns:
            bancos_set.update(
                df_trf[df_trf["YM"].isin(ym_sels)]["DESTINO"]
                .dropna()
                .astype(str)
                .map(_upper)
                .tolist()
            )

    if (df_saldo_ini is not None) and (not df_saldo_ini.empty) and ("BANCO" in df_saldo_ini.columns):
        bancos_set.update(df_saldo_ini["BANCO"].dropna().astype(str).map(_upper).tolist())

    banco_opts = sorted([b for b in bancos_set if b])
    banco_sel = st.multiselect("Banco", options=banco_opts, default=banco_opts)

def apply_filters():
    ent = df_ent[df_ent["YM"].isin(ym_sels)].copy() if not df_ent.empty else df_ent.copy()
    sai = df_sai[df_sai["YM"].isin(ym_sels)].copy() if not df_sai.empty else df_sai.copy()
    trf = df_trf[df_trf["YM"].isin(ym_sels)].copy() if not df_trf.empty else df_trf.copy()

    if dt_ini and dt_fim:
        if not ent.empty:
            ent = ent[(ent["DATA"] >= dt_ini) & (ent["DATA"] <= dt_fim)].copy()
        if not sai.empty:
            sai = sai[(sai["DATA_REF"] >= dt_ini) & (sai["DATA_REF"] <= dt_fim)].copy()
        if not trf.empty:
            trf = trf[(trf["DATA"] >= dt_ini) & (trf["DATA"] <= dt_fim)].copy()

    if capt_sel and (not ent.empty) and ("CAPTACAO" in ent.columns):
        ent = ent[ent["CAPTACAO"].isin([_upper(x) for x in capt_sel])].copy()

    if banco_sel:
        bset = [_upper(x) for x in banco_sel]

        if (not ent.empty) and ("BANCO" in ent.columns):
            ent = ent[ent["BANCO"].isin(bset)].copy()

        if (not sai.empty) and ("BANCO" in sai.columns):
            sai = sai[sai["BANCO"].isin(bset)].copy()

        if not trf.empty:
            if ("ORIGEM" in trf.columns) and ("DESTINO" in trf.columns):
                trf = trf[(trf["ORIGEM"].isin(bset)) | (trf["DESTINO"].isin(bset))].copy()
            elif "ORIGEM" in trf.columns:
                trf = trf[trf["ORIGEM"].isin(bset)].copy()
            elif "DESTINO" in trf.columns:
                trf = trf[trf["DESTINO"].isin(bset)].copy()

    return ent, sai, trf

ent_f, sai_f, trf_f = apply_filters()

# more derived sets
inv_mask = pd.Series(False, index=sai_f.index)
if not sai_f.empty and "CONTA" in sai_f.columns:
    inv_mask = sai_f["CONTA"].astype(str).map(_upper).str.contains("INVEST", na=False)

# ====================== PAGES ======================
if page.startswith("📊"):
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)

    # KPIs gerais do filtro
    total_ent = float(ent_f["VALOR"].sum()) if not ent_f.empty else 0.0
    total_sai = float(sai_f["VALOR"].sum()) if not sai_f.empty else 0.0
    total_inv = float(sai_f.loc[inv_mask, "VALOR"].sum()) if not sai_f.empty else 0.0
    saldo = total_ent - total_sai

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st_kpi("Entradas", fmt_brl(total_ent), sub=f"{len(ent_f)} lançamentos")
    with c2:
        st_kpi("Saídas", fmt_brl(total_sai), sub=f"{len(sai_f)} lançamentos")
    with c3:
        st_kpi("Investimentos", fmt_brl(total_inv), sub="Detectado por conta/categoria")
    with c4:
        badge = ("positivo", "good") if saldo >= 0 else ("negativo", "bad")
        st_kpi("Resultado", fmt_brl(saldo), sub="Entradas - Saídas", badge=badge)

    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    t1, t2 = st.columns([1.15, 1])

    # Série mensal consolidada
    ent_m = (
        ent_f.groupby("YM", as_index=False)["VALOR"].sum().rename(columns={"VALOR": "Entradas"})
        if not ent_f.empty else pd.DataFrame(columns=["YM", "Entradas"])
    )
    sai_m = (
        sai_f.groupby("YM", as_index=False)["VALOR"].sum().rename(columns={"VALOR": "Saídas"})
        if not sai_f.empty else pd.DataFrame(columns=["YM", "Saídas"])
    )
    serie = pd.DataFrame({"YM": sorted(set(ym_sels))})
    serie = serie.merge(ent_m, on="YM", how="left").merge(sai_m, on="YM", how="left").fillna(0.0)
    serie["Resultado"] = serie["Entradas"] - serie["Saídas"]
    serie["Mês"] = serie["YM"].map(month_label)

    with t1:
        st.markdown("### Evolução mensal")
        base = alt.Chart(serie).encode(x=alt.X("Mês:N", sort=list(serie["Mês"]), title=""))
        bars1 = base.mark_bar(opacity=.85).encode(y=alt.Y("Entradas:Q", title="R$"), tooltip=["Mês", alt.Tooltip("Entradas:Q", format=",.2f")])
        bars2 = base.mark_bar(opacity=.65).encode(y=alt.Y("Saídas:Q", title="R$"), tooltip=["Mês", alt.Tooltip("Saídas:Q", format=",.2f")])
        line = base.mark_line(point=True).encode(y=alt.Y("Resultado:Q", title="R$"), tooltip=["Mês", alt.Tooltip("Resultado:Q", format=",.2f")])
        st.altair_chart((bars1 + bars2 + line).resolve_scale(y="shared"), use_container_width=True)

    with t2:
        st.markdown("### Entradas por captação")
        if ent_f.empty or "CAPTACAO" not in ent_f.columns:
            st.caption("Sem dados de captação no filtro.")
        else:
            cap = (
                ent_f.groupby("CAPTACAO", as_index=False)["VALOR"].sum()
                .sort_values("VALOR", ascending=False)
                .head(12)
            )
            pie = alt.Chart(cap).mark_arc(innerRadius=55).encode(
                theta=alt.Theta("VALOR:Q"),
                color=alt.Color("CAPTACAO:N", legend=alt.Legend(title="Captação")),
                tooltip=["CAPTACAO", alt.Tooltip("VALOR:Q", format=",.2f", title="R$")],
            ).properties(height=320)
            st.altair_chart(pie, use_container_width=True)

    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)

    # Tabela resumo por banco
    st.markdown("### Resumo por banco")
    if banco_opts:
        # Consolidado por banco considerando filtro de meses/datas e captação aplicada nas entradas
        ent_b = ent_f.groupby("BANCO", as_index=False)["VALOR"].sum().rename(columns={"VALOR": "Entradas"}) if (not ent_f.empty and "BANCO" in ent_f.columns) else pd.DataFrame(columns=["BANCO", "Entradas"])
        sai_b = sai_f.groupby("BANCO", as_index=False)["VALOR"].sum().rename(columns={"VALOR": "Saídas"}) if (not sai_f.empty and "BANCO" in sai_f.columns) else pd.DataFrame(columns=["BANCO", "Saídas"])

        trf_out = (
            trf_f.groupby("ORIGEM", as_index=False)["VALOR"].sum().rename(columns={"ORIGEM": "BANCO", "VALOR": "Transfer. Saída"})
            if (not trf_f.empty and "ORIGEM" in trf_f.columns) else pd.DataFrame(columns=["BANCO", "Transfer. Saída"])
        )
        trf_in = (
            trf_f.groupby("DESTINO", as_index=False)["VALOR"].sum().rename(columns={"DESTINO": "BANCO", "VALOR": "Transfer. Entrada"})
            if (not trf_f.empty and "DESTINO" in trf_f.columns) else pd.DataFrame(columns=["BANCO", "Transfer. Entrada"])
        )

        resumo = pd.DataFrame({"BANCO": banco_sel if banco_sel else banco_opts})
        resumo = (
            resumo.merge(ent_b, on="BANCO", how="left")
            .merge(sai_b, on="BANCO", how="left")
            .merge(trf_out, on="BANCO", how="left")
            .merge(trf_in, on="BANCO", how="left")
            .fillna(0.0)
        )
        resumo["Resultado"] = resumo["Entradas"] - resumo["Saídas"]
        resumo["Mov. Líq. c/ Transfer"] = resumo["Resultado"] - resumo["Transfer. Saída"] + resumo["Transfer. Entrada"]

        show = resumo.copy()
        for c in ["Entradas", "Saídas", "Transfer. Saída", "Transfer. Entrada", "Resultado", "Mov. Líq. c/ Transfer"]:
            show[c] = show[c].apply(fmt_brl)
        st.dataframe(show, use_container_width=True, hide_index=True)
    else:
        st.caption("Sem bancos identificados.")

elif page.startswith("📈"):
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Entradas")

    total = float(ent_f["VALOR"].sum()) if not ent_f.empty else 0.0
    c1, c2 = st.columns(2)
    with c1:
        st_kpi("Total de entradas", fmt_brl(total))
    with c2:
        st_kpi("Lançamentos", str(int(len(ent_f))))

    if ent_f.empty:
        st.info("Sem entradas no período/filtro.")
    else:
        a, b = st.columns([1.1, 1])

        with a:
            st.markdown("### Top captações")
            top = (
                ent_f.groupby("CAPTACAO", as_index=False)["VALOR"].sum()
                .sort_values("VALOR", ascending=False)
                .head(15)
            )
            bars = alt.Chart(top).mark_bar().encode(
                x=alt.X("VALOR:Q", title="R$"),
                y=alt.Y("CAPTACAO:N", sort="-x", title=""),
                tooltip=["CAPTACAO", alt.Tooltip("VALOR:Q", format=",.2f", title="R$")],
            ).properties(height=420)
            txt = alt.Chart(top).mark_text(dx=6, align="left").encode(
                x="VALOR:Q", y=alt.Y("CAPTACAO:N", sort="-x"), text=alt.Text("VALOR:Q", format=",.0f")
            )
            st.altair_chart(bars + txt, use_container_width=True)

        with b:
            st.markdown("### Distribuição por banco")
            if "BANCO" in ent_f.columns and ent_f["BANCO"].astype(str).str.len().gt(0).any():
                g = ent_f.groupby("BANCO", as_index=False)["VALOR"].sum().sort_values("VALOR", ascending=False)
                donut = alt.Chart(g).mark_arc(innerRadius=60).encode(
                    theta="VALOR:Q",
                    color=alt.Color("BANCO:N", legend=alt.Legend(title="Banco")),
                    tooltip=["BANCO", alt.Tooltip("VALOR:Q", format=",.2f", title="R$")],
                ).properties(height=420)
                st.altair_chart(donut, use_container_width=True)
            else:
                st.caption("Sem banco informado nas entradas.")

        st.markdown("### Tabela detalhada")
        tbl = ent_f.sort_values("DATA", ascending=False).copy()
        tbl["R$"] = tbl["VALOR"].map(fmt_brl)
        st.dataframe(tbl.drop(columns=["VALOR"], errors="ignore"), use_container_width=True, hide_index=True)

elif page.startswith("📉"):
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Saídas")

    total = float(sai_f["VALOR"].sum()) if not sai_f.empty else 0.0
    c1, c2 = st.columns(2)
    with c1:
        st_kpi("Total de saídas", fmt_brl(total))
    with c2:
        st_kpi("Lançamentos", str(int(len(sai_f))))

    if sai_f.empty:
        st.info("Sem saídas no período/filtro.")
    else:
        hist_months = sorted(set(ym_sels))
        last_m = hist_months[-1]
        prev_m = hist_months[-2] if len(hist_months) >= 2 else last_m

        # top contas com AV/AH
        sai_hist = df_sai[df_sai["YM"].isin(hist_months)].copy()
        if banco_sel and ("BANCO" in sai_hist.columns):
            sai_hist = sai_hist[sai_hist["BANCO"].isin([_upper(x) for x in banco_sel])].copy()

        ent_hist = df_ent[df_ent["YM"].isin(hist_months)].copy()
        if capt_sel and ("CAPTACAO" in ent_hist.columns):
            ent_hist = ent_hist[ent_hist["CAPTACAO"].isin([_upper(x) for x in capt_sel])].copy()
        if banco_sel and ("BANCO" in ent_hist.columns):
            ent_hist = ent_hist[ent_hist["BANCO"].isin([_upper(x) for x in banco_sel])].copy()

        top = (
            sai_hist.groupby(["CONTA", "YM"], as_index=False)["VALOR"].sum()
            .pivot(index="CONTA", columns="YM", values="VALOR")
            .fillna(0.0)
        )
        for m in hist_months:
            if m not in top.columns:
                top[m] = 0.0
        top = top[hist_months]
        top["TOTAL"] = top.sum(axis=1)
        top = top.sort_values("TOTAL", ascending=False).head(12).drop(columns=["TOTAL"]).reset_index()
        totals_last = float(ent_hist[ent_hist["YM"] == last_m]["VALOR"].sum()) if (not ent_hist.empty) else 0.0

        top["AV_%"] = top[last_m].apply(lambda v: (v / totals_last) if totals_last else np.nan)
        top["AH_%"] = top.apply(lambda r: ((r[last_m] / r[prev_m]) - 1.0) if r[prev_m] != 0 else np.nan, axis=1)

        cV, cH = st.columns(2)

        with cV:
            st.markdown("### Vertical — composição (mês mais recente)")
            d = top[["CONTA", last_m, "AV_%"]].copy().rename(columns={last_m: "Valor"})
            bars = alt.Chart(d).mark_bar().encode(
                x=alt.X("AV_%:Q", title="% do total", axis=alt.Axis(format=".0%")),
                y=alt.Y("CONTA:N", sort='-x', title=""),
                tooltip=["CONTA", alt.Tooltip("Valor:Q", format=",.2f"), alt.Tooltip("AV_%:Q", format=".1%")],
            ).properties(height=320)
            txt = alt.Chart(d).mark_text(dx=6, align="left").encode(
                x="AV_%:Q", y=alt.Y("CONTA:N", sort='-x'), text=alt.Text("AV_%:Q", format=".0%")
            )
            st.altair_chart(bars + txt, use_container_width=True)

        with cH:
            st.markdown("### Horizontal — evolução (últimos meses)")
            tot = pd.DataFrame({"YM": hist_months})
            tot["Saídas"] = tot["YM"].map(lambda m: float(sai_hist[sai_hist["YM"] == m]["VALOR"].sum()))
            tot["Mês"] = tot["YM"].map(month_label)
            line = alt.Chart(tot).mark_line(point=True).encode(
                x=alt.X("Mês:N", sort=list(tot["Mês"]), title=""),
                y=alt.Y("Saídas:Q", title="R$"),
                tooltip=["Mês", alt.Tooltip("Saídas:Q", format=",.2f", title="R$")],
            ).properties(height=320)
            st.altair_chart(line, use_container_width=True)

        st.markdown("### Tabela (AH/AV) — top contas (Saídas)")
        out = top[["CONTA"] + hist_months + ["AH_%", "AV_%"]].copy()
        for m in hist_months:
            out[m] = out[m].apply(lambda v: safe_num(v))
        show = out.copy()
        for m in hist_months:
            show[m] = show[m].apply(fmt_brl)
        show["AH_%"] = show["AH_%"].apply(lambda v: "" if pd.isna(v) else f"{v*100:.1f}%")
        show["AV_%"] = show["AV_%"].apply(lambda v: "" if pd.isna(v) else f"{v*100:.1f}%")
        st.dataframe(show, use_container_width=True, hide_index=True)


elif page.startswith("🟨"):
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Investimentos (regra inicial)")

    inv = sai_f.loc[inv_mask].copy() if not sai_f.empty else pd.DataFrame()
    c1, c2 = st.columns(2)
    with c1:
        st_kpi("Total investimentos", fmt_brl(inv["VALOR"].sum() if not inv.empty else 0))
    with c2:
        st_kpi("Lançamentos", str(int(len(inv))))

    inv_out = inv.sort_values("DATA_REF", ascending=False).copy() if not inv.empty else inv
    if not inv_out.empty:
        inv_out["R$"] = inv_out["VALOR"].map(fmt_brl)
    st.dataframe(inv_out.drop(columns=["VALOR"], errors="ignore"), use_container_width=True, hide_index=True)


elif page.startswith("💧"):
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Fluxo de Caixa")

    # =========================
    # 1) TENTA USAR A ABA 7 (CONCILIAÇÃO) COMO VERDADE DO SALDO ACUMULADO
    # Regras para usar:
    # - apenas 1 mês selecionado
    # - a aba 7 conseguiu ser interpretada (conc_tbl_all)
    # - ano/mês da aba 7 bate com o mês selecionado
    # - se houver filtro de banco, precisa ser apenas 1 banco e bater com o banco da conciliação (quando identificável)
    # =========================

    # --- Se houver conciliação (aba 7), usamos como VERDADE do saldo acumulado do mês ---
    # Regra: quando o usuário seleciona APENAS 1 mês, a coluna "SALDO ACUMULADO MÊS"
    # da aba 7 é o valor correto para SALDO_ACUM.
    conc_tbl = None
    try:
        use_conc = (len(ym_sels) == 1) and (conc_tbl_all is not None) and (not conc_tbl_all.empty)
        if use_conc:
            # monta DATA a partir do mês selecionado + DIA
            y_sel = int(ym_sels[0][:4])
            m_sel = int(ym_sels[0][5:7])
            conc_tbl = conc_tbl_all.copy()
            conc_tbl["DATA"] = conc_tbl["DIA"].apply(lambda d: date(y_sel, m_sel, int(d)))

            # recorte por período (date_input)
            if dt_ini and dt_fim and (not conc_tbl.empty):
                conc_tbl = conc_tbl[(conc_tbl["DATA"] >= dt_ini) & (conc_tbl["DATA"] <= dt_fim)].copy()
    except Exception:
        conc_tbl = None

    if conc_tbl is not None and (not conc_tbl.empty):
        st.caption("Fonte do saldo acumulado: **7. Conciliação** (coluna 'Saldo acumulado mês').")

        fluxo_disp = conc_tbl[["DATA", "ENTRADAS", "SAIDAS", "SALDO_DIA", "SALDO_ACUM"]].sort_values("DATA").copy()

        melt = fluxo_disp.melt(
            id_vars=["DATA"],
            value_vars=["ENTRADAS", "SAIDAS", "SALDO_DIA"],
            var_name="Métrica",
            value_name="Valor",
        )
        melt["Métrica"] = melt["Métrica"].replace({"ENTRADAS": "Entradas", "SAIDAS": "Saídas", "SALDO_DIA": "Saldo do dia"})

        chart = alt.Chart(melt).mark_line(point=True).encode(
            x=alt.X("DATA:T", title="Data", axis=alt.Axis(format="%d/%m")),
            y=alt.Y("Valor:Q", title="R$"),
            color=alt.Color("Métrica:N", legend=alt.Legend(title="")),
            tooltip=[alt.Tooltip("DATA:T", title="Data", format="%d/%m/%Y"), "Métrica", alt.Tooltip("Valor:Q", format=",.2f", title="R$")],
        ).properties(height=320)
        st.altair_chart(chart, use_container_width=True)

        st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
        cA, cB, cC, cD, cE = st.columns(5)

        with cA:
            st_kpi("Entradas", fmt_brl(fluxo_disp["ENTRADAS"].sum()), sub="Somatório no período")

        with cB:
            st_kpi("Saídas", fmt_brl(fluxo_disp["SAIDAS"].sum()), sub="Somatório no período")

        with cC:
            saldo = float(fluxo_disp["SALDO_DIA"].sum())
            badge = ("positivo", "good") if saldo >= 0 else ("negativo", "bad")
            st_kpi("Saldo no período", fmt_brl(saldo), sub="Entradas - Saídas", badge=badge)

        with cD:
            saldo_filtro = float(fluxo_disp["SALDO_ACUM"].iloc[-1])
            badge = ("positivo", "good") if saldo_filtro >= 0 else ("negativo", "bad")
            st_kpi("Saldo acumulado (filtro)", fmt_brl(saldo_filtro), sub="Bancos filtrados", badge=badge)

        def get_saldo_bancos(df):
            for i in range(len(df)):
                for j in range(len(df.columns)):
                    txt = str(df.iloc[i, j]).upper()
                    if "SALDO ACUMULADO" in txt and "BANCO" in txt:
                        try:
                            return money_to_float(df.iloc[i, j + 1])
                        except:
                            pass
            return None

        saldo_todos = get_saldo_bancos(df_conc_raw)

        with cE:
            if saldo_todos is not None:
                badge = ("positivo", "good") if saldo_todos >= 0 else ("negativo", "bad")
                st_kpi("Saldo acumulado (todos)", fmt_brl(saldo_todos), sub="Todos os bancos", badge=badge)

        st.markdown("### Tabela do fluxo (por dia)")
        fluxo_tbl_show = fluxo_disp.copy()
        for c in ["ENTRADAS", "SAIDAS", "SALDO_DIA", "SALDO_ACUM"]:
            fluxo_tbl_show[c] = fluxo_tbl_show[c].apply(fmt_brl)
        st.dataframe(fluxo_tbl_show, use_container_width=True, hide_index=True)

        st.stop()

         # 2) FALLBACK (AUTOMÁTICO): CALCULA PELO HISTÓRICO + SALDO INICIAL
    # Isso garante que o fluxo nunca fique "vazio" só porque a aba 7 não foi reconhecida.
    # =========================
    ent_hist = df_ent.copy()
    sai_hist = df_sai.copy()
    trf_hist = df_trf.copy()

    # filtros (captação e banco) aplicados no histórico para refletir o que o usuário selecionou
    if capt_sel and ("CAPTACAO" in ent_hist.columns):
        ent_hist = ent_hist[ent_hist["CAPTACAO"].isin([_upper(x) for x in capt_sel])].copy()

    if banco_sel:
        bset = [_upper(x) for x in banco_sel]
        if (not ent_hist.empty) and ("BANCO" in ent_hist.columns):
            ent_hist = ent_hist[ent_hist["BANCO"].isin(bset)].copy()
        if (not sai_hist.empty) and ("BANCO" in sai_hist.columns):
            sai_hist = sai_hist[sai_hist["BANCO"].isin(bset)].copy()
        if not trf_hist.empty:
            # mantém transferências onde origem OU destino está no conjunto selecionado
            if "ORIGEM" in trf_hist.columns and "DESTINO" in trf_hist.columns:
                trf_hist = trf_hist[(trf_hist["ORIGEM"].isin(bset)) | (trf_hist["DESTINO"].isin(bset))].copy()

    mv_banks_daily, resumo_banks = compute_saldo_bancos(ent_hist, sai_hist, trf_hist, df_saldo_ini, saldo_base_date)
    fluxo_disp = build_fluxo_total_from_mv(mv_banks_daily, banco_sel, dt_ini, dt_fim)

    if fluxo_disp.empty:
        st.info("Sem dados suficientes para exibir o fluxo de caixa neste filtro.")
    else:
        melt = fluxo_disp.melt(
            id_vars=["DATA"],
            value_vars=["ENTRADAS", "SAIDAS", "SALDO_DIA"],
            var_name="Métrica",
            value_name="Valor",
        )
        melt["Métrica"] = melt["Métrica"].replace({"ENTRADAS": "Entradas", "SAIDAS": "Saídas", "SALDO_DIA": "Saldo do dia"})

        chart = alt.Chart(melt).mark_line(point=True).encode(
            x=alt.X("DATA:T", title="Data", axis=alt.Axis(format="%d/%m")),
            y=alt.Y("Valor:Q", title="R$"),
            color=alt.Color("Métrica:N", legend=alt.Legend(title="")),
            tooltip=[
                alt.Tooltip("DATA:T", title="Data", format="%d/%m/%Y"),
                "Métrica",
                alt.Tooltip("Valor:Q", format=",.2f", title="R$"),
            ],
        ).properties(height=320)
        st.altair_chart(chart, use_container_width=True)

        st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
        cA, cB, cC, cD = st.columns(4)
        with cA:
            st_kpi("Entradas", fmt_brl(fluxo_disp["ENTRADAS"].sum()), sub="Somatório no período")
        with cB:
            st_kpi("Saídas", fmt_brl(fluxo_disp["SAIDAS"].sum()), sub="Somatório no período")
        with cC:
            saldo = float(fluxo_disp["SALDO_DIA"].sum())
            badge = ("positivo", "good") if saldo >= 0 else ("negativo", "bad")
            st_kpi("Saldo no período", fmt_brl(saldo), sub="Entradas - Saídas", badge=badge)
        with cD:
            final_real = float(fluxo_disp.sort_values("DATA")["SALDO_REAL"].iloc[-1])
            badge = ("positivo", "good") if final_real >= 0 else ("negativo", "bad")
            st_kpi("Saldo acumulado", fmt_brl(final_real), sub="Saldo real (carryover)", badge=badge)

        st.markdown("### Tabela do fluxo (por dia)")
        fluxo_tbl_show = fluxo_disp.copy().sort_values("DATA")
        fluxo_tbl_show = fluxo_tbl_show.rename(columns={"SALDO_REAL": "SALDO_ACUM"})
        for c in ["ENTRADAS", "SAIDAS", "SALDO_DIA", "SALDO_ACUM"]:
            fluxo_tbl_show[c] = fluxo_tbl_show[c].apply(fmt_brl)
        st.dataframe(fluxo_tbl_show[["DATA", "ENTRADAS", "SAIDAS", "SALDO_DIA", "SALDO_ACUM"]], use_container_width=True, hide_index=True)

        st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
        st.markdown("### Saldo por banco (final do período)")

        resumo_banks_show = resumo_banks.copy() if (resumo_banks is not None and not resumo_banks.empty) else pd.DataFrame()
        if not resumo_banks_show.empty and banco_sel:
            resumo_banks_show = resumo_banks_show[resumo_banks_show["BANCO"].isin([_upper(x) for x in banco_sel])].copy()

        if resumo_banks_show.empty:
            st.caption("Sem saldos por banco disponíveis.")
        else:
            show = resumo_banks_show.copy()
            for c in ["SALDO_INICIAL", "SALDO_MOV", "SALDO_REAL_FINAL"]:
                show[c] = show[c].apply(fmt_brl)
            st.dataframe(show, use_container_width=True, hide_index=True)


elif page.startswith("⏳"):
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Receber / Pagar (títulos em aberto e vencidos)")

    today = date.today()

    # -------- Contas a Receber (a partir da aba Entradas RAW, para capturar VENCIMENTO mesmo sem recebimento) --------
    rec = df_ent_raw.copy()
    rec.columns = [_norm_col(c) for c in rec.columns]

    c_data_rec = pick_col(list(rec.columns), "DATA RECEBIMENTO", "DATA", "RECEBIMENTO")
    c_venc_rec = pick_col(list(rec.columns), "DATA VENCIMENTO", "VENCIMENTO")
    c_val_rec = pick_col(list(rec.columns), "VALOR", "R$ ENTRADA", "R$ENTRADA", "R$")
    c_cliente = pick_col(list(rec.columns), "CLIENTE", "CLIENTES")
    c_capt = pick_col(list(rec.columns), "CAPTACAO", "CAPTAÇÃO")

    rec["RECEBIMENTO"] = rec[c_data_rec].apply(parse_date_any) if c_data_rec else pd.NaT
    rec["VENCIMENTO"] = rec[c_venc_rec].apply(parse_date_any) if c_venc_rec else pd.NaT
    rec["VALOR"] = rec[c_val_rec].apply(money_to_float) if c_val_rec else 0.0
    rec["CLIENTE"] = rec[c_cliente].astype(str).map(_upper) if c_cliente else ""
    rec["CAPTACAO"] = rec[c_capt].astype(str).map(_upper) if c_capt else rec["CLIENTE"]

    # filtra meses selecionados pelo VENCIMENTO (se existir), senão pelo recebimento
    rec["DATA_BASE"] = rec["VENCIMENTO"].where(rec["VENCIMENTO"].notna(), rec["RECEBIMENTO"])
    rec["YM"] = rec["DATA_BASE"].apply(to_ym)
    rec = rec[rec["YM"].isin(ym_sels)].copy()

    rec_aberto = rec[rec["RECEBIMENTO"].isna() & rec["VENCIMENTO"].notna()].copy()
    rec_vencido = rec_aberto[rec_aberto["VENCIMENTO"] < today].copy()

    # próximos X dias
    dias = st.slider("Próximos dias", min_value=1, max_value=60, value=15, step=1)
    limite = today + timedelta(days=dias)
    rec_prox = rec_aberto[(rec_aberto["VENCIMENTO"] >= today) & (rec_aberto["VENCIMENTO"] <= limite)].copy()

    # -------- Contas a Pagar (da saída normalizada, já contém VENCIMENTO/PAGAMENTO) --------
    pay = df_sai.copy()
    pay = pay[pay["YM"].isin(ym_sels)].copy()
    if banco_sel and ("BANCO" in pay.columns):
        pay = pay[pay["BANCO"].isin([_upper(x) for x in banco_sel])].copy()

    pay_aberto = pay[pay["PAGAMENTO"].isna() & pay["VENCIMENTO"].notna()].copy()
    pay_vencido = pay_aberto[pay_aberto["VENCIMENTO"] < today].copy()
    pay_prox = pay_aberto[(pay_aberto["VENCIMENTO"] >= today) & (pay_aberto["VENCIMENTO"] <= limite)].copy()

    # -------- KPIs --------
    a1, a2, a3, a4 = st.columns(4)
    with a1:
        st_kpi("A receber vencido", fmt_brl(rec_vencido["VALOR"].sum() if not rec_vencido.empty else 0.0), sub="Contas vencidas e em aberto")
    with a2:
        st_kpi(f"A receber em {dias} dias", fmt_brl(rec_prox["VALOR"].sum() if not rec_prox.empty else 0.0), sub="Vencem nos próximos dias")
    with a3:
        st_kpi("A pagar vencido", fmt_brl(pay_vencido["VALOR"].sum() if not pay_vencido.empty else 0.0), sub="Contas vencidas e em aberto")
    with a4:
        st_kpi(f"A pagar em {dias} dias", fmt_brl(pay_prox["VALOR"].sum() if not pay_prox.empty else 0.0), sub="Vencem nos próximos dias")

    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)

    # -------- Listas --------
    cL, cR = st.columns(2)

    with cL:
        st.markdown("### Quem está atrasado para me pagar")
        if rec_vencido.empty:
            st.caption("Sem contas a receber vencidas no período.")
        else:
            show = rec_vencido.sort_values("VENCIMENTO").copy()
            show["R$"] = show["VALOR"].map(fmt_brl)
            st.dataframe(show[["VENCIMENTO", "CAPTACAO", "CLIENTE", "R$"]], use_container_width=True, hide_index=True)

        st.markdown("### Quem vai me pagar nos próximos dias")
        if rec_prox.empty:
            st.caption("Sem contas a receber nos próximos dias.")
        else:
            show = rec_prox.sort_values("VENCIMENTO").copy()
            show["R$"] = show["VALOR"].map(fmt_brl)
            st.dataframe(show[["VENCIMENTO", "CAPTACAO", "CLIENTE", "R$"]], use_container_width=True, hide_index=True)

    with cR:
        st.markdown("### Quem está atrasado para eu pagar")
        if pay_vencido.empty:
            st.caption("Sem contas a pagar vencidas no período.")
        else:
            show = pay_vencido.sort_values("VENCIMENTO").copy()
            show["R$"] = show["VALOR"].map(fmt_brl)
            cols = [c for c in ["VENCIMENTO", "FORNECEDOR", "CONTA", "BANCO", "R$"] if c in show.columns]
            st.dataframe(show[cols], use_container_width=True, hide_index=True)

        st.markdown("### Quem devo pagar nos próximos dias")
        if pay_prox.empty:
            st.caption("Sem contas a pagar nos próximos dias.")
        else:
            show = pay_prox.sort_values("VENCIMENTO").copy()
            show["R$"] = show["VALOR"].map(fmt_brl)
            cols = [c for c in ["VENCIMENTO", "FORNECEDOR", "CONTA", "BANCO", "R$"] if c in show.columns]
            st.dataframe(show[cols], use_container_width=True, hide_index=True)


elif page.startswith("🧾"):
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Conciliação (por banco + transferências)")

    if sai_f.empty:
        st.info("Sem saídas no período.")
    else:
        by_bank_out = (
            sai_f.groupby("BANCO")["VALOR"].sum().reset_index().rename(columns={"VALOR": "Saídas"})
            if "BANCO" in sai_f.columns
            else pd.DataFrame(columns=["BANCO", "Saídas"])
        )

        if not trf_f.empty:
            trf_out = trf_f.groupby("ORIGEM")["VALOR"].sum().reset_index().rename(columns={"ORIGEM": "BANCO", "VALOR": "Transfer. Saída"})
            trf_in = trf_f.groupby("DESTINO")["VALOR"].sum().reset_index().rename(columns={"DESTINO": "BANCO", "VALOR": "Transfer. Entrada"})
        else:
            trf_out = pd.DataFrame(columns=["BANCO", "Transfer. Saída"])
            trf_in = pd.DataFrame(columns=["BANCO", "Transfer. Entrada"])

        conc = by_bank_out.merge(trf_out, on="BANCO", how="outer").merge(trf_in, on="BANCO", how="outer").fillna(0.0)
        conc["Mov. Líq. Transferências"] = conc["Transfer. Entrada"] - conc["Transfer. Saída"]
        conc = conc.sort_values("Saídas", ascending=False)

        conc_show = conc.copy()
        for c in ["Saídas", "Transfer. Saída", "Transfer. Entrada", "Mov. Líq. Transferências"]:
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
    st.download_button(
        "Baixar Entradas (CSV)",
        data=ent_out.drop(columns=["VALOR"], errors="ignore").to_csv(index=False).encode("utf-8-sig"),
        file_name=f"entradas_{ym_focus}.csv",
        mime="text/csv",
    )

    sai_out = sai_f.copy()
    if not sai_out.empty:
        sai_out["R$"] = sai_out["VALOR"].map(fmt_brl)
    st.download_button(
        "Baixar Saídas (CSV)",
        data=sai_out.drop(columns=["VALOR"], errors="ignore").to_csv(index=False).encode("utf-8-sig"),
        file_name=f"saidas_{ym_focus}.csv",
        mime="text/csv",
    )

    trf_out = trf_f.copy()
    if not trf_out.empty:
        trf_out["R$"] = trf_out["VALOR"].map(fmt_brl)
    st.download_button(
        "Baixar Transferências (CSV)",
        data=trf_out.drop(columns=["VALOR"], errors="ignore").to_csv(index=False).encode("utf-8-sig"),
        file_name=f"transferencias_{ym_focus}.csv",
        mime="text/csv",
    )
