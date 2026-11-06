export const PROVIDERS = {
  ollama: [
    'qwen3:8b',
    'qwen2.5-coder:14b',
    'qwen2.5-coder:1.5b',
    'qwen2.5-coder:3b',
    'qwen2.5-coder:7b',
  ],
  gemini: [
    'gemini-3.1-flash-lite',
    'gemini-3.5-flash-lite',
    'gemini-3.8-flash',
  ],
  'google-genai': [
    'gemini-3.1-flash-lite',
    'gemini-3.5-flash-lite',
    'gemini-3.8-flash',
  ],
  openai: ['gpt-4o-mini'],
  groq: ['openai/gpt-oss-20b', 'openai/gpt-oss-safeguard-20b'],
  openrouter: [
    'qwen/qwen3.8-27b:free',
    'google/gemma-4-26b-a4b-it:free',
    'google/gemma-4-31b-it:free',
  ],
  claude: [
    'claude-3-7-sonnet-20250219',
    'claude-3-5-sonnet-20241022',
    'claude-3-5-haiku-20241022',
    'claude-3-opus-20240229',
    'claude-3-haiku-20240307',
  ],
  anthropic: [
    'claude-3-7-sonnet-20250219',
    'claude-3-5-sonnet-20241022',
    'claude-3-5-haiku-20241022',
    'claude-3-opus-20240229',
    'claude-3-haiku-20240307',
  ],
};

export type ProviderName = keyof typeof PROVIDERS;
