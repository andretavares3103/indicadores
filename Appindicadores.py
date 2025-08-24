# -*- coding: utf-8 -*-
# -------------------------------------------------------------
# Vavivê — Dashboard de Indicadores (Streamlit)
# -------------------------------------------------------------
# Fontes suportadas (selecionáveis na sidebar):
#   1) Drive fixo (IDs nas Secrets)
#   2) Drive (IDs na sidebar)
#   3) Upload manual (arquivos .xlsx)
# -------------------------------------------------------------

import streamlit as st
import pandas as pd
import numpy as np
import unicodedata
from datetime import datetime, date
from dateutil import parser
import io

# Plotly com fallback automático
try:
    import plotly.express as px
    USE_PLOTLY = True
except Exception:
    USE_PLOTLY = False
    st.warning("Plotly não está instalado. Usando gráficos nativos do Streamlit como fallback.")

# Google Drive libs (opcionais)
try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
    USE_GDRIVE_LIBS = True
except Exception:
    USE_GDRIVE_LIBS = False

st.set_page_config(
    page_title="Vavivê | Indicadores",
    page_icon="🧹",
    layout="wide",
    initial_sidebar_state="expanded",
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

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if df.empty:
        return df
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
    for c in candidates:
        if c in df.columns:
            df[new] = df[c]
            return df
    if new not in df.columns:
        df[new] = np.nan
    return df

# =============================================================
# Google Drive — auth/listagem/leitura (compatível com Shared Drives)
# =============================================================

def get_drive_service():
    """Cria o client do Drive e valida o acesso incluindo Shared Drives."""
    if not USE_GDRIVE_LIBS:
        st.error("Bibliotecas Google não instaladas (google-api-python-client, google-auth, ...).")
        return None
    try:
        info = st.secrets.get("gdrive_service_account", None)
        if info is None:
            st.error("Secret 'gdrive_service_account' não encontrada.")
            return None
        if isinstance(info, str):
            import json
            info = json.loads(info)
        creds = service_account.Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
        )
        service = build("drive", "v3", credentials=creds, cache_discovery=False)
        # Validação rápida (considerando Shared Drives)
        service.files().list(
            pageSize=1,
            fields="files(id)",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            corpora="allDrives",
        ).execute()
        return service
    except Exception as e:
        st.error(f"Falha autenticando no Drive: {type(e).__name__}: {e}")
        return None

@st.cache_data(ttl=600, show_spinner=False)
def drive_list_files(folder_id: str, recurse: bool = False, max_depth: int = 10):
    service = get_drive_service()
    if service is None or not folder_id:
        return []

    def _list_children(fid):
        q = f"'{fid}' in parents and trashed = false"
        fields = "nextPageToken, files(id, name, mimeType, modifiedTime)"
        files = []
        page_token = None
        while True:
            resp = service.files().list(
                q=q,
                fields=fields,
                pageToken=page_token,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                corpora="allDrives",
            ).execute()
            files.extend(resp.get("files", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return files

    results = []
    if not recurse:
        results = _list_children(folder_id)
    else:
        # BFS em subpastas
        queue = [(folder_id, 0)]
        while queue:
            fid, depth = queue.pop(0)
            try:
                children = _list_children(fid)
            except Exception:
                children = []
            for item in children:
                if item.get("mimeType") == "application/vnd.google-apps.folder":
                    if depth < max_depth:
                        queue.append((item.get("id"), depth + 1))
                else:
                    results.append(item)

    results.sort(key=lambda x: x.get("modifiedTime", ""), reverse=True)
    return results

def _drive_download_bytes(file_id: str, mime_type: str) -> bytes:
    service = get_drive_service()
    if service is None:
        return b""
    buf = io.BytesIO()
    if mime_type == "application/vnd.google-apps.spreadsheet":
        req = service.files().export_media(
            fileId=file_id,
            mimeType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            supportsAllDrives=True,
        )
    else:
        req = service.files().get_media(
            fileId=file_id,
            supportsAllDrives=True,
        )
    downloader = MediaIoBaseDownload(buf, req)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    return buf.getvalue()

def read_drive_folder(folder_id: str, preferred_sheet: str | None = None, mode: str = "latest", recurse: bool = False) -> pd.DataFrame:
    """Lê arquivos (Excel/Google Sheets/CSV) de uma pasta do Drive (opcionalmente recursiva).
    mode: 'latest' pega o mais recente; 'concat' concatena todos.
    """
    files = drive_list_files(folder_id, recurse=recurse)
    if not files:
        return pd.DataFrame()

    allowed = {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
        "application/vnd.ms-excel": "xls",
        "text/csv": "csv",
        "application/vnd.google-apps.spreadsheet": "gsheet",
    }

    dfs = []
    take = files if mode == "concat" else [files[0]]
    for f in take:
        mt = f.get("mimeType")
        if mt not in allowed:
            continue
        raw = _drive_download_bytes(f["id"], mt)
        if not raw:
            continue
        bio = io.BytesIO(raw)
        try:
            if mt == "text/csv":
                df = pd.read_csv(bio)
            elif mt == "application/vnd.ms-excel":
                try:
                    xls = pd.ExcelFile(bio, engine="xlrd")
                    first = xls.sheet_names[0] if preferred_sheet is None else preferred_sheet
                    df = pd.read_excel(bio, sheet_name=first, engine="xlrd")
                except Exception:
                    first = None
                    try:
                        xls = pd.ExcelFile(bio)
                        first = xls.sheet_names[0]
                    except Exception:
                        pass
                    df = pd.read_excel(bio, sheet_name=(preferred_sheet or first))
            else:
                if preferred_sheet is None:
                    xls = pd.ExcelFile(bio)
                    df = pd.read_excel(xls, sheet_name=xls.sheet_names[0])
                else:
                    df = pd.read_excel(bio, sheet_name=preferred_sheet)
            df["_source_file"] = f.get("name")
            df["_modified"] = f.get("modifiedTime")
            dfs.append(df)
        except Exception as e:
            st.warning(f"Falha ao ler {f.get('name')}: {e}")
            continue
    if not dfs:
        return pd.DataFrame()
    return pd.concat(dfs, ignore_index=True, sort=False) if mode == "concat" else dfs[0]

# =============================================================
# Local files (upload)
# =============================================================

def load_excel(uploaded_file, fallback_path=None, sheet=None) -> pd.DataFrame:
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

# =============================================================
# Sidebar — seleção da fonte
# =============================================================

st.sidebar.header("⚙️ Fonte dos dados")
fonte = st.sidebar.radio(
    "Escolha a origem:",
    ["Drive fixo (Secrets)", "Drive (IDs na sidebar)", "Upload manual"],
    index=0,
)

# Configurações padrão (Secrets)
DEFAULT_FOLDER_IDS = {
    "clientes":      st.secrets.get("GDRIVE_CLIENTES_FOLDER_ID", ""),
    "profissionais": st.secrets.get("GDRIVE_PROFISSIONAIS_FOLDER_ID", ""),
    "atendimentos":  st.secrets.get("GDRIVE_ATENDIMENTOS_FOLDER_ID", ""),
    "receber":       st.secrets.get("GDRIVE_RECEBER_FOLDER_ID", ""),
    "repasses":      st.secrets.get("GDRIVE_REPASSES_FOLDER_ID", ""),
}

# Parâmetros comuns
mode = st.sidebar.selectbox("Modo de leitura", ["concat (todos os arquivos)", "latest (apenas o mais recente)"], index=0)
GDRIVE_MODE = "concat" if mode.startswith("concat") else "latest"
GDRIVE_RECURSE = st.sidebar.checkbox("Buscar em subpastas (recursivo)", value=True)

# Inputs conforme a fonte
uploaded = {}
folder_ids = DEFAULT_FOLDER_IDS.copy()

if fonte == "Drive (IDs na sidebar)":
    st.sidebar.caption("Cole os IDs das pastas do Drive (ou deixe os que já vieram das Secrets).")
    folder_ids["clientes"]      = st.sidebar.text_input("Pasta — Clientes",      DEFAULT_FOLDER_IDS["clientes"])
    folder_ids["profissionais"] = st.sidebar.text_input("Pasta — Profissionais", DEFAULT_FOLDER_IDS["profissionais"])
    folder_ids["atendimentos"]  = st.sidebar.text_input("Pasta — Atendimentos",  DEFAULT_FOLDER_IDS["atendimentos"])
    folder_ids["receber"]       = st.sidebar.text_input("Pasta — Contas a Receber", DEFAULT_FOLDER_IDS["receber"])
    folder_ids["repasses"]      = st.sidebar.text_input("Pasta — Repasses",      DEFAULT_FOLDER_IDS["repasses"])
elif fonte == "Upload manual":
    st.sidebar.caption("Envie os arquivos .xlsx (abas padrão podem ser alteradas abaixo).")
    uploaded["clientes"] = st.sidebar.file_uploader("Clientes.xlsx", type=["xlsx", "xls", "csv"], key="up_cli")
    uploaded["prof"]     = st.sidebar.file_uploader("Profissionais.xlsx", type=["xlsx", "xls", "csv"], key="up_pro")
    uploaded["atend"]    = st.sidebar.file_uploader("Atendimentos_*.xlsx", type=["xlsx", "xls", "csv"], key="up_atd")
    uploaded["receber"]  = st.sidebar.file_uploader("Receber_*.xlsx", type=["xlsx", "xls", "csv"], key="up_rec")
    uploaded["repasses"] = st.sidebar.file_uploader("Repasses_*.xlsx", type=["xlsx", "xls", "csv"], key="up_rep")
    st.sidebar.markdown("**Abas (opcional)**")
    sheet_atd = st.sidebar.text_input("Aba de Atendimentos", "Clientes")
    sheet_fin = st.sidebar.text_input("Aba de Financeiro (Receber/Repasses)", "Dados Financeiros")
else:
    # Drive fixo (Secrets)
    pass

# =============================================================
# Diagnóstico
# =============================================================
with st.expander("🔧 Diagnóstico Google Drive"):
    st.write("Libs Google importadas?", USE_GDRIVE_LIBS)
    has_secret = "gdrive_service_account" in st.secrets
    st.write("Secret presente?", has_secret)
    if has_secret:
        try:
            import json
            _info = st.secrets.get("gdrive_service_account")
            if isinstance(_info, str):
                _info = json.loads(_info)
            st.write("client_email:", _info.get("client_email", "(vazio)"))
        except Exception as e:
            st.error(f"Erro lendo secret: {e}")
    if fonte != "Upload manual":
        svc = get_drive_service()
        st.write("Service account autenticada?", bool(svc))
        if svc:
            for nome, fid in folder_ids.items():
                try:
                    files = drive_list_files(fid, recurse=GDRIVE_RECURSE)
                    st.write(f"{nome}: {len(files)} arquivo(s) visível(is)")
                    if files:
                        st.write("Mais recente:", files[0].get("name"), files[0].get("modifiedTime"))
                except Exception as e:
                    st.error(f"Falha ao listar {nome}: {e}")

# =============================================================
# Carregar dados conforme a FONTE selecionada
# =============================================================
if fonte == "Upload manual":
    raw_clientes = load_excel(uploaded.get("clientes"))
    raw_prof     = load_excel(uploaded.get("prof"))
    raw_atend    = load_excel(uploaded.get("atend"),    sheet=(sheet_atd or None))
    raw_receber  = load_excel(uploaded.get("receber"),  sheet=(sheet_fin or None))
    raw_repasses = load_excel(uploaded.get("repasses"), sheet=(sheet_fin or None))
else:
    # Google Drive (via Secrets ou IDs na sidebar)
    raw_clientes = read_drive_folder(folder_ids.get("clientes", ""),     preferred_sheet=None,                 mode=GDRIVE_MODE, recurse=GDRIVE_RECURSE)
    raw_prof     = read_drive_folder(folder_ids.get("profissionais", ""), preferred_sheet=None,                 mode=GDRIVE_MODE, recurse=GDRIVE_RECURSE)
    raw_atend    = read_drive_folder(folder_ids.get("atendimentos", ""),  preferred_sheet="Clientes",          mode=GDRIVE_MODE, recurse=GDRIVE_RECURSE)
    raw_receber  = read_drive_folder(folder_ids.get("receber", ""),       preferred_sheet="Dados Financeiros", mode=GDRIVE_MODE, recurse=GDRIVE_RECURSE)
    raw_repasses = read_drive_folder(folder_ids.get("repasses", ""),      preferred_sheet="Dados Financeiros", mode=GDRIVE_MODE, recurse=GDRIVE_RECURSE)

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
# Filtros globais por data
# =============================================================
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
if not fin_f.empty:
    fin_f["_data"] = pd.NaT
    if "data_pagamento" in fin_f.columns:
        fin_f["_data"] = fin_f["data_pagamento"].fillna(fin_f.get("data_vencimento"))
    if "data_pagamento_repasse" in fin_f.columns:
        fin_f["_data"] = fin_f["_data"].fillna(fin_f["data_pagamento_repasse"]).fillna(fin_f.get("data_vencimento_repasse"))
    fin_f = fin_f[(pd.to_datetime(fin_f["_data"], errors="coerce") >= pd.to_datetime(sel_ini)) & (pd.to_datetime(fin_f["_data"], errors="coerce") <= pd.to_datetime(sel_fim))]

# =============================================================
# View auxiliar — OS unificada (Atend + Financeiro + Prof)
# =============================================================
atd_base = pd.DataFrame()
if not atd_f.empty:
    keep_cols = [c for c in ["os_id", "cliente_nome", "data_atendimento", "valor_atendimento", "endereco", "rua", "bairro", "cidade", "cep", "complemento"] if c in atd_f.columns]
    atd_base = atd_f[keep_cols].copy()

fin_base = pd.DataFrame()
if not fin_f.empty:
    fin_base = fin_f[[c for c in ["os_id", "cliente_nome", "valor_recebido", "valor_repasse", "mc", "profissional_nome", "prof_cpf"] if c in fin_f.columns]].copy()

pro_base = pd.DataFrame()
if not pro.empty:
    if "prof_cpf" in pro.columns:
        pro_base = pro[[c for c in ["prof_cpf", "prof_nome", "prof_rua", "prof_bairro", "prof_cidade", "prof_cep"] if c in pro.columns]].drop_duplicates(subset=["prof_cpf"])
    else:
        pro_base = pro[[c for c in ["prof_nome", "prof_rua", "prof_bairro", "prof_cidade", "prof_cep"] if c in pro.columns]].drop_duplicates()

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
# UI — TABS
# =============================================================
st.title("Indicadores — Vavivê")

if all([df.empty for df in [cli, pro, atd, rec, rep]]):
    st.info("Envie/aponte as bases para visualizar os indicadores.")

tabs = st.tabs([
    "📋 Visão Geral",
    "👥 Clientes & Regiões",
    "🧑‍💼 Profissionais",
    "🧹 Atendimentos",
    "💰 Financeiro (Receber & Repasses)",
    "🔎 OS — Detalhe",
])

# Visão Geral
with tabs[0]:
    st.subheader("KPIs do Período")
    total_clientes = int(cli.shape[0]) if not cli.empty else 0
    total_prof = int(pro.shape[0]) if not pro.empty else 0
    concl = int(atd_f[atd_f.get("status_servico").fillna("").str.lower().eq("concluido")].shape[0]) if not atd_f.empty and "status_servico" in atd_f.columns else 0
    agend = int(atd_f[atd_f.get("status_servico").fillna("").str.lower().eq("agendado")].shape[0]) if not atd_f.empty and "status_servico" in atd_f.columns else 0
    canc  = int(atd_f[atd_f.get("status_servico").fillna("").str.lower().eq("cancelado")].shape[0]) if not atd_f.empty and "status_servico" in atd_f.columns else 0

    receita = float(rec_f.get("valor_recebido").sum()) if not rec_f.empty and "valor_recebido" in rec_f.columns else 0.0
    repasses = float(rep_f.get("valor_repasse").sum()) if not rep_f.empty and "valor_repasse" in rep_f.columns else 0.0
    mc_total = float(fin_f.get("mc").sum()) if not fin_f.empty and "mc" in fin_f.columns else (receita - repasses)

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Clientes (cadastro)", f"{total_clientes:,}".replace(",", "."))
    c2.metric("Profissionais (cadastro)", f"{total_prof:,}".replace(",", "."))
    c3.metric("Atendimentos Concluídos", f"{concl:,}".replace(",", "."))
    c4.metric("Agendados", f"{agend:,}".replace(",", "."))
    c5.metric("Cancelados", f"{canc:,}".replace(",", "."))
    c6.metric("Margem de Contribuição", f"R$ {mc_total:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

    st.markdown("---")
    st.caption("MC = Receita (Contas a Receber) − Repasses às Profissionais.")

# Clientes & Regiões
with tabs[1]:
    st.subheader("Clientes")
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
                fig = px.bar(origem_counts, x="origem", y="quantidade", title="Origem dos Clientes", text_auto=True)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.bar_chart(origem_counts.set_index("origem")["quantidade"])
        else:
            st.info("Coluna de origem do cliente não encontrada.")

        st.markdown("---")
        st.subheader("Regiões")
        col_bairro = next((cc for c in ["bairro", "endereco_bairro", "endereco-1-bairro"] if (cc := _slug(c)) in cli.columns), None)
        col_cidade = "cidade" if "cidade" in cli.columns else None
        cols = st.columns(2)
        if col_bairro:
            bairro_counts = cli[col_bairro].fillna("(sem bairro)").astype(str).replace({"": "(sem bairro)"}).value_counts().reset_index()
            bairro_counts.columns = ["bairro", "clientes"]
            if USE_PLOTLY:
                fig_b = px.bar(bairro_counts.head(20), x="bairro", y="clientes", title="Top Bairros por Clientes", text_auto=True)
                cols[0].plotly_chart(fig_b, use_container_width=True)
            else:
                cols[0].bar_chart(bairro_counts.set_index("bairro")["clientes"])
        else:
            cols[0].info("Coluna de bairro não encontrada.")
        if col_cidade:
            cidade_counts = cli[col_cidade].fillna("(sem cidade)").astype(str).replace({"": "(sem cidade)"}).value_counts().reset_index()
            cidade_counts.columns = ["cidade", "clientes"]
            if USE_PLOTLY:
                fig_c = px.bar(cidade_counts, x="cidade", y="clientes", title="Clientes por Cidade", text_auto=True)
                cols[1].plotly_chart(fig_c, use_container_width=True)
            else:
                cols[1].bar_chart(cidade_counts.set_index("cidade")["clientes"])
        else:
            cols[1].info("Coluna de cidade não encontrada.")

# Profissionais
with tabs[2]:
    st.subheader("Profissionais")
    if pro.empty and atd_f.empty:
        st.warning("Bases de Profissionais e Atendimentos não carregadas.")
    else:
        cols = st.columns(3)
        total_prof = int(pro.shape[0]) if not pro.empty else 0
        cols[0].metric("Total de Profissionais (cadastro)", f"{total_prof:,}".replace(",", "."))
        if not atd_f.empty and "status_servico" in atd_f.columns:
            concluidos = atd_f[atd_f["status_servico"].str.lower() == "concluido"]
            cols[1].metric("Atendimentos Concluídos (período)", f"{concluidos.shape[0]:,}".replace(",", "."))
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
        st.caption("Quando a OS trouxer o ID/CPF da profissional, o ranking detalhado aparecerá aqui.")

# Atendimentos
with tabs[3]:
    st.subheader("Atendimentos")
    if atd_f.empty:
        st.warning("Base de Atendimentos não carregada ou sem dados no período.")
    else:
        if {"data_atendimento", "status_servico"} <= set(atd_f.columns):
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

# Financeiro
with tabs[4]:
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
        inad = 0
        if not rec_f.empty and {"data_vencimento", "data_pagamento"} <= set(rec_f.columns):
            hoje = pd.Timestamp.today().normalize()
            pend = rec_f[(rec_f["data_pagamento"].isna()) & (pd.to_datetime(rec_f["data_vencimento"], errors="coerce") < hoje)]
            inad = float(pend.get("valor_recebido").sum()) if "valor_recebido" in pend.columns else 0.0
        c4.metric("Inadimplência (valor em aberto)", f"R$ {inad:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

        st.markdown("---")
        if not fin_f.empty:
            st.caption("Tabela por atendimento (OS) — Receita x Repasse x MC")
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
                charts[0].plotly_chart(px.bar(g, x="mes", y="valor_recebido", title="Receita por Mês"), use_container_width=True)
            else:
                charts[0].bar_chart(g.set_index("mes")["valor_recebido"])
        if not rep_f.empty and "valor_repasse" in rep_f.columns and "data_pagamento_repasse" in rep_f.columns:
            rep_serie = rep_f.copy()
            rep_serie["mes"] = pd.to_datetime(rep_serie["data_pagamento_repasse"], errors="coerce").dt.to_period("M").dt.to_timestamp()
            g2 = rep_serie.groupby("mes")["valor_repasse"].sum().reset_index()
            if USE_PLOTLY:
                charts[1].plotly_chart(px.bar(g2, x="mes", y="valor_repasse", title="Repasses por Mês"), use_container_width=True)
            else:
                charts[1].bar_chart(g2.set_index("mes")["valor_repasse"])

# OS — Detalhe
with tabs[5]:
    st.subheader("Consulta por OS (Atendimento)")
    if os_view.empty:
        st.info("Não há dados suficientes para a visão por OS. Garanta Atendimentos, Receber e Repasses carregados.")
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
st.caption("© Vavivê — Dashboard de indicadores. Suporta Drive Compartilhado (IDs/Secrets), edição via sidebar ou upload local.")
