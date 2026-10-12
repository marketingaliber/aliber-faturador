# -*- coding: utf-8 -*-
"""
Aliber Faturador - emite NFC-e das vendas de cartão/Pix que ficaram sem nota no PDV+.

Como funciona:
  1. Lê o banco do PDV+ (somente leitura) e separa as vendas que devem ser faturadas.
  2. Um macro opera a tela "Buscar negociações" do PDV+, filtrando UMA venda por vez
     pelo minuto em que ela foi aberta, e clica em Emitir NFC-e.
  3. Depois de cada emissão, confere no banco se a nota foi para a venda certa.
     Qualquer coisa fora do esperado -> o macro para.

Tecla ESC (ou mouse no canto superior esquerdo da tela) para o macro a qualquer momento.
"""
import csv
import json
import os
import queue
import sys
import threading
import time
from datetime import date, datetime, timedelta

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import atualizador
import faturador_core as fc
import ocr

APP_NOME = "Aliber Faturador"
VERSAO = "2.1"
VERMELHO = "#D7262B"
AZUL = "#1B4FA0"

if getattr(sys, "frozen", False):
    PASTA = os.path.dirname(sys.executable)
else:
    PASTA = os.path.dirname(os.path.abspath(__file__))
ARQ_CONFIG = os.path.join(PASTA, "faturador_config.json")
PASTA_CALIB = os.path.join(PASTA, "calibracao")
ARQ_HIST = os.path.join(PASTA, "historico_faturamento.csv")
ARQ_FALHA = os.path.join(PASTA, "calibracao", "ultima_busca_sem_sucesso.png")

# Pontos que o macro clica. (chave, instrução, opcional, confere_imagem)
PONTOS = [
    ("filtrar", "Botão vermelho FILTRAR (painel Filtros de 'Buscar negociações')", False, True),
    ("nota_nao_vinculada", "Clique em 'Nota não vinculada' para ela ficar AZUL. Depois: mouse em cima dela e F8", False, True),
    ("p_hoje", "Botão de período HOJE", False, False),
    ("p_7d", "Botão de período 7D", False, False),
    ("p_1m", "Botão de período 1M", False, False),
    ("p_3m", "Botão de período 3M", False, False),
    ("p_6m", "Botão de período 6M", False, False),
    ("p_1a", "Botão de período 1A", False, False),
    ("abrir_filtros", "OPCIONAL: botão que reabre o painel Filtros, se ele fechar (F9 pula)", True, False),
    ("lista_sup", "Clique em 3M e em Filtrar. Agora: canto SUPERIOR ESQUERDO da lista (logo abaixo do título 'Negociações')", False, False),
    ("lista_inf", "Canto INFERIOR DIREITO da lista (acima da barra 'Total das negociações', antes do painel Filtros)", False, False),
    ("emitir_nfce", "Abra uma venda da lista. Agora: 'Emitir NFC-e' no menu da direita", False, True),
    ("voltar", "Seta de VOLTAR, no canto superior esquerdo (só F8, não clique)", False, False),
    ("emitir_sem_cliente", "Clique em Emitir NFC-e. Agora: 'EMITIR SEM CLIENTE' (F9 se não apareceu)", True, True),
    ("emitir", "Clique em Emitir sem cliente. Agora: botão 'EMITIR' da confirmação", False, True),
    ("cancelar", "Botão 'CANCELAR' dessa mesma confirmação (depois clique nele de verdade)", False, False),
]

# Botão de período do PDV+ conforme a idade da venda (em dias). Limites com folga.
PERIODOS = [(0, "p_hoje"), (5, "p_7d"), (27, "p_1m"), (88, "p_3m"), (178, "p_6m"), (360, "p_1a")]

CONFIG_PADRAO = {
    "db_path": fc.DB_PADRAO,
    "formas_faturar": fc.FORMAS_FATURAR_PADRAO,
    "pontos": {},
    "espera_clique": 0.8,
    "espera_lista": 2.5,
    "espera_tela": 2.0,
    "espera_pos_emissao": 4.0,
    "tempo_limite_nota": 60,
    "tolerancia_imagem": 28,
    "rolagem": 3,          # "cliques" da rodinha do mouse por passo ao procurar na lista
    "max_rolagens": 80,
}


def carregar_config():
    cfg = dict(CONFIG_PADRAO)
    try:
        with open(ARQ_CONFIG, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def salvar_config(cfg):
    with open(ARQ_CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def registrar_historico(venda, resultado):
    novo = not os.path.exists(ARQ_HIST)
    with open(ARQ_HIST, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        if novo:
            w.writerow(["data_hora", "venda", "aberta_em", "valor", "pagamento", "resultado"])
        w.writerow([datetime.now().strftime("%d/%m/%Y %H:%M:%S"), venda["numero"],
                    venda["criado"].strftime("%d/%m/%Y %H:%M"),
                    f"{venda['valor']:.2f}".replace(".", ","), venda["formas"], resultado])


# ----------------------------------------------------------------------------------
# Tela / mouse / teclado (só Windows)
# ----------------------------------------------------------------------------------
class Parado(Exception):
    pass


class Tela:
    def __init__(self, cfg):
        import pyautogui
        import pygetwindow
        self.pg = pyautogui
        self.gw = pygetwindow
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.05
        self.cfg = cfg
        self.parar = threading.Event()

    # janela do PDV+
    def janela(self):
        for w in self.gw.getAllWindows():
            t = (w.title or "").strip()
            if t.startswith("PDV+") and w.width > 300:
                return w
        return None

    def ativar(self):
        w = self.janela()
        if not w:
            raise Parado("Janela do PDV+ não encontrada. Abra o PDV+ na tela 'Buscar negociações'.")
        try:
            if w.isMinimized:
                w.restore()
            if not w.isMaximized:
                w.maximize()
            w.activate()
        except Exception:
            pass
        time.sleep(0.6)
        return w

    def origem(self):
        w = self.janela()
        return (w.left, w.top) if w else (0, 0)

    def ponto(self, chave):
        p = self.cfg["pontos"].get(chave)
        if not p:
            return None
        ox, oy = self.origem()
        return ox + p["x"], oy + p["y"]

    def checar_parada(self):
        if self.parar.is_set():
            raise Parado("Parado pelo usuário (ESC).")

    def esperar(self, seg):
        fim = time.time() + seg
        while time.time() < fim:
            self.checar_parada()
            time.sleep(0.1)

    def clicar(self, chave):
        self.checar_parada()
        xy = self.ponto(chave)
        if not xy:
            raise Parado(f"Ponto '{chave}' não calibrado.")
        self.pg.click(*xy)
        self.esperar(self.cfg["espera_clique"])

    def digitar(self, texto, apagar=14):
        """Limpa o campo sem depender do Ctrl+A (o PDV+ ignora): End + vários Backspace, depois digita."""
        self.checar_parada()
        self.pg.press("end")
        time.sleep(0.1)
        self.pg.press("backspace", presses=apagar, interval=0.03)
        time.sleep(0.15)
        digitar_unicode(texto)
        time.sleep(0.3)

    # leitura de tela
    def regiao_lista(self):
        """Da borda esquerda da janela até o canto direito calibrado, com folga acima do canto
        superior: assim nenhum número fica cortado, mesmo se o canto foi marcado sobre o texto."""
        a, b = self.ponto("lista_sup"), self.ponto("lista_inf")
        ox, oy = self.origem()
        x0 = max(0, ox) + 2
        y0 = max(max(0, oy) + 2, a[1] - 40)
        return (int(x0), int(y0), int(b[0] - x0), int(b[1] - y0))

    def regiao_titulo(self):
        x, y = self.ponto("voltar")
        return (int(x + 20), int(max(0, y - 26)), 1100, 52)

    def captura(self, reg):
        return self.pg.screenshot(region=reg)

    def rolar(self, reg, cliques):
        """Rodinha do mouse sobre a lista. cliques > 0 sobe, < 0 desce."""
        import ctypes
        self.checar_parada()
        self.pg.moveTo(reg[0] + reg[2] // 2, reg[1] + reg[3] // 2)
        passo = 1 if cliques > 0 else -1
        for _ in range(abs(cliques)):
            ctypes.windll.user32.mouse_event(0x0800, 0, 0, 120 * passo, 0)
            time.sleep(0.02)

    # conferência visual: compara um recorte da tela com o salvo na calibração
    def recorte(self, chave):
        xy = self.ponto(chave)
        if not xy:
            return None
        return self.pg.screenshot(region=(xy[0] - 30, xy[1] - 12, 60, 24))

    def confere(self, chave):
        from PIL import Image, ImageChops, ImageStat
        arq = os.path.join(PASTA_CALIB, f"{chave}.png")
        if not os.path.exists(arq):
            return True  # sem referência, não bloqueia
        xy = self.ponto(chave)
        self.pg.moveTo(*xy)
        time.sleep(0.35)
        atual = self.recorte(chave).convert("RGB")
        ref = Image.open(arq).convert("RGB")
        dif = ImageStat.Stat(ImageChops.difference(atual, ref)).mean
        return sum(dif) / 3 <= self.cfg["tolerancia_imagem"]


def digitar_unicode(texto):
    """Digita caractere a caractere via SendInput Unicode (não depende do layout ABNT/US)."""
    import ctypes
    from ctypes import wintypes

    INPUT_KEYBOARD, KEYEVENTF_UNICODE, KEYEVENTF_KEYUP = 1, 0x0004, 0x0002
    ULONG_PTR = ctypes.c_size_t

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    for ch in texto:
        for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
            inp = INPUT(type=INPUT_KEYBOARD)
            inp.u.ki = KEYBDINPUT(0, ord(ch), flags, 0, 0)
            ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        time.sleep(0.04)


# ----------------------------------------------------------------------------------
# O macro
# ----------------------------------------------------------------------------------
def filtrar(tela, cfg, venda):
    """Período adequado à idade da venda + 'Nota não vinculada' + Filtrar."""
    dias = (date.today() - venda["criado"].date()).days
    chave = next((k for limite, k in PERIODOS if dias <= limite), None)
    if not chave:
        raise Parado(f"Venda {venda['numero']} é mais antiga que o maior período do PDV+ (1A).")
    if not tela.confere("filtrar"):
        if cfg["pontos"].get("abrir_filtros"):
            tela.clicar("abrir_filtros")
            tela.esperar(1.0)
        if not tela.confere("filtrar"):
            raise Parado("Não encontrei o painel Filtros de 'Buscar negociações' na tela.")
    tela.clicar(chave)
    if not tela.confere("nota_nao_vinculada"):
        tela.clicar("nota_nao_vinculada")
        tela.esperar(0.5)
        if not tela.confere("nota_nao_vinculada"):
            raise Parado("Não consegui deixar 'Nota não vinculada' selecionado.")
    tela.clicar("filtrar")
    tela.esperar(cfg["espera_lista"])


def localizar(tela, cfg, num, log):
    """Lê a lista, rolando se preciso, até achar o número. Retorna (x, y) na tela ou None."""
    reg = tela.regiao_lista()
    tela.rolar(reg, 40)  # garante o topo da lista
    tela.esperar(0.6)
    anterior = None
    for i in range(int(cfg["max_rolagens"])):
        img = tela.captura(reg)
        linhas = ocr.ler(img)
        pos, _ = ocr.achar_numero(linhas, num)
        if pos:
            return reg[0] + pos[0], reg[1] + pos[1]
        atual = ocr.assinatura(linhas)
        if atual == anterior:  # rolou e nada mudou: fim da lista
            break
        anterior = atual
        tela.rolar(reg, -int(cfg["rolagem"]))
        tela.esperar(0.7)
    try:
        os.makedirs(os.path.dirname(ARQ_FALHA), exist_ok=True)
        img.save(ARQ_FALHA)
        with open(ARQ_FALHA.replace(".png", ".txt"), "w", encoding="utf-8") as f:
            f.write(f"Procurando: {num}\n\n" + "\n".join(t for t, _ in linhas))
    except Exception:
        pass
    log(f"   não achei {num} na lista ({i + 1} tela(s) lida(s)); imagem salva em calibracao/")
    return None


def emitir_uma(tela, cfg, venda, teste, log):
    db = cfg["db_path"]
    ok, motivo = fc.reavaliar(db, venda["id_venda"])
    if not ok:
        return "pulada", motivo

    tela.ativar()
    filtrar(tela, cfg, venda)

    # 1) acha a venda na lista pelo número e abre
    xy = localizar(tela, cfg, venda["num"], log)
    if not xy:
        return "pulada", "não encontrada na lista do PDV+"
    antes = fc.vendas_com_nota(db)
    tela.pg.click(*xy)
    tela.esperar(cfg["espera_tela"])

    # 2) confere o número no título ANTES de emitir
    titulo = ocr.ler(tela.captura(tela.regiao_titulo()))
    if ocr.achar_numero(titulo, venda["num"])[0] is None:
        lido = " / ".join(t for t, _ in titulo) or "nada"
        tela.clicar("voltar")
        raise Parado(f"Abri uma venda, mas o título não mostra {venda['num']} (li: {lido}). Nada foi emitido.")
    if not tela.confere("emitir_nfce"):
        raise Parado(f"Venda {venda['numero']}: menu 'Emitir NFC-e' não encontrado. Nada foi emitido.")
    tela.clicar("emitir_nfce")
    tela.esperar(1.0)

    # 3) diálogo de CPF/CNPJ (só aparece quando a venda não tem cliente com documento)
    if cfg["pontos"].get("emitir_sem_cliente") and tela.confere("emitir_sem_cliente"):
        if venda["tem_doc"]:
            raise Parado(f"Venda {venda['numero']} tem CPF/CNPJ, mas o PDV+ pediu o documento. "
                         "Para não emitir sem o cliente, emita essa à mão e rode de novo.")
        tela.clicar("emitir_sem_cliente")
        tela.esperar(1.0)

    # 4) confirmação "Emitir NFC-e?"
    if not tela.confere("emitir"):
        raise Parado(f"Venda {venda['numero']}: a confirmação 'Emitir NFC-e?' não apareceu.")
    if teste:
        tela.clicar("cancelar")
        tela.esperar(1.0)
        tela.clicar("voltar")
        tela.esperar(cfg["espera_tela"])
        return "teste", "achou a venda, conferiu o título e cancelou na confirmação (nada emitido)"

    tela.clicar("emitir")
    log(f"{venda['numero']}: emitindo, aguardando a SEFAZ...")
    ok, msg = fc.aguardar_nota(db, venda["id_venda"], antes, cfg["tempo_limite_nota"])
    if not ok:
        raise Parado(f"Venda {venda['numero']}: {msg}")
    tela.esperar(cfg["espera_pos_emissao"])  # aviso de confirmação some sozinho
    tela.clicar("voltar")
    tela.esperar(cfg["espera_tela"])
    return "emitida", msg


def rodar_macro(cfg, vendas, teste, fila):
    def log(m):
        fila.put(("log", m))

    try:
        tela = Tela(cfg)
    except Exception as e:  # noqa
        fila.put(("fim", f"Não foi possível iniciar o macro: {e}"))
        return

    from pynput import keyboard

    def tecla(k):
        if k == keyboard.Key.esc:
            tela.parar.set()
    ouvinte = keyboard.Listener(on_press=tecla)
    ouvinte.start()

    emitidas = puladas = 0
    total_valor = 0.0
    msg_final = ""
    st = None
    try:
        for i, v in enumerate(vendas, 1):
            log(f"[{i}/{len(vendas)}] {v['numero']}  R$ {v['valor']:.2f}  {v['formas']}")
            st, msg = emitir_uma(tela, cfg, v, teste, log)
            log(f"   -> {st}: {msg}")
            if not teste:
                registrar_historico(v, f"{st}: {msg}")
            if st == "emitida":
                emitidas += 1
                total_valor += v["valor"]
            elif st == "pulada":
                puladas += 1
            if teste and st == "teste":
                break
        if teste and st == "teste":
            msg_final = (f"Teste concluído com a venda {v['numero']}: achou na lista, conferiu o número e "
                         "CANCELOU na confirmação. Nenhuma nota foi emitida.")
        elif teste:
            msg_final = ("Teste: nenhuma venda pendente foi encontrada na tela do PDV+. Nada foi emitido. "
                         "Veja a imagem calibracao/ultima_busca_sem_sucesso.png.")
        else:
            msg_final = f"Concluído. {emitidas} NFC-e emitida(s), R$ {total_valor:.2f}. {puladas} pulada(s)."
    except Parado as e:
        msg_final = f"MACRO PARADO: {e}\n\nEmitidas antes da parada: {emitidas}."
        if not teste:
            registrar_historico({"numero": "-", "criado": datetime.now(), "valor": 0, "formas": ""},
                                f"PARADO: {e}")
    except Exception as e:  # pyautogui.FailSafeException e outros
        msg_final = f"MACRO PARADO: {type(e).__name__}: {e}\n\nEmitidas antes da parada: {emitidas}."
    finally:
        ouvinte.stop()
    fila.put(("fim", msg_final))


# ----------------------------------------------------------------------------------
# Interface
# ----------------------------------------------------------------------------------
class App:
    def __init__(self, root):
        self.root = root
        self.cfg = carregar_config()
        self.vendas = []
        self.fila = queue.Queue()
        root.title(f"{APP_NOME} {VERSAO}")
        root.geometry("1100x640")
        root.minsize(900, 500)

        topo = tk.Frame(root, bg=AZUL)
        topo.pack(fill="x")
        tk.Label(topo, text="Aliber", fg=VERMELHO, bg=AZUL, font=("Segoe UI", 18, "bold")).pack(side="left", padx=(16, 4), pady=8)
        tk.Label(topo, text="Faturador", fg="white", bg=AZUL, font=("Segoe UI", 18)).pack(side="left", pady=8)
        tk.Label(topo, text="NFC-e posterior para vendas de cartão e Pix", fg="#cfd8ea", bg=AZUL,
                 font=("Segoe UI", 10)).pack(side="left", padx=16)

        filtro = tk.Frame(root)
        filtro.pack(fill="x", padx=12, pady=8)
        tk.Label(filtro, text="De").pack(side="left")
        self.ent_de = ttk.Entry(filtro, width=11)
        self.ent_de.pack(side="left", padx=4)
        tk.Label(filtro, text="Até").pack(side="left")
        self.ent_ate = ttk.Entry(filtro, width=11)
        self.ent_ate.pack(side="left", padx=4)
        for nome, d0, d1 in [("Hoje", 0, 0), ("Ontem", 1, 1), ("7 dias", 6, 0), ("30 dias", 29, 0)]:
            ttk.Button(filtro, text=nome, command=lambda a=d0, b=d1: self.periodo(a, b)).pack(side="left", padx=2)
        ttk.Button(filtro, text="Mês atual", command=self.mes_atual).pack(side="left", padx=2)
        self.var_todas = tk.BooleanVar(value=False)
        ttk.Checkbutton(filtro, text="Mostrar as que já têm nota", variable=self.var_todas,
                        command=self.preencher).pack(side="left", padx=12)
        ttk.Button(filtro, text="Atualizar", command=self.atualizar).pack(side="right")

        cols = ("status", "numero", "data", "valor", "formas", "cliente", "motivo")
        corpo = tk.Frame(root)
        corpo.pack(fill="both", expand=True, padx=12)
        self.tree = ttk.Treeview(corpo, columns=cols, show="headings")
        for c, t, w, a in [("status", "Situação", 170, "w"), ("numero", "Nº", 130, "w"),
                           ("data", "Aberta em", 110, "w"), ("valor", "Valor", 90, "e"),
                           ("formas", "Pagamento", 230, "w"), ("cliente", "Cliente", 150, "w"),
                           ("motivo", "Observação", 260, "w")]:
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor=a)
        self.tree.tag_configure("faturar", background="#e3f4e6")
        self.tree.tag_configure("manual", background="#fff3cd")
        self.tree.tag_configure("cinza", foreground="#888888")
        sb = ttk.Scrollbar(corpo, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)

        rodape = tk.Frame(root)
        rodape.pack(fill="x", padx=12, pady=8)
        self.lbl_resumo = tk.Label(rodape, text="", font=("Segoe UI", 10, "bold"))
        self.lbl_resumo.pack(side="left")
        tk.Button(rodape, text="  FATURAR PENDENTES  ", bg=VERMELHO, fg="white", font=("Segoe UI", 10, "bold"),
                  relief="flat", command=lambda: self.iniciar(False)).pack(side="right", padx=4)
        ttk.Button(rodape, text="Testar (sem emitir)", command=lambda: self.iniciar(True)).pack(side="right", padx=4)
        ttk.Button(rodape, text="Calibrar cliques", command=self.calibrar).pack(side="right", padx=4)
        ttk.Button(rodape, text="Configurações", command=self.configuracoes).pack(side="right", padx=4)

        self.txt_log = tk.Text(root, height=6, font=("Consolas", 9), state="disabled", bg="#f6f7f9")
        self.txt_log.pack(fill="x", padx=12, pady=(0, 10))

        self.periodo(0, 0)
        self.root.after(200, self.verificar_fila)
        if not all(k in self.cfg["pontos"] for k, _, opc, _ in PONTOS if not opc):
            self.log("Calibração pendente: clique em 'Calibrar cliques' antes de faturar.")
        atualizador.limpar_antigos()
        self.root.after(1500, self.checar_atualizacao)

    # ---- atualização ----
    def checar_atualizacao(self, avisar=False):
        def tarefa():
            try:
                info = atualizador.buscar(VERSAO)
                self.fila.put(("atualizacao", (info, avisar, None)))
            except Exception as e:  # sem internet, sem GitHub configurado etc.
                self.fila.put(("atualizacao", (None, avisar, str(e))))
        threading.Thread(target=tarefa, daemon=True).start()

    def oferecer_atualizacao(self, info, avisar, erro):
        if not info:
            if avisar:
                (messagebox.showwarning if erro else messagebox.showinfo)(
                    APP_NOME, f"Não foi possível verificar: {erro}" if erro else f"Você já está na versão mais nova ({VERSAO}).")
            return
        notas = info.get("notas", "")
        if not atualizador.pode_se_atualizar():
            self.log(f"Versão {info['versao']} disponível (atualização automática só funciona no .exe).")
            return
        if not messagebox.askyesno(APP_NOME, f"Nova versão disponível: {info['versao']} (você está na {VERSAO}).\n\n"
                                   f"{notas}\n\nAtualizar agora? O programa fecha e abre sozinho."):
            return
        self.log(f"Baixando versão {info['versao']}...")

        def tarefa():
            try:
                atualizador.instalar(info, lambda p: self.fila.put(("log", f"Baixando... {p}%")) if p % 20 == 0 else None)
                self.fila.put(("reiniciar", None))
            except Exception as e:
                self.fila.put(("log", f"Falha na atualização: {e}"))
        threading.Thread(target=tarefa, daemon=True).start()

    # ---- período ----
    def periodo(self, dias_atras_ini, dias_atras_fim):
        hoje = date.today()
        self._set_datas(hoje - timedelta(days=dias_atras_ini), hoje - timedelta(days=dias_atras_fim))

    def mes_atual(self):
        hoje = date.today()
        self._set_datas(hoje.replace(day=1), hoje)

    def _set_datas(self, d0, d1):
        for ent, d in ((self.ent_de, d0), (self.ent_ate, d1)):
            ent.delete(0, "end")
            ent.insert(0, d.strftime("%d/%m/%Y"))
        self.atualizar()

    def _ler_datas(self):
        try:
            d0 = datetime.strptime(self.ent_de.get().strip(), "%d/%m/%Y").date()
            d1 = datetime.strptime(self.ent_ate.get().strip(), "%d/%m/%Y").date()
            return d0, d1
        except ValueError:
            messagebox.showerror(APP_NOME, "Data inválida. Use dd/mm/aaaa.")
            return None

    # ---- dados ----
    def atualizar(self):
        datas = self._ler_datas()
        if not datas:
            return
        if not os.path.exists(self.cfg["db_path"]):
            self.vendas = []
            self.preencher()
            self.lbl_resumo.config(text="Banco do PDV+ não encontrado. Ajuste em Configurações.", fg=VERMELHO)
            return
        try:
            self.vendas = fc.triagem(self.cfg["db_path"], datas[0], datas[1], self.cfg["formas_faturar"])
        except Exception as e:
            messagebox.showerror(APP_NOME, f"Erro ao ler o banco do PDV+:\n{e}")
            return
        self.preencher()

    def pendentes(self):
        # da mais antiga para a mais recente
        return sorted([v for v in self.vendas if v["status"] == fc.FATURAR], key=lambda v: v["criado"])

    def preencher(self):
        self.tree.delete(*self.tree.get_children())
        for v in self.vendas:
            if v["status"] == fc.JA_FATURADA and not self.var_todas.get():
                continue
            tag = ("faturar" if v["status"] == fc.FATURAR else
                   "manual" if v["status"] == fc.MANUAL else "cinza")
            self.tree.insert("", "end", tags=(tag,), values=(
                v["status"], v["numero"], v["criado"].strftime("%d/%m %H:%M"),
                f"{v['valor']:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                v["formas"], v["cliente"], v["motivo"]))
        p = self.pendentes()
        manuais = sum(1 for v in self.vendas if v["status"] == fc.MANUAL)
        txt = f"{len(p)} venda(s) para faturar  •  R$ {sum(v['valor'] for v in p):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        if manuais:
            txt += f"   |   {manuais} para emitir à mão (amarelo)"
        self.lbl_resumo.config(text=txt, fg=AZUL)

    def log(self, m):
        self.txt_log.config(state="normal")
        self.txt_log.insert("end", f"{datetime.now():%H:%M:%S}  {m}\n")
        self.txt_log.see("end")
        self.txt_log.config(state="disabled")

    # ---- macro ----
    def iniciar(self, teste):
        faltando = [k for k, _, opc, _ in PONTOS if not opc and k not in self.cfg["pontos"]]
        if faltando:
            messagebox.showwarning(APP_NOME, "Calibre os cliques primeiro (botão 'Calibrar cliques').")
            return
        self.atualizar()
        p = self.pendentes()
        if not p:
            messagebox.showinfo(APP_NOME, "Nenhuma venda de cartão/Pix sem nota no período.")
            return
        if teste:
            texto = (f"TESTE com a venda {p[0]['numero']} (R$ {p[0]['valor']:.2f}).\n\n"
                     "O macro vai fazer todo o caminho e clicar em CANCELAR na confirmação. "
                     "Nenhuma nota será emitida.\n\n"
                     "Deixe o PDV+ aberto em 'Buscar negociações' com o painel Filtros visível "
                     "e não mexa no mouse. ESC para parar.")
        else:
            total = sum(v["valor"] for v in p)
            texto = (f"Emitir NFC-e de {len(p)} venda(s), total R$ {total:.2f}?\n\n"
                     "Deixe o PDV+ aberto em 'Buscar negociações' com o painel Filtros visível "
                     "e não mexa no mouse/teclado até terminar.\n\n"
                     "ESC (ou mouse no canto superior esquerdo) para parar.")
        if not messagebox.askokcancel(APP_NOME, texto):
            return
        self.log(("TESTE: " if teste else "") + f"iniciando com {len(p)} venda(s)")
        self.root.iconify()
        threading.Thread(target=rodar_macro, args=(self.cfg, p, teste, self.fila), daemon=True).start()

    def verificar_fila(self):
        try:
            while True:
                tipo, m = self.fila.get_nowait()
                if tipo == "log":
                    self.log(m)
                elif tipo == "atualizacao":
                    self.oferecer_atualizacao(*m)
                elif tipo == "reiniciar":
                    self.root.destroy()
                    return
                elif tipo == "fim":
                    self.log(m.replace("\n", " "))
                    self.root.deiconify()
                    self.root.lift()
                    self.atualizar()
                    (messagebox.showwarning if "PARADO" in m else messagebox.showinfo)(APP_NOME, m)
        except queue.Empty:
            pass
        self.root.after(200, self.verificar_fila)

    # ---- calibração ----
    def calibrar(self):
        if not messagebox.askokcancel(APP_NOME,
                "Calibração dos cliques\n\n"
                "1. Abra o PDV+ (maximizado) em Caixa > menu > Buscar negociações, com o painel Filtros aberto.\n"
                "2. Uma janelinha vai mostrar onde colocar o mouse. Posicione e aperte F8.\n"
                "   (F9 pula os itens opcionais. ESC cancela.)\n"
                "3. Entre um passo e outro, vá clicando no PDV+ normalmente para chegar na próxima tela.\n\n"
                "IMPORTANTE: na confirmação 'Emitir NFC-e?' clique em CANCELAR, não emita."):
            return
        Calibrador(self)

    def configuracoes(self):
        Configuracoes(self)


class Calibrador:
    def __init__(self, app):
        import pyautogui
        import pygetwindow  # noqa
        from pynput import keyboard
        self.app = app
        self.pg = pyautogui
        self.tela = Tela(app.cfg)
        self.idx = 0
        self.novos = {}
        self.fila = queue.Queue()
        os.makedirs(PASTA_CALIB, exist_ok=True)

        self.win = tk.Toplevel(app.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        sh = self.win.winfo_screenheight()
        self.win.geometry(f"560x90+20+{sh - 170}")
        self.win.configure(bg=AZUL)
        self.lbl = tk.Label(self.win, text="", fg="white", bg=AZUL, wraplength=540,
                            font=("Segoe UI", 11, "bold"), justify="left")
        self.lbl.pack(fill="both", expand=True, padx=10, pady=8)
        app.root.iconify()

        def tecla(k):
            if k in (keyboard.Key.f8, keyboard.Key.f9, keyboard.Key.esc):
                self.fila.put(k)
        self.keyboard = keyboard
        self.ouvinte = keyboard.Listener(on_press=tecla)
        self.ouvinte.start()
        self.mostrar()
        self.win.after(100, self.loop)

    def mostrar(self):
        chave, instr, opc, _ = PONTOS[self.idx]
        self.lbl.config(text=f"Passo {self.idx + 1}/{len(PONTOS)}: {instr}\n"
                             f"Mouse em cima e aperte F8{'  (F9 = pular)' if opc else ''}   •   ESC cancela")

    def loop(self):
        try:
            k = self.fila.get_nowait()
        except queue.Empty:
            self.win.after(100, self.loop)
            return
        K = self.keyboard.Key
        chave, _, opc, confere = PONTOS[self.idx]
        if k == K.esc:
            return self.fim(False)
        if k == K.f9:
            if not opc:
                self.win.after(100, self.loop)
                return
            self.novos[chave] = None
        else:
            x, y = self.pg.position()
            ox, oy = self.tela.origem()
            self.novos[chave] = {"x": x - ox, "y": y - oy}
            if confere:
                self.win.withdraw()
                self.win.update()
                time.sleep(0.3)
                self.pg.screenshot(region=(x - 30, y - 12, 60, 24)).save(os.path.join(PASTA_CALIB, f"{chave}.png"))
                self.win.deiconify()
        self.idx += 1
        if self.idx >= len(PONTOS):
            return self.fim(True)
        self.mostrar()
        self.win.after(100, self.loop)

    def fim(self, ok):
        self.ouvinte.stop()
        self.win.destroy()
        self.app.root.deiconify()
        if ok:
            pts = {k: v for k, v in self.novos.items() if v}
            for k, v in self.novos.items():
                if v is None:
                    arq = os.path.join(PASTA_CALIB, f"{k}.png")
                    if os.path.exists(arq):
                        os.remove(arq)
            self.app.cfg["pontos"] = pts
            salvar_config(self.app.cfg)
            self.app.log("Calibração salva.")
            messagebox.showinfo(APP_NOME, "Calibração salva. Agora volte o PDV+ para 'Buscar negociações' e use 'Testar (sem emitir)'.")
        else:
            self.app.log("Calibração cancelada.")


class Configuracoes:
    def __init__(self, app):
        self.app = app
        cfg = app.cfg
        w = self.w = tk.Toplevel(app.root)
        w.title("Configurações")
        w.transient(app.root)
        w.grab_set()
        f = ttk.Frame(w, padding=14)
        f.pack(fill="both", expand=True)

        ttk.Label(f, text="Banco do PDV+ (navi_db.db)").grid(row=0, column=0, sticky="w")
        self.db = ttk.Entry(f, width=70)
        self.db.insert(0, cfg["db_path"])
        self.db.grid(row=1, column=0, columnspan=3, sticky="we")
        ttk.Button(f, text="Procurar...", command=self.procurar).grid(row=1, column=3, padx=4)

        ttk.Label(f, text="Formas de pagamento que entram no faturamento posterior").grid(row=2, column=0, sticky="w", pady=(12, 2))
        self.vars = {}
        fr = ttk.Frame(f)
        fr.grid(row=3, column=0, columnspan=4, sticky="w")
        for i, (cod, nome) in enumerate(fc.FORMAS.items()):
            v = tk.BooleanVar(value=cod in cfg["formas_faturar"])
            self.vars[cod] = v
            ttk.Checkbutton(fr, text=f"{nome} ({cod})", variable=v).grid(row=i // 4, column=i % 4, sticky="w", padx=6)

        ttk.Label(f, text="Tempos (segundos) — aumente se o computador for lento").grid(row=4, column=0, sticky="w", pady=(12, 2))
        self.esperas = {}
        for i, (k, t) in enumerate([("espera_clique", "após cada clique"), ("espera_lista", "lista carregar"),
                                    ("espera_tela", "tela da venda abrir"), ("espera_pos_emissao", "aviso de emissão sumir"),
                                    ("tempo_limite_nota", "limite para a nota sair")]):
            ttk.Label(f, text=t).grid(row=5 + i, column=0, sticky="w")
            e = ttk.Entry(f, width=8)
            e.insert(0, str(cfg[k]))
            e.grid(row=5 + i, column=1, sticky="w")
            self.esperas[k] = e

        ttk.Label(f, text=f"Versão instalada: {VERSAO}").grid(row=10, column=0, sticky="w", pady=(16, 0))
        ttk.Button(f, text="Procurar atualização", command=lambda: app.checar_atualizacao(avisar=True)
                   ).grid(row=10, column=1, columnspan=2, sticky="w", pady=(16, 0))
        ttk.Button(f, text="Salvar", command=self.salvar).grid(row=11, column=3, sticky="e", pady=(16, 0))

    def procurar(self):
        p = filedialog.askopenfilename(filetypes=[("Banco SQLite", "*.db"), ("Todos", "*.*")])
        if p:
            self.db.delete(0, "end")
            self.db.insert(0, os.path.normpath(p))

    def salvar(self):
        cfg = self.app.cfg
        try:
            for k, e in self.esperas.items():
                cfg[k] = float(e.get().replace(",", "."))
        except ValueError:
            messagebox.showerror(APP_NOME, "Tempo inválido.", parent=self.w)
            return
        cfg["db_path"] = self.db.get().strip()
        cfg["formas_faturar"] = [c for c, v in self.vars.items() if v.get()]
        salvar_config(cfg)
        self.w.destroy()
        self.app.atualizar()


def recurso(nome):
    base = getattr(sys, "_MEIPASS", PASTA)
    return os.path.join(base, nome)


def autoteste_ocr():
    """Usado na publicação: o .exe lê um texto desenhado e sai com 0 (ok) ou 1 (falha)."""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (700, 120), (240, 240, 244))
    try:
        fonte = ImageFont.truetype("arial.ttf", 28)
    except OSError:
        fonte = ImageFont.load_default()
    ImageDraw.Draw(img).text((20, 40), "Negociação Nº 006.39 (mesa 3)", fill=(20, 20, 20), font=fonte)
    try:
        ok = ocr.achar_numero(ocr.ler(img), "006.39")[0] is not None
    except Exception:
        ok = False
    sys.exit(0 if ok else 1)


def main():
    if "--teste-ocr" in sys.argv:
        autoteste_ocr()
    root = tk.Tk()
    try:
        root.iconbitmap(default=recurso("aliber.ico"))
    except tk.TclError:
        pass
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
