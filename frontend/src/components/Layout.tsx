import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { authApi } from '../api/resources';
import { useAuthStore } from '../store/auth';
import { Button } from './ui';

export function Layout() {
  const navigate = useNavigate();
  const { user, refreshToken, clear } = useAuthStore();

  async function handleLogout() {
    try {
      await authApi.logout(refreshToken);
    } finally {
      clear();
      navigate('/login', { replace: true });
    }
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">mail-server</div>
        <nav className="nav">
          <NavLink to="/domains" className="nav-link">
            Domains
          </NavLink>
          {user?.role === 'superadmin' && (
            <NavLink to="/audit" className="nav-link">
              Audit log
            </NavLink>
          )}
        </nav>
        <div className="sidebar-footer">
          <div className="user-meta">
            <div className="user-email">{user?.email}</div>
            <div className="user-role">{user?.role}</div>
          </div>
          <Button variant="ghost" onClick={handleLogout}>
            Sign out
          </Button>
        </div>
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
