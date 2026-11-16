import { useCallback, useEffect, useState } from 'react'
import { send } from './api'
import { ChecksSection, chf, ExtractedSection, FraudReportSection, OutcomeSection, ProposalSection } from './PipelineSections'
import type { PipelineClaim } from './PipelineSections'

// The handler desk (issue #84, package B3): the queue of the claims that wait for a person, and the
// case page with the decisions of a handler. The system never refuses a claim: a handler does, here.
type Row = {
  claim_id: string
  customer: string
  policy_number: string | null
  type: string | null
  claimed_amount: number | null
  status: string
  outcome: string | null
  score: number | null
  reasons: string[]
  opened_at: string | null
}
type Decision = { action: string; by: string; reason: string; amount: number | null; time: string }
type Case = PipelineClaim & {
  id: string
  customer: { name: string }
  policy: { number: string; line_of_business: string } | null
  description: string | null
  incident_date: string | null
  claimed_amount: number | null
  cause: string | null
  status: string
  notice?: { key: string; question: string; why: string; answer: string | null }[]
  decisions: Decision[]
  investigation?: Investigation | null
  reserve?: number | null
  payment?: { amount: number; account: string; reference: string; time: string } | null
}
type Message = { role: string; text: string; time: string }
type Task = { id: string; text: string; codes: string[]; done: boolean; by: string | null; note: string }
type Investigation = {
  tasks: Task[]
  notes: { by: string; text: string; time: string }[]
  conclusion: { result: string; by: string; text: string; time: string } | null
}
type Earlier = { id: string; date: string; line: string; cause: string; amount: number; result: string; provider: string | null }
type History = {
  person: { name: string; account: string; phone: string; address: string } | null
  earlier_claims: Earlier[]
  links: { person: string; name: string; shared: string[]; claims: Earlier[] }[]
}

const earlier = (c: Earlier) => `${c.id} · ${c.date} · ${c.line}, ${c.cause} · ${chf(c.amount)}`

// The history of the customer and the persons linked to him (issue #90). The signals R006 to R010 read the same data.
function HistorySection({ history }: { history: History }) {
  return (
    <div className="stack" aria-label="History and links">
      <h3>History and links</h3>
      {history.person && <p className="muted">{`Payout account ${history.person.account} · ${history.person.phone} · ${history.person.address}`}</p>}
      {history.earlier_claims.length === 0 ? (
        <p className="muted">No earlier claim of this customer.</p>
      ) : (
        <ul aria-label="Earlier claims">
          {history.earlier_claims.map((c) => (
            <li key={c.id}>
              {earlier(c)} <span className={`pill ${c.result === 'fraud' ? 'pill-bad' : 'pill-idle'}`}>{c.result}</span>
            </li>
          ))}
        </ul>
      )}
      {history.links.length === 0 ? (
        <p className="muted">No other person shares the account, the phone or the address.</p>
      ) : (
        history.links.map((l) => (
          <div key={l.person} className="card desk-link" aria-label={`Link to ${l.name}`}>
            <p>
              <span className="pill pill-bad">{`same ${l.shared.join(', ')}`}</span> {`${l.name} (${l.person})`}
            </p>
            <ul>
              {l.claims.map((c) => (
                <li key={c.id}>
                  {earlier(c)} <span className={`pill ${c.result === 'fraud' ? 'pill-bad' : 'pill-idle'}`}>{c.result}</span>
                </li>
              ))}
            </ul>
          </div>
        ))
      )}
    </div>
  )
}

// most urgent first; a status that is not here does not wait for a handler
const TO_DO = ['in_investigation', 'in_review', 'waiting_customer']
const STATUS_PILL: Record<string, string> = {
  in_investigation: 'pill-bad',
  in_review: 'pill-warn',
  waiting_customer: 'pill-attention',
  approved: 'pill-ok',
  paid: 'pill-ok',
  validated: 'pill-ok',
  refused: 'pill-idle',
  open: 'pill-idle',
}
const label = (status: string) => status.replace(/_/g, ' ')
const rank = (r: Row) => (TO_DO.includes(r.status) ? TO_DO.indexOf(r.status) : TO_DO.length)
const byUrgency = (a: Row, b: Row) => rank(a) - rank(b) || (a.opened_at ?? '').localeCompare(b.opened_at ?? '')

function age(opened: string | null) {
  if (!opened) return '—'
  const minutes = Math.max(0, Math.round((Date.now() - Date.parse(opened)) / 60000))
  if (minutes < 60) return `${minutes} min`
  return minutes < 2880 ? `${Math.round(minutes / 60)} h` : `${Math.round(minutes / 1440)} d`
}

const ACTIONS: { action: string; text: string; className?: string; when: (status: string) => boolean }[] = [
  // the investigator reports first: no approval and no refusal while a claim is in investigation (issue #92)
  { action: 'approve', text: 'Approve', className: 'btn-primary', when: (s) => s !== 'in_investigation' },
  { action: 'request', text: 'Ask the customer', when: (s) => s !== 'waiting_customer' },
  { action: 'investigate', text: 'Send to investigation', when: (s) => s !== 'in_investigation' },
  { action: 'clear', text: 'Conclude: not confirmed', className: 'btn-primary', when: (s) => s === 'in_investigation' },
  { action: 'confirm', text: 'Conclude: fraud confirmed', className: 'btn-danger', when: (s) => s === 'in_investigation' },
  { action: 'refuse', text: 'Refuse', className: 'btn-danger', when: (s) => s !== 'in_investigation' },
]

// The case of an investigator (issue #92): the plan, the evidence log and the conclusion. `change` posts
// one change and gives the new claim back; the conclusion itself is a decision (the buttons above).
function InvestigationSection({ case_, open, change }: { case_: Investigation; open: boolean; change: (path: string, body: object) => Promise<unknown> }) {
  const [notes, setNotes] = useState<Record<string, string>>({})
  const [entry, setEntry] = useState('')
  const left = case_.tasks.filter((t) => !t.done).length
  return (
    <div className="stack" aria-label="Investigation">
      <h3>Investigation</h3>
      {case_.conclusion ? (
        <p>
          <span className={`pill ${case_.conclusion.result === 'confirmed' ? 'pill-bad' : 'pill-ok'}`}>{case_.conclusion.result === 'confirmed' ? 'fraud confirmed' : 'not confirmed'}</span>{' '}
          {`${case_.conclusion.by}: ${case_.conclusion.text}`}
        </p>
      ) : (
        <p className="muted">{left === 0 ? 'Every task is done: write the conclusion in the decision form above.' : `${left} of ${case_.tasks.length} tasks to do. The conclusion comes after the last one.`}</p>
      )}
      <ul className="desk-tasks" aria-label="Plan">
        {case_.tasks.map((t) => (
          <li key={t.id}>
            <label>
              <input
                type="checkbox"
                checked={t.done}
                disabled={!open}
                aria-label={`${t.id}: ${t.text}`}
                onChange={(e) => void change(`tasks/${t.id}`, { done: e.target.checked, note: notes[t.id] ?? '' })}
              />
              <span>
                {t.text} {t.codes.length > 0 && <span className="mono muted">{t.codes.join(', ')}</span>}
              </span>
            </label>
            {t.done || !open ? (
              <span className="muted">{[t.by, t.note].filter(Boolean).join(': ')}</span>
            ) : (
              <input aria-label={`Note for ${t.id}`} placeholder="What you found" value={notes[t.id] ?? ''} onChange={(e) => setNotes({ ...notes, [t.id]: e.target.value })} />
            )}
          </li>
        ))}
      </ul>
      <h4>Evidence log</h4>
      {case_.notes.length === 0 ? (
        <p className="muted">No note yet.</p>
      ) : (
        <ol className="desk-history" aria-label="Evidence log">
          {case_.notes.map((n) => (
            <li key={n.time}>
              <span className="mono muted">{n.time.slice(0, 16).replace('T', ' ')}</span> {`${n.by}: ${n.text}`}
            </li>
          ))}
        </ol>
      )}
      {open && (
        <form
          className="desk-actions"
          onSubmit={(e) => {
            e.preventDefault()
            if (entry.trim()) void change('notes', { text: entry }).then(() => setEntry(''))
          }}
        >
          <input aria-label="New note" placeholder="A fact for the file" value={entry} onChange={(e) => setEntry(e.target.value)} />
          <button type="submit">Add the note</button>
        </form>
      )}
    </div>
  )
}

export default function HandlerDesk() {
  const [rows, setRows] = useState<Row[]>([])
  const [all, setAll] = useState(false)
  const [picked, setPicked] = useState<string | null>(null)
  const [error, setError] = useState('')

  const load = useCallback(
    () =>
      send<Row[]>('/claims')
        .then((r) => {
          setRows(Array.isArray(r) ? r : [])
          setError('')
        })
        .catch(() => setError('The server did not answer.')),
    [],
  )
  useEffect(() => {
    void load()
    // ponytail: the queue asks again every 5 s; an event stream of all claims if the desk grows
    const timer = setInterval(() => void load(), 5000)
    return () => clearInterval(timer)
  }, [load])

  const shown = rows.filter((r) => all || TO_DO.includes(r.status)).sort(byUrgency)
  return (
    <>
      <header className="page-head">
        <div className="title">
          <span className="eyebrow">Handler</span>
          <h1>Handler desk</h1>
          <p>The claims that wait for a person. The system routes a claim; a handler decides.</p>
        </div>
      </header>
      <div className="panels">
        <section className="card panel panel-customer" aria-label="Queue">
          <header className="panel-head">
            <span className="eyebrow">{`Queue · ${rows.filter((r) => TO_DO.includes(r.status)).length} to do`}</span>
            <label className="desk-all">
              <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} aria-label="Show every claim" />
              All
            </label>
          </header>
          <div className="panel-body">
            {error && <p className="pill pill-bad">{error}</p>}
            {shown.length === 0 && !error && <p className="muted">No claim waits for a handler.</p>}
            {shown.map((r) => (
              <button key={r.claim_id} type="button" className="desk-row" aria-current={r.claim_id === picked ? 'true' : undefined} onClick={() => setPicked(r.claim_id)}>
                <span className="desk-row-top">
                  <span className="mono">{r.claim_id}</span>
                  <span className={`pill ${STATUS_PILL[r.status] ?? 'pill-idle'}`}>{label(r.status)}</span>
                </span>
                <span>{`${r.customer} · ${r.type ?? 'no policy yet'} · ${chf(r.claimed_amount)}`}</span>
                <span className="muted">{`${age(r.opened_at)}${r.score === null ? '' : ` · score ${r.score}`}${r.reasons[0] ? ` · ${r.reasons[0]}` : ''}`}</span>
              </button>
            ))}
          </div>
        </section>
        <section className="card panel panel-pipeline" aria-label="Case">
          {picked ? (
            <CasePage key={picked} claimId={picked} onDecided={load} />
          ) : (
            <div className="panel-body">
              <p className="muted">Pick a claim in the queue.</p>
            </div>
          )}
        </section>
      </div>
    </>
  )
}

function CasePage({ claimId, onDecided }: { claimId: string; onDecided: () => unknown }) {
  const [claim, setClaim] = useState<Case | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [handlers, setHandlers] = useState<string[]>([])
  const [history, setHistory] = useState<History | null>(null)
  const [by, setBy] = useState('')
  const [reason, setReason] = useState('')
  const [amount, setAmount] = useState('') // CHF to approve: the proposal, until the handler types another amount
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const fail = () => setError('The server did not answer.')
    send<Case>(`/claims/${claimId}`)
      .then((c) => {
        setClaim(c)
        setAmount(c.proposal?.payable == null ? '' : String(c.proposal.payable))
      })
      .catch(fail)
    send<Message[]>(`/claims/${claimId}/messages`).then(setMessages).catch(fail)
    send<History>(`/claims/${claimId}/history`)
      .then(setHistory)
      .catch(() => setHistory(null)) // the case page works without it
    send<string[]>('/handlers')
      .then((h) => {
        setHandlers(h)
        setBy((b) => b || h[0] || '')
      })
      .catch(fail)
  }, [claimId])

  const decide = async (action: string) => {
    setBusy(true)
    setError('')
    try {
      const res = await fetch(`/claims/${claimId}/decisions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, by, reason, ...(action === 'approve' && amount.trim() !== '' ? { amount: Number(amount) } : {}) }),
      })
      const body = (await res.json()) as Case & { detail?: string }
      if (!res.ok) {
        setError(typeof body.detail === 'string' ? body.detail : 'The decision was not saved.')
        return
      }
      setClaim(body)
      setReason('')
      setMessages(await send<Message[]>(`/claims/${claimId}/messages`))
      onDecided()
    } catch {
      setError('The server did not answer.')
    } finally {
      setBusy(false)
    }
  }

  // one change of the investigation case, signed by the handler picked in the decision form
  const investigate = async (path: string, body: object) => {
    setError('')
    try {
      const res = await fetch(`/claims/${claimId}/investigation/${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ by, ...body }),
      })
      const answer = (await res.json()) as Case & { detail?: string }
      if (res.ok) setClaim(answer)
      else setError(typeof answer.detail === 'string' ? answer.detail : 'The change was not saved.')
    } catch {
      setError('The server did not answer.')
    }
  }

  if (!claim) return <div className="panel-body">{error ? <p className="pill pill-bad">{error}</p> : <p className="muted">Loading…</p>}</div>
  const canDecide = TO_DO.includes(claim.status)
  return (
    <>
      <header className="panel-head">
        <h2 className="desk-title">
          <span className="mono">{claim.id}</span> <span className={`pill ${STATUS_PILL[claim.status] ?? 'pill-idle'}`}>{label(claim.status)}</span>
        </h2>
      </header>
      <div className="panel-body">
        <dl className="desk-facts" aria-label="Summary">
          <dt>Customer</dt>
          <dd>{claim.customer.name}</dd>
          <dt>Policy</dt>
          <dd className="mono">{claim.policy ? `${claim.policy.number} · ${claim.policy.line_of_business}` : '—'}</dd>
          <dt>What happened</dt>
          <dd>{claim.description ?? '—'}</dd>
          <dt>Cause</dt>
          <dd>{claim.cause ?? '—'}</dd>
          <dt>Date</dt>
          <dd className="mono">{claim.incident_date ?? '—'}</dd>
          <dt>Claimed</dt>
          <dd className="mono">{chf(claim.claimed_amount)}</dd>
        </dl>

        {canDecide && (
          <div className="stack" aria-label="Decision">
            <h3>Decision</h3>
            <label className="desk-field">
              <span>Handler</span>
              <select aria-label="Handler" value={by} disabled={busy} onChange={(e) => setBy(e.target.value)}>
                {handlers.map((h) => (
                  <option key={h}>{h}</option>
                ))}
              </select>
            </label>
            <label className="desk-field">
              <span>Amount to approve, in CHF (the proposal, or another amount with a reason)</span>
              <input aria-label="Amount to approve" type="number" min="0" step="0.05" value={amount} disabled={busy} onChange={(e) => setAmount(e.target.value)} />
            </label>
            <label className="desk-field">
              <span>Reason, or text for the customer</span>
              <textarea aria-label="Reason, or text for the customer" rows={2} value={reason} disabled={busy} onChange={(e) => setReason(e.target.value)} />
            </label>
            <p className="muted">
              A reason is required to refuse, to send to investigation and to clear. The customer never reads it. “Ask the customer” sends this text to the customer: write it in neutral
              words.
            </p>
            <div className="desk-actions">
              {ACTIONS.filter((a) => a.when(claim.status)).map((a) => (
                <button key={a.action} type="button" className={a.className} disabled={busy} onClick={() => void decide(a.action)}>
                  {a.text}
                </button>
              ))}
            </div>
          </div>
        )}
        {error && <p className="pill pill-bad">{error}</p>}

        {claim.notice && claim.notice.length > 0 && (
          <div className="stack" aria-label="First notice">
            <h3>First notice</h3>
            <dl className="desk-notice">
              {claim.notice.map((n) => (
                <div key={n.key}>
                  <dt>
                    {n.question} <span className="muted">{n.why}</span>
                  </dt>
                  <dd>{n.answer ?? <span className="pill pill-warn">not answered</span>}</dd>
                </div>
              ))}
            </dl>
          </div>
        )}

        {(claim.reserve != null || claim.payment) && (
          <div className="stack" aria-label="Reserve and payment">
            <h3>Reserve and payment</h3>
            <dl className="desk-facts">
              <dt>Reserve</dt>
              <dd className="mono">{claim.reserve == null ? '—' : chf(claim.reserve)}</dd>
              {claim.payment && (
                <>
                  <dt>Paid</dt>
                  <dd className="mono">{`${chf(claim.payment.amount)} · ${claim.payment.time.slice(0, 16).replace('T', ' ')}`}</dd>
                  <dt>To the account</dt>
                  <dd className="mono">{claim.payment.account}</dd>
                  <dt>Reference</dt>
                  <dd className="mono">{claim.payment.reference}</dd>
                  <dt>Claims system</dt>
                  <dd>
                    <a href={`/claims/${claim.id}/export`} target="_blank" rel="noreferrer">
                      Exported record (JSON)
                    </a>
                  </dd>
                </>
              )}
            </dl>
          </div>
        )}

        {claim.decisions.length > 0 && (
          <div className="stack" aria-label="Decisions">
            <h3>Decisions</h3>
            <ol className="desk-history">
              {claim.decisions.map((d) => (
                <li key={d.time}>
                  <span className="mono muted">{d.time.slice(0, 16).replace('T', ' ')}</span> {[d.action, d.amount == null ? '' : chf(d.amount), d.by, d.reason].filter(Boolean).join(' · ')}
                </li>
              ))}
            </ol>
          </div>
        )}

        {claim.investigation && <InvestigationSection case_={claim.investigation} open={claim.status === 'in_investigation' && !claim.investigation.conclusion} change={investigate} />}
        {claim.fraud_report && <FraudReportSection report={claim.fraud_report} />}
        <ProposalSection claim={claim} />
        <OutcomeSection claim={claim} />
        <ChecksSection claim={claim} ran={claim.outcome !== null} />
        {history && <HistorySection history={history} />}
        <ExtractedSection docs={claim.documents} claimId={claim.id} />
        {/* issue #28 (the intern): confirm or correct a field goes here, next to its source page */}

        <div className="stack" aria-label="Conversation">
          <h3>Conversation</h3>
          {messages.map((m, i) => (
            <p key={i} className={`bubble bubble-${m.role}`}>
              {m.text}
            </p>
          ))}
        </div>
      </div>
    </>
  )
}
