import { describe, expect, it } from 'vitest';
import { eventsUrl } from './eventsUrl';

describe('eventsUrl', () => {
  it('addresses the orchestrator directly when it serves a different port', () => {
    expect(eventsUrl({ protocol: 'http:', hostname: 'localhost', port: '3410' }, '3412')).toBe(
      'http://localhost:3412/api/events',
    );
  });

  it('keeps the hostname the page was loaded from', () => {
    expect(
      eventsUrl({ protocol: 'http:', hostname: 'desk.example.ts.net', port: '3410' }, '3412'),
    ).toBe('http://desk.example.ts.net:3412/api/events');
  });

  it('stays same-origin when no orchestrator port is configured', () => {
    expect(eventsUrl({ protocol: 'http:', hostname: 'localhost', port: '3410' }, undefined)).toBe(
      '/api/events',
    );
  });
});
