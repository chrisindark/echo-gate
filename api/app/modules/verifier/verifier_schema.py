from pydantic import BaseModel


class VerificationRequest(BaseModel):
    query: str
    candidates: list[str]


class CandidateScore(BaseModel):
    candidate: str
    score: float


class VerificationResponse(BaseModel):
    results: list[CandidateScore]
