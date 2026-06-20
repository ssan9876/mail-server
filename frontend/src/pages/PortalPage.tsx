import { type FormEvent, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { errorMessage } from '../api/client';
import { mailboxPortalApi } from '../api/resources';
import { useAuthStore } from '../store/auth';
import { Badge, Banner, Button } from '../components/ui';

export function PortalPage() {
  const navigate = useNavigate();
  const { mailbox, refreshToken, clear } = useAuthStore();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleLogout() {
    try {
      await mailboxPortalApi.logout(refreshToken);
    } finally {
      clear();
      navigate('/portal/login', { replace: true });
    }
  }

  async function handleChangePassword(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    if (next !== confirm) {
      setError('New passwords do not match.');
      return;
    }
    setBusy(true);
    try {
      await mailboxPortalApi.changePassword(current, next);
      setSuccess('Password updated.');
      setCurrent('');
      setNext('');
      setConfirm('');
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="portal">
      <header className="portal-header">
        <div>
          <h1>Mailbox settings</h1>
          <p className="mono">{mailbox?.address}</p>
        </div>
        <Button variant="ghost" onClick={handleLogout}>
          Sign out
        </Button>
      </header>

      <section className="card">
        <h3>Account</h3>
        <dl className="detail-grid">
          <dt>Address</dt>
          <dd className="mono">{mailbox?.address}</dd>
          <dt>Display name</dt>
          <dd>{mailbox?.display_name ?? '—'}</dd>
          <dt>Quota</dt>
          <dd>{mailbox?.quota_mb} MB</dd>
          <dt>Status</dt>
          <dd>
            <Badge ok={!!mailbox?.is_active} label={mailbox?.is_active ? 'active' : 'disabled'} />
          </dd>
        </dl>
      </section>

      <section className="card">
        <h3>Change password</h3>
        {error && <Banner kind="error">{error}</Banner>}
        {success && <Banner kind="success">{success}</Banner>}
        <form onSubmit={handleChangePassword}>
          <label>
            Current password
            <input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} required />
          </label>
          <label>
            New password
            <input type="password" value={next} onChange={(e) => setNext(e.target.value)} minLength={8} required />
          </label>
          <label>
            Confirm new password
            <input
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              minLength={8}
              required
            />
          </label>
          <Button type="submit" disabled={busy}>
            {busy ? 'Updating…' : 'Update password'}
          </Button>
        </form>
      </section>
    </div>
  );
}
