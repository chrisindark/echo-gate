from fastapi import APIRouter, Depends

from app.core.dependencies import DependencyContainer
from app.core.responses import BaseAPIResponse
from app.modules.verifier.cross_encoder_service import CrossEncoderService
from app.modules.verifier.verifier_schema import (
    CandidateScore,
    VerificationRequest,
    VerificationResponse,
)

api_v1_router = APIRouter(prefix="/api/v1/verifier", tags=["verifier"])


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
    scores = verifier_service.rerank_predict(pairs)
    results = [
        CandidateScore(candidate=candidate, score=score)
        for candidate, score in zip(request.candidates, scores, strict=False)
    ]
    # Sort by score descending
    results.sort(key=lambda x: x.score, reverse=True)
    return BaseAPIResponse.success_response(VerificationResponse(results=results))
