import { fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import ReaderBench from './ReaderBench'

afterEach(() => vi.unstubAllGlobals())

const kit = [
  { folder: 'kit-lukas-household', file: 'repair-quote.pdf', document_type: 'repair_quote', title: 'Broken living-room window', variant: null },
  { folder: 'claim-2-property', file: 'damage-photo-1.jpg', document_type: 'damage_photo', title: 'Water damage in the kitchen', variant: null },
  { folder: 'hard', file: 'repair-invoice-scan.pdf', document_type: 'repair_invoice', title: 'Water damage in the kitchen', variant: 'scan' },
]
const field = (name: string, status: string, expected: unknown, read: unknown, more: object = {}) => ({
  name,
  status,
  expected,
  read,
  expected_page: 1,
  page: read === null ? null : 1,
  page_right: read !== null,
  snippet: read === null ? null : String(read),
  confidence: read === null ? null : 0.91,
  ...more,
})
const result = {
  reader: 'omni',
  workflow: 'property',
  pages: 1,
  variant: null,
  error: null,
  seconds: { classify: 1.2, extract: 4.31 },
  type: { expected: 'repair_quote', read: 'repair_invoice', right: false },
  fields: [
    field('quote_number', 'right', 'GFB-Q-2026-0388', 'GFB-Q-2026-0388'),
    field('total', 'wrong', 1260, 1206),
    field('quote_date', 'missing', '2026-09-16', null),
    field('customer_name', 'right', 'Lukas Meier', 'Lukas Meier', { page: 2, page_right: false }),
    field('vat_number', 'extra', null, 'CHE-1', { expected_page: null, page_right: false }),
  ],
  counts: { right: 2, wrong: 1, missing: 1, extra: 1 },
  line_items: { expected: 4, read: 3, right: false },
}

const stub = (answer: object = result) => {
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (url === '/demo/kit') return Promise.resolve(Response.json(kit))
    if (url === '/bench/read' && init?.method === 'POST') return Promise.resolve(Response.json(answer))
    return Promise.resolve(new Response('', { status: 404 }))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

test('the documents of the kit are in one list, grouped by scenario, the hard versions marked', async () => {
  stub()
  render(<ReaderBench />)
  const pick = await screen.findByRole('combobox', { name: 'Document' })
  await within(pick).findByRole('option', { name: 'repair-quote.pdf · repair_quote' })
  expect(within(pick).getAllByRole('group').map((g) => g.getAttribute('label'))).toEqual([
    'Broken living-room window',
    'Water damage in the kitchen',
    'Hard versions (scans and phone photos)',
  ])
  expect(within(pick).getByRole('option', { name: 'repair-invoice-scan.pdf · repair_invoice · scan' })).toBeInTheDocument()
  // the picked document is shown next to the result: a PDF in a frame
  expect(screen.getByTitle('repair-quote.pdf')).toHaveAttribute('src', '/demo/kit/kit-lukas-household/repair-quote.pdf#toolbar=0&navpanes=0&view=FitH')
})

test('Read sends the picked document and shows each field with its status', async () => {
  const fetchMock = stub()
  render(<ReaderBench />)
  const pick = await screen.findByRole('combobox', { name: 'Document' })
  await within(pick).findByRole('option', { name: 'repair-quote.pdf · repair_quote' })
  fireEvent.click(screen.getByRole('button', { name: 'Read with the active reader' }))
  const table = await screen.findByRole('table', { name: 'Fields' })
  const post = fetchMock.mock.calls.find(([u]) => u === '/bench/read')![1]!
  expect(JSON.parse(post.body as string)).toEqual({ folder: 'kit-lukas-household', file: 'repair-quote.pdf' })

  const row = (name: string) => within(table).getByRole('row', { name: new RegExp(`^${name}`) })
  expect(within(row('quote_number')).getByText('right')).toHaveClass('pill-ok')
  expect(within(row('total')).getByText('wrong')).toHaveClass('pill-bad')
  expect(row('total')).toHaveTextContent('1260')
  expect(row('total')).toHaveTextContent('1206')
  expect(within(row('quote_date')).getByText('missing')).toHaveClass('pill-bad')
  expect(within(row('vat_number')).getByText('extra')).toHaveClass('pill-warn')
  // a right value on the wrong page is said: every field must point to its page
  expect(row('customer_name')).toHaveTextContent('page 2, expected 1')

  const summary = screen.getByRole('status')
  expect(summary).toHaveTextContent('Reader omni')
  expect(summary).toHaveTextContent('2 of 4 fields right')
  expect(summary).toHaveTextContent('1 wrong')
  expect(summary).toHaveTextContent('1 missing')
  expect(summary).toHaveTextContent('1 extra')
  expect(summary).toHaveTextContent('5.5 s')
  expect(summary).toHaveTextContent('Type: repair_invoice, expected repair_quote')
  expect(summary).toHaveTextContent('Line items: 3 read, 4 expected')
})

test('an image is shown as an image, and a reader error is shown in plain words', async () => {
  stub({ ...result, error: 'RuntimeError: the model did not answer' })
  render(<ReaderBench />)
  const pick = await screen.findByRole('combobox', { name: 'Document' })
  await within(pick).findByRole('option', { name: 'damage-photo-1.jpg · damage_photo' })
  fireEvent.change(pick, { target: { value: 'claim-2-property/damage-photo-1.jpg' } })
  expect(screen.getByRole('img', { name: 'damage-photo-1.jpg' })).toHaveAttribute('src', '/demo/kit/claim-2-property/damage-photo-1.jpg')
  expect(screen.queryByRole('table', { name: 'Fields' })).toBeNull() // a new pick clears the old result
  fireEvent.click(screen.getByRole('button', { name: 'Read with the active reader' }))
  expect(await screen.findByText('The reader failed: RuntimeError: the model did not answer')).toBeInTheDocument()
})

test('a failed request says so', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string) => Promise.resolve(url === '/demo/kit' ? Response.json(kit) : new Response('', { status: 500 }))),
  )
  render(<ReaderBench />)
  const pick = await screen.findByRole('combobox', { name: 'Document' })
  await within(pick).findByRole('option', { name: 'repair-quote.pdf · repair_quote' })
  fireEvent.click(screen.getByRole('button', { name: 'Read with the active reader' }))
  expect(await screen.findByText('The server did not answer.')).toBeInTheDocument()
})
