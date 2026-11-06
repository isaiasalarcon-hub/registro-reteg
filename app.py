import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
from datetime import date

st.set_page_config(page_title="REGISTRO RETEG", layout="wide")
st.title("REGISTRO RETEG")
st.caption("Registro de tratamiento endoscópico de dehiscencias posquirúrgicas")

@st.cache_resource
def worksheet():
    credentials = Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return gspread.authorize(credentials).open_by_key(
        st.secrets["spreadsheet_id"]
    ).worksheet("REGISTRO INICIAL")

SURGERIES = ["Sleeve", "Bypass gástrico", "SASI", "Funduplicatura / hiato",
             "Esofaguectomía", "Gastrectomía", "Otra"]
DIAGNOSES = ["TC con contraste", "EDA", "Tránsito con contraste",
             "Diagnóstico intraoperatorio", "Otros"]
THERAPIES = ["EVT / EndoVAC", "VACStent", "Prótesis / stent",
             "Pigtail", "Clips / OTSC", "Sutura endoscópica", "Otros"]

with st.form("reteg"):
    patient_id = st.text_input("ID del paciente")
    surgery = st.selectbox("Cirugía previa", SURGERIES)
    if surgery == "Otra":
        surgery = st.text_input("Especificar cirugía")
    surgery_date = st.date_input("Fecha de cirugía", value=None, max_value=date.today(), format="DD/MM/YYYY")
    diagnosis = st.multiselect("Método diagnóstico de la fuga", DIAGNOSES)
    therapy = st.multiselect("Terapia endoscópica utilizada", THERAPIES)
    finished = st.radio("Fin de proceso", ["NO", "SI"], horizontal=True)
    review = st.date_input("Fecha siguiente revisión", value=None, format="DD/MM/YYYY") if finished == "NO" else None
    save = st.form_submit_button("Guardar", type="primary")

if save:
    if not patient_id.strip() or not surgery.strip() or surgery_date is None or not diagnosis or not therapy or (finished == "NO" and review is None):
        st.error("Completa los campos obligatorios y la fecha de revisión si el proceso no ha finalizado.")
    else:
        try:
            ws = worksheet()
            existing = ws.col_values(1)[1:]
            if patient_id.strip() in existing:
                st.error("Este ID ya existe. No se ha creado un registro duplicado.")
            else:
                ws.append_row([
                    patient_id.strip(), surgery, surgery_date.strftime("%d/%m/%Y"),
                    ", ".join(diagnosis), ", ".join(therapy), finished,
                    review.strftime("%d/%m/%Y") if review else ""
                ], value_input_option="RAW")
                st.success("Registro guardado en Google Sheets.")
        except Exception:
            st.error("No se ha podido confirmar el guardado. Comprueba la conexión con Google Sheets.")

st.divider()
st.subheader("Registros")
try:
    data = worksheet().get_all_records()
    if data:
        st.dataframe(data, use_container_width=True, hide_index=True)
    else:
        st.info("Todavía no hay registros.")
except Exception:
    st.info("Pendiente de configurar la conexión con Google Sheets en Streamlit Secrets.")
