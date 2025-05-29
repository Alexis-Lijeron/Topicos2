import os
import re
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, Response, request
from twilio.twiml.messaging_response import MessagingResponse
from openai import OpenAI
from dotenv import load_dotenv
import chromadb
from chromadb.config import Settings
from chromadb.utils import embedding_functions
from chromadb import PersistentClient

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
app = Flask(__name__)
conversaciones = {}

# === CONFIGURACIÓN DE CHROMADB ===
embed_fn = embedding_functions.OpenAIEmbeddingFunction(
    api_key=os.getenv("OPENAI_API_KEY"), model_name="text-embedding-3-small"
)
chroma = PersistentClient(path="chroma_db")
collection = chroma.get_or_create_collection(
    name="productos_marketing", embedding_function=embed_fn
)

# === SINÓNIMOS ===
SINONIMOS = {
    "libretas": "cuaderno",
    "libreta": "cuaderno",
    "adhesivos": "stickers",
    "pegatinas": "stickers",
    "adhesivo": "stickers",
    "lapiceros": "lápices de colores",
    "lapicero": "lápices de colores",
    "respuesto de hojas": "hojas",
    "respuestos": "hojas",
    "respuesto": "hojas",
    "hojitas": "hojas",
    "marcador": "marcadores",
    "marcadores stabilo": "bolígrafos",
    "lapiceras": "bolígrafos",
    "lapicera": "bolígrafos",
    "corrector": "correctores",
    "goma": "correctores",
    "estuche": "estucheras",
    "cartuchera": "estucheras",
    "porta lápiz": "estucheras",
    "porta lapiz": "estucheras",
}


def conectar_bd():
    return psycopg2.connect(os.getenv("DATABASE_URL"), cursor_factory=RealDictCursor)


def reemplazar_sinonimos(texto):
    texto = texto.lower()
    for sin, real in SINONIMOS.items():
        texto = re.sub(rf"\\b{re.escape(sin)}\\b", real, texto)
    return texto


def obtener_chat_id(telefono):
    conn = conectar_bd()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT c.id AS chat_id, cl.nombre
        FROM Chat c
        JOIN Cliente cl ON c.cliente_id = cl.id
        WHERE cl.telefono = %s AND c.estado = 'abierto'
        ORDER BY c.fecha_inicio DESC LIMIT 1;
        """,
        (telefono,),
    )
    chat = cur.fetchone()

    if chat:
        conn.close()
        return chat["chat_id"], chat["nombre"]

    cur.execute("SELECT id, nombre FROM Cliente WHERE telefono = %s;", (telefono,))
    cliente = cur.fetchone()
    if not cliente:
        cur.execute(
            """
            INSERT INTO Cliente (nombre, telefono) VALUES (%s, %s) RETURNING id;
            """,
            ("Invitado", telefono),
        )
        cliente_id = cur.fetchone()["id"]
        nombre_cliente = "Invitado"
    else:
        cliente_id = cliente["id"]
        nombre_cliente = cliente["nombre"]

    cur.execute(
        "INSERT INTO Chat (cliente_id) VALUES (%s) RETURNING id;", (cliente_id,)
    )
    chat_id = cur.fetchone()["id"]
    conn.commit()
    conn.close()
    return chat_id, nombre_cliente


def guardar_mensaje(chat_id, contenido, emisor="cliente", tipo="texto"):
    conn = conectar_bd()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO Mensaje (chat_id, emisor, tipo, contenido)
        VALUES (%s, %s, %s, %s);
        """,
        (chat_id, emisor, tipo, contenido),
    )
    conn.commit()
    conn.close()


def ya_saludo_hoy(chat_id):
    conn = conectar_bd()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT contenido, fecha_envio::date
        FROM Mensaje
        WHERE chat_id = %s AND emisor = 'sistema'
        ORDER BY fecha_envio DESC LIMIT 1;
        """,
        (chat_id,),
    )
    mensaje = cur.fetchone()
    conn.close()
    if not mensaje:
        return False
    return (
        mensaje["fecha_envio"] == datetime.now().date()
        and "hola" in mensaje["contenido"].lower()
    )


def buscar_productos_embedding(pregunta):
    resultados = collection.query(query_texts=[pregunta], n_results=3)
    docs = resultados.get("documents", [[]])[0]
    return docs


def detectar_pregunta_general(texto):
    texto = texto.lower()
    if re.search(r"\b(productos|tienen|hay)\b", texto) and re.search(
        r"\b(stickers?|cuadernos?|marcadores?|hojas|micropen|bolígrafos?|estucheras?|correctores?)\b",
        texto,
    ):
        for sin, real in SINONIMOS.items():
            texto = texto.replace(sin, real)
        return "categoria"
    elif re.search(r"(qué|que)\s+(productos|tienen|hay)", texto):
        return "productos"
    elif "categoría" in texto or "categorias" in texto:
        return "categorias"
    elif "promoción" in texto or "promociones" in texto:
        return "promociones"
    return None

    # def responder_general(tipo):
    conn = conectar_bd()
    cur = conn.cursor()

    if tipo == "productos":
        cur.execute("SELECT nombre FROM Producto ORDER BY nombre;")
        productos = [p["nombre"] for p in cur.fetchall()]
        return "Actualmente contamos con los siguientes productos:\n- " + "\n- ".join(
            productos
        )

    elif tipo == "categorias":
        cur.execute(
            """
            SELECT c.nombre, COUNT(p.id) as cantidad
            FROM Categoria c
            LEFT JOIN Producto p ON p.categoria_id = c.id
            GROUP BY c.nombre ORDER BY c.nombre;
        """
        )
        categorias = cur.fetchall()
        return "Nuestras categorías disponibles son:\n" + "\n".join(
            [f"- {c['nombre']} ({c['cantidad']} productos)" for c in categorias]
        )

    elif tipo == "promociones":
        cur.execute(
            """
            SELECT nombre, porcentaje_descuento, fecha_fin
            FROM Promocion
            WHERE fecha_fin >= CURRENT_DATE
            ORDER BY fecha_fin;
        """
        )
        promos = cur.fetchall()
        if promos:
            return "Estas son nuestras promociones activas:\n" + "\n".join(
                [
                    f"- {p['nombre']} ({p['porcentaje_descuento']}% hasta {p['fecha_fin']})"
                    for p in promos
                ]
            )
        else:
            return "En este momento no contamos con promociones activas."

    conn.close()
    return "Lo siento, no encontré información."

    # def responder_general_con_ia(tipo):
    conn = conectar_bd()
    cur = conn.cursor()

    contexto = ""

    if tipo == "productos":
        cur.execute(
            """
            SELECT p.nombre, p.stock, COALESCE(pp.monto, 0) AS precio, c.nombre AS categoria
            FROM Producto p
            JOIN Categoria c ON p.categoria_id = c.id
            LEFT JOIN PrecioProducto pp ON pp.producto_id = p.id AND pp.lista_precio_id = 1
            ORDER BY c.nombre, p.nombre;
        """
        )
        productos = cur.fetchall()
        for p in productos:
            contexto += f"- {p['nombre']} (Categoría: {p['categoria']}, Precio: Bs. {p['precio']}, Stock: {p['stock']})\n"

    elif tipo == "categorias":
        cur.execute(
            """
            SELECT c.nombre, COUNT(p.id) as cantidad
            FROM Categoria c
            LEFT JOIN Producto p ON p.categoria_id = c.id
            GROUP BY c.nombre
            ORDER BY c.nombre;
        """
        )
        categorias = cur.fetchall()
        for c in categorias:
            contexto += f"- {c['nombre']}: {c['cantidad']} productos\n"

    elif tipo == "promociones":
        cur.execute(
            """
            SELECT pr.id, pr.nombre, pr.porcentaje_descuento, pr.fecha_fin
            FROM Promocion pr
            WHERE pr.fecha_fin >= CURRENT_DATE
            ORDER BY pr.fecha_fin;
        """
        )
        promociones = cur.fetchall()

        for promo in promociones:
            contexto += f"📣 {promo['nombre']} ({promo['porcentaje_descuento']}% hasta {promo['fecha_fin']}):\n"
            cur.execute(
                """
                SELECT p.nombre
                FROM ProductoPromocion pp
                JOIN Producto p ON p.id = pp.producto_id
                WHERE pp.promocion_id = %s;
            """,
                (promo["id"],),
            )
            productos = cur.fetchall()
            for p in productos:
                contexto += f"   - {p['nombre']}\n"

        if not promociones:
            contexto = "Actualmente no hay promociones activas."

    conn.close()

    prompt = f"""
Eres un asistente amigable de una tienda de papelería. Responde con tono cálido y profesional lo siguiente, basado en la información de abajo:

{contexto}

La respuesta debe ser clara, en español, con viñetas o listas si es necesario. No inventes nada fuera de lo mostrado.
    """.strip()

    respuesta = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {
                "role": "system",
                "content": "Eres un asistente conversacional experto en ventas de papelería.",
            },
            {"role": "user", "content": prompt},
        ],
    )
    return respuesta.choices[0].message.content.strip()

    # @app.route("/whatsapp", methods=["POST"])
    # def whatsapp():
    try:
        numero_completo = request.values.get("From", "")
        user_number = numero_completo.replace("whatsapp:", "")
        incoming_msg = request.values.get("Body", "").strip()

        resp = MessagingResponse()
        msg = resp.message()

        chat_id, nombre_cliente = obtener_chat_id(user_number)
        guardar_mensaje(chat_id, incoming_msg, emisor="cliente")

        if numero_completo not in conversaciones:
            conversaciones[numero_completo] = []
            if not ya_saludo_hoy(chat_id):
                saludo = f"¡Hola {nombre_cliente}! 😊 ¿En qué podemos ayudarte hoy?"
                guardar_mensaje(chat_id, saludo, emisor="sistema")
                msg.body(saludo)
                return Response(str(resp), content_type="application/xml")

        consulta = reemplazar_sinonimos(incoming_msg)
        contexto = buscar_productos_embedding(consulta)

        prompt = (
            "Responde como un vendedor de papelería basado en los siguientes productos encontrados:\n\n"
            + "\n---\n".join(contexto)
            + f"\n\nCliente: {incoming_msg}\nRespuesta:"
        )

        conversaciones[numero_completo].append(
            {"role": "user", "content": incoming_msg}
        )

        respuesta = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system",
                    "content": "Eres un asistente de ventas de productos de papelería.",
                },
                {"role": "user", "content": prompt},
            ],
        )

        reply = respuesta.choices[0].message.content.strip()
        if not reply:
            reply = "Lo siento, no entendí tu mensaje. ¿Podrías reformularlo?"

        guardar_mensaje(chat_id, reply, emisor="sistema")
        msg.body(reply)
        return Response(str(resp), content_type="application/xml")

    except Exception as e:
        print("❌ Error:", e)
        resp = MessagingResponse()
        msg = resp.message(
            "Ocurrió un error al procesar tu mensaje. Intenta nuevamente."
        )
        return Response(str(resp), content_type="application/xml")


def responder_general_con_ia(tipo, filtro_categoria=None):
    conn = conectar_bd()
    cur = conn.cursor()
    contexto = ""

    if tipo == "productos" and filtro_categoria:
        cur.execute(
            """
            SELECT p.nombre, p.stock, COALESCE(pp.monto, 0) AS precio
            FROM Producto p
            JOIN Categoria c ON p.categoria_id = c.id
            LEFT JOIN PrecioProducto pp ON pp.producto_id = p.id AND pp.lista_precio_id = 1
            WHERE LOWER(c.nombre) LIKE %s
            ORDER BY p.nombre;
        """,
            (f"%{filtro_categoria}%",),
        )
        productos = cur.fetchall()
        for p in productos:
            contexto += (
                f"- {p['nombre']} (Precio: Bs. {p['precio']}, Stock: {p['stock']})\n"
            )

    elif tipo == "productos":
        cur.execute(
            """
            SELECT p.nombre, p.stock, COALESCE(pp.monto, 0) AS precio, c.nombre AS categoria
            FROM Producto p
            JOIN Categoria c ON p.categoria_id = c.id
            LEFT JOIN PrecioProducto pp ON pp.producto_id = p.id AND pp.lista_precio_id = 1
            ORDER BY c.nombre, p.nombre;
        """
        )
        productos = cur.fetchall()
        for p in productos:
            contexto += f"- {p['nombre']} (Categoría: {p['categoria']}, Precio: Bs. {p['precio']}, Stock: {p['stock']})\n"

    elif tipo == "categorias":
        cur.execute(
            """
            SELECT c.nombre, COUNT(p.id) as cantidad
            FROM Categoria c
            LEFT JOIN Producto p ON p.categoria_id = c.id
            GROUP BY c.nombre
            ORDER BY c.nombre;
        """
        )
        categorias = cur.fetchall()
        for c in categorias:
            contexto += f"- {c['nombre']}: {c['cantidad']} productos\n"

    elif tipo == "promociones":
        cur.execute(
            """
            SELECT pr.id, pr.nombre, pr.porcentaje_descuento, pr.fecha_fin
            FROM Promocion pr
            WHERE pr.fecha_fin >= CURRENT_DATE
            ORDER BY pr.fecha_fin;
        """
        )
        promociones = cur.fetchall()

        for promo in promociones:
            contexto += f"📣 {promo['nombre']} ({promo['porcentaje_descuento']}% hasta {promo['fecha_fin']}):\n"
            cur.execute(
                """
                SELECT p.nombre
                FROM ProductoPromocion pp
                JOIN Producto p ON p.id = pp.producto_id
                WHERE pp.promocion_id = %s;
            """,
                (promo["id"],),
            )
            productos = cur.fetchall()
            for p in productos:
                contexto += f"   - {p['nombre']}\n"
        if not promociones:
            contexto = "Actualmente no hay promociones activas."

    conn.close()

    prompt = f"""
Eres un asistente amigable de una tienda de papelería. Responde con tono cálido y profesional lo siguiente, basado en la información de abajo:

{contexto}

La respuesta debe ser clara, en español, con viñetas o listas si es necesario. No inventes nada fuera de lo mostrado.
    """.strip()

    respuesta = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {
                "role": "system",
                "content": "Eres un asistente conversacional experto en ventas de papelería.",
            },
            {"role": "user", "content": prompt},
        ],
    )
    return respuesta.choices[0].message.content.strip()


#@app.route("/whatsapp", methods=["POST"])
#def whatsapp():
    try:
        numero_completo = request.values.get("From", "")
        user_number = numero_completo.replace("whatsapp:", "")
        incoming_msg = request.values.get("Body", "").strip()

        resp = MessagingResponse()
        msg = resp.message()

        chat_id, nombre_cliente = obtener_chat_id(user_number)
        guardar_mensaje(chat_id, incoming_msg, emisor="cliente")

        if numero_completo not in conversaciones:
            conversaciones[numero_completo] = []
            if not ya_saludo_hoy(chat_id):
                saludo = f"¡Hola {nombre_cliente}! 😊 ¿En qué podemos ayudarte hoy?"
                guardar_mensaje(chat_id, saludo, emisor="sistema")
                msg.body(saludo)
                return Response(str(resp), content_type="application/xml")

        consulta = reemplazar_sinonimos(incoming_msg)
        tipo_pregunta = detectar_pregunta_general(consulta)

        # Preguntas generales: productos, categorías o promociones
        if tipo_pregunta in ["productos", "categorias", "promociones"]:
            respuesta_texto = responder_general_con_ia(tipo_pregunta)
        else:
            # Preguntas específicas: usar ChromaDB
            contexto = buscar_productos_embedding(consulta)
            prompt = (
                "Responde como un vendedor de papelería basado en los siguientes productos encontrados:\n\n"
                + "\n---\n".join(contexto)
                + f"\n\nCliente: {incoming_msg}\nRespuesta:"
            )

            conversaciones[numero_completo].append(
                {"role": "user", "content": incoming_msg}
            )

            respuesta = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {
                        "role": "system",
                        "content": "Eres un asistente de ventas de productos de papelería.",
                    },
                    {"role": "user", "content": prompt},
                ],
            )

            respuesta_texto = respuesta.choices[0].message.content.strip()
            if not respuesta_texto:
                respuesta_texto = (
                    "Lo siento, no entendí tu mensaje. ¿Podrías reformularlo?"
                )

        guardar_mensaje(chat_id, respuesta_texto, emisor="sistema")
        msg.body(respuesta_texto)
        return Response(str(resp), content_type="application/xml")

    except Exception as e:
        print("❌ Error:", e)
        resp = MessagingResponse()
        msg = resp.message(
            "Ocurrió un error al procesar tu mensaje. Intenta nuevamente."
        )
        return Response(str(resp), content_type="application/xml")

@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    try:
        numero_completo = request.values.get("From", "")
        user_number = numero_completo.replace("whatsapp:", "")
        incoming_msg = request.values.get("Body", "").strip()

        resp = MessagingResponse()
        msg = resp.message()

        chat_id, nombre_cliente = obtener_chat_id(user_number)
        guardar_mensaje(chat_id, incoming_msg, emisor="cliente")

        if numero_completo not in conversaciones:
            conversaciones[numero_completo] = []
            if not ya_saludo_hoy(chat_id):
                saludo = f"¡Hola {nombre_cliente}! 😊 ¿En qué podemos ayudarte hoy?"
                guardar_mensaje(chat_id, saludo, emisor="sistema")
                msg.body(saludo)
                return Response(str(resp), content_type="application/xml")

        consulta = reemplazar_sinonimos(incoming_msg)
        intencion = detectar_pregunta_general(consulta)

        if intencion == "productos":
            reply = responder_general_con_ia("productos")
        elif intencion == "categorias":
            reply = responder_general_con_ia("categorias")
        elif intencion == "promociones":
            reply = responder_general_con_ia("promociones")
        elif intencion == "categoria":
            match = re.search(r"\bde\s+(\w+)|\ben\s+(\w+)", consulta)
            categoria = match.group(1) if match else consulta.split()[-1]
            reply = responder_general_con_ia("productos", filtro_categoria=categoria)
        else:
            contexto = buscar_productos_embedding(consulta)
            prompt = (
                "Responde como un vendedor de papelería basado en los siguientes productos encontrados:\n\n"
                + "\n---\n".join(contexto)
                + f"\n\nCliente: {incoming_msg}\nRespuesta:"
            )
            conversaciones[numero_completo].append({"role": "user", "content": incoming_msg})
            respuesta = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {
                        "role": "system",
                        "content": "Eres un asistente de ventas de productos de papelería.",
                    },
                    {"role": "user", "content": prompt},
                ],
            )
            reply = respuesta.choices[0].message.content.strip()
            if not reply:
                reply = "Lo siento, no entendí tu mensaje. ¿Podrías reformularlo?"

        guardar_mensaje(chat_id, reply, emisor="sistema")
        msg.body(reply)
        return Response(str(resp), content_type="application/xml")

    except Exception as e:
        print("❌ Error:", e)
        resp = MessagingResponse()
        msg = resp.message("Ocurrió un error al procesar tu mensaje. Intenta nuevamente.")
        return Response(str(resp), content_type="application/xml")

if __name__ == "__main__":
    app.run(debug=True)
