"""
=============================================================================
Modelo Determinístico de Programación Quirúrgica Semanal
=============================================================================
Grupo 11 - ICS2122 Taller de Investigación Operativa
PUC Chile

Modelo MIP que asigna pacientes de la lista de espera a (pabellón, día, slot
de inicio) maximizando la prioridad clínica total atendida durante una semana,
sujeto a:
  - Capacidad de pabellones (no solapamiento).
  - Ventanas horarias por servicio clínico.
  - Capacidad de camas básicas y UCI (con regla especial Vascular/AV Fistula).

Requiere: pandas, numpy, openpyxl, gurobipy
=============================================================================
"""

import pandas as pd
import numpy as np
import gurobipy as gp
from gurobipy import GRB
from math import ceil
import os
from os import path
import unicodedata

# =============================================================================
# 1. PARÁMETROS DE CONFIGURACIÓN — AJUSTAR SEGÚN NECESIDAD
# =============================================================================

# Directorio base de simulación
SIMULACION_DIR = path.abspath(path.dirname(__file__))

# Ruta del archivo Excel con los datos
RUTA_EXCEL = path.abspath(
    path.join(SIMULACION_DIR, "..", "preprocesamiento", "Datos", "Datos Operaciones y lista de espera.xlsx")
)

# Ruta del archivo CSV de entrada para reprogramación (creado por simulacion.py)
RUTA_CSV_ENTRADA = path.join(SIMULACION_DIR, "lista_espera_reprogramacion.csv")
RUTA_CSV_SALIDA = path.join(SIMULACION_DIR, "resultados", "resultado_programacion.csv")

# Parámetros del horizonte
N_DIAS = 7                  # |D|
SLOT_MIN = 15               # tamaño del slot en minutos
HORA_INICIO_JORNADA = 8     # 8:00 AM
HORA_FIN_JORNADA = 18       # 6:00 PM

# Recursos
N_PABELLONES = 8            # |J|
CAP_CAMAS_BASICAS = 75      # C^BAS — ajustar según supuesto del hospital
CAP_CAMAS_UCI = 2           # C^UCI — fijado por el enunciado
DIAS_UCI = 2                # σ — días obligatorios en UCI para Vascular/AV Fistula

# Filtro inicial: usar solo los N pacientes con mayor prioridad para acotar tamaño
# Pon None para usar TODOS los pacientes (puede tardar mucho)
N_PACIENTES_MAX = 500

# Parámetros de penalización (Efecto fin de horizonte)
PENALIZACION_CAMA_BASICA = 0.9  # Costo en la f.o. por ocupar una cama básica el día 7
PENALIZACION_CAMA_UCI =  0.9     # Costo en la f.o. por ocupar una cama UCI el día 7


# Parámetros de Gurobi
TIEMPO_LIMITE_SEG = 20000     # 10 minutos
MIP_GAP = 0            # detener cuando el gap sea menor al 1%

# =============================================================================
# 2. CARGA Y PREPARACIÓN DE DATOS
# =============================================================================

print("=" * 70)
print("CARGA DE DATOS")
print("=" * 70)

os.makedirs(path.dirname(RUTA_CSV_SALIDA), exist_ok=True)
if path.exists(RUTA_CSV_SALIDA):
    os.remove(RUTA_CSV_SALIDA)

# Intentar cargar desde CSV de entrada (creado por simulacion.py)
# Si no existe, cargar del Excel normalmente
if path.exists(RUTA_CSV_ENTRADA):
    print(f"Leyendo lista de espera desde: {RUTA_CSV_ENTRADA}")
    df_base = pd.read_excel(RUTA_EXCEL, sheet_name="Datos base")
    df_espera = pd.read_csv(RUTA_CSV_ENTRADA, sep=",", encoding="utf-8-sig")

    def normalize_column(col):
        if pd.isna(col):
            return col
        normalized = unicodedata.normalize("NFKD", str(col))
        normalized = normalized.encode("ascii", "ignore").decode("ascii")
        return " ".join(normalized.split()).strip()

    # Normalizar nombres de columna que pueden venir desde simulación
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

    if "Prioridad de paciente" not in df_espera.columns and "Prioridad" in df_espera.columns:
        df_espera["Prioridad de paciente"] = df_espera["Prioridad"]
    if "Duración agendada (min)" not in df_espera.columns and "Duración (min)" in df_espera.columns:
        df_espera["Duración agendada (min)"] = df_espera["Duración (min)"]
    if "Duración agendada (min)" not in df_espera.columns and "Duracion agendada (min)" in df_espera.columns:
        df_espera["Duración agendada (min)"] = df_espera["Duracion agendada (min)"]
    if "Servicio" not in df_espera.columns and "servicio" in df_espera.columns:
        df_espera["Servicio"] = df_espera["servicio"]
    if "Descripción" not in df_espera.columns and "descripcion" in df_espera.columns:
        df_espera["Descripción"] = df_espera["descripcion"]
    if "Descripción" not in df_espera.columns and "Descripcion" in df_espera.columns:
        df_espera["Descripción"] = df_espera["Descripcion"]

    # Coalesce duplicated service/description columns if present
    for alt in ["Servicio.1", "servicio", "servicio.1", "Servicio.2", "servicio.2"]:
        if alt in df_espera.columns:
            df_espera["Servicio"] = df_espera["Servicio"].fillna(df_espera[alt])
    for alt in ["Descripción.1", "descripcion", "descripcion.1", "Descripción.2", "descripcion.2"]:
        if alt in df_espera.columns:
            df_espera["Descripción"] = df_espera["Descripción"].fillna(df_espera[alt])
    for alt in ["OR Suite.1", "OR Suite.2", "OR Suite.0"]:
        if alt in df_espera.columns:
            df_espera["OR Suite"] = df_espera["OR Suite"].fillna(df_espera[alt])

    # Normalize service and description text
    def normalize_text(value):
        if pd.isna(value):
            return pd.NA
        value = str(value).strip()
        return value if value != "" else pd.NA

    df_espera["Servicio"] = df_espera["Servicio"].apply(normalize_text)
    df_espera["Descripción"] = df_espera["Descripción"].apply(normalize_text)

    # Limpiar columnas duplicadas innecesarias
    df_espera = df_espera.loc[:, ~df_espera.columns.duplicated()]

    def parse_minutes(value):
        if pd.isna(value):
            return np.nan
        if isinstance(value, (int, float, np.integer, np.floating)):
            return float(value)
        if isinstance(value, pd.Timedelta):
            return value.total_seconds() / 60
        if isinstance(value, str):
            value = value.strip()
            if value == "":
                return np.nan
            try:
                return float(value)
            except ValueError:
                try:
                    td = pd.to_timedelta(value)
                    return td.total_seconds() / 60
                except Exception:
                    return np.nan
        return np.nan

    df_espera["Duración agendada (min)"] = df_espera["Duración agendada (min)"].apply(parse_minutes)
    df_espera["Prioridad de paciente"] = pd.to_numeric(df_espera["Prioridad de paciente"], errors="coerce")
    df_espera["Correlativo"] = pd.to_numeric(df_espera["Correlativo"], errors="coerce").astype("Int64")

    if df_espera["Duración agendada (min)"].isna().any():
        raise ValueError("Al menos un registro de reprogramación tiene Duración agendada (min) inválida")
    if df_espera["Prioridad de paciente"].isna().any():
        raise ValueError("Al menos un registro de reprogramación tiene Prioridad de paciente inválida")

    # Validar que las columnas esperadas existan
    required_columns = ["Correlativo", "Servicio", "Descripción", "Prioridad de paciente", "Duración agendada (min)", "OR Suite"]
    missing_columns = [c for c in required_columns if c not in df_espera.columns]
    if missing_columns:
        raise ValueError(f"Columnas faltantes en el CSV de reprogramación: {missing_columns}")

    # Normalizar texto antes de validar los servicios
    df_espera["Servicio"] = df_espera["Servicio"].astype("string").str.strip().replace({"": pd.NA})

    df_espera["OR Suite"] = pd.to_numeric(df_espera["OR Suite"], errors="coerce").astype("Int64")
    if df_espera["OR Suite"].isna().any():
        raise ValueError("Al menos un registro de reprogramación tiene OR Suite inválido")

    print(f"Lista de espera cargada desde CSV (reprogramación)")
    print(f"  Registros históricos (Datos base): {len(df_base)}")
    print(f"  Pacientes para reprogramar: {len(df_espera)}")
    # Remover CSV después de leerlo
    try:
        path.exists(RUTA_CSV_ENTRADA) and os.remove(RUTA_CSV_ENTRADA)
    except:
        pass
else:
    print(f"No se encontró CSV de reprogramación: {RUTA_CSV_ENTRADA}")
    print(f"Leyendo lista de espera completa desde: {RUTA_EXCEL}")
    df_base = pd.read_excel(RUTA_EXCEL, sheet_name="Datos base")
    df_espera = pd.read_excel(RUTA_EXCEL, sheet_name="Lista de espera")
    print(f"Registros históricos (Datos base): {len(df_base)}")
    print(f"Lista de espera: {len(df_espera)}")


def parse_minutes_common(value):
    if pd.isna(value):
        return np.nan
    if isinstance(value, pd.Timedelta):
        return value.total_seconds() / 60
    if isinstance(value, (int, float, np.integer, np.floating)):
        return float(value)
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return np.nan
        try:
            return float(value)
        except ValueError:
            try:
                return pd.to_timedelta(value).total_seconds() / 60
            except Exception:
                return np.nan
    return np.nan


required_columns = ["Correlativo", "Servicio", "Descripción", "Prioridad de paciente", "Duración agendada (min)", "OR Suite"]
missing_columns = [c for c in required_columns if c not in df_espera.columns]
if missing_columns:
    raise ValueError(f"Columnas faltantes en datos de reprogramación: {missing_columns}")

df_espera["Servicio"] = df_espera["Servicio"].astype("string").str.strip().replace({"": pd.NA})
df_espera["Descripción"] = df_espera["Descripción"].astype("string").str.strip().replace({"": pd.NA})
df_espera["Duración agendada (min)"] = df_espera["Duración agendada (min)"].apply(parse_minutes_common)
df_espera["Prioridad de paciente"] = pd.to_numeric(df_espera["Prioridad de paciente"], errors="coerce")
df_espera["Correlativo"] = pd.to_numeric(df_espera["Correlativo"], errors="coerce")
df_espera["OR Suite"] = pd.to_numeric(df_espera["OR Suite"], errors="coerce")

invalid_rows = df_espera[required_columns].isna().any(axis=1)
if invalid_rows.any():
    raise ValueError(f"{invalid_rows.sum()} registros tienen datos requeridos inválidos para reprogramación")

df_espera["Correlativo"] = df_espera["Correlativo"].astype(int)
df_espera["OR Suite"] = df_espera["OR Suite"].astype(int)
if df_espera.empty:
    raise ValueError("No hay pacientes válidos para reprogramar")



# --- Estimar días de permanencia para la lista de espera ---
# La lista de espera no incluye 'Días de permanencia programados'. Estimamos
# usando máximo del histórico agrupando por descripción de la cirugía.
# --- Estimar días de permanencia para la lista de espera (IGUAL AL GREEDY) ---
df_base["Servicio"] = df_base["Servicio"].astype("string").str.strip()
df_base["Descripción"] = df_base["Descripción"].astype("string").str.strip()

col_hist = "Días de permanencia efectivos" if "Días de permanencia efectivos" in df_base.columns else "Días de permanencia programados"

mediana_detallada = df_base.groupby(["Servicio", "Descripción"])[col_hist].median().fillna(0).round().astype(int).to_dict()
mediana_servicio = df_base.groupby("Servicio")[col_hist].median().fillna(0).round().astype(int).to_dict()
mediana_global = int(round(df_base[col_hist].median(skipna=True)))

def estimar_perm(row):
    val = mediana_detallada.get((row['Servicio'], row['Descripción']))
    if pd.isna(val):
        val = mediana_servicio.get(row['Servicio'])
    if pd.isna(val):
        val = mediana_global
    return int(val)

df_espera["Permanencia_estimada"] = df_espera.apply(estimar_perm, axis=1)

# --- Identificar pacientes que requieren UCI (Vascular + AV Fistula) ---
df_espera["Requiere_UCI"] = (
    (df_espera["Servicio"].str.lower() == "vascular")
    & (df_espera["Descripción"].str.lower().str.contains("fistula", na=False))
)

# --- Filtro opcional para reducir el tamaño del problema ---
if N_PACIENTES_MAX is not None and len(df_espera) > N_PACIENTES_MAX:
    df_espera = df_espera.head(N_PACIENTES_MAX).reset_index(drop=True)
    print(f"Lista de espera filtrada a los top {N_PACIENTES_MAX} pacientes.")

print(f"Pacientes a considerar: {len(df_espera)}")
print(f"  - Que requieren UCI: {df_espera['Requiere_UCI'].sum()}")
print(f"  - Ambulatorios (permanencia=0): {(df_espera['Permanencia_estimada']==0).sum()}")

# =============================================================================
# 3. CONJUNTOS Y PARÁMETROS DEL MODELO
# =============================================================================

# Conjuntos
I = df_espera.index.tolist()                           # pacientes
J = list(range(1, N_PABELLONES + 1))                   # pabellones
D = list(range(1, N_DIAS + 1))                         # días
N_SLOTS = (HORA_FIN_JORNADA - HORA_INICIO_JORNADA) * 60 // SLOT_MIN
S = list(range(1, N_SLOTS + 1))                        # slots
print(f"\n|I|={len(I)}, |J|={len(J)}, |D|={len(D)}, |S|={len(S)}")

# Parámetros del paciente
p = (6 - df_espera["Prioridad de paciente"]).to_dict()                            # prioridad invertida                                 # prioridad
ell = {i: ceil(df_espera.loc[i, "Duración agendada (min)"] / SLOT_MIN) for i in I}  # nº de slots
s_perm = df_espera["Permanencia_estimada"].to_dict()                               # días permanencia
g = df_espera["Servicio"].to_dict()                                                # servicio

ruta_camas_basicas = path.join(SIMULACION_DIR, "camas_basicas_activas.csv")
ruta_camas_uci = path.join(SIMULACION_DIR, "camas_uci_activas.csv")

try:
    if path.exists(ruta_camas_basicas):
        camas_previas_basicas = pd.read_csv(ruta_camas_basicas)
        # Si por alguna razón el CSV existe pero no tiene la columna correcta
        if "Dias_Restantes" not in camas_previas_basicas.columns:
            camas_previas_basicas = pd.DataFrame(columns=["Dias_Restantes"])
    else:
        camas_previas_basicas = pd.DataFrame(columns=["Dias_Restantes"])
except pd.errors.EmptyDataError:
    camas_previas_basicas = pd.DataFrame(columns=["Dias_Restantes"])

# Cargar ocupación previa de camas UCI a prueba de fallos
try:
    if path.exists(ruta_camas_uci):
        camas_previas_uci = pd.read_csv(ruta_camas_uci)
        if "Dias_Restantes" not in camas_previas_uci.columns:
            camas_previas_uci = pd.DataFrame(columns=["Dias_Restantes"])
    else:
        camas_previas_uci = pd.DataFrame(columns=["Dias_Restantes"])
except pd.errors.EmptyDataError:
    camas_previas_uci = pd.DataFrame(columns=["Dias_Restantes"])

# Funciones para calcular el castigo (camas ocupadas) por día 'd'
def camas_basicas_bloqueadas_el_dia(d):
    return sum(1 for _, row in camas_previas_basicas.iterrows() if row["Dias_Restantes"] >= d)

def camas_uci_bloqueadas_el_dia(d):
    return sum(1 for _, row in camas_previas_uci.iterrows() if row["Dias_Restantes"] >= d)

# Subconjunto UCI
I_UCI = df_espera[df_espera["Requiere_UCI"]].index.tolist()

# --- Ventanas horarias por servicio ---
# Cada ventana se da en (hora_inicio, hora_fin). Convertimos a slots.
def horas_a_slots(rangos):
    """Convierte una lista de tuplas (hora_inicio, hora_fin) a un set de slots."""
    if rangos == "todos":
        return set(S)
    slots = set()
    for h_ini, h_fin in rangos:
        s_ini = (h_ini - HORA_INICIO_JORNADA) * 60 // SLOT_MIN + 1
        s_fin = (h_fin - HORA_INICIO_JORNADA) * 60 // SLOT_MIN
        slots.update(range(s_ini, s_fin + 1))
    return slots

ventanas_servicio = {
    "ENT":           [(10, 15)],
    "General":       "todos",
    "OBGYN":         [(8, 13)],
    "Ophthalmology": [(8, 13)],
    "Orthopedics":   [(8, 11), (14, 18)],
    "Pediatrics":    [(8, 11), (14, 18)],
    "Plastic":       [(11, 16)],
    "Podiatry":      [(8, 13), (14, 17)],
    "Urology":       "todos",
    "Vascular":      [(8, 11), (15, 18)],
}
W = {srv: horas_a_slots(rangos) for srv, rangos in ventanas_servicio.items()}

print("\nVentanas horarias por servicio (en slots):")
for srv, slots in W.items():
    print(f"  {srv:<14}: {len(slots)} slots disponibles")

servicios_desconocidos = sorted(set(df_espera["Servicio"].dropna()) - set(W))
if servicios_desconocidos:
    raise ValueError(f"Servicios sin ventana horaria definida: {servicios_desconocidos}")

pabellones_invalidos = sorted(set(df_espera["OR Suite"].dropna().astype(int)) - set(J))
if pabellones_invalidos:
    raise ValueError(f"OR Suite fuera de rango 1..{N_PABELLONES}: {pabellones_invalidos}")

# =============================================================================
# 4. PRECÁLCULO DE COMBINACIONES (i,j,d,s) FACTIBLES
# =============================================================================
# Aplicamos R2 (cabe en jornada) y R3 (respeta ventana horaria) ANTES de crear
# variables, para reducir drásticamente el tamaño del modelo.

print("\nGenerando combinaciones factibles (filtro R2 + R3)...")
combinaciones_validas = []  # lista de tuplas (i, j, d, s)

# Diagnóstico: contar pacientes sin slots de inicio válidos
pacientes_sin_slot = []
or_suite = df_espera["OR Suite"].to_dict()

for i in I:
    li = ell[i]
    Wg = W[g[i]]
    # Slots de inicio válidos: la cirugía completa cabe dentro de la ventana
    slots_inicio_validos = []
    for s in S:
        if s + li - 1 > N_SLOTS:
            continue  # R2: no cabe en la jornada
        # R3: todos los slots ocupados deben estar en la ventana del servicio
        if all((s + k) in Wg for k in range(li)):
            slots_inicio_validos.append(s)

    if not slots_inicio_validos:
        pacientes_sin_slot.append((i, g[i], df_espera.loc[i, "Descripción"],
                                    df_espera.loc[i, "Duración agendada (min)"]))

    j_permitido = or_suite[i]
    for d in D:
        for s in slots_inicio_validos:
            combinaciones_validas.append((i, j_permitido, d, s))

print(f"Combinaciones (i,j,d,s) válidas: {len(combinaciones_validas):,}")
total_teorico = len(I) * len(J) * len(D) * len(S)
print(f"Reducción vs. máximo teórico ({total_teorico:,}): "
      f"{100*(1 - len(combinaciones_validas)/total_teorico):.1f}%")

if pacientes_sin_slot:
    print(f"\n⚠️  ADVERTENCIA: {len(pacientes_sin_slot)} pacientes no tienen slots válidos:")
    for i, srv, desc, dur in pacientes_sin_slot[:10]:
        print(f"    Paciente {i}: {srv} | {desc} | {dur} min")
    if len(pacientes_sin_slot) > 10:
        print(f"    ... y {len(pacientes_sin_slot) - 10} más")

# =============================================================================
# 5. CONSTRUCCIÓN DEL MODELO GUROBI
# =============================================================================

print("\n" + "=" * 70)
print("CONSTRUCCIÓN DEL MODELO")
print("=" * 70)

m = gp.Model("ProgramacionQuirurgica")
m.setParam("TimeLimit", TIEMPO_LIMITE_SEG)
m.setParam("MIPGap", MIP_GAP)

# --- Variables ---
# x[i,j,d,s] = 1 si paciente i empieza cirugía en pabellón j, día d, slot s
x = m.addVars(combinaciones_validas, vtype=GRB.BINARY, name="x")

# u[i,d] = 1 si paciente i ocupa cama básica el día d
u = m.addVars(I, D, vtype=GRB.BINARY, name="u")

# v[i,d] = 1 si paciente i (UCI) ocupa cama UCI el día d
v = m.addVars(I_UCI, D, vtype=GRB.BINARY, name="v")

# ------------------------- Función objetivo con penalización de fin de horizonte ----------------------------------------
# 1. Beneficio por operar (prioridad del paciente)
beneficio_prioridad = gp.quicksum(
    p[i] * x[i, j, d, s] 
    for (i, j, d, s) in combinaciones_validas
)

# 2. Penalización por ocupar camas en el último día del horizonte (N_DIAS)
# Esto desincentiva programar cirugías con largas estadías hacia el final de la semana
penalizacion_basicas = gp.quicksum(
    PENALIZACION_CAMA_BASICA * u[i, N_DIAS] 
    for i in I
)

penalizacion_uci = gp.quicksum(
    PENALIZACION_CAMA_UCI * v[i, N_DIAS] 
    for i in I_UCI
)

# Objetivo final: Maximizar beneficio priorizando la liberación de camas al final
m.setObjective(
    beneficio_prioridad - penalizacion_basicas - penalizacion_uci,
    GRB.MAXIMIZE
)

# --- R1: cada paciente se opera a lo más una vez ---
for i in I:
    m.addConstr(
        gp.quicksum(
            x[i, j, d, s]
            for (ii, j, d, s) in combinaciones_validas if ii == i
        )
        <= 1,
        name=f"R1_paciente_{i}",
    )

# Reorganizamos las combinaciones por (j, d) para R4 más eficiente
combs_por_jd = {}
for (i, j, d, s) in combinaciones_validas:
    combs_por_jd.setdefault((j, d), []).append((i, s))

# --- R4: no solapamiento de cirugías en el mismo pabellón ---
for (j, d), lista in combs_por_jd.items():
    for s in S:
        # Cirugías que están en curso en el slot s
        ocupando = [
            (i, s_prima)
            for (i, s_prima) in lista
            if s_prima <= s <= s_prima + ell[i] - 1
        ]
        if ocupando:
            m.addConstr(
                gp.quicksum(x[i, j, d, s_prima] for (i, s_prima) in ocupando) <= 1,
                name=f"R4_pab{j}_d{d}_s{s}",
            )

# Reorganizamos por (i, d') para R5/R6/R7 más eficiente
combs_por_id = {}
for (i, j, d, s) in combinaciones_validas:
    combs_por_id.setdefault((i, d), []).append((j, s))

# --- R5: vinculación cama básica para pacientes NO-UCI ---
for i in I:
    if i in I_UCI:
        continue  # los UCI los maneja R6
    si = s_perm[i]
    if si == 0:
        continue  # ambulatorio: nunca ocupa cama
    for d_prima in D:
        if (i, d_prima) not in combs_por_id:
            continue
        suma_x = gp.quicksum(x[i, j, d_prima, s] for (j, s) in combs_por_id[(i, d_prima)])
        for d in range(d_prima, min(d_prima + si - 1, N_DIAS) + 1):
            m.addConstr(u[i, d] >= suma_x, name=f"R5_i{i}_dp{d_prima}_d{d}")

# --- R6: vinculación cama básica para pacientes UCI (después de los días UCI) ---
for i in I_UCI:
    si = s_perm[i]
    if si <= DIAS_UCI:
        continue  # paciente nunca pasa a cama básica
    for d_prima in D:
        if (i, d_prima) not in combs_por_id:
            continue
        suma_x = gp.quicksum(x[i, j, d_prima, s] for (j, s) in combs_por_id[(i, d_prima)])
        for d in range(d_prima + DIAS_UCI, min(d_prima + si - 1, N_DIAS) + 1):
            m.addConstr(u[i, d] >= suma_x, name=f"R6_i{i}_dp{d_prima}_d{d}")

# --- R7: vinculación cama UCI durante los primeros DIAS_UCI días ---
for i in I_UCI:
    for d_prima in D:
        if (i, d_prima) not in combs_por_id:
            continue
        suma_x = gp.quicksum(x[i, j, d_prima, s] for (j, s) in combs_por_id[(i, d_prima)])
        for d in range(d_prima, min(d_prima + DIAS_UCI - 1, N_DIAS) + 1):
            m.addConstr(v[i, d] >= suma_x, name=f"R7_i{i}_dp{d_prima}_d{d}")

# --------------------------------------------------
# COTA SUPERIOR CAMA BÁSICA - NO UCI
# (ocupa desde d' hasta d' + s_perm[i] - 1)
# --------------------------------------------------
for i in I:
    if i in I_UCI:
        continue

    si = s_perm[i]
    if si == 0:
        continue  # ambulatorio

    for d in D:
        d_primas_validos = [
            d_prima for d_prima in D
            if d_prima <= d <= d_prima + si - 1
        ]

        m.addConstr(
            u[i, d] <= gp.quicksum(
                x[i, j, d_prima, s]
                for d_prima in d_primas_validos

                if (i, d_prima) in combs_por_id
                for (j, s) in combs_por_id[(i, d_prima)]

            ),
            name=f"lib_u_noUCI_{i}_{d}",
        )


# --------------------------------------------------
# COTA SUPERIOR CAMA BÁSICA - UCI
# (después de los días UCI)
# --------------------------------------------------
for i in I_UCI:
    si = s_perm[i]

    if si <= DIAS_UCI:
        continue  # nunca usa cama básica

    for d in D:
        d_primas_validos = [
            d_prima for d_prima in D
            if d_prima + DIAS_UCI <= d <= d_prima + si - 1
        ]

        m.addConstr(
            u[i, d] <= gp.quicksum(
                x[i, j, d_prima, s]
                for d_prima in d_primas_validos
                if (i, d_prima) in combs_por_id
                for (j, s) in combs_por_id[(i, d_prima)]

            ),
            name=f"lib_u_UCI_{i}_{d}",
        )


# --------------------------------------------------
# COTA SUPERIOR CAMA UCI
# (primeros DIAS_UCI días)
# --------------------------------------------------
for i in I_UCI:
    for d in D:

        d_primas_validos = [
            d_prima for d_prima in D
            if d_prima <= d <= d_prima + DIAS_UCI - 1
        ]

        m.addConstr(
            v[i, d] <= gp.quicksum(
                x[i, j, d_prima, s]
                for d_prima in d_primas_validos
                if (i, d_prima) in combs_por_id
                for (j, s) in combs_por_id[(i, d_prima)]

            ),
            name=f"lib_v_UCI_{i}_{d}",
        )


# --- R8: capacidad de camas básicas ---
for d in D:
    # Restamos las camas ocupadas por la semana anterior
    disp_basicas = CAP_CAMAS_BASICAS - camas_basicas_bloqueadas_el_dia(d)
    disp_basicas = max(0, disp_basicas) # Evitar capacidades negativas por seguridad
    
    m.addConstr(
        gp.quicksum(u[i, d] for i in I) <= disp_basicas,
        name=f"R8_camasBas_d{d}",
    )
# --- R9: capacidad de camas UCI ---
for d in D:
    # Restamos las camas UCI ocupadas por la semana anterior
    disp_uci = CAP_CAMAS_UCI - camas_uci_bloqueadas_el_dia(d)
    disp_uci = max(0, disp_uci) # Evitar capacidades negativas por seguridad
    
    m.addConstr(
        gp.quicksum(v[i, d] for i in I_UCI) <= disp_uci,
        name=f"R9_camasUCI_d{d}",
    )
m.update()
print(f"Variables: {m.NumVars:,}")
print(f"Restricciones: {m.NumConstrs:,}")

# =============================================================================
# 6. RESOLUCIÓN
# =============================================================================

print("\n" + "=" * 70)
print("RESOLUCIÓN")
print("=" * 70)
m.optimize()

# =============================================================================
# 7. RESULTADOS
# =============================================================================

print("\n" + "=" * 70)
print("RESULTADOS")
print("=" * 70)

if m.SolCount == 0:
    print("No se encontró ninguna solución factible.")
else:
    print(f"Estado: {m.Status}")
    print(f"Valor objetivo (suma de prioridades): {m.ObjVal:.0f}")
    print(f"Mejor cota (bound): {m.ObjBound:.0f}")
    if m.ObjBound > 0:
        print(f"Gap: {100*abs(m.ObjBound - m.ObjVal)/abs(m.ObjBound):.2f}%")

    # Construir tabla de cirugías programadas
    asignaciones = []
    for (i, j, d, s) in combinaciones_validas:
        if x[i, j, d, s].X > 0.5:
            hora_inicio = HORA_INICIO_JORNADA * 60 + (s - 1) * SLOT_MIN
            hora_fin = hora_inicio + ell[i] * SLOT_MIN
            asignaciones.append({
                "Correlativo": df_espera.loc[i, "Correlativo"],
                "Servicio": g[i],
                "Descripción": df_espera.loc[i, "Descripción"],
                "Prioridad de paciente": df_espera.loc[i, "Prioridad de paciente"],
                "Prioridad": df_espera.loc[i, "Prioridad de paciente"],
                "Pabellón": j,
                "Día": d,
                "Hora inicio": f"{hora_inicio//60:02d}:{hora_inicio%60:02d}",
                "Hora fin": f"{hora_fin//60:02d}:{hora_fin%60:02d}",
                "Duración (min)": ell[i] * SLOT_MIN,
                "Permanencia": s_perm[i],
                "Requiere UCI": i in I_UCI,
            })

    if len(asignaciones) == 0:
        print("\n⚠️  El modelo terminó pero NO programó ningún paciente.")
        print("    Posibles causas:")
        print("    1. Las ventanas horarias bloquean todas las cirugías (revisar duraciones).")
        print("    2. La capacidad de camas (CAP_CAMAS_BASICAS) es muy restrictiva.")
        print("    3. Algún parámetro está mal configurado.")
        print("\n    Revisa la advertencia de 'pacientes sin slots válidos' arriba.")
    else:
        df_resultado = pd.DataFrame(asignaciones).sort_values(
            by=["Día", "Pabellón", "Hora inicio"]
        ).reset_index(drop=True)

        print(f"\nTotal pacientes programados: {len(df_resultado)} de {len(I)} "
              f"({100*len(df_resultado)/len(I):.1f}%)")
        print(f"Pacientes UCI programados: {df_resultado['Requiere UCI'].sum()} de {len(I_UCI)}")

        print("\nPacientes programados por prioridad:")
        print(df_resultado["Prioridad"].value_counts().sort_index(ascending=False))

        # Exportar resultados a CSV
        os.makedirs(path.dirname(RUTA_CSV_SALIDA), exist_ok=True)
        df_resultado.to_csv(RUTA_CSV_SALIDA, index=False)
        print(f"\nResultados exportados a: {RUTA_CSV_SALIDA}")

        print("\nPrimeras 15 cirugías programadas:")
        print(df_resultado.head(15).to_string())
