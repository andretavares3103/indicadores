# -*- coding: utf-8 -*-
# -------------------------------------------------------------
# Vavivê — Dashboard de Indicadores (Streamlit)
# -------------------------------------------------------------
# Sem sidebar. Lê planilhas de pastas locais:
#   ./Clientes, ./Profissionais, ./Atendimentos, ./Contas Receber, ./Repasses
# Empilha (concat) automaticamente todos os arquivos por pasta.
# TODAS as abas e KPIs são sensíveis ao filtro de período.
# -------------------------------------------------------------

import streamlit as st
import pandas as pd
import numpy as np
import unicodedata
from datetime import datetime, date
from dateutil import parser
from pathlib import Path

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
)

# =============================================================
# Helpers
# =============================================================

def _slug(s: str) -> str:
    if s is None:
        return ""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii")
    s = s.strip().lower().replace("/", " ")
    for ch in ["(", ")", "[", "]", ",", ";", ":", "-", "."]:
        s = s.replace(ch, " ")
    return "_".join(s.split())

def _norm_text(x) -> str:
    """Normaliza textos para comparações (remove acentos e baixa)."""
    if pd.isna(x):
        return ""
    return unicodedata.normalize("NFKD", str(x)).encode("ascii", "ignore").decode("ascii").strip().lower()

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if df.empty:
        return df
    df.columns = [_slug(c) for c in df.columns]
    return df

def try_parse_date(x):
    """Converte string, datetime ou serial numérico do Excel -> Timestamp."""
    if pd.isna(x):
        return pd.NaT
    if isinstance(x, (pd.Timestamp, datetime, date)):
        return pd.to_datetime(x)
    if isinstance(x, (int, float)) and not np.isnan(x):
        # serial Excel (dias desde 1899-12-30); aceita frações (horas)
        if 59 < float(x) < 80000:
            base = pd.Timestamp("1899-12-30")
            return base + pd.to_timedelta(float(x), unit="D")
    return pd.to_datetime(str(x), dayfirst=True, errors="coerce")

def coalesce_inplace(df: pd.DataFrame, candidates: list[str], new: str) -> pd.DataFrame:
    for c in candidates:
        if c in df.columns:
            df[new] = df[c]
            return df
    if new not in df.columns:
        df[new] = np.nan
    return df

# =============================================================
# Leitura local (pasta do repositório) — SEMPRE CONCAT
# =============================================================

@st.cache_data(ttl=600, show_spinner=False)
def read_local_folder(
    folder_path: str,
    preferred_sheet: str | None = None,
    recurse: bool = True,
    patterns: tuple[str, ...] = ("*.xlsx", "*.xls", "*.csv"),
    alt_sheet_names: list[str] | None = None,
) -> pd.DataFrame:
    """
    Lê todos os arquivos suportados da pasta (e subpastas) e concatena.
    - Excel: tenta preferred_sheet; se não houver, tenta alt_sheet_names; senão usa a 1ª.
    - CSV: detecta separador ';' ou ',' automaticamente.
    """
    if not folder_path:
        return pd.DataFrame()

    base = Path(folder_path).expanduser().resolve()
    if not base.exists() or not base.is_dir():
        st.warning(f"Pasta não encontrada: {base}")
        return pd.DataFrame()

    files: list[Path] = []
    if recurse:
        for pat in patterns:
            files.extend(base.rglob(pat))
    else:
        for pat in patterns:
            files.extend(base.glob(pat))
    if not files:
        return pd.DataFrame()

    files = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)

    def _guess_sep(sample: str) -> str:
        return ";" if sample.count(";") > sample.count(",") else ","

    def _norm(x: str) -> str:
        return _slug(x)

    alt_sheet_names = alt_sheet_names or []

    dfs = []
    for p in files:
        try:
            suf = p.suffix.lower()
            if suf == ".csv":
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    sample = f.read(8192)
                sep = _guess_sep(sample)
                df = pd.read_csv(p, sep=sep)

            elif suf in (".xls", ".xlsx"):
                xls = pd.ExcelFile(p)
                sheet_to_use = None

                # 1) preferred_sheet exata
                if preferred_sheet and preferred_sheet in xls.sheet_names:
                    sheet_to_use = preferred_sheet
                else:
                    # 2) match por nomes alternativos (normalizados)
                    targets = {_norm(nm) for nm in ([preferred_sheet] if preferred_sheet else [])} | {_norm(nm) for nm in alt_sheet_names}
                    for nm in xls.sheet_names:
                        if _norm(nm) in targets:
                            sheet_to_use = nm
                            break
                    # 3) fallback: primeira aba
                    if sheet_to_use is None:
                        sheet_to_use = xls.sheet_names[0]

                df = pd.read_excel(xls, sheet_name=sheet_to_use)

            else:
                continue

            df["_source_file"] = p.name
            df["_modified"] = pd.to_datetime(p.stat().st_mtime, unit="s")
            dfs.append(df)

        except Exception as e:
            st.warning(f"Falha ao ler {p.name}: {e}")
            continue

    return pd.concat(dfs, ignore_index=True, sort=False) if dfs else pd.DataFrame()

# =============================================================
# Caminhos (ajuste aqui se mudar a estrutura de pastas)
# =============================================================

DEFAULT_LOCAL_DIRS = {
    "clientes":      "./Clientes",
    "profissionais": "./Profissionais",
    "atendimentos":  "./Atendimentos",
    "receber":       "./Contas Receber",
    "repasses":      "./Repasses",
}

local_dirs = {
    "clientes":      st.secrets.get("LOCAL_CLIENTES_DIR", DEFAULT_LOCAL_DIRS["clientes"]),
    "profissionais": st.secrets.get("LOCAL_PROFISSIONAIS_DIR", DEFAULT_LOCAL_DIRS["profissionais"]),
    "atendimentos":  st.secrets.get("LOCAL_ATENDIMENTOS_DIR", DEFAULT_LOCAL_DIRS["atendimentos"]),
    "receber":       st.secrets.get("LOCAL_RECEBER_DIR", DEFAULT_LOCAL_DIRS["receber"]),
    "repasses":      st.secrets.get("LOCAL_REPASSES_DIR", DEFAULT_LOCAL_DIRS["repasses"]),
}

# =============================================================
# Diagnóstico (opcional)
# =============================================================
with st.expander("🔧 Diagnóstico das pastas"):
    for nome, pth in local_dirs.items():
        p = Path(pth).expanduser().resolve()
        ok = p.exists() and p.is_dir()
        st.write(f"{nome}: caminho='{p}', existe? {ok}")
        if ok:
            encontrados = (
                sum(1 for _ in p.rglob("*.xlsx")) +
                sum(1 for _ in p.rglob("*.xls")) +
                sum(1 for _ in p.rglob("*.csv"))
            )
            st.write(f"Arquivos suportados encontrados: {encontrados}")

# =============================================================
# Carregar dados (concat automático) a partir das pastas
# =============================================================
raw_clientes = read_local_folder(local_dirs["clientes"],     preferred_sheet=None,                 recurse=True)
raw_prof     = read_local_folder(local_dirs["profissionais"], preferred_sheet="Profissionais",     recurse=True,
                                 alt_sheet_names=["Profissional", "Prestadores", "Cadastro", "Dados"])
raw_atend    = read_local_folder(local_dirs["atendimentos"],  preferred_sheet="Clientes",          recurse=True)
raw_receber  = read_local_folder(local_dirs["receber"],       preferred_sheet="Dados Financeiros", recurse=True,
                                 alt_sheet_names=["Financeiro", "Receber", "Contas a Receber", "Dados"])
raw_repasses = read_local_folder(local_dirs["repasses"],      preferred_sheet="Dados Financeiros", recurse=True,
                                 alt_sheet_names=["Financeiro", "Repasses", "Repasse", "Dados"])

# =============================================================
# Normalização das bases
# =============================================================
cli = normalize_columns(raw_clientes) if not raw_clientes.empty else pd.DataFrame()
pro = normalize_columns(raw_prof)     if not raw_prof.empty     else pd.DataFrame()
atd = normalize_columns(raw_atend)    if not raw_atend.empty    else pd.DataFrame()
rec = normalize_columns(raw_receber)  if not raw_receber.empty  else pd.DataFrame()
rep = normalize_columns(raw_repasses) if not raw_repasses.empty else pd.DataFrame()

# ===================== Padronizações ==========================
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
        "nome": "cliente_nome",
    }, inplace=True)

if not pro.empty:
    pro.rename(columns={
        "id": "prof_id",
        "nome": "prof_nome",
        "nome_prestador": "prof_nome",
        "cpf": "prof_cpf",
        "atendimentos_feitos": "att_feitos",
        "atendimentos_recusado": "att_recusados",
    }, inplace=True)
    coalesce_inplace(pro, ["endereco_1_rua", "endereco_rua", "rua", "logradouro"], "prof_rua")
    coalesce_inplace(pro, ["endereco_1_bairro", "endereco_bairro", "bairro"], "prof_bairro")
    coalesce_inplace(pro, ["endereco_1_cidade", "cidade"], "prof_cidade")
    coalesce_inplace(pro, ["endereco_1_cep", "cep"], "prof_cep")
    pro = pro.loc[:, ~pro.columns.duplicated()]

if not atd.empty:
    coalesce_inplace(atd, ["os", "os_id", "atendimento_id"], "os_id")
    # Data do atendimento (inclui "Data 1" vindo da aba "Clientes")
    coalesce_inplace(atd, ["data_1", "data", "data_do_atendimento", "data_atendimento"], "data_atendimento")
    atd["data_atendimento"] = atd["data_atendimento"].apply(try_parse_date)
    # Status (para contagem de concluídos/agendados/cancelados)
    coalesce_inplace(
        atd,
        ["status_servico", "status", "status_do_servico", "situacao", "situacao_servico"],
        "status_servico"
    )
    atd.rename(columns={
        "cliente": "cliente_nome",
        "cliente_novo?": "cliente_novo",
        "origem_venda": "origem_venda",
        "endereco_bairro": "bairro",
        "atendimento_bairro": "bairro",
        "atendimento_rua": "rua",
    }, inplace=True)
    coalesce_inplace(atd, ["valor_atendimento", "valor", "valores", "procv_valores", "valor_total"], "valor_atendimento")
    if "os_id" in atd.columns:
        atd["os_id"] = atd["os_id"].astype(str)
    atd = atd.loc[:, ~atd.columns.duplicated()]

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
    if "situacao" in rec.columns and "status" in rec.columns:
        rec["situacao"] = rec["situacao"].fillna(rec["status"]);  rec.drop(columns=["status"], inplace=True)
    elif "situacao" not in rec.columns and "status" in rec.columns:
        rec.rename(columns={"status": "situacao"}, inplace=True)
    if "data_pagamento" in rec.columns:
        rec["data_pagamento"] = rec["data_pagamento"].apply(try_parse_date)
    if "data_vencimento" in rec.columns:
        rec["data_vencimento"] = rec["data_vencimento"].apply(try_parse_date)
    rec = rec.loc[:, ~rec.columns.duplicated()]

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
        if "situacao" in rep.columns: rep.rename(columns={"situacao": "situacao_repasse"}, inplace=True)
        elif "status" in rep.columns: rep.rename(columns={"status": "situacao_repasse"}, inplace=True)
    else:
        for _c in ["situacao", "status"]:
            if _c in rep.columns: rep.drop(columns=[_c], inplace=True)
    for dtc in ["data_pagamento_repasse", "data_vencimento_repasse"]:
        if dtc in rep.columns: rep[dtc] = rep[dtc].apply(try_parse_date)
    rep = rep.loc[:, ~rep.columns.duplicated()]

# =============================================================
# Montagem financeira (por OS)
# =============================================================
fin = pd.DataFrame()
if not rec.empty or not rep.empty:
    left = rec.copy() if not rec.empty else pd.DataFrame(columns=["os_id"])
    right = rep.copy() if not rep.empty else pd.DataFrame(columns=["os_id"])
    left["os_id"] = left["os_id"].astype(str)
    right["os_id"] = right["os_id"].astype(str)

    def _first_nonnull(s): return s.dropna().iloc[0] if s.dropna().size else np.nan

    rec_ag = left.groupby("os_id", as_index=False).agg({
        "cliente_nome": _first_nonnull if "cliente_nome" in left.columns else (lambda s: np.nan),
        "valor_recebido": "sum" if "valor_recebido" in left.columns else (lambda s: np.nan),
        "data_pagamento": "max" if "data_pagamento" in left.columns else (lambda s: np.nan),
        "data_vencimento": "max" if "data_vencimento" in left.columns else (lambda s: np.nan),
        "situacao": _first_nonnull if "situacao" in left.columns else (lambda s: np.nan),
        "prof_cpf": _first_nonnull if "prof_cpf" in left.columns else (lambda s: np.nan),
    }) if not left.empty else pd.DataFrame(columns=["os_id"])

    rep_ag = right.groupby("os_id", as_index=False).agg({
        "profissional_nome": _first_nonnull if "profissional_nome" in right.columns else (lambda s: np.nan),
        "valor_repasse": "sum" if "valor_repasse" in right.columns else (lambda s: np.nan),
        "data_pagamento_repasse": "max" if "data_pagamento_repasse" in right.columns else (lambda s: np.nan),
        "data_vencimento_repasse": "max" if "data_vencimento_repasse" in right.columns else (lambda s: np.nan),
        "situacao_repasse": _first_nonnull if "situacao_repasse" in right.columns else (lambda s: np.nan),
        "prof_cpf": _first_nonnull if "prof_cpf" in right.columns else (lambda s: np.nan),
    }) if not right.empty else pd.DataFrame(columns=["os_id"])

    fin = pd.merge(rec_ag, rep_ag, on="os_id", how="outer", suffixes=("_rec", "_rep"))
    if "valor_recebido" not in fin.columns: fin["valor_recebido"] = np.nan
    if "valor_repasse" not in fin.columns: fin["valor_repasse"] = np.nan
    fin["mc"] = fin["valor_recebido"].fillna(0) - fin["valor_repasse"].fillna(0)
    fin = fin.loc[:, ~fin.columns.duplicated()]

# =============================================================
# Filtro de período (na página, sem sidebar)
# =============================================================
# Coleta datas globais para default do widget
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
    dmin = min(all_dates).date(); dmax = max(all_dates).date()
else:
    today = date.today(); dmin = date(today.year, 1, 1); dmax = today

st.markdown("## 🗓️ Período")
sel_ini, sel_fim = st.date_input("Selecione o intervalo", value=(dmin, dmax))
dt_ini = pd.to_datetime(sel_ini)
dt_fim = pd.to_datetime(sel_fim)

# ------- aplica filtro nas tabelas "de evento" -------
if not atd.empty and "data_atendimento" in atd.columns:
    atd_f = atd[(atd["data_atendimento"] >= dt_ini) & (atd["data_atendimento"] <= dt_fim)].copy()
else:
    atd_f = atd.copy()

if not rec.empty:
    dt_rec = rec.copy()
    if "data_pagamento" in dt_rec.columns and dt_rec["data_pagamento"].notna().any():
        dt_rec["_data_fin"] = dt_rec["data_pagamento"].fillna(dt_rec.get("data_vencimento"))
    else:
        dt_rec["_data_fin"] = dt_rec.get("data_vencimento")
    rec_f = dt_rec[(pd.to_datetime(dt_rec["_data_fin"], errors="coerce") >= dt_ini) & (pd.to_datetime(dt_rec["_data_fin"], errors="coerce") <= dt_fim)].copy()
else:
    rec_f = rec.copy()

if not rep.empty:
    dt_rep = rep.copy()
    base_col = "data_pagamento_repasse" if "data_pagamento_repasse" in dt_rep.columns else "data_vencimento_repasse"
    if base_col in dt_rep.columns:
        rep_f = dt_rep[(pd.to_datetime(dt_rep[base_col], errors="coerce") >= dt_ini) & (pd.to_datetime(dt_rep[base_col], errors="coerce") <= dt_fim)].copy()
    else:
        rep_f = rep.copy()
else:
    rep_f = rep.copy()

fin_f = fin.copy()
if not fin_f.empty:
    fin_f["_data"] = pd.NaT
    if "data_pagamento" in fin_f.columns:
        fin_f["_data"] = fin_f["data_pagamento"].fillna(fin_f.get("data_vencimento"))
    if "data_pagamento_repasse" in fin_f.columns:
        fin_f["_data"] = fin_f["_data"].fillna(fin_f["data_pagamento_repasse"]).fillna(fin_f.get("data_vencimento_repasse"))
    fin_f = fin_f[(pd.to_datetime(fin_f["_data"], errors="coerce") >= dt_ini) & (pd.to_datetime(fin_f["_data"], errors="coerce") <= dt_fim)].copy()

# ------- aplica filtro nas tabelas "de cadastro" (clientes/profissionais) -------
def _filter_by_date_or_activity(df: pd.DataFrame, date_candidates: list[str], activity_df: pd.DataFrame, pairs: list[tuple[str, str]]) -> pd.DataFrame:
    """
    1) Se houver alguma coluna de data válida no df, filtra por dt_ini..dt_fim.
    2) Caso não haja data, filtra por 'atividade': mantém apenas registros cujo identificador
       aparece no activity_df (ex.: clientes que tiveram atendimento no período).
    pairs: lista de pares (col_df, col_activity) para match.
    """
    if df.empty:
        return df.copy()

    # 1) tentar por data interna
    for col in date_candidates:
        if col in df.columns:
            dates = pd.to_datetime(df[col].apply(try_parse_date), errors="coerce")
            if dates.notna().any():
                dff = df.copy()
                dff["_date"] = dates
                return dff[(dff["_date"] >= dt_ini) & (dff["_date"] <= dt_fim)].copy()

    # 2) fallback por atividade
    if activity_df is not None and not activity_df.empty:
        for col_df, col_act in pairs:
            if (col_df in df.columns) and (col_act in activity_df.columns):
                keys = set(activity_df[col_act].dropna().astype(str).str.strip())
                dff = df.copy()
                return dff[dff[col_df].astype(str).str.strip().isin(keys)].copy()

    # se nada der, retorna vazio para honrar "sensível ao período"
    return df.iloc[0:0].copy()

# Clientes sensíveis ao período
cli_date_candidates = ["data_cadastro", "created_at", "criado_em", "cadastro_data", "data", "data_1"]
# tentar parear por id/cpf/email/nome com atendimentos
cli_pairs = [
    ("cliente_id", "cliente_id"),
    ("cliente_cpf", "cliente_cpf"),
    ("cliente_email", "cliente_email"),
    ("cliente_nome", "cliente_nome"),
    ("nome", "cliente_nome"),
]
cli_f = _filter_by_date_or_activity(cli, cli_date_candidates, atd_f, cli_pairs)

# Profissionais sensíveis ao período
pro_date_candidates = ["data_cadastro", "created_at", "criado_em", "cadastro_data", "data", "data_1"]
# parear preferencialmente com financeiro (tem prof_cpf e profissional_nome), senão atend
pro_pairs_fin = [
    ("prof_cpf", "prof_cpf"),
    ("prof_nome", "profissional_nome"),
]
pro_pairs_atd = [
    ("prof_cpf", "prof_cpf"),
    ("prof_nome", "profissional_nome"),
]
pro_f = _filter_by_date_or_activity(pro, pro_date_candidates, fin_f if not fin_f.empty else atd_f, pro_pairs_fin if not fin_f.empty else pro_pairs_atd)

# =============================================================
# View auxiliar — OS unificada (Atend + Financeiro + Prof) [filtrada]
# =============================================================
atd_base = pd.DataFrame()
if not atd_f.empty:
    keep_cols = [c for c in ["os_id", "cliente_nome", "data_atendimento", "valor_atendimento", "endereco", "rua", "bairro", "cidade", "cep", "complemento"] if c in atd_f.columns]
    atd_base = atd_f[keep_cols].copy()

fin_base = pd.DataFrame()
if not fin_f.empty:
    fin_base = fin_f[[c for c in ["os_id", "cliente_nome", "valor_recebido", "situacao", "data_pagamento",
                                  "valor_repasse", "situacao_repasse", "data_pagamento_repasse", "mc", "prof_cpf", "profissional_nome"]
                      if c in fin_f.columns]].copy()

pro_base = pd.DataFrame()
if not pro_f.empty:
    if "prof_cpf" in pro_f.columns:
        pro_base = pro_f[[c for c in ["prof_cpf", "prof_nome", "prof_rua", "prof_bairro", "prof_cidade", "prof_cep"] if c in pro_f.columns]].drop_duplicates(subset=["prof_cpf"])
    else:
        pro_base = pro_f[[c for c in ["prof_nome", "prof_rua", "prof_bairro", "prof_cidade", "prof_cep"] if c in pro_f.columns]].drop_duplicates()

os_view = pd.DataFrame()
if not atd_base.empty or not fin_base.empty:
    if "os_id" in atd_base.columns: atd_base["os_id"] = atd_base["os_id"].astype(str)
    if "os_id" in fin_base.columns: fin_base["os_id"] = fin_base["os_id"].astype(str)

    if ("os_id" in atd_base.columns) and ("os_id" in fin_base.columns):
        os_view = pd.merge(atd_base, fin_base, on="os_id", how="outer")
    else:
        common = [c for c in ["cliente_nome"] if (c in atd_base.columns) and (c in fin_base.columns)]
        os_view = pd.merge(atd_base, fin_base, on=common, how="outer") if common else pd.concat([atd_base.reset_index(drop=True), fin_base.reset_index(drop=True)], axis=1)

    if not pro_base.empty:
        if ("prof_cpf" in os_view.columns) and ("prof_cpf" in pro_base.columns):
            os_view = pd.merge(os_view, pro_base, on="prof_cpf", how="left")
        elif ("profissional_nome" in os_view.columns) and ("prof_nome" in pro_base.columns):
            os_view = pd.merge(os_view, pro_base, left_on="profissional_nome", right_on="prof_nome", how="left")

    os_view = os_view.loc[:, ~os_view.columns.duplicated()]

# =============================================================
# UI — TABS (TODO período-sensível em todas)
# =============================================================
st.title("Indicadores — Vavivê")

if all([df.empty for df in [cli_f, pro_f, atd_f, rec_f, rep_f]]):
    st.info("Nenhuma base com dados no período selecionado.")

tabs = st.tabs([
    "📋 Visão Geral",
    "👥 Clientes & Regiões",
    "🧑‍💼 Profissionais",
    "🧹 Atendimentos",
    "💰 Financeiro (Receber & Repasses)",
    "🔎 OS — Detalhe",
])

# Visão Geral (PERÍODO)
with tabs[0]:
    st.subheader("KPIs do Período")

    status_norm = atd_f.get("status_servico").map(_norm_text) if ("status_servico" in atd_f.columns) else pd.Series(dtype=str)

    total_clientes = int(cli_f.shape[0]) if not cli_f.empty else 0
    total_prof = int(pro_f.shape[0]) if not pro_f.empty else 0
    concl = int((status_norm == "concluido").sum()) if not atd_f.empty else 0
    agend = int((status_norm == "agendado").sum()) if not atd_f.empty else 0
    canc  = int((status_norm == "cancelado").sum()) if not atd_f.empty else 0

    receita = float(rec_f.get("valor_recebido").sum()) if not rec_f.empty and "valor_recebido" in rec_f.columns else 0.0
    repasses = float(rep_f.get("valor_repasse").sum()) if not rep_f.empty and "valor_repasse" in rep_f.columns else 0.0
    mc_total = float(fin_f.get("mc").sum()) if not fin_f.empty and "mc" in fin_f.columns else (receita - repasses)

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Clientes (no período)", f"{total_clientes:,}".replace(",", "."))
    c2.metric("Profissionais (no período)", f"{total_prof:,}".replace(",", "."))
    c3.metric("Concluídos", f"{concl:,}".replace(",", "."))
    c4.metric("Agendados", f"{agend:,}".replace(",", "."))
    c5.metric("Cancelados", f"{canc:,}".replace(",", "."))
    c6.metric("MC (no período)", f"R$ {mc_total:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

    st.markdown("---")
    st.caption("MC = Receita (Contas a Receber) − Repasses às Profissionais — apenas dentro do período.")

# Clientes & Regiões (PERÍODO)
with tabs[1]:
    st.subheader("Clientes (no período)")
    if cli_f.empty:
        st.warning("Nenhum cliente no período (considerando data de cadastro ou atividade em atendimentos).")
    else:
        col_origem = next((c for c in ["origem_cliente", "origem"] if c in cli_f.columns), None)
        if col_origem:
            origem_counts = (
                cli_f[col_origem].fillna("(não informado)").replace({"": "(não informado)"}).value_counts().reset_index()
            )
            origem_counts.columns = ["origem", "quantidade"]
            if USE_PLOTLY:
                fig = px.bar(origem_counts, x="origem", y="quantidade", title="Origem dos Clientes (período)", text_auto=True)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.bar_chart(origem_counts.set_index("origem")["quantidade"])
        else:
            st.info("Coluna de origem do cliente não encontrada.")

        st.markdown("---")
        st.subheader("Regiões (no período)")
        col_bairro = next((cc for c in ["bairro", "endereco_bairro", "endereco-1-bairro"] if (cc := _slug(c)) in cli_f.columns), None)
        col_cidade = "cidade" if "cidade" in cli_f.columns else None
        cols = st.columns(2)
        if col_bairro:
            bairro_counts = cli_f[col_bairro].fillna("(sem bairro)").astype(str).replace({"": "(sem bairro)"}).value_counts().reset_index()
            bairro_counts.columns = ["bairro", "clientes"]
            if USE_PLOTLY:
                fig_b = px.bar(bairro_counts.head(20), x="bairro", y="clientes", title="Top Bairros por Clientes (período)", text_auto=True)
                cols[0].plotly_chart(fig_b, use_container_width=True)
            else:
                cols[0].bar_chart(bairro_counts.set_index("bairro")["clientes"])
        else:
            cols[0].info("Coluna de bairro não encontrada.")
        if col_cidade:
            cidade_counts = cli_f[col_cidade].fillna("(sem cidade)").astype(str).replace({"": "(sem cidade)"}).value_counts().reset_index()
            cidade_counts.columns = ["cidade", "clientes"]
            if USE_PLOTLY:
                fig_c = px.bar(cidade_counts, x="cidade", y="clientes", title="Clientes por Cidade (período)", text_auto=True)
                cols[1].plotly_chart(fig_c, use_container_width=True)
            else:
                cols[1].bar_chart(cidade_counts.set_index("cidade")["clientes"])
        else:
            cols[1].info("Coluna de cidade não encontrada.")

# Profissionais (PERÍODO)
with tabs[2]:
    st.subheader("Profissionais (no período)")
    if pro_f.empty and atd_f.empty:
        st.warning("Sem profissionais no período.")
    else:
        cols = st.columns(3)
        total_prof = int(pro_f.shape[0]) if not pro_f.empty else 0
        cols[0].metric("Total de Profissionais", f"{total_prof:,}".replace(",", "."))
        if not atd_f.empty and "status_servico" in atd_f.columns:
            status_norm2 = atd_f["status_servico"].map(_norm_text)
            concluidos = (status_norm2 == "concluido").sum()
            cols[1].metric("Atendimentos Concluídos (período)", f"{int(concluidos):,}".replace(",", "."))
        else:
            cols[1].metric("Atendimentos Concluídos (período)", "0")
        if not pro_f.empty and {"att_feitos", "att_recusados"} <= set(pro_f.columns):
            feitos = pro_f["att_feitos"].fillna(0).astype(float).sum()
            recusados = pro_f["att_recusados"].fillna(0).astype(float).sum()
            taxa = (recusados / (feitos + recusados) * 100) if (feitos + recusados) > 0 else 0
            cols[2].metric("Taxa de Recusa (cadastro/ativos no período)", f"{taxa:.1f}%")
        else:
            cols[2].metric("Taxa de Recusa (cadastro/ativos no período)", "—")

        st.markdown("---")
        st.caption("A lista considera profissionais com data de cadastro no período OU que atuaram em atendimentos/financeiro no período.")

# Atendimentos (PERÍODO)
with tabs[3]:
    st.subheader("Atendimentos (no período)")
    if atd_f.empty:
        st.warning("Sem atendimentos no período.")
    else:
        if {"data_atendimento", "status_servico"} <= set(atd_f.columns):
            tmp = atd_f.copy()
            tmp["dia"] = tmp["data_atendimento"].dt.to_period("D").dt.to_timestamp()
            tmp["status_norm"] = tmp["status_servico"].map(_norm_text)
            serie = tmp.groupby(["dia", "status_norm"]).size().reset_index(name="qtd")
            if USE_PLOTLY:
                fig = px.line(serie, x="dia", y="qtd", color="status_norm", markers=True, title="Atendimentos por Dia (período)")
                st.plotly_chart(fig, use_container_width=True)
            else:
                pivot = serie.pivot(index="dia", columns="status_norm", values="qtd").fillna(0).sort_index()
                st.line_chart(pivot)

        cols = st.columns(3)
        status_norm3 = atd_f.get("status_servico").map(_norm_text) if ("status_servico" in atd_f.columns) else pd.Series(dtype=str)
        concl = int((status_norm3 == "concluido").sum()) if not atd_f.empty else 0
        agend = int((status_norm3 == "agendado").sum()) if not atd_f.empty else 0
        canc  = int((status_norm3 == "cancelado").sum()) if not atd_f.empty else 0
        total = concl + agend + canc
        taxa_cancel = (canc / total * 100) if total > 0 else 0
        cols[0].metric("Concluídos", f"{concl:,}".replace(",", "."))
        cols[1].metric("Agendados", f"{agend:,}".replace(",", "."))
        cols[2].metric("Taxa de Cancelamento", f"{taxa_cancel:.1f}%")

        st.markdown("---")
        st.dataframe(atd_f.head(200))

# Financeiro (PERÍODO)
with tabs[4]:
    st.subheader("Receita, Repasses e MC (no período)")
    if fin_f.empty and rec_f.empty and rep_f.empty:
        st.warning("Sem dados financeiros no período.")
    else:
        receita = float(rec_f.get("valor_recebido").sum()) if not rec_f.empty and "valor_recebido" in rec_f.columns else 0.0
        repasses = float(rep_f.get("valor_repasse").sum()) if not rep_f.empty and "valor_repasse" in rep_f.columns else 0.0
        mc_total = float(fin_f.get("mc").sum()) if not fin_f.empty and "mc" in fin_f.columns else (receita - repasses)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Receita (período)", f"R$ {receita:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        c2.metric("Repasses (período)", f"R$ {repasses:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        c3.metric("MC (período)", f"R$ {mc_total:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        inad = 0
        if not rec_f.empty and {"data_vencimento", "data_pagamento"} <= set(rec_f.columns):
            hoje = pd.Timestamp.today().normalize()
            pend = rec_f[(rec_f["data_pagamento"].isna()) & (pd.to_datetime(rec_f["data_vencimento"], errors="coerce") < hoje)]
            inad = float(pend.get("valor_recebido").sum()) if "valor_recebido" in pend.columns else 0.0
        c4.metric("Inadimplência (em aberto, período)", f"R$ {inad:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

        st.markdown("---")
        if not fin_f.empty:
            st.caption("Por atendimento (OS) — apenas dentro do período")
            show_cols = [c for c in [
                "os_id", "cliente_nome", "valor_recebido", "situacao", "data_pagamento",
                "valor_repasse", "situacao_repasse", "data_pagamento_repasse", "mc",
            ] if c in fin_f.columns]
            show_cols = list(dict.fromkeys(show_cols))
            fin_view = fin_f.loc[:, ~fin_f.columns.duplicated()]
            st.dataframe(fin_view[show_cols].sort_values("mc", ascending=False).reset_index(drop=True))

        charts = st.columns(2)
        if not rec_f.empty and "valor_recebido" in rec_f.columns and "data_pagamento" in rec_f.columns:
            rec_serie = rec_f.copy()
            rec_serie["mes"] = pd.to_datetime(rec_serie["data_pagamento"], errors="coerce").dt.to_period("M").dt.to_timestamp()
            g = rec_serie.groupby("mes")["valor_recebido"].sum().reset_index()
            if USE_PLOTLY:
                charts[0].plotly_chart(px.bar(g, x="mes", y="valor_recebido", title="Receita por Mês (período)"), use_container_width=True)
            else:
                charts[0].bar_chart(g.set_index("mes")["valor_recebido"])
        if not rep_f.empty and "valor_repasse" in rep_f.columns and "data_pagamento_repasse" in rep_f.columns:
            rep_serie = rep_f.copy()
            rep_serie["mes"] = pd.to_datetime(rep_serie["data_pagamento_repasse"], errors="coerce").dt.to_period("M").dt.to_timestamp()
            g2 = rep_serie.groupby("mes")["valor_repasse"].sum().reset_index()
            if USE_PLOTLY:
                charts[1].plotly_chart(px.bar(g2, x="mes", y="valor_repasse", title="Repasses por Mês (período)"), use_container_width=True)
            else:
                charts[1].bar_chart(g2.set_index("mes")["valor_repasse"])

# OS — Detalhe (PERÍODO)
with tabs[5]:
    st.subheader("Consulta por OS (Atendimento) — período")
    if os_view.empty:
        st.info("Não há dados suficientes no período selecionado.")
    else:
        os_view["os_id"] = os_view["os_id"].astype(str)
        sel_os = st.selectbox("Selecione a OS", options=sorted(os_view["os_id"].dropna().unique().tolist()))
        registro = os_view[os_view["os_id"] == str(sel_os)].copy()
        if registro.empty:
            st.warning("OS não encontrada na seleção.")
        else:
            reg = registro.iloc[0]
            v_atend = float(reg.get("valor_atendimento", np.nan)) if not pd.isna(reg.get("valor_atendimento", np.nan)) else np.nan
            v_pago  = float(reg.get("valor_recebido", 0) or 0)
            v_rep   = float(reg.get("valor_repasse", 0) or 0)
            mc      = float(reg.get("mc", v_pago - v_rep))
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Valor do Atendimento", ("R$ %0.2f" % v_atend).replace(".", ",") if not np.isnan(v_atend) else "—")
            k2.metric("Valor Pago (Recebido)", ("R$ %0.2f" % v_pago).replace(".", ","))
            k3.metric("Repasse", ("R$ %0.2f" % v_rep).replace(".", ","))
            k4.metric("MC (Pago − Repasse)", ("R$ %0.2f" % mc).replace(".", ","))
            st.markdown("---")
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("### Cliente & Atendimento")
                st.write({
                    "OS": reg.get("os_id"),
                    "Cliente": reg.get("cliente_nome"),
                    "Data do Atendimento": (pd.to_datetime(reg.get("data_atendimento")).strftime('%d/%m/%Y') if pd.notna(reg.get("data_atendimento")) else "—"),
                    "Endereço": reg.get("endereco") or reg.get("rua"),
                    "Bairro": reg.get("bairro"),
                    "Cidade": reg.get("cidade"),
                    "CEP": reg.get("cep"),
                })
            with c2:
                st.markdown("### Profissional & Repasse")
                st.write({
                    "Profissional": reg.get("profissional_nome") or reg.get("prof_nome"),
                    "CPF Profissional": reg.get("prof_cpf"),
                    "Endereço Profissional": reg.get("prof_rua"),
                    "Bairro Profissional": reg.get("prof_bairro"),
                    "Cidade Profissional": reg.get("prof_cidade"),
                    "CEP Profissional": reg.get("prof_cep"),
                })

st.markdown("---")
st.caption("© Vavivê — Dashboard de indicadores. Tudo sensível ao período selecionado.")
