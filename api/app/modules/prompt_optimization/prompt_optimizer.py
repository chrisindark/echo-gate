from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from app.core.config import config
from app.core.helpers import count_tokens
from app.modules.prompt_optimization.prompt_optimization_schema import (
    PromptOptimizationConfig,
    PromptOptimizationResult,
)

if TYPE_CHECKING:
    from app.modules.chat.chat_schema import ChatCompletionRequest, ChatMessage

logger = logging.getLogger(__name__)

# Pattern to isolate markdown fenced code blocks (``` or ~~~)
_FENCED_CODE_BLOCK_PATTERN = re.compile(
    r"(```[\s\S]*?```|~~~[\s\S]*?~~~)", re.MULTILINE
)
# Pattern to isolate inline code (`code`)
_INLINE_CODE_PATTERN = re.compile(r"(`[^`\n]+`)")


class PromptOptimizer:
    """Pre-processing engine to reduce prompt token footprint rule-based and losslessly."""

    def collapse_whitespace(self, text: str | None) -> str | None:
        """Collapse repetitive spaces, tabs, and duplicate newline runs while preserving code blocks.

        - Outside code: runs of spaces and tabs [ \\t]+ collapse to ' ', 3+ newlines collapse to 2.
        - Inside fenced code blocks: indentation is preserved; trailing whitespace stripped; 3+ newlines collapse to 2.
        """
        if not text:
            return text

        # Split text into code blocks and prose segments
        segments = _FENCED_CODE_BLOCK_PATTERN.split(text)
        cleaned_segments: list[str] = []

        for seg in segments:
            if not seg:
                continue
            is_code_block = (seg.startswith("```") and seg.endswith("```")) or (
                seg.startswith("~~~") and seg.endswith("~~~")
            )

            if is_code_block:
                # Inside fenced code block: preserve indentation, strip trailing spaces, cap blank lines
                lines = seg.split("\n")
                stripped_lines = [line.rstrip() for line in lines]
                code_text = "\n".join(stripped_lines)
                code_text = re.sub(r"\n{3,}", "\n\n", code_text)
                cleaned_segments.append(code_text)
            else:
                # Outside fenced code block: process lines while protecting inline code snippets
                lines = seg.split("\n")
                cleaned_lines: list[str] = []
                for line in lines:
                    sub_parts = _INLINE_CODE_PATTERN.split(line)
                    cleaned_parts: list[str] = []
                    for part in sub_parts:
                        if part.startswith("`") and part.endswith("`"):
                            cleaned_parts.append(part)
                        else:
                            cleaned_parts.append(re.sub(r"[ \t]+", " ", part))
                    recombined = "".join(cleaned_parts).strip()
                    cleaned_lines.append(recombined)

                cleaned_seg = "\n".join(cleaned_lines)
                cleaned_seg = re.sub(r"\n{3,}", "\n\n", cleaned_seg)
                cleaned_segments.append(cleaned_seg)

        result = "".join(cleaned_segments).strip()
        return result

    def _group_into_turns(self, messages: list[ChatMessage]) -> list[list[ChatMessage]]:
        """Group messages into atomic turns preserving tool call and response pairings."""
        turns: list[list[ChatMessage]] = []
        i = 0
        while i < len(messages):
            msg = messages[i]
            if msg.role == "assistant" and msg.tool_calls:
                turn_msgs = [msg]
                j = i + 1
                while j < len(messages) and messages[j].role in ("tool", "function"):
                    turn_msgs.append(messages[j])
                    j += 1
                turns.append(turn_msgs)
                i = j
            else:
                turns.append([msg])
                i += 1
        return turns

    def _flatten_turns(self, turns: list[list[ChatMessage]]) -> list[ChatMessage]:
        """Flatten atomic turns back into a flat list of messages."""
        flattened: list[ChatMessage] = []
        for t in turns:
            flattened.extend(t)
        return flattened

    def deduplicate_session(self, messages: list[ChatMessage]) -> list[ChatMessage]:
        """Detect and remove exact-match duplicate turns in multi-turn sessions.

        Maintains conversation semantics:
        - Never removes the final message/turn (the active prompt).
        - Preserves the initial system prompt.
        - Preserves tool call and tool response pairings.
        """
        if len(messages) <= 2:
            return messages

        turns = self._group_into_turns(messages)
        if len(turns) <= 2:
            return messages

        def _turn_fingerprint(turn: list[ChatMessage]) -> tuple:
            sigs = []
            for m in turn:
                tc_sig = tuple(
                    (tc.id or "", tc.function.name or "", tc.function.arguments or "")
                    for tc in (m.tool_calls or [])
                )
                sigs.append(
                    (
                        m.role or "",
                        m.content or "",
                        m.name or "",
                        m.tool_call_id or "",
                        tc_sig,
                    )
                )
            return tuple(sigs)

        # 1. Consecutive identical turns deduplication
        deduped_turns: list[list[ChatMessage]] = []
        for idx, turn in enumerate(turns):
            if idx == len(turns) - 1:
                # Always preserve the final active turn
                deduped_turns.append(turn)
                continue
            if idx > 0:
                prev_fp = _turn_fingerprint(deduped_turns[-1])
                curr_fp = _turn_fingerprint(turn)
                if prev_fp == curr_fp:
                    # Duplicate consecutive turn, skip
                    continue
            deduped_turns.append(turn)

        # 2. Consecutive duplicate turn pairs (e.g., [User, Assistant] repeated verbatim)
        if len(deduped_turns) >= 5:
            filtered_turns: list[list[ChatMessage]] = []
            idx = 0
            n = len(deduped_turns)
            while idx < n:
                if idx < n - 4:
                    pair_a = (
                        _turn_fingerprint(deduped_turns[idx]),
                        _turn_fingerprint(deduped_turns[idx + 1]),
                    )
                    pair_b = (
                        _turn_fingerprint(deduped_turns[idx + 2]),
                        _turn_fingerprint(deduped_turns[idx + 3]),
                    )
                    if pair_a == pair_b:
                        # Skip the earlier duplicate pair
                        idx += 2
                        continue
                filtered_turns.append(deduped_turns[idx])
                idx += 1
            deduped_turns = filtered_turns

        return self._flatten_turns(deduped_turns)

    def reduce_context(
        self,
        messages: list[ChatMessage],
        keep_last_n_turns: int = 5,
        strategy: str = "condense",
        max_turn_chars: int = 150,
    ) -> list[ChatMessage]:
        """Conversation Context Reduction (CCR - Phase B).

        Retains the initial system prompt and the last N conversational turns in full fidelity,
        while condensing or omitting intermediate turns to avoid context bloat.
        """
        turns = self._group_into_turns(messages)
        if len(turns) <= 2:
            return messages

        # Separate initial system turns
        system_turns: list[list[ChatMessage]] = []
        i = 0
        while i < len(turns) and all(m.role == "system" for m in turns[i]):
            system_turns.append(turns[i])
            i += 1

        conv_turns = turns[i:]
        if len(conv_turns) <= keep_last_n_turns:
            return messages

        recent_turns = conv_turns[-keep_last_n_turns:]
        intermediate_turns = conv_turns[:-keep_last_n_turns]

        from app.modules.chat.chat_schema import ChatMessage

        if strategy == "drop":
            marker = ChatMessage(
                role="system",
                content=f"[... {len(intermediate_turns)} earlier conversational turns omitted for context reduction ...]",
            )
            reduced_turns = system_turns + [[marker]] + recent_turns
        else:
            # "condense" strategy
            summary_lines: list[str] = []
            for t in intermediate_turns:
                for m in t:
                    role_label = m.role or "unknown"
                    if m.role == "assistant" and m.tool_calls:
                        calls_str = ", ".join(
                            tc.function.name
                            for tc in m.tool_calls
                            if tc.function and tc.function.name
                        )
                        snippet = f"Called tool(s): {calls_str}"
                    else:
                        snippet = (m.content or "").strip()
                        if len(snippet) > max_turn_chars:
                            snippet = snippet[:max_turn_chars].rstrip() + "..."
                    if snippet:
                        summary_lines.append(f"{role_label}: {snippet}")

            briefing = "\n".join(summary_lines)
            marker = ChatMessage(
                role="system",
                content=f"[Context briefing ({len(intermediate_turns)} earlier turns)]:\n{briefing}",
            )
            reduced_turns = system_turns + [[marker]] + recent_turns

        return self._flatten_turns(reduced_turns)

    def trim_headroom(
        self,
        messages: list[ChatMessage],
        model: str,
        max_tokens: int | None = None,
        target_tokens: int | None = None,
        buffer_tokens: int = 100,
    ) -> list[ChatMessage]:
        """Headroom Trimmer (Phase B): dynamically prunes oldest turns to guarantee prompt fit within model context window.

        allowed_prompt_tokens = context_limit - completion_budget - buffer_tokens
        """
        from app.modules.llm.model_registry import model_registry

        if target_tokens is not None:
            context_limit = target_tokens
        else:
            context_limit = model_registry.get_context_window(model)

        completion_budget = (
            max_tokens
            if max_tokens is not None
            else getattr(config, "ESTIMATE_DEFAULT_COMPLETION_TOKENS", 256)
        )
        allowed_prompt_tokens = max(
            100, context_limit - completion_budget - buffer_tokens
        )

        def _count_total_tokens(msgs: list[ChatMessage]) -> int:
            return sum(count_tokens(m.content or "", model) for m in msgs)

        current_tokens = _count_total_tokens(messages)
        if current_tokens <= allowed_prompt_tokens:
            return messages

        turns = self._group_into_turns(messages)
        if not turns:
            return messages

        # Separate initial system turns and the final active turn
        system_turns: list[list[ChatMessage]] = []
        i = 0
        while i < len(turns) - 1 and all(m.role == "system" for m in turns[i]):
            system_turns.append(turns[i])
            i += 1

        final_turn = turns[-1]
        intermediate_turns = turns[i:-1]

        # Iteratively prune oldest intermediate turns
        while intermediate_turns:
            candidate_turns = system_turns + intermediate_turns + [final_turn]
            if (
                _count_total_tokens(self._flatten_turns(candidate_turns))
                <= allowed_prompt_tokens
            ):
                return self._flatten_turns(candidate_turns)
            intermediate_turns.pop(0)

        # If intermediate turns are exhausted, check system + final turn
        candidate_turns = system_turns + [final_turn]
        flat = self._flatten_turns(candidate_turns)
        if _count_total_tokens(flat) <= allowed_prompt_tokens:
            return flat

        # If even system_turns + final_turn exceeds allowed_prompt_tokens, truncate final turn content
        sys_tokens = _count_total_tokens(self._flatten_turns(system_turns))
        budget_for_final = max(50, allowed_prompt_tokens - sys_tokens)

        truncated_final: list[ChatMessage] = []
        for m in final_turn:
            m_copy = m.model_copy()
            if m_copy.content:
                m_tokens = count_tokens(m_copy.content, model)
                if m_tokens > budget_for_final:
                    allowed_chars = max(50, budget_for_final * 3)
                    m_copy.content = (
                        m_copy.content[:allowed_chars].rstrip()
                        + "\n[... Content truncated to fit within model context window ...]"
                    )
            truncated_final.append(m_copy)

        return self._flatten_turns(system_turns + [truncated_final])

    def tabularize_json_array(
        self, content: str | None, min_rows: int = 2
    ) -> str | None:
        """Rewrite large uniform JSON arrays from tool calls into compact markdown pipe tables.

        Only rewrites when the tabular representation is strictly smaller than the original JSON.
        """
        if not content or not isinstance(content, str):
            return content

        stripped = content.strip()
        if not (stripped.startswith("[") or stripped.startswith("{")):
            return content

        try:
            parsed = json.loads(stripped)
        except (ValueError, TypeError):
            return content

        rows: list[dict[str, Any]] | None = None
        wrapper_key: str | None = None
        other_metadata: dict[str, Any] = {}

        if isinstance(parsed, list):
            if all(isinstance(r, dict) for r in parsed):
                rows = parsed
        elif isinstance(parsed, dict):
            # Check if there is a single key with list[dict], or a primary items list
            list_keys = [
                k
                for k, v in parsed.items()
                if isinstance(v, list) and all(isinstance(item, dict) for item in v)
            ]
            if len(list_keys) == 1:
                wrapper_key = list_keys[0]
                rows = parsed[wrapper_key]
                other_metadata = {k: v for k, v in parsed.items() if k != wrapper_key}

        if not rows or len(rows) < min_rows:
            return content

        # Gather headers across all rows in order of first appearance
        headers: list[str] = []
        for r in rows:
            for k in r.keys():
                if k not in headers:
                    headers.append(k)

        if not headers:
            return content

        # Build Markdown pipe table
        header_line = "| " + " | ".join(headers) + " |"
        sep_line = "| " + " | ".join("---" for _ in headers) + " |"
        data_lines: list[str] = []

        for r in rows:
            cells = []
            for h in headers:
                val = r.get(h)
                if val is None:
                    cell_str = ""
                elif isinstance(val, bool):
                    cell_str = "true" if val else "false"
                elif isinstance(val, (dict, list)):
                    cell_str = json.dumps(val, separators=(",", ":"))
                else:
                    cell_str = str(val)
                # Escape pipes and replace newlines
                cell_str = (
                    cell_str.replace("|", r"\|").replace("\r\n", " ").replace("\n", " ")
                )
                cells.append(cell_str)
            data_lines.append("| " + " | ".join(cells) + " |")

        table = "\n".join([header_line, sep_line] + data_lines)

        if wrapper_key and other_metadata:
            meta_str = ", ".join(f"{k}={v}" for k, v in other_metadata.items())
            result_str = f"metadata: {meta_str}\n**{wrapper_key}**:\n{table}"
        elif wrapper_key:
            result_str = f"**{wrapper_key}**:\n{table}"
        else:
            result_str = table

        # Strict invariant: Only return table if it reduces character footprint
        if len(result_str) < len(content):
            return result_str
        return content

    def optimize(
        self,
        request: ChatCompletionRequest,
        options: PromptOptimizationConfig | None = None,
    ) -> tuple[ChatCompletionRequest, PromptOptimizationResult]:
        """Execute the prompt optimization pipeline on a ChatCompletionRequest.

        Returns (optimized_request, result_metrics).
        Fail-open: any unexpected error logs a warning and returns the original request unmodified.
        """
        try:
            # 1. Resolve configuration
            cfg: PromptOptimizationConfig
            if options is not None:
                cfg = options
            elif isinstance(request.prompt_optimization, PromptOptimizationConfig):
                cfg = request.prompt_optimization
            elif isinstance(request.prompt_optimization, dict):
                cfg = PromptOptimizationConfig(**request.prompt_optimization)
            elif request.prompt_optimization is True:
                cfg = PromptOptimizationConfig(
                    enabled=True,
                    whitespace=config.PROMPT_OPTIMIZATION_WHITESPACE,
                    session_dedup=config.PROMPT_OPTIMIZATION_SESSION_DEDUP,
                    json_table=config.PROMPT_OPTIMIZATION_JSON_TABLE,
                    json_table_min_rows=config.PROMPT_OPTIMIZATION_JSON_TABLE_MIN_ROWS,
                    ccr=config.PROMPT_OPTIMIZATION_CCR_ENABLED,
                    ccr_keep_last_n_turns=config.PROMPT_OPTIMIZATION_CCR_KEEP_LAST_N_TURNS,
                    ccr_strategy=config.PROMPT_OPTIMIZATION_CCR_STRATEGY,
                    headroom_trim=config.PROMPT_OPTIMIZATION_HEADROOM_ENABLED,
                    headroom_buffer_tokens=config.PROMPT_OPTIMIZATION_HEADROOM_BUFFER,
                )
            elif request.prompt_optimization is False:
                cfg = PromptOptimizationConfig(enabled=False)
            else:
                cfg = PromptOptimizationConfig(
                    enabled=config.PROMPT_OPTIMIZATION_ENABLED,
                    whitespace=config.PROMPT_OPTIMIZATION_WHITESPACE,
                    session_dedup=config.PROMPT_OPTIMIZATION_SESSION_DEDUP,
                    json_table=config.PROMPT_OPTIMIZATION_JSON_TABLE,
                    json_table_min_rows=config.PROMPT_OPTIMIZATION_JSON_TABLE_MIN_ROWS,
                    ccr=config.PROMPT_OPTIMIZATION_CCR_ENABLED,
                    ccr_keep_last_n_turns=config.PROMPT_OPTIMIZATION_CCR_KEEP_LAST_N_TURNS,
                    ccr_strategy=config.PROMPT_OPTIMIZATION_CCR_STRATEGY,
                    headroom_trim=config.PROMPT_OPTIMIZATION_HEADROOM_ENABLED,
                    headroom_buffer_tokens=config.PROMPT_OPTIMIZATION_HEADROOM_BUFFER,
                )

            if not cfg.enabled:
                return request, PromptOptimizationResult(applied=False)

            # Compute initial metrics
            orig_chars = sum(len(m.content or "") for m in request.messages)
            orig_tokens = sum(
                count_tokens(m.content or "", request.model) for m in request.messages
            )

            messages = [m.model_copy() for m in request.messages]
            strategies_applied: list[str] = []

            # 2. Tool JSON tabularization
            if cfg.json_table:
                table_applied = False
                for m in messages:
                    if (m.role in ("tool", "function") or m.tool_call_id) and m.content:
                        tabular = self.tabularize_json_array(
                            m.content, min_rows=cfg.json_table_min_rows
                        )
                        if tabular != m.content:
                            m.content = tabular
                            table_applied = True
                if table_applied:
                    strategies_applied.append("json-table")

            # 3. Session deduplication
            if cfg.session_dedup:
                deduped = self.deduplicate_session(messages)
                if len(deduped) != len(messages) or any(
                    d.content != o.content
                    for d, o in zip(deduped, messages, strict=False)
                ):
                    messages = deduped
                    strategies_applied.append("session-dedup")

            # 4. Whitespace collapse
            if cfg.whitespace:
                ws_applied = False
                for m in messages:
                    if m.content:
                        collapsed = self.collapse_whitespace(m.content)
                        if collapsed != m.content:
                            m.content = collapsed
                            ws_applied = True
                if ws_applied:
                    strategies_applied.append("whitespace")

            # 5. Conversation Context Reduction (CCR - Phase B)
            if cfg.ccr:
                reduced = self.reduce_context(
                    messages,
                    keep_last_n_turns=cfg.ccr_keep_last_n_turns,
                    strategy=cfg.ccr_strategy,
                    max_turn_chars=cfg.ccr_max_turn_chars,
                )
                if len(reduced) != len(messages) or any(
                    r.content != m.content
                    for r, m in zip(reduced, messages, strict=False)
                ):
                    messages = reduced
                    strategies_applied.append("ccr")

            # 6. Target Model Headroom Trimmer (Phase B)
            if cfg.headroom_trim:
                trimmed = self.trim_headroom(
                    messages,
                    model=request.model,
                    max_tokens=request.max_tokens,
                    target_tokens=cfg.headroom_target_tokens,
                    buffer_tokens=cfg.headroom_buffer_tokens,
                )
                if len(trimmed) != len(messages) or any(
                    t.content != m.content
                    for t, m in zip(trimmed, messages, strict=False)
                ):
                    messages = trimmed
                    strategies_applied.append("headroom-trim")

            opt_chars = sum(len(m.content or "") for m in messages)
            opt_tokens = sum(
                count_tokens(m.content or "", request.model) for m in messages
            )

            if strategies_applied and (
                opt_chars < orig_chars or opt_tokens < orig_tokens
            ):
                # Build cloned request with optimized messages
                optimized_req = request.model_copy(update={"messages": messages})
                # Preserve private attributes
                optimized_req.tenant_id = request.tenant_id
                optimized_req.user_id = request.user_id

                result = PromptOptimizationResult(
                    applied=True,
                    original_tokens=orig_tokens,
                    optimized_tokens=opt_tokens,
                    tokens_saved=max(0, orig_tokens - opt_tokens),
                    original_chars=orig_chars,
                    optimized_chars=opt_chars,
                    chars_saved=max(0, orig_chars - opt_chars),
                    strategies=strategies_applied,
                )
                logger.info(
                    "Prompt optimization applied (%s): saved %d chars, ~%d tokens",
                    ",".join(strategies_applied),
                    result.chars_saved,
                    result.tokens_saved,
                )
                return optimized_req, result

            return request, PromptOptimizationResult(
                applied=False,
                original_tokens=orig_tokens,
                optimized_tokens=orig_tokens,
                original_chars=orig_chars,
                optimized_chars=orig_chars,
            )

        except Exception as e:  # Fail-open
            logger.warning(
                "Prompt optimization failed (fail-open): %s", e, exc_info=True
            )
            return request, PromptOptimizationResult(applied=False)
