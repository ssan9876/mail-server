import { useEffect } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { authApi, mailboxPortalApi } from './api/resources';
import { useAuthStore } from './store/auth';
import { Layout } from './components/Layout';
import { ProtectedRoute } from './components/ProtectedRoute';
import { LoginPage } from './pages/LoginPage';
import { DomainsPage } from './pages/DomainsPage';
import { DomainDetailPage } from './pages/DomainDetailPage';
import { AuditPage } from './pages/AuditPage';
import { PortalLoginPage } from './pages/PortalLoginPage';
import { PortalPage } from './pages/PortalPage';

export default function App() {
  const { accessToken, principal, user, mailbox, setUser, setMailbox, clear } = useAuthStore();

  // Rehydrate the current principal's profile after a page reload.
  useEffect(() => {
    if (!accessToken) return;
    if (principal === 'admin' && !user) {
      authApi.me().then(setUser).catch(clear);
    } else if (principal === 'mailbox' && !mailbox) {
      mailboxPortalApi.me().then(setMailbox).catch(clear);
    }
  }, [accessToken, principal, user, mailbox, setUser, setMailbox, clear]);

  return (
    <BrowserRouter>
      <Routes>
        {/* Admin dashboard */}
        <Route path="/login" element={<LoginPage />} />
        <Route
          element={
            <ProtectedRoute principal="admin">
              <Layout />
            </ProtectedRoute>
          }
        >
          <Route path="/" element={<Navigate to="/domains" replace />} />
          <Route path="/domains" element={<DomainsPage />} />
          <Route path="/domains/:id" element={<DomainDetailPage />} />
          <Route
            path="/audit"
            element={
              <ProtectedRoute principal="admin" roles={['superadmin']}>
                <AuditPage />
              </ProtectedRoute>
            }
          />
        </Route>

        {/* Mailbox self-service portal */}
        <Route path="/portal/login" element={<PortalLoginPage />} />
        <Route
          path="/portal"
          element={
            <ProtectedRoute principal="mailbox">
              <PortalPage />
            </ProtectedRoute>
          }
        />

        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
