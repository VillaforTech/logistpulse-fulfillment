import assert from 'node:assert/strict';
import test from 'node:test';
import {inspectLiveFrame} from '../../scripts/grafana-live-protocol.mjs';

const expected = {organizationId: 1, wireChannel: '1/stream/logistpulse/business', logicalChannel: 'stream/logistpulse/business'};
const subscribe = JSON.stringify({subscribe: {channel: expected.wireChannel}, id: 4});

test('recognizes the actual organization-prefixed subscription shape', () => {
  assert.deepEqual(inspectLiveFrame(subscribe).subscriptions, [expected]);
});
test('recognizes newline batches while omitting connect data and dashboard channels', () => {
  const batch = [JSON.stringify({connect: {token: 'synthetic-private-value'}, id: 1}),
    JSON.stringify({subscribe: {channel: '1/grafana/dashboard/uid/logistpulse-business'}, id: 2}), subscribe].join('\n');
  const result = inspectLiveFrame(batch);
  assert.deepEqual(result.subscriptions, [expected]);
  assert.deepEqual(result.shapes, ['connect,id', 'subscribe,id', 'subscribe,id']);
  assert.equal(JSON.stringify(result).includes('synthetic-private-value'), false);
});
test('rejects other organizations, missing prefixes and other logical channels', () => {
  for (const channel of ['2/stream/logistpulse/business', '01/stream/logistpulse/business', 'stream/logistpulse/business',
    '1/stream/other/business', '1/stream/logistpulse/business-extra', '1/grafana/dashboard/uid/logistpulse-business']) {
    assert.deepEqual(inspectLiveFrame(JSON.stringify({subscribe: {channel}})).subscriptions, []);
  }
});
test('records malformed input without accepting it as a subscription', () => {
  const result = inspectLiveFrame('not-json\nnull\n[]\n' + subscribe);
  assert.equal(result.invalidLines, 1);
  assert.deepEqual(result.subscriptions, [expected]);
});
test('validates an explicit expected organization', () => {
  assert.throws(() => inspectLiveFrame(subscribe, 0), RangeError);
  assert.deepEqual(inspectLiveFrame(subscribe, 2).subscriptions, []);
});
