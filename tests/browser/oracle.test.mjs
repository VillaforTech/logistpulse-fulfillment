import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

// Execute the actual read-only browser predicate, without launching the benchmark.
const source = fs.readFileSync('scripts/browser-test.mjs', 'utf8');
const marker = 'await context.addInitScript(()=>{';
const start = source.indexOf(marker);
assert.ok(start >= 0, 'browser instrumentation must remain identifiable');
const end = source.indexOf('\n});', start);
assert.ok(end > start);
const window = {};
vm.runInNewContext(source.slice(start + marker.length, end), {window, Date, Number, Math, Set});

const order = {
  orderId: 'ORD-1', status: 'READY', total: '25.50',
  createdAt: '2026-09-14T10:00:00.000000Z', readyAt: '2026-09-14T10:00:04.000000Z',
};
const card = field => ({
  logistpulsePanel: field, quality: 'FRESH', revision: '10', eventId: 'event-1',
  computedAt: '2026-09-14T10:00:20.000000Z', sample: '1', value: '0', display: '0',
});
const valid = () => ['lk1', 'lk2', 'lk3'].map(card);

test('accepts three distinct finite coherent KPI cards', () => {
  assert.ok(window.matchLogist(valid(), [order]));
});
test('rejects NaN even when the visible text is also NaN', () => {
  const cards = valid().map(c => ({...c, value: 'NaN', display: 'NaN'}));
  assert.equal(window.matchLogist(cards, [order]), null);
});
test('rejects three copies of one otherwise valid KPI', () => {
  assert.equal(window.matchLogist(['lk1', 'lk1', 'lk1'].map(card), [order]), null);
});
test('rejects infinity and unknown KPI fields', () => {
  const cards = valid(); cards[0].value = 'Infinity';
  assert.equal(window.matchLogist(cards, [order]), null);
  assert.equal(window.matchLogist(['lk1', 'lk2', 'other'].map(card), [order]), null);
});
test('rejects a stale card and an inconsistent revision', () => {
  const stale = valid(); stale[0].quality = 'STALE';
  assert.equal(window.matchLogist(stale, [order]), null);
  const mixed = valid(); mixed[0].revision = '11';
  assert.equal(window.matchLogist(mixed, [order]), null);
});
test('rejects a wrong displayed value and empty event identity', () => {
  const wrong = valid(); wrong[0].display = '9';
  assert.equal(window.matchLogist(wrong, [order]), null);
  assert.equal(window.matchLogist(valid().map(c => ({...c, eventId: ''})), [order]), null);
});
