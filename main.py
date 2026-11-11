import sqlite3
from datetime import datetime, timedelta, date, timezone
import calendar
import os
import base64
import urllib.request
import re
import flet as ft

# ==========================================
# 1. CONFIGURACIÓN Y BASE DE DATOS
# ==========================================
DB_NAME = "diana_rua_spa.db"
ADMIN_PASS = "diana2026"

ICAL_URL_DEFAULT = ""

SERVICIOS_INICIALES = [
    ("Manicure semipermanente", 48000, "#D81B60"),
    ("Manicure base rubber", 53000, "#E91E63"),
    ("Forrado Dippin", 63000, "#C2185B"),
    ("Uñas Soft gel", 85000, "#AD1457"),
    ("Retoque Soft gel", 75000, "#EC407A"),
    ("Acrílicas esculpidas (desde)", 105000, "#880E4F"),
    ("Forrado Acrílico", 85000, "#D81B60"),
    ("Retoque Acrílico", 70000, "#E91E63"),
    ("Pedicure semipermanente", 40000, "#F06292"),
    ("Pedicure tradicional", 24000, "#F48FB1"),
    ("Manicure hombre semipermanente", 32000, "#455A64"),
    ("Pedicure hombre semipermanente", 35000, "#37474F"),
    ("Manicure hombre tradicional", 20000, "#607D8B"),
    ("Pedicure hombre tradicional", 22000, "#78909C"),
    ("Pedi spa semipermanente", 65000, "#9C27B0"),
    ("Pedi spa tradicional", 49000, "#BA68C8"),
    ("Retiro Acrílico", 15000, "#8E8E93"),
    ("Retiro Soft gel", 12000, "#8E8E93"),
    ("Retiro semipermanente", 10000, "#8E8E93"),
]

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS servicios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            precio REAL NOT NULL,
            color TEXT DEFAULT '#B359C6'
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS citas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            clienta TEXT NOT NULL,
            telefono TEXT,
            servicio_id INTEGER,
            fecha TEXT NOT NULL,
            hora_inicio TEXT NOT NULL,
            hora_fin TEXT DEFAULT '',
            estado TEXT DEFAULT 'Programada',
            notas TEXT,
            gcal_uid TEXT,
            FOREIGN KEY (servicio_id) REFERENCES servicios(id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS config (
            clave TEXT PRIMARY KEY,
            valor TEXT
        )
    """)
    
    c.execute("PRAGMA table_info(citas)")
    columnas = [col[1] for col in c.fetchall()]
    if "hora_fin" not in columnas:
        c.execute("ALTER TABLE citas ADD COLUMN hora_fin TEXT DEFAULT ''")
    if "gcal_uid" not in columnas:
        c.execute("ALTER TABLE citas ADD COLUMN gcal_uid TEXT")

    c.execute("SELECT COUNT(*) FROM servicios")
    if c.fetchone()[0] == 0:
        c.executemany(
            "INSERT INTO servicios (nombre, precio, color) VALUES (?, ?, ?)",
            SERVICIOS_INICIALES
        )
        
    if ICAL_URL_DEFAULT:
        c.execute("INSERT OR IGNORE INTO config (clave, valor) VALUES ('ical_url', ?)", (ICAL_URL_DEFAULT,))

    conn.commit()
    conn.close()

def db_query(query, params=(), fetch=True):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(query, params)
    res = c.fetchall() if fetch else None
    conn.commit()
    conn.close()
    return res

def obtener_config(clave, defecto=""):
    res = db_query("SELECT valor FROM config WHERE clave=?", (clave,))
    return res[0][0] if res else defecto

def guardar_config(clave, valor):
    db_query("INSERT OR REPLACE INTO config (clave, valor) VALUES (?, ?)", (clave, valor), fetch=False)

# ==========================================
# 2. UTILIDADES DE TIEMPO Y GOOGLE CALENDAR
# ==========================================
HORA_INICIO_GRID = 6
HORA_FIN_GRID = 24
PX_POR_HORA = 68
ANCHO_COL_DIA = 175  # Ampliado para mejor visibilidad en móviles
ANCHO_COL_HORA = 65
ALTO_TOTAL_GRID = (HORA_FIN_GRID - HORA_INICIO_GRID) * PX_POR_HORA

def ahora_colombia():
    return datetime.now(timezone.utc) - timedelta(hours=5)

def texto_hora_a_decimal(h_str, defecto=8.0):
    if not h_str:
        return defecto
    s = h_str.strip().upper().replace(".", "")
    es_pm = "PM" in s
    es_am = "AM" in s
    s = s.replace("AM", "").replace("PM", "").strip()
    try:
        partes = s.split(":")
        h = int(partes[0].strip())
        m = int(partes[1].strip()) if len(partes) > 1 else 0
        if es_pm and h < 12:
            h += 12
        elif es_am and h == 12:
            h = 0
        return h + (m / 60.0)
    except Exception:
        return defecto

def decimal_a_texto_hora(dec):
    h = int(dec) % 24
    m = int(round((dec - int(dec)) * 60))
    if m == 60:
        h = (h + 1) % 24
        m = 0
    sufijo = "AM" if h < 12 else "PM"
    h_12 = h % 12
    if h_12 == 0:
        h_12 = 12
    return f"{h_12:02d}:{m:02d} {sufijo}"

def actualizar_citas_completadas_por_tiempo():
    ahora = ahora_colombia()
    hoy_str = ahora.strftime("%Y-%m-%d")
    hora_actual_dec = ahora.hour + (ahora.minute / 60.0)

    citas_activas = db_query("SELECT id, fecha, hora_inicio, hora_fin, estado FROM citas WHERE estado != 'Cancelada'")
    if not citas_activas:
        return

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    hubo_cambios = False

    for c_id, fecha_str, h_ini, h_fin, estado_actual in citas_activas:
        dec_ini = texto_hora_a_decimal(h_ini, defecto=8.0)
        dec_fin = texto_hora_a_decimal(h_fin, defecto=dec_ini + 2.0) if (h_fin and h_fin.strip()) else (dec_ini + 2.0)
        if dec_fin <= dec_ini:
            dec_fin = dec_ini + 1.0

        ya_termino = False
        if fecha_str < hoy_str:
            ya_termino = True
        elif fecha_str == hoy_str and hora_actual_dec >= dec_fin:
            ya_termino = True

        nuevo_estado = "Completada" if ya_termino else "Programada"
        if nuevo_estado != estado_actual:
            c.execute("UPDATE citas SET estado=? WHERE id=?", (nuevo_estado, c_id))
            hubo_cambios = True

    if hubo_cambios:
        conn.commit()
    conn.close()

def parsear_fecha_ics(val_str):
    val = val_str.strip()
    if "T" not in val:
        return None, None
    es_utc = val.endswith("Z")
    limpio = val.replace("Z", "")
    try:
        dt = datetime.strptime(limpio[:15], "%Y%m%dT%H%M%S")
        if es_utc:
            dt = dt - timedelta(hours=5)
        return dt.strftime("%Y-%m-%d"), dt.strftime("%I:%M %p")
    except Exception:
        return None, None

def sincronizar_desde_google_calendar(url_ics):
    if not url_ics.strip():
        return False, "Pega primero el enlace .ics de tu Google Calendar."
    
    try:
        req = urllib.request.Request(url_ics.strip(), headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw_ics = resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        return False, f"No se pudo conectar al enlace: {str(e)}"

    lineas_unidas = re.sub(r"\r?\n[ \t]", "", raw_ics)
    bloques = lineas_unidas.split("BEGIN:VEVENT")
    
    if len(bloques) <= 1:
        return False, "El enlace no contiene eventos o no es un archivo iCal (.ics) válido."

    servicios_db = db_query("SELECT id, nombre, precio FROM servicios")
    importadas = 0

    for b in bloques[1:]:
        cuerpo = b.split("END:VEVENT")[0]
        uid = ""
        resumen = ""
        dtstart = ""
        dtend = ""

        for linea in cuerpo.splitlines():
            if linea.startswith("UID:"):
                uid = linea.split(":", 1)[1].strip()
            elif linea.startswith("SUMMARY"):
                resumen = linea.split(":", 1)[1].strip().replace("\\,", ",").replace("\\;", ";")
            elif linea.startswith("DTSTART"):
                dtstart = linea.split(":", 1)[1].strip()
            elif linea.startswith("DTEND"):
                dtend = linea.split(":", 1)[1].strip()

        if not dtstart or not resumen:
            continue

        fecha_str, h_ini_str = parsear_fecha_ics(dtstart)
        _, h_fin_str = parsear_fecha_ics(dtend)

        if not fecha_str or not h_ini_str:
            continue

        if not h_fin_str:
            h_fin_str = decimal_a_texto_hora(texto_hora_a_decimal(h_ini_str) + 2.0)

        if "-" in resumen:
            partes = resumen.split("-", 1)
            nombre_clienta = partes[0].strip()
            nota_servicio = partes[1].strip()
        else:
            nombre_clienta = resumen.strip()
            nota_servicio = "Servicio agendado en Google Calendar"

        serv_id_match = servicios_db[0][0] if servicios_db else None
        texto_busq = nota_servicio.lower()
        if "rubber" in texto_busq or "ruber" in texto_busq:
            for s in servicios_db:
                if "rubber" in s[1].lower():
                    serv_id_match = s[0]
                    break
        elif "dippin" in texto_busq or "dipping" in texto_busq:
            for s in servicios_db:
                if "dippin" in s[1].lower():
                    serv_id_match = s[0]
                    break
        elif "acrilico" in texto_busq or "acrílico" in texto_busq:
            for s in servicios_db:
                if "acrílico" in s[1].lower():
                    serv_id_match = s[0]
                    break
        elif "soft" in texto_busq:
            for s in servicios_db:
                if "soft" in s[1].lower():
                    serv_id_match = s[0]
                    break
        elif "pies" in texto_busq or "pedi" in texto_busq:
            for s in servicios_db:
                if "pedicure" in s[1].lower():
                    serv_id_match = s[0]
                    break

        existe = db_query("SELECT id FROM citas WHERE gcal_uid=?", (uid,)) if uid else []
        if existe:
            db_query("""
                UPDATE citas SET clienta=?, fecha=?, hora_inicio=?, hora_fin=?, notas=?, servicio_id=?
                WHERE gcal_uid=?
            """, (nombre_clienta, fecha_str, h_ini_str, h_fin_str, nota_servicio, serv_id_match, uid), fetch=False)
        else:
            db_query("""
                INSERT INTO citas (clienta, telefono, servicio_id, fecha, hora_inicio, hora_fin, estado, notas, gcal_uid)
                VALUES (?, '', ?, ?, ?, ?, 'Programada', ?, ?)
            """, (nombre_clienta, serv_id_match, fecha_str, h_ini_str, h_fin_str, nota_servicio, uid), fetch=False)
        importadas += 1

    actualizar_citas_completadas_por_tiempo()
    return True, f"✅ Se sincronizaron {importadas} citas desde Google Calendar."

# ==========================================
# 3. PALETA DE COLORES Y UTILIDADES
# ==========================================
IOS_BG = "#FFF5F8"
IOS_CARD = "#FFFFFF"
LOGO_PINK = "#FFB6C1"
IOS_PINK = "#D81B60"
GCAL_PURPLE = "#9C41B6"
IOS_BLUE = "#007AFF"
IOS_GRAY = "#8E8E93"
IOS_GREEN = "#34C759"
IOS_RED = "#FF3B30"
IOS_DARK = "#1C1C1E"

DIAS_SEMANA_LD = ["LUN", "MAR", "MIÉ", "JUE", "VIE", "SÁB", "DOM"]
MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]

HORARIOS_RAPIDOS_INICIO = [
    "06:00 AM", "07:00 AM", "08:00 AM", "09:00 AM", "09:30 AM", "10:00 AM", "11:00 AM",
    "01:00 PM", "01:30 PM", "02:00 PM", "02:30 PM", "04:00 PM", "05:30 PM", "06:00 PM"
]
HORARIOS_RAPIDOS_FIN = [
    "09:30 AM", "11:00 AM", "12:30 PM", "01:00 PM", "01:30 PM", "04:30 PM",
    "05:30 PM", "06:00 PM", "08:00 PM", "08:30 PM", "09:00 PM", "10:30 PM"
]

def sumar_meses(fecha_actual, meses_offset):
    mes_indice = fecha_actual.month - 1 + meses_offset
    anio = fecha_actual.year + mes_indice // 12
    mes = mes_indice % 12 + 1
    max_dia = calendar.monthrange(anio, mes)[1]
    dia = min(fecha_actual.day, max_dia)
    return date(anio, mes, dia)

def obtener_logo_data_uri():
    carpeta_actual = os.path.dirname(os.path.abspath(__file__))
    posibles_nombres = [
        "logo.jpeg",
        "logo.jpg",
        "logo.png",
        "WhatsApp Image 2026-11-10 at 8.19.03 PM.jpeg"
    ]
    for nombre in posibles_nombres:
        ruta = os.path.join(carpeta_actual, nombre)
        if os.path.exists(ruta):
            with open(ruta, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
                mime = "image/png" if nombre.lower().endswith(".png") else "image/jpeg"
                return f"data:{mime};base64,{b64}"
    for nombre in os.listdir(carpeta_actual):
        if nombre.lower().endswith((".png", ".jpg", ".jpeg")):
            ruta = os.path.join(carpeta_actual, nombre)
            with open(ruta, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
                mime = "image/png" if nombre.lower().endswith(".png") else "image/jpeg"
                return f"data:{mime};base64,{b64}"
    return None

def boton_ios(texto, bgcolor, color_texto, accion, ancho=None, expandir=False, visible=True, padding=10):
    return ft.Container(
        width=ancho,
        expand=expandir,
        visible=visible,
        bgcolor=bgcolor,
        padding=padding,
        border_radius=12,
        on_click=accion,
        content=ft.Row(
            [ft.Text(texto, color=color_texto, weight="bold", size=12)],
            alignment="center"
        )
    )

# ==========================================
# 4. APLICACIÓN PRINCIPAL
# ==========================================
def main(page: ft.Page):
    init_db()
    
    page.title = "Diana Rua • Nails Artist"
    page.bgcolor = "#FFE4EC"
    page.padding = 0

    estado_app = {
        "fecha_seleccionada": ahora_colombia().date(),
        "es_admin": False
    }

    txt_banner = ft.Text("", color="white", weight="bold", size=13)
    banner_aviso = ft.Container(
        visible=False,
        padding=12,
        margin=10,
        border_radius=12,
        bgcolor=IOS_RED,
        content=txt_banner
    )

    def mostrar_alerta(mensaje, color=IOS_RED):
        banner_aviso.bgcolor = color
        txt_banner.value = mensaje
        banner_aviso.visible = True
        page.update()

    # ==========================================
    # TARJETAS KPI (OCULTAS HASTA ENTRAR EN MODO ADMIN)
    # ==========================================
    txt_kpi_total = ft.Text("0", size=20, weight="bold", color=IOS_DARK)
    txt_kpi_prog = ft.Text("0", size=20, weight="bold", color=IOS_BLUE)
    txt_kpi_comp = ft.Text("0", size=20, weight="bold", color=IOS_GREEN)
    txt_kpi_ingresos = ft.Text("$0", size=18, weight="bold", color=IOS_PINK)

    def crear_kpi_card(titulo, widget_valor, subtitulo):
        return ft.Container(
            bgcolor=IOS_CARD,
            padding=10,
            border_radius=14,
            width=155,
            content=ft.Column([
                ft.Text(titulo, size=10, weight="bold", color=IOS_GRAY),
                widget_valor,
                ft.Text(subtitulo, size=9, color=IOS_GRAY)
            ], spacing=2)
        )

    fila_kpis = ft.Row(
        visible=False,
        scroll="auto",
        spacing=8,
        controls=[
            crear_kpi_card("CITAS SEMANA", txt_kpi_total, "Total en la semana"),
            crear_kpi_card("PENDIENTES", txt_kpi_prog, "En curso / próximas"),
            crear_kpi_card("COMPLETADAS", txt_kpi_comp, "Auto por horario"),
            crear_kpi_card("INGRESOS SEMANA", txt_kpi_ingresos, "Estimado semanal")
        ]
    )

    # ==========================================
    # BUSCADOR PÚBLICO DE CITAS
    # ==========================================
    contenedor_resultados_busqueda = ft.Column(spacing=8, visible=False)

    def buscar_cita_por_nombre(e):
        actualizar_citas_completadas_por_tiempo()
        termino = inp_buscador_clienta.value.strip()
        contenedor_resultados_busqueda.controls.clear()
        
        if not termino:
            contenedor_resultados_busqueda.visible = False
            page.update()
            return

        resultados = db_query("""
            SELECT c.id, c.clienta, c.telefono, s.nombre, c.hora_inicio, c.estado, s.color, c.servicio_id, s.precio, c.fecha, c.hora_fin, c.notas
            FROM citas c
            LEFT JOIN servicios s ON c.servicio_id = s.id
            WHERE LOWER(c.clienta) LIKE LOWER(?)
            ORDER BY c.fecha DESC, c.hora_inicio ASC
        """, (f"%{termino}%",))

        contenedor_resultados_busqueda.visible = True
        contenedor_resultados_busqueda.controls.append(
            ft.Row([
                ft.Text(f"Resultados para '{termino}':", size=13, weight="bold", color=IOS_DARK),
                boton_ios("Limpiar", IOS_CARD, IOS_GRAY, limpiar_busqueda, ancho=90, padding=6)
            ], alignment="spaceBetween")
        )

        if not resultados:
            contenedor_resultados_busqueda.controls.append(
                ft.Container(
                    bgcolor=IOS_CARD, padding=14, border_radius=12,
                    content=ft.Text("No se encontraron citas registradas con ese nombre.", color=IOS_GRAY, size=13)
                )
            )
        else:
            for r in resultados:
                c_id, clienta, tel, serv_nom, h_ini, estado, color_serv, serv_id, precio_serv, fecha_cita, h_fin, notas = r
                col_est = IOS_BLUE if estado == "Programada" else (IOS_GREEN if estado == "Completada" else IOS_RED)
                rango_h = f"{h_ini} – {h_fin}" if h_fin else h_ini
                detalle_serv = notas if notas else (serv_nom or "Servicio")
                
                item_res = ft.Container(
                    bgcolor=IOS_CARD,
                    border_radius=14,
                    padding=12,
                    on_click=(lambda e, datos=r: mostrar_formulario_cita(datos)) if estado_app["es_admin"] else None,
                    content=ft.Row([
                        ft.Column([
                            ft.Text(f"📅 {fecha_cita}", size=12, weight="bold", color=IOS_PINK),
                            ft.Text(f"⏰ {rango_h}", size=12, weight="bold", color=IOS_DARK),
                        ], width=135),
                        ft.Column([
                            ft.Text(clienta, size=14, weight="bold", color=IOS_DARK),
                            ft.Text(f"💅 {detalle_serv} • ${precio_serv:,.0f}" if precio_serv else f"💅 {detalle_serv}", size=11, color=IOS_GRAY),
                        ], expand=True),
                        ft.Container(
                            padding=6, border_radius=8, bgcolor=col_est,
                            content=ft.Text(estado, size=10, weight="bold", color="white")
                        )
                    ])
                )
                contenedor_resultados_busqueda.controls.append(item_res)
        page.update()

    def limpiar_busqueda(e=None):
        inp_buscador_clienta.value = ""
        contenedor_resultados_busqueda.controls.clear()
        contenedor_resultados_busqueda.visible = False
        page.update()

    inp_buscador_clienta = ft.TextField(
        hint_text="🔍 ¿Tienes cita? Escribe tu nombre aquí para consultar tu día y hora...",
        border_radius=14,
        bgcolor=IOS_CARD,
        on_change=buscar_cita_por_nombre
    )

    # ==========================================
    # SECCIÓN A: CALENDARIO SEMANAL AMPLIADO
    # ==========================================
    lbl_mes_anio = ft.Text(size=16, weight="bold", color=IOS_DARK)
    lbl_rango_semana = ft.Text(size=12, weight="bold", color=IOS_PINK)

    cabecera_dias_grid = ft.Row(spacing=2)
    cuerpo_cuadricula_horas = ft.Row(spacing=2, vertical_alignment="start")

    def cambiar_mes(meses_offset):
        banner_aviso.visible = False
        estado_app["fecha_seleccionada"] = sumar_meses(estado_app["fecha_seleccionada"], meses_offset)
        renderizar_cuadricula_google()

    def cambiar_semana(semanas_offset):
        banner_aviso.visible = False
        f_actual = estado_app["fecha_seleccionada"]
        lunes_actual = f_actual - timedelta(days=f_actual.weekday())
        estado_app["fecha_seleccionada"] = lunes_actual + timedelta(days=semanas_offset * 7)
        renderizar_cuadricula_google()

    def click_en_celda_hora(dia_elegido, hora_entera):
        if not estado_app["es_admin"]:
            return
        h_ini_sug = decimal_a_texto_hora(hora_entera)
        h_fin_sug = decimal_a_texto_hora(min(hora_entera + 2.5, 23.5))
        mostrar_formulario_cita(None, fecha_forzada=dia_elegido, hora_ini_forzada=h_ini_sug, hora_fin_forzada=h_fin_sug)

    def renderizar_cuadricula_google():
        actualizar_citas_completadas_por_tiempo()

        f_sel = estado_app["fecha_seleccionada"]
        lunes_semana = f_sel - timedelta(days=f_sel.weekday())
        domingo_semana = lunes_semana + timedelta(days=6)

        lbl_mes_anio.value = f"🗓️ {MESES[f_sel.month - 1]} {f_sel.year}"
        lbl_rango_semana.value = f"Lun {lunes_semana.day} {MESES[lunes_semana.month - 1][:3]} — Dom {domingo_semana.day} {MESES[domingo_semana.month - 1][:3]}"

        citas_semana = db_query("""
            SELECT c.id, c.clienta, c.telefono, s.nombre, c.hora_inicio, c.estado, s.color, c.servicio_id, s.precio, c.fecha, c.hora_fin, c.notas
            FROM citas c
            LEFT JOIN servicios s ON c.servicio_id = s.id
            WHERE c.fecha BETWEEN ? AND ?
        """, (lunes_semana.strftime("%Y-%m-%d"), domingo_semana.strftime("%Y-%m-%d")))

        total_sem = len(citas_semana)
        prog_sem = sum(1 for c in citas_semana if c[5] == "Programada")
        comp_sem = sum(1 for c in citas_semana if c[5] == "Completada")
        ingresos_sem = sum((c[8] or 0) for c in citas_semana if c[5] != "Cancelada")

        txt_kpi_total.value = str(total_sem)
        txt_kpi_prog.value = str(prog_sem)
        txt_kpi_comp.value = str(comp_sem)
        txt_kpi_ingresos.value = f"${ingresos_sem:,.0f}"
        fila_kpis.visible = estado_app["es_admin"]

        citas_por_dia = {}
        for c in citas_semana:
            f_cita = c[9]
            if f_cita not in citas_por_dia:
                citas_por_dia[f_cita] = []
            citas_por_dia[f_cita].append(c)

        cabecera_dias_grid.controls.clear()
        cabecera_dias_grid.controls.append(
            ft.Container(
                width=ANCHO_COL_HORA,
                height=64,
                bgcolor="#F9F0F4",
                border_radius=10,
                padding=4,
                content=ft.Column([
                    ft.Text("GMT-5", size=9, color=IOS_GRAY, weight="bold"),
                    ft.Text("Hora", size=11, color=IOS_DARK, weight="bold")
                ], alignment="center", horizontal_alignment="center", spacing=1)
            )
        )

        hoy_col = ahora_colombia().date()
        for i in range(7):
            dia = lunes_semana + timedelta(days=i)
            es_hoy = (dia == hoy_col)
            
            cabecera_dias_grid.controls.append(
                ft.Container(
                    width=ANCHO_COL_DIA,
                    height=64,
                    bgcolor=IOS_DARK if es_hoy else "#F9F0F4",
                    border_radius=10,
                    padding=6,
                    on_click=lambda e, d=dia: click_en_celda_hora(d, 8),
                    content=ft.Column([
                        ft.Text(DIAS_SEMANA_LD[i], size=12, weight="bold", color=LOGO_PINK if es_hoy else IOS_GRAY),
                        ft.Text(str(dia.day), size=19, weight="bold", color="white" if es_hoy else IOS_DARK)
                    ], alignment="center", horizontal_alignment="center", spacing=2)
                )
            )

        cuerpo_cuadricula_horas.controls.clear()
        etiquetas_horas = []
        for h in range(HORA_INICIO_GRID, HORA_FIN_GRID):
            etiqueta = f"{h} AM" if h < 12 else ("12 PM" if h == 12 else f"{h-12} PM")
            etiquetas_horas.append(
                ft.Container(
                    width=ANCHO_COL_HORA,
                    height=PX_POR_HORA,
                    padding=4,
                    bgcolor="#F9F0F4",
                    content=ft.Text(etiqueta, size=10, weight="bold", color=IOS_GRAY)
                )
            )
        cuerpo_cuadricula_horas.controls.append(ft.Column(etiquetas_horas, spacing=0))

        for i in range(7):
            dia = lunes_semana + timedelta(days=i)
            dia_str = dia.strftime("%Y-%m-%d")
            citas_del_dia = citas_por_dia.get(dia_str, [])

            celdas_fondo = []
            for h in range(HORA_INICIO_GRID, HORA_FIN_GRID):
                celdas_fondo.append(
                    ft.Container(
                        width=ANCHO_COL_DIA,
                        height=PX_POR_HORA,
                        bgcolor=IOS_CARD if h % 2 == 0 else "#FDF8FA",
                        on_click=lambda e, d=dia, hora_sel=h: click_en_celda_hora(d, hora_sel),
                        content=ft.Column([
                            ft.Container(height=1, bgcolor="#EADCE2", width=ANCHO_COL_DIA)
                        ], alignment="start")
                    )
                )

            capas_stack_dia = [ft.Column(celdas_fondo, spacing=0)]

            for c in citas_del_dia:
                c_id, clienta, tel, serv_nom, h_ini, estado, color_serv, serv_id, precio_serv, f_cita, h_fin, notas = c
                
                dec_ini = texto_hora_a_decimal(h_ini, defecto=8.0)
                if h_fin and h_fin.strip():
                    dec_fin = texto_hora_a_decimal(h_fin, defecto=dec_ini + 2.0)
                else:
                    dec_fin = dec_ini + 2.0

                if dec_fin <= dec_ini:
                    dec_fin = dec_ini + 1.0

                dec_ini_vis = max(float(HORA_INICIO_GRID), min(float(HORA_FIN_GRID - 0.5), dec_ini))
                dec_fin_vis = max(dec_ini_vis + 0.5, min(float(HORA_FIN_GRID), dec_fin))

                top_px = int((dec_ini_vis - HORA_INICIO_GRID) * PX_POR_HORA)
                alto_px = max(36, int((dec_fin_vis - dec_ini_vis) * PX_POR_HORA) - 2)

                if estado == "Cancelada":
                    color_bloque = IOS_RED
                    opacidad = 0.5
                    icono_est = "❌ "
                elif estado == "Completada":
                    color_bloque = IOS_GREEN
                    opacidad = 0.95
                    icono_est = "✓ "
                else:
                    color_bloque = GCAL_PURPLE
                    opacidad = 0.95
                    icono_est = ""

                rango_texto = f"{h_ini} – {h_fin}" if h_fin else h_ini
                detalle_servicio = notas if notas else (serv_nom or "Servicio")
                texto_valor = f" • ${precio_serv:,.0f}" if precio_serv else ""

                bloque_cita = ft.Container(
                    top=top_px,
                    left=2,
                    width=ANCHO_COL_DIA - 4,
                    height=alto_px,
                    bgcolor=color_bloque,
                    border_radius=8,
                    padding=6,
                    opacity=opacidad,
                    on_click=(lambda e, datos=c: mostrar_formulario_cita(datos)) if estado_app["es_admin"] else None,
                    content=ft.Column([
                        ft.Text(f"{icono_est}{clienta} - {detalle_servicio}", size=11, weight="bold", color="white"),
                        ft.Text(f"{rango_texto}{texto_valor}", size=10, color="#F3E5F5"),
                    ], spacing=1)
                )
                capas_stack_dia.append(bloque_cita)

            columna_dia_contenedor = ft.Container(
                width=ANCHO_COL_DIA,
                height=ALTO_TOTAL_GRID,
                content=ft.Stack(
                    width=ANCHO_COL_DIA,
                    height=ALTO_TOTAL_GRID,
                    controls=capas_stack_dia
                )
            )
            cuerpo_cuadricula_horas.controls.append(columna_dia_contenedor)

        page.update()

    tablero_calendario_completo = ft.Container(
        expand=True,
        bgcolor=IOS_CARD,
        border_radius=16,
        padding=6,
        content=ft.Column(
            expand=True,
            scroll="auto",
            spacing=0,
            controls=[
                ft.Row(
                    scroll="auto",
                    controls=[
                        ft.Column(
                            spacing=4,
                            controls=[
                                cabecera_dias_grid,
                                cuerpo_cuadricula_horas
                            ]
                        )
                    ]
                )
            ]
        )
    )

    barra_controles_calendario = ft.Container(
        bgcolor="#E6FFFFFF",
        padding=8,
        border_radius=16,
        content=ft.Row([
            ft.Row([
                boton_ios("◀ Mes", IOS_CARD, IOS_DARK, lambda e: cambiar_mes(-1), ancho=68, padding=8),
                lbl_mes_anio,
                boton_ios("Mes ▶", IOS_CARD, IOS_DARK, lambda e: cambiar_mes(1), ancho=68, padding=8),
            ], spacing=6),
            ft.Row([
                boton_ios("◀ Sem.", IOS_DARK, "white", lambda e: cambiar_semana(-1), ancho=72, padding=8),
                lbl_rango_semana,
                boton_ios("Sem. ▶", IOS_DARK, "white", lambda e: cambiar_semana(1), ancho=72, padding=8),
            ], spacing=6)
        ], alignment="spaceBetween", wrap=True)
    )

    # ==========================================
    # MODALES (ADMIN, GOOGLE CALENDAR Y CITAS)
    # ==========================================
    panel_modal = ft.Container(visible=False, bgcolor=IOS_CARD, border_radius=20, padding=16, margin=10, expand=True)

    def abrir_panel_administracion(e=None):
        """Al hacer clic en el logo, se despliega el menú flotante para Admin o cerrar sesión."""
        banner_aviso.visible = False
        if estado_app["es_admin"]:
            # Si ya es admin, mostrar opciones de cerrar sesión o sincronizar
            def cerrar_sesion(ev):
                estado_app["es_admin"] = False
                actualizar_interfaz_permisos()
                panel_modal.visible = False
                vista_agenda.visible = True
                mostrar_alerta("Sesión de administrador cerrada.", IOS_GRAY)

            panel_modal.content = ft.Column([
                ft.Row([
                    ft.Text("⚙️ Panel de Administradora", size=18, weight="bold", color=IOS_DARK),
                    boton_ios("Cerrar", IOS_BG, IOS_DARK, lambda ev: cerrar_login_modal(), ancho=75)
                ], alignment="spaceBetween"),
                ft.Text("Estás conectada como Administradora.", size=13, color=IOS_GRAY),
                boton_ios("🔄 Sincronizar Google Calendar", GCAL_PURPLE, "white", lambda ev: mostrar_modal_google_calendar(), padding=12),
                boton_ios("🚪 Cerrar Sesión de Admin", IOS_RED, "white", cerrar_sesion, padding=12)
            ], spacing=12)
        else:
            # Si es clienta, pedir contraseña para entrar a modo Admin
            inp_pass = ft.TextField(label="Contraseña de la cuenta principal", password=True, can_reveal_password=True, border_radius=12)

            def verificar_login(ev):
                if inp_pass.value.strip() == ADMIN_PASS:
                    estado_app["es_admin"] = True
                    panel_modal.visible = False
                    vista_agenda.visible = True
                    actualizar_interfaz_permisos()
                    mostrar_alerta("¡Bienvenida Diana! Modo Administrador activado.", IOS_GREEN)
                else:
                    mostrar_alerta("Contraseña incorrecta. Intenta de nuevo.", IOS_RED)

            panel_modal.content = ft.Column([
                ft.Row([
                    ft.Text("🔐 Acceso Cuenta Principal", size=18, weight="bold", color=IOS_DARK),
                    boton_ios("Cerrar", IOS_BG, IOS_DARK, lambda ev: cerrar_login_modal(), ancho=75)
                ], alignment="spaceBetween"),
                ft.Text("Ingresa la clave para administrar citas y ver estadísticas.", size=12, color=IOS_GRAY),
                inp_pass,
                boton_ios("Ingresar al Panel de Control", IOS_DARK, "white", verificar_login, padding=12)
            ], spacing=14)

        vista_agenda.visible = False
        vista_servicios.visible = False
        panel_modal.visible = True
        page.update()

    def cerrar_login_modal():
        panel_modal.visible = False
        vista_agenda.visible = True
        page.update()

    def mostrar_modal_google_calendar(e=None):
        url_guardada = obtener_config("ical_url", ICAL_URL_DEFAULT)
        inp_ical = ft.TextField(
            label="Dirección iCal (.ics) de Google Calendar",
            value=url_guardada,
            border_radius=12,
            hint_text="https://calendar.google.com/calendar/ical/.../basic.ics"
        )

        def ejecutar_sincronizacion(ev):
            url = inp_ical.value.strip()
            guardar_config("ical_url", url)
            mostrar_alerta("Conectando con Google Calendar...", IOS_BLUE)
            exito, msg = sincronizar_desde_google_calendar(url)
            if exito:
                panel_modal.visible = False
                vista_agenda.visible = True
                renderizar_cuadricula_google()
                mostrar_alerta(msg, IOS_GREEN)
            else:
                mostrar_alerta(msg, IOS_RED)

        panel_modal.content = ft.Column([
            ft.Row([
                ft.Text("🔄 Sincronizar Google Calendar", size=18, weight="bold", color=IOS_DARK),
                boton_ios("Atrás", IOS_BG, IOS_DARK, abrir_panel_administracion, ancho=75)
            ], alignment="spaceBetween"),
            ft.Text("Pega tu enlace iCal para actualizar las citas:", size=12, color=IOS_GRAY),
            inp_ical,
            boton_ios("🔄 Descargar y Sincronizar Ahora", GCAL_PURPLE, "white", ejecutar_sincronizacion, padding=12)
        ], spacing=12)
        page.update()

    # Formulario de citas compacto y sin colapsar (2 columnas en PC, adaptable en móvil)
    def mostrar_formulario_cita(cita_existente=None, fecha_forzada=None, hora_ini_forzada=None, hora_fin_forzada=None):
        if not estado_app["es_admin"]:
            mostrar_alerta("Solo la administradora puede crear o modificar citas.")
            return

        banner_aviso.visible = False
        servicios = db_query("SELECT id, nombre, precio FROM servicios")
        es_edicion = cita_existente is not None

        estado_inicial = "Cancelada" if (es_edicion and cita_existente[5] == "Cancelada") else "Programada"
        estado_form = {
            "servicio_id": cita_existente[7] if (es_edicion and cita_existente[7]) else (servicios[0][0] if servicios else None),
            "estado": estado_inicial
        }

        if fecha_forzada:
            fecha_inicial = fecha_forzada.strftime("%Y-%m-%d")
        elif es_edicion and len(cita_existente) > 9:
            fecha_inicial = cita_existente[9]
        else:
            fecha_inicial = estado_app["fecha_seleccionada"].strftime("%Y-%m-%d")

        h_ini_val = hora_ini_forzada if hora_ini_forzada else (cita_existente[4] if es_edicion else "08:00 AM")
        h_fin_val = hora_fin_forzada if hora_fin_forzada else ((cita_existente[10] if (es_edicion and len(cita_existente) > 10 and cita_existente[10]) else "10:30 AM"))
        notas_val = cita_existente[11] if (es_edicion and len(cita_existente) > 11 and cita_existente[11]) else ""

        inp_clienta = ft.TextField(label="Nombre de la clienta", value=cita_existente[1] if es_edicion else "", border_radius=10, dense=True)
        inp_telefono = ft.TextField(label="Teléfono / WhatsApp", value=cita_existente[2] if es_edicion else "", border_radius=10, dense=True)
        inp_notas = ft.TextField(label="Detalle del servicio (ej. manos semi Ruber)", value=notas_val, border_radius=10, dense=True)
        inp_fecha = ft.TextField(label="Fecha (AAAA-MM-DD)", value=fecha_inicial, border_radius=10, dense=True)
        inp_hora_ini = ft.TextField(label="Hora Inicio", value=h_ini_val, border_radius=10, dense=True)
        inp_hora_fin = ft.TextField(label="Hora Fin", value=h_fin_val, border_radius=10, dense=True)

        # Burbujas uniformes para horarios rápidos
        fila_ini_rapidos = ft.Row(scroll="auto", spacing=4)
        for h_s in HORARIOS_RAPIDOS_INICIO:
            fila_ini_rapidos.controls.append(
                ft.Container(
                    padding=6, border_radius=10, bgcolor=IOS_BG,
                    on_click=lambda e, h=h_s: asignar_ini(h),
                    content=ft.Text(h_s, size=10, weight="bold", color=IOS_DARK)
                )
            )

        fila_fin_rapidos = ft.Row(scroll="auto", spacing=4)
        for h_f in HORARIOS_RAPIDOS_FIN:
            fila_fin_rapidos.controls.append(
                ft.Container(
                    padding=6, border_radius=10, bgcolor=IOS_BG,
                    on_click=lambda e, h=h_f: asignar_fin(h),
                    content=ft.Text(h_f, size=10, weight="bold", color=IOS_PINK)
                )
            )

        def asignar_ini(h):
            inp_hora_ini.value = h
            page.update()

        def asignar_fin(h):
            inp_hora_fin.value = h
            page.update()

        grid_servicios_dos_col = ft.Column(spacing=6, height=230, scroll="auto")

        def render_chips_servicios():
            grid_servicios_dos_col.controls.clear()
            for idx in range(0, len(servicios), 2):
                par = servicios[idx:idx + 2]
                fila_par = ft.Row(spacing=6)
                for s_id, nom, pre in par:
                    seleccionado = (estado_form["servicio_id"] == s_id)
                    chip = ft.Container(
                        expand=True,
                        padding=8,
                        border_radius=10,
                        bgcolor=IOS_DARK if seleccionado else IOS_BG,
                        on_click=lambda e, id_s=s_id, n_s=nom: elegir_servicio(id_s, n_s),
                        content=ft.Column([
                            ft.Text(nom, color="white" if seleccionado else IOS_DARK, weight="bold", size=11),
                            ft.Text(f"${pre:,.0f}", color=LOGO_PINK if seleccionado else IOS_PINK, size=11, weight="bold")
                        ], spacing=1)
                    )
                    fila_par.controls.append(chip)
                if len(par) == 1:
                    fila_par.controls.append(ft.Container(expand=True))
                grid_servicios_dos_col.controls.append(fila_par)

        def elegir_servicio(id_s, nombre_serv):
            estado_form["servicio_id"] = id_s
            if not inp_notas.value.strip():
                inp_notas.value = nombre_serv
            render_chips_servicios()
            page.update()

        fila_estados = ft.Row(spacing=8, visible=es_edicion)

        def render_chips_estado():
            fila_estados.controls.clear()
            opciones_est = [
                ("Programada", "📅 Cita Activa", IOS_BLUE),
                ("Cancelada", "❌ Cancelar", IOS_RED)
            ]
            for valor_est, etiqueta_est, col_est in opciones_est:
                sel = (estado_form["estado"] == valor_est)
                fila_estados.controls.append(
                    ft.Container(
                        padding=8,
                        border_radius=10,
                        bgcolor=col_est if sel else IOS_BG,
                        on_click=lambda e, nuevo_e=valor_est: elegir_estado(nuevo_e),
                        content=ft.Text(etiqueta_est, color="white" if sel else IOS_DARK, size=11, weight="bold")
                    )
                )

        def elegir_estado(nuevo_e):
            estado_form["estado"] = nuevo_e
            render_chips_estado()
            page.update()

        def guardar(e):
            if not inp_clienta.value.strip():
                mostrar_alerta("Escribe el nombre de la clienta")
                return
            if not inp_hora_ini.value.strip() or not inp_fecha.value.strip():
                mostrar_alerta("Verifica la fecha y la hora")
                return

            if es_edicion:
                db_query("""
                    UPDATE citas SET clienta=?, telefono=?, servicio_id=?, fecha=?, hora_inicio=?, hora_fin=?, estado=?, notas=?
                    WHERE id=?
                """, (inp_clienta.value.strip(), inp_telefono.value.strip(), estado_form["servicio_id"],
                      inp_fecha.value.strip(), inp_hora_ini.value.strip(), inp_hora_fin.value.strip(),
                      estado_form["estado"], inp_notas.value.strip(), cita_existente[0]), fetch=False)
                mostrar_alerta("Cita actualizada correctamente", IOS_GREEN)
            else:
                db_query("""
                    INSERT INTO citas (clienta, telefono, servicio_id, fecha, hora_inicio, hora_fin, estado, notas)
                    VALUES (?, ?, ?, ?, ?, ?, 'Programada', ?)
                """, (inp_clienta.value.strip(), inp_telefono.value.strip(), estado_form["servicio_id"],
                      inp_fecha.value.strip(), inp_hora_ini.value.strip(), inp_hora_fin.value.strip(),
                      inp_notas.value.strip()), fetch=False)
                mostrar_alerta("Cita agendada con éxito", IOS_GREEN)

            try:
                estado_app["fecha_seleccionada"] = datetime.strptime(inp_fecha.value.strip(), "%Y-%m-%d").date()
            except Exception:
                pass

            cerrar_modal()
            renderizar_cuadricula_google()
            if inp_buscador_clienta.value.strip():
                buscar_cita_por_nombre(None)

        def eliminar(e):
            db_query("DELETE FROM citas WHERE id=?", (cita_existente[0],), fetch=False)
            cerrar_modal()
            renderizar_cuadricula_google()
            if inp_buscador_clienta.value.strip():
                buscar_cita_por_nombre(None)
            mostrar_alerta("Cita eliminada", IOS_GRAY)

        def cerrar_modal(e=None):
            panel_modal.visible = False
            vista_agenda.visible = True
            page.update()

        render_chips_servicios()
        render_chips_estado()

        columna_izquierda_datos = ft.Container(
            width=340,
            padding=8,
            border_radius=12,
            bgcolor="#FDF8FA",
            content=ft.Column([
                ft.Text("1. Datos y Horario", size=12, weight="bold", color=IOS_PINK),
                ft.Row([ft.Container(inp_clienta, expand=True), ft.Container(inp_telefono, width=125)], spacing=4),
                ft.Row([ft.Container(inp_fecha, expand=True), ft.Container(inp_hora_ini, expand=True), ft.Container(inp_hora_fin, expand=True)], spacing=4),
                ft.Text("Inicio rápido:", size=10, weight="bold", color=IOS_GRAY),
                fila_ini_rapidos,
                ft.Text("Fin rápido:", size=10, weight="bold", color=IOS_GRAY),
                fila_fin_rapidos,
                inp_notas,
                fila_estados
            ], spacing=6)
        )

        columna_derecha_servicios = ft.Container(
            width=360,
            padding=8,
            border_radius=12,
            bgcolor="#FDF8FA",
            content=ft.Column([
                ft.Text("2. Seleccionar Servicio Base", size=12, weight="bold", color=IOS_PINK),
                grid_servicios_dos_col
            ], spacing=6)
        )

        panel_modal.content = ft.Column([
            ft.Row([
                ft.Text("✏️ Editar Cita" if es_edicion else "➕ Agendar Cita", size=18, weight="bold", color=IOS_DARK),
                boton_ios("Cerrar", IOS_BG, IOS_DARK, cerrar_modal, ancho=75, padding=8)
            ], alignment="spaceBetween"),
            ft.Row([
                columna_izquierda_datos,
                columna_derecha_servicios
            ], spacing=10, wrap=True, vertical_alignment="start"),
            ft.Row([
                boton_ios("🗑️ Eliminar", "#FFE5E5", IOS_RED, eliminar, ancho=110, visible=es_edicion, padding=8),
                boton_ios("💾 Guardar" if es_edicion else "💾 Agendar", IOS_DARK, "white", guardar, expandir=True, padding=8)
            ], spacing=8)
        ], spacing=8, scroll="auto")

        vista_agenda.visible = False
        panel_modal.visible = True
        page.update()

    # ==========================================
    # SECCIÓN B: CATÁLOGO DE SERVICIOS
    # ==========================================
    lista_servicios_ui = ft.Column(spacing=10, scroll="auto", expand=True)
    inp_serv_nom = ft.TextField(label="Nombre del nuevo servicio", border_radius=12)
    inp_serv_pre = ft.TextField(label="Precio en pesos (ej. 48000)", border_radius=12)

    caja_nuevo_servicio_admin = ft.Container(
        bgcolor=IOS_CARD, padding=14, border_radius=16, visible=False,
        content=ft.Column([
            ft.Text("Agregar nuevo servicio al catálogo:", weight="bold", size=14, color=IOS_DARK),
            inp_serv_nom,
            inp_serv_pre,
            boton_ios("Guardar Servicio", IOS_DARK, "white", lambda e: guardar_nuevo_servicio(e))
        ], spacing=8)
    )

    def guardar_nuevo_servicio(e):
        if not estado_app["es_admin"]:
            return
        if not inp_serv_nom.value or not inp_serv_pre.value:
            mostrar_alerta("Escribe el nombre y el precio del servicio")
            return
        try:
            pre_val = float(inp_serv_pre.value.replace(".", "").replace(",", "").replace("$", "").strip())
        except ValueError:
            mostrar_alerta("El precio debe contener solo números")
            return

        db_query(
            "INSERT INTO servicios (nombre, precio, color) VALUES (?, ?, ?)",
            (inp_serv_nom.value.strip(), pre_val, IOS_PINK), fetch=False
        )
        inp_serv_nom.value = ""
        inp_serv_pre.value = ""
        cargar_vista_servicios()
        mostrar_alerta("Servicio agregado al catálogo", IOS_GREEN)

    def borrar_servicio(s_id):
        if not estado_app["es_admin"]:
            return
        db_query("DELETE FROM servicios WHERE id=?", (s_id,), fetch=False)
        cargar_vista_servicios()

    def cargar_vista_servicios():
        lista_servicios_ui.controls.clear()
        caja_nuevo_servicio_admin.visible = estado_app["es_admin"]
        servicios = db_query("SELECT id, nombre, precio, color FROM servicios")
        
        for s_id, nom, precio, col in servicios:
            item = ft.Container(
                bgcolor=IOS_CARD,
                padding=14,
                border_radius=14,
                content=ft.Row([
                    ft.Container(width=8, height=38, border_radius=4, bgcolor=col or IOS_PINK),
                    ft.Column([
                        ft.Text(nom, size=14, weight="bold", color=IOS_DARK),
                        ft.Text("Precio oficial", size=11, color=IOS_GRAY)
                    ], expand=True, spacing=2),
                    ft.Text(f"${precio:,.0f}", size=15, color=IOS_PINK, weight="bold"),
                    boton_ios("Borrar", "#FFE5E5", IOS_RED, lambda e, id_b=s_id: borrar_servicio(id_b), ancho=70, visible=estado_app["es_admin"])
                ], spacing=10)
            )
            lista_servicios_ui.controls.append(item)
        page.update()

    # ==========================================
    # ENCABEZADO SUPERIOR: EL LOGO ABRE EL PANEL ADMIN
    # ==========================================
    logo_uri = obtener_logo_data_uri()
    if logo_uri:
        widget_logo = ft.Container(
            width=54,
            height=54,
            border_radius=27,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            on_click=abrir_panel_administracion,
            content=ft.Image(src=logo_uri, width=54, height=54)
        )
    else:
        widget_logo = ft.Container(
            width=54, height=54, bgcolor=LOGO_PINK, border_radius=27,
            on_click=abrir_panel_administracion,
            content=ft.Column([ft.Text("D", size=22, weight="bold", color=IOS_DARK)], alignment="center", horizontal_alignment="center")
        )

    badge_modo = ft.Container(
        padding=5,
        border_radius=8,
        bgcolor=LOGO_PINK,
        content=ft.Text("👁️ Vista Pública (Clientas)", size=10, weight="bold", color=IOS_DARK)
    )

    encabezado_logo = ft.Row([
        widget_logo,
        ft.Column([
            ft.Text("Diana Rua • Nails Artist 🖤", size=15, weight="bold", color=IOS_DARK),
            badge_modo
        ], spacing=3)
    ], spacing=10)

    btn_nueva_cita = boton_ios("+ Cita", IOS_PINK, "white", lambda e: mostrar_formulario_cita(None), ancho=75, visible=False, padding=9)

    barra_superior_fija = ft.Container(
        padding=10,
        content=ft.Row([
            encabezado_logo,
            ft.Row([
                btn_nueva_cita
            ], spacing=6)
        ], alignment="spaceBetween")
    )

    def actualizar_interfaz_permisos():
        es_adm = estado_app["es_admin"]
        btn_nueva_cita.visible = es_adm
        fila_kpis.visible = es_adm
        
        badge_modo.bgcolor = IOS_GREEN if es_adm else LOGO_PINK
        badge_modo.content.value = "✅ Modo Administradora" if es_adm else "👁️ Vista Pública (Clientas)"
        badge_modo.content.color = "white" if es_adm else IOS_DARK

        renderizar_cuadricula_google()
        cargar_vista_servicios()
        if inp_buscador_clienta.value.strip():
            buscar_cita_por_nombre(None)
        page.update()

    # ==========================================
    # ESTRUCTURA GENERAL DEL DASHBOARD
    # ==========================================
    vista_agenda = ft.Container(
        padding=10,
        expand=True,
        content=ft.Column([
            fila_kpis,
            inp_buscador_clienta,
            contenedor_resultados_busqueda,
            barra_controles_calendario,
            tablero_calendario_completo
        ], expand=True, spacing=8)
    )

    vista_servicios = ft.Container(
        padding=14,
        expand=True,
        visible=False,
        content=ft.Column([
            ft.Row([
                ft.Text("💅 Servicios y Precios", size=20, weight="bold", color=IOS_DARK),
            ], alignment="spaceBetween"),
            caja_nuevo_servicio_admin,
            lista_servicios_ui
        ], expand=True, spacing=10)
    )

    txt_tab_agenda = ft.Text("📅 Agenda Semanal", color="white", weight="bold", size=13)
    txt_tab_servicios = ft.Text("💅 Servicios (19)", color=IOS_DARK, weight="bold", size=13)

    def ir_a_agenda(e):
        banner_aviso.visible = False
        panel_modal.visible = False
        vista_servicios.visible = False
        vista_agenda.visible = True
        btn_tab_agenda.bgcolor = IOS_DARK
        txt_tab_agenda.color = "white"
        btn_tab_servicios.bgcolor = IOS_BG
        txt_tab_servicios.color = IOS_DARK
        renderizar_cuadricula_google()

    def ir_a_servicios(e):
        banner_aviso.visible = False
        panel_modal.visible = False
        vista_agenda.visible = False
        vista_servicios.visible = True
        btn_tab_servicios.bgcolor = IOS_DARK
        txt_tab_servicios.color = "white"
        btn_tab_agenda.bgcolor = IOS_BG
        txt_tab_agenda.color = IOS_DARK
        cargar_vista_servicios()
        page.update()

    btn_tab_agenda = ft.Container(
        bgcolor=IOS_DARK, padding=12, border_radius=12, expand=True, on_click=ir_a_agenda,
        content=ft.Row([txt_tab_agenda], alignment="center")
    )
    btn_tab_servicios = ft.Container(
        bgcolor=IOS_BG, padding=12, border_radius=12, expand=True, on_click=ir_a_servicios,
        content=ft.Row([txt_tab_servicios], alignment="center")
    )

    barra_inferior_ios = ft.Container(
        bgcolor=IOS_CARD,
        padding=10,
        content=ft.Row([btn_tab_agenda, btn_tab_servicios], spacing=10)
    )

    # ==========================================
    # FONDO DEGRADADO CON NUBES
    # ==========================================
    def crear_nube(ancho, alto, top=None, left=None, right=None, bottom=None, color="#88FFFFFF"):
        return ft.Container(
            width=ancho,
            height=alto,
            top=top,
            left=left,
            right=right,
            bottom=bottom,
            bgcolor=color,
            border_radius=alto // 2,
        )

    capa_cielo_y_nubes = ft.Stack(
        expand=True,
        controls=[
            ft.Container(
                expand=True,
                gradient=ft.LinearGradient(
                    begin=ft.Alignment(0, -1),
                    end=ft.Alignment(0, 1),
                    colors=[
                        "#FFD6E4",
                        "#FFF0F5",
                        "#FFFFFF",
                        "#FCE4EC",
                    ],
                ),
            ),
            crear_nube(320, 110, top=20, left=-60, color="#77FFFFFF"),
            crear_nube(200, 90, top=5, left=120, color="#99FFFFFF"),
            crear_nube(360, 120, top=40, right=-80, color="#88FFFFFF"),
            crear_nube(240, 95, top=220, left=40, color="#66FFF5F8"),
            crear_nube(400, 130, top=340, right=60, color="#77FFFFFF"),
            crear_nube(300, 105, bottom=90, left=-40, color="#88FFFFFF"),
            crear_nube(260, 90, bottom=120, right=-30, color="#77FFF0F5"),
            
            ft.Column([
                barra_superior_fija,
                banner_aviso,
                vista_agenda,
                vista_servicios,
                panel_modal,
                barra_inferior_ios
            ], expand=True, spacing=0)
        ]
    )

    page.add(capa_cielo_y_nubes)

    url_inicial = obtener_config("ical_url", ICAL_URL_DEFAULT)
    if url_inicial:
        sincronizar_desde_google_calendar(url_inicial)

    renderizar_cuadricula_google()

# ==========================================
# 5. ARRANQUE OFICIAL
# ==========================================
if __name__ == "__main__":
    puerto = int(os.environ.get("PORT", 8550))
    en_servidor_nube = "PORT" in os.environ
    
    print("====================================================")
    print(f"🚀 Servidor Diana Rua iniciado en: http://localhost:{puerto}")
    print("====================================================")
    
    vista = None if en_servidor_nube else ft.AppView.WEB_BROWSER
    ft.run(main, view=vista, host="0.0.0.0", port=puerto)