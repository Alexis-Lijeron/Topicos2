import os
from dotenv import load_dotenv
import chromadb
from chromadb.utils import embedding_functions
from chromadb import PersistentClient

# Cargar API key
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Conectar a ChromaDB local
client = PersistentClient(path="chroma_db")

# Usar función de embedding con OpenAI
embed_fn = embedding_functions.OpenAIEmbeddingFunction(
    api_key=OPENAI_API_KEY, model_name="text-embedding-3-small"
)

# Cargar colección
collection = client.get_or_create_collection(
    name="productos_marketing", embedding_function=embed_fn
)

# Consultar en bucle
print("🔍 Escribe tu consulta sobre los productos (o escribe 'salir'):")

while True:
    query = input("🧠 Tú: ").strip()
    if query.lower() in ("salir", "exit", "quit"):
        break

    resultados = collection.query(query_texts=[query], n_results=3)

    print("\n📦 Productos más relevantes:")
    for i, (doc, meta) in enumerate(
        zip(resultados["documents"][0], resultados["metadatas"][0]), 1
    ):
        print(f"\n#{i}")
        print(f"📝 Nombre: {meta.get('nombre')}")
        print(f"📂 Categoría: {meta.get('categoria')}")
        print(f"💵 Precio: Bs. {meta.get('precio')}")
        print(f"🧾 Detalle: {doc[:300]}...")  # limitar texto largo
    print("-" * 50)
