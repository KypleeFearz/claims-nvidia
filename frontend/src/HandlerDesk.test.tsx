import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import HandlerDesk from './HandlerDesk'

afterEach(() => vi.unstubAllGlobals())

const row = (claim_id: string, status: string, more: object = {}) => ({
  claim_id,
  customer: 'Marco Bianchi',
  policy_number: 'HH-2024-0933',
  type: 'property',
  claimed_amount: 3200,
  status,
  outcome: 'handler_review',
  score: 0,
  reasons: ['Claimed amount CHF 3,200.00 is above the straight-through limit of CHF 2,000.'],
  opened_at: '2026-11-16T08:00:00Z',
  ...more,
})
const rows = [
  row('CL-open', 'open', { outcome: null, reasons: [] }),
  row('CL-review', 'in_review'),
  row('CL-done', 'validated', { outcome: 'straight_through', customer: 'Anna Keller' }),
  row('CL-siu', 'in_investigation', { outcome: 'investigation', score: 130, customer: 'Lukas Meier' }),
  row('CL-wait', 'waiting_customer'),
]
const full = (status: string, decisions: object[] = []) => ({
  id: 'CL-review',
  customer: { id: 'C-1002', name: 'Marco Bianchi' },
  policy: { number: 'HH-2024-0933', line_of_business: 'Household' },
  type: 'property',
  description: 'Water damage in the kitchen',
  incident_date: '2026-09-14',
  claimed_amount: 3200,
  cause: 'water',
  status,
  outcome: 'handler_review',
  score: 0,
  outcome_reasons: ['Claimed amount CHF 3,200.00 is above the straight-through limit of CHF 2,000.'],
  signals: [],
  documents: [
    {
      id: 'D-1',
      file: 'CL-review/D-1.pdf',
      document_type: 'repair_invoice',
      fields: [{ name: 'total', value: 3200, source: { page: 1, snippet: 'CHF 3,200.00' } }],
      file_metadata: { original_name: 'repair-invoice.pdf' },
    },
  ],
  fraud_report: null,
  proposal: {
    wording: 'General conditions Household, edition 2024',
    payable: 3000,
    needs_person: [],
    lines: [
      { label: 'Documented loss', amount: 3200, reason: 'Total of the repair invoice.', article: 'Art. 5.1: The insurer pays the cost of the repair.', sources: [{ document_id: 'D-1', page: 1, snippet: 'CHF 3,200.00' }] },
      { label: 'Deductible', amount: -200, reason: 'Deductible of the policy: 200.00.', article: 'Art. 8.1: The deductible.', sources: [] },
    ],
  },
  decisions,
})
const messages = [
  { role: 'agent', text: 'What happened?', time: '2026-11-16T08:00:01Z' },
  { role: 'customer', text: 'Water damage in the kitchen.', time: '2026-11-16T08:00:05Z' },
]

type Call = [string, RequestInit | undefined]
const stub = (decision: (body: { action: string }) => Response = () => Response.json(full('approved'))) => {
  let status = 'in_review'
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (url === '/claims') return Promise.resolve(Response.json(rows))
    if (url === '/handlers') return Promise.resolve(Response.json(['Sofia Brunner', 'David Frei']))
    if (url === '/claims/CL-review/messages') return Promise.resolve(Response.json(messages))
    if (url === '/claims/CL-review/history')
      return Promise.resolve(
        Response.json({
          person: { name: 'Marco Bianchi', account: 'MOCK-ACCT-1002', phone: '+41 00 000 10 02', address: 'Via Nassa 18, 6900 Lugano' },
          earlier_claims: [{ id: 'H-2025-0618', date: '2025-02-11', line: 'Household', cause: 'breakage', amount: 380, result: 'paid', provider: 'Vetro Ticino SA' }],
          links: [
            {
              person: 'X-9001',
              name: 'Reto Vogel',
              shared: ['account', 'phone'],
              claims: [{ id: 'H-2025-1120', date: '2025-09-19', line: 'Travel', cause: 'cancellation', amount: 4100, result: 'fraud', provider: null }],
            },
          ],
        }),
      )
    if (url === '/claims/CL-review/decisions' && init?.method === 'POST') {
      const res = decision(JSON.parse(init.body as string) as { action: string })
      if (res.ok) status = 'approved'
      return Promise.resolve(res)
    }
    if (url === '/claims/CL-review') return Promise.resolve(Response.json(full(status)))
    return Promise.resolve(new Response('', { status: 404 }))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}
const open = async () => {
  fireEvent.click(await screen.findByRole('button', { name: /CL-review/ }))
  return screen.findByRole('region', { name: 'Case' })
}

test('the queue shows the claims that wait for a person, investigations first, and All shows every claim', async () => {
  stub()
  render(<HandlerDesk />)
  const queue = screen.getByRole('region', { name: 'Queue' })
  await within(queue).findByRole('button', { name: /CL-review/ })
  const ids = () => within(queue).getAllByRole('button', { name: /^CL-/ }).map((b) => b.querySelector('.mono')?.textContent)
  expect(ids()).toEqual(['CL-siu', 'CL-review', 'CL-wait'])
  expect(within(queue).getByRole('button', { name: /CL-siu/ })).toHaveTextContent('Lukas Meier')
  expect(within(queue).getByRole('button', { name: /CL-siu/ })).toHaveTextContent('in investigation')
  fireEvent.click(within(queue).getByRole('checkbox', { name: 'Show every claim' }))
  expect(ids()).toEqual(['CL-siu', 'CL-review', 'CL-wait', 'CL-open', 'CL-done'])
})

test('the case page shows the facts, why the claim is here, the fields with their source and the conversation', async () => {
  stub()
  render(<HandlerDesk />)
  const page = await open()
  expect(within(page).getByRole('heading', { level: 2 })).toHaveTextContent('CL-review')
  expect(page).toHaveTextContent('Marco Bianchi')
  expect(page).toHaveTextContent('HH-2024-0933')
  expect(page).toHaveTextContent('Water damage in the kitchen')
  expect(page).toHaveTextContent('2026-09-14')
  expect(page).toHaveTextContent('CHF 3,200.00')
  expect(within(page).getByLabelText('Outcome')).toHaveTextContent('above the straight-through limit')
  expect(within(page).getByLabelText('Extracted data')).toHaveTextContent('page 1 · “CHF 3,200.00”')
  expect(within(page).getByLabelText('Conversation')).toHaveTextContent('Water damage in the kitchen.')
  // the proposal: each line with its article and its source, and the amount ready in the form
  const proposal = within(page).getByLabelText('Settlement proposal')
  expect(proposal).toHaveTextContent('CHF 3,000.00')
  expect(proposal).toHaveTextContent('the contract alone')
  expect(within(proposal).getByRole('row', { name: /^Documented loss/ })).toHaveTextContent('repair-invoice.pdf · page 1')
  expect(within(proposal).getByRole('row', { name: /^Deductible/ })).toHaveTextContent('-200.00')
  expect(within(proposal).getByRole('row', { name: /^Deductible/ })).toHaveTextContent('Art. 8.1')
  expect(within(page).getByRole('spinbutton', { name: 'Amount to approve' })).toHaveValue(3000)
  // the handler never sends the case to itself: a claim in review cannot be cleared
  expect(within(page).queryByRole('button', { name: 'Clear the investigation' })).toBeNull()
  expect(within(page).getByRole('button', { name: 'Send to investigation' })).toBeInTheDocument()
})

test('Approve sends the decision with the handler and the reason, then shows the new status and the history', async () => {
  const fetchMock = stub(() => Response.json(full('approved', [{ action: 'approve', by: 'David Frei', reason: 'Fits.', amount: 3000, time: '2026-11-16T09:00:00Z' }])))
  render(<HandlerDesk />)
  const page = await open()
  fireEvent.change(within(page).getByRole('combobox', { name: 'Handler' }), { target: { value: 'David Frei' } })
  fireEvent.change(within(page).getByRole('textbox', { name: 'Reason, or text for the customer' }), { target: { value: 'Fits.' } })
  fireEvent.click(within(page).getByRole('button', { name: 'Approve' }))
  await waitFor(() => expect(within(page).getByLabelText('Decisions')).toHaveTextContent('approve · CHF 3,000.00 · David Frei · Fits.'))
  const post = (fetchMock.mock.calls as Call[]).find(([u, i]) => u === '/claims/CL-review/decisions' && i?.method === 'POST')![1]!
  expect(JSON.parse(post.body as string)).toEqual({ action: 'approve', by: 'David Frei', reason: 'Fits.', amount: 3000 })
  expect(within(page).getByRole('heading', { level: 2 })).toHaveTextContent('approved')
  // decided: no action left
  expect(within(page).queryByRole('button', { name: 'Approve' })).toBeNull()
  expect(within(page).queryByRole('button', { name: 'Refuse' })).toBeNull()
})

test('a decision that the server does not take shows its reason and changes nothing', async () => {
  stub(() => Response.json({ detail: 'a written reason is required' }, { status: 422 }))
  render(<HandlerDesk />)
  const page = await open()
  fireEvent.click(within(page).getByRole('button', { name: 'Refuse' }))
  expect(await within(page).findByText('a written reason is required')).toBeInTheDocument()
  expect(within(page).getByRole('heading', { level: 2 })).toHaveTextContent('in review')
  expect(within(page).getByRole('button', { name: 'Refuse' })).toBeEnabled()
})

test('no claim picked: the case panel says what to do', async () => {
  stub()
  render(<HandlerDesk />)
  expect(await screen.findByText('Pick a claim in the queue.')).toBeInTheDocument()
})

test('the case page shows the earlier claims of the customer and the persons linked to him', async () => {
  stub()
  render(<HandlerDesk />)
  const page = await open()
  const section = await within(page).findByLabelText('History and links')
  expect(section).toHaveTextContent('Payout account MOCK-ACCT-1002')
  expect(within(section).getByLabelText('Earlier claims')).toHaveTextContent('H-2025-0618 · 2025-02-11 · Household, breakage · CHF 380.00')
  const link = within(section).getByLabelText('Link to Reto Vogel')
  expect(link).toHaveTextContent('same account, phone')
  expect(link).toHaveTextContent('H-2025-1120')
  expect(within(link).getByText('fraud')).toHaveClass('pill-bad')
})
