import os
import requests
import hashlib
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from jinja2 import Environment, FileSystemLoader
from dotenv import load_dotenv
from pathlib import Path
from weasyprint import HTML

# Configuración
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
RUTA_SALIDA = "./pdfs_clientes"
RUTA_PLANTILLAS = "./plantillas"
RUTA_IMAGENES = "./imagenes"

env = Environment(loader=FileSystemLoader(RUTA_PLANTILLAS), autoescape=True)
Path(RUTA_IMAGENES).mkdir(exist_ok=True)
Path(RUTA_SALIDA).mkdir(exist_ok=True)


def descargar_imagen_local(url, producto_id):
    if not url:
        return None
    hash_nombre = hashlib.md5(url.encode()).hexdigest()
    extension = url.split(".")[-1].split("?")[0]
    nombre_archivo = f"{producto_id}_{hash_nombre}.{extension}"
    ruta_local = Path(RUTA_IMAGENES) / nombre_archivo

    if not ruta_local.exists():
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                with open(ruta_local, "wb") as f:
                    f.write(r.content)
        except Exception as e:
            print(f"⚠️ Error al descargar imagen: {url} -> {e}")
            return None

    return str(ruta_local.resolve().as_posix())


def conectar_bd():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def obtener_intereses_del_dia():
    hoy = datetime.now().date()
    conn = conectar_bd()
    cur = conn.cursor()

    # === Productos de interés ===
    cur.execute(
        """
        SELECT ipc.id, c.id AS chat_id, cl.nombre AS cliente_nombre, cl.telefono, cl.correo,
               p.id AS producto_id, p.nombre AS producto_nombre, p.descripcion AS producto_descripcion,
               cat.nombre AS categoria_nombre,
               pp.monto AS producto_precio,
               ip.url AS imagen_url,
               prom.nombre AS promocion_nombre, prom.fecha_fin AS promocion_fecha_fin
        FROM InteresProductoChat ipc
        JOIN Chat c ON ipc.chat_id = c.id
        JOIN Cliente cl ON c.cliente_id = cl.id
        JOIN Producto p ON p.id = ipc.producto_id
        LEFT JOIN ImagenProducto ip ON ip.producto_id = p.id
        LEFT JOIN Categoria cat ON cat.id = p.categoria_id
        LEFT JOIN PrecioProducto pp ON pp.producto_id = p.id
             AND CURRENT_DATE BETWEEN pp.fecha_inicio AND COALESCE(pp.fecha_fin, CURRENT_DATE)
        LEFT JOIN ProductoPromocion pprom ON pprom.producto_id = p.id
        LEFT JOIN Promocion prom ON prom.id = pprom.promocion_id
             AND CURRENT_DATE BETWEEN prom.fecha_inicio AND prom.fecha_fin
        WHERE ipc.fecha_registro::date = %s AND ipc.estado = 'pendiente';
    """,
        (hoy,),
    )
    productos = cur.fetchall()
    for p in productos:
        p["imagen_url"] = limpiar_url_cloudinary(p["imagen_url"])

    # === Categorías de interés ===
    cur.execute(
        """
        SELECT icc.id, c.id AS chat_id, cl.nombre AS cliente_nombre, cl.telefono, cl.correo,
               cat.id AS categoria_id, cat.nombre AS categoria_nombre, cat.descripcion AS categoria_descripcion,
               icc.observacion
        FROM InteresCategoriaChat icc
        JOIN Chat c ON icc.chat_id = c.id
        JOIN Cliente cl ON c.cliente_id = cl.id
        JOIN Categoria cat ON cat.id = icc.categoria_id
        WHERE icc.fecha_registro::date = %s AND icc.estado = 'pendiente';
    """,
        (hoy,),
    )
    categorias = cur.fetchall()

    for cat in categorias:
        cur.execute(
            """
            SELECT p.id AS producto_id, p.nombre AS producto_nombre, p.descripcion AS producto_descripcion,
                   ip.url AS imagen_url,
                   pp.monto AS producto_precio,
                   prom.nombre AS promocion_nombre, prom.fecha_fin AS promocion_fecha_fin
            FROM Producto p
            LEFT JOIN ImagenProducto ip ON ip.producto_id = p.id
            LEFT JOIN PrecioProducto pp ON pp.producto_id = p.id 
                 AND CURRENT_DATE BETWEEN pp.fecha_inicio AND COALESCE(pp.fecha_fin, CURRENT_DATE)
            LEFT JOIN ProductoPromocion pprom ON pprom.producto_id = p.id
            LEFT JOIN Promocion prom ON prom.id = pprom.promocion_id 
                 AND CURRENT_DATE BETWEEN prom.fecha_inicio AND prom.fecha_fin
            WHERE p.categoria_id = %s
        """,
            (cat["categoria_id"],),
        )
        productos_categoria = cur.fetchall()
        for p in productos_categoria:
            p["imagen_url"] = limpiar_url_cloudinary(p["imagen_url"])
        cat["productos"] = productos_categoria

    # === Promociones de interés ===
    cur.execute(
        """
        SELECT ipc.id, c.id AS chat_id, cl.nombre AS cliente_nombre, cl.telefono, cl.correo,
               prom.id AS promocion_id, prom.nombre AS promocion_nombre, prom.descripcion AS promocion_descripcion,
               prom.porcentaje_descuento, prom.monto_descuento, prom.fecha_inicio, prom.fecha_fin,
               ipc.observacion
        FROM InteresPromocionChat ipc
        JOIN Chat c ON ipc.chat_id = c.id
        JOIN Cliente cl ON c.cliente_id = cl.id
        JOIN Promocion prom ON prom.id = ipc.promocion_id
        WHERE ipc.fecha_registro::date = %s AND ipc.estado = 'pendiente';
    """,
        (hoy,),
    )
    promociones = cur.fetchall()

    for promo in promociones:
        cur.execute(
            """
            SELECT p.id AS producto_id, p.nombre AS producto_nombre, p.descripcion AS producto_descripcion,
                   ip.url AS imagen_url,
                   pp.monto AS producto_precio
            FROM ProductoPromocion pprom
            JOIN Producto p ON p.id = pprom.producto_id
            LEFT JOIN ImagenProducto ip ON ip.producto_id = p.id
            LEFT JOIN PrecioProducto pp ON pp.producto_id = p.id 
                 AND CURRENT_DATE BETWEEN pp.fecha_inicio AND COALESCE(pp.fecha_fin, CURRENT_DATE)
            WHERE pprom.promocion_id = %s
        """,
            (promo["promocion_id"],),
        )
        productos_promo = cur.fetchall()
        for p in productos_promo:
            p["imagen_url"] = limpiar_url_cloudinary(p["imagen_url"])
        promo["productos"] = productos_promo

    conn.close()
    return productos, categorias, promociones


def agrupar_por_cliente(data):
    agrupado = {}
    for row in data:
        clave = (row["chat_id"], row["cliente_nombre"], row["telefono"], row["correo"])
        if clave not in agrupado:
            agrupado[clave] = []
        agrupado[clave].append(row)
    return agrupado


def generar_pdf(nombre_archivo, plantilla_html, contexto):
    template = env.get_template(plantilla_html)
    html_rendered = template.render(contexto)
    HTML(string=html_rendered, base_url=".").write_pdf(nombre_archivo)


def eliminar_duplicados_por_id(lista, clave="producto_id"):
    vistos = set()
    resultado = []
    for item in lista:
        valor = item.get(clave)
        if valor and valor not in vistos:
            resultado.append(item)
            vistos.add(valor)
    return resultado


def limpiar_url_cloudinary(url):
    """Asegura que la URL de Cloudinary sea válida, completa y con https."""
    if not url:
        return None
    url = url.strip()
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://"):
        return url.replace("http://", "https://")
    return url  # si ya es https


def eliminar_duplicados_por_id(lista, clave="producto_id"):
    """Filtra elementos únicos en base al campo clave."""
    vistos = set()
    resultado = []
    for item in lista:
        valor = item.get(clave)
        if valor and valor not in vistos:
            resultado.append(item)
            vistos.add(valor)
    return resultado


def main():
    productos, categorias, promociones = obtener_intereses_del_dia()
    hoy_str = datetime.now().strftime("%Y-%m-%d")
    hora_str = datetime.now().strftime("%H-%M-%S")

    # Reemplazar imagen_url en productos por URL limpia
    for p in productos:
        p["imagen_url"] = limpiar_url_cloudinary(p["imagen_url"])

    for (chat_id, cliente_nombre, telefono, correo), items in agrupar_por_cliente(
        productos
    ).items():
        items_unicos = eliminar_duplicados_por_id(items)
    n_intereses = len(items_unicos)
    nombre_archivo = f"{RUTA_SALIDA}/PRODUCTOS_{cliente_nombre.replace(' ', '_')}_{telefono.replace(' ', '_')}_{chat_id}_{hoy_str}_{hora_str}_{n_intereses}.pdf"
    generar_pdf(
        nombre_archivo,
        "productos.html",
        {
            "nombre": cliente_nombre,
            "telefono": telefono,
            "correo": correo,
            "products": items_unicos,
        },
    )

    for cat in categorias:
        for p in cat.get("productos", []):
            p["imagen_url"] = limpiar_url_cloudinary(p["imagen_url"])
        cat["productos"] = eliminar_duplicados_por_id(cat.get("productos", []))

    for (chat_id, cliente_nombre, telefono, correo), items in agrupar_por_cliente(
        categorias
    ).items():
        n_intereses = len(items)
        nombre_archivo = f"{RUTA_SALIDA}/CATEGORIAS_{cliente_nombre.replace(' ', '_')}_{telefono.replace(' ', '_')}_{chat_id}_{hoy_str}_{hora_str}_{n_intereses}.pdf"
        generar_pdf(
            nombre_archivo,
            "categoria.html",
            {
                "nombre": cliente_nombre,
                "telefono": telefono,
                "correo": correo,
                "categories": items,
            },
        )

    for promo in promociones:
        for p in promo.get("productos", []):
            p["imagen_url"] = limpiar_url_cloudinary(p["imagen_url"])
        promo["productos"] = eliminar_duplicados_por_id(promo.get("productos", []))

    for (chat_id, cliente_nombre, telefono, correo), items in agrupar_por_cliente(
        promociones
    ).items():
        n_intereses = len(items)
        nombre_archivo = f"{RUTA_SALIDA}/PROMOS_{cliente_nombre.replace(' ', '_')}_{telefono.replace(' ', '_')}_{chat_id}_{hoy_str}_{hora_str}_{n_intereses}.pdf"
        generar_pdf(
            nombre_archivo,
            "promocion.html",
            {
                "nombre": cliente_nombre,
                "telefono": telefono,
                "correo": correo,
                "promociones": items,
            },
        )


if __name__ == "__main__":
    main()
