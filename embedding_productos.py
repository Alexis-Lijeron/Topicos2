import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import chromadb
from chromadb.config import Settings
from chromadb.utils import embedding_functions
from chromadb import PersistentClient

# Carga las variables de entorno
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")


# === 1. Conectar a la base de datos ===
def conectar_bd():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


# === 2. Obtener todos los productos con datos relevantes ===
def obtener_productos():
    conn = conectar_bd()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT 
            p.id,
            p.nombre,
            COALESCE(p.descripcion, '') AS descripcion,
            p.stock,
            c.nombre AS categoria,
            pp.monto AS precio,
            ARRAY(
                SELECT pr.nombre || ' (' || pr.porcentaje_descuento || '% hasta ' || pr.fecha_fin || ')'
                FROM ProductoPromocion pprom
                JOIN Promocion pr ON pr.id = pprom.promocion_id
                WHERE pprom.producto_id = p.id
            ) AS promociones
        FROM Producto p
        JOIN Categoria c ON p.categoria_id = c.id
        LEFT JOIN PrecioProducto pp ON pp.producto_id = p.id AND pp.lista_precio_id = 1
    """
    )

    productos = cur.fetchall()
    conn.close()
    return productos


# === 3. Preparar los textos para embedding ===
def preparar_documentos(productos):
    documentos = []
    metadatos = []
    ids = []

    for p in productos:
        promo_texto = (
            ", ".join(p["promociones"]) if p["promociones"] else "Sin promociones"
        )
        texto = f"""
        Producto: {p['nombre']}
        Categoría: {p['categoria']}
        Descripción: {p['descripcion']}
        Precio: Bs. {p['precio']}
        Stock disponible: {p['stock']}
        Promociones: {promo_texto}
        """.strip()

        documentos.append(texto)
        ids.append(f"producto_{p['id']}")
        metadatos.append(
            {
                "categoria": p["categoria"],
                "precio": str(p["precio"]),
                "stock": p["stock"],
            }
        )

    return documentos, ids, metadatos


# === 4. Guardar en ChromaDB ===
def guardar_en_chroma(documents, ids, metadatas):
    client = PersistentClient(path="chroma_db")  # <- esta es la nueva forma correcta

    embed_fn = embedding_functions.OpenAIEmbeddingFunction(
        api_key=os.getenv("OPENAI_API_KEY"), model_name="text-embedding-3-small"
    )

    collection = client.get_or_create_collection(
        name="productos_marketing", embedding_function=embed_fn
    )

    collection.add(documents=documents, ids=ids, metadatas=metadatas)

    print("✅ Guardado en ChromaDB con persistencia local (chroma_db/)")


# === EJECUCIÓN ===
if __name__ == "__main__":
    productos = obtener_productos()
    if not productos:
        print("⚠️ No se encontraron productos en la base de datos.")
    else:
        docs, ids, metas = preparar_documentos(productos)
        guardar_en_chroma(docs, ids, metas)
