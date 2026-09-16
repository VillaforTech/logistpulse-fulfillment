# Observabilidad de la referencia

Arrancar desde la raíz con `bash scripts/up.sh`. El wrapper `scripts/compose.sh` combina los manifiestos con un único proyecto aislado `logistpulse-reference`.

- Consola y Grafana por proxy: puerto 28080, enlace **Business KPIs**.
- Grafana directo: puerto 23000, ruta `/grafana/`; credenciales generadas en `.env` privado.
- Prometheus: 29090. Métricas técnicas de APIs, worker, relay, analytics y adapter.
- cAdvisor opcional: `bash scripts/compose.sh --profile resources up -d cadvisor`, puerto 28088.

El dashboard **LOGISTPULSE Business** usa el datasource nativo Grafana Live para L-K1/L-K2/L-K3 y la identidad de sus snapshots. El dashboard Overview heredado conserva los paneles técnicos. Los monitores independientes de disponibilidad/edad usan Prometheus y no se presentan como prueba de streaming.

Ver [contratos y límites de frescura](../docs/kpis-deber-01.md) y [recuperación](../docs/events-deber-01.md). El benchmark con cien observaciones reales guarda su resultado y capturas bajo `artifacts/streaming`.
