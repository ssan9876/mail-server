import { useState } from 'react';
import { auditApi } from '../api/resources';
import { useAsync } from '../hooks/useAsync';
import { Banner, Button, EmptyState, Spinner } from '../components/ui';

export function AuditPage() {
  const [filter, setFilter] = useState('');
  const [applied, setApplied] = useState<string | undefined>(undefined);
  const { data, loading, error } = useAsync(
    () => auditApi.list({ action: applied, limit: 200 }),
    [applied],
  );

  return (
    <div>
      <div className="page-header">
        <h2>Audit log</h2>
        <div className="header-actions">
          <input
            className="inline-input"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="filter by action (e.g. domain.created)"
          />
          <Button variant="secondary" onClick={() => setApplied(filter || undefined)}>
            Filter
          </Button>
        </div>
      </div>

      {error && <Banner kind="error">{error}</Banner>}
      {loading ? (
        <Spinner />
      ) : !data || data.length === 0 ? (
        <EmptyState>No audit entries.</EmptyState>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Action</th>
              <th>Actor</th>
              <th>Target</th>
              <th>IP</th>
            </tr>
          </thead>
          <tbody>
            {data.map((e) => (
              <tr key={e.id}>
                <td className="mono">{new Date(e.created_at).toLocaleString()}</td>
                <td>{e.action}</td>
                <td className="mono">{e.actor_type ?? '—'}</td>
                <td className="mono">
                  {e.target_type ? `${e.target_type}:${e.target_id?.slice(0, 8) ?? ''}` : '—'}
                </td>
                <td className="mono">{e.ip_address ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
