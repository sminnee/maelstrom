/**
 * The readings a chip can colour something by.
 *
 * A tone names how a thing reads, never what it is: `bad` and `busy`, never
 * `ci-failed`. That is what lets one chip serve a pull request today and
 * anything else that reports a state later, and it keeps the mapping — which
 * state is which reading — a decision each domain makes for itself.
 */
export type ChipTone = 'neutral' | 'quiet' | 'good' | 'bad' | 'busy' | 'special';
