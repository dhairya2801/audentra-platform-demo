/**
 * Deterministic randomness for the synthetic university.
 *
 * Every value in this data layer has to be reproducible from a seed: the demo
 * fixtures, the evaluation transcripts, and the persona ids in `personas.js`
 * all assume that the same seed rebuilds byte-identical data. `Math.random`
 * would quietly break that, so it appears nowhere in this directory.
 */

/** Milliseconds in a day, used by every date helper in this directory. */
export const DAY_MS = 86_400_000;

/**
 * mulberry32: a 32-bit PRNG that is short enough to audit by eye and has a
 * long enough period for the ~100k draws a full universe needs.
 *
 * @param {number} seed
 * @returns {() => number} a function returning a float in [0, 1)
 */
export function createRng(seed) {
  let state = (Number(seed) >>> 0) || 0x9e37_79b9;
  return function rng() {
    state = (state + 0x6d2b_79f5) >>> 0;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4_294_967_296;
  };
}

/**
 * FNV-1a. Used to turn a stable string key (a term code, a persona key) into a
 * seed, so those ids stay put even when the draw order around them changes.
 *
 * @param {string} text
 * @returns {number}
 */
export function hashString(text) {
  let hash = 0x811c_9dc5;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 0x0100_0193) >>> 0;
  }
  return hash >>> 0;
}

/**
 * @template T
 * @param {() => number} rng
 * @param {readonly T[]} array
 * @returns {T}
 */
export function pick(rng, array) {
  if (!array.length) throw new RangeError("pick() needs a non-empty array");
  return array[Math.floor(rng() * array.length)];
}

/**
 * @template T
 * @param {() => number} rng
 * @param {readonly [T, number][]} entries value/weight pairs; weights need not sum to 1
 * @returns {T}
 */
export function weighted(rng, entries) {
  let total = 0;
  for (const [, weight] of entries) total += weight;
  if (total <= 0) throw new RangeError("weighted() needs a positive total weight");
  let roll = rng() * total;
  for (const [value, weight] of entries) {
    roll -= weight;
    if (roll < 0) return value;
  }
  return entries[entries.length - 1][0];
}

/**
 * Inclusive on both ends, which is what every caller here actually wants.
 *
 * @param {() => number} rng
 * @param {number} min
 * @param {number} max
 * @returns {number}
 */
export function intBetween(rng, min, max) {
  if (max < min) throw new RangeError("intBetween() needs max >= min");
  return min + Math.floor(rng() * (max - min + 1));
}

/**
 * @param {() => number} rng
 * @param {number} min
 * @param {number} max
 * @param {number} [decimals]
 * @returns {number}
 */
export function floatBetween(rng, min, max, decimals = 2) {
  const value = min + rng() * (max - min);
  return Number(value.toFixed(decimals));
}

/**
 * @param {() => number} rng
 * @param {number} probability between 0 and 1
 * @returns {boolean}
 */
export function chance(rng, probability) {
  return rng() < probability;
}

/**
 * Fisher-Yates on a copy: callers pass frozen catalogues, so shuffling in
 * place would throw.
 *
 * @template T
 * @param {() => number} rng
 * @param {readonly T[]} array
 * @returns {T[]}
 */
export function shuffle(rng, array) {
  const copy = [...array];
  for (let index = copy.length - 1; index > 0; index -= 1) {
    const swap = Math.floor(rng() * (index + 1));
    [copy[index], copy[swap]] = [copy[swap], copy[index]];
  }
  return copy;
}

/**
 * @template T
 * @param {() => number} rng
 * @param {readonly T[]} array
 * @param {number} count
 * @returns {T[]} up to `count` distinct members, in shuffled order
 */
export function sample(rng, array, count) {
  return shuffle(rng, array).slice(0, Math.max(0, Math.min(count, array.length)));
}

const HEX = "0123456789abcdef";

/**
 * RFC-4122-shaped v4 identifier drawn from the seeded stream. It is not a
 * cryptographic uuid and must never be treated as one; it exists so synthetic
 * ids look like the real system's ids without importing entropy.
 *
 * @param {() => number} rng
 * @returns {string}
 */
export function uuidFrom(rng) {
  const nibbles = new Array(32);
  for (let index = 0; index < 32; index += 1) {
    nibbles[index] = HEX[Math.floor(rng() * 16)];
  }
  nibbles[12] = "4";
  nibbles[16] = HEX[8 + Math.floor(rng() * 4)];
  const hex = nibbles.join("");
  return [
    hex.slice(0, 8),
    hex.slice(8, 12),
    hex.slice(12, 16),
    hex.slice(16, 20),
    hex.slice(20, 32),
  ].join("-");
}

/**
 * A uuid that depends only on the string, not on the draw order. Catalogue
 * rows and persona students use this so their ids survive edits elsewhere in
 * the generator.
 *
 * @param {string} text
 * @returns {string}
 */
export function uuidFromString(text) {
  return uuidFrom(createRng(hashString(text)));
}

/**
 * @param {string} iso
 * @param {number} days may be negative
 * @returns {string}
 */
export function addDays(iso, days) {
  return new Date(Date.parse(iso) + days * DAY_MS).toISOString();
}

/**
 * @param {string} iso
 * @param {number} hours may be negative
 * @returns {string}
 */
export function addHours(iso, hours) {
  return new Date(Date.parse(iso) + hours * 3_600_000).toISOString();
}
