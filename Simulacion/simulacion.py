import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import tkinter as tk
import os
import subprocess
import sys
from os import path

# =========================
# CONFIG
# =========================
RANDOM_SEED = 42
np.random.seed(RANDOM_SEED)

SPEED = 200  # ms por paso (menor = más rápido)
TOTAL_CAMAS = 75
TOTAL_UCI = 2
SIMULACION_DIR = path.abspath(path.dirname(__file__))
RUTA_REPROGRAMAR_SCRIPT = path.join(SIMULACION_DIR, "Reprogramar.py")
RUTA_REPROGRAMACION_SALIDA = path.join(SIMULACION_DIR, "resultados", "resultado_programacion.csv")

reprogramacion_pendiente = False


# =========================
# CARGAR CSV
# =========================
ruta = path.join("..","Capstone-Equipo-11","Simulacion","Estado_Inicial", "programacion_inicial.csv")
ruta2 = path.join("..", "Capstone-Equipo-11","Simulacion","Estado_Inicial","lista_espera_base.csv")
df_programacion = pd.read_csv(ruta, sep=",", encoding="utf-8-sig")
df_caso_base = pd.read_csv(ruta2, sep=",", encoding="utf-8-sig")


df_final = df_programacion.merge(
    df_caso_base,
    on="Correlativo",
    how="left",
    suffixes=("_prog", "_caso_base")
)

# =========================
# PREPROCESAMIENTO
# =========================

# Convertir hora a datetime base
BASE_DATE = datetime(2026, 1, 1)
SCHEDULE_ANCHOR_DATE = BASE_DATE.date()

def construir_datetime(dia, hora_str):
    hora = datetime.strptime(hora_str, "%H:%M").time()
    return datetime.combine(
        SCHEDULE_ANCHOR_DATE + timedelta(days=dia - 1),
        hora
    )

df_final["inicio_dt"] = df_final.apply(
    lambda r: construir_datetime(r["Día"], r["Hora inicio"]),
    axis=1
)

df_final["fin_dt"] = df_final.apply(
    lambda r: construir_datetime(
        r["Día"] + (r["Hora fin"] < r["Hora inicio"]),
        r["Hora fin"]
    ),
    axis=1
)

# Formatear datos a tiempo
df_final = df_final.drop(columns=["Duración (min)"])
df_final["Duración agendada (min)"] = pd.to_timedelta(df_final["Duración agendada (min)"], unit = "m")
df_final["duracion_efectiva_intervencion"] = pd.to_timedelta(df_final["duracion_efectiva_intervencion"], unit = "m")

df_final["dias_permanencia_programados"] = pd.to_timedelta(df_final["dias_permanencia_programados"], unit = "d")
df_final["dias_permanencia_efectivos"] = pd.to_timedelta(df_final["dias_permanencia_efectivos"], unit = "d")

# Eliminar duplicados  columnas desc y serv.
df_final["descripcion"] = df_final["Descripción_prog"]
df_final["servicio"] = df_final["Servicio_prog"]

df_final = df_final.drop(columns=["Descripción_prog", "Descripción_caso_base", "Servicio_prog", "Servicio_caso_base"])


df = df_final.copy()

# Ordenar por inicio
df = df.sort_values("inicio_dt")


# =========================
# ESTADO
# =========================

current_time = df["inicio_dt"].min()
SCHEDULE_ANCHOR_DATE = current_time.date()

RUNNING = True

pabellones = df["Pabellón"].unique()

estado_pabellones = {p: None for p in pabellones}
camas = []
camas_uci = []
operaciones_realizadas = []
operaciones_exportadas = 0
cancelaciones_pendientes = []
cirugias_canceladas = set()

eventos = []
day_paused = False
last_paused_date = None
reprogram_triggered_today = False
cancelaciones_dia = 0

# Crear eventos
for _, row in df.iterrows():
    eventos.append({
        "tipo": "inicio",
        "tiempo": row["inicio_dt"],
        "data": row
    })
    eventos.append({
        "tipo": "fin",
        "tiempo": row["fin_dt"],
        "data": row
    })

eventos = sorted(eventos, key=lambda x: x["tiempo"])
final_time = eventos[-1]["tiempo"] if eventos else current_time
week_finished = False

modo_saturado = False



# =========================
# UI
# =========================
root = tk.Tk()
root.title("Simulación Pabellones")

frame = tk.Frame(root)
frame.pack()

labels_pabellon = {}
for p in pabellones:
    lbl = tk.Label(frame, text=f"Pabellón {p}: Libre", width=40, bg="green")
    lbl.pack()
    labels_pabellon[p] = lbl

label_camas = tk.Label(root, text="Camas: 0")
label_camas.pack()

label_tiempo = tk.Label(root, text=str(current_time))
label_tiempo.pack()

label_resumen = tk.Label(root, text="", justify=tk.LEFT, anchor="w", font=("Arial", 10))
label_resumen.pack(fill=tk.X, padx=10, pady=5)

# BOTONES PAUSA/REANUDAR
def pausar():
    global RUNNING
    RUNNING = False
    exportar_estado()
    print("Simulación pausada ⏸")
    label_tiempo.config(text=f"{current_time} (Pausado)")


def reanudar():
    global RUNNING
    RUNNING = True
    label_resumen.config(text="")
    print("Simulación reanudada ▶")
    label_tiempo.config(text=str(current_time))


# Control velocidad
def faster():
    global SPEED
    SPEED = max(50, SPEED - 50)

def slower():
    global SPEED
    SPEED += 50

tk.Button(root, text="Más rápido", command=faster).pack()
tk.Button(root, text="Más lento", command=slower).pack()

tk.Button(root, text="⏸ Pausar", command=pausar, bg="orange").pack(pady=5)
tk.Button(root, text="▶ Reanudar", command=reanudar, bg="lightgreen").pack(pady=5)
tk.Button(root, text="🔁 Reprogramar semana", command=lambda: request_reprogramar(full_week=False), bg="lightblue").pack(pady=5)


def on_closing():
    # Eliminar archivos de estado temporales creados durante la simulación
    try:
        if path.exists(RUTA_ESTADO_ACTUAL):
            for file_name in os.listdir(RUTA_ESTADO_ACTUAL):
                if file_name.endswith(".csv"):
                    file_path = path.join(RUTA_ESTADO_ACTUAL, file_name)
                    os.remove(file_path)
    except Exception as err:
        print(f"Error al limpiar archivos temporales: {err}")
    root.destroy()

root.protocol("WM_DELETE_WINDOW", on_closing)


# =========================
# LÓGICA SIMULACIÓN
# =========================
def eliminar_eventos_correlativo(correlativo):
    global eventos
    eventos = [
        ev for ev in eventos
        if ev["data"]["Correlativo"] != correlativo
    ]


def registrar_cancelacion(row, motivo):
    global cancelaciones_dia, cancelaciones_pendientes, cirugias_canceladas

    cancelaciones_dia += 1
    cancelaciones_pendientes.append(row.to_dict())
    cirugias_canceladas.add(row["Correlativo"])
    eliminar_eventos_correlativo(row["Correlativo"])

    detalle = (
        f"Operación {row['Correlativo']} ({row['descripcion']}) cancelada por {motivo}."
    )
    label_resumen.config(
        text=label_resumen.cget("text") + "\n" + detalle
        if label_resumen.cget("text") else detalle
    )


def procesar_evento(ev):
    global camas, camas_uci, modo_saturado, df

    row = ev["data"]
    pab = row["Pabellón"]

    if ev["tipo"] == "inicio":
        if row["Correlativo"] in cirugias_canceladas:
            return

        estado_pabellones[pab] = row
        labels_pabellon[pab].config(
            text=f"Pabellón {pab}: {row['descripcion']}",
            bg="red"
        )

        #  Si estamos saturados, NO iniciar cirugías con cama
        dias = int(row["dias_permanencia_efectivos"].total_seconds() / 86400)
        if modo_saturado and dias > 0:
            registrar_cancelacion(row, "saturación total de camas")
            # Liberar pabellón
            estado_pabellones[pab] = None
            labels_pabellon[pab].config(text=f"Pabellón {pab}: Libre", bg="green")
            return

    elif ev["tipo"] == "fin":
        if row["Correlativo"] in cirugias_canceladas:
            return

        estado_pabellones[pab] = None
        labels_pabellon[pab].config(
            text=f"Pabellón {pab}: Libre",
            bg="green"
        )

        # --- asignar cama ---
        dias = int(row["dias_permanencia_efectivos"].total_seconds() / 86400)

        cancelada = False
        if dias > 0:
            alta_dia = (ev["tiempo"] + timedelta(days=dias)).date()
            alta = datetime.combine(alta_dia, datetime.strptime("08:00", "%H:%M").time())

            if row["Requiere UCI"]:
                if len(camas_uci) >= TOTAL_UCI:
                    registrar_cancelacion(row, "falta de camas UCI")
                    cancelada = True
                else:
                    camas_uci.append((ev["tiempo"], alta))
            else:
                if len(camas) >= TOTAL_CAMAS:
                    registrar_cancelacion(row, "falta de camas básicas")
                    cancelada = True
                else:
                    camas.append((ev["tiempo"], alta))

        if not cancelada and row["Correlativo"] not in cirugias_canceladas:
            operaciones_realizadas.append({
            "Correlativo": row["Correlativo"],
            "Servicio": row["servicio"],
            "Descripción": row["descripcion"],
            "Pabellón": pab,
            "Día": row["Día"],
            "Hora inicio": row["Hora inicio"],
            "Hora fin": row["Hora fin"],
            "Requiere UCI": row["Requiere UCI"],
            "Fin operación": ev["tiempo"].strftime("%Y-%m-%d %H:%M"),
            "Alta cama": alta.strftime("%Y-%m-%d %H:%M") if dias > 0 else "",
        })
            df.drop(index=df[df["Correlativo"] == row["Correlativo"]].index, inplace=True)

# =========================
# EXPORTAR ESTADO ACTUAL
# =========================
RUTA_ESTADO_ACTUAL = path.join(SIMULACION_DIR, "Estados_Simulacion")

def exportar_estado():
    global operaciones_exportadas, BASE_DATE
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    os.makedirs(RUTA_ESTADO_ACTUAL, exist_ok=True)

    # ---- Estado general
    ruta_general = path.join(RUTA_ESTADO_ACTUAL, "estado_general.csv")
    estado_general = {"current_time": current_time}
    if 'full_week_param' in globals():
        estado_general["full_week"] = full_week_param
    pd.DataFrame([estado_general]).to_csv(ruta_general, index=False)

    # ---- Operaciones pendientes
    pendientes = df[df["inicio_dt"] > current_time].copy()
    if cirugias_canceladas:
        pendientes = pendientes[~pendientes["Correlativo"].isin(cirugias_canceladas)]
    if cancelaciones_pendientes:
        pendientes = pd.concat(
            [pendientes, pd.DataFrame(cancelaciones_pendientes)],
            ignore_index=True,
            sort=False,
        )

    # ---- Estado de camas
    camas_estado = pd.DataFrame([{"inicio": c[0], "fin": c[1]} for c in camas], columns=["inicio", "fin"]) if camas else pd.DataFrame(columns=["inicio", "fin"])

    camas_uci_estado = pd.DataFrame([{"inicio": c[0], "fin": c[1]} for c in camas_uci], columns=["inicio", "fin"]) if camas_uci else pd.DataFrame(columns=["inicio", "fin"])

    # ---- Guardar estado
    pendientes.to_csv(path.join(RUTA_ESTADO_ACTUAL, f"Estado_pendientes.csv"), index=False)
    camas_estado.to_csv(path.join(RUTA_ESTADO_ACTUAL, f"Estado_camas.csv"), index=False)
    camas_uci_estado.to_csv(path.join(RUTA_ESTADO_ACTUAL, f"Estado_uci.csv"), index=False)

    # ---- Guardar operaciones realizadas
    ruta_operaciones = path.join(RUTA_ESTADO_ACTUAL, "Operaciones_realizadas.csv")
    if operaciones_exportadas < len(operaciones_realizadas):
        nuevas_ops = pd.DataFrame(operaciones_realizadas[operaciones_exportadas:])
        if path.exists(ruta_operaciones):
            nuevas_ops.to_csv(ruta_operaciones, mode="a", index=False, header=False)
        else:
            nuevas_ops.to_csv(ruta_operaciones, index=False)
        operaciones_exportadas = len(operaciones_realizadas)

    # ---- Guardar cancelaciones diarias
    ruta_cancelaciones = path.join(RUTA_ESTADO_ACTUAL, "Estado_cancelaciones.csv")
    pd.DataFrame([{
        "Fecha": current_time.strftime("%Y-%m-%d"),
        "Cancelaciones": cancelaciones_dia,
    }]).to_csv(ruta_cancelaciones, index=False)

    if cancelaciones_pendientes:
        ruta_cancelaciones_pendientes = path.join(RUTA_ESTADO_ACTUAL, "Pendientes_cancelados.csv")
        pd.DataFrame(cancelaciones_pendientes).to_csv(ruta_cancelaciones_pendientes, index=False)

    print(f"Estado exportado: {timestamp} ✔")


def asegurarse_resultados_dir():
    resultados_dir = path.dirname(RUTA_REPROGRAMACION_SALIDA)
    os.makedirs(resultados_dir, exist_ok=True)
    return resultados_dir


def eventos_desde_dataframe(df_schedule):
    eventos_nuevos = []
    for _, row in df_schedule.iterrows():
        row = row.copy()
        inicio = construir_datetime(row["Día"], row["Hora inicio"])
        fin = construir_datetime(
            row["Día"] + (row["Hora fin"] < row["Hora inicio"]),
            row["Hora fin"],
        )
        row["inicio_dt"] = inicio
        row["fin_dt"] = fin
        eventos_nuevos.append({"tipo": "inicio", "tiempo": inicio, "data": row})
        eventos_nuevos.append({"tipo": "fin", "tiempo": fin, "data": row})
    return eventos_nuevos


def merge_active_events(new_events):
    active_fin = [
        ev for ev in eventos
        if ev["tipo"] == "fin"
        and ev["tiempo"] > current_time
        and ev["data"]["inicio_dt"] <= current_time
    ]
    future_events = [ev for ev in new_events if ev["tiempo"] > current_time]
    return sorted(active_fin + future_events, key=lambda x: x["tiempo"])


def restaurar_camas_estado():
    global camas, camas_uci
    camas = []
    camas_uci = []
    ruta_camas = path.join(RUTA_ESTADO_ACTUAL, "Estado_camas.csv")
    ruta_uci = path.join(RUTA_ESTADO_ACTUAL, "Estado_uci.csv")

    if path.exists(ruta_camas):
        df_camas = pd.read_csv(ruta_camas)
        df_camas["inicio"] = pd.to_datetime(df_camas["inicio"])
        df_camas["fin"] = pd.to_datetime(df_camas["fin"])
        for _, row in df_camas.iterrows():
            if row["fin"] > current_time:
                camas.append((row["inicio"], row["fin"]))

    if path.exists(ruta_uci):
        df_uci = pd.read_csv(ruta_uci)
        df_uci["inicio"] = pd.to_datetime(df_uci["inicio"])
        df_uci["fin"] = pd.to_datetime(df_uci["fin"])
        for _, row in df_uci.iterrows():
            if row["fin"] > current_time:
                camas_uci.append((row["inicio"], row["fin"]))


def cargar_agenda_desde_csv(ruta_csv):
    global df_programacion, df_final, df, eventos, idx_evento, pabellones, final_time

    if not path.exists(ruta_csv):
        raise FileNotFoundError(f"No existe el archivo de agenda: {ruta_csv}")

    df_programacion = pd.read_csv(ruta_csv, sep="," , encoding="utf-8-sig")

    # Normalize columns from reprogrammed CSV
    df_programacion = df_programacion.rename(columns={
        "Permanencia": "dias_permanencia_efectivos",
        "Duración (min)": "Duración agendada (min)",
        "Descripción": "descripcion",
        "Servicio": "servicio",
        "Requiere UCI": "Requiere UCI",
    })

    # Add missing columns
    df_programacion["dias_permanencia_programados"] = df_programacion["dias_permanencia_efectivos"]
    df_programacion["duracion_efectiva_intervencion"] = df_programacion["Duración agendada (min)"]

    # Convert to timedelta
    df_programacion["Duración agendada (min)"] = pd.to_timedelta(df_programacion["Duración agendada (min)"], unit="m")
    df_programacion["duracion_efectiva_intervencion"] = pd.to_timedelta(df_programacion["duracion_efectiva_intervencion"], unit="m")
    df_programacion["dias_permanencia_programados"] = pd.to_timedelta(df_programacion["dias_permanencia_programados"], unit="d")
    df_programacion["dias_permanencia_efectivos"] = pd.to_timedelta(df_programacion["dias_permanencia_efectivos"], unit="d")

    df_programacion["descripcion"] = df_programacion["descripcion"]
    df_programacion["servicio"] = df_programacion["servicio"]
    df_programacion["inicio_dt"] = df_programacion.apply(
        lambda r: construir_datetime(r["Día"], r["Hora inicio"]),
        axis=1,
    )
    df_programacion["fin_dt"] = df_programacion.apply(
        lambda r: construir_datetime(
            r["Día"] + (r["Hora fin"] < r["Hora inicio"]),
            r["Hora fin"],
        ),
        axis=1,
    )
    df = df_programacion.copy()
    df = df.sort_values("inicio_dt")
    df = df[df["inicio_dt"] > current_time]

    restaurar_camas_estado()
    nuevos_eventos = eventos_desde_dataframe(df)
    eventos = merge_active_events(nuevos_eventos)
    idx_evento = 0


    pabellones = sorted(set(df["Pabellón"]).union(set(estado_pabellones.keys())))
    for p in pabellones:
        if p not in labels_pabellon:
            lbl = tk.Label(frame, text=f"Pabellón {p}: Libre", width=40, bg="green")
            lbl.pack()
            labels_pabellon[p] = lbl
        if estado_pabellones.get(p) is None:
            labels_pabellon[p].config(text=f"Pabellón {p}: Libre", bg="green")

    final_time = max((ev["tiempo"] for ev in eventos), default=current_time)
    print(f"Nueva agenda cargada desde: {ruta_csv}")

def request_reprogramar(full_week=False, already_retried=False):
    global current_time, RUNNING, week_finished, day_paused, reprogram_triggered_today, full_week_param

    print("Iniciando reprogramación...")
    full_week_param = full_week
    exportar_estado()
    asegurarse_resultados_dir()

    # La simulación DEBE estar pausada durante la reprogramación
    RUNNING = False

    try:
        subprocess.run(
            [sys.executable, RUTA_REPROGRAMAR_SCRIPT],
            cwd=SIMULACION_DIR,
            check=True,
        )
    except subprocess.CalledProcessError as err:
        print(f"Error al ejecutar Reprogramar.py: {err}")
        return

    # --- Avance temporal controlado ---

    current_time = current_time

    #  NUEVO: actualizar ancla de agenda
    global SCHEDULE_ANCHOR_DATE
    SCHEDULE_ANCHOR_DATE = current_time.date()

    # --- Cargar nueva agenda ---
    cargar_agenda_desde_csv(RUTA_REPROGRAMACION_SALIDA)

    # Resetear estado de pabellones
    for p in estado_pabellones:
        estado_pabellones[p] = None
        labels_pabellon[p].config(text=f"Pabellón {p}: Libre", bg="green")

    # --- Reset de banderas ---
    week_finished = False
    day_paused = False
    reprogram_triggered_today = True  # importantísimo

    #  NO decidir RUNNING aquí
    # quien llama (botón, saturación, pausa diaria) decide si reanudar

    print("Reprogramación completada.")

    # Reanudar automáticamente si es reprogramación por saturación (daily)
    if not full_week:
        RUNNING = True
        print("Simulación reanudada automáticamente después de reprogramación por saturación.")

    # --- Reintento por baja ocupación ---
    if not full_week and len(camas) < 15 and not already_retried:
        print("Ocupación baja, reintentando reprogramación...")
        request_reprogramar(full_week=False, already_retried=True)

# =========================
# UPDATE LOOP
# =========================
idx_evento = 0


def calcular_altas_proximas(dias_futuros=3):
    """Predice cuántas camas se liberarán en los próximos N días."""
    altas_camas_basicas = 0
    altas_camas_uci = 0
    fecha_limite = current_time + timedelta(days=dias_futuros)
    
    for inicio, alta in camas:
        if current_time < alta <= fecha_limite:
            altas_camas_basicas += 1
    
    for inicio, alta in camas_uci:
        if current_time < alta <= fecha_limite:
            altas_camas_uci += 1
    
    return altas_camas_basicas, altas_camas_uci


def transferir_uci_a_normal():
    """Transfiere pacientes de UCI a cama normal si llevan > 2 días en UCI."""
    global camas, camas_uci
    DIAS_UCI_MAX = 2
    
    camas_uci_vigentes = []
    for inicio, alta in camas_uci:
        dias_en_uci = (current_time - inicio).days
        if dias_en_uci > DIAS_UCI_MAX and len(camas) < TOTAL_CAMAS:
            # Transferir a cama normal
            camas.append((current_time, alta))
            print(f"Paciente transferido de UCI a cama normal (estaba {dias_en_uci} días en UCI)")
        else:
            camas_uci_vigentes.append((inicio, alta))
    
    camas_uci = camas_uci_vigentes


def riesgo_proyectado():
    ocupacion_actual = len(camas)
    
    # cirugías próximas (ej: próximas 24h)
    proximas = df[
        (df["inicio_dt"] > current_time) &
        (df["inicio_dt"] <= current_time + timedelta(hours=24)) &
        (df["dias_permanencia_efectivos"] > pd.Timedelta(0))
    ]

    demanda_futura = len(proximas)

    return ocupacion_actual + demanda_futura > TOTAL_CAMAS


def seleccionar_cancelaciones():
    futuras = df[df["inicio_dt"] > current_time].copy()

    # ordenar por peor prioridad primero
    futuras = futuras.sort_values("Prioridad")

    ocupacion = len(camas)
    canceladas = []

    for _, row in futuras.iterrows():
        if ocupacion < TOTAL_CAMAS:
            break

        canceladas.append(row)
        ocupacion -= 1  # liberar impacto futuro

    return canceladas


def remover_eventos(canceladas):
    global eventos

    ids_cancelados = set(canceladas["Correlativo"])

    eventos = [
        ev for ev in eventos
        if ev["data"]["Correlativo"] not in ids_cancelados
    ]


def hay_riesgo_saturacion():
    return riesgo_proyectado()

def update():
    global current_time, idx_evento, camas, camas_uci
    global RUNNING, week_finished, day_paused
    global last_paused_date, reprogram_triggered_today, cancelaciones_dia, modo_saturado

    # ======================
    # AVANCE DE TIEMPO
    # ======================
    if RUNNING:
        current_time += timedelta(minutes=5)

        while idx_evento < len(eventos) and eventos[idx_evento]["tiempo"] <= current_time:
            procesar_evento(eventos[idx_evento])
            idx_evento += 1

        camas = [c for c in camas if c[1] > current_time]
        camas_uci = [c for c in camas_uci if c[1] > current_time]

        transferir_uci_a_normal()

    # ======================
    # SATURACIÓN REAL (HARD)
    # ======================
    if RUNNING and len(camas) >= TOTAL_CAMAS:

        if not modo_saturado:
            print(" CAMAS LLENAS → entrando en modo saturado")

            modo_saturado = True

            label_resumen.config(
                text=(
                    " Saturación total de camas\n"
                    "Se detienen cirugías con hospitalización\n"
                    "Esperando altas..."
                )
            )

        # El tiempo sigue avanzando, pero las cirugías con cama se cancelan en procesar_evento

    # ======================
    # UI BÁSICA
    # ======================
    label_camas.config(
        text=f"Camas: {len(camas)}/{TOTAL_CAMAS} | UCI: {len(camas_uci)}/{TOTAL_UCI}"
    )
    label_tiempo.config(
        text=str(current_time) + (" (Pausado)" if not RUNNING else "")
    )

    # ======================
    # RESET DIARIO
    # ======================
    if last_paused_date is not None and current_time.date() != last_paused_date:
        day_paused = False
        last_paused_date = None
        reprogram_triggered_today = False
        cancelaciones_dia = 0
        label_resumen.config(text="")

    # ======================
    # RIESGO DE SATURACIÓN
    # ======================
    if RUNNING and not modo_saturado and not reprogram_triggered_today and riesgo_proyectado():
        request_reprogramar(full_week=False)
        reprogram_triggered_today = True

    # ======================
    # SALIDA DE SATURACIÓN
    # ======================
    if modo_saturado and len(camas) < TOTAL_CAMAS * 0.9:

        print(" Camas liberadas → saliendo de modo saturado")

        modo_saturado = False

        #  IMPORTANTE: NO reprogramar aquí inmediatamente

    # ======================
    # PAUSA DIARIA
    # ======================
    if (
        RUNNING and
        not day_paused and
        current_time.hour >= 20 and
        (last_paused_date is None or current_time.date() != last_paused_date)
    ):
        RUNNING = False
        day_paused = True
        last_paused_date = current_time.date()
        reprogram_triggered_today = True

        operaciones_dia = [
            op for op in operaciones_realizadas
            if pd.to_datetime(op["Fin operación"]).date() == current_time.date()
        ]

        resumen = (
            f"--- Resumen del día {current_time.date()} ---\n"
            f"Operaciones realizadas: {len(operaciones_dia)}\n"
            f"Ocupación camas: {len(camas)}/{TOTAL_CAMAS} | "
            f"UCI: {len(camas_uci)}/{TOTAL_UCI}\n"
            f"Operaciones pendientes: {len(df[df['inicio_dt'] > current_time])}\n"
        )

        if cancelaciones_dia > 0:
            resumen += f"Cancelaciones: {cancelaciones_dia}\n"

        resumen += "Presiona 'Reprogramar semana'."

        label_resumen.config(text=resumen)
        label_tiempo.config(text=f"{current_time} (Día terminado)")

        guardar_estado_diario(resumen)

    # ======================
    # FIN DE SEMANA
    # ======================
    if (
        idx_evento >= len(eventos) and
        current_time >= final_time and
        not week_finished
    ):
        week_finished = True
        RUNNING = False
        label_tiempo.config(text=f"{current_time} (Semana terminada)")

    root.after(SPEED, update)


def guardar_estado_diario(resumen_text):
    global current_time, camas, camas_uci, operaciones_realizadas, cancelaciones_pendientes, modo_saturado, cancelaciones_dia, df

    fecha_str = current_time.strftime("%Y-%m-%d")

    carpeta_diaria = path.join(SIMULACION_DIR, "Estados_Diarios", fecha_str)
    os.makedirs(carpeta_diaria, exist_ok=True)

    # =========================
    # 1. RESUMEN GENERAL
    # =========================
    operaciones_dia = [
        op for op in operaciones_realizadas
        if pd.to_datetime(op["Fin operación"]).date() == current_time.date()
    ]

    pendientes = df[df["inicio_dt"] > current_time]

    resumen_dict = {
        "Fecha": fecha_str,
        "Hora corte": current_time.strftime("%H:%M"),
        "Operaciones realizadas": len(operaciones_dia),
        "Ocupacion camas": len(camas),
        "Capacidad camas": TOTAL_CAMAS,
        "Ocupacion UCI": len(camas_uci),
        "Capacidad UCI": TOTAL_UCI,
        "Operaciones pendientes": len(pendientes),
        "Cancelaciones del dia": cancelaciones_dia,
        "Modo saturado": modo_saturado,
        "Resumen": resumen_text
    }

    pd.DataFrame([resumen_dict]).to_csv(
        path.join(carpeta_diaria, "resumen.csv"),
        index=False
    )

    # =========================
    # 2. CAMAS ACTIVAS
    # =========================
    df_camas = pd.DataFrame(camas, columns=["inicio", "alta"]) if camas else pd.DataFrame(columns=["inicio", "alta"])
    df_camas["tipo"] = "basica"

    df_uci = pd.DataFrame(camas_uci, columns=["inicio", "alta"]) if camas_uci else pd.DataFrame(columns=["inicio", "alta"])
    df_uci["tipo"] = "uci"

    df_camas_total = pd.concat([df_camas, df_uci], ignore_index=True)

    if not df_camas_total.empty:
        df_camas_total.to_csv(
            path.join(carpeta_diaria, "camas_activas.csv"),
            index=False
        )

    # =========================
    # 3. OPERACIONES DEL DÍA
    # =========================
    if operaciones_dia:
        pd.DataFrame(operaciones_dia).to_csv(
            path.join(carpeta_diaria, "operaciones_realizadas.csv"),
            index=False
        )

    # =========================
    # 4. CANCELACIONES
    # =========================
    if cancelaciones_pendientes:
        df_cancel = pd.DataFrame(cancelaciones_pendientes)

        if "inicio_dt" in df_cancel.columns:
            df_cancel["inicio_dt"] = pd.to_datetime(df_cancel["inicio_dt"])
            df_cancel_dia = df_cancel[df_cancel["inicio_dt"].dt.date == current_time.date()]
        else:
            df_cancel_dia = df_cancel

        if not df_cancel_dia.empty:
            df_cancel_dia.to_csv(
                path.join(carpeta_diaria, "cancelaciones.csv"),
                index=False
            )

        # =========================
        # 6. CIRUGÍAS CANCELADAS (DETALLADO)
        # =========================
        columnas_utiles = [
            "Correlativo",
            "descripcion",
            "servicio",
            "Prioridad",
            "Requiere UCI",
            "inicio_dt"
        ]

        columnas_existentes = [c for c in columnas_utiles if c in df_cancel.columns]
        df_cancel_detalle = df_cancel[columnas_existentes].copy()

        if "inicio_dt" in df_cancel_detalle.columns:
            df_cancel_detalle["inicio_dt"] = pd.to_datetime(df_cancel_detalle["inicio_dt"])
            df_cancel_detalle = df_cancel_detalle[df_cancel_detalle["inicio_dt"].dt.date == current_time.date()]

        if not df_cancel_detalle.empty:
            df_cancel_detalle.to_csv(
                path.join(carpeta_diaria, "cirugias_canceladas_detalle.csv"),
                index=False
            )

    # =========================
    # 5. OPERACIONES PENDIENTES
    # =========================
    if not pendientes.empty:
        pendientes.to_csv(
            path.join(carpeta_diaria, "pendientes.csv"),
            index=False
        )

    print(f" Estado diario guardado para {fecha_str}")

# =========================
# START
# =========================
update()
root.mainloop()