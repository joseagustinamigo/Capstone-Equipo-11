"""
================================================================================
CASO BASE / POLÍTICA MIOPE - Asignación de tablas quirúrgicas
ICS2122 - Taller de Investigación Operativa - Grupo 11 - Entrega 2
================================================================================

SCRIPT ENFOCADO EN EXPORTACIÓN EXCLUSIVA A CSV:
  1. Ejecuta la heurística greedy de asignación (Fijando pabellón original).
  2. Lee las estadías directamente del CSV de simulación (sin estimar nada).
  3. Genera un archivo CSV limpio con el formato y orden estricto de columnas.

Todos los outputs se guardan en la carpeta 'resultados/'.
"""

import os
from collections import defaultdict
import pandas as pd
import numpy as np
import unicodedata

# =============================================================================
# 1. PARÁMETROS DE CONFIGURACIÓN 
# =============================================================================

# Directorio base de simulación
SIMULACION_DIR = os.path.abspath(os.path.dirname(__file__))

# Rutas de archivos (Solo lee el CSV entregado por la simulación)
RUTA_CSV_ENTRADA = os.path.join(SIMULACION_DIR, "lista_espera_reprogramacion.csv")
RUTA_CSV_SALIDA = os.path.join(SIMULACION_DIR, "resultados", "caso_base_asignaciones.csv")

# Rutas de estado de camas heredado
RUTA_CAMAS_BASICAS = os.path.join(SIMULACION_DIR, "camas_basicas_activas.csv")
RUTA_CAMAS_UCI = os.path.join(SIMULACION_DIR, "camas_uci_activas.csv")

# Convención de prioridad: True si 5 = mayor, False si 1 = mayor.
PRIORIDAD_MAX_ES_MAYOR = False   

# Parámetros del horizonte y Recursos
DIAS = 7                    
PABELLONES = list(range(1, 9))
HORA_INICIO = 8 * 60        # 8:00 AM en minutos
HORA_FIN = 18 * 60          # 6:00 PM en minutos
CAP_CAMAS_BASICAS = 75      
CAP_CAMAS_UCI = 2           

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

# =============================================================================
# 2. CARGA Y PREPARACIÓN DE DATOS
# =============================================================================

def cargar_datos():
    """Carga y limpia la lista de espera usando estrictamente los datos del CSV."""
    print("=" * 70)
    print("CARGA DE DATOS")
    print("=" * 70)

    # Asegurarnos que el directorio de salida exista
    os.makedirs(os.path.dirname(RUTA_CSV_SALIDA), exist_ok=True)
    if os.path.exists(RUTA_CSV_SALIDA):
        os.remove(RUTA_CSV_SALIDA)

    if not os.path.exists(RUTA_CSV_ENTRADA):
        raise FileNotFoundError(f"ERROR: No se encontró el archivo de entrada: {RUTA_CSV_ENTRADA}")

    print(f"Leyendo lista de espera desde: {RUTA_CSV_ENTRADA}")
    df_espera = pd.read_csv(RUTA_CSV_ENTRADA, sep=",", encoding="utf-8-sig")

    def normalize_column(col):
        if pd.isna(col): return col
        normalized = unicodedata.normalize("NFKD", str(col)).encode("ascii", "ignore").decode("ascii")
        return " ".join(normalized.split()).strip()

    df_espera.columns = [normalize_column(c) for c in df_espera.columns]
    
    # Evitar columnas duplicadas
    if df_espera.columns.duplicated().any():
        cols = []
        counts = {}
        for col in df_espera.columns:
            if col in counts:
                counts[col] += 1
                cols.append(f"{col}.{counts[col]}")
            else:
                counts[col] = 0
                cols.append(col)
        df_espera.columns = cols

    # Normalización básica de nombres de columnas
    column_mapping = {
        "Prioridad": "Prioridad de paciente",
        "Duración (min)": "Duración agendada (min)",
        "Duracion agendada (min)": "Duración agendada (min)",
        "servicio": "Servicio",
        "descripcion": "Descripción",
        "Descripcion": "Descripción"
    }
    for old_col, new_col in column_mapping.items():
        if old_col in df_espera.columns and new_col not in df_espera.columns:
            df_espera[new_col] = df_espera[old_col]

    # ---> BÚSQUEDA ROBUSTA PARA LA COLUMNA DE ESTADÍA <---
    if "Permanencia" not in df_espera.columns:
        for col in df_espera.columns:
            col_lower = str(col).lower()
            # Si la columna contiene la palabra permanencia, estadia o estimado, la toma.
            if "permanencia" in col_lower or "estad" in col_lower or "estimad" in col_lower:
                df_espera["Permanencia"] = df_espera[col]
                break

    # Fusionar columnas duplicadas de texto
    for alt in ["Servicio.1", "servicio", "servicio.1", "Servicio.2", "servicio.2"]:
        if alt in df_espera.columns: df_espera["Servicio"] = df_espera["Servicio"].fillna(df_espera[alt])
    for alt in ["Descripción.1", "descripcion", "descripcion.1", "Descripción.2", "descripcion.2"]:
        if alt in df_espera.columns: df_espera["Descripción"] = df_espera["Descripción"].fillna(df_espera[alt])
    for alt in ["OR Suite.1", "OR Suite.2", "OR Suite.0"]:
        if alt in df_espera.columns: df_espera["OR Suite"] = df_espera["OR Suite"].fillna(df_espera[alt])

    def normalize_text(value):
        if pd.isna(value): return pd.NA
        value = str(value).strip()
        return value if value != "" else pd.NA

    df_espera["Servicio"] = df_espera["Servicio"].apply(normalize_text)
    df_espera["Descripción"] = df_espera["Descripción"].apply(normalize_text)
    df_espera = df_espera.loc[:, ~df_espera.columns.duplicated()]

    # Limpiar CSV despues de lectura
    try: os.remove(RUTA_CSV_ENTRADA)
    except: pass

    # Función común para parsear minutos
    def parse_minutes_common(value):
        if pd.isna(value): return np.nan
        if isinstance(value, pd.Timedelta): return value.total_seconds() / 60
        if isinstance(value, (int, float, np.integer, np.floating)): return float(value)
        if isinstance(value, str):
            value = value.strip()
            if value == "": return np.nan
            try: return float(value)
            except ValueError:
                try: return pd.to_timedelta(value).total_seconds() / 60
                except Exception: return np.nan
        return np.nan

    # Validaciones Finales
    required_columns = ["Correlativo", "Servicio", "Descripción", "Prioridad de paciente", "Duración agendada (min)", "OR Suite", "Permanencia"]
    missing_columns = [c for c in required_columns if c not in df_espera.columns]
    
    if missing_columns:
        print(f"\n[DEBUG] ⚠️ Columnas que el código pudo leer del CSV: {list(df_espera.columns)}")
        raise ValueError(f"Columnas faltantes: {missing_columns}. Revisa el DEBUG de arriba para ver cómo se llama realmente tu columna en el CSV.")

    df_espera["Servicio"] = df_espera["Servicio"].astype("string").str.strip().replace({"": pd.NA})
    df_espera["Descripción"] = df_espera["Descripción"].astype("string").str.strip().replace({"": pd.NA})
    df_espera["Duración agendada (min)"] = df_espera["Duración agendada (min)"].apply(parse_minutes_common)
    df_espera["Prioridad de paciente"] = pd.to_numeric(df_espera["Prioridad de paciente"], errors="coerce")
    df_espera["Correlativo"] = pd.to_numeric(df_espera["Correlativo"], errors="coerce").astype(int)
    df_espera["OR Suite"] = pd.to_numeric(df_espera["OR Suite"], errors="coerce").astype(int)
    
    # Convertir estadía a número entero de días
    df_espera["Permanencia"] = pd.to_numeric(df_espera["Permanencia"], errors="coerce").fillna(0).astype(int)

    invalid_rows = df_espera[required_columns].isna().any(axis=1)
    if invalid_rows.any():
        raise ValueError(f"{invalid_rows.sum()} registros tienen datos requeridos inválidos")

    return df_espera
# ============================================================================
# 3. UTILIDADES DE FACTIBILIDAD
# ============================================================================

def ventanas_factibles(servicio, duracion):
    ventanas = RESTRICCIONES_HORARIAS.get(servicio, [(HORA_INICIO, HORA_FIN)])
    return [(vi, vf) for vi, vf in ventanas if vf - vi >= duracion]

def encontrar_hora_inicio(ocupacion_pabellon, ventanas, duracion):
    for vi, vf in ventanas:
        cursor = vi
        for ini, fin in ocupacion_pabellon:
            if fin <= cursor: continue
            if ini >= cursor + duracion and ini <= vf: return cursor
            cursor = max(cursor, fin)
            if cursor + duracion > vf: break
        else:
            if cursor + duracion <= vf: return cursor
    return None

def insertar_ordenado(lista_intervalos, intervalo):
    lista_intervalos.append(intervalo)
    lista_intervalos.sort()

# ============================================================================
# 4. HEURÍSTICA GREEDY DE ASIGNACIÓN
# ============================================================================

def asignar_greedy(df):
    """Política miope que respeta las camas ocupadas previas y asigna al OR Suite original."""
    if PRIORIDAD_MAX_ES_MAYOR:
        df['_prio_orden'] = -df['Prioridad de paciente']
    else:
        df['_prio_orden'] = df['Prioridad de paciente']
        
    df = df.sort_values(['_prio_orden', 'Duración agendada (min)'],
                        ascending=[True, True]).reset_index(drop=True)

    ocupacion = {d: {p: [] for p in PABELLONES} for d in range(DIAS)}
    camas_uso = defaultdict(int)
    uci_uso = defaultdict(int)

    # --- PRECARGA DE CAMAS (ESTADO HEREDADO) ---
    if os.path.exists(RUTA_CAMAS_BASICAS):
        df_basicas = pd.read_csv(RUTA_CAMAS_BASICAS)
        for d in range(DIAS):
            camas_uso[d] = df_basicas[df_basicas['Dias_Restantes'] > d].shape[0]

    if os.path.exists(RUTA_CAMAS_UCI):
        df_uci = pd.read_csv(RUTA_CAMAS_UCI)
        for d in range(DIAS):
            uci_uso[d] = df_uci[df_uci['Dias_Restantes'] > d].shape[0]

    asignaciones = []
    no_asignados = []

    for _, paciente in df.iterrows():
        servicio = paciente['Servicio']
        duracion = int(paciente['Duración agendada (min)'])
        dias_cama = int(paciente['Permanencia']) # <--- Aquí se toma directo del CSV
        
        es_uci = (servicio == 'Vascular' and 'fistula' in str(paciente['Descripción']).lower())
        capacidad_camas = CAP_CAMAS_UCI if es_uci else CAP_CAMAS_BASICAS
        uso_actual = uci_uso if es_uci else camas_uso

        ventanas = ventanas_factibles(servicio, duracion)
        if not ventanas:
            no_asignados.append({**paciente.to_dict(), 'motivo': 'duración > ventana servicio'})
            continue

        p = int(paciente['OR Suite'])
        if p not in PABELLONES:
            no_asignados.append({**paciente.to_dict(), 'motivo': f"Pabellón {p} no existe"})
            continue

        asignado = False
        
        for d in range(DIAS):
            if asignado: break

            if dias_cama > 0:
                cabe_cama = all(
                    uso_actual[d + k] + 1 <= capacidad_camas
                    for k in range(dias_cama) if d + k < DIAS
                )
                if not cabe_cama: continue

            hora = encontrar_hora_inicio(ocupacion[d][p], ventanas, duracion)
            
            if hora is not None:
                insertar_ordenado(ocupacion[d][p], (hora, hora + duracion))
                
                if dias_cama > 0:
                    for k in range(dias_cama):
                        if d + k < DIAS: uso_actual[d + k] += 1
                            
                asignaciones.append({
                    'Correlativo': paciente['Correlativo'],
                    'Servicio': servicio,
                    'Descripción': paciente['Descripción'],
                    'Prioridad': paciente['Prioridad de paciente'],
                    'Pabellón': p,
                    'Día': d + 1,
                    'Hora inicio': f'{hora//60:02d}:{hora%60:02d}',
                    'Hora fin': f'{(hora+duracion)//60:02d}:{(hora+duracion)%60:02d}',
                    'Duración (min)': duracion,
                    'Permanencia': dias_cama, # Incluido en las variables resultantes
                    'Requiere UCI': es_uci,
                    'Hora inicio (min)': hora 
                })
                asignado = True

        if not asignado:
            no_asignados.append({**paciente.to_dict(), 'motivo': f"sin capacidad en su pabellón fijo ({p}) o camas"})

    return pd.DataFrame(asignaciones), pd.DataFrame(no_asignados)

# ============================================================================
# 5. EXPORTAR CSV DE RESULTADOS
# ============================================================================

def exportar_csv(asign, ruta_salida):
    """Filtra, ordena y exporta las asignaciones estrictamente en el formato CSV requerido."""
    # Mantenemos las columnas exactas
    cols_csv = [
        'Correlativo', 'Servicio', 'Descripción', 'Prioridad', 
        'Pabellón', 'Día', 'Hora inicio', 'Hora fin', 
        'Duración (min)', 'Permanencia', 'Requiere UCI'
    ]

    if asign.empty:
        asign_export = pd.DataFrame(columns=cols_csv)
    else:
        asign_export = asign.sort_values(['Día', 'Pabellón', 'Hora inicio (min)'])[cols_csv]

    os.makedirs(os.path.dirname(ruta_salida), exist_ok=True)
    asign_export.to_csv(ruta_salida, index=False, encoding='utf-8-sig')

# ============================================================================
# 6. EJECUCIÓN PRINCIPAL
# ============================================================================

def main():
    print("=" * 78)
    print("CASO BASE - POLÍTICA MIOPE GREEDY (SOLO OUTPUT CSV)")
    print("=" * 78)

    # Cargar datos
    df_espera = cargar_datos()
    print(f"\n[1] Pacientes pendientes en lista de espera: {len(df_espera)}")

    # Heurística
    print(f"\n[2] Ejecutando heurística greedy (Prioridad + SPT)...")
    asign, no_asign = asignar_greedy(df_espera)
    print(f"    -> Asignados: {len(asign)}  |  No asignados: {len(no_asign)}")

    # Exportar directamente a CSV
    exportar_csv(asign, RUTA_CSV_SALIDA)
    print(f"\n[3] CSV generado exitosamente en: {RUTA_CSV_SALIDA}")
    print("\nListo.")

if __name__ == '__main__':
    main()