import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
from datetime import date, datetime
from google.auth.exceptions import RefreshError
from streamlit.errors import StreamlitSecretNotFoundError

st.set_page_config(page_title="REGISTRO RETEG", layout="wide")
st.title("REGISTRO RETEG")
st.caption("Registro de tratamiento endoscópico de dehiscencias posquirúrgicas")

@st.cache_resource
def spreadsheet():
    account = dict(st.secrets["gcp_service_account"])
    # Accept literal escaped newlines from pasted TOML credentials.
    if isinstance(account.get("private_key"), str):
        account["private_key"] = account["private_key"].replace("\\n", "\n")
    credentials = Credentials.from_service_account_info(
        account,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return gspread.authorize(credentials).open_by_key(st.secrets["spreadsheet_id"])


def connection_error(exc):
    # Never show raw exception messages: they may contain credentials or sheet data.
    if isinstance(exc, StreamlitSecretNotFoundError):
        return "Falta configurar Secrets en Streamlit: spreadsheet_id y gcp_service_account."
    if isinstance(exc, KeyError):
        field = exc.args[0] if exc.args else None
        if field in ("spreadsheet_id", "gcp_service_account"):
            return f"Falta el campo {field} en Streamlit Secrets."
        return "La configuración de Secrets está incompleta: comprueba spreadsheet_id y gcp_service_account."
    if isinstance(exc, (PermissionError, gspread.exceptions.SpreadsheetNotFound)):
        return "No se puede acceder a la hoja: comprueba spreadsheet_id y que la cuenta de servicio tenga permiso de Editor."
    if isinstance(exc, RefreshError):
        return "Google rechaza las credenciales de la cuenta de servicio. Revisa su clave en Streamlit Secrets."
    if isinstance(exc, gspread.exceptions.APIError):
        code = getattr(exc, "code", None)
        if code == 403:
            return "Google deniega el acceso (403). Revisa los permisos de la hoja y que Google Sheets API esté habilitada."
        if code == 429:
            return "Google ha limitado temporalmente las consultas. Espera un momento y pulsa Reintentar conexión."
        return "Google Sheets no ha respondido correctamente. Reintenta la conexión."
    if isinstance(exc, ValueError):
        if str(exc) in (
            "La estructura de ENDOSCOPIAS no coincide con el formulario.",
            "Número de endoscopia no válido en los registros.",
        ):
            return str(exc)
        return "Hay un formato no válido en las credenciales o en la numeración de ENDOSCOPIAS."
    return "No se ha podido conectar con Google Sheets. Revisa la configuración y reintenta."

def retry_connection():
    spreadsheet.clear()
    st.session_state.current_endoscopy = None

SURGERIES = ["Sleeve", "Bypass gástrico", "SASI", "Funduplicatura / hiato",
             "Esofaguectomía", "Gastrectomía", "Otra"]
DIGESTIVOS = ["GARCIA", "MARTIN", "MARTINEZ", "DE LA SANTA", "BELDA", "OTRO"]
PLACES = ["Sala RX", "Sala convencional", "Quirófano urgencias", "UCI", "OTRO"]
THERAPIES = ["ESPONJA", "VAC STENT", "STENT", "FIN TRATAMIENTO"]
HEADERS = ["ID", "Cirugía", "Fecha cirugía", "N.º endoscopia", "Fecha endoscopia",
           "Digestivo", "Lugar", "Tratamiento", "Fecha estimada siguiente recambio"]

def endoscopy_sheet(create=False):
    book = spreadsheet()
    try:
        ws = book.worksheet("ENDOSCOPIAS")
    except gspread.WorksheetNotFound:
        if not create:
            return None
        ws = book.add_worksheet(title="ENDOSCOPIAS", rows=1000, cols=len(HEADERS))
    headers = ws.row_values(1)
    if not headers and create:
        ws.append_row(HEADERS, value_input_option="RAW")
    elif headers and headers != HEADERS:
        raise ValueError("La estructura de ENDOSCOPIAS no coincide con el formulario.")
    return ws

def validate_record(patient_id, surgery, surgery_date, endoscopy_date, digestivo,
                    place, therapy, review):
    if not patient_id.strip() or not (surgery or "").strip() or not surgery_date or not endoscopy_date:
        return "Completa el ID, la cirugía y ambas fechas."
    if not digestivo or not place or not therapy:
        return "Selecciona digestivo, lugar y tratamiento."
    if endoscopy_date < surgery_date:
        return "La fecha de endoscopia no puede ser anterior a la cirugía."
    if therapy != "FIN TRATAMIENTO":
        if review is None:
            return "Indica la fecha estimada del siguiente recambio."
        if review < endoscopy_date:
            return "La fecha del siguiente recambio no puede ser anterior a la endoscopia."
    return None


def latest_patient_records(records):
    """The highest endoscopy number determines each patient's current status."""
    latest = {}
    for record in records:
        patient = str(record.get("ID", "")).strip()
        if not patient:
            continue
        number = int(record["N.º endoscopia"])
        if number < 1:
            raise ValueError("Número de endoscopia no válido en los registros.")
        if patient not in latest or number > latest[patient][0]:
            latest[patient] = (number, record)
    return [record for _, record in latest.values()]

def clear_form():
    for key in list(st.session_state):
        if key.startswith(("patient:", "endo:")) or key in ("active_patient", "current_endoscopy"):
            del st.session_state[key]
    st.session_state["table_version"] = st.session_state.get("table_version", 0) + 1

def select_case(table_key, records):
    selected = st.session_state.get(table_key, {}).get("selection", {}).get("rows", [])
    if selected and 0 <= selected[0] < len(records):
        st.session_state["open_case"] = dict(records[selected[0]])

def load_case(record):
    # Validate before changing any form state.
    surgery_date = datetime.strptime(str(record["Fecha cirugía"]).strip(), "%d/%m/%Y").date()
    patient = str(record["ID"]).strip()
    surgery = str(record["Cirugía"]).strip()
    clear_form()
    st.session_state["patient:id"] = patient
    st.session_state["patient:surgery"] = surgery if surgery in SURGERIES else "Otra"
    if surgery not in SURGERIES:
        st.session_state["patient:other_surgery"] = surgery
    st.session_state["patient:surgery_date"] = surgery_date
    # Let the existing connection logic recalculate the next number from Sheets.
    st.session_state["active_patient"] = patient
    st.session_state["current_endoscopy"] = None

def change_section():
    if st.session_state["section"] == "Nuevo caso":
        st.session_state.reset_endoscopy_form = True
        st.session_state.pop("open_case", None)

# Reset before the table and input widgets are instantiated.
if st.session_state.pop("reset_endoscopy_form", False):
    clear_form()
if case := st.session_state.pop("open_case", None):
    try:
        load_case(case)
    except (KeyError, ValueError):
        st.error("No se puede abrir este caso: revisa su ID, cirugía y fecha de cirugía.")

with st.sidebar:
    st.header("Menú")
    section = st.radio(
        "Ver", ["Pendientes", "Total", "Nuevo caso"],
        key="section", on_change=change_section,
    )

if message := st.session_state.pop("endoscopy_notice", None):
    st.success(message)

if section != "Nuevo caso":
    st.subheader("Casos pendientes" if section == "Pendientes" else "Todos los casos")
    try:
        ws = endoscopy_sheet()
        data = ws.get_all_records(numericise_ignore=["all"]) if ws else []
        latest = latest_patient_records(data)
        visible = [
            record for record in latest
            if section == "Total"
            or str(record.get("Tratamiento", "")).strip().upper() != "FIN TRATAMIENTO"
        ]
        st.caption(f"{len(visible)} casos")
        if visible:
            summary = [
                {
                    "ID": str(record["ID"]).strip(),
                    "Tratamiento": record.get("Tratamiento", ""),
                    "Fecha siguiente revisión": record.get("Fecha estimada siguiente recambio", ""),
                }
                for record in visible
            ]
            table_key = f"cases:{section}:{st.session_state.get('table_version', 0)}"
            st.dataframe(
                summary, use_container_width=True, hide_index=True,
                key=table_key, selection_mode="single-row",
                on_select=lambda key=table_key, records=visible: select_case(key, records),
            )
            st.caption("Selecciona una fila para añadir la siguiente endoscopia de ese paciente.")
        else:
            st.info("No hay casos pendientes." if section == "Pendientes"
                    else "Todavía no hay endoscopias registradas.")
        if section == "Pendientes":
            st.caption("Casos iniciados cuya última endoscopia no indica FIN TRATAMIENTO.")
    except Exception as exc:
        st.error(connection_error(exc))
    st.divider()
else:
    st.subheader("Nuevo caso")

with st.container(border=True):
    st.subheader("Paciente y cirugía")
    c1, c2, c3 = st.columns([1, 2, 1])
    with c1:
        patient_id = st.text_input("ID", key="patient:id")
    with c2:
        surgery = st.selectbox("Cirugía", SURGERIES, index=None,
                               placeholder="Seleccionar cirugía", key="patient:surgery")
        if surgery == "Otra":
            surgery = st.text_input("Especificar cirugía", key="patient:other_surgery")
    with c3:
        surgery_date = st.date_input("Fecha cirugía", value=None,
                                    max_value=date.today(), format="DD/MM/YYYY", key="patient:surgery_date")

def next_endoscopy_number(rows, patient_id):
    numbers = []
    for row in rows:
        if len(row) > 3 and row[0].strip() == patient_id.strip():
            number = int(row[3])
            if number < 1:
                raise ValueError("Número de endoscopia no válido en los registros.")
            numbers.append(number)
    return max(numbers, default=0) + 1

patient_key = patient_id.strip()
if st.session_state.get("active_patient") != patient_key:
    st.session_state.active_patient = patient_key
    st.session_state.current_endoscopy = None

if patient_key and st.session_state.get("current_endoscopy") is None:
    try:
        ws = endoscopy_sheet()
        rows = ws.get_all_values()[1:] if ws else []
        st.session_state.current_endoscopy = next_endoscopy_number(rows, patient_key)
    except Exception as exc:
        st.warning(connection_error(exc))
        st.button("Reintentar conexión", on_click=retry_connection)

endoscopy_number = st.session_state.get("current_endoscopy")
field_key = f"endo:{patient_key}:{endoscopy_number}:"

with st.container(border=True):
    st.subheader("Endoscopia actual")
    c1, c2 = st.columns(2)
    with c1:
        st.metric("N.º de endoscopia", endoscopy_number if endoscopy_number else "—")
        st.caption("Numeración automática por paciente.")
    with c2:
        endoscopy_date = st.date_input("Fecha de endoscopia", value=None,
                                      max_value=date.today(), format="DD/MM/YYYY", key=field_key + "date")
    c1, c2 = st.columns(2)
    with c1:
        digestivo = st.selectbox("Digestivo", DIGESTIVOS, index=None,
                                placeholder="Seleccionar digestivo", key=field_key + "digestivo")
        if digestivo == "OTRO":
            other_digestivo = st.text_input("Especificar otro digestivo", key=field_key + "other_digestivo")
            digestivo = "OTRO: " + other_digestivo.strip() if other_digestivo.strip() else None
    with c2:
        place = st.selectbox("Lugar", PLACES, index=None, placeholder="Seleccionar lugar", key=field_key + "place")
        if place == "OTRO":
            other_place = st.text_input("Especificar otro lugar", key=field_key + "other_place")
            place = "OTRO: " + other_place.strip() if other_place.strip() else None
    therapy = st.selectbox("Tratamiento", THERAPIES, index=None,
                           placeholder="Seleccionar tratamiento", key=field_key + "therapy")
    review = None
    if therapy == "FIN TRATAMIENTO":
        st.info("Fin del tratamiento: no se programa un siguiente recambio.")
    else:
        review = st.date_input("Fecha estimada siguiente recambio", value=None,
                               format="DD/MM/YYYY", key=field_key + "review")

    next_clicked = st.button(
        "Siguiente endoscopia",
        disabled=endoscopy_number is None or therapy == "FIN TRATAMIENTO",
        help="Guarda la endoscopia actual y abre la siguiente conservando los datos del paciente.",
    )

save_clicked = st.button("Guardar endoscopia", type="primary",
                         disabled=endoscopy_number is None)
if save_clicked or next_clicked:
    error = validate_record(patient_id, surgery, surgery_date, endoscopy_date,
                            digestivo, place, therapy, review)
    if error:
        st.error(error)
    else:
        try:
            ws = endoscopy_sheet(create=True)
            rows = ws.get_all_values()[1:]
            record = [
                patient_key, surgery, surgery_date.strftime("%d/%m/%Y"),
                str(endoscopy_number), endoscopy_date.strftime("%d/%m/%Y"),
                digestivo, place, therapy,
                review.strftime("%d/%m/%Y") if review else ""
            ]
            matches = [row for row in rows if len(row) > 3
                       and row[0].strip() == patient_key
                       and row[3] == str(endoscopy_number)]
            # A previously saved identical record can advance without being duplicated.
            if matches and (len(matches) != 1 or
                            (matches[0] + [""] * len(HEADERS))[:len(HEADERS)] != record):
                st.error("Esta endoscopia ya existe con otros datos. No se ha sobrescrito ni se ha avanzado.")
            else:
                if not matches:
                    ws.append_row(record, value_input_option="RAW")
                if next_clicked:
                    updated_rows = ws.get_all_values()[1:]
                    st.session_state.current_endoscopy = next_endoscopy_number(updated_rows, patient_key)
                    st.session_state.endoscopy_notice = (
                        f"Endoscopia {endoscopy_number} guardada. Puedes rellenar la siguiente."
                    )
                    st.rerun()
                else:
                    st.session_state.reset_endoscopy_form = True
                    st.session_state.endoscopy_notice = (
                        f"Endoscopia {endoscopy_number} guardada en Google Sheets. "
                        "Formulario listo para un nuevo registro."
                    )
                    st.rerun()
        except ValueError as exc:
            st.error(str(exc))
        except Exception:
            st.error("No se ha podido confirmar el guardado. Se conserva la endoscopia actual; comprueba la conexión antes de reintentar.")

