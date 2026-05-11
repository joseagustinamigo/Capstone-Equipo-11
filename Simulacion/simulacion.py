import pandas as pd
import numpy as np
from datetime import datetime, timedelta, time
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
ruta = path.join("..","Capstone-Equipo-11","Simulacion","Estado_Inicial", "7_dias.csv") 
ruta2 = path.join("..", "Capstone-Equipo-11","Simulacion","Estado_Inicial","Escenarios","escenario_1.csv")
ruta3 = path.join("..", "Capstone-Equipo-11","Simulacion","Estado_Inicial","caso_base_resultados_asignaciones.csv")
df_programacion = pd.read_csv(ruta, sep=",", encoding="utf-8-sig")
df_escenario_1 = pd.read_csv(ruta2, sep=",", encoding="utf-8-sig")
df_caso_base = pd.read_csv(ruta3, sep=",", encoding="utf-8-sig")

if "Requiere UCI" not in df_caso_base.columns:
    df_caso_base["Requiere UCI"] = (
        df_caso_base["Descripción"] == "AV Fistula"
    )

df_final = df_programacion.merge(  # Cambiar aquí para que caso probar (  df_programacion  / df_caso_base    )
    df_escenario_1,                     
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
df = df.sort_values(["Día", "inicio_dt"])


# =========================
# ESTADO
# =========================
idx_evento = 0
current_time = df["inicio_dt"].min()

RUNNING = True

pabellones = df["Pabellón"].unique()


estado_pabellones = {
    p: {
        "ocupado": False,
        "cirugia_actual": None,
        "fin_programado": None
    }
    for p in pabellones}

estado_pabellones = {p: None for p in pabellones}

camas = []
camas_uci = []
operaciones_realizadas = []
operaciones_exportadas = 0
cirugias_canceladas = set()
cancelaciones_pendientes = []

eventos = []
day_paused = False
last_paused_date = None
cancelaciones_dia = 0

# Crear eventos
records = df.to_dict("records")
for row in records:
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


eventos = sorted(
    eventos,
    key=lambda x: (x["tiempo"], 0 if x["tipo"] == "fin" else 1))

final_time = eventos[-1]["tiempo"] if eventos else current_time
week_finished = False

modo_saturado = False



# =========================
# UI
# =========================
root = tk.Tk()
root.title("Simulación Pabellones")
root.geometry("900x600")
root.configure(bg="#f4f6f7")

# =========================
# HEADER
# =========================
header = tk.Frame(root, bg="#2c3e50", height=60)
header.pack(fill=tk.X)

title = tk.Label(
    header,
    text="Simulación Hospitalaria",
    fg="white",
    bg="#2c3e50",
    font=("Arial", 16, "bold")
)
title.pack(pady=10)

# =========================
# CONTENEDOR PRINCIPAL
# =========================
main_frame = tk.Frame(root, bg="#f4f6f7")
main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

# =========================
# PANEL IZQUIERDO (PABELLONES)
# =========================
frame_pabellones = tk.LabelFrame(
    main_frame,
    text="Pabellones",
    padx=10,
    pady=10,
    bg="white",
    font=("Arial", 11, "bold")
)
frame_pabellones.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)

labels_pabellon = {}

# Grilla fija 
COLUMNAS = 4

for i, p in enumerate(pabellones):
    fila = i // COLUMNAS
    col = i % COLUMNAS

    card = tk.Frame(frame_pabellones, bg="#ecf0f1", bd=1, relief="solid")
    card.grid(row=fila, column=col, padx=8, pady=8, sticky="nsew")

    lbl = tk.Label(
        card,
        text=f"Pabellón {p}\nLibre",
        width=20,
        height=4,
        bg="#27ae60",
        fg="white",
        font=("Arial", 10, "bold"),
        justify="center"
    )
    lbl.pack(fill=tk.BOTH, expand=True)

    labels_pabellon[p] = lbl

# Expandir columnas
for c in range(COLUMNAS):
    frame_pabellones.grid_columnconfigure(c, weight=1)

# =========================
# PANEL DERECHO (INFO + CONTROLES)
# =========================
panel_derecho = tk.Frame(main_frame, bg="#f4f6f7")
panel_derecho.pack(side=tk.RIGHT, fill=tk.Y, padx=5)

# ---- INFO ----
frame_info = tk.LabelFrame(
    panel_derecho,
    text="Estado",
    padx=10,
    pady=10,
    bg="white",
    font=("Arial", 11, "bold")
)
frame_info.pack(fill=tk.X, pady=5)

label_tiempo = tk.Label(frame_info, text=str(current_time), font=("Arial", 10))
label_tiempo.pack(anchor="w")

label_camas = tk.Label(frame_info, text="Camas: 0", font=("Arial", 10))
label_camas.pack(anchor="w")

label_resumen = tk.Label(
    frame_info,
    text="",
    justify=tk.LEFT,
    anchor="w",
    font=("Arial", 10),
    wraplength=250
)
label_resumen.pack(anchor="w", pady=5)

# ---- CONTROLES ----
frame_controles = tk.LabelFrame(
    panel_derecho,
    text="Controles",
    padx=10,
    pady=10,
    bg="white",
    font=("Arial", 11, "bold")
)
frame_controles.pack(fill=tk.X, pady=5)

# BOTONES
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


def faster():
    global SPEED
    SPEED = max(20, SPEED - 50)


def slower():
    global SPEED
    SPEED += 50


def saltar_a_manana():
    global current_time, RUNNING, day_paused

    if not day_paused:
        print("Solo puedes saltar desde la pausa del día.")
        return

    # Ir al día siguiente 07:50
    siguiente_dia = current_time.date() + timedelta(days=1)
    current_time = datetime.combine(siguiente_dia, time(7, 50))

    print(f" Saltando a {current_time}")

    # Mantener la simulación pausada (el usuario decide después)
    RUNNING = False

    # Limpiar resumen visual
    label_resumen.config(
        text=f" Saltado a {current_time.strftime('%Y-%m-%d %H:%M')}\nPresiona 'Reanudar'."
    )

    label_tiempo.config(
        text=str(current_time) + " (Saltado)"
    )


btn_pausa = tk.Button(frame_controles, text="⏸ Pausar", command=pausar, bg="#f39c12", width=20)
btn_pausa.pack(pady=3)

btn_rea = tk.Button(frame_controles, text="▶ Reanudar", command=reanudar, bg="#2ecc71", width=20)
btn_rea.pack(pady=3)

btn_fast = tk.Button(frame_controles, text="⚡ Más rápido", command=faster, width=20)
btn_fast.pack(pady=3)

btn_slow = tk.Button(frame_controles, text="🐢 Más lento", command=slower, width=20)
btn_slow.pack(pady=3)

btn_rep = tk.Button(
    frame_controles,
    text="🔁 Reprogramar semana",
    command=lambda: request_reprogramar(full_week=False),
    bg="#3498db",
    fg="white",
    width=20
)
btn_rep.pack(pady=5)


tk.Button(
    frame_controles,
    text="⏩ Saltar a mañana (07:50)",
    command=saltar_a_manana,
    bg="#9b59b6",
    fg="white",
    width=20
).pack(pady=5)



#-------------------------------   Revisar esto ---------------------------------

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

# --------------------------------           ----------------------------------------

# =========================
# LÓGICA SIMULACIÓN
# =========================
def registrar_cancelacion(row, motivo):
    global cancelaciones_dia, cancelaciones_pendientes, cirugias_canceladas

    correlativo = row["Correlativo"]
    pab = row["Pabellón"]

    # Evitar doble cancelación
    if correlativo in cirugias_canceladas:
        return

    cancelaciones_dia += 1

    
    # Guardar copia segura con prioridad actualizada
    row_cancelado = row.copy()

    #  subir prioridad a 1
    if "Prioridad" in row_cancelado:
        row_cancelado["Prioridad"] = 1

    if "Prioridad de paciente" in row_cancelado:
        row_cancelado["Prioridad de paciente"] = 1

    cancelaciones_pendientes.append(row_cancelado)

    
    if estado_pabellones.get(pab) is not None:
        estado_pabellones[pab] = None
        labels_pabellon[pab].config(
            text=f"Pabellón {pab}: Libre",
            bg="green"
        )

    # Mensaje
    detalle = f"Operación {correlativo} ({row['descripcion']}) cancelada por {motivo}."

    # --- UI 
    texto_actual = label_resumen.cget("text")
    lineas = texto_actual.split("\n") if texto_actual else []

    lineas.append(detalle)

    # limitar tamaño del log
    MAX_LOG = 10
    lineas = lineas[-MAX_LOG:]

    label_resumen.config(text="\n".join(lineas))

# =========================
# LIBERAR CAMAS
# =========================
def liberar_camas(current_time):
    global camas, camas_uci

    camas = [
        (inicio, fin)
        for (inicio, fin) in camas
        if fin > current_time
    ]

    camas_uci = [
        (inicio, fin)
        for (inicio, fin) in camas_uci
        if fin > current_time
    ]


def procesar_evento(ev):
    global camas, camas_uci

    row = ev["data"]
    pab = row["Pabellón"]

    #  liberar camas primero
    liberar_camas(ev["tiempo"])

    #  recalcular saturación dinámicamente
    modo_saturado = (len(camas) >= TOTAL_CAMAS and len(camas_uci) >= TOTAL_UCI)

    #  ignorar si ya fue cancelada
    if row["Correlativo"] in cirugias_canceladas:
        return

    if ev["tipo"] == "inicio":

        dias = int(np.ceil(row["dias_permanencia_efectivos"].total_seconds() / 86400))

        #  validar pabellón ocupado
        if estado_pabellones[pab] is not None:
            registrar_cancelacion(row, "pabellón ocupado")
            return

        #  validar saturación ANTES de ocupar
        if modo_saturado and dias > 0:
            registrar_cancelacion(row, "saturación total de camas")
            return

        #  ocupar pabellón
        estado_pabellones[pab] = row

        labels_pabellon[pab].config(
            text=f"Pabellón {pab}: {row['descripcion']}",
            bg="red"
        )

    elif ev["tipo"] == "fin":

        #  liberar pabellón
        estado_pabellones[pab] = None
        labels_pabellon[pab].config(
            text=f"Pabellón {pab}: Libre",
            bg="green"
        )

        dias = int(np.ceil(row["dias_permanencia_efectivos"].total_seconds() / 86400))

        cancelada = False
        alta = None

        #  lógica correcta de alta (día + dias, a las 08:00)
        if dias > 0:
            fecha_alta = (ev["tiempo"] + timedelta(days=dias)).date()
            alta = datetime.combine(fecha_alta, time(8, 0))

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

        #  registrar operación exitosa
        if not cancelada:
            operaciones_realizadas.append({
                "Correlativo": row["Correlativo"],
                "Servicio": row["servicio"],
                "Descripción": row["descripcion"],
                "Pabellón": pab,
                "Inicio operación": row["Hora inicio"],
                "Fin operación": ev["tiempo"].strftime("%Y-%m-%d %H:%M"),
                "Alta cama": alta.strftime("%Y-%m-%d %H:%M") if alta else "",
            })


# =========================
# EXPORTAR ESTADO ACTUAL
# =========================
RUTA_ESTADO_ACTUAL = path.join(SIMULACION_DIR, "Estados_Simulacion")

def exportar_estado():
    global operaciones_exportadas, BASE_DATE

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    os.makedirs(RUTA_ESTADO_ACTUAL, exist_ok=True)

    # =========================
    # ESTADO GENERAL
    # =========================
    ruta_general = path.join(RUTA_ESTADO_ACTUAL, "estado_general.csv")

    estado_general = {
        "current_time": current_time,
    }

    if 'full_week_param' in globals():
        estado_general["full_week"] = full_week_param

    pd.DataFrame([estado_general]).to_csv(ruta_general, index=False)

    # =========================
    # CIRUGÍAS FUTURAS
    # =========================
    pendientes = df[df["inicio_dt"] > current_time].copy()

    if cirugias_canceladas:
        pendientes = pendientes[
            ~pendientes["Correlativo"].isin(cirugias_canceladas)
        ]

    ruta_pendientes = path.join(RUTA_ESTADO_ACTUAL, "Estado_pendientes.csv")
    pendientes.to_csv(ruta_pendientes, index=False)

    # =========================
    # CIRUGÍAS EN CURSO (NUEVO - CRÍTICO)
    # =========================
    en_curso = df[
        (df["inicio_dt"] <= current_time) &
        (df["fin_dt"] > current_time)
    ].copy()

    ruta_en_curso = path.join(RUTA_ESTADO_ACTUAL, "Estado_en_curso.csv")
    en_curso.to_csv(ruta_en_curso, index=False)

    # =========================
    # ESTADO DE CAMAS
    # =========================
    camas_estado = (
        pd.DataFrame([{"inicio": c[0], "fin": c[1]} for c in camas])
        if camas else pd.DataFrame(columns=["inicio", "fin"])
    )

    camas_uci_estado = (
        pd.DataFrame([{"inicio": c[0], "fin": c[1]} for c in camas_uci])
        if camas_uci else pd.DataFrame(columns=["inicio", "fin"])
    )

    camas_estado.to_csv(path.join(RUTA_ESTADO_ACTUAL, "Estado_camas.csv"), index=False)
    camas_uci_estado.to_csv(path.join(RUTA_ESTADO_ACTUAL, "Estado_uci.csv"), index=False)

    # =========================
    # OPERACIONES REALIZADAS (INCREMENTAL)
    # =========================
    ruta_operaciones = path.join(RUTA_ESTADO_ACTUAL, "Operaciones_realizadas.csv")

    if operaciones_exportadas < len(operaciones_realizadas):
        nuevas_ops = pd.DataFrame(
            operaciones_realizadas[operaciones_exportadas:]
        )

        if path.exists(ruta_operaciones):
            nuevas_ops.to_csv(
                ruta_operaciones,
                mode="a",
                index=False,
                header=False
            )
        else:
            nuevas_ops.to_csv(ruta_operaciones, index=False)

        operaciones_exportadas = len(operaciones_realizadas)

    # =========================
    # CANCELACIONES (SEPARADAS)
    # =========================
    ruta_cancelaciones = path.join(RUTA_ESTADO_ACTUAL, "Estado_cancelaciones.csv")

    df_cancelaciones = pd.DataFrame([{
        "Fecha": current_time.strftime("%Y-%m-%d"),
        "Cancelaciones": cancelaciones_dia,
    }])

    if path.exists(ruta_cancelaciones):
        df_cancelaciones.to_csv(
            ruta_cancelaciones,
            mode="a",
            index=False,
            header=False
        )
    else:
        df_cancelaciones.to_csv(ruta_cancelaciones, index=False)

    # Guardar cancelaciones detalladas (NO mezcladas con pendientes)
    if cancelaciones_pendientes:
        ruta_cancelaciones_pendientes = path.join(
            RUTA_ESTADO_ACTUAL,
            "Pendientes_cancelados.csv"
        )

        pd.DataFrame(cancelaciones_pendientes).to_csv(
            ruta_cancelaciones_pendientes,
            index=False
        )

    print(f"Estado exportado: {timestamp} ✔")


def asegurarse_resultados_dir():
    resultados_dir = path.dirname(RUTA_REPROGRAMACION_SALIDA)
    os.makedirs(resultados_dir, exist_ok=True)
    return resultados_dir


def eventos_desde_dataframe(df_schedule):
    eventos_nuevos = []

    records = df_schedule.to_dict("records")

    for row in records:
        inicio = row["inicio_dt"]
        fin = row["fin_dt"]

        eventos_nuevos.append({
            "tipo": "inicio",
            "tiempo": inicio,
            "data": row
        })

        eventos_nuevos.append({
            "tipo": "fin",
            "tiempo": fin,
            "data": row
        })

    return eventos_nuevos


def merge_active_events(new_events):
    global eventos

    # Eventos de fin de cirugías en curso
    active_fin = [
        ev for ev in eventos
        if ev["tipo"] == "fin"
        and ev["tiempo"] > current_time
        and ev["data"]["inicio_dt"] <= current_time
    ]

    # Eventos futuros nuevos
    future_events = [
        ev for ev in new_events
        if ev["tiempo"] > current_time
    ]

    # Merge robusto con orden correcto
    eventos_merge = active_fin + future_events

    eventos_merge = sorted(
        eventos_merge,
        key=lambda x: (x["tiempo"], 0 if x["tipo"] == "fin" else 1)
    )

    return eventos_merge

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

        camas = [
            (row["inicio"], row["fin"])
            for _, row in df_camas.iterrows()
            if row["fin"] > current_time
        ]

    if path.exists(ruta_uci):
        df_uci = pd.read_csv(ruta_uci)
        df_uci["inicio"] = pd.to_datetime(df_uci["inicio"])
        df_uci["fin"] = pd.to_datetime(df_uci["fin"])

        camas_uci = [
            (row["inicio"], row["fin"])
            for _, row in df_uci.iterrows()
            if row["fin"] > current_time
        ]

def cargar_agenda_desde_csv(ruta_csv):
    global df_programacion, df, eventos, idx_evento, pabellones, final_time

    if not path.exists(ruta_csv):
        raise FileNotFoundError(f"No existe el archivo de agenda: {ruta_csv}")

    df_programacion = pd.read_csv(ruta_csv, sep=",", encoding="utf-8-sig")

    # =========================
    # NORMALIZACIÓN
    # =========================
    df_programacion = df_programacion.rename(columns={
        "Permanencia": "dias_permanencia_efectivos",
        "Duración (min)": "Duración agendada (min)",
        "Descripción": "descripcion",
        "Servicio": "servicio",
    })

    # =========================
    # CONVERSIONES
    # =========================
    df_programacion["Duración agendada (min)"] = pd.to_timedelta(
        df_programacion["Duración agendada (min)"], unit="m"
    )
    df_programacion["duracion_efectiva_intervencion"] = df_programacion["Duración agendada (min)"]

    df_programacion["dias_permanencia_efectivos"] = pd.to_timedelta(
        df_programacion["dias_permanencia_efectivos"], unit="d"
    )
    df_programacion["dias_permanencia_programados"] = df_programacion["dias_permanencia_efectivos"]

    # =========================
    # USAR DIRECTAMENTE LOS DATETIME (NO RECALCULAR)
    # =========================
    if "inicio_dt" not in df_programacion.columns:
        raise ValueError("La agenda reprogramada debe incluir 'inicio_dt'")

    if "fin_dt" not in df_programacion.columns:
        raise ValueError("La agenda reprogramada debe incluir 'fin_dt'")

    df = df_programacion.copy()
    df = df.sort_values("inicio_dt")

    # =========================
    # RESTAURAR CAMAS
    # =========================
    restaurar_camas_estado()

    # =========================
    # CREAR EVENTOS NUEVOS
    # =========================
    nuevos_eventos = eventos_desde_dataframe(df)

    # =========================
    # MEZCLAR EVENTOS CORRECTAMENTE
    # =========================
    eventos = merge_active_events(nuevos_eventos)

    idx_evento = 0

    # =========================
    # PABELLONES (NO RESETEAR OCUPACIÓN)
    # =========================
    pabellones = sorted(set(df["Pabellón"]).union(set(estado_pabellones.keys())))

    for p in pabellones:
        if p not in labels_pabellon:
            #  usar mismo layout
            continue

        if estado_pabellones.get(p) is None:
            labels_pabellon[p].config(
                text=f"Pabellón {p}: Libre",
                bg="green"
            )

    # =========================
    # TIEMPO FINAL
    # =========================
    final_time = max((ev["tiempo"] for ev in eventos), default=current_time)

    print(f"Nueva agenda cargada desde: {ruta_csv}")


def request_reprogramar(full_week=False):
    global RUNNING, week_finished, day_paused, reprogram_triggered_today, full_week_param

    #  SOLO permitir reprogramación en pausa (20:00)
    if not day_paused:
        print(" Solo se puede reprogramar durante la pausa de las 20:00.")
        return

    print("Iniciando reprogramación manual...")

    full_week_param = full_week

    #  Exportar estado actual
    exportar_estado()
    asegurarse_resultados_dir()

    #  Pausar simulación
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

    #  NO modificar current_time
    #  NO cambiar SCHEDULE_ANCHOR_DATE

    #  Cargar nueva agenda
    cargar_agenda_desde_csv(RUTA_REPROGRAMACION_SALIDA)

    #  NO resetear pabellones (mantener estado real)

    # =========================
    # RESET DE FLAGS CONTROLADOS
    # =========================
    week_finished = False
    reprogram_triggered_today = True

    print(" Reprogramación completada. Esperando decisión de reanudación.")

def transferir_uci_a_normal():
    """Transfiere pacientes de UCI a cama normal si llevan > 2 días en UCI."""
    global camas, camas_uci

    DIAS_UCI_MAX = 2
    nuevos_uci = []

    for inicio, alta in camas_uci:
        #  tiempo REAL en días (no truncado)
        dias_en_uci = (current_time - inicio).total_seconds() / 86400

        #  condición correcta
        if dias_en_uci > DIAS_UCI_MAX and len(camas) < TOTAL_CAMAS:
            camas.append((current_time, alta))
            print(f"Paciente transferido de UCI a cama normal ({dias_en_uci:.2f} días en UCI)")
        else:
            nuevos_uci.append((inicio, alta))

    camas_uci = nuevos_uci


def guardar_estado_diario(resumen_text):
    global current_time, camas, camas_uci
    global operaciones_realizadas, cancelaciones_pendientes
    global modo_saturado, cancelaciones_dia, df

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
            df_cancel_dia = df_cancel[
                df_cancel["inicio_dt"].dt.date == current_time.date()
            ]
        else:
            df_cancel_dia = df_cancel

        if not df_cancel_dia.empty:
            df_cancel_dia.to_csv(
                path.join(carpeta_diaria, "cancelaciones.csv"),
                index=False
            )

        # =========================
        # DETALLE CANCELACIONES
        # =========================
        columnas_utiles = [
            "Correlativo",
            "descripcion",
            "servicio",
            "Prioridad",
            "Requiere UCI",
            "inicio_dt"
        ]

        columnas_existentes = [
            c for c in columnas_utiles if c in df_cancel.columns
        ]

        df_cancel_detalle = df_cancel[columnas_existentes].copy()

        if "inicio_dt" in df_cancel_detalle.columns:
            df_cancel_detalle["inicio_dt"] = pd.to_datetime(df_cancel_detalle["inicio_dt"])
            df_cancel_detalle = df_cancel_detalle[
                df_cancel_detalle["inicio_dt"].dt.date == current_time.date()
            ]

        if not df_cancel_detalle.empty:
            df_cancel_detalle.to_csv(
                path.join(carpeta_diaria, "cirugias_canceladas_detalle.csv"),
                index=False
            )

    # =========================
    # 5. PENDIENTES
    # =========================
    if not pendientes.empty:
        pendientes.to_csv(
            path.join(carpeta_diaria, "pendientes.csv"),
            index=False
        )

    print(f"Estado diario guardado para {fecha_str}")

def update():
    global current_time, idx_evento
    global RUNNING, week_finished, day_paused
    global last_paused_date, reprogram_triggered_today
    global cancelaciones_dia, modo_saturado

    # ======================
    # AVANCE DE TIEMPO
    # ======================
    if RUNNING:
        current_time += timedelta(minutes=5)

        # Procesar eventos en orden
        while (
            idx_evento < len(eventos) and
            eventos[idx_evento]["tiempo"] <= current_time
        ):
            procesar_evento(eventos[idx_evento])
            idx_evento += 1

        #  usar SOLO liberar_camas (no duplicar lógica)
        liberar_camas(current_time)

        # Transferencias UCI → normal
        transferir_uci_a_normal()

    # ======================
    # SATURACIÓN REAL 
    # ======================
    if RUNNING and len(camas) >= TOTAL_CAMAS:

        if not modo_saturado:
            print("CAMAS LLENAS → entrando en modo saturado")

            modo_saturado = True

            label_resumen.config(
                text=(
                    "Saturación total de camas\n"
                    "Se cancelarán cirugías con hospitalización\n"
                    "Esperando altas..."
                )
            )

    # ======================
    # SALIDA DE SATURACIÓN
    # ======================
    if modo_saturado and len(camas) < TOTAL_CAMAS:
        print("Camas liberadas → saliendo de modo saturado")
        modo_saturado = False

    # ======================
    # UI
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
    # PAUSA DIARIA (20:00)
    # ======================
    if (
        RUNNING
        and not day_paused
        and current_time.hour >= 20
        and (last_paused_date is None or current_time.date() != last_paused_date)
    ):
        RUNNING = False
        day_paused = True
        last_paused_date = current_time.date()

        # ======================
        # RESUMEN DEL DÍA
        # ======================
        operaciones_dia = [
            op for op in operaciones_realizadas
            if pd.to_datetime(op["Fin operación"]).date() == current_time.date()
        ]

        pendientes = df[df["inicio_dt"] > current_time]

        camas_disponibles = TOTAL_CAMAS - len(camas)

        resumen = (
            f"--- Resumen del día {current_time.date()} ---\n"
            f"Operaciones realizadas: {len(operaciones_dia)}\n"
            f"Ocupación camas: {len(camas)}/{TOTAL_CAMAS} | "
            f"UCI: {len(camas_uci)}/{TOTAL_UCI}\n"
            f"Operaciones pendientes: {len(pendientes)}\n"
        )

        if cancelaciones_dia > 0:
            resumen += f"Cancelaciones: {cancelaciones_dia}\n"

        #  sugerencia 
        if camas_disponibles < 10:
            resumen += (
                "\n Baja disponibilidad de camas\n"
                "Evaluar reprogramación manual\n"
                "Presiona 'Reprogramar semana' si lo deseas."
            )
        else:
            resumen += "\nPresiona 'Reanudar' para continuar."

        label_resumen.config(text=resumen)
        label_tiempo.config(text=f"{current_time} (Día terminado)")

        guardar_estado_diario(resumen)
        exportar_estado()

    # ======================
    # FIN DE SEMANA
    # ======================
    if (
        idx_evento >= len(eventos)
        and current_time >= final_time
        and not week_finished
    ):
        week_finished = True
        RUNNING = False

        label_tiempo.config(
            text=f"{current_time} (Semana terminada)"
        )

    # ======================
    # LOOP
    # ======================
    root.after(SPEED, update)

# =========================
# START
# =========================
update()
root.mainloop()