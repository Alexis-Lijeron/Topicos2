@echo off
cd /d "%~dp0"

echo ================================
echo Activando entorno virtual...
echo ================================
call venv\Scripts\activate

echo.
echo ================================
echo Ejecutando: detectar_intenciones.py
echo ================================
python detectar_intenciones.py

echo.
echo ================================
echo Esperando a que finalice para generar PDFs...
echo ================================
timeout /t 2 > nul

echo.
echo ================================
echo Ejecutando: generar_pdfs_interes_weasyprint.py
echo ================================
python generar_pdfs_interes_weasyprint.py

timeout /t 2 > nul
echo.
echo ================================
echo Ejecutando: enviar_pdfs_email.py
echo ================================
python enviar_pdfs_email.py

echo.
echo ================================
echo ✅ Proceso completo finalizado.
echo ================================
pause
