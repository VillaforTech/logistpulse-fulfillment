export const LOGICAL_CHANNEL = 'stream/logistpulse/business';

// Grafana prefixes the wire channel with the organization ID. This lab uses org 1.
// Return only whitelisted channel identities and coarse shapes, never auth payloads.
export function inspectLiveFrame(payload, organizationId = 1) {
  if (!Number.isSafeInteger(organizationId) || organizationId < 1) {
    throw new RangeError('Expected a positive Grafana organization ID');
  }
  const wireChannel = `${organizationId}/${LOGICAL_CHANNEL}`;
  const result = {subscriptions: [], shapes: [], invalidLines: 0};
  for (const line of String(payload).split('\n').filter(line => line.trim())) {
    let message;
    try { message = JSON.parse(line); } catch { result.invalidLines++; continue; }
    if (!message || typeof message !== 'object' || Array.isArray(message)) {
      result.shapes.push('non-object');
      continue;
    }
    result.shapes.push(['connect', 'subscribe', 'unsubscribe', 'id'].filter(key => key in message).join(',') || 'other');
    if (message.subscribe?.channel === wireChannel) {
      result.subscriptions.push({organizationId, wireChannel, logicalChannel: LOGICAL_CHANNEL});
    }
  }
  return result;
}
