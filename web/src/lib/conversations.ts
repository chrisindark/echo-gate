import { API_ROOT } from '@/lib/api';

export interface ConversationMessage {
  id: string;
  conversation_id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  created_at: string;
}

export interface ConversationItem {
  id: string;
  user_id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface ConversationWithMessages extends ConversationItem {
  messages: ConversationMessage[];
}

export async function fetchConversations(
  apiKey?: string
): Promise<ConversationItem[]> {
  const headers: Record<string, string> = {};
  if (apiKey) headers['X-API-Key'] = apiKey;

  const res = await fetch(`${API_ROOT}/api/v1/conversations`, { headers });
  if (!res.ok) {
    const errorText = await res.text().catch(() => '');
    throw new Error(`Failed to fetch conversations: ${res.status} ${errorText}`);
  }
  const json = await res.json();
  return json.response ?? json.data ?? [];
}

export async function createConversation(
  title?: string,
  apiKey?: string
): Promise<ConversationItem> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };
  if (apiKey) headers['X-API-Key'] = apiKey;

  const res = await fetch(`${API_ROOT}/api/v1/conversations`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ title: title || 'New Conversation' }),
  });
  if (!res.ok) {
    const errorText = await res.text().catch(() => '');
    throw new Error(`Failed to create conversation: ${res.status} ${errorText}`);
  }
  const json = await res.json();
  return json.response ?? json.data;
}

export async function getConversation(
  conversationId: string,
  apiKey?: string
): Promise<ConversationWithMessages> {
  const headers: Record<string, string> = {};
  if (apiKey) headers['X-API-Key'] = apiKey;

  const res = await fetch(`${API_ROOT}/api/v1/conversations/${conversationId}`, {
    headers,
  });
  if (!res.ok) {
    const errorText = await res.text().catch(() => '');
    throw new Error(`Failed to load conversation: ${res.status} ${errorText}`);
  }
  const json = await res.json();
  return json.response ?? json.data;
}

export async function updateConversationTitle(
  conversationId: string,
  title: string,
  apiKey?: string
): Promise<ConversationItem> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };
  if (apiKey) headers['X-API-Key'] = apiKey;

  const res = await fetch(`${API_ROOT}/api/v1/conversations/${conversationId}`, {
    method: 'PATCH',
    headers,
    body: JSON.stringify({ title }),
  });
  if (!res.ok) {
    const errorText = await res.text().catch(() => '');
    throw new Error(`Failed to update conversation: ${res.status} ${errorText}`);
  }
  const json = await res.json();
  return json.response ?? json.data;
}

export async function deleteConversation(
  conversationId: string,
  apiKey?: string
): Promise<boolean> {
  const headers: Record<string, string> = {};
  if (apiKey) headers['X-API-Key'] = apiKey;

  const res = await fetch(`${API_ROOT}/api/v1/conversations/${conversationId}`, {
    method: 'DELETE',
    headers,
  });
  if (!res.ok) {
    const errorText = await res.text().catch(() => '');
    throw new Error(`Failed to delete conversation: ${res.status} ${errorText}`);
  }
  return true;
}
