import { type FormEvent, useState } from 'react';
import { errorMessage } from '../api/client';
import { mailboxesApi } from '../api/resources';
import { useAsync } from '../hooks/useAsync';
import { Badge, Banner, Button, EmptyState, Modal, Spinner } from './ui';

export function MailboxesPanel({ domainId }: { domainId: string }) {
  const { data, loading, error, reload } = useAsync(
    () => mailboxesApi.list(domainId),
    [domainId],
  );
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({ local_part: '', password: '', display_name: '', quota_mb: '' });
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setFormError(null);
    try {
      await mailboxesApi.create(domainId, {
        local_part: form.local_part,
        password: form.password,
        display_name: form.display_name || null,
        quota_mb: form.quota_mb ? Number(form.quota_mb) : null,
      });
      setShowCreate(false);
      setForm({ local_part: '', password: '', display_name: '', quota_mb: '' });
      reload();
    } catch (err) {
      setFormError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(id: string, label: string) {
    if (!confirm(`Delete mailbox ${label}?`)) return;
    try {
      await mailboxesApi.remove(id);
      reload();
    } catch (err) {
      alert(errorMessage(err));
    }
  }

  return (
    <div>
      <div className="panel-header">
        <Button onClick={() => setShowCreate(true)}>Add mailbox</Button>
      </div>
      {error && <Banner kind="error">{error}</Banner>}
      {loading ? (
        <Spinner />
      ) : !data || data.length === 0 ? (
        <EmptyState>No mailboxes yet.</EmptyState>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Address</th>
              <th>Display name</th>
              <th>Quota (MB)</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {data.map((m) => (
              <tr key={m.id}>
                <td className="mono">{m.local_part}</td>
                <td>{m.display_name ?? '—'}</td>
                <td>{m.quota_mb}</td>
                <td><Badge ok={m.is_active} label={m.is_active ? 'active' : 'disabled'} /></td>
                <td>
                  <Button variant="danger" onClick={() => handleDelete(m.id, m.local_part)}>
                    Delete
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {showCreate && (
        <Modal title="Add mailbox" onClose={() => setShowCreate(false)}>
          <form onSubmit={handleCreate}>
            {formError && <Banner kind="error">{formError}</Banner>}
            <label>
              Local part
              <input
                value={form.local_part}
                onChange={(e) => setForm({ ...form, local_part: e.target.value })}
                placeholder="john"
                required
              />
            </label>
            <label>
              Password
              <input
                type="password"
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
                minLength={8}
                required
              />
            </label>
            <label>
              Display name (optional)
              <input
                value={form.display_name}
                onChange={(e) => setForm({ ...form, display_name: e.target.value })}
              />
            </label>
            <label>
              Quota MB (optional)
              <input
                type="number"
                value={form.quota_mb}
                onChange={(e) => setForm({ ...form, quota_mb: e.target.value })}
                placeholder="default"
              />
            </label>
            <div className="modal-actions">
              <Button type="button" variant="secondary" onClick={() => setShowCreate(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={busy}>
                {busy ? 'Creating…' : 'Create'}
              </Button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
