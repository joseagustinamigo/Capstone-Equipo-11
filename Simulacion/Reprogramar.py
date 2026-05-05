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
from datetime import datetime, timedelta

# =============================================================================
# 1. PARÁMETROS DE CONFIGURACIÓN — AJUSTAR SEGÚN NECESIDAD
# =============================================================================

SCRIPT_DIR = path.dirname(path.abspath(__file__))
RUTA_ESPERA = path.join(SCRIPT_DIR, "Estado_Inicial", "lista_espera_base.csv")
RUTA_OPERACIONES = path.join(SCRIPT_DIR, "Estados_Simulacion", "Operaciones_realizadas.csv")
RUTA_PENDIENTES = path.join(SCRIPT_DIR, "Estados_Simulacion", "Estado_pendientes.csv")
RUTA_PROGRAMACION_INICIAL = path.join(SCRIPT_DIR, "Estado_Inicial", "programacion_inicial.csv")

BASE_DATE = datetime(2026, 1, 1)

ruta_general = path.join(SCRIPT_DIR, "Estados_Simulacion", "estado_general.csv")
if path.exists(ruta_general):
    df_gen = pd.read_csv(ruta_general)


if not path.exists(ruta_general):
    raise RuntimeError(
        "No existe estado_general.csv. No se puede reconstruir current_time."
    )

df_gen = pd.read_csv(ruta_general)

if "current_time" not in df_gen.columns:
    raise RuntimeError(
        "estado_general.csv no contiene la columna 'current_time'."
    )

current_time = pd.to_datetime(df_gen["current_time"].iloc[0])

full_week = df_gen.get("full_week", [True]).iloc[0]  # default True

print(f"[Reprogramar] current_time detectado: {current_time}")
print(f"[Reprogramar] full_week: {full_week}")


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
N_PACIENTES_MAX = 300

# Parámetros de Gurobi
TIEMPO_LIMITE_SEG = 300     # 5 minutos
MIP_GAP = 0.05              # detener cuando el gap sea menor al 1%


def normalize_columns(df):
    mapping = {}
    for col in df.columns:
        clean = col.strip()
        if "Correl" in clean:
            mapping[col] = "Correlativo"
        elif "Servicio" in clean:
            mapping[col] = "Servicio"
        elif "Descr" in clean:
            mapping[col] = "Descripción"
        elif "Prioridad" in clean and "paciente" in clean.lower():
            mapping[col] = "Prioridad de paciente"
        elif clean == "Prioridad":
            mapping[col] = "Prioridad"
        elif "Duración agendada" in clean or ("Duraci" in clean and "agendada" in clean.lower()):
            mapping[col] = "Duración agendada (min)"
        elif "Duración (min)" in clean or "Duraci" in clean and "min" in clean.lower():
            mapping[col] = "Duración (min)"
        elif "dias_permanencia_programados" in clean:
            mapping[col] = "dias_permanencia_programados"
        elif "dias_permanencia_efectivos" in clean:
            mapping[col] = "dias_permanencia_efectivos"
        elif "Día" in clean or "Dia" in clean:
            mapping[col] = "Día"
        elif "Requiere UCI" in clean or "Requiere Uci" in clean:
            mapping[col] = "Requiere UCI"
    return df.rename(columns=mapping)


def get_first_remaining_day():
    if not path.exists(RUTA_PENDIENTES):
        return 1
    df_pend = pd.read_csv(RUTA_PENDIENTES, encoding="latin1")
    df_pend = normalize_columns(df_pend)
    if "Día" not in df_pend.columns or df_pend.empty:
        return 1
    return int(df_pend["Día"].min())


def get_operated_correlativos():
    if not path.exists(RUTA_OPERACIONES):
        return set()

    df_ops = pd.read_csv(RUTA_OPERACIONES, encoding="latin1")
    df_ops = normalize_columns(df_ops)
    if "Correlativo" not in df_ops.columns:
        return set()
    return set(df_ops["Correlativo"].dropna().astype(int).tolist())


def get_pending_correlativos():
    if not path.exists(RUTA_PENDIENTES):
        return set()

    df_pend = pd.read_csv(RUTA_PENDIENTES, encoding="latin1")
    df_pend = normalize_columns(df_pend)
    if "Correlativo" not in df_pend.columns:
        return set()
    return set(df_pend["Correlativo"].dropna().astype(int).tolist())


def get_programmed_correlativos():
    if not path.exists(RUTA_PROGRAMACION_INICIAL):
        return set()

    df_prog = pd.read_csv(RUTA_PROGRAMACION_INICIAL, encoding="latin1")
    df_prog = normalize_columns(df_prog)
    if "Correlativo" not in df_prog.columns:
        return set()
    return set(df_prog["Correlativo"].dropna().astype(int).tolist())


# Horizonte fijo de reprogramación desde el tiempo actual (días absolutos)
if full_week:
    HORIZONTE_DIAS = 7
    offset = 7
else:
    HORIZONTE_DIAS = 1
    offset = 1

# d = 0 → hoy + offset, d = 1 → mañana + offset, ..., d = HORIZONTE_DIAS-1 → hoy + offset + HORIZONTE_DIAS-1
D = list(range(HORIZONTE_DIAS))
N_DIAS = len(D)

# Cargar estado de camas ocupadas
RUTA_ESTADO_CAMAS = path.join(SCRIPT_DIR, "Estados_Simulacion", "Estado_camas.csv")
RUTA_ESTADO_UCI = path.join(SCRIPT_DIR, "Estados_Simulacion", "Estado_uci.csv")
# =========================================
# OCUPACIÓN DE CAMAS PARA EL MODELO
# =========================================

# Horizonte fijo de planificación desde current_time
N_DIAS = len(D)

ocupadas_bas = [0] * N_DIAS
ocupadas_uci = [0] * N_DIAS

if path.exists(RUTA_ESTADO_CAMAS):
    df_camas = pd.read_csv(RUTA_ESTADO_CAMAS)
    df_camas["inicio"] = pd.to_datetime(df_camas["inicio"])
    df_camas["fin"] = pd.to_datetime(df_camas["fin"])

    for idx, d in enumerate(D):
        fecha_dia = current_time.date() + timedelta(days=offset + d)
        instante = datetime.combine(
            fecha_dia,
            datetime.strptime("08:00", "%H:%M").time()
        )
        ocupadas_bas[idx] = len(
            df_camas[
                (df_camas["inicio"] <= instante) &
                (df_camas["fin"] > instante)
            ]
        )

if path.exists(RUTA_ESTADO_UCI):
    df_uci = pd.read_csv(RUTA_ESTADO_UCI)
    df_uci["inicio"] = pd.to_datetime(df_uci["inicio"])
    df_uci["fin"] = pd.to_datetime(df_uci["fin"])

    for idx, d in enumerate(D):
        fecha_dia = current_time.date() + timedelta(days=offset + d)
        instante = datetime.combine(
            fecha_dia,
            datetime.strptime("08:00", "%H:%M").time()
        )
        ocupadas_uci[idx] = len(
            df_uci[
                (df_uci["inicio"] <= instante) &
                (df_uci["fin"] > instante)
            ]
        )

print(f"Ocupación inicial camas básicas por día: {ocupadas_bas}")
print(f"Ocupación inicial camas UCI por día: {ocupadas_uci}")
print(f"Ocupación inicial de camas por día: {ocupadas_bas}")
print(f"Ocupación inicial de UCI por día: {ocupadas_uci}")

# =============================================================================
# 2. CARGA Y PREPARACIÓN DE DATOS
# =============================================================================

print("=" * 70)
print("CARGA DE DATOS")
print("=" * 70)

if not path.exists(RUTA_ESPERA):
    raise FileNotFoundError(f"No se encontró el archivo de lista de espera: {RUTA_ESPERA}")


df_espera = pd.read_csv(RUTA_ESPERA, encoding="latin1")
df_espera = normalize_columns(df_espera)

if "Prioridad de paciente" not in df_espera.columns and "Prioridad" in df_espera.columns:
    df_espera["Prioridad de paciente"] = df_espera["Prioridad"]

if "Duración agendada (min)" not in df_espera.columns and "Duración (min)" in df_espera.columns:
    df_espera["Duración agendada (min)"] = df_espera["Duración (min)"]

operados = get_operated_correlativos()
pendientes = get_pending_correlativos()

# Filtrar pacientes ya operados y los que están actualmente en operación.
df_disponibles = df_espera[
    ~df_espera["Correlativo"].isin(operados)].copy()
df_pendientes = df_disponibles[df_disponibles["Correlativo"].isin(pendientes)].copy()
df_pendientes = df_disponibles[
    df_disponibles["Correlativo"].isin(pendientes)
].drop_duplicates(subset="Correlativo")

df_restantes = df_disponibles[~df_disponibles["Correlativo"].isin(pendientes)].copy()

if N_PACIENTES_MAX is not None:
    objetivo = N_PACIENTES_MAX - len(df_pendientes)
    if objetivo > 0:
        df_adicionales = df_restantes.sort_values(
            by=["Prioridad de paciente", "Duración agendada (min)"],
            ascending=[True, True],
        ).head(objetivo)

        df_espera = pd.concat([df_pendientes, df_adicionales], ignore_index=True)
print(f"Pacientes totales en lista de espera: {len(df_espera) + len(operados)}")
print(f"Pacientes ya operados: {len(operados)}")
print(f"Pacientes por reprogramar: {len(df_espera)}")

if df_espera.empty:
    print("No hay pacientes nuevos por operar en la lista de espera.")
    raise SystemExit(0)

if "dias_permanencia_programados" in df_espera.columns:
    df_espera["Permanencia_estimada"] = df_espera["dias_permanencia_programados"].fillna(0).astype(int)
else:
    df_espera["Permanencia_estimada"] = 0

# --- Identificar pacientes que requieren UCI (Vascular + AV Fistula) ---
df_espera["Requiere_UCI"] = (
    (df_espera["Servicio"].astype(str).str.lower() == "vascular")
    & (df_espera["Descripción"].astype(str).str.lower().str.contains("fistula", na=False))
)

# --- Filtro opcional para reducir el tamaño del problema ---
if N_PACIENTES_MAX is not None and len(df_espera) > N_PACIENTES_MAX:
    df_espera = df_espera.sort_values(
        by=["Prioridad de paciente", "Duración agendada (min)"],
        ascending=[True, True],
    ).head(N_PACIENTES_MAX).reset_index(drop=True)
    print(f"Lista de espera filtrada a los top {N_PACIENTES_MAX} pacientes por prioridad.")

print(f"Pacientes a considerar: {len(df_espera)}")
print(f"  - Que requieren UCI: {df_espera['Requiere_UCI'].sum()}")
print(f"  - Ambulatorios (permanencia=0): {(df_espera['Permanencia_estimada']==0).sum()}")

# =============================================================================
# 3. CONJUNTOS Y PARÁMETROS DEL MODELO
# =============================================================================

# Conjuntos
I = df_espera.index.tolist()                           # pacientes
J = list(range(1, N_PABELLONES + 1))                   # pabellones
N_SLOTS = (HORA_FIN_JORNADA - HORA_INICIO_JORNADA) * 60 // SLOT_MIN 
S = list(range(1, N_SLOTS + 1))                        # slots
print(f"\n|I|={len(I)}, |J|={len(J)}, |D|={len(D)}, |S|={len(S)}")

# Parámetros del paciente
p = (6 - df_espera["Prioridad de paciente"]).to_dict()                            # prioridad invertida                                 # prioridad
ell = {i: ceil(df_espera.loc[i, "Duración agendada (min)"] / SLOT_MIN) for i in I}  # nº de slots
s_perm = df_espera["Permanencia_estimada"].to_dict()                               # días permanencia
g = df_espera["Servicio"].to_dict()                                                # servicio

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

# =============================================================================
# 4. PRECÁLCULO DE COMBINACIONES (i,j,d,s) FACTIBLES
# =============================================================================
# Aplicamos R2 (cabe en jornada) y R3 (respeta ventana horaria) ANTES de crear
# variables, para reducir drásticamente el tamaño del modelo.

print("\nGenerando combinaciones factibles (filtro R2 + R3)...")
combinaciones_validas = []  # lista de tuplas (i, j, d, s)

# Diagnóstico: contar pacientes sin slots de inicio válidos
pacientes_sin_slot = []

for i in I:
    li = ell[i]
    Wg = W[g[i]]
    # Slots de inicio válidos: la cirugía completa cabe dentro de la ventana
    slots_inicio_validos = []
    for s in S:
        if s + li - 1 > N_SLOTS:
            continue  # R2: no cabe en la jornada

        hora_inicio = HORA_INICIO_JORNADA * 60 + (s - 1) * SLOT_MIN
        hora_fin = hora_inicio + li * SLOT_MIN
        if hora_fin > HORA_FIN_JORNADA * 60:
            continue  # no puede terminar después del cierre de la jornada

        # R3: todos los slots ocupados deben estar en la ventana del servicio
        if all((s + k) in Wg for k in range(li)):
            slots_inicio_validos.append(s)

    if not slots_inicio_validos:
        pacientes_sin_slot.append((i, g[i], df_espera.loc[i, "Descripción"],
                                    df_espera.loc[i, "Duración agendada (min)"]))

    for j in J:
        for d in D:
            for s in slots_inicio_validos:
                combinaciones_validas.append((i, j, d, s))

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

# --- Función objetivo ---
m.setObjective(
    gp.quicksum(p[i] * x[i, j, d, s] for (i, j, d, s) in combinaciones_validas),
    GRB.MAXIMIZE,
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
        for d in range(d_prima, min(d_prima + si - 1, N_DIAS-1) + 1):
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
        for d in range(d_prima + DIAS_UCI, min(d_prima + si - 1, N_DIAS-1) + 1):
            m.addConstr(u[i, d] >= suma_x, name=f"R6_i{i}_dp{d_prima}_d{d}")

# --- R7: vinculación cama UCI durante los primeros DIAS_UCI días ---
for i in I_UCI:
    for d_prima in D:
        if (i, d_prima) not in combs_por_id:
            continue
        suma_x = gp.quicksum(x[i, j, d_prima, s] for (j, s) in combs_por_id[(i, d_prima)])
        for d in range(d_prima, min(d_prima + DIAS_UCI - 1, N_DIAS-1) + 1):
            m.addConstr(v[i, d] >= suma_x, name=f"R7_i{i}_dp{d_prima}_d{d}")

# --- R8: capacidad de camas básicas ---
for idx, d in enumerate(D):
    m.addConstr(
        gp.quicksum(u[i, d] for i in I) <= CAP_CAMAS_BASICAS - ocupadas_bas[idx],
        name=f"R8_camasBas_d{d}",
    )

# --- R9: capacidad de camas UCI ---
for idx, d in enumerate(D):
    m.addConstr(
        gp.quicksum(v[i, d] for i in I_UCI) <= CAP_CAMAS_UCI - ocupadas_uci[idx],
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
                "Prioridad": p[i],
                "Pabellón": j,
                "Día": d + 1,
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
        archivo_salida = path.join("..","Capstone-Equipo-11","Simulacion","resultados","resultado_programacion.csv")
        os.makedirs(path.dirname(archivo_salida), exist_ok=True)
        df_resultado.to_csv(archivo_salida, index=False)
        print(f"\nResultados exportados a: {archivo_salida}")

        print("\nPrimeras 15 cirugías programadas:")
        print(df_resultado.head(15).to_string())
