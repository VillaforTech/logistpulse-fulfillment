# 3. Persistir hechos y proyecciones de negocio con outbox y Grafana Live

Date: 2026-09-14

## Status

Accepted

## Context

La creación HTTP y los health checks no prueban la promesa READY <=15s. Publicar directamente a Kafka después de guardar el pedido deja una ventana de pérdida; consumir solo los últimos 20 pedidos omite población de los KPIs. El alcance aprobado de la referencia incluye recuperación y streaming real.

## Decision

Usar [outbox transaccional, inbox y hechos versionados](../events-deber-01.md). Analytics posee una DB y rol separados, bootstrap por API, proyección y offsets atómicos, control de cobertura y timer de 200ms. Publica snapshots por WebSocket a un adapter de Grafana Live nativo. El [gate](../../.github/workflows/ci.yml) exige pruebas de dominio, transacciones, negocio, render y recuperación; el job de diagnóstico no depende del éxito unitario.

Se descartan publicación directa sin outbox y polling de una lista limitada. También se descarta presentar auto-refresh de Grafana como streaming: el benchmark observa el canal WebSocket y el render de la identidad solicitada.

## Consequences

Hay más procesos y estado persistente que operar. Con una partición, una réplica y un worker esta es una referencia de laboratorio, sin promesa de alta disponibilidad. La analítica invalida los KPIs cuando no puede confirmar cobertura/frescura. Los ensayos de rollback, reordenamiento, poison records, timers y reinicios están en [las pruebas PostgreSQL](../../tests/database/test_transactions.py) y [el ensayo de resiliencia](../../scripts/resilience_test.py). Las ejecuciones reales y su resultado se registran aparte de la decisión.
