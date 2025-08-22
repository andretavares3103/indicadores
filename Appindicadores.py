## -*- coding: utf-8 -*-
import streamlit as st
import pandas as pd
import numpy as np
import os
import locale
from datetime import datetime, timedelta
from geopy.distance import geodesic
import tempfile
import io

import smtplib
from email.mime.text import MIMEText

PORTAL_EXCEL = "portal_atendimentos_clientes.xlsx"  # ou o nome correto do seu arquivo de clientes
PORTAL_OS_LIST = "portal_atendimentos_os_list.json" # ou o nome correto da lista de OS (caso use JSON, por exemplo)

st.set_page_config(page_title="BELO HORIZONTE || Otimização Rotas Vavivê", layout="wide")

ACEITES_FILE = "aceites.xlsx"
ROTAS_FILE = "rotas_bh_dados_tratados_completos.xlsx"

def enviar_email_aceite_gmail(os_id, profissional, telefone):
    remetente = "andre.mtavares3@gmail.com"  # <-- seu e-mail de envio
    senha = "3473010803474"                  # <-- sua senha de app do Gmail (cuidado ao versionar!)
    destinatario = "bh.savassi@vavive.com.br"

    assunto = f"Novo aceite registrado | OS {os_id}"
    corpo = f"""
    Um novo aceite foi registrado:
    
    OS: {os_id}
    Profissional: {profissional}
    Telefone: {telefone}
    Data/Hora: [inserir data/hora se quiser]
    """

    msg = MIMEText(corpo)
    msg['Subject'] = assunto
    msg['From'] = remetente
    msg['To'] = destinatario

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(remetente, senha)
            smtp.sendmail(remetente, destinatario, msg.as_string())
        print("Alerta de aceite enviado por e-mail!")
    except Exception as e:
        print("Erro ao enviar e-mail:", e)

def exibe_formulario_aceite(os_id, origem=None):
    st.header(f"Validação de Aceite (OS {os_id})")
    profissional = st.text_input("Nome da Profissional (OBRIGATORIO)")
    telefone = st.text_input("Telefone para contato (OBRIGATORIO)")
    resposta = st.empty()

    # (NOVO) obrigatoriedade: só habilita botões quando os dois campos estiverem preenchidos
    _ok = bool((profissional or "").strip()) and bool((telefone or "").strip())

    col1, col2 = st.columns(2)
    aceite_submetido = False

    with col1:
        if st.button("Sim, aceito este atendimento", disabled=not _ok):
            try:
                salvar_aceite(os_id, profissional, telefone, True, origem=origem)
            except ValueError as e:
                resposta.error(f"❌ {e}")
            else:
                resposta.success("✅ Obrigado! Seu aceite foi registrado com sucesso. Em breve daremos retorno sobre o atendimento!")
                aceite_submetido = True

    with col2:
        if st.button("Não posso aceitar", disabled=not _ok):
            try:
                salvar_aceite(os_id, profissional, telefone, False, origem=origem)
            except ValueError as e:
                resposta.error(f"❌ {e}")
            else:
                resposta.success("✅ Obrigado! Fique de olho em novas oportunidades.")
                aceite_submetido = True

    if aceite_submetido:
        st.stop()

def salvar_aceite(os_id, profissional, telefone, aceitou, origem=None):
    # (NOVO) obrigatoriedade: valida back-end
    profissional = (profissional or "").strip()
    telefone = (telefone or "").strip()
    if not profissional:
        raise ValueError("Nome da Profissional é obrigatório.")
    if not telefone:
        raise ValueError("Telefone é obrigatório.")

    agora = pd.Timestamp.now()
    data = agora.strftime("%d/%m/%Y")
    dia_semana = agora.strftime("%A")
    horario = agora.strftime("%H:%M:%S")
    if os.path.exists(ACEITES_FILE):
        df = pd.read_excel(ACEITES_FILE)
    else:
        df = pd.DataFrame(columns=[
            "OS", "Profissional", "Telefone", "Aceitou", 
            "Data do Aceite", "Dia da Semana", "Horário do Aceite", "Origem"
        ])
    nova_linha = {
        "OS": os_id,
        "Profissional": profissional,
        "Telefone": telefone,
        "Aceitou": "Sim" if aceitou else "Não",
        "Data do Aceite": data,
        "Dia da Semana": dia_semana,
        "Horário do Aceite": horario,
        "Origem": origem if origem else ""
    }
    df = pd.concat([df, pd.DataFrame([nova_linha])], ignore_index=True)
    df.to_excel(ACEITES_FILE, index=False)
    enviar_email_aceite_gmail(os_id, profissional, telefone)

aceite_os = st.query_params.get("aceite", None)
origem_aceite = st.query_params.get("origem", None)

if aceite_os:
    exibe_formulario_aceite(aceite_os, origem=origem_aceite)
    st.stop()

def traduzir_dia_semana(date_obj):
    dias_pt = {
        "Monday": "segunda-feira", "Tuesday": "terça-feira", "Wednesday": "quarta-feira",
        "Thursday": "quinta-feira", "Friday": "sexta-feira", "Saturday": "sábado", "Sunday": "domingo"
    }
    return dias_pt[date_obj.strftime('%A')]

def formatar_nome_simples(nome):
    nome = str(nome or "").strip()
    nome = nome.replace("CI ", "").replace("Ci ", "").replace("C i ", "").replace("C I ", "")
    partes = nome.split()
    if partes and partes[0].lower() in ['ana', 'maria'] and len(partes) > 1:
        return " ".join(partes[:2])
    elif partes:
        return partes[0]
    return nome

def gerar_mensagem_personalizada(
    nome_profissional, nome_cliente, data_servico, servico,
    duracao, rua, numero, complemento, bairro, cidade, latitude, longitude,
    ja_atendeu, hora_entrada, obs_prestador 
):
    nome_profissional_fmt = formatar_nome_simples(nome_profissional)
    nome_cliente_fmt = str(nome_cliente).split()[0].strip().title()
    if isinstance(data_servico, str):
        data_dt = pd.to_datetime(data_servico, dayfirst=True, errors="coerce")
    else:
        data_dt = data_servico
    if pd.isnull(data_dt):
        data_formatada = ""
        dia_semana = ""
    else:
        dia_semana = traduzir_dia_semana(data_dt)
        data_formatada = data_dt.strftime("%d/%m/%Y")
    data_linha = f"{dia_semana}, {data_formatada}"
    endereco_str = f"{rua}, {numero}"
    if complemento and str(complemento).strip().lower() not in ["nan", "none", "-"]:
        endereco_str += f", {complemento}"
    if pd.notnull(latitude) and pd.notnull(longitude):
        maps_url = f"https://maps.google.com/?q={latitude},{longitude}"
    else:
        maps_url = ""
    fechamento = (
        "SIM ou NÃO para o aceite!" if ja_atendeu
        else "Acesse o link ao final da mensagem e responda com SIM caso tenha disponibilidade!"
    )
    rodape = """
O atendimento será confirmado após o aceite!
*1)*    Lembre que o cliente irá receber o *profissional indicado pela Vavivê*.
*2)*    Lembre-se das nossas  confirmações do atendimento!

Abs, Vavivê!
"""
    mensagem = f"""Olá, Tudo bem com você?
Temos uma oportunidade especial para você dentro da sua rota!
*Cliente:* {nome_cliente_fmt}
📅 *Data:* {data_linha}
🛠️ *Serviço:* {servico}
🕒 *Hora de entrada:* {hora_entrada}
⏱️ *Duração do Atendimento:* {duracao}
📍 *Endereço:* {endereco_str}
📍 *Bairro:* {bairro}
🏙️ *Cidade:* {cidade}
💬 *Observações do Atendimento:* {obs_prestador}
*GOOGLE MAPAS* {"🌎 (" + maps_url + ")" if maps_url else ""}
{fechamento}
{rodape}
"""
    return mensagem

def padronizar_cpf_cnpj(coluna):
    return (
        coluna.astype(str)
        .str.replace(r'\D', '', regex=True)
        .str.zfill(14)
        .str.strip()
    )

def salvar_df(df, nome_arquivo, output_dir):
    caminho = os.path.join(output_dir, f"{nome_arquivo}.xlsx")
    df.to_excel(caminho, index=False)

def pipeline(file_path, output_dir):
    import xlsxwriter
    from collections import defaultdict

    # ============================
    # 1) Base: leitura e normalização
    # ============================
    df_clientes_raw = pd.read_excel(file_path, sheet_name="Clientes")
    df_clientes = df_clientes_raw[[
        "ID","UpdatedAt","celular","cpf",
        "endereco-1-bairro","endereco-1-cidade","endereco-1-complemento",
        "endereco-1-estado","endereco-1-latitude","endereco-1-longitude",
        "endereco-1-numero","endereco-1-rua","nome"
    ]].copy()
    df_clientes["ID Cliente"] = (
        df_clientes["ID"].fillna("0").astype(str)
        .str.replace(r"\.0$", "", regex=True).str.strip()
    )
    df_clientes["CPF_CNPJ"] = padronizar_cpf_cnpj(df_clientes["cpf"])
    df_clientes["Celular"] = df_clientes["celular"].astype(str).str.strip()
    df_clientes["Complemento"] = df_clientes["endereco-1-complemento"].astype(str).str.strip()
    df_clientes["Número"] = df_clientes["endereco-1-numero"].astype(str).str.strip()
    df_clientes["Nome Cliente"] = df_clientes["nome"].astype(str).str.strip()
    df_clientes = df_clientes.rename(columns={
        "endereco-1-bairro": "Bairro",
        "endereco-1-cidade": "Cidade",
        "endereco-1-estado": "Estado",
        "endereco-1-latitude": "Latitude Cliente",
        "endereco-1-longitude": "Longitude Cliente",
        "endereco-1-rua": "Rua"
    })
    df_clientes["Latitude Cliente"] = pd.to_numeric(df_clientes["Latitude Cliente"], errors="coerce")
    df_clientes["Longitude Cliente"] = pd.to_numeric(df_clientes["Longitude Cliente"], errors="coerce")
    coord_invertida = df_clientes["Latitude Cliente"] < -40
    if coord_invertida.any():
        lat_temp = df_clientes.loc[coord_invertida, "Latitude Cliente"].copy()
        df_clientes.loc[coord_invertida, "Latitude Cliente"] = df_clientes.loc[coord_invertida, "Longitude Cliente"]
        df_clientes.loc[coord_invertida, "Longitude Cliente"] = lat_temp
    df_clientes["coordenadas_validas"] = df_clientes["Latitude Cliente"].notnull() & df_clientes["Longitude Cliente"].notnull()
    df_clientes = df_clientes.sort_values(by=["CPF_CNPJ", "coordenadas_validas"], ascending=[True, False])
    df_clientes = df_clientes.drop_duplicates(subset="CPF_CNPJ", keep="first")
    df_clientes.drop(columns=["coordenadas_validas"], inplace=True)
    df_clientes = df_clientes[[
        "ID Cliente","UpdatedAt","Celular","CPF_CNPJ",
        "Bairro","Cidade","Complemento","Estado","Latitude Cliente","Longitude Cliente",
        "Número","Rua","Nome Cliente"
    ]]
    salvar_df(df_clientes, "df_clientes", output_dir)

    df_profissionais_raw = pd.read_excel(file_path, sheet_name="Profissionais")
    df_profissionais = df_profissionais_raw[[
        "ID","atendimentos_feitos","celular","cpf",
        "endereco-bairro","endereco-cidade","endereco-complemento","endereco-estado",
        "endereco-latitude","endereco-longitude","endereco-numero","endereco-rua","nome"
    ]].copy()
    df_profissionais["ID Prestador"] = (
        df_profissionais["ID"].fillna("0").astype(str)
        .str.replace(r"\.0$", "", regex=True).str.strip()
    )
    df_profissionais["Qtd Atendimentos"] = df_profissionais["atendimentos_feitos"].fillna(0).astype(int)
    df_profissionais["Celular"] = df_profissionais["celular"].astype(str).str.strip()
    df_profissionais["cpf"] = (
        df_profissionais["cpf"].astype(str).str.replace(r"\D", "", regex=True).str.strip()
    )
    df_profissionais["Complemento"] = df_profissionais["endereco-complemento"].astype(str).str.strip()
    df_profissionais["Número"] = df_profissionais["endereco-numero"].astype(str).str.strip()
    df_profissionais["Nome Prestador"] = df_profissionais["nome"].astype(str).str.strip()
    df_profissionais = df_profissionais.rename(columns={
        "endereco-bairro": "Bairro",
        "endereco-cidade": "Cidade",
        "endereco-estado": "Estado",
        "endereco-latitude": "Latitude Profissional",
        "endereco-longitude": "Longitude Profissional",
        "endereco-rua": "Rua"
    })
    df_profissionais = df_profissionais[~df_profissionais["Nome Prestador"].str.contains("inativo", case=False, na=False)].copy()
    df_profissionais["Latitude Profissional"] = pd.to_numeric(df_profissionais["Latitude Profissional"], errors="coerce")
    df_profissionais["Longitude Profissional"] = pd.to_numeric(df_profissionais["Longitude Profissional"], errors="coerce")
    df_profissionais = df_profissionais[
        df_profissionais["Latitude Profissional"].notnull() &
        df_profissionais["Longitude Profissional"].notnull()
    ].copy()
    df_profissionais = df_profissionais[[
        "ID Prestador","Qtd Atendimentos","Celular","cpf",
        "Bairro","Cidade","Complemento","Estado","Latitude Profissional","Longitude Profissional",
        "Número","Rua","Nome Prestador"
    ]]
    salvar_df(df_profissionais, "df_profissionais", output_dir)

    df_preferencias_raw = pd.read_excel(file_path, sheet_name="Preferencias")
    df_preferencias = df_preferencias_raw[[
        "CPF/CNPJ","Cliente","ID Profissional","Prestador"
    ]].copy()
    df_preferencias["CPF_CNPJ"] = padronizar_cpf_cnpj(df_preferencias["CPF/CNPJ"])
    df_preferencias["Nome Cliente"] = df_preferencias["Cliente"].astype(str).str.strip()
    df_preferencias["ID Prestador"] = (
        df_preferencias["ID Profissional"].fillna("0").astype(str)
        .str.replace(r"\.0$", "", regex=True).str.strip()
    )
    df_preferencias["Nome Prestador"] = df_preferencias["Prestador"].astype(str).str.strip()
    df_preferencias = df_preferencias[[
        "CPF_CNPJ","Nome Cliente","ID Prestador","Nome Prestador"
    ]]
    salvar_df(df_preferencias, "df_preferencias", output_dir)

    df_bloqueio_raw = pd.read_excel(file_path, sheet_name="Bloqueio")
    df_bloqueio = df_bloqueio_raw[[
        "CPF/CNPJ","Cliente","ID Profissional","Prestador"
    ]].copy()
    df_bloqueio["CPF_CNPJ"] = padronizar_cpf_cnpj(df_bloqueio["CPF/CNPJ"])
    df_bloqueio["Nome Cliente"] = df_bloqueio["Cliente"].astype(str).str.strip()
    df_bloqueio["ID Prestador"] = (
        df_bloqueio["ID Profissional"].fillna("0").astype(str)
        .str.replace(r"\.0$", "", regex=True).str.strip()
    )
    df_bloqueio["Nome Prestador"] = df_bloqueio["Prestador"].astype(str).str.strip()
    df_bloqueio = df_bloqueio[[
        "CPF_CNPJ","Nome Cliente","ID Prestador","Nome Prestador"
    ]]
    salvar_df(df_bloqueio, "df_bloqueio", output_dir)

    df_queridinhos_raw = pd.read_excel(file_path, sheet_name="Profissionais Preferenciais")
    df_queridinhos = df_queridinhos_raw[[
        "ID Profissional","Profissional"
    ]].copy()
    df_queridinhos["ID Prestador"] = (
        df_queridinhos["ID Profissional"].fillna("0").astype(str)
        .str.replace(r"\.0$", "", regex=True).str.strip()
    )
    df_queridinhos["Nome Prestador"] = df_queridinhos["Profissional"].astype(str).str.strip()
    df_queridinhos = df_queridinhos[["ID Prestador","Nome Prestador"]]
    salvar_df(df_queridinhos, "df_queridinhos", output_dir)

    df_sumidinhos_raw = pd.read_excel(file_path, sheet_name="Baixa Disponibilidade")
    df_sumidinhos = df_sumidinhos_raw[[
        "ID Profissional","Profissional"
    ]].copy()
    df_sumidinhos["ID Prestador"] = (
        df_sumidinhos["ID Profissional"].fillna("0").astype(str)
        .str.replace(r"\.0$", "", regex=True).str.strip()
    )
    df_sumidinhos["Nome Prestador"] = df_sumidinhos["Profissional"].astype(str).str.strip()
    df_sumidinhos = df_sumidinhos[["ID Prestador","Nome Prestador"]]
    salvar_df(df_sumidinhos, "df_sumidinhos", output_dir)

    df_atendimentos = pd.read_excel(file_path, sheet_name="Atendimentos")
    colunas_desejadas = [
        "OS","Status Serviço","Data 1","Plano","CPF/ CNPJ","Cliente","Serviço",
        "Horas de serviço","Hora de entrada","Observações atendimento",
        "Observações prestador","Ponto de Referencia","#Num Prestador","Prestador"
    ]
    df_atendimentos = df_atendimentos[colunas_desejadas].copy()
    df_atendimentos["Data 1"] = pd.to_datetime(df_atendimentos["Data 1"], errors="coerce")
    df_atendimentos["CPF_CNPJ"] = padronizar_cpf_cnpj(df_atendimentos["CPF/ CNPJ"])
    df_atendimentos["Cliente"] = df_atendimentos["Cliente"].astype(str).str.strip()
    df_atendimentos["Duração do Serviço"] = df_atendimentos["Horas de serviço"]
    df_atendimentos["ID Prestador"] = (
        df_atendimentos["#Num Prestador"].fillna("0").astype(str)
        .str.replace(r"\.0$", "", regex=True).str.strip()
    )
    salvar_df(df_atendimentos, "df_atendimentos", output_dir)

    hoje = datetime.now().date()
    limite = hoje - timedelta(days=60)
    data1_datetime = pd.to_datetime(df_atendimentos["Data 1"], errors="coerce")
    df_historico_60_dias = df_atendimentos[
        (df_atendimentos["Status Serviço"].astype(str).str.lower() != "cancelado") &
        (data1_datetime.dt.date < hoje) &
        (data1_datetime.dt.date >= limite)
    ].copy()
    df_historico_60_dias = df_historico_60_dias[[
        "CPF_CNPJ","Cliente","Data 1","Status Serviço","Serviço",
        "Duração do Serviço","Hora de entrada","ID Prestador","Prestador", "Observações prestador"
    ]]
    salvar_df(df_historico_60_dias, "df_historico_60_dias", output_dir)

    df_cliente_prestador = df_historico_60_dias.groupby(
        ["CPF_CNPJ","ID Prestador"]
    ).size().reset_index(name="Qtd Atendimentos Cliente-Prestador")
    salvar_df(df_cliente_prestador, "df_cliente_prestador", output_dir)

    df_qtd_por_prestador = df_historico_60_dias.groupby(
        "ID Prestador"
    ).size().reset_index(name="Qtd Atendimentos Prestador")
    salvar_df(df_qtd_por_prestador, "df_qtd_por_prestador", output_dir)

    df_clientes_coord = df_clientes[["CPF_CNPJ","Latitude Cliente","Longitude Cliente"]].dropna().drop_duplicates("CPF_CNPJ")
    df_profissionais_coord = df_profissionais[["ID Prestador","Latitude Profissional","Longitude Profissional"]].dropna().drop_duplicates("ID Prestador")

    # (Opcional) cálculo em batch para auditoria
    distancias = []
    for _, cliente in df_clientes_coord.iterrows():
        coord_cliente = (cliente["Latitude Cliente"], cliente["Longitude Cliente"])
        for _, profissional in df_profissionais_coord.iterrows():
            coord_prof = (profissional["Latitude Profissional"], profissional["Longitude Profissional"])
            distancia_km = round(geodesic(coord_cliente, coord_prof).km, 2)
            distancias.append({
                "CPF_CNPJ": cliente["CPF_CNPJ"],
                "ID Prestador": profissional["ID Prestador"],
                "Distância (km)": distancia_km
            })
    df_distancias = pd.DataFrame(distancias)
    df_distancias_alerta = df_distancias[df_distancias["Distância (km)"] > 1000]
    salvar_df(df_distancias_alerta, "df_distancias_alerta", output_dir)
    salvar_df(df_distancias, "df_distancias", output_dir)

    df_preferencias_completo = df_preferencias.merge(
        df_clientes_coord, on="CPF_CNPJ", how="left"
    ).merge(
        df_profissionais_coord, on="ID Prestador", how="left"
    )
    df_preferencias_completo = df_preferencias_completo[[
        "CPF_CNPJ","Nome Cliente","ID Prestador","Nome Prestador",
        "Latitude Cliente","Longitude Cliente",
        "Latitude Profissional","Longitude Profissional"
    ]]
    salvar_df(df_preferencias_completo, "df_preferencias_completo", output_dir)

    df_bloqueio_completo = df_bloqueio.merge(
        df_clientes_coord, on="CPF_CNPJ", how="left"
    ).merge(
        df_profissionais_coord, on="ID Prestador", how="left"
    )
    df_bloqueio_completo = df_bloqueio_completo[[
        "CPF_CNPJ","Nome Cliente","ID Prestador","Nome Prestador",
        "Latitude Cliente","Longitude Cliente",
        "Latitude Profissional","Longitude Profissional"
    ]]
    salvar_df(df_bloqueio_completo, "df_bloqueio_completo", output_dir)

    ontem = datetime.now().date() - timedelta(days=1)
    df_futuros = df_atendimentos[
        (df_atendimentos["Status Serviço"].astype(str).str.lower() != "cancelado") &
        (df_atendimentos["Data 1"].dt.date > ontem)
    ].copy()
    df_futuros_com_clientes = df_futuros.merge(
        df_clientes_coord, on="CPF_CNPJ", how="left"
    )
    colunas_uteis = [
        "OS","Data 1","Status Serviço","CPF_CNPJ","Cliente","Serviço",
        "Duração do Serviço","Hora de entrada","Ponto de Referencia",
        "ID Prestador","Prestador","Latitude Cliente","Longitude Cliente","Plano", "Observações prestador"
    ]
    df_atendimentos_futuros_validos = df_futuros_com_clientes[
        df_futuros_com_clientes["Latitude Cliente"].notnull() &
        df_futuros_com_clientes["Longitude Cliente"].notnull()
    ][colunas_uteis].copy()
    salvar_df(df_atendimentos_futuros_validos, "df_atendimentos_futuros_validos", output_dir)

    df_atendimentos_sem_localizacao = df_futuros_com_clientes[
        df_futuros_com_clientes["Latitude Cliente"].isnull() |
        df_futuros_com_clientes["Longitude Cliente"].isnull()
    ][colunas_uteis].copy()
    salvar_df(df_atendimentos_sem_localizacao, "df_atendimentos_sem_localizacao", output_dir)

    # Persistências (opcional)
    df_clientes.to_pickle('df_clientes.pkl')
    df_profissionais.to_pickle('df_profissionais.pkl')
    df_preferencias.to_pickle('df_preferencias.pkl')
    df_bloqueio.to_pickle('df_bloqueio.pkl')
    df_queridinhos.to_pickle('df_queridinhos.pkl')
    df_sumidinhos.to_pickle('df_sumidinhos.pkl')
    df_atendimentos.to_pickle('df_atendimentos.pkl')
    df_historico_60_dias.to_pickle('df_historico_60_dias.pkl')
    df_cliente_prestador.to_pickle('df_cliente_prestador.pkl')
    df_qtd_por_prestador.to_pickle('df_qtd_por_prestador.pkl')
    df_distancias.to_pickle('df_distancias.pkl')
    df_preferencias_completo.to_pickle('df_preferencias_completo.pkl')
    df_bloqueio_completo.to_pickle('df_bloqueio_completo.pkl')
    df_atendimentos_futuros_validos.to_pickle('df_atendimentos_futuros_validos.pkl')
    df_atendimentos_sem_localizacao.to_pickle('df_atendimentos_sem_localizacao.pkl')
    df_distancias_alerta.to_pickle('df_distancias_alerta.pkl')

    # ============================
    # PARÂMETROS
    # ============================
    DELTA_KM = 1.0
    RAIO_QUERIDINHOS = 5.0
    GARANTIR_COTA_QUERIDINHO = True
    EVITAR_REPETIR_EM_LISTAS_NO_DIA = True
    RECALC_DIST_ON_THE_FLY = False  # aqui vamos usar df_distancias como fonte de verdade
    
    from collections import defaultdict
    
    # ----------------------------
    # Helpers de distância (usa df_distancias como "truth")
    # ----------------------------
    def _dist_from_df(cpf, id_prof, df_dist):
        row = df_dist[
            (df_dist["CPF_CNPJ"] == cpf) &
            (df_dist["ID Prestador"].astype(str).str.strip() == str(id_prof).strip())
        ]
        return float(row["Distância (km)"].iloc[0]) if not row.empty else None
    
    def _parse_hora(hora_str):
        try:
            s = str(hora_str).strip()
            h, m = s.split(":")
            return (int(h), int(m))
        except Exception:
            return (99, 99)
    
    def _prof_ok(id_prof, df_profissionais_):
        prof = df_profissionais_[df_profissionais_["ID Prestador"].astype(str).str.strip() == str(id_prof).strip()]
        if prof.empty:
            return None
        if "inativo" in str(prof.iloc[0]["Nome Prestador"]).lower():
            return None
        if pd.isnull(prof.iloc[0]["Latitude Profissional"]) or pd.isnull(prof.iloc[0]["Longitude Profissional"]):
            return None
        return prof.iloc[0]
    
    def _qtd_cli(df_cliente_prestador_, cpf, id_prof):
        x = df_cliente_prestador_[
            (df_cliente_prestador_["CPF_CNPJ"] == cpf) &
            (df_cliente_prestador_["ID Prestador"].astype(str).str.strip() == str(id_prof).strip())
        ]
        return int(x["Qtd Atendimentos Cliente-Prestador"].iloc[0]) if not x.empty else 0
    
    def _qtd_tot(df_qtd_por_prestador_, id_prof):
        x = df_qtd_por_prestador_[df_qtd_por_prestador_["ID Prestador"].astype(str).str.strip() == str(id_prof).strip()]
        return int(x["Qtd Atendimentos Prestador"].iloc[0]) if not x.empty else 0
    
    def _ordena_os(df_do_dia):
        tmp = df_do_dia.copy()
        tmp["_hora_tuple"] = tmp["Hora de entrada"].apply(_parse_hora)
        tmp["_dur"] = tmp["Duração do Serviço"]
        return tmp.sort_values(by=["_hora_tuple", "_dur"], ascending=[True, False])
    
    # ============================
    # Pré-reservas por dia (preferidas)
    # ============================
    preferida_do_cliente_no_dia     = defaultdict(dict)  # {date: {cpf: id_prof}}
    profissionais_reservadas_no_dia = defaultdict(set)   # {date: {id_prof}}
    profissionais_ocupadas_no_dia   = defaultdict(set)   # {date: {id_prof}}  # efetivamente na 1a posição
    profissionais_sugeridas_no_dia  = defaultdict(set)   # {date: {id_prof}}  # apareceu em qualquer OS
    
    pref_map = df_preferencias.set_index("CPF_CNPJ")["ID Prestador"].astype(str).str.strip().to_dict()
    
    for data_atend, df_do_dia in df_atendimentos_futuros_validos.groupby(df_atendimentos_futuros_validos["Data 1"].dt.date):
        candidatos = []
        for _, row in df_do_dia.iterrows():
            cpf = row["CPF_CNPJ"]
            id_pref = pref_map.get(cpf, "")
            if not id_pref:
                continue
            bloqueados = (
                df_bloqueio[df_bloqueio["CPF_CNPJ"] == cpf]["ID Prestador"]
                .astype(str).str.strip().tolist()
            )
            if id_pref in bloqueados:
                continue
            prof = _prof_ok(id_pref, df_profissionais)
            if prof is None:
                continue
            candidatos.append({
                "cpf": cpf,
                "id_prof": id_pref,
                "qtd_cli": _qtd_cli(df_cliente_prestador, cpf, id_pref),
                "dist_km": _dist_from_df(cpf, id_pref, df_distancias) or 9999.0,
                "hora": _parse_hora(row.get("Hora de entrada", "")),
            })
        # resolve conflitos por profissional
        por_prof = defaultdict(list)
        for c in candidatos:
            por_prof[c["id_prof"]].append(c)
        for id_prof, lst in por_prof.items():
            lst.sort(key=lambda x: (-x["qtd_cli"], x["dist_km"], x["hora"]))
            esc = lst[0]
            preferida_do_cliente_no_dia[data_atend][esc["cpf"]] = id_prof
            profissionais_reservadas_no_dia[data_atend].add(id_prof)
    
    # ============================
    # Alocação da 1ª candidata por OS nas camadas 1..3
    # (Preferida -> Mais atendeu -> Último -> Cota Queridinho)
    # ============================
    os_primeira_candidata = {}  # (date, OS) -> (id_prof, crit_texto, criterio_nome)
    
    for data_atend, df_do_dia in df_atendimentos_futuros_validos.groupby(df_atendimentos_futuros_validos["Data 1"].dt.date):
        df_sorted = _ordena_os(df_do_dia)
        for _, row in df_sorted.iterrows():
            os_id = row["OS"]; cpf = row["CPF_CNPJ"]
            bloqueados = (
                df_bloqueio[df_bloqueio["CPF_CNPJ"] == cpf]["ID Prestador"]
                .astype(str).str.strip().tolist()
            )
    
            # 1) Preferida (hard lock se estiver reservada ao mesmo CPF)
            pref_id = preferida_do_cliente_no_dia[data_atend].get(cpf)
            if pref_id:
                if (pref_id not in bloqueados) and (pref_id not in profissionais_ocupadas_no_dia[data_atend]):
                    prof = _prof_ok(pref_id, df_profissionais)
                    if prof is not None:
                        crit = f"cliente: {_qtd_cli(df_cliente_prestador, cpf, pref_id)} | total: {_qtd_tot(df_qtd_por_prestador, pref_id)}"
                        d = _dist_from_df(cpf, pref_id, df_distancias)
                        if d is not None: crit += f" — {d:.2f} km"
                        os_primeira_candidata[(data_atend, os_id)] = (pref_id, crit, "Preferência do Cliente")
                        profissionais_ocupadas_no_dia[data_atend].add(pref_id)
                        profissionais_sugeridas_no_dia[data_atend].add(pref_id)
                        continue  # próxima OS
    
            # 2) Mais atendeu o cliente
            df_mais = df_cliente_prestador[df_cliente_prestador["CPF_CNPJ"] == cpf]
            escolhido = None
            if not df_mais.empty:
                max_at = df_mais["Qtd Atendimentos Cliente-Prestador"].max()
                ids_mais = df_mais[df_mais["Qtd Atendimentos Cliente-Prestador"] == max_at]["ID Prestador"].astype(str).tolist()
                # desempata por distância e disponibilidade
                ids_mais = sorted(
                    [i for i in ids_mais if i not in bloqueados and i not in profissionais_ocupadas_no_dia[data_atend] and _prof_ok(i, df_profissionais) is not None],
                    key=lambda i: (_dist_from_df(cpf, i, df_distancias) or 9999.0)
                )
                if ids_mais:
                    escolhido = ids_mais[0]
                    crit = f"cliente: {max_at} | total: {_qtd_tot(df_qtd_por_prestador, escolhido)}"
                    d = _dist_from_df(cpf, escolhido, df_distancias)
                    if d is not None: crit += f" — {d:.2f} km"
                    os_primeira_candidata[(data_atend, os_id)] = (escolhido, crit, "Mais atendeu o cliente")
                    profissionais_ocupadas_no_dia[data_atend].add(escolhido)
                    profissionais_sugeridas_no_dia[data_atend].add(escolhido)
                    continue
    
            # 3) Último profissional (60 dias)
            df_hist = df_historico_60_dias[df_historico_60_dias["CPF_CNPJ"] == cpf].sort_values("Data 1", ascending=False)
            if not df_hist.empty:
                ult_id = str(df_hist["ID Prestador"].iloc[0]).strip()
                if (ult_id not in bloqueados) and (ult_id not in profissionais_ocupadas_no_dia[data_atend]) and (_prof_ok(ult_id, df_profissionais) is not None):
                    crit = f"cliente: {_qtd_cli(df_cliente_prestador, cpf, ult_id)} | total: {_qtd_tot(df_qtd_por_prestador, ult_id)}"
                    d = _dist_from_df(cpf, ult_id, df_distancias)
                    if d is not None: crit += f" — {d:.2f} km"
                    os_primeira_candidata[(data_atend, os_id)] = (ult_id, crit, "Último profissional que atendeu")
                    profissionais_ocupadas_no_dia[data_atend].add(ult_id)
                    profissionais_sugeridas_no_dia[data_atend].add(ult_id)
                    continue
    
    # 3.5) Cota mínima de queridinhos
    if GARANTIR_COTA_QUERIDINHO:
        for data_atend, df_do_dia in df_atendimentos_futuros_validos.groupby(df_atendimentos_futuros_validos["Data 1"].dt.date):
            df_sorted = _ordena_os(df_do_dia)
            for _, qrow in df_queridinhos.iterrows():
                qid = str(qrow["ID Prestador"]).strip()
                if qid in profissionais_ocupadas_no_dia[data_atend]:
                    continue
                if EVITAR_REPETIR_EM_LISTAS_NO_DIA and qid in profissionais_sugeridas_no_dia[data_atend]:
                    continue
                for _, row in df_sorted.iterrows():
                    os_id = row["OS"]; cpf = row["CPF_CNPJ"]
                    if (data_atend, os_id) in os_primeira_candidata:
                        continue
                    if qid in profissionais_ocupadas_no_dia[data_atend]:
                        break
                    bloqueados = (
                        df_bloqueio[df_bloqueio["CPF_CNPJ"] == cpf]["ID Prestador"]
                        .astype(str).str.strip().tolist()
                    )
                    if qid in bloqueados:
                        continue
                    prof = _prof_ok(qid, df_profissionais)
                    if prof is None:
                        continue
                    d = _dist_from_df(cpf, qid, df_distancias)
                    if d is None or d > RAIO_QUERIDINHOS:
                        continue
                    crit = f"cliente: {_qtd_cli(df_cliente_prestador, cpf, qid)} | total: {_qtd_tot(df_qtd_por_prestador, qid)}"
                    crit += f" — {d:.2f} km"
                    os_primeira_candidata[(data_atend, os_id)] = (qid, crit, "Cota mínima queridinho")
                    profissionais_ocupadas_no_dia[data_atend].add(qid)
                    profissionais_sugeridas_no_dia[data_atend].add(qid)
                    break
    
    # ============================
    # CAMADA 4 — OTIMIZAÇÃO POR PROXIMIDADE (Hungarian)
    # ============================
    
    # implementação hungarian fallback (pura) com penalidade alta para "arestas inválidas"
    try:
        from scipy.optimize import linear_sum_assignment
        def hungarian_min_cost(cost_matrix):
            r, c = linear_sum_assignment(cost_matrix)
            return list(zip(r, c))
        _has_scipy = True
    except Exception:
        _has_scipy = False
        def hungarian_min_cost(cost):
            # Implementação simplificada: converte para maximização via subtração da max
            # e aplica algoritmo de Kuh-Munkres O(n^3).
            import math
            n = max(len(cost), len(cost[0]) if cost else 0)
            # pad para matriz n x n
            C = [[10**6 for _ in range(n)] for __ in range(n)]
            for i in range(len(cost)):
                for j in range(len(cost[0])):
                    C[i][j] = cost[i][j]
            u = [0]* (n+1); v = [0]*(n+1); p = [0]*(n+1); way = [0]*(n+1)
            for i in range(1, n+1):
                p[0] = i
                j0 = 0
                minv = [math.inf]*(n+1)
                used = [False]*(n+1)
                while True:
                    used[j0] = True
                    i0 = p[j0]; delta = math.inf; j1 = 0
                    for j in range(1, n+1):
                        if not used[j]:
                            cur = C[i0-1][j-1] - u[i0] - v[j]
                            if cur < minv[j]:
                                minv[j] = cur; way[j] = j0
                            if minv[j] < delta:
                                delta = minv[j]; j1 = j
                    for j in range(0, n+1):
                        if used[j]:
                            u[p[j]] += delta; v[j] -= delta
                        else:
                            minv[j] -= delta
                    j0 = j1
                    if p[j0] == 0:
                        break
                while True:
                    j1 = way[j0]; p[j0] = p[j1]; j0 = j1
                    if j0 == 0:
                        break
            ans = [(-1,-1)]*n
            for j in range(1, n+1):
                if p[j] != 0 and p[j]-1 < len(cost) and j-1 < len(cost[0]):
                    ans[p[j]-1] = (p[j]-1, j-1)
            return [(i,j) for (i,j) in ans if i != -1 and j != -1]
    
    # Auditoria
    auditoria_proximidade = []  # linhas com: data, OS, cpf, prof_escolhida, dist_escolhida, prof_mais_proxima_elegivel, dist_mais_proxima, motivo
    
    PENAL = 10**6  # custo alto para impossíveis
    
    for data_atend, df_do_dia in df_atendimentos_futuros_validos.groupby(df_atendimentos_futuros_validos["Data 1"].dt.date):
        # OS ainda sem #1
        df_pend = df_do_dia[~df_do_dia["OS"].apply(lambda os_: (data_atend, os_) in os_primeira_candidata)].copy()
        if df_pend.empty:
            continue
    
        # profissionais livres (não ocupadas por camadas 1..3) e válidas
        prof_livres = [
            pid for pid in df_profissionais["ID Prestador"].astype(str)
            if (pid not in profissionais_ocupadas_no_dia[data_atend]) and (_prof_ok(pid, df_profissionais) is not None)
        ]
        if not prof_livres:
            # sem ninguém livre — nada a otimizar
            continue
    
        # montar matriz de custos (linhas = OS pendentes; colunas = prof_livres)
        pend_rows = df_pend.reset_index(drop=True)
        cost = []
        elig_map = []  # guarda quais pares são válidos
        for _, row in pend_rows.iterrows():
            cpf = row["CPF_CNPJ"]
            bloqueados = (
                df_bloqueio[df_bloqueio["CPF_CNPJ"] == cpf]["ID Prestador"]
                .astype(str).str.strip().tolist()
            )
            linha_cost = []
            linha_elig = []
            for pid in prof_livres:
                # inválidos:
                if pid in bloqueados:
                    linha_cost.append(PENAL); linha_elig.append(False); continue
                # se a profissional foi reservada como preferida para outro CPF
                if pid in profissionais_reservadas_no_dia[data_atend]:
                    aloc = preferida_do_cliente_no_dia[data_atend]
                    reservado_para = next((c for c, p in aloc.items() if str(p).strip() == pid), None)
                    if reservado_para and reservado_para != cpf:
                        linha_cost.append(PENAL); linha_elig.append(False); continue
                d = _dist_from_df(cpf, pid, df_distancias)
                if d is None:
                    linha_cost.append(PENAL); linha_elig.append(False); continue
                linha_cost.append(d)
                linha_elig.append(True)
            cost.append(linha_cost)
            elig_map.append(linha_elig)
    
        if not cost or not cost[0]:
            continue
    
        pairs = hungarian_min_cost(cost)  # lista de (i_linha_os, j_col_prof)
    
        for i, j in pairs:
            if i < 0 or j < 0: 
                continue
            if cost[i][j] >= PENAL:
                continue  # pareamento inválido
    
            row = pend_rows.iloc[i]
            os_id = row["OS"]; cpf = row["CPF_CNPJ"]
            pid = str(prof_livres[j])
    
            # marca alocação como "Mais próxima geograficamente (otimizado)"
            d = cost[i][j]
            crit_texto = f"cliente: {_qtd_cli(df_cliente_prestador, cpf, pid)} | total: {_qtd_tot(df_qtd_por_prestador, pid)} — {d:.2f} km"
            os_primeira_candidata[(data_atend, os_id)] = (pid, crit_texto, "Mais próxima geograficamente (otimizado)")
            profissionais_ocupadas_no_dia[data_atend].add(pid)
            profissionais_sugeridas_no_dia[data_atend].add(pid)
    
        # Auditoria: por OS pendente, quem era a mais próxima elegível?
        for idx, row in pend_rows.iterrows():
            os_id = row["OS"]; cpf = row["CPF_CNPJ"]
            # mais próxima elegível
            dist_line = cost[idx]
            elig_line = elig_map[idx]
            if not dist_line:
                continue
            best_j = None; best_d = None
            for jj, (dd, ok) in enumerate(zip(dist_line, elig_line)):
                if not ok: 
                    continue
                if (best_d is None) or (dd < best_d):
                    best_d = dd; best_j = jj
            escolhido = os_primeira_candidata.get((data_atend, os_id))
            if escolhido:
                pid_escolhido = escolhido[0]
                dist_escolhida = _dist_from_df(cpf, pid_escolhido, df_distancias)
                pid_best = str(prof_livres[best_j]) if best_j is not None else None
                dist_best = float(best_d) if best_d is not None else None
                motivo = ""
                if pid_best is not None and pid_escolhido != pid_best:
                    motivo = "Alocação ótima global (outra OS precisava mais), mantendo não-repetição no dia."
                auditoria_proximidade.append({
                    "Data": data_atend, "OS": os_id, "CPF_CNPJ": cpf,
                    "Prof_Atribuida": pid_escolhido, "Dist_Atribuida_km": dist_escolhida,
                    "Prof_Mais_Prox_Elegivel": pid_best, "Dist_Mais_Prox_km": dist_best,
                    "Motivo_Nao_Mais_Proxima": motivo
                })
    
    # ============================
    # LOOP PRINCIPAL — montar colunas 1..15 (apresentação)
    # ============================
    def _reservada_para_outro(data_atendimento, id_prof, cpf):
        id_prof = str(id_prof).strip()
        if id_prof not in profissionais_reservadas_no_dia[data_atendimento]:
            return False
        aloc = preferida_do_cliente_no_dia[data_atendimento]
        reservado_para = next((c for c, p in aloc.items() if str(p).strip() == id_prof), None)
        return bool(reservado_para and reservado_para != cpf)
    
    matriz_resultado_corrigida = []
    
    for _, atendimento in df_atendimentos_futuros_validos.iterrows():
        data_atendimento = atendimento["Data 1"].date()
        os_id = atendimento["OS"]
        cpf = atendimento["CPF_CNPJ"]
        nome_cliente = atendimento["Cliente"]
        data_1 = atendimento["Data 1"]
        servico = atendimento["Serviço"]
        duracao_servico = atendimento["Duração do Serviço"]
        hora_entrada = atendimento["Hora de entrada"]
        obs_prestador = atendimento["Observações prestador"]
        ponto_referencia = atendimento["Ponto de Referencia"]
        plano = atendimento.get("Plano", "")
    
        bloqueados = (
            df_bloqueio[df_bloqueio["CPF_CNPJ"] == cpf]["ID Prestador"]
            .astype(str).str.strip().tolist()
        )
    
        cli = df_clientes[df_clientes["CPF_CNPJ"] == cpf]
        if not cli.empty:
            rua = cli.iloc[0]["Rua"]; numero = cli.iloc[0]["Número"]
            complemento = cli.iloc[0]["Complemento"]; bairro = cli.iloc[0]["Bairro"]
            cidade = cli.iloc[0]["Cidade"]; latitude = cli.iloc[0]["Latitude Cliente"]; longitude = cli.iloc[0]["Longitude Cliente"]
        else:
            rua = numero = complemento = bairro = cidade = latitude = longitude = ""
    
        linha = {
            "OS": os_id, "CPF_CNPJ": cpf, "Nome Cliente": nome_cliente, "Plano": plano,
            "Data 1": data_1, "Serviço": servico, "Duração do Serviço": duracao_servico,
            "Hora de entrada": hora_entrada, "Observações prestador": obs_prestador,
            "Ponto de Referencia": ponto_referencia
        }
        linha["Mensagem Padrão"] = gerar_mensagem_personalizada(
            "PROFISSIONAL", nome_cliente, data_1, servico, duracao_servico,
            rua, numero, complemento, bairro, cidade, latitude, longitude,
            ja_atendeu=False, hora_entrada=hora_entrada, obs_prestador=obs_prestador
        )
    
        utilizados = set()
        col = 1
    
        def _add(id_prof, criterio_usado, ja_atendeu_flag):
            nonlocal col
            id_prof = str(id_prof).strip()
            if col > 15:
                return False
            if EVITAR_REPETIR_EM_LISTAS_NO_DIA and id_prof in profissionais_sugeridas_no_dia[data_atendimento]:
                return False
            if id_prof in utilizados:
                return False
            if id_prof in bloqueados:
                return False
            if id_prof in profissionais_ocupadas_no_dia[data_atendimento]:
                return False
            prof = _prof_ok(id_prof, df_profissionais)
            if prof is None:
                return False
            if _reservada_para_outro(data_atendimento, id_prof, cpf):
                return False
    
            q_cli = _qtd_cli(df_cliente_prestador, cpf, id_prof)
            q_tot = _qtd_tot(df_qtd_por_prestador, id_prof)
            d = _dist_from_df(cpf, id_prof, df_distancias)
            crit = f"cliente: {q_cli} | total: {q_tot}" + (f" — {d:.2f} km" if d is not None else "")
    
            linha[f"Classificação da Profissional {col}"] = col
            linha[f"Critério {col}"] = crit
            linha[f"Nome Prestador {col}"] = prof["Nome Prestador"]
            linha[f"Celular {col}"] = prof["Celular"]
            linha[f"Mensagem {col}"] = gerar_mensagem_personalizada(
                prof["Nome Prestador"], nome_cliente, data_1, servico, duracao_servico,
                rua, numero, complemento, bairro, cidade, latitude, longitude,
                ja_atendeu=ja_atendeu_flag, hora_entrada=hora_entrada, obs_prestador=obs_prestador
            )
            linha[f"Critério Utilizado {col}"] = criterio_usado
    
            utilizados.add(id_prof)
            if EVITAR_REPETIR_EM_LISTAS_NO_DIA:
                profissionais_sugeridas_no_dia[data_atendimento].add(id_prof)
            col += 1
            return True
    
        # posição 1: resultado das camadas (inclui proximidade otimizada)
        primeira = os_primeira_candidata.get((data_atendimento, os_id))
        if primeira:
            idp, crit_text, criterio_nome = primeira
            prof = _prof_ok(idp, df_profissionais)
            if prof is not None:
                linha[f"Classificação da Profissional {col}"] = col
                linha[f"Critério {col}"] = crit_text
                linha[f"Nome Prestador {col}"] = prof["Nome Prestador"]
                linha[f"Celular {col}"] = prof["Celular"]
                linha[f"Mensagem {col}"] = gerar_mensagem_personalizada(
                    prof["Nome Prestador"], nome_cliente, data_1, servico, duracao_servico,
                    rua, numero, complemento, bairro, cidade, latitude, longitude,
                    ja_atendeu=True, hora_entrada=hora_entrada, obs_prestador=obs_prestador
                )
                linha[f"Critério Utilizado {col}"] = criterio_nome
                utilizados.add(str(idp).strip()); col += 1

                # >>> NOVO: se a 1ª é Preferência do Cliente, não listar mais ninguém
                if criterio_nome == "Preferência do Cliente":
                    matriz_resultado_corrigida.append(linha)
                    continue  # vai para a próxima OS
    
        # 2) Mais atendeu o cliente
        if col <= 15:
            df_mais = df_cliente_prestador[df_cliente_prestador["CPF_CNPJ"] == cpf]
            if not df_mais.empty:
                max_at = df_mais["Qtd Atendimentos Cliente-Prestador"].max()
                for idp in df_mais[df_mais["Qtd Atendimentos Cliente-Prestador"] == max_at]["ID Prestador"].astype(str):
                    if col > 15: break
                    _add(idp, "Mais atendeu o cliente", True)
    
        # 3) Último profissional (60 dias)
        if col <= 15:
            df_hist = df_historico_60_dias[df_historico_60_dias["CPF_CNPJ"] == cpf].sort_values("Data 1", ascending=False)
            if not df_hist.empty:
                _add(str(df_hist["ID Prestador"].iloc[0]), "Último profissional que atendeu", True)
    
        # 4) Queridinhos (≤ 5 km)
        if col <= 15:
            ids_q = []
            for _, qrow in df_queridinhos.iterrows():
                qid = str(qrow["ID Prestador"]).strip()
                if EVITAR_REPETIR_EM_LISTAS_NO_DIA and qid in profissionais_sugeridas_no_dia[data_atendimento]:
                    continue
                if qid in profissionais_ocupadas_no_dia[data_atendimento]:
                    continue
                d = _dist_from_df(cpf, qid, df_distancias)
                if d is not None and d <= RAIO_QUERIDINHOS:
                    ids_q.append((qid, d))
            for qid, _ in sorted(ids_q, key=lambda x: x[1]):
                if col > 15: break
                _add(qid, "Profissional preferencial da plataforma (até 5 km)", _qtd_cli(df_cliente_prestador, cpf, qid) > 0)
    
        # 5) Mais próximas geograficamente (deg. 1 km entre entradas)
        if col <= 15:
            dist_cand = df_distancias[df_distancias["CPF_CNPJ"] == cpf].copy()
            dist_cand["ID Prestador"] = dist_cand["ID Prestador"].astype(str).str.strip()
            # tira invalidáveis
            def _ban(x):
                return (
                    (x in bloqueados) or
                    (x in utilizados) or
                    (x in profissionais_ocupadas_no_dia[data_atendimento]) or
                    _reservada_para_outro(data_atendimento, x, cpf) or
                    (EVITAR_REPETIR_EM_LISTAS_NO_DIA and x in profissionais_sugeridas_no_dia[data_atendimento]) or
                    (_prof_ok(x, df_profissionais) is None)
                )
            dist_cand = dist_cand[~dist_cand["ID Prestador"].apply(_ban)].sort_values("Distância (km)")
            ultimo_km = None
            for _, rowd in dist_cand.iterrows():
                if col > 15: break
                idp = rowd["ID Prestador"]; dkm = float(rowd["Distância (km)"])
                if ultimo_km is None:
                    if _add(idp, "Mais próxima geograficamente", _qtd_cli(df_cliente_prestador, cpf, idp) > 0):
                        ultimo_km = dkm
                else:
                    if dkm >= (ultimo_km + DELTA_KM):
                        if _add(idp, "Mais próxima geograficamente", _qtd_cli(df_cliente_prestador, cpf, idp) > 0):
                            ultimo_km = dkm
    
        # 6) Sumidinhas
        if col <= 15:
            for sid in df_sumidinhos["ID Prestador"].astype(str):
                if col > 15: break
                if EVITAR_REPETIR_EM_LISTAS_NO_DIA and sid in profissionais_sugeridas_no_dia[data_atendimento]:
                    continue
                if sid in profissionais_ocupadas_no_dia[data_atendimento]:
                    continue
                _add(sid, "Baixa Disponibilidade", _qtd_cli(df_cliente_prestador, cpf, sid) > 0)
    
        matriz_resultado_corrigida.append(linha)
    
    # ============================
    # Auditoria da camada de proximidade — salva junto no Excel
    # ============================
    df_auditoria = pd.DataFrame(auditoria_proximidade) if auditoria_proximidade else pd.DataFrame(
        columns=["Data","OS","CPF_CNPJ","Prof_Atribuida","Dist_Atribuida_km","Prof_Mais_Prox_Elegivel","Dist_Mais_Prox_km","Motivo_Nao_Mais_Proxima"]
    )
    
    # ============================
    # DataFrame final de Rotas + Excel
    # ============================
    df_matriz_rotas = pd.DataFrame(matriz_resultado_corrigida)
    app_url = "https://rotasvavive.streamlit.app/"
    df_matriz_rotas["Mensagem Padrão"] = df_matriz_rotas.apply(
        lambda row: f"👉 [Clique aqui para validar seu aceite]({app_url}?aceite={row['OS']})\n\n{row['Mensagem Padrão']}",
        axis=1
    )
    
    for i in range(1, 16):
        for c in [f"Classificação da Profissional {i}", f"Critério {i}", f"Nome Prestador {i}", f"Celular {i}", f"Critério Utilizado {i}"]:
            if c not in df_matriz_rotas.columns:
                df_matriz_rotas[c] = pd.NA
    
    base_cols = [
        "OS", "CPF_CNPJ", "Nome Cliente", "Data 1", "Serviço", "Plano",
        "Duração do Serviço", "Hora de entrada", "Observações prestador",
        "Ponto de Referencia", "Mensagem Padrão"
    ]
    prestador_cols = []
    for i in range(1, 16):
        prestador_cols.extend([
            f"Classificação da Profissional {i}",
            f"Critério {i}",
            f"Nome Prestador {i}",
            f"Celular {i}",
            f"Critério Utilizado {i}",
        ])
    df_matriz_rotas = df_matriz_rotas[base_cols + prestador_cols]
    
    final_path = os.path.join(output_dir, "rotas_bh_dados_tratados_completos.xlsx")
    with pd.ExcelWriter(final_path, engine='xlsxwriter') as writer:
        df_matriz_rotas.to_excel(writer, sheet_name="Rotas", index=False)
        df_atendimentos.to_excel(writer, sheet_name="Atendimentos", index=False)
        df_clientes.to_excel(writer, sheet_name="Clientes", index=False)
        df_profissionais.to_excel(writer, sheet_name="Profissionais", index=False)
        df_preferencias.to_excel(writer, sheet_name="Preferencias", index=False)
        df_bloqueio.to_excel(writer, sheet_name="Bloqueio", index=False)
        df_queridinhos.to_excel(writer, sheet_name="Queridinhos", index=False)
        df_sumidinhos.to_excel(writer, sheet_name="Sumidinhos", index=False)
        df_historico_60_dias.to_excel(writer, sheet_name="Historico 60 dias", index=False)
        df_cliente_prestador.to_excel(writer, sheet_name="Cliente x Prestador", index=False)
        df_qtd_por_prestador.to_excel(writer, sheet_name="Qtd por Prestador", index=False)
        df_distancias.to_excel(writer, sheet_name="Distancias", index=False)
        df_preferencias_completo.to_excel(writer, sheet_name="Preferencias Geo", index=False)
        df_bloqueio_completo.to_excel(writer, sheet_name="Bloqueios Geo", index=False)
        df_atendimentos_futuros_validos.to_excel(writer, sheet_name="Atend Futuros OK", index=False)
        df_atendimentos_sem_localizacao.to_excel(writer, sheet_name="Atend Futuros Sem Loc", index=False)
        # NOVO: auditoria
        df_auditoria.to_excel(writer, sheet_name="Auditoria Proximidade", index=False)


    return final_path

import streamlit as st
import os
import json
import pandas as pd

# Tente configurar o locale (pode ser ignorado se não funcionar)
try:
    locale.setlocale(locale.LC_TIME, 'pt_BR.UTF-8')
except:
    pass

def formatar_data_portugues(data):
    dias_pt = {
        "Monday": "segunda-feira",
        "Tuesday": "terça-feira",
        "Wednesday": "quarta-feira",
        "Thursday": "quinta-feira",
        "Friday": "sexta-feira",
        "Saturday": "sábado",
        "Sunday": "domingo"
    }
    if pd.isnull(data) or data == "":
        return ""
    try:
        s = str(data)
        if len(s) >= 10 and s[4] == '-' and s[7] == '-':
            # Trata explicitamente AAAA-MM-DD para não inverter mês/dia
            ano = s[0:4]
            mes = s[5:7]
            dia = s[8:10]
            dt = pd.Timestamp(year=int(ano), month=int(mes), day=int(dia))
        else:
            dt = pd.to_datetime(data, dayfirst=True, errors='coerce')
        if pd.isnull(dt):
            return str(data)
        dia_semana_en = dt.strftime("%A")
        dia_semana_pt = dias_pt.get(dia_semana_en, dia_semana_en)
        return f"{dia_semana_pt}, {dt.strftime('%d/%m/%Y')}"
    except Exception:
        return str(data)



PORTAL_EXCEL = "portal_atendimentos_clientes.xlsx"
PORTAL_OS_LIST = "portal_atendimentos_os_list.json"

# Função para registrar aceite (usada nos cards públicos ANTES da senha)
def salvar_aceite(os_id, profissional, telefone, aceitou, origem=None):
    # (NOVO) obrigatoriedade: valida back-end (esta é a segunda definição que você já tinha)
    profissional = (profissional or "").strip()
    telefone = (telefone or "").strip()
    if not profissional:
        raise ValueError("Nome da Profissional é obrigatório.")
    if not telefone:
        raise ValueError("Telefone é obrigatório.")

    from datetime import datetime
    ACEITES_FILE = "aceites.xlsx"
    agora = datetime.now()
    data = agora.strftime("%d/%m/%Y")
    dia_semana = agora.strftime("%A")
    horario = agora.strftime("%H:%M:%S")
    if os.path.exists(ACEITES_FILE):
        df = pd.read_excel(ACEITES_FILE)
    else:
        df = pd.DataFrame(columns=[
            "OS", "Profissional", "Telefone", "Aceitou", 
            "Data do Aceite", "Dia da Semana", "Horário do Aceite", "Origem"
        ])
    nova_linha = {
        "OS": os_id,
        "Profissional": profissional,
        "Telefone": telefone,
        "Aceitou": "Sim" if aceitou else "Não",
        "Data do Aceite": data,
        "Dia da Semana": dia_semana,
        "Horário do Aceite": horario,
        "Origem": origem if origem else ""
    }
    df = pd.concat([df, pd.DataFrame([nova_linha])], ignore_index=True)
    df.to_excel(ACEITES_FILE, index=False)

# Controle de autenticação global
if "admin_autenticado" not in st.session_state:
    st.session_state.admin_autenticado = False

# Só mostra cards e campo de senha enquanto não autenticou
if not st.session_state.admin_autenticado:
    st.markdown("""
        <div style='display:flex;align-items:center;gap:16px'>
            <img src='https://i.imgur.com/gIhC0fC.png' height='48'>
            <span style='font-size:1.7em;font-weight:700;color:#18d96b;letter-spacing:1px;'>BELO HORIZONTE || PORTAL DE ATENDIMENTOS</span>
        </div>
        <p style='color:#666;font-size:1.08em;margin:8px 0 18px 0'>
            Consulte abaixo os atendimentos disponíveis!
        </p>
    """, unsafe_allow_html=True)

    # ---- BLOCO VISUALIZAÇÃO (PÚBLICO) ----
    if os.path.exists(PORTAL_EXCEL) and os.path.exists(PORTAL_OS_LIST):
        df = pd.read_excel(PORTAL_EXCEL, sheet_name="Clientes")
        with open(PORTAL_OS_LIST, "r") as f:
            os_list = json.load(f)
        df = df[~df["OS"].isna()]  # remove linhas totalmente vazias de OS
        df = df[pd.to_numeric(df["OS"], errors="coerce").isin(os_list)]

       # ---- REMOVER OS COM 3+ ACEITES SIM ----
        ACEITES_FILE = "aceites.xlsx"
        if os.path.exists(ACEITES_FILE):
            def padronizar_os_coluna(col):
                def safe_os(x):
                    try:
                        return str(int(float(x))).strip()
                    except:
                        return ""
                return col.apply(safe_os).astype(str)
            df_aceites = pd.read_excel(ACEITES_FILE)
            df_aceites["OS"] = padronizar_os_coluna(df_aceites["OS"])
            df["OS"] = padronizar_os_coluna(df["OS"])
            aceites_sim = df_aceites[df_aceites["Aceitou"].astype(str).str.strip().str.lower() == "sim"]
            contagem = aceites_sim.groupby("OS").size()
            os_3mais = contagem[contagem >= 3].index.tolist()
            df = df[~df["OS"].isin(os_3mais)]
        # --------------------------------------



        
        if df.empty:
            st.info("Nenhum atendimento disponível.")
        else:
            st.write(f"Exibindo {len(df)} atendimentos selecionados pelo administrador:")
            for _, row in df.iterrows():
                servico = row.get("Serviço", "")
                nome_cliente = row.get("Cliente", "")
                bairro = row.get("Bairro", "")
                data = row.get("Data 1", "")
                data_pt = formatar_data_portugues(data)
                hora_entrada = row.get("Hora de entrada", "")
                hora_servico = row.get("Horas de serviço", "")
                referencia = row.get("Ponto de Referencia", "")
                os_id = int(row["OS"])

                st.markdown(f"""
                    <div style="
                        background: #fff;
                        border: 1.5px solid #eee;
                        border-radius: 18px;
                        padding: 18px 18px 12px 18px;
                        margin-bottom: 14px;
                        min-width: 260px;
                        max-width: 440px;
                        color: #00008B;
                        font-family: Arial, sans-serif;
                    ">
                        <div style="font-size:1.2em; font-weight:bold; color:#00008B; margin-bottom:2px;">
                            {servico}
                        </div>
                        <div style="font-size:1em; color:#00008B; margin-bottom:7px;">
                            <b style="color:#00008B;margin-left:24px">Bairro:</b> <span>{bairro}</span>
                        </div>
                        <div style="font-size:0.95em; color:#00008B;">
                            <b>Data:</b> <span>{data_pt}</span><br>
                            <b>Hora de entrada:</b> <span>{hora_entrada}</span><br>
                            <b>Horas de serviço:</b> <span>{hora_servico}</span><br>
                            <b>Ponto de Referência:</b> <span>{referencia if referencia and referencia != 'nan' else '-'}</span>
                        </div>
                    </div>
                """, unsafe_allow_html=True)
                expander_style = """
                <style>
                /* Aplica fundo verde e texto branco ao expander do Streamlit */
                div[role="button"][aria-expanded] {
                    background: #25D366 !important;
                    color: #fff !important;
                    border-radius: 10px !important;
                    font-weight: bold;
                    font-size: 1.08em;
                }
                </style>
                """
                st.markdown(expander_style, unsafe_allow_html=True)
                with st.expander("Tem disponibilidade? Clique aqui para aceitar este atendimento!"):
                    profissional = st.text_input(f"Nome da Profissional", key=f"prof_nome_{os_id}")
                    telefone = st.text_input(f"Telefone para contato", key=f"prof_tel_{os_id}")
                    resposta = st.empty()

                    # (NOVO) obrigatoriedade no card público (antes da senha)
                    _ok = bool((profissional or "").strip()) and bool((telefone or "").strip())

                    if st.button("Sim, tenho interesse neste atendimento.", key=f"btn_real_{os_id}", use_container_width=True, disabled=not _ok):
                        try:
                            salvar_aceite(os_id, profissional, telefone, True, origem="portal")
                        except ValueError as e:
                            resposta.error(f"❌ {e}")
                        else:
                            resposta.success("✅ Obrigado! Seu interesse foi registrado com sucesso. Em breve daremos retorno sobre o atendimento!")
    else:
        st.info("Nenhum atendimento disponível. Aguarde liberação do admin.")

    # ---- CAMPO DE SENHA para liberar as demais abas ----
    senha = st.text_input("Área restrita. Digite a senha para liberar as demais abas:", type="password")
    if st.button("Entrar", key="btn_senha_global"):
        if senha == "vvv":
            st.session_state.admin_autenticado = True
            st.rerun()
        else:
            st.error("Senha incorreta. Acesso restrito.")

    # Impede de ver as outras abas
    st.stop()


# Se autenticado, agora sim mostra TODAS as abas normalmente!
tabs = st.tabs(["Portal Atendimentos", "Upload de Arquivo", "Matriz de Rotas", "Aceites", "Profissionais Próximos", "Mensagem Rápida", "Auditoria (Proximidade)"])

with tabs[1]:
    if "excel_processado" not in st.session_state:
        st.session_state.excel_processado = False
    if "nome_arquivo_processado" not in st.session_state:
        st.session_state.nome_arquivo_processado = None

    uploaded_file = st.file_uploader("Selecione o arquivo Excel original", type=["xlsx"])

    # Só processa se o arquivo mudou ou nunca foi processado
    if uploaded_file is not None:
        if (
            not st.session_state.excel_processado
            or st.session_state.nome_arquivo_processado != uploaded_file.name
        ):
            with st.spinner("Processando... Isso pode levar alguns segundos."):
                with tempfile.TemporaryDirectory() as tempdir:
                    temp_path = os.path.join(tempdir, uploaded_file.name)
                    with open(temp_path, "wb") as f:
                        f.write(uploaded_file.read())
                    try:
                        excel_path = pipeline(temp_path, tempdir)
                        if os.path.exists(excel_path):
                            st.success("Processamento finalizado com sucesso!")
                            st.download_button(
                                label="📥 Baixar Excel consolidado",
                                data=open(excel_path, "rb").read(),
                                file_name="rotas_bh_dados_tratados_completos.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                key="download_excel_consolidado"
                            )
                            import shutil
                            shutil.copy(excel_path, "rotas_bh_dados_tratados_completos.xlsx")
                            st.session_state.excel_processado = True
                            st.session_state.nome_arquivo_processado = uploaded_file.name
                        else:
                            st.error("Arquivo final não encontrado. Ocorreu um erro no pipeline.")
                    except Exception as e:
                        st.error(f"Erro no processamento: {e}") 
        else:
            # Já processado: só mostra o botão de download
            if os.path.exists("rotas_bh_dados_tratados_completos.xlsx"):
                st.download_button(
                    label="📥 Baixar Excel consolidado",
                    data=open("rotas_bh_dados_tratados_completos.xlsx", "rb").read(),
                    file_name="rotas_bh_dados_tratados_completos.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="download_excel_consolidado"
                )
    else:
        # Resetar caso usuário remova o arquivo
        st.session_state.excel_processado = False
        st.session_state.nome_arquivo_processado = None
    

with tabs[2]:
    
    if os.path.exists(ROTAS_FILE):
        df_rotas = pd.read_excel(ROTAS_FILE, sheet_name="Rotas")
        datas = df_rotas["Data 1"].dropna().sort_values().dt.date.unique()
        data_sel = st.selectbox("Filtrar por data", options=["Todos"] + [str(d) for d in datas], key="data_rotas")
        clientes = df_rotas["Nome Cliente"].dropna().unique()
        cliente_sel = st.selectbox("Filtrar por cliente", options=["Todos"] + list(clientes), key="cliente_rotas")
        profissionais = []
        for i in range(1, 11):
            profissionais.extend(df_rotas[f"Nome Prestador {i}"].dropna().unique())
        profissionais = list(set([p for p in profissionais if isinstance(p, str)]))
        profissional_sel = st.selectbox("Filtrar por profissional", options=["Todos"] + profissionais, key="prof_rotas")
        df_rotas_filt = df_rotas.copy()
        if data_sel != "Todos":
            df_rotas_filt = df_rotas_filt[df_rotas_filt["Data 1"].dt.date.astype(str) == data_sel]
        if cliente_sel != "Todos":
            df_rotas_filt = df_rotas_filt[df_rotas_filt["Nome Cliente"] == cliente_sel]
        if profissional_sel != "Todos":
            mask = False
            for i in range(1, 11):
                mask |= (df_rotas_filt[f"Nome Prestador {i}"] == profissional_sel)
            df_rotas_filt = df_rotas_filt[mask]
        st.dataframe(df_rotas_filt, use_container_width=True)
        st.download_button(
            label="📥 Baixar Excel consolidado",
            data=open(ROTAS_FILE, "rb").read(),
            file_name="rotas_bh_dados_tratados_completos.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    else:
        st.info("Faça o upload e aguarde o processamento para liberar a matriz de rotas.")



with tabs[3]:
    if "atualizar_aceites" not in st.session_state:
        st.session_state.atualizar_aceites = False

    if st.button("🔄 Atualizar aceites"):
        st.session_state.atualizar_aceites = not st.session_state.atualizar_aceites
        st.rerun()

    # ... todo o código atual da aba ...



    if os.path.exists(ACEITES_FILE) and os.path.exists(ROTAS_FILE):
        import io
        from datetime import datetime

        df_aceites = pd.read_excel(ACEITES_FILE)
        df_rotas = pd.read_excel(ROTAS_FILE, sheet_name="Rotas")

        df_aceites["OS"] = df_aceites["OS"].astype(str).str.strip()
        df_rotas["OS"] = df_rotas["OS"].astype(str).str.strip()

        df_aceites_completo = pd.merge(
            df_aceites, df_rotas[
                ["OS", "CPF_CNPJ", "Nome Cliente", "Data 1", "Serviço", "Plano",
                 "Duração do Serviço", "Hora de entrada", "Observações prestador", "Ponto de Referencia"]
            ],
            how="left", on="OS"
        )

        
        # ---------- BLOCO DE INDICADOR: Quantos aceites SIM por OS ----------
        datas = df_rotas["Data 1"].dropna().sort_values().dt.date.unique()
        data_sel = st.selectbox("Filtrar por data do atendimento", options=["Todos"] + [str(d) for d in datas], key="data_aceite")
        df_rotas_sel = df_rotas.copy()
        if data_sel != "Todos":
            df_rotas_sel = df_rotas_sel[df_rotas_sel["Data 1"].dt.date.astype(str) == data_sel]
        else:
            hoje = datetime.now().date()
            df_rotas_sel = df_rotas_sel[df_rotas_sel["Data 1"].dt.date == hoje]
        os_do_dia = df_rotas_sel["OS"].astype(str).unique()
        aceites_do_dia = df_aceites_completo[df_aceites_completo["OS"].astype(str).isin(os_do_dia)]
        
        # Normaliza colunas OS
        df_rotas_sel["OS"] = df_rotas_sel["OS"].astype(str).str.strip()
        aceites_do_dia["OS"] = aceites_do_dia["OS"].astype(str).str.strip()
        
        # Só aceita SIM
        aceites_sim = aceites_do_dia[aceites_do_dia["Aceitou"].astype(str).str.strip().str.lower() == "sim"]
        qtd_aceites_por_os = aceites_sim.groupby("OS").size()
        
        df_qtd_aceites = pd.DataFrame({'OS': os_do_dia})
        df_qtd_aceites["Qtd Aceites"] = df_qtd_aceites["OS"].map(qtd_aceites_por_os).fillna(0).astype(int)
        df_qtd_aceites = df_qtd_aceites.sort_values("OS")
        
        st.markdown("### Indicador: Quantidade de Aceites por OS")
        
        custom_css = """
        <style>
        th, td {
            min-width: 80px !important;
            max-width: 100px !important;
            text-align: center !important;
        }
        </style>
        """
        st.markdown(custom_css, unsafe_allow_html=True)
        st.markdown(df_qtd_aceites.to_html(index=False), unsafe_allow_html=True)

        # ---------- FIM DO BLOCO DE INDICADOR ----------


        # Filtros detalhados
        clientes = df_aceites_completo["Nome Cliente"].dropna().unique()
        cliente_sel = st.selectbox("Filtrar por cliente", options=["Todos"] + list(clientes), key="cliente_aceite")
        profissionais = df_aceites_completo["Profissional"].dropna().unique() if "Profissional" in df_aceites_completo else []
        profissional_sel = st.selectbox("Filtrar por profissional", options=["Todos"] + list(profissionais), key="prof_aceite")
        # Gera lista de OS válidas
        os_validos = df_aceites_completo["OS"].dropna().astype(str).unique()
        os_sel = st.selectbox("Filtrar por OS", options=["Todos"] + list(os_validos), key="os_aceite")

        df_aceites_filt = df_aceites_completo.copy()
        if data_sel != "Todos":
            df_aceites_filt = df_aceites_filt[df_aceites_filt["Data 1"].dt.date.astype(str) == data_sel]
        if cliente_sel != "Todos":
            df_aceites_filt = df_aceites_filt[df_aceites_filt["Nome Cliente"] == cliente_sel]
        if profissional_sel != "Todos" and "Profissional" in df_aceites_filt:
            df_aceites_filt = df_aceites_filt[df_aceites_filt["Profissional"] == profissional_sel]
        if os_sel != "Todos":
            df_aceites_filt = df_aceites_filt[df_aceites_filt["OS"].astype(str) == os_sel]

        st.dataframe(df_aceites_filt, use_container_width=True)
        output = io.BytesIO()
        df_aceites_filt.to_excel(output, index=False)
        st.download_button(
            label="Baixar histórico de aceites (completo)",
            data=output.getvalue(),
            file_name="aceites_completo.xlsx",
            key="download_aceites_completo"
        )
    elif os.path.exists(ACEITES_FILE):
        import io
        df_aceites = pd.read_excel(ACEITES_FILE)
        st.dataframe(df_aceites)
        output = io.BytesIO()
        df_aceites.to_excel(output, index=False)
        st.download_button(
            label="Baixar histórico de aceites",
            data=output.getvalue(),
            file_name="aceites.xlsx",
            key="download_aceites"
        )
    else:
        st.info("Nenhum aceite registrado ainda.")



import json
import urllib.parse


with tabs[0]:
    st.markdown("""
        <div style='display:flex;align-items:center;gap:16px'>
            <img src='https://i.imgur.com/gIhC0fC.png' height='48'>
            <span style='font-size:1.7em;font-weight:700;color:#18d96b;letter-spacing:1px;'>BELO HORIZONTE || PORTAL DE ATENDIMENTOS</span>
        </div>
        <p style='color:#666;font-size:1.08em;margin:8px 0 18px 0'>
            Consulte abaixo os atendimentos disponíveis!
        </p>
        """, unsafe_allow_html=True)

    # Controle de exibição e autenticação admin
    if "exibir_admin_portal" not in st.session_state:
        st.session_state.exibir_admin_portal = False
    if "admin_autenticado_portal" not in st.session_state:
        st.session_state.admin_autenticado_portal = False

    # Botão para mostrar a área admin
    if st.button("Acesso admin para editar atendimentos do portal"):
        st.session_state.exibir_admin_portal = True

    # ---- BLOCO ADMIN ----
    if st.session_state.exibir_admin_portal:
        senha = st.text_input("Digite a senha de administrador", type="password", key="senha_portal_admin")
        if st.button("Validar senha", key="btn_validar_senha_portal"):
            if senha == "vvv":
                st.session_state.admin_autenticado_portal = True
            else:
                st.error("Senha incorreta.")

    if st.session_state.admin_autenticado_portal:
        # Permite upload OU reutilização do arquivo salvo
        if "portal_file_buffer" not in st.session_state:
            st.session_state.portal_file_buffer = None
    
        uploaded_file = st.file_uploader("Faça upload do arquivo Excel", type=["xlsx"], key="portal_upload")
        use_last_file = False
    
        if uploaded_file:
            # Salva arquivo na sessão e disco
            st.session_state.portal_file_buffer = uploaded_file.getbuffer()
            with open(PORTAL_EXCEL, "wb") as f:
                f.write(st.session_state.portal_file_buffer)
            st.success("Arquivo salvo! Escolha agora os atendimentos que ficarão visíveis.")
            df = pd.read_excel(PORTAL_EXCEL, sheet_name="Clientes")
        elif st.session_state.portal_file_buffer:
            # Usa o arquivo já carregado na sessão
            with open(PORTAL_EXCEL, "wb") as f:
                f.write(st.session_state.portal_file_buffer)
            df = pd.read_excel(PORTAL_EXCEL, sheet_name="Clientes")
        elif os.path.exists(PORTAL_EXCEL):
            # Usa o arquivo salvo no disco
            df = pd.read_excel(PORTAL_EXCEL, sheet_name="Clientes")
        else:
            df = None
    
        if df is not None:
            # ------- FILTRO POR DATA1 -------
            datas_disponiveis = sorted(df["Data 1"].dropna().unique())
            datas_formatadas = [str(pd.to_datetime(d).date()) for d in datas_disponiveis]
            datas_selecionadas = st.multiselect(
                "Filtrar atendimentos por Data",
                options=datas_formatadas,
                default=[],
                key="datas_multiselect"
            )
            if datas_selecionadas:
                df = df[df["Data 1"].astype(str).apply(lambda d: str(pd.to_datetime(d).date()) in datas_selecionadas)]
    
            # Monta opções com OS, Cliente, Serviço e Bairro
            opcoes = [
                f'OS {int(row.OS)} | {row["Cliente"]} | {row.get("Serviço", "")} | {row.get("Bairro", "")}'
                for _, row in df.iterrows()
                if not pd.isnull(row.OS)
            ]
            selecionadas = st.multiselect(
                "Selecione os atendimentos para exibir (OS | Cliente | Serviço | Bairro)",
                opcoes,
                key="os_multiselect"
            )
            if st.button("Salvar atendimentos exibidos", key="salvar_os_btn"):
                # Para salvar apenas a lista de OS selecionadas (extraindo da string)
                os_ids = [
                    int(op.split()[1]) for op in selecionadas
                    if op.startswith("OS ")
                ]
                with open(PORTAL_OS_LIST, "w") as f:
                    json.dump(os_ids, f)
                st.success("Seleção salva! Agora os atendimentos já ficam disponíveis a todos.")
                st.session_state.exibir_admin_portal = False
                st.session_state.admin_autenticado_portal = False
                st.rerun()


    # ---- BLOCO VISUALIZAÇÃO (PÚBLICO) ----
    if not st.session_state.exibir_admin_portal:
        if os.path.exists(PORTAL_EXCEL) and os.path.exists(PORTAL_OS_LIST):
            df = pd.read_excel(PORTAL_EXCEL, sheet_name="Clientes")
            with open(PORTAL_OS_LIST, "r") as f:
                os_list = json.load(f)
            # Só exibe OS selecionadas
            df = df[~df["OS"].isna()]  # remove linhas totalmente vazias de OS
            df = df[pd.to_numeric(df["OS"], errors="coerce").isin(os_list)]
            if df.empty:
                st.info("Nenhum atendimento disponível.")
            else:
                st.write(f"Exibindo {len(df)} atendimentos selecionados pelo administrador:")
                for _, row in df.iterrows():
                    servico = row.get("Serviço", "")
                    nome_cliente = row.get("Cliente", "")
                    bairro = row.get("Bairro", "")
                    data = formatar_data_portugues(row.get("Data 1", ""))
                    hora_entrada = row.get("Hora de entrada", "")
                    hora_servico = row.get("Horas de serviço", "")
                    referencia = row.get("Ponto de Referencia", "")
                    os_id = int(row["OS"])
                    
                    st.markdown(f"""
                        <div style="
                            background: #fff;
                            border: 1.5px solid #eee;
                            border-radius: 18px;
                            padding: 18px 18px 12px 18px;
                            margin-bottom: 14px;
                            min-width: 260px;
                            max-width: 440px;
                            color: #00008B;
                            font-family: Arial, sans-serif;
                        ">
                            <div style="font-size:1.2em; font-weight:bold; color:#00008B; margin-bottom:2px;">
                                {servico}
                            </div>
                            <div style="font-size:1em; color:#00008B; margin-bottom:7px;">
                                <b style="color:#00008B;margin-left:24px">Bairro:</b> <span>{bairro}</span>
                            </div>
                            <div style="font-size:0.95em; color:#00008B;">
                                <b>Data:</b> <span>{data}</span><br>
                                <b>Hora de entrada:</b> <span>{hora_entrada}</span><br>
                                <b>Horas de serviço:</b> <span>{hora_servico}</span><br>
                                <b>Ponto de Referência:</b> <span>{referencia if referencia and referencia != 'nan' else '-'}</span>
                            </div>
                        </div>
                    """, unsafe_allow_html=True)
                    
                    expander_style = """
                    <style>
                    /* Aplica fundo verde e texto branco ao expander do Streamlit */
                    div[role="button"][aria-expanded] {
                        background: #25D66 !important;
                        color: #fff !important;
                        border-radius: 10px !important;
                        font-weight: bold;
                        font-size: 1.08em;
                    }
                    </style>
                    """
                    st.markdown(expander_style, unsafe_allow_html=True)
                    
                    with st.expander("Tem disponibilidade? Clique aqui para aceitar este atendimento!"):
                        profissional = st.text_input(f"Nome da Profissional (OBRIGATORIO)", key=f"prof_nome_{os_id}")
                        telefone = st.text_input(f"Telefone para contato (OBRIGATORIO)", key=f"prof_tel_{os_id}")
                        resposta = st.empty()

                        # (NOVO) obrigatoriedade também neste card (aba Portal)
                        _ok = bool((profissional or "").strip()) and bool((telefone or "").strip())

                        if st.button("Sim, tenho interesse neste atendimento.", key=f"btn_real_{os_id}", use_container_width=True, disabled=not _ok):
                            try:
                                salvar_aceite(os_id, profissional, telefone, True, origem="portal")
                            except ValueError as e:
                                resposta.error(f"❌ {e}")
                            else:
                                resposta.success("✅ Obrigado! Seu interesse foi registrado com sucesso. Em breve daremos retorno sobre o atendimento!")


        else:
            st.info("Nenhum atendimento disponível. Aguarde liberação do admin.")

with tabs[4]:
        st.subheader("Buscar Profissionais Próximos")
        lat = st.number_input("Latitude", value=-19.9, format="%.6f")
        lon = st.number_input("Longitude", value=-4.9, format="%.6f")
        n = st.number_input("Qtd. profissionais", min_value=1, value=5, step=1)
        if st.button("Buscar"):
            # Usa o df_profissionais já tratado do pipeline
            if os.path.exists(ROTAS_FILE):
                df_profissionais = pd.read_excel(ROTAS_FILE, sheet_name="Profissionais")
                mask_inativo_nome = df_profissionais['Nome Prestador'].astype(str).str.contains('inativo', case=False, na=False)
                df_profissionais = df_profissionais[~mask_inativo_nome]
                df_profissionais = df_profissionais.dropna(subset=['Latitude Profissional', 'Longitude Profissional'])
                input_coords = (lat, lon)
                df_profissionais['Distância_km'] = df_profissionais.apply(
                    lambda row: geodesic(input_coords, (row['Latitude Profissional'], row['Longitude Profissional'])).km, axis=1
                )
                df_melhores = df_profissionais.sort_values('Distância_km').head(int(n))
                st.dataframe(df_melhores[['Nome Prestador', 'Celular', 'Qtd Atendimentos', 'Latitude Profissional', 'Longitude Profissional', 'Distância_km']])
            else:
                st.info("Faça upload e processamento do arquivo para habilitar a busca.")
    
# Aba "Mensagem Rápida"
with tabs[5]:
    st.subheader("Gerar Mensagem Rápida WhatsApp")
    os_id = st.text_input("Código da OS* (obrigatório)", max_chars=12)
    data = st.text_input("Data do Atendimento (ex: 20/06/2025)")
    bairro = st.text_input("Bairro")
    servico = st.text_input("Serviço")
    hora_entrada = st.text_input("Hora de entrada (ex: 08:00)")
    duracao = st.text_input("Duração do atendimento (ex: 2h)")

    app_url = "https://rotasvavive.streamlit.app"  # sua URL real
    if os_id.strip():
        link_aceite = f"{app_url}?aceite={os_id}&origem=mensagem_rapida"
    else:
        link_aceite = ""

    if st.button("Gerar Mensagem"):
        if not os_id.strip():
            st.error("Preencha o código da OS!")
        else:
            mensagem = (
                "🚨🚨🚨\n"
                "     *Oportunidade Relâmpago*\n"
                "                              🚨🚨🚨\n\n"
                f"Olá, tudo bem com você?\n\n"
                f"*Data:* {data}\n"
                f"*Bairro:* {bairro}\n"
                f"*Serviço:* {servico}\n"
                f"*Hora de entrada:* {hora_entrada}\n"
                f"*Duração do atendimento:* {duracao}\n\n"
                f"👉 Para aceitar ou recusar, acesse: {link_aceite}\n\n"
                "Se tiver interesse, por favor, nos avise!"
            )
            st.text_area("Mensagem WhatsApp", value=mensagem, height=260)


with tabs[6]:
    st.subheader("Auditoria por OS — Camada 4 (Proximidade)")
    if not os.path.exists(ROTAS_FILE):
        st.info("Faça o upload e processamento do arquivo para habilitar a auditoria.")
    else:
        try:
            df_aud = pd.read_excel(ROTAS_FILE, sheet_name="Auditoria Proximidade")
        except Exception:
            st.info("A planilha não contém a aba 'Auditoria Proximidade'. Rode o pipeline novamente para gerar.")
            df_aud = pd.DataFrame()

        if df_aud.empty:
            st.info("Sem registros de auditoria para exibir.")
        else:
            # Normalizações básicas
            df_aud["OS"] = df_aud["OS"].astype(str).str.strip()

            # Carrega apoio para enriquecer (datas, cliente e nomes das profissionais)
            df_rotas = pd.read_excel(ROTAS_FILE, sheet_name="Rotas")
            df_rotas["OS"] = df_rotas["OS"].astype(str).str.strip()
            df_profs = pd.read_excel(ROTAS_FILE, sheet_name="Profissionais")
            df_profs["ID Prestador"] = df_profs["ID Prestador"].astype(str).str.strip()

            # Mapa id->nome
            id2nome = dict(zip(df_profs["ID Prestador"], df_profs["Nome Prestador"]))

            # Enriquecimentos
            df_aud["Nome Prof Atribuída"] = df_aud["Prof_Atribuida"].astype(str).str.strip().map(id2nome)
            df_aud["Nome Prof Mais Próx."] = df_aud["Prof_Mais_Prox_Elegivel"].astype(str).str.strip().map(id2nome)

            df_aud = df_aud.merge(
                df_rotas[["OS", "Nome Cliente", "Data 1", "Serviço"]],
                how="left", on="OS"
            )

            # Sinaliza divergência (quando não foi a mais próxima individual)
            df_aud["Divergência"] = (
                df_aud["Prof_Atribuida"].astype(str).str.strip() !=
                df_aud["Prof_Mais_Prox_Elegivel"].astype(str).str.strip()
            )

            # ---- Filtros ----
            datas = (
                df_aud["Data 1"].dropna().sort_values().dt.date.astype(str).unique()
                if "Data 1" in df_aud and df_aud["Data 1"].notna().any() else []
            )
            colf1, colf2, colf3 = st.columns([1,1,1])
            data_sel = colf1.selectbox("Filtrar por data", options=["Todos"] + list(datas))
            only_div = colf2.toggle("Mostrar apenas divergências (não foi a mais próxima)", value=False)
            os_opcoes = ["Todos"] + sorted(df_aud["OS"].dropna().astype(str).unique().tolist())
            os_sel = colf3.selectbox("Filtrar por OS", options=os_opcoes)

            df_view = df_aud.copy()
            if data_sel != "Todos" and "Data 1" in df_view:
                df_view = df_view[df_view["Data 1"].dt.date.astype(str) == data_sel]
            if only_div:
                df_view = df_view[df_view["Divergência"]]
            if os_sel != "Todos":
                df_view = df_view[df_view["OS"].astype(str) == os_sel]

            # Ordenação amigável
            if "Data 1" in df_view:
                df_view = df_view.sort_values(["Data 1", "OS"])

            # Seleção de colunas e formatações
            cols_show = [
                "Data 1", "OS", "Nome Cliente", "Serviço",
                "Prof_Atribuida", "Nome Prof Atribuída", "Dist_Atribuida_km",
                "Prof_Mais_Prox_Elegivel", "Nome Prof Mais Próx.", "Dist_Mais_Prox_km",
                "Motivo_Nao_Mais_Proxima", "Divergência"
            ]
            cols_show = [c for c in cols_show if c in df_view.columns]
            st.markdown("#### Resultado da Auditoria")
            st.dataframe(df_view[cols_show], use_container_width=True)

            # Download
            import io
            buff = io.BytesIO()
            df_view.to_excel(buff, index=False)
            st.download_button(
                "📥 Baixar auditoria filtrada (xlsx)",
                data=buff.getvalue(),
                file_name="auditoria_proximidade_filtrada.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

            # Indicadores rápidos
            total_linhas = len(df_view)
            divergentes = int(df_view["Divergência"].sum()) if "Divergência" in df_view else 0
            st.caption(f"Linhas exibidas: {total_linhas} | Divergências: {divergentes}")


