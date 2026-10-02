from fastapi import APIRouter, Depends

from app.core.dependencies import DependencyContainer
from app.modules.verifiers.cross_encoder_verifier_service import (
    CrossEncoderVerifierService,
)
from app.modules.verifiers.verifier_schema import (
    CandidateScore,
    VerificationRequest,
    VerificationResponse,
)

api_v1_router = APIRouter(prefix="/api/v1/verifiers", tags=["verifiers"])


def get_verifier_service():
    return DependencyContainer.get_cross_encoder_verifier_service()


@api_v1_router.post("/verify", response_model=VerificationResponse)
async def verify_candidates(
    request: VerificationRequest,
    verifier_service: CrossEncoderVerifierService = Depends(get_verifier_service),
):
    scores = verifier_service.verify(request.query, request.candidates)
    results = [
        CandidateScore(candidate=candidate, score=score)
        for candidate, score in zip(request.candidates, scores)
    ]
    # Sort by score descending
    results.sort(key=lambda x: x.score, reverse=True)
    return VerificationResponse(results=results)
