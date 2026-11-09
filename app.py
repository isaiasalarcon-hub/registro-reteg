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
    return gspread.authorize(credentials).open_by_key("1oBBqc0Z1uDEB80IjWC8aL1WF23ktnD6Tmk4nmyGVeGA")


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
DIGESTIVOS = ["GARCIA", "MARTIN", "MARTINEZ", "DE LA SANTA", "BELDA", "MORENO", "OTRO"]
PLACES = ["Sala RX", "Sala convencional", "Quirófano urgencias", "UCI", "OTRO"]
THERAPIES = ["ESPONJA", "VAC STENT", "STENT", "FIN TRATAMIENTO"]
HEADERS = ["ID", "Cirugía", "Fecha cirugía", "N.º endoscopia", "Fecha endoscopia",
           "Digestivo", "Lugar", "Tratamiento", "Fecha estimada siguiente recambio", "Observaciones", "Cambio de terapia", "Fin de tratamiento"]

def endoscopy_sheet(create=False):
    book = spreadsheet()
    try:
        ws = book.worksheet("ENDOSCOPIAS")
    except gspread.WorksheetNotFound:
        if not create:
            return None
        ws = book.add_worksheet(title="ENDOSCOPIAS", rows=1000, cols=len(HEADERS))
    headers = ws.row_values(1)
    if headers and headers not in (HEADERS, HEADERS[:9]):
        raise ValueError("La estructura de ENDOSCOPIAS no coincide con el formulario.")
    if create:
        # Older worksheets were created with nine columns. Expand first, then
        # migrate headers without touching existing patient rows.
        if ws.col_count < len(HEADERS):
            ws.add_cols(len(HEADERS) - ws.col_count)
        if not headers:
            ws.update(range_name="A1:L1", values=[HEADERS])
        elif headers == HEADERS[:9]:
            ws.update(range_name="J1:L1", values=[HEADERS[9:]])
    return ws

def validate_record(patient_id, surgery, surgery_date, endoscopy_date, digestivo,
                    place, therapy, review, pending_date=False, finished="NO", observations=""):
    if not patient_id.strip() or not (surgery or "").strip() or not surgery_date or not endoscopy_date:
        return "Completa el ID, la cirugía y ambas fechas."
    if not digestivo or not place or not therapy:
        return "Selecciona digestivo, lugar y tratamiento."
    if not (observations or "").strip():
        return "Completa OBSERVACIONES. Si no hay incidencias, escribe Sin incidencias."
    if endoscopy_date < surgery_date:
        return "La fecha de endoscopia no puede ser anterior a la cirugía."
    if finished != "SI" and therapy != "FIN TRATAMIENTO":
        if review is None and not pending_date:
            return "Indica la fecha estimada del siguiente recambio o marca PENDIENTE DE FECHA."
        if review is not None and review < endoscopy_date:
            return "La fecha del siguiente recambio no puede ser anterior a la endoscopia."
    return None


def missing_field_labels(patient_id, surgery, surgery_date, endoscopy_date, digestivo,
                         place, therapy, review, pending_date=False, finished="NO",
                         observations=""):
    """Return the labels that must be corrected before saving."""
    missing = []
    if not str(patient_id or "").strip():
        missing.append("ID")
    if not str(surgery or "").strip():
        missing.append("Cirugía")
        missing.append("Especificar cirugía")
    if surgery_date is None:
        missing.append("Fecha cirugía")
    if endoscopy_date is None or (surgery_date and endoscopy_date and endoscopy_date < surgery_date):
        missing.append("Fecha de endoscopia")
        missing.append("Fecha endoscopia")
    if not str(digestivo or "").strip():
        missing.extend(["Digestivo", "Especificar otro digestivo", "Especificar digestivo"])
    if not str(place or "").strip():
        missing.extend(["Lugar", "Especificar otro lugar", "Especificar lugar"])
    if not str(therapy or "").strip():
        missing.append("Tratamiento")
    if not str(observations or "").strip():
        missing.append("OBSERVACIONES")
    if finished != "SI" and therapy != "FIN TRATAMIENTO" and not pending_date:
        if review is None or (endoscopy_date and review < endoscopy_date):
            missing.append("Fecha estimada siguiente recambio")
    return list(dict.fromkeys(missing))


def highlight_missing_fields(labels):
    """Highlight only invalid Streamlit fields, including their labels."""
    import html
    if not labels:
        return
    rules = []
    for label in labels:
        safe = label.replace("\\", "\\\\").replace('"', '\\"')
        for kind in ("stTextInput", "stDateInput", "stSelectbox", "stTextArea"):
            selector = f'div[data-testid="{kind}"]:has([aria-label="{safe}"])'
            rules.append(f'{selector} label {{ color: #c62828 !important; font-weight: 700 !important; }}')
            rules.append(f'{selector} [data-baseweb="input"], {selector} [data-baseweb="select"], {selector} textarea {{ border: 2px solid #d32f2f !important; border-radius: 6px !important; }}')
    st.markdown("<style>" + "\n".join(rules) + "</style>", unsafe_allow_html=True)
    st.error("Hay campos obligatorios sin completar o con fechas no válidas. Revisa los campos resaltados en rojo.")
    st.caption("Revisar: " + ", ".join(dict.fromkeys(labels)))


def is_finished(record):
    value = str(record.get("Fin de tratamiento", "")).strip().upper()
    if value in ("SI", "SÍ"):
        return True
    if value == "NO":
        return False
    return str(record.get("Tratamiento", "")).strip().upper() == "FIN TRATAMIENTO"


def parse_record_date(value):
    try:
        return datetime.strptime(str(value).strip(), "%d/%m/%Y").date()
    except (ValueError, TypeError):
        return None


def delete_reteg_records(patient_id, number=None):
    """Delete records from Google Sheets, which is RETEG's source of truth.

    Individual deletion is limited to the latest endoscopy so numbering stays
    consecutive. A patient deletion removes all their rows from both sheets.
    """
    patient_id = str(patient_id).strip()
    if not patient_id:
        raise ValueError("ID de paciente no válido.")
    book = spreadsheet()
    ws = book.worksheet("ENDOSCOPIAS")
    values = ws.get_all_values()
    matches = [(idx, row) for idx, row in enumerate(values[1:], start=2)
               if row and str(row[0]).strip() == patient_id]
    if not matches:
        raise ValueError("No se han encontrado endoscopias para ese ID.")
    if number is not None:
        number = str(number)
        last = max(int(row[3]) for _, row in matches)
        if int(number) != last:
            raise ValueError(
                "Por seguridad, elimina primero las endoscopias más recientes. "
                "Así se mantiene la numeración consecutiva."
            )
        matches = [(idx, row) for idx, row in matches if len(row) > 3 and row[3].strip() == number]
        if len(matches) != 1:
            raise ValueError("No se ha identificado una única endoscopia; no se ha borrado nada.")
    else:
        # Remove any matching patient-level rows as well.
        try:
            initial_ws = book.worksheet("REGISTRO INICIAL")
        except gspread.WorksheetNotFound:
            initial_ws = None
        if initial_ws is not None:
            initial_rows = initial_ws.get_all_values()
            if initial_rows:
                headers = [h.strip().upper() for h in initial_rows[0]]
                id_col = headers.index("ID") if "ID" in headers else None
                if id_col is not None:
                    for idx in range(len(initial_rows), 1, -1):
                        row = initial_rows[idx - 1]
                        if len(row) > id_col and str(row[id_col]).strip() == patient_id:
                            initial_ws.delete_rows(idx)
    for idx, _ in sorted(matches, key=lambda item: item[0], reverse=True):
        # Delete from bottom to top to avoid shifting row indexes.
        ws.delete_rows(idx)
    return len(matches)


def deletion_controls(patient_id, number=None):
    """Require an explicit confirmation before deleting any saved record."""
    scope = f"endo:{number}" if number is not None else "patient"
    key = f"delete:{patient_id}:{scope}"
    label = ("esta endoscopia" if number is not None else
             "el paciente y todas sus endoscopias")
    if not st.session_state.get(key, False):
        if st.button("🗑️ ELIMINAR " + ("ENDOSCOPIA" if number is not None else "PACIENTE"),
                     key=key + ":open"):
            st.session_state[key] = True
            st.rerun()
        return
    st.warning(f"Vas a eliminar {label} del ID {patient_id} de la app y del Google Sheets. Esta acción no se puede deshacer desde la aplicación.")
    confirm = st.checkbox("Entiendo que se eliminarán los datos del Excel", key=key + ":confirm")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("CONFIRMAR ELIMINACIÓN", key=key + ":yes", type="primary",
                     disabled=not confirm):
            try:
                total = delete_reteg_records(patient_id, number)
                st.session_state.pop(key, None)
                st.session_state.pop(key + ":confirm", None)
                st.session_state.pop("edit_endoscopy", None)
                st.session_state["endoscopy_notice"] = (
                    f"Eliminación confirmada: {total} endoscopia(s) eliminada(s) del Google Sheets."
                )
                if number is None:
                    clear_form()
                    st.session_state["case_page"] = False
                    st.session_state["section"] = "Total"
                else:
                    st.session_state["endoscopy_form_open"] = False
                    st.session_state["current_endoscopy"] = None
                st.rerun()
            except (ValueError, gspread.exceptions.APIError, gspread.WorksheetNotFound) as exc:
                st.error(f"No se ha completado la eliminación: {type(exc).__name__}. Revisa el registro antes de volver a intentarlo.")
    with c2:
        if st.button("CANCELAR", key=key + ":no"):
            st.session_state.pop(key, None)
            st.session_state.pop(key + ":confirm", None)
            st.rerun()


def edit_existing_endoscopy(record):
    """Edit a saved endoscopy in place, retaining its patient ID and number."""
    patient = str(record["ID"]).strip()
    number = str(record["N.º endoscopia"]).strip()
    prefix = f"edit:{patient}:{number}:"
    st.markdown(f"**EDITAR ENDOSCOPIA {number}**")
    st.caption("Se actualizará el registro existente. El ID y el número de endoscopia no cambian.")
    with st.form(prefix + "form"):
        surgery_options = SURGERIES
        old_surgery = str(record.get("Cirugía", "")).strip()
        surgery_choice = st.selectbox("Cirugía", surgery_options,
            index=surgery_options.index(old_surgery) if old_surgery in surgery_options else len(surgery_options)-1)
        surgery = st.text_input("Especificar cirugía", value=old_surgery if surgery_choice == "Otra" and old_surgery not in surgery_options else "") if surgery_choice == "Otra" else surgery_choice
        surgery_date = st.date_input("Fecha cirugía", value=parse_record_date(record.get("Fecha cirugía")), format="DD/MM/YYYY")
        endoscopy_date = st.date_input("Fecha endoscopia", value=parse_record_date(record.get("Fecha endoscopia")), format="DD/MM/YYYY")
        old_digestivo = str(record.get("Digestivo", "")).strip()
        digestivo_choice = st.selectbox("Digestivo", DIGESTIVOS, index=DIGESTIVOS.index(old_digestivo) if old_digestivo in DIGESTIVOS else (DIGESTIVOS.index("OTRO") if old_digestivo else None))
        digestivo = st.text_input("Especificar digestivo", value=old_digestivo.removeprefix("OTRO: ").strip() if old_digestivo else "") if digestivo_choice == "OTRO" else digestivo_choice
        old_place = str(record.get("Lugar", "")).strip()
        place_choice = st.selectbox("Lugar", PLACES, index=PLACES.index(old_place) if old_place in PLACES else (PLACES.index("OTRO") if old_place else None))
        place = st.text_input("Especificar lugar", value=old_place.removeprefix("OTRO: ").strip() if old_place else "") if place_choice == "OTRO" else place_choice
        old_therapy = str(record.get("Tratamiento", "")).strip()
        therapy = st.selectbox("Tratamiento", THERAPIES, index=THERAPIES.index(old_therapy) if old_therapy in THERAPIES else None)
        old_finished = is_finished(record)
        finished = "SI" if therapy == "FIN TRATAMIENTO" else st.radio("Fin de tratamiento", ["NO", "SI"], index=1 if old_finished else 0, horizontal=True)
        old_review = str(record.get("Fecha estimada siguiente recambio", "")).strip()
        review = None
        pending_date = False
        if finished == "NO":
            pending_date = st.checkbox("PENDIENTE DE FECHA", value=old_review == "PENDIENTE DE FECHA")
            if not pending_date:
                review = st.date_input("Fecha estimada siguiente recambio", value=parse_record_date(old_review), format="DD/MM/YYYY")
        observations = st.text_area("OBSERVACIONES", value=str(record.get("Observaciones", "")))
        old_change = str(record.get("Cambio de terapia", "NO")).strip().upper()
        change_therapy = "NO"
        if number != "1" and finished == "NO":
            change_therapy = st.radio("CAMBIO DE TERAPIA", ["NO", "SI"], index=1 if old_change in ("SI", "SÍ") else 0, horizontal=True)
        submitted = st.form_submit_button("GUARDAR CAMBIOS", type="primary")
    if not submitted:
        return
    error = validate_record(patient, surgery, surgery_date, endoscopy_date, digestivo,
                            place, therapy, review, pending_date=pending_date,
                            finished=finished, observations=observations)
    if error:
        highlight_missing_fields(missing_field_labels(
            patient, surgery, surgery_date, endoscopy_date, digestivo, place,
            therapy, review, pending_date=pending_date, finished=finished,
            observations=observations))
        st.error(error)
        return
    try:
        ws = endoscopy_sheet(create=True)
        rows = ws.get_all_values()
        matches = [i for i, row in enumerate(rows[1:], start=2)
                   if len(row) > 3 and row[0].strip() == patient and row[3].strip() == number]
        if len(matches) != 1:
            st.error("No se ha encontrado una única endoscopia. No se han modificado datos.")
            return
        new_row = [patient, surgery, surgery_date.strftime("%d/%m/%Y"), number,
                   endoscopy_date.strftime("%d/%m/%Y"), digestivo, place, therapy,
                   review.strftime("%d/%m/%Y") if review else ("PENDIENTE DE FECHA" if pending_date and finished == "NO" else ""),
                   observations.strip(), change_therapy, finished]
        # Update the patient's surgery details consistently across all endoscopies.
        for i, row in enumerate(rows[1:], start=2):
            if row and row[0].strip() == patient and i != matches[0]:
                ws.update(range_name=f"B{i}:C{i}", values=[[surgery, surgery_date.strftime("%d/%m/%Y")]])
        ws.update(range_name=f"A{matches[0]}:L{matches[0]}", values=[new_row])
        st.session_state.pop("edit_endoscopy", None)
        st.session_state["endoscopy_notice"] = f"Endoscopia {number} del paciente {patient} actualizada."
        st.rerun()
    except Exception as exc:
        st.error(connection_error(exc))


def show_history(records, patient_id):
    history = sorted(
        [r for r in records if str(r.get("ID", "")).strip() == patient_id],
        key=lambda r: int(r["N.º endoscopia"]),
    )
    if not history:
        return
    first = history[0]
    with st.container(border=True):
        st.subheader("Datos generales")
        st.write(f"**ID:** {patient_id}")
        st.write(f"**Cirugía:** {first.get('Cirugía', '')}")
        st.write(f"**Fecha cirugía:** {first.get('Fecha cirugía', '')}")
    st.subheader("Endoscopias guardadas")
    for record in history:
        number = str(record["N.º endoscopia"])
        label = ("ENDOSCOPIA INICIAL" if number == "1" else f"{number}ª ENDOSCOPIA")
        label += f" · {record.get('Fecha endoscopia', '')} · {record.get('Tratamiento', '')}"
        with st.expander(label, expanded=False):
            for field in HEADERS[4:]:
                st.write(f"**{field}:** {record.get(field, '') or '—'}")
            edit_key = f"{patient_id}:{number}"
            if st.session_state.get("edit_endoscopy") == edit_key:
                if st.button("CANCELAR EDICIÓN", key=f"cancel_edit:{edit_key}"):
                    st.session_state.pop("edit_endoscopy", None)
                    st.rerun()
                edit_existing_endoscopy(record)
            elif st.button("✏️ EDITAR DATOS", key=f"start_edit:{edit_key}"):
                st.session_state["edit_endoscopy"] = edit_key
                st.rerun()
            deletion_controls(patient_id, number)


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
        if key.startswith(("patient:", "endo:")) or key in ("active_patient", "current_endoscopy", "reteg_missing_fields", "endoscopy_form_open"):
            del st.session_state[key]
    st.session_state["table_version"] = st.session_state.get("table_version", 0) + 1

def select_case(table_key, records):
    selected = st.session_state.get(table_key, {}).get("selection", {}).get("rows", [])
    if selected and 0 <= selected[0] < len(records):
        st.session_state["open_case"] = dict(records[selected[0]])
        st.session_state["case_source"] = "Total"

def load_case(record):
    # Validate before changing any form state.
    surgery_date = datetime.strptime(str(record["Fecha cirugía"]).strip(), "%d/%m/%Y").date()
    patient = str(record["ID"]).strip()
    surgery = str(record["Cirugía"]).strip()
    clear_form()
    st.session_state.pop("edit_endoscopy", None)
    st.session_state["patient:id"] = patient
    st.session_state["patient:surgery"] = surgery if surgery in SURGERIES else "Otra"
    if surgery not in SURGERIES:
        st.session_state["patient:other_surgery"] = surgery
    st.session_state["patient:surgery_date"] = surgery_date
    # Let the existing connection logic recalculate the next number from Sheets.
    st.session_state["active_patient"] = patient
    st.session_state["current_endoscopy"] = None
    st.session_state["case_page"] = True
    st.session_state["endoscopy_form_open"] = False

def back_to_list():
    st.session_state["case_page"] = False
    st.session_state.pop("case_source", None)
    st.session_state["section"] = st.session_state.get("last_list_section", "Pendientes")
    st.session_state["reset_endoscopy_form"] = True
    st.session_state.pop("open_case", None)

def change_section():
    st.session_state["case_page"] = False
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

show_form = section == "Nuevo caso" or st.session_state.get("case_page", False)

def go_to_new_patient():
    # Callbacks run before Streamlit instantiates the sidebar radio.
    st.session_state["section"] = "Nuevo caso"
    st.session_state["case_page"] = False
    st.session_state["reset_endoscopy_form"] = True
    st.session_state.pop("date_edit_record", None)


def open_date_editor(record):
    st.session_state["date_edit_record"] = dict(record)


def date_editor(record):
    patient = str(record["ID"]).strip()
    number = str(record["N.º endoscopia"]).strip()
    st.subheader(f"Asignar próxima fecha · Paciente {patient}")
    st.caption(f"Editar fecha de revisión de la endoscopia {number}, sin crear otra.")
    st.write(f"**Fecha de endoscopia:** {record.get('Fecha endoscopia', '')}")
    st.write(f"**Tratamiento:** {record.get('Tratamiento', '')}")
    with st.form("assign_review_date"):
        new_date = st.date_input("Fecha de la próxima revisión", value=None, format="DD/MM/YYYY")
        submitted = st.form_submit_button("Guardar fecha", type="primary")
    if submitted:
        if new_date is None:
            st.error("Selecciona una fecha.")
            return
        try:
            original_date = datetime.strptime(str(record["Fecha endoscopia"]).strip(), "%d/%m/%Y").date()
            if new_date < original_date:
                st.error("La revisión no puede ser anterior a la endoscopia realizada.")
                return
            ws = endoscopy_sheet()
            if ws is None:
                st.error("No se ha encontrado la hoja ENDOSCOPIAS.")
                return
            rows = ws.get_all_values()
            matching = [
                i for i, row in enumerate(rows[1:], start=2)
                if len(row) > 3 and row[0].strip() == patient and row[3].strip() == number
            ]
            if len(matching) != 1:
                st.error("No se ha identificado una única endoscopia. No se ha modificado ningún dato.")
                return
            row_index = matching[0]
            row = rows[row_index - 1]
            old_value = row[8].strip() if len(row) > 8 else ""
            if old_value not in ("", "PENDIENTE DE FECHA"):
                st.error("La fecha ya ha sido modificada. Vuelve al listado para actualizar la información.")
                return
            if len(row) > 11 and row[11].strip().upper() in ("SI", "SÍ"):
                st.error("El tratamiento está finalizado y no permite programar otra revisión.")
                return
            ws.update_cell(row_index, 9, new_date.strftime("%d/%m/%Y"))
            st.session_state.pop("date_edit_record", None)
            st.session_state["endoscopy_notice"] = f"Fecha de revisión del paciente {patient} actualizada."
            st.rerun()
        except Exception as exc:
            st.error(connection_error(exc))


def open_patient(record, source):
    st.session_state["open_case"] = dict(record)
    st.session_state["case_source"] = source


if st.session_state.get("date_edit_record") is not None:
    st.button("← Volver a PENDIENTES", on_click=lambda: st.session_state.pop("date_edit_record", None))
    date_editor(st.session_state["date_edit_record"])
    st.stop()

if not show_form:
    st.session_state["last_list_section"] = section
    try:
        ws = endoscopy_sheet()
        data = ws.get_all_records(numericise_ignore=["all"]) if ws else []
        latest = latest_patient_records(data)
        pending = [r for r in latest if not is_finished(r)]

        if section == "Pendientes":
            st.subheader("PENDIENTES")
            st.caption(f"{len(pending)} pacientes pendientes · Pulsa cualquier tarjeta para abrir la siguiente endoscopia")
            if pending:
                for record in sorted(pending, key=lambda r: str(r.get("Fecha estimada siguiente recambio", ""))):
                    patient_id = str(record.get("ID", "")).strip()
                    therapy = str(record.get("Tratamiento", ""))
                    review = str(record.get("Fecha estimada siguiente recambio", "")) or "PENDIENTE DE FECHA"
                    number = int(record["N.º endoscopia"]) + 1
                    name = f"{number}ª ENDOSCOPIA"
                    if review == "PENDIENTE DE FECHA":
                        left, right = st.columns([3, 2])
                        with left:
                            st.button(
                                f"ID {patient_id}  |  {therapy}",
                                key=f"pending:{patient_id}",
                                use_container_width=True,
                                on_click=open_patient,
                                args=(record, "Pendientes"),
                            )
                        with right:
                            st.button(
                                "PENDIENTE DE FECHA · Asignar",
                                key=f"date:{patient_id}",
                                use_container_width=True,
                                on_click=open_date_editor,
                                args=(record,),
                            )
                        continue
                    st.button(
                        f"ID {patient_id}  |  {therapy}  |  Próxima: {review}",
                        key=f"pending:{patient_id}",
                        use_container_width=True,
                        on_click=open_patient,
                        args=(record, "Pendientes"),
                    )
            else:
                st.info("No hay casos pendientes.")
            st.divider()

        st.button(
            "➕ NUEVO PACIENTE",
            type="primary",
            use_container_width=True,
            key="home_new_patient",
            on_click=go_to_new_patient,
        )
        st.divider()

        st.subheader("TOTAL")
        st.caption(f"{len(latest)} pacientes registrados · Un registro por paciente")
        if latest:
            summary = [
                {
                    "ID": str(record["ID"]).strip(),
                    "Última endoscopia": record.get("N.º endoscopia", ""),
                    "Tratamiento": record.get("Tratamiento", ""),
                    "Fecha siguiente revisión": record.get("Fecha estimada siguiente recambio", ""),
                    "Estado": "Finalizado" if is_finished(record) else "Pendiente",
                }
                for record in latest
            ]
            table_key = f"cases:total:{st.session_state.get('table_version', 0)}"
            st.dataframe(
                summary, use_container_width=True, hide_index=True,
                key=table_key, selection_mode="single-row",
                on_select=lambda key=table_key, records=latest: select_case(key, records),
            )
            st.caption("Selecciona un paciente para consultar todas sus endoscopias en el desplegable.")
        else:
            st.info("Todavía no hay pacientes registrados.")
    except Exception as exc:
        st.error(connection_error(exc))
    st.stop()

st.button("← Volver al listado", on_click=back_to_list)
if section == "Nuevo caso" or not st.session_state.get("patient:id"):
    st.subheader("Nuevo caso")
else:
    st.title(f"ID {st.session_state['patient:id']}")

if section == "Nuevo caso" and not st.session_state.get("case_page"):
    with st.container(border=True):
        st.markdown("**PACIENTE**")
        c1, c2, c3 = st.columns([1, 2, 1], gap="small")
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
else:
    patient_id = st.session_state.get("patient:id", "")
    surgery = st.session_state.get("patient:surgery", "")
    if surgery == "Otra":
        surgery = st.session_state.get("patient:other_surgery", "")
    surgery_date = st.session_state.get("patient:surgery_date")

# Show previously saved endoscopies as collapsed panels, independently
# of whether the case was opened from PENDIENTES, TOTAL or just saved.
if st.session_state.get("case_page") and st.session_state.get("patient:id"):
    try:
        history_ws = endoscopy_sheet()
        saved_records = history_ws.get_all_records(numericise_ignore=["all"]) if history_ws else []
        show_history(saved_records, str(st.session_state["patient:id"]).strip())
        with st.expander("Eliminar paciente completo", expanded=False):
            deletion_controls(str(st.session_state["patient:id"]).strip())
        current_records = [r for r in saved_records if str(r.get("ID", "")).strip() == str(st.session_state["patient:id"]).strip()]
        last_record = max(current_records, key=lambda r: int(r["N.º endoscopia"])) if current_records else None
        if last_record and not is_finished(last_record) and not st.session_state.get("endoscopy_form_open", False):
            if st.button("➕ CARGAR SIGUIENTE ENDOSCOPIA", type="primary", use_container_width=True):
                st.session_state["endoscopy_form_open"] = True
                st.rerun()
        if last_record and is_finished(last_record):
            st.success("Tratamiento finalizado. No hay una nueva endoscopia pendiente.")
    except Exception as exc:
        st.error(connection_error(exc))

# After saving, remain on the closed history until explicitly loading the next form.
if st.session_state.get("case_page") and not st.session_state.get("endoscopy_form_open", False):
    st.stop()

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
if st.session_state.get("case_page") and endoscopy_number and endoscopy_number > 1:
    try:
        previous_ws = endoscopy_sheet()
        previous_records = previous_ws.get_all_records(numericise_ignore=["all"]) if previous_ws else []
        previous = next(
            (r for r in previous_records
             if str(r.get("ID", "")).strip() == patient_key
             and str(r.get("N.º endoscopia", "")).strip() == str(endoscopy_number - 1)),
            None,
        )
        if previous:
            st.info(
                f"ENDOSCOPIA PREVIA · {previous.get('Fecha endoscopia', 'Sin fecha')} "
                f"· Tratamiento colocado: {previous.get('Tratamiento', 'No consta')}"
            )
    except Exception as exc:
        st.warning(connection_error(exc))
field_key = f"endo:{patient_key}:{endoscopy_number}:"

if st.session_state.get("reteg_missing_fields"):
    highlight_missing_fields(st.session_state["reteg_missing_fields"])

with st.container(border=True):
    st.subheader("ENDOSCOPIA INICIAL" if endoscopy_number == 1 else f"{endoscopy_number}ª ENDOSCOPIA" if endoscopy_number else "Endoscopia actual")
    c1, c2 = st.columns(2)
    with c1:
        if endoscopy_number != 1:
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
    pending_date = False
    if therapy == "FIN TRATAMIENTO":
        st.info("Fin del tratamiento: no se programa un siguiente recambio.")
    else:
        pending_date = st.checkbox("PENDIENTE DE FECHA", key=field_key + "pending_date",
                                   help="El tratamiento continúa, pero todavía no hay fecha para la próxima endoscopia.")
        if not pending_date:
            review = st.date_input("Fecha estimada siguiente recambio", value=None,
                                   format="DD/MM/YYYY", key=field_key + "review")
        else:
            st.caption("La próxima endoscopia seguirá en PENDIENTES sin fecha asignada.")

    observations = st.text_area("OBSERVACIONES", key=field_key + "observations")
    finished = "SI" if therapy == "FIN TRATAMIENTO" else st.radio(
        "FIN DE TRATAMIENTO", ["NO", "SI"], horizontal=True, key=field_key + "finished"
    )
    change_therapy = "NO"
    if endoscopy_number != 1 and finished == "NO":
        change_therapy = st.radio("CAMBIO DE TERAPIA", ["NO", "SI"], horizontal=True, key=field_key + "change")
    if finished == "SI":
        review = None


save_clicked = st.button("GUARDAR ENDOSCOPIA", type="primary",
                         disabled=endoscopy_number is None)
if save_clicked:
    error = validate_record(patient_id, surgery, surgery_date, endoscopy_date,
                            digestivo, place, therapy, review if finished == "NO" else None,
                            pending_date=pending_date and finished == "NO", finished=finished,
                            observations=observations)
    if error:
        st.session_state["reteg_missing_fields"] = missing_field_labels(
            patient_id, surgery, surgery_date, endoscopy_date, digestivo, place,
            therapy, review if finished == "NO" else None,
            pending_date=pending_date and finished == "NO", finished=finished,
            observations=observations)
        st.rerun()
    else:
        st.session_state.pop("reteg_missing_fields", None)
        try:
            ws = endoscopy_sheet(create=True)
            rows = ws.get_all_values()[1:]
            record = [
                patient_key, surgery, surgery_date.strftime("%d/%m/%Y"),
                str(endoscopy_number), endoscopy_date.strftime("%d/%m/%Y"),
                digestivo, place, therapy,
                review.strftime("%d/%m/%Y") if review else ("PENDIENTE DE FECHA" if pending_date and finished == "NO" else ""),
                observations, change_therapy, finished
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
                # Close the saved endoscopy. Never open the next blank form automatically.
                for key in list(st.session_state):
                    if key.startswith("endo:"):
                        del st.session_state[key]
                st.session_state["case_page"] = True
                st.session_state["endoscopy_form_open"] = False
                st.session_state.current_endoscopy = None
                if finished == "NO":
                    st.session_state.endoscopy_notice = (
                        f"Endoscopia {endoscopy_number} guardada. "
                        "Pulsa CARGAR SIGUIENTE ENDOSCOPIA cuando quieras continuar."
                    )
                else:
                    st.session_state.endoscopy_notice = (
                        f"Endoscopia {endoscopy_number} guardada. Tratamiento finalizado."
                    )
                st.rerun()
        except ValueError as exc:
            st.error(str(exc))
        except Exception:
            st.error("No se ha podido confirmar el guardado. Se conserva la endoscopia actual; comprueba la conexión antes de reintentar.")

