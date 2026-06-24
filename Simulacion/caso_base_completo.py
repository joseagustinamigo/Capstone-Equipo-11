"""
================================================================================
CASO BASE / POLÍTICA MIOPE - Asignación de tablas quirúrgicas
ICS2122 - Taller de Investigación Operativa - Grupo 11 - Entrega 2
================================================================================

SCRIPT ENFOCADO EN EXPORTACIÓN EXCLUSIVA A CSV:
  1. Ejecuta la heurística greedy de asignación (CEGUERA CLÍNICA: Orden por Correlativo).
  2. ESTIMA la estadía usando la MEDIANA HISTÓRICA.
  3. AGENDAMIENTO MANUAL: Fuerza inicios de cirugía cada 60 minutos (ineficiencia).
"""

import os
from collections import defaultdict
import pandas as pd
import numpy as np
import unicodedata
import math

# =============================================================================
# 1. PARÁMETROS DE CONFIGURACIÓN 
# =============================================================================

SIMULACION_DIR = os.path.abspath(os.path.dirname(__file__))

RUTA_CSV_ENTRADA = os.path.join(SIMULACION_DIR, "lista_espera_reprogramacion.csv")
RUTA_CSV_SALIDA = os.path.join(SIMULACION_DIR, "resultados", "caso_base_asignaciones.csv")
RUTA_EXCEL = os.path.abspath(
    os.path.join(SIMULACION_DIR, "..", "preprocesamiento", "Datos", "Datos Operaciones y lista de espera.xlsx")
)

RUTA_CAMAS_BASICAS = os.path.join(SIMULACION_DIR, "camas_basicas_activas.csv")
RUTA_CAMAS_UCI = os.path.join(SIMULACION_DIR, "camas_uci_activas.csv")

PRIORIDAD_MAX_ES_MAYOR = False   

DIAS = 7                    
PABELLONES = list(range(1, 9))
SLOT_MIN = 15               
BLOQUE_MANUAL = 60          # <--- NUEVO: Ineficiencia humana (Obliga a empezar a la hora en punto)
HORA_INICIO = 8 * 60        
HORA_FIN = 18 * 60          
CAP_CAMAS_BASICAS = 75      
CAP_CAMAS_UCI = 2           

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
    print("=" * 70)
    print("CARGA DE DATOS")
    print("=" * 70)

    os.makedirs(os.path.dirname(RUTA_CSV_SALIDA), exist_ok=True)
    if os.path.exists(RUTA_CSV_SALIDA):
        os.remove(RUTA_CSV_SALIDA)

    if not os.path.exists(RUTA_CSV_ENTRADA):
        raise FileNotFoundError(f"ERROR: No se encontró el archivo de entrada: {RUTA_CSV_ENTRADA}")

    df_espera = pd.read_csv(RUTA_CSV_ENTRADA, sep=",", encoding="utf-8-sig")
    df_base = pd.read_excel(RUTA_EXCEL, sheet_name="Datos base")

    def normalize_column(col):
        if pd.isna(col): return col
        normalized = unicodedata.normalize("NFKD", str(col)).encode("ascii", "ignore").decode("ascii")
        return " ".join(normalized.split()).strip()

    df_espera.columns = [normalize_column(c) for c in df_espera.columns]
    
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

    try: os.remove(RUTA_CSV_ENTRADA)
    except: pass

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

    required_columns = ["Correlativo", "Servicio", "Descripción", "Prioridad de paciente", "Duración agendada (min)", "OR Suite"]
    missing_columns = [c for c in required_columns if c not in df_espera.columns]
    
    if missing_columns:
        raise ValueError(f"Columnas faltantes en la lista de espera: {missing_columns}.")

    df_espera["Servicio"] = df_espera["Servicio"].astype("string").str.strip().replace({"": pd.NA})
    df_espera["Descripción"] = df_espera["Descripción"].astype("string").str.strip().replace({"": pd.NA})
    df_espera["Duración agendada (min)"] = df_espera["Duración agendada (min)"].apply(parse_minutes_common)
    df_espera["Prioridad de paciente"] = pd.to_numeric(df_espera["Prioridad de paciente"], errors="coerce")
    df_espera["Correlativo"] = pd.to_numeric(df_espera["Correlativo"], errors="coerce").astype(int)
    df_espera["OR Suite"] = pd.to_numeric(df_espera["OR Suite"], errors="coerce").astype(int)

    invalid_rows = df_espera[required_columns].isna().any(axis=1)
    if invalid_rows.any():
        raise ValueError(f"{invalid_rows.sum()} registros tienen datos requeridos inválidos")

    # ---> CÁLCULO DE PERMANENCIA ESTIMADA ESTRICTAMENTE HISTÓRICA <---
    df_base["Servicio"] = df_base["Servicio"].astype("string").str.strip()
    df_base["Descripción"] = df_base["Descripción"].astype("string").str.strip()
    
    col_historica = "Días de permanencia efectivos" if "Días de permanencia efectivos" in df_base.columns else "Días de permanencia programados"
    
    mediana_detallada = (
        df_base.groupby(["Servicio", "Descripción"])[col_historica]
        .median().fillna(0).round().astype(int)
        .reset_index()
        .rename(columns={col_historica: "Permanencia"})
    )
    
    mediana_servicio = (
        df_base.groupby("Servicio")[col_historica]
        .median().fillna(0).round().astype(int)
    )
    
    mediana_global = int(round(df_base[col_historica].median(skipna=True)))

    df_espera = df_espera.merge(mediana_detallada, on=["Servicio", "Descripción"], how="left")
    
    df_espera["Permanencia"] = df_espera["Permanencia"].fillna(
        df_espera["Servicio"].map(mediana_servicio)
    ).fillna(mediana_global).astype(int)

    return df_espera

# ============================================================================
# 3. UTILIDADES DE FACTIBILIDAD
# ============================================================================

def ventanas_factibles(servicio, duracion_bloqueada):
    ventanas = RESTRICCIONES_HORARIAS.get(servicio, [(HORA_INICIO, HORA_FIN)])
    return [(vi, vf) for vi, vf in ventanas if vf - vi >= duracion_bloqueada]

def encontrar_hora_inicio(ocupacion_pabellon, ventanas, duracion_bloqueada):
    for vi, vf in ventanas:
        # ---> INEFICIENCIA HUMANA: Forzar inicios a la "hora en punto" (Ej: 08:00, 09:00)
        cursor = math.ceil(vi / BLOQUE_MANUAL) * BLOQUE_MANUAL
        
        for ini, fin in ocupacion_pabellon:
            if fin <= cursor: continue
            if ini >= cursor + duracion_bloqueada and ini <= vf: return cursor
            
            # Si choca con otra cirugía, la mueve a la próxima "hora en punto" libre
            cursor = max(cursor, math.ceil(fin / BLOQUE_MANUAL) * BLOQUE_MANUAL)
            if cursor + duracion_bloqueada > vf: break
        else:
            if cursor + duracion_bloqueada <= vf: return cursor
    return None

def insertar_ordenado(lista_intervalos, intervalo):
    lista_intervalos.append(intervalo)
    lista_intervalos.sort()

# ============================================================================
# 4. HEURÍSTICA GREEDY DE ASIGNACIÓN
# ============================================================================

def asignar_greedy(df, n_pacientes=250):
    """
    Política miope administrativa pura:
    - Ordena por tiempo de llegada (Correlativo), ignorando prioridad médica.
    - Usa "Bloques de 1 hora" para agendar, perdiendo tiempo valioso de pabellón.
    """
    
    # ---> CEGUERA CLÍNICA: Ordenamos estrictamente por antigüedad de llegada
    df = df.sort_values('Correlativo', ascending=True).reset_index(drop=True)
    
    df_activa = df.head(n_pacientes).copy()
    df_restante = df.iloc[n_pacientes:].copy()
    no_asignados = []
    
    for _, paciente in df_restante.iterrows():
        no_asignados.append({**paciente.to_dict(), 'motivo': f'fuera del lote de {n_pacientes}'})

    ocupacion = {d: {p: [] for p in PABELLONES} for d in range(DIAS)}
    camas_uso = defaultdict(int)
    uci_uso = defaultdict(int)

    if os.path.exists(RUTA_CAMAS_BASICAS):
        df_basicas = pd.read_csv(RUTA_CAMAS_BASICAS)
        for d in range(DIAS):
            camas_uso[d] = df_basicas[df_basicas['Dias_Restantes'] > d].shape[0]

    if os.path.exists(RUTA_CAMAS_UCI):
        df_uci = pd.read_csv(RUTA_CAMAS_UCI)
        for d in range(DIAS):
            uci_uso[d] = df_uci[df_uci['Dias_Restantes'] > d].shape[0]

    asignaciones = []

    for _, paciente in df_activa.iterrows():
        servicio = paciente['Servicio']
        
        # Lógica de bloques
        duracion_original = int(paciente['Duración agendada (min)'])
        slots_requeridos = math.ceil(duracion_original / SLOT_MIN)
        duracion_bloqueada = slots_requeridos * SLOT_MIN  
        
        dias_cama = int(paciente['Permanencia']) 
        es_uci = (servicio == 'Vascular' and 'fistula' in str(paciente['Descripción']).lower())
        
        if es_uci:
            capacidad_camas = CAP_CAMAS_UCI
            uso_actual = uci_uso
            dias_cama = min(2, dias_cama) # NUNCA consumen más de 2 días de UCI
        else:
            capacidad_camas = CAP_CAMAS_BASICAS
            uso_actual = camas_uso

        ventanas = ventanas_factibles(servicio, duracion_bloqueada)
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

            hora = encontrar_hora_inicio(ocupacion[d][p], ventanas, duracion_bloqueada)
            
            if hora is not None:
                insertar_ordenado(ocupacion[d][p], (hora, hora + duracion_bloqueada))
                
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
                    'Hora fin': f'{(hora+duracion_bloqueada)//60:02d}:{(hora+duracion_bloqueada)%60:02d}',
                    'Duración (min)': duracion_original, 
                    'Permanencia': dias_cama, 
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
    print("CASO BASE - POLÍTICA MIOPE ADMINISTRATIVA (ANTIGÜEDAD + BLOQUES)")
    print("=" * 78)

    df_espera = cargar_datos()
    print(f"\n[1] Pacientes procesados en el lote actual: {len(df_espera)}")

    print(f"\n[2] Ejecutando heurística administrativa (Fuerza inicios en punto)...")
    asign, no_asign = asignar_greedy(df_espera)
    print(f"    -> Asignados: {len(asign)}  |  No asignados: {len(no_asign)}")

    exportar_csv(asign, RUTA_CSV_SALIDA)
    print(f"\n[3] CSV generado exitosamente en: {RUTA_CSV_SALIDA}")
    print("\nListo.")

if __name__ == '__main__':
    main()