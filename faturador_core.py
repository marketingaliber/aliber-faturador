# -*- coding: utf-8 -*-
"""
Aliber Faturador - triagem das vendas (somente LEITURA do banco do PDV+).

Nada aqui grava no banco. A emissão da NFC-e é sempre feita pelo próprio PDV+
(via macro), e este módulo só decide QUAIS vendas devem ser faturadas e confere
depois se a nota foi gerada.
"""
import sqlite3
import time
from datetime import datetime, timedelta

DB_PADRAO = r"C:\PDV+\.dart_tool\sqflite_common_ffi\databases\navi_db.db"

# Códigos de forma de pagamento do PDV+ (conferidos no banco + tela)
FORMAS = {
    0: "Dinheiro",
    1: "Cartão de crédito",
    2: "Crediário",
    3: "Pix",
    4: "Cartão de débito",
    5: "Forma 5",
    6: "Forma 6",
}
FORMAS_FATURAR_PADRAO = [1, 3, 4]  # cartão de crédito, Pix, cartão de débito

# Status da triagem
FATURAR = "FATURAR"
MANUAL = "MANUAL"          # elegível, mas mais antiga que o maior filtro do PDV+ (1 ano)
JA_FATURADA = "Já tem NFC-e"
RECEBIMENTO = "Recebimento de crediário"
NAO_FATURAR = "Não faturar"
CANCELADA = "Cancelada"
ABERTA = "Em aberto"


def conectar(db_path):
    """Abre o banco em modo somente-leitura. Nunca bloqueia o PDV+ por muito tempo."""
    uri = "file:" + db_path.replace("\\", "/") + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def _dt(s):
    if not s:
        return None
    return datetime.fromisoformat(s[:19])


def nome_formas(codigos):
    return " + ".join(FORMAS.get(c, f"Forma {c}") for c in codigos)


DIAS_MAXIMOS = 360  # o maior período do filtro do PDV+ é 1A


def triagem(db_path, data_ini, data_fim, formas_faturar=None):
    """
    Lista as vendas do período (data_ini/data_fim = date) com a classificação.
    Retorna lista de dicts, da mais recente para a mais antiga.
    """
    formas_faturar = set(formas_faturar or FORMAS_FATURAR_PADRAO)
    ini = data_ini.strftime("%Y-%m-%dT00:00:00")
    fim = (data_fim + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")
    con = conectar(db_path)
    try:
        vendas = con.execute(
            """select v.id_venda, v.codigo_dispositivo, v.codigo_venda, v.data_hora_criado,
                      v.data_hora_finalizada, v.valor_total, v.valor_total_liquido_itens,
                      v.valor_total_liquido_recebimentos, v.id_nota_fiscal,
                      v.cancelada_pelo_usuario, v.nome_cliente,
                      coalesce(v.cpf_nota_fiscal, v.cpf_cliente, v.cnpj_cliente) as doc,
                      (select count(*) from venda_item i where i.id_venda = v.id_venda
                          and i.data_hora_deletado is null and coalesce(i.cancelado,0)=0) as n_itens,
                      (select coalesce(m.numero, c.numero, a.identificador)
                         from venda_atendimento a
                         left join mesa_config m on m.id_mesa_config = a.id_mesa_config
                         left join comanda_config c on c.id_comanda_config = a.id_comanda_config
                        where a.id_venda = v.id_venda limit 1) as mesa,
                      (select 1 from venda_atendimento a where a.id_venda=v.id_venda
                          and a.id_mesa_config is not null) as eh_mesa
                 from venda v
                where v.data_hora_deletado is null
                  and v.data_hora_criado >= ? and v.data_hora_criado < ?
                order by v.data_hora_criado desc""",
            (ini, fim),
        ).fetchall()

        res = []
        for v in vendas:
            pags = con.execute(
                """select forma_pagamento, coalesce(valor,0) valor, coalesce(valor_venda,0) vv,
                          coalesce(valor_recebimento,0) vr
                     from venda_forma_pagamento
                    where id_venda = ? and data_hora_deletado is null
                      and coalesce(pagamento_cancelado_pelo_usuario,0) = 0""",
                (v["id_venda"],),
            ).fetchall()
            codigos = []
            for p in pags:
                if p["forma_pagamento"] not in codigos:
                    codigos.append(p["forma_pagamento"])
            criado = _dt(v["data_hora_criado"])
            num_puro = f"{v['codigo_dispositivo']:03d}.{v['codigo_venda']}"
            num = num_puro
            if v["mesa"] is not None:
                num += f" ({'mesa' if v['eh_mesa'] else 'comanda'} {v['mesa']})"

            # ---- classificação (a ordem importa) ----
            if v["id_nota_fiscal"]:
                st, motivo = JA_FATURADA, ""
            elif v["cancelada_pelo_usuario"]:
                st, motivo = CANCELADA, ""
            elif not v["data_hora_finalizada"]:
                st, motivo = ABERTA, "venda ainda não finalizada"
            elif v["n_itens"] == 0 or (v["valor_total_liquido_itens"] or 0) <= 0:
                st, motivo = RECEBIMENTO, "pagamento de conta, sem itens, não leva NFC-e"
            elif not (set(codigos) & formas_faturar):
                st = NAO_FATURAR
                motivo = ("dinheiro" if codigos == [0] else
                          "crediário" if 2 in codigos else
                          "sem pagamento" if not codigos else nome_formas(codigos).lower())
            elif (datetime.now() - criado).days > DIAS_MAXIMOS:
                st, motivo = MANUAL, "venda com mais de 1 ano, fora dos filtros do PDV+"
            else:
                st, motivo = FATURAR, ("pagamento misto" if len(codigos) > 1 else "")

            res.append({
                "id_venda": v["id_venda"],
                "numero": num,
                "num": num_puro,
                "criado": criado,
                "valor": round(v["valor_total"] or 0, 2),
                "formas": nome_formas(codigos) if codigos else "-",
                "codigos": codigos,
                "cliente": v["nome_cliente"] or "",
                "tem_doc": bool(v["doc"]),
                "status": st,
                "motivo": motivo,
            })
        return res
    finally:
        con.close()


def reavaliar(db_path, id_venda):
    """Antes de cada emissão: a venda ainda está sem nota?"""
    con = conectar(db_path)
    try:
        v = con.execute(
            "select id_nota_fiscal, data_hora_criado, data_hora_deletado, cancelada_pelo_usuario "
            "from venda where id_venda=?", (id_venda,)).fetchone()
        if not v or v["data_hora_deletado"] or v["cancelada_pelo_usuario"]:
            return False, "venda excluída ou cancelada"
        if v["id_nota_fiscal"]:
            return False, "já tem NFC-e"
        return True, ""
    finally:
        con.close()


def vendas_com_nota(db_path):
    con = conectar(db_path)
    try:
        return {r[0] for r in con.execute(
            "select id_venda from venda where id_nota_fiscal is not null")}
    finally:
        con.close()


def aguardar_nota(db_path, id_venda, antes, timeout=60):
    """
    Espera a NFC-e aparecer no banco para ESTA venda.
    Retorna (ok, mensagem). Para tudo se outra venda tiver ganhado nota.
    """
    limite = time.time() + timeout
    while time.time() < limite:
        time.sleep(1.5)
        try:
            con = conectar(db_path)
            try:
                r = con.execute(
                    """select n.situacao, n.is_pendente, n.is_offline, n.numero, n.serie, n.x_msg
                         from venda v join nota_fiscal n on n.id_nota_fiscal = v.id_nota_fiscal
                        where v.id_venda = ?""", (id_venda,)).fetchone()
                agora = {x[0] for x in con.execute(
                    "select id_venda from venda where id_nota_fiscal is not null")}
            finally:
                con.close()
        except sqlite3.OperationalError:
            continue  # banco ocupado pelo PDV+, tenta de novo
        novas = agora - antes - {id_venda}
        if novas:
            return False, ("PARADA DE SEGURANÇA: outra venda recebeu nota durante a emissão "
                           f"({len(novas)}). Confira no PDV+ antes de continuar.")
        if r:
            if r["situacao"] == 1 and not r["is_pendente"]:
                extra = " (contingência offline)" if r["is_offline"] else ""
                return True, f"NFC-e nº {r['numero']} série {r['serie']} autorizada{extra}"
            if r["is_pendente"] or r["is_offline"]:
                return True, f"NFC-e nº {r['numero']} gerada em contingência/pendente, conferir depois"
            return False, f"NFC-e gerada com situação {r['situacao']}: {r['x_msg'] or 'sem mensagem'}"
    return False, "a nota não apareceu no banco dentro do tempo limite, confira a tela do PDV+"
