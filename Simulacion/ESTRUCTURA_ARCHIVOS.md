# Estructura Estandarizada de Gestión de Archivos

## Descripción General

El sistema ha sido mejorado para proporcionar un manejo robusto y consistente de todos los archivos generados durante la simulación. Se mantiene un registro detallado de cada operación y su estado a través de toda su vida en el sistema.

## Estructura de Directorios

```
Estados_Simulacion/
├── operaciones/          # Registro de operaciones por estado
│   ├── 00_Operaciones_En_Curso.csv
│   ├── 01_Operaciones_Realizadas.csv
│   ├── 02_Operaciones_Pendientes.csv
│   └── 03_Operaciones_Canceladas.csv
│
├── recursos/             # Estado de recursos hospitalarios
│   ├── Estado_Camas.csv
│   └── Resumen_Ocupacion.csv
│
├── logs/                 # Logs y registros detallados
│   ├── Auditoria_Cambios_Estado.csv
│   ├── Resumen_Dia_YYYY-MM-DD.csv
│   ├── Operaciones_Realizadas_YYYY-MM-DD.csv
│   └── Operaciones_Canceladas_YYYY-MM-DD.csv
│
└── snapshots/            # Snapshots periódicos del estado
    └── Snapshot_YYYYMMDDhhmmss.csv
```

## Estados de Operación

Cada operación transita por estos estados:

1. **PROGRAMADA**: Operación registrada pero no ha iniciado
2. **EN_CURSO**: Operación en ejecución en el pabellón
3. **REALIZADA**: Operación completada exitosamente
4. **CANCELADA**: Operación cancelada (por cualquier motivo)

## Archivos Generados

### `/operaciones/`

Estos archivos contienen el estado actual de todas las operaciones agrupadas por estado:

#### `00_Operaciones_En_Curso.csv`
Operaciones actualmente en ejecución en pabellones.

**Columnas estandarizadas:**
- Correlativo
- Servicio
- Descripción
- Pabellón
- Día, Hora inicio, Hora fin
- Prioridad, Requiere UCI
- Duración agendada (min)
- Permanencia
- Fecha inicio dt, Fecha fin dt
- Estado
- Timestamp Estado

#### `01_Operaciones_Realizadas.csv`
Todas las operaciones completadas exitosamente.

#### `02_Operaciones_Pendientes.csv`
Operaciones programadas que aún no han iniciado.

#### `03_Operaciones_Canceladas.csv`
Todas las operaciones que fueron canceladas con motivo.

### `/recursos/`

#### `Estado_Camas.csv`
Registro de todas las camas ocupadas actualmente:

| Inicio | Fin | Tipo | Estado |
|--------|-----|------|--------|
| 2026-01-01 09:15:00 | 2026-01-03 08:00:00 | Básica | Ocupada |
| 2026-01-01 10:30:00 | 2026-01-02 08:00:00 | UCI | Ocupada |

#### `Resumen_Ocupacion.csv`
Snapshot actualizado periódicamente con tasas de ocupación:

| Timestamp | Camas_Basicas_Ocupadas | Camas_Basicas_Total | Camas_UCI_Ocupadas | Camas_UCI_Total | Tasa_Ocupacion_Basicas | Tasa_Ocupacion_UCI |
|-----------|------------------------|---------------------|-------------------|-----------------|-----------------------|--------------------|

### `/logs/`

#### `Auditoria_Cambios_Estado.csv`
Registro completo de TODOS los cambios de estado de cada operación:

| Correlativo | Timestamp | Estado anterior | Estado nuevo | Motivo |
|-------------|-----------|-----------------|--------------|--------|
| OP-001 | 2026-01-01 09:15:00 | PROGRAMADA | EN_CURSO | Inicio de operación |
| OP-001 | 2026-01-01 10:45:00 | EN_CURSO | REALIZADA | Operación completada |
| OP-002 | 2026-01-01 09:30:00 | PROGRAMADA | CANCELADA | falta camas |

#### `Resumen_Dia_YYYY-MM-DD.csv`
Resumen ejecutivo del día:

| Fecha | Hora_Corte | Operaciones_Realizadas_Hoy | Operaciones_Canceladas_Hoy | Operaciones_Pendientes | Camas_Ocupadas | UCI_Ocupadas | Capacidad_Camas | Capacidad_UCI | Modo_Saturado |
|-------|-----------|-------|---------|----------|--------|-------|---------|-------|------|
| 2026-01-01 | 20:00 | 15 | 2 | 35 | 72 | 2 | 75 | 2 | true |

#### `Operaciones_Realizadas_YYYY-MM-DD.csv`
Detalle de operaciones realizadas en ese día específico.

#### `Operaciones_Canceladas_YYYY-MM-DD.csv`
Detalle de operaciones canceladas en ese día específico.

### `/snapshots/`

#### `Snapshot_YYYYMMDDhhmmss.csv`
Captura de estado en un momento específico:

| Timestamp | Operaciones_Realizadas | Operaciones_Pendientes | Operaciones_En_Curso | Operaciones_Canceladas | Camas_Ocupadas | UCI_Ocupadas | Modo_Saturado |
|-----------|-------|---------|----------|---------|--------|-------|------|

## Flujo de Datos y Consistencia

### Inicialización
```
Dataframe Original
    ↓
Creación de operaciones_estado{} con estado PROGRAMADA
    ↓
Registro en auditoria_cambios[] (estado inicial)
```

### Durante Simulación
```
Evento de Inicio
    ↓
cambiar_estado_operacion(correlativo, EN_CURSO, "Inicio de operación")
    ↓
Registrar en auditoria_cambios[] y actualizar timestamp_estado
    ↓
Operación visible en 00_Operaciones_En_Curso.csv
```

### Finalización/Cancelación
```
Evento de Fin (Éxito)
    ↓
cambiar_estado_operacion(correlativo, REALIZADA)
    ↓
Registrar en auditoria_cambios[] y fecha_alta
    ↓
Operación visible en 01_Operaciones_Realizadas.csv

O

Cancelación
    ↓
registrar_cancelacion(row, motivo)
    ↓
cambiar_estado_operacion(correlativo, CANCELADA, motivo)
    ↓
Registrar en auditoria_cambios[]
    ↓
Operación visible en 03_Operaciones_Canceladas.csv
```

## Funciones de Exportación

### `exportar_estado()`
Exporta estado completo (llamada al pausar, fin del día, fin de simulación)

Ejecuta:
1. `exportar_operaciones_en_curso()` → 00_Operaciones_En_Curso.csv
2. `exportar_operaciones_realizadas()` → 01_Operaciones_Realizadas.csv
3. `exportar_operaciones_pendientes()` → 02_Operaciones_Pendientes.csv
4. `exportar_operaciones_canceladas()` → 03_Operaciones_Canceladas.csv
5. `exportar_auditoria()` → Auditoria_Cambios_Estado.csv
6. `exportar_resurcos_hospitalarios()` → Estado_Camas.csv + Resumen_Ocupacion.csv
7. `exportar_snapshot_completo()` → Snapshot_timestamp.csv

### `exportar_resumen_dia()`
Ejecuta:
1. Cuenta operaciones del día
2. Crea Resumen_Dia_YYYY-MM-DD.csv
3. Exporta Operaciones_Realizadas_YYYY-MM-DD.csv
4. Exporta Operaciones_Canceladas_YYYY-MM-DD.csv

## Puntos de Exportación en el Ciclo

1. **Cada pausa manual**: Exporta estado completo
2. **Fin del día (20:00)**: Exporta resumen diario + estado completo
3. **Fin de simulación**: Exporta estado completo + snapshots finales
4. **Antes de reprogramación**: Exporta estado para auditoría

## Beneficios de la Estructura

✓ **Trazabilidad Completa**: Cada cambio de estado está registrado
✓ **Consistencia**: Esquema estandarizado de columnas
✓ **Auditoría**: Log detallado de motivos de cancelaciones
✓ **Facilita Reprogramación**: Registros claros de qué fue/no fue realizado
✓ **Análisis**: Datos organizados para reportes y análisis posterior

## Ejemplo de Análisis Posible

Usando estos archivos puedes:

1. **Identificar cuellos de botella**: Comparar fechas de cancelaciones
2. **Análisis de capacidad**: Revisar Resumen_Ocupacion.csv
3. **Auditar decisiones**: Consultar Auditoria_Cambios_Estado.csv
4. **Validar reprogramación**: Comparar estado antes/después
5. **Métricas de desempeño**: KPIs por día usando Resumen_Dia_*.csv

## Consideraciones para la Reprogramación

Cuando se implemente la reprogramación:

1. Se preservan los estados EN_CURSO y REALIZADA
2. CANCELADA se marca con timestamp y motivo
3. Las nuevas operaciones inician como PROGRAMADA
4. Se mantiene registro en Auditoria_Cambios_Estado.csv
5. Se puede auditar el antes/después comparando snapshots

