"""Pre-routing PII scrubbing schemas."""

from dataclasses import dataclass, field


@dataclass
class PiiScrubbingResult:
    """Summary of Stage 0 PII scrubbing applied to a request."""

    applied: bool = False
    entity_counts: dict[str, int] = field(default_factory=dict)

    @property
    def total_redactions(self) -> int:
        return sum(self.entity_counts.values())

    def to_header_value(self) -> str:
        if not self.applied or not self.entity_counts:
            return ""
        parts = [
            f"{kind}={count}" for kind, count in sorted(self.entity_counts.items())
        ]
        return ",".join(parts)
