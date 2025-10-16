import requests
import re
from bs4 import BeautifulSoup
import pandas as pd
from datetime import datetime
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import os
import functions_framework

# Configurações das variáveis de ambiente
EMAIL_REMETENTE = os.environ.get('EMAIL_REMETENTE')
SENHA_APP = os.environ.get('SENHA_APP')
EMAILS_DESTINATARIOS = os.environ.get('EMAILS_DESTINATARIOS', '').split(',')

def verificar_periodo_execucao():
    """Verifica se deve executar baseado no mês atual"""
    mes_atual = datetime.now().month
    ano_atual = datetime.now().year
    
    if mes_atual == 8:  # Agosto - Primeiro Semestre
        return True, 1, 6, f"1_Semestre_{ano_atual}", f"Primeiro Semestre de {ano_atual}"
    elif mes_atual == 12:  # Dezembro - Ano Completo
        return True, 1, 12, f"Ano_Completo_{ano_atual}", f"Ano Completo de {ano_atual}"
    else:
        return False, None, None, None, None

def coletar_dados_vereadores(ano, mes_inicio, mes_fim):
    """Coleta dados dos vereadores do site oficial"""
    print(f"📊 Coletando dados de {mes_inicio:02d}/{ano} até {mes_fim:02d}/{ano}...")
    dados = []
    
    for vmes in range(mes_inicio, mes_fim + 1):
        mes = f"{vmes:02d}"
        anomes = str(ano) + mes
        url = f"https://sisgvarmazenamento.blob.core.windows.net/prd/PublicacaoPortal/Arquivos/{anomes}.htm"
        
        print(f"  → Processando {mes}/{ano}...")
        
        try:
            response = requests.get(url, timeout=30)
            soup = BeautifulSoup(response.content, 'html5lib')
            
            # Função para remover tags HTML
            TAG_RE = re.compile('<.*?>|&([a-z0-9]+|#[0-9]{1,6}|#x[0-9a-f]{1,6});')
            remove_tags = lambda text: TAG_RE.sub('', text)
            
            # Variáveis de controle
            nomeVereador = ""
            categoriaDespesa = ""
            cnpj = ""
            LugarDespesa = ""
            start = 0
            skip = 0
            ignore = 0
            bugduplo = 1
            
            name_list = soup.find('body')
            if not name_list:
                print(f"  ⚠️  Nenhum conteúdo encontrado para {mes}/{ano}")
                continue
            
            for tr in name_list.find_all('tr'):
                if bugduplo == 0:
                    bugduplo = -1
                    continue
                
                # Detecta início de novo vereador
                if tr.find(text=re.compile(r"[\s\S]+(Vereador)\((a)\)[:\s]", re.I)):
                    start = 1
                    for name in tr.find_all('td'):
                        if name.find(text=re.compile(r"[\s\S]+(Vereador)\((a)\)", re.I)):
                            names = str(name.contents[1]) if len(name.contents) > 1 else ""
                            names = re.sub(r"[\s\S]+(Vereador)\((a)\)[:\s]", "", names)
                            nomeVereador = remove_tags(names)
                            ignore = 0
                            if bugduplo == 1:
                                bugduplo = 0
                
                if start != 0:
                    for name in tr.find_all('td'):
                        if len(name.contents) == 0:
                            continue
                        
                        names = str(name.contents[0])
                        
                        if skip == 1:
                            skip = 0
                            continue
                        if ignore == 1:
                            continue
                        
                        names = remove_tags(names)
                        names = re.sub("(Natureza da despesa|Valor utilizado|VALORES GASTOS|VALORES DISPONIBILIZADOS)", "", names)
                        names = re.sub("(TOTAL DO ITEM)", "VXASkip", names)
                        names = re.sub("(TOTAL DO MÊS|VEREADOR AFASTADO)", "VXBSkip", names)
                        
                        if re.match(r"\d{2}.?\d{3}.?\d{3}/?\d{4}-?\d{2}", names):
                            start = 2
                        if re.match(r"[\s\S]*(VXASkip)", names):
                            start = 1
                            skip = 1
                            continue
                        if re.match(r"[\s\S]*(VXBSkip)", names):
                            start = 0
                            ignore = 1
                            break
                        if re.match(r'^\s*$', names):
                            continue
                        
                        if start == 1:
                            categoriaDespesa = names
                            start = 2
                        elif start == 2:
                            cnpj = names
                            start = 3
                        elif start == 3:
                            LugarDespesa = names
                            start = 4
                        elif start >= 4:
                            dados.append({
                                'Vereador': nomeVereador,
                                'Tipo_de_Gasto': categoriaDespesa,
                                'Nome_Da_Empresa': LugarDespesa,
                                'CNPJ': cnpj,
                                'Valor': names,
                                'Mes/Ano': f"{mes}/{ano}"
                            })
                            start = 2
                        
                        if start > 0 and start < 4:
                            start += 1
        
        except Exception as e:
            print(f"   Erro ao processar {mes}/{ano}: {str(e)}")
            continue
    
    print(f" Total coletado: {len(dados)} registros")
    return dados

def criar_excel(dados, nome_arquivo):
    """Cria arquivo Excel com os dados coletados"""
    if not dados:
        raise Exception("Nenhum dado para gerar Excel")
    
    df = pd.DataFrame(dados)
    df = df.sort_values(by='Vereador')
    
    # Google Cloud Functions usa /tmp como diretório temporário
    temp_file = f"/tmp/{nome_arquivo}.xlsx"
    df.to_excel(temp_file, index=False, engine='openpyxl')
    
    print(f" Excel gerado: {len(df)} registros")
    return temp_file

def enviar_email(arquivo, periodo_descricao):
    """Envia email com o arquivo Excel anexado"""
    if not EMAILS_DESTINATARIOS or not EMAILS_DESTINATARIOS[0]:
        raise Exception("Emails destinatários não configurados")
    
    if not SENHA_APP:
        raise Exception("Senha de aplicativo não configurada")
    
    print(f" Enviando email para {len(EMAILS_DESTINATARIOS)} destinatário(s)...")
    
    # Montar mensagem
    msg = MIMEMultipart()
    msg['From'] = EMAIL_REMETENTE
    msg['To'] = ", ".join(EMAILS_DESTINATARIOS)
    msg['Subject'] = f"Gastos de Vereadores - {periodo_descricao}"
    
    corpo = f"""
    <html>
    <body style="font-family: Arial, sans-serif;">
        <h2 style="color: #2c3e50;"> Relatório de Gastos dos Vereadores</h2>
        <p>Segue em anexo o relatório de gastos dos vereadores referente ao período:</p>
        <p style="font-size: 18px; font-weight: bold; color: #3498db;">{periodo_descricao}</p>
        <hr style="border: 1px solid #ecf0f1;">
        <p style="font-size: 12px; color: #7f8c8d;">
            <strong>Data de geração:</strong> {datetime.now().strftime('%d/%m/%Y às %H:%M')}<br>
            <strong>Sistema:</strong> Google Cloud Functions + GitHub<br>
            <em>Este email foi gerado automaticamente.</em>
        </p>
    </body>
    </html>
    """
    
    msg.attach(MIMEText(corpo, 'html'))
    
    # Anexar arquivo Excel
    with open(arquivo, 'rb') as anexo:
        parte = MIMEBase('application', 'vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        parte.set_payload(anexo.read())
        encoders.encode_base64(parte)
        parte.add_header(
            'Content-Disposition',
            f'attachment; filename={os.path.basename(arquivo)}'
        )
        msg.attach(parte)
    
    # Enviar via SMTP
    try:
        servidor = smtplib.SMTP('smtp.gmail.com', 587)
        servidor.starttls()
        servidor.login(EMAIL_REMETENTE, SENHA_APP)
        servidor.send_message(msg)
        servidor.quit()
        
        print(f" Email enviado com sucesso!")
        for email in EMAILS_DESTINATARIOS:
            print(f"   {email}")
        
        return True
    except Exception as e:
        print(f" Erro ao enviar email: {str(e)}")
        raise

@functions_framework.http
def processar_gastos(request):
    """
    Função principal do Cloud Functions
    
    Esta função é chamada pelo Cloud Scheduler em Agosto e Dezembro
    para coletar dados dos vereadores e enviar por email.
    """
    print("=" * 60)
    print("🚀 INICIANDO COLETA DE GASTOS DOS VEREADORES")
    print("=" * 60)
    
    try:
        # Verificar se é período de execução
        deve_executar, mes_inicio, mes_fim, nome_arquivo, periodo_descricao = verificar_periodo_execucao()
        
        if not deve_executar:
            mes_atual = datetime.now().strftime('%B')
            mensagem = f"Script não deve executar em {mes_atual}. Aguardando Agosto ou Dezembro."
            print(f"  {mensagem}")
            return {
                'status': 'skipped',
                'message': mensagem,
                'mes_atual': mes_atual
            }, 200
        
        ano_atual = datetime.now().year
        print(f"✓ Período válido: {periodo_descricao}")
        print(f"  Coletando dados de {mes_inicio:02d}/{ano_atual} até {mes_fim:02d}/{ano_atual}")
        
        # Validar configurações
        if not EMAIL_REMETENTE or not SENHA_APP:
            raise Exception("Variáveis de ambiente EMAIL_REMETENTE ou SENHA_APP não configuradas")
        
        # Coletar dados
        dados = coletar_dados_vereadores(ano_atual, mes_inicio, mes_fim)
        
        if not dados:
            raise Exception("Nenhum dado foi coletado")
        
        # Criar Excel
        arquivo_excel = criar_excel(dados, f"Gastos_Vereadores_{nome_arquivo}")
        
        # Enviar por email
        enviar_email(arquivo_excel, periodo_descricao)
        
        # Limpar arquivo temporário
        if os.path.exists(arquivo_excel):
            os.remove(arquivo_excel)
        
        resultado = {
            'status': 'success',
            'periodo': periodo_descricao,
            'registros_coletados': len(dados),
            'destinatarios': len(EMAILS_DESTINATARIOS),
            'data_execucao': datetime.now().isoformat()
        }
        
        print("\n" + "=" * 60)
        print(" PROCESSO CONCLUÍDO COM SUCESSO!")
        print("=" * 60)
        print(f"Período: {periodo_descricao}")
        print(f"Registros: {len(dados)}")
        print(f"Emails enviados: {len(EMAILS_DESTINATARIOS)}")
        
        return resultado, 200
        
    except Exception as e:
        erro = {
            'status': 'error',
            'message': str(e),
            'data_execucao': datetime.now().isoformat()
        }
        
        print("\n" + "=" * 60)
        print(" ERRO NA EXECUÇÃO")
        print("=" * 60)
        print(f"Erro: {str(e)}")
        
        return erro, 500
