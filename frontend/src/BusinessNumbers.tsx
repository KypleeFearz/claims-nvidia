import { useCallback, useEffect, useState } from 'react'
import { send } from './api'
import { chf } from './PipelineSections'

// Business numbers (issue #96): what the claims of this demo say, and the kit played through the active
// reader. The evaluation of the reading (issue #19) is field by field; this page is what the business sees.
type Live = {
  claims: number
  by_status: Record<string, number>
  routed: number
  straight_through: number
  straight_through_share: number | null
  to_a_person: number
  ended: number
  median_minutes_to_end: number | null
  claimed: number
  proposed: number
  paid: number
  open_reserve: number
  investigations: { open: number; confirmed: number; not_confirmed: number; not_confirmed_share: number | null }
  decisions: Record<string, number>
  experts_open?: number
  recoveries?: { open: number; open_amount: number; recovered: number; recovered_amount: number }
}
type KitRow = {
  story: string
  title: string
  expected_outcome: string
  outcome: string | null
  outcome_right: boolean
  expected_signals: string[]
  signals: string[]
  signals_right: boolean
  expected_payable: number | null
  payable: number | null
  payable_right: boolean
  error: string | null
}
type Kit = {
  reader: string
  stories: number
  outcomes_right: number
  signals_right: number
  amounts_right: number
  seeded_fraud: number
  fraud_caught: number
  fraud_missed: number
  false_alarms: number
  rows: KitRow[]
}

const percent = (share: number | null) => (share === null ? '—' : `${Math.round(share * 100)}%`)
const pill = (ok: boolean) => `pill ${ok ? 'pill-ok' : 'pill-bad'}`

function Tile({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="card tile">
      <span className="eyebrow">{label}</span>
      <strong>{value}</strong>
      {note && <span className="muted">{note}</span>}
    </div>
  )
}

export default function BusinessNumbers() {
  const [live, setLive] = useState<Live | null>(null)
  const [kit, setKit] = useState<Kit | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(
    () =>
      send<Live>('/numbers')
        .then((n) => setLive(n && typeof n.claims === 'number' ? n : null))
        .catch(() => setError('The server did not answer.')),
    [],
  )
  useEffect(() => {
    void load()
  }, [load])

  const play = () => {
    setBusy(true)
    setError('')
    send<Kit>('/numbers/kit', { method: 'POST' })
      .then(setKit)
      .catch((e: Error) => setError(e.message === '409' ? 'A kit run is in progress.' : 'The kit run failed.'))
      .finally(() => setBusy(false))
  }

  return (
    <>
      <header className="page-head">
        <div className="title">
          <span className="eyebrow">Numbers · business</span>
          <h1>Business numbers</h1>
          <p>What the claims of this demo say, and what the kit gives through the active reader.</p>
        </div>
      </header>
      <div className="numbers">
        {error && <p className="pill pill-bad">{error}</p>}
        <section className="card panel" aria-label="Claims of the demo">
          <header className="panel-head">
            <span className="eyebrow">Claims of this demo</span>
            <button type="button" onClick={() => void load()}>
              Refresh
            </button>
          </header>
          <div className="panel-body">
            {!live ? (
              <p className="muted">Loading…</p>
            ) : live.claims === 0 ? (
              <p className="muted">No claim yet. Play a claim on the page “Claim demo”.</p>
            ) : (
              <>
                <div className="tiles">
                  <Tile label="Passed with no person" value={percent(live.straight_through_share)} note={`${live.straight_through} of ${live.routed} routed claims`} />
                  <Tile label="To a person" value={String(live.to_a_person)} note={`${live.investigations.open} in investigation now`} />
                  <Tile
                    label="First notice to the end"
                    value={live.median_minutes_to_end === null ? '—' : `${live.median_minutes_to_end} min`}
                    note={`median of ${live.ended} ended claims`}
                  />
                  <Tile label="Paid" value={chf(live.paid)} note={`claimed ${chf(live.claimed)} · proposed ${chf(live.proposed)}`} />
                  <Tile label="Open reserve" value={chf(live.open_reserve)} />
                  {live.recoveries && (
                    <Tile
                      label="Recovered from third parties"
                      value={chf(live.recoveries.recovered_amount)}
                      note={`${live.recoveries.open} open for ${chf(live.recoveries.open_amount)} · ${live.experts_open ?? 0} expert requests open`}
                    />
                  )}
                  <Tile
                    label="Alerts not confirmed"
                    value={percent(live.investigations.not_confirmed_share)}
                    note={`${live.investigations.not_confirmed} not confirmed · ${live.investigations.confirmed} confirmed`}
                  />
                </div>
                <p className="muted" aria-label="By status">
                  {Object.entries(live.by_status)
                    .map(([s, n]) => `${s.replace(/_/g, ' ')}: ${n}`)
                    .join(' · ')}
                </p>
                {Object.keys(live.decisions).length > 0 && (
                  <p className="muted" aria-label="Decisions of the handlers">
                    {`Decisions of the handlers · ${Object.entries(live.decisions)
                      .map(([a, n]) => `${a}: ${n}`)
                      .join(' · ')}`}
                  </p>
                )}
              </>
            )}
          </div>
        </section>

        <section className="card panel" aria-label="Kit">
          <header className="panel-head">
            <span className="eyebrow">The kit through the active reader</span>
            <button type="button" className="btn-primary" disabled={busy} onClick={play}>
              {busy ? 'Playing…' : 'Play the kit'}
            </button>
          </header>
          <div className="panel-body">
            {!kit ? (
              <p className="muted">
                {busy
                  ? 'The stories of the kit run through the reader, the checks, the routing and the settlement…'
                  : 'Plays every story of the kit with no agent model, in a store of its own, and compares with the expected outcome, the expected signals and the amount calculated by hand.'}
              </p>
            ) : (
              <>
                <p role="status" className="bench-summary">
                  <span className="pill pill-idle">{`Reader ${kit.reader}`}</span>
                  <span className={pill(kit.outcomes_right === kit.stories)}>{`${kit.outcomes_right} of ${kit.stories} outcomes right`}</span>
                  <span className={pill(kit.signals_right === kit.stories)}>{`${kit.signals_right} of ${kit.stories} signal sets right`}</span>
                  <span className={pill(kit.amounts_right === kit.stories)}>{`${kit.amounts_right} of ${kit.stories} amounts right`}</span>
                  <span className={pill(kit.fraud_missed === 0)}>{`Fraud caught: ${kit.fraud_caught} of ${kit.seeded_fraud}`}</span>
                  <span className={pill(kit.false_alarms === 0)}>{`False alarms: ${kit.false_alarms}`}</span>
                </p>
                <div className="table-scroll">
                  <table aria-label="Kit stories">
                    <thead>
                      <tr>
                        <th>Story</th>
                        <th>Outcome</th>
                        <th>Signals</th>
                        <th>To pay</th>
                      </tr>
                    </thead>
                    <tbody>
                      {kit.rows.map((r) => (
                        <tr key={r.story}>
                          <th scope="row">
                            {r.story}
                            <span className="muted letter-why">{r.error ?? r.title}</span>
                          </th>
                          <td>
                            <span className={pill(r.outcome_right)}>{r.outcome ?? 'none'}</span>
                            {!r.outcome_right && <span className="muted letter-why">{`expected ${r.expected_outcome}`}</span>}
                          </td>
                          <td className="mono">
                            <span className={pill(r.signals_right)}>{r.signals.join(' ') || 'none'}</span>
                            {!r.signals_right && <span className="muted letter-why">{`expected ${r.expected_signals.join(' ') || 'none'}`}</span>}
                          </td>
                          <td className="mono">
                            <span className={pill(r.payable_right)}>{chf(r.payable)}</span>
                            {!r.payable_right && <span className="muted letter-why">{`expected ${chf(r.expected_payable)}`}</span>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </div>
        </section>
      </div>
    </>
  )
}
