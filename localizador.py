# -*- coding: utf-8 -*-
"""
Localiza os elementos da tela do PDV+ pelo TEXTO lido (OCR), sem calibração.

Todas as funções recebem a imagem da janela e as linhas lidas pelo OCR (coordenadas da imagem)
e devolvem posições na imagem. Não dependem de Windows, então são testáveis em qualquer lugar.
"""
import re
import unicodedata

import ocr


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def distancia(a, b):
    """Quantas letras precisam mudar para 'a' virar 'b' (Levenshtein)."""
    if a == b:
        return 0
    ant = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(ant[j] + 1, cur[j - 1] + 1, ant[j - 1] + (ca != cb)))
        ant = cur
    return ant[-1]


def parecido(lido, alvo, prefixo=False):
    """Igual, tolerando 1 erro a cada 5 letras nas palavras longas (OCR troca acentos e letras)."""
    if prefixo:
        if lido.startswith(alvo):
            return True
        if len(alvo) < 5:
            return False
        n = len(alvo)
        return min(distancia(lido[:k], alvo) for k in range(max(1, n - 2), n + 3)) <= max(1, n // 5)
    if len(alvo) < 5:
        return lido == alvo
    return distancia(lido, alvo) <= max(1, len(alvo) // 5)


class Achado:
    def __init__(self, x, y, w, h, texto=""):
        self.x, self.y, self.w, self.h, self.texto = x, y, w, h, texto

    @property
    def centro(self):
        return (self.x + self.w / 2, self.y + self.h / 2)

    def __repr__(self):
        cx, cy = self.centro
        return f"<{self.texto!r} em ({cx:.0f},{cy:.0f})>"


def _uniao(palavras):
    xs = [r[0] for _, r in palavras]
    ys = [r[1] for _, r in palavras]
    x2 = [r[0] + r[2] for _, r in palavras]
    y2 = [r[1] + r[3] for _, r in palavras]
    return Achado(min(xs), min(ys), max(x2) - min(xs), max(y2) - min(ys), " ".join(p for p, _ in palavras))


def procurar(linhas, frase, prefixo=False, filtro=None):
    """Todas as ocorrências da frase (sem acento, maiúsc/minúsc, pontuação, tolerando erros de leitura)."""
    alvo = norm(frase)
    achados = []
    for _, palavras in linhas:
        n = len(palavras)
        for i in range(n):
            junto = ""
            for j in range(i, min(n, i + 6)):
                junto += norm(palavras[j][0])
                if not junto:
                    continue
                if parecido(junto, alvo, prefixo):
                    a = _uniao(palavras[i:j + 1])
                    if filtro is None or filtro(a):
                        achados.append(a)
                    break
                if len(junto) > len(alvo) + 2:
                    break
    achados.sort(key=lambda a: (a.y, a.x))
    return achados


def _primeiro(lista):
    return lista[0] if lista else None


# ---------------------------------------------------------------- tela "Buscar negociações"
def painel_filtros(img, linhas):
    """Título 'Filtros' do painel da direita. Retorna o x onde o painel começa, ou None."""
    W = img.width
    t = _primeiro(procurar(linhas, "Filtros", filtro=lambda a: a.x > W * 0.55))
    return (t.x - 15) if t else None


PERIODOS = {
    "p_hoje": ["HOJE"],
    "p_7d": ["7D", "TD"],
    "p_1m": ["1M", "IM", "LM"],
    "p_3m": ["3M"],
    "p_6m": ["6M", "GM"],
    "p_1a": ["1A", "IA", "LA"],
}


def botao_periodo(img, linhas, chave):
    x0 = painel_filtros(img, linhas)
    if x0 is None:
        return None
    lim_y = img.height * 0.3
    cli = _primeiro(procurar(linhas, "Cliente", filtro=lambda a: a.x > x0))
    if cli:
        lim_y = cli.y
    def achar(k):
        for variante in PERIODOS[k]:
            a = _primeiro(procurar(linhas, variante, filtro=lambda a: a.x > x0 and a.y < lim_y))
            if a:
                return a
        return None

    a = achar(chave)
    if a or chave in ("p_6m", "p_1a"):
        return a
    # reserva: a 1ª fileira (Hoje 7D 1M 3M) fica logo acima da 2ª (6M 1A), com o mesmo espaçamento
    m6, a1 = achar("p_6m"), achar("p_1a")
    if not (m6 and a1):
        return None
    passo = a1.centro[0] - m6.centro[0]          # distância entre botões vizinhos
    sobe = m6.h * 3.0                            # altura de uma fileira
    col = {"p_hoje": 0, "p_7d": 1, "p_1m": 2, "p_3m": 3}[chave]
    cx = m6.centro[0] + 5 + col * (passo + 6)
    cy = m6.centro[1] - sobe
    return Achado(cx - 15, cy - m6.h / 2, 30, m6.h, f"{chave} (calculado)")


def nota_nao_vinculada(img, linhas):
    """Retorna (Achado, selecionado: bool) ou (None, False)."""
    x0 = painel_filtros(img, linhas)
    if x0 is None:
        return None, False
    a = _primeiro(procurar(linhas, "Nota não vinculada", filtro=lambda a: a.x > x0))
    if not a:
        return None, False
    return a, _fundo_azul(img, a)


def _fundo_azul(img, a):
    """O botão selecionado tem fundo azul. Mede pixels logo à esquerda e à direita do texto."""
    rgb = img.convert("RGB")
    cy = int(a.y + a.h / 2)
    azuis = total = 0
    for dx in (-10, -7, -4):
        for x in (int(a.x) + dx, int(a.x + a.w) - dx):
            for dy in (-3, 0, 3):
                if 0 <= x < rgb.width and 0 <= cy + dy < rgb.height:
                    r, g, b = rgb.getpixel((x, cy + dy))
                    total += 1
                    if b > 110 and b > r + 50 and b > g + 20:
                        azuis += 1
    return total > 0 and azuis / total > 0.5


def botao_filtrar(img, linhas):
    x0 = painel_filtros(img, linhas)
    if x0 is None:
        return None
    achados = procurar(linhas, "Filtrar", filtro=lambda a: a.x > x0)
    if achados:
        return achados[-1]
    # reserva: o botão Filtrar é o único bloco vermelho grande no pé do painel
    rgb = img.convert("RGB")
    xs, ys = [], []
    for y in range(int(img.height * 0.75), img.height, 3):
        for x in range(int(x0), img.width, 3):
            r, g, b = rgb.getpixel((x, y))
            if r > 170 and g < 90 and b < 90:
                xs.append(x)
                ys.append(y)
    if len(xs) > 200:
        return Achado(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys), "Filtrar (pela cor)")
    return None


def regiao_lista(img, linhas):
    """(x, y, largura, altura) da área das vendas: abaixo do título 'Negociações',
    acima da barra 'Total das negociações filtradas' e à esquerda do painel Filtros."""
    W, H = img.width, img.height
    x0 = painel_filtros(img, linhas)
    direita = (x0 - 10) if x0 else int(W * 0.83)
    topo = 70
    t = _primeiro(procurar(linhas, "Negociações", filtro=lambda a: a.y < H * 0.12 and a.x < W * 0.3))
    if t:
        topo = int(t.y + t.h + 8)
    base = H - 20
    embaixo = lambda a: a.y > H * 0.5  # noqa: E731
    tot = (_primeiro(procurar(linhas, "Total das negociações", prefixo=True, filtro=embaixo))
           or _primeiro(procurar(linhas, "filtradas", filtro=embaixo)))
    if tot:
        base = int(tot.y - 28)
    return (2, topo, max(50, direita - 2), max(50, base - topo))


# ---------------------------------------------------------------- tela da venda
def titulo_venda(img, linhas):
    """Linha do título 'Negociação Nº ...' no topo. Retorna (texto, Achado da palavra Negociação)."""
    H = img.height
    for texto, palavras in linhas:
        for p, r in palavras:
            n = re.sub(r"^[^A-Z]*", "", norm(p))
            if r[1] < H * 0.12 and len(n) >= 8:
                d1, d2 = distancia(n[:10], "NEGOCIACAO"), distancia(n[:11], "NEGOCIACOES")
                if d1 <= 2 and d1 < d2:
                    return texto, Achado(*r, p)
    return None, None


def numero_no_titulo(img, linhas, numero):
    texto, _ = titulo_venda(img, linhas)
    if not texto:
        return False
    return ocr.achar_numero([(texto, [])], numero)[1] > 0 or bool(ocr.padrao_numero(numero).search(ocr._normalizar(texto)))


def seta_voltar(img, linhas):
    """A seta não tem texto: é o primeiro desenho escuro à esquerda, na altura do título."""
    _, t = titulo_venda(img, linhas)
    if not t:
        return None
    rgb = img.convert("RGB")
    cy = int(t.y + t.h / 2)
    y0, y1 = max(0, cy - 14), min(rgb.height, cy + 14)
    colunas = []
    for x in range(0, min(rgb.width, int(t.x + t.w))):
        escuros = [y for y in range(y0, y1) if sum(rgb.getpixel((x, y))) < 250]
        colunas.append(escuros)
    inicio = next((x for x, c in enumerate(colunas) if c), None)
    if inicio is None:
        return None
    fim, vazio = inicio, 0
    for x in range(inicio, len(colunas)):
        if colunas[x]:
            fim, vazio = x, 0
        else:
            vazio += 1
            if vazio > 6:
                break
    ys = [y for x in range(inicio, fim + 1) for y in colunas[x]]
    return ((inicio + fim) / 2, sum(ys) / len(ys))


def menu_emitir(img, linhas):
    W = img.width
    return _primeiro(procurar(linhas, "Emitir NFC-e", prefixo=True, filtro=lambda a: a.x > W * 0.7))


# ---------------------------------------------------------------- diálogos (centro da tela)
def _centro(img):
    W = img.width
    return lambda a: W * 0.25 < a.x + a.w / 2 < W * 0.75


def dialogo_cpf(img, linhas):
    """Diálogo 'Cliente sem CPF/CNPJ'. Retorna o Achado do botão EMITIR SEM CLIENTE, ou None."""
    c = _centro(img)
    if not procurar(linhas, "Cliente sem CPF", prefixo=True, filtro=c):
        return None
    return _primeiro(procurar(linhas, "Emitir sem cliente", filtro=c))


def dialogo_confirmar(img, linhas):
    """Diálogo 'Emitir NFC-e?'. Retorna (botão EMITIR, botão CANCELAR) ou (None, None)."""
    c = _centro(img)
    if procurar(linhas, "Cliente sem CPF", prefixo=True, filtro=c):
        return None, None
    titulo = _primeiro(procurar(linhas, "Emitir NFC-e", prefixo=True, filtro=c))
    if not titulo:
        return None, None
    abaixo = lambda a: c(a) and a.y > titulo.y + titulo.h  # noqa: E731
    emitir = _primeiro(procurar(linhas, "Emitir", filtro=abaixo))
    cancelar = _primeiro(procurar(linhas, "Cancelar", filtro=abaixo))
    return emitir, cancelar
