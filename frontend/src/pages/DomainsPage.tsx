import { type FormEvent, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { errorMessage } from '../api/client';
import { domainsApi } from '../api/resources';
import { useAsync } from '../hooks/useAsync';
import { Badge, Banner, Button, EmptyState, Modal, Spinner } from '../components/ui';

export function DomainsPage() {
  const navigate = useNavigate();
  const { data: domains, loading, error, reload } = useAsync(() => domainsApi.list(), []);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setFormError(null);
    try {
      await domainsApi.create(newName);
      setShowCreate(false);
      setNewName('');
      reload();
    } catch (err) {
      setFormError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="page-header">
        <h2>Domains</h2>
        <Button onClick={() => setShowCreate(true)}>Add domain</Button>
      </div>

      {error && <Banner kind="error">{error}</Banner>}
      {loading ? (
        <Spinner />
      ) : !domains || domains.length === 0 ? (
        <EmptyState>No domains yet. Add your first domain to get started.</EmptyState>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Domain</th>
              <th>DNS</th>
              <th>MX</th>
              <th>SPF</th>
              <th>DKIM</th>
              <th>DMARC</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {domains.map((d) => (
              <tr key={d.id} className="clickable" onClick={() => navigate(`/domains/${d.id}`)}>
                <td className="mono">{d.name}</td>
                <td><Badge ok={d.dns_verified} label={d.dns_verified ? 'verified' : 'pending'} /></td>
                <td><Badge ok={d.mx_verified} label={d.mx_verified ? 'ok' : '—'} /></td>
                <td><Badge ok={d.spf_verified} label={d.spf_verified ? 'ok' : '—'} /></td>
                <td><Badge ok={!!d.dkim_selector} label={d.dkim_selector ? 'key' : '—'} /></td>
                <td><Badge ok={d.dmarc_verified} label={d.dmarc_verified ? 'ok' : '—'} /></td>
                <td><Badge ok={d.is_active} label={d.is_active ? 'active' : 'disabled'} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {showCreate && (
        <Modal title="Add domain" onClose={() => setShowCreate(false)}>
          <form onSubmit={handleCreate}>
            {formError && <Banner kind="error">{formError}</Banner>}
            <label>
              Domain name
              <input
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="example.com"
                required
              />
            </label>
            <p className="hint">A DKIM keypair is generated automatically.</p>
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
