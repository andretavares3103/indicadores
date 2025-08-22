# -*- coding: utf-8 -*-
# -------------------------------------------------------------
# Vavivê — Dashboard de Indicadores (Streamlit)
# -------------------------------------------------------------
# Como usar com GitHub + Streamlit Cloud:
# 1) Crie um repositório com este arquivo como `app.py` (ou `Appindicadores.py`).
# 2) Inclua um `requirements.txt` com:
#    streamlit\npandas\nnumpy\nplotly\nopenpyxl\npython-dateutil
# 3) (Opcional) Suba também os arquivos .xlsx na raiz do repo com estes nomes:
#    - Clientes.xlsx (1ª aba)
#    - Profissionais.xlsx (1ª aba)
#    - Atendimentos_202507.xlsx (aba "Clientes")
#    - Receber_202507.xlsx (aba "Dados Financeiros")
#    - Repasses_202507.xlsx (aba "Dados Financeiros")
# 4) No Streamlit Cloud, aponte o app para este arquivo.
# -------------------------------------------------------------

import streamlit as st
import pandas as pd
import numpy as np
import unicodedata
from datetime import datetime, date
from dateutil import parser

# Plotly com fallback automático
try:
    import plotly.express as px
    USE_PLOTLY = True
except Exception:
    USE_PLOTLY = False
    st.warning("Plotly não está instalado. Usando gráficos nativos do Streamlit como fallback.")

st.set_page_config(
    page_title="Vavivê | Indicadores",
    page_icon="🧹",
    layout="wide",
    initial_sidebar_state="expanded",
)

# -------------------------------
# Helpers de normalização e carga
# -------------------------------

def _slug(s: str) -> str:
    if s is None:
        return ""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii")
    s = s.strip().lower()
    s = s.replace("/", " ")
    for ch in ["(", ")", "[", "]", ",", ";", ":", "-", "."]:
        s = s.replace(ch, " ")
    s = " ".join(s.split())
    return s.replace(" ", "_")


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [_slug(c) for c in df.columns]
    return df


def try_parse_date(x):
    if pd.isna(x):
        return pd.NaT
    if isinstance(x, (pd.Timestamp, datetime, date)):
        return pd.to_datetime(x)
    try:
        return pd.to_datetime(parser.parse(str(x), dayfirst=True, fuzzy=True))
    except Exception:
        return pd.NaT


def coalesce_inplace(df: pd.DataFrame, candidates: list[str], new: str) -> pd.DataFrame:
    """Cria/atualiza coluna `new` a partir da primeira coluna existente em candidates."""
    for c in candidates:
        if c in df.columns:
            df[new] = df[c]
            return df
    if new not in df.columns:
        df[new] = np.nan
    return df


def load_excel(uploaded_file, fallback_path=None, sheet=None) -> pd.DataFrame:
    """Carrega Excel do uploader ou caminho local. Sempre retorna **DataFrame**.
    Se `sheet` for None, pega a **primeira aba** automaticamente.
    """
    try:
        if uploaded_file is not None:
            if sheet is None:
                xls = pd.ExcelFile(uploaded_file)
                first = xls.sheet_names[0]
                return pd.read_excel(uploaded_file, sheet_name=first)
            else:
                return pd.read_excel(uploaded_file, sheet_name=sheet)
        if fallback_path is not None:
            if sheet is None:
                xls = pd.ExcelFile(fallback_path)
                first = xls.sheet_names[0]
                return pd.read_excel(fallback_path, sheet_name=first)
            else:
                return pd.read_excel(fallback_path, sheet_name=sheet)
        return pd.DataFrame()
    except Exception:
        return pd.DataFrame()

# -------------------------------
# Sidebar — Uploads e Filtros
# -------------------------------
with st.sidebar:
    st.markdown("## 📥 Arquivos de Dados")
    up_clientes = st.file_uploader("Clientes.xlsx", type=["xlsx"], key="clientes")
    up_prof     = st.file_uploader("Profissionais.xlsx", type=["xlsx"], key="prof")
    up_atend    = st.file_uploader("Atendimentos_2025MM.xlsx", type=["xlsx"], key="atend")
    up_receber  = st.file_uploader("Receber_2025MM.xlsx", type=["xlsx"], key="receber")
    up_repasses = st.file_uploader("Repasses_2025MM.xlsx", type=["xlsx"], key="repasses")
    st.caption("Se não enviar, o app tenta ler arquivos com esses nomes na pasta do repositório.")

# Carregar dados (com nomes e abas padrão já mapeados)
raw_clientes = load_excel(up_clientes, "Clientes.xlsx")
raw_prof     = load_excel(up_prof, "Profissionais.xlsx")
raw_atend    = load_excel(up_atend, "Atendimentos_202507.xlsx", sheet="Clientes")
raw_receber  = load_excel(up_receber, "Receber_202507.xlsx", sheet="Dados Financeiros")
raw_repasses = load_excel(up_repasses, "Repasses_202507.xlsx", sheet="Dados Financeiros")

# Normalizar colunas
cli = normalize_columns(raw_clientes) if not raw_clientes.empty else pd.DataFrame()
pro = normalize_columns(raw_prof)     if not raw_prof.empty     else pd.DataFrame()
atd = normalize_columns(raw_atend)    if not raw_atend.empty    else pd.DataFrame()
rec = normalize_columns(raw_receber)  if not raw_receber.empty  else pd.DataFrame()
rep = normalize_columns(raw_repasses) if not raw_repasses.empty else pd.DataFrame()

# -------------------------------
# Padronização de nomes essenciais
# -------------------------------
# CLIENTES
if not cli.empty:
    cli.rename(columns={
        "id": "cliente_id",
        "cpf": "cliente_cpf",
        "email": "cliente_email",
        "celular": "cliente_celular",
        "telefone": "cliente_telefone",
        "endereco_1_bairro": "bairro",
        "endereco_1_cidade": "cidade",
        "endereco_1_rua": "rua",
        "endereco_1_cep": "cep",
        "origem": "origem_cliente",
    }, inplace=True)

# PROFISSIONAIS
if not pro.empty:
    pro.rename(columns={
        "id": "prof_id",
        "nome": "prof_nome",
        "nome_prestador": "prof_nome",
        "cpf": "prof_cpf",
        "atendimentos_feitos": "att_feitos",
        "atendimentos_recusado": "att_recusados",
    }, inplace=True)
    # Endereço do profissional (coalesce genérico)
    coalesce_inplace(pro, ["endereco_1_rua", "endereco_rua", "rua", "logradouro"], "prof_rua")
    coalesce_inplace(pro, ["endereco_1_bairro", "endereco_bairro", "bairro"], "prof_bairro")
    coalesce_inplace(pro, ["endereco_1_cidade", "cidade"], "prof_cidade")
    coalesce_inplace(pro, ["endereco_1_cep", "cep"], "prof_cep")
    pro = pro.loc[:, ~pro.columns.duplicated()]

# ATENDIMENTOS (OS)
if not atd.empty:
    coalesce_inplace(atd, ["os", "os_id", "atendimento_id"], "os_id")
    coalesce_inplace(atd, ["data_1", "data", "data_do_atendimento", "data_atendimento"], "data_atendimento")
    atd["data_atendimento"] = atd["data_atendimento"].apply(try_parse_date)
    atd.rename(columns={
        "cliente": "cliente_nome",
        "cliente_novo?": "cliente_novo",
        "origem_venda": "origem_venda",
        "status_servico": "status_servico",
        "endereco_bairro": "bairro",
        "atendimento_bairro": "bairro",
        "atendimento_rua": "rua",
    }, inplace=True)
    # Valor do atendimento (se existir na OS)
    coalesce_inplace(atd, ["valor_atendimento", "valor", "valores", "procv_valores", "valor_total"], "valor_atendimento")
    atd = atd.loc[:, ~atd.columns.duplicated()]

# RECEBER (Contas a Receber)
if not rec.empty:
    coalesce_inplace(rec, ["atendimento_id", "os", "os_id"], "os_id")
    rec.rename(columns={
        "nome": "cliente_nome",
        "valor": "valor_recebido",
        "data_de_pagamento": "data_pagamento",
        "data_pagamento": "data_pagamento",
        "data_de_vencimento": "data_vencimento",
        "data_vencimento": "data_vencimento",
        "profissional_cpf": "prof_cpf",
        "profissional_celular": "prof_celular",
    }, inplace=True)
    # Harmonizar situacao/status
    if "situacao" in rec.columns and "status" in rec.columns:
        rec["situacao"] = rec["situacao"].fillna(rec["status"])
        rec.drop(columns=["status"], inplace=True)
    elif "situacao" not in rec.columns and "status" in rec.columns:
        rec.rename(columns={"status": "situacao"}, inplace=True)
    # Datas
    if "data_pagamento" in rec.columns:
        rec["data_pagamento"] = rec["data_pagamento"].apply(try_parse_date)
    if "data_vencimento" in rec.columns:
        rec["data_vencimento"] = rec["data_vencimento"].apply(try_parse_date)
    rec = rec.loc[:, ~rec.columns.duplicated()]

# REPASSES (Pagamentos às profissionais)
if not rep.empty:
    coalesce_inplace(rep, ["atendimento_id", "os", "os_id"], "os_id")
    rep.rename(columns={
        "nome": "profissional_nome",
        "profissional": "profissional_nome",
        "valor": "valor_repasse",
        "data_de_pagamento": "data_pagamento_repasse",
        "data_pagamento": "data_pagamento_repasse",
        "data_de_vencimento": "data_vencimento_repasse",
        "data_vencimento": "data_vencimento_repasse",
        "profissional_cpf": "prof_cpf",
        "cpf": "prof_cpf",
    }, inplace=True)
    if "situacao_repasse" not in rep.columns:
        if "situacao" in rep.columns:
            rep.rename(columns={"situacao": "situacao_repasse"}, inplace=True)
        elif "status" in rep.columns:
            rep.rename(columns={"status": "situacao_repasse"}, inplace=True)
    else:
        for _c in ["situacao", "status"]:
            if _c in rep.columns:
                rep.drop(columns=[_c], inplace=True)
    for dtc in ["data_pagamento_repasse", "data_vencimento_repasse"]:
        if dtc in rep.columns:
            rep[dtc] = rep[dtc].apply(try_parse_date)
    rep = rep.loc[:, ~rep.columns.duplicated()]

# -------------------------------
# Montagem Financeira: Receita x Repasse x Margem (por OS)
# -------------------------------
fin = pd.DataFrame()
if not rec.empty or not rep.empty:
    left = rec.copy() if not rec.empty else pd.DataFrame(columns=["os_id"])  # Contas a Receber
    right = rep.copy() if not rep.empty else pd.DataFrame(columns=["os_id"]) # Repasses

    left["os_id"] = left["os_id"].astype(str)
