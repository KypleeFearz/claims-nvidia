import { useEffect, useState } from 'react'
import { json, send } from './api'
import Chat, { Typing } from './Chat'
import type { ChatView } from './Chat'

type Customer = { id: string; name: string }
type Policy = { number: string; line_of_business: string }
// the customer endpoint: claim id, policy, status and the result text, nothing else
type LetterLine = { label: string; amount: number; reason: string; article: string }
type Letter = { amount: number; lines: LetterLine[]; note: string }
type View = {
  claim_id: string
  policy_number: string | null
  status: string
  message: string | null
  status_line?: string
  requests?: string[]
  letter?: Letter | null
}

const money = (n: number) => n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const STATUS_PILL: Record<string, string> = { waiting_customer: 'pill-attention', validated: 'pill-ok', approved: 'pill-ok', paid: 'pill-ok' }

// What the customer follows after the chat (issue #88): where the claim is, what is asked, and the
// settlement. The same for a review and for an investigation: the server sends the same view.
function FollowUp({ view }: { view: View }) {
  if (!view.status_line || view.status === 'open') return null
  return (
    <div className="follow-up" aria-label="Your claim">
      <p className={`pill ${STATUS_PILL[view.status] ?? 'pill-idle'}`}>{view.status_line}</p>
      {view.requests && view.requests.length > 0 && (
        <ul aria-label="What we still need">
          {view.requests.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      )}
      {view.letter && (
        <div className="card letter" aria-label="Settlement">
          <h3>{`We pay CHF ${money(view.letter.amount)}`}</h3>
          {view.letter.note && <p>{view.letter.note}</p>}
          {view.letter.lines.length > 0 && (
            <table>
              <tbody>
                {view.letter.lines.map((l) => (
                  <tr key={l.label}>
                    <th scope="row">
                      {l.label}
                      <span className="muted letter-why">{`${l.reason} ${l.article}`}</span>
                    </th>
                    <td className="mono">{money(l.amount)}</td>
                  </tr>
                ))}
                <tr>
                  <th scope="row">To pay</th>
                  <td className="mono">{money(view.letter.amount)}</td>
                </tr>
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  )
}

export default function CustomerPanel({ claimId, onClaim }: { claimId: string | null; onClaim: (id: string | null) => void }) {
  const [customers, setCustomers] = useState<Customer[]>([])
  const [policies, setPolicies] = useState<Policy[]>([])
  const [filter, setFilter] = useState('')
  const [claim, setClaim] = useState<View | null>(null)
  const [name, setName] = useState('')
  const [error, setError] = useState('')
  const [mine, setMine] = useState<{ customer: Customer; claims: View[] } | null>(null) // the earlier claims of the picked account
  const [starting, setStarting] = useState(false) // the policy is being set: the agent writes its first message

  useEffect(() => {
    send<Customer[]>('/customers')
      .then(setCustomers)
      .catch(() => setError('The server did not answer.'))
  }, [])
  useEffect(() => {
    if (!claimId) return
    const fail = () => setError('The server did not answer.')
    send<View>(`/claims/${claimId}/customer`)
      .then((c) => {
        setMine(null)
        setClaim(c)
        // the policy list goes through the gateway: it is the first event of the claim
        if (!c.policy_number) send<Policy[]>(`/claims/${claimId}/policies`).then(setPolicies).catch(fail)
      })
      // a claim that does not exist any more (the demo was reset): forget it
      .catch((e: Error) => (e.message === '404' ? onClaim(null) : fail()))
  }, [claimId, onClaim])

  // a new claim is created before any policy
  const openClaim = (c: Customer) =>
    send<View>('/claims', json('POST', { customer_id: c.id }))
      .then((created) => {
        setName(c.name)
        setMine(null)
        onClaim(created.claim_id)
      })
      .catch(() => setError('The claim was not created.'))

  // the choice of the account (issue #107): a customer with earlier claims picks one or opens a new one;
  // with none, a new claim opens at once
  const pickCustomer = (c: Customer) =>
    send<View[]>(`/customers/${c.id}/claims`)
      .then((claims) => (Array.isArray(claims) && claims.length > 0 ? setMine({ customer: c, claims }) : openClaim(c)))
      .catch(() => openClaim(c))

  const pickPolicy = (p: Policy) => {
    setStarting(true) // nim and sandbox mode: the first message takes 5 to 10 seconds
    return send<View>(`/claims/${claimId}/policy`, json('PUT', { policy_number: p.number }))
      .then((c) => {
        setClaim(c)
        setFilter('')
      })
      .catch(() => setError('The policy was not set.'))
      .finally(() => setStarting(false))
  }

  const newClaim = () => {
    setMine(null)
    setClaim(null)
    setPolicies([])
    onClaim(null) // no data is deleted: the old claim stays in the store
  }

  const q = filter.trim().toLowerCase()
  const shown = policies.filter((p) => `${p.number} ${p.line_of_business}`.toLowerCase().includes(q))

  return (
    <section className="card panel panel-customer" aria-label="Customer">
      <header className="panel-head">
        <span className="eyebrow">Customer view</span>
        {claimId && (
          <button type="button" onClick={newClaim}>
            New claim
          </button>
        )}
      </header>
      <div className="panel-body">
        <p className="notice">Demo. Do not upload a real document.</p>
        {error && <p className="pill pill-bad">{error}</p>}
        {!claimId && mine ? (
          <div className="stack" aria-label="Your claims">
            <h3>{`Your claims, ${mine.customer.name}`}</h3>
            {mine.claims.map((c) => (
              <button key={c.claim_id} type="button" className="desk-row" onClick={() => onClaim(c.claim_id)}>
                <span className="mono">{`${c.claim_id} · ${c.policy_number}`}</span>
                <span className="muted">{c.status_line}</span>
              </button>
            ))}
            <button type="button" className="btn-primary" onClick={() => void openClaim(mine.customer)}>
              New claim
            </button>
          </div>
        ) : !claimId ? (
          <div className="stack">
            <h3>1. Pick an account</h3>
            {customers.map((c) => (
              <button key={c.id} type="button" onClick={() => void pickCustomer(c)}>
                {c.name}
              </button>
            ))}
          </div>
        ) : claim?.policy_number ? (
          <>
            <p className="mono claim-line">{`Claim ${claim.claim_id} · ${claim.policy_number}`}</p>
            <FollowUp view={claim} />
            <Chat claimId={claim.claim_id} policy={claim.policy_number} onView={(v: ChatView) => setClaim((c) => (c ? { ...c, ...v } : c))} />
          </>
        ) : claim && starting ? (
          <div className="chat-list" role="log" aria-label="Conversation">
            <Typing />
          </div>
        ) : claim ? (
          <div className="stack">
            <h3>{name ? `2. Pick a policy of ${name}` : '2. Pick a policy'}</h3>
            <input aria-label="Filter policies" placeholder="Number or line of business" value={filter} onChange={(e) => setFilter(e.target.value)} />
            {shown.map((p) => (
              <button key={p.number} type="button" onClick={() => void pickPolicy(p)}>
                <span className="mono">{p.number}</span> · {p.line_of_business}
              </button>
            ))}
          </div>
        ) : null}
      </div>
    </section>
  )
}
