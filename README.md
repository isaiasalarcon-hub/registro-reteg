# REGISTRO RETEG

Aplicación Streamlit para el registro inicial de tratamiento endoscópico de dehiscencias posquirúrgicas.

## Despliegue

1. Crear una app en Streamlit Community Cloud vinculada a este repositorio, rama `main`, archivo `app.py`.
2. Configurar en **Advanced settings → Secrets** las claves `spreadsheet_id` y `gcp_service_account`.
3. Compartir la hoja de Google Sheets con el correo de la cuenta de servicio como **Editor**.
4. La hoja debe contener la pestaña `REGISTRO INICIAL` y las siete columnas acordadas.

Ejemplo de estructura de secretos (sin credenciales reales):

```toml
spreadsheet_id = "ID_DE_LA_HOJA"

[gcp_service_account]
type = "service_account"
project_id = "PROYECTO"
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----\\n"
client_email = "cuenta@proyecto.iam.gserviceaccount.com"
client_id = "..."
token_uri = "https://oauth2.googleapis.com/token"
```

**Privacidad:** no publicar datos de pacientes, claves, ni archivos de secretos en GitHub. Para uso clínico real, revisar autorización institucional, control de acceso y requisitos de protección de datos. La versión inicial no incorpora autenticación de usuarios ni auditoría.
