# LOGISTPULSE — referencia del Deber 01

Gemelo académico de [LOGISTPULSE-GOLDEN_2](https://github.com/VillaforTech/LOGISTPULSE-GOLDEN_2), creado desde `d35fded`. El original conserva el trabajo compartido del equipo. Esta referencia añade una solución verificable al contrato de fulfillment y a los tres KPIs de negocio; conserva los dominios y el smoke test técnico del laboratorio.

Esta implementación de referencia es de Roberto Villafuerte, con asistencia de Codex. Se conserva el historial del repositorio original. Las asignaciones de sus issues indican responsabilidades planificadas; no acreditan por sí mismas aportes de los compañeros a esta referencia.

## Arranque aislado

Requisitos: Docker con Compose v2, Python 3.12 y Node 22 para las pruebas de navegador. En Codespaces se prepara `.env` automáticamente. Las credenciales locales se generan sin publicarlas; se pueden consultar en ese archivo privado para entrar a Grafana.

```bash
python3 scripts/configure.py
bash scripts/up.sh
bash scripts/smoke.sh
# Ejecutar down SOLO al terminar también las pruebas de la sección siguiente.
# Conserva los volúmenes y su evidencia:
bash scripts/down.sh
```

| Superficie | Puerto / ruta |
| --- | --- |
| Consola | <http://localhost:28080> |
| KPIs nativos Grafana Live | <http://localhost:28080/grafana/d/logistpulse-business/logistpulse-business> |
| Grafana directo | <http://localhost:23000/grafana/> |
| Prometheus | <http://localhost:29090> |
| cAdvisor opcional | `bash scripts/compose.sh --profile resources up -d cadvisor` → puerto 28088 |

En Codespaces abrir el puerto **28080**; `scripts/configure.py` detecta su URL para el proxy de Grafana. El nombre de proyecto `logistpulse-reference` aísla red y volúmenes. Usar siempre `scripts/compose.sh` para combinar los dos manifiestos. No usar los comandos/puertos del original en este gemelo.

```bash
curl -fsS http://localhost:28080/api/fulfillment/orders \
  -H 'Content-Type: application/json' -H 'X-Correlation-ID: ejemplo-01' \
  -d '{"storeId":"STORE-042","channel":"DEMO","total":"25.50","fixtureRunId":"ejemplo-01"}'
# Consultar el orderId devuelto, sin depender de la lista de últimos 20:
curl -fsS http://localhost:28080/api/fulfillment/orders/ORD_ID_DEVUELTO
curl -fsS http://localhost:28080/api/business/snapshot
```

`25.50 DEMO` es una cantidad simulada, sin moneda real. Se almacena como decimal y se publica como string; `total` numérico en el API existe por compatibilidad, `totalExact` es la representación exacta.

## Comprobar el contrato completo

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest tests/unit -q
node --test tests/browser/*.test.mjs
mkdir -p artifacts
bash scripts/compose.sh run --rm --build database-tests
.venv/bin/python scripts/business_test.py
npm ci
npx playwright install --with-deps chromium # Linux / Codespaces
# En macOS: npx playwright install chromium
npm run browser-test
.venv/bin/python scripts/resilience_test.py
.venv/bin/python scripts/verify_evidence.py
bash scripts/capture.sh
```

El benchmark realiza **100 pedidos secuenciales** y tarda aproximadamente 8 minutos porque respeta la preparación de cuatro segundos. Mide con el reloj del navegador desde la llamada al API hasta las tres tarjetas Grafana con identidad, revisión, calidad y valores coherentes, revalidados tras dos frames de render; cuenta pérdidas. El gate exige 100/100 observaciones y p95 < 1 s. El tiempo de preparación no se confunde con la latencia de visualización.

El contexto del navegador fija `en-US`, un identificador BCP 47 válido, para que Chromium no herede un locale POSIX del runner que Grafana no pueda interpretar. Las cantidades del panel mantienen formato explícito `es-EC`. Los reportes registran versión del navegador, idioma observado, errores de consola/red, SHA y recursos de Docker.

Las pruebas PostgreSQL usan esquemas temporales `test_<uuid>` y eliminan únicamente esos esquemas. El ensayo de resiliencia detiene/reinicia servicios **del gemelo** y deja su pedido de prueba en el historial. No debe ejecutarse sobre un despliegue ajeno.

## Decisiones y evidencia

- [Resultados conservados: 100/100, p95 619.1 ms, vencimiento y recuperación](docs/evidence/README.md).
- [Contrato de negocio, fórmula y bordes](docs/kpis-deber-01.md).
- [Hechos, outbox, checkpoints y calidad](docs/events-deber-01.md).
- [Entrega y recorrido rojo → verde](docs/deber-01.md).
- [ADR Tools local y validación](docs/adr/README.md).
- [ADR de esta implementación](docs/adr/0003-persistir-hechos-y-proyecciones-de-negocio-con-outbox-y-grafana-live.md).

| Issue del original | Implementación de referencia | Pruebas / evidencia |
| --- | --- | --- |
| [#1 Capability, dominio y eventos](https://github.com/VillaforTech/LOGISTPULSE-GOLDEN_2/issues/1) | `services/logist/{domain,fulfillment,storage,relay,worker}.py` | `tests/unit/test_domain.py`, transacciones e inbox/outbox, `scripts/business_test.py` |
| [#2 Proyección, KPIs y timers](https://github.com/VillaforTech/LOGISTPULSE-GOLDEN_2/issues/2) | `domain.py`, `analytics.py` | cobertura, deduplicación, timers e historial en `tests/database/test_transactions.py` |
| [#3 Streaming y panel](https://github.com/VillaforTech/LOGISTPULSE-GOLDEN_2/issues/3) | `live.py`, dashboard y plugin Business, consola | `scripts/browser-test.mjs`, `scripts/resilience_test.py` |
| [#4 CI y release gate](https://github.com/VillaforTech/LOGISTPULSE-GOLDEN_2/issues/4) | `.github/workflows/ci.yml` | artifacts unit, technical, business-and-streaming |
| [#5 Reproducción y entrega](https://github.com/VillaforTech/LOGISTPULSE-GOLDEN_2/issues/5) | esta guía, `.devcontainer/`, `docs/deber-01.md` | registros reales de ejecución y PRs; no equivalen a entrega D2L |

`Release gate` exige architecture, unit, integration y business-lab exitosos. El laboratorio de negocio corre aunque fallen las pruebas unitarias; conserva evidencia antes de apagar. No se permite convertir errores, cancelaciones ni etapas omitidas en PASS. La rama principal del gemelo requiere PR y este contexto; la implementación no cambia las protecciones del original.

Los archivos heredados `services/fulfillment-api` y `services/fulfillment-worker` se conservan como procedencia. El runtime activo está en `services/logist`; Compose indica los comandos exactos. El panel Overview técnico muestra solo los últimos 20 pedidos; los KPIs consultan toda la proyección.
