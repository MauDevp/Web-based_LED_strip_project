# Steren · Estudio de luz

Editor visual local de una tira SHOME-1280 de 5 m / 150 LEDs. Interfaz en español, servidor Python sin frameworks y comunicación LAN con TinyTuya 3.5. Las claves permanecen en el servidor.

## Iniciar en Windows

Desde esta carpeta:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe dashboard.py
```

Abra http://127.0.0.1:8765 (use la IP exacta, no localhost). Ctrl+C detiene el servidor. Si el puerto está ocupado, use `--port 8767`. Abrir la página, consultar o reintentar conexión no envía cambios.

La configuración privada `.secrets/device-local.json` se carga junto a `dashboard.py`, sin depender del directorio actual. Contiene `device_id`, `ip`, `local_key`, `version`; no la comparta. Está excluida de Git y del servidor estático.

```powershell
.venv\Scripts\python.exe dashboard.py --demo --port 8766
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

El modo demo no lee secretos ni comunica con hardware.

## Flujo visual

1. Seleccione LEDs mediante clic, Mayús + clic, arrastre o los campos de rango. Flechas mueven el foco; Espacio selecciona, Mayús + flechas amplían.
2. Elija color/brillo y pulse **Pintar en el borrador**. Todavía no cambia el hardware.
3. Acepte el protocolo experimental y aplique selección o diseño completo. Los colores del borrador se agrupan y envían a DP61, con índices 1–150 y bloques de hasta 116 índices/128 bytes.
4. Configure otra sección con otro color. Los índices no seleccionados no se envían.

El dibujo no es lectura física LED por LED. El controlador aceptó un comando de pintura durante una prueba previa, pero el resultado visible y el mapeo LED/segmento aún no fueron confirmados por el usuario. No se debe anunciar soporte físico verificado. Una confirmación del controlador no demuestra el color final. Si falla una aplicación de varios comandos, puede haber cambios parciales y se indica cuántos se confirmaron.

Guardar/cargar diseños usa únicamente `localStorage` del navegador, hasta 10 diseños validados de 150 LEDs; nunca credenciales. Cargar o limpiar el borrador no envía comandos. Las consultas periódicas no sobrescriben el diseño.

## Efectos y límites

Arcoíris, respiración y secuencias se pueden ejecutar en la selección visual (DP61 experimental) o en toda la tira con color uniforme (DP21/24). Máximo 300 segundos y 2 comandos/s en total, serializados. Selecciones de más de 116 LEDs necesitan varios bloques y no cambian simultáneamente. Los efectos requieren mantener el servidor activo, no encienden automáticamente y al detener conservan el último color; no restauran el borrador previo. Cerrar el navegador no detiene un efecto. No hay modo de destellos rápidos.

Las escenas nativas avanzadas DP51 son optativas, experimentales, sin temporizador y con modos numéricos sin interpretación física verificada. Para salir use color general o Apagar; detener software no detiene estas escenas. No se escribe DP46/47 (solo lectura), DP53 (conteo) ni se restablece el equipo.

La barra de conexión muestra la última lectura correcta. Reintentar solo consulta de nuevo la IP configurada, sin reiniciar efectos ni emitir cambios. Si cambió la IP por DHCP, actualice la configuración privada; no se escanea ni cambia IP automáticamente.

## Seguridad

Solo escucha en 127.0.0.1. Host/origen exactos y token aleatorio por proceso para cambios. JSON máximo 16 KiB y validación estricta de entradas. Archivos estáticos con lista permitida; secretos y rutas arbitrarias nunca se sirven. Errores saneados; sin respuestas crudas del controlador. Otros procesos/usuarios confiables del equipo pueden usar el servicio: no exponga puertos ni túneles públicos.

Detener espera una solicitud en curso, y una falla de Wi-Fi puede retrasarlo unos segundos. Aplicar diseños se serializa como operación completa; muchos colores pueden tardar varios segundos. Pruebas automatizadas y QA usan mocks/demo sin cambios reales.

## Referencias del protocolo

[Codificador oficial de pintura de Tuya](https://github.com/Tuya-Community/tuya-ray-materials/blob/main/template/PublicPanelStripLamp/src/devices/protocols/parsers/SmearFormatter.ts), [escenas oficiales](https://github.com/Tuya-Community/tuya-ray-materials/blob/main/template/PublicPanelStripLamp/src/standModel/sceneModel/LocalScene/dpParser/localScene__dreamlight_scene_mode.ts), [configuración Tuya Local con DP51 en base64](https://github.com/make-all/tuya-local/blob/main/custom_components/tuya_local/devices/dreamlight_led_strip.yaml). Pruebas comparan bytes exactos; no sustituyen la verificación física.
