'use client';

import { Checkbox, Flex, Select, Text, TextField } from '@radix-ui/themes';
import { EyeOff, ShieldAlert, ShieldCheck } from 'lucide-react';

export type GuardrailUiMode = 'off' | 'inject' | 'block';

export function buildGuardrailsPayload(
  mode: GuardrailUiMode,
  instruction: string
): Record<string, unknown> | string | undefined {
  if (mode === 'off') {
    return undefined;
  }
  if (mode === 'block') {
    return 'block';
  }
  const trimmed = instruction.trim();
  return trimmed
    ? { enabled: true, mode: 'inject', instruction: trimmed }
    : 'inject';
}

interface PrivacyDirectivesFieldProps {
  disableLog: boolean;
  onDisableLogChange: (value: boolean) => void;
  redactPii: boolean;
  onRedactPiiChange: (value: boolean) => void;
  guardrailsMode: GuardrailUiMode;
  onGuardrailsModeChange: (value: GuardrailUiMode) => void;
  guardrailsInstruction: string;
  onGuardrailsInstructionChange: (value: string) => void;
  idPrefix?: string;
}

export function PrivacyDirectivesField({
  disableLog,
  onDisableLogChange,
  redactPii,
  onRedactPiiChange,
  guardrailsMode,
  onGuardrailsModeChange,
  guardrailsInstruction,
  onGuardrailsInstructionChange,
  idPrefix = 'privacy',
}: PrivacyDirectivesFieldProps) {
  const disableLogId = `${idPrefix}-disable-log-check`;
  const redactPiiId = `${idPrefix}-redact-pii-check`;
  const guardrailsId = `${idPrefix}-guardrails-mode`;

  return (
    <div className="space-y-3 pt-1">
      <div className="space-y-1">
        <Flex align="center" gap="2">
          <Checkbox
            checked={disableLog}
            onCheckedChange={(val) => onDisableLogChange(Boolean(val))}
            id={disableLogId}
          />
          <Text
            as="label"
            size="2"
            weight="bold"
            htmlFor={disableLogId}
            className="cursor-pointer select-none flex items-center gap-1.5"
          >
            <EyeOff className="w-4 h-4 text-rose-400" />
            Zero-Retention Mode
          </Text>
        </Flex>
        <Text size="1" color="gray" className="block pl-6 leading-snug">
          Skip Postgres request/usage logs and Redis/Qdrant caching.
        </Text>
      </div>

      <div className="space-y-1">
        <Flex align="center" gap="2">
          <Checkbox
            checked={redactPii}
            onCheckedChange={(val) => onRedactPiiChange(Boolean(val))}
            id={redactPiiId}
          />
          <Text
            as="label"
            size="2"
            weight="bold"
            htmlFor={redactPiiId}
            className="cursor-pointer select-none flex items-center gap-1.5"
          >
            <ShieldCheck className="w-4 h-4 text-sky-400" />
            Pre-Routing PII Scrubber
          </Text>
        </Flex>
        <Text size="1" color="gray" className="block pl-6 leading-snug">
          Mask email, phone, card (Luhn), SSN &amp; IBAN before routing.
        </Text>
      </div>

      <div className="space-y-1.5">
        <Flex direction="column" gap="1">
          <Text
            as="label"
            size="2"
            weight="bold"
            htmlFor={guardrailsId}
            className="flex items-center gap-1.5"
          >
            <ShieldAlert className="w-4 h-4 text-violet-400" />
            Policy Guardrails
          </Text>
          <Select.Root
            value={guardrailsMode}
            onValueChange={(val) =>
              onGuardrailsModeChange(val as GuardrailUiMode)
            }
          >
            <Select.Trigger id={guardrailsId} className="w-full" />
            <Select.Content>
              <Select.Item value="off">Off (Server Default)</Select.Item>
              <Select.Item value="inject">
                Soft-Steering (Inject Policy)
              </Select.Item>
              <Select.Item value="block">
                Hard-Blocking (403 Reject)
              </Select.Item>
            </Select.Content>
          </Select.Root>
        </Flex>
        {guardrailsMode === 'inject' && (
          <TextField.Root
            size="2"
            placeholder="Optional policy instruction appended to system prompt..."
            value={guardrailsInstruction}
            onChange={(e) => onGuardrailsInstructionChange(e.target.value)}
            aria-label="Guardrail policy instruction"
          />
        )}
        <Text size="1" color="gray" className="block leading-snug">
          {guardrailsMode === 'block'
            ? 'Abort policy violations (403) before cache lookup or dispatch.'
            : guardrailsMode === 'inject'
              ? 'Append safety policy guidance to the system prompt without failing.'
              : 'Use gateway configuration defaults for policy guardrails.'}
        </Text>
      </div>
    </div>
  );
}
