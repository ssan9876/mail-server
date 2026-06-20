import { type FormEvent, useState } from 'react';
import { errorMessage } from '../api/client';
import { aliasesApi } from '../api/resources';
import { useAsync } from '../hooks/useAsync';
import { Badge, Banner, Button, EmptyState, Modal, Spinner } from './ui';

export function AliasesPanel({ domainId }: { domainId: string }) {
  const { data, loading, error, reload } = useAsync(
    () => aliasesApi.list(domainId),
    [domainId],
  );
  const [showCreate, setShowCreate] = useState(false);
  const [localPart, setLocalPart] = useState('');
  const [destinations, setDestinations] = useState('');
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setFormError(null);
    try {
      const dests = destinations
        .split(',')
        .map((d) => d.trim())
        .filter(Boolean);
      await aliasesApi.create(domainId, localPart, dests);
      setShowCreate(false);
      setLocalPart('');
      setDestinations('');
      reload();
    } catch (err) {
      setFormError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(id: string, label: string) {
    if (!confirm(`Delete alias ${label}?`)) return;
    try {
      await aliasesApi.remove(id);
      reload();
    } catch (err) {
      alert(errorMessage(err));
    }
  }

  return (
    <div>
      <div className="panel-header">
        <Button onClick={() => setShowCreate(true)}>Add alias</Button>
        <span className="hint">Use “@” as the local part for a catch-all.</span>
      </div>
      {error && <Banner kind="error">{error}</Banner>}
      {loading ? (
        <Spinner />
      ) : !data || data.length === 0 ? (
        <EmptyState>No aliases yet.</EmptyState>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Source</th>
              <th>Destinations</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {data.map((a) => (
              <tr key={a.id}>
                <td className="mono">{a.local_part === '@' ? '@ (catch-all)' : a.local_part}</td>
                <td className="mono wrap">{a.destinations.join(', ')}</td>
                <td><Badge ok={a.is_active} label={a.is_active ? 'active' : 'disabled'} /></td>
                <td>
                  <Button variant="danger" onClick={() => handleDelete(a.id, a.local_part)}>
                    Delete
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {showCreate && (
        <Modal title="Add alias" onClose={() => setShowCreate(false)}>
          <form onSubmit={handleCreate}>
            {formError && <Banner kind="error">{formError}</Banner>}
            <label>
              Local part (or “@” for catch-all)
              <input value={localPart} onChange={(e) => setLocalPart(e.target.value)} required />
            </label>
            <label>
              Destinations (comma-separated)
              <input
                value={destinations}
                onChange={(e) => setDestinations(e.target.value)}
                placeholder="alice@example.com, bob@example.com"
                required
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
