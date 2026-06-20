// Shared API types — mirror the backend Pydantic schemas.

export type UserRole = 'superadmin' | 'domain_admin' | 'user';

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface User {
  id: string;
  email: string;
  role: UserRole;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface Domain {
  id: string;
  name: string;
  owner_id: string | null;
  is_active: boolean;
  catch_all_box: string | null;
  dkim_selector: string | null;
  dns_verified: boolean;
  mx_verified: boolean;
  spf_verified: boolean;
  dmarc_verified: boolean;
  created_at: string;
  updated_at: string;
}

export interface DnsRecord {
  type: string;
  name: string;
  content: string;
  priority: number | null;
  ttl: number;
}

export interface VerificationResult {
  mx_verified: boolean;
  spf_verified: boolean;
  dkim_verified: boolean;
  dmarc_verified: boolean;
  dns_verified: boolean;
}

export interface Mailbox {
  id: string;
  domain_id: string;
  local_part: string;
  display_name: string | null;
  quota_mb: number;
  is_active: boolean;
  maildir_path: string;
  created_at: string;
  updated_at: string;
}

export interface MailboxProfile {
  id: string;
  address: string;
  display_name: string | null;
  quota_mb: number;
  is_active: boolean;
  created_at: string;
}

export interface Alias {
  id: string;
  domain_id: string;
  local_part: string;
  destinations: string[];
  is_active: boolean;
  created_at: string;
}

export interface AuditLog {
  id: number;
  actor_id: string | null;
  actor_type: 'user' | 'mailbox' | 'system' | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  meta: Record<string, unknown> | null;
  ip_address: string | null;
  created_at: string;
}

export interface ApiError {
  error: { code: string; message: string };
}
