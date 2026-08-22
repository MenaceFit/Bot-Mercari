@echo off
REM Mercari Sniper - lancement accessible depuis le telephone.
REM Identique a run.bat, mais le dashboard ecoute aussi sur le reseau
REM local : le bot affiche alors une adresse "Depuis ton telephone :
REM http://192.168..." a saisir dans l'application mobile.
REM Le PC et le telephone doivent etre sur le meme Wi-Fi.
call "%~dp0run.bat" --lan %*
