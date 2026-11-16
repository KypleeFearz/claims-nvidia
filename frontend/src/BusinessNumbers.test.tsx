import { fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import BusinessNumbers from './BusinessNumbers'

afterEach(() => vi.unstubAllGlobals())

const live = {
  claims: 5,
  by_status: { open: 1, in_review: 1, refused: 1, paid: 2 },
  routed: 4,
  straight_through: 1,
  straight_through_share: 0.25,
  to_a_person: 3,
  ended: 3,
  median_minutes_to_end: 2.5,
  claimed: 9650,
  proposed: 9250,
  paid: 4500,
  open_reserve: 1750,
  investigations: { open: 0, confirmed: 0, not_confirmed: 1, not_confirmed_share: 1 },
  decisions: { approve: 1, refuse: 1, clear: 1 },
}
const row = (story: string, more: object = {}) => ({
  story,
  title: 'A story',
  expected_outcome: 'straight_through',
  outcome: 'straight_through',
  outcome_right: true,
  expected_signals: [],
  signals: [],
  signals_right: true,
  expected_payable: 1500,
  payable: 1500,
  payable_right: true,
  error: null,
  ...more,
})
const kit = {
  reader: 'omni',
  stories: 2,
  outcomes_right: 1,
  signals_right: 1,
  amounts_right: 1,
  seeded_fraud: 1,
  fraud_caught: 0,
  fraud_missed: 1,
  false_alarms: 0,
  rows: [
    row('claim-1-travel/1500'),
    row('claim-3-health/4450', {
      expected_outcome: 'investigation',
      outcome: 'handler_review',
      outcome_right: false,
      expected_signals: ['C001', 'T003'],
      signals: ['C001'],
      signals_right: false,
      expected_payable: 1305,
      payable: 4005,
      payable_right: false,
    }),
  ],
}

const stub = (numbers: object = live, kitAnswer: Response = Response.json(kit)) => {
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (url === '/numbers') return Promise.resolve(Response.json(numbers))
    if (url === '/numbers/kit' && init?.method === 'POST') return Promise.resolve(kitAnswer)
    return Promise.resolve(new Response('', { status: 404 }))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

test('the claims of the demo: the share with no person, the time, the money and the alerts', async () => {
  stub()
  render(<BusinessNumbers />)
  const panel = screen.getByRole('region', { name: 'Claims of the demo' })
  expect(await within(panel).findByText('25%')).toBeInTheDocument()
  expect(panel).toHaveTextContent('1 of 4 routed claims')
  expect(panel).toHaveTextContent('2.5 min')
  expect(panel).toHaveTextContent('median of 3 ended claims')
  expect(panel).toHaveTextContent('CHF 4,500.00')
  expect(panel).toHaveTextContent('claimed CHF 9,650.00 · proposed CHF 9,250.00')
  expect(panel).toHaveTextContent('CHF 1,750.00')
  expect(panel).toHaveTextContent('1 not confirmed · 0 confirmed')
  expect(within(panel).getByLabelText('By status')).toHaveTextContent('open: 1 · in review: 1 · refused: 1 · paid: 2')
  expect(within(panel).getByLabelText('Decisions of the handlers')).toHaveTextContent('approve: 1 · refuse: 1 · clear: 1')
})

test('no claim yet: the page says where to play one', async () => {
  stub({ ...live, claims: 0, by_status: {}, straight_through_share: null })
  render(<BusinessNumbers />)
  expect(await screen.findByText('No claim yet. Play a claim on the page “Claim demo”.')).toBeInTheDocument()
})

test('Play the kit shows the summary and each story, with what was expected when it is wrong', async () => {
  const fetchMock = stub()
  render(<BusinessNumbers />)
  const panel = screen.getByRole('region', { name: 'Kit' })
  fireEvent.click(within(panel).getByRole('button', { name: 'Play the kit' }))
  const summary = await within(panel).findByRole('status')
  expect(fetchMock.mock.calls.some(([u, i]) => u === '/numbers/kit' && i?.method === 'POST')).toBe(true)
  expect(summary).toHaveTextContent('Reader omni')
  expect(within(summary).getByText('1 of 2 outcomes right')).toHaveClass('pill-bad')
  expect(within(summary).getByText('Fraud caught: 0 of 1')).toHaveClass('pill-bad')
  expect(within(summary).getByText('False alarms: 0')).toHaveClass('pill-ok')
  const table = within(panel).getByRole('table', { name: 'Kit stories' })
  const good = within(table).getByRole('row', { name: /^claim-1-travel\/1500/ })
  expect(within(good).getByText('straight_through')).toHaveClass('pill-ok')
  const bad = within(table).getByRole('row', { name: /^claim-3-health\/4450/ })
  expect(within(bad).getByText('handler_review')).toHaveClass('pill-bad')
  expect(bad).toHaveTextContent('expected investigation')
  expect(bad).toHaveTextContent('expected C001 T003')
  expect(bad).toHaveTextContent('CHF 4,005.00')
  expect(bad).toHaveTextContent('expected CHF 1,305.00')
})

test('a kit run that is refused says why', async () => {
  stub(live, new Response('', { status: 409 }))
  render(<BusinessNumbers />)
  fireEvent.click(screen.getByRole('button', { name: 'Play the kit' }))
  expect(await screen.findByText('A kit run is in progress.')).toBeInTheDocument()
})
