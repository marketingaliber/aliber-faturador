# -*- coding: utf-8 -*-
"""
Teste do localizador (achar botões pelo texto) com prints reais do PDV+.
No Windows usa o OCR do Windows; fora dele (desenvolvimento) usa Tesseract como substituto.
"""
import os
import sys

from PIL import Image

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
import ocr  # noqa: E402
import localizador as L  # noqa: E402

if sys.platform != "win32":  # substituto para testar fora do Windows
    import pytesseract

    def _ler_tess(img, completo=False):
        r = _ler_tess1(img)
        if completo:
            r += _ler_tess1(ocr.tratar(img))
        return r

    def _ler_tess1(img):
        g = img.convert("L").resize((img.width * 2, img.height * 2))
        d = pytesseract.image_to_data(g, lang="eng", output_type=pytesseract.Output.DICT)
        linhas = {}
        for i, t in enumerate(d["text"]):
            if not t.strip():
                continue
            k = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
            linhas.setdefault(k, []).append((t, (d["left"][i] / 2, d["top"][i] / 2, d["width"][i] / 2, d["height"][i] / 2)))
        return [(" ".join(p for p, _ in ps), ps) for ps in linhas.values()]
    ocr.ler = _ler_tess

falhas = []


def perto(achado, x, y, tol=25):
    if achado is None:
        return False
    cx, cy = achado.centro if hasattr(achado, "centro") else achado
    return abs(cx - x) <= tol and abs(cy - y) <= tol


def checa(cond, msg):
    print(("OK    " if cond else "FALHA ") + msg)
    if not cond:
        falhas.append(msg)


def abrir(nome):
    img = Image.open(os.path.join(AQUI, nome)).convert("RGB")
    return img, ocr.ler(img, completo=True)


# --- painel de filtros, 'Qualquer' selecionado
img, ln = abrir("filtros.png")
checa(L.painel_filtros(img, ln) is not None and 1590 < L.painel_filtros(img, ln) < 1630, f"painel Filtros começa em x={L.painel_filtros(img, ln)}")
for chave, (x, y) in {"p_hoje": (1662, 127), "p_7d": (1727, 127), "p_1m": (1784, 127), "p_3m": (1843, 127),
                      "p_6m": (1657, 167), "p_1a": (1714, 167)}.items():
    a = L.botao_periodo(img, ln, chave)
    checa(perto(a, x, y), f"{chave} -> {a}")
a, sel = L.nota_nao_vinculada(img, ln)
checa(perto(a, 1719, 798) and not sel, f"Nota não vinculada {a}, selecionado={sel} (esperado False)")
checa(perto(L.botao_filtrar(img, ln), 1767, 1002), f"Filtrar -> {L.botao_filtrar(img, ln)}")

# --- tela menor (1366x768), botão 1M selecionado, painel cortado antes de 'Situação'
img, ln = abrir("filtros_1366.png")
x0 = L.painel_filtros(img, ln)
checa(x0 is not None and 1040 < x0 < 1080, f"[1366] painel Filtros começa em x={x0}")
cx = L.botoes_periodo(img, ln)
checa(len(cx) == 6, f"[1366] 6 botões de período pelo desenho ({len(cx)} achados)")
for chave, (x, y) in {"p_hoje": (1108, 127), "p_7d": (1172, 127), "p_1m": (1230, 127), "p_3m": (1289, 127),
                      "p_6m": (1104, 167), "p_1a": (1161, 167)}.items():
    a = L.botao_periodo(img, ln, chave)
    checa(perto(a, x, y, 15), f"[1366] {chave} -> {a}")
checa(perto(L.botao_filtrar(img, ln), 1214, 682, 40), f"[1366] Filtrar -> {L.botao_filtrar(img, ln)}")
checa(L.nota_nao_vinculada(img, ln)[0] is None, "[1366] 'Nota não vinculada' fora da tela (precisa rolar o painel)")

# --- a 1920 também pelo desenho
img, ln = abrir("filtros.png")
checa(len(L.botoes_periodo(img, ln)) == 6, "[1920] 6 botões de período pelo desenho")

# --- lista com 'Nota não vinculada' selecionado
img, ln = abrir("lista.png")
a, sel = L.nota_nao_vinculada(img, ln)
checa(sel, f"Nota não vinculada selecionado={sel} (esperado True)")
x, y, w, h = L.regiao_lista(img, ln)
checa(60 <= y <= 95 and 1560 <= x + w <= 1625 and 900 <= y + h <= 960, f"região da lista {(x, y, w, h)}")
checa(L.titulo_venda(img, ln)[0] is None, "lista não é confundida com tela de venda")

# --- tela da venda com menu
img, ln = abrir("venda.png")
checa(L.numero_no_titulo(img, ln, "006.39"), "título mostra 006.39")
checa(not L.numero_no_titulo(img, ln, "006.98"), "título não mostra 006.98")
checa(perto(L.seta_voltar(img, ln), 28, 51, 12), f"seta voltar -> {L.seta_voltar(img, ln)}")
checa(perto(L.menu_emitir(img, ln), 1677, 265, 40), f"menu Emitir NFC-e -> {L.menu_emitir(img, ln)}")
checa(L.dialogo_cpf(img, ln) is None and L.dialogo_confirmar(img, ln) == (None, None), "sem diálogos abertos")

# --- diálogo CPF
img, ln = abrir("dialogo_cpf.png")
b = L.dialogo_cpf(img, ln)
checa(perto(b, 955, 603, 40), f"EMITIR SEM CLIENTE -> {b}")
checa(L.dialogo_confirmar(img, ln) == (None, None), "diálogo CPF não é confundido com a confirmação")

# --- diálogo de confirmação
img, ln = abrir("dialogo_emitir.png")
checa(L.dialogo_cpf(img, ln) is None, "confirmação não é confundida com diálogo CPF")
e, c = L.dialogo_confirmar(img, ln)
checa(perto(e, 1058, 566, 30), f"EMITIR -> {e}")
checa(perto(c, 968, 566, 40), f"CANCELAR -> {c}")

print(f"\n{len(falhas)} falha(s)")
sys.exit(1 if falhas else 0)
