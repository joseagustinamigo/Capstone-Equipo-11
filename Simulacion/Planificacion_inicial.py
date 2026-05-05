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
from os import path

# =============================================================================
# 1. PARÁMETROS DE CONFIGURACIÓN — AJUSTAR SEGÚN NECESIDAD
# =============================================================================

# Ruta del archivo Excel con los datos
RUTA_EXCEL = path.join("..","Capstone-Equipo-11","preprocesamiento","Datos","Datos Operaciones y lista de espera.xlsx")

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
N_PACIENTES_MAX = 1257

# Parámetros de Gurobi
TIEMPO_LIMITE_SEG = 300     # 10 minutos
MIP_GAP = 0.005              # detener cuando el gap sea menor al 1%

# =============================================================================
# 2. CARGA Y PREPARACIÓN DE DATOS
# =============================================================================

print("=" * 70)
print("CARGA DE DATOS")
print("=" * 70)

df_base = pd.read_excel(RUTA_EXCEL, sheet_name="Datos base")
df_espera = pd.read_excel(RUTA_EXCEL, sheet_name="Lista de espera")

print(f"Registros históricos (Datos base): {len(df_base)}")
print(f"Lista de espera: {len(df_espera)}")

# --- Estimar días de permanencia para la lista de espera ---
# La lista de espera no incluye 'Días de permanencia programados'. Estimamos
# usando el mínmo del histórico agrupando por descripción de la cirugía.
permanencia_por_desc = (
    df_base.groupby("Descripción")["Días de permanencia programados"]
    .min()
    .astype(int)
    .to_dict()
)

df_espera["Permanencia_estimada"] = df_espera["Descripción"].map(permanencia_por_desc)

# Verificación: que todas las descripciones tengan permanencia asignada
faltantes = df_espera["Permanencia_estimada"].isna().sum()
if faltantes > 0:
    print(f"ADVERTENCIA: {faltantes} pacientes sin permanencia estimada. Se asume 0.")
    df_espera["Permanencia_estimada"] = df_espera["Permanencia_estimada"].fillna(0).astype(int)

# --- Identificar pacientes que requieren UCI (Vascular + AV Fistula) ---
df_espera["Requiere_UCI"] = (
    (df_espera["Servicio"].str.lower() == "vascular")
    & (df_espera["Descripción"].str.lower().str.contains("fistula", na=False))
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
D = list(range(1, N_DIAS + 1))                         # días
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

# --- R8: capacidad de camas básicas ---
for d in D:
    m.addConstr(
        gp.quicksum(u[i, d] for i in I) <= CAP_CAMAS_BASICAS,
        name=f"R8_camasBas_d{d}",
    )

# --- R9: capacidad de camas UCI ---
for d in D:
    m.addConstr(
        gp.quicksum(v[i, d] for i in I_UCI) <= CAP_CAMAS_UCI,
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
        archivo_salida = path.join("..","Capstone-Equipo-11","Simulacion","resultados","resultado_programacion.csv")
        df_resultado.to_csv(archivo_salida, index=False)
        print(f"\nResultados exportados a: {archivo_salida}")

        print("\nPrimeras 15 cirugías programadas:")
        print(df_resultado.head(15).to_string())
