import os
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
import pdfkit
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
RUTA_SALIDA = "./pdfs_clientes"
config = pdfkit.configuration(wkhtmltopdf=r"C:/Program Files/wkhtmltopdf/bin/wkhtmltopdf.exe")


def conectar_bd():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def obtener_promociones_extras(chat_id, cur):
    cur.execute("""
        SELECT prom.id, prom.nombre, prom.porcentaje_descuento, prom.monto_descuento, prom.fecha_fin
        FROM InteresPromocionChat ipc
        JOIN Promocion prom ON ipc.promocion_id = prom.id
        WHERE ipc.chat_id = %s
        AND CURRENT_DATE BETWEEN prom.fecha_inicio AND prom.fecha_fin;
    """, (chat_id,))
    promociones = cur.fetchall()

    resultado = {}
    for promo in promociones:
        cur.execute("""
            SELECT p.nombre AS producto, p.descripcion, p.stock
            FROM ProductoPromocion pp
            JOIN Producto p ON pp.producto_id = p.id
            WHERE pp.promocion_id = %s;
        """, (promo["id"],))
        promo["productos"] = cur.fetchall()
        resultado[promo["id"]] = promo
    return resultado


def obtener_intereses_completos():
    conn = conectar_bd()
    cur = conn.cursor()
    hoy = datetime.now().date()

    cur.execute("""
        WITH interes_base AS (
            SELECT 
                c.id AS chat_id, cl.nombre, cl.correo, 
                p.id AS producto_id, p.nombre AS producto, p.descripcion, p.stock,
                cat.id AS categoria_id, cat.nombre AS categoria,
                pp.monto AS precio,
                ipc.observacion,
                prom.id AS promo_id, prom.nombre AS promocion, prom.porcentaje_descuento, 
                prom.monto_descuento, prom.fecha_fin
            FROM InteresProductoChat ipc
            JOIN Chat c ON ipc.chat_id = c.id
            JOIN Cliente cl ON c.cliente_id = cl.id
            JOIN Producto p ON ipc.producto_id = p.id
            LEFT JOIN Categoria cat ON p.categoria_id = cat.id
            LEFT JOIN PrecioProducto pp ON pp.producto_id = p.id
                AND CURRENT_DATE BETWEEN pp.fecha_inicio AND COALESCE(pp.fecha_fin, CURRENT_DATE)
            LEFT JOIN ProductoPromocion ppr ON ppr.producto_id = p.id
            LEFT JOIN Promocion prom ON prom.id = ppr.promocion_id
                AND CURRENT_DATE BETWEEN prom.fecha_inicio AND prom.fecha_fin
            WHERE ipc.fecha_registro::date = %s
        )
        SELECT * FROM interes_base
        ORDER BY nombre;
    """, (hoy,))

    resultados = cur.fetchall()
    chat_promos = {}

    for row in resultados:
        chat_id = row["chat_id"]

        if chat_id not in chat_promos:
            # Agrega promociones explícitas (InteresPromocionChat)
            chat_promos[chat_id] = obtener_promociones_extras(chat_id, cur)
        row["promociones_extras"] = chat_promos[chat_id]

        # productos por promoción
        if row['promo_id']:
            cur.execute("""
                SELECT p.nombre AS producto, p.descripcion, p.stock
                FROM ProductoPromocion pp
                JOIN Producto p ON pp.producto_id = p.id
                WHERE pp.promocion_id = %s;
            """, (row['promo_id'],))
            row['productos_promocion'] = cur.fetchall()
        else:
            row['productos_promocion'] = []

        # productos por categoría
        if row['categoria_id']:
            cur.execute("""
                SELECT p.nombre AS producto, p.descripcion, p.stock
                FROM Producto p
                WHERE p.categoria_id = %s AND p.id != %s;
            """, (row['categoria_id'], row['producto_id']))
            row['productos_categoria'] = cur.fetchall()
        else:
            row['productos_categoria'] = []

    conn.close()
    return resultados


def agrupar_por_cliente(data):
    agrupado = {}
    for row in data:
        clave = (row["chat_id"], row["nombre"], row["correo"])
        if clave not in agrupado:
            agrupado[clave] = {
                "productos": [],
                "promociones": {}
            }
        agrupado[clave]["productos"].append(row)

        # promociones ligadas al producto
        if row.get("promocion"):
            promo_id = row["promo_id"]
            if promo_id and promo_id not in agrupado[clave]["promociones"]:
                agrupado[clave]["promociones"][promo_id] = {
                    "nombre": row["promocion"],
                    "descuento": f"{row['porcentaje_descuento']}% / Bs. {row['monto_descuento']}",
                    "fecha_fin": row["fecha_fin"],
                    "productos": row["productos_promocion"]
                }

        # promociones explícitas desde InteresPromocionChat
        for pid, promo in row.get("promociones_extras", {}).items():
            if pid not in agrupado[clave]["promociones"]:
                agrupado[clave]["promociones"][pid] = {
                    "nombre": promo["nombre"],
                    "descuento": f"{promo['porcentaje_descuento']}% / Bs. {promo['monto_descuento']}",
                    "fecha_fin": promo["fecha_fin"],
                    "productos": promo["productos"]
                }

    return agrupado


def crear_pdf_para_cliente(nombre, correo, data_cliente, nombre_archivo):
    productos = data_cliente["productos"]
    promociones = data_cliente["promociones"]

    html = f"""<html><head><meta charset="UTF-8"><style>
    body {{ font-family: Arial; margin: 40px; color: #333; }}
    h1 {{ color: #2c3e50; border-bottom: 2px solid #ccc; }}
    h2 {{ margin-top: 30px; }}
    .producto, .promo-rel, .cat-rel {{
        border: 1px solid #ddd; padding: 15px; margin-bottom: 15px;
        border-radius: 8px; background-color: #f9f9f9;
    }}
    .promo {{ background-color: #dff0d8; border-left: 5px solid #3c763d; }}
    .categoria {{ background-color: #e8f4fc; border-left: 5px solid #31708f; }}
    </style></head><body>
    <h1>Resumen de Intereses - {nombre}</h1>
    <p>Correo: <strong>{correo}</strong></p>
    <h2>Productos de Interés</h2>
    """

    for p in productos:
        html += f"""
        <div class="producto">
            <strong>Producto:</strong> {p['producto']}<br>
            <strong>Descripción:</strong> {p['descripcion']}<br>
            <strong>Precio:</strong> Bs. {p['precio']:.2f}<br>
            <strong>Stock:</strong> {p['stock']} unidades<br>
            <strong>Observación:</strong> {p['observacion']}<br>
        """
        if p.get("promocion"):
            html += f"""<div class="promo">
                <strong>Promoción:</strong> {p['promocion']}<br>
                <strong>Descuento:</strong> {p['porcentaje_descuento']}% / Bs. {p['monto_descuento']}<br>
                <strong>Vigente hasta:</strong> {p['fecha_fin']}
            </div>"""
        if p.get("categoria"):
            html += f"""<div class="categoria"><strong>Categoría:</strong> {p['categoria']}</div>"""

        if p['productos_promocion']:
            html += "<div class='promo-rel'><strong>Otros productos en esta promoción:</strong><ul>"
            for prod in p['productos_promocion']:
                html += f"<li>{prod['producto']} - {prod['descripcion']} (Stock: {prod['stock']})</li>"
            html += "</ul></div>"

        if p['productos_categoria']:
            html += "<div class='cat-rel'><strong>Otros productos en esta categoría:</strong><ul>"
            for prod in p['productos_categoria']:
                html += f"<li>{prod['producto']} - {prod['descripcion']} (Stock: {prod['stock']})</li>"
            html += "</ul></div>"

        html += "</div>"

    if promociones:
        html += "<h2>Promociones de Interés</h2>"
        for promo in promociones.values():
            html += f"""
            <div class="promo">
                <strong>Promoción:</strong> {promo['nombre']}<br>
                <strong>Descuento:</strong> {promo['descuento']}<br>
                <strong>Vigente hasta:</strong> {promo['fecha_fin']}<br>
                <strong>Productos incluidos:</strong><ul>
            """
            for prod in promo["productos"]:
                html += f"<li>{prod['producto']} - {prod['descripcion']} (Stock: {prod['stock']})</li>"
            html += "</ul></div>"

    html += "</body></html>"
    pdfkit.from_string(html, nombre_archivo, configuration=config)


def main():
    os.makedirs(RUTA_SALIDA, exist_ok=True)
    data = obtener_intereses_completos()
    agrupado = agrupar_por_cliente(data)
    for (chat_id, nombre, correo), data_cliente in agrupado.items():
        archivo = os.path.join(RUTA_SALIDA, f"{nombre.replace(' ', '_')}_{chat_id}.pdf")
        crear_pdf_para_cliente(nombre, correo, data_cliente, archivo)
        print(f"✅ PDF generado para {nombre} ({correo}): {archivo}")


if __name__ == "__main__":
    main()
