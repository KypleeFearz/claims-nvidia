import { useEffect, useState } from 'react'
import { json, send } from './api'

// The reader bench (issue #82): one document of the kit, read by the active reader, next to what a
// handler would type (its expected.json). To see one error fast. The numbers on the whole kit are issue #19.
type KitDoc = { folder: string; file: string; document_type: string | null; title: string | null; variant: string | null }
type Row = {
  name: string
  status: 'right' | 'wrong' | 'missing' | 'extra'
  expected: unknown
  read: unknown
  expected_page: number | null
  page: number | null
  page_right: boolean
  snippet: string | null
  confidence: number | null
}
type Result = {
  reader: string
  workflow: string
  error: string | null
  seconds: { classify: number; extract: number }
  type: { expected: string; read: string; right: boolean }
  fields: Row[]
  counts: { right: number; wrong: number; missing: number; extra: number }
  line_items: { expected: number; read: number; right: boolean }
}

const HARD = 'Hard versions (scans and phone photos)'
const PILL = { right: 'pill-ok', wrong: 'pill-bad', missing: 'pill-bad', extra: 'pill-warn' }
const idOf = (d: KitDoc) => `${d.folder}/${d.file}`
const show = (v: unknown) => (v === null || v === undefined ? '—' : String(v))

function pageOf(r: Row) {
  if (r.page === null) return '—'
  if (r.expected_page === null || r.page_right) return `page ${r.page}`
  return `page ${r.page}, expected ${r.expected_page}`
}

export default function ReaderBench() {
  const [kit, setKit] = useState<KitDoc[]>([])
  const [picked, setPicked] = useState('')
  const [result, setResult] = useState<Result | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    send<KitDoc[]>('/demo/kit')
      .then((answer) => {
        const docs = Array.isArray(answer) ? answer : [] // an answer that is not the list shows an empty picker
        setKit(docs)
        setPicked((p) => p || (docs[0] ? idOf(docs[0]) : ''))
      })
      .catch(() => setError('The server did not answer.'))
  }, [])

  const doc = kit.find((d) => idOf(d) === picked)
  const groups = [...new Set(kit.map((d) => (d.variant ? HARD : (d.title ?? d.folder))))]

  const pick = (id: string) => {
    setPicked(id)
    setResult(null) // the old result is of another document
    setError('')
  }
  const read = () => {
    if (!doc) return
    setBusy(true)
    setError('')
    setResult(null)
    send<Result>('/bench/read', json('POST', { folder: doc.folder, file: doc.file }))
      .then(setResult)
      .catch(() => setError('The server did not answer.'))
      .finally(() => setBusy(false))
  }

  const expected = result ? result.counts.right + result.counts.wrong + result.counts.missing : 0
  return (
    <>
      <header className="page-head">
        <div className="title">
          <span className="eyebrow">Tools · document reading</span>
          <h1>Reader bench</h1>
          <p>One document of the kit, read by the active reader, next to the values a handler would type.</p>
        </div>
      </header>
      <div className="panels">
        <section className="card panel panel-customer" aria-label="Document">
          <header className="panel-head">
            <span className="eyebrow">Document</span>
          </header>
          <div className="panel-body">
            <select aria-label="Document" value={picked} disabled={busy} onChange={(e) => pick(e.target.value)}>
              {groups.map((g) => (
                <optgroup key={g} label={g}>
                  {kit
                    .filter((d) => (d.variant ? HARD : (d.title ?? d.folder)) === g)
                    .map((d) => (
                      <option key={idOf(d)} value={idOf(d)}>
                        {[d.file, d.document_type ?? 'document', d.variant].filter(Boolean).join(' · ')}
                      </option>
                    ))}
                </optgroup>
              ))}
            </select>
            <button type="button" className="btn-primary" disabled={!doc || busy} onClick={read}>
              {busy ? 'Reading…' : 'Read with the active reader'}
            </button>
            {doc &&
              (doc.file.endsWith('.pdf') ? (
                <iframe className="bench-doc" title={doc.file} src={`/demo/kit/${idOf(doc)}#toolbar=0&navpanes=0&view=FitH`} />
              ) : (
                <img className="bench-doc" alt={doc.file} src={`/demo/kit/${idOf(doc)}`} />
              ))}
          </div>
        </section>
        <section className="card panel panel-pipeline" aria-label="Result">
          <header className="panel-head">
            <span className="eyebrow">Read against expected</span>
          </header>
          <div className="panel-body">
            {error && <p className="pill pill-bad">{error}</p>}
            {!result && !error && <p className="muted">{busy ? 'The reader works…' : 'Pick a document and press Read.'}</p>}
            {result && (
              <>
                {result.error && <p className="pill pill-bad">{`The reader failed: ${result.error}`}</p>}
                <p role="status" className="bench-summary">
                  <span className="pill pill-idle">{`Reader ${result.reader}`}</span>
                  <span className={`pill ${result.counts.right === expected ? 'pill-ok' : 'pill-bad'}`}>{`${result.counts.right} of ${expected} fields right`}</span>
                  {result.counts.wrong > 0 && <span className="pill pill-bad">{`${result.counts.wrong} wrong`}</span>}
                  {result.counts.missing > 0 && <span className="pill pill-bad">{`${result.counts.missing} missing`}</span>}
                  {result.counts.extra > 0 && <span className="pill pill-warn">{`${result.counts.extra} extra`}</span>}
                  <span className={`pill ${result.type.right ? 'pill-ok' : 'pill-bad'}`}>
                    {result.type.right ? `Type: ${result.type.read}` : `Type: ${result.type.read}, expected ${result.type.expected}`}
                  </span>
                  <span className={`pill ${result.line_items.right ? 'pill-ok' : 'pill-bad'}`}>
                    {`Line items: ${result.line_items.read} read, ${result.line_items.expected} expected`}
                  </span>
                  <span className="pill pill-idle">{`${(result.seconds.classify + result.seconds.extract).toFixed(1)} s`}</span>
                  <span className="pill pill-idle">{`workflow ${result.workflow}`}</span>
                </p>
                <div className="table-scroll">
                  <table aria-label="Fields">
                    <thead>
                      <tr>
                        <th>Field</th>
                        <th>Status</th>
                        <th>Expected</th>
                        <th>Read</th>
                        <th>Source</th>
                        <th>Confidence</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.fields.map((r) => (
                        <tr key={r.name}>
                          <th scope="row">{r.name}</th>
                          <td>
                            <span className={`pill ${PILL[r.status]}`}>{r.status}</span>
                          </td>
                          <td className="mono">{show(r.expected)}</td>
                          <td className="mono">{show(r.read)}</td>
                          <td className={r.page !== null && r.expected_page !== null && !r.page_right ? 'bench-bad' : 'muted'}>
                            {pageOf(r)}
                            {r.snippet ? ` · “${r.snippet}”` : ''}
                          </td>
                          <td className="mono">{r.confidence === null ? '—' : r.confidence.toFixed(2)}</td>
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
