import pandas as pd
import numpy as np
from datetime import datetime, timedelta, time
import tkinter as tk
import os
import subprocess
import sys
from os import path
import unicodedata


def normalize_column_name(name):
    if pd.isna(name):
        return name
    normalized = unicodedata.normalize("NFKD", str(name))
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    return " ".join(normalized.split()).strip()


def normalize_dataframe_columns(df):
    canonical = {
        "correlativo": "Correlativo",
        "id paciente": "ID paciente",
        "fecha": "Fecha",
        "or suite": "OR Suite",
        "servicio": "Servicio",
        "descripcion": "Descripción",
        "descripcion": "Descripción",
        "duracion agendada (min)": "Duración agendada (min)",
        "duracion (min)": "Duración (min)",
        "prioridad de paciente": "Prioridad de paciente",
        "prioridad": "Prioridad",
        "dias de permanencia efectivos": "Días de permanencia efectivos",
        "dias de permanencia programados": "Días de permanencia programados",
        "duracion agendada (min)": "Duración agendada (min)",
    }
    rename_map = {}
    for col in df.columns:
        key = normalize_column_name(col).lower()
        rename_map[col] = canonical.get(key, str(col).strip())
    normalized = df.rename(columns=rename_map)
    if normalized.columns.duplicated().any():
        normalized = collapse_duplicate_columns(normalized)
    return normalized


def collapse_duplicate_columns(df):
    if not df.columns.duplicated().any():
        return df
    result = pd.DataFrame(index=df.index)
    for col in dict.fromkeys(df.columns):
        same = [c for c in df.columns if c == col]
        if len(same) == 1:
            result[col] = df[same[0]]
        else:
            result[col] = df[same].bfill(axis=1).iloc[:, 0]
    return result

# ============================================================================
# CONFIGURACIÓN
# ============================================================================
RANDOM_SEED = 42
np.random.seed(RANDOM_SEED)

SPEED = 1
TOTAL_CAMAS = 75
TOTAL_UCI = 2
DIAS_UCI_MAX = 2

SIMULACION_DIR = path.abspath(path.dirname(__file__))
RUTA_REPROGRAMAR_SCRIPT = path.join(SIMULACION_DIR, "Reprogramar.py")
RUTA_REPROGRAMACION_SALIDA = path.join(SIMULACION_DIR, "resultados", "resultado_programacion.csv")

# Estructura de directorios estandarizada
RUTA_ESTADO_ACTUAL = path.join(SIMULACION_DIR, "Estados_Simulacion")
DIR_LOGS = path.join(RUTA_ESTADO_ACTUAL, "logs")
DIR_OPERACIONES = path.join(RUTA_ESTADO_ACTUAL, "operaciones")
DIR_RECUROS = path.join(RUTA_ESTADO_ACTUAL, "recursos")
DIR_SNAPSHOTS = path.join(RUTA_ESTADO_ACTUAL, "snapshots")

BASE_DATE = datetime(2026, 1, 1)
SCHEDULE_ANCHOR_DATE = BASE_DATE.date()

# Estados de operación
ESTADO_PROGRAMADA = "PROGRAMADA"
ESTADO_EN_CURSO = "EN_CURSO"
ESTADO_REALIZADA = "REALIZADA"
ESTADO_CANCELADA = "CANCELADA"

# Esquema estándar de columnas para operaciones
OPERACIONES_SCHEMA = [
    "Correlativo", "Servicio", "Descripción", "Pabellón",
    "Día", "Hora inicio", "Hora fin",
    "Prioridad", "Requiere UCI",
    "Duración agendada (min)", "Permanencia",
    "Fecha inicio dt", "Fecha fin dt"
]

# ============================================================================
# FUNCIONES DE PREPARACIÓN DE DATOS
# ============================================================================
def parse_hora(hora_val):
    """Convierte una hora HH:MM, datetime o time a datetime.time."""
    if isinstance(hora_val, time):
        return hora_val
    if isinstance(hora_val, datetime):
        return hora_val.time()
    if pd.isna(hora_val):
        raise ValueError("Hora vacía o inválida")

    hora_txt = str(hora_val).strip()
    if len(hora_txt) >= 5:
        hora_txt = hora_txt[:5]
    return datetime.strptime(hora_txt, "%H:%M").time()


def construir_datetime(dia, hora_str, anchor_date=None):
    anchor = anchor_date or SCHEDULE_ANCHOR_DATE
    hora = parse_hora(hora_str)
    return datetime.combine(anchor + timedelta(days=int(dia) - 1), hora)


def fecha_inicio_siguiente_agenda():
    """La agenda reprogramada usa días relativos 1..7 desde el día siguiente."""
    return current_time.date() + timedelta(days=1)


def limpiar_resultado_reprogramacion():
    if path.exists(RUTA_REPROGRAMACION_SALIDA):
        os.remove(RUTA_REPROGRAMACION_SALIDA)


def preparar_dataframe():
    """Carga y prepara datos iniciales."""
    ruta_programacion = path.join(SIMULACION_DIR, "Estado_Inicial", "resultado_programacion_corregido.csv")
    ruta_escenario = path.join(SIMULACION_DIR, "Estado_Inicial", "Escenarios", "escenario_1.csv")

    df_prog = pd.read_csv(ruta_programacion, sep=",", encoding="utf-8-sig")
    df_esc = pd.read_csv(ruta_escenario, sep=",", encoding="utf-8-sig")

    df = df_prog.merge(df_esc, on="Correlativo", how="left", suffixes=("_prog", "_caso_base"))

    # Crear datetime
    df["inicio_dt"] = df.apply(lambda r: construir_datetime(r["Día"], r["Hora inicio"]), axis=1)
    df["fin_dt"] = df.apply(
        lambda r: construir_datetime(
            r["Día"] + (parse_hora(r["Hora fin"]) < parse_hora(r["Hora inicio"])),
            r["Hora fin"],
        ),
        axis=1
    )

    # Convertir duraciones
    df["Duración agendada (min)"] = pd.to_timedelta(df["Duración agendada (min)"], unit="m")
    df["duracion_efectiva_intervencion"] = pd.to_timedelta(df["duracion_efectiva_intervencion"], unit="m")
    df["dias_permanencia_programados"] = pd.to_timedelta(df["dias_permanencia_programados"], unit="d")
    df["dias_permanencia_efectivos"] = pd.to_timedelta(df["dias_permanencia_efectivos"], unit="d")

    # Normalizar columnas
    df["descripcion"] = df.get("Descripción_prog", df.get("Descripción", ""))
    df["servicio"] = df.get("Servicio_prog", df.get("Servicio", ""))

    cols_drop = ["Descripción_prog", "Descripción_caso_base", "Servicio_prog", "Servicio_caso_base", "Duración (min)"]
    df = df.drop(columns=[c for c in cols_drop if c in df.columns])

    return df.sort_values(["Día", "inicio_dt"]).reset_index(drop=True)


df = preparar_dataframe()

# ============================================================================
# ESTADO INICIAL
# ============================================================================
idx_evento = 0
current_time = df["inicio_dt"].min()
RUNNING = True
week_finished = False
day_paused = False
last_paused_date = None
reprogram_triggered_today = False
modo_saturado = False
cancelaciones_dia = 0
full_week_param = False

# Pabellones
pabellones = sorted(df["Pabellón"].unique())
estado_pabellones = {p: None for p in pabellones}

# Camas y operaciones
camas = []
camas_uci = []

# Tracker de operaciones con estado detallado
operaciones_estado = {}  # {correlativo: {estado, timestamp, detalles}}
auditoria_cambios = []   # Registro de todos los cambios de estado
operaciones_exportadas = 0

# Eventos
eventos = []
for row in df.to_dict("records"):
    correlativo = row["Correlativo"]
    operaciones_estado[correlativo] = {
        "estado": ESTADO_PROGRAMADA,
        "timestamp_estado": current_time,
        "data": row.copy()
    }
    eventos.append({"tipo": "inicio", "tiempo": row["inicio_dt"], "data": row})
    eventos.append({"tipo": "fin", "tiempo": row["fin_dt"], "data": row})

eventos = sorted(eventos, key=lambda x: (x["tiempo"], 0 if x["tipo"] == "fin" else 1))
final_time = eventos[-1]["tiempo"] if eventos else current_time



# ============================================================================
# INTERFAZ GRÁFICA - DISEÑO PROFESIONAL HOSPITALARIO
# ============================================================================

# Paleta de colores hospitalaria
COLOR_HEADER = "#1a5490"
COLOR_PRIMARY = "#2471a3"
COLOR_ACCENT = "#117a65"
COLOR_WARNING = "#ca6f1e"
COLOR_DANGER = "#a93226"
COLOR_BG = "#ecf0f1"
COLOR_WHITE = "#ffffff"
COLOR_TEXT = "#2c3e50"

root = tk.Tk()
root.title("Sistema de Simulación Hospitalaria - Planificación de Pabellones")
root.geometry("1400x800")
root.configure(bg=COLOR_BG)

# ========== HEADER ==========
header = tk.Frame(root, bg=COLOR_HEADER, height=80)
header.pack(fill=tk.X)

header_title = tk.Label(header, text="SISTEMA DE SIMULACIÓN HOSPITALARIA",
                        fg=COLOR_WHITE, bg=COLOR_HEADER, font=("Arial", 18, "bold"))
header_title.pack(pady=8)

header_subtitle = tk.Label(header, text="Planificación y Optimización de Pabellones Quirúrgicos",
                          fg=COLOR_WHITE, bg=COLOR_HEADER, font=("Arial", 10))
header_subtitle.pack()

# ========== CONTENEDOR PRINCIPAL ==========
main_frame = tk.Frame(root, bg=COLOR_BG)
main_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=15)

# ========== PANEL IZQUIERDO: PABELLONES + INFORMACIÓN SCROLLEABLE ==========
panel_izquierdo = tk.Frame(main_frame, bg=COLOR_BG)
panel_izquierdo.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)

# Pabellones
frame_pabellones = tk.LabelFrame(panel_izquierdo, text="PABELLONES QUIRÚRGICOS",
                                 padx=12, pady=12, bg=COLOR_WHITE,
                                 font=("Arial", 12, "bold"), fg=COLOR_HEADER,
                                 border=2, relief=tk.FLAT)
frame_pabellones.pack(fill=tk.BOTH, expand=True, pady=5)

labels_pabellon = {}
COLUMNAS = 4

for i, p in enumerate(pabellones):
    fila, col = i // COLUMNAS, i % COLUMNAS
    card = tk.Frame(frame_pabellones, bg=COLOR_ACCENT, bd=0, relief="flat")
    card.grid(row=fila, column=col, padx=8, pady=8, sticky="nsew")

    lbl = tk.Label(card, text=f"PABELLÓN {p}\n● LIBRE", width=16, height=5,
                   bg=COLOR_ACCENT, fg=COLOR_WHITE,
                   font=("Arial", 10, "bold"), justify="center")
    lbl.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
    labels_pabellon[p] = lbl

for c in range(COLUMNAS):
    frame_pabellones.grid_columnconfigure(c, weight=1)

# Panel de información scrolleable
frame_info_scroll = tk.LabelFrame(panel_izquierdo, text="INFORMACIÓN DETALLADA",
                                  padx=6, pady=6, bg=COLOR_WHITE,
                                  font=("Arial", 11, "bold"), fg=COLOR_HEADER,
                                  border=1, relief=tk.FLAT)
frame_info_scroll.pack(fill=tk.BOTH, expand=False, pady=5, ipady=6)

# Canvas con scrollbar
canvas_info = tk.Canvas(frame_info_scroll, bg=COLOR_WHITE, highlightthickness=0)
scrollbar_info = tk.Scrollbar(frame_info_scroll, orient=tk.VERTICAL, command=canvas_info.yview)
frame_info_contenido = tk.Frame(canvas_info, bg=COLOR_WHITE)

frame_info_contenido.bind(
    "<Configure>",
    lambda e: canvas_info.configure(scrollregion=canvas_info.bbox("all"))
)

canvas_info.create_window((0, 0), window=frame_info_contenido, anchor="nw")
canvas_info.config(yscrollcommand=scrollbar_info.set)

canvas_info.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
scrollbar_info.pack(side=tk.RIGHT, fill=tk.Y)

label_resumen = tk.Label(frame_info_contenido, text="", justify=tk.LEFT, anchor="nw",
                         font=("Arial", 9), fg=COLOR_TEXT, wraplength=350, bg=COLOR_WHITE)
label_resumen.pack(anchor="nw", pady=5, padx=5, fill=tk.BOTH, expand=True)

# ========== PANEL DERECHO: TIEMPO, RECURSOS Y CONTROLES ==========
panel_derecho = tk.Frame(main_frame, bg=COLOR_BG, width=400)
panel_derecho.pack(side=tk.RIGHT, fill=tk.BOTH, expand=False, padx=5)

# --- PANEL DE TIEMPO ---
frame_tiempo = tk.LabelFrame(panel_derecho, text="TIEMPO ACTUAL", padx=12, pady=10,
                             bg=COLOR_WHITE, font=("Arial", 11, "bold"), fg=COLOR_HEADER,
                             border=1, relief=tk.FLAT)
frame_tiempo.pack(fill=tk.X, pady=8)

label_tiempo = tk.Label(frame_tiempo, text=str(current_time),
                        font=("Arial", 14, "bold"), fg=COLOR_PRIMARY)
label_tiempo.pack()

# --- PANEL DE OCUPACIÓN DE RECURSOS ---
frame_recursos = tk.LabelFrame(panel_derecho, text="OCUPACIÓN DE RECURSOS", padx=12, pady=10,
                               bg=COLOR_WHITE, font=("Arial", 11, "bold"), fg=COLOR_HEADER,
                               border=1, relief=tk.FLAT)
frame_recursos.pack(fill=tk.X, pady=8)

label_camas = tk.Label(frame_recursos, text="Camas Básicas: 0/75",
                       font=("Arial", 10), fg=COLOR_TEXT)
label_camas.pack(anchor="w", pady=3)

label_uci = tk.Label(frame_recursos, text="Camas UCI: 0/2",
                     font=("Arial", 10), fg=COLOR_TEXT)
label_uci.pack(anchor="w", pady=3)

# --- PANEL DE ESTADÍSTICAS SEMANA ---
frame_stats = tk.LabelFrame(panel_derecho, text="ESTADÍSTICAS SEMANA", padx=12, pady=10,
                            bg=COLOR_WHITE, font=("Arial", 11, "bold"), fg=COLOR_HEADER,
                            border=1, relief=tk.FLAT)
frame_stats.pack(fill=tk.X, pady=8)

label_realizadas = tk.Label(frame_stats, text="Realizadas: 0",
                            font=("Arial", 9), fg=COLOR_ACCENT, anchor="w")
label_realizadas.pack(anchor="w", pady=2)

label_pendientes = tk.Label(frame_stats, text="Pendientes: 0",
                            font=("Arial", 9), fg=COLOR_PRIMARY, anchor="w")
label_pendientes.pack(anchor="w", pady=2)

label_canceladas = tk.Label(frame_stats, text="Canceladas: 0",
                            font=("Arial", 9), fg=COLOR_DANGER, anchor="w")
label_canceladas.pack(anchor="w", pady=2)

label_en_curso = tk.Label(frame_stats, text="En Curso: 0",
                          font=("Arial", 9), fg=COLOR_WARNING, anchor="w")
label_en_curso.pack(anchor="w", pady=2)

# --- PANEL DE CONTROLES ---
frame_controles = tk.LabelFrame(panel_derecho, text="CONTROLES", padx=12, pady=10,
                                bg=COLOR_WHITE, font=("Arial", 11, "bold"), fg=COLOR_HEADER,
                                border=1, relief=tk.FLAT)
frame_controles.pack(fill=tk.BOTH, expand=True, pady=8)

def pausar():
    global RUNNING
    RUNNING = False
    exportar_estado()
    label_tiempo.config(text=f"{current_time} (PAUSADO)")

def reanudar():
    global RUNNING, current_time, day_paused
    RUNNING = True
    label_resumen.config(text="")

    # Si está en pausa de las 20:00, saltar automáticamente a mañana 07:50
    if day_paused and current_time.hour >= 20:
        siguiente_dia = current_time.date() + timedelta(days=1)
        current_time = datetime.combine(siguiente_dia, time(7, 50))
        day_paused = False

    label_tiempo.config(text=str(current_time))

def faster():
    global SPEED
    SPEED = max(20, SPEED - 50)

def slower():
    global SPEED
    SPEED += 50

btn_pausar = tk.Button(frame_controles, text="⏸ PAUSAR", command=pausar,
                       bg=COLOR_WARNING, fg=COLOR_WHITE, width=28, font=("Arial", 10, "bold"),
                       relief=tk.FLAT, padx=10, pady=8)
btn_pausar.pack(pady=5)

btn_reanudar = tk.Button(frame_controles, text="▶ REANUDAR", command=reanudar,
                         bg=COLOR_ACCENT, fg=COLOR_WHITE, width=28, font=("Arial", 10, "bold"),
                         relief=tk.FLAT, padx=10, pady=8)
btn_reanudar.pack(pady=5)

# Fila de controles de velocidad
frame_velocidad = tk.Frame(frame_controles, bg=COLOR_WHITE)
frame_velocidad.pack(pady=5)

tk.Button(frame_velocidad, text="⚡ Rápido", command=faster,
          bg=COLOR_PRIMARY, fg=COLOR_WHITE, width=13, font=("Arial", 9, "bold"),
          relief=tk.FLAT, padx=5, pady=6).pack(side=tk.LEFT, padx=3)

tk.Button(frame_velocidad, text="🐌 Lento", command=slower,
          bg=COLOR_PRIMARY, fg=COLOR_WHITE, width=13, font=("Arial", 9, "bold"),
          relief=tk.FLAT, padx=5, pady=6).pack(side=tk.LEFT, padx=3)

tk.Button(frame_controles, text="🔁 REPROGRAMAR", command=lambda: request_reprogramar(False),
          bg=COLOR_DANGER, fg=COLOR_WHITE, width=28, font=("Arial", 10, "bold"),
          relief=tk.FLAT, padx=10, pady=8).pack(pady=5)

def on_closing():
    try:
        if path.exists(RUTA_ESTADO_ACTUAL):
            for f in os.listdir(RUTA_ESTADO_ACTUAL):
                if f.endswith(".csv"):
                    os.remove(path.join(RUTA_ESTADO_ACTUAL, f))
    except Exception as e:
        pass
    root.destroy()

root.protocol("WM_DELETE_WINDOW", on_closing)


# ============================================================================
# FUNCIONES DE LÓGICA DE SIMULACIÓN
# ============================================================================

def cambiar_estado_operacion(correlativo, nuevo_estado, motivo=""):
    """Cambia estado de una operación y registra auditoría."""
    if correlativo not in operaciones_estado:
        return

    estado_anterior = operaciones_estado[correlativo]["estado"]

    if estado_anterior == nuevo_estado:
        return  # Sin cambios

    operaciones_estado[correlativo]["estado"] = nuevo_estado
    operaciones_estado[correlativo]["timestamp_estado"] = current_time

    # Registrar en auditoría
    auditoria_cambios.append({
        "Correlativo": correlativo,
        "Timestamp": current_time.strftime("%Y-%m-%d %H:%M:%S"),
        "Estado anterior": estado_anterior,
        "Estado nuevo": nuevo_estado,
        "Motivo": motivo
    })


def liberar_camas(ct):
    """Libera camas cuyo tiempo de alta ya pasó."""
    global camas, camas_uci
    camas = [(inicio, fin) for inicio, fin in camas if fin > ct]
    camas_uci = [(inicio, fin) for inicio, fin in camas_uci if fin > ct]


def registrar_cancelacion(row, motivo):
    """Registra una cirugía cancelada."""
    global cancelaciones_dia

    correlativo = row["Correlativo"]

    # Evitar doble cancelación
    if operaciones_estado[correlativo]["estado"] == ESTADO_CANCELADA:
        return

    cancelaciones_dia += 1
    cambiar_estado_operacion(correlativo, ESTADO_CANCELADA, motivo)

    pab = row["Pabellón"]
    if estado_pabellones.get(pab) is not None:
        estado_pabellones[pab] = None
        labels_pabellon[pab].config(text=f"Pabellón {pab}: Libre", bg="green")

    detalle = f"Operación {correlativo} ({row['descripcion']}) cancelada: {motivo}"
    texto = label_resumen.cget("text").split("\n") if label_resumen.cget("text") else []
    texto.append(detalle)
    label_resumen.config(text="\n".join(texto[-10:]))


def procesar_evento(ev):
    """Procesa eventos de inicio/fin de cirugías."""
    global camas, camas_uci, modo_saturado

    row = ev["data"]
    correlativo = row["Correlativo"]
    pab = row["Pabellón"]

    # Ignorar si ya fue cancelada
    if operaciones_estado[correlativo]["estado"] == ESTADO_CANCELADA:
        return

    liberar_camas(ev["tiempo"])
    modo_saturado_temp = (len(camas) >= TOTAL_CAMAS and len(camas_uci) >= TOTAL_UCI)

    if ev["tipo"] == "inicio":
        dias = int(np.ceil(row["dias_permanencia_efectivos"].total_seconds() / 86400))

        if estado_pabellones[pab] is not None:
            registrar_cancelacion(row, "pabellón ocupado")
            return

        if modo_saturado_temp and dias > 0:
            registrar_cancelacion(row, "saturación de camas")
            return

        # Marcar como en curso
        cambiar_estado_operacion(correlativo, ESTADO_EN_CURSO)
        estado_pabellones[pab] = row
        labels_pabellon[pab].config(text=f"Pabellón {pab}: {row['descripcion']}", bg="red")

    elif ev["tipo"] == "fin":
        estado_pabellones[pab] = None
        labels_pabellon[pab].config(text=f"Pabellón {pab}: Libre", bg="green")

        dias = int(np.ceil(row["dias_permanencia_efectivos"].total_seconds() / 86400))
        cancelada = False
        alta = None

        if dias > 0:
            fecha_alta = (ev["tiempo"] + timedelta(days=dias)).date()
            alta = datetime.combine(fecha_alta, time(8, 0))

            if row["Requiere UCI"]:
                if len(camas_uci) >= TOTAL_UCI:
                    registrar_cancelacion(row, "falta UCI")
                    cancelada = True
                else:
                    camas_uci.append((ev["tiempo"], alta))
            else:
                if len(camas) >= TOTAL_CAMAS:
                    registrar_cancelacion(row, "falta camas")
                    cancelada = True
                else:
                    camas.append((ev["tiempo"], alta))

        if not cancelada:
            # Marcar como realizada
            cambiar_estado_operacion(correlativo, ESTADO_REALIZADA)
            operaciones_estado[correlativo]["fecha_alta"] = alta


def transferir_uci_a_normal():
    """Transfiere pacientes de UCI a cama normal después de DIAS_UCI_MAX."""
    global camas, camas_uci

    nuevos_uci = []
    for inicio, alta in camas_uci:
        dias_en_uci = (current_time - inicio).total_seconds() / 86400
        if dias_en_uci > DIAS_UCI_MAX and len(camas) < TOTAL_CAMAS:
            camas.append((current_time, alta))
        else:
            nuevos_uci.append((inicio, alta))
    camas_uci = nuevos_uci



# ============================================================================
# FUNCIONES DE EXPORTACIÓN ESTANDARIZADA
# ============================================================================

def crear_directorios():
    """Crea estructura de directorios estandarizada."""
    for dir_path in [RUTA_ESTADO_ACTUAL, DIR_LOGS, DIR_OPERACIONES, DIR_RECUROS, DIR_SNAPSHOTS]:
        os.makedirs(dir_path, exist_ok=True)


def obtener_operaciones_por_estado(estado):
    """Retorna lista de operaciones en un estado específico."""
    return [
        {**operaciones_estado[corr]["data"],
         "Estado": estado,
         "Timestamp Estado": operaciones_estado[corr]["timestamp_estado"].strftime("%Y-%m-%d %H:%M:%S")}
        for corr in operaciones_estado
        if operaciones_estado[corr]["estado"] == estado
    ]


def exportar_operaciones_realizadas():
    """Exporta operaciones completadas exitosamente."""
    crear_directorios()

    ops_realizadas = obtener_operaciones_por_estado(ESTADO_REALIZADA)

    if ops_realizadas:
        df_realizar = pd.DataFrame(ops_realizadas)
        # Seleccionar columnas relevantes en orden
        cols = [c for c in OPERACIONES_SCHEMA + ["Estado", "Timestamp Estado"] if c in df_realizar.columns]
        df_realizar = df_realizar[cols]

        ruta = path.join(DIR_OPERACIONES, "01_Operaciones_Realizadas.csv")
        df_realizar.to_csv(ruta, index=False, encoding="utf-8-sig")
        return len(ops_realizadas)
    return 0


def exportar_operaciones_pendientes():
    """Exporta operaciones programadas pero no iniciadas aún."""
    crear_directorios()

    ops_pendientes = obtener_operaciones_por_estado(ESTADO_PROGRAMADA)

    if ops_pendientes:
        df_pend = pd.DataFrame(ops_pendientes)
        cols = [c for c in OPERACIONES_SCHEMA + ["Estado", "Timestamp Estado"] if c in df_pend.columns]
        df_pend = df_pend[cols]

        ruta = path.join(DIR_OPERACIONES, "02_Operaciones_Pendientes.csv")
        df_pend.to_csv(ruta, index=False, encoding="utf-8-sig")
        return len(ops_pendientes)
    return 0


def exportar_operaciones_canceladas():
    """Exporta operaciones que fueron canceladas."""
    crear_directorios()

    ops_canceladas = obtener_operaciones_por_estado(ESTADO_CANCELADA)

    if ops_canceladas:
        df_cancel = pd.DataFrame(ops_canceladas)
        cols = [c for c in OPERACIONES_SCHEMA + ["Estado", "Timestamp Estado"] if c in df_cancel.columns]
        df_cancel = df_cancel[cols]

        ruta = path.join(DIR_OPERACIONES, "03_Operaciones_Canceladas.csv")
        df_cancel.to_csv(ruta, index=False, encoding="utf-8-sig")
        return len(ops_canceladas)
    return 0


def exportar_operaciones_en_curso():
    """Exporta operaciones actualmente en ejecución."""
    crear_directorios()

    ops_en_curso = obtener_operaciones_por_estado(ESTADO_EN_CURSO)

    if ops_en_curso:
        df_curso = pd.DataFrame(ops_en_curso)
        cols = [c for c in OPERACIONES_SCHEMA + ["Estado", "Timestamp Estado"] if c in df_curso.columns]
        df_curso = df_curso[cols]

        ruta = path.join(DIR_OPERACIONES, "00_Operaciones_En_Curso.csv")
        df_curso.to_csv(ruta, index=False, encoding="utf-8-sig")
        return len(ops_en_curso)
    return 0


def exportar_auditoria():
    """Exporta log de auditoría con todos los cambios de estado."""
    crear_directorios()

    if auditoria_cambios:
        df_audit = pd.DataFrame(auditoria_cambios)
        ruta = path.join(DIR_LOGS, "Auditoria_Cambios_Estado.csv")
        df_audit.to_csv(ruta, index=False, encoding="utf-8-sig")


def exportar_resurcos_hospitalarios():
    """Exporta estado de camas y recursos."""
    crear_directorios()

    # Camas básicas
    if camas:
        df_camas = pd.DataFrame([{
            "Inicio": inicio.strftime("%Y-%m-%d %H:%M:%S"),
            "Fin": fin.strftime("%Y-%m-%d %H:%M:%S"),
            "Tipo": "Básica",
            "Estado": "Ocupada"
        } for inicio, fin in camas])
    else:
        df_camas = pd.DataFrame(columns=["Inicio", "Fin", "Tipo", "Estado"])

    # Camas UCI
    if camas_uci:
        df_uci = pd.DataFrame([{
            "Inicio": inicio.strftime("%Y-%m-%d %H:%M:%S"),
            "Fin": fin.strftime("%Y-%m-%d %H:%M:%S"),
            "Tipo": "UCI",
            "Estado": "Ocupada"
        } for inicio, fin in camas_uci])
        df_camas = pd.concat([df_camas, df_uci], ignore_index=True)

    if not df_camas.empty:
        ruta = path.join(DIR_RECUROS, "Estado_Camas.csv")
        df_camas.to_csv(ruta, index=False, encoding="utf-8-sig")

    # Resumen de ocupación
    resumen_recursos = {
        "Timestamp": current_time.strftime("%Y-%m-%d %H:%M:%S"),
        "Camas_Basicas_Ocupadas": len(camas),
        "Camas_Basicas_Total": TOTAL_CAMAS,
        "Camas_UCI_Ocupadas": len(camas_uci),
        "Camas_UCI_Total": TOTAL_UCI,
        "Tasa_Ocupacion_Basicas": f"{len(camas)/TOTAL_CAMAS*100:.1f}%",
        "Tasa_Ocupacion_UCI": f"{len(camas_uci)/TOTAL_UCI*100:.1f}%"
    }

    df_resumen = pd.DataFrame([resumen_recursos])
    ruta_resumen = path.join(DIR_RECUROS, "Resumen_Ocupacion.csv")
    if path.exists(ruta_resumen):
        df_resumen.to_csv(ruta_resumen, mode="a", index=False, header=False, encoding="utf-8-sig")
    else:
        df_resumen.to_csv(ruta_resumen, index=False, encoding="utf-8-sig")


def exportar_snapshot_completo():
    """Exporta snapshot completo del estado actual."""
    crear_directorios()

    timestamp = current_time.strftime("%Y%m%d_%H%M%S")
    archivo_snapshot = path.join(DIR_SNAPSHOTS, f"Snapshot_{timestamp}.csv")

    snapshot_data = {
        "Timestamp": current_time.strftime("%Y-%m-%d %H:%M:%S"),
        "Operaciones_Realizadas": len(obtener_operaciones_por_estado(ESTADO_REALIZADA)),
        "Operaciones_Pendientes": len(obtener_operaciones_por_estado(ESTADO_PROGRAMADA)),
        "Operaciones_En_Curso": len(obtener_operaciones_por_estado(ESTADO_EN_CURSO)),
        "Operaciones_Canceladas": len(obtener_operaciones_por_estado(ESTADO_CANCELADA)),
        "Camas_Ocupadas": len(camas),
        "UCI_Ocupadas": len(camas_uci),
        "Modo_Saturado": modo_saturado
    }

    df_snapshot = pd.DataFrame([snapshot_data])
    df_snapshot.to_csv(archivo_snapshot, index=False, encoding="utf-8-sig")


def exportar_estado():
    """Exporta estado completo de la simulación."""
    crear_directorios()

    # Exportar operaciones por estado
    exportar_operaciones_en_curso()
    exportar_operaciones_realizadas()
    exportar_operaciones_pendientes()
    exportar_operaciones_canceladas()

    # Exportar auditoría y recursos
    exportar_auditoria()
    exportar_resurcos_hospitalarios()
    exportar_snapshot_completo()


def exportar_resumen_dia():
    """Exporta resumen detallado del día."""
    crear_directorios()

    fecha_str = current_time.strftime("%Y-%m-%d")

    # Contar operaciones del día
    realizadas_hoy = [o for o in obtener_operaciones_por_estado(ESTADO_REALIZADA)
                      if o.get("Fecha inicio dt", "")[:10] == fecha_str]
    canceladas_hoy = [o for o in obtener_operaciones_por_estado(ESTADO_CANCELADA)
                      if o.get("Fecha inicio dt", "")[:10] == fecha_str]

    resumen_dia = {
        "Fecha": fecha_str,
        "Hora_Corte": current_time.strftime("%H:%M"),
        "Operaciones_Realizadas_Hoy": len(realizadas_hoy),
        "Operaciones_Canceladas_Hoy": len(canceladas_hoy),
        "Operaciones_Pendientes": len(obtener_operaciones_por_estado(ESTADO_PROGRAMADA)),
        "Camas_Ocupadas": len(camas),
        "UCI_Ocupadas": len(camas_uci),
        "Capacidad_Camas": TOTAL_CAMAS,
        "Capacidad_UCI": TOTAL_UCI,
        "Modo_Saturado": modo_saturado
    }

    ruta_resumen_dia = path.join(DIR_LOGS, f"Resumen_Dia_{fecha_str}.csv")
    pd.DataFrame([resumen_dia]).to_csv(ruta_resumen_dia, index=False, encoding="utf-8-sig")


def restaurar_camas_estado():
    """Restaura estado de camas desde archivos previos."""
    global camas, camas_uci

    camas, camas_uci = [], []

    ruta_camas = path.join(DIR_RECUROS, "Estado_Camas.csv")
    if path.exists(ruta_camas):
        df_c = pd.read_csv(ruta_camas)
        df_c["Inicio"] = pd.to_datetime(df_c["Inicio"])
        df_c["Fin"] = pd.to_datetime(df_c["Fin"])

        camas_temp = [(row["Inicio"], row["Fin"]) for _, row in df_c.iterrows()
                      if row["Tipo"] == "Básica" and row["Fin"] > current_time]
        camas_uci_temp = [(row["Inicio"], row["Fin"]) for _, row in df_c.iterrows()
                          if row["Tipo"] == "UCI" and row["Fin"] > current_time]

        camas = camas_temp
        camas_uci = camas_uci_temp


def cargar_agenda_desde_csv(ruta_csv, anchor_date=None):
    """Carga nueva agenda desde archivo CSV usando días relativos al ancla indicada."""
    global df, eventos, idx_evento, pabellones, final_time, operaciones_estado, week_finished

    if not path.exists(ruta_csv):
        raise FileNotFoundError(f"No existe: {ruta_csv}")

    anchor_date = anchor_date or fecha_inicio_siguiente_agenda()
    df_new = pd.read_csv(ruta_csv, sep=",", encoding="utf-8-sig")
    df_new = normalize_dataframe_columns(df_new)

    df_new = df_new.rename(columns={
        "Permanencia": "dias_permanencia_efectivos",
        "Duración (min)": "Duración agendada (min)",
    })

    if "Pabellón" not in df_new.columns and "OR Suite" in df_new.columns:
        df_new["Pabellón"] = df_new["OR Suite"]
    if "OR Suite" not in df_new.columns and "Pabellón" in df_new.columns:
        df_new["OR Suite"] = df_new["Pabellón"]
    if "Prioridad" not in df_new.columns and "Prioridad de paciente" in df_new.columns:
        df_new["Prioridad"] = df_new["Prioridad de paciente"]
    if "descripcion" not in df_new.columns and "Descripción" in df_new.columns:
        df_new["descripcion"] = df_new["Descripción"]
    if "Descripción" not in df_new.columns and "descripcion" in df_new.columns:
        df_new["Descripción"] = df_new["descripcion"]
    if "servicio" not in df_new.columns and "Servicio" in df_new.columns:
        df_new["servicio"] = df_new["Servicio"]
    if "Servicio" not in df_new.columns and "servicio" in df_new.columns:
        df_new["Servicio"] = df_new["servicio"]

    required = [
        "Correlativo", "Servicio", "Descripción", "Pabellón", "OR Suite",
        "Día", "Hora inicio", "Hora fin", "Duración agendada (min)",
        "dias_permanencia_efectivos", "Requiere UCI",
    ]
    missing = [col for col in required if col not in df_new.columns]
    if missing:
        raise ValueError(f"La agenda reprogramada no tiene columnas requeridas: {missing}")

    for col in ["Correlativo", "Pabellón", "OR Suite", "Día"]:
        df_new[col] = pd.to_numeric(df_new[col], errors="coerce")
    df_new["Duración agendada (min)"] = pd.to_numeric(df_new["Duración agendada (min)"], errors="coerce")
    df_new["dias_permanencia_efectivos"] = pd.to_numeric(df_new["dias_permanencia_efectivos"], errors="coerce")
    df_new = df_new.dropna(subset=required).reset_index(drop=True)
    if df_new.empty:
        raise ValueError("La agenda reprogramada no contiene operaciones válidas")

    for col in ["Correlativo", "Pabellón", "OR Suite", "Día"]:
        df_new[col] = df_new[col].astype(int)

    def parse_bool(value):
        if isinstance(value, (bool, np.bool_)):
            return bool(value)
        if pd.isna(value):
            return False
        return str(value).strip().lower() in {"true", "1", "si", "sí", "yes", "y"}

    df_new["Requiere UCI"] = df_new["Requiere UCI"].apply(parse_bool)

    df_new["inicio_dt"] = df_new.apply(
        lambda r: construir_datetime(r["Día"], r["Hora inicio"], anchor_date),
        axis=1,
    )
    df_new["fin_dt"] = df_new.apply(
        lambda r: construir_datetime(
            r["Día"] + (parse_hora(r["Hora fin"]) < parse_hora(r["Hora inicio"])),
            r["Hora fin"],
            anchor_date,
        ),
        axis=1,
    )

    df_new["Duración agendada (min)"] = pd.to_timedelta(df_new["Duración agendada (min)"], unit="m")
    df_new["duracion_efectiva_intervencion"] = df_new["Duración agendada (min)"]
    df_new["dias_permanencia_efectivos"] = pd.to_timedelta(df_new["dias_permanencia_efectivos"], unit="d")
    df_new["dias_permanencia_programados"] = df_new["dias_permanencia_efectivos"]

    df = df_new.sort_values("inicio_dt").reset_index(drop=True)

    # Actualizar estado de operaciones reprogramables. Las realizadas/en curso se preservan.
    for row in df.to_dict("records"):
        correlativo = row["Correlativo"]
        estado_actual = operaciones_estado.get(correlativo, {}).get("estado")

        if estado_actual in {ESTADO_REALIZADA, ESTADO_EN_CURSO}:
            continue

        if correlativo not in operaciones_estado:
            operaciones_estado[correlativo] = {
                "estado": ESTADO_PROGRAMADA,
                "timestamp_estado": current_time,
                "data": row.copy()
            }
        else:
            operaciones_estado[correlativo]["data"] = row.copy()
            if estado_actual != ESTADO_PROGRAMADA:
                cambiar_estado_operacion(
                    correlativo,
                    ESTADO_PROGRAMADA,
                    "Reprogramada en nueva agenda",
                )
            else:
                operaciones_estado[correlativo]["timestamp_estado"] = current_time

    restaurar_camas_estado()

    # Reconstruir eventos
    eventos = []
    for row in df.to_dict("records"):
        correlativo = row["Correlativo"]
        if operaciones_estado[correlativo]["estado"] != ESTADO_PROGRAMADA:
            continue

        if row["fin_dt"] <= current_time:
            continue
        if row["inicio_dt"] <= current_time < row["fin_dt"]:
            eventos.append({"tipo": "fin", "tiempo": row["fin_dt"], "data": row})
        else:
            eventos.append({"tipo": "inicio", "tiempo": row["inicio_dt"], "data": row})
            eventos.append({"tipo": "fin", "tiempo": row["fin_dt"], "data": row})

    eventos = sorted(eventos, key=lambda x: (x["tiempo"], 0 if x["tipo"] == "fin" else 1))
    idx_evento = 0
    week_finished = False

    pabellones = sorted(set(df["Pabellón"]).union(set(estado_pabellones.keys())))
    for p in pabellones:
        if p in estado_pabellones:
            estado_pabellones[p] = None
        if p in labels_pabellon and estado_pabellones.get(p) is None:
            labels_pabellon[p].config(text=f"Pabellón {p}: Libre", bg="green")

    final_time = max((ev["tiempo"] for ev in eventos), default=current_time)
    print(
        f"Agenda cargada desde {ruta_csv}: {len(df)} operaciones, "
        f"{len(eventos)} eventos, ancla {anchor_date}"
    )


def preparar_datos_reprogramacion():
    """
    Prepara los datos para la reprogramación de la semana siguiente.
    - Obtiene operaciones canceladas de la semana actual
    - Selecciona 500 operaciones más prioritarias de la lista de espera
    - Crea CSV con estos datos para Reprogramar.py
    """
    global df

    try:
        # Ruta del Excel con lista de espera completa
        ruta_excel = path.join(SIMULACION_DIR, "..", "preprocesamiento", "Datos",
                              "Datos Operaciones y lista de espera.xlsx")
        ruta_excel = path.abspath(ruta_excel)

        # Leer lista de espera completa
        df_lista_espera = pd.read_excel(ruta_excel, sheet_name="Lista de espera")
        df_datos_base = pd.read_excel(ruta_excel, sheet_name="Datos base")

        print("\n" + "="*70)
        print("PREPARACIÓN DE DATOS PARA REPROGRAMACIÓN")
        print("="*70)

        # 1. Obtener operaciones canceladas (máxima prioridad)
        ops_canceladas = obtener_operaciones_por_estado(ESTADO_CANCELADA)
        print(f"\n1. Operaciones canceladas en semana: {len(ops_canceladas)}")

        df_canceladas = pd.DataFrame(ops_canceladas) if ops_canceladas else pd.DataFrame()
        canceladas_correlativas = set()

        if not df_canceladas.empty:
            if "Prioridad de paciente" not in df_canceladas.columns and "Prioridad" in df_canceladas.columns:
                df_canceladas["Prioridad de paciente"] = df_canceladas["Prioridad"]
            if "Duración agendada (min)" not in df_canceladas.columns and "Duración (min)" in df_canceladas.columns:
                df_canceladas["Duración agendada (min)"] = df_canceladas["Duración (min)"]

            if "Duración agendada (min)" in df_canceladas.columns:
                df_canceladas["Duración agendada (min)"] = df_canceladas["Duración agendada (min)"].apply(
                    lambda x: x.total_seconds() / 60 if isinstance(x, pd.Timedelta) else x
                )
                df_canceladas["Duración agendada (min)"] = pd.to_numeric(
                    df_canceladas["Duración agendada (min)"], errors="coerce"
                )

            canceladas_correlativas = set(df_canceladas["Correlativo"].dropna().tolist())
            print(f"   Correlativas canceladas: {df_canceladas['Correlativo'].tolist()[:10]}")

        # 2. Obtener operaciones ya realizadas
        ops_realizadas = obtener_operaciones_por_estado(ESTADO_REALIZADA)
        realizadas_correlativas = {o["Correlativo"] for o in ops_realizadas}

        ops_en_curso = obtener_operaciones_por_estado(ESTADO_EN_CURSO)
        en_curso_correlativas = {o["Correlativo"] for o in ops_en_curso}

        ops_prog = obtener_operaciones_por_estado(ESTADO_PROGRAMADA)
        prog_correlativas = {o["Correlativo"] for o in ops_prog}

        print(f"   Realizadas en semana: {len(realizadas_correlativas)}")
        print(f"   En curso: {len(en_curso_correlativas)}")
        print(f"   Aún programadas: {len(prog_correlativas)}")

        # 3. Filtrar candidatos disponibles
        excluir_correlativas = (
            realizadas_correlativas
            | en_curso_correlativas
            | prog_correlativas
            | canceladas_correlativas
        )

        df_disponibles = df_lista_espera[
            ~df_lista_espera["Correlativo"].isin(excluir_correlativas)
        ].copy()

        print(f"\n2. Disponibles en lista de espera: {len(df_disponibles)}")

        # 4. Seleccionar 500
        if len(df_disponibles) > 0:
            top_500 = min(500, len(df_disponibles))
            df_top_500 = (
                df_disponibles
                .sort_values(
                    by=["Prioridad de paciente", "Duración agendada (min)"],
                    ascending=[True, True],
                )
                .head(top_500)
                .copy()
            )
            print(f"   Seleccionadas top 500 más prioritarias: {len(df_top_500)}")
        else:
            df_top_500 = pd.DataFrame()
            print("    No hay disponibles en lista de espera")

        # 5. Combinar
        if df_canceladas.empty and df_top_500.empty:
            print("    No hay pacientes candidatos para reprogramar")
            return False

        df_para_reprogramar = pd.concat([df_canceladas, df_top_500], ignore_index=True)
        df_para_reprogramar = df_para_reprogramar.drop_duplicates(subset=["Correlativo"], keep="first")

        df_para_reprogramar = normalize_dataframe_columns(df_para_reprogramar)

        # ---- COALESCE ----
        def coalesce(df, main_col, alternatives):
            if main_col not in df.columns:
                df[main_col] = pd.NA
            for alt in alternatives:
                if alt in df.columns:
                    df[main_col] = df[main_col].fillna(df[alt])
            return df

        df_para_reprogramar = coalesce(df_para_reprogramar, "Descripción", ["descripcion", "Descripcion"])
        df_para_reprogramar = coalesce(df_para_reprogramar, "Servicio", ["servicio"])
        df_para_reprogramar = coalesce(df_para_reprogramar, "Prioridad de paciente", ["Prioridad"])
        df_para_reprogramar = coalesce(df_para_reprogramar, "Duración agendada (min)", ["Duración (min)", "Duracion agendada (min)"])

        # ---- LIMPIEZA ----
        for col in ["Descripción", "Servicio"]:
            df_para_reprogramar[col] = (
                df_para_reprogramar[col]
                .astype("string")
                .str.strip()
                .replace({"": pd.NA})
            )

        # ---- PARSE MINUTOS ----
        def parse_minutes(value):
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
                except:
                    try:
                        return pd.to_timedelta(value).total_seconds() / 60
                    except:
                        return np.nan
            return np.nan

        df_para_reprogramar["Duración agendada (min)"] = df_para_reprogramar["Duración agendada (min)"].apply(parse_minutes)
        df_para_reprogramar["Duración agendada (min)"] = pd.to_numeric(df_para_reprogramar["Duración agendada (min)"], errors="coerce")
        df_para_reprogramar["Prioridad de paciente"] = pd.to_numeric(df_para_reprogramar["Prioridad de paciente"], errors="coerce")
        df_para_reprogramar["Correlativo"] = pd.to_numeric(df_para_reprogramar["Correlativo"], errors="coerce").astype("Int64")

        if "OR Suite" not in df_para_reprogramar.columns:
            raise ValueError("Falta columna OR Suite en datos de reprogramación")

        df_para_reprogramar["OR Suite"] = pd.to_numeric(df_para_reprogramar["OR Suite"], errors="coerce")

        df_para_reprogramar = df_para_reprogramar.dropna(
            subset=["Correlativo", "Servicio", "Descripción", "Prioridad de paciente", "Duración agendada (min)", "OR Suite"]
        ).reset_index(drop=True)

        df_para_reprogramar = df_para_reprogramar[
            ["Correlativo", "Servicio", "Descripción", "Prioridad de paciente", "Duración agendada (min)", "OR Suite"]
        ]

        print(f"\n3. Total para reprogramación: {len(df_para_reprogramar)}")
        print(f"   - Canceladas: {len(df_canceladas)}")
        print(f"   - De lista de espera: {len(df_top_500)}")

        # 6. Permanencia
        permanencia_por_desc = (
            df_datos_base.groupby("Descripción")["Días de permanencia efectivos"]
            .apply(lambda x: x.value_counts().idxmax() if len(x) > 0 else 0)
            .astype(int)
            .to_dict()
        )

        df_para_reprogramar["Permanencia_estimada"] = df_para_reprogramar["Descripción"].map(
            permanencia_por_desc
        ).fillna(0).astype(int)

        # 7. UCI
        df_para_reprogramar["Requiere_UCI"] = (
            (df_para_reprogramar["Servicio"].str.lower() == "vascular")
            & (df_para_reprogramar["Descripción"].str.lower().str.contains("fistula", na=False))
        )

        # 8. Exportar
        ruta_csv_salida = path.join(SIMULACION_DIR, "lista_espera_reprogramacion.csv")
        df_para_reprogramar.to_csv(ruta_csv_salida, index=False, encoding="utf-8-sig")

        print(f"\n4. CSV exportado a: {ruta_csv_salida}")
        print(f"   Pacientes UCI: {df_para_reprogramar['Requiere_UCI'].sum()}")

        return True

    except Exception as e:
        print(f"\n ERROR en preparar_datos_reprogramacion: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def ejecutar_reprogramacion_automatica():
    """
    Ejecuta la reprogramación automática al fin de la semana 1.
    Se llama en el update() cuando llegamos al día 7 a las 20:00.
    """
    global RUNNING, week_finished, current_time, reprogram_triggered_today

    try:
        print("\n" + "="*70)
        print("INICIANDO REPROGRAMACIÓN AUTOMÁTICA - SEMANA 2")
        print("="*70)
        print(f"Fecha: {current_time}")

        # 1. Preparar datos
        if not preparar_datos_reprogramacion():
            print(" Fallo en preparación de datos")
            return False

        # 2. Ejecutar Reprogramar.py
        print("\n5. Ejecutando modelo de optimización...")
        exportar_estado()
        os.makedirs(path.dirname(RUTA_REPROGRAMACION_SALIDA), exist_ok=True)
        limpiar_resultado_reprogramacion()
        RUNNING = False

        try:
            resultado = subprocess.run(
                [sys.executable, RUTA_REPROGRAMAR_SCRIPT],
                cwd=SIMULACION_DIR,
                check=True,
                capture_output=True,
                text=True,
                timeout=300  # 5 minutos
            )
            if resultado.stdout:
                print(resultado.stdout)
            print("✓ Modelo ejecutado exitosamente")

        except subprocess.TimeoutExpired:
            print(" Timeout en ejecución del modelo (5 minutos)")
            return False
        except subprocess.CalledProcessError as e:
            print(f" Error en ejecución del modelo: {e.stderr}")
            return False

        # 3. Cargar nueva agenda
        if path.exists(RUTA_REPROGRAMACION_SALIDA):
            print(f"\n6. Cargando nueva agenda desde: {RUTA_REPROGRAMACION_SALIDA}")
            cargar_agenda_desde_csv(
                RUTA_REPROGRAMACION_SALIDA,
                anchor_date=fecha_inicio_siguiente_agenda(),
            )
            print("✓ Nueva agenda cargada exitosamente")

            num_nuevas = len(df)
            print(f"   Total de operaciones programadas para semana 2: {num_nuevas}")

            reprogram_triggered_today = True
            return True
        else:
            print(f" No se generó archivo de salida: {RUTA_REPROGRAMACION_SALIDA}")
            return False

    except Exception as e:
        print(f"\n ERROR en ejecutar_reprogramacion_automatica: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def request_reprogramar(full_week=False):
    """Solicita reprogramación de la agenda."""
    global RUNNING, week_finished, day_paused, reprogram_triggered_today, full_week_param

    if not day_paused:
        return

    full_week_param = full_week
    if not preparar_datos_reprogramacion():
        print(" Fallo en preparación de datos para reprogramación manual")
        return

    exportar_estado()
    os.makedirs(path.dirname(RUTA_REPROGRAMACION_SALIDA), exist_ok=True)
    limpiar_resultado_reprogramacion()
    RUNNING = False

    try:
        resultado = subprocess.run(
            [sys.executable, RUTA_REPROGRAMAR_SCRIPT],
            cwd=SIMULACION_DIR,
            check=True,
            capture_output=True,
            text=True,
            timeout=300
        )
        if resultado.stdout:
            print(resultado.stdout)
    except subprocess.TimeoutExpired:
        print(" Timeout en reprogramación manual.")
        return
    except subprocess.CalledProcessError as e:
        print(f" Error en reprogramación manual: {e.stderr}")
        return

    if path.exists(RUTA_REPROGRAMACION_SALIDA):
        cargar_agenda_desde_csv(
            RUTA_REPROGRAMACION_SALIDA,
            anchor_date=fecha_inicio_siguiente_agenda(),
        )
        week_finished = False
        reprogram_triggered_today = True
    else:
        print(f" No se encontró el archivo de salida: {RUTA_REPROGRAMACION_SALIDA}")


# ============================================================================
# FUNCIONES DE GUARDADO
# ============================================================================

def guardar_estado_diario(resumen_text):
    """Guarda estado detallado del día con estructura estandarizada."""
    exportar_resumen_dia()

    # Exportar operaciones del día
    fecha_str = current_time.strftime("%Y-%m-%d")

    realizadas_hoy = [
        o for o in obtener_operaciones_por_estado(ESTADO_REALIZADA)
        if pd.to_datetime(o.get("Fecha inicio dt", current_time.strftime("%Y-%m-%d"))).date() == current_time.date()
    ]

    canceladas_hoy = [
        o for o in obtener_operaciones_por_estado(ESTADO_CANCELADA)
        if pd.to_datetime(o.get("Fecha inicio dt", current_time.strftime("%Y-%m-%d"))).date() == current_time.date()
    ]

    if realizadas_hoy:
        ruta_dia = path.join(DIR_LOGS, f"Operaciones_Realizadas_{fecha_str}.csv")
        pd.DataFrame(realizadas_hoy).to_csv(ruta_dia, index=False, encoding="utf-8-sig")

    if canceladas_hoy:
        ruta_cancel = path.join(DIR_LOGS, f"Operaciones_Canceladas_{fecha_str}.csv")
        pd.DataFrame(canceladas_hoy).to_csv(ruta_cancel, index=False, encoding="utf-8-sig")




# ============================================================================
# LOOP PRINCIPAL DE SIMULACIÓN
# ============================================================================

def actualizar_estadisticas_ui():
    """Actualiza labels de estadísticas en la UI."""
    num_realizadas = len(obtener_operaciones_por_estado(ESTADO_REALIZADA))
    num_pendientes = len(obtener_operaciones_por_estado(ESTADO_PROGRAMADA))
    num_canceladas = len(obtener_operaciones_por_estado(ESTADO_CANCELADA))
    num_en_curso = len(obtener_operaciones_por_estado(ESTADO_EN_CURSO))

    label_realizadas.config(text=f"Realizadas: {num_realizadas}")
    label_pendientes.config(text=f"Pendientes: {num_pendientes}")
    label_canceladas.config(text=f"Canceladas: {num_canceladas}")
    label_en_curso.config(text=f"En Curso: {num_en_curso}")

    # Actualizar camas
    label_camas.config(text=f"Camas Básicas: {len(camas)}/75")
    label_uci.config(text=f"Camas UCI: {len(camas_uci)}/2")


def update():
    """Loop principal de simulación con estadísticas actualizadas."""
    global current_time, idx_evento, RUNNING, week_finished, day_paused
    global last_paused_date, reprogram_triggered_today, cancelaciones_dia, modo_saturado

    if RUNNING:
        current_time += timedelta(minutes=5)

        # Procesar eventos
        while idx_evento < len(eventos) and eventos[idx_evento]["tiempo"] <= current_time:
            procesar_evento(eventos[idx_evento])
            idx_evento += 1

        liberar_camas(current_time)
        transferir_uci_a_normal()

    # Gestionar saturación
    if RUNNING and len(camas) >= TOTAL_CAMAS:
        if not modo_saturado:
            modo_saturado = True
            label_resumen.config(text="⚠ SATURACIÓN CRÍTICA\nTodas las camas ocupadas\nSe cancelarán nuevas cirugías")

    if modo_saturado and len(camas) < TOTAL_CAMAS:
        modo_saturado = False

    # Actualizar UI
    actualizar_estadisticas_ui()
    label_tiempo.config(text=str(current_time) + (" (PAUSADO)" if not RUNNING else ""))

    # Actualizar color de pabellones según ocupación
    ocupacion_pabellones = sum(1 for est in estado_pabellones.values() if est is not None)
    for p, lbl in labels_pabellon.items():
        if estado_pabellones[p] is None:
            lbl.config(bg=COLOR_ACCENT, text=f"PABELLÓN {p}\n● LIBRE")
        else:
            row = estado_pabellones[p]
            lbl.config(bg=COLOR_DANGER, text=f"PABELLÓN {p}\n● EN USO\n{row['descripcion'][:15]}")

    # Reset diario
    if last_paused_date is not None and current_time.date() != last_paused_date:
        day_paused = False
        last_paused_date = None
        reprogram_triggered_today = False
        cancelaciones_dia = 0
        label_resumen.config(text="")

    # Pausa diaria a las 20:00
    if RUNNING and not day_paused and current_time.hour >= 20 and (last_paused_date is None or current_time.date() != last_paused_date):
        RUNNING = False
        day_paused = True
        last_paused_date = current_time.date()

        num_realizadas = len(obtener_operaciones_por_estado(ESTADO_REALIZADA))
        num_pendientes = len(obtener_operaciones_por_estado(ESTADO_PROGRAMADA))
        num_canceladas = len(obtener_operaciones_por_estado(ESTADO_CANCELADA))
        camas_disp = TOTAL_CAMAS - len(camas)

        # Determinar el día de simulación
        dias_desde_inicio = (current_time.date() - SCHEDULE_ANCHOR_DATE).days
        dia_simulacion = dias_desde_inicio + 1  # Días desde 1 a 7

        # Si es el día 7, ejecutar reprogramación automática
        if (dia_simulacion % 7) == 0 and not reprogram_triggered_today:
            label_resumen.config(text="⏳ REPROGRAMANDO SEMANA 2...\nPor favor espere...")
            root.update()

            # Ejecutar reprogramación
            if ejecutar_reprogramacion_automatica():
                resumen = (
                    f"═══ SEMANA 1 FINALIZADA ═══\n"
                    f"Reprogramación completada\n\n"
                    f"✓ Realizadas: {num_realizadas}\n"
                    f"✗ Canceladas: {num_canceladas}\n"
                    f"⧖ Pendientes: {num_pendientes}\n\n"
                    f"SEMANA 2: Agenda reprogramada\n\n"
                    f"Presiona 'REANUDAR' para\ncontinuar a las 07:50 h"
                )
            else:
                resumen = (
                    f"═══ SEMANA 1 FINALIZADA ═══\n"
                    f"⚠ Error en reprogramación\n\n"
                    f"✓ Realizadas: {num_realizadas}\n"
                    f"✗ Canceladas: {num_canceladas}\n"
                    f"⧖ Pendientes: {num_pendientes}\n\n"
                    f"Presiona 'REANUDAR' para\ncontinuar"
                )
        else:
            resumen = (
                f"═══ RESUMEN DEL DÍA ═══\n"
                f"Fecha: {current_time.date()}\n"
                f"(Día {dia_simulacion} de 7)\n\n"
                f"✓ Realizadas: {num_realizadas}\n"
                f"✗ Canceladas: {num_canceladas}\n"
                f"⧖ Pendientes: {num_pendientes}\n\n"
                f"Camas disponibles: {camas_disp}/75\n"
                f"UCI disponibles: {2-len(camas_uci)}/2\n\n"
                f"Presiona 'REANUDAR' para\ncontinuar a las 07:50 h"
            )

        label_resumen.config(text=resumen)
        label_tiempo.config(text=f"{current_time} (DÍA TERMINADO)")

        guardar_estado_diario(resumen)
        exportar_estado()

    # Fin de semana
    if idx_evento >= len(eventos) and current_time >= final_time and not week_finished:
        week_finished = True
        RUNNING = False
        exportar_estado()

        num_realizadas = len(obtener_operaciones_por_estado(ESTADO_REALIZADA))
        num_canceladas = len(obtener_operaciones_por_estado(ESTADO_CANCELADA))

        resumen_final = (
            f"═══ SIMULACIÓN COMPLETADA ═══\n\n"
            f"RESULTADOS FINALES:\n"
            f"✓ Operaciones Realizadas: {num_realizadas}\n"
            f"✗ Operaciones Canceladas: {num_canceladas}\n\n"
            f"Tasa de Éxito: "
            f"{num_realizadas/(num_realizadas+num_canceladas)*100:.1f}%"
        )
        label_resumen.config(text=resumen_final)
        label_tiempo.config(text=f"{current_time} (SIMULACIÓN FINALIZADA)")

    root.after(SPEED, update)

# ============================================================================
# INICIO
# ============================================================================


update()
root.mainloop()
