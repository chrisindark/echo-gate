from app.core.dependencies import DependencyContainer

q = DependencyContainer.get_qdrant_client_service()
print("Reinitialized")
