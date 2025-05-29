import os
from chromadb import PersistentClient
from chromadb.utils import embedding_functions
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# === 1. Inicializar cliente de ChromaDB con función de embedding ===
embed_fn = embedding_functions.OpenAIEmbeddingFunction(
    api_key=OPENAI_API_KEY, model_name="text-embedding-3-small"
)

chroma_client = PersistentClient(path="chroma_db")
collection = chroma_client.get_or_create_collection(
    name="productos_marketing", embedding_function=embed_fn
)


# === 2. Función para consultar los productos más relevantes ===
def consultar_productos_similares(pregunta_usuario, k=5):
    resultados = collection.query(query_texts=[pregunta_usuario], n_results=k)

    print("🔍 Consulta:", pregunta_usuario)
    for i in range(len(resultados["documents"][0])):
        doc = resultados["documents"][0][i]
        meta = resultados["metadatas"][0][i]
        print(f"\n🎯 Producto {i+1}:")
        print(doc)
        print("📌 Metadata:", meta)


# === 3. Ejemplo de uso ===
if __name__ == "__main__":
    consultar_productos_similares("¿Qué cuadernos con descuento tienes disponibles?")
