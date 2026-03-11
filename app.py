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


# ---------------------- CONCILIAÇÃO (aba 7) ----------------------
MONTH_MAP_PT = {
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


def normalize_conciliacao(df: pd.DataFrame) -> Tuple[Optional[int], Optional[int], Optional[str], pd.DataFrame]:
    """Interpreta a aba '7. Conciliação' (layout tipo relatório) e devolve:
    - ano (int) se encontrado (opcional)
    - mes (int) se encontrado (opcional)
    - banco (str) se encontrado (opcional)
    - tabela por dia: DIA, ENTRADAS, SAIDAS, SALDO_DIA, SALDO_ACUM

    Importante:
    - Nesta aba o valor correto que você confere no Excel/Sheets é a coluna
      'SALDO ACUMULADO MÊS'. É isso que o painel deve exibir como SALDO_ACUM.
    - Ano/mês/banco nem sempre estão no cabeçalho; por isso eles são opcionais.
      No Fluxo de Caixa, quando o usuário escolhe UM mês, o painel monta a DATA
      usando o mês selecionado + o DIA da tabela.
    """
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

    # tenta achar ano/mês no cabeçalho (quando existir)
    for c in orig_cols:
        s = str(c).strip()
        if re.fullmatch(r"20\d{2}", s):
            try:
                year = int(s)
                break
            except Exception:
                pass

    for c in orig_cols:
        s = _strip_accents(str(c)).upper().strip()
        if s in MONTH_MAP_PT:
            month = MONTH_MAP_PT[s]
            break

    # tenta achar banco no cabeçalho (quando existir)
    try:
        i = cols_norm.index("BANCO")
        if i + 1 < len(orig_cols):
            b = str(orig_cols[i + 1]).strip()
            if b:
                bank = _upper(b)
    except Exception:
        pass

    # colunas da tabela (na sua planilha ficam nos 5 primeiros campos)
    c_dia = pick_col(list(df.columns), "DIA DO MES", "DIA DO MÊS", "DIA")
    c_ent = pick_col(list(df.columns), "ENTRADAS", "ENTRADA")
    c_sai = pick_col(list(df.columns), "SAIDAS", "SAÍDAS", "SAIDA")
    c_sd = pick_col(list(df.columns), "SALDO DO DIA", "SALDO_DIA")
    c_sa = pick_col(
        list(df.columns),
        "SALDO ACUMULADO MES",
        "SALDO ACUMULADO MÊS",
        "SALDO ACUMULADO",
        "SALDO_ACUMULADO",
        "SALDO_ACUM",
    )

    if not (c_dia and c_ent and c_sai and c_sa):
        return year, month, bank, empty

    def _parse_day(v):
        """Aceita '15', 15.0, 'Dia 15' etc. Retorna np.nan se não conseguir."""
        if v is None:
            return np.nan
        s = str(v).strip()
        if s == "" or s.lower() in {"nan", "none"}:
            return np.nan
        mm = re.search(r"(\d{1,2})", s)
        if not mm:
            return np.nan
        try:
            d = int(mm.group(1))
            return d if 1 <= d <= 31 else np.nan
        except Exception:
            return np.nan

    out = pd.DataFrame()
    out["DIA"] = df[c_dia].apply(_parse_day)
    out["ENTRADAS"] = df[c_ent].apply(money_to_float)
    out["SAIDAS"] = df[c_sai].apply(money_to_float)
    out["SALDO_DIA"] = df[c_sd].apply(money_to_float) if c_sd else (out["ENTRADAS"] - out["SAIDAS"])
    out["SALDO_ACUM"] = df[c_sa].apply(money_to_float)

    out = out.dropna(subset=["DIA"]).copy()
    out["DIA"] = out["DIA"].astype(int)
    out = out[(out["DIA"] >= 1) & (out["DIA"] <= 31)].copy()
    out = out[["DIA", "ENTRADAS", "SAIDAS", "SALDO_DIA", "SALDO_ACUM"]]
    return year, month, bank, out


def parse_saldo_inicial_sheet(df: pd.DataFrame) -> Tuple[Optional[date], pd.DataFrame]:
    """Lê a aba '1. Saldo inicial' (layout livre) e extrai:
    - base_date: data de referência do saldo inicial (se encontrada)
    - saldos: DataFrame com colunas BANCO, SALDO (somado por banco)

    Heurística (compatível com a sua planilha):
    - A data costuma aparecer no cabeçalho como '01/07/2024' (2ª coluna).
    - As linhas de saldo costumam estar nas primeiras linhas: [BANCO | SALDO | ...].
    """
    if df is None or df.empty:
        return None, pd.DataFrame(columns=["BANCO", "SALDO"])

    df = df.copy()

    # --- tenta achar a data base no cabeçalho ---
    base_date = None
    date_re = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$")
    for c in list(df.columns):
        s = str(c).strip()
        mm = date_re.match(s)
        if mm:
            try:
                d = datetime.strptime(s, "%d/%m/%Y").date()
                base_date = d
                break
            except Exception:
                pass

    # fallback: procura no corpo alguma célula 'DATA DO SALDO INICIAL' e pega a célula ao lado
    if base_date is None:
        try:
            mat = df.astype(str).values
            for i in range(min(mat.shape[0], 20)):
                for j in range(min(mat.shape[1], 10)):
                    if _strip_accents(mat[i, j]).upper().strip() == "DATA DO SALDO INICIAL":
                        if j + 1 < mat.shape[1]:
                            bd = parse_date_any(mat[i, j + 1])
                            if pd.notna(bd):
                                base_date = bd
                                break
                if base_date is not None:
                    break
        except Exception:
            pass

    # --- extrai linhas BANCO/SALDO ---
    # Considera as duas primeiras colunas como BANCO e SALDO (como na sua aba)
    cols = list(df.columns)
    if len(cols) < 2:
        return base_date, pd.DataFrame(columns=["BANCO", "SALDO"])

    c_bank = cols[0]
    c_val = cols[1]

    out = pd.DataFrame()
    out["BANCO"] = df[c_bank].astype(str).map(_upper)
    out["SALDO"] = df[c_val].apply(money_to_float)

    # limpa ruídos (cabeçalhos, vazios)
    bad = {"", "NAN", "NONE"}
    out = out[~out["BANCO"].isin(bad)].copy()
    out = out[~out["BANCO"].str.contains("DATA DO SALDO INICIAL", na=False)].copy()
    out = out[~out["BANCO"].str.contains("^BANCO$", na=False)].copy()

    # mantém apenas linhas com algum valor (aceita 0 também)
    out = out[out["BANCO"] != ""].copy()

    # remove linhas claramente não-banco (ex.: 'DIA', 'ANO', 'MES')
    out = out[~out["BANCO"].isin({"DIA", "ANO", "MES", "MÊS"})].copy()

    if out.empty:
        return base_date, pd.DataFrame(columns=["BANCO", "SALDO"])

    out = out.groupby("BANCO", as_index=False)["SALDO"].sum()
    return base_date, out


def compute_fluxo_caixa(df_ent: pd.DataFrame, df_sai: pd.DataFrame) -> pd.DataFrame:
    ent_day = (
        df_ent.groupby("DATA")["VALOR"].sum().reset_index().rename(columns={"VALOR": "ENTRADAS"})
        if not df_ent.empty
        else pd.DataFrame(columns=["DATA", "ENTRADAS"])
    )
    sai_day = (
        df_sai.groupby("DATA_REF")["VALOR"].sum().reset_index().rename(columns={"DATA_REF": "DATA", "VALOR": "SAIDAS"})
        if not df_sai.empty
        else pd.DataFrame(columns=["DATA", "SAIDAS"])
    )
    base = ent_day.merge(sai_day, on="DATA", how="outer").fillna(0.0)
    base["SALDO_DIA"] = base["ENTRADAS"] - base["SAIDAS"]
    base = base.sort_values("DATA")
    base["SALDO_ACUM"] = base["SALDO_DIA"].cumsum()
    base["YM"] = base["DATA"].apply(to_ym)
    return base
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


# ---------------------- CONCILIAÇÃO (aba 7) ----------------------
MONTH_MAP_PT = {
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


def normalize_conciliacao(df: pd.DataFrame) -> Tuple[Optional[int], Optional[int], Optional[str], pd.DataFrame]:
    """Interpreta a aba '7. Conciliação' (layout tipo relatório) e devolve:
    - ano (int) se encontrado (opcional)
    - mes (int) se encontrado (opcional)
    - banco (str) se encontrado (opcional)
    - tabela por dia: DIA, ENTRADAS, SAIDAS, SALDO_DIA, SALDO_ACUM

    Importante:
    - Nesta aba o valor correto que você confere no Excel/Sheets é a coluna
      'SALDO ACUMULADO MÊS'. É isso que o painel deve exibir como SALDO_ACUM.
    - Ano/mês/banco nem sempre estão no cabeçalho; por isso eles são opcionais.
      No Fluxo de Caixa, quando o usuário escolhe UM mês, o painel monta a DATA
      usando o mês selecionado + o DIA da tabela.
    """
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

    # tenta achar ano/mês no cabeçalho (quando existir)
    for c in orig_cols:
        s = str(c).strip()
        if re.fullmatch(r"20\d{2}", s):
            try:
                year = int(s)
                break
            except Exception:
                pass

    for c in orig_cols:
        s = _strip_accents(str(c)).upper().strip()
        if s in MONTH_MAP_PT:
            month = MONTH_MAP_PT[s]
            break

    # tenta achar banco no cabeçalho (quando existir)
    try:
        i = cols_norm.index("BANCO")
        if i + 1 < len(orig_cols):
            b = str(orig_cols[i + 1]).strip()
            if b:
                bank = _upper(b)
    except Exception:
        pass

    # colunas da tabela (na sua planilha ficam nos 5 primeiros campos)
    c_dia = pick_col(list(df.columns), "DIA DO MES", "DIA DO MÊS", "DIA")
    c_ent = pick_col(list(df.columns), "ENTRADAS", "ENTRADA")
    c_sai = pick_col(list(df.columns), "SAIDAS", "SAÍDAS", "SAIDA")
    c_sd = pick_col(list(df.columns), "SALDO DO DIA", "SALDO_DIA")
    c_sa = pick_col(
        list(df.columns),
        "SALDO ACUMULADO MES",
        "SALDO ACUMULADO MÊS",
        "SALDO ACUMULADO",
        "SALDO_ACUMULADO",
        "SALDO_ACUM",
    )

    if not (c_dia and c_ent and c_sai and c_sa):
        return year, month, bank, empty

    def _parse_day(v):
        """Aceita '15', 15.0, 'Dia 15' etc. Retorna np.nan se não conseguir."""
        if v is None:
            return np.nan
        s = str(v).strip()
        if s == "" or s.lower() in {"nan", "none"}:
            return np.nan
        mm = re.search(r"(\d{1,2})", s)
        if not mm:
            return np.nan
        try:
            d = int(mm.group(1))
            return d if 1 <= d <= 31 else np.nan
        except Exception:
            return np.nan

    out = pd.DataFrame()
    out["DIA"] = df[c_dia].apply(_parse_day)
    out["ENTRADAS"] = df[c_ent].apply(money_to_float)
    out["SAIDAS"] = df[c_sai].apply(money_to_float)
    out["SALDO_DIA"] = df[c_sd].apply(money_to_float) if c_sd else (out["ENTRADAS"] - out["SAIDAS"])
    out["SALDO_ACUM"] = df[c_sa].apply(money_to_float)

    out = out.dropna(subset=["DIA"]).copy()
    out["DIA"] = out["DIA"].astype(int)
    out = out[(out["DIA"] >= 1) & (out["DIA"] <= 31)].copy()
    out = out[["DIA", "ENTRADAS", "SAIDAS", "SALDO_DIA", "SALDO_ACUM"]]
    return year, month, bank, out


def parse_saldo_inicial_sheet(df: pd.DataFrame) -> Tuple[Optional[date], pd.DataFrame]:
    """Lê a aba '1. Saldo inicial' (layout livre) e extrai:
    - base_date: data de referência do saldo inicial (se encontrada)
    - saldos: DataFrame com colunas BANCO, SALDO (somado por banco)

    Heurística (compatível com a sua planilha):
    - A data costuma aparecer no cabeçalho como '01/07/2024' (2ª coluna).
    - As linhas de saldo costumam estar nas primeiras linhas: [BANCO | SALDO | ...].
    """
    if df is None or df.empty:
        return None, pd.DataFrame(columns=["BANCO", "SALDO"])

    df = df.copy()

    # --- tenta achar a data base no cabeçalho ---
    base_date = None
    date_re = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$")
    for c in list(df.columns):
        s = str(c).strip()
        mm = date_re.match(s)
        if mm:
            try:
                d = datetime.strptime(s, "%d/%m/%Y").date()
                base_date = d
                break
            except Exception:
                pass

    # fallback: procura no corpo alguma célula 'DATA DO SALDO INICIAL' e pega a célula ao lado
    if base_date is None:
        try:
            mat = df.astype(str).values
            for i in range(min(mat.shape[0], 20)):
                for j in range(min(mat.shape[1], 10)):
                    if _strip_accents(mat[i, j]).upper().strip() == "DATA DO SALDO INICIAL":
                        if j + 1 < mat.shape[1]:
                            bd = parse_date_any(mat[i, j + 1])
                            if pd.notna(bd):
                                base_date = bd
                                break
                if base_date is not None:
                    break
        except Exception:
            pass

    # --- extrai linhas BANCO/SALDO ---
    # Considera as duas primeiras colunas como BANCO e SALDO (como na sua aba)
    cols = list(df.columns)
    if len(cols) < 2:
        return base_date, pd.DataFrame(columns=["BANCO", "SALDO"])

    c_bank = cols[0]
    c_val = cols[1]

    out = pd.DataFrame()
    out["BANCO"] = df[c_bank].astype(str).map(_upper)
    out["SALDO"] = df[c_val].apply(money_to_float)

    # limpa ruídos (cabeçalhos, vazios)
    bad = {"", "NAN", "NONE"}
    out = out[~out["BANCO"].isin(bad)].copy()
    out = out[~out["BANCO"].str.contains("DATA DO SALDO INICIAL", na=False)].copy()
    out = out[~out["BANCO"].str.contains("^BANCO$", na=False)].copy()

    # mantém apenas linhas com algum valor (aceita 0 também)
    out = out[out["BANCO"] != ""].copy()

    # remove linhas claramente não-banco (ex.: 'DIA', 'ANO', 'MES')
    out = out[~out["BANCO"].isin({"DIA", "ANO", "MES", "MÊS"})].copy()

    if out.empty:
        return base_date, pd.DataFrame(columns=["BANCO", "SALDO"])

    out = out.groupby("BANCO", as_index=False)["SALDO"].sum()
    return base_date, out


def compute_fluxo_caixa(df_ent: pd.DataFrame, df_sai: pd.DataFrame) -> pd.DataFrame:
    ent_day = (
        df_ent.groupby("DATA")["VALOR"].sum().reset_index().rename(columns={"VALOR": "ENTRADAS"})
        if not df_ent.empty
        else pd.DataFrame(columns=["DATA", "ENTRADAS"])
    )
    sai_day = (
        df_sai.groupby("DATA_REF")["VALOR"].sum().reset_index().rename(columns={"DATA_REF": "DATA", "VALOR": "SAIDAS"})
        if not df_sai.empty
        else pd.DataFrame(columns=["DATA", "SAIDAS"])
    )
    base = ent_day.merge(sai_day, on="DATA", how="outer").fillna(0.0)
    base["SALDO_DIA"] = base["ENTRADAS"] - base["SAIDAS"]
    base = base.sort_values("DATA")
    base["SALDO_ACUM"] = base["SALDO_DIA"].cumsum()
    base["YM"] = base["DATA"].apply(to_ym)
    return base
def compute_saldo_bancos(
    df_ent_all: pd.DataFrame,
    df_sai_all: pd.DataFrame,
    df_trf_all: pd.DataFrame,
    df_saldo_ini: pd.DataFrame,
    base_date: Optional[date],
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Movimentação e saldo por banco (diário), com acumulado real ao longo do histórico.

    Regra:
      SALDO_REAL(dia) = SALDO_INICIAL + cumsum(ENTRADAS - SAIDAS + TRF_IN - TRF_OUT) desde base_date.

    Retorna:
      mv_daily: DATA, BANCO, ENTRADAS, SAIDAS, TRF_IN, TRF_OUT, SALDO_DIA, SALDO_REAL, SALDO_INICIAL
      resumo:   BANCO, SALDO_INICIAL, SALDO_MOV, SALDO_REAL_FINAL
    """

    saldo_ini_map = {}
    if (
        df_saldo_ini is not None
        and not df_saldo_ini.empty
        and {"BANCO", "SALDO"}.issubset(df_saldo_ini.columns)
    ):
        saldo_ini_map = {
            str(k).upper().strip(): float(v)
            for k, v in df_saldo_ini[["BANCO", "SALDO"]].values
        }

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
    if (
        df_ent_all is not None
        and not df_ent_all.empty
        and {"DATA", "BANCO", "VALOR"}.issubset(df_ent_all.columns)
    ):
        tmp = df_ent_all.copy()
        tmp["BANCO"] = tmp["BANCO"].astype(str).map(_upper)
        ent = (
            tmp.groupby(["DATA", "BANCO"], as_index=False)["VALOR"]
            .sum()
            .rename(columns={"VALOR": "ENTRADAS"})
        )

    sai = pd.DataFrame(columns=["DATA", "BANCO", "SAIDAS"])
    if (
        df_sai_all is not None
        and not df_sai_all.empty
        and {"DATA_REF", "BANCO", "VALOR"}.issubset(df_sai_all.columns)
    ):
        tmp = df_sai_all.copy()
        tmp["BANCO"] = tmp["BANCO"].astype(str).map(_upper)
        sai = (
            tmp.groupby(["DATA_REF", "BANCO"], as_index=False)["VALOR"]
            .sum()
            .rename(columns={"DATA_REF": "DATA", "VALOR": "SAIDAS"})
        )

    trf_in = pd.DataFrame(columns=["DATA", "BANCO", "TRF_IN"])
    trf_out = pd.DataFrame(columns=["DATA", "BANCO", "TRF_OUT"])
    if (
        df_trf_all is not None
        and not df_trf_all.empty
        and {"DATA", "VALOR"}.issubset(df_trf_all.columns)
    ):
        tmp = df_trf_all.copy()

        if "DESTINO" in tmp.columns:
            tmp["DESTINO"] = tmp["DESTINO"].astype(str).map(_upper)
            trf_in = (
                tmp.groupby(["DATA", "DESTINO"], as_index=False)["VALOR"]
                .sum()
                .rename(columns={"DESTINO": "BANCO", "VALOR": "TRF_IN"})
            )

        if "ORIGEM" in tmp.columns:
            tmp["ORIGEM"] = tmp["ORIGEM"].astype(str).map(_upper)
            trf_out = (
                tmp.groupby(["DATA", "ORIGEM"], as_index=False)["VALOR"]
                .sum()
                .rename(columns={"ORIGEM": "BANCO", "VALOR": "TRF_OUT"})
            )

    mv = (
        ent.merge(sai, on=["DATA", "BANCO"], how="outer")
        .merge(trf_in, on=["DATA", "BANCO"], how="outer")
        .merge(trf_out, on=["DATA", "BANCO"], how="outer")
        .fillna(0.0)
    )

    if mv.empty:
        mv_daily = pd.DataFrame(
            columns=[
                "DATA",
                "BANCO",
                "ENTRADAS",
                "SAIDAS",
                "TRF_IN",
                "TRF_OUT",
                "SALDO_DIA",
                "SALDO_REAL",
                "SALDO_INICIAL",
            ]
        )
        resumo = pd.DataFrame(
            columns=["BANCO", "SALDO_INICIAL", "SALDO_MOV", "SALDO_REAL_FINAL"]
        )
        return mv_daily, resumo

    mv["SALDO_DIA"] = mv["ENTRADAS"] - mv["SAIDAS"] + mv["TRF_IN"] - mv["TRF_OUT"]
    mv["BANCO"] = mv["BANCO"].astype(str).map(_upper)

    dmin = mv["DATA"].min()
    dmax = mv["DATA"].max()
    if base_date is not None and pd.notna(dmin):
        dmin = max(dmin, base_date)

    all_dates = pd.date_range(pd.to_datetime(dmin), pd.to_datetime(dmax), freq="D")

    pieces = []
    for bank, g in mv.groupby("BANCO"):
        g = g.sort_values("DATA").copy()
        g_idx = pd.to_datetime(g["DATA"])
        base = pd.DataFrame(index=all_dates)

        for col in ["ENTRADAS", "SAIDAS", "TRF_IN", "TRF_OUT", "SALDO_DIA"]:
            s = pd.Series(g[col].values, index=g_idx)
            base[col] = s.reindex(all_dates).fillna(0.0)

        ini = float(saldo_ini_map.get(str(bank).upper().strip(), 0.0))
        base["SALDO_INICIAL"] = ini
        base["SALDO_REAL"] = ini + base["SALDO_DIA"].cumsum()

        base = base.reset_index().rename(columns={"index": "DATA"})
        base["DATA"] = base["DATA"].dt.date
        base["BANCO"] = bank
        pieces.append(base)

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

    mv["SALDO_DIA"] = mv["ENTRADAS"] - mv["SAIDAS"] + mv["TRF_IN"] - mv["TRF_OUT"]
    mv["BANCO"] = mv["BANCO"].astype(str).map(_upper)

    # range diário global
    dmin = mv["DATA"].min()
    dmax = mv["DATA"].max()
    if base_date is not None and pd.notna(dmin):
        dmin = max(dmin, base_date)

    all_dates = pd.date_range(pd.to_datetime(dmin), pd.to_datetime(dmax), freq="D")

    pieces = []
    for bank, g in mv.groupby("BANCO"):
        g = g.sort_values("DATA").copy()
        g_idx = pd.to_datetime(g["DATA"])
        base = pd.DataFrame(index=all_dates)

        for col in ["ENTRADAS", "SAIDAS", "TRF_IN", "TRF_OUT", "SALDO_DIA"]:
            s = pd.Series(g[col].values, index=g_idx)
            base[col] = s.reindex(all_dates).fillna(0.0)

        ini = float(saldo_ini_map.get(str(bank).upper().strip(), 0.0))
        base["SALDO_INICIAL"] = ini
        base["SALDO_REAL"] = ini + base["SALDO_DIA"].cumsum()

        base = base.reset_index().rename(columns={"index": "DATA"})
        base["DATA"] = base["DATA"].dt.date
        base["BANCO"] = bank
        pieces.append(base)

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
    """Total diário (somando bancos) preservando SALDO_REAL (saldo final real por dia).

    mv_banks_daily: saída de compute_saldo_bancos (já diário).
    bancos: lista de bancos selecionados (UPPER). Se vazio, usa todos.
    Retorna: DATA, ENTRADAS, SAIDAS, SALDO_DIA, SALDO_REAL
    """
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
    if label is not None:
        d["SÉRIE"] = label
    return d


# ====================== LOAD DATA ======================
st.sidebar.markdown(f"### {COMPANY_NAME}")
if LOGO_URL:
    st.sidebar.image(LOGO_URL, use_container_width=True)
st.sidebar.markdown("<div class='small'>Financeiro • Streamlit</div>", unsafe_allow_html=True)
st.sidebar.markdown("<div class='hr'></div>", unsafe_allow_html=True)

PAGES = [
    ("Dashboard", "📊"),
    ("Entradas", "💚"),
    ("Saídas", "💸"),
    ("Investimentos", "🟨"),
    ("Fluxo de Caixa", "💧"),
    ("Receber / Pagar", "⏳"),
    ("Conciliação", "🧾"),
    ("Exportar", "⬇️"),
]
page = st.sidebar.radio("Menu", [f"{ico}  {name}" for name, ico in PAGES], index=0)

with st.spinner("Carregando planilha..."):
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

# período do mês escolhido (pelas datas efetivas)
dates_in_month: List[date] = []

def _as_date(v):
    """Garante date (e evita pd.NaT quebrando st.date_input)."""
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
    return None

if not df_ent.empty:
    for v in df_ent[df_ent["YM"].isin(ym_sels)]["DATA"].tolist():
        d = _as_date(v)
        if d:
            dates_in_month.append(d)

if not df_sai.empty:
    for v in df_sai[df_sai["YM"].isin(ym_sels)]["DATA_REF"].tolist():
        d = _as_date(v)
        if d:
            dates_in_month.append(d)

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
    capt_opts = (
        sorted(df_ent[df_ent["YM"].isin(ym_sels)]["CAPTACAO"].dropna().unique().tolist())
        if (not df_ent.empty and "CAPTACAO" in df_ent.columns)
        else []
    )
    capt_sel = st.multiselect("Captação", options=capt_opts, default=capt_opts)

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

    # aplica o mesmo filtro de banco também nas ENTRADAS (se a aba tiver BANCO)
    if banco_sel and (not ent.empty) and ("BANCO" in ent.columns):
        ent = ent[ent["BANCO"].isin([_upper(x) for x in banco_sel])].copy()

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
    with cA:
        st_kpi("Receita Total", fmt_brl(ent_total), sub=f"Período {sel_period_label}")
    with cB:
        st_kpi("Despesas", fmt_brl(desp_total), sub="Saídas (sem investimentos)")
    with cC:
        st_kpi("Investimentos", fmt_brl(inv_total), sub="Regra: CONTA contém 'INVEST'", badge=("revisável", "warn"))
    with cD:
        st_kpi("Total de Saídas", fmt_brl(sai_total), sub="Despesas + investimentos")
    with cE:
        badge = ("positivo", "good") if lucro_liq >= 0 else ("negativo", "bad")
        st_kpi("Resultado Líquido", fmt_brl(lucro_liq), sub="Receita - Saídas", badge=badge)

    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Evolução (mensal)")
    m_ent = (
        df_ent.groupby("YM")["VALOR"].sum().reset_index().rename(columns={"VALOR": "Receitas"})
        if not df_ent.empty
        else pd.DataFrame(columns=["YM", "Receitas"])
    )
    m_sai = (
        df_sai.groupby("YM")["VALOR"].sum().reset_index().rename(columns={"VALOR": "Saídas"})
        if not df_sai.empty
        else pd.DataFrame(columns=["YM", "Saídas"])
    )
    evo = m_ent.merge(m_sai, on="YM", how="outer").fillna(0.0)
    evo["Resultado"] = evo["Receitas"] - evo["Saídas"]
    evo = evo.sort_values("YM")
    evo["Mês"] = evo["YM"].map(month_label)
    evo_melt = evo.melt(id_vars=["YM", "Mês"], value_vars=["Receitas", "Saídas", "Resultado"], var_name="Métrica", value_name="Valor")

    bars = alt.Chart(evo_melt).mark_bar().encode(
        x=alt.X("Mês:N", sort=list(evo["Mês"]), title=""),
        y=alt.Y("Valor:Q", title="R$"),
        color=alt.Color("Métrica:N", legend=alt.Legend(title="")),
        tooltip=["Mês", "Métrica", alt.Tooltip("Valor:Q", format=",.2f")],
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

    qtd = int(len(ent_f)) if not ent_f.empty else 0
    dias = int(ent_f["DATA"].nunique()) if (not ent_f.empty and "DATA" in ent_f.columns) else 0
    media_dia = (ent_total / dias) if dias > 0 else 0.0
    maior_dia = float(ent_f.groupby("DATA")["VALOR"].sum().max()) if not ent_f.empty else 0.0

    cA, cB, cC, cD = st.columns(4)
    with cA:
        st_kpi("Total Entradas", fmt_brl(ent_total), sub=f"{qtd} lançamentos")
    with cB:
        st_kpi("Média por dia", fmt_brl(media_dia), sub=f"{dias} dias com movimento")
    with cC:
        st_kpi("Maior dia", fmt_brl(maior_dia), sub="Pico de entradas no período")
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
        last = last_point_label(daily, "DATA", "VALOR")
        lbl = alt.Chart(last).mark_text(align="left", dx=8, dy=-8).encode(x="DATA:T", y="VALOR:Q", text="LABEL:N")
        st.altair_chart(line + lbl, use_container_width=True)

    out = ent_f.sort_values("DATA", ascending=False).copy() if not ent_f.empty else ent_f
    if not out.empty:
        out["R$"] = out["VALOR"].map(fmt_brl)
    st.dataframe(out.drop(columns=["VALOR"], errors="ignore"), use_container_width=True, hide_index=True)
    # -------- Análise Vertical & Horizontal (Entradas) --------
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Análise Vertical e Horizontal — Entradas")

    # meses do histórico (até o último mês selecionado)
    hist_months_all = [m for m in months if m <= ym_focus]
    last_n = 6
    hist_months = hist_months_all[-last_n:] if len(hist_months_all) > last_n else hist_months_all

    ent_hist = df_ent.copy()
    if capt_sel and ("CAPTACAO" in ent_hist.columns):
        ent_hist = ent_hist[ent_hist["CAPTACAO"].isin([_upper(x) for x in capt_sel])].copy()

    if ent_hist.empty or len(hist_months) < 2:
        st.caption("Sem histórico suficiente para calcular a análise (precisa de pelo menos 2 meses).")
    else:
        t = (
            ent_hist[ent_hist["YM"].isin(hist_months)]
            .groupby(["PLANO_CONTAS", "YM"])["VALOR"]
            .sum()
            .reset_index()
        )

        if t.empty:
            st.caption("Sem histórico suficiente após os filtros para calcular a análise.")
        else:
            piv = t.pivot(index="PLANO_CONTAS", columns="YM", values="VALOR").fillna(0.0)
            piv = piv.reset_index().rename(columns={"PLANO_CONTAS": "CONTA"})

            meses_disponiveis = [m for m in hist_months if m in piv.columns]

            if len(meses_disponiveis) < 2:
                st.caption("Sem histórico suficiente após os filtros para calcular a análise.")
            else:
                last_m = meses_disponiveis[-1]
                prev_m = meses_disponiveis[-2]

                piv["__LAST"] = piv[last_m]
                top = piv.sort_values("__LAST", ascending=False).head(10).drop(columns="__LAST")

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
                    tot = pd.DataFrame({"YM": meses_disponiveis})
                    tot["Entradas"] = tot["YM"].map(lambda m: float(ent_hist[ent_hist["YM"] == m]["VALOR"].sum()))
                    tot["Mês"] = tot["YM"].map(month_label)
                    line = alt.Chart(tot).mark_line(point=True).encode(
                        x=alt.X("Mês:N", sort=list(tot["Mês"]), title=""),
                        y=alt.Y("Entradas:Q", title="R$"),
                        tooltip=["Mês", alt.Tooltip("Entradas:Q", format=",.2f", title="R$")],
                    ).properties(height=320)
                    st.altair_chart(line, use_container_width=True)

                st.markdown("### Tabela (AH/AV) — top contas (Entradas)")
                out = top[["CONTA"] + meses_disponiveis + ["AH_%", "AV_%"]].copy()
                for m in meses_disponiveis:
                    out[m] = out[m].apply(lambda v: safe_num(v))
                show = out.copy()
                for m in meses_disponiveis:
                    show[m] = show[m].apply(fmt_brl)
                show["AH_%"] = show["AH_%"].apply(lambda v: "" if pd.isna(v) else f"{v*100:.1f}%")
                show["AV_%"] = show["AV_%"].apply(lambda v: "" if pd.isna(v) else f"{v*100:.1f}%")
                st.dataframe(show, use_container_width=True, hide_index=True)
