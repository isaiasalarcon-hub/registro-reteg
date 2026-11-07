import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
from datetime import date
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
    if not patient_id.strip() or not surgery.strip() or not surgery_date or not endoscopy_date:
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

with st.container(border=True):
    st.subheader("Paciente y cirugía")
    c1, c2, c3 = st.columns([1, 2, 1])
    with c1:
        patient_id = st.text_input("ID")
    with c2:
        surgery = st.selectbox("Cirugía", SURGERIES)
        if surgery == "Otra":
            surgery = st.text_input("Especificar cirugía")
    with c3:
        surgery_date = st.date_input("Fecha cirugía", value=None,
                                    max_value=date.today(), format="DD/MM/YYYY")

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
if message := st.session_state.pop("endoscopy_notice", None):
    st.success(message)

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
                    st.success("Endoscopia guardada en Google Sheets.")
        except ValueError as exc:
            st.error(str(exc))
        except Exception:
            st.error("No se ha podido confirmar el guardado. Se conserva la endoscopia actual; comprueba la conexión antes de reintentar.")

st.divider()
st.subheader("Endoscopias registradas")
try:
    ws = endoscopy_sheet()
    data = ws.get_all_records(numericise_ignore=["all"]) if ws else []
    if data:
        st.dataframe(data, use_container_width=True, hide_index=True)
    else:
        st.info("Todavía no hay endoscopias registradas.")
    try:
        previous = spreadsheet().worksheet("REGISTRO INICIAL").get_all_records(numericise_ignore=["all"])
    except gspread.WorksheetNotFound:
        previous = []
    if previous:
        with st.expander("Registros iniciales anteriores"):
            st.dataframe(previous, use_container_width=True, hide_index=True)
except Exception as exc:
    st.error(connection_error(exc))
