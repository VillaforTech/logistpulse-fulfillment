# Reproducción del gemelo en Codespaces

Usar este repositorio `LOGISTPULSE-GOLDEN_2-REFERENCE`, con la rama de referencia indicada por el PR. No abrir el original para estos comandos. El devcontainer declara Python 3.12, Node 22, Docker/Compose y GitHub CLI. Al crear el contenedor genera `.env` privado y la URL del puerto 28080; no publica credenciales.

```bash
bash scripts/up.sh
bash scripts/smoke.sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest tests/unit -q
mkdir -p artifacts
bash scripts/compose.sh run --rm --build database-tests
.venv/bin/python scripts/business_test.py
npm ci
npx playwright install --with-deps chromium
npm run browser-test
.venv/bin/python scripts/resilience_test.py
.venv/bin/python scripts/verify_evidence.py
bash scripts/capture.sh
# Solo después de guardar evidencia:
bash scripts/down.sh
```

Abrir el puerto **28080** en Ports para consola y `/grafana/d/logistpulse-business/logistpulse-business`. Puertos adicionales: Grafana 23000, Prometheus 29090, cAdvisor opcional 28088. Mantener la visibilidad privada del Codespace. El usuario/clave de Grafana están en `.env` del propio Codespace. `PUBLIC_BASE_URL` debe ser la URL pública de su puerto28080; el configurador la deduce de las variables de Codespaces.

Usar `scripts/compose.sh` como único wrapper: combina aplicación/observabilidad y aísla red y volúmenes con `logistpulse-reference`. No levantar los dos manifiestos por separado. Las pruebas de navegador se ejecutan en el contenedor contra `localhost:28080`; no requieren exponer puertos ni modificar permisos de GitHub.

Guardar SHA probado, logs y reportes. La configuración no prueba que una persona haya reproducido el laboratorio: esa evidencia se registra por separado cuando ocurra. Hasta entonces la reproducción en Codespaces permanece pendiente.
