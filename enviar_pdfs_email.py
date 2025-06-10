import os
import smtplib
import mimetypes
from pathlib import Path
from email.message import EmailMessage
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor

# === Cargar configuración ===
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
EMAIL_REMITENTE = os.getenv("EMAIL_REMITENTE")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
RUTA_SALIDA = "./pdfs_clientes"


# === Conexión BD ===
def conectar_bd():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


# Buscar cliente por número de teléfono
def obtener_datos_cliente_por_telefono(telefono):
    conn = conectar_bd()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT nombre, correo, telefono
        FROM Cliente
        WHERE REPLACE(telefono, ' ', '') = %s
    """,
        (telefono.replace(" ", ""),),
    )
    resultado = cur.fetchone()
    conn.close()
    return resultado


# Generador de mensaje según tipo
def mensaje_para(tipo, nombre_cliente):
    if tipo == "PRODUCTOS":
        return f"Hola {nombre_cliente},\n\nTe enviamos los productos que despertaron tu interés hoy.\n¡Gracias por tu preferencia!"
    elif tipo == "CATEGORIAS":
        return f"Hola {nombre_cliente},\n\nAquí tienes productos de las categorías que estuviste revisando.\n¡Esperamos que alguno sea de tu agrado!"
    elif tipo == "PROMOS":
        return f"Hola {nombre_cliente},\n\nTe compartimos promociones relacionadas a tus intereses recientes.\n¡Aprovecha antes que finalicen!"
    else:
        return (
            f"Hola {nombre_cliente},\n\nAdjunto encontrarás la información solicitada."
        )


# Enviar correo con adjunto
def enviar_correo(destinatario, asunto, cuerpo, archivo_adjunto):
    msg = EmailMessage()
    msg["From"] = EMAIL_REMITENTE
    msg["To"] = destinatario
    msg["Subject"] = asunto
    msg.set_content(cuerpo)

    with open(archivo_adjunto, "rb") as f:
        nombre_archivo = os.path.basename(archivo_adjunto)
        maintype, subtype = mimetypes.guess_type(nombre_archivo)[0].split("/")
        msg.add_attachment(
            f.read(), maintype=maintype, subtype=subtype, filename=nombre_archivo
        )

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_REMITENTE, EMAIL_PASSWORD)
            server.send_message(msg)
            print(f"✅ Enviado a {destinatario}: {nombre_archivo}")
    except Exception as e:
        print(f"❌ Error al enviar a {destinatario}: {e}")


# Actualizar estado e insertar registro de envío
def actualizar_estado_envio(chat_id, tipo):
    queries = {
        "PRODUCTOS": {
            "update": """
                UPDATE InteresProductoChat SET estado = 'enviado'
                WHERE chat_id = %s AND fecha_registro::date = CURRENT_DATE;
            """,
            "insert": """
                INSERT INTO EnvioInteresProductoChat (interes_producto_chat_id, medio, observacion)
                SELECT id, 'email', 'Enviado automáticamente por script'
                FROM InteresProductoChat
                WHERE chat_id = %s AND fecha_registro::date = CURRENT_DATE;
            """,
        },
        "CATEGORIAS": {
            "update": """
                UPDATE InteresCategoriaChat SET estado = 'enviado'
                WHERE chat_id = %s AND fecha_registro::date = CURRENT_DATE;
            """,
            "insert": """
                INSERT INTO EnvioInteresCategoriaChat (interes_categoria_chat_id, medio, observacion)
                SELECT id, 'email', 'Enviado automáticamente por script'
                FROM InteresCategoriaChat
                WHERE chat_id = %s AND fecha_registro::date = CURRENT_DATE;
            """,
        },
        "PROMOS": {
            "update": """
                UPDATE InteresPromocionChat SET estado = 'enviado'
                WHERE chat_id = %s AND fecha_registro::date = CURRENT_DATE;
            """,
            "insert": """
                INSERT INTO EnvioInteresPromocionChat (interes_promocion_chat_id, medio, observacion)
                SELECT id, 'email', 'Enviado automáticamente por script'
                FROM InteresPromocionChat
                WHERE chat_id = %s AND fecha_registro::date = CURRENT_DATE;
            """,
        },
    }

    if tipo not in queries:
        print(f"⚠️ Tipo desconocido para actualización: {tipo}")
        return

    conn = conectar_bd()
    cur = conn.cursor()
    try:
        cur.execute(queries[tipo]["update"], (chat_id,))
        cur.execute(queries[tipo]["insert"], (chat_id,))
        conn.commit()
        print(
            f"🔄 Estado actualizado e inserción registrada para chat_id {chat_id} ({tipo})"
        )
    except Exception as e:
        print(f"❌ Error al actualizar estado de {tipo}: {e}")
        conn.rollback()
    finally:
        conn.close()


def ya_se_envio(chat_id, tipo):
    query_map = {
        "PRODUCTOS": "SELECT 1 FROM EnvioInteresProductoChat ep JOIN InteresProductoChat ip ON ep.interes_producto_chat_id = ip.id WHERE ip.chat_id = %s AND ip.fecha_registro::date = CURRENT_DATE;",
        "CATEGORIAS": "SELECT 1 FROM EnvioInteresCategoriaChat ec JOIN InteresCategoriaChat ic ON ec.interes_categoria_chat_id = ic.id WHERE ic.chat_id = %s AND ic.fecha_registro::date = CURRENT_DATE;",
        "PROMOS": "SELECT 1 FROM EnvioInteresPromocionChat ep JOIN InteresPromocionChat ip ON ep.interes_promocion_chat_id = ip.id WHERE ip.chat_id = %s AND ip.fecha_registro::date = CURRENT_DATE;",
    }

    if tipo not in query_map:
        print(f"⚠️ Tipo inválido para verificación: {tipo}")
        return True  # por seguridad asumimos que ya fue enviado

    conn = conectar_bd()
    cur = conn.cursor()
    try:
        cur.execute(query_map[tipo], (chat_id,))
        resultado = cur.fetchone()
        return resultado is not None
    except Exception as e:
        print(
            f"❌ Error al verificar si ya se envió {tipo} para chat_id {chat_id}: {e}"
        )
        return True
    finally:
        conn.close()

    # Proceso principal
    # def main():
    for archivo in Path(RUTA_SALIDA).glob("*.pdf"):
        partes = archivo.name.split("_")
        if len(partes) < 5:
            print(f"⚠️ Nombre de archivo inválido: {archivo.name}")
            continue

        tipo = partes[0]

        try:
            telefono = partes[-3].replace(" ", "")
            chat_id = int(partes[-2])
        except (IndexError, ValueError):
            print(f"⚠️ No se pudo extraer teléfono o chat_id de: {archivo.name}")
            continue

        cliente = obtener_datos_cliente_por_telefono(telefono)
        if not cliente:
            print(f"⚠️ Cliente no encontrado para teléfono: {telefono}")
            continue

        asunto = f"{tipo.capitalize()} de tu interés - PDF adjunto"
        cuerpo = mensaje_para(tipo, cliente["nombre"])
        enviar_correo(cliente["correo"], asunto, cuerpo, archivo)
        actualizar_estado_envio(chat_id, tipo)


def main():
    for archivo in Path(RUTA_SALIDA).glob("*.pdf"):
        partes = archivo.name.split("_")
        if len(partes) < 7:
            print(f"⚠️ Nombre de archivo inválido: {archivo.name}")
            continue

        tipo = partes[0]

        try:
            telefono = partes[-5].replace(" ", "")
            chat_id = int(partes[-4])
        except (IndexError, ValueError):
            print(f"⚠️ No se pudo extraer teléfono o chat_id de: {archivo.name}")
            continue

        if ya_se_envio(chat_id, tipo):
            print(f"⏭️ Ya se envió {tipo} para chat_id {chat_id}, se omite.")
            continue

        cliente = obtener_datos_cliente_por_telefono(telefono)
        if not cliente:
            print(f"⚠️ Cliente no encontrado para teléfono: {telefono}")
            continue

        asunto = f"{tipo.capitalize()} de tu interés - PDF adjunto"
        cuerpo = mensaje_para(tipo, cliente["nombre"])
        enviar_correo(cliente["correo"], asunto, cuerpo, archivo)
        actualizar_estado_envio(chat_id, tipo)


if __name__ == "__main__":
    main()
