# Diana — Facturación y Contabilidad Canopolis

Un solo programa de Windows: **Diana.exe** (doble clic y abre). Se compila en GitHub Actions; en Releases
queda siempre la versión más nueva (Diana.exe y Diana-portable-N.zip).

Parte de Facturación Canopolis v1.61 (todo lo de facturación sigue igual y usa la misma carpeta de datos)
y le agrega la contabilidad:

- **Diario General**: asientos directos (período, fecha, número, auto-reverso, débitos/créditos, subcuentas).
- **Catálogo de cuentas**: Activo, Pasivo, Ingreso, Gasto, Capital (Ganancias retenidas).
- **Año continuo** (sin cierre de año) con **fecha de bloqueo**.
- Cada venta, venta libre, nota de crédito/débito, conduce, compra recibida, cobro, pago a suplidor y ajuste de
  inventario abre la ventana de asiento para que usted elija las cuentas. Un documento = un asiento
  (al editarlo se corrige el mismo; al anularlo se reversa).
- **Importa** toda la contabilidad anterior (catálogo y todos los asientos) y cuadra con sus saldos.
- Reportes PDF: Estado de resultados, Balance General, Balanza de comprobación, Diario General, Mayor.
- Un solo respaldo (.db) en la carpeta que usted elija.
