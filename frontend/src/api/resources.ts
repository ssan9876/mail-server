// Typed API calls grouped by resource.
import { api } from './client';
import type {
  Alias,
  AuditLog,
  Domain,
  DnsRecord,
  Mailbox,
  MailboxProfile,
  TokenPair,
  User,
  VerificationResult,
} from './types';

// --- Auth ------------------------------------------------------------------
export const authApi = {
  login: (email: string, password: string) =>
    api.post<TokenPair>('/auth/login', { email, password }).then((r) => r.data),
  me: () => api.get<User>('/auth/me').then((r) => r.data),
  logout: (refresh_token: string | null) =>
    api.post('/auth/logout', { refresh_token }).then(() => undefined),
};

// --- Domains ---------------------------------------------------------------
export const domainsApi = {
  list: () => api.get<Domain[]>('/domains').then((r) => r.data),
  get: (id: string) => api.get<Domain>(`/domains/${id}`).then((r) => r.data),
  create: (name: string) => api.post<Domain>('/domains', { name }).then((r) => r.data),
  remove: (id: string) => api.delete(`/domains/${id}`).then(() => undefined),
  dnsRecords: (id: string) =>
    api.get<DnsRecord[]>(`/domains/${id}/dns-records`).then((r) => r.data),
  rotateDkim: (id: string) =>
    api.post<Domain>(`/domains/${id}/dkim/rotate`).then((r) => r.data),
  publishDns: (id: string) =>
    api.post<DnsRecord[]>(`/domains/${id}/dns/publish`).then((r) => r.data),
  verifyDns: (id: string) =>
    api.post<VerificationResult>(`/domains/${id}/dns/verify`).then((r) => r.data),
};

// --- Mailboxes -------------------------------------------------------------
export interface MailboxInput {
  local_part: string;
  password: string;
  display_name?: string | null;
  quota_mb?: number | null;
}

export const mailboxesApi = {
  list: (domainId: string) =>
    api.get<Mailbox[]>(`/domains/${domainId}/mailboxes`).then((r) => r.data),
  create: (domainId: string, input: MailboxInput) =>
    api.post<Mailbox>(`/domains/${domainId}/mailboxes`, input).then((r) => r.data),
  update: (id: string, patch: Partial<MailboxInput> & { is_active?: boolean }) =>
    api.patch<Mailbox>(`/mailboxes/${id}`, patch).then((r) => r.data),
  remove: (id: string) => api.delete(`/mailboxes/${id}`).then(() => undefined),
};

// --- Aliases ---------------------------------------------------------------
export const aliasesApi = {
  list: (domainId: string) =>
    api.get<Alias[]>(`/domains/${domainId}/aliases`).then((r) => r.data),
  create: (domainId: string, local_part: string, destinations: string[]) =>
    api.post<Alias>(`/domains/${domainId}/aliases`, { local_part, destinations }).then((r) => r.data),
  remove: (id: string) => api.delete(`/aliases/${id}`).then(() => undefined),
};

// --- Mailbox self-service portal -------------------------------------------
export const mailboxPortalApi = {
  login: (email: string, password: string) =>
    api.post<TokenPair>('/mailbox/login', { email, password }).then((r) => r.data),
  me: () => api.get<MailboxProfile>('/mailbox/me').then((r) => r.data),
  logout: (refresh_token: string | null) =>
    api.post('/mailbox/logout', { refresh_token }).then(() => undefined),
  changePassword: (current_password: string, new_password: string) =>
    api
      .post<MailboxProfile>('/mailbox/password', { current_password, new_password })
      .then((r) => r.data),
};

// --- Audit -----------------------------------------------------------------
export const auditApi = {
  list: (params?: { action?: string; limit?: number; offset?: number }) =>
    api.get<AuditLog[]>('/audit', { params }).then((r) => r.data),
};
