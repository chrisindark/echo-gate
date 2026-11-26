'use client';

import {
  DEFAULT_MAX_TOKENS,
  MAX_TOKENS_HARD_CAP,
  clampMaxTokens,
  formatTokenCount,
} from '@/lib/tokens';
import { Flex, Text, TextField } from '@radix-ui/themes';
import { useState } from 'react';

interface MaxTokensFieldProps {
  value: number;
  onChange: (value: number) => void;
  modelContextWindow?: number;
}

export function MaxTokensField({
  value,
  onChange,
  modelContextWindow,
}: MaxTokensFieldProps) {
  const [draftText, setDraftText] = useState<string | null>(null);
  const displayValue = draftText !== null ? draftText : String(value);

  const handleChange = (raw: string) => {
    if (raw === '') {
      setDraftText('');
      return;
    }
    const parsed = Number.parseInt(raw, 10);
    if (Number.isNaN(parsed)) {
      return;
    }
    if (parsed > MAX_TOKENS_HARD_CAP) {
      setDraftText(String(MAX_TOKENS_HARD_CAP));
      onChange(MAX_TOKENS_HARD_CAP);
      return;
    }
    setDraftText(raw);
    if (parsed >= 1) {
      onChange(parsed);
    }
  };

  const handleBlur = () => {
    const parsed = draftText !== null ? Number.parseInt(draftText, 10) : value;
    const clamped = Number.isNaN(parsed)
      ? DEFAULT_MAX_TOKENS
      : clampMaxTokens(parsed);
    onChange(clamped);
    setDraftText(null);
  };

  const exceedsModelWindow =
    Boolean(modelContextWindow && modelContextWindow > 0) &&
    value > (modelContextWindow ?? 0);

  return (
    <Flex direction="column" gap="1">
      <Flex justify="between" align="center">
        <Text as="label" size="2" weight="bold">
          Max Tokens
        </Text>
        <Text size="1" color="gray" className="font-mono">
          Max: {formatTokenCount(MAX_TOKENS_HARD_CAP)}
        </Text>
      </Flex>
      <TextField.Root
        type="number"
        min={1}
        max={MAX_TOKENS_HARD_CAP}
        value={displayValue}
        onChange={(e) => handleChange(e.target.value)}
        onBlur={handleBlur}
      />
      {exceedsModelWindow && modelContextWindow && (
        <Text size="1" color="amber">
          Exceeds selected model&apos;s {formatTokenCount(modelContextWindow)}{' '}
          context window; the provider may reject or cap output.
        </Text>
      )}
    </Flex>
  );
}
