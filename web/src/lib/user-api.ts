import { API_ROOT, errorMessageFrom } from '@/lib/api';

export interface UserProfile {
  user_id: string;
  tenant_id: string;
  email?: string | null;
  role?: string | null;
}

export interface ApiKeyItem {
  id: number;
  name: string;
  key_prefix: string;
  tenant_id: string;
  user_id?: string | null;
  is_active: boolean;
  expires_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ApiKeyCreated extends ApiKeyItem {
  api_key: string;
}

export interface ApiKeyRotated {
  new_key: ApiKeyCreated;
  expiring_key: ApiKeyItem;
}

interface ApiResponse<T> {
  success: boolean;
  response: T;
  error?: string | null;
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let message = `Request failed with status ${res.status}`;
    try {
      const data = await res.json();
      message = errorMessageFrom(data, message);
    } catch {
      // ignore
    }
    throw new Error(message);
  }
  const body = (await res.json()) as ApiResponse<T>;
  return body.response;
}

export async function getUserProfile(token: string): Promise<UserProfile> {
  const res = await fetch(`${API_ROOT}/api/v1/user/me`, {
    headers: {
      Authorization: `Bearer ${token}`,
    },
  });
  return handleResponse<UserProfile>(res);
}

export async function listUserApiKeys(
  token: string,
  skip = 0,
  limit = 100
): Promise<ApiKeyItem[]> {
  const res = await fetch(
    `${API_ROOT}/api/v1/user/api-keys?skip=${skip}&limit=${limit}`,
    {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    }
  );
  return handleResponse<ApiKeyItem[]>(res);
}

export async function createUserApiKey(
  token: string,
  name: string
): Promise<ApiKeyCreated> {
  const res = await fetch(`${API_ROOT}/api/v1/user/api-keys`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ name }),
  });
  return handleResponse<ApiKeyCreated>(res);
}

export async function revokeUserApiKey(
  token: string,
  keyId: number
): Promise<void> {
  const res = await fetch(`${API_ROOT}/api/v1/user/api-keys/${keyId}`, {
    method: 'DELETE',
    headers: {
      Authorization: `Bearer ${token}`,
    },
  });
  await handleResponse<null>(res);
}

export async function rotateUserApiKey(
  token: string,
  keyId: number,
  graceHours = 24
): Promise<ApiKeyRotated> {
  const res = await fetch(`${API_ROOT}/api/v1/user/api-keys/${keyId}/rotate`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ grace_hours: graceHours }),
  });
  return handleResponse<ApiKeyRotated>(res);
}
