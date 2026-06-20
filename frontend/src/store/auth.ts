// Auth state. Supports two principal kinds — admin operators and mailbox users —
// so the SPA can host both the admin dashboard and the mailbox portal.
import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { MailboxProfile, User } from '../api/types';

export type Principal = 'admin' | 'mailbox';

interface AuthState {
  accessToken: string | null;
  refreshToken: string | null;
  principal: Principal | null;
  user: User | null;
  mailbox: MailboxProfile | null;
  /** Start a session (sets tokens + principal). */
  setSession: (access: string, refresh: string, principal: Principal) => void;
  /** Replace tokens only (used by the silent refresh; principal unchanged). */
  setTokens: (access: string, refresh: string) => void;
  setUser: (user: User | null) => void;
  setMailbox: (mailbox: MailboxProfile | null) => void;
  clear: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      accessToken: null,
      refreshToken: null,
      principal: null,
      user: null,
      mailbox: null,
      setSession: (access, refresh, principal) =>
        set({ accessToken: access, refreshToken: refresh, principal }),
      setTokens: (access, refresh) => set({ accessToken: access, refreshToken: refresh }),
      setUser: (user) => set({ user }),
      setMailbox: (mailbox) => set({ mailbox }),
      clear: () =>
        set({ accessToken: null, refreshToken: null, principal: null, user: null, mailbox: null }),
    }),
    { name: 'mailserver-auth' },
  ),
);
