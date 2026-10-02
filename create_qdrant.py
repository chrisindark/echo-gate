from app.core.dependencies import DependencyContainer

q = DependencyContainer.get_qdrant_service()
print("Reinitialized")
