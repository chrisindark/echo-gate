import os

from qdrant_client import QdrantClient, models
from tqdm import tqdm

from app.modules.embedding.embedding_service import EmbeddingService

# --------------------------------------------------
# Configuration
# --------------------------------------------------

QDRANT_URL = "http://localhost:6333"
SOURCE_COLLECTION = "embeddings_multivector"
TARGET_COLLECTION = "embeddings_multivector_v2"

EMBEDDING_DIM = 768  # Change this to your embedding model's dimension

client = QdrantClient(url=QDRANT_URL)


# --------------------------------------------------
# Embedding function
# --------------------------------------------------


def embed(text: str) -> list[float]:
    """
    Replace this with your actual embedding call.

    Example:
        response = ollama.embeddings(
            model="nomic-embed-text:latest",
            prompt=text,
        )
        return response["embedding"]
    """
    os.environ["OLLAMA_URL"] = "http://localhost:11434"
    os.environ["BM25_MODEL_PATH"] = (
        "/Users/christopherpaul/projects/echo-gate/.model_cache/models--Qdrant--bm25"
    )
    embedding_service = EmbeddingService()
    vector = embedding_service.get_embedding(f"{text}")
    return vector


# --------------------------------------------------
# Parse existing prompt
# --------------------------------------------------


def extract_system_prompt(prompt: str) -> str:
    """
    Extract:

        system: ...

    from something like:

        model:gemini-3.1-flash-lite|system: You are a gene...
    """

    if not prompt:
        return ""

    marker = "|system:"

    if marker in prompt:
        return prompt.split(marker, 1)[1].strip()

    return ""


def extract_user_prompt(prompt: str) -> str:
    if not prompt:
        return ""

    marker = "user:"

    if marker in prompt:
        return prompt.split(marker, 1)[1].strip()

    return ""


def extract_model(prompt: str) -> str:
    """
    Extract:

        model:gemini-3.1-flash-lite

    from the existing prompt.
    """

    if not prompt:
        return ""

    if prompt.startswith("model:"):
        first_part = prompt.split("|", 1)[0]
        return first_part.replace("model:", "", 1).strip()

    return ""


# --------------------------------------------------
# Create target collection
# --------------------------------------------------

if client.collection_exists(TARGET_COLLECTION):
    print(f"Collection {TARGET_COLLECTION} already exists")
else:
    client.create_collection(
        collection_name=TARGET_COLLECTION,
        vectors_config={
            "prompt_embedding": models.VectorParams(
                size=EMBEDDING_DIM,
                distance=models.Distance.COSINE,
            ),
            "system_prompt_embedding": models.VectorParams(
                size=EMBEDDING_DIM,
                distance=models.Distance.COSINE,
            ),
            "intent_embedding": models.VectorParams(
                size=EMBEDDING_DIM,
                distance=models.Distance.COSINE,
            ),
            "response_embedding": models.VectorParams(
                size=EMBEDDING_DIM,
                distance=models.Distance.COSINE,
            ),
        },
    )


# --------------------------------------------------
# Process source collection
# --------------------------------------------------

offset = None

while True:
    points, offset = client.scroll(
        collection_name=SOURCE_COLLECTION,
        limit=1000,
        offset=offset,
        with_payload=True,
        with_vectors=False,
    )

    if not points:
        break

    new_points = []

    for point in tqdm(points, desc="Creating embeddings"):
        payload = point.payload or {}

        prompt = payload.get("prompt", "")
        intent = payload.get("intent", "")
        response = payload.get("response", "")

        # ------------------------------
        # Extract system prompt
        # ------------------------------
        print("------------------------------")
        print("point id:", point.id)
        print("full prompt: ", prompt)

        system_prompt = extract_system_prompt(prompt)
        print("system prompt: ", system_prompt)
        user_prompt = extract_user_prompt(prompt)
        print("user prompt: ", user_prompt)
        print("intent: ", intent)
        print("response: ", response)

        # ------------------------------
        # Normalize response
        # ------------------------------

        if isinstance(response, dict):
            # Adapt this depending on your actual response structure
            response_text = str(response)
        else:
            response_text = str(response or "")

        # ------------------------------
        # Generate embeddings
        # ------------------------------

        prompt_vector = embed(prompt)
        system_vector = embed(system_prompt)
        intent_vector = embed(intent)
        response_vector = embed(response_text)

        # ------------------------------
        # Keep original payload
        # ------------------------------

        new_payload = dict(payload)

        # ------------------------------
        # Create point
        # ------------------------------

        new_points.append(
            models.PointStruct(
                id=point.id,
                vector={
                    "prompt_embedding": prompt_vector,
                    "system_prompt_embedding": system_vector,
                    "intent_embedding": intent_vector,
                    "response_embedding": response_vector,
                },
                payload=new_payload,
            )
        )

    # ------------------------------
    # Upsert batch
    # ------------------------------

    client.upsert(
        collection_name=TARGET_COLLECTION,
        points=new_points,
    )

    print(f"Processed {len(points)} points")

    if offset is None:
        break

print("Done!")
