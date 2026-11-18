import { useCallback, useEffect, useState } from 'react'
import { send } from './api'

// Metrics (issue #19): the last evaluation of the reader on the whole kit, field by field.
// A command writes the report (`python -m app.docintel.evaluate`); this page only reads it. It never starts a model.
// The types are the contract of the report, schema_version 1.
type Stat = { mean: number | null; min: number | null; max: number | null; spread: number | null }
type Timing = { count: number; total: number; mean: number | null; median: number | null; min: number | null; max: number | null }
type FieldCounts = {
  expected: number
  right: number
  wrong: number
  missing: number
  extra: number
  read: number // values read: right + wrong, less a field expected empty and not read
  page_right: number
  verified: number
  accuracy: number | null
  verified_accuracy: number | null
  page_accuracy: number | null
}
type DocCounts = { count: number; passed: number; pass_rate: number | null; errors: number; classified_right: number }
type LineCounts = { documents: number; documents_right: number; expected: number; read: number; right: number }
type Aggregate = { documents: DocCounts; fields: FieldCounts; line_items: LineCounts; seconds: Timing }
type TypeAggregate = Aggregate & { by_field: Record<string, FieldCounts> }
type DocSummary = {
  id: string
  document_type: string
  kind: string
  runs: number
  passed_runs: number
  classified_right_runs: number
  fields: FieldCounts
  seconds: Timing
  errors: string[]
}
type Http = { requests: number; by_status: Record<string, number>; transport_errors: number; http_429: number }
type Report = {
  schema_version: 1
  created_at: string
  reader: string
  command: string
  settings: Record<string, unknown>
  runs: number
  complete: boolean
  kit: { documents: number; by_kind: Record<string, number>; expected_fields: number; expected_line_items: number }
  answer_key_errors: { document: string; error: string }[]
  summary: {
    accuracy: Stat
    verified_accuracy: Stat
    page_accuracy: Stat
    pass_rate: Stat
    seconds_per_document: Stat
    wrong: Stat
    missing: Stat
    errors: Stat
  }
  // the counts of every run added together; `per_document` is one line per document of the kit
  pooled: Aggregate & {
    by_type: Record<string, TypeAggregate>
    by_kind: Record<string, Aggregate>
    by_field: Record<string, FieldCounts>
    per_document: DocSummary[]
  }
  http: Http | null
  run_details: unknown[]
}

type State = { kind: 'loading' } | { kind: 'none' } | { kind: 'bad' } | { kind: 'down' } | { kind: 'report'; report: Report }

const COMMAND = 'cd backend && uv run python -m app.docintel.evaluate --reader fixture'
const KIND_ORDER = ['clean', 'scan', 'photo']

const percent = (share: number | null) => {
  if (share === null) return '—'
  if (share === 1) return '100%'
  if (share === 0) return '0%'
  return `${(share * 100).toFixed(1)}%`
}
const secs = (v: number | null, digits: number) => (v === null ? '—' : v.toFixed(digits))
const range = (s: Stat) => ` · runs ${percent(s.min)}–${percent(s.max)}`

function Tile({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="card tile">
      <span className="eyebrow">{label}</span>
      <strong>{value}</strong>
      {note && <span className="muted">{note}</span>}
    </div>
  )
}

function Tiles({ report }: { report: Report }) {
  const { pooled, summary } = report
  const f = pooled.fields
  const docs = pooled.documents
  const many = report.runs > 1
  return (
    <div className="tiles">
      <Tile
        label="Fields right"
        value={percent(f.accuracy)}
        note={`${f.right} of ${f.expected} · ${f.wrong} wrong · ${f.missing} missing${many ? range(summary.accuracy) : ''}`}
      />
      <Tile
        label="Right value, right page"
        value={percent(f.verified_accuracy)}
        note={`${f.verified} of ${f.expected} fields${many ? range(summary.verified_accuracy) : ''}`}
      />
      <Tile
        label="Source page right"
        value={percent(f.page_accuracy)}
        note={`of the ${f.read} values read${many ? range(summary.page_accuracy) : ''}`}
      />
      <Tile
        label="Documents all right"
        value={percent(docs.pass_rate)}
        note={`${docs.passed} of ${docs.count} documents · ${docs.errors} reader errors${many ? range(summary.pass_rate) : ''}`}
      />
      <Tile
        label="Seconds per document"
        value={pooled.seconds.mean === null ? '—' : `${pooled.seconds.mean.toFixed(1)} s`}
        note={`median ${secs(pooled.seconds.median, 1)} · max ${secs(pooled.seconds.max, 1)}`}
      />
      <Tile label="Line items right" value={`${pooled.line_items.right} of ${pooled.line_items.expected}`} note="scored apart: not in the field accuracy" />
      <Tile
        label="Type right"
        value={`${docs.classified_right} of ${docs.count}`}
        note="classification, scored apart: not in the field accuracy or the pass rate"
      />
    </div>
  )
}

// The Omni reader never raises: when the endpoint fails, its fields are only "missing". The failed
// requests show that it was the endpoint, not the page. A 429 is apart: the reader waits and tries again.
function HttpPills({ http }: { http: Http }) {
  const refused = Object.entries(http.by_status).filter(([code]) => Number(code) >= 400 && code !== '429')
  const failed = http.transport_errors + refused.reduce((n, [, count]) => n + count, 0)
  const detail = [...refused.map(([code, n]) => `${code}: ${n}`), ...(http.transport_errors ? [`no answer: ${http.transport_errors}`] : [])]
  return (
    <>
      <span className="pill pill-idle">{`${http.requests} requests`}</span>
      <span className={`pill ${http.http_429 === 0 ? 'pill-ok' : 'pill-warn'}`}>{`429: ${http.http_429}`}</span>
      {failed > 0 && <span className="pill pill-bad">{`${failed} requests failed (${detail.join(', ')})`}</span>}
    </>
  )
}

function LastRun({ report }: { report: Report }) {
  const model = report.settings.model
  return (
    <>
      <p role="status" className="bench-summary">
        <span className="pill pill-idle">{`Reader ${report.reader}`}</span>
        <span className="pill pill-idle">{`${report.runs} ${report.runs === 1 ? 'run' : 'runs'}`}</span>
        <span className="pill pill-idle">{new Date(report.created_at).toLocaleString()}</span>
        {typeof model === 'string' && <span className="pill pill-idle">{`Model ${model}`}</span>}
        <span className="pill pill-idle">{`${report.kit.documents} documents`}</span>
        {report.http && <HttpPills http={report.http} />}
        {!report.complete && <span className="pill pill-bad">{`${report.answer_key_errors.length} answer keys could not be read`}</span>}
      </p>
      {!report.complete && (
        <ul>
          {report.answer_key_errors.map((e) => (
            <li key={e.document}>{`${e.document}: ${e.error}`}</li>
          ))}
        </ul>
      )}
      <Tiles report={report} />
    </>
  )
}

function ByKind({ pooled }: { pooled: Report['pooled'] }) {
  const keys = Object.keys(pooled.by_kind)
  const kinds = [...KIND_ORDER.filter((k) => keys.includes(k)), ...keys.filter((k) => !KIND_ORDER.includes(k))]
  return (
    <div className="table-scroll">
      <table aria-label="By document kind">
        <thead>
          <tr>
            <th>Kind</th>
            <th>Documents</th>
            <th>Fields right</th>
            <th>Wrong</th>
            <th>Missing</th>
            <th>Accuracy</th>
            <th>All right</th>
            <th>Seconds per document</th>
          </tr>
        </thead>
        <tbody>
          {kinds.map((k) => {
            const a = pooled.by_kind[k]
            return (
              <tr key={k}>
                <th scope="row">{k}</th>
                <td className="mono">{a.documents.count}</td>
                <td className="mono">{a.fields.right}</td>
                <td className="mono">{a.fields.wrong}</td>
                <td className="mono">{a.fields.missing}</td>
                <td className="mono">{percent(a.fields.accuracy)}</td>
                <td className="mono">{`${a.documents.passed} of ${a.documents.count}`}</td>
                <td className="mono">{secs(a.seconds.mean, 1)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function TypeTable({ type, agg }: { type: string; agg: TypeAggregate }) {
  const bad = (n: number) => (n > 0 ? 'mono bench-bad' : 'mono')
  return (
    <>
      <h3>{type}</h3>
      <p className="muted">{`${agg.documents.count} documents · ${agg.documents.passed} all right · ${secs(agg.seconds.mean, 1)} s per document`}</p>
      <div className="table-scroll">
        <table aria-label={type}>
          <thead>
            <tr>
              <th>Field</th>
              <th>Expected</th>
              <th>Right</th>
              <th>Wrong</th>
              <th>Missing</th>
              <th>Extra</th>
              <th>Accuracy</th>
              <th>Page right</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(agg.by_field).map(([name, c]) => (
              <tr key={name}>
                <th scope="row">{name}</th>
                <td className="mono">{c.expected}</td>
                <td className="mono">{c.right}</td>
                <td className={bad(c.wrong)}>{c.wrong}</td>
                <td className={bad(c.missing)}>{c.missing}</td>
                <td className="mono">{c.extra}</td>
                <td className="mono">{percent(c.accuracy)}</td>
                <td className="mono">{c.read === 0 ? '—' : `${c.page_right} of ${c.read}`}</td>
              </tr>
            ))}
            {agg.line_items.expected > 0 && (
              <tr>
                <th scope="row">line items</th>
                <td className="mono" colSpan={7}>{`${agg.line_items.right} of ${agg.line_items.expected} lines right`}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  )
}

function Documents({ docs }: { docs: DocSummary[] }) {
  return (
    <div className="table-scroll">
      <table aria-label="Documents">
        <thead>
          <tr>
            <th>Document</th>
            <th>Type</th>
            <th>Kind</th>
            <th>All right</th>
            <th>Right</th>
            <th>Wrong</th>
            <th>Missing</th>
            <th>Seconds</th>
            <th>Error</th>
          </tr>
        </thead>
        <tbody>
          {docs.map((d) => (
            <tr key={d.id}>
              <th scope="row">{d.id}</th>
              <td>{d.document_type}</td>
              <td>{d.kind}</td>
              <td>
                <span className={`pill ${d.passed_runs === d.runs ? 'pill-ok' : 'pill-bad'}`}>{`${d.passed_runs} of ${d.runs}`}</span>
              </td>
              <td className="mono">{d.fields.right}</td>
              <td className="mono">{d.fields.wrong}</td>
              <td className="mono">{d.fields.missing}</td>
              <td className="mono">{secs(d.seconds.mean, 2)}</td>
              <td>{d.errors.length > 0 ? <span className="pill pill-bad">{d.errors.join('; ')}</span> : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// A status error from `send` is `Error('404')`; a fetch that rejects (no server) is a TypeError.
const isStatus = (e: unknown) => e instanceof Error && /^\d+$/.test(e.message)
// The page shows a report only when every part that it reads is there: a broken file is "cannot be read",
// not a blank page.
const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const readable = (r: unknown): r is Report => {
  if (!isObject(r) || r.schema_version !== 1) return false
  const { pooled, http } = r
  return (
    [r.settings, r.kit, r.summary].every(isObject) &&
    Array.isArray(r.answer_key_errors) &&
    (http === null || isObject(http)) &&
    isObject(pooled) &&
    [pooled.documents, pooled.fields, pooled.line_items, pooled.seconds, pooled.by_type, pooled.by_kind].every(isObject) &&
    Array.isArray(pooled.per_document)
  )
}

export default function Metrics() {
  const [state, setState] = useState<State>({ kind: 'loading' })

  const load = useCallback(
    () =>
      send<unknown>('/evaluation/latest')
        .then((r) => setState(readable(r) ? { kind: 'report', report: r } : { kind: 'bad' }))
        .catch((e: unknown) => setState(!isStatus(e) ? { kind: 'down' } : (e as Error).message === '404' ? { kind: 'none' } : { kind: 'bad' })),
    [],
  )
  useEffect(() => {
    void load()
  }, [load])

  const report = state.kind === 'report' ? state.report : null
  return (
    <>
      <header className="page-head">
        <div className="title">
          <span className="eyebrow">Numbers · reading</span>
          <h1>Metrics</h1>
          <p>The last evaluation of the reader on the whole kit, field by field. A command writes the report; this page only reads it.</p>
        </div>
      </header>
      <div className="numbers">
        <section className="card panel" aria-label="Last run">
          <header className="panel-head">
            <span className="eyebrow">Last run</span>
            <button type="button" onClick={() => void load()}>
              Reload
            </button>
          </header>
          <div className="panel-body">
            {state.kind === 'loading' && <p className="muted">Loading…</p>}
            {state.kind === 'none' && (
              <>
                <p className="muted">No evaluation report yet.</p>
                <p className="muted">Write one with:</p>
                <code>{COMMAND}</code>
              </>
            )}
            {state.kind === 'bad' && <p className="pill pill-bad">The last report cannot be read.</p>}
            {state.kind === 'down' && <p className="pill pill-bad">The server did not answer.</p>}
            {report && <LastRun report={report} />}
          </div>
        </section>

        {report && (
          <>
            <section className="card panel" aria-label="By document kind">
              <header className="panel-head">
                <span className="eyebrow">By document kind</span>
              </header>
              <div className="panel-body">
                <ByKind pooled={report.pooled} />
              </div>
            </section>

            <section className="card panel" aria-label="By document type">
              <header className="panel-head">
                <span className="eyebrow">By document type</span>
              </header>
              <div className="panel-body">
                {Object.entries(report.pooled.by_type).map(([type, agg]) => (
                  <TypeTable key={type} type={type} agg={agg} />
                ))}
              </div>
            </section>

            <section className="card panel" aria-label="Documents">
              <header className="panel-head">
                <span className="eyebrow">Documents</span>
              </header>
              <div className="panel-body">
                <Documents docs={report.pooled.per_document} />
              </div>
            </section>
          </>
        )}
      </div>
    </>
  )
}
