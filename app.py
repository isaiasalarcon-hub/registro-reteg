import streamlit as st
import gspread
import io
import re
import uuid
import time
import hmac
import hashlib
import secrets
import requests
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
from googleapiclient.errors import HttpError
from google.oauth2.service_account import Credentials
from google.oauth2.credentials import Credentials as UserCredentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow
from datetime import date, datetime
from google.auth.exceptions import RefreshError
from streamlit.errors import StreamlitSecretNotFoundError

DRIVE_ROOT_FOLDER = "16ICuMpO49ULwZfyZBNHTWt754A-y0hwd"
MEDIA_HEADERS = ["ID", "N.º endoscopia", "Archivo", "Tipo", "Drive ID", "Fecha subida"]


OAUTH_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]
RETEG_OWNER_EMAIL = "isaiasalarcon@gmail.com"
RETEG_REDIRECT_URI = "https://registro-reteg-88lhepzn444rsz5vkystm9.streamlit.app/"


def oauth_config():
    config = st.secrets["reteg_oauth"]
    return {
        "web": {
            "client_id": config["client_id"],
            "client_secret": config["client_secret"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [RETEG_REDIRECT_URI],
        }
    }


def oauth_state():
    # Signed, short-lived state. It also transports the PKCE verifier across
    # Streamlit browser sessions after the Google redirect.
    timestamp = str(int(time.time()))
    verifier = secrets.token_urlsafe(48)
    payload = f"{timestamp}.{verifier}"
    secret = str(st.secrets["reteg_oauth"]["client_secret"]).encode()
    signature = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def valid_oauth_state(value):
    try:
        timestamp, nonce, signature = value.split(".", 2)
        if abs(time.time() - int(timestamp)) > 900 or not nonce:
            return False
        secret = str(st.secrets["reteg_oauth"]["client_secret"]).encode()
        expected = hmac.new(secret, f"{timestamp}.{nonce}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)
    except (ValueError, KeyError, AttributeError, TypeError):
        return False


def oauth_flow(state=None):
    flow = Flow.from_client_config(oauth_config(), scopes=OAUTH_SCOPES, state=state)
    flow.redirect_uri = RETEG_REDIRECT_URI
    return flow


@st.cache_data(ttl=180, max_entries=20, show_spinner=False)
def exchange_google_code_once(code, client_id, client_secret, redirect_uri, code_verifier):
    """Deduplicate token exchange across Streamlit reruns and sessions."""
    response = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        },
        timeout=25,
    )
    payload = response.json()
    if not response.ok:
        return {"ok": False, "error": payload.get("error", "unknown"),
                "description": str(payload.get("error_description", ""))}
    return {"ok": True, "tokens": payload}


def authorize_drive_callback():
    # The OAuth redirect may open a new Streamlit session; validate a signed,
    # short-lived state rather than relying on session_state across redirects.
    code = st.query_params.get("code")
    if not code:
        return
    state = st.query_params.get("state", "")
    # Do not clear query parameters before exchanging the single-use code:
    # changing query params can trigger a Streamlit rerun and consume it twice.
    if st.session_state.get("oauth_processing_code") == code:
        return
    st.session_state["oauth_processing_code"] = code
    if not valid_oauth_state(state):
        st.query_params.clear()
        st.session_state["drive_auth_error"] = "Autorización caducada o no válida. Inténtalo de nuevo."
        return
    try:
        # Exchange the code directly: Google can return a different scope order
        # or extra identity scopes, which oauthlib otherwise rejects.
        config = st.secrets["reteg_oauth"]
        result = exchange_google_code_once(
            code, config["client_id"], config["client_secret"], RETEG_REDIRECT_URI,
            state.split(".", 2)[1]
        )
        if not result["ok"]:
            reason = result["error"]
            description = result["description"]
            st.session_state["drive_auth_error"] = (
                f"Google rechazó la autorización ({reason}). "
                f"Detalle: {description[:120] if description else 'sin detalle'}. "
                "Si persiste, revisaremos el retorno de Google sin cambiar credenciales."
            )
            return
        tokens = result["tokens"]
        creds = UserCredentials(
            token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=config["client_id"],
            client_secret=config["client_secret"],
            scopes=OAUTH_SCOPES,
        )
        identity = build("oauth2", "v2", credentials=creds, cache_discovery=False)
        email = identity.userinfo().get().execute().get("email", "").lower()
        if email != RETEG_OWNER_EMAIL:
            st.session_state["drive_auth_error"] = "Debes autorizar con la cuenta propietaria del Drive de RETEG."
            return
        st.session_state["drive_oauth_credentials"] = creds.to_json()
        st.session_state.pop("drive_auth_error", None)
    except requests.RequestException:
        st.session_state["drive_auth_error"] = "No se pudo conectar con Google para validar la autorización."
    except Exception as exc:
        st.session_state["drive_auth_error"] = (
            f"Error al completar la autorización ({type(exc).__name__}). "
            "Vuelve a intentarlo."
        )
    finally:
        # Clear the one-use authorization code only after the exchange completes.
        st.query_params.clear()


def drive_credentials():
    raw = st.session_state.get("drive_oauth_credentials")
    if not raw:
        return None
    import json
    creds = UserCredentials.from_authorized_user_info(json.loads(raw), scopes=OAUTH_SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                st.session_state["drive_oauth_credentials"] = creds.to_json()
            except Exception:
                st.session_state.pop("drive_oauth_credentials", None)
                return None
        else:
            return None
    return creds


def drive_service():
    creds = drive_credentials()
    if creds is None:
        raise PermissionError("Es necesario autorizar Google Drive.")
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def drive_authorization_ui():
    if error := st.session_state.pop("drive_auth_error", None):
        st.error(error)
    if drive_credentials() is not None:
        return True
    try:
        # Explicit PKCE challenge paired with the verifier carried in signed state.
        import base64
        state = oauth_state()
        verifier = state.split(".", 2)[1]
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).decode("ascii").rstrip("=")
        flow = oauth_flow()
        url, _ = flow.authorization_url(
            access_type="offline", prompt="consent", state=state,
            include_granted_scopes="false",
            code_challenge=challenge, code_challenge_method="S256",
        )
        st.info("Autoriza tu cuenta de Google para ver y subir fotos y vídeos a RETEG.")
        st.link_button("AUTORIZAR GOOGLE DRIVE", url)
    except (KeyError, StreamlitSecretNotFoundError):
        st.warning("Falta configurar [reteg_oauth] en los Secrets de Streamlit.")
    return False


def media_sheet():
    book = spreadsheet()
    try:
        ws = book.worksheet("ARCHIVOS")
    except gspread.WorksheetNotFound:
        ws = book.add_worksheet(title="ARCHIVOS", rows=1000, cols=len(MEDIA_HEADERS))
    if not ws.row_values(1):
        ws.update("A1:F1", [MEDIA_HEADERS])
    elif ws.row_values(1) != MEDIA_HEADERS:
        raise ValueError("La estructura de ARCHIVOS no coincide.")
    return ws


def find_or_create_folder(service, parent, name):
    safe_name = name.replace("'", "\\'")
    query = (
        f"'{parent}' in parents and name = '{safe_name}' and "
        "mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    )
    found = service.files().list(q=query, fields="files(id,name)", pageSize=10,
                                 supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
    if found.get("files"):
        return found["files"][0]["id"]
    created = service.files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.folder",
              "parents": [parent]},
        fields="id", supportsAllDrives=True
    ).execute()
    return created["id"]


@st.cache_data(ttl=300, show_spinner=False, max_entries=24)
def drive_file_bytes(file_id):
    service = drive_service()
    stream = io.BytesIO()
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    downloader = MediaIoBaseDownload(stream, request, chunksize=1024 * 1024)
    done = False
    while not done:
        _, done = downloader.next_chunk()
        if stream.tell() > 150 * 1024 * 1024:
            raise ValueError("Archivo demasiado grande para visualizar.")
    return stream.getvalue()


def media_section(patient, number):
    st.markdown("**FOTOS Y VÍDEOS**")
    if not st.secrets.get("reteg_media_enabled", True):
        st.info("Subida de archivos pendiente de habilitar: requiere permisos de Drive y acceso restringido a la aplicación.")
        return
    if not drive_authorization_ui():
        return
    try:
        ws = media_sheet()
        existing = [
            row for row in ws.get_all_records(numericise_ignore=["all"])
            if str(row.get("ID", "")).strip() == str(patient)
            and str(row.get("N.º endoscopia", "")).strip() == str(number)
        ]
        if existing:
            st.success(f"Archivos guardados en Drive: {len(existing)}")
        else:
            st.info("Todavía no hay fotografías ni vídeos guardados para esta endoscopia.")
        for row in existing:
            file_id = str(row.get("Drive ID", "")).strip()
            if not file_id:
                continue
            filename = str(row.get("Archivo", "Adjunto"))
            mime = str(row.get("Tipo", "")).lower()
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            is_video = mime.startswith("video/") or ext in {"mp4", "mov", "m4v", "webm"}
            is_image = mime.startswith("image/") or ext in {"jpg", "jpeg", "png", "webp", "heic"}
            with st.container(border=True):
                st.caption(filename)
                # Videos load on demand to avoid downloading large files on every rerun.
                show = True
                if is_video:
                    show = st.toggle("Reproducir vídeo", key=f"play:{patient}:{number}:{file_id}")
                if show and (is_image or is_video):
                    try:
                        with st.spinner("Cargando archivo desde Drive..."):
                            data = drive_file_bytes(file_id)
                        if is_video:
                            st.video(data, format=mime if mime.startswith("video/") else "video/mp4")
                        elif ext == "heic":
                            st.info("Este formato de imagen puede no ser compatible con el visor.")
                        else:
                            st.image(data, caption=filename, use_container_width=True)
                    except Exception:
                        st.warning("No se ha podido mostrar este archivo. Comprueba los permisos de Drive.")
                st.link_button(
                    "Abrir archivo en Drive",
                    f"https://drive.google.com/file/d/{file_id}/view",
                )
    except Exception:
        st.warning("No se ha podido consultar el listado de archivos adjuntos.")
    if notice := st.session_state.pop(f"upload_notice:{patient}:{number}", None):
        st.success(notice)
    uploads = st.file_uploader(
        "Seleccionar fotos o vídeos del móvil",
        type=["jpg", "jpeg", "png", "webp", "heic", "mp4", "mov", "m4v", "webm"],
        accept_multiple_files=True, key=f"upload:{patient}:{number}",
    )
    if uploads:
        st.caption(f"{len(uploads)} archivo(s) seleccionado(s). Pulsa SUBIR A DRIVE para guardarlos.")
    if uploads and st.button("SUBIR A DRIVE", key=f"upload_button:{patient}:{number}", type="primary"):
        stage = "conexión con Drive"
        try:
            service = drive_service()
            stage = "creación de carpeta del paciente"
            patient_folder = find_or_create_folder(service, DRIVE_ROOT_FOLDER, f"ID_{patient}")
            stage = "creación de carpeta de endoscopia"
            folder_name = "ENDOSCOPIA INICIAL" if int(number) == 1 else f"ENDOSCOPIA {number}"
            endo_folder = find_or_create_folder(service, patient_folder, folder_name)
            stage = "acceso a hoja ARCHIVOS"
            ws = media_sheet()
            saved = 0
            for upload in uploads:
                if upload.size > 150 * 1024 * 1024:
                    st.error("Un archivo supera 150 MB. Súbelo comprimido.")
                    continue
                ext = upload.name.rsplit(".", 1)[-1].lower()
                if ext not in {"jpg", "jpeg", "png", "webp", "heic", "mp4", "mov", "m4v", "webm"}:
                    st.error("Tipo de archivo no admitido.")
                    continue
                stage = "preparación del archivo"
                media = MediaIoBaseUpload(io.BytesIO(upload.getvalue()),
                                          mimetype=upload.type or "application/octet-stream",
                                          resumable=True)
                safe_file = f"{uuid.uuid4().hex}.{ext}"
                stage = "envío a Google Drive"
                result = service.files().create(
                    body={"name": safe_file, "parents": [endo_folder]},
                    media_body=media, fields="id", supportsAllDrives=True
                ).execute()
                stage = "registro en Google Sheets"
                ws.append_row([str(patient), str(number), upload.name,
                               upload.type or "", result["id"],
                               datetime.now().strftime("%d/%m/%Y %H:%M")],
                              value_input_option="RAW")
                saved += 1
            if saved:
                st.session_state[f"upload_notice:{patient}:{number}"] = f"{saved} archivo(s) guardado(s) en Drive."
                st.rerun()
        except HttpError as exc:
            status = getattr(exc.resp, "status", None)
            reason = ""
            try:
                import json
                details = json.loads(exc.content.decode("utf-8"))
                reason = details.get("error", {}).get("errors", [{}])[0].get("reason", "")
            except (ValueError, UnicodeDecodeError, IndexError, AttributeError, TypeError):
                pass
            if status == 403 and reason in ("storageQuotaExceeded", "quotaExceeded"):
                st.error("Google Drive ha rechazado la subida por falta de cuota de almacenamiento de la cuenta de servicio. Las cuentas de servicio no disponen de almacenamiento personal. Se necesita una unidad compartida compatible o un método de subida autorizado con la cuenta propietaria.")
            elif status == 403 and reason in ("insufficientPermissions", "forbidden"):
                st.error("Google Drive deniega esta operación (403). Comprueba que la API de Drive esté habilitada y que la cuenta de servicio pueda crear archivos en la ubicación.")
            elif status == 404:
                st.error("Google Drive no encuentra la carpeta de destino (404). Comprueba su ID y acceso.")
            else:
                st.error(f"Google Drive ha rechazado la operación (HTTP {status or 'desconocido'}, motivo: {reason or 'no especificado'}).")
        except Exception as exc:
            if isinstance(exc, (KeyError, StreamlitSecretNotFoundError, RefreshError)):
                st.error("Error de configuración o autenticación de la cuenta de servicio.")
            else:
                st.error(f"Error durante {stage} ({type(exc).__name__}). Revisa los registros de Streamlit para más detalles.")
                if isinstance(exc, TypeError):
                    st.code(str(exc)[:400])


st.set_page_config(page_title="REGISTRO RETEG", layout="wide")
authorize_drive_callback()
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
                    place, therapy, review, pending_date=False):
    if not patient_id.strip() or not (surgery or "").strip() or not surgery_date or not endoscopy_date:
        return "Completa el ID, la cirugía y ambas fechas."
    if not digestivo or not place or not therapy:
        return "Selecciona digestivo, lugar y tratamiento."
    if endoscopy_date < surgery_date:
        return "La fecha de endoscopia no puede ser anterior a la cirugía."
    if therapy != "FIN TRATAMIENTO":
        if review is None and not pending_date:
            return "Indica la fecha estimada del siguiente recambio o marca PENDIENTE DE FECHA."
        if review is not None and review < endoscopy_date:
            return "La fecha del siguiente recambio no puede ser anterior a la endoscopia."
    return None


def is_finished(record):
    value = str(record.get("Fin de tratamiento", "")).strip().upper()
    if value in ("SI", "SÍ"):
        return True
    if value == "NO":
        return False
    return str(record.get("Tratamiento", "")).strip().upper() == "FIN TRATAMIENTO"


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
    options = list(range(len(history)))
    chosen = st.selectbox(
        "Seleccionar endoscopia",
        options,
        format_func=lambda i: (
            f"{'ENDOSCOPIA INICIAL' if int(history[i]['N.º endoscopia']) == 1 else str(history[i]['N.º endoscopia']) + 'ª ENDOSCOPIA'} · "
            f"{history[i].get('Fecha endoscopia', '')} · "
            f"{history[i].get('Tratamiento', '')}"
        ),
        key=f"history:{patient_id}",
    )
    record = history[chosen]
    with st.container(border=True):
        for field in HEADERS[4:]:
            st.write(f"**{field}:** {record.get(field, '') or '—'}")
    media_section(patient_id, record["N.º endoscopia"])


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
        st.session_state["case_source"] = "Total"

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
    st.session_state["case_page"] = True

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

if st.session_state.get("case_page") and st.session_state.get("case_source") == "Total":
    try:
        history_ws = endoscopy_sheet()
        show_history(history_ws.get_all_records(numericise_ignore=["all"]) if history_ws else [],
                     st.session_state["patient:id"])
    except Exception as exc:
        st.error(connection_error(exc))

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

    if patient_key and endoscopy_number:
        media_section(patient_key, endoscopy_number)
    else:
        st.caption("Introduce primero el ID del paciente para adjuntar fotos o vídeos.")

    next_clicked = st.button(
        "SIGUIENTE ENDOSCOPIA",
        disabled=endoscopy_number is None or finished == "SI",
        help="Guarda la endoscopia actual y abre la siguiente conservando los datos del paciente.",
    )

save_clicked = st.button("Guardar endoscopia", type="primary",
                         disabled=endoscopy_number is None)
if save_clicked or next_clicked:
    error = validate_record(patient_id, surgery, surgery_date, endoscopy_date,
                            digestivo, place, therapy, review if finished == "NO" else endoscopy_date,
                            pending_date=pending_date and finished == "NO")
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
                if next_clicked:
                    updated_rows = ws.get_all_values()[1:]
                    st.session_state.current_endoscopy = next_endoscopy_number(updated_rows, patient_key)
                    st.session_state.endoscopy_notice = (
                        f"Endoscopia {endoscopy_number} guardada. Puedes rellenar la siguiente."
                    )
                    st.rerun()
                else:
                    # Keep patient and surgery details; advance to the next endoscopy.
                    for key in list(st.session_state):
                        if key.startswith("endo:"):
                            del st.session_state[key]
                    updated_rows = ws.get_all_values()[1:]
                    st.session_state.current_endoscopy = next_endoscopy_number(updated_rows, patient_key)
                    st.session_state["case_page"] = True
                    st.session_state.endoscopy_notice = (
                        f"Endoscopia {endoscopy_number} guardada. SIGUIENTE ENDOSCOPIA: "
                        f"{st.session_state.current_endoscopy}."
                    )
                    st.rerun()
        except ValueError as exc:
            st.error(str(exc))
        except Exception:
            st.error("No se ha podido confirmar el guardado. Se conserva la endoscopia actual; comprueba la conexión antes de reintentar.")

