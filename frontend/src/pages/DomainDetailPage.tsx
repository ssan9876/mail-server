import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { errorMessage } from '../api/client';
import { domainsApi } from '../api/resources';
import type { VerificationResult } from '../api/types';
import { useAsync } from '../hooks/useAsync';
import { Badge, Banner, Button, Spinner } from '../components/ui';
import { MailboxesPanel } from '../components/MailboxesPanel';
import { AliasesPanel } from '../components/AliasesPanel';

type Tab = 'dns' | 'mailboxes' | 'aliases';

export function DomainDetailPage() {
  const { id = '' } = useParams();
  const navigate = useNavigate();
  const domainState = useAsync(() => domainsApi.get(id), [id]);
  const recordsState = useAsync(() => domainsApi.dnsRecords(id), [id]);

  const [tab, setTab] = useState<Tab>('dns');
  const [action, setAction] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [verify, setVerify] = useState<VerificationResult | null>(null);

  async function run(name: string, fn: () => Promise<unknown>) {
    setAction(name);
    setActionError(null);
    try {
      await fn();
    } catch (err) {
      setActionError(errorMessage(err));
    } finally {
      setAction(null);
    }
  }

  if (domainState.loading) return <Spinner />;
  if (domainState.error || !domainState.data) {
    return <Banner kind="error">{domainState.error ?? 'Domain not found.'}</Banner>;
  }
  const domain = domainState.data;

  return (
    <div>
      <button className="back-link" onClick={() => navigate('/domains')}>
        ← Domains
      </button>
      <div className="page-header">
        <h2 className="mono">{domain.name}</h2>
        <div className="header-actions">
          <Button
            variant="secondary"
            disabled={action !== null}
            onClick={() =>
              run('verify', async () => {
                setVerify(await domainsApi.verifyDns(id));
                domainState.reload();
              })
            }
          >
            {action === 'verify' ? 'Verifying…' : 'Verify DNS'}
          </Button>
          <Button
            variant="secondary"
            disabled={action !== null}
            onClick={() => run('publish', () => domainsApi.publishDns(id))}
          >
            {action === 'publish' ? 'Publishing…' : 'Publish to Cloudflare'}
          </Button>
          <Button
            variant="secondary"
            disabled={action !== null}
            onClick={() =>
              run('rotate', async () => {
                await domainsApi.rotateDkim(id);
                recordsState.reload();
                domainState.reload();
              })
            }
          >
            {action === 'rotate' ? 'Rotating…' : 'Rotate DKIM'}
          </Button>
          <Button
            variant="danger"
            disabled={action !== null}
            onClick={() => {
              if (confirm(`Delete ${domain.name} and all its mailboxes?`)) {
                run('delete', async () => {
                  await domainsApi.remove(id);
                  navigate('/domains');
                });
              }
            }}
          >
            Delete
          </Button>
        </div>
      </div>

      {actionError && <Banner kind="error">{actionError}</Banner>}
      {verify && (
        <Banner kind={verify.dns_verified ? 'success' : 'info'}>
          MX {verify.mx_verified ? '✓' : '✗'} · SPF {verify.spf_verified ? '✓' : '✗'} · DKIM{' '}
          {verify.dkim_verified ? '✓' : '✗'} · DMARC {verify.dmarc_verified ? '✓' : '✗'}
        </Banner>
      )}

      <div className="status-row">
        <Badge ok={domain.dns_verified} label={`DNS ${domain.dns_verified ? 'verified' : 'pending'}`} />
        <Badge ok={domain.is_active} label={domain.is_active ? 'active' : 'disabled'} />
        {domain.dkim_selector && <span className="chip">selector: {domain.dkim_selector}</span>}
      </div>

      <div className="tabs">
        {(['dns', 'mailboxes', 'aliases'] as Tab[]).map((t) => (
          <button
            key={t}
            className={`tab ${tab === t ? 'tab-active' : ''}`}
            onClick={() => setTab(t)}
          >
            {t === 'dns' ? 'DNS records' : t}
          </button>
        ))}
      </div>

      {tab === 'dns' && (
        <div>
          {recordsState.loading ? (
            <Spinner />
          ) : recordsState.error ? (
            <Banner kind="error">{recordsState.error}</Banner>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Type</th>
                  <th>Name</th>
                  <th>Value</th>
                  <th>Prio</th>
                </tr>
              </thead>
              <tbody>
                {recordsState.data?.map((r, i) => (
                  <tr key={i}>
                    <td>{r.type}</td>
                    <td className="mono">{r.name}</td>
                    <td className="mono wrap">{r.content}</td>
                    <td>{r.priority ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {tab === 'mailboxes' && <MailboxesPanel domainId={id} />}
      {tab === 'aliases' && <AliasesPanel domainId={id} />}
    </div>
  );
}
