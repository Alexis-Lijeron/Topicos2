import os
from flask import json
import psycopg2
from psycopg2.extras import RealDictCursor
from openai import OpenAI
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
DATABASE_URL = os.getenv("DATABASE_URL")


def conectar_bd():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def obtener_chats_del_dia():
    conn = conectar_bd()
    cur = conn.cursor()
    hoy = datetime.now().date()
    # hoy= "2025-05-22"
    cur.execute(
        """
        SELECT DISTINCT c.id AS chat_id, cl.nombre, cl.telefono
        FROM Chat c
        JOIN Mensaje m ON m.chat_id = c.id
        JOIN Cliente cl ON c.cliente_id = cl.id
        WHERE m.fecha_envio::date = %s;
    """,
        (hoy,),
    )
    resultados = cur.fetchall()
    conn.close()
    return resultados


def reconstruir_historial(chat_id):
    conn = conectar_bd()
    cur = conn.cursor()
    hoy = datetime.now().date()

    cur.execute(
        """
        SELECT emisor, contenido
        FROM Mensaje
        WHERE chat_id = %s AND fecha_envio::date = %s
        ORDER BY fecha_envio ASC;
    """,
        (chat_id, hoy),
    )
    mensajes = cur.fetchall()
    conn.close()

    historial = [
        {
            "role": "system",
            "content": "Eres un asistente que detecta intereses de productos, promociones o categorías mencionadas en los chats de clientes.",
        }
        # {
        #    "role": "system",
        #    "content": (
        #        "Eres un asistente que detecta intereses de productos, promociones o categorías mencionadas "
        #        "en los chats de clientes. Solo registra productos si se mencionan explícitamente, no los asumas "
        #        "por mención de categoría."
        #    ),
        # }
    ]
    for m in mensajes:
        if m["emisor"] == "cliente":
            historial.append({"role": "user", "content": m["contenido"]})
        else:
            historial.append({"role": "assistant", "content": m["contenido"]})
    return historial


# Evitar duplicados exactos en la base
def registrar_interes_si_no_existe(chat_id, producto_id, observacion=""):
    conn = conectar_bd()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id FROM InteresProductoChat
        WHERE chat_id = %s AND producto_id = %s
        AND fecha_registro::date = CURRENT_DATE;
        """,
        (chat_id, producto_id),
    )
    existe = cur.fetchone()
    if not existe:
        cur.execute(
            """
            INSERT INTO InteresProductoChat (chat_id, producto_id, observacion)
            VALUES (%s, %s, %s);
            """,
            (chat_id, producto_id, observacion),
        )
        conn.commit()
    conn.close()


def detectar_intenciones(historial):
    prompt_final = {
        "role": "user",
        # "content": (
        #    "Con base en la conversación anterior, dime los productos, promociones y categorías mencionadas. "
        #    "Devuélvelo en formato JSON con estas claves: 'productos', 'categorias', 'promociones'. "
        #    'Ejemplo: {"productos": ["Sticker Retro"], "categorias": ["Stickers"], "promociones": ["Vuelta al cole"]}'
        # )
        "content": (
            "Analiza la conversación entre el cliente y el asistente. Detecta los intereses explícitos o implícitos del cliente sobre productos, categorías o promociones. Devuelve un JSON con la siguiente estructura:\n\n"
            '{\n  "productos": [\n    {\n      "nombre": "Cuaderno A4",\n      "precio": true,\n      "stock": true,\n      "promocion": false,\n      "menciones": 2,\n      "observacion": "El cliente preguntó dos veces por el producto Cuaderno A4, incluyendo preguntas sobre precio y stock."\n    }\n  ],\n  "categorias": [\n    {\n      "nombre": "Cuaderno",\n      "observacion": "El cliente preguntó por productos de la categoría Cuaderno."\n    }\n  ],\n  "promociones": [\n    {\n      "nombre": "Vuelta al cole",\n      "observacion": "Se mencionó la promoción Vuelta al cole."\n    }\n  ]\n}\n\nSolo devuelve el JSON, sin explicaciones. Usa los mensajes anteriores como contexto.'
        ),
    }
    historial.append(prompt_final)

    respuesta = client.chat.completions.create(
        model="gpt-3.5-turbo", messages=historial, temperature=0.2
    )
    return respuesta.choices[0].message.content.strip()

    # def guardar_intereses(chat_id, data):
    import json

    try:
        datos = json.loads(data)
    except json.JSONDecodeError:
        print(f"⚠️ No se pudo decodificar JSON para chat {chat_id}: {data}")
        return

    conn = conectar_bd()
    cur = conn.cursor()

    for producto in datos.get("productos", []):
        cur.execute(
            "SELECT id FROM Producto WHERE LOWER(nombre) = LOWER(%s) LIMIT 1;",
            (producto,),
        )
        p = cur.fetchone()
        if p:
            cur.execute(
                """
                INSERT INTO InteresProductoChat (chat_id, producto_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING;
            """,
                (chat_id, p["id"]),
            )

    for categoria in datos.get("categorias", []):
        cur.execute(
            "SELECT id FROM Categoria WHERE LOWER(nombre) = LOWER(%s) LIMIT 1;",
            (categoria,),
        )
        c = cur.fetchone()
        if c:
            cur.execute(
                """
                INSERT INTO InteresCategoriaChat (chat_id, categoria_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING;
            """,
                (chat_id, c["id"]),
            )

    for promocion in datos.get("promociones", []):
        cur.execute(
            "SELECT id FROM Promocion WHERE LOWER(nombre) = LOWER(%s) LIMIT 1;",
            (promocion,),
        )
        pr = cur.fetchone()
        if pr:
            cur.execute(
                """
                INSERT INTO InteresPromocionChat (chat_id, promocion_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING;
            """,
                (chat_id, pr["id"]),
            )

    conn.commit()
    conn.close()


def guardar_intereses(chat_id, data):
    try:
        datos = json.loads(data)
    except json.JSONDecodeError:
        print(f"⚠️ No se pudo decodificar JSON para chat {chat_id}: {data}")
        return

    conn = conectar_bd()
    cur = conn.cursor()
    hoy = datetime.now().date()

    for prod in datos.get("productos", []):
        cur.execute(
            "SELECT id FROM Producto WHERE LOWER(nombre) = LOWER(%s) LIMIT 1;",
            (prod["nombre"],),
        )
        p = cur.fetchone()
        if p:
            cur.execute(
                """
                SELECT id FROM InteresProductoChat
                WHERE chat_id = %s AND producto_id = %s AND fecha_registro::date = %s;
            """,
                (chat_id, p["id"], hoy),
            )
            if not cur.fetchone():
                cur.execute(
                    """
                    INSERT INTO InteresProductoChat (chat_id, producto_id, observacion)
                    VALUES (%s, %s, %s);
                """,
                    (chat_id, p["id"], prod.get("observacion", "")),
                )

    for cat in datos.get("categorias", []):
        cur.execute(
            "SELECT id FROM Categoria WHERE LOWER(nombre) = LOWER(%s) LIMIT 1;",
            (cat["nombre"],),
        )
        c = cur.fetchone()
        if c:
            cur.execute(
                """
                SELECT id FROM InteresCategoriaChat
                WHERE chat_id = %s AND categoria_id = %s AND fecha_registro::date = %s;
            """,
                (chat_id, c["id"], hoy),
            )
            if not cur.fetchone():
                cur.execute(
                    """
                    INSERT INTO InteresCategoriaChat (chat_id, categoria_id, observacion)
                    VALUES (%s, %s, %s);
                """,
                    (chat_id, c["id"], cat.get("observacion", "")),
                )

    for promo in datos.get("promociones", []):
        cur.execute(
            "SELECT id FROM Promocion WHERE LOWER(nombre) = LOWER(%s) LIMIT 1;",
            (promo["nombre"],),
        )
        pr = cur.fetchone()
        if pr:
            cur.execute(
                """
                SELECT id FROM InteresPromocionChat
                WHERE chat_id = %s AND promocion_id = %s AND fecha_registro::date = %s;
            """,
                (chat_id, pr["id"], hoy),
            )
            if not cur.fetchone():
                cur.execute(
                    """
                    INSERT INTO InteresPromocionChat (chat_id, promocion_id, observacion)
                    VALUES (%s, %s, %s);
                """,
                    (chat_id, pr["id"], promo.get("observacion", "")),
                )

    conn.commit()
    conn.close()


def main():
    chats = obtener_chats_del_dia()
    print(f"🧠 Procesando {len(chats)} chats del día...")

    for chat in chats:
        print(f"📊 INTENCIONES CHAT {chat['chat_id']} ({chat['telefono']}):")
        historial = reconstruir_historial(chat["chat_id"])
        respuesta = detectar_intenciones(historial)
        print(respuesta)
        guardar_intereses(chat["chat_id"], respuesta)


if __name__ == "__main__":
    main()
