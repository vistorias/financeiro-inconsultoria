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
TAB_CONC = "7. Conciliação"


TAB_SALDO_INI = "1. Saldo inicial"
@st.cache_data(ttl=300, show_spinner=False)
def read_tab(sheet_id: str, tab: str) -> pd.DataFrame:
    """Leitura robusta (evita erros do get_all_records quando há cabeçalhos duplicados/vazios).
    Se a aba não existir, retorna DataFrame vazio.
    """
    sh = client.open_by_key(sheet_id)
    try:
        ws = sh.worksheet(tab)
    except Exception:
        return pd.DataFrame()
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
    col_val = pick_col(cols_norm, "VALOR", "R$ ENTRADA", "R$ENTRADA", "R$")

    c_cliente = pick_col(cols_norm, "CLIENTE", "CLIENTES")
    c_plano = pick_col(cols_norm, "PLANO DE CONTAS", "PLANO DE CONTA", "CONTA")
    c_desc = pick_col(cols_norm, "DESCRICAO", "DESCRIÇÃO", "HISTORICO", "HISTÓRICO", "OBS", "OBSERVACAO", "OBSERVAÇÃO")
    c_meio = pick_col(cols_norm, "MEIO")
    c_area = pick_col(cols_norm, "AREA")
    c_prod = pick_col(cols_norm, "PRODUTO")
    c_capt = pick_col(cols_norm, "CAPTACAO", "CAPTAÇÃO")

    c_banco = pick_col(cols_norm, "BANCO", "CONTA BANCARIA", "CONTA BANCÁRIA")
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

    df["BANCO"] = df[c_banco].astype(str).map(_upper) if c_banco else ""

    if (df["CAPTACAO"] == "").all():
        df["CAPTACAO"] = df["CLIENTE"]

    df["YM"] = df["DATA"].apply(to_ym)

    df = df[df["DATA"].notna()].copy()
    df = df[df["VALOR"] != 0].copy()

    keep = [
        "DATA",
        "YM",
        "VENCIMENTO",
        "BANCO",
        "CAPTACAO",
        "CLIENTE",
        "PLANO_CONTAS",
        "MEIO",
        "AREA",
        "PRODUTO",
        "DESCRICAO",
        "VALOR",
    ]
    keep = [c for c in keep if c in df.columns]
    return df[keep].copy()


def normalize_saidas(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    cols_norm = [_norm_col(c) for c in df.columns]
    df.columns = cols_norm

    c_venc = pick_col(cols_norm, "DATA VENCIMENTO", "VENCIMENTO")
    c_pag = pick_col(cols_norm, "DATA PAGAMENTO", "PAGAMENTO")
    c_val = pick_col(cols_norm, "VALOR", "R$ VALOR", "R$VALOR", "R$")

    c_banco = pick_col(cols_norm, "BANCO")
    c_plano = pick_col(cols_norm, "PLANO DE CONTAS", "PLANO DE CONTA", "CONTA")
    c_tipo = pick_col(cols_norm, "TIPO")
    c_cc = pick_col(cols_norm, "CENTRO DE CUSTO", "INDIRETO")
    c_forn = pick_col(cols_norm, "FORNECEDOR")
    c_desc = pick_col(cols_norm, "DESCRICAO", "DESCRIÇÃO", "HISTORICO", "HISTÓRICO", "OBS", "OBSERVACAO", "OBSERVAÇÃO")

    df["VENCIMENTO"] = df[c_venc].apply(parse_date_any) if c_venc else pd.NaT
    df["PAGAMENTO"] = df[c_pag].apply(parse_date_any) if c_pag else pd.NaT

    # DATA_REF: pagamento se existir; senão vencimento
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

    keep = [
        "DATA_REF",
        "YM",
        "VENCIMENTO",
        "PAGAMENTO",
        "BANCO",
        "CONTA",
        "TIPO",
        "CENTRO_CUSTO",
        "FORNECEDOR",
        "DESCRICAO",
        "VALOR",
    ]
    keep = [c for c in keep if c in df.columns]
    return df[keep].copy()


def normalize_transferencias(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    cols_norm = [_norm_col(c) for c in df.columns]
    df.columns = cols_norm

    c_data = pick_col(cols_norm, "DATA")
    c_or = pick_col(cols_norm, "BANCO SAIDA", "BANCO SAÍDA", "ORIGEM")
    c_de = pick_col(cols_norm, "BANCO ENTRADA", "DESTINO")
    c_val = pick_col(cols_norm, "VALOR", "R$ VALOR", "R$VALOR", "R$")
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


def parse_saldo_inicial_sheet(df: pd.DataFrame) -> Tuple[Optional[date], pd.DataFrame]:
    if df is None or df.empty:
        return None, pd.DataFrame(columns=["BANCO", "SALDO"])

    base_date = None
    date_re = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$")

    for c in list(df.columns):
        s = str(c).strip()
        if date_re.match(s):
            try:
                base_date = datetime.strptime(s, "%d/%m/%Y").date()
                break
            except Exception:
                pass

    cols = list(df.columns)
    if len(cols) < 2:
        return base_date, pd.DataFrame(columns=["BANCO", "SALDO"])

    c_banco = cols[0]
    c_saldo = cols[1]

    out = pd.DataFrame()
    out["BANCO"] = df[c_banco].astype(str).map(_upper)
    out["SALDO"] = df[c_saldo].apply(money_to_float)
    out = out[(out["BANCO"] != "") & (out["SALDO"] != 0)].copy()
    out = out.groupby("BANCO", as_index=False)["SALDO"].sum().sort_values("BANCO")

    return base_date, out


def normalize_conciliacao(df: pd.DataFrame) -> Tuple[Optional[int], Optional[int], Optional[str], pd.DataFrame]:
    empty = pd.DataFrame(columns=["DIA", "ENTRADAS", "SAIDAS", "SALDO_DIA", "SALDO_ACUM"])
    if df is None or df.empty:
        return None, None, None, empty

    df = df.copy()
    orig_cols = [str(c).strip() for c in df.columns]
    cols_norm = [_norm_col(c) for c in orig_cols]
    df.columns = cols_norm

    year = None
    month = None
    bank = None

    for c in orig_cols:
        s = str(c).strip()
        if re.fullmatch(r"20\d{2}", s):
            try:
                year = int(s)
                break
            except Exception:
                pass

    month_map = {
        "JAN": 1, "JANEIRO": 1,
        "FEV": 2, "FEVEREIRO": 2,
        "MAR": 3, "MARCO": 3, "MARÇO": 3,
        "ABR": 4, "ABRIL": 4,
        "MAI": 5, "MAIO": 5,
        "JUN": 6, "JUNHO": 6,
        "JUL": 7, "JULHO": 7,
        "AGO": 8, "AGOSTO": 8,
        "SET": 9, "SETEMBRO": 9,
        "OUT": 10, "OUTUBRO": 10,
        "NOV": 11, "NOVEMBRO": 11,
        "DEZ": 12, "DEZEMBRO": 12,
    }

    for c in orig_cols:
        s = _strip_accents(str(c)).upper().strip()
        if s in month_map:
            month = month_map[s]
            break

    try:
        i = cols_norm.index("BANCO")
        if i + 1 < len(orig_cols):
            b = str(orig_cols[i + 1]).strip()
            if b:
                bank = _upper(b)
    except Exception:
        pass

    c_dia = pick_col(list(df.columns), "DIA DO MES", "DIA DO MÊS", "DIA")
    c_ent = pick_col(list(df.columns), "ENTRADAS", "ENTRADA")
    c_sai = pick_col(list(df.columns), "SAIDAS", "SAÍDAS", "SAIDA")
    c_sd = pick_col(list(df.columns), "SALDO DO DIA", "SALDO_DIA")
    c_sa = pick_col(list(df.columns), "SALDO ACUMULADO MES", "SALDO ACUMULADO MÊS", "SALDO ACUMULADO")

    if not all([c_dia, c_ent, c_sai, c_sa]):
        return year, month, bank, empty

    def _parse_day(v):
        try:
            return int(float(str(v).replace(",", ".")))
        except Exception:
            return np.nan

    out = pd.DataFrame()
    out["DIA"] = df[c_dia].apply(_parse_day)
    out["ENTRADAS"] = df[c_ent].apply(money_to_float)
    out["SAIDAS"] = df[c_sai].apply(money_to_float)
    out["SALDO_DIA"] = df[c_sd].apply(money_to_float) if c_sd else (out["ENTRADAS"] - out["SAIDAS"])
    out["SALDO_ACUM"] = df[c_sa].apply(money_to_float)

    out = out.dropna(subset=["DIA"]).copy()
    if out.empty:
        return year, month, bank, empty

    out["DIA"] = out["DIA"].astype(int)
    out = out.sort_values("DIA")
    return year, month, bank, out


def compute_saldo_bancos(
    df_ent_all: pd.DataFrame,
    df_sai_all: pd.DataFrame,
    df_trf_all: pd.DataFrame,
    df_saldo_ini: pd.DataFrame,
    base_date: Optional[date],
) -> Tuple[pd.DataFrame, pd.DataFrame]:

    saldo_ini_map = {}
    if df_saldo_ini is not None and not df_saldo_ini.empty and {"BANCO", "SALDO"}.issubset(df_saldo_ini.columns):
        saldo_ini_map = {str(k).upper().strip(): float(v) for k, v in df_saldo_ini[["BANCO", "SALDO"]].values}

    def _cut(df: pd.DataFrame, col: str) -> pd.DataFrame:
        if df is None or df.empty or col not in df.columns:
            return pd.DataFrame(columns=df.columns if df is not None else [])
        out = df.copy()
        if base_date is not None:
            out = out[out[col] >= base_date].copy()
        return out

    df_ent_all = _cut(df_ent_all, "DATA")
    df_sai_all = _cut(df_sai_all, "DATA_REF")
    df_trf_all = _cut(df_trf_all, "DATA")

    ent = pd.DataFrame(columns=["DATA", "BANCO", "ENTRADAS"])
    if not df_ent_all.empty and {"DATA", "BANCO", "VALOR"}.issubset(df_ent_all.columns):
        ent = (
            df_ent_all.groupby(["DATA", "BANCO"], as_index=False)["VALOR"]
            .sum()
            .rename(columns={"VALOR": "ENTRADAS"})
        )

    sai = pd.DataFrame(columns=["DATA", "BANCO", "SAIDAS"])
    if not df_sai_all.empty and {"DATA_REF", "BANCO", "VALOR"}.issubset(df_sai_all.columns):
        sai = (
            df_sai_all.groupby(["DATA_REF", "BANCO"], as_index=False)["VALOR"]
            .sum()
            .rename(columns={"DATA_REF": "DATA", "VALOR": "SAIDAS"})
        )

    trf_out = pd.DataFrame(columns=["DATA", "BANCO", "TRF_OUT"])
    trf_in = pd.DataFrame(columns=["DATA", "BANCO", "TRF_IN"])
    if not df_trf_all.empty and {"DATA", "ORIGEM", "DESTINO", "VALOR"}.issubset(df_trf_all.columns):
        trf_out = (
            df_trf_all.groupby(["DATA", "ORIGEM"], as_index=False)["VALOR"]
            .sum()
            .rename(columns={"ORIGEM": "BANCO", "VALOR": "TRF_OUT"})
        )
        trf_in = (
            df_trf_all.groupby(["DATA", "DESTINO"], as_index=False)["VALOR"]
            .sum()
            .rename(columns={"DESTINO": "BANCO", "VALOR": "TRF_IN"})
        )

    pieces = []
    for bank in sorted(set(list(ent.get("BANCO", [])) + list(sai.get("BANCO", [])) + list(trf_out.get("BANCO", [])) + list(trf_in.get("BANCO", [])) + list(saldo_ini_map.keys()))):
        dd = pd.DataFrame()
        dates = set()

        if not ent.empty:
            dates |= set(ent.loc[ent["BANCO"] == bank, "DATA"].tolist())
        if not sai.empty:
            dates |= set(sai.loc[sai["BANCO"] == bank, "DATA"].tolist())
        if not trf_out.empty:
            dates |= set(trf_out.loc[trf_out["BANCO"] == bank, "DATA"].tolist())
        if not trf_in.empty:
            dates |= set(trf_in.loc[trf_in["BANCO"] == bank, "DATA"].tolist())

        if not dates and bank not in saldo_ini_map:
            continue

        if not dates and bank in saldo_ini_map and base_date is not None:
            dates = {base_date}

        dd["DATA"] = sorted(dates)
        dd["BANCO"] = bank

        if not ent.empty:
            ee = ent[ent["BANCO"] == bank][["DATA", "ENTRADAS"]]
            dd = dd.merge(ee, on="DATA", how="left")
        else:
            dd["ENTRADAS"] = 0.0

        if not sai.empty:
            ss = sai[sai["BANCO"] == bank][["DATA", "SAIDAS"]]
            dd = dd.merge(ss, on="DATA", how="left")
        else:
            dd["SAIDAS"] = 0.0

        if not trf_out.empty:
            oo = trf_out[trf_out["BANCO"] == bank][["DATA", "TRF_OUT"]]
            dd = dd.merge(oo, on="DATA", how="left")
        else:
            dd["TRF_OUT"] = 0.0

        if not trf_in.empty:
            ii = trf_in[trf_in["BANCO"] == bank][["DATA", "TRF_IN"]]
            dd = dd.merge(ii, on="DATA", how="left")
        else:
            dd["TRF_IN"] = 0.0

        for c in ["ENTRADAS", "SAIDAS", "TRF_OUT", "TRF_IN"]:
            if c not in dd.columns:
                dd[c] = 0.0
            dd[c] = dd[c].fillna(0.0)

        dd = dd.sort_values("DATA")
        dd["SALDO_DIA"] = dd["ENTRADAS"] - dd["SAIDAS"] + dd["TRF_IN"] - dd["TRF_OUT"]
        dd["SALDO_INICIAL"] = float(saldo_ini_map.get(bank, 0.0))
        dd["SALDO_REAL"] = dd["SALDO_INICIAL"].iloc[0] + dd["SALDO_DIA"].cumsum()
        pieces.append(dd)

    mv_daily = pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame()
    resumo = (
        mv_daily.groupby("BANCO", as_index=False)
        .agg(
            SALDO_INICIAL=("SALDO_INICIAL", "max"),
            SALDO_MOV=("SALDO_DIA", "sum"),
            SALDO_REAL_FINAL=("SALDO_REAL", "last"),
        )
        .sort_values("SALDO_REAL_FINAL", ascending=False)
    )
    return mv_daily, resumo


def build_fluxo_total_from_mv(
    mv_banks_daily: pd.DataFrame,
    bancos: List[str],
    dt_ini: Optional[date],
    dt_fim: Optional[date],
) -> pd.DataFrame:

    if mv_banks_daily is None or mv_banks_daily.empty:
        return pd.DataFrame(columns=["DATA", "ENTRADAS", "SAIDAS", "SALDO_DIA", "SALDO_REAL"])

    mv = mv_banks_daily.copy()
    mv["BANCO"] = mv["BANCO"].astype(str).map(_upper)

    if bancos:
        bset = set([_upper(b) for b in bancos])
        mv = mv[mv["BANCO"].isin(bset)].copy()
        if mv.empty:
            return pd.DataFrame(columns=["DATA", "ENTRADAS", "SAIDAS", "SALDO_DIA", "SALDO_REAL"])

    if dt_ini and dt_fim:
        mv = mv[(mv["DATA"] >= dt_ini) & (mv["DATA"] <= dt_fim)].copy()

    total = (
        mv.groupby("DATA", as_index=False)[["ENTRADAS", "SAIDAS", "SALDO_DIA", "SALDO_REAL"]]
        .sum()
        .sort_values("DATA")
    )
    return total


def last_point_label(df: pd.DataFrame, xcol: str, ycol: str, label: str = None):
    if df.empty:
        return pd.DataFrame(columns=[xcol, ycol, "LABEL"])
    d = df.sort_values(xcol).tail(1).copy()
    d["LABEL"] = d[ycol].apply(lambda v: fmt_brl(v) if isinstance(v, (int, float, np.number)) else str(v))
    if label:
        d["LABEL"] = label
    return d[[xcol, ycol, "LABEL"]]


# ====================== LOAD DATA ======================
with st.spinner("Carregando dados do Google Sheets..."):
    df_ent_raw = read_tab(SHEET_ID, TAB_ENT)
    df_sai_raw = read_tab(SHEET_ID, TAB_SAI)
    df_trf_raw = read_tab(SHEET_ID, TAB_TRF)
    df_conc_raw = read_tab(SHEET_ID, TAB_CONC)
    df_saldo_raw = read_tab(SHEET_ID, TAB_SALDO_INI)

df_ent = normalize_entradas(df_ent_raw)
df_sai = normalize_saidas(df_sai_raw)
df_trf = normalize_transferencias(df_trf_raw)
conc_year, conc_month, conc_bank, conc_tbl_all = normalize_conciliacao(df_conc_raw)
saldo_base_date, df_saldo_ini = parse_saldo_inicial_sheet(df_saldo_raw)

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
    labels = list(month_label_map.keys())
    default_label = month_label(months[-1])
    sel_labels = st.multiselect("Mês(es)", options=labels, default=[default_label])
    ym_sels = sorted([month_label_map[l] for l in sel_labels]) if sel_labels else [months[-1]]
    ym_focus = ym_sels[-1]
    sel_period_label = default_label if len(ym_sels)==1 else f"{month_label(ym_sels[0])} – {month_label(ym_sels[-1])}"

dates_in_month: List[date] = []

def _as_date(v):
    if v is None or v == "":
        return None
    try:
        if pd.isna(v):
            return None
    except Exception:
        pass
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime().date()
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return parse_date_any(v)

if not df_ent.empty:
    dates_in_month += [_as_date(d) for d in df_ent[df_ent["YM"].isin(ym_sels)]["DATA"].tolist()]
if not df_sai.empty:
    dates_in_month += [_as_date(d) for d in df_sai[df_sai["YM"].isin(ym_sels)]["DATA_REF"].tolist()]
dates_in_month = [d for d in dates_in_month if isinstance(d, date)]

if dates_in_month:
    dmin_default, dmax_default = min(dates_in_month), max(dates_in_month)
else:
    y, m = map(int, ym_focus.split("-"))
    dmin_default = date(y, m, 1)
    dmax_default = date(y, m, 28)

with c2:
    dt_ini, dt_fim = st.date_input("Período", value=(dmin_default, dmax_default), format="DD/MM/YYYY")
    if isinstance(dt_ini, (tuple, list)):
        dt_ini, dt_fim = dt_ini[0], dt_ini[1]

with c3:
    capt_opts = (
        sorted(df_ent[df_ent["YM"].isin(ym_sels)]["CAPTACAO"].dropna().unique().tolist())
        if (not df_ent.empty and "CAPTACAO" in df_ent.columns)
        else []
    )
    capt_sel = st.multiselect("Captação", options=capt_opts, default=[])

with c4:
    banco_opts = (
        sorted(df_sai[df_sai["YM"].isin(ym_sels)]["BANCO"].dropna().unique().tolist())
        if (not df_sai.empty and "BANCO" in df_sai.columns)
        else []
    )
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
        if (not sai.empty) and ("BANCO" in sai.columns):
            sai = sai[sai["BANCO"].isin([_upper(x) for x in banco_sel])].copy()
        if (not ent.empty) and ("BANCO" in ent.columns):
            ent = ent[ent["BANCO"].isin([_upper(x) for x in banco_sel])].copy()

    return ent, sai, trf

ent_f, sai_f, trf_f = apply_filters()


# ====================== NAV ======================
page = st.radio(
    "Página",
    [
        "🏠 Visão Geral",
        "🧰 Entradas / Saídas",
        "📈 Análise Vertical / Horizontal",
        "🟨 Investimentos",
        "💧 Fluxo de Caixa",
        "⏳ Receber / Pagar",
        "🧾 Conciliação",
        "📤 Exportar",
    ],
    horizontal=True,
)

# ====================== VISÃO GERAL ======================
if page.startswith("🏠"):
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)

    total_ent = float(ent_f["VALOR"].sum()) if not ent_f.empty else 0.0
    total_sai = float(sai_f["VALOR"].sum()) if not sai_f.empty else 0.0
    total_inv = 0.0
    if not sai_f.empty and "CONTA" in sai_f.columns:
        total_inv = float(sai_f[sai_f["CONTA"].astype(str).str.contains("INVEST", case=False, na=False)]["VALOR"].sum())
    saldo = total_ent - total_sai

    a, b, c, d = st.columns(4)
    with a:
        st_kpi("Entradas", fmt_brl(total_ent), f"{len(ent_f)} lançamentos")
    with b:
        st_kpi("Saídas", fmt_brl(total_sai), f"{len(sai_f)} lançamentos")
    with c:
        st_kpi("Investimentos", fmt_brl(total_inv), "Contas marcadas como investimento")
    with d:
        st_kpi("Resultado", fmt_brl(saldo), f"Período {sel_period_label}",
               badge=("positivo", "good") if saldo >= 0 else ("negativo", "bad"))

    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    left, right = st.columns([1.25, 1])

    with left:
        st.markdown("### Fluxo diário")
        fluxo = build_daily_fluxo(ent_f, sai_f)
        if dt_ini and dt_fim and not fluxo.empty:
            fluxo = fluxo[(fluxo["DATA"] >= dt_ini) & (fluxo["DATA"] <= dt_fim)].copy()

        if fluxo.empty:
            st.info("Sem dados para o período selecionado.")
        else:
            bars = alt.Chart(fluxo).mark_bar(opacity=0.35).encode(
                x=alt.X("DATA:T", title=""),
                y=alt.Y("SALDO_DIA:Q", title="R$"),
                tooltip=[
                    alt.Tooltip("DATA:T", title="Data", format="%d/%m/%Y"),
                    alt.Tooltip("ENTRADAS:Q", title="Entradas", format=",.2f"),
                    alt.Tooltip("SAIDAS:Q", title="Saídas", format=",.2f"),
                    alt.Tooltip("SALDO_DIA:Q", title="Saldo do dia", format=",.2f"),
                    alt.Tooltip("SALDO_ACUM:Q", title="Saldo acumulado", format=",.2f"),
                ],
            )
            line = alt.Chart(fluxo).mark_line(point=True).encode(
                x="DATA:T",
                y=alt.Y("SALDO_ACUM:Q", title="R$"),
                tooltip=[
                    alt.Tooltip("DATA:T", title="Data", format="%d/%m/%Y"),
                    alt.Tooltip("SALDO_ACUM:Q", title="Saldo acumulado", format=",.2f"),
                ],
            )
            label = last_point_label(fluxo, "DATA", "SALDO_ACUM")
            text = alt.Chart(label).mark_text(dx=8, dy=-8, align="left").encode(
                x="DATA:T", y="SALDO_ACUM:Q", text="LABEL:N"
            )
            st.altair_chart((bars + line + text).properties(height=360), use_container_width=True)

    with right:
        st.markdown("### Saídas por conta")
        if sai_f.empty or "CONTA" not in sai_f.columns:
            st.info("Sem saídas para compor o gráfico.")
        else:
            top = (
                sai_f.groupby("CONTA", as_index=False)["VALOR"]
                .sum()
                .sort_values("VALOR", ascending=False)
                .head(12)
            )
            chart = alt.Chart(top).mark_bar().encode(
                x=alt.X("VALOR:Q", title="R$"),
                y=alt.Y("CONTA:N", sort="-x", title=""),
                tooltip=["CONTA", alt.Tooltip("VALOR:Q", format=",.2f", title="R$")],
            ).properties(height=360)
            st.altair_chart(chart, use_container_width=True)

    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("### Tabela resumida")
    base_tbl = pd.DataFrame({
        "Indicador": ["Entradas", "Saídas", "Investimentos", "Resultado"],
        "Valor": [fmt_brl(total_ent), fmt_brl(total_sai), fmt_brl(total_inv), fmt_brl(saldo)],
    })
    st.dataframe(base_tbl, use_container_width=True, hide_index=True)


# ====================== ENTRADAS / SAÍDAS ======================
elif page.startswith("🧰"):
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)

    c1, c2 = st.columns(2)

    with c1:
        st.markdown("## Entradas")
        if ent_f.empty:
            st.info("Sem entradas no período.")
        else:
            tbl = ent_f.sort_values("DATA", ascending=False).copy()
            tbl["R$"] = tbl["VALOR"].map(fmt_brl)
            show_cols = [c for c in ["DATA", "BANCO", "CAPTACAO", "CLIENTE", "PLANO_CONTAS", "DESCRICAO", "R$"] if c in tbl.columns]
            st.dataframe(tbl[show_cols], use_container_width=True, hide_index=True)

    with c2:
        st.markdown("## Saídas")
        if sai_f.empty:
            st.info("Sem saídas no período.")
        else:
            tbl = sai_f.sort_values("DATA_REF", ascending=False).copy()
            tbl["R$"] = tbl["VALOR"].map(fmt_brl)
            show_cols = [c for c in ["DATA_REF", "BANCO", "CONTA", "FORNECEDOR", "DESCRICAO", "R$"] if c in tbl.columns]
            st.dataframe(tbl[show_cols], use_container_width=True, hide_index=True)


# ====================== AV / AH ======================
elif page.startswith("📈"):
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Análise Vertical / Horizontal")

    hist_months = sorted(list(set(ym_sels)))
    if len(hist_months) == 0:
        st.info("Selecione pelo menos um mês.")
    else:
        last_m = hist_months[-1]
        prev_m = hist_months[-2]
        t = (
            ent_hist[ent_hist["YM"].isin(hist_months)]
            .groupby(["PLANO_CONTAS", "YM"])["VALOR"]
            .sum()
            .reset_index()
        )
        piv = t.pivot(index="PLANO_CONTAS", columns="YM", values="VALOR").fillna(0.0).reset_index()

        if last_m not in piv.columns:
            piv[last_m] = 0.0
        if prev_m not in piv.columns:
            piv[prev_m] = 0.0

        piv["__LAST"] = piv[last_m]
        top = piv.sort_values("__LAST", ascending=False).head(10).drop(columns="__LAST")

        totals_last = float(ent_hist[ent_hist["YM"] == last_m]["VALOR"].sum()) if (not ent_hist.empty) else 0.0

        top["AV_%"] = top[last_m].apply(lambda v: (v / totals_last) if totals_last else np.nan)
        top["AH_%"] = top.apply(lambda r: ((r[last_m] / r[prev_m]) - 1.0) if r[prev_m] != 0 else np.nan, axis=1)

        cV, cH = st.columns(2)

        with cV:
            st.markdown("### Vertical — composição (mês mais recente)")
            d = top[["PLANO_CONTAS", last_m, "AV_%"]].copy().rename(columns={last_m: "Valor"})
            bars = alt.Chart(d).mark_bar().encode(
                x=alt.X("AV_%:Q", title="% do total", axis=alt.Axis(format=".0%")),
                y=alt.Y("PLANO_CONTAS:N", sort='-x', title=""),
                tooltip=["PLANO_CONTAS", alt.Tooltip("Valor:Q", format=",.2f"), alt.Tooltip("AV_%:Q", format=".1%")],
            ).properties(height=320)
            txt = alt.Chart(d).mark_text(dx=6, align="left").encode(
                x="AV_%:Q", y=alt.Y("PLANO_CONTAS:N", sort='-x'), text=alt.Text("AV_%:Q", format=".0%")
            )
            st.altair_chart(bars + txt, use_container_width=True)

        with cH:
            st.markdown("### Horizontal — evolução (últimos meses)")
            tot = pd.DataFrame({"YM": hist_months})
            tot["Entradas"] = tot["YM"].map(lambda m: float(ent_hist[ent_hist["YM"] == m]["VALOR"].sum()))
            tot["Mês"] = tot["YM"].map(month_label)
            line = alt.Chart(tot).mark_line(point=True).encode(
                x=alt.X("Mês:N", sort=list(tot["Mês"]), title=""),
                y=alt.Y("Entradas:Q", title="R$"),
                tooltip=["Mês", alt.Tooltip("Entradas:Q", format=",.2f", title="R$")],
            ).properties(height=320)
            st.altair_chart(line, use_container_width=True)

        st.markdown("### Tabela (AH/AV) — top planos (Entradas)")
        out = top[["PLANO_CONTAS"] + hist_months + ["AH_%", "AV_%"]].copy()
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

    inv = sai_f.loc[sai_f["CONTA"].astype(str).str.contains("INVEST", case=False, na=False)].copy() if not sai_f.empty else pd.DataFrame()
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

    conc_tbl = None
    try:
        use_conc = (len(ym_sels) == 1) and (conc_tbl_all is not None) and (not conc_tbl_all.empty)
        if use_conc:
            y_sel = int(ym_sels[0][:4])
            m_sel = int(ym_sels[0][5:7])
            conc_tbl = conc_tbl_all.copy()
            conc_tbl["DATA"] = conc_tbl["DIA"].apply(lambda d: date(y_sel, m_sel, int(d)))

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

    ent_hist = df_ent.copy()
    sai_hist = df_sai.copy()
    trf_hist = df_trf.copy()

    if capt_sel and ("CAPTACAO" in ent_hist.columns):
        ent_hist = ent_hist[ent_hist["CAPTACAO"].isin([_upper(x) for x in capt_sel])].copy()

    if banco_sel:
        bset = [_upper(x) for x in banco_sel]
        if (not ent_hist.empty) and ("BANCO" in ent_hist.columns):
            ent_hist = ent_hist[ent_hist["BANCO"].isin(bset)].copy()
        if (not sai_hist.empty) and ("BANCO" in sai_hist.columns):
            sai_hist = sai_hist[sai_hist["BANCO"].isin(bset)].copy()
        if not trf_hist.empty:
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

        st.markdown("### Saldo por banco (final do período)")
        if resumo_banks is None or resumo_banks.empty:
            st.caption("Sem saldos por banco disponíveis.")
        else:
            show = resumo_banks.copy()
            for c in ["SALDO_INICIAL", "SALDO_MOV", "SALDO_REAL_FINAL"]:
                show[c] = show[c].apply(fmt_brl)
            st.dataframe(show, use_container_width=True, hide_index=True)


elif page.startswith("⏳"):
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Receber / Pagar (títulos em aberto e vencidos)")

    today = date.today()

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

    rec["DATA_BASE"] = rec["VENCIMENTO"].where(rec["VENCIMENTO"].notna(), rec["RECEBIMENTO"])
    rec["YM"] = rec["DATA_BASE"].apply(to_ym)
    rec = rec[rec["YM"].isin(ym_sels)].copy()

    rec_aberto = rec[rec["RECEBIMENTO"].isna() & rec["VENCIMENTO"].notna()].copy()
    rec_vencido = rec_aberto[rec_aberto["VENCIMENTO"] < today].copy()

    dias = st.slider("Próximos dias", min_value=1, max_value=60, value=15, step=1)
    limite = today + timedelta(days=dias)
    rec_prox = rec_aberto[(rec_aberto["VENCIMENTO"] >= today) & (rec_aberto["VENCIMENTO"] <= limite)].copy()

    pay = df_sai.copy()
    pay = pay[pay["YM"].isin(ym_sels)].copy()
    if banco_sel and ("BANCO" in pay.columns):
        pay = pay[pay["BANCO"].isin([_upper(x) for x in banco_sel])].copy()

    pay_aberto = pay[pay["PAGAMENTO"].isna() & pay["VENCIMENTO"].notna()].copy()
    pay_vencido = pay_aberto[pay_aberto["VENCIMENTO"] < today].copy()
    pay_prox = pay_aberto[(pay_aberto["VENCIMENTO"] >= today) & (pay_aberto["VENCIMENTO"] <= limite)].copy()

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
