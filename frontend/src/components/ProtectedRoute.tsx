import type { ReactNode } from 'react';
import { Navigate } from 'react-router-dom';
import { useAuthStore, type Principal } from '../store/auth';
import type { UserRole } from '../api/types';

/**
 * Gate a route on authentication, the principal kind (admin vs mailbox), and
 * optionally a set of operator roles.
 */
export function ProtectedRoute({
  children,
  principal,
  roles,
}: {
  children: ReactNode;
  principal?: Principal;
  roles?: UserRole[];
}) {
  const { accessToken, principal: currentPrincipal, user } = useAuthStore();

  if (!accessToken) {
    return <Navigate to={principal === 'mailbox' ? '/portal/login' : '/login'} replace />;
  }
  if (principal && currentPrincipal !== principal) {
    // Wrong principal kind for this area — send to its own home.
    return <Navigate to={currentPrincipal === 'mailbox' ? '/portal' : '/domains'} replace />;
  }
  if (roles && user && !roles.includes(user.role)) {
    return <Navigate to="/domains" replace />;
  }
  return <>{children}</>;
}
