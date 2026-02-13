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
    """Movimentação e saldo por banco (por dia), com acumulado REAL ao longo de todo o histórico.

    Regra:
      SALDO_REAL(dia) = SALDO_INICIAL_BASE + cumsum(ENTRADAS - SAIDAS + TRF_IN - TRF_OUT) desde base_date.

    Observação:
    - Passe df_* com TODO o histórico necessário (pelo menos até o fim do período filtrado),
      para que o saldo no mês já venha com o "carregado" correto.
    """

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
    if (df_ent_all is not None) and (not df_ent_all.empty) and {"DATA", "BANCO", "VALOR"}.issubset(df_ent_all.columns):
        ent = (
            df_ent_all.groupby(["DATA", "BANCO"], as_index=False)["VALOR"]
            .sum()
            .rename(columns={"VALOR": "ENTRADAS"})
        )

    sai = pd.DataFrame(columns=["DATA", "BANCO", "SAIDAS"])
    if (df_sai_all is not None) and (not df_sai_all.empty) and {"DATA_REF", "BANCO", "VALOR"}.issubset(df_sai_all.columns):
        sai = (
            df_sai_all.groupby(["DATA_REF", "BANCO"], as_index=False)["VALOR"]
            .sum()
            .rename(columns={"DATA_REF": "DATA", "VALOR": "SAIDAS"})
        )

    trf_in = pd.DataFrame(columns=["DATA", "BANCO", "TRF_IN"])
    trf_out = pd.DataFrame(columns=["DATA", "BANCO", "TRF_OUT"])
    if (df_trf_all is not None) and (not df_trf_all.empty) and {"DATA", "VALOR"}.issubset(df_trf_all.columns):
        if "DESTINO" in df_trf_all.columns:
            trf_in = (
                df_trf_all.groupby(["DATA", "DESTINO"], as_index=False)["VALOR"]
                .sum()
                .rename(columns={"DESTINO": "BANCO", "VALOR": "TRF_IN"})
            )
        if "ORIGEM" in df_trf_all.columns:
            trf_out = (
                df_trf_all.groupby(["DATA", "ORIGEM"], as_index=False)["VALOR"]
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
        mv = pd.DataFrame(columns=["DATA", "BANCO", "ENTRADAS", "SAIDAS", "TRF_IN", "TRF_OUT", "SALDO_DIA", "SALDO_MOV_ACUM", "SALDO_REAL", "SALDO_INICIAL"])
        resumo = pd.DataFrame(columns=["BANCO", "SALDO_INICIAL", "SALDO_MOV", "SALDO_REAL_FINAL"])
        return mv, resumo


def build_fluxo_total_from_mv(mv_banks: pd.DataFrame, bancos: List[str], dt_ini: Optional[date], dt_fim: Optional[date]) -> pd.DataFrame:
    """Constrói um fluxo diário TOTAL com SALDO_REAL correto (com carryover), a partir do mv (por banco).

    mv_banks: saída de compute_saldo_bancos (linhas somente em dias com movimento por banco).
    bancos: lista de bancos selecionados (já em UPPER). Se vazio, usa todos.
    Retorna colunas: DATA, ENTRADAS, SAIDAS, SALDO_DIA, SALDO_REAL
    """
    if mv_banks is None or mv_banks.empty:
        return pd.DataFrame(columns=["DATA", "ENTRADAS", "SAIDAS", "SALDO_DIA", "SALDO_REAL"])

    mv = mv_banks.copy()
    mv["BANCO"] = mv["BANCO"].astype(str).map(_upper)

    if bancos:
        mv = mv[mv["BANCO"].isin([_upper(b) for b in bancos])].copy()
        if mv.empty:
            return pd.DataFrame(columns=["DATA", "ENTRADAS", "SAIDAS", "SALDO_DIA", "SALDO_REAL"])

    # define janela de datas
    dmin = mv["DATA"].min()
    dmax = mv["DATA"].max()
    if dt_ini:
        dmin = max(dmin, dt_ini)
    if dt_fim:
        dmax = min(dmax, dt_fim)
    if (dmin is None) or (dmax is None) or pd.isna(dmin) or pd.isna(dmax) or dmin > dmax:
        return pd.DataFrame(columns=["DATA", "ENTRADAS", "SAIDAS", "SALDO_DIA", "SALDO_REAL"])

    all_dates = pd.date_range(dmin, dmax, freq="D")

    pieces = []
    for bank, g in mv.groupby("BANCO"):
        g = g.sort_values("DATA").copy()
        g_idx = pd.to_datetime(g["DATA"])

        base = pd.DataFrame(index=all_dates)
        # movimentos (0 nos dias sem linha)
        for col in ["ENTRADAS", "SAIDAS", "TRF_IN", "TRF_OUT", "SALDO_DIA"]:
            if col in g.columns:
                s = pd.Series(g[col].values, index=g_idx)
                base[col] = s.reindex(all_dates).fillna(0.0)
            else:
                base[col] = 0.0

        # saldo real (ffill nos dias sem linha)
        sreal = pd.Series(g["SALDO_REAL"].values, index=g_idx) if "SALDO_REAL" in g.columns else pd.Series(dtype=float)
        base["SALDO_REAL"] = sreal.reindex(all_dates).ffill()

        # preenche o início com o saldo inicial do banco, se existir
        ini = float(g["SALDO_INICIAL"].iloc[0]) if "SALDO_INICIAL" in g.columns and len(g) else 0.0
        base["SALDO_REAL"] = base["SALDO_REAL"].fillna(ini)

        base = base.reset_index().rename(columns={"index": "DATA"})
        base["DATA"] = base["DATA"].dt.date
        base["BANCO"] = bank
        pieces.append(base)

    full = pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame()
    if full.empty:
        return pd.DataFrame(columns=["DATA", "ENTRADAS", "SAIDAS", "SALDO_DIA", "SALDO_REAL"])

    # totaliza (somatório por dia)
    total = (
        full.groupby("DATA", as_index=False)[["ENTRADAS", "SAIDAS", "SALDO_DIA", "SALDO_REAL"]]
        .sum()
        .sort_values("DATA")
    )
    return total
    mv["SALDO_DIA"] = mv["ENTRADAS"] - mv["SAIDAS"] + mv["TRF_IN"] - mv["TRF_OUT"]

    def _add_acum(g: pd.DataFrame) -> pd.DataFrame:
        g = g.sort_values("DATA").copy()
        bank = str(g["BANCO"].iloc[0]).upper().strip()
        ini = float(saldo_ini_map.get(bank, 0.0))
        g["SALDO_MOV_ACUM"] = g["SALDO_DIA"].cumsum()
        g["SALDO_REAL"] = ini + g["SALDO_MOV_ACUM"]
        g["SALDO_INICIAL"] = ini
        return g

    mv = mv.groupby("BANCO", group_keys=False).apply(_add_acum).sort_values(["BANCO", "DATA"])

    resumo = (
        mv.groupby("BANCO", as_index=False)
        .agg(
            SALDO_INICIAL=("SALDO_INICIAL", "max"),
            SALDO_MOV=("SALDO_DIA", "sum"),
            SALDO_REAL_FINAL=("SALDO_REAL", "last"),
        )
        .sort_values("SALDO_REAL_FINAL", ascending=False)
    )

    return mv, resumo

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
def normalize_conciliacao(df: pd.DataFrame) -> Tuple[pd.DataFrame, Optional[str], Optional[str], Optional[str]]:
    """Normaliza a aba 7. Conciliação (tabela diária já com saldo acumulado do mês).
    Retorna:
      - tabela (DATA, ENTRADAS, SAIDAS, SALDO_DIA, SALDO_ACUM_MES)
      - ano (str) / mes (str) / banco (str) encontrados no cabeçalho (se existirem)
    """
    if df is None or df.empty:
        return pd.DataFrame(), None, None, None

    d = df.copy()
    d.columns = [_norm_col(c) for c in d.columns]

    c_dia = pick_col(list(d.columns), "DIA DO MES", "DIA")
    c_ent = pick_col(list(d.columns), "ENTRADAS", "ENTRADA")
    c_sai = pick_col(list(d.columns), "SAIDAS", "SAIDA")
    c_saldo_dia = pick_col(list(d.columns), "SALDO DO DIA", "SALDO_DIA")
    c_saldo_acum = pick_col(list(d.columns), "SALDO ACUMULADO MES", "SALDO ACUMULADO MÊS", "SALDO ACUMULADO", "SALDO_ACUMULADO_MES")

    # tenta achar ano/mes/banco em colunas soltas da própria aba (layout livre)
    ano_val = None
    mes_val = None
    banco_val = None
    try:
        if "ANO" in d.columns:
            vv = d["ANO"].replace("", np.nan).dropna()
            if len(vv) > 0:
                ano_val = str(int(float(vv.iloc[0])))
        if "MES" in d.columns:
            vv = d["MES"].replace("", np.nan).dropna()
            if len(vv) > 0:
                mes_val = str(vv.iloc[0]).strip()
        if "MÊS" in d.columns and mes_val is None:
            vv = d["MÊS"].replace("", np.nan).dropna()
            if len(vv) > 0:
                mes_val = str(vv.iloc[0]).strip()
        if "BANCO" in d.columns:
            vv = d["BANCO"].replace("", np.nan).dropna()
            if len(vv) > 0:
                banco_val = _upper(vv.iloc[0])
    except Exception:
        pass

    out = pd.DataFrame()
    if c_dia:
        out["DIA"] = pd.to_numeric(d[c_dia], errors="coerce")
    else:
        out["DIA"] = pd.Series(dtype=float)

    out["ENTRADAS"] = d[c_ent].apply(money_to_float) if c_ent else 0.0
    out["SAIDAS"] = d[c_sai].apply(money_to_float) if c_sai else 0.0

    if c_saldo_dia:
        out["SALDO_DIA"] = d[c_saldo_dia].apply(money_to_float)
    else:
        out["SALDO_DIA"] = out["ENTRADAS"] - out["SAIDAS"]

    if c_saldo_acum:
        out["SALDO_ACUM_MES"] = d[c_saldo_acum].apply(money_to_float)
    else:
        out["SALDO_ACUM_MES"] = np.nan

    out = out[out["DIA"].notna()].copy()
    out["DIA"] = out["DIA"].astype(int)

    # DATA será preenchida depois, com base em ym selecionado (YYYY-MM)
    out["DATA"] = pd.NaT

    out = out.sort_values("DIA")
    return out[["DATA", "DIA", "ENTRADAS", "SAIDAS", "SALDO_DIA", "SALDO_ACUM_MES"]].copy(), ano_val, mes_val, banco_val

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
        last_m = hist_months[-1]
        prev_m = hist_months[-2]

        t = (
            ent_hist[ent_hist["YM"].isin(hist_months)]
            .groupby(["PLANO_CONTAS", "YM"])["VALOR"]
            .sum()
            .reset_index()
        )
        piv = t.pivot(index="PLANO_CONTAS", columns="YM", values="VALOR").fillna(0.0)
        piv = piv.reset_index().rename(columns={"PLANO_CONTAS": "CONTA"})

        # top contas pelo mês mais recente
        piv["__LAST"] = piv[last_m]
        top = piv.sort_values("__LAST", ascending=False).head(10).drop(columns="__LAST")

        totals_last = float(ent_hist[ent_hist["YM"] == last_m]["VALOR"].sum()) if (not ent_hist.empty) else 0.0

        # AV e AH (com tratamento de divisão por zero)
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
            tot["Entradas"] = tot["YM"].map(lambda m: float(ent_hist[ent_hist["YM"] == m]["VALOR"].sum()))
            tot["Mês"] = tot["YM"].map(month_label)
            line = alt.Chart(tot).mark_line(point=True).encode(
                x=alt.X("Mês:N", sort=list(tot["Mês"]), title=""),
                y=alt.Y("Entradas:Q", title="R$"),
                tooltip=["Mês", alt.Tooltip("Entradas:Q", format=",.2f", title="R$")],
            ).properties(height=320)
            st.altair_chart(line, use_container_width=True)

        st.markdown("### Tabela (AH/AV) — top contas (Entradas)")
        out = top[["CONTA"] + hist_months + ["AH_%", "AV_%"]].copy()
        for m in hist_months:
            out[m] = out[m].apply(lambda v: safe_num(v))
        show = out.copy()
        for m in hist_months:
            show[m] = show[m].apply(fmt_brl)
        show["AH_%"] = show["AH_%"].apply(lambda v: "" if pd.isna(v) else f"{v*100:.1f}%")
        show["AV_%"] = show["AV_%"].apply(lambda v: "" if pd.isna(v) else f"{v*100:.1f}%")
        st.dataframe(show, use_container_width=True, hide_index=True)


elif page.startswith("💸"):
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Saídas — visão analítica")

    qtd = int(len(sai_f)) if not sai_f.empty else 0
    dias = int(sai_f["DATA_REF"].nunique()) if (not sai_f.empty and "DATA_REF" in sai_f.columns) else 0
    media_dia = (sai_total / dias) if dias > 0 else 0.0
    maior_dia = float(sai_f.groupby("DATA_REF")["VALOR"].sum().max()) if not sai_f.empty else 0.0

    aberto = 0.0
    if (not sai_f.empty) and ("VENCIMENTO" in sai_f.columns):
        mask_aberto = sai_f["PAGAMENTO"].isna() if "PAGAMENTO" in sai_f.columns else pd.Series([False] * len(sai_f))
        aberto = float(sai_f.loc[mask_aberto, "VALOR"].sum()) if "VALOR" in sai_f.columns else 0.0

    cA, cB, cC, cD = st.columns(4)
    with cA:
        st_kpi("Total Saídas", fmt_brl(sai_total), sub=f"{qtd} lançamentos")
    with cB:
        st_kpi("Média por dia", fmt_brl(media_dia), sub=f"{dias} dias com movimento")
    with cC:
        st_kpi("Maior dia", fmt_brl(maior_dia), sub="Pico de saídas no período")
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
        lbl = alt.Chart(last).mark_text(align="left", dx=8, dy=-8).encode(x="DATA_REF:T", y="VALOR:Q", text="LABEL:N")
        st.altair_chart(line + lbl, use_container_width=True)

    out = sai_f.sort_values("DATA_REF", ascending=False).copy() if not sai_f.empty else sai_f
    if not out.empty:
        out["R$"] = out["VALOR"].map(fmt_brl)
    st.dataframe(out.drop(columns=["VALOR"], errors="ignore"), use_container_width=True, hide_index=True)
    # -------- Análise Vertical & Horizontal (Saídas) --------
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Análise Vertical e Horizontal — Saídas")

    hist_months_all = [m for m in months if m <= ym_focus]
    last_n = 6
    hist_months = hist_months_all[-last_n:] if len(hist_months_all) > last_n else hist_months_all

    sai_hist = df_sai.copy()
    if banco_sel and ("BANCO" in sai_hist.columns):
        sai_hist = sai_hist[sai_hist["BANCO"].isin([_upper(x) for x in banco_sel])].copy()

    # Total de receita (Entradas) — base para AV (Entradas e Saídas)
    ent_hist = df_ent.copy()
    if capt_sel and ("CAPTACAO" in ent_hist.columns):
        ent_hist = ent_hist[ent_hist["CAPTACAO"].isin([_upper(x) for x in capt_sel])].copy()

    if sai_hist.empty or len(hist_months) < 2:
        st.caption("Sem histórico suficiente para calcular a análise (precisa de pelo menos 2 meses).")
    else:
        last_m = hist_months[-1]
        prev_m = hist_months[-2]

        t = (
            sai_hist[sai_hist["YM"].isin(hist_months)]
            .groupby(["CONTA", "YM"])["VALOR"]
            .sum()
            .reset_index()
        )
        piv = t.pivot(index="CONTA", columns="YM", values="VALOR").fillna(0.0).reset_index()

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

    # =========================================================
    # PRIORIDADE: usar a aba 7. Conciliação (já tem o "saldo acumulado do mês"
    # com o carryover do mês anterior — exatamente como você confere no Excel).
    # =========================================================
    use_conc = (len(ym_sels) == 1) and (conc_tbl is not None) and (not conc_tbl.empty)

    # se a conciliação vier amarrada a um banco específico, respeita o filtro (quando possível)
    if use_conc and conc_banco and banco_sel:
        # se o usuário filtrou bancos e o banco da conciliação não está selecionado, não usa
        if _upper(conc_banco) not in [_upper(b) for b in banco_sel]:
            use_conc = False

    if use_conc:
        y = int(ym_focus[:4])
        m = int(ym_focus[5:7])

        fluxo_disp = conc_tbl.copy()
        # preenche DATA a partir do DIA + (ano/mês do filtro)
        fluxo_disp["DATA"] = fluxo_disp["DIA"].apply(lambda d: date(y, m, int(d)))

        # recorte por período (date_input)
        if dt_ini and dt_fim:
            fluxo_disp = fluxo_disp[(fluxo_disp["DATA"] >= dt_ini) & (fluxo_disp["DATA"] <= dt_fim)].copy()

        # garante saldo acumulado do mês (se a coluna vier vazia por algum motivo, calcula pelo 1º saldo de referência)
        if "SALDO_ACUM_MES" not in fluxo_disp.columns:
            fluxo_disp["SALDO_ACUM_MES"] = np.nan
        if fluxo_disp["SALDO_ACUM_MES"].isna().all():
            # tenta inferir o saldo inicial do mês: (saldo_acum do 1º dia - saldo_dia do 1º dia)
            if len(fluxo_disp) > 0:
                ini_mes = float(fluxo_disp["SALDO_DIA"].iloc[0]) * 0.0
                ini_mes = float(fluxo_disp["SALDO_DIA"].iloc[0]) * 0.0  # fallback 0
                # se existir alguma célula de saldo acumulado não-numérica, tenta extrair
                try:
                    ini_mes = float(fluxo_disp["SALDO_ACUM_MES"].iloc[0]) - float(fluxo_disp["SALDO_DIA"].iloc[0])
                except Exception:
                    ini_mes = 0.0
                fluxo_disp = fluxo_disp.sort_values("DATA").copy()
                fluxo_disp["SALDO_ACUM_MES"] = ini_mes + fluxo_disp["SALDO_DIA"].cumsum()

        # gráfico (entradas / saídas / saldo do dia)
        melt = fluxo_disp.melt(
            id_vars=["DATA"],
            value_vars=["ENTRADAS", "SAIDAS", "SALDO_DIA"],
            var_name="Métrica",
            value_name="Valor",
        )
        melt["Métrica"] = melt["Métrica"].replace(
            {"ENTRADAS": "Entradas", "SAIDAS": "Saídas", "SALDO_DIA": "Saldo do dia"}
        )

        chart = (
            alt.Chart(melt)
            .mark_line(point=True)
            .encode(
                x=alt.X("DATA:T", title="Data", axis=alt.Axis(format="%d/%m")),
                y=alt.Y("Valor:Q", title="R$"),
                color=alt.Color("Métrica:N", legend=alt.Legend(title="")),
                tooltip=[
                    alt.Tooltip("DATA:T", title="Data", format="%d/%m/%Y"),
                    "Métrica",
                    alt.Tooltip("Valor:Q", format=",.2f", title="R$"),
                ],
            )
            .properties(height=320)
        )
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
            final_mes = float(fluxo_disp.sort_values("DATA")["SALDO_ACUM_MES"].iloc[-1])
            badge = ("positivo", "good") if final_mes >= 0 else ("negativo", "bad")
            st_kpi("Saldo acumulado (mês)", fmt_brl(final_mes), sub="Carryover + movimentação", badge=badge)

        st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
        st.markdown("### Tabela do fluxo (por dia)")
        fluxo_tbl_show = fluxo_disp[["DATA", "ENTRADAS", "SAIDAS", "SALDO_DIA", "SALDO_ACUM_MES"]].copy().sort_values("DATA")
        fluxo_tbl_show = fluxo_tbl_show.rename(columns={"SALDO_ACUM_MES": "SALDO_ACUM"})
        for c in ["ENTRADAS", "SAIDAS", "SALDO_DIA", "SALDO_ACUM"]:
            fluxo_tbl_show[c] = fluxo_tbl_show[c].apply(fmt_brl)
        st.dataframe(fluxo_tbl_show, use_container_width=True, hide_index=True)

    else:
        # =========================================================
        # FALLBACK: calcula pelo histórico (entradas/saídas/transferências)
        # (mantém o painel funcional mesmo se a aba 7. Conciliação não existir)
        # =========================================================

        # 1) Fluxo "histórico" inclui o saldo inicial (base) para o acumulado REAL
        ent_hist = df_ent.copy()
        sai_hist = df_sai.copy()
        trf_hist = df_trf.copy()

        # aplica filtro de bancos também nas entradas (importante para não zerar as entradas quando filtra banco)
        if banco_sel and (not ent_hist.empty) and ("BANCO" in ent_hist.columns):
            ent_hist = ent_hist[ent_hist["BANCO"].isin([_upper(x) for x in banco_sel])].copy()

        # limita ao período a partir do saldo_base_date para construir acumulado correto
        if saldo_base_date is not None:
            if not ent_hist.empty:
                ent_hist = ent_hist[ent_hist["DATA"] >= saldo_base_date].copy()
            if not sai_hist.empty:
                sai_hist = sai_hist[sai_hist["DATA_REF"] >= saldo_base_date].copy()
            if not trf_hist.empty:
                trf_hist = trf_hist[trf_hist["DATA"] >= saldo_base_date].copy()

        fluxo = compute_fluxo_caixa(ent_hist, sai_hist)

        # recorta para o período exibido (filtros atuais)
        if dt_ini and dt_fim and (not fluxo.empty):
            fluxo_disp = fluxo[(fluxo["DATA"] >= dt_ini) & (fluxo["DATA"] <= dt_fim)].copy()
        else:
            fluxo_disp = fluxo.copy()

        if fluxo_disp.empty:
            st.info("Sem dados suficientes para fluxo.")
        else:
            melt = fluxo_disp.melt(
                id_vars=["DATA"],
                value_vars=["ENTRADAS", "SAIDAS", "SALDO_DIA"],
                var_name="Métrica",
                value_name="Valor",
            )
            melt["Métrica"] = melt["Métrica"].replace(
                {"ENTRADAS": "Entradas", "SAIDAS": "Saídas", "SALDO_DIA": "Saldo do dia"}
            )
            chart = (
                alt.Chart(melt)
                .mark_line(point=True)
                .encode(
                    x=alt.X("DATA:T", title="Data", axis=alt.Axis(format="%d/%m")),
                    y=alt.Y("Valor:Q", title="R$"),
                    color=alt.Color("Métrica:N", legend=alt.Legend(title="")),
                    tooltip=[
                        alt.Tooltip("DATA:T", title="Data", format="%d/%m/%Y"),
                        "Métrica",
                        alt.Tooltip("Valor:Q", format=",.2f", title="R$"),
                    ],
                )
                .properties(height=320)
            )
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
                final_mov = float(fluxo_disp["SALDO_ACUM"].iloc[-1])
                badge = ("positivo", "good") if final_mov >= 0 else ("negativo", "bad")
                st_kpi("Saldo acumulado (mov.)", fmt_brl(final_mov), sub="Cumulativo", badge=badge)

            st.markdown("### Tabela do fluxo (por dia)")
            fluxo_tbl = fluxo_disp[["DATA", "ENTRADAS", "SAIDAS", "SALDO_DIA", "SALDO_ACUM"]].copy().sort_values("DATA")
            fluxo_tbl_show = fluxo_tbl.copy()
            for c in ["ENTRADAS", "SAIDAS", "SALDO_DIA", "SALDO_ACUM"]:
                fluxo_tbl_show[c] = fluxo_tbl_show[c].apply(fmt_brl)
            st.dataframe(fluxo_tbl_show, use_container_width=True, hide_index=True)

            # 3) Pagamentos x Vencimentos (saídas)
            if (not sai_f.empty) and ("VENCIMENTO" in sai_f.columns):
                st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
                st.markdown("### Pagamentos x Vencimentos (saídas)")

                dfp = sai_f.copy()
                dfp["VENC"] = pd.to_datetime(dfp["VENCIMENTO"], errors="coerce")
                dfp["PAG"] = pd.to_datetime(dfp["PAGAMENTO"], errors="coerce") if "PAGAMENTO" in dfp.columns else pd.NaT

                if dt_ini and dt_fim:
                    dt_ini_ts = pd.to_datetime(dt_ini)
                    dt_fim_ts = pd.to_datetime(dt_fim)
                    dfp = dfp[(dfp["VENC"].between(dt_ini_ts, dt_fim_ts)) | (dfp["PAG"].between(dt_ini_ts, dt_fim_ts))].copy()

                venc = dfp[dfp["VENC"].notna()].groupby("VENC")["VALOR"].sum().reset_index().rename(columns={"VENC": "DATA", "VALOR": "Vencimentos"})
                pag = dfp[dfp["PAG"].notna()].groupby("PAG")["VALOR"].sum().reset_index().rename(columns={"PAG": "DATA", "VALOR": "Pagamentos"})

                limite = pd.to_datetime(dt_fim) if dt_fim else pd.Timestamp.max
                aberto = (
                    dfp[(dfp["VENC"].notna()) & ((dfp["PAG"].isna()) | (dfp["PAG"] > limite))]
                    .groupby("VENC")["VALOR"].sum().reset_index()
                    .rename(columns={"VENC": "DATA", "VALOR": "Em aberto"})
                )

                pv = venc.merge(pag, on="DATA", how="outer").merge(aberto, on="DATA", how="outer").fillna(0.0).sort_values("DATA")
                pv_melt = pv.melt(id_vars=["DATA"], value_vars=["Em aberto", "Pagamentos", "Vencimentos"], var_name="Métrica", value_name="Valor")

                bars = alt.Chart(pv_melt).mark_bar().encode(
                    x=alt.X("DATA:T", title="Data", axis=alt.Axis(format="%d/%m")),
                    y=alt.Y("Valor:Q", title="R$"),
                    color=alt.Color("Métrica:N", legend=alt.Legend(title="")),
                    tooltip=[alt.Tooltip("DATA:T", title="Data", format="%d/%m/%Y"), "Métrica", alt.Tooltip("Valor:Q", format=",.2f", title="R$")],
                ).properties(height=320)
                st.altair_chart(bars, use_container_width=True)
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
