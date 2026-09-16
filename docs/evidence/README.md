# Evidencia conservada en el repositorio

## Corrida local sana — d283f62

[Mediciones completas](local-d283f62/measurements.json) · [100 filas CSV](local-d283f62/latency-100.csv) · [fuente al terminar](local-d283f62/source-after.json) · [contrato de negocio](local-d283f62/business.json) · [recuperación](local-d283f62/resilience.json).

El 14 de septiembre de 2026 se observaron **100 de 100 operaciones**, sin pérdidas. p50: **478.1 ms**; p95: **619.1 ms**; máximo: **1143.7 ms**. La observación mayor a un segundo está incluida; el criterio aprobado es p95 < 1000 ms. Cada observación contiene las tres tarjetas con campos diferentes, valores finitos, calidad FRESH, la misma revisión/evento y la correlación del pedido. Una reconstrucción independiente desde `source-after.json` confirmó los valores de las 300 tarjetas en su `computedAt`, incluidas las cohortes que cambian con el tiempo.

Entorno registrado: macOS ARM64, Node 26.0.0, Chromium 140.0.7339.186, locale en-US; Docker con 12 CPU y 8,217,751,552 bytes de memoria. El tiempo de preparación de cuatro segundos se espera entre operaciones y no se descuenta de la latencia de render. Esta latencia se mide con `performance.now()` desde la invocación del API hasta las tres tarjetas coherentes, revalidadas después de dos frames.

El ensayo separado cruzó el vencimiento real de 15 segundos sin recibir otro hecho: el primer snapshot incumplido observado se calculó **19.23 ms** después del límite y se renderizó **88.33 ms** después. Ambos cumplen <=1000 ms. Para esa comparación se usa el reloj UTC compartido por servicios y navegador en este host; los valores sin redondear y los timestamps están en el JSON.

Navegador offline, adapter detenido y Grafana detenido invalidaron las tarjetas. Los tres recuperaron una revisión FRESH nueva **sin recargar la página**. El ensayo de recuperación de backend también verificó digest, cantidad de agregados/eventos, posición de fuente y `readyAt` persistidos tras reiniciar analytics y recuperarse de una caída del broker.

## Capturas reales

- [Vencimiento real](local-d283f62/grafana-real-deadline.png): backlog de 25.50 DEMO y deuda positiva, con datos FRESH.
- [Adapter detenido](local-d283f62/grafana-adapter-stale.png): tarjetas STALE aunque la página siga abierta.
- [Vista FRESH después de los ensayos](local-d283f62/grafana-fresh-after-tests.png): captura diagnóstica adicional en un contexto nuevo. La recuperación sin reload se acredita con las revisiones del JSON de medición.

La foto tomada inmediatamente después de las 100 operaciones coincidió con un INCOMPLETE breve de la última transición READY; el original se conserva en los artifacts locales y no se presenta como una captura FRESH. En el harness posterior, la foto final espera también una revisión coherente FRESH. El screenshot inmediato tras reiniciar Grafana conserva un aviso 502 residual del corte; sus tarjetas ya habían recibido una revisión FRESH. Ninguna captura fue generada o retocada.

Esa captura inmediata también mostró SIN CONEXIÓN con fondo verde en dos monitores técnicos. El dashboard posterior asigna gris a valores ausentes/NaN y al umbral base, y usa la última muestra incluso si es nula. La captura limpia adicional muestra los monitores ya recuperados; no se retocó el original para ocultar el fallo de presentación.

L-K1 puede seguir en alerta después de recuperar la infraestructura: el pedido del ensayo de vencimiento terminó tarde y conserva ese incumplimiento hasta salir de la ventana. L-K2 y L-K3 vuelven a cero cuando se vacía el backlog. No se eliminaron pedidos ni se cambiaron sus tiempos para producir ceros.

## Pruebas y procedencia

La corrida d283f62 pasó 54 pruebas Python en PostgreSQL (incluyen 35 unitarias), 7 pruebas ADR, smoke técnico, negocio, streaming y resiliencia. [JUnit PostgreSQL](local-d283f62/database-tests.xml) · [JUnit unitario](local-d283f62/unit.xml) · [verificación del formateo sin cambio de AST](local-d283f62/format-ast.json).

Una revisión posterior endureció el oráculo frente a NaN, infinito, campos duplicados/desconocidos y observaciones inconsistentes. Sus [6 regresiones Node](local-d283f62/browser-oracle-hardening.tap) se ejecutaron después de la corrida local; no se atribuyen al SHA d283f62. El validador endurecido volvió a aceptar las 100 filas conservadas. CI ejecuta ese oráculo sobre el SHA exacto del PR.

Los [intentos locales anteriores](local-d283f62/prior-attempts.json) siguen identificados como fallos: 4/100 observaciones por un await incorrecto del harness; posteriormente 100/100 con p95 1078 ms antes de optimizar la confirmación de cobertura. No se mezclan con el resultado sano.

El archivo de medición exportado conserva todos los tiempos y las identidades. Solo se reemplazaron los mensajes WebSocket de conexión por los nombres de canales de suscripción observados, para no publicar envelopes de autenticación. El SHA del original está registrado en `artifactTransformations`; [SHA256 de la selección](local-d283f62/sha256.json) permite comprobar sus archivos. Son pedidos sintéticos de laboratorio.

## GitHub

[PR de implementación #1](https://github.com/VillaforTech/LOGISTPULSE-GOLDEN_2-REFERENCE/pull/1). El [run sano inicial 34886316963](https://github.com/VillaforTech/LOGISTPULSE-GOLDEN_2-REFERENCE/actions/runs/34886316963) ejecutó d283f62 y pasó todos los jobs, incluido Release gate. El recorrido controlado rojo y el PR correctivo se documentan en [Deber 01](../deber-01.md) cuando su estado se observa.

La corrida Ubuntu también observó 100/100 operaciones sin pérdidas: p50 **438.0 ms**, p95 **597.7 ms**, máximo **650.6 ms**. Su vencimiento real se detectó en **116.06 ms** y se renderizó en **251.44 ms**; las tres reconexiones pasaron. Entorno registrado: Linux x64, Node 22.23.2, Chromium 140, Docker con 4 CPU y 16,765,374,464 bytes. Se conservan [run y jobs](ci-d283f62/run.json), [mediciones](ci-d283f62/measurements.json), [negocio](ci-d283f62/business.json), [resiliencia](ci-d283f62/resilience.json) y [decisión PASS del gate](ci-d283f62/release-gate.json).

Esta evidencia no acredita por sí sola merge, aprobación de un docente ni envío a D2L. La reproducción desde un clon limpio/devcontainer se registra por separado cuando termine.
