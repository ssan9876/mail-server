import { type FormEvent, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { errorMessage } from '../api/client';
import { mailboxPortalApi } from '../api/resources';
import { useAuthStore } from '../store/auth';
import { Banner, Button } from '../components/ui';

export function PortalLoginPage() {
  const navigate = useNavigate();
  const { setSession, setMailbox } = useAuthStore();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const tokens = await mailboxPortalApi.login(email, password);
      setSession(tokens.access_token, tokens.refresh_token, 'mailbox');
      setMailbox(await mailboxPortalApi.me());
      navigate('/portal', { replace: true });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-screen">
      <form className="login-card" onSubmit={handleSubmit}>
        <h1>Mailbox portal</h1>
        <p className="login-sub">Sign in with your email address</p>
        {error && <Banner kind="error">{error}</Banner>}
        <label>
          Email
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="username"
            required
          />
        </label>
        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </label>
        <Button type="submit" disabled={busy}>
          {busy ? 'Signing in…' : 'Sign in'}
        </Button>
      </form>
    </div>
  );
}
