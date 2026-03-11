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


def normalize_saldo_inicial(df: pd.DataFrame) -> Tuple[pd.DataFrame, Optional[date]]:
    if df.empty:
        return pd.DataFrame(columns=["BANCO", "SALDO_INICIAL"]), None

    x = df.copy()
    x.columns = [_norm_col(c) for c in x.columns]
    cols = list(x.columns)

    c_banco = pick_col(cols, "BANCO", "CONTA BANCARIA", "CONTA BANCÁRIA", "CONTA")
    c_saldo = pick_col(cols, "SALDO INICIAL", "SALDO", "VALOR", "R$")
    c_data = pick_col(cols, "DATA", "DATA BASE", "COMPETENCIA", "COMPETÊNCIA")

    if not c_banco or not c_saldo:
        return pd.DataFrame(columns=["BANCO", "SALDO_INICIAL"]), None

    out = pd.DataFrame()
    out["BANCO"] = x[c_banco].astype(str).map(_upper)
    out["SALDO_INICIAL"] = x[c_saldo].apply(money_to_float)
    out = out[(out["BANCO"] != "")].copy()

    saldo_base_date = None
    if c_data and c_data in x.columns:
        dates = x[c_data].apply(parse_date_any)
        dates = dates[dates.notna()]
        if not dates.empty:
            saldo_base_date = dates.iloc[0]

    return out, saldo_base_date


# ====================== CONCILIACAO ======================

def parse_conciliacao(df_raw: pd.DataFrame):
    """
    Lê a tabela principal da aba 7.
    Também localiza o quadro 'Saldo Acumulado Bancos'.

    Retorna:
    - conc_tbl_all: DIA, ENTRADAS, SAIDAS, SALDO_DIA, SALDO_ACUM
    - saldo_bancos_total
    - df_raw
    """
    if df_raw is None or df_raw.empty:
        return pd.DataFrame(columns=["DIA", "ENTRADAS", "SAIDAS", "SALDO_DIA", "SALDO_ACUM"]), None, pd.DataFrame()

    raw = df_raw.copy()
    raw = raw.fillna("")

    # tenta leitura com header normal
    x = raw.copy()
    x.columns = [_norm_col(c) for c in x.columns]
    cols = list(x.columns)

    c_dia = pick_col(cols, "DIA", "DIA DO MÊS", "DIA DO MES")
    c_ent = pick_col(cols, "ENTRADAS")
    c_sai = pick_col(cols, "SAIDAS", "SAÍDAS")
    c_saldo_dia = pick_col(cols, "SALDO DO DIA", "SALDO DIA")
    c_saldo_acum = pick_col(cols, "SALDO ACUMULADO MÊS", "SALDO ACUMULADO MES", "SALDO ACUMULADO")

    conc_tbl = pd.DataFrame(columns=["DIA", "ENTRADAS", "SAIDAS", "SALDO_DIA", "SALDO_ACUM"])

    if c_dia and c_ent and c_sai and c_saldo_dia and c_saldo_acum:
        conc_tbl = pd.DataFrame({
            "DIA": pd.to_numeric(x[c_dia], errors="coerce"),
            "ENTRADAS": x[c_ent].apply(money_to_float),
            "SAIDAS": x[c_sai].apply(money_to_float),
            "SALDO_DIA": x[c_saldo_dia].apply(money_to_float),
            "SALDO_ACUM": x[c_saldo_acum].apply(money_to_float),
        })
        conc_tbl = conc_tbl.dropna(subset=["DIA"]).copy()
        if not conc_tbl.empty:
            conc_tbl["DIA"] = conc_tbl["DIA"].astype(int)
            conc_tbl = conc_tbl.sort_values("DIA")

    # quadro saldo acumulado bancos
    saldo_bancos_total = None
    arr = raw.astype(str).values.tolist()
    for i, row in enumerate(arr):
        for j, cell in enumerate(row):
            txt = _upper(cell)
            if "SALDO ACUMULADO" in txt and "BANCO" in txt:
                if j + 1 < len(row):
                    saldo_bancos_total = money_to_float(row[j + 1])
                    break
        if saldo_bancos_total is not None:
            break

    return conc_tbl, saldo_bancos_total, raw


def get_conc_last_row_value(conc_tbl: pd.DataFrame, dt_ini: Optional[date], dt_fim: Optional[date], ym_sels: List[str]) -> Optional[float]:
    """
    Usa a ÚLTIMA LINHA DA TABELA da aba 7 para representar o saldo acumulado filtrado.
    Isso mantém o comportamento por banco, porque a própria planilha já recalcula a tabela
    quando o filtro de banco está ativo no Google Sheets.
    """
    if conc_tbl is None or conc_tbl.empty:
        return None
    if len(ym_sels) != 1:
        return None

    try:
        y_sel = int(ym_sels[0][:4])
        m_sel = int(ym_sels[0][5:7])
        x = conc_tbl.copy()
        x["DATA"] = x["DIA"].apply(lambda d: date(y_sel, m_sel, int(d)))

        if dt_ini and dt_fim:
            x = x[(x["DATA"] >= dt_ini) & (x["DATA"] <= dt_fim)].copy()

        if x.empty:
            return None

        x = x.sort_values("DATA")
        return float(x["SALDO_ACUM"].iloc[-1])
    except Exception:
        return None


# ====================== SALDO POR BANCO / FLUXO ======================

def compute_saldo_bancos(
    ent_hist: pd.DataFrame,
    sai_hist: pd.DataFrame,
    trf_hist: pd.DataFrame,
    df_saldo_ini: pd.DataFrame,
    saldo_base_date: Optional[date],
):
    """
    Calcula movimento diário e saldo por banco.
    """
    bancos = set()

    if ent_hist is not None and not ent_hist.empty and "BANCO" in ent_hist.columns:
        bancos.update(ent_hist["BANCO"].dropna().astype(str).map(_upper).tolist())
    if sai_hist is not None and not sai_hist.empty and "BANCO" in sai_hist.columns:
        bancos.update(sai_hist["BANCO"].dropna().astype(str).map(_upper).tolist())
    if trf_hist is not None and not trf_hist.empty:
        if "ORIGEM" in trf_hist.columns:
            bancos.update(trf_hist["ORIGEM"].dropna().astype(str).map(_upper).tolist())
        if "DESTINO" in trf_hist.columns:
            bancos.update(trf_hist["DESTINO"].dropna().astype(str).map(_upper).tolist())
    if df_saldo_ini is not None and not df_saldo_ini.empty and "BANCO" in df_saldo_ini.columns:
        bancos.update(df_saldo_ini["BANCO"].dropna().astype(str).map(_upper).tolist())

    bancos = sorted([b for b in bancos if b])

    if not bancos:
        return pd.DataFrame(), pd.DataFrame()

    # faixa de datas do histórico
    datas = []
    if ent_hist is not None and not ent_hist.empty:
        datas.extend([d for d in ent_hist["DATA"].dropna().tolist()])
    if sai_hist is not None and not sai_hist.empty:
        datas.extend([d for d in sai_hist["DATA_REF"].dropna().tolist()])
    if trf_hist is not None and not trf_hist.empty:
        datas.extend([d for d in trf_hist["DATA"].dropna().tolist()])

    if not datas:
        return pd.DataFrame(), pd.DataFrame()

    dmin = min(datas)
    dmax = max(datas)
    date_range = pd.date_range(dmin, dmax, freq="D").date

    base = pd.MultiIndex.from_product([date_range, bancos], names=["DATA", "BANCO"]).to_frame(index=False)

    ent_daily = pd.DataFrame(columns=["DATA", "BANCO", "ENTRADAS"])
    if ent_hist is not None and not ent_hist.empty:
        ent_daily = (
            ent_hist.groupby(["DATA", "BANCO"], as_index=False)["VALOR"]
            .sum()
            .rename(columns={"VALOR": "ENTRADAS"})
        )

    sai_daily = pd.DataFrame(columns=["DATA", "BANCO", "SAIDAS"])
    if sai_hist is not None and not sai_hist.empty:
        sai_daily = (
            sai_hist.groupby(["DATA_REF", "BANCO"], as_index=False)["VALOR"]
            .sum()
            .rename(columns={"DATA_REF": "DATA", "VALOR": "SAIDAS"})
        )

    trf_out = pd.DataFrame(columns=["DATA", "BANCO", "TRF_OUT"])
    trf_in = pd.DataFrame(columns=["DATA", "BANCO", "TRF_IN"])
    if trf_hist is not None and not trf_hist.empty:
        if "ORIGEM" in trf_hist.columns:
            trf_out = (
                trf_hist.groupby(["DATA", "ORIGEM"], as_index=False)["VALOR"]
                .sum()
                .rename(columns={"ORIGEM": "BANCO", "VALOR": "TRF_OUT"})
            )
        if "DESTINO" in trf_hist.columns:
            trf_in = (
                trf_hist.groupby(["DATA", "DESTINO"], as_index=False)["VALOR"]
                .sum()
                .rename(columns={"DESTINO": "BANCO", "VALOR": "TRF_IN"})
            )

    mv = base.merge(ent_daily, on=["DATA", "BANCO"], how="left")
    mv = mv.merge(sai_daily, on=["DATA", "BANCO"], how="left")
    mv = mv.merge(trf_out, on=["DATA", "BANCO"], how="left")
    mv = mv.merge(trf_in, on=["DATA", "BANCO"], how="left")
    mv[["ENTRADAS", "SAIDAS", "TRF_OUT", "TRF_IN"]] = mv[["ENTRADAS", "SAIDAS", "TRF_OUT", "TRF_IN"]].fillna(0.0)

    mv["SALDO_MOV"] = mv["ENTRADAS"] - mv["SAIDAS"] - mv["TRF_OUT"] + mv["TRF_IN"]

    saldo_map = {}
    if df_saldo_ini is not None and not df_saldo_ini.empty:
        tmp = df_saldo_ini.copy()
        tmp["BANCO"] = tmp["BANCO"].astype(str).map(_upper)
        saldo_map = dict(zip(tmp["BANCO"], tmp["SALDO_INICIAL"]))

    mv["SALDO_INICIAL"] = mv["BANCO"].map(lambda b: float(saldo_map.get(b, 0.0)))

    mv = mv.sort_values(["BANCO", "DATA"]).copy()
    mv["SALDO_REAL"] = 0.0

    for banco in bancos:
        mask = mv["BANCO"] == banco
        ini = float(saldo_map.get(banco, 0.0))
        mv.loc[mask, "SALDO_REAL"] = ini + mv.loc[mask, "SALDO_MOV"].cumsum()

    resumo = (
        mv.groupby("BANCO", as_index=False)
        .agg(
            SALDO_INICIAL=("SALDO_INICIAL", "max"),
            SALDO_MOV=("SALDO_MOV", "sum"),
            SALDO_REAL_FINAL=("SALDO_REAL", "last"),
        )
        .sort_values("BANCO")
    )

    return mv, resumo


def build_fluxo_total_from_mv(mv_banks_daily: pd.DataFrame, banco_sel: List[str], dt_ini: Optional[date], dt_fim: Optional[date]):
    if mv_banks_daily is None or mv_banks_daily.empty:
        return pd.DataFrame(columns=["DATA", "ENTRADAS", "SAIDAS", "SALDO_DIA", "SALDO_REAL"])

    x = mv_banks_daily.copy()

    if banco_sel:
        bset = [_upper(x) for x in banco_sel]
        x = x[x["BANCO"].isin(bset)].copy()

    if dt_ini and dt_fim:
        x = x[(x["DATA"] >= dt_ini) & (x["DATA"] <= dt_fim)].copy()

    if x.empty:
        return pd.DataFrame(columns=["DATA", "ENTRADAS", "SAIDAS", "SALDO_DIA", "SALDO_REAL"])

    fluxo = (
        x.groupby("DATA", as_index=False)[["ENTRADAS", "SAIDAS"]]
        .sum()
        .sort_values("DATA")
    )
    fluxo["SALDO_DIA"] = fluxo["ENTRADAS"] - fluxo["SAIDAS"]

    saldo_real = (
        x.groupby("DATA", as_index=False)["SALDO_REAL"]
        .sum()
        .sort_values("DATA")
    )

    fluxo = fluxo.merge(saldo_real, on="DATA", how="left")
    return fluxo


# ====================== LOAD RAW ======================
df_ent_raw = read_tab(SHEET_ID, TAB_ENT)
df_sai_raw = read_tab(SHEET_ID, TAB_SAI)
df_trf_raw = read_tab(SHEET_ID, TAB_TRF)
df_conc_raw = read_tab(SHEET_ID, TAB_CONC)
df_saldo_ini_raw = read_tab(SHEET_ID, TAB_SALDO_INI)

# ====================== NORMALIZED ======================
df_ent = normalize_entradas(df_ent_raw)
df_sai = normalize_saidas(df_sai_raw)
df_trf = normalize_transferencias(df_trf_raw)
df_saldo_ini, saldo_base_date = normalize_saldo_inicial(df_saldo_ini_raw)

conc_tbl_all, saldo_bancos_total, df_conc_raw = parse_conciliacao(df_conc_raw)

# ====================== MONTHS ======================
months = sorted(
    set(
        [m for m in df_ent["YM"].dropna().tolist()] +
        [m for m in df_sai["YM"].dropna().tolist()] +
        [m for m in df_trf["YM"].dropna().tolist()]
    )
)

if not months:
    st.warning("Não encontrei meses válidos nas abas financeiras.")
    st.stop()

ym_focus = months[-1]
# ====================== SIDEBAR ======================
with st.sidebar:
    st.markdown("## Navegação")
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

    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
    st.markdown("## Filtros")

    ym_focus = st.selectbox(
        "Mês foco",
        options=months,
        index=len(months) - 1,
        format_func=month_label,
    )

    ym_sels = st.multiselect(
        "Meses para comparação",
        options=months,
        default=[ym_focus],
        format_func=month_label,
    )
    if not ym_sels:
        ym_sels = [ym_focus]

    # período interno ao mês foco
    y0, m0 = int(ym_focus[:4]), int(ym_focus[5:7])
    start_default = date(y0, m0, 1)
    if m0 == 12:
        end_default = date(y0 + 1, 1, 1) - timedelta(days=1)
    else:
        end_default = date(y0, m0 + 1, 1) - timedelta(days=1)

    dt_range = st.date_input(
        "Período dentro do mês foco",
        value=(start_default, end_default),
        format="DD/MM/YYYY",
    )
    if isinstance(dt_range, (tuple, list)) and len(dt_range) == 2:
        dt_ini, dt_fim = dt_range
    else:
        dt_ini, dt_fim = start_default, end_default

    c1, c2 = st.columns(2)
    with c1:
        capt_opts = (
            sorted(df_ent[df_ent["YM"].isin(ym_sels)]["CAPTACAO"].dropna().astype(str).map(_upper).unique().tolist())
            if (not df_ent.empty and "CAPTACAO" in df_ent.columns)
            else []
        )
        capt_sel = st.multiselect("Captação", options=capt_opts, default=[])

    with c2:
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

# flags auxiliares
inv_mask = pd.Series(False, index=sai_f.index)
if not sai_f.empty and "CONTA" in sai_f.columns:
    inv_mask = sai_f["CONTA"].astype(str).map(_upper).str.contains("INVEST", na=False)

# ====================== HEADER ======================
st.markdown(f"# {COMPANY_NAME}")
st.caption("Dashboard Financeiro conectado ao Google Sheets")

# ====================== PAGES ======================
if page.startswith("📊"):
    st.markdown("<div class='hr'></div>", unsafe_allow_html=True)

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

    st.markdown("### Resumo por banco")
    if banco_opts:
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

        sai_hist = df_sai[df_sai["YM"].isin(hist_months)].copy()
        if banco_sel and ("BANCO" in sai_hist.columns):
            sai_hist = sai_hist[sai_hist["BANCO"].isin([_upper(x) for x in banco_sel])].copy()

        ent_hist = df_ent[df_ent["YM"].isin(hist_months)].copy()
        if capt_sel and ("CAPTACAO" in ent_hist.columns):
            ent_hist = ent_hist[ent_hist["CAPTACAO"].isin([_upper(x) for x in capt_sel])].copy()
        if banco_sel and ("BANCO" in ent_hist.columns):
            ent_hist = ent_hist[ent_hist["BANCO"].isin([_upper(x) for x in banco_sel])].copy()

        t = (
            sai_hist.groupby(["CONTA", "YM"])["VALOR"]
            .sum()
            .reset_index()
        )
        piv = t.pivot(index="CONTA", columns="YM", values="VALOR").fillna(0.0).reset_index()

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

    # 1) saldo filtrado = última linha da tabela da aba 7
    saldo_filtro_conc = get_conc_last_row_value(conc_tbl_all, dt_ini, dt_fim, ym_sels)

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
        st.caption("Fonte do saldo acumulado: **7. Conciliação**.")

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
            valor_filtro = saldo_filtro_conc if saldo_filtro_conc is not None else float(fluxo_disp["SALDO_ACUM"].iloc[-1])
            badge = ("positivo", "good") if valor_filtro >= 0 else ("negativo", "bad")
            st_kpi("Saldo acumulado (filtro)", fmt_brl(valor_filtro), sub="Última linha da tabela", badge=badge)

        with cE:
            if saldo_bancos_total is not None:
                badge = ("positivo", "good") if saldo_bancos_total >= 0 else ("negativo", "bad")
                st_kpi("Saldo acumulado (todos)", fmt_brl(saldo_bancos_total), sub="Quadro Saldo Acumulado Bancos", badge=badge)

        st.markdown("### Tabela do fluxo (por dia)")
        fluxo_tbl_show = fluxo_disp.copy()
        for c in ["ENTRADAS", "SAIDAS", "SALDO_DIA", "SALDO_ACUM"]:
            fluxo_tbl_show[c] = fluxo_tbl_show[c].apply(fmt_brl)
        st.dataframe(fluxo_tbl_show, use_container_width=True, hide_index=True)

        # não dá stop aqui; fallback continua disponível para saldo por banco detalhado

    # 2) fallback para consolidado + individual por banco
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
            elif "ORIGEM" in trf_hist.columns:
                trf_hist = trf_hist[trf_hist["ORIGEM"].isin(bset)].copy()
            elif "DESTINO" in trf_hist.columns:
                trf_hist = trf_hist[trf_hist["DESTINO"].isin(bset)].copy()

    mv_banks_daily, resumo_banks = compute_saldo_bancos(ent_hist, sai_hist, trf_hist, df_saldo_ini, saldo_base_date)
    fluxo_disp2 = build_fluxo_total_from_mv(mv_banks_daily, banco_sel, dt_ini, dt_fim)

    if fluxo_disp2.empty:
        if conc_tbl is None or conc_tbl.empty:
            st.info("Sem dados suficientes para exibir o fluxo de caixa neste filtro.")
    else:
        st.markdown("<div class='hr'></div>", unsafe_allow_html=True)
        st.markdown("### Fluxo calculado pelo histórico")

        melt = fluxo_disp2.melt(
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
            st_kpi("Entradas", fmt_brl(fluxo_disp2["ENTRADAS"].sum()), sub="Somatório no período")
        with cB:
            st_kpi("Saídas", fmt_brl(fluxo_disp2["SAIDAS"].sum()), sub="Somatório no período")
        with cC:
            saldo = float(fluxo_disp2["SALDO_DIA"].sum())
            badge = ("positivo", "good") if saldo >= 0 else ("negativo", "bad")
            st_kpi("Saldo no período", fmt_brl(saldo), sub="Entradas - Saídas", badge=badge)
        with cD:
            final_real = float(fluxo_disp2.sort_values("DATA")["SALDO_REAL"].iloc[-1])
            badge = ("positivo", "good") if final_real >= 0 else ("negativo", "bad")
            st_kpi("Saldo acumulado", fmt_brl(final_real), sub="Saldo real (carryover)", badge=badge)

        st.markdown("### Tabela do fluxo (por dia)")
        fluxo_tbl_show = fluxo_disp2.copy().sort_values("DATA")
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
