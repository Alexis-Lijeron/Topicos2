import os
import re
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, Response, request
from twilio.twiml.messaging_response import MessagingResponse  # type: ignore
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

with open("contexto.txt", "r", encoding="utf-8") as f:
    contexto_negocio = f.read()

app = Flask(__name__)
conversaciones = {}

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
        texto = re.sub(rf"\b{re.escape(sin)}\b", real, texto)
    return texto


# === GUARDAR Y VINCULAR CHAT / MENSAJE / INTERÉS ===
def obtener_chat_id(telefono):
    conn = conectar_bd()
    cur = conn.cursor()

    # Buscar cliente y chat abierto
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

    # Crear cliente si no existe
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

    # Crear nuevo chat
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

def reconstruir_historial(chat_id):
    conn = conectar_bd()
    cur = conn.cursor()
    cur.execute("""
        SELECT emisor, contenido
        FROM Mensaje
        WHERE chat_id = %s
        ORDER BY fecha_envio ASC;
    """, (chat_id,))
    mensajes = cur.fetchall()
    conn.close()

    historial = [{"role": "system", "content": contexto_negocio}]

    for m in mensajes:
        if m["emisor"] == "cliente":
            historial.append({"role": "user", "content": m["contenido"]})
        elif m["emisor"] in ("sistema", "agente"):
            historial.append({"role": "assistant", "content": m["contenido"]})

    return historial

def registrar_interes(chat_id, producto_id):
    conn = conectar_bd()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO InteresProductoChat (chat_id, producto_id)
        VALUES (%s, %s);
    """,
        (chat_id, producto_id),
    )
    conn.commit()
    conn.close()


# === CONSULTA INTELIGENTE A BD ===
def consultar_producto(pregunta, chat_id=None):
    conn = conectar_bd()
    cur = conn.cursor()
    pregunta = reemplazar_sinonimos(pregunta)

    productos = [
        "ABT Pro",
        "Cuaderno A4",
        "Cuaderno Infinito A4",
        "Cuaderno Infinito A5",
        "Libro Stickers Vinta",
        "Nota Transparente Grande",
        "Nota Transparente Mediana",
        "Pad 200 hojas",
        "Pigma Micropen",
        "Repuesto A5",
        "Sticker Imágenes",
        "Sticker Princesas",
        "Sticker Vintage",
        "Stickers Retro",
        "Tablero Poccaco",
    ]

    for nombre in productos:
        if nombre.lower() in pregunta:
            cur.execute(
                """
                SELECT p.id, p.nombre, p.stock, pp.monto
                FROM Producto p
                JOIN PrecioProducto pp ON p.id = pp.producto_id
                WHERE p.nombre = %s AND pp.lista_precio_id = 1;
            """,
                (nombre,),
            )
            resultado = cur.fetchone()
            if resultado:
                if chat_id:
                    registrar_interes(chat_id, resultado["id"])
                conn.close()
                return f"{resultado['nombre']}: Bs. {resultado['monto']}, stock disponible: {resultado['stock']}"
            else:
                conn.close()
                return f"No se encontró información para {nombre}."

    # Categoría específica
    cur.execute("SELECT id, nombre FROM Categoria;")
    categorias = cur.fetchall()
    for cat in categorias:
        if cat["nombre"].lower() in pregunta:
            cur.execute(
                """
                SELECT nombre, id FROM Producto
                WHERE categoria_id = %s;
            """,
                (cat["id"],),
            )
            productos_cat = cur.fetchall()
            conn.close()
            if productos_cat:
                nombres = [p["nombre"] for p in productos_cat]
                if chat_id:
                    for p in productos_cat:
                        registrar_interes(chat_id, p["id"])
                return (
                    f"Los productos en la categoría {cat['nombre']} son:\n- "
                    + "\n- ".join(nombres)
                )
            else:
                return f"No hay productos en la categoría {cat['nombre']}."

    # Resumen de categorías
    if "categoría" in pregunta or "categorias" in pregunta:
        cur.execute(
            """
            SELECT c.nombre, COUNT(p.id) as cantidad
            FROM Categoria c
            LEFT JOIN Producto p ON p.categoria_id = c.id
            GROUP BY c.nombre;
        """
        )
        lista = cur.fetchall()
        conn.close()
        return "\n".join([f"{c['nombre']}: {c['cantidad']} productos" for c in lista])

    conn.close()
    return None


@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    try:
        numero_completo = request.values.get("From", "")  # Ej: whatsapp:+591...
        user_number = numero_completo.replace("whatsapp:", "")
        incoming_msg = request.values.get("Body", "").strip()

        resp = MessagingResponse()
        msg = resp.message()

        print(f"📩 Mensaje recibido de {user_number}: {incoming_msg}")

        chat_id, nombre_cliente = obtener_chat_id(user_number)
        guardar_mensaje(chat_id, incoming_msg, emisor="cliente")

        if numero_completo not in conversaciones:
            conversaciones[numero_completo] = [
                {"role": "system", "content": contexto_negocio}
            ]
            saludo = f"¡Hola {nombre_cliente}! 😊 ¿En qué podemos ayudarte hoy?"
            guardar_mensaje(chat_id, saludo, emisor="sistema")
            msg.body(saludo)
            print("👋 Se envió saludo inicial.")
            return Response(str(resp), content_type="application/xml")

        # Consultar si se puede responder desde la BD
        respuesta_bd = consultar_producto(incoming_msg, chat_id=chat_id)
        if respuesta_bd:
            guardar_mensaje(chat_id, respuesta_bd, emisor="sistema")
            msg.body(respuesta_bd)
            print("📦 Se respondió desde base de datos.")
            return Response(str(resp), content_type="application/xml")
        else:
            print("🔍 No se encontró respuesta en BD. Enviando a OpenAI...")

        conversaciones[numero_completo].append(
            {"role": "user", "content": incoming_msg}
        )

        response = client.chat.completions.create(
            model="gpt-3.5-turbo", messages=conversaciones[numero_completo]
        )
        reply = response.choices[0].message.content.strip()

        if not reply:
            reply = "Lo siento, no entendí tu mensaje. ¿Podrías reformularlo?"

        conversaciones[numero_completo].append({"role": "assistant", "content": reply})
        guardar_mensaje(chat_id, reply, emisor="sistema")
        msg.body(reply)
        print("🤖 Respuesta generada por OpenAI.")
        return Response(str(resp), content_type="application/xml")

    except Exception as e:
        print("❌ Error en /whatsapp:", e)
        resp = MessagingResponse()
        msg = resp.message(
            "Ocurrió un error al procesar tu mensaje. Intenta nuevamente."
        )
        return Response(str(resp), content_type="application/xml")


if __name__ == "__main__":
    app.run(debug=True)
