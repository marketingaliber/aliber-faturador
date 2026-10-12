# -*- coding: utf-8 -*-
"""
Leitura de texto da tela usando o OCR que já vem no Windows 10/11 (Windows.Media.Ocr).
Não precisa instalar nada no computador do cliente.
"""
import asyncio
import re

ESCALA = 2  # ampliar a imagem melhora muito a leitura de fontes pequenas

_engine = None


def _criar_engine():
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.globalization import Language
    eng = OcrEngine.try_create_from_user_profile_languages()
    if eng is None:
        for tag in ("pt-BR", "en-US"):
            try:
                lang = Language(tag)
                if OcrEngine.is_language_supported(lang):
                    eng = OcrEngine.try_create_from_language(lang)
                    if eng:
                        break
            except Exception:
                pass
    if eng is None:
        langs = OcrEngine.available_recognizer_languages
        langs = list(langs() if callable(langs) else langs)
        if langs:
            eng = OcrEngine.try_create_from_language(langs[0])
    if eng is None:
        raise RuntimeError("O leitor de tela do Windows (OCR) não está disponível neste computador.")
    return eng


def _bitmap(img):
    from winrt.windows.graphics.imaging import SoftwareBitmap, BitmapPixelFormat
    from winrt.windows.storage.streams import DataWriter
    rgba = img.convert("RGBA")
    r, g, b, a = rgba.split()
    from PIL import Image
    bgra = Image.merge("RGBA", (b, g, r, a)).tobytes()
    dw = DataWriter()
    dw.write_bytes(bgra)
    buf = dw.detach_buffer()
    return SoftwareBitmap.create_copy_from_buffer(buf, BitmapPixelFormat.BGRA8, rgba.width, rgba.height)


async def _ler_async(img):
    global _engine
    if _engine is None:
        _engine = _criar_engine()
    res = await _engine.recognize_async(_bitmap(img))
    linhas = []
    for ln in res.lines:
        palavras = []
        for w in ln.words:
            r = w.bounding_rect
            palavras.append((w.text, (r.x / ESCALA, r.y / ESCALA, r.width / ESCALA, r.height / ESCALA)))
        linhas.append((ln.text, palavras))
    return linhas


def ler(img):
    """Retorna [(texto_da_linha, [(palavra, (x, y, largura, altura)), ...]), ...] em pixels da imagem."""
    from PIL import Image
    grande = img.convert("RGB").resize((img.width * ESCALA, img.height * ESCALA), Image.LANCZOS)
    return asyncio.run(_ler_async(grande))


# ---------------------------------------------------------------------------------------------
# Busca do número da venda (funções puras, testáveis em qualquer sistema)
# ---------------------------------------------------------------------------------------------
def _normalizar(txt):
    """Corrige confusões comuns do OCR em números: O->0, vírgula->ponto, espaços em volta do ponto."""
    def conserta(m):
        return m.group(0).replace("O", "0").replace("o", "0").replace(",", ".")
    txt = re.sub(r"[0-9Oo]{2,}\s*[.,]\s*[0-9Oo]+", conserta, txt)
    return re.sub(r"(\d)\s*\.\s*(\d)", r"\1.\2", txt)


def padrao_numero(numero):
    """numero = '006.39' -> casa '006.39' mas não '006.390', '1006.39' nem '006.3'."""
    return re.compile(r"(?<![\d.])" + re.escape(numero) + r"(?![\d])")


def achar_numero(linhas, numero):
    """
    Procura o número da venda nas linhas lidas. Retorna (x, y) do centro da palavra, ou None.
    Retorna também quantas vezes apareceu (para detectar ambiguidade).
    """
    pad = padrao_numero(numero)
    achados = []
    for texto, palavras in linhas:
        if not pad.search(_normalizar(texto)):
            continue
        alvo = None
        for p, (x, y, w, h) in palavras:
            if pad.search(_normalizar(p)) or numero.split(".")[0] in _normalizar(p):
                alvo = (x + w / 2, y + h / 2)
                break
        if alvo is None and palavras:  # número quebrado em várias palavras: usa a linha
            xs = [x for _, (x, y, w, h) in palavras]
            ys = [y + h / 2 for _, (x, y, w, h) in palavras]
            alvo = (min(xs) + 20, sum(ys) / len(ys))
        if alvo:
            achados.append(alvo)
    return (achados[0] if achados else None), len(achados)


def assinatura(linhas):
    """Texto resumido da tela, para saber se a rolagem chegou ao fim (nada mudou)."""
    return "|".join(t for t, _ in linhas)
