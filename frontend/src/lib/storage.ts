import { normalizeCity } from "./format";

const PINS_KEY = "weather-cache:pins";
const TRACKED_MIRROR_KEY = "weather-cache:tracked";

export const MAX_PINS = 5;

/** localStorage throws in private-mode Safari and when quota is exceeded. */
function readList(key: string): string[] {
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === "string") : [];
  } catch {
    return [];
  }
}

function writeList(key: string, values: string[]): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(values));
  } catch {
    // Persisting preferences is best-effort; the in-memory state still applies.
  }
}

export function readPins(): string[] {
  return readList(PINS_KEY).slice(0, MAX_PINS);
}

export function writePins(pins: string[]): void {
  writeList(PINS_KEY, pins.slice(0, MAX_PINS));
}

/**
 * Local copy of the server's tracked list, used to render something sensible
 * before /cities resolves and if it fails outright. Reconciled to the server on
 * every successful load, so a stale client can't resurrect a removed city.
 */
export function readTrackedMirror(): string[] {
  return readList(TRACKED_MIRROR_KEY);
}

export function writeTrackedMirror(cities: string[]): void {
  writeList(TRACKED_MIRROR_KEY, cities);
}

/** Drop pins for cities that are no longer tracked, preserving pin order. */
export function reconcilePins(pins: string[], tracked: string[]): string[] {
  const trackedKeys = new Set(tracked.map(normalizeCity));
  return pins.filter((pin) => trackedKeys.has(normalizeCity(pin)));
}
