@echo off
chcp 65001 > nul
title RO Auto Answer Helper (Liquid Glass Edition)
echo ===================================================
echo   กำลังเริ่มต้นโปรแกรม RO Trivia Helper...
echo ===================================================
echo.
py app.py
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] ไม่สามารถเริ่มโปรแกรมได้
    pause
)