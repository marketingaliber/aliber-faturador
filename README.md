# Aliber Faturador

Emite NFC-e, depois da venda, das vendas de cartão e Pix que ficaram sem nota no PDV+ (Aliber Sistema).
Lê o banco do PDV+ só para leitura, separa as vendas elegíveis e opera a tela "Buscar negociações"
com um macro, conferindo no banco cada nota emitida.

**Baixar:** [AliberFaturador.exe (última versão)](../../releases/latest/download/AliberFaturador.exe)

Instruções de uso: veja `LEIA-ME.txt`.

## Publicar uma versão nova
1. Altere `VERSAO` em `faturador.py` e descreva as mudanças em `NOVIDADES.txt`.
2. Crie a tag `vX.Y` com o mesmo número. O GitHub compila o .exe e publica a versão;
   os programas instalados se atualizam ao abrir.
