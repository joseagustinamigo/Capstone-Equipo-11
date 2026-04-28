import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from os import path

# =========================
# CONFIG
# =========================
RANDOM_SEED = 42
np.random.seed(RANDOM_SEED)

# =========================
# CARGAR CSV
# =========================
ruta = path.join("..","Capstone-Equipo-11","Simulacion","resultado_programacion.csv")

df = pd.read_csv(ruta)

# =========================
# PREPROCESAMIENTO
# =========================

# Convertir hora a datetime base
BASE_DATE = datetime(2026, 1, 1)

def construir_datetime(dia, hora_str):
    hora = datetime.strptime(hora_str, "%H:%M").time()
    return datetime.combine(BASE_DATE + timedelta(days=dia-1), hora)

df["inicio_dt"] = df.apply(lambda r: construir_datetime(r["Día"], r["Hora inicio"]), axis=1)
df["fin_dt"] = df.apply(lambda r: construir_datetime(r["Día"], r["Hora fin"]), axis=1)

# =========================
# MODELOS DE INCERTIDUMBRE
# =========================

def sample_duracion(duracion_prog, descripcion):
    # ruido multiplicativo
    return max(10, np.random.normal(duracion_prog, duracion_prog * 0.2))

def sample_estadia(estadia_prog, descripcion):
    if estadia_prog == 0:
        return 0
    return max(1, int(np.random.normal(estadia_prog, 1)))

# =========================
# SIMULACIÓN
# =========================

resultados = []
camas = []
camas_uci = []

for _, row in df.iterrows():

    # --- Simular duración ---
    dur_real = sample_duracion(row["Duración (min)"])
    inicio_real = row["inicio_dt"]
    fin_real = inicio_real + timedelta(minutes=dur_real)

    # --- Simular estadía ---
    dias_estadia = sample_estadia(row["Permanencia"])

    # --- Alta ---
    if dias_estadia > 0:
        alta = (BASE_DATE + timedelta(days=row["Día"]-1)) + timedelta(days=dias_estadia + 1)
    else:
        alta = None

    # --- Registrar camas ---
    if dias_estadia > 0:
        if row["Requiere UCI"]:
            camas_uci.append({
                "inicio": BASE_DATE + timedelta(days=row["Día"]-1),
                "fin": alta
            })
        else:
            camas.append({
                "inicio": BASE_DATE + timedelta(days=row["Día"]-1),
                "fin": alta
            })

    # --- Guardar resultado ---
    resultados.append({
        "Paciente": row["Paciente"],
        "Pabellon": row["Pabellón"],
        "inicio_real": inicio_real,
        "fin_real": fin_real,
        "duracion_real": dur_real,
        "dias_estadia_real": dias_estadia
    })

sim_df = pd.DataFrame(resultados)

# =========================
# OCUPACIÓN DE CAMAS
# =========================

inicio_sim = BASE_DATE
fin_sim = BASE_DATE + timedelta(days= 17)

fechas = pd.date_range(inicio_sim, fin_sim, freq="D")

ocupacion = []

for dia in fechas:
    ocupadas = sum(1 for c in camas if c["inicio"] <= dia < c["fin"])
    ocupadas_uci = sum(1 for c in camas_uci if c["inicio"] <= dia < c["fin"])

    ocupacion.append({
        "fecha": dia,
        "camas_ocupadas": ocupadas,
        "camas_uci_ocupadas": ocupadas_uci,
        "camas_totales": ocupadas + ocupadas_uci
    })

ocup_df = pd.DataFrame(ocupacion)

# =========================
# MÉTRICAS CLAVE
# =========================

print("\n=== MÉTRICAS ===")
print("Máxima ocupación:", ocup_df["camas_totales"].max())
print("Promedio ocupación:", ocup_df["camas_totales"].mean())

# =========================
# EXPORTAR
# =========================

sim_df.to_csv(path.join("..","Capstone-Equipo-11","Simulacion","resultados","resultado_simulacion.csv"), index=False)
ocup_df.to_csv(path.join("..","Capstone-Equipo-11","Simulacion","resultados","ocupacion.csv"), index=False)

print("\nSimulación completada ")