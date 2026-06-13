import streamlit as st
import pandas as pd
import sqlite3
import os
from datetime import datetime
import PyPDF2
import re
import unicodedata
from fpdf import FPDF 

# Cria as pastas físicas para salvar os arquivos
PASTA_NOTAS = 'cofre_notas'
PASTA_EXTRATOS = 'cofre_extratos'
os.makedirs(PASTA_NOTAS, exist_ok=True)
os.makedirs(PASTA_EXTRATOS, exist_ok=True)

# --- INICIALIZAÇÃO DE VARIÁVEIS DE SESSÃO ---
# Mês atual automático para o painel iniciar no mês em que estamos.
MES_ATUAL = datetime.now().strftime("%m/%Y")
ANO_ATUAL = datetime.now().year

if "delete_step" not in st.session_state:
    st.session_state.delete_step = 0

# Inicializa o mês padrão uma vez por sessão.
# Assim o site abre no mês atual, mas ainda permite trocar o mês manualmente depois.
if "mes_salvo_auto_inicializado" not in st.session_state:
    st.session_state.mes_salvo = MES_ATUAL
    st.session_state.mes_salvo_auto_inicializado = True

# --- FUNÇÃO DE MÁSCARA BRASILEIRA (R$ 1.000,00) ---
def formatar_brl(valor):
    try:
        v = float(valor)
        return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return "R$ 0,00"

# --- DOCUMENTOS QUE ENTRAM COMO GANHO ---
def normalizar_texto_chave(texto):
    """Cria uma chave simples para comparar nomes ignorando acentos, maiúsculas e espaços extras."""
    texto = str(texto or "").strip()
    texto = " ".join(texto.split())
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return texto.lower()


def normalizar_nome_plataforma(origem):
    """
    Padroniza o nome da plataforma para que lançamentos manuais somem junto
    com os nomes que já aparecem no BALANÇO GERAL.
    Ex.: Mercado Livre, mercado livre, MERCADO LIVRE e Mercado Pago ficam no mesmo grupo.
    """
    origem_limpa = " ".join(str(origem or "").strip().split())
    chave = normalizar_texto_chave(origem_limpa)

    if not origem_limpa:
        return "Sem identificação"

    aliases = [
        (["mercado livre", "mercado pago", "mercadolivre", "mercadopago", "ebazar", "mercado livre/pago"], "Mercado Livre"),
        (["shopee", "shps"], "Shopee (SHPS Tecnologia)"),
        (["amazon"], "Amazon"),
        (["awin"], "AWIN"),
        (["cssbuy", "css buy"], "CSSBuy"),
        (["aliexpress", "ali express", "alibaba"], "Aliexpress"),
        (["google llc"], "Google LLC"),
        (["google"], "Google"),
        (["magazine", "magalu"], "Magazine"),
        (["terabyte", "terabyte shop"], "Terabyte"),
    ]

    for termos, nome_padrao in aliases:
        if any(termo in chave for termo in termos):
            return nome_padrao

    return origem_limpa


def filtrar_documentos_de_ganho(df):
    """
    Tudo que não for guia de imposto entra como ganho.
    Também padroniza o nome da plataforma para somar manual + nota no mesmo grupo.
    """
    if df.empty or 'tipo' not in df.columns:
        return df

    df_ganho = df[df['tipo'].fillna('').str.strip() != "Guia de Imposto (DAS/DARF)"].copy()

    if 'origem' in df_ganho.columns:
        df_ganho['origem'] = df_ganho['origem'].apply(normalizar_nome_plataforma)

    df_ganho['valor'] = pd.to_numeric(df_ganho['valor'], errors='coerce').fillna(0.0)
    return df_ganho


def agrupar_ganhos_por_plataforma(df):
    """Agrupa ganhos por plataforma já com nome normalizado."""
    if df.empty or 'origem' not in df.columns or 'valor' not in df.columns:
        return pd.DataFrame(columns=['origem', 'valor'])

    df_tmp = df.copy()
    df_tmp['origem'] = df_tmp['origem'].apply(normalizar_nome_plataforma)
    df_tmp['valor'] = pd.to_numeric(df_tmp['valor'], errors='coerce').fillna(0.0)

    return (
        df_tmp.groupby('origem', as_index=False)['valor']
        .sum()
        .sort_values('origem')
    )

# --- FUNÇÃO PARA GARANTIR VALOR ORIGINAL + VALOR CONSIDERADO ---
def normalizar_valor_ajustado(df):
    """Garante que todo lançamento tenha um valor ajustável para a matemática do painel.
    O campo 'valor' continua sendo o valor original do extrato.
    O campo 'valor_ajustado' é o valor que entra nos cálculos.
    """
    if df.empty:
        return df
    df = df.copy()
    df['valor'] = pd.to_numeric(df['valor'], errors='coerce').fillna(0.0)
    if 'valor_ajustado' not in df.columns:
        df['valor_ajustado'] = df['valor']
    else:
        df['valor_ajustado'] = pd.to_numeric(df['valor_ajustado'], errors='coerce')
        df['valor_ajustado'] = df['valor_ajustado'].fillna(df['valor'])
    return df

# --- FUNÇÃO DE GERAÇÃO DE PDF ---
def gerar_relatorio_pdf(mes, ganho_txt, gasto_txt, lucro_txt, df_notas, df_gastos_cat_pf, df_gastos_cat_pj, fatura_pf_txt, fatura_pj_txt):
    pdf = FPDF()
    pdf.add_page()
    
    # Título
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, f"RELATORIO FINANCEIRO - {mes}", ln=True, align="C")
    pdf.ln(5)
    
    # Resumo
    pdf.set_fill_color(230, 230, 230)
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 10, " VEREDICTO DO MES", ln=True, fill=True)
    pdf.set_font("Arial", "", 12)
    pdf.cell(0, 8, f" Total Ganho: {ganho_txt}", ln=True)
    pdf.cell(0, 8, f" Total Gasto: {gasto_txt}", ln=True)
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 8, f" LUCRO LIQUIDO: {lucro_txt}", ln=True)
    pdf.ln(5)
    
    # Faturas de Cartão (Apenas para noção)
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 10, " FATURAS DE CARTAO DE CREDITO (Informativo)", ln=True, fill=True)
    pdf.set_font("Arial", "", 10)
    pdf.cell(0, 8, f"  - Cartao Empresa (PJ): {fatura_pj_txt}", ln=True)
    pdf.cell(0, 8, f"  - Cartao Pessoal (PF): {fatura_pf_txt}", ln=True)
    pdf.ln(5)
    
    # Notas Fiscais
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 10, " ORIGEM DOS GANHOS (Notas Fiscais / PJ)", ln=True, fill=True)
    pdf.set_font("Arial", "", 10)
    if not df_notas.empty:
        for _, row in df_notas.iterrows():
            origem = str(row['origem']).encode('ascii', 'ignore').decode('ascii') 
            pdf.cell(0, 8, f"  - {origem}: {formatar_brl(row['valor'])}", ln=True)
    else:
        pdf.cell(0, 8, "  - Nenhuma nota de lucro vinculada neste mes.", ln=True)
    pdf.ln(5)
    
    # Gastos PJ (Empresa)
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 10, " DESPESAS POR CATEGORIA (PJ / EMPRESA)", ln=True, fill=True)
    pdf.set_font("Arial", "", 10)
    if not df_gastos_cat_pj.empty:
        for _, row in df_gastos_cat_pj.iterrows():
            cat = str(row['categoria']).encode('ascii', 'ignore').decode('ascii')
            pdf.cell(0, 8, f"  - {cat}: {formatar_brl(row['valor'])}", ln=True)
    else:
        pdf.cell(0, 8, "  - Nenhum gasto PJ classificado neste mes.", ln=True)
    pdf.ln(5)

    # Gastos PF (Pessoal)
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 10, " DESPESAS POR CATEGORIA (PF / PESSOAL)", ln=True, fill=True)
    pdf.set_font("Arial", "", 10)
    if not df_gastos_cat_pf.empty:
        for _, row in df_gastos_cat_pf.iterrows():
            cat = str(row['categoria']).encode('ascii', 'ignore').decode('ascii')
            pdf.cell(0, 8, f"  - {cat}: {formatar_brl(row['valor'])}", ln=True)
    else:
        pdf.cell(0, 8, "  - Nenhum gasto PF classificado neste mes.", ln=True)
        
    # Compatibilidade com versões novas e antigas do fpdf2:
    # algumas versões retornam string e outras retornam bytearray/bytes.
    pdf_data = pdf.output(dest="S")
    if isinstance(pdf_data, str):
        return pdf_data.encode("latin-1")
    return bytes(pdf_data)

# --- BANCO DE DADOS ---
def init_db():
    conn = sqlite3.connect('slet_financas.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS transacoes (
            identificador TEXT PRIMARY KEY,
            tipo_conta TEXT,
            data TEXT,
            valor REAL,
            valor_ajustado REAL,
            descricao TEXT,
            categoria TEXT,
            arquivo_origem TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS notas_fiscais (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mes_ano TEXT,
            tipo TEXT,
            origem TEXT,
            valor REAL,
            nome_arquivo TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS extratos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome_arquivo TEXT,
            tipo_conta TEXT,
            data_upload TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS faturas_cartao (
            mes_ano TEXT PRIMARY KEY,
            fatura_pf REAL,
            fatura_pj REAL
        )
    ''')
    
    c.execute("PRAGMA table_info(transacoes)")
    colunas = [info[1] for info in c.fetchall()]
    if 'categoria' not in colunas:
        c.execute("ALTER TABLE transacoes ADD COLUMN categoria TEXT DEFAULT 'Não Classificado'")
    if 'arquivo_origem' not in colunas:
        c.execute("ALTER TABLE transacoes ADD COLUMN arquivo_origem TEXT DEFAULT 'Desconhecido'")
    if 'valor_ajustado' not in colunas:
        c.execute("ALTER TABLE transacoes ADD COLUMN valor_ajustado REAL")
        c.execute("UPDATE transacoes SET valor_ajustado = valor WHERE valor_ajustado IS NULL")
        
    conn.commit()
    return conn

# --- FUNÇÕES DE FATURA ---
def salvar_faturas(mes_ano, pf, pj):
    conn = init_db()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO faturas_cartao (mes_ano, fatura_pf, fatura_pj) VALUES (?, ?, ?)", (mes_ano, pf, pj))
    conn.commit()
    conn.close()

def carregar_faturas(mes_ano):
    conn = init_db()
    c = conn.cursor()
    if mes_ano == "Todos":
        c.execute("SELECT SUM(fatura_pf), SUM(fatura_pj) FROM faturas_cartao")
    else:
        c.execute("SELECT fatura_pf, fatura_pj FROM faturas_cartao WHERE mes_ano = ?", (mes_ano,))
    row = c.fetchone()
    conn.close()
    if row and row[0] is not None:
        return row[0], row[1]
    return 0.0, 0.0

# --- FUNÇÃO DE INJEÇÃO DE GASTOS FIXOS ---
def lancar_gastos_fixos_manuais(mes_ano):
    conn = init_db()
    c = conn.cursor()
    
    data_lancamento = f"01/{mes_ano}"
    
    id_zapi = f"FIXO_ZAPI_{mes_ano.replace('/', '')}"
    c.execute("SELECT 1 FROM transacoes WHERE identificador = ?", (id_zapi,))
    if not c.fetchone():
        c.execute("""
            INSERT INTO transacoes 
            (identificador, tipo_conta, data, valor, valor_ajustado, descricao, categoria, arquivo_origem)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (id_zapi, "PJ (Empresa)", data_lancamento, -99.99, -99.99, "ZAPI (Bot de Encaminhar)", "Gastos Fixos", "Lancamento_Automatico"))
                  
    id_do = f"FIXO_DO_{mes_ano.replace('/', '')}"
    c.execute("SELECT 1 FROM transacoes WHERE identificador = ?", (id_do,))
    if not c.fetchone():
        c.execute("""
            INSERT INTO transacoes 
            (identificador, tipo_conta, data, valor, valor_ajustado, descricao, categoria, arquivo_origem)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (id_do, "PJ (Empresa)", data_lancamento, -60.99, -60.99, "Digital Ocean (Servidor)", "Gastos Fixos", "Lancamento_Automatico"))
                  
    conn.commit()
    conn.close()

# --- FUNÇÕES DE SALÁRIO MANUAL ---
def _id_salario_manual(mes_ano):
    return f"SALARIO_MANUAL_TOP_MOVEIS_{mes_ano.replace('/', '')}"


def carregar_salario_manual(mes_ano):
    if mes_ano == "Todos":
        return 0.0
    conn = init_db()
    c = conn.cursor()
    c.execute("SELECT COALESCE(valor_ajustado, valor) FROM transacoes WHERE identificador = ?", (_id_salario_manual(mes_ano),))
    row = c.fetchone()
    conn.close()
    return float(row[0]) if row and row[0] is not None else 0.0


def salvar_salario_manual(mes_ano, valor):
    conn = init_db()
    c = conn.cursor()
    id_salario = _id_salario_manual(mes_ano)
    
    # Se salvar R$ 0,00, remove o salário manual do mês para evitar duplicidade.
    if valor <= 0:
        c.execute("DELETE FROM transacoes WHERE identificador = ?", (id_salario,))
    else:
        data_lancamento = f"01/{mes_ano}"
        descricao = "TOP MOVEIS - Salário Manual (PicPay/Outro App)"
        c.execute("""
            INSERT OR REPLACE INTO transacoes 
            (identificador, tipo_conta, data, valor, valor_ajustado, descricao, categoria, arquivo_origem)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (id_salario, "PF (Pessoal)", data_lancamento, float(valor), float(valor), descricao, "Salário", "Lancamento_Manual"))
    
    conn.commit()
    conn.close()

def salvar_extrato(df, tipo_conta, nome_arquivo):
    conn = init_db()
    c = conn.cursor()
    novos_registros = 0
    
    c.execute("SELECT 1 FROM extratos WHERE nome_arquivo = ?", (nome_arquivo,))
    if not c.fetchone():
        hoje = datetime.now().strftime("%d/%m/%Y %H:%M")
        c.execute("INSERT INTO extratos (nome_arquivo, tipo_conta, data_upload) VALUES (?, ?, ?)", (nome_arquivo, tipo_conta, hoje))
    
    for _, row in df.iterrows():
        id_transacao = str(row['Identificador'])
        c.execute("SELECT 1 FROM transacoes WHERE identificador = ?", (id_transacao,))
        if not c.fetchone():
            desc = str(row['Descrição'])
            val_float = float(row['Valor'])
            cat = "Não Classificado"
            
            # --- AUTO-CLASSIFICAÇÃO AVANÇADA ---
            if re.search(r'Digital Ocean|ZAPI', desc, re.IGNORECASE):
                cat = "Gastos Fixos"
            elif re.search(r'Compra de FII', desc, re.IGNORECASE):
                cat = "Investimentos"
            elif re.search(r'BARBARA EMANUELLE DOS SANTOS OLIVEIRA', desc, re.IGNORECASE) and val_float < 0:
                if val_float == -50.00:
                    cat = "Pagamentos"
                else:
                    cat = "🗑️ Excluído (Ignorar)"
            elif re.search(r'Compra no débito via NuPay - iFood', desc, re.IGNORECASE):
                cat = "Eu e Bárbara"
            elif re.search(r'Pagamento de fatura', desc, re.IGNORECASE):
                cat = "Pagamentos"
            elif re.search(r'Compra no débito', desc, re.IGNORECASE):
                cat = "Compras"
            elif re.search(r'Transferência enviada pelo Pix', desc, re.IGNORECASE):
                cat = "Compras"
                
            c.execute("""
                INSERT INTO transacoes 
                (identificador, tipo_conta, data, valor, valor_ajustado, descricao, categoria, arquivo_origem)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (id_transacao, tipo_conta, str(row['Data']), val_float, val_float, desc, cat, nome_arquivo))
            novos_registros += 1
            
    conn.commit()
    conn.close()
    return novos_registros

def carregar_dados():
    conn = init_db()
    df = pd.read_sql_query("SELECT * FROM transacoes", conn)
    conn.close()
    return df

def carregar_lista_extratos():
    conn = init_db()
    df = pd.read_sql_query("SELECT * FROM extratos", conn)
    conn.close()
    return df

def carregar_notas():
    conn = init_db()
    df = pd.read_sql_query("SELECT * FROM notas_fiscais", conn)
    conn.close()
    return df

def salvar_nota_db(mes_ano, tipo, origem, valor, nome_arquivo):
    conn = init_db()
    c = conn.cursor()
    c.execute("INSERT INTO notas_fiscais (mes_ano, tipo, origem, valor, nome_arquivo) VALUES (?, ?, ?, ?, ?)",
              (mes_ano, tipo, origem, valor, nome_arquivo))
    conn.commit()
    conn.close()

def excluir_nota_db(id_nota, nome_arquivo):
    conn = init_db()
    c = conn.cursor()
    c.execute("DELETE FROM notas_fiscais WHERE id = ?", (id_nota,))
    conn.commit()
    conn.close()
    
    # O PDF é opcional. Quando for lançamento manual, nome_arquivo vem vazio.
    # Então só tenta apagar se existir um arquivo real dentro da pasta cofre_notas.
    nome_arquivo = str(nome_arquivo or "").strip()
    if nome_arquivo:
        caminho_arquivo = os.path.join(PASTA_NOTAS, nome_arquivo)
        if os.path.isfile(caminho_arquivo):
            os.remove(caminho_arquivo)

def excluir_extrato_db(nome_arquivo):
    conn = init_db()
    c = conn.cursor()
    c.execute("DELETE FROM extratos WHERE nome_arquivo = ?", (nome_arquivo,))
    c.execute("DELETE FROM transacoes WHERE arquivo_origem = ?", (nome_arquivo,))
    conn.commit()
    conn.close()
    
    caminho_arquivo = os.path.join(PASTA_EXTRATOS, nome_arquivo)
    if os.path.exists(caminho_arquivo):
        os.remove(caminho_arquivo)

def atualizar_transacao_db(identificador, nova_categoria, valor_ajustado):
    conn = init_db()
    c = conn.cursor()
    c.execute(
        "UPDATE transacoes SET categoria = ?, valor_ajustado = ? WHERE identificador = ?",
        (nova_categoria, float(valor_ajustado), identificador)
    )
    conn.commit()
    conn.close()

# Mantém compatibilidade caso alguma parte antiga chame só categoria.
def atualizar_categoria_db(identificador, nova_categoria):
    conn = init_db()
    c = conn.cursor()
    c.execute("UPDATE transacoes SET categoria = ? WHERE identificador = ?", (nova_categoria, identificador))
    conn.commit()
    conn.close()

def limpar_banco_de_dados():
    conn = init_db()
    c = conn.cursor()
    c.execute("DELETE FROM transacoes")
    c.execute("DELETE FROM extratos")
    c.execute("DELETE FROM faturas_cartao")
    conn.commit()
    conn.close()
    
    for f in os.listdir(PASTA_EXTRATOS):
        os.remove(os.path.join(PASTA_EXTRATOS, f))

# --- FILTROS INTELIGENTES ---
def aplicar_filtros_automaticos(df, palavra_salario, nome_usuario):
    df_proc = df.copy()
    
    # 1. Transferência Interna
    if nome_usuario:
        mask_transferencia = (df_proc['valor'] < 0) & (df_proc['descricao'].str.contains(nome_usuario, case=False, na=False)) & (df_proc['categoria'] == "Não Classificado")
        df_proc.loc[mask_transferencia, 'categoria'] = "🔄 Transferência Interna"
        
    # 2. Investimentos (FIIs)
    mask_fii = (df_proc['descricao'].str.contains("Compra de FII", case=False, na=False)) & (df_proc['categoria'] == "Não Classificado")
    df_proc.loc[mask_fii, 'categoria'] = "Investimentos"

    # 3. Regra Especial Bárbara
    mask_barbara_50 = (df_proc['descricao'].str.contains("BARBARA EMANUELLE DOS SANTOS OLIVEIRA", case=False, na=False)) & (df_proc['valor'] == -50.00) & (df_proc['categoria'] == "Não Classificado")
    df_proc.loc[mask_barbara_50, 'categoria'] = "Pagamentos"
    
    mask_barbara_outros = (df_proc['descricao'].str.contains("BARBARA EMANUELLE DOS SANTOS OLIVEIRA", case=False, na=False)) & (df_proc['valor'] < 0) & (df_proc['valor'] != -50.00) & (df_proc['categoria'] == "Não Classificado")
    df_proc.loc[mask_barbara_outros, 'categoria'] = "🗑️ Excluído (Ignorar)"

    # 4. iFood
    mask_ifood = (df_proc['descricao'].str.contains("Compra no débito via NuPay - iFood", case=False, na=False)) & (df_proc['categoria'] == "Não Classificado")
    df_proc.loc[mask_ifood, 'categoria'] = "Eu e Bárbara"

    # 5. Pagamento de Fatura
    mask_fatura = (df_proc['descricao'].str.contains("Pagamento de fatura", case=False, na=False)) & (df_proc['categoria'] == "Não Classificado")
    df_proc.loc[mask_fatura, 'categoria'] = "Pagamentos"

    # 6. Compras no Débito genéricas
    mask_compras = (df_proc['descricao'].str.contains("Compra no débito", case=False, na=False)) & (df_proc['categoria'] == "Não Classificado")
    df_proc.loc[mask_compras, 'categoria'] = "Compras"

    # 7. Pix Genérico
    mask_pix = (df_proc['descricao'].str.contains("Transferência enviada pelo Pix", case=False, na=False)) & (df_proc['categoria'] == "Não Classificado")
    df_proc.loc[mask_pix, 'categoria'] = "Compras"

    # 8. Anulado/Reembolso
    is_salario = df_proc['descricao'].str.contains(palavra_salario, case=False, na=False)
    idx_entradas = df_proc[(df_proc['valor'] > 0) & (~is_salario)].index.tolist()
    idx_saidas = df_proc[df_proc['valor'] < 0].index.tolist()
    
    for idx_in in idx_entradas:
        val = df_proc.loc[idx_in, 'valor']
        matches = [i for i in idx_saidas if df_proc.loc[i, 'valor'] == -val]
        if matches:
            idx_out = matches[0]
            df_proc.loc[idx_in, 'categoria'] = "🔁 Anulado/Reembolso"
            df_proc.loc[idx_out, 'categoria'] = "🔁 Anulado/Reembolso"
            idx_saidas.remove(idx_out)
            
    return df_proc

def processar_datas(df):
    if not df.empty:
        df['data_dt'] = pd.to_datetime(df['data'], format='%d/%m/%Y', errors='coerce')
        df['mes_ano'] = df['data_dt'].dt.strftime('%m/%Y').fillna('Desconhecido')
    return df

# --- INTERFACE WEB ---
st.set_page_config(page_title="Painel Financeiro", layout="wide", page_icon="📊")

df_banco = carregar_dados()
df_mestre = pd.DataFrame()

meses_fixos = [f"{str(m).zfill(2)}/{ANO_ATUAL}" for m in range(1, 13)]
meses_disponiveis = meses_fixos + ["Todos"]

with st.sidebar:
    # --- ÁREA DE BACKUP ---
    st.header("💾 Backup e Segurança")
    if os.path.exists("slet_financas.db"):
        with open("slet_financas.db", "rb") as f:
            st.download_button(label="📥 Exportar Backup (.db)", data=f, file_name="backup_financas.db", mime="application/octet-stream")
            
    st.markdown("*Para restaurar, faça upload do arquivo abaixo:*")
    arquivo_db = st.file_uploader("📤 Restaurar Backup", type=["db"], label_visibility="collapsed")
    if arquivo_db:
        if st.button("🔄 Confirmar Restauração"):
            with open("slet_financas.db", "wb") as f:
                f.write(arquivo_db.getbuffer())
            st.success("Backup restaurado! Atualize a página.")
            st.rerun()
            
    st.markdown("---")

    st.header("⚙️ Configurações")
    palavra_salario_input = st.text_input("Qual texto indica seu Salário?", "TOP MOVEIS")
    nome_usuario_input = st.text_input("Seu Nome (Ignorar Pix p/ você)", "William Brayon")
    
    if not df_banco.empty:
        df_banco = normalizar_valor_ajustado(df_banco)
        df_banco = processar_datas(df_banco)
        df_mestre = aplicar_filtros_automaticos(df_banco, palavra_salario_input, nome_usuario_input)
        
    st.markdown("---")
    st.header("📅 Filtro de Tempo")
    
    idx_padrao = meses_disponiveis.index(st.session_state.mes_salvo) if st.session_state.mes_salvo in meses_disponiveis else meses_disponiveis.index(MES_ATUAL)
    mes_selecionado = st.selectbox("Escolha o Mês para visualizar no Painel:", meses_disponiveis, index=idx_padrao)
    st.session_state.mes_salvo = mes_selecionado
    
    # --- ÁREA DE SALÁRIO MANUAL ---
    st.markdown("---")
    st.header("💵 Salário Manual")
    st.markdown("*Use quando o salário da Top Móveis cair no PicPay/outro app e não aparecer no extrato do Nubank.*")
    
    if mes_selecionado != "Todos":
        salario_manual_atual = carregar_salario_manual(mes_selecionado)
        salario_manual_input = st.number_input(
            "Valor do salário Top Móveis (R$)",
            min_value=0.0,
            value=salario_manual_atual,
            format="%.2f",
            step=50.0,
            help="Salve R$ 0,00 para remover o lançamento manual deste mês. Não use se o salário já estiver no extrato, para não duplicar."
        )
        if st.button("➕ Salvar Salário Manual"):
            salvar_salario_manual(mes_selecionado, salario_manual_input)
            st.success("Salário manual atualizado!")
            st.rerun()
    else:
        st.info("Selecione um mês específico para lançar salário manual.")
    
    # --- ÁREA DE FATURAS DO CARTÃO ---
    st.markdown("---")
    st.header("💳 Faturas de Cartão")
    st.markdown(f"*Lançamento manual para noção ({mes_selecionado})*")
    
    fat_pf_atual, fat_pj_atual = carregar_faturas(mes_selecionado)
    
    if mes_selecionado != "Todos":
        fat_pj_input = st.number_input("Fatura PJ (R$)", min_value=0.0, value=fat_pj_atual, format="%.2f", step=10.0)
        fat_pf_input = st.number_input("Fatura PF (R$)", min_value=0.0, value=fat_pf_atual, format="%.2f", step=10.0)
        
        if st.button("💾 Salvar Faturas"):
            salvar_faturas(mes_selecionado, fat_pf_input, fat_pj_input)
            st.success("Faturas atualizadas!")
            st.rerun()
    else:
        st.info("As faturas exibidas são a soma do ano.")
    
    # --- ÁREA DE LANÇAMENTO DE GASTOS FIXOS ---
    st.markdown("---")
    st.header("📌 Gastos Fixos")
    st.markdown("*Lançamento Rápido no Cartão PJ*")
    
    if mes_selecionado != "Todos":
        if st.button(f"⚡ Lançar D.Ocean e ZAPI em {mes_selecionado}"):
            lancar_gastos_fixos_manuais(mes_selecionado)
            st.success("Gastos fixos registrados com sucesso!")
            st.rerun()
    else:
        st.info("Selecione um mês específico acima para lançar.")
    
    st.markdown("---")
    
    st.header("💰 BALANÇO GERAL")
    st.markdown("*Acumulado de Todos os Meses*")
    
    if not df_mestre.empty:
        df_todas_notas_global = carregar_notas()
        df_notas_lucro_global = pd.DataFrame()
        soma_notas_global = 0.0
        
        if not df_todas_notas_global.empty:
            df_notas_lucro_global = filtrar_documentos_de_ganho(df_todas_notas_global)
            soma_notas_global = df_notas_lucro_global['valor'].sum()
            
            if not df_notas_lucro_global.empty:
                st.markdown("**Ganhos por Plataforma:**")
                agrupado_plataformas = agrupar_ganhos_por_plataforma(df_notas_lucro_global)
                for _, row_plataforma in agrupado_plataformas.iterrows():
                    st.write(f"• {row_plataforma['origem']}: {formatar_brl(row_plataforma['valor'])}")
        
        df_pf_global = df_mestre[df_mestre['tipo_conta'] == "PF (Pessoal)"]
        salario_global = df_pf_global[(df_pf_global['valor_ajustado'] > 0) & (df_pf_global['descricao'].str.contains(palavra_salario_input, case=False, na=False))]['valor_ajustado'].sum()
        
        st.markdown("**Salário Acumulado:**")
        st.write(f"• CLT ({palavra_salario_input}): {formatar_brl(salario_global)}")
        
        ganho_total_global = soma_notas_global + salario_global
        
        df_pj_global = df_mestre[df_mestre['tipo_conta'] == "PJ (Empresa)"]
        
        categorias_ignorar_soma = ["Não Classificado", "🔁 Anulado/Reembolso", "🔄 Transferência Interna", "🗑️ Excluído (Ignorar)"]
        
        saidas_pj_global = df_pj_global[(df_pj_global['valor_ajustado'] < 0) & (~df_pj_global['categoria'].isin(categorias_ignorar_soma))]['valor_ajustado'].sum()
        saidas_pf_global = df_pf_global[(df_pf_global['valor_ajustado'] < 0) & (~df_pf_global['categoria'].isin(categorias_ignorar_soma))]['valor_ajustado'].sum()
        
        gasto_total_global = abs(saidas_pj_global) + abs(saidas_pf_global)
        liquido_global = ganho_total_global - gasto_total_global
        cor_liq = "#00cc66" if liquido_global >= 0 else "#ff4d4d"
        
        st.markdown(f"""
        <div style="background-color: #2b2b2b; padding: 10px; border-radius: 5px; margin-top: 10px;">
            <p style="margin:0; font-size: 14px; color: #aaa;">Total Ganho: {formatar_brl(ganho_total_global)}</p>
            <p style="margin:0; font-size: 14px; color: #aaa;">Total Gasto: {formatar_brl(gasto_total_global)}</p>
            <hr style="margin: 5px 0;">
            <p style="margin:0; font-size: 18px; font-weight: bold; color: {cor_liq};">Líquido: {formatar_brl(liquido_global)}</p>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.write("Sem dados para calcular.")
        
    st.markdown("---")
    
    # --- ÁREA DE PERIGO (COM 3 ALERTAS) ---
    st.markdown("**🚨 ZONA DE PERIGO**")
    if st.session_state.delete_step == 0:
        if st.button("🗑️ Limpar Todos os Extratos"):
            st.session_state.delete_step = 1
            st.rerun()
    elif st.session_state.delete_step == 1:
        st.warning("⚠️ ALERTA 1: Tem certeza? Isso vai apagar todo o banco de dados!")
        if st.button("Sim, quero apagar"):
            st.session_state.delete_step = 2
            st.rerun()
        if st.button("Cancelar"):
            st.session_state.delete_step = 0
            st.rerun()
    elif st.session_state.delete_step == 2:
        st.error("🔴 ALERTA 2: ÚLTIMO AVISO! Não tem volta. Apagar tudo?")
        if st.button("💥 DESTRUIR DADOS AGORA"):
            limpar_banco_de_dados()
            st.session_state.delete_step = 0
            st.success("Extratos apagados com sucesso! Atualize a página.")
            st.rerun()
        if st.button("Ufa, Mudei de Ideia"):
            st.session_state.delete_step = 0
            st.rerun()

st.title("📊 Painel Automático de Finanças")
st.caption("✅ Versão corrigida: nomes avulsos somam na mesma plataforma do BALANÇO GERAL")
st.caption("✅ Versão corrigida: PDF opcional no cofre + comprovante genérico soma como ganho")

aba_dash, aba_historico, aba_upload, aba_classificar, aba_notas = st.tabs([
    "📈 Dashboard do Mês", "📅 Histórico Anual", "📂 Importar Extratos", "👆 Classificar Gastos", "🧾 Cofre de Notas"
])

df_mes_atual = df_mestre.copy()
if mes_selecionado != "Todos" and not df_mestre.empty:
    df_mes_atual = df_mestre[df_mestre['mes_ano'] == mes_selecionado]

# === ABA 1: UPLOAD DE EXTRATOS ===
with aba_upload:
    st.subheader("📥 Importar e Gerenciar Extratos (CSV)")
    
    if "extrato_key" not in st.session_state: st.session_state.extrato_key = 0
    
    col_up, col_list = st.columns([1, 1.5])
    
    with col_up:
        st.markdown("**Novo Extrato**")
        tipo_conta = st.radio("De qual conta é este extrato?", ["PJ (Empresa)", "PF (Pessoal)"])
        arquivo = st.file_uploader("Arraste o arquivo CSV aqui", type=["csv"], key=f"ext_{st.session_state.extrato_key}")
        
        if st.button("💾 Processar e Salvar"):
            if arquivo:
                df_import = pd.read_csv(arquivo)
                if 'Identificador' in df_import.columns:
                    caminho_salvar = os.path.join(PASTA_EXTRATOS, arquivo.name)
                    with open(caminho_salvar, "wb") as f:
                        f.write(arquivo.getbuffer())
                        
                    novas = salvar_extrato(df_import, tipo_conta, arquivo.name)
                    st.session_state.extrato_key += 1
                    
                    if novas > 0:
                        st.success(f"✅ Sucesso! {novas} novas transações adicionadas.")
                    else:
                        st.info("ℹ️ Arquivo guardado, mas todas as transações já existiam no sistema.")
                    st.rerun()
                else:
                    st.error("Erro: O arquivo precisa ser o CSV original do Nubank.")
            else:
                st.warning("Anexe o arquivo primeiro.")
                
    with col_list:
        st.markdown("**Extratos Guardados no Cofre**")
        df_extratos = carregar_lista_extratos()
        
        if not df_extratos.empty:
            st.dataframe(df_extratos[['nome_arquivo', 'tipo_conta', 'data_upload']], 
                         column_config={"nome_arquivo": "Arquivo CSV", "tipo_conta": "Conta", "data_upload": "Data de Upload"},
                         hide_index=True, use_container_width=True)
            
            st.markdown("**Gerenciar:**")
            for _, row in df_extratos.iterrows():
                c1, c2 = st.columns([4, 1])
                with c1:
                    caminho_arquivo = os.path.join(PASTA_EXTRATOS, row['nome_arquivo'])
                    if os.path.exists(caminho_arquivo):
                        with open(caminho_arquivo, "rb") as file:
                            st.download_button(label=f"📥 Baixar {row['nome_arquivo']}", data=file, file_name=row['nome_arquivo'], mime="text/csv", key=f"dl_ext_{row['id']}")
                    else:
                        st.write(f"⚠️ {row['nome_arquivo']} (Físico não encontrado)")
                        
                with c2:
                    if st.button("🗑️ Excluir", key=f"del_ext_{row['id']}"):
                        excluir_extrato_db(row['nome_arquivo'])
                        st.rerun()
        else:
            st.info("Nenhum extrato importado ainda.")

# === ABA 4: CLASSIFICAR GASTOS ===
with aba_classificar:
    st.subheader(f"Classificação Manual ({mes_selecionado})")
    
    col_info, col_lixeira = st.columns([3, 1])
    with col_info:
        st.markdown("💡 *Mude a categoria para **'🗑️ Excluído (Ignorar)'** para apagar faturas e sumir com elas da matemática.*")
    with col_lixeira:
        mostrar_apagados = st.checkbox("👁️ Mostrar itens apagados")
    
    if not df_mes_atual.empty:
        if mostrar_apagados:
            categorias_ignorar = ["🔁 Anulado/Reembolso", "🔄 Transferência Interna"]
        else:
            categorias_ignorar = ["🔁 Anulado/Reembolso", "🔄 Transferência Interna", "🗑️ Excluído (Ignorar)"]
            
        df_gastos_gerais = df_mes_atual[(df_mes_atual['valor'] < 0) & 
                                 (~df_mes_atual['categoria'].isin(categorias_ignorar))].copy()
        df_gastos_gerais = normalizar_valor_ajustado(df_gastos_gerais)
        
        colunas_mostrar = ['identificador', 'tipo_conta', 'data', 'descricao', 'valor', 'valor_ajustado', 'categoria']
        df_tela = df_gastos_gerais[colunas_mostrar].copy()
        
        opcoes_categorias = ["Não Classificado", "Eu", "Grupos", "Eu e Bárbara", "Pagamentos", "Gastos Fixos", "Impostos", "Compras", "Investimentos", "🗑️ Excluído (Ignorar)"]
        
        df_editado = st.data_editor(
            df_tela,
            column_config={
                "categoria": st.column_config.SelectboxColumn("Categoria", options=opcoes_categorias, required=True),
                "tipo_conta": "Conta Origem",
                "identificador": None,
                "valor": st.column_config.NumberColumn("Valor Original", format="%.2f", help="Valor vindo do extrato, não editável."),
                "valor_ajustado": st.column_config.NumberColumn("Valor Considerado", format="%.2f", help="Edite aqui o valor que deve entrar na conta. Ex.: gasto de -100,00 rachado pela metade = -50,00.")
            },
            disabled=["tipo_conta", "data", "descricao", "valor"], hide_index=True, use_container_width=True
        )
        
        if st.button("💾 Salvar Classificações e Valores"):
            alteracoes = 0
            for index, row in df_editado.iterrows():
                id_transacao = row['identificador']
                nova_cat = row['categoria']
                novo_valor_ajustado = float(row['valor_ajustado'])
                linha_antiga = df_tela.loc[df_tela['identificador'] == id_transacao].iloc[0]
                cat_antiga = linha_antiga['categoria']
                valor_antigo = float(linha_antiga['valor_ajustado'])
                
                if nova_cat != cat_antiga or abs(novo_valor_ajustado - valor_antigo) > 0.0001:
                    atualizar_transacao_db(id_transacao, nova_cat, novo_valor_ajustado)
                    alteracoes += 1
                    
            if alteracoes > 0:
                st.success(f"✅ Sucesso! {alteracoes} alterações salvas.")
                st.rerun()
            else:
                st.info("Nenhuma alteração para salvar.")
    else:
        st.warning("Nenhum dado bancário para este mês.")

# === ABA 5: COFRE DE NOTAS FISCAIS COM OCR ===
with aba_notas:
    st.subheader("🧾 Cofre de Notas Fiscais e Impostos")
    
    if "nota_atual" not in st.session_state: st.session_state.nota_atual = None
    if "tipo_auto" not in st.session_state: st.session_state.tipo_auto = "Nota Fiscal de Lucro"
    if "origem_auto" not in st.session_state: st.session_state.origem_auto = ""
    if "valor_auto" not in st.session_state: st.session_state.valor_auto = 0.0
    if "uploader_key" not in st.session_state: st.session_state.uploader_key = 0 

    col_form, col_lista = st.columns([1, 1.5])
    
    with col_form:
        st.markdown("**Guardar Novo Documento**")
        nota_arquivo = st.file_uploader("Upload da Nota (PDF)", type=["pdf"], key=f"uploader_{st.session_state.uploader_key}")
        
        if nota_arquivo is not None:
            if st.session_state.nota_atual != nota_arquivo.name:
                st.session_state.nota_atual = nota_arquivo.name
                try:
                    reader = PyPDF2.PdfReader(nota_arquivo)
                    texto = ""
                    for page in reader.pages:
                        texto += page.extract_text() + "\n"
                    
                    tipo_doc = "Nota Fiscal de Lucro"
                    nome_empresa = ""
                    valor_final = 0.0
                    
                    if re.search(r'Documento de Arrecadação do Simples Nacional', texto, re.IGNORECASE):
                        tipo_doc = "Guia de Imposto (DAS/DARF)"
                        nome_empresa = "Simples Nacional"
                        match_valor = re.search(r'Valor Total do Documento[\s\S]{0,20}R\$?\s*([\d\.,]+)', texto, re.IGNORECASE)
                        if not match_valor: 
                             match_valor = re.search(r'Valor:\s*([\d\.,]+)', texto, re.IGNORECASE)
                        if match_valor:
                            try:
                                v_str = match_valor.group(1).replace('.', '').replace(',', '.')
                                valor_final = float(v_str)
                            except: pass
                    else:
                        if re.search(r'SHPS|SHOPEE', texto, re.IGNORECASE): nome_empresa = "Shopee (SHPS Tecnologia)"
                        elif re.search(r'MERCADO\s*LIVRE|EBAZAR', texto, re.IGNORECASE): nome_empresa = "Mercado Livre"
                        elif re.search(r'AMAZON', texto, re.IGNORECASE): nome_empresa = "Amazon"
                        elif re.search(r'AWIN', texto, re.IGNORECASE): nome_empresa = "AWIN"
                        elif re.search(r'CSSBUY', texto, re.IGNORECASE): nome_empresa = "CSSBuy"
                        
                        if not nome_empresa:
                            cnpjs = re.findall(r'\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}', texto)
                            if not cnpjs: cnpjs = re.findall(r'CNPJ/CPF/NIF\s*(\d{14})', texto)
                            
                            cnpjs_unicos = list(dict.fromkeys(cnpjs))
                            for c in cnpjs_unicos:
                                if "64651918" not in c.replace(".","").replace("/","").replace("-",""):
                                    nome_empresa = f"CNPJ: {c}"
                                    break
                            
                            if not nome_empresa:
                                match_nome = re.search(r'TOMADOR DO SERVIÇO[\s\S]*?Nome / Nome Empresarial\s*(.*?)\n', texto, re.IGNORECASE)
                                if match_nome: nome_empresa = match_nome.group(1).strip()
                        
                        padroes_valor = [
                            r'Valor Líquido da\s*NFS-?\s*e[\s\S]{0,50}R\$?\s*([\d\.,]+)',
                            r'VALOR TOTAL DA\s*NFS-?\s*E[\s\S]{0,50}R\$?\s*([\d\.,]+)',
                            r'Valor Recebido:\s*R\$?\s*([\d\.,]+)',
                            r'Valor do\s*Serviço[\s\S]{0,50}R\$?\s*([\d\.,]+)',
                            r'R\$\s*([\d\.,]+)'
                        ]
                        for p in padroes_valor:
                            matches = re.findall(p, texto, re.IGNORECASE)
                            if matches:
                                v_str = matches[-1].replace('.', '').replace(',', '.')
                                try:
                                    valor_final = float(v_str)
                                    if valor_final > 0: break
                                except: pass
                    
                    st.session_state.tipo_auto = tipo_doc
                    st.session_state.origem_auto = nome_empresa
                    st.session_state.valor_auto = valor_final
                    nota_arquivo.seek(0)
                except Exception as e:
                    pass
        elif st.session_state.nota_atual is not None:
             st.session_state.nota_atual = None
             st.session_state.tipo_auto = "Nota Fiscal de Lucro"
             st.session_state.origem_auto = ""
             st.session_state.valor_auto = 0.0

        opcoes_tipo = ["Nota Fiscal de Lucro", "Guia de Imposto (DAS/DARF)", "Comprovante Genérico"]
        idx_tipo = opcoes_tipo.index(st.session_state.tipo_auto) if st.session_state.tipo_auto in opcoes_tipo else 0
        
        nota_tipo = st.selectbox("Tipo de Documento", opcoes_tipo, index=idx_tipo)
        nota_origem = st.text_input("Empresa/CNPJ Pagador", value=st.session_state.origem_auto)
        nota_valor = st.number_input("Valor da Nota (R$)", min_value=0.0, format="%.2f", value=st.session_state.valor_auto)
        
        idx_mes_nota = meses_fixos.index(mes_selecionado) if mes_selecionado in meses_fixos else meses_fixos.index(MES_ATUAL)
        nota_mes = st.selectbox("Mês de Referência do Arquivo", meses_fixos, index=idx_mes_nota)
        
        if st.button("☁️ Salvar no Cofre"):
            # PDF opcional: se anexar, salva o arquivo; se não anexar, salva apenas o lançamento manual.
            if nota_origem.strip() and nota_valor > 0:
                nome_arquivo_salvo = ""

                if nota_arquivo is not None:
                    caminho_salvar = os.path.join(PASTA_NOTAS, nota_arquivo.name)
                    with open(caminho_salvar, "wb") as f:
                        f.write(nota_arquivo.getbuffer())
                    nome_arquivo_salvo = nota_arquivo.name
                
                salvar_nota_db(nota_mes, nota_tipo, nota_origem.strip(), nota_valor, nome_arquivo_salvo)

                if nome_arquivo_salvo:
                    st.success("Documento salvo com sucesso no cofre!")
                else:
                    st.success("Lançamento manual salvo com sucesso, sem PDF anexado!")
                
                st.session_state.tipo_auto = "Nota Fiscal de Lucro"
                st.session_state.origem_auto = ""
                st.session_state.valor_auto = 0.0
                st.session_state.nota_atual = None
                st.session_state.uploader_key += 1 
                st.rerun()
            else:
                st.error("Preencha a empresa/pagador e o valor da nota. O PDF é opcional.")
                
    with col_lista:
        st.markdown(f"**Documentos Guardados ({mes_selecionado})**")
        df_notas = carregar_notas()
        if not df_notas.empty:
            if mes_selecionado != "Todos":
                df_notas = df_notas[df_notas['mes_ano'] == mes_selecionado]
            
            if not df_notas.empty:
                df_display_notas = df_notas[['origem', 'tipo', 'valor']].copy()
                df_display_notas['valor'] = df_display_notas['valor'].apply(formatar_brl)
                
                st.dataframe(
                    df_display_notas, 
                    column_config={"origem": "Empresa / Pagador", "tipo": "Tipo de Doc", "valor": "Valor (R$)"},
                    hide_index=True, use_container_width=True
                )
                
                st.markdown("**Gerenciar Documentos:**")
                for _, row in df_notas.iterrows():
                    col_dl, col_del = st.columns([4, 1])
                    with col_dl:
                        nome_arquivo_doc = str(row.get('nome_arquivo') or "").strip()

                        if nome_arquivo_doc:
                            caminho_arquivo = os.path.join(PASTA_NOTAS, nome_arquivo_doc)
                            if os.path.isfile(caminho_arquivo):
                                with open(caminho_arquivo, "rb") as file:
                                    st.download_button(
                                        label=f"📥 {row['origem']} ({nome_arquivo_doc})",
                                        data=file,
                                        file_name=nome_arquivo_doc,
                                        mime="application/octet-stream",
                                        key=f"dl_{row['id']}"
                                    )
                            else:
                                st.write(f"⚠️ {row['origem']} (Arquivo não encontrado na pasta)")
                        else:
                            st.write(f"📝 {row['origem']} (lançamento manual, sem PDF)")
                            
                    with col_del:
                        if st.button("🗑️ Excluir", key=f"del_{row['id']}"):
                            excluir_nota_db(row['id'], row.get('nome_arquivo', ""))
                            st.rerun()
            else:
                st.info("Nenhuma nota salva para este mês.")
        else:
            st.info("O cofre está vazio.")

# === ABA 2: DASHBOARD DO MÊS E PDF ===
with aba_dash:
    st.markdown(f"### Resumo: {mes_selecionado}")
    
    df_todas_notas = carregar_notas()
    df_notas_pj = pd.DataFrame()
    soma_notas_pj = 0.0
    
    if not df_todas_notas.empty and mes_selecionado != "Todos":
        df_notas_pj = filtrar_documentos_de_ganho(df_todas_notas[df_todas_notas['mes_ano'] == mes_selecionado])
        soma_notas_pj = df_notas_pj['valor'].sum()
    elif not df_todas_notas.empty and mes_selecionado == "Todos":
        df_notas_pj = filtrar_documentos_de_ganho(df_todas_notas)
        soma_notas_pj = df_notas_pj['valor'].sum()

    if df_mes_atual.empty and df_notas_pj.empty:
        st.warning("Nenhum dado bancário ou nota fiscal lançada para este mês.")
    else:
        df_pj = pd.DataFrame() if df_mes_atual.empty else df_mes_atual[df_mes_atual['tipo_conta'] == "PJ (Empresa)"]
        df_pf = pd.DataFrame() if df_mes_atual.empty else df_mes_atual[df_mes_atual['tipo_conta'] == "PF (Pessoal)"]
        
        entradas_pf_salario = df_pf[(df_pf['valor_ajustado'] > 0) & (df_pf['descricao'].str.contains(palavra_salario_input, case=False, na=False))]['valor_ajustado'].sum() if not df_pf.empty else 0.0
        
        categorias_ignorar_soma = ["Não Classificado", "🔁 Anulado/Reembolso", "🔄 Transferência Interna", "🗑️ Excluído (Ignorar)"]
        
        # PJ
        saidas_pj_reais = 0.0
        gastos_por_categoria_pj = pd.DataFrame()
        if not df_pj.empty:
            df_pj_gastos = df_pj[(df_pj['valor_ajustado'] < 0) & (~df_pj['categoria'].isin(categorias_ignorar_soma))]
            saidas_pj_reais = df_pj_gastos['valor_ajustado'].sum()
            gastos_por_categoria_pj = df_pj_gastos.groupby('categoria')['valor_ajustado'].sum().reset_index()
            gastos_por_categoria_pj = gastos_por_categoria_pj.rename(columns={'valor_ajustado': 'valor'})
            gastos_por_categoria_pj['valor'] = gastos_por_categoria_pj['valor'].abs()
        
        # PF
        saidas_pf_reais = 0.0
        gastos_por_categoria_pf = pd.DataFrame()
        if not df_pf.empty:
            df_pf_gastos = df_pf[(df_pf['valor_ajustado'] < 0) & (~df_pf['categoria'].isin(categorias_ignorar_soma))]
            saidas_pf_reais = df_pf_gastos['valor_ajustado'].sum()
            gastos_por_categoria_pf = df_pf_gastos.groupby('categoria')['valor_ajustado'].sum().reset_index()
            gastos_por_categoria_pf = gastos_por_categoria_pf.rename(columns={'valor_ajustado': 'valor'})
            gastos_por_categoria_pf['valor'] = gastos_por_categoria_pf['valor'].abs()
        
        investimentos_mes = df_pf[(df_pf['valor_ajustado'] < 0) & (df_pf['categoria'] == "Investimentos")]['valor_ajustado'].sum() if not df_pf.empty else 0.0
        
        total_ganhos = soma_notas_pj + entradas_pf_salario
        total_gastos_reais = abs(saidas_pj_reais) + abs(saidas_pf_reais) 
        lucro_liquido = total_ganhos - total_gastos_reais
        
        cor_resultado = "#00cc66" if lucro_liquido >= 0 else "#ff4d4d" 
        texto_resultado = "LUCRO LÍQUIDO" if lucro_liquido >= 0 else "PREJUÍZO LÍQUIDO"
        
        # --- BOTÃO PARA BAIXAR PDF ---
        pdf_bytes = gerar_relatorio_pdf(
            mes_selecionado, 
            formatar_brl(total_ganhos), 
            formatar_brl(total_gastos_reais), 
            formatar_brl(lucro_liquido), 
            df_notas_pj, 
            gastos_por_categoria_pf,
            gastos_por_categoria_pj,
            formatar_brl(fat_pf_atual),
            formatar_brl(fat_pj_atual)
        )
        st.download_button(label="🖨️ Baixar Relatório Mensal (PDF)", data=pdf_bytes, file_name=f"Relatorio_Financeiro_{mes_selecionado[:2]}_{mes_selecionado[3:]}.pdf", mime="application/pdf")
        
        st.markdown(f"""
        <div style="background-color: #1e1e1e; padding: 20px; border-radius: 10px; text-align: center; border: 2px solid {cor_resultado}; margin-top: 15px;">
            <h2 style="color: white; margin-bottom: 5px;">VEREDICTO DO MÊS ({mes_selecionado})</h2>
            <h4 style="color: #aaaaaa; margin-top: 0px;">Total Ganho: {formatar_brl(total_ganhos)} &nbsp;|&nbsp; Total Gasto: {formatar_brl(total_gastos_reais)}</h4>
            <h1 style="color: {cor_resultado}; font-size: 45px; margin-top: 10px;">{texto_resultado}: {formatar_brl(lucro_liquido)}</h1>
        </div>
        """, unsafe_allow_html=True)
        st.markdown("---")
        
        col_pj, col_pf = st.columns(2)
        with col_pj:
            st.info("🏢 **Conta PJ (Empresa)**")
            st.metric("Total de Gastos (PJ)", formatar_brl(abs(saidas_pj_reais)))
            
            # --- EXIBIÇÃO DA FATURA DO CARTÃO ---
            st.markdown(f"💳 Fatura Cartão (PJ): **{formatar_brl(fat_pj_atual)}** *(Info)*")
            
            st.markdown("👇 **Despesas PJ por Categoria**")
            if not gastos_por_categoria_pj.empty:
                df_pj_disp = gastos_por_categoria_pj.copy()
                df_pj_disp['valor'] = df_pj_disp['valor'].apply(formatar_brl)
                st.dataframe(df_pj_disp, hide_index=True, column_config={"categoria": "Categoria", "valor": "Total Gasto"}, use_container_width=True)
            else:
                st.write("Nenhuma despesa PJ classificada.")
            
            st.markdown("👇 **Origem dos Ganhos (Baseado nas Notas)**")
            if not df_notas_pj.empty:
                df_display_pj = agrupar_ganhos_por_plataforma(df_notas_pj)
                df_display_pj['valor'] = df_display_pj['valor'].apply(formatar_brl)
                st.dataframe(df_display_pj, hide_index=True, column_config={"origem": "Empresa / Plataforma", "valor": "Valor Recebido"}, use_container_width=True)
            else:
                st.write("Nenhuma nota de lucro anexada neste mês.")
            
        with col_pf:
            st.warning("👤 **Conta PF (Pessoal)**")
            st.metric(f"Salário ('{palavra_salario_input}')", formatar_brl(entradas_pf_salario))
            st.metric("Total de Gastos (PF)", formatar_brl(abs(saidas_pf_reais)))
            st.metric("Total Investido", formatar_brl(abs(investimentos_mes)))
            
            # --- EXIBIÇÃO DA FATURA DO CARTÃO ---
            st.markdown(f"💳 Fatura Cartão (PF): **{formatar_brl(fat_pf_atual)}** *(Info)*")
            
            st.markdown("👇 **Despesas PF por Categoria**")
            if not gastos_por_categoria_pf.empty:
                df_gastos_disp = gastos_por_categoria_pf.copy()
                df_gastos_disp['valor'] = df_gastos_disp['valor'].apply(formatar_brl)
                st.dataframe(df_gastos_disp, hide_index=True, column_config={"categoria": "Categoria", "valor": "Total Gasto"}, use_container_width=True)
            else:
                st.write("Nenhuma despesa PF classificada.")

# === ABA 3: HISTÓRICO ANUAL ===
with aba_historico:
    st.subheader("📅 Histórico Completo de Meses")
    dados_historico = []
    
    df_todas_notas_hist = carregar_notas()
    meses_com_dados = []
    if not df_mestre.empty: meses_com_dados.extend(df_mestre['mes_ano'].dropna().unique().tolist())
    if not df_todas_notas_hist.empty: meses_com_dados.extend(df_todas_notas_hist['mes_ano'].dropna().unique().tolist())
    meses_unicos = list(set(meses_com_dados))
    
    if meses_unicos:
        for mes in meses_unicos:
            df_mes = pd.DataFrame() if df_mestre.empty else df_mestre[df_mestre['mes_ano'] == mes]
            df_pj = pd.DataFrame() if df_mes.empty else df_mes[df_mes['tipo_conta'] == "PJ (Empresa)"]
            df_pf = pd.DataFrame() if df_mes.empty else df_mes[df_mes['tipo_conta'] == "PF (Pessoal)"]
            
            soma_notas = 0.0
            if not df_todas_notas_hist.empty:
                notas_mes = filtrar_documentos_de_ganho(df_todas_notas_hist[df_todas_notas_hist['mes_ano'] == mes])
                soma_notas = notas_mes['valor'].sum()
                
            e_pf = df_pf[(df_pf['valor_ajustado'] > 0) & (df_pf['descricao'].str.contains(palavra_salario_input, case=False, na=False))]['valor_ajustado'].sum() if not df_pf.empty else 0.0
            
            categorias_ignorar_soma_hist = ["Não Classificado", "🔁 Anulado/Reembolso", "🔄 Transferência Interna", "🗑️ Excluído (Ignorar)"]
            
            s_pj = 0.0
            if not df_pj.empty:
                df_pj_g = df_pj[(df_pj['valor_ajustado'] < 0) & (~df_pj['categoria'].isin(categorias_ignorar_soma_hist))]
                s_pj = df_pj_g['valor_ajustado'].sum()
            
            s_pf = 0.0
            inv_pf = 0.0
            if not df_pf.empty:
                df_pf_g = df_pf[(df_pf['valor_ajustado'] < 0) & (~df_pf['categoria'].isin(categorias_ignorar_soma_hist))]
                s_pf = df_pf_g['valor_ajustado'].sum()
                inv_pf = abs(df_pf[(df_pf['valor_ajustado'] < 0) & (df_pf['categoria'] == "Investimentos")]['valor_ajustado'].sum())
            
            t_ganhos = soma_notas + e_pf
            t_gastos = abs(s_pj) + abs(s_pf)
            lucro = t_ganhos - t_gastos
            
            dados_historico.append({
                "Mês/Ano": mes,
                "Total Ganhos": formatar_brl(t_ganhos),
                "Total Gastos": formatar_brl(t_gastos),
                "Total Investido": formatar_brl(inv_pf),
                "Saldo Líquido": formatar_brl(lucro)
            })
            
        st.dataframe(pd.DataFrame(dados_historico), use_container_width=True)
    else:
        st.warning("Nenhum dado importado ou nota salva ainda.")
