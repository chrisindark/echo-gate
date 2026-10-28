from fastapi import APIRouter, Depends, HTTPException

from app.core.dependencies import DependencyContainer
from app.core.responses import BaseAPIResponse
from app.modules.verifier.cross_encoder_service import (
    CrossEncoderService,
    InferenceUnavailable,
)
from app.modules.verifier.verifier_schema import (
    CandidateScore,
    VerificationRequest,
    VerificationResponse,
)

api_v1_router = APIRouter(prefix="/api/v1/verifier", tags=["Verifier"])


def get_verifier_service():
    return DependencyContainer.get_cross_encoder_service()


@api_v1_router.post(
    "/predict/rerank", response_model=BaseAPIResponse[VerificationResponse]
)
async def rerank_predict_candidates(
    request: VerificationRequest,
    verifier_service: CrossEncoderService = Depends(get_verifier_service),
):
    pairs = [(request.query, candidate) for candidate in request.candidates]
    try:
        scores = await verifier_service.rerank_predict_async(pairs)
    except InferenceUnavailable as exc:
        # This is an operator endpoint, not the cache path: an unavailable
        # engine is a real error to report rather than an empty result to hide.
        raise HTTPException(
            status_code=503, detail=f"Inference engine unavailable: {exc}"
        ) from exc
    results = [
        CandidateScore(candidate=candidate, score=score)
        for candidate, score in zip(request.candidates, scores, strict=False)
    ]
    # Sort by score descending
    results.sort(key=lambda x: x.score, reverse=True)
    return BaseAPIResponse.success_response(VerificationResponse(results=results))
