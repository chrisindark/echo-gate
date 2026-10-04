import json
import re


class InstructionVerifier:
    def __init__(self):
        # Pre-compile constraint patterns
        self.re_json = re.compile(r"\b(json|json format|valid json)\b", re.IGNORECASE)
        self.re_table = re.compile(
            r"\b(markdown table|in a table|table format|tabular format|as a table)\b",
            re.IGNORECASE,
        )
        self.re_bullets = re.compile(
            r"\b(bullet points|bulleted list|bullet list|in a list|as a list|list format)\b",
            re.IGNORECASE,
        )

        num_pattern = r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten)"

        self.re_word_limit = re.compile(
            rf"(?:under|less than|max(?:imum)?|in)\s+{num_pattern}\s+words",
            re.IGNORECASE,
        )
        self.re_exact_count = re.compile(
            rf"\b(?:exactly|list|provide|give|write|generate)\s+(?:me\s+)?{num_pattern}\s+(?:bullet points|points|items|reasons|tips)\b",
            re.IGNORECASE,
        )
        self.re_sentence_limit = re.compile(
            rf"(?:under|less than|max(?:imum)?|in)\s+{num_pattern}\s+sentences?",
            re.IGNORECASE,
        )
        self.re_exact_sentence_count = re.compile(
            rf"\b(?:exactly|list|provide|give|write|generate)\s+(?:me\s+)?{num_pattern}\s+sentences?\b",
            re.IGNORECASE,
        )

    def _parse_number(self, val: str) -> int:
        words = {
            "one": 1,
            "two": 2,
            "three": 3,
            "four": 4,
            "five": 5,
            "six": 6,
            "seven": 7,
            "eight": 8,
            "nine": 9,
            "ten": 10,
        }
        if val.isdigit():
            return int(val)
        return words.get(val.lower(), 0)

    def verify_instruction(
        self, query: str, candidates: list[str], json_only: bool = False
    ) -> list[float]:
        if not candidates:
            return []

        # 1. Detect constraints in query
        active_checks = []

        if self.re_json.search(query):

            def is_valid_json(text: str) -> bool:
                try:
                    clean = re.sub(
                        r"^```json\s*|\s*```$", "", text.strip(), flags=re.MULTILINE
                    )
                    json.loads(clean)
                    return True
                except Exception:
                    return False

            active_checks.append(is_valid_json)

        if not json_only:
            if self.re_table.search(query):
                active_checks.append(
                    lambda t: bool(re.search(r"\|.*\|.*\n\|[-:\s|]+\|", t))
                )

            if self.re_bullets.search(query):
                active_checks.append(
                    lambda t: bool(re.search(r"(?m)^[\s]*[-*•\d+\.]\s+", t))
                )

            word_match = self.re_word_limit.search(query)
            if word_match:
                max_words = self._parse_number(word_match.group(1)) * 1.10
                active_checks.append(lambda t: len(t.split()) <= max_words)

            count_match = self.re_exact_count.search(query)
            if count_match:
                target = self._parse_number(count_match.group(1))
                active_checks.append(
                    lambda t: (
                        abs(len(re.findall(r"(?m)^[\s]*[-*•\d+\.]\s+", t)) - target)
                        <= 1
                    )
                )

            sentence_limit_match = self.re_sentence_limit.search(query)
            if sentence_limit_match:
                max_sentences = self._parse_number(sentence_limit_match.group(1))
                active_checks.append(
                    lambda t: (
                        len([s for s in re.split(r"[.!?]+", t) if s.strip()])
                        <= max_sentences
                    )
                )

            sentence_exact_match = self.re_exact_sentence_count.search(query)
            if sentence_exact_match:
                target_sentences = self._parse_number(sentence_exact_match.group(1))
                active_checks.append(
                    lambda t: (
                        len([s for s in re.split(r"[.!?]+", t) if s.strip()])
                        == target_sentences
                    )
                )

        # 2. Default rule: No constraints in query -> perfect 1.0 for all candidates
        if not active_checks:
            return [1.0] * len(candidates)

        # 3. Evaluate active checks per candidate
        scores = []
        num_checks = len(active_checks)
        for cand in candidates:
            passed = sum(1 for check in active_checks if check(cand))
            scores.append(round(passed / num_checks, 2))

        return scores
