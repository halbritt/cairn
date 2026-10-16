'use strict';
const LIMIT = 3 * 1024 * 1024;
const MAX_OP = Number.MAX_SAFE_INTEGER;
class ProfileError extends Error {}
const integer = (v, lo, hi) => typeof v === 'number' && Number.isSafeInteger(v) && v >= lo && v <= hi;
// Intrinsic getters avoid trusting shadowed instance properties.
const proto = Object.getPrototypeOf(Uint8Array.prototype);
const get = name => Object.getOwnPropertyDescriptor(proto, name).get;
const length = get('length'), backing = get('buffer'), tag = get(Symbol.toStringTag);
const abLength = Object.getOwnPropertyDescriptor(ArrayBuffer.prototype, 'byteLength').get;
const resizable = Object.getOwnPropertyDescriptor(ArrayBuffer.prototype, 'resizable')?.get;
function receipt(body, width) {
  try {
    if (!ArrayBuffer.isView(body) || tag.call(body) !== 'Uint8Array') throw new ProfileError('receipt_type');
    const buffer = backing.call(body);
    abLength.call(buffer); // Reject SharedArrayBuffer without coercion.
    if (resizable && resizable.call(buffer)) throw new ProfileError('receipt_resizable');
    if (length.call(body) !== width) throw new ProfileError('receipt_width');
    if (width === 2) return body[0] * 256 + body[1];
    if (body[0] !== 1) throw new ProfileError('close_status');
    return body[1] * 16777216 + body[2] * 65536 + body[3] * 256 + body[4];
  } catch (_) { throw new ProfileError('receipt_invalid'); }
}
const parseCustody = body => receipt(body, 2);
const parseEnd = body => receipt(body, 5);
class InputClient {
  #maximum; #state = 'ready'; #pending = null; #last = 0; #custody = 0; #native = null; #unknown = false;
  constructor(profile, maximum) {
    if (!integer(profile, 2, 2) || !integer(maximum, 1, LIMIT)) throw new ProfileError('configuration');
    this.#maximum = maximum;
  }
  snapshot() {
    return {state: this.#state, pending: this.#pending === null ? null : [...this.#pending],
      custodied_total: this.#custody, known_native_total: this.#native, unknown: this.#unknown};
  }
  #fail(code) { this.#state = 'failed'; throw new ProfileError(code); }
  #begin(op, kind, offered) {
    if (this.#state !== 'ready' || this.#pending !== null) this.#fail('order');
    if (!integer(op, 1, MAX_OP) || op <= this.#last) this.#fail('operation');
    this.#last = op; this.#pending = [op, kind, offered]; this.#state = 'pending';
  }
  beginCustody(op, offered) {
    if (!integer(offered, 1, 512) || offered > this.#maximum - this.#custody) this.#fail('budget_or_offer');
    this.#begin(op, 140, offered);
  }
  beginEnd(op) { this.#begin(op, 133, 0); }
  result(op, kind, body) {
    try {
      if (this.#state !== 'pending' || this.#pending === null || !integer(op, 1, MAX_OP)
          || !integer(kind, 0, 255) || op !== this.#pending[0] || kind !== this.#pending[1]) this.#fail('correlation');
      if (kind === 140) {
        const count = parseCustody(body);
        if (count > this.#pending[2]) this.#fail('count');
        this.#custody += count; this.#state = count === 0 ? 'paused' : 'ready';
      } else {
        const total = parseEnd(body);
        if (total !== this.#custody) this.#fail('close_total');
        this.#native = total; this.#state = 'closed';
      }
      this.#pending = null;
    } catch (e) { this.#state = 'failed'; throw e; }
  }
  writable() {
    if (this.#state !== 'paused' || this.#pending !== null) this.#fail('writable_order');
    this.#state = 'ready';
  }
  transportUnknown() {
    if (!['ready', 'pending', 'paused'].includes(this.#state)) this.#fail('unknown_order');
    this.#unknown = true; this.#fail('transport_unknown');
  }
}
module.exports = {InputClient, parseCustody, parseEnd, ProfileError};
