import { fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import Metrics from './Metrics'

afterEach(() => vi.unstubAllGlobals())

// Builders for the report of the contract (schema_version 1). Only the fields the page reads are exact.
const fc = (more: { right?: number; wrong?: number; read?: number; [k: string]: unknown } = {}) => ({
  expected: 0,
  right: 0,
  wrong: 0,
  missing: 0,
  extra: 0,
  read: (more.right ?? 0) + (more.wrong ?? 0), // the usual case: every right or wrong value was read
  page_right: 0,
  verified: 0,
  accuracy: null,
  verified_accuracy: null,
  page_accuracy: null,
  ...more,
})
const tm = (mean: number | null, more: object = {}) => ({ count: 2, total: 0, mean, median: mean, min: mean, max: mean, ...more })
const stat = (mean: number, min: number, max: number) => ({ mean, min, max, spread: max - min })
const dc = (count: number, passed: number, errors = 0) => ({ count, passed, pass_rate: count ? passed / count : null, errors, classified_right: count })
const lines = (expected: number, right: number) => ({ documents: 1, documents_right: 1, expected, read: right, right })

const CREATED = '2026-11-18T09:30:00Z'
const report = {
  schema_version: 1,
  created_at: CREATED,
  reader: 'omni',
  command: 'python -m app.docintel.evaluate --reader omni --runs 2',
  settings: { reader: 'omni', model: 'nvidia/omni-test', dpi: 150 },
  runs: 2,
  complete: true,
  kit: { documents: 3, by_kind: { clean: 2, scan: 1 }, expected_fields: 6, expected_line_items: 3 },
  answer_key_errors: [],
  summary: {
    accuracy: stat(0.8333, 0.7, 0.8),
    verified_accuracy: stat(0.75, 0.6, 0.65),
    page_accuracy: stat(0.9091, 0.85, 0.95),
    pass_rate: stat(0.8333, 0.5, 0.75),
    seconds_per_document: stat(2.345, 2, 3),
    wrong: stat(0.5, 0, 1),
    missing: stat(0.5, 0, 1),
    errors: stat(0.5, 0, 1),
  },
  pooled: {
    documents: dc(6, 5, 1),
    per_document: [
      {
        id: 'claim-1/invoice.pdf',
        document_type: 'invoice',
        kind: 'clean',
        runs: 2,
        passed_runs: 2,
        classified_right_runs: 2,
        fields: fc({ expected: 4, right: 4 }),
        seconds: tm(1.234),
        errors: [],
      },
      {
        id: 'claim-1/receipt.pdf',
        document_type: 'receipt',
        kind: 'clean',
        runs: 2,
        passed_runs: 2,
        classified_right_runs: 2,
        fields: fc({ expected: 4, right: 4 }),
        seconds: tm(0.5),
        errors: [],
      },
      {
        id: 'claim-1/invoice-scan.pdf',
        document_type: 'invoice',
        kind: 'scan',
        runs: 2,
        passed_runs: 1,
        classified_right_runs: 1,
        fields: fc({ expected: 4, right: 2, wrong: 1, missing: 1 }),
        seconds: tm(4.5),
        errors: ['model timeout'],
      },
    ],
    fields: fc({ expected: 12, right: 10, wrong: 1, missing: 1, extra: 1, page_right: 10, verified: 9, accuracy: 10 / 12, verified_accuracy: 0.75, page_accuracy: 10 / 11 }),
    line_items: lines(6, 5),
    seconds: tm(2.345, { median: 2, max: 5.678 }),
    by_kind: {
      scan: { documents: dc(2, 1, 1), fields: fc({ expected: 4, right: 2, wrong: 1, missing: 1, accuracy: 0.5 }), line_items: lines(3, 2), seconds: tm(4) },
      clean: { documents: dc(4, 4), fields: fc({ expected: 8, right: 8, accuracy: 1 }), line_items: lines(3, 3), seconds: tm(1.5) },
    },
    by_type: {
      invoice: {
        documents: dc(4, 3, 1),
        fields: fc({ expected: 8, right: 6, wrong: 1, missing: 1 }),
        line_items: lines(6, 5),
        seconds: tm(3),
        by_field: {
          total_amount: fc({ expected: 4, right: 3, wrong: 1, page_right: 3, accuracy: 0.75 }),
          invoice_date: fc({ expected: 4, right: 3, missing: 1, page_right: 3, accuracy: 0.75 }),
        },
      },
      receipt: {
        documents: dc(2, 2),
        fields: fc({ expected: 4, right: 4 }),
        line_items: lines(0, 0),
        seconds: tm(1.2),
        by_field: { merchant: fc({ expected: 2, right: 2, page_right: 2, accuracy: 1 }) },
      },
    },
    by_field: {},
  },
  http: { requests: 20, by_status: { '200': 19, '429': 1 }, transport_errors: 0, http_429: 1 },
  run_details: [],
}

const stub = (make: () => Promise<Response>) => {
  const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(() => make())
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}
const answer = (body: object, status = 200) => stub(() => Promise.resolve(Response.json(body, { status })))

const lastRun = () => screen.getByRole('region', { name: 'Last run' })
const tile = (label: string) => within(lastRun()).getByText(label).closest('.tile') as HTMLElement
const cells = (row: HTMLElement) => within(row).getAllByRole('cell').map((c) => c.textContent)

test('the report: the summary pills, the tiles and the range over the runs', async () => {
  answer(report)
  const { container } = render(<Metrics />)
  const summary = await within(lastRun()).findByRole('status')
  expect(summary).toHaveTextContent('Reader omni')
  expect(within(summary).getByText('2 runs')).toBeInTheDocument()
  expect(within(summary).getByText(new Date(CREATED).toLocaleString())).toBeInTheDocument()
  expect(within(summary).getByText('Model nvidia/omni-test')).toBeInTheDocument()
  expect(within(summary).getByText('3 documents')).toBeInTheDocument()
  expect(within(summary).getByText('429: 1')).toHaveClass('pill-warn')
  expect(within(summary).queryByText(/answer keys/)).not.toBeInTheDocument()

  expect(tile('Fields right')).toHaveTextContent('83.3%')
  expect(tile('Fields right')).toHaveTextContent('10 of 12 · 1 wrong · 1 missing · runs 70.0%–80.0%')
  expect(tile('Right value, right page')).toHaveTextContent('75.0%')
  expect(tile('Right value, right page')).toHaveTextContent('9 of 12 fields · runs 60.0%–65.0%')
  expect(tile('Source page right')).toHaveTextContent('90.9%')
  expect(tile('Source page right')).toHaveTextContent('of the 11 values read · runs 85.0%–95.0%')
  expect(tile('Documents all right')).toHaveTextContent('83.3%')
  expect(tile('Documents all right')).toHaveTextContent('5 of 6 documents · 1 reader errors · runs 50.0%–75.0%')
  expect(tile('Seconds per document')).toHaveTextContent('2.3 s')
  expect(tile('Seconds per document')).toHaveTextContent('median 2.0 · max 5.7')
  expect(tile('Line items right')).toHaveTextContent('5 of 6')
  expect(tile('Line items right')).toHaveTextContent('scored apart: not in the field accuracy')
  expect(tile('Type right')).toHaveTextContent('6 of 6')
  expect(tile('Type right')).toHaveTextContent('classification, scored apart')
  expect(container).not.toHaveTextContent(/fraud|rejected/i)
})

test('the page sends one GET and nothing else; Reload sends the same GET again', async () => {
  const fetchMock = answer(report)
  render(<Metrics />)
  await within(lastRun()).findByRole('status')
  expect(fetchMock).toHaveBeenCalledTimes(1)
  fireEvent.click(within(lastRun()).getByRole('button', { name: 'Reload' }))
  await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
  for (const [url, init] of fetchMock.mock.calls) {
    expect(url).toBe('/evaluation/latest')
    expect(init?.method ?? 'GET').toBe('GET')
    expect(init?.body).toBeUndefined()
  }
  expect(await within(lastRun()).findByRole('status')).toBeInTheDocument()
})

test('by kind: clean, scan, then the others, with the numbers of each row', async () => {
  answer(report)
  render(<Metrics />)
  const table = await screen.findByRole('table', { name: 'By document kind' })
  const rows = within(table).getAllByRole('row').slice(1)
  expect(rows.map((r) => within(r).getByRole('rowheader').textContent)).toEqual(['clean', 'scan']) // the report gives scan first
  expect(cells(rows[0])).toEqual(['4', '8', '0', '0', '100%', '4 of 4', '1.5'])
  expect(cells(rows[1])).toEqual(['2', '2', '1', '1', '50.0%', '1 of 2', '4.0'])
})

test('by type: one table per type, with the counts of each field and the line items', async () => {
  answer(report)
  render(<Metrics />)
  const panel = await screen.findByRole('region', { name: 'By document type' })
  expect(within(panel).getAllByRole('heading', { level: 3 }).map((h) => h.textContent)).toEqual(['invoice', 'receipt'])
  expect(panel).toHaveTextContent('4 documents · 3 all right · 3.0 s per document')
  expect(panel).toHaveTextContent('2 documents · 2 all right · 1.2 s per document')

  const invoice = within(panel).getByRole('table', { name: 'invoice' })
  const amount = within(invoice).getByRole('row', { name: /^total_amount/ })
  const amountCells = within(amount).getAllByRole('cell')
  expect(cells(amount)).toEqual(['4', '3', '1', '0', '0', '75.0%', '3 of 4'])
  expect(amountCells[2]).toHaveClass('bench-bad') // wrong
  expect(amountCells[3]).not.toHaveClass('bench-bad') // missing is 0
  const date = within(invoice).getByRole('row', { name: /^invoice_date/ })
  expect(within(date).getAllByRole('cell')[3]).toHaveClass('bench-bad') // missing
  expect(cells(date)).toEqual(['4', '3', '0', '1', '0', '75.0%', '3 of 3'])
  expect(within(invoice).getByRole('row', { name: /^line items/ })).toHaveTextContent('5 of 6 lines right')

  const receipt = within(panel).getByRole('table', { name: 'receipt' })
  expect(cells(within(receipt).getByRole('row', { name: /^merchant/ }))).toEqual(['2', '2', '0', '0', '0', '100%', '2 of 2'])
  expect(within(receipt).queryByRole('row', { name: /^line items/ })).not.toBeInTheDocument() // none expected
})

test('documents: how many runs were all right, the numbers and the reader error', async () => {
  answer(report)
  render(<Metrics />)
  const table = await screen.findByRole('table', { name: 'Documents' })
  const good = within(table).getByRole('row', { name: /^claim-1\/invoice\.pdf/ })
  expect(within(good).getByText('2 of 2')).toHaveClass('pill-ok')
  expect(cells(good)).toEqual(['invoice', 'clean', '2 of 2', '4', '0', '0', '1.23', '—'])
  const bad = within(table).getByRole('row', { name: /^claim-1\/invoice-scan\.pdf/ })
  expect(within(bad).getByText('1 of 2')).toHaveClass('pill-bad')
  expect(within(bad).getByText('model timeout')).toHaveClass('pill-bad')
  expect(cells(bad)).toEqual(['invoice', 'scan', '1 of 2', '2', '1', '1', '4.50', 'model timeout'])
})

test('no report yet: the empty state gives the command', async () => {
  answer({ detail: 'no evaluation report yet' }, 404)
  render(<Metrics />)
  expect(await screen.findByText('No evaluation report yet.')).toBeInTheDocument()
  expect(screen.getByText('Write one with:')).toBeInTheDocument()
  expect(screen.getByText('cd backend && uv run python -m app.docintel.evaluate --reader fixture').tagName).toBe('CODE')
  expect(screen.queryByRole('table')).not.toBeInTheDocument()
})

test('a server error, or a report of another schema, says the report cannot be read', async () => {
  answer({ detail: 'the latest evaluation report cannot be read' }, 500)
  const first = render(<Metrics />)
  expect(await screen.findByText('The last report cannot be read.')).toHaveClass('pill-bad')
  first.unmount()

  answer({ ...report, schema_version: 2 })
  render(<Metrics />)
  expect(await screen.findByText('The last report cannot be read.')).toHaveClass('pill-bad')
  expect(screen.queryByRole('table')).not.toBeInTheDocument()
})

test('a server that does not answer is said apart from a bad report', async () => {
  stub(() => Promise.reject(new TypeError('Failed to fetch')))
  render(<Metrics />)
  expect(await screen.findByText('The server did not answer.')).toHaveClass('pill-bad')
  expect(screen.queryByText('The last report cannot be read.')).not.toBeInTheDocument()
})

test('a report with answer keys that could not be read lists them', async () => {
  answer({ ...report, complete: false, answer_key_errors: [{ document: 'claim-9/x.pdf', error: 'expected.json is not valid' }, { document: 'claim-8/y.pdf', error: 'no expected.json' }] })
  render(<Metrics />)
  const summary = await within(lastRun()).findByRole('status')
  expect(within(summary).getByText('2 answer keys could not be read')).toHaveClass('pill-bad')
  expect(within(lastRun()).getByRole('list')).toHaveTextContent('claim-9/x.pdf: expected.json is not valid')
  expect(within(lastRun()).getByRole('list')).toHaveTextContent('claim-8/y.pdf: no expected.json')
})

test('a report of the fixture reader has no model, no 429 and no range over runs', async () => {
  answer({ ...report, reader: 'fixture', settings: { reader: 'fixture' }, runs: 1, http: null })
  render(<Metrics />)
  const summary = await within(lastRun()).findByRole('status')
  expect(summary).toHaveTextContent('Reader fixture')
  expect(within(summary).getByText('1 run')).toBeInTheDocument()
  expect(summary).not.toHaveTextContent(/Model|429/)
  expect(tile('Fields right')).toHaveTextContent('10 of 12 · 1 wrong · 1 missing')
  expect(lastRun()).not.toHaveTextContent('runs 70.0%')
  expect(lastRun()).not.toHaveTextContent(/ · runs /)
})

test('failed requests of the endpoint are said, so an outage does not look like missing fields', async () => {
  answer({ ...report, http: { requests: 70, by_status: { '200': 60, '401': 5, '429': 2 }, transport_errors: 3, http_429: 2 } })
  render(<Metrics />)
  const summary = await within(lastRun()).findByRole('status')
  expect(within(summary).getByText('70 requests')).toBeInTheDocument()
  expect(within(summary).getByText('429: 2')).toHaveClass('pill-warn')
  expect(within(summary).getByText('8 requests failed (401: 5, no answer: 3)')).toHaveClass('pill-bad')
})

test('no failed request: no failure pill', async () => {
  answer(report)
  render(<Metrics />)
  const summary = await within(lastRun()).findByRole('status')
  expect(within(summary).getByText('20 requests')).toBeInTheDocument()
  expect(summary).not.toHaveTextContent(/failed/)
})

test('a report of schema 1 with a part missing says it cannot be read, and does not crash', async () => {
  for (const broken of [
    { ...report, settings: undefined },
    { ...report, kit: null },
    { ...report, pooled: { ...report.pooled, by_kind: undefined } },
    { ...report, pooled: { ...report.pooled, per_document: {} } },
  ]) {
    answer(broken)
    const page = render(<Metrics />)
    expect(await screen.findByText('The last report cannot be read.')).toHaveClass('pill-bad')
    page.unmount()
  }
})

test('the page right counts only the values read: a field expected empty is out of it', async () => {
  const one = fc({ expected: 2, right: 2, read: 1, page_right: 1, verified: 2, accuracy: 1 })
  const byType = { invoice: { ...report.pooled.by_type.invoice, by_field: { note: one } } }
  answer({ ...report, pooled: { ...report.pooled, by_type: byType } })
  render(<Metrics />)
  const table = await screen.findByRole('table', { name: 'invoice' })
  expect(cells(within(table).getByRole('row', { name: /^note/ }))).toEqual(['2', '2', '0', '0', '0', '100%', '1 of 1'])
})
