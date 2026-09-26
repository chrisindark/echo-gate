from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

class QdrantPayload(BaseModel):
    """
    Schema for saving vectors to Qdrant.
    """
    prompt: str = Field(..., description="The original prompt string that was embedded")
    response: Dict[str, Any] = Field(..., description="The LLM completion response dictionary")
    exact_hash: Optional[str] = Field(None, description="SHA256 hash of the exact prompt for fast exact matching")
    model: Optional[str] = Field(None, description="The model used for the completion")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Additional custom metadata")

    def to_qdrant_dict(self) -> Dict[str, Any]:
        """Convert the schema to a dictionary for Qdrant payload."""
        base = self.model_dump(exclude_none=True, exclude={"metadata"})
        if self.metadata:
            base.update(self.metadata)
        return base
