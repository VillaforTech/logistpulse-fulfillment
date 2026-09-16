# Contratos de eventos y recuperación

## Fuente y propietarios

Fulfillment posee `fulfillment_db`: pedidos, inbox de comandos y outbox. Analytics posee `analytics_db` con rol restringido: proyección, hechos procesados, checkpoints, huecos, cuarentena, snapshots e historial de alertas. Analytics lee el API público y Kafka; no se conecta a las tablas del dominio. Inventario, distribución y operaciones conservan sus almacenes.

| Canal | Contrato |
| --- | --- |
| `logistpulse.orders` | comando `PREPARE_ORDER`, commandId estable, orderId, correlationId y fixtureRunId. Se conserva compatibilidad con el payload legado `event:ORDER_CREATED`. Grupo `kitchen-worker`. |
| `logistpulse.fulfillment.events.v1` | hechos `ORDER_CREATED`, `ORDER_PREPARING`, `ORDER_READY`. Grupo `logistpulse-business-analytics-v1`. Key = aggregateId. |

Los nombres corresponden respectivamente a pedido aceptado, preparación iniciada y pedido listo descritos en los issues. No se envían comandos a la proyección como si fueran hechos.

Envelope: `schemaVersion:1`, eventId UUID, eventType, aggregateType `Order`, aggregateId, aggregateVersion entero ≥1, occurredAt UTC ISO8601, correlationId, fixtureRunId opcional, sourcePosition y `data` con snapshot completo. Data incluye orderId, storeId, channel, total como string decimal de dos posiciones, unit `DEMO`, status, createdAt, updatedAt, readyAt nullable, version, correlationId y fixtureRunId. El schema rechaza timestamps incoherentes y READY sin readyAt; **un READY tardío sigue siendo un hecho válido** y no se elimina para mejorar el KPI.

## Orden de los commits

1. API/worker guarda cambio del agregado y hecho de outbox en la misma transacción PostgreSQL; creación incluye el comando de cocina.
2. Relay toma registros pendientes, publica con key y acks=all, y confirma el checkpoint de publicación solo después del ack. Un corte puede repetir un eventId; no perder silenciosamente la transición.
3. Worker confirma su offset Kafka después del commit de estado/inbox. Un reinicio en PREPARING retoma el tiempo persistido; READY repetido no cambia versión ni readyAt.
4. Analytics valida y guarda hecho, proyección, gaps y offset en una transacción. Luego confirma Kafka. Al asignar de nuevo una partición restaura el offset de su DB. Repetidos exactos no cambian el estado; payload distinto con el mismo eventId o la misma identidad/versión se pone en cuarentena. JSON malformado también se persiste en cuarentena con checkpoint, evitando un poison loop.
5. Las versiones antiguas no revierten una proyección nueva. Los huecos se cierran cuando llega la versión faltante. Conflictos requieren diagnóstico explícito; el laboratorio no incluye un botón que borre cuarentena para ponerlo verde.

Bootstrap usa un snapshot completo del API con corte REPEATABLE READ y watermark. Los heartbeats posteriores comparan pending, posición, número de agregados y digest ordenado de `(orderId,version)`; no se usa solo un máximo que podría ocultar un pedido entero perdido. Si el legado tenía READY sin readyAt, el bootstrap declara cobertura incompleta y no fabrica la fecha.

Snapshot v1: snapshotId UUID, computedAt, revision, sourceEventId, sourceAggregateId/version, correlationId, watermark de offsets y posición, quality, coverage, kpis, alertas, deadlineSeconds y windowSeconds. Un timer persistente deriva plazos del estado; no depende de recibir otro evento. El adapter reconecta mediante snapshot REST + WS, ignora revisiones viejas y publica STALE durante una desconexión. Broker, PostgreSQL y Grafana mantienen volúmenes exclusivos del gemelo.

## Verificación

`tests/database/test_transactions.py` ejecuta PostgreSQL real en esquemas temporales: commit y rollback de API/outbox; publish parcialmente exitoso y repetición; comandos repetidos; reinicio en PREPARING; orden inverso, gap y replay; identidad contradictoria; JSON inválido; fallo antes de checkpoint; cobertura por población/digest/pending/lag; activación y resolución de alertas.

`resilience_test.py` corta el broker mientras el API confirma un pedido, comprueba calidad inválida y recuperación; reinicia analytics y verifica continuidad de revisión/población/readyAt; reinicia adapter y Grafana. `browser-test.mjs` observa el canal Live nativo y la identidad renderizada para 100 operaciones con pérdida explícita y p50/p95/máximo. Los resultados están en `artifacts/`; no se sustituyen con capturas generadas.
