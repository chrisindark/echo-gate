export const DEFAULT_MAX_TOKENS = 8192;
export const MAX_TOKENS_HARD_CAP = 2_000_000;

export const CONTEXT_WARNING_RATIO = 0.7;
export const CONTEXT_CRITICAL_RATIO = 0.9;

const MESSAGE_OVERHEAD_TOKENS = 4;
const REPLY_PRIMING_TOKENS = 3;
const CHARS_PER_TOKEN = 4;

export function clampMaxTokens(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_MAX_TOKENS;
  return Math.min(MAX_TOKENS_HARD_CAP, Math.max(1, Math.trunc(value)));
}

export function formatTokenCount(tokens: number): string {
  const safe = Math.max(0, Math.round(tokens));
  if (safe >= 1_000_000) {
    const millions = safe / 1_000_000;
    return `${Number.isInteger(millions) ? millions : millions.toFixed(2).replace(/\.?0+$/, '')}M`;
  }
  if (safe >= 1000) {
    const thousands = safe / 1000;
    if (thousands >= 100) {
      return `${Math.round(thousands)}k`;
    }
    return `${thousands.toFixed(1).replace(/\.0$/, '')}k`;
  }
  return String(safe);
}

export function estimateMessageTokens(
  messages: Array<{ role: string; content: string }>,
  draftPrompt?: string
): number {
  const trimmedDraft = draftPrompt?.trim() ?? '';
  if (messages.length === 0 && !trimmedDraft) {
    return 0;
  }

  let total = REPLY_PRIMING_TOKENS;
  for (const msg of messages) {
    total += MESSAGE_OVERHEAD_TOKENS;
    total += Math.ceil((msg.role?.length ?? 0) / CHARS_PER_TOKEN);
    if (msg.content) {
      total += Math.max(1, Math.ceil(msg.content.length / CHARS_PER_TOKEN));
    }
  }

  if (trimmedDraft) {
    total += MESSAGE_OVERHEAD_TOKENS;
    total += 1;
    total += Math.max(1, Math.ceil(trimmedDraft.length / CHARS_PER_TOKEN));
  }

  return total;
}

export type ContextHealthLevel = 'normal' | 'warning' | 'critical';

export interface ContextUsageStatus {
  estimatedTokens: number;
  contextWindow: number;
  ratio: number;
  percent: number;
  level: ContextHealthLevel;
}

export function evaluateContextUsage(
  estimatedTokens: number,
  contextWindow?: number
): ContextUsageStatus | null {
  if (!contextWindow || contextWindow <= 0) {
    return null;
  }
  const ratio = estimatedTokens / contextWindow;
  const percent = Math.round(ratio * 100);
  let level: ContextHealthLevel = 'normal';
  if (ratio >= CONTEXT_CRITICAL_RATIO) {
    level = 'critical';
  } else if (ratio >= CONTEXT_WARNING_RATIO) {
    level = 'warning';
  }
  return {
    estimatedTokens,
    contextWindow,
    ratio,
    percent,
    level,
  };
}
