import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
from datetime import date

st.set_page_config(page_title="REGISTRO RETEG", layout="wide")
st.title("REGISTRO RETEG")
st.caption("Registro de tratamiento endoscópico de dehiscencias posquirúrgicas")

@st.cache_resource
def spreadsheet():
    credentials = Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return gspread.authorize(credentials).open_by_key(st.secrets["spreadsheet_id"])

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

with st.container(border=True):
    st.subheader("Endoscopia actual")
    c1, c2 = st.columns(2)
    with c1:
        endoscopy_number = st.number_input("N.º de endoscopia", min_value=1, step=1,
                                          help="1 para la primera endoscopia; 2, 3… para las siguientes.")
    with c2:
        endoscopy_date = st.date_input("Fecha de endoscopia", value=None,
                                      max_value=date.today(), format="DD/MM/YYYY")
    c1, c2 = st.columns(2)
    with c1:
        digestivo = st.selectbox("Digestivo", DIGESTIVOS, index=None,
                                placeholder="Seleccionar digestivo")
        if digestivo == "OTRO":
            other_digestivo = st.text_input("Especificar otro digestivo")
            digestivo = "OTRO: " + other_digestivo.strip() if other_digestivo.strip() else None
    with c2:
        place = st.selectbox("Lugar", PLACES, index=None, placeholder="Seleccionar lugar")
        if place == "OTRO":
            other_place = st.text_input("Especificar otro lugar")
            place = "OTRO: " + other_place.strip() if other_place.strip() else None
    therapy = st.selectbox("Tratamiento", THERAPIES, index=None,
                           placeholder="Seleccionar tratamiento")
    review = None
    if therapy == "FIN TRATAMIENTO":
        st.info("Fin del tratamiento: no se programa un siguiente recambio.")
    else:
        review = st.date_input("Fecha estimada siguiente recambio", value=None,
                               format="DD/MM/YYYY")

if st.button("Guardar endoscopia", type="primary"):
    error = validate_record(patient_id, surgery, surgery_date, endoscopy_date,
                            digestivo, place, therapy, review)
    if error:
        st.error(error)
    else:
        try:
            ws = endoscopy_sheet(create=True)
            rows = ws.get_all_values()[1:]
            duplicate = any(len(row) > 3 and row[0].strip() == patient_id.strip()
                            and row[3] == str(int(endoscopy_number)) for row in rows)
            if duplicate:
                st.error("Este ID ya tiene guardado ese número de endoscopia. No se ha duplicado.")
            else:
                ws.append_row([
                    patient_id.strip(), surgery, surgery_date.strftime("%d/%m/%Y"),
                    int(endoscopy_number), endoscopy_date.strftime("%d/%m/%Y"),
                    digestivo, place, therapy,
                    review.strftime("%d/%m/%Y") if review else ""
                ], value_input_option="RAW")
                st.success("Endoscopia guardada en Google Sheets.")
        except ValueError as exc:
            st.error(str(exc))
        except Exception:
            st.error("No se ha podido confirmar el guardado. Comprueba la conexión y la tabla antes de reintentar.")

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
except ValueError as exc:
    st.error(str(exc))
except Exception:
    st.info("No se pueden cargar los registros. Comprueba la conexión con Google Sheets en Streamlit Secrets.")
