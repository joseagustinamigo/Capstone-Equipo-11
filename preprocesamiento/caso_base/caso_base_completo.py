"""
================================================================================
CASO BASE / POLÍTICA MIOPE - Asignación de tablas quirúrgicas
ICS2122 - Taller de Investigación Operativa - Grupo 11 - Entrega 2
================================================================================

SCRIPT ENFOCADO EN EXPORTACIÓN EXCLUSIVA A CSV:
  1. Ejecuta la heurística greedy de asignación (Fijando pabellón original).
  2. Genera un archivo CSV limpio con el formato y orden estricto de columnas.

Todos los outputs se guardan en la carpeta 'outputs/' creada al lado de este script.
"""

import os
from collections import defaultdict
import pandas as pd
import numpy as np

# ============================================================================
# 1. PARÁMETROS 
# ============================================================================

# Ruta al Excel de datos. Cambiar si el archivo está en otra ubicación.
ARCHIVO_DATOS = os.path.join("preprocesamiento", "Datos", "Datos Operaciones y lista de espera.xlsx")

# Carpeta de salida (se crea automáticamente si no existe)
CARPETA_OUTPUTS = 'outputs'

# Convención de prioridad: True si 5 = mayor, False si 1 = mayor.
PRIORIDAD_MAX_ES_MAYOR = False   # 1 = mayor prioridad

# Parámetros del problema
DIAS = 7                                # horizonte: 1 semana hábil
PABELLONES = list(range(1, 9))          # 8 pabellones
HORA_INICIO = 8 * 60                    # 8:00 en minutos
HORA_FIN = 18 * 60                      # 18:00 en minutos
CAMAS_HOSPITALIZACION = 75
CAMAS_UCI = 2

# Ventanas horarias por servicio (en minutos desde medianoche)
RESTRICCIONES_HORARIAS = {
    'ENT':           [(10*60, 15*60)],
    'General':       [(HORA_INICIO, HORA_FIN)],
    'OBGYN':         [(8*60, 13*60)],
    'Ophthalmology': [(8*60, 13*60)],
    'Orthopedics':   [(8*60, 11*60), (14*60, 18*60)],
    'Pediatrics':    [(8*60, 11*60), (14*60, 18*60)],
    'Plastic':       [(11*60, 16*60)],
    'Podiatry':      [(8*60, 13*60), (14*60, 17*60)],
    'Urology':       [(HORA_INICIO, HORA_FIN)],
    'Vascular':      [(8*60, 11*60), (15*60, 18*60)],
}


# ============================================================================
# 2. CARGA Y PREPARACIÓN DE DATOS
# ============================================================================

def cargar_datos():
    """Carga lista de espera y estima días de permanencia desde datos base."""
    db = pd.read_excel(ARCHIVO_DATOS, sheet_name='Datos base')
    le = pd.read_excel(ARCHIVO_DATOS, sheet_name='Lista de espera').copy()

    estim = (db.groupby(['Servicio', 'Descripción'])['Días de permanencia programados']
               .mean().round().astype(int).reset_index()
               .rename(columns={'Días de permanencia programados': 'dias_estimados'}))

    le = le.merge(estim, on=['Servicio', 'Descripción'], how='left')
    le['dias_estimados'] = le['dias_estimados'].fillna(
        le['Servicio'].map(db.groupby('Servicio')['Días de permanencia programados']
                             .mean().round().astype(int))
    ).fillna(0).astype(int)

    return le, db


# ============================================================================
# 3. UTILIDADES DE FACTIBILIDAD
# ============================================================================

def ventanas_factibles(servicio, duracion):
    """Ventanas horarias en las que cabe una cirugía de la duración dada."""
    return [(vi, vf) for vi, vf in RESTRICCIONES_HORARIAS[servicio]
            if vf - vi >= duracion]


def encontrar_hora_inicio(ocupacion_pabellon, ventanas, duracion):
    """Busca el primer hueco factible. Retorna hora inicio o None."""
    for vi, vf in ventanas:
        cursor = vi
        for ini, fin in ocupacion_pabellon:
            if fin <= cursor:
                continue
            if ini >= cursor + duracion and ini <= vf:
                return cursor
            cursor = max(cursor, fin)
            if cursor + duracion > vf:
                break
        else:
            if cursor + duracion <= vf:
                return cursor
    return None


def insertar_ordenado(lista_intervalos, intervalo):
    lista_intervalos.append(intervalo)
    lista_intervalos.sort()


# ============================================================================
# 4. HEURÍSTICA GREEDY DE ASIGNACIÓN
# ============================================================================

def asignar_greedy(lista_espera):
    """Política miope: ordena por prioridad y SPT, asigna ÚNICAMENTE al pabellón por defecto."""
    df = lista_espera.copy()
    if PRIORIDAD_MAX_ES_MAYOR:
        df['_prio_orden'] = -df['Prioridad de paciente']
    else:
        df['_prio_orden'] = df['Prioridad de paciente']
    df = df.sort_values(['_prio_orden', 'Duración agendada (min)'],
                        ascending=[True, True]).reset_index(drop=True)

    # Estructuras de control originales
    ocupacion = {d: {p: [] for p in PABELLONES} for d in range(DIAS)}
    camas_uso = defaultdict(int)
    uci_uso = defaultdict(int)

    asignaciones = []
    no_asignados = []

    for _, paciente in df.iterrows():
        servicio = paciente['Servicio']
        duracion = int(paciente['Duración agendada (min)'])
        dias_perm = int(paciente['dias_estimados'])
        es_uci = (servicio == 'Vascular' and
                  'fistula' in str(paciente['Descripción']).lower())

        dias_cama = 2 if es_uci else dias_perm
        capacidad_camas = CAMAS_UCI if es_uci else CAMAS_HOSPITALIZACION
        uso_actual = uci_uso if es_uci else camas_uso

        ventanas = ventanas_factibles(servicio, duracion)
        if not ventanas:
            no_asignados.append({**paciente.to_dict(),
                                 'motivo': 'duración > ventana servicio'})
            continue

        # 1. Obtener el pabellón asignado por defecto
        try:
            p = int(paciente['OR Suite'])
        except (ValueError, TypeError):
            no_asignados.append({**paciente.to_dict(),
                                 'motivo': f"OR Suite inválido: {paciente.get('OR Suite')}"})
            continue

        # 2. Validar que el pabellón exista en la lista original (PABELLONES 1 al 8)
        if p not in PABELLONES:
            no_asignados.append({**paciente.to_dict(),
                                 'motivo': f"Pabellón {p} no existe"})
            continue

        asignado = False
        
        # 3. Buscar hueco SÓLO en el pabellón fijo 'p' a lo largo de los días
        for d in range(DIAS):
            if asignado:
                break

            # Validar camas para la estadía
            if dias_cama > 0:
                cabe_cama = all(
                    uso_actual[d + k] + 1 <= capacidad_camas
                    for k in range(dias_cama)
                    if d + k < DIAS
                )
                if not cabe_cama:
                    continue

            # Buscar hora usando las utilidades originales de factibilidad
            hora = encontrar_hora_inicio(ocupacion[d][p], ventanas, duracion)
            
            if hora is not None:
                insertar_ordenado(ocupacion[d][p], (hora, hora + duracion))
                
                # Consumir camas
                if dias_cama > 0:
                    for k in range(dias_cama):
                        if d + k < DIAS:
                            uso_actual[d + k] += 1
                            
                asignaciones.append({
                    'Correlativo': paciente['Correlativo'],
                    'Servicio': servicio,
                    'Descripción': paciente['Descripción'],
                    'Prioridad': paciente['Prioridad de paciente'],
                    'Duración (min)': duracion,
                    'Días cama': dias_cama,
                    'Día': d + 1,
                    'Pabellón': p,
                    'Hora inicio (min)': hora,
                    'Hora inicio': f'{hora//60:02d}:{hora%60:02d}',
                    'Hora fin': f'{(hora+duracion)//60:02d}:{(hora+duracion)%60:02d}',
                    # --- Nuevos campos agregados para consistencia del CSV ---
                    'Permanencia': dias_perm,
                    'Requiere UCI': es_uci
                })
                asignado = True

        if not asignado:
            no_asignados.append({**paciente.to_dict(),
                                 'motivo': f"sin capacidad en su pabellón fijo ({p}) o camas"})

    return (pd.DataFrame(asignaciones),
            pd.DataFrame(no_asignados),
            ocupacion, camas_uso, uci_uso)


# ============================================================================
# 5. EXPORTAR CSV DE RESULTADOS
# ============================================================================

def exportar_csv(asign, ruta_salida):
    """Filtra, ordena y exporta las asignaciones estrictamente en el formato CSV requerido."""
    # Definimos el orden EXACTO solicitado para las columnas del CSV
    cols_csv = [
        'Correlativo', 'Servicio', 'Descripción', 'Prioridad', 
        'Pabellón', 'Día', 'Hora inicio', 'Hora fin', 
        'Duración (min)', 'Permanencia', 'Requiere UCI'
    ]

    # Ordenamos cronológicamente por Día, Pabellón y Hora de inicio
    asign_export = (
        asign
        .sort_values(['Día', 'Pabellón', 'Hora inicio (min)'])
        [cols_csv]
    )

    # Guardamos directamente en formato CSV sin índice de pandas
    asign_export.to_csv(ruta_salida, index=False, encoding='utf-8-sig')


# ============================================================================
# 6. EJECUCIÓN PRINCIPAL
# ============================================================================

def main():
    print("=" * 78)
    print("CASO BASE - POLÍTICA MIOPE GREEDY (SOLO OUTPUT CSV)")
    print("=" * 78)

    # Crear carpeta de outputs
    os.makedirs(CARPETA_OUTPUTS, exist_ok=True)
    print(f"\nCarpeta de salida: {os.path.abspath(CARPETA_OUTPUTS)}")

    # Cargar datos
    le, db = cargar_datos()
    print(f"\n[1] Pacientes en lista de espera: {len(le)}")
    convencion = '5 = mayor' if PRIORIDAD_MAX_ES_MAYOR else '1 = mayor'
    print(f"    Convención de prioridad: {convencion}")

    # Heurística
    print(f"\n[2] Ejecutando heurística greedy (Prioridad + SPT)...")
    asign, no_asign, ocup, camas, uci = asignar_greedy(le)
    print(f"    -> Asignados: {len(asign)}  |  No asignados: {len(no_asign)}")

    # Exportar directamente a CSV
    ruta_csv = os.path.join('caso_base_asignaciones.csv')
    exportar_csv(asign, ruta_csv)
    print(f"\n[3] CSV generado exitosamente en: {ruta_csv}")

    print("\nListo.")
    return asign, no_asign

if __name__ == '__main__':
    main()