// Axios instance with JWT access/refresh handling.
//
// - Attaches the access token to every request.
// - On a 401, transparently tries the refresh token once, replays the request,
//   and queues concurrent requests while the refresh is in flight.
// - On refresh failure, clears auth and redirects to /login.
import axios, {
  AxiosError,
  type AxiosInstance,
  type InternalAxiosRequestConfig,
} from 'axios';
import { useAuthStore } from '../store/auth';
import type { ApiError, TokenPair } from './types';

const BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api/v1';

export const api: AxiosInstance = axios.create({ baseURL: BASE_URL });

api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = useAuthStore.getState().accessToken;
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

let refreshing: Promise<string | null> | null = null;

async function refreshAccessToken(): Promise<string | null> {
  const { refreshToken, principal, setTokens, clear } = useAuthStore.getState();
  if (!refreshToken) return null;
  // Mailbox and admin principals have separate refresh endpoints.
  const path = principal === 'mailbox' ? '/mailbox/refresh' : '/auth/refresh';
  try {
    // Bare axios (not `api`) to avoid the interceptor recursing.
    const resp = await axios.post<TokenPair>(`${BASE_URL}${path}`, {
      refresh_token: refreshToken,
    });
    setTokens(resp.data.access_token, resp.data.refresh_token);
    return resp.data.access_token;
  } catch {
    clear();
    return null;
  }
}

function loginPathForPrincipal(): string {
  return useAuthStore.getState().principal === 'mailbox' ? '/portal/login' : '/login';
}

api.interceptors.response.use(
  (resp) => resp,
  async (error: AxiosError<ApiError>) => {
    const original = error.config as InternalAxiosRequestConfig & { _retry?: boolean };
    const status = error.response?.status;

    if (status === 401 && original && !original._retry) {
      original._retry = true;
      refreshing = refreshing ?? refreshAccessToken();
      const newToken = await refreshing;
      refreshing = null;

      if (newToken) {
        original.headers.Authorization = `Bearer ${newToken}`;
        return api(original);
      }
      const loginPath = loginPathForPrincipal();
      if (window.location.pathname !== loginPath) {
        window.location.assign(loginPath);
      }
    }
    return Promise.reject(error);
  },
);

/** Extract a human-readable message from an axios error. */
export function errorMessage(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const data = err.response?.data as ApiError | undefined;
    return data?.error?.message ?? err.message;
  }
  return err instanceof Error ? err.message : 'Unexpected error';
}
