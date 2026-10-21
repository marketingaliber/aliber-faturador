# -*- coding: utf-8 -*-
"""
Atualizador automático do Aliber Faturador.

Ao abrir, o programa consulta um pequeno arquivo versao.json publicado junto com cada
versão nova. Se houver versão mais nova, baixa o .exe novo e troca sozinho:
  1. baixa para  AliberFaturador.exe.novo
  2. renomeia o .exe em uso para AliberFaturador.antigo.exe (o Windows permite)
  3. coloca o novo no lugar, abre ele e fecha o antigo
Configuração, calibração e histórico ficam na mesma pasta e não são tocados.
"""
import glob
import json
import os
import subprocess
import sys
import urllib.request

# Conta do GitHub onde as versões são publicadas. Vazio = atualizador desligado.
DONO_GITHUB = "marketingaliber"
REPO = "aliber-faturador"


def url_versao():
    if not DONO_GITHUB:
        return None
    return f"https://github.com/{DONO_GITHUB}/{REPO}/releases/latest/download/versao.json"


def _num(v):
    return tuple(int(x) for x in str(v).strip().lstrip("vV").split(".") if x.isdigit())


def buscar(versao_atual):
    """Retorna dict {versao, url, notas} se houver versão mais nova; None se não houver."""
    url = url_versao()
    if not url:
        raise RuntimeError("atualizador ainda não configurado")
    req = urllib.request.Request(url, headers={"User-Agent": "AliberFaturador"})
    with urllib.request.urlopen(req, timeout=10) as r:
        info = json.loads(r.read().decode("utf-8-sig"))
    if _num(info["versao"]) > _num(versao_atual):
        return info
    return None


def pode_se_atualizar():
    return getattr(sys, "frozen", False)


def instalar(info, progresso=None):
    """Baixa e troca o .exe. Só funciona no .exe (não no .py)."""
    exe = sys.executable
    novo = exe + ".novo"
    antigo = os.path.splitext(exe)[0] + ".antigo.exe"
    req = urllib.request.Request(info["url"], headers={"User-Agent": "AliberFaturador"})
    with urllib.request.urlopen(req, timeout=60) as r, open(novo, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        lido = 0
        while True:
            bloco = r.read(256 * 1024)
            if not bloco:
                break
            f.write(bloco)
            lido += len(bloco)
            if progresso and total:
                progresso(lido * 100 // total)
    with open(novo, "rb") as f:
        if f.read(2) != b"MZ" or os.path.getsize(novo) < 1_000_000:
            os.remove(novo)
            raise RuntimeError("o arquivo baixado não é um programa válido")
    if os.path.exists(antigo):
        os.remove(antigo)
    os.replace(exe, antigo)
    os.replace(novo, exe)
    reabrir(exe)


def ambiente_limpo():
    """Variáveis de ambiente SEM as marcas do .exe atual. Se a versão nova herdar essas marcas,
    ela tenta usar a pasta temporária da versão antiga (que é apagada ao fechar) e quebra com
    erros como 'cannot import name _imaging' ou 'unable to import pyscreeze'."""
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("_PYI") or k.startswith("_MEIPASS") or k == "TCL_LIBRARY" or k == "TK_LIBRARY")}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def reabrir(exe, args=()):
    """Abre a versão nova só depois que esta fechar, como um programa independente."""
    pasta = os.path.dirname(exe)
    extra = "".join(f' "{a}"' for a in args)
    comando = f'ping -n 3 127.0.0.1 >nul & start "" "{exe}"{extra}'
    subprocess.Popen([exe] + list(args), cwd=pasta, close_fds=True)  # MODO ANTIGO (teste negativo)


def limpar_antigos():
    if not pode_se_atualizar():
        return
    for arq in glob.glob(os.path.join(os.path.dirname(sys.executable), "*.antigo.exe")):
        try:
            os.remove(arq)
        except OSError:
            pass
