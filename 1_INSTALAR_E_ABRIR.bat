@echo off
chcp 65001 >nul
title Aliber Faturador
cd /d "%~dp0"

rem Procura um Python compativel (prefere 3.13 / 3.12)
set "PY="
for %%V in (3.13 3.12 3.11) do (
  if not defined PY ( py -%%V --version >nul 2>nul && set "PY=py -%%V" )
)
if not defined PY ( python --version >nul 2>nul && set "PY=python" )
if not defined PY (
  echo.
  echo  Python nao encontrado.
  echo  Abra o Prompt de Comando e rode:  py install 3.13
  echo  Depois rode este arquivo de novo.
  echo.
  pause
  exit /b
)
echo Usando: %PY%
echo Instalando/atualizando componentes (so demora na primeira vez)...
%PY% -m pip install --upgrade -q -r requirements.txt
if errorlevel 1 (
  echo Falha ao instalar componentes. Verifique a internet.
  pause
  exit /b
)
echo Abrindo o Faturador... (deixe esta janela preta aberta enquanto usa)
%PY% faturador.py
if errorlevel 1 pause
