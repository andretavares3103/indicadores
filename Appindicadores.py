# -*- coding: utf-8 -*-
# -------------------------------------------------------------
# Vavivê — Dashboard de Indicadores (Streamlit)
# -------------------------------------------------------------
# Sem sidebar. Lê planilhas de pastas locais:
#   ./Clientes, ./Profissionais, ./Atendimentos, ./Contas Receber, ./Repasses
# Empilha (concat) automaticamente todos os arquivos por pasta.
#
# Regras de período (visão de CAIXA):
# - Atendimentos: filtra por "Data 1" (mapeada para data_atendimento).
# - Receber:
#     * Recebidos  = tem data_pagamento → filtra por data_pagamento
#     * A receber  = sem data_pagamento → filtra por data_vencimento
# - Repasses:
#     * Pagos      = tem data_pagamento_repasse → filtra por data_pagamento_repasse
#     * A pagar    = sem data_pagamento_repasse → filtra por data_vencimento_repasse
# - Clientes e Profissionais NÃO são sensíveis ao período.
#
# Aba "Atendimento + Foto" (tabs[6]) — conforme solicitado:
#   • Usa APENAS dados do atendimento (cliente_nome, data_atendimento, status_servico,
#     endereco, bairro, cidade, prof_nome e prof_id da própria planilha).
#   • NÃO mostra CEP nem bloco financeiro.
#   • Profissional vem da própria tabela de atendimentos:
#       - prof_id  <= "Num Prestador" (normalizado para num_prestador)
#       - prof_nome <= "Prestador"
#     Se prof_cpf estiver vazio, tenta completar a partir do cadastro via prof_id.
#   • Foto: tenta no próprio registro → depois busca no cadastro (por prof_id, prof_cpf, prof_nome)
#           → depois monta via template PHOTO_URL_TEMPLATE ({prof_id}, {prof_cpf}, {os_id}).
# -------------------------------------------------------------

import streamlit as st
import pandas as pd
import numpy as np
import unicodedata
import re
from datetime import datetime, date
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
    if pd.isna(x):
        return ""
    return unicodedata.normalize("NFKD", str(x)).encode("ascii", "ignore").decode("ascii").strip().lower()

def _only_digits(x) -> str:
    s = "" if pd.isna(x) else str(x)
    return "".join(ch for ch in s if ch.isdigit())

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if df.empty:
        return df
    df.columns = [_slug(c) for c in df.columns]
    return df

# ===== Novo parser de datas: dmy/mdy/auto =====
PREFER_DATE_ORDER = (st.secrets.get("RAW_DATE_FORMAT", "dmy") or "dmy").lower()  # "dmy" | "mdy" | "auto"

def try_parse_date(x, prefer: str | None = None):
    """
    Converte string, datetime ou serial Excel -> Timestamp.
    Suporta formatos dmy/mdy e tenta 'auto' para CSV/planilhas mistas.
    prefer: "dmy" (pt-BR), "mdy" (US) ou "auto". Default vem de st.secrets.
    """
    prefer = (prefer or PREFER_DATE_ORDER or "dmy").lower()
    if pd.isna(x):
        return pd.NaT
    if isinstance(x, (pd.Timestamp, datetime, date)):
        return pd.to_datetime(x, errors="coerce")

    # Serial Excel
    if isinstance(x, (int, float)) and not np.isnan(x):
        if 59 < float(x) < 80000:  # range plausível de serial Excel
            base = pd.Timestamp("1899-12-30")
            return base + pd.to_timedelta(float(x), unit="D")

    s = str(x).strip()
    if not s:
        return pd.NaT

    # ISO (yyyy-mm-dd) claro
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return pd.to_datetime(s, errors="coerce")

    # dd/mm/yyyy ou mm/dd/yyyy (ambíguo)
    amb = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if amb:
        d1, d2, y = int(amb.group(1)), int(amb.group(2)), int(amb.group(3))
        if prefer == "mdy":
            return pd.to_datetime(s, dayfirst=False, errors="coerce")
        if prefer == "dmy":
            return pd.to_datetime(s, dayfirst=True, errors="coerce")
        # auto: heurística simples
        # se d1 > 12 => claramente dmy; se d2 > 12 => claramente mdy;
        # se ambos <= 12, padrão BR (dmy)
        if d1 > 12:
            return pd.to_datetime(s, dayfirst=True, errors="coerce")
        if d2 > 12:
            return pd.to_datetime(s, dayfirst=False, errors="coerce")
        return pd.to_datetime(s, dayfirst=True, errors="coerce")

    # fallback geral: respeita prefer
    return pd.to_datetime(s, dayfirst=(prefer != "mdy"), errors="coerce")

def coalesce_inplace(df: pd.DataFrame, candidates: list[str], new: str) -> pd.DataFrame:
    for c in candidates:
        if c in df.columns:
            df[new] = df[c]
            return df
    if new not in df.columns:
        df[new] = np.nan
    return df

def safe_cols(df: pd.DataFrame, cols: list[str]) -> list[str]:
    """Retorna apenas colunas existentes em df, na ordem pedida."""
    return [c for c in cols if c in df.columns]

# =============================================================
# Fotos — mapeamento e template
# =============================================================

PHOTO_COLS = [
    "foto_url", "foto", "imagem_url", "imagem",
    "url_foto", "link_foto", "photo", "photo_url", "avatar", "avatar_url"
]

# Template opcional via secrets: ex. "https://cdn.exemplo.com/profissionais/{prof_id}.jpg"
PHOTO_URL_TEMPLATE = st.secrets.get("PHOTO_URL_TEMPLATE", "")

def build_photo_from_template(prof_id: str | None = None,
                              prof_cpf: str | None = None,
                              os_id: str | None = None) -> str | None:
    tpl = str(PHOTO_URL_TEMPLATE or "").strip()
    if not tpl:
        return None
    try:
        return tpl.format(
            prof_id=(str(prof_id) if prof_id is not None else ""),
            prof_cpf=_only_digits(prof_cpf) if prof_cpf else "",
            os_id=(str(os_id) if os_id is not None else ""),
        )
    except Exception:
        return None

@st.cache_data(ttl=600, show_spinner=False)
def load_photo_map() -> pd.DataFrame:
    """
    Carrega uma tabela opcional com mapeamento de foto por ID/CPF/Nome.
    Procura primeiro Carteirinhas.xlsx na raiz, depois outras convenções.
    Colunas aceitas e normalizadas:
      - ID:    prof_id / id / id_profissional / id_prof
      - CPF:   prof_cpf / cpf / profissional_cpf
      - Nome:  prof_nome / nome / profissional
      - URL:   foto_url / imagem / imagem_url / url / url_foto / link / link_foto / photo / photo_url / avatar / avatar_url
    """
    candidates = [
        Path("./Carteirinhas.xlsx"),
        Path("./fotos_profissionais.csv"),
        Path("./profissionais_fotos.csv"),
        Path("./fotos.csv"),
        Path("./Fotos/fotos_profissionais.csv"),
        Path("./Fotos/fotos_profissionais.xlsx"),
        Path("./Profissionais/fotos_profissionais.csv"),
        Path("./Profissionais/fotos_profissionais.xlsx"),
    ]
    for p in candidates:
        if not p.exists():
            continue
        try:
            if p.suffix.lower() == ".csv":
                df = pd.read_csv(p)
            else:
                xls = pd.ExcelFile(p)
                df = pd.read_excel(xls, sheet_name=xls.sheet_names[0])
            df = normalize_columns(df)
            ren = {}
            for c in df.columns:
                if c in {"id", "prof_id", "id_profissional", "id_prof"}:
                    ren[c] = "prof_id"
                if c in {"cpf", "profissional_cpf", "prof_cpf"}:
                    ren[c] = "prof_cpf"
                if c in {"nome", "profissional", "prof_nome"}:
                    ren[c] = "prof_nome"
                if c in {"foto", "foto_url", "url", "url_foto", "link", "link_foto", "imagem", "imagem_url",
                         "photo", "photo_url", "avatar", "avatar_url"}:
                    ren[c] = "foto_url"
            df = df.rename(columns=ren)
            keep = safe_cols(df, ["prof_id", "prof_cpf", "prof_nome", "foto_url"])
            if "foto_url" not in keep:
                continue
            df = df[keep].copy()
            if "prof_id" in df.columns:
                df["prof_id"] = df["prof_id"].astype(str)
            if "prof_cpf" in df.columns:
                df["prof_cpf"] = df["prof_cpf"].astype(str).map(_only_digits)
            if "prof_nome" in df.columns:
                df["prof_nome"] = df["prof_nome"].astype(str)
            return df.dropna(subset=["foto_url"]).drop_duplicates()
        except Exception as e:
            st.warning(f"Falha ao ler mapa de fotos '{p.name}': {e}")
            continue
    return pd.DataFrame()

# =============================================================
# Leitura local (concat) — pastas do repositório
# =============================================================

@st.cache_data(ttl=600, show_spinner=False)
def read_local_folder(
    folder_path: str,
    preferred_sheet: str | None = None,
    recurse: bool = True,
    patterns: tuple[str, ...] = ("*.xlsx", "*.xls", "*.csv"),
    alt_sheet_names: list[str] | None = None,
) -> pd.DataFrame:
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
                # escolhe a aba
                sheet_to_use = None
                if preferred_sheet and preferred_sheet in xls.sheet_names:
                    sheet_to_use = preferred_sheet
                else:
                    targets = {_slug(nm) for nm in ([preferred_sheet] if preferred_sheet else [])} | {_slug(nm) for nm in alt_sheet_names}
                    for nm in xls.sheet_names:
                        if _slug(nm) in targets:
                            sheet_to_use = nm
                            break
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
# Carregar dados (concat)
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
    # Captura/normaliza coluna de foto no cadastro se existir
    if "foto_url" not in pro.columns:
        for c in PHOTO_COLS:
            if c in pro.columns:
                pro["foto_url"] = pro[c]
                break
    if "prof_id" in pro.columns:
        pro["prof_id"] = pro["prof_id"].astype(str)
    pro = pro.loc[:, ~pro.columns.duplicated()]

if not atd.empty:
    coalesce_inplace(atd, ["os", "os_id", "atendimento_id"], "os_id")

    # Data do atendimento (usa "Data 1" da aba "Clientes")
    coalesce_inplace(atd, ["data_1", "data", "data_do_atendimento", "data_atendimento"], "data_atendimento")
    # >>> usar parser esperto; pode forçar por secrets RAW_DATE_FORMAT ("dmy"|"mdy"|"auto")
    atd["data_atendimento"] = atd["data_atendimento"].apply(lambda v: try_parse_date(v, prefer=PREFER_DATE_ORDER))

    # PROFISSIONAL: usar campos da própria planilha de atendimentos
    coalesce_inplace(atd, ["num_prestador", "id_profissional", "profissional_id", "id_prestador", "id_prof", "prof_id"], "prof_id")
    coalesce_inplace(atd, ["prestador", "profissional", "prof_nome", "nome_prestador"], "prof_nome")

    if "prof_id" in atd.columns:
        atd["prof_id"] = atd["prof_id"].astype(str)

    # Status
    coalesce_inplace(
        atd,
        ["status_servico", "status", "status_do_servico", "situacao", "situacao_servico"],
        "status_servico"
    )

    # Endereço (usar apenas da própria tabela de atendimentos)
    coalesce_inplace(atd, ["endereco", "endereco_completo", "endereco_atendimento"], "endereco")
    coalesce_inplace(atd, ["rua", "logradouro", "endereco_rua", "atendimento_rua"], "rua")
    coalesce_inplace(atd, ["numero", "n", "num", "endereco_numero", "atendimento_numero"], "numero")
    coalesce_inplace(atd, ["bairro", "endereco_bairro", "atendimento_bairro"], "bairro")
    coalesce_inplace(atd, ["cidade", "municipio", "endereco_cidade", "atendimento_cidade"], "cidade")
    # CEP é carregado mas não será mostrado na aba de Foto

    atd.rename(columns={
        "cliente": "cliente_nome",
        "cliente_novo?": "cliente_novo",
        "origem_venda": "origem_venda",
    }, inplace=True)

    coalesce_inplace(atd, ["valor_atendimento", "valor", "valores", "procv_valores", "valor_total"], "valor_atendimento")

    if "os_id" in atd.columns:
        atd["os_id"] = atd["os_id"].astype(str)

    atd = atd.loc[:, ~atd.columns.duplicated()]

if not rec.empty:
    coalesce_inplace(rec, ["atendimento_id", "os", "os_id"], "os_id")
    coalesce_inplace(rec, ["prof_id", "id_profissional", "profissional_id", "id_prof", "num_prestador"], "prof_id")
    if "prof_id" in rec.columns:
        rec["prof_id"] = rec["prof_id"].astype(str)
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
    # >>> parser esperto (pagamento e vencimento)
    if "data_pagamento" in rec.columns:
        rec["data_pagamento"] = rec["data_pagamento"].apply(lambda v: try_parse_date(v, prefer=PREFER_DATE_ORDER))
    if "data_vencimento" in rec.columns:
        rec["data_vencimento"] = rec["data_vencimento"].apply(lambda v: try_parse_date(v, prefer=PREFER_DATE_ORDER))
    rec = rec.loc[:, ~rec.columns.duplicated()]

if not rep.empty:
    coalesce_inplace(rep, ["atendimento_id", "os", "os_id"], "os_id")
    coalesce_inplace(rep, ["prof_id", "id_profissional", "profissional_id", "id_prof", "num_prestador"], "prof_id")
    if "prof_id" in rep.columns:
        rep["prof_id"] = rep["prof_id"].astype(str)
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
    # >>> parser esperto (datas de repasse)
    for dtc in ["data_pagamento_repasse", "data_vencimento_repasse"]:
        if dtc in rep.columns:
            rep[dtc] = rep[dtc].apply(lambda v: try_parse_date(v, prefer=PREFER_DATE_ORDER))
    rep = rep.loc[:, ~rep.columns.duplicated()]

# =============================================================
# Filtro de período (sem sidebar)
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
# >>> limite superior EXCLUSIVO: inclui o dia final completo
dt_fim_excl = pd.to_datetime(sel_fim) + pd.Timedelta(days=1)

# ------- aplica filtro nas tabelas sensíveis ao período -------
# ATENDIMENTOS: usa Data 1 -> data_atendimento
if not atd.empty and "data_atendimento" in atd.columns:
    atd["data_atendimento"] = pd.to_datetime(atd["data_atendimento"], errors="coerce")
    atd_f = atd[(atd["data_atendimento"] >= dt_ini) & (atd["data_atendimento"] < dt_fim_excl)].copy()
else:
    atd_f = atd.copy()

# RECEBER: separar em recebidos (pelo pagamento) e a receber (pelo vencimento)
rec_recebidos_f = pd.DataFrame()
rec_a_receber_f = pd.DataFrame()
if not rec.empty:
    if "data_pagamento" in rec.columns:
        rec["data_pagamento"] = pd.to_datetime(rec["data_pagamento"], errors="coerce")
        rec_recebidos_f = rec[rec["data_pagamento"].notna() &
                              (rec["data_pagamento"] >= dt_ini) &
                              (rec["data_pagamento"] < dt_fim_excl)].copy()
        rec_recebidos_f["categoria_rec"] = "recebido"
    if "data_vencimento" in rec.columns:
        rec["data_vencimento"] = pd.to_datetime(rec["data_vencimento"], errors="coerce")
        base = rec[rec.get("data_pagamento").isna()] if "data_pagamento" in rec.columns else rec
        rec_a_receber_f = base[(base["data_vencimento"] >= dt_ini) &
                               (base["data_vencimento"] < dt_fim_excl)].copy()
        rec_a_receber_f["categoria_rec"] = "a_receber"

# REPASSES: separar pagos (pelo pagamento) e a pagar (pelo vencimento)
rep_pagos_f = pd.DataFrame()
rep_a_pagar_f = pd.DataFrame()
if not rep.empty:
    for _c in ["data_pagamento_repasse", "data_vencimento_repasse"]:
        if _c in rep.columns:
            rep[_c] = pd.to_datetime(rep[_c], errors="coerce")
    if "data_pagamento_repasse" in rep.columns:
        rep_pagos_f = rep[rep["data_pagamento_repasse"].notna() &
                          (rep["data_pagamento_repasse"] >= dt_ini) &
                          (rep["data_pagamento_repasse"] < dt_fim_excl)].copy()
        rep_pagos_f["categoria_rep"] = "pago"
    if "data_vencimento_repasse" in rep.columns:
        base = rep[rep.get("data_pagamento_repasse").isna()] if "data_pagamento_repasse" in rep.columns else rep
        rep_a_pagar_f = base[(base["data_vencimento_repasse"] >= dt_ini) &
                             (base["data_vencimento_repasse"] < dt_fim_excl)].copy()
        rep_a_pagar_f["categoria_rep"] = "a_pagar"

# Para compatibilidade (se algo ainda usa rec_f/rep_f)
rec_f = pd.concat([df for df in [rec_recebidos_f, rec_a_receber_f] if not df.empty], ignore_index=True) \
        if (not rec_recebidos_f.empty or not rec_a_receber_f.empty) else rec.copy()
rep_f = pd.concat([df for df in [rep_pagos_f, rep_a_pagar_f] if not df.empty], ignore_index=True) \
        if (not rep_pagos_f.empty or not rep_a_pagar_f.empty) else rep.copy()

# ========= Financeiro (no período) por OS =========
def _agg_sum(df, key, value_col, newname):
    if df.empty or value_col not in df.columns:
        return pd.DataFrame(columns=[key, newname])
    g = df.groupby(key, as_index=False)[value_col].sum().rename(columns={value_col: newname})
    g[key] = g[key].astype(str)
    return g

# Garantir os_id como string nas bases
for _df in [rec_recebidos_f, rec_a_receber_f, rep_pagos_f, rep_a_pagar_f]:
    if not _df.empty and "os_id" in _df.columns:
        _df["os_id"] = _df["os_id"].astype(str)

rec_pg_ag = _agg_sum(rec_recebidos_f, "os_id", "valor_recebido", "valor_recebido")                 # recebidos (CAIXA)
rec_ar_ag = _agg_sum(rec_a_receber_f, "os_id", "valor_recebido", "valor_a_receber")               # aberto
rep_pg_ag = _agg_sum(rep_pagos_f, "os_id", "valor_repasse", "valor_repasse")                      # pagos (CAIXA)
rep_ap_ag = _agg_sum(rep_a_pagar_f, "os_id", "valor_repasse", "valor_repasse_a_pagar")            # aberto

fin_f = rec_pg_ag.merge(rec_ar_ag, on="os_id", how="outer") \
                 .merge(rep_pg_ag, on="os_id", how="outer") \
                 .merge(rep_ap_ag, on="os_id", how="outer")

for c in ["valor_recebido", "valor_a_receber", "valor_repasse", "valor_repasse_a_pagar"]:
    if c not in fin_f.columns: fin_f[c] = 0.0
fin_f["mc"] = fin_f["valor_recebido"] - fin_f["valor_repasse"]  # MC caixa
fin_f["mc_projetada"] = (fin_f["valor_recebido"] + fin_f["valor_a_receber"]) - (fin_f["valor_repasse"] + fin_f["valor_repasse_a_pagar"])

# =============================================================
# View auxiliar — OS unificada (Atend + Financeiro + Prof)
# =============================================================
# Atendimentos (período) -> leve também prof_id e prof_nome (da própria planilha)
atd_base = pd.DataFrame()
if not atd_f.empty:
    keep_cols = [c for c in [
        "os_id", "prof_id", "prof_nome",
        "cliente_nome", "data_atendimento", "valor_atendimento", "status_servico",
        "endereco", "rua", "numero", "bairro", "cidade", "cep", "complemento"
    ] if c in atd_f.columns]
    atd_base = atd_f[keep_cols].copy()

# Financeiro (período)
fin_base = pd.DataFrame()
if not fin_f.empty:
    fin_base = fin_f[[c for c in [
        "os_id", "valor_recebido", "valor_a_receber", "valor_repasse", "valor_repasse_a_pagar", "mc", "mc_projetada"
    ] if c in fin_f.columns]].copy()

# Profissionais (cadastro completo) — preferir unicidade por prof_id
pro_base = pd.DataFrame()
if not pro.empty:
    cols_pro = safe_cols(pro, ["prof_id", "prof_cpf", "prof_nome",
                               "prof_rua", "prof_bairro", "prof_cidade", "prof_cep",
                               "foto_url"])
    if "prof_id" in pro.columns:
        pro_base = pro[cols_pro].drop_duplicates(subset=["prof_id"])
        pro_base["prof_id"] = pro_base["prof_id"].astype(str)
    elif "prof_cpf" in pro.columns:
        pro_base = pro[cols_pro].drop_duplicates(subset=["prof_cpf"])
    else:
        pro_base = pro[cols_pro].drop_duplicates()

# Incorpora mapa externo de fotos (ID, CPF ou nome)
photo_map_df = load_photo_map()
if not pro_base.empty and not photo_map_df.empty:
    # por prof_id primeiro
    if "prof_id" in pro_base.columns and "prof_id" in photo_map_df.columns:
        tmp_id = photo_map_df[safe_cols(photo_map_df, ["prof_id", "foto_url"])].dropna(subset=["foto_url"]).copy()
        if "prof_id" in tmp_id.columns:
            tmp_id["prof_id"] = tmp_id["prof_id"].astype(str)
        pro_base = pro_base.merge(tmp_id.rename(columns={"foto_url": "foto_url_map_id"}), on="prof_id", how="left")
    # cpf
    if "prof_cpf" in pro_base.columns and "prof_cpf" in photo_map_df.columns:
        tmp = photo_map_df[safe_cols(photo_map_df, ["prof_cpf", "foto_url"])].dropna(subset=["foto_url"]).copy()
        if "prof_cpf" in tmp.columns:
            tmp["prof_cpf"] = tmp["prof_cpf"].astype(str).map(_only_digits)
            pro_base["prof_cpf"] = pro_base["prof_cpf"].astype(str).map(_only_digits)
        pro_base = pro_base.merge(tmp.rename(columns={"foto_url": "foto_url_map"}), on="prof_cpf", how="left")
    # nome
    if "prof_nome" in pro_base.columns and "prof_nome" in photo_map_df.columns:
        tmp2 = photo_map_df[safe_cols(photo_map_df, ["prof_nome", "foto_url"])].dropna(subset=["foto_url"]).copy()
        if not tmp2.empty:
            tmp2["__nome_norm"] = tmp2["prof_nome"].astype(str).map(_norm_text)
            pro_base["__nome_norm"] = pro_base["prof_nome"].astype(str).map(_norm_text)
            pro_base = pro_base.merge(tmp2[["__nome_norm", "foto_url"]].rename(columns={"foto_url": "foto_url_map_nome"}), on="__nome_norm", how="left")

    if "foto_url" not in pro_base.columns:
        pro_base["foto_url"] = np.nan
    pro_base["foto_url"] = (
        pro_base["foto_url"]
        .fillna(pro_base.get("foto_url_map_id"))
        .fillna(pro_base.get("foto_url_map"))
        .fillna(pro_base.get("foto_url_map_nome"))
    )
    for c in ["__nome_norm", "foto_url_map_id", "foto_url_map", "foto_url_map_nome"]:
        if c in pro_base.columns: pro_base.drop(columns=[c], inplace=True)

def merge_profissionais_sem_erros(os_view: pd.DataFrame, pro_base: pd.DataFrame) -> pd.DataFrame:
    """Tenta enriquecer os_view com colunas de pro_base sem causar KeyError."""
    if os_view.empty or pro_base.empty:
        return os_view
    out = os_view.copy()

    # 1) Merge por prof_id, se existir em ambos
    if ("prof_id" in out.columns) and ("prof_id" in pro_base.columns):
        cols_take = safe_cols(pro_base, ["prof_id", "prof_cpf", "foto_url"])
        if cols_take:
            out = out.merge(pro_base[cols_take], on="prof_id", how="left", suffixes=("", "_pro"))

    # 2) Merge por prof_cpf (somente se ainda faltar foto_url e houver cpf em ambos)
    if (("foto_url" not in out.columns) or out["foto_url"].isna().all()) and ("prof_cpf" in out.columns) and ("prof_cpf" in pro_base.columns):
        cols_take = safe_cols(pro_base, ["prof_cpf", "foto_url"])
        if cols_take and ("foto_url" in cols_take):
            tmp = pro_base[cols_take].copy()
            if not tmp.empty:
                tmp["prof_cpf"] = tmp["prof_cpf"].astype(str).map(_only_digits)
                out["prof_cpf"] = out["prof_cpf"].astype(str).map(_only_digits)
                out = out.merge(tmp, on="prof_cpf", how="left", suffixes=("", "_pro2"))
                if "foto_url_pro2" in out.columns and "foto_url" in out.columns:
                    out["foto_url"] = out["foto_url"].fillna(out["foto_url_pro2"])
                    out.drop(columns=[c for c in ["foto_url_pro2"] if c in out.columns], inplace=True)

    # 3) Merge por nome (normalizado)
    need_name_merge = ("foto_url" not in out.columns) or out["foto_url"].isna().all()
    if need_name_merge and ("prof_nome" in out.columns) and ("prof_nome" in pro_base.columns) and ("foto_url" in pro_base.columns):
        tmp = pro_base[safe_cols(pro_base, ["prof_nome", "foto_url"])].copy()
        if not tmp.empty and ("prof_nome" in tmp.columns) and ("foto_url" in tmp.columns):
            tmp["__nome_norm"] = tmp["prof_nome"].astype(str).map(_norm_text)
            out["__nome_norm"] = out["prof_nome"].astype(str).map(_norm_text)
            cols_final = safe_cols(tmp, ["__nome_norm", "foto_url"])
            if set(["__nome_norm", "foto_url"]).issubset(set(cols_final)):
                tmp_final = tmp[cols_final].rename(columns={"foto_url": "foto_url_pro3"})
                out = out.merge(tmp_final, on="__nome_norm", how="left")
                if "foto_url_pro3" in out.columns:
                    if "foto_url" not in out.columns:
                        out["foto_url"] = out["foto_url_pro3"]
                    else:
                        out["foto_url"] = out["foto_url"].fillna(out["foto_url_pro3"])
                out.drop(columns=["__nome_norm", "foto_url_pro3"], inplace=True, errors="ignore")

    return out

# ====== Merge principal OS ======
os_view = pd.DataFrame()
if not atd_base.empty or not fin_base.empty:
    if "os_id" in atd_base.columns: atd_base["os_id"] = atd_base["os_id"].astype(str)
    if "os_id" in fin_base.columns: fin_base["os_id"] = fin_base["os_id"].astype(str)

    # 1) Atend + Fin por os_id
    if ("os_id" in atd_base.columns) and ("os_id" in fin_base.columns):
        os_view = pd.merge(atd_base, fin_base, on="os_id", how="outer")
    else:
        common = [c for c in ["cliente_nome"] if (c in atd_base.columns) and (c in fin_base.columns)]
        os_view = pd.merge(atd_base, fin_base, on=common, how="outer") if common else pd.concat([atd_base.reset_index(drop=True), fin_base.reset_index(drop=True)], axis=1)

    # 2) Enriquecimento com Profissionais — usando função tolerante
    os_view = merge_profissionais_sem_erros(os_view, pro_base)

    os_view = os_view.loc[:, ~os_view.columns.duplicated()]

# =============================================================
# UI — TABS
# =============================================================
st.title("Indicadores — Vavivê")

if all([df.empty for df in [cli, pro, atd_f, rec_f, rep_f]]):
    st.info("Nenhuma base com dados (ou no período selecionado).")

tabs = st.tabs([
    "📋 Visão Geral",
    "👥 Clientes & Regiões",
    "🧑‍💼 Profissionais",
    "🧹 Atendimentos",
    "💰 Financeiro (Recebidos/A Receber & Pagos/A Pagar)",
    "🔎 OS — Detalhe",
    "🖼️ Atendimento + Foto",
])

# Visão Geral
with tabs[0]:
    st.subheader("KPIs")
    status_norm = atd_f.get("status_servico").map(_norm_text) if ("status_servico" in atd_f.columns) else pd.Series(dtype=str)

    # NÃO sensíveis
    total_clientes = int(cli.shape[0]) if not cli.empty else 0
    total_prof = int(pro.shape[0]) if not pro.empty else 0

    # Atendimentos (período)
    concl = int((status_norm == "concluido").sum()) if not atd_f.empty else 0
    agend = int((status_norm == "agendado").sum()) if not atd_f.empty else 0
    canc  = int((status_norm == "cancelado").sum()) if not atd_f.empty else 0

    # Caixa do período
    receita     = float(rec_recebidos_f.get("valor_recebido", pd.Series(dtype=float)).sum()) if not rec_recebidos_f.empty else 0.0
    a_receber   = float(rec_a_receber_f.get("valor_recebido", pd.Series(dtype=float)).sum()) if not rec_a_receber_f.empty else 0.0
    repasses    = float(rep_pagos_f.get("valor_repasse", pd.Series(dtype=float)).sum()) if not rep_pagos_f.empty else 0.0
    a_pagar     = float(rep_a_pagar_f.get("valor_repasse", pd.Series(dtype=float)).sum()) if not rep_a_pagar_f.empty else 0.0
    mc_caixa    = receita - repasses
    mc_proj     = (receita + a_receber) - (repasses + a_pagar)

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Clientes (cadastro)", f"{total_clientes:,}".replace(",", "."))
    c2.metric("Profissionais (cadastro)", f"{total_prof:,}".replace(",", "."))
    c3.metric("Concluídos (período)", f"{concl:,}".replace(",", "."))
    c4.metric("Agendados (período)", f"{agend:,}".replace(",", "."))
    c5.metric("Cancelados (período)", f"{canc:,}".replace(",", "."))
    c6.metric("MC (Caixa, período)", f"R$ {mc_caixa:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

    st.caption(f"MC projetada (recebidos+a receber − pagos−a pagar): R$ {mc_proj:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

# Clientes & Regiões (NÃO sensível ao período)
with tabs[1]:
    st.subheader("Clientes (cadastro completo)")
    if cli.empty:
        st.warning("Base de Clientes não carregada.")
    else:
        col_origem = next((c for c in ["origem_cliente", "origem"] if c in cli.columns), None)
        if col_origem:
            origem_counts = (
                cli[col_origem].fillna("(não informado)").replace({"": "(não informado)"}).value_counts().reset_index()
            )
            origem_counts.columns = ["origem", "quantidade"]
            if USE_PLOTLY:
                fig = px.bar(origem_counts, x="origem", y="quantidade", title="Origem dos Clientes (cadastro)", text_auto=True)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.bar_chart(origem_counts.set_index("origem")["quantidade"])
        else:
            st.info("Coluna de origem do cliente não encontrada.")

        st.markdown("---")
        st.subheader("Regiões (cadastro)")
        col_bairro = next((cc for c in ["bairro", "endereco_bairro", "endereco-1-bairro"] if (cc := _slug(c)) in cli.columns), None)
        col_cidade = "cidade" if "cidade" in cli.columns else None
        cols = st.columns(2)
        if col_bairro:
            bairro_counts = cli[col_bairro].fillna("(sem bairro)").astype(str).replace({"": "(sem bairro)"}).value_counts().reset_index()
            bairro_counts.columns = ["bairro", "clientes"]
            if USE_PLOTLY:
                fig_b = px.bar(bairro_counts.head(20), x="bairro", y="clientes", title="Top Bairros por Clientes (cadastro)", text_auto=True)
                cols[0].plotly_chart(fig_b, use_container_width=True)
            else:
                cols[0].bar_chart(bairro_counts.set_index("bairro")["clientes"])
        else:
            cols[0].info("Coluna de bairro não encontrada.")
        if col_cidade:
            cidade_counts = cli[col_cidade].fillna("(sem cidade)").astype(str).replace({"": "(sem cidade)"}).value_counts().reset_index()
            cidade_counts.columns = ["cidade", "clientes"]
            if USE_PLOTLY:
                fig_c = px.bar(cidade_counts, x="cidade", y="clientes", title="Clientes por Cidade (cadastro)", text_auto=True)
                cols[1].plotly_chart(fig_c, use_container_width=True)
            else:
                cols[1].bar_chart(cidade_counts.set_index("cidade")["clientes"])
        else:
            cols[1].info("Coluna de cidade não encontrada.")

# Profissionais (NÃO sensível ao período)
with tabs[2]:
    st.subheader("Profissionais (cadastro completo)")
    if pro.empty and atd_f.empty:
        st.warning("Base de Profissionais não carregada.")
    else:
        cols = st.columns(3)
        total_prof = int(pro.shape[0]) if not pro.empty else 0
        cols[0].metric("Total de Profissionais (cadastro)", f"{total_prof:,}".replace(",", "."))
        if not atd_f.empty and "status_servico" in atd_f.columns:
            status_norm2 = atd_f["status_servico"].map(_norm_text)
            concluidos = (status_norm2 == "concluido").sum()
            cols[1].metric("Atendimentos Concluídos (período)", f"{int(concluidos):,}".replace(",", "."))
        else:
            cols[1].metric("Atendimentos Concluídos (período)", "0")
        if not pro.empty and {"att_feitos", "att_recusados"} <= set(pro.columns):
            feitos = pro["att_feitos"].fillna(0).astype(float).sum()
            recusados = pro["att_recusados"].fillna(0).astype(float).sum()
            taxa = (recusados / (feitos + recusados) * 100) if (feitos + recusados) > 0 else 0
            cols[2].metric("Taxa de Recusa (cadastro)", f"{taxa:.1f}%")
        else:
            cols[2].metric("Taxa de Recusa (cadastro)", "—")

        st.markdown("---")
        st.caption("Cadastro completo de profissionais; métricas de atendimentos são do período.")

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

# Financeiro (PERÍODO) — visão de caixa e aberto
with tabs[4]:
    st.subheader("Recebidos/A Receber & Pagos/A Pagar (período)")

    receita     = float(rec_recebidos_f.get("valor_recebido", pd.Series(dtype=float)).sum()) if not rec_recebidos_f.empty else 0.0
    a_receber   = float(rec_a_receber_f.get("valor_recebido", pd.Series(dtype=float)).sum()) if not rec_a_receber_f.empty else 0.0
    repasses    = float(rep_pagos_f.get("valor_repasse", pd.Series(dtype=float)).sum()) if not rep_pagos_f.empty else 0.0
    a_pagar     = float(rep_a_pagar_f.get("valor_repasse", pd.Series(dtype=float)).sum()) if not rep_a_pagar_f.empty else 0.0
    mc_caixa    = receita - repasses
    mc_proj     = (receita + a_receber) - (repasses + a_pagar)

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Recebidos (caixa)", f"R$ {receita:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    c2.metric("A Receber (aberto)", f"R$ {a_receber:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    c3.metric("Repasses Pagos", f"R$ {repasses:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    c4.metric("Repasses a Pagar", f"R$ {a_pagar:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    c5.metric("MC (Caixa)", f"R$ {mc_caixa:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    c6.metric("MC Projetada", f"R$ {mc_proj:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

    st.markdown("---")
    if not fin_f.empty:
        st.caption("Por atendimento (OS) — dentro do período")
        show_cols = [c for c in [
            "os_id", "valor_recebido", "valor_a_receber",
            "valor_repasse", "valor_repasse_a_pagar",
            "mc", "mc_projetada"
        ] if c in fin_f.columns]
        fin_view = fin_f.loc[:, ~fin_f.columns.duplicated()]
        st.dataframe(fin_view[show_cols].sort_values("mc", ascending=False).reset_index(drop=True))

    charts = st.columns(2)
    if not rec_recebidos_f.empty and "valor_recebido" in rec_recebidos_f.columns and "data_pagamento" in rec_recebidos_f.columns:
        rec_serie = rec_recebidos_f.copy()
        rec_serie["mes"] = pd.to_datetime(rec_serie["data_pagamento"], errors="coerce").dt.to_period("M").dt.to_timestamp()
        g = rec_serie.groupby("mes")["valor_recebido"].sum().reset_index()
        if USE_PLOTLY:
            charts[0].plotly_chart(px.bar(g, x="mes", y="valor_recebido", title="Recebidos por Mês (caixa)"), use_container_width=True)
        else:
            charts[0].bar_chart(g.set_index("mes")["valor_recebido"])
    if not rep_pagos_f.empty and "valor_repasse" in rep_pagos_f.columns and "data_pagamento_repasse" in rep_pagos_f.columns:
        rep_serie = rep_pagos_f.copy()
        rep_serie["mes"] = pd.to_datetime(rep_serie["data_pagamento_repasse"], errors="coerce").dt.to_period("M").dt.to_timestamp()
        g2 = rep_serie.groupby("mes")["valor_repasse"].sum().reset_index()
        if USE_PLOTLY:
            charts[1].plotly_chart(px.bar(g2, x="mes", y="valor_repasse", title="Repasses Pagos por Mês (caixa)"), use_container_width=True)
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
            v_rec   = float(reg.get("valor_recebido", 0) or 0)
            v_ar    = float(reg.get("valor_a_receber", 0) or 0)
            v_rep   = float(reg.get("valor_repasse", 0) or 0)
            v_ap    = float(reg.get("valor_repasse_a_pagar", 0) or 0)
            mc      = float(reg.get("mc", v_rec - v_rep))
            mc_proj = float(reg.get("mc_projetada", (v_rec + v_ar) - (v_rep + v_ap)))
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Valor do Atendimento", ("R$ %0.2f" % v_atend).replace(".", ",") if not np.isnan(v_atend) else "—")
            k2.metric("Recebidos (OS)", ("R$ %0.2f" % v_rec).replace(".", ","))
            k3.metric("Repasses Pagos (OS)", ("R$ %0.2f" % v_rep).replace(".", ","))
            k4.metric("MC (Caixa, OS)", ("R$ %0.2f" % mc).replace(".", ","))
            st.caption(f"MC projetada (OS): {('R$ %0.2f' % mc_proj).replace('.', ',')}")
            st.markdown("---")
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("### Cliente & Atendimento")
                st.write({
                    "OS": reg.get("os_id"),
                    "Cliente": reg.get("cliente_nome"),
                    "Data do Atendimento": (pd.to_datetime(reg.get("data_atendimento")).strftime('%d/%m/%Y') if pd.notna(reg.get("data_atendimento")) else "—"),
                    "Status": reg.get("status_servico"),
                    "Endereço": reg.get("endereco") or reg.get("rua"),
                    "Bairro": reg.get("bairro"),
                    "Cidade": reg.get("cidade"),
                    "CEP": reg.get("cep"),
                })
            with c2:
                st.markdown("### Profissional & Endereço (cadastro)")
                st.write({
                    "Profissional": reg.get("prof_nome"),
                    "CPF Profissional": reg.get("prof_cpf"),
                    "Endereço Profissional": reg.get("prof_rua"),
                    "Bairro Profissional": reg.get("prof_bairro"),
                    "Cidade Profissional": reg.get("prof_cidade"),
                    "CEP Profissional": reg.get("prof_cep"),
                })

# Atendimento + Foto (PERÍODO) — APENAS dados do atendimento (sem CEP e sem Financeiro)
with tabs[6]:
    st.subheader("Atendimento + Foto")
    if os_view.empty:
        st.info("Não há dados suficientes no período selecionado.")
    else:
        os_view["os_id"] = os_view["os_id"].astype(str)
        sel_os2 = st.selectbox("Selecione a OS (com foto)", options=sorted(os_view["os_id"].dropna().unique().tolist()), key="os_foto")
        registro2 = os_view[os_view["os_id"] == str(sel_os2)].copy()
        if registro2.empty:
            st.warning("OS não encontrada na seleção.")
        else:
            reg = registro2.iloc[0]

            # ----- Dados do atendimento (da própria tabela) -----
            cliente_nome = reg.get("cliente_nome")
            dt_txt = pd.to_datetime(reg.get("data_atendimento")).strftime('%d/%m/%Y') if pd.notna(reg.get("data_atendimento")) else "—"
            status = reg.get("status_servico")
            endereco = reg.get("endereco") or reg.get("rua")
            bairro = reg.get("bairro")
            cidade = reg.get("cidade")

            # ----- Profissional da própria tabela de atendimentos -----
            prof_id_show = reg.get("prof_id")      # Num Prestador normalizado
            prof_nome_show = reg.get("prof_nome")  # Prestador
            prof_cpf_show = reg.get("prof_cpf")
            if (not isinstance(prof_cpf_show, str) or not prof_cpf_show.strip()) and isinstance(prof_id_show, (str,int,float)):
                pid = str(prof_id_show)
                if not pro_base.empty and "prof_id" in pro_base.columns:
                    rowp = pro_base[pro_base["prof_id"].astype(str) == pid]
                    if not rowp.empty:
                        prof_cpf_show = rowp.iloc[0].get("prof_cpf") or prof_cpf_show
                        if not prof_nome_show or str(prof_nome_show).strip() == "":
                            prof_nome_show = rowp.iloc[0].get("prof_nome") or prof_nome_show

            left, right = st.columns([2, 1])
            with left:
                st.markdown(f"#### OS #{reg.get('os_id','')} — {cliente_nome or ''}")
                st.write({
                    "Data": dt_txt,
                    "Status": status,
                    "Endereço": endereco or "—",
                    "Bairro": bairro or "—",
                    "Cidade": cidade or "—",
                    "Profissional": prof_nome_show or "—",
                    "ID Profissional": str(prof_id_show) if pd.notna(prof_id_show) else "—",
                    "CPF Profissional": prof_cpf_show or "—",
                })

            with right:
                # 1) foto no registro da OS
                foto_url = None
                for c in ["foto_url", "foto", "imagem_url", "imagem", "url_foto", "link_foto", "photo_url", "avatar_url"]:
                    if c in registro2.columns:
                        val = reg.get(c)
                        if isinstance(val, str) and val.strip():
                            foto_url = val.strip()
                            break

                # 2) se não houver, busca no cadastro por prof_id > cpf > nome
                if not foto_url and not pro_base.empty:
                    found = False
                    if isinstance(prof_id_show, (str,int,float)) and "prof_id" in pro_base.columns:
                        pid = str(prof_id_show)
                        rowp = pro_base[pro_base["prof_id"].astype(str) == pid]
                        if not rowp.empty:
                            foto_url = rowp.iloc[0].get("foto_url"); found = True
                    if (not found) and isinstance(prof_cpf_show, str) and "prof_cpf" in pro_base.columns and prof_cpf_show.strip():
                        cpf_d = _only_digits(prof_cpf_show)
                        rowp = pro_base[pro_base["prof_cpf"].astype(str).map(_only_digits) == cpf_d]
                        if not rowp.empty:
                            foto_url = rowp.iloc[0].get("foto_url"); found = True
                    if (not found) and isinstance(prof_nome_show, str) and "prof_nome" in pro_base.columns and prof_nome_show.strip():
                        nome_n = _norm_text(prof_nome_show)
                        rowp = pro_base[pro_base["prof_nome"].astype(str).map(_norm_text) == nome_n]
                        if not rowp.empty:
                            foto_url = rowp.iloc[0].get("foto_url")

                # 3) fallback: template
                if not foto_url:
                    foto_url = build_photo_from_template(
                        prof_id=prof_id_show,
                        prof_cpf=prof_cpf_show,
                        os_id=reg.get("os_id"),
                    )

                if isinstance(foto_url, str) and foto_url.startswith("http"):
                    st.image(foto_url, caption=(prof_nome_show or "Profissional"), use_column_width=True)
                    st.caption("Fonte: Atendimentos / cadastro (Profissionais) / Carteirinhas.xlsx ou PHOTO_URL_TEMPLATE.")
                elif isinstance(foto_url, str) and foto_url:
                    st.write("**Link da foto:**", foto_url)
                else:
                    st.info("Sem foto para esta profissional. Garanta `prof_id` na OS e um mapeamento em Carteirinhas.xlsx (`prof_id` → `foto_url`).")

st.markdown("---")
st.caption("© Vavivê — Dashboard. Clientes/Profissionais não filtram por período; Atendimentos/Financeiro/OS sim. Aba 'Atendimento + Foto' usa apenas dados do atendimento e foto da profissional.")
