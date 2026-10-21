/**
 * A memory size in the one short unit a row holds: `512B`, `12K`, `340M` or `5.6G`.
 *
 * The same rule as `_bytes` in tangier's `gate status`, so one job's memory reads
 * the same way in the dashboard and in the terminal. A unit is 1024 of the one
 * below it. Below 10 of a unit the figure keeps one decimal place.
 */
export function bytes(size: number): string {
  for (const [unit, scale] of UNITS) {
    if (size >= scale) {
      const figure = size / scale;
      const places = figure < 10 ? 1 : 0;
      return `${roundHalfEven(figure, places).toFixed(places)}${unit}`;
    }
  }
  return `${size}B`;
}

/** Python's rounding: a half goes to the even digit. `toFixed` rounds it up. */
function roundHalfEven(figure: number, places: number): number {
  const scale = 10 ** places;
  const scaled = figure * scale;
  const floor = Math.floor(scaled);
  const rest = scaled - floor;
  const rounded = rest > 0.5 || (rest === 0.5 && floor % 2 === 1) ? floor + 1 : floor;
  return rounded / scale;
}

const UNITS: [string, number][] = [
  ['G', 1024 ** 3],
  ['M', 1024 ** 2],
  ['K', 1024],
];
