# -*- coding: utf-8 -*-
"""
Teste da leitura de tela com prints reais do PDV+ (nomes de clientes apagados).
Roda no Windows do GitHub antes de publicar cada versão.
"""
import os
import sys

from PIL import Image

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
import ocr  # noqa: E402

falhas = []


def checa(cond, msg):
    print(("OK    " if cond else "FALHA ") + msg)
    if not cond:
        falhas.append(msg)


lista = Image.open(os.path.join(AQUI, "lista.png"))
venda = Image.open(os.path.join(AQUI, "venda.png"))

# região da lista como o macro recorta: à esquerda do painel Filtros
OX, OY = 0, 70
regiao = lista.crop((OX, OY, 1600, 950))
for escala in (1.0, 0.75):
    img = regiao if escala == 1.0 else regiao.resize((int(regiao.width * escala), int(regiao.height * escala)), Image.LANCZOS)
    linhas = ocr.ler(img)
    print(f"\n--- lista, escala {escala}: {len(linhas)} linhas lidas")
    for t, _ in linhas[:14]:
        print("   ", t)
    esperados = {"004.9": 96, "004.8": 180, "006.98": 264, "006.39": 332, "004.7": 400, "003.1": 883}
    for num, y_real in esperados.items():
        pos, qtd = ocr.achar_numero(linhas, num)
        if pos is None:
            checa(False, f"[{escala}] encontrar {num}")
            continue
        y = pos[1] / escala + OY
        checa(abs(y - y_real) < 15 and qtd == 1, f"[{escala}] {num} na altura {y:.0f} (esperado ~{y_real}), {qtd} ocorrência(s)")
    checa(ocr.achar_numero(linhas, "006.3")[0] is None, f"[{escala}] não confundir 006.3 com 006.39")

# parte de cima da lista apenas (simula a venda estar mais abaixo, fora da tela)
linhas = ocr.ler(lista.crop((0, 70, 1600, 450)))
checa(ocr.achar_numero(linhas, "003.1")[0] is None, "003.1 fora da área visível não é encontrado")

# título da tela da venda
titulo = venda.crop((20, 25, 1120, 75))
linhas = ocr.ler(titulo)
print("\n--- título:", [t for t, _ in linhas])
checa(ocr.achar_numero(linhas, "006.39")[0] is not None, "título da venda contém 006.39")
checa(ocr.achar_numero(linhas, "006.98")[0] is None, "título da venda não contém 006.98")

print(f"\n{len(falhas)} falha(s)")
sys.exit(1 if falhas else 0)
