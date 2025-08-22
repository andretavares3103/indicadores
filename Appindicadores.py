# -*- coding: utf-8 -*-
# -------------------------------------------------------------
# Vavivê — Dashboard de Indicadores (Streamlit)
# -------------------------------------------------------------
# Como usar com GitHub + Streamlit Cloud:
# 1) Crie um repositório com este arquivo como `app.py`.
# 2) Inclua um `requirements.txt` com:
#    streamlit\npandas\nnumpy\nplotly\nopenpyxl\npython-dateutil
# 3) (Opcional) Suba também os arquivos .xlsx na raiz do repo com estes nomes:
#    - Clientes.xlsx
#    - Profissionais.xlsx
#    - Atendimentos_202507.xlsx
#    - Receber_202507.xlsx
#    - Repasses_202507.xlsx
# 4) No Streamlit Cloud, aponte o app para `app.py`.
# -------------------------------------------------------------

import streamlit as st
import pandas as pd
import numpy as np
import unicodedata
from datetime import datetime, date
from dateutil import parser
try:
    import plotly.express as px
    USE_PLOTLY = True
except Exception:
    USE_PLOTLY = False

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


def coalesce_cols(df: pd.DataFrame, candidates: list[str], new: str) -> pd.DataFrame:
    """Cria coluna `new` a partir da primeira coluna existente em candidates."""
    for c in candidates:
        if c in df.columns:
            df[new] = df[c]
            return df
    df[new] = np.nan
    return df


def load_excel(uploaded_file, fallback_path=None, sheet=None) -> pd.DataFrame:
    """Carrega Excel do uploader ou caminho local.
    Se `sheet` for None, sempre carrega a **primeira aba** e retorna um DataFrame.
    Evita retornar dict (com todas as abas), o que quebraria `.empty`.
    """
    try:
        # Prioridade: arquivo enviado
        if uploaded_file is not None:
            if sheet is None:
                xls = pd.ExcelFile(uploaded_file)
                first = xls.sheet_names[0]
                return pd.read_excel(uploaded_file, sheet_name=first)
            else:
                return pd.read_excel(uploaded_file, sheet_name=sheet)
        # Fallback: arquivo local
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

# Carregar dados (com nomes e abas padrão já mapeados na sua base)
# Clientes.xlsx -> primeira aba
raw_clientes = load_excel(up_clientes, "Clientes.xlsx")
# Profissionais.xlsx -> primeira aba
raw_prof     = load_excel(up_prof, "Profissionais.xlsx")
# Atendimentos -> aba "Clientes" (a planilha de OS)
raw_atend    = load_excel(up_atend, "Atendimentos_202507.xlsx", sheet="Clientes")
# Receber -> aba "Dados Financeiros"
raw_receber  = load_excel(up_receber, "Receber_202507.xlsx", sheet="Dados Financeiros")
# Repasses -> aba "Dados Financeiros"
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
# Clientes
if not cli.empty:
    rename_cli = {
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
    }
    # lidar com colunas tipo "endereco-1-bairro" -> já viram como endereco_1_bairro após normalize
    cli.rename(columns={c: rename_cli[c] for c in rename_cli if c in cli.columns}, inplace=True)

# Profissionais
if not pro.empty:
    rename_pro = {
        "id": "prof_id",
        "nome": "prof_nome",
        "nome_prestador": "prof_nome",
        "cpf": "prof_cpf",
        "atendimentos_feitos": "att_feitos",
        "atendimentos_recusado": "att_recusados",
        "zona": "zona",
        "status_profissional": "status_profissional",
    }
    pro.rename(columns={c: rename_pro[c] for c in rename_pro if c in pro.columns}, inplace=True)

# Atendimentos (OS)
if not atd.empty:
    # Coalesce chaves e datas
    atd = coalesce_cols(atd, ["os", "os_id", "atendimento_id"], "os_id")
    atd = coalesce_cols(atd, ["data_1", "data", "data_do_atendimento", "data_atendimento"], "data_atendimento")
    atd["data_atendimento"] = atd["data_atendimento"].apply(try_parse_date)

    rename_atd = {
        "cliente": "cliente_nome",
        "cliente_novo?": "cliente_novo",
        "origem_cliente": "origem_cliente",
        "origem_venda": "origem_venda",
        "status_servico": "status_servico",
        "repasse": "repasse_placeholder",  # algumas versões tinham coluna, mas usaremos planilha de repasses
        "endereco": "endereco",
        "endereco_bairro": "bairro",
        "atendimento_bairro": "bairro",
        "atendimento_rua": "rua",
    }
    atd.rename(columns={c: rename_atd[c] for c in rename_atd if c in atd.columns}, inplace=True)

# Receber (Contas a Receber)
if not rec.empty:
    rec = coalesce_cols(rec, ["atendimento_id", "os", "os_id"], "os_id")
    rename_rec = {
        "nome": "cliente_nome",
        "valor": "valor_recebido",
        "data_de_pagamento": "data_pagamento",
        "data_pagamento": "data_pagamento",
        "data_de_vencimento": "data_vencimento",
        "data_vencimento": "data_vencimento",
    }
    rec.rename(columns={c: rename_rec[c] for c in rename_rec if c in rec.columns}, inplace=True)

    # Harmonizar situação/status evitando colunas duplicadas
    if "situacao" in rec.columns and "status" in rec.columns:
        rec["situacao"] = rec["situacao"].fillna(rec["status"])
        rec.drop(columns=["status"], inplace=True)
    elif "situacao" not in rec.columns and "status" in rec.columns:
        rec.rename(columns={"status": "situacao"}, inplace=True)

    rec["data_pagamento"] = rec["data_pagamento"].apply(try_parse_date) if "data_pagamento" in rec.columns else pd.NaT
    rec["data_vencimento"] = rec["data_vencimento"].apply(try_parse_date) if "data_vencimento" in rec.columns else pd.NaT

    # Remover colunas duplicadas, mantendo a primeira ocorrência
    rec = rec.loc[:, ~rec.columns.duplicated()]

# Repasses (Pagamentos às profissionais)
if not rep.empty:
    rep = coalesce_cols(rep, ["atendimento_id", "os", "os_id"], "os_id")
    rename_rep = {
        "nome": "profissional_nome",
        "valor": "valor_repasse",
        "data_de_pagamento": "data_pagamento_repasse",
        "data_pagamento": "data_pagamento_repasse",
        "data_de_vencimento": "data_vencimento_repasse",
        "data_vencimento": "data_vencimento_repasse",
        "profissional": "profissional_nome",
    }
    rep.rename(columns={c: rename_rep[c] for c in rename_rep if c in rep.columns}, inplace=True)

    # Harmonizar situacao/status do repasse evitando duplicatas
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

    # Remover colunas duplicadas, mantendo a primeira ocorrência
    rep = rep.loc[:, ~rep.columns.duplicated()]

# -------------------------------
# Montagem Financeira: Receita x Repasse x Margem
# -------------------------------
fin = pd.DataFrame()
if not rec.empty or not rep.empty:
    left = rec.copy() if not rec.empty else pd.DataFrame(columns=["os_id"])  # Contas a Receber
    right = rep.copy() if not rep.empty else pd.DataFrame(columns=["os_id"]) # Repasses

    # garantias de tipo da chave
    left["os_id"] = left["os_id"].astype(str)
    right["os_id"] = right["os_id"].astype(str)

    fin = pd.merge(left, right[[c for c in ["os_id", "valor_repasse", "situacao_repasse", "profissional_nome", "data_pagamento_repasse", "data_vencimento_repasse"] if c in right.columns]],
                   on="os_id", how="outer", suffixes=("_rec", "_rep"))
    if "valor_recebido" not in fin.columns:
        fin["valor_recebido"] = np.nan
    if "valor_repasse" not in fin.columns:
        fin["valor_repasse"] = np.nan
    fin["mc"] = fin["valor_recebido"].fillna(0) - fin["valor_repasse"].fillna(0)

# -------------------------------
# Filtros Globais por Data
# -------------------------------
# Tenta inferir o range com base em colunas de data disponíveis
all_dates = []
for _df, cols in [
    (atd, ["data_atendimento"]),
    (rec, ["data_pagamento", "data_vencimento"]),
    (rep, ["data_pagamento_repasse", "data_vencimento_repasse"]),
]:
    if not _df.empty:
        for c in cols:
            if c in _df.columns:
                vals = pd.to_datetime(_df[c], errors="coerce")
                all_dates.extend(list(vals.dropna()))

if all_dates:
    dmin = min(all_dates).date()
    dmax = max(all_dates).date()
else:
    today = date.today()
    dmin = date(today.year, 1, 1)
    dmax = today

with st.sidebar:
    st.markdown("---")
    st.markdown("## 🗓️ Período")
    sel_ini, sel_fim = st.date_input("Selecione o intervalo", value=(dmin, dmax))

# Aplicar filtros de data
if not atd.empty and "data_atendimento" in atd.columns:
    atd_f = atd[(atd["data_atendimento"] >= pd.to_datetime(sel_ini)) & (atd["data_atendimento"] <= pd.to_datetime(sel_fim))]
else:
    atd_f = atd.copy()

if not rec.empty:
    # considerar pagamento quando existir; senão, vencimento
    dt_rec = rec.copy()
    if "data_pagamento" in dt_rec.columns and dt_rec["data_pagamento"].notna().any():
        dt_rec["_data_fin"] = dt_rec["data_pagamento"].fillna(dt_rec.get("data_vencimento"))
    else:
        dt_rec["_data_fin"] = dt_rec.get("data_vencimento")
    rec_f = dt_rec[(pd.to_datetime(dt_rec["_data_fin"], errors="coerce") >= pd.to_datetime(sel_ini)) & (pd.to_datetime(dt_rec["_data_fin"], errors="coerce") <= pd.to_datetime(sel_fim))]
else:
    rec_f = rec.copy()

if not rep.empty:
    dt_rep = rep.copy()
    base_col = "data_pagamento_repasse" if "data_pagamento_repasse" in dt_rep.columns else "data_vencimento_repasse"
    if base_col in dt_rep.columns:
        rep_f = dt_rep[(pd.to_datetime(dt_rep[base_col], errors="coerce") >= pd.to_datetime(sel_ini)) & (pd.to_datetime(dt_rep[base_col], errors="coerce") <= pd.to_datetime(sel_fim))]
    else:
        rep_f = rep.copy()
else:
    rep_f = rep.copy()

fin_f = fin.copy()
# filtrar fin_f usando datas de rec/rep
if not fin_f.empty:
    # cria proxy de data consolidada
    fin_f["_data"] = pd.NaT
    if "data_pagamento" in fin_f.columns:
        fin_f["_data"] = fin_f["data_pagamento"].fillna(fin_f.get("data_vencimento"))
    if "data_pagamento_repasse" in fin_f.columns:
        fin_f["_data"] = fin_f["_data"].fillna(fin_f["data_pagamento_repasse"]).fillna(fin_f.get("data_vencimento_repasse"))
    fin_f = fin_f[(pd.to_datetime(fin_f["_data"], errors="coerce") >= pd.to_datetime(sel_ini)) & (pd.to_datetime(fin_f["_data"], errors="coerce") <= pd.to_datetime(sel_fim))]

# -------------------------------
# UI — TABS
# -------------------------------
st.title("Indicadores — Vavivê")

if all([df.empty for df in [cli, pro, atd, rec, rep]]):
    st.info("Envie ou inclua os arquivos .xlsx solicitados para visualizar os indicadores.")

aba = st.tabs([
    "📋 Visão Geral",
    "👥 Clientes & Regiões",
    "🧑‍💼 Profissionais",
    "🧹 Atendimentos",
    "💰 Financeiro (Receber & Repasses)",
])

# ---------------------------------
# 📋 Visão Geral
# ---------------------------------
with aba[0]:
    st.subheader("KPIs do Período")

    # Clientes
    total_clientes = int(cli.shape[0]) if not cli.empty else 0

    # Profissionais
    total_prof = int(pro.shape[0]) if not pro.empty else 0

    # Atendimentos
    concl = int(atd_f[atd_f.get("status_servico").fillna("").str.lower().eq("concluido")].shape[0]) if not atd_f.empty and "status_servico" in atd_f.columns else 0
    agend = int(atd_f[atd_f.get("status_servico").fillna("").str.lower().eq("agendado")].shape[0]) if not atd_f.empty and "status_servico" in atd_f.columns else 0
    canc  = int(atd_f[atd_f.get("status_servico").fillna("").str.lower().eq("cancelado")].shape[0]) if not atd_f.empty and "status_servico" in atd_f.columns else 0

    # Financeiro
    receita = float(rec_f.get("valor_recebido").sum()) if not rec_f.empty and "valor_recebido" in rec_f.columns else 0.0
    repasses = float(rep_f.get("valor_repasse").sum()) if not rep_f.empty and "valor_repasse" in rep_f.columns else 0.0
    mc_total = float(fin_f.get("mc").sum()) if not fin_f.empty and "mc" in fin_f.columns else (receita - repasses)

    # Métricas em colunas
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Clientes (cadastro)", f"{total_clientes:,}".replace(",", "."))
    c2.metric("Profissionais (cadastro)", f"{total_prof:,}".replace(",", "."))
    c3.metric("Atendimentos Concluídos", f"{concl:,}".replace(",", "."))
    c4.metric("Agendados", f"{agend:,}".replace(",", "."))
    c5.metric("Cancelados", f"{canc:,}".replace(",", "."))
    c6.metric("Margem de Contribuição", f"R$ {mc_total:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

    st.markdown("---")
    st.caption("A margem de contribuição é calculada como **Receita (Contas a Receber)** menos **Repasses às Profissionais**.")

# ---------------------------------
# 👥 Clientes & Regiões
# ---------------------------------
with aba[1]:
    st.subheader("Clientes")

    if cli.empty:
        st.warning("Base de Clientes não carregada.")
    else:
        # Origem
        col_origem = None
        for c in ["origem_cliente", "origem"]:
            if c in cli.columns:
                col_origem = c
                break
        if col_origem:
            origem_counts = (
                cli[col_origem]
                .fillna("(não informado)")
                .replace({"": "(não informado)"})
                .value_counts()
                .reset_index()
            )
            origem_counts.columns = ["origem", "quantidade"]
            if USE_PLOTLY:
                fig = px.bar(origem_counts, x="origem", y="quantidade", title="Origem dos Clientes", text_auto=True)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.bar_chart(origem_counts.set_index("origem")["quantidade"])
        else:
            st.info("Coluna de origem do cliente não encontrada.")

        st.markdown("---")
        st.subheader("Regiões")
        # Bairro / Cidade
        col_bairro = None
        for c in ["bairro", "endereco_bairro", "endereco-1-bairro"]:
            cc = _slug(c)
            if cc in cli.columns:
                col_bairro = cc
                break
        col_cidade = "cidade" if "cidade" in cli.columns else None

        cols = st.columns(2)
        if col_bairro:
            bairro_counts = cli[col_bairro].fillna("(sem bairro)").astype(str)
            bairro_counts = bairro_counts.replace({"": "(sem bairro)"}).value_counts().reset_index()
            bairro_counts.columns = ["bairro", "clientes"]
            if USE_PLOTLY:
                fig_b = px.bar(bairro_counts.head(20), x="bairro", y="clientes", title="Top Bairros por Clientes", text_auto=True)
                cols[0].plotly_chart(fig_b, use_container_width=True)
            else:
                cols[0].bar_chart(bairro_counts.set_index("bairro")["clientes"])
        else:
            cols[0].info("Coluna de bairro não encontrada.")

        if col_cidade:
            cidade_counts = cli[col_cidade].fillna("(sem cidade)").astype(str)
            cidade_counts = cidade_counts.replace({"": "(sem cidade)"}).value_counts().reset_index()
            cidade_counts.columns = ["cidade", "clientes"]
            if USE_PLOTLY:
                fig_c = px.bar(cidade_counts, x="cidade", y="clientes", title="Clientes por Cidade", text_auto=True)
                cols[1].plotly_chart(fig_c, use_container_width=True)
            else:
                cols[1].bar_chart(cidade_counts.set_index("cidade")["clientes"])
        else:
            cols[1].info("Coluna de cidade não encontrada.")

# ---------------------------------
# 🧑‍💼 Profissionais
# ---------------------------------
with aba[2]:
    st.subheader("Profissionais")

    if pro.empty and atd_f.empty:
        st.warning("Bases de Profissionais e Atendimentos não carregadas.")
    else:
        cols = st.columns(3)
        total_prof = int(pro.shape[0]) if not pro.empty else 0
        cols[0].metric("Total de Profissionais (cadastro)", f"{total_prof:,}".replace(",", "."))

        # Produtividade por profissional a partir dos atendimentos concluídos
        if not atd_f.empty and "status_servico" in atd_f.columns:
            concluidos = atd_f[atd_f["status_servico"].str.lower() == "concluido"].copy()
            # tentar encontrar coluna que liga profissional (se existir), senão usar ranking por cliente/endereco
            # Muitas bases não trazem ID da profissional por atendimento; então mostramos apenas total de concluidos
            cols[1].metric("Atendimentos Concluídos (período)", f"{concluidos.shape[0]:,}".replace(",", "."))
        else:
            cols[1].metric("Atendimentos Concluídos (período)", "0")

        # Taxa de recusa (se existir na base de profissionais)
        if not pro.empty and {"att_feitos", "att_recusados"}.issubset(set(pro.columns)):
            feitos = pro["att_feitos"].fillna(0).astype(float).sum()
            recusados = pro["att_recusados"].fillna(0).astype(float).sum()
            taxa_recusa = (recusados / (feitos + recusados) * 100) if (feitos + recusados) > 0 else 0
            cols[2].metric("Taxa de Recusa (cadastro)", f"{taxa_recusa:.1f}%")
        else:
            cols[2].metric("Taxa de Recusa (cadastro)", "—")

        st.markdown("---")
        # Ranking (se houver alguma forma de associar profissional aos atendimentos no futuro)
        st.caption("Caso a OS traga o ID/Nome da profissional, o ranking por profissional aparecerá aqui.")

# ---------------------------------
# 🧹 Atendimentos
# ---------------------------------
with aba[3]:
    st.subheader("Atendimentos")

    if atd_f.empty:
        st.warning("Base de Atendimentos não carregada ou sem dados no período.")
    else:
        # Série temporal por status
        if "data_atendimento" in atd_f.columns and "status_servico" in atd_f.columns:
            tmp = atd_f.copy()
            tmp["dia"] = tmp["data_atendimento"].dt.to_period("D").dt.to_timestamp()
            serie = tmp.groupby(["dia", "status_servico"]).size().reset_index(name="qtd")
            if USE_PLOTLY:
                fig = px.line(serie, x="dia", y="qtd", color="status_servico", markers=True, title="Atendimentos por Dia (por status)")
                st.plotly_chart(fig, use_container_width=True)
            else:
                pivot = serie.pivot(index="dia", columns="status_servico", values="qtd").fillna(0).sort_index()
                st.line_chart(pivot)

        cols = st.columns(3)
        concl = int(atd_f[atd_f.get("status_servico").fillna("").str.lower().eq("concluido")].shape[0]) if "status_servico" in atd_f.columns else 0
        agend = int(atd_f[atd_f.get("status_servico").fillna("").str.lower().eq("agendado")].shape[0]) if "status_servico" in atd_f.columns else 0
        canc  = int(atd_f[atd_f.get("status_servico").fillna("").str.lower().eq("cancelado")].shape[0]) if "status_servico" in atd_f.columns else 0

        total = concl + agend + canc
        taxa_cancel = (canc / total * 100) if total > 0 else 0
        cols[0].metric("Concluídos", f"{concl:,}".replace(",", "."))
        cols[1].metric("Agendados", f"{agend:,}".replace(",", "."))
        cols[2].metric("Taxa de Cancelamento", f"{taxa_cancel:.1f}%")

        st.markdown("---")
        st.dataframe(atd_f.head(200))

# ---------------------------------
# 💰 Financeiro (Receber & Repasses)
# ---------------------------------
with aba[4]:
    st.subheader("Receita, Repasses e Margem de Contribuição")

    if fin_f.empty and rec_f.empty and rep_f.empty:
        st.warning("Bases financeiras não carregadas.")
    else:
        receita = float(rec_f.get("valor_recebido").sum()) if not rec_f.empty and "valor_recebido" in rec_f.columns else 0.0
        repasses = float(rep_f.get("valor_repasse").sum()) if not rep_f.empty and "valor_repasse" in rep_f.columns else 0.0
        mc_total = float(fin_f.get("mc").sum()) if not fin_f.empty and "mc" in fin_f.columns else (receita - repasses)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Receita no período", f"R$ {receita:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        c2.metric("Repasses no período", f"R$ {repasses:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        c3.metric("Margem de Contribuição", f"R$ {mc_total:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

        # Inadimplência (Clientes com título vencido e sem pagamento)
        inad = 0
        if not rec_f.empty and {"data_vencimento", "data_pagamento"}.issubset(set(rec_f.columns)):
            hoje = pd.Timestamp.today().normalize()
            pend = rec_f[(rec_f["data_pagamento"].isna()) & (pd.to_datetime(rec_f["data_vencimento"], errors="coerce") < hoje)]
            inad = float(pend.get("valor_recebido").sum()) if "valor_recebido" in pend.columns else pend.shape[0]
        c4.metric("Inadimplência (valor em aberto)", f"R$ {inad:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

        st.markdown("---")
        if not fin_f.empty:
            st.caption("Tabela por atendimento (OS) — Receita x Repasse x MC")
            show_cols = [c for c in [
                "os_id",
                "cliente_nome",
                "valor_recebido",
                "situacao",
                "data_pagamento",
                "valor_repasse",
                "situacao_repasse",
                "data_pagamento_repasse",
                "mc",
            ] if c in fin_f.columns]
            st.dataframe(fin_f[show_cols].sort_values("mc", ascending=False).reset_index(drop=True))

        # Distribuições
        charts = st.columns(2)
        if not rec_f.empty and "valor_recebido" in rec_f.columns:
            rec_serie = rec_f.copy()
            # por mês
            if "data_pagamento" in rec_serie.columns:
                rec_serie["mes"] = pd.to_datetime(rec_serie["data_pagamento"], errors="coerce").dt.to_period("M").dt.to_timestamp()
                g = rec_serie.groupby("mes")["valor_recebido"].sum().reset_index()
                if USE_PLOTLY:
                    fig_r = px.bar(g, x="mes", y="valor_recebido", title="Receita por Mês")
                    charts[0].plotly_chart(fig_r, use_container_width=True)
                else:
                    charts[0].bar_chart(g.set_index("mes")["valor_recebido"])

        if not rep_f.empty and "valor_repasse" in rep_f.columns:
            rep_serie = rep_f.copy()
            base_col = "data_pagamento_repasse" if "data_pagamento_repasse" in rep_serie.columns else None
            if base_col:
                rep_serie["mes"] = pd.to_datetime(rep_serie[base_col], errors="coerce").dt.to_period("M").dt.to_timestamp()
                g2 = rep_serie.groupby("mes")["valor_repasse"].sum().reset_index()
                if USE_PLOTLY:
                    fig_p = px.bar(g2, x="mes", y="valor_repasse", title="Repasses por Mês")
                    charts[1].plotly_chart(fig_p, use_container_width=True)
                else:
                    charts[1].bar_chart(g2.set_index("mes")["valor_repasse"])

st.markdown("---")
st.caption("© Vavivê — Dashboard de indicadores. Este app aceita variações de nomes de colunas e tenta normalizar automaticamente. Para colunas ausentes, alguns gráficos podem não aparecer.")
